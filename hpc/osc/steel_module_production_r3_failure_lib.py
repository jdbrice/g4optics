#!/usr/bin/env python3
"""Immutable evidence for the fixed failed R3 compute preflight.

This module is deliberately separate from the successful R3 probe evidence
namespace.  It never queries or mutates a scheduler and it never fabricates a
raw probe result.  Scheduler output is supplied as external, read-only input.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from steel_module_campaign_lib import (
    canonical_json,
    load_json,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    verify_recursive_checksums,
    write_recursive_checksums,
)
from steel_module_production_phase2b_lib import (
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
    ManagedExecution,
    ROOT_STATIC_MANIFEST,
    fsync_tree,
    verify_execution_static_checksums,
)


FAILURE_SCHEMA_VERSION = (
    "steel-module-production-r3-preflight-failure-evidence-v1"
)
SNAPSHOT_SCHEMA_VERSION = "steel-module-production-execution-snapshot-v1"
FORMAL_EXECUTION_ID = "sm-v1-production-bc-s1-execution-v2-50aefd35ac58"
FORMAL_EXECUTION_HASH = (
    "8352b7949657fb3161ec2e31ddf384997634ae6092f1f3e884ef95f7f22427de"
)
FORMAL_JOB_ID = "50544247"
FORMAL_JOB_NAME = "g4sm-r3-8352b7949657"
FORMAL_EXECUTION_NAME = "steel-module-production-bc-s1-execution-v2"
FORMAL_ACCOUNT = "PAS2524"
FORMAL_FAILURE_CLASSIFICATION = "pre-apptainer-python-import-failure"
FORMAL_FAILURE_STAGE = "slurm-wrapper-python-import"
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")
FORMAL_INPUT_SHA256 = {
    "held_scontrol": "4cc7a063988818176cb5fe9c37a07956785183a10076ac5d11a93e0e6822d84a",
    "sacct": "0229887554c425327f4ac02d2290291cc209619980f8ffca4e5133a56c94aef5",
    "squeue": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "slurm_output": "c825ed709aff6ab0fc9b41b7ada09a5262606d4217c59446e5218680e7b1d6b0",
}

EXPECTED_LOG_LINES = (
    "Traceback (most recent call last):",
    '  File "/var/spool/slurmd/job50544247/slurm_script", line 11, in <module>',
    "    from steel_module_production_r3_probe_lib import run_r3_container_probe",
    "ModuleNotFoundError: No module named 'steel_module_production_r3_probe_lib'",
)

FAILURE_BUNDLE_FILES = {
    "execution_snapshot.json",
    "failure.json",
    "held_scontrol.txt",
    "sacct.psv",
    "slurm-50544247.out",
    "squeue.psv",
}


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def canonical_r3_failure_evidence_root(execution_dir: Path) -> Path:
    execution = execution_dir.expanduser().resolve()
    if execution.parent.name != "campaigns":
        raise ValueError("formal R3 execution is not under the canonical campaigns root")
    return execution.parent.parent / "evidence" / "steel-module-production-r3"


def _require_regular_input(path: Path, label: str) -> Path:
    requested = path.expanduser()
    if requested.is_symlink() or not requested.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    resolved = requested.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"{label} resolved to an unsafe input")
    return resolved


def _require_empty_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
        raise ValueError(f"{label} must be an existing empty directory")


def _validate_execution_identity(
    execution_dir: Path, *, test_mode: bool
) -> tuple[Path, dict[str, Any]]:
    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("R3 failure execution path must not be a symlink")
    execution = requested.resolve()
    if execution.name != FORMAL_EXECUTION_NAME:
        raise ValueError("R3 failure evidence requires the fixed v2 successor")
    if execution.parent.name != "campaigns":
        raise ValueError("R3 failure execution is outside the canonical campaigns root")
    verify_execution_static_checksums(execution)
    manifest = load_json(execution / "managed_execution.json")
    if (
        manifest.get("schema_version") != EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1
        or manifest.get("execution_id") != FORMAL_EXECUTION_ID
        or manifest.get("execution_hash") != FORMAL_EXECUTION_HASH
        or manifest.get("test_mode") is not test_mode
        or manifest.get("accepted_statistical_evidence") is not (not test_mode)
    ):
        raise ValueError("fixed R3 failure execution identity or evidence mode mismatch")
    campaign_path = execution / "campaign.json"
    if campaign_path.exists() or campaign_path.is_symlink():
        raise ValueError("successor execution unexpectedly contains campaign.json")
    for name in ("intents", "attempts", "finalized"):
        _require_empty_directory(execution / name, f"execution {name}/")
    return execution, manifest


def _validate_execution(
    execution_dir: Path,
    *,
    repo_root: Path | None,
    test_mode: bool,
) -> tuple[Path, dict[str, Any]]:
    execution, manifest = _validate_execution_identity(
        execution_dir, test_mode=test_mode
    )
    if not test_mode:
        # A self-consistent STATIC_SHA256SUMS is not sufficient formal
        # authority.  The historical v1 predecessor records absolute Phase-1
        # and Phase-2A lock paths and needs the live Git object database, so a
        # frozen source archive is deliberately not accepted as repo_root.
        if repo_root is None:
            raise ValueError(
                "formal R3 failure validation requires the canonical live repo root"
            )
        from steel_module_production_successor_lib import load_successor_execution

        loaded = load_successor_execution(
            execution,
            repo_root=repo_root.expanduser().resolve(),
            require_readiness=False,
            verify_runtime=True,
            verify_phase2a_control_plane=True,
            verify_live_predecessor=True,
            allow_closed_v2=True,
        )
        if (
            loaded.execution_id != FORMAL_EXECUTION_ID
            or loaded.execution_hash != FORMAL_EXECUTION_HASH
            or loaded.directory != execution
        ):
            raise ValueError("formal failed-R3 successor validation disagrees")
    return execution, manifest


def _validate_preloaded_execution(
    execution_dir: Path,
    *,
    loaded: ManagedExecution,
    test_mode: bool,
) -> tuple[Path, dict[str, Any]]:
    """Bind a failure bundle to an execution already admitted by its caller.

    Live successor admission loads the predecessor with the canonical checkout
    and complete historical validation.  A frozen worker loads that same
    predecessor from its checksum-bound control archive.  Reusing the admitted
    object here avoids inventing a second repo-root policy inside bundle
    validation while still rechecking the complete current snapshot below.
    """

    execution, manifest = _validate_execution_identity(
        execution_dir, test_mode=test_mode
    )
    if (
        not isinstance(loaded, ManagedExecution)
        or loaded.directory != execution
        or loaded.execution_id != FORMAL_EXECUTION_ID
        or loaded.execution_hash != FORMAL_EXECUTION_HASH
        or loaded.manifest != manifest
    ):
        raise ValueError("preloaded failed-R3 successor identity disagrees")
    return execution, manifest


def _snapshot_execution(execution: Path) -> dict[str, Any]:
    """Capture regular files and directories, including empty directories."""

    records: list[dict[str, Any]] = []
    root_lstat = execution.lstat()
    if not stat.S_ISDIR(root_lstat.st_mode):
        raise ValueError("execution snapshot root is not a directory")
    records.append(
        {
            "path": ".",
            "type": "directory",
            "mode": stat.S_IMODE(root_lstat.st_mode),
        }
    )
    for current, directories, files in os.walk(execution, followlinks=False):
        current_path = Path(current)
        directories.sort()
        files.sort()
        for name in directories:
            path = current_path / name
            relative = path.relative_to(execution).as_posix()
            info = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"unsafe execution snapshot directory: {relative}")
            records.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(info.st_mode),
                }
            )
        for name in files:
            path = current_path / name
            relative = path.relative_to(execution).as_posix()
            info = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(info.st_mode):
                raise ValueError(f"unsafe execution snapshot file: {relative}")
            records.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": stat.S_IMODE(info.st_mode),
                    "size_bytes": info.st_size,
                    "sha256": sha256_file(path),
                }
            )
    records.sort(key=lambda row: (str(row["path"]), str(row["type"])))
    snapshot: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "execution_directory": str(execution),
        "records": records,
    }
    snapshot["snapshot_hash"] = _canonical_hash(snapshot)
    return snapshot


def _validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if set(snapshot) != {
        "schema_version",
        "execution_directory",
        "records",
        "snapshot_hash",
    }:
        raise ValueError("execution snapshot field set mismatch")
    recorded_hash = snapshot.get("snapshot_hash")
    unhashed = dict(snapshot)
    unhashed.pop("snapshot_hash")
    if (
        snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(snapshot.get("execution_directory"), str)
        or not isinstance(snapshot.get("records"), list)
        or recorded_hash != _canonical_hash(unhashed)
    ):
        raise ValueError("execution snapshot semantic hash mismatch")
    seen: set[str] = set()
    for record in snapshot["records"]:
        if not isinstance(record, dict):
            raise ValueError("execution snapshot record is not an object")
        kind = record.get("type")
        expected = {"path", "type", "mode"}
        if kind == "file":
            expected |= {"size_bytes", "sha256"}
        raw_path = record.get("path")
        if isinstance(raw_path, str):
            relative = Path(raw_path)
            path_is_safe = (
                raw_path == "."
                or (
                    raw_path
                    and not relative.is_absolute()
                    and ".." not in relative.parts
                    and relative.as_posix() == raw_path
                    and raw_path != "."
                )
            )
        else:
            path_is_safe = False
        if (
            kind not in {"file", "directory"}
            or set(record) != expected
            or not path_is_safe
            or raw_path in seen
            or not isinstance(record.get("mode"), int)
            or not 0 <= record["mode"] <= 0o7777
        ):
            raise ValueError("execution snapshot record is invalid")
        if kind == "file" and (
            not isinstance(record.get("size_bytes"), int)
            or record["size_bytes"] < 0
            or not isinstance(record.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
        ):
            raise ValueError("execution snapshot file record is invalid")
        seen.add(str(raw_path))
    if (
        not snapshot["records"]
        or snapshot["records"][0].get("path") != "."
        or snapshot["records"][0].get("type") != "directory"
        or snapshot["records"]
        != sorted(
            snapshot["records"],
            key=lambda row: (str(row["path"]), str(row["type"])),
        )
    ):
        raise ValueError("execution snapshot records are not canonical")
    if "." not in seen:
        raise ValueError("execution snapshot lacks its root record")
    kinds = {str(record["path"]): record["type"] for record in snapshot["records"]}
    for raw_path in kinds:
        if raw_path == ".":
            continue
        parent = PurePosixPath(raw_path).parent.as_posix()
        if kinds.get(parent) != "directory":
            raise ValueError("execution snapshot record lacks a parent directory")
    return snapshot


def _parse_scontrol(
    text: str, *, execution: Path, r3_root: Path
) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("failed R3 held evidence must contain one scontrol row")
    fields: dict[str, str] = {}
    for token in lines[0].split():
        if "=" not in token:
            continue
        name, value = token.split("=", 1)
        if name in fields:
            raise ValueError("failed R3 scontrol evidence contains duplicate fields")
        fields[name] = value
    required = {
        "JobId",
        "JobName",
        "Account",
        "JobState",
        "Reason",
        "Requeue",
        "Command",
        "WorkDir",
        "StdOut",
    }
    expected = {
        "JobId": FORMAL_JOB_ID,
        "JobName": FORMAL_JOB_NAME,
        "JobState": "PENDING",
        "Reason": "JobHeldUser",
        "Requeue": "0",
        "Command": str(
            execution
            / "sources/control/hpc/osc/"
            "run_steel_module_production_r3_container_probe.py"
        ),
        "WorkDir": str(execution),
        "StdOut": str(r3_root / f"slurm-{FORMAL_JOB_ID}.out"),
    }
    if (
        not required <= set(fields)
        or fields.get("Account", "").casefold() != FORMAL_ACCOUNT.casefold()
        or any(fields.get(key) != value for key, value in expected.items())
        or fields.get("ArrayJobId") not in {None, "", "N/A"}
        or fields.get("ArrayTaskId") not in {None, "", "N/A"}
    ):
        raise ValueError("failed R3 held-job scheduler identity mismatch")
    return {
        "job_id": FORMAL_JOB_ID,
        "job_name": FORMAL_JOB_NAME,
        "account": fields["Account"],
        "state": "PENDING",
        "reason": "JobHeldUser",
        "requeue": "0",
        "non_array": True,
        "command": fields["Command"],
        "work_dir": fields["WorkDir"],
        "stdout": fields["StdOut"],
    }


def _parse_sacct(text: str) -> list[dict[str, str]]:
    names = ("job_id", "job_name", "account", "state", "exit_code", "elapsed_raw")
    rows: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        values = raw.split("|")
        if len(values) != len(names):
            raise ValueError(f"invalid failed R3 sacct row {number}: {raw!r}")
        rows.append(dict(zip(names, values)))
    expected = (
        (FORMAL_JOB_ID, FORMAL_JOB_NAME, "FAILED", "1:0"),
        (f"{FORMAL_JOB_ID}.batch", "batch", "FAILED", "1:0"),
        (f"{FORMAL_JOB_ID}.extern", "extern", "COMPLETED", "0:0"),
    )
    if len(rows) != len(expected):
        raise ValueError("failed R3 accounting must contain parent, batch, and extern only")
    for row, (job_id, job_name, state, exit_code) in zip(rows, expected):
        if (
            row["job_id"] != job_id
            or row["job_name"] != job_name
            or row["account"].casefold() != FORMAL_ACCOUNT.casefold()
            or row["state"] != state
            or row["exit_code"] != exit_code
            or not row["elapsed_raw"].isdigit()
        ):
            raise ValueError("failed R3 terminal accounting identity mismatch")
    return rows


def _validate_failure_log(text: str) -> None:
    if tuple(text.splitlines()) != EXPECTED_LOG_LINES:
        raise ValueError("failed R3 log is not the exact pre-Apptainer import failure")


def _failure_hash(value: Mapping[str, Any]) -> str:
    unhashed = dict(value)
    unhashed.pop("failure_id", None)
    unhashed.pop("failure_hash", None)
    return _canonical_hash(unhashed)


def collect_r3_failure_evidence(
    *,
    execution_dir: Path,
    held_scontrol_input: Path,
    sacct_input: Path,
    squeue_input: Path,
    slurm_output_input: Path,
    repo_root: Path | None = None,
    test_mode: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate external evidence and return deterministic payload + snapshot."""

    execution, manifest = _validate_execution(
        execution_dir, repo_root=repo_root, test_mode=test_mode
    )
    r3_root = canonical_r3_failure_evidence_root(execution)
    if r3_root.is_symlink() or not r3_root.is_dir():
        raise ValueError("canonical R3 evidence root is missing or unsafe")
    raw_root = r3_root / "raw"
    _require_empty_directory(raw_root, "R3 raw root")
    expected_raw = raw_root / FORMAL_JOB_NAME
    if expected_raw.exists() or expected_raw.is_symlink():
        raise ValueError("failed R3 unexpectedly created a raw probe workspace")

    inputs = {
        "held_scontrol": _require_regular_input(held_scontrol_input, "held scontrol input"),
        "sacct": _require_regular_input(sacct_input, "sacct input"),
        "squeue": _require_regular_input(squeue_input, "squeue input"),
        "slurm_output": _require_regular_input(slurm_output_input, "Slurm output input"),
    }
    if len(set(inputs.values())) != len(inputs):
        raise ValueError("failed R3 inputs must be distinct regular files")
    if not test_mode:
        actual_input_hashes = {
            name: sha256_file(path) for name, path in inputs.items()
        }
        if actual_input_hashes != FORMAL_INPUT_SHA256:
            raise ValueError("failed R3 formal input SHA-256 identity mismatch")
    expected_slurm_output = r3_root / f"slurm-{FORMAL_JOB_ID}.out"
    if inputs["slurm_output"] != expected_slurm_output:
        raise ValueError("failed R3 Slurm output input is not at the scheduler-recorded path")

    before = _snapshot_execution(execution)
    held_text = inputs["held_scontrol"].read_text(encoding="utf-8")
    sacct_text = inputs["sacct"].read_text(encoding="utf-8")
    squeue_text = inputs["squeue"].read_text(encoding="utf-8")
    log_text = inputs["slurm_output"].read_text(encoding="utf-8")
    held = _parse_scontrol(held_text, execution=execution, r3_root=r3_root)
    rows = _parse_sacct(sacct_text)
    if squeue_text.strip():
        raise ValueError("failed R3 job remains active in supplied squeue evidence")
    _validate_failure_log(log_text)
    after = _snapshot_execution(execution)
    if before != after:
        raise ValueError("successor execution changed while collecting R3 failure evidence")

    payload: dict[str, Any] = {
        "schema_version": FAILURE_SCHEMA_VERSION,
        "test_mode": test_mode,
        "accepted_compute_preflight_evidence": False,
        "classification": FORMAL_FAILURE_CLASSIFICATION,
        "execution": {
            "directory": str(execution),
            "execution_id": FORMAL_EXECUTION_ID,
            "execution_hash": FORMAL_EXECUTION_HASH,
            "managed_execution_sha256": sha256_file(execution / "managed_execution.json"),
            "static_manifest_sha256": sha256_file(execution / ROOT_STATIC_MANIFEST),
            "snapshot_hash": before["snapshot_hash"],
            "snapshot_record_count": len(before["records"]),
        },
        "scheduler": {
            "job_id": FORMAL_JOB_ID,
            "job_name": FORMAL_JOB_NAME,
            "account": held["account"],
            "held_identity": held,
            "terminal_rows": rows,
            "squeue_terminal_empty": True,
        },
        "runtime_boundary": {
            "failure_stage": FORMAL_FAILURE_STAGE,
            "python_exception": EXPECTED_LOG_LINES[-1],
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "raw_probe_result_created": False,
        },
        "mutable_execution_roots": {
            "intents_empty": True,
            "attempts_empty": True,
            "finalized_empty": True,
        },
        "raw_workspace": {
            "path": str(expected_raw),
            "exists": False,
            "raw_root_empty": True,
        },
        "consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "inputs": {
            "held_scontrol_sha256": sha256_file(inputs["held_scontrol"]),
            "sacct_sha256": sha256_file(inputs["sacct"]),
            "squeue_sha256": sha256_file(inputs["squeue"]),
            "slurm_output_sha256": sha256_file(inputs["slurm_output"]),
        },
        "scheduler_contact_performed_by_tool": False,
        "simulation_started": False,
    }
    payload["failure_hash"] = _failure_hash(payload)
    payload["failure_id"] = (
        f"sm-v1-r3-preflight-failure-{FORMAL_JOB_ID}-{payload['failure_hash'][:12]}"
    )
    return payload, before


