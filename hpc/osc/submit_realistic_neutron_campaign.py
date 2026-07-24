#!/usr/bin/env python3
"""Validate, submit, resume, or retry one realistic-neutron OSC campaign."""

from __future__ import annotations

import argparse
import csv
import fcntl
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from realistic_neutron_campaign_lib import (
    ATTEMPT_FIELDS,
    ATTEMPT_MANIFEST_NAME,
    RESULT_SCHEMA_VERSION,
    CampaignBundle,
    CampaignTask,
    atomic_write_json,
    campaign_relative,
    load_campaign,
    load_json,
    read_attempt_rows,
    resolve_campaign_path,
    resolve_recorded_artifact,
    sha256_file,
    task_by_logical_id,
    write_tsv,
)
from record_realistic_neutron_task_result import (
    integer_field,
    read_single_csv_row,
    validate_run_config,
)


TERMINAL_FAILURE_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}
ACTIVE_STATES = {
    "COMPLETING",
    "CONFIGURING",
    "PENDING",
    "REQUEUED",
    "RESIZING",
    "RUNNING",
    "SUSPENDED",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Clean repository checkout used to prepare the frozen source archive.",
    )
    parser.add_argument("--account", help="OSC Slurm account; required for submission.")
    parser.add_argument(
        "--g4-data-root",
        type=Path,
        help="Host directory containing the pinned Geant4 11.4.2 datasets.",
    )
    parser.add_argument(
        "--frozen-root",
        type=Path,
        help="Parent directory for immutable commit-specific source archives.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-active-tasks", type=int, default=1000)
    parser.add_argument("--sbatch-command", default="sbatch")
    parser.add_argument("--sacct-command", default="sacct")
    parser.add_argument("--squeue-command", default="squeue")
    return parser.parse_args()


