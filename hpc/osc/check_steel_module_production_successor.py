#!/usr/bin/env python3
"""No-scheduler adversarial checks for the incident-bound BC-S1 successor.

The fixture deliberately seals a complete synthetic predecessor incident before
materializing the successor.  Synthetic evidence is never accepted statistical
evidence, but it has the same 32-task/8,000-event/64-seed shape and the same
cross-file accounting and event-chain contracts as the formal OSC incident.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from argparse import Namespace
from contextlib import contextmanager
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import seal_steel_module_production_phase2b_incident as incident_lib
import steel_module_production_phase2b_lib as phase2b_lib
import steel_module_production_r3_failure_lib as failure_lib
import steel_module_production_successor_lib as successor_lib
import validate_steel_module_production_successor_execution as v2_validator_cli
import validate_steel_module_production_successor_v3_execution as v3_validator_cli
from check_steel_module_production_phase2b_control import (
    FakeSlurm,
    _fixture_sources,
    _prepare,
    make_execution,
)
from generate_steel_module_production_successor_readiness import (
    FORMAL_PROPOSAL_NAME,
    validate_proposal_target,
)
from manage_steel_module_production_attempt import submit_intent
from seal_steel_module_production_phase2b_incident import (
    EXPECTED_FAILURE_LINE,
    publish_incident_bundle,
    validate_incident_bundle,
)
from steel_module_campaign_lib import canonical_json, sha256_bytes, sha256_file
from steel_module_production_checkpoint_lib import write_recursive_checksums
from steel_module_production_phase2b_lib import prepare_attempt_intent
from steel_module_production_program_lib import (
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)
from steel_module_production_r3_failure_lib import (
    EXPECTED_LOG_LINES as R3_FAILURE_LOG_LINES,
    FORMAL_JOB_ID as R3_FAILURE_JOB_ID,
    FORMAL_JOB_NAME as R3_FAILURE_JOB_NAME,
    seal_r3_failure_evidence,
)
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME,
    FORMAL_SUCCESSOR_EXECUTION_NAME_V2,
    FORMAL_SUCCESSOR_EXECUTION_NAME_V3,
    RECOVERY_READINESS_LOCK_RELATIVE,
    RECOVERY_READINESS_LOCK_RELATIVE_V2,
    RECOVERY_READINESS_LOCK_RELATIVE_V3,
    SUCCESSOR_EXECUTION_GENERATION,
    SUCCESSOR_EXECUTION_GENERATION_V2,
    SUCCESSOR_EXECUTION_GENERATION_V3,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3,
    SUCCESSOR_OBJECT_KIND,
    build_successor_recovery_readiness_candidate,
    load_execution_for_frozen_worker,
    load_successor_execution,
    materialize_successor_execution_companion,
    materialize_successor_v3_execution_companion,
    preview_successor_execution,
    preview_successor_v3_execution,
    successor_predecessor_retry_task_ids,
    verify_successor_recovery_readiness,
)


FIXTURE_READINESS = {
    "path": "/fixture/readiness.json",
    "sha256": "a" * 64,
    "schema_version": "fixture-readiness-v1",
    "status": "accepted-fixture",
    "implementation_commit": "f" * 40,
    "readiness_commit": "e" * 40,
}


def _tree_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Capture content and filesystem identity without following symlinks."""

    records: dict[str, dict[str, Any]] = {}
    paths = [root, *sorted(root.rglob("*"), key=lambda value: value.as_posix())]
    for path in paths:
        value = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        record: dict[str, Any] = {
            "mode": stat.S_IMODE(value.st_mode),
            "link_count": value.st_nlink,
            "size_bytes": value.st_size,
        }
        if stat.S_ISREG(value.st_mode):
            record.update(kind="file", sha256=sha256_file(path))
        elif stat.S_ISDIR(value.st_mode):
            record["kind"] = "directory"
        elif stat.S_ISLNK(value.st_mode):
            record.update(kind="symlink", target=os.readlink(path))
        else:
            record["kind"] = "other"
        records[relative] = record
    return records


def _contains_exact(value: object, expected: object) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains_exact(child, expected) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact(child, expected) for child in value)
    return False


def _make_sealed_incident_fixture(
    repo_root: Path, scratch: Path
) -> tuple[object, Path, dict[str, Any]]:
    predecessor = make_execution(repo_root, scratch, "successor-predecessor")
    intent = _prepare(predecessor, "20260719T120000Z-initial")
    fake = FakeSlurm(
        predecessor, str(intent["attempt_id"]), mode="all-failed-accounting"
    )
    submit_intent(
        predecessor,
        attempt_id=str(intent["attempt_id"]),
        intent_sha256=str(intent["intent_sha256"]),
        actor="successor-fixture-reviewer",
        runner=fake,
    )
    attempt_dir = predecessor.directory / "attempts" / str(intent["attempt_id"])
    for index in range(1, 33):
        (attempt_dir / f"slurm-{fake.job_id}_{index}.out").write_text(
            EXPECTED_FAILURE_LINE + "\n", encoding="utf-8"
        )
    incident = publish_incident_bundle(
        predecessor,
        repo_root=repo_root,
        out_parent=scratch / "successor-incidents",
        attempt_id=str(intent["attempt_id"]),
        intent_sha256=str(intent["intent_sha256"]),
        actor="successor-fixture-reviewer",
        runner=fake,
        historical_readiness=FIXTURE_READINESS,
    )
    value = validate_incident_bundle(incident, execution=predecessor)
    assert value["accepted_incident_evidence"] is False
    assert value["simulation_disposition"]["events_consumed"] == 0
    assert value["simulation_disposition"]["production_seeds_consumed"] == 0
    assert value["scheduler"]["states"] == {"FAILED": 32}
    assert value["scheduler"]["exit_codes"] == {"1:0": 32}
    return predecessor, incident, value