def _validate_failure_payload(payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "classification",
        "execution",
        "scheduler",
        "runtime_boundary",
        "mutable_execution_roots",
        "raw_workspace",
        "consumption",
        "inputs",
        "scheduler_contact_performed_by_tool",
        "simulation_started",
        "failure_hash",
        "failure_id",
    }
    if set(payload) != expected_keys:
        raise ValueError("R3 failure payload field set mismatch")
    failure_hash = _failure_hash(payload)
    execution = payload.get("execution")
    scheduler = payload.get("scheduler")
    runtime = payload.get("runtime_boundary")
    mutable = payload.get("mutable_execution_roots")
    raw = payload.get("raw_workspace")
    consumption = payload.get("consumption")
    inputs = payload.get("inputs")
    if not all(
        isinstance(value, dict)
        for value in (execution, scheduler, runtime, mutable, raw, consumption, inputs)
    ):
        raise ValueError("R3 failure nested payload is invalid")
    nested_keys = (
        (
            execution,
            {
                "directory",
                "execution_id",
                "execution_hash",
                "managed_execution_sha256",
                "static_manifest_sha256",
                "snapshot_hash",
                "snapshot_record_count",
            },
            "execution",
        ),
        (
            scheduler,
            {
                "job_id",
                "job_name",
                "account",
                "held_identity",
                "terminal_rows",
                "squeue_terminal_empty",
            },
            "scheduler",
        ),
        (
            runtime,
            {
                "failure_stage",
                "python_exception",
                "apptainer_invoked",
                "geant4_invoked",
                "raw_probe_result_created",
            },
            "runtime boundary",
        ),
        (
            mutable,
            {"intents_empty", "attempts_empty", "finalized_empty"},
            "mutable roots",
        ),
        (raw, {"path", "exists", "raw_root_empty"}, "raw workspace"),
        (
            consumption,
            {"events_consumed", "production_seeds_consumed"},
            "consumption",
        ),
        (
            inputs,
            {
                "held_scontrol_sha256",
                "sacct_sha256",
                "squeue_sha256",
                "slurm_output_sha256",
            },
            "inputs",
        ),
    )
    for value, expected, label in nested_keys:
        if set(value) != expected:
            raise ValueError(f"R3 failure {label} field set mismatch")
    if (
        payload.get("schema_version") != FAILURE_SCHEMA_VERSION
        or not isinstance(payload.get("test_mode"), bool)
        or payload.get("accepted_compute_preflight_evidence") is not False
        or payload.get("classification") != FORMAL_FAILURE_CLASSIFICATION
        or payload.get("scheduler_contact_performed_by_tool") is not False
        or payload.get("simulation_started") is not False
        or payload.get("failure_hash") != failure_hash
        or payload.get("failure_id")
        != f"sm-v1-r3-preflight-failure-{FORMAL_JOB_ID}-{failure_hash[:12]}"
        or execution.get("execution_id") != FORMAL_EXECUTION_ID
        or execution.get("execution_hash") != FORMAL_EXECUTION_HASH
        or execution.get("directory") != snapshot.get("execution_directory")
        or execution.get("snapshot_hash") != snapshot.get("snapshot_hash")
        or execution.get("snapshot_record_count") != len(snapshot.get("records", []))
        or scheduler.get("job_id") != FORMAL_JOB_ID
        or scheduler.get("job_name") != FORMAL_JOB_NAME
        or scheduler.get("squeue_terminal_empty") is not True
        or runtime
        != {
            "failure_stage": FORMAL_FAILURE_STAGE,
            "python_exception": EXPECTED_LOG_LINES[-1],
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "raw_probe_result_created": False,
        }
        or mutable
        != {
            "intents_empty": True,
            "attempts_empty": True,
            "finalized_empty": True,
        }
        or raw.get("exists") is not False
        or raw.get("raw_root_empty") is not True
        or consumption
        != {"events_consumed": 0, "production_seeds_consumed": 0}
        or set(inputs)
        != {
            "held_scontrol_sha256",
            "sacct_sha256",
            "squeue_sha256",
            "slurm_output_sha256",
        }
    ):
        raise ValueError("R3 failure payload semantic validation failed")