def run_command(
    command: list[str], *, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result


def git_value(project_root: Path, *args: str) -> str:
    return run_command(["git", *args], cwd=project_root).stdout.strip()


def verify_production_checkout(project_root: Path, bundle: CampaignBundle) -> None:
    root = project_root.expanduser().resolve()
    if not (root / ".git").exists():
        raise ValueError(f"project root is not a Git checkout: {root}")
    head = git_value(root, "rev-parse", "HEAD")
    if head != bundle.git_commit:
        raise ValueError(
            f"checkout commit {head} does not match campaign commit {bundle.git_commit}"
        )
    dirty = git_value(root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise ValueError("production submission requires a clean checkout")
    if bundle.manifest["git"].get("dirty") is not False:
        raise ValueError("production campaign was not generated from a clean checkout")


def verify_production_environment(
    bundle: CampaignBundle,
) -> tuple[Path, str, Path, str, Path, str]:
    environment = bundle.environment
    if environment.get("mode") != "osc-production":
        raise ValueError("only osc-production campaigns may be submitted")
    if environment.get("geant4_version") != "11.4.2":
        raise ValueError("production campaign must pin Geant4 11.4.2")
    if environment.get("accepted_statistical_evidence") is not True:
        raise ValueError("campaign environment is not accepted statistical evidence")
    image, image_sha = resolve_recorded_artifact(
        environment.get("image"), "Apptainer image"
    )
    data_manifest, data_sha = resolve_recorded_artifact(
        environment.get("g4_data_manifest"), "Geant4 data manifest"
    )
    executable, executable_sha = resolve_recorded_artifact(
        environment.get("build_artifact"), "prebuilt executable"
    )
    if (executable.stat().st_mode & 0o111) == 0:
        raise ValueError(f"prebuilt artifact is not executable: {executable}")
    return image, image_sha, data_manifest, data_sha, executable, executable_sha


def safe_archive_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        if path.is_dir():
            path.chmod((mode & 0o555) | 0o555)
        else:
            path.chmod(mode & ~0o222)
    root.chmod(0o555)


def source_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0")
            digest.update(os.readlink(path).encode("utf-8") + b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            digest.update(b"X\0" if path.stat().st_mode & 0o111 else b"N\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            raise ValueError(f"unsupported frozen-source entry: {path}")
    return digest.hexdigest()


def prepare_frozen_source(
    project_root: Path, frozen_root: Path, bundle: CampaignBundle
) -> Path:
    base = frozen_root.expanduser().resolve()
    bundle_root = base / f"{bundle.campaign_id}-{bundle.git_commit[:12]}"
    source = bundle_root / "source"
    identity_path = bundle_root / "source-identity.json"
    preset = bundle.manifest.get("study_preset")
    schema_prefix = (
        "steel-module-stack"
        if preset == "steel-module-stack-v1"
        else "steel-module"
        if preset == "steel-module-scan-v1"
        else "realistic-neutron"
    )
    base_identity = {
        "schema_version": f"{schema_prefix}-frozen-source-v1",
        "campaign_id": bundle.campaign_id,
        "plan_hash": bundle.plan_hash,
        "git_commit": bundle.git_commit,
    }
    if source.exists() or identity_path.exists():
        if not source.is_dir() or not identity_path.is_file():
            raise ValueError(f"incomplete frozen source bundle: {bundle_root}")
        recorded_identity = load_json(identity_path)
        for key, value in base_identity.items():
            if recorded_identity.get(key) != value:
                raise ValueError(f"frozen source identity mismatch: {bundle_root}")
        recorded_tree_hash = recorded_identity.get("source_tree_sha256")
        if not isinstance(recorded_tree_hash, str) or source_tree_hash(source) != recorded_tree_hash:
            raise ValueError(f"frozen source identity mismatch: {bundle_root}")
        return source

    bundle_root.mkdir(parents=True, exist_ok=False)
    source.mkdir()
    process = subprocess.Popen(
        ["git", "archive", "--format=tar", bundle.git_commit],
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if not safe_archive_member(member.name):
                    raise ValueError(f"unsafe Git archive member: {member.name}")
                archive.extract(member, source)
    finally:
        process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise ValueError(f"git archive failed ({return_code}): {stderr.strip()}")
    required_script = source / "hpc/osc/submit_scan.sbatch"
    if not required_script.is_file():
        raise ValueError(f"frozen source is missing {required_script.relative_to(source)}")
    identity = {
        **base_identity,
        "source_tree_sha256": source_tree_hash(source),
    }
    atomic_write_json(identity_path, identity)
    make_tree_read_only(source)
    identity_path.chmod(0o444)
    bundle_root.chmod(0o555)
    return source


def attempt_task_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    required = {"array_index", "task_index", "logical_task_id"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError(f"invalid attempt task map header: {path}")
    return rows


def submitted_task_attempts(
    bundle: CampaignBundle, attempts: Iterable[dict[str, str]]
) -> dict[str, list[tuple[dict[str, str], int]]]:
    known = task_by_logical_id(bundle)
    mapped: dict[str, list[tuple[dict[str, str], int]]] = {
        task.logical_task_id: [] for task in bundle.tasks
    }
    for attempt in attempts:
        if attempt.get("status") != "submitted":
            continue
        if (
            attempt.get("campaign_id") != bundle.campaign_id
            or attempt.get("plan_hash") != bundle.plan_hash
            or attempt.get("git_commit") != bundle.git_commit
        ):
            raise ValueError(f"attempt identity mismatch: {attempt.get('attempt_id')}")
        path = resolve_campaign_path(bundle.directory, attempt["attempt_tasks_tsv"])
        rows = attempt_task_rows(path)
        if len(rows) != int(attempt["task_count"]):
            raise ValueError(f"attempt task count mismatch: {attempt.get('attempt_id')}")
        seen_logical_ids: set[str] = set()
        for expected_array_index, row in enumerate(rows, start=1):
            logical_id = row["logical_task_id"]
            if logical_id not in known:
                raise ValueError(f"attempt contains unknown task: {logical_id}")
            if logical_id in seen_logical_ids:
                raise ValueError(f"attempt contains duplicate task: {logical_id}")
            seen_logical_ids.add(logical_id)
            task = known[logical_id]
            if row.get("array_index") != str(expected_array_index):
                raise ValueError(
                    f"attempt array index mismatch: {attempt.get('attempt_id')}"
                )
            for key, expected in asdict(task).items():
                if row.get(key) != str(expected):
                    raise ValueError(
                        f"attempt task identity mismatch for {logical_id}: {key}"
                    )
            mapped[logical_id].append((attempt, int(row["array_index"])))
    return mapped


def validate_attempt_journal(
    campaign_dir: Path, attempts: list[dict[str, str]]
) -> None:
    manifest_rows: dict[str, dict[str, str]] = {}
    for row in attempts:
        attempt_id = row.get("attempt_id", "")
        if not attempt_id or attempt_id in manifest_rows:
            raise ValueError(f"duplicate or empty attempt ID: {attempt_id!r}")
        manifest_rows[attempt_id] = row

    attempts_root = campaign_dir / "attempts"
    recorded_json: set[str] = set()
    if attempts_root.is_dir():
        for attempt_path in sorted(attempts_root.glob("*/attempt.json")):
            value = load_json(attempt_path)
            attempt_id = value.get("attempt_id")
            if attempt_id != attempt_path.parent.name:
                raise ValueError(f"attempt directory identity mismatch: {attempt_path}")
            row = manifest_rows.get(str(attempt_id))
            if row is None:
                raise ValueError(
                    "unrecorded submission attempt requires manual Slurm audit before "
                    f"continuing: {attempt_path.parent}"
                )
            if (
                value.get("status") != row.get("status")
                or str(value.get("slurm_job_id") or "") != row.get("slurm_job_id")
            ):
                raise ValueError(f"attempt journal mismatch: {attempt_path.parent}")
            recorded_json.add(str(attempt_id))

    missing_json = set(manifest_rows) - recorded_json
    if missing_json:
        raise ValueError(
            f"attempt manifest rows lack attempt.json records: {sorted(missing_json)}"
        )


def shallow_successful_results(
    bundle: CampaignBundle, attempts_by_task: dict[str, list[tuple[dict[str, str], int]]]
) -> set[str]:
    tasks = task_by_logical_id(bundle)
    valid_attempts_by_task = {
        logical_id: {item[0]["attempt_id"] for item in values}
        for logical_id, values in attempts_by_task.items()
    }
    successful: set[str] = set()
    for marker in bundle.directory.glob("attempts/*/tasks/*/task_result.json"):
        try:
            result = load_json(marker)
            logical_id = result.get("logical_task_id")
            task = tasks.get(logical_id)
            attempt_id = result.get("attempt_id")
            expected_marker = (
                bundle.directory
                / "attempts"
                / str(attempt_id)
                / "tasks"
                / str(logical_id)
                / "task_result.json"
            ).resolve()
            if marker.resolve() != expected_marker:
                continue
            if (
                result.get("schema_version") == RESULT_SCHEMA_VERSION
                and result.get("campaign_id") == bundle.campaign_id
                and result.get("plan_hash") == bundle.plan_hash
                and task is not None
                and attempt_id in valid_attempts_by_task[logical_id]
                and result.get("task_index") == task.task_index
                and result.get("configuration_hash") == task.configuration_hash
                and result.get("seed1") == task.seed1
                and result.get("seed2") == task.seed2
                and result.get("events") == task.events
            ):
                artifacts = result.get("artifacts")
                if not isinstance(artifacts, dict):
                    continue
                resolved: dict[str, Path] = {}
                for key in (
                    "run_config",
                    "points",
                    "macro",
                    "simulation_log",
                    "root",
                    "summary",
                    "efficiency_map",
                ):
                    record = artifacts.get(key)
                    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                        raise ValueError(f"invalid task-result artifact {key}")
                    path = resolve_campaign_path(bundle.directory, record["path"])
                    if not path.is_file() or path.stat().st_size != record.get("size_bytes"):
                        raise ValueError(f"missing or changed task-result artifact {key}")
                    digest = record.get("sha256")
                    if not isinstance(digest, str) or sha256_file(path) != digest:
                        raise ValueError(f"task-result artifact checksum mismatch: {key}")
                    resolved[key] = path
                config = load_json(resolved["run_config"])
                validate_run_config(config, task, str(attempt_id), bundle)
                _, summary = read_single_csv_row(resolved["summary"])
                if integer_field(summary, "events") != task.events:
                    raise ValueError("task-result summary event count mismatch")
                if integer_field(summary, "committed_events") != task.events:
                    raise ValueError("task-result committed-event count mismatch")
                successful.add(logical_id)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return successful


def normalize_slurm_state(value: str) -> str:
    return value.strip().split()[0].rstrip("+") if value.strip() else ""


def sacct_states(command: str, project_root: Path, job_id: str) -> dict[int, str]:
    result = run_command(
        [
            command,
            "--noheader",
            "--parsable2",
            "--jobs",
            job_id,
            "--format=JobIDRaw,State,ExitCode",
        ],
        cwd=project_root,
    )
    states: dict[int, str] = {}
    pattern = re.compile(rf"^{re.escape(job_id)}_(\d+)$")
    for line in result.stdout.splitlines():
        fields = line.split("|")
        if len(fields) < 2:
            continue
        match = pattern.match(fields[0].strip())
        if match:
            states[int(match.group(1))] = normalize_slurm_state(fields[1])
    return states


def select_tasks(
    mode: str,
    bundle: CampaignBundle,
    attempts_by_task: dict[str, list[tuple[dict[str, str], int]]],
    successful: set[str],
    *,
    project_root: Path,
    sacct_command: str,
) -> tuple[list[CampaignTask], list[str]]:
    messages: list[str] = []
    if mode == "submit":
        if any(attempts_by_task.values()):
            raise ValueError(
                "campaign already has submitted attempts; use --resume or --retry-failed"
            )
        return list(bundle.tasks), messages
    if mode == "resume":
        selected = [
            task for task in bundle.tasks if not attempts_by_task[task.logical_task_id]
        ]
        return selected, messages

    state_cache: dict[str, dict[int, str]] = {}
    selected: list[CampaignTask] = []
    for task in bundle.tasks:
        logical_id = task.logical_task_id
        if logical_id in successful:
            continue
        attempts = attempts_by_task[logical_id]
        if not attempts:
            messages.append(f"not previously submitted; use --resume: {logical_id}")
            continue
        latest, array_index = attempts[-1]
        job_id = latest["slurm_job_id"]
        if job_id not in state_cache:
            state_cache[job_id] = sacct_states(sacct_command, project_root, job_id)
        state = state_cache[job_id].get(array_index, "")
        if state in TERMINAL_FAILURE_STATES or state == "COMPLETED":
            selected.append(task)
        elif state in ACTIVE_STATES:
            messages.append(f"still {state.lower()}: {logical_id}")
        else:
            messages.append(f"unknown Slurm state; not retried: {logical_id}")
    return selected, messages


def active_task_count(command: str, project_root: Path) -> int:
    result = run_command(
        [command, "-h", "-r", "-u", getpass.getuser()], cwd=project_root
    )
    return sum(1 for line in result.stdout.splitlines() if line.strip())


def utc_attempt_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{mode}"


def write_attempt_files(
    bundle: CampaignBundle,
    tasks: list[CampaignTask],
    mode: str,
    frozen_source: Path,
) -> tuple[str, Path, Path, Path]:
    attempt_id = utc_attempt_id(mode)
    attempt_dir = bundle.directory / "attempts" / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=False)
    task_map = attempt_dir / "tasks.tsv"
    scan_args = attempt_dir / "scan_args.txt"
    rows = []
    for array_index, task in enumerate(tasks, start=1):
        rows.append({"array_index": array_index, **asdict(task)})
    fields = ["array_index", *CampaignTask.__dataclass_fields__]
    write_tsv(task_map, fields, rows)
    preset = bundle.manifest.get("study_preset")
    schema_prefix = (
        "steel-module-stack"
        if preset == "steel-module-stack-v1"
        else "steel-module"
        if preset == "steel-module-scan-v1"
        else "realistic-neutron"
    )
    scan_args.write_text(
        "\n".join(
            [
                f"# schema_version={schema_prefix}-attempt-plan-v1",
                f"# campaign_id={bundle.campaign_id}",
                f"# attempt_id={attempt_id}",
                *[bundle.scan_args[task.task_index - 1] for task in tasks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    attempt_json = attempt_dir / "attempt.json"
    atomic_write_json(
        attempt_json,
        {
            "schema_version": f"{schema_prefix}-submission-attempt-v1",
            "attempt_id": attempt_id,
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "mode": mode,
            "status": "prepared",
            "task_count": len(tasks),
            "logical_task_ids": [task.logical_task_id for task in tasks],
            "frozen_source": str(frozen_source),
        },
    )
    task_map.chmod(0o444)
    scan_args.chmod(0o444)
    return attempt_id, attempt_dir, task_map, scan_args


def validate_export_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        if not value or any(char in value for char in (",", "\n", "\r")):
            raise ValueError(f"unsafe Slurm export value for {key}: {value!r}")


def campaign_scheduler_args(bundle: CampaignBundle) -> list[str]:
    if bundle.manifest.get("study_preset") != "steel-module-stack-v1":
        return []
    scheduler = bundle.manifest.get("scheduler_contract")
    if not isinstance(scheduler, dict):
        raise ValueError("stack campaign lacks scheduler contract")
    expected = {
        "nodes": 1,
        "ntasks": 1,
        "cpus_per_task": 1,
        "time_seconds": 3600,
    }
    if any(scheduler.get(key) != value for key, value in expected.items()):
        raise ValueError("stack scheduler contract mismatch")
    memory_gib = scheduler.get("memory_gib")
    if not isinstance(memory_gib, int) or memory_gib < 2:
        raise ValueError("stack scheduler memory is invalid")
    return [
        "--time=01:00:00",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=1",
        f"--mem={memory_gib}G",
    ]


def append_attempt_row(campaign_dir: Path, row: dict[str, object]) -> None:
    path = campaign_dir / ATTEMPT_MANIFEST_NAME
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ATTEMPT_FIELDS, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow(row)
        stream.flush()
        os.fsync(stream.fileno())


def update_attempt_status(
    path: Path, *, status: str, submitted_utc: str, job_id: str, command: list[str]
) -> None:
    value = load_json(path)
    value.update(
        {
            "status": status,
            "submitted_utc": submitted_utc,
            "slurm_job_id": job_id or None,
            "submission_command": command,
        }
    )
    atomic_write_json(path, value)


def submit_attempt(
    *,
    args: argparse.Namespace,
    bundle: CampaignBundle,
    tasks: list[CampaignTask],
    mode: str,
    frozen_source: Path,
    image: Path,
    image_sha: str,
    data_sha: str,
    executable: Path,
    executable_sha: str,
) -> str:
    attempt_id, attempt_dir, task_map, scan_args = write_attempt_files(
        bundle, tasks, mode, frozen_source
    )
    preset = bundle.manifest.get("study_preset")
    task_result_recorder = (
        "record_steel_module_stack_task_result.py"
        if preset == "steel-module-stack-v1"
        else "record_steel_module_task_result.py"
        if preset == "steel-module-scan-v1"
        else "record_realistic_neutron_task_result.py"
    )
    exports = {
        "SCAN_ARGS_FILE": str(scan_args),
        "RN_CAMPAIGN_HOST_DIR": str(bundle.directory),
        "RN_ATTEMPT_ID": attempt_id,
        "RN_ATTEMPT_TASKS_FILE": str(task_map),
        "RN_PLAN_HASH": bundle.plan_hash,
        "RN_GIT_COMMIT": bundle.git_commit,
        "RN_ENVIRONMENT_MODE": "osc-production",
        "RN_ENVIRONMENT_IDENTITY": bundle.environment["identity_hash"],
        "RN_IMAGE_SHA256": image_sha,
        "RN_G4_DATA_MANIFEST_SHA256": data_sha,
        "RN_EXECUTABLE_SHA256": executable_sha,
        "RN_TASK_RESULT_RECORDER": task_result_recorder,
        "G4_PROJECT_ROOT": str(frozen_source),
        "G4_DATA_ROOT": str(args.g4_data_root.expanduser().resolve()),
        "G4_APPTAINER_IMAGE": str(image),
        "G4_PREBUILT_EXECUTABLE": str(executable),
        "G4RUN_MANAGER_TYPE": "Serial",
        "PLOT_WITH_ROOT": "0",
    }
    validate_export_values(exports)
    array_spec = f"1-{len(tasks)}"
    output_pattern = attempt_dir / "slurm-%A_%a.out"
    command = [
        args.sbatch_command,
        "--parsable",
        "-A",
        args.account,
        *campaign_scheduler_args(bundle),
        "--array",
        array_spec,
        "--output",
        str(output_pattern),
        "--export",
        "ALL," + ",".join(f"{key}={value}" for key, value in exports.items()),
        str(frozen_source / "hpc/osc/submit_scan.sbatch"),
    ]
    submitted_utc = datetime.now(timezone.utc).isoformat()
    try:
        result = run_command(command, cwd=frozen_source, check=False)
    except OSError as exc:
        result = subprocess.CompletedProcess(command, 127, "", str(exc))
    raw_job_id = result.stdout.strip().split(";", 1)[0]
    status = "submitted" if result.returncode == 0 and raw_job_id.isdigit() else "submission_failed"
    job_id = raw_job_id if status == "submitted" else ""
    update_attempt_status(
        attempt_dir / "attempt.json",
        status=status,
        submitted_utc=submitted_utc,
        job_id=job_id,
        command=command,
    )
    row: dict[str, object] = {
        "attempt_id": attempt_id,
        "submitted_utc": submitted_utc,
        "mode": mode,
        "status": status,
        "campaign_id": bundle.campaign_id,
        "plan_hash": bundle.plan_hash,
        "git_commit": bundle.git_commit,
        "image_sha256": image_sha,
        "g4_data_manifest_sha256": data_sha,
        "executable_sha256": executable_sha,
        "slurm_job_id": job_id,
        "array_spec": array_spec,
        "task_count": len(tasks),
        "attempt_tasks_tsv": campaign_relative(task_map, bundle.directory),
        "scan_args_file": campaign_relative(scan_args, bundle.directory),
        "frozen_source": str(frozen_source),
    }
    append_attempt_row(bundle.directory, row)
    if status != "submitted":
        detail = result.stderr.strip() or result.stdout.strip() or "no sbatch job ID"
        raise ValueError(f"sbatch submission failed; attempt was recorded: {detail}")
    return job_id


def submission_mode(args: argparse.Namespace) -> str:
    if args.resume:
        return "resume"
    if args.retry_failed:
        return "retry-failed"
    return "submit"


def print_campaign_state(
    bundle: CampaignBundle,
    attempts_by_task: dict[str, list[tuple[dict[str, str], int]]],
    successful: set[str],
) -> None:
    attempted = sum(bool(value) for value in attempts_by_task.values())
    print(f"Campaign: {bundle.campaign_id}")
    print(f"Stage: {bundle.manifest['stage']}")
    print(f"Tasks: {len(bundle.tasks)} total, {attempted} submitted, {len(successful)} complete")
    print(
        "Environment: "
        f"{bundle.environment.get('mode')} / Geant4 {bundle.environment.get('geant4_version')}"
    )


def main() -> int:
    args = parse_args()
    if args.max_active_tasks <= 0:
        print("--max-active-tasks must be positive", file=sys.stderr)
        return 2
    project_root = args.project_root.expanduser().resolve()
    try:
        bundle = load_campaign(
            args.campaign_dir,
            verify_external_artifacts=bundle_is_production(args.campaign_dir),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot load campaign: {exc}", file=sys.stderr)
        return 1

    try:
        attempts = read_attempt_rows(bundle.directory)
        validate_attempt_journal(bundle.directory, attempts)
        attempts_by_task = submitted_task_attempts(bundle, attempts)
        successful = shallow_successful_results(bundle, attempts_by_task)
        print_campaign_state(bundle, attempts_by_task, successful)
        if args.check_only:
            if bundle.environment.get("mode") == "osc-production":
                verify_production_checkout(project_root, bundle)
                verify_production_environment(bundle)
                if args.g4_data_root is not None and not args.g4_data_root.expanduser().is_dir():
                    raise ValueError(f"missing Geant4 data root: {args.g4_data_root}")
            print("Campaign validation passed; no Slurm job was submitted.")
            return 0

        if not args.account or args.g4_data_root is None or args.frozen_root is None:
            raise ValueError(
                "submission requires --account, --g4-data-root, and --frozen-root"
            )
        if not args.g4_data_root.expanduser().is_dir():
            raise ValueError(f"missing Geant4 data root: {args.g4_data_root}")
        verify_production_checkout(project_root, bundle)
        image, image_sha, _, data_sha, executable, executable_sha = (
            verify_production_environment(bundle)
        )

        lock_path = bundle.directory / ".submission.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            attempts = read_attempt_rows(bundle.directory)
            validate_attempt_journal(bundle.directory, attempts)
            attempts_by_task = submitted_task_attempts(bundle, attempts)
            successful = shallow_successful_results(bundle, attempts_by_task)
            mode = submission_mode(args)
            selected, messages = select_tasks(
                mode,
                bundle,
                attempts_by_task,
                successful,
                project_root=project_root,
                sacct_command=args.sacct_command,
            )
            for message in messages:
                print(f"Skip: {message}")
            if not selected:
                print(f"No tasks require {mode} submission.")
                return 0
            active = active_task_count(args.squeue_command, project_root)
            if active + len(selected) > args.max_active_tasks:
                raise ValueError(
                    f"{active} active tasks + {len(selected)} selected tasks exceeds "
                    f"--max-active-tasks={args.max_active_tasks}"
                )
            frozen_source = prepare_frozen_source(
                project_root, args.frozen_root, bundle
            )
            job_id = submit_attempt(
                args=args,
                bundle=bundle,
                tasks=selected,
                mode=mode,
                frozen_source=frozen_source,
                image=image,
                image_sha=image_sha,
                data_sha=data_sha,
                executable=executable,
                executable_sha=executable_sha,
            )
        print(f"Submitted Slurm array {job_id} with {len(selected)} logical tasks.")
        print(f"Attempt manifest: {bundle.directory / ATTEMPT_MANIFEST_NAME}")
        return 0
    except (BlockingIOError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot submit campaign: {exc}", file=sys.stderr)
        return 1


def bundle_is_production(campaign_dir: Path) -> bool:
    try:
        value = load_json(campaign_dir.expanduser().resolve() / "campaign.json")
        environment = value.get("environment")
        return isinstance(environment, dict) and environment.get("mode") == "osc-production"
    except (OSError, ValueError, json.JSONDecodeError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