def _successor_inputs(
    repo_root: Path,
    scratch: Path,
    predecessor: object,
    incident: Path,
) -> dict[str, Any]:
    source_root = scratch / "successor-source-inputs"
    source_root.mkdir()
    return {
        "repo_root": repo_root,
        "managed_child_dir": predecessor.managed_child.directory,
        "predecessor_dir": predecessor.directory,
        "incident_dir": incident,
        "pilot_dir": Path(predecessor.manifest["sealed_pilot"]["directory"]),
        "test_mode": True,
        "fixture_sources": _fixture_sources(source_root),
    }


@contextmanager
def _fixture_r3_failure_identity(execution: object):
    original_id = failure_lib.FORMAL_EXECUTION_ID
    original_hash = failure_lib.FORMAL_EXECUTION_HASH
    failure_lib.FORMAL_EXECUTION_ID = execution.execution_id
    failure_lib.FORMAL_EXECUTION_HASH = execution.execution_hash
    try:
        yield
    finally:
        failure_lib.FORMAL_EXECUTION_ID = original_id
        failure_lib.FORMAL_EXECUTION_HASH = original_hash


def _seal_failed_r3_fixture(execution: object, scratch: Path) -> Path:
    r3 = execution.directory.parent.parent / "evidence/steel-module-production-r3"
    raw = r3 / "raw"
    raw.mkdir(parents=True)
    inputs = scratch / "r3-failure-inputs"
    inputs.mkdir()
    held = inputs / "held.txt"
    sacct = inputs / "sacct.psv"
    squeue = inputs / "squeue.psv"
    log = r3 / f"slurm-{R3_FAILURE_JOB_ID}.out"
    command = (
        execution.directory
        / "sources/control/hpc/osc/"
        "run_steel_module_production_r3_container_probe.py"
    )
    held.write_text(
        " ".join(
            (
                f"JobId={R3_FAILURE_JOB_ID}",
                f"JobName={R3_FAILURE_JOB_NAME}",
                "Account=pas2524",
                "JobState=PENDING",
                "Reason=JobHeldUser",
                "Requeue=0",
                f"Command={command}",
                f"WorkDir={execution.directory}",
                f"StdOut={log}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    sacct.write_text(
        "\n".join(
            (
                f"{R3_FAILURE_JOB_ID}|{R3_FAILURE_JOB_NAME}|pas2524|FAILED|1:0|1",
                f"{R3_FAILURE_JOB_ID}.batch|batch|pas2524|FAILED|1:0|1",
                f"{R3_FAILURE_JOB_ID}.extern|extern|pas2524|COMPLETED|0:0|1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    squeue.write_text("", encoding="utf-8")
    log.write_text("\n".join(R3_FAILURE_LOG_LINES) + "\n", encoding="utf-8")
    with _fixture_r3_failure_identity(execution):
        return seal_r3_failure_evidence(
            execution_dir=execution.directory,
            held_scontrol_input=held,
            sacct_input=sacct,
            squeue_input=squeue,
            slurm_output_input=log,
            test_mode=True,
        )


def _make_v3_fixture(
    repo_root: Path, scratch: Path
) -> tuple[object, object, Path, dict[str, object]]:
    predecessor_v1, incident, _ = _make_sealed_incident_fixture(repo_root, scratch)
    v2_inputs = _successor_inputs(
        repo_root, scratch, predecessor_v1, incident
    )
    work_campaigns = scratch / "work/campaigns"
    work_campaigns.mkdir(parents=True)
    v2_target = work_campaigns / FORMAL_SUCCESSOR_EXECUTION_NAME_V2
    v2 = materialize_successor_execution_companion(
        **v2_inputs, out_dir=v2_target
    )
    with _fixture_r3_failure_identity(v2):
        failure = _seal_failed_r3_fixture(v2, scratch)
    source_root = scratch / "successor-v3-source-inputs"
    simulation = v2_inputs["fixture_sources"][0]
    control = source_root / "control-source"
    shutil.copytree(v2_inputs["fixture_sources"][1], control)
    launcher = control / "hpc/osc/run_steel_module_production_r3_probe.sbatch"
    launcher.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    (control / "successor-v3-marker.txt").write_text(
        "updated control plane\n", encoding="utf-8"
    )
    inputs: dict[str, object] = {
        "repo_root": repo_root,
        "managed_child_dir": v2.managed_child.directory,
        "predecessor_dir": v2.directory,
        "failure_dir": failure,
        "pilot_dir": Path(v2.manifest["sealed_pilot"]["directory"]),
        "test_mode": True,
        "fixture_sources": (simulation, control),
    }
    return predecessor_v1, v2, failure, inputs


def _scheduler_sentinels(
    scratch: Path,
) -> tuple[Path, dict[str, Path], str]:
    sentinel_bin = scratch / "successor-sentinel-bin"
    sentinel_bin.mkdir()
    markers: dict[str, Path] = {}
    for command in ("sbatch", "scontrol", "squeue", "sacct"):
        marker = scratch / f"forbidden-{command}-was-invoked"
        executable = sentinel_bin / command
        executable.write_text(
            "#!/usr/bin/env bash\n"
            f"printf invoked > {str(marker)!r}\n"
            "exit 97\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        markers[command] = marker
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(sentinel_bin) + os.pathsep + original_path
    return sentinel_bin, markers, original_path


def _assert_no_scheduler_contact(markers: dict[str, Path]) -> None:
    invoked = sorted(name for name, marker in markers.items() if marker.exists())
    assert not invoked, f"successor R2 contacted scheduler commands: {invoked}"


def _assert_successor_identity(
    successor: object,
    predecessor: object,
    incident_value: dict[str, Any],
) -> None:
    manifest = successor.manifest
    assert manifest["schema_version"] == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2
    assert manifest["execution_generation"] == SUCCESSOR_EXECUTION_GENERATION_V2
    assert manifest["object_kind"] == SUCCESSOR_OBJECT_KIND
    assert manifest["test_mode"] is True
    assert manifest["accepted_statistical_evidence"] is False
    assert successor.execution_id != predecessor.execution_id
    assert successor.execution_hash != predecessor.execution_hash
    assert manifest["submission_policy"]["formal_initial_mode"] == (
        "predecessor-retry"
    )
    assert manifest["readiness_gate"]["tracked_path"] == (
        RECOVERY_READINESS_LOCK_RELATIVE_V2.as_posix()
    )
    control_lock = manifest["artifacts"]["control_lock"]
    assert control_lock["protocol"] == phase2b_lib.CONTROL_LOCK_PROTOCOL_V2
    assert control_lock["content_token"] != predecessor.manifest["artifacts"][
        "control_lock"
    ].get("content_token")

    expected_authority = (
        predecessor.execution_id,
        predecessor.execution_hash,
        incident_value["incident_id"],
        incident_value["incident_hash"],
        incident_value["attempt"]["attempt_id"],
        incident_value["attempt"]["intent_sha256"],
        incident_value["attempt"]["job_id"],
        incident_value["attempt"]["event_chain_sha256"],
        incident_value["attempt"]["accounting_manifest_sha256"],
        incident_value["historical_readiness"]["sha256"],
    )
    for expected in expected_authority:
        assert _contains_exact(manifest, expected), (
            f"successor manifest omits predecessor authority {expected!r}"
        )


def _assert_exact_task_seed_reuse(successor: object, predecessor: object) -> None:
    parent_tasks = predecessor.managed_child.plan.tasks
    assert successor.tasks == predecessor.tasks == parent_tasks
    assert successor.scan_args == predecessor.scan_args
    assert (successor.directory / "tasks.tsv").read_bytes() == (
        predecessor.directory / "tasks.tsv"
    ).read_bytes()
    assert (successor.directory / "scan_args.txt").read_bytes() == (
        predecessor.directory / "scan_args.txt"
    ).read_bytes()
    seeds = tuple(seed for task in successor.tasks for seed in (task.seed1, task.seed2))
    parent_seeds = tuple(
        seed for task in parent_tasks for seed in (task.seed1, task.seed2)
    )
    assert len(seeds) == 64 and len(set(seeds)) == 64
    assert seeds == parent_seeds
    child = predecessor.managed_child.binding["child"]
    assert task_set_hash(successor.tasks) == child["task_set_hash"]
    assert seed_pair_registry_hash(successor.tasks) == child["task_seed_mapping_hash"]
    assert seed_set_hash(seeds) == child["seed_set_hash"]


def test_happy_path_and_r2_gate(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, incident_value = _make_sealed_incident_fixture(
        repo_root, scratch
    )
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    # A real recovery necessarily uses a newer control-plane commit than the
    # sealed predecessor incident.  Keep the simulation fixture unchanged but
    # make the successor control archive observably different so the happy path
    # proves that retry equivalence is physics-only, not control-source equality.
    successor_control = inputs["fixture_sources"][1]
    (successor_control / "recovery-control-marker.txt").write_text(
        "incident-bound successor control plane\n", encoding="utf-8"
    )
    target = scratch / "successor-execution"
    predecessor_before = _tree_snapshot(predecessor.directory)
    incident_before = _tree_snapshot(incident)
    _, markers, original_path = _scheduler_sentinels(scratch)
    try:
        preview = preview_successor_execution(**inputs)
        assert preview is not None
        assert not target.exists()
        _assert_no_scheduler_contact(markers)

        successor = materialize_successor_execution_companion(
            **inputs, out_dir=target
        )
        _assert_no_scheduler_contact(markers)
        assert successor.directory == target.resolve()
        assert target.stat().st_mode & 0o222 == 0
        for name in (
            "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
            "scan_args.txt", "source_archives.json", "tasks.tsv",
        ):
            assert (target / name).stat().st_mode & 0o222 == 0
        assert not (target / "campaign.json").exists()
        for name in ("intents", "attempts", "finalized"):
            directory = target / name
            assert directory.is_dir() and not directory.is_symlink()
            assert not list(directory.iterdir())

        loaded = load_successor_execution(
            target,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        )
        _assert_successor_identity(loaded, predecessor, incident_value)
        _assert_exact_task_seed_reuse(loaded, predecessor)
        assert loaded.manifest["sources"]["simulation"] == (
            incident_value["source_identities"]["simulation"]
        )
        assert loaded.manifest["sources"]["control_plane"] != (
            incident_value["source_identities"]["control_plane"]
        )
        assert successor_predecessor_retry_task_ids(loaded) == tuple(
            task.logical_task_id for task in loaded.tasks
        )
        for mode in ("initial", "resume", "retry-failed"):
            try:
                phase2b_lib.selected_task_ids(loaded, mode)
            except ValueError:
                pass
            else:
                raise AssertionError(f"successor accepted first mode {mode}")
        try:
            load_execution_for_frozen_worker(target, control_root=repo_root)
        except ValueError:
            pass
        else:
            raise AssertionError("R2 successor worker bypassed additive readiness")
        assert _tree_snapshot(predecessor.directory) == predecessor_before
        assert _tree_snapshot(incident) == incident_before

        # The old predecessor readiness lock must not authorize this successor.
        try:
            load_successor_execution(
                target,
                allow_test_mode=True,
                require_readiness=True,
                verify_live_predecessor=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("R2 successor accepted predecessor readiness")

        # Direct library calls are part of the security boundary: R2 has no
        # intent even when a caller supplies a syntactically valid lock hash.
        for mode in ("initial", "resume", "retry-failed", "predecessor-retry"):
            try:
                prepare_attempt_intent(
                    loaded,
                    attempt_id=f"20260719T13000{len(mode) % 10}Z-{mode}",
                    mode=mode,
                    actor="successor-fixture-reviewer",
                    write=True,
                    readiness_lock_sha256="f" * 64,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"R2 successor wrote a {mode} intent")
            assert not list((target / "intents").iterdir())
        _assert_no_scheduler_contact(markers)
    finally:
        os.environ["PATH"] = original_path


def _clone_rehashed_incident(
    incident: Path,
    parent: Path,
    label: str,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    staging = parent / f"incident-policy-{label}-staging"
    shutil.copytree(incident, staging)
    for path in staging.rglob("*"):
        if path.is_file() and not path.is_symlink():
            path.chmod(0o644)
    value = json.loads((staging / "incident.json").read_text(encoding="utf-8"))
    mutate(value)
    value["incident_hash"] = incident_lib._incident_hash(value)
    value["incident_id"] = (
        "sm-v1-bc-s1-pre-simulation-incident-"
        f"{value['incident_hash'][:12]}"
    )
    (staging / "incident.json").write_text(
        json.dumps(value, indent=2) + "\n", encoding="utf-8"
    )
    checksum = staging / "SHA256SUMS"
    checksum.unlink()
    write_recursive_checksums(staging)
    target = parent / (
        f"bc-s1-{value['attempt']['job_id']}-{value['incident_hash'][:12]}"
    )
    staging.rename(target)
    # These mutations intentionally remain internally checksum-valid; the
    # successor policy, rather than a trivial checksum failure, must reject them.
    validate_incident_bundle(target)
    return target


def test_incident_authority_policy(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, _ = _make_sealed_incident_fixture(repo_root, scratch)
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    policy_parent = scratch / "successor-policy-incidents"
    policy_parent.mkdir()
    cases: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
        (
            "classification",
            lambda value: value.__setitem__("classification", "simulation-result"),
        ),
        (
            "events-consumed",
            lambda value: value["simulation_disposition"].__setitem__(
                "events_consumed", 1
            ),
        ),
        (
            "seeds-consumed",
            lambda value: value["simulation_disposition"].__setitem__(
                "production_seeds_consumed", 1
            ),
        ),
        (
            "geant4-invoked",
            lambda value: value["simulation_disposition"].__setitem__(
                "geant4_invoked", True
            ),
        ),
        (
            "task-output",
            lambda value: value["simulation_disposition"].__setitem__(
                "task_outputs_created", True
            ),
        ),
        (
            "seed-reuse-authority",
            lambda value: value["simulation_disposition"].__setitem__(
                "seed_reuse_requires_successor_authority", False
            ),
        ),
        (
            "seed-mapping",
            lambda value: value.__setitem__("task_seed_mapping_hash", "0" * 64),
        ),
        (
            "runtime",
            lambda value: value["runtime_identity"].__setitem__(
                "executable_sha256", "0" * 64
            ),
        ),
        (
            "predecessor-control-source",
            lambda value: value["source_identities"]["control_plane"].__setitem__(
                "tree_sha256", "0" * 64
            ),
        ),
    )
    for label, mutate in cases:
        tampered = _clone_rehashed_incident(
            incident, policy_parent, label, mutate
        )
        attempt_inputs = dict(inputs, incident_dir=tampered)
        try:
            preview_successor_execution(**attempt_inputs)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"successor accepted checksum-valid incident policy tamper: {label}"
            )
    assert not list((scratch / "successor-predecessor" / "execution" / "intents").glob("*successor*"))


def _refresh_successor_static_manifest(directory: Path) -> None:
    manifest_path = directory / "managed_execution.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["tasks"]["sha256"] = sha256_file(
        directory / "tasks.tsv"
    )
    manifest["artifacts"]["scan_args"]["sha256"] = sha256_file(
        directory / "scan_args.txt"
    )
    manifest["managed_child"]["tasks_sha256"] = sha256_file(
        directory / "tasks.tsv"
    )
    manifest["managed_child"]["scan_args_sha256"] = sha256_file(
        directory / "scan_args.txt"
    )
    manifest["execution_hash"] = phase2b_lib._semantic_hash(
        manifest, "execution_hash"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    phase2b_lib._write_static_checksums(directory)
    for name in (
        "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv",
    ):
        (directory / name).chmod(0o444)
    directory.chmod(0o555)


def _rewrite_task_rows(
    directory: Path, mutate: Callable[[list[dict[str, str]], list[str]], None]
) -> None:
    tasks_path = directory / "tasks.tsv"
    directory.chmod(0o755)
    for name in ("tasks.tsv", "scan_args.txt", "managed_execution.json", "STATIC_SHA256SUMS"):
        (directory / name).chmod(0o644)
    with tasks_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    scan_path = directory / "scan_args.txt"
    lines = scan_path.read_text(encoding="utf-8").splitlines()
    mutate(rows, lines)
    with tasks_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    scan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_successor_static_manifest(directory)


def test_exact_task_seed_reuse_and_binding(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, incident_value = _make_sealed_incident_fixture(
        repo_root, scratch
    )
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    original = materialize_successor_execution_companion(
        **inputs, out_dir=scratch / "successor-plan-original"
    )
    _assert_exact_task_seed_reuse(original, predecessor)

    def change_seed(rows: list[dict[str, str]], lines: list[str]) -> None:
        old = rows[0]["seed1"]
        replacement = str(int(old) + 1_000_003)
        rows[0]["seed1"] = replacement
        lines[0] = lines[0].replace(
            f"--seed1 {old}", f"--seed1 {replacement}", 1
        )

    def duplicate_seed(rows: list[dict[str, str]], lines: list[str]) -> None:
        old = rows[0]["seed1"]
        replacement = rows[1]["seed1"]
        rows[0]["seed1"] = replacement
        lines[0] = lines[0].replace(
            f"--seed1 {old}", f"--seed1 {replacement}", 1
        )

    def reorder(rows: list[dict[str, str]], lines: list[str]) -> None:
        rows[0], rows[1] = rows[1], rows[0]
        lines[0], lines[1] = lines[1], lines[0]

    for label, mutate in (
        ("changed-seed", change_seed),
        ("duplicate-seed", duplicate_seed),
        ("reordered-tasks", reorder),
    ):
        clone = scratch / f"successor-plan-{label}"
        shutil.copytree(original.directory, clone)
        _rewrite_task_rows(clone, mutate)
        try:
            load_successor_execution(
                clone,
                allow_test_mode=True,
                require_readiness=False,
                verify_live_predecessor=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"successor accepted self-rehashed {label}")

    # Rebinding the successor manifest to another incident hash must not be
    # made valid merely by recomputing its local semantic/static hashes.
    rebound = scratch / "successor-rebound-incident"
    shutil.copytree(original.directory, rebound)
    rebound.chmod(0o755)
    manifest_path = rebound / "managed_execution.json"
    manifest_path.chmod(0o644)
    (rebound / "STATIC_SHA256SUMS").chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = json.dumps(manifest)
    changed = before.replace(incident_value["incident_hash"], "b" * 64)
    assert changed != before
    manifest = json.loads(changed)
    manifest["execution_hash"] = phase2b_lib._semantic_hash(
        manifest, "execution_hash"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    phase2b_lib._write_static_checksums(rebound)
    for name in (
        "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv",
    ):
        (rebound / name).chmod(0o444)
    rebound.chmod(0o555)
    try:
        load_successor_execution(
            rebound,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("successor accepted a self-rehashed predecessor binding")

    deleted = scratch / "successor-deleted-authority-field"
    shutil.copytree(original.directory, deleted)
    deleted.chmod(0o755)
    deleted_manifest_path = deleted / "managed_execution.json"
    deleted_manifest_path.chmod(0o644)
    (deleted / "STATIC_SHA256SUMS").chmod(0o644)
    deleted_manifest = json.loads(deleted_manifest_path.read_text(encoding="utf-8"))
    del deleted_manifest["recovery_authority"]["predecessor_attempt"][
        "terminal_event_sha256"
    ]
    deleted_manifest["recovery_authority"]["authority_hash"] = (
        phase2b_lib._semantic_hash(
            deleted_manifest["recovery_authority"], "authority_hash"
        )
    )
    deleted_manifest["execution_hash"] = phase2b_lib._semantic_hash(
        deleted_manifest, "execution_hash"
    )
    deleted_manifest_path.write_text(
        json.dumps(deleted_manifest, indent=2) + "\n", encoding="utf-8"
    )
    phase2b_lib._write_static_checksums(deleted)
    for name in (
        "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv",
    ):
        (deleted / name).chmod(0o444)
    deleted.chmod(0o555)
    try:
        load_successor_execution(
            deleted,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("successor accepted deleted attempt authority")


def test_v3_failure_bound_successor(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    _, v2, failure, inputs = _make_v3_fixture(repo_root, scratch)
    target = scratch / "successor-v3-execution"
    v2_before = _tree_snapshot(v2.directory)
    failure_before = _tree_snapshot(failure)
    with _fixture_r3_failure_identity(v2):
        preview = preview_successor_v3_execution(**inputs)
        assert preview.predecessor.execution_id == v2.execution_id
        assert preview.failure["execution"]["execution_id"] == v2.execution_id
        v3 = materialize_successor_v3_execution_companion(
            **inputs, out_dir=target
        )
        assert v3.manifest["schema_version"] == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3
        assert v3.manifest["execution_generation"] == SUCCESSOR_EXECUTION_GENERATION_V3
        assert v3.tasks == v2.tasks
        assert v3.scan_args == v2.scan_args
        assert v3.manifest["runtime"] == v2.manifest["runtime"]
        assert v3.manifest["sources"]["simulation"] == v2.manifest["sources"]["simulation"]
        assert v3.manifest["sources"]["control_plane"] != v2.manifest["sources"]["control_plane"]
        authority = v3.manifest["recovery_authority"]
        assert authority["predecessor_execution"]["execution_id"] == v2.execution_id
        assert authority["predecessor_execution"]["execution_hash"] == v2.execution_hash
        assert authority["predecessor_execution"]["static_checksums_sha256"] == sha256_file(
            v2.directory / "STATIC_SHA256SUMS"
        )
        assert authority["failed_r3_preflight"]["failure_hash"] == preview.failure["failure_hash"]
        assert authority["original_production_incident"] == v2.manifest["recovery_authority"]["incident"]
        _assert_exact_task_seed_reuse(v3, v2)

        # A successful v3 R3 workspace shares the evidence/raw parent with the
        # failed v2 probe.  It must not invalidate the old failure bundle;
        # only the old v2 job name acquiring a workspace is contradictory.
        raw_root = failure.parent.parent / "raw"
        v3_raw = raw_root / ("g4sm-r3-" + v3.execution_hash[:12])
        v3_raw.mkdir()
        assert load_successor_execution(
            v3.directory,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        ).execution_hash == v3.execution_hash
        failed_v2_raw = raw_root / R3_FAILURE_JOB_NAME
        failed_v2_raw.mkdir()
        try:
            load_successor_execution(
                v3.directory,
                allow_test_mode=True,
                require_readiness=False,
                verify_live_predecessor=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("successor-v3 accepted a raw workspace for failed v2 R3")
        failed_v2_raw.rmdir()

        # The closed v2 is admissible only as explicit lineage evidence. It can
        # never become the active loader, readiness, worker, or submission target.
        try:
            load_successor_execution(
                v2.directory,
                allow_test_mode=True,
                require_readiness=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("closed successor-v2 accepted readiness")
        try:
            load_execution_for_frozen_worker(v2.directory, control_root=repo_root)
        except ValueError:
            pass
        else:
            raise AssertionError("closed successor-v2 accepted worker dispatch")

        # Rehashing the local v3 manifest cannot replace the external failed-R3
        # evidence binding.
        tampered = scratch / "successor-v3-tampered-failure"
        shutil.copytree(v3.directory, tampered)
        tampered.chmod(0o755)
        manifest_path = tampered / "managed_execution.json"
        manifest_path.chmod(0o644)
        (tampered / "STATIC_SHA256SUMS").chmod(0o644)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["recovery_authority"]["failed_r3_preflight"]["failure_hash"] = "7" * 64
        manifest["recovery_authority"]["authority_hash"] = phase2b_lib._semantic_hash(
            manifest["recovery_authority"], "authority_hash"
        )
        manifest["execution_hash"] = phase2b_lib._semantic_hash(
            manifest, "execution_hash"
        )
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        phase2b_lib._write_static_checksums(tampered)
        for name in (
            "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
            "scan_args.txt", "source_archives.json", "tasks.tsv",
        ):
            (tampered / name).chmod(0o444)
        tampered.chmod(0o555)
        try:
            load_successor_execution(
                tampered,
                allow_test_mode=True,
                verify_live_predecessor=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("successor-v3 accepted rehashed failed-R3 lineage")
    assert _tree_snapshot(v2.directory) == v2_before
    assert _tree_snapshot(failure) == failure_before


def _git_checked(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            "readiness fixture git command failed: "
            f"git {' '.join(arguments)}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def test_v3_recovery_readiness_job_lineage(
    repo_root: Path, scratch: Path
) -> None:
    """Exercise R4 list semantics behind the real one-commit Git gate.

    The upstream successor/R3 checkers already prove that test evidence cannot
    become formal evidence.  This fixture therefore uses a formal-shaped view
    of the otherwise fully validated synthetic v3 execution and stubs only the
    completed-R3 evidence binding.  Candidate construction, readiness hashing,
    the lock-only Git gate, and readiness verification are the production
    implementations.
    """

    scratch.mkdir()
    lineage = scratch / "lineage"
    lineage.mkdir()
    _, v2, _, inputs = _make_v3_fixture(repo_root, lineage)
    with _fixture_r3_failure_identity(v2):
        v3 = materialize_successor_v3_execution_companion(
            **inputs, out_dir=scratch / "successor-v3-execution"
        )

    git_root = scratch / "formal-shaped-control"
    git_root.mkdir()
    _git_checked(git_root, "init", "-q")
    _git_checked(git_root, "config", "user.name", "Successor Fixture")
    _git_checked(git_root, "config", "user.email", "fixture@example.invalid")
    _git_checked(git_root, "config", "commit.gpgsign", "false")
    (git_root / "frozen-control.txt").write_text(
        "formal-shaped v3 control baseline\n", encoding="utf-8"
    )
    _git_checked(git_root, "add", "frozen-control.txt")
    _git_checked(git_root, "commit", "-q", "-m", "freeze v3 control fixture")
    control_commit = _git_checked(git_root, "rev-parse", "HEAD")
    control_tree = _git_checked(git_root, "rev-parse", "HEAD^{tree}")

    manifest = json.loads(json.dumps(v3.manifest))
    manifest["test_mode"] = False
    manifest["accepted_statistical_evidence"] = True
    control_source = manifest["sources"]["control_plane"]
    control_source["git_commit"] = control_commit
    control_source["git_tree"] = control_tree
    formal_view = phase2b_lib.ManagedExecution(
        v3.directory,
        v3.managed_child,
        manifest,
        v3.tasks,
        v3.scan_args,
    )

    accepted_job_id = "70000991"
    preflight_evidence = scratch / "accepted-r3-evidence"
    preflight_evidence.mkdir()
    preflight_binding = {
        "directory": str(preflight_evidence.resolve()),
        "job_id": accepted_job_id,
    }
    original_binding = successor_lib.successor_r3_preflight_binding

    def fixture_binding(
        execution: object,
        evidence_dir: Path,
        *,
        allow_test_mode: bool = False,
        require_current_snapshot: bool = False,
    ) -> dict[str, str]:
        assert execution is formal_view
        assert evidence_dir.resolve() == preflight_evidence.resolve()
        assert allow_test_mode is False
        assert require_current_snapshot is True
        return dict(preflight_binding)

    successor_lib.successor_r3_preflight_binding = fixture_binding
    try:
        candidate = build_successor_recovery_readiness_candidate(
            formal_view, preflight_evidence_dir=preflight_evidence
        )
        assert candidate["failed_compute_preflight_slurm_job_ids"] == [
            "50544247"
        ]
        assert candidate["compute_preflight_slurm_job_ids"] == [
            accepted_job_id
        ]
        assert candidate["all_compute_preflight_slurm_job_ids"] == [
            "50544247",
            accepted_job_id,
        ]

        lock = git_root / RECOVERY_READINESS_LOCK_RELATIVE_V3
        lock.parent.mkdir(parents=True)
        lock.write_text(
            json.dumps(candidate, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        lock.chmod(0o644)
        _git_checked(
            git_root, "add", "--", RECOVERY_READINESS_LOCK_RELATIVE_V3.as_posix()
        )
        _git_checked(git_root, "commit", "-q", "-m", "add R4 readiness lock")
        verified_path, verified = verify_successor_recovery_readiness(
            formal_view, repo_root=git_root
        )
        assert verified_path == lock.resolve()
        assert verified == candidate

        # Preserve a valid semantic signature and a valid one-commit Git shape
        # while reversing the union.  Verification must still reject the
        # policy-level lineage order instead of trusting the re-signed payload.
        tampered = json.loads(json.dumps(candidate))
        tampered["all_compute_preflight_slurm_job_ids"] = [
            accepted_job_id,
            "50544247",
        ]
        tampered["readiness_hash"] = phase2b_lib._semantic_hash(
            tampered, "readiness_hash"
        )
        lock.write_text(
            json.dumps(tampered, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        lock.chmod(0o644)
        _git_checked(
            git_root, "add", "--", RECOVERY_READINESS_LOCK_RELATIVE_V3.as_posix()
        )
        _git_checked(git_root, "commit", "-q", "--amend", "--no-edit")
        try:
            verify_successor_recovery_readiness(formal_view, repo_root=git_root)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "successor readiness accepted a re-signed compute-job union tamper"
            )
    finally:
        successor_lib.successor_r3_preflight_binding = original_binding


def test_recovery_readiness_proposal_target_policy(scratch: Path) -> None:
    """Keep the inert proposal out of every immutable evidence authority."""

    work_root = scratch / "g4optics-rn"
    execution = (
        work_root / "campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME
    )
    execution.mkdir(parents=True)
    evidence = work_root / "evidence"
    evidence.mkdir()
    canonical = evidence / FORMAL_PROPOSAL_NAME
    assert validate_proposal_target(
        canonical, execution_root=execution
    ) == canonical.resolve()

    forbidden_parents = (
        evidence / "steel-module-production-r3/evidence/accepted-r3",
        evidence / "steel-module-production-r3/failures/failed-r3",
        work_root / "campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME_V2,
        scratch / "control-repository",
        execution,
    )
    for parent in forbidden_parents:
        parent.mkdir(parents=True, exist_ok=True)
        requested = parent / FORMAL_PROPOSAL_NAME
        try:
            validate_proposal_target(requested, execution_root=execution)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "successor readiness proposal accepted non-canonical target: "
                f"{requested}"
            )


def test_live_and_frozen_root_routing_contracts(
    repo_root: Path, scratch: Path
) -> None:
    """Lock the two legitimate repo-root policies at their public entries."""

    scratch.mkdir()
    # The read-only v2 history validator is a live CLI.  It must never pair an
    # archive root with live-predecessor validation again.
    v2_calls: list[dict[str, object]] = []
    child = scratch / "managed-child"
    original_parse = v2_validator_cli.parse_args
    original_phase2a = v2_validator_cli.load_phase2a_lock
    original_load = v2_validator_cli.load_successor_execution

    def v2_load(execution_dir: Path, **kwargs: object) -> SimpleNamespace:
        v2_calls.append({"execution_dir": execution_dir, **kwargs})
        return SimpleNamespace(
            execution_id="fixture-v2-id",
            execution_hash="c" * 64,
            manifest={
                "recovery_authority": {
                    "authority_hash": "d" * 64,
                    "incident": {"incident_hash": "e" * 64},
                }
            },
        )

    v2_validator_cli.parse_args = lambda: Namespace(check_only=True)
    v2_validator_cli.load_phase2a_lock = lambda root: (
        root / phase2b_lib.PHASE2A_LOCK_RELATIVE,
        {"canonical_directory": str(child)},
    )
    v2_validator_cli.load_successor_execution = v2_load
    try:
        with redirect_stdout(StringIO()):
            assert v2_validator_cli.main() == 0
    finally:
        v2_validator_cli.parse_args = original_parse
        v2_validator_cli.load_phase2a_lock = original_phase2a
        v2_validator_cli.load_successor_execution = original_load
    assert v2_calls == [
        {
            "execution_dir": child.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V2,
            "repo_root": repo_root,
            "require_readiness": False,
            "verify_phase2a_control_plane": True,
            "verify_live_predecessor": True,
            "allow_closed_v2": True,
        }
    ]

    # The v3 pre-submit validator performs both real admission policies on the
    # login node and rejects any disagreement before a compute preflight can be
    # submitted.
    v3_calls: list[dict[str, object]] = []
    v3_boundaries: list[tuple[object, Path]] = []
    v3_execution_dir = child.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V3
    v3_execution = SimpleNamespace(
        directory=v3_execution_dir,
        execution_id="fixture-v3-id",
        execution_hash="f" * 64,
        manifest={
            "recovery_authority": {
                "authority_hash": "1" * 64,
                "failed_r3_preflight": {"failure_hash": "2" * 64},
            }
        },
        tasks=("fixture-task",),
        scan_args=("--fixture",),
    )
    original_v3_parse = v3_validator_cli.parse_args
    original_v3_phase2a = v3_validator_cli.load_phase2a_lock
    original_v3_load = v3_validator_cli.load_successor_execution
    original_v3_boundary = v3_validator_cli.validate_successor_r2_boundary

    def v3_load(execution_dir: Path, **kwargs: object) -> object:
        v3_calls.append({"execution_dir": execution_dir, **kwargs})
        return v3_execution

    def v3_boundary(execution: object, *, repo_root: Path) -> None:
        v3_boundaries.append((execution, repo_root))

    v3_validator_cli.parse_args = lambda: Namespace(check_only=True)
    v3_validator_cli.load_phase2a_lock = lambda root: (
        root / phase2b_lib.PHASE2A_LOCK_RELATIVE,
        {"canonical_directory": str(child)},
    )
    v3_validator_cli.load_successor_execution = v3_load
    v3_validator_cli.validate_successor_r2_boundary = v3_boundary
    try:
        with redirect_stdout(StringIO()):
            assert v3_validator_cli.main() == 0
    finally:
        v3_validator_cli.parse_args = original_v3_parse
        v3_validator_cli.load_phase2a_lock = original_v3_phase2a
        v3_validator_cli.load_successor_execution = original_v3_load
        v3_validator_cli.validate_successor_r2_boundary = original_v3_boundary
    assert v3_calls == [
        {
            "execution_dir": v3_execution_dir,
            "repo_root": repo_root,
            "require_readiness": False,
            "verify_live_predecessor": True,
        },
        {
            "execution_dir": v3_execution_dir,
            "repo_root": v3_execution_dir / "sources/control",
            "require_readiness": False,
            "verify_phase2a_control_plane": False,
            "verify_live_predecessor": False,
        },
    ]
    assert v3_boundaries == [(v3_execution, repo_root)]

    # A compute worker has the opposite, explicitly frozen policy.  Admission
    # uses the checksum-bound control archive and must not recurse into a live
    # predecessor.  The real test-mode v3 tests separately exercise its full
    # failure-bundle/preloaded-predecessor binding.
    frozen = scratch / "frozen-v3"
    frozen.mkdir()
    (frozen / "managed_execution.json").write_text(
        json.dumps({"schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION}) + "\n",
        encoding="utf-8",
    )
    control_root = frozen / "sources/control"
    control_root.mkdir(parents=True)
    sentinel = SimpleNamespace(
        manifest={"schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION}
    )
    frozen_calls: list[dict[str, object]] = []
    readiness_calls: list[tuple[object, object]] = []
    original_successor_load = successor_lib.load_successor_execution
    original_readiness = successor_lib.verify_successor_recovery_readiness

    def frozen_load(execution_dir: Path, **kwargs: object) -> object:
        frozen_calls.append({"execution_dir": execution_dir, **kwargs})
        return sentinel

    def frozen_readiness(execution: object, *, repo_root: Path | None) -> None:
        readiness_calls.append((execution, repo_root))

    successor_lib.load_successor_execution = frozen_load
    successor_lib.verify_successor_recovery_readiness = frozen_readiness
    try:
        assert load_execution_for_frozen_worker(
            frozen, control_root=control_root
        ) is sentinel
    finally:
        successor_lib.load_successor_execution = original_successor_load
        successor_lib.verify_successor_recovery_readiness = original_readiness
    assert frozen_calls == [
        {
            "execution_dir": frozen,
            "repo_root": control_root,
            "require_readiness": False,
            "verify_phase2a_control_plane": False,
            "verify_live_predecessor": False,
        }
    ], frozen_calls
    assert readiness_calls == [(sentinel, None)]


def test_atomicity_and_concurrency(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, _ = _make_sealed_incident_fixture(repo_root, scratch)
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    predecessor_before = _tree_snapshot(predecessor.directory)
    incident_before = _tree_snapshot(incident)

    for point in ("tasks", "predecessor", "sources", "checksums"):
        target = scratch / f"successor-fault-{point}"
        try:
            materialize_successor_execution_companion(
                **inputs, out_dir=target, fault_after=point
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"successor fault injection succeeded: {point}")
        assert not target.exists()
        assert not (target.parent / f".{target.name}.materialize.lock").exists()
        assert not list(target.parent.glob(f".{target.name}.tmp-*"))

    target = scratch / "successor-concurrent"

    def materialize() -> tuple[str, str]:
        try:
            value = materialize_successor_execution_companion(
                **inputs, out_dir=target
            )
        except ValueError as exc:
            return "rejected", str(exc)
        return "published", value.execution_hash

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: materialize(), range(2)))
    assert sum(kind == "published" for kind, _ in outcomes) == 1
    assert sum(kind == "rejected" for kind, _ in outcomes) == 1
    load_successor_execution(
        target,
        allow_test_mode=True,
        require_readiness=False,
        verify_live_predecessor=True,
    )
    assert not (target.parent / f".{target.name}.materialize.lock").exists()
    assert not list(target.parent.glob(f".{target.name}.tmp-*"))
    assert _tree_snapshot(predecessor.directory) == predecessor_before
    assert _tree_snapshot(incident) == incident_before

    existing = scratch / "successor-existing"
    existing.mkdir()
    try:
        materialize_successor_execution_companion(**inputs, out_dir=existing)
    except ValueError:
        pass
    else:
        raise AssertionError("successor materializer overwrote an existing target")

    escaped = incident / "forbidden-successor-child"
    try:
        materialize_successor_execution_companion(**inputs, out_dir=escaped)
    except ValueError:
        pass
    else:
        raise AssertionError("successor materializer wrote inside incident evidence")
    assert not escaped.exists()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    assert FORMAL_SUCCESSOR_EXECUTION_NAME_V2 != phase2b_lib.FORMAL_EXECUTION_NAME
    assert FORMAL_SUCCESSOR_EXECUTION_NAME_V3 != FORMAL_SUCCESSOR_EXECUTION_NAME_V2
    assert FORMAL_SUCCESSOR_EXECUTION_NAME != FORMAL_SUCCESSOR_EXECUTION_NAME_V3
    assert SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3 != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2
    assert SUCCESSOR_EXECUTION_SCHEMA_VERSION != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3
    assert SUCCESSOR_EXECUTION_GENERATION_V3 != SUCCESSOR_EXECUTION_GENERATION_V2
    assert SUCCESSOR_EXECUTION_GENERATION != SUCCESSOR_EXECUTION_GENERATION_V3
    assert RECOVERY_READINESS_LOCK_RELATIVE != phase2b_lib.READINESS_LOCK_RELATIVE
    worker_source = (
        repo_root / "hpc/osc/run_steel_module_production_phase2b_task.py"
    ).read_text(encoding="utf-8")
    contract_source = (
        repo_root / "hpc/osc/steel_module_production_container_contract.py"
    ).read_text(encoding="utf-8")
    for fragment in (
        "build_production_apptainer_prefix",
        "production_apptainer_host_environment",
        "AdditionalBind(task_root, container_task_root, writable=True)",
        "cwd=control_source",
    ):
        assert fragment in worker_source, fragment
    for fragment in (
        'NO_MOUNT_CLASSES = "hostfs,cwd,bind-paths"',
        '"--cleanenv"',
        '"--containall"',
        '"--no-home"',
        'CONTAINER_EXECUTION_ROOT',
        'writable=False',
    ):
        assert fragment in contract_source, fragment
    with tempfile.TemporaryDirectory(
        prefix="steel-module-production-successor-"
    ) as temporary:
        scratch = Path(temporary)
        _, markers, original_path = _scheduler_sentinels(scratch)
        try:
            test_happy_path_and_r2_gate(repo_root, scratch / "happy")
            test_incident_authority_policy(repo_root, scratch / "authority")
            test_exact_task_seed_reuse_and_binding(repo_root, scratch / "plan")
            test_v3_failure_bound_successor(repo_root, scratch / "v3")
            test_v3_recovery_readiness_job_lineage(
                repo_root, scratch / "readiness"
            )
            test_recovery_readiness_proposal_target_policy(
                scratch / "proposal-target"
            )
            test_live_and_frozen_root_routing_contracts(
                repo_root, scratch / "root-routing"
            )
            test_atomicity_and_concurrency(repo_root, scratch / "atomic")
            _assert_no_scheduler_contact(markers)
        finally:
            os.environ["PATH"] = original_path
    print("steel-module production successor v2/v3/v4 dispatch: PASS")
    print("scheduler: forbidden command sentinels; real Slurm calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