def seal_r3_failure_evidence(
    *,
    execution_dir: Path,
    held_scontrol_input: Path,
    sacct_input: Path,
    squeue_input: Path,
    slurm_output_input: Path,
    repo_root: Path | None = None,
    test_mode: bool = False,
) -> Path:
    payload, snapshot = collect_r3_failure_evidence(
        execution_dir=execution_dir,
        held_scontrol_input=held_scontrol_input,
        sacct_input=sacct_input,
        squeue_input=squeue_input,
        slurm_output_input=slurm_output_input,
        repo_root=repo_root,
        test_mode=test_mode,
    )
    execution = Path(payload["execution"]["directory"])
    failures = canonical_r3_failure_evidence_root(execution) / "failures"
    if failures.is_symlink():
        raise ValueError("R3 failure output root must not be a symlink")
    if not failures.exists():
        failures.mkdir(mode=0o700)
    if not failures.is_dir():
        raise ValueError("R3 failure output root is not a directory")
    target = failures / f"r3-preflight-{FORMAL_JOB_ID}-{payload['failure_hash'][:12]}"
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite R3 failure bundle: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=failures))
    try:
        source_paths = {
            "held_scontrol.txt": held_scontrol_input,
            "sacct.psv": sacct_input,
            "squeue.psv": squeue_input,
            f"slurm-{FORMAL_JOB_ID}.out": slurm_output_input,
        }
        for name, source in source_paths.items():
            shutil.copyfile(_require_regular_input(Path(source), name), temporary / name)
        (temporary / "execution_snapshot.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "failure.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_recursive_checksums(temporary)
        for path in temporary.iterdir():
            path.chmod(0o444)
        validate_r3_failure_bundle(
            temporary,
            execution_dir=execution,
            require_current_execution=True,
            allow_test_mode=test_mode,
            require_canonical_location=False,
            repo_root=repo_root,
        )
        fsync_tree(temporary)
        temporary.chmod(0o555)
        publish_directory_no_replace(temporary, target)
        temporary = None
        validate_r3_failure_bundle(
            target,
            execution_dir=execution,
            require_current_execution=True,
            allow_test_mode=test_mode,
            repo_root=repo_root,
        )
        return target
    finally:
        if temporary is not None and temporary.exists():
            temporary.chmod(0o700)
            shutil.rmtree(temporary)


def validate_r3_failure_bundle(
    bundle_dir: Path,
    *,
    execution_dir: Path | None = None,
    require_current_execution: bool = False,
    allow_test_mode: bool = False,
    require_canonical_location: bool = True,
    repo_root: Path | None = None,
    preloaded_execution: ManagedExecution | None = None,
) -> dict[str, Any]:
    """Validate a sealed failure bundle without scheduler contact."""

    requested = bundle_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink() or not requested.is_dir():
        raise ValueError("R3 failure bundle must be a regular directory")
    bundle = requested.resolve()
    records = verify_recursive_checksums(bundle, required=FAILURE_BUNDLE_FILES)
    if set(records) != FAILURE_BUNDLE_FILES:
        raise ValueError("R3 failure bundle file set mismatch")
    payload = load_json(bundle / "failure.json")
    snapshot = _validate_snapshot(load_json(bundle / "execution_snapshot.json"))
    _validate_failure_payload(payload, snapshot)
    test_mode = payload["test_mode"]
    if test_mode and not allow_test_mode:
        raise ValueError("test R3 failure evidence is not formal evidence")
    execution = Path(payload["execution"]["directory"])
    if execution_dir is not None and execution != execution_dir.expanduser().resolve():
        raise ValueError("R3 failure bundle execution directory mismatch")
    failures = canonical_r3_failure_evidence_root(execution) / "failures"
    expected = failures / f"r3-preflight-{FORMAL_JOB_ID}-{payload['failure_hash'][:12]}"
    if require_canonical_location and bundle != expected:
        raise ValueError("R3 failure bundle is outside its canonical content address")
    if require_canonical_location and (
        bundle.stat().st_mode & 0o222
        or any(path.stat().st_mode & 0o222 for path in bundle.iterdir())
    ):
        raise ValueError("canonical R3 failure bundle is unexpectedly writable")

    texts = {
        "held": (bundle / "held_scontrol.txt").read_text(encoding="utf-8"),
        "sacct": (bundle / "sacct.psv").read_text(encoding="utf-8"),
        "squeue": (bundle / "squeue.psv").read_text(encoding="utf-8"),
        "log": (bundle / f"slurm-{FORMAL_JOB_ID}.out").read_text(encoding="utf-8"),
    }
    held = _parse_scontrol(
        texts["held"],
        execution=execution,
        r3_root=canonical_r3_failure_evidence_root(execution),
    )
    rows = _parse_sacct(texts["sacct"])
    if texts["squeue"].strip():
        raise ValueError("sealed failed R3 squeue evidence is not empty")
    _validate_failure_log(texts["log"])
    actual_input_hashes = {
        "held_scontrol": sha256_file(bundle / "held_scontrol.txt"),
        "sacct": sha256_file(bundle / "sacct.psv"),
        "squeue": sha256_file(bundle / "squeue.psv"),
        "slurm_output": sha256_file(bundle / f"slurm-{FORMAL_JOB_ID}.out"),
    }
    if not test_mode and actual_input_hashes != FORMAL_INPUT_SHA256:
        raise ValueError("sealed formal R3 input SHA-256 identity mismatch")
    snapshot_files = {
        record["path"]: record
        for record in snapshot["records"]
        if record.get("type") == "file"
    }
    if (
        snapshot_files.get("managed_execution.json", {}).get("sha256")
        != payload["execution"].get("managed_execution_sha256")
        or snapshot_files.get(ROOT_STATIC_MANIFEST, {}).get("sha256")
        != payload["execution"].get("static_manifest_sha256")
    ):
        raise ValueError("sealed R3 execution snapshot identity mismatch")
    if (
        payload["scheduler"].get("held_identity") != held
        or payload["scheduler"].get("terminal_rows") != rows
        or payload["scheduler"].get("account") != held.get("account")
        or payload["raw_workspace"].get("path")
        != str(
            canonical_r3_failure_evidence_root(execution)
            / "raw"
            / FORMAL_JOB_NAME
        )
        or payload["inputs"]
        != {
            "held_scontrol_sha256": actual_input_hashes["held_scontrol"],
            "sacct_sha256": actual_input_hashes["sacct"],
            "squeue_sha256": actual_input_hashes["squeue"],
            "slurm_output_sha256": actual_input_hashes["slurm_output"],
        }
    ):
        raise ValueError("sealed R3 failure source identity mismatch")
    if preloaded_execution is not None and not require_current_execution:
        raise ValueError(
            "preloaded execution requires current-execution validation"
        )
    if preloaded_execution is not None and repo_root is not None:
        raise ValueError(
            "preloaded execution and repo-root validation are mutually exclusive"
        )
    if require_current_execution:
        if preloaded_execution is None:
            current, manifest = _validate_execution(
                execution, repo_root=repo_root, test_mode=test_mode
            )
        else:
            current, manifest = _validate_preloaded_execution(
                execution, loaded=preloaded_execution, test_mode=test_mode
            )
        current_snapshot = _snapshot_execution(current)
        if current_snapshot != snapshot:
            raise ValueError("successor execution changed since failed R3 evidence capture")
        if (
            payload["execution"].get("managed_execution_sha256")
            != sha256_file(current / "managed_execution.json")
            or payload["execution"].get("static_manifest_sha256")
            != sha256_file(current / ROOT_STATIC_MANIFEST)
            or manifest.get("execution_id") != FORMAL_EXECUTION_ID
        ):
            raise ValueError("current successor execution identity mismatch")
        raw_root = canonical_r3_failure_evidence_root(current) / "raw"
        if raw_root.is_symlink() or not raw_root.is_dir():
            raise ValueError("current R3 raw root is missing or unsafe")
        failed_workspace = raw_root / FORMAL_JOB_NAME
        if failed_workspace.exists() or failed_workspace.is_symlink():
            raise ValueError("failed R3 unexpectedly acquired a raw workspace")
    return payload


__all__ = [
    "EXPECTED_LOG_LINES",
    "FAILURE_SCHEMA_VERSION",
    "FORMAL_ACCOUNT",
    "FORMAL_EXECUTION_HASH",
    "FORMAL_EXECUTION_ID",
    "FORMAL_JOB_ID",
    "FORMAL_JOB_NAME",
    "collect_r3_failure_evidence",
    "seal_r3_failure_evidence",
    "validate_r3_failure_bundle",
]
