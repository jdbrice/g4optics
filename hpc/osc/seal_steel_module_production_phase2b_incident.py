#!/usr/bin/env python3
"""Seal the failed first BC-S1 attempt as a pre-simulation incident.

This recovery tool is intentionally limited to the historical Phase-2B
execution companion.  It can preview the exact evidence without writing, then
freeze terminal accounting and publish one content-addressed incident bundle.
It never creates an intent and never invokes sbatch or scontrol.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

from manage_steel_module_production_attempt import (
    freeze_terminal_accounting,
    preview_terminal_accounting,
    validate_terminal_accounting_snapshot,
)
from steel_module_campaign_lib import (
    canonical_json,
    load_json,
    require_dict,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    verify_recursive_checksums,
    write_recursive_checksums,
)
from steel_module_production_phase2b_lib import (
    ACCOUNTING_SCHEMA_VERSION_V2,
    ACTOR_RE,
    EVENT_SCHEMA_VERSION,
    HISTORICAL_CLOSED_ATTEMPT_ID,
    HISTORICAL_CLOSED_EXECUTION_HASH,
    HISTORICAL_CLOSED_EXECUTION_ID,
    HISTORICAL_CLOSED_INTENT_SHA256,
    HISTORICAL_CLOSED_JOB_ID,
    READINESS_LOCK_RELATIVE,
    READINESS_LOCK_SCHEMA_VERSION,
    ManagedExecution,
    attempt_state,
    fsync_tree,
    list_attempt_ids,
    load_attempt_intent,
    load_frozen_accounting,
    load_historical_closed_execution_for_recovery,
    load_phase2a_lock,
    read_attempt_events,
    utc_now,
)


INCIDENT_SCHEMA_VERSION = "steel-module-production-phase2b-incident-v1"
FORMAL_EXECUTION_NAME = "steel-module-production-bc-s1-execution"
FORMAL_ATTEMPT_ID = HISTORICAL_CLOSED_ATTEMPT_ID
FORMAL_JOB_ID = HISTORICAL_CLOSED_JOB_ID
FORMAL_INTENT_SHA256 = HISTORICAL_CLOSED_INTENT_SHA256
FORMAL_EXECUTION_ID = HISTORICAL_CLOSED_EXECUTION_ID
FORMAL_EXECUTION_HASH = HISTORICAL_CLOSED_EXECUTION_HASH
FORMAL_IMPLEMENTATION_COMMIT = "20c9007f8cd3da27fe1b4fbd13cad8bdaefdc292"
FORMAL_READINESS_COMMIT = "5eaf22a23ee84d3bab3ac2539923d62cb8276bdb"
FORMAL_RECOVERY_ACTOR = "anolddriver66-account-case-adopt"
FORMAL_RECOVERY_SCRIPT_SHA256 = (
    "b608a2fbc04e6f30ee209709c5c9647c6ebe8af665f0e046a9c5e43231359d80"
)
FORMAL_SUBMISSION_COMMAND_SHA256 = (
    "9d4af119247bbf233c16801194bd4a50b92e4c6ea608f6a36c56a28a4115fa68"
)
FORMAL_RELEASE_AMBIGUOUS_EVENT_PREFIX = "8fd96fa45f98"
EXPECTED_FAILURE_LINE = (
    "Cannot run managed steel-module production task: managed execution "
    "control-lock inode identity mismatch"
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _regular_file_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"incident evidence is not a regular file: {path}")
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _historical_readiness_identity(
    repo_root: Path, execution: ManagedExecution
) -> dict[str, Any]:
    path = repo_root / READINESS_LOCK_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("historical Phase-2B readiness lock is missing")
    value = load_json(path)
    if (
        value.get("schema_version") != READINESS_LOCK_SCHEMA_VERSION
        or value.get("status") != "accepted-formal-phase-2b-ready"
        or value.get("managed_submission_ready") is not True
    ):
        raise ValueError("historical Phase-2B readiness lock is not accepted")
    companion = require_dict(value, "execution_companion")
    if (
        companion.get("execution_id") != execution.execution_id
        or companion.get("execution_hash") != execution.execution_hash
        or companion.get("directory") != str(execution.directory)
        or companion.get("managed_execution_json_sha256")
        != sha256_file(execution.directory / "managed_execution.json")
        or companion.get("static_checksum_manifest_sha256")
        != sha256_file(execution.directory / "STATIC_SHA256SUMS")
        or companion.get("control_lock")
        != require_dict(require_dict(execution.manifest, "artifacts"), "control_lock")
    ):
        raise ValueError("historical readiness lock execution identity mismatch")
    scheduler_commands = require_dict(
        require_dict(value, "osc_capabilities"), "commands"
    )
    if (
        scheduler_commands.get("sacct") != "/usr/bin/sacct"
        or scheduler_commands.get("squeue") != "/usr/bin/squeue"
    ):
        raise ValueError("historical readiness scheduler-read identity mismatch")
    control = require_dict(value, "control_plane")
    frozen_control = require_dict(require_dict(execution.manifest, "sources"), "control_plane")
    if control.get("git_commit") != FORMAL_IMPLEMENTATION_COMMIT:
        raise ValueError("historical readiness lock implementation commit mismatch")
    for key in ("git_commit", "git_tree", "source_tree_sha256"):
        if control.get(key) != frozen_control.get(key):
            raise ValueError("historical readiness/control archive mismatch")
    parent = subprocess.run(
        ["git", "rev-parse", f"{FORMAL_READINESS_COMMIT}^"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if parent.returncode or parent.stdout.strip() != FORMAL_IMPLEMENTATION_COMMIT:
        raise ValueError("historical readiness commit parent is not implementation C")
    changed = subprocess.run(
        [
            "git", "diff-tree", "--no-commit-id", "--name-only", "-r",
            FORMAL_READINESS_COMMIT,
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if changed.returncode or changed.stdout.splitlines() != [
        READINESS_LOCK_RELATIVE.as_posix()
    ]:
        raise ValueError("historical readiness commit changed files beyond the lock")
    blob = subprocess.run(
        [
            "git",
            "show",
            f"{FORMAL_READINESS_COMMIT}:{READINESS_LOCK_RELATIVE.as_posix()}",
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if blob.returncode or blob.stdout != path.read_bytes():
        raise ValueError("historical readiness file differs from commit R blob")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "schema_version": value["schema_version"],
        "status": value["status"],
        "implementation_commit": FORMAL_IMPLEMENTATION_COMMIT,
        "readiness_commit": FORMAL_READINESS_COMMIT,
        "readiness_commit_parent": FORMAL_IMPLEMENTATION_COMMIT,
        "commit_blob_sha256": sha256_bytes(blob.stdout),
        "scheduler_read_commands": {
            "sacct": scheduler_commands["sacct"],
            "squeue": scheduler_commands["squeue"],
        },
    }


def _historical_scheduler_read_runner(
    execution: ManagedExecution, readiness: dict[str, Any]
) -> CommandRunner:
    """Return a subprocess gateway for the two fixed read-only Slurm calls."""

    intent = load_attempt_intent(execution, FORMAL_ATTEMPT_ID)
    job_name = require_dict(intent, "scheduler")["job_name"]
    commands = require_dict(readiness, "scheduler_read_commands")
    allowed = {
        (
            "sacct", "-n", "-P", "-j", FORMAL_JOB_ID,
            "--format=JobID,JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS,MaxVMSize",
        ): commands["sacct"],
        (
            "squeue", "-h", "-o", "%A|%j|%a|%T|%r", "--name", job_name,
        ): commands["squeue"],
    }

    def run(
        command: Sequence[str], *, cwd: Path, timeout: int = 120
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(command)
        executable = allowed.get(key)
        if executable is None:
            raise ValueError("historical recovery rejected a non-allowlisted command")
        if cwd.resolve() != execution.directory:
            raise ValueError("historical scheduler read used an unexpected work directory")
        argv = [executable, *key[1:]]
        return subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "")},
        )

    return run


def _recovery_control_plane_identity(repo_root: Path) -> dict[str, Any]:
    def git_value(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "recovery Git identity failed")
        return result.stdout.strip()

    artifacts = (
        "hpc/osc/seal_steel_module_production_phase2b_incident.py",
        "hpc/osc/manage_steel_module_production_attempt.py",
        "hpc/osc/steel_module_production_phase2b_lib.py",
    )
    head = git_value("rev-parse", "HEAD")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", FORMAL_READINESS_COMMIT, head],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if ancestry.returncode:
        raise ValueError("recovery control plane does not descend from readiness R")
    return {
        "git_commit": head,
        "git_tree": git_value("rev-parse", "HEAD^{tree}"),
        "descends_from_readiness_commit": FORMAL_READINESS_COMMIT,
        "checkout_clean": not bool(
            git_value("status", "--porcelain", "--untracked-files=all")
        ),
        "artifacts": {
            relative: {"sha256": sha256_file(repo_root / relative)}
            for relative in artifacts
        },
    }


def _validate_failure_outputs(
    execution: ManagedExecution, attempt_id: str, job_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intent = load_attempt_intent(execution, attempt_id)
    selected = intent["selected"]["logical_task_ids"]
    attempt_dir = execution.directory / "attempts" / attempt_id
    tasks_dir = attempt_dir / "tasks"
    if tasks_dir.exists() or tasks_dir.is_symlink():
        raise ValueError("pre-simulation incident unexpectedly has a tasks directory")
    forbidden = []
    for pattern in ("*.root", "task_result.json", "run_config.json", "summary.csv"):
        forbidden.extend(attempt_dir.rglob(pattern))
    if forbidden:
        raise ValueError(
            "pre-simulation incident contains forbidden simulation output: "
            + ", ".join(str(path) for path in sorted(forbidden))
        )

    expected_paths: set[Path] = set()
    logs: list[dict[str, Any]] = []
    for array_index, logical_id in enumerate(selected, start=1):
        path = attempt_dir / f"slurm-{job_id}_{array_index}.out"
        expected_paths.add(path)
        record = _regular_file_record(path)
        if path.read_text(encoding="utf-8").splitlines() != [EXPECTED_FAILURE_LINE]:
            raise ValueError(f"array task {array_index} has an unexpected failure log")
        logs.append(
            {
                "array_index": array_index,
                "logical_task_id": logical_id,
                **record,
                "failure_line": EXPECTED_FAILURE_LINE,
            }
        )
    auxiliary: list[dict[str, Any]] = []
    all_slurm_logs = set(attempt_dir.rglob("slurm-*.out"))
    job_logs = set(attempt_dir.glob(f"slurm-{job_id}_*.out"))
    foreign = sorted(all_slurm_logs - job_logs)
    if foreign:
        raise ValueError(
            "pre-simulation incident contains a foreign Slurm log: "
            + ", ".join(str(path) for path in foreign)
        )
    for path in sorted(job_logs):
        if path in expected_paths:
            continue
        record = _regular_file_record(path)
        if path.stat().st_size:
            raise ValueError(f"unexpected non-empty auxiliary Slurm log: {path}")
        auxiliary.append(record)
    allowed_entries = {
        "events",
        *(path.name for path in job_logs),
    }
    accounting_dir = attempt_dir / "accounting"
    if accounting_dir.is_dir() and not accounting_dir.is_symlink():
        allowed_entries.add("accounting")
    unexpected_entries = sorted(
        path.name for path in attempt_dir.iterdir() if path.name not in allowed_entries
    )
    if unexpected_entries:
        raise ValueError(
            "pre-simulation incident contains unexpected attempt entries: "
            + ", ".join(unexpected_entries)
        )
    if len(logs) != 32:
        raise ValueError("formal pre-simulation incident requires exactly 32 task logs")
    return logs, auxiliary


def _snapshot_from_frozen(
    execution: ManagedExecution, attempt_id: str
) -> dict[str, Any]:
    accounting = load_frozen_accounting(execution, attempt_id)
    directory = execution.directory / "attempts" / attempt_id / "accounting"
    return {
        "payload": accounting,
        "sacct_psv": (directory / "sacct.psv").read_text(encoding="utf-8"),
        "squeue_psv": (directory / "squeue.psv").read_text(encoding="utf-8"),
    }


def _formal_execution_identity(execution: ManagedExecution) -> bool:
    return (
        execution.execution_id == FORMAL_EXECUTION_ID
        and execution.execution_hash == FORMAL_EXECUTION_HASH
    )


def _validate_formal_event_lineage(
    intent: dict[str, Any], state: Any
) -> None:
    """Validate the exact six-step account-case recovery history."""

    events = tuple(state.events)
    if state.status == "terminal-accounting-frozen":
        if not events or events[-1]["event_type"] != "terminal-accounting-frozen":
            raise ValueError("formal incident terminal event lineage is invalid")
        events = events[:-1]
    event_types = tuple(event["event_type"] for event in events)
    if event_types != (
        "submission-invoked",
        "submitted-held",
        "release-ambiguous",
        "release-scheduler-observation",
        "job-verified",
        "job-released",
    ):
        raise ValueError("formal incident event lineage is not the six-step history")
    scheduler = require_dict(intent, "scheduler")
    job_name = scheduler.get("job_name")
    waiver = {
        "field": "Account",
        "intent_value": "PAS2524",
        "scheduler_value": "pas2524",
        "rule": "exact-ASCII-lowercase-equivalence-only",
    }
    if require_dict(events[0], "payload") != {
        "command_sha256": FORMAL_SUBMISSION_COMMAND_SHA256,
        "job_name": job_name,
    }:
        raise ValueError("formal incident submission event changed")
    if require_dict(events[1], "payload") != {"job_id": FORMAL_JOB_ID}:
        raise ValueError("formal incident submitted-held event changed")
    if (
        not str(events[2].get("event_sha256", "")).startswith(
            FORMAL_RELEASE_AMBIGUOUS_EVENT_PREFIX
        )
        or require_dict(events[2], "payload")
        != {"job_id": FORMAL_JOB_ID, "reason": "Slurm held job Account mismatch"}
    ):
        raise ValueError("formal incident release-ambiguous event changed")

    observation = require_dict(events[3], "payload")
    observation_stdout = observation.get("scontrol_stdout")
    if (
        events[3].get("actor") != FORMAL_RECOVERY_ACTOR
        or set(observation)
        != {
            "job_id",
            "match_count",
            "manual_review",
            "account_case_waiver",
            "submission_command_sha256",
            "recovery_script_sha256",
            "squeue_psv",
            "sacct_psv",
            "scontrol_stdout",
            "scontrol_stdout_sha256",
            "no_sbatch",
        }
        or observation.get("job_id") != FORMAL_JOB_ID
        or observation.get("match_count") != 1
        or observation.get("manual_review")
        != "OSC canonicalized the Slurm account to lowercase"
        or observation.get("account_case_waiver") != waiver
        or observation.get("submission_command_sha256")
        != FORMAL_SUBMISSION_COMMAND_SHA256
        or observation.get("recovery_script_sha256")
        != FORMAL_RECOVERY_SCRIPT_SHA256
        or observation.get("squeue_psv")
        != f"{FORMAL_JOB_ID}|{job_name}|pas2524|PENDING|JobHeldUser\n"
        or observation.get("sacct_psv")
        != f"{FORMAL_JOB_ID}|{job_name}|PENDING|0:0\n"
        or not isinstance(observation_stdout, str)
        or observation.get("scontrol_stdout_sha256")
        != sha256_bytes(observation_stdout.encode())
        or observation.get("no_sbatch") is not True
    ):
        raise ValueError("formal incident scheduler observation changed")

    verified = require_dict(events[4], "payload")
    if (
        events[4].get("actor") != FORMAL_RECOVERY_ACTOR
        or set(verified)
        != {
            "job_id",
            "job_name",
            "state",
            "reason",
            "reconciled",
            "account_case_waiver",
            "scontrol_stdout_sha256",
        }
        or verified.get("job_id") != FORMAL_JOB_ID
        or verified.get("job_name") != job_name
        or verified.get("state") != "PENDING"
        or verified.get("reason") != "JobHeldUser"
        or verified.get("reconciled") is not True
        or verified.get("account_case_waiver") != waiver
        or not isinstance(verified.get("scontrol_stdout_sha256"), str)
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(verified.get("scontrol_stdout_sha256"))
        )
    ):
        raise ValueError("formal incident job-verification event changed")
    if (
        events[5].get("actor") != FORMAL_RECOVERY_ACTOR
        or require_dict(events[5], "payload")
        != {
            "job_id": FORMAL_JOB_ID,
            "reconciled": True,
            "manual_account_case_recovery": True,
            "release_command": ["/usr/bin/scontrol", "release", FORMAL_JOB_ID],
            "return_code": 0,
        }
    ):
        raise ValueError("formal incident job-release event changed")


def _validate_incident_authority(
    execution: ManagedExecution,
    *,
    repo_root: Path,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    historical_readiness: dict[str, Any] | None,
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    """Validate all durable authority before scheduler reads or writes."""

    if not isinstance(actor, str) or not ACTOR_RE.fullmatch(actor):
        raise ValueError("incident actor identity is invalid")
    intent = load_attempt_intent(execution, attempt_id)
    if intent.get("intent_sha256") != intent_sha256:
        raise ValueError("incident intent SHA-256 mismatch")
    state = attempt_state(execution, attempt_id)
    if state.status not in {"released-active", "terminal-accounting-frozen"}:
        raise ValueError(f"incident attempt is not terminalizable: {state.status}")
    if state.job_id is None:
        raise ValueError("incident attempt has no immutable Slurm job identity")
    formal = _formal_execution_identity(execution)
    if formal:
        if (
            attempt_id != FORMAL_ATTEMPT_ID
            or intent_sha256 != FORMAL_INTENT_SHA256
            or state.job_id != FORMAL_JOB_ID
        ):
            raise ValueError("formal incident authority identity mismatch")
        if list_attempt_ids(execution) != (FORMAL_ATTEMPT_ID,):
            raise ValueError("formal incident does not have one unique intent")
        for root_name, expected_names in (
            ("intents", {FORMAL_ATTEMPT_ID}),
            ("attempts", {FORMAL_ATTEMPT_ID}),
            ("finalized", set()),
        ):
            root = execution.directory / root_name
            names = {path.name for path in root.iterdir()}
            if names != expected_names:
                raise ValueError(
                    f"formal incident contains unexpected {root_name} lineage"
                )
        _validate_formal_event_lineage(intent, state)
    if formal:
        readiness = _historical_readiness_identity(repo_root, execution)
        if historical_readiness is not None and historical_readiness != readiness:
            raise ValueError("supplied historical readiness identity is not authoritative")
    else:
        readiness = historical_readiness or _historical_readiness_identity(
            repo_root, execution
        )
    if intent.get("readiness_lock_sha256") != readiness.get("sha256"):
        raise ValueError("incident intent/readiness digest mismatch")
    return intent, state, readiness


def collect_incident_evidence(
    execution: ManagedExecution,
    *,
    repo_root: Path,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner,
    historical_readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent, state, readiness = _validate_incident_authority(
        execution,
        repo_root=repo_root,
        attempt_id=attempt_id,
        intent_sha256=intent_sha256,
        actor=actor,
        historical_readiness=historical_readiness,
    )
    # Prove the local pre-simulation disposition before any scheduler read.
    logs, auxiliary_logs = _validate_failure_outputs(
        execution, attempt_id, str(state.job_id)
    )
    if state.status == "released-active":
        snapshot = preview_terminal_accounting(
            execution,
            attempt_id=attempt_id,
            intent_sha256=intent_sha256,
            actor=actor,
            runner=runner,
        )
    elif state.status == "terminal-accounting-frozen":
        snapshot = _snapshot_from_frozen(execution, attempt_id)
    accounting = require_dict(snapshot, "payload")
    job_id = accounting.get("job_id")
    indices = {str(index) for index in range(1, 33)}
    if (
        accounting.get("accepted_terminal") is not True
        or accounting.get("all_tasks_completed") is not False
        or accounting.get("selected_task_count") != 32
        or accounting.get("terminal_task_count") != 32
        or set(require_dict(accounting, "task_states")) != indices
        or set(require_dict(accounting, "task_exit_codes")) != indices
        or set(require_dict(accounting, "task_job_ids")) != indices
        or set(require_dict(accounting, "task_job_ids_raw")) != indices
        or set(accounting["task_states"].values()) != {"FAILED"}
        or set(accounting["task_exit_codes"].values()) != {"1:0"}
        or accounting.get("array_index_identity_field") != "JobID"
        or accounting.get("array_parent_row_present") is not False
    ):
        raise ValueError("scheduler evidence is not the exact OSC 32-task failure")
    events = read_attempt_events(execution, attempt_id)
    accounting_manifest_sha256 = None
    if state.status == "terminal-accounting-frozen":
        accounting_manifest_sha256 = sha256_file(
            execution.directory
            / "attempts"
            / attempt_id
            / "accounting"
            / "SHA256SUMS"
        )
    lock_path = execution.directory / ".control.lock"
    lock_stat = lock_path.lstat()
    recovery_control = _recovery_control_plane_identity(repo_root)
    accepted = (
        execution.manifest.get("test_mode") is False
        and _formal_execution_identity(execution)
        and recovery_control["checkout_clean"] is True
        and readiness.get("readiness_commit") == FORMAL_READINESS_COMMIT
        and readiness.get("implementation_commit") == FORMAL_IMPLEMENTATION_COMMIT
    )
    return {
        "schema_version": INCIDENT_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "created_by": actor,
        "incident_id": None,
        "incident_hash": None,
        "classification": "pre-simulation-control-plane-failure",
        "root_cause": "cross-node-control-lock-device-or-inode-mismatch",
        "accepted_incident_evidence": accepted,
        "execution": {
            "directory": str(execution.directory),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "managed_execution_sha256": sha256_file(
                execution.directory / "managed_execution.json"
            ),
            "static_checksums_sha256": sha256_file(
                execution.directory / "STATIC_SHA256SUMS"
            ),
        },
        "historical_readiness": readiness,
        "recovery_control_plane": recovery_control,
        "attempt": {
            "attempt_id": attempt_id,
            "intent_sha256": intent_sha256,
            "job_id": job_id,
            "job_name": intent["scheduler"]["job_name"],
            "event_count": len(events),
            "event_chain_sha256": sha256_bytes(canonical_json(events)),
            "terminal_event_sha256": events[-1]["event_sha256"]
            if state.status == "terminal-accounting-frozen"
            else None,
            "accounting_manifest_sha256": accounting_manifest_sha256,
        },
        "scheduler": {
            "array_task_count": 32,
            "logical_job_id_field": "JobID",
            "raw_job_id_field": "JobIDRaw",
            "array_parent_row_present": False,
            "states": {"FAILED": 32},
            "exit_codes": {"1:0": 32},
            "sacct_psv_sha256": sha256_bytes(snapshot["sacct_psv"].encode()),
            "squeue_psv_sha256": sha256_bytes(snapshot["squeue_psv"].encode()),
            "task_job_ids": accounting["task_job_ids"],
            "task_job_ids_raw": accounting["task_job_ids_raw"],
        },
        "simulation_disposition": {
            "loader_completed": False,
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "task_outputs_created": False,
            "root_outputs_created": False,
            "events_consumed": 0,
            "production_seed_count": 64,
            "production_seeds_consumed": 0,
            "seed_reuse_requires_successor_authority": True,
        },
        "control_lock_login_observation": {
            "device": lock_stat.st_dev,
            "inode": lock_stat.st_ino,
            "mode": lock_stat.st_mode & 0o777,
            "size_bytes": lock_stat.st_size,
            "link_count": lock_stat.st_nlink,
            "sha256": sha256_file(lock_path),
            "matches_historical_manifest": (
                require_dict(require_dict(execution.manifest, "artifacts"), "control_lock")
                == {
                    "path": ".control.lock",
                    "device": lock_stat.st_dev,
                    "inode": lock_stat.st_ino,
                    "mode": lock_stat.st_mode & 0o777,
                    "size_bytes": lock_stat.st_size,
                    "sha256": sha256_file(lock_path),
                }
            ),
        },
        "failure_logs": logs,
        "auxiliary_logs": auxiliary_logs,
        "source_identities": execution.manifest["sources"],
        "runtime_identity": execution.manifest["runtime"],
        "task_seed_mapping_hash": execution.manifest["managed_child"][
            "task_seed_mapping_hash"
        ],
    }


def _incident_invariants(value: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(value))
    for key in ("created_at_utc", "created_by", "incident_id", "incident_hash"):
        copied.pop(key, None)
    return copied


def _incident_hash(value: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(_incident_invariants(value)))


def _live_evidence_invariants(value: dict[str, Any]) -> dict[str, Any]:
    """Compare durable evidence without requiring the current checkout to be C2."""

    copied = _incident_invariants(value)
    copied.pop("recovery_control_plane", None)
    return copied


def _validate_recorded_recovery_control_plane(
    repo_root: Path, value: dict[str, Any]
) -> None:
    """Revalidate the clean historical C2 commit recorded by accepted evidence."""

    if value.get("accepted_incident_evidence") is not True:
        return
    control = require_dict(value, "recovery_control_plane")
    commit = control.get("git_commit")
    tree = control.get("git_tree")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(tree, str)
        or not re.fullmatch(r"[0-9a-f]{40}", tree)
        or control.get("checkout_clean") is not True
        or control.get("descends_from_readiness_commit")
        != FORMAL_READINESS_COMMIT
    ):
        raise ValueError("recorded recovery control-plane identity is invalid")
    recorded_tree = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", FORMAL_READINESS_COMMIT, commit],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if recorded_tree.returncode or recorded_tree.stdout.strip() != tree or ancestry.returncode:
        raise ValueError("recorded recovery control-plane Git identity is unavailable")
    artifacts = require_dict(control, "artifacts")
    expected_paths = {
        "hpc/osc/seal_steel_module_production_phase2b_incident.py",
        "hpc/osc/manage_steel_module_production_attempt.py",
        "hpc/osc/steel_module_production_phase2b_lib.py",
    }
    if set(artifacts) != expected_paths:
        raise ValueError("recorded recovery control-plane artifact set mismatch")
    for relative in sorted(expected_paths):
        blob = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        record = require_dict(artifacts, relative)
        if blob.returncode or record.get("sha256") != sha256_bytes(blob.stdout):
            raise ValueError("recorded recovery control-plane artifact mismatch")


def validate_incident_bundle(
    path: Path,
    *,
    execution: ManagedExecution | None = None,
) -> dict[str, Any]:
    """Verify the bundle checksums and every embedded cross-file identity."""

    verify_recursive_checksums(
        path,
        required=(
            "incident.json", "failure_logs.tsv", "event_chain.json",
            "sacct.psv", "squeue.psv", "accounting.json",
        ),
    )
    value = load_json(path / "incident.json")
    if value.get("schema_version") != INCIDENT_SCHEMA_VERSION:
        raise ValueError("incident bundle schema mismatch")
    if value.get("incident_hash") != _incident_hash(value):
        raise ValueError("incident bundle semantic hash mismatch")
    expected_id = (
        "sm-v1-bc-s1-pre-simulation-incident-"
        f"{value['incident_hash'][:12]}"
    )
    if value.get("incident_id") != expected_id:
        raise ValueError("incident bundle ID mismatch")
    attempt = require_dict(value, "attempt")
    expected_name = f"bc-s1-{attempt.get('job_id')}-{value['incident_hash'][:12]}"
    if path.name != expected_name:
        raise ValueError("incident bundle directory identity mismatch")

    event_chain = json.loads((path / "event_chain.json").read_text(encoding="utf-8"))
    if not isinstance(event_chain, list) or not event_chain:
        raise ValueError("incident bundle event chain is empty or invalid")
    previous_event_sha256: str | None = None
    for sequence, event in enumerate(event_chain, start=1):
        if not isinstance(event, dict):
            raise ValueError("incident bundle event-chain entry is invalid")
        unhashed_event = dict(event)
        recorded_event_sha256 = unhashed_event.pop("event_sha256", None)
        if (
            event.get("schema_version") != EVENT_SCHEMA_VERSION
            or event.get("sequence") != sequence
            or event.get("attempt_id") != attempt.get("attempt_id")
            or event.get("intent_sha256") != attempt.get("intent_sha256")
            or event.get("previous_event_sha256") != previous_event_sha256
            or recorded_event_sha256
            != sha256_bytes(canonical_json(unhashed_event))
        ):
            raise ValueError("incident bundle event-chain semantics mismatch")
        previous_event_sha256 = recorded_event_sha256
    if (
        sha256_bytes(canonical_json(event_chain))
        != attempt.get("event_chain_sha256")
        or len(event_chain) != attempt.get("event_count")
        or event_chain[-1].get("event_type") != "terminal-accounting-frozen"
        or event_chain[-1].get("event_sha256")
        != attempt.get("terminal_event_sha256")
    ):
        raise ValueError("incident bundle event-chain binding mismatch")

    sacct_psv = (path / "sacct.psv").read_text(encoding="utf-8")
    squeue_psv = (path / "squeue.psv").read_text(encoding="utf-8")
    accounting = load_json(path / "accounting.json")
    scheduler = require_dict(value, "scheduler")
    incident_execution = require_dict(value, "execution")
    expected_keys = {str(index) for index in range(1, 33)}
    accounting_logical_ids = require_dict(accounting, "task_job_ids")
    accounting_raw_ids = require_dict(accounting, "task_job_ids_raw")
    accounting_states = require_dict(accounting, "task_states")
    accounting_exit_codes = require_dict(accounting, "task_exit_codes")
    if (
        accounting.get("schema_version") != ACCOUNTING_SCHEMA_VERSION_V2
        or accounting.get("accepted_terminal") is not True
        or accounting.get("all_tasks_completed") is not False
        or accounting.get("selected_task_count") != 32
        or accounting.get("terminal_task_count") != 32
        or accounting.get("execution_id") != incident_execution.get("execution_id")
        or accounting.get("execution_hash")
        != incident_execution.get("execution_hash")
        or accounting.get("array_index_identity_field") != "JobID"
        or accounting.get("array_parent_row_present") is not False
        or accounting.get("sacct_field_order")
        != [
            "JobID", "JobIDRaw", "State", "ExitCode", "ElapsedRaw",
            "MaxRSS", "MaxVMSize",
        ]
        or scheduler.get("array_task_count") != 32
        or scheduler.get("logical_job_id_field") != "JobID"
        or scheduler.get("raw_job_id_field") != "JobIDRaw"
        or scheduler.get("sacct_psv_sha256")
        != sha256_bytes(sacct_psv.encode())
        or scheduler.get("squeue_psv_sha256")
        != sha256_bytes(squeue_psv.encode())
        or accounting.get("attempt_id") != attempt.get("attempt_id")
        or accounting.get("intent_sha256") != attempt.get("intent_sha256")
        or accounting.get("job_id") != attempt.get("job_id")
        or accounting.get("task_job_ids") != scheduler.get("task_job_ids")
        or accounting.get("task_job_ids_raw")
        != scheduler.get("task_job_ids_raw")
        or set(accounting_logical_ids) != expected_keys
        or set(accounting_raw_ids) != expected_keys
        or set(accounting_states) != expected_keys
        or set(accounting_exit_codes) != expected_keys
        or any(not value for value in accounting_raw_ids.values())
        or any(
            accounting_logical_ids[str(index)]
            != f"{attempt.get('job_id')}_{index}"
            for index in range(1, 33)
        )
    ):
        raise ValueError("incident bundle accounting binding mismatch")
    state_counts: dict[str, int] = {}
    for state in accounting_states.values():
        state_counts[state] = state_counts.get(state, 0) + 1
    exit_counts: dict[str, int] = {}
    for exit_code in accounting_exit_codes.values():
        exit_counts[exit_code] = exit_counts.get(exit_code, 0) + 1
    if state_counts != scheduler.get("states") or exit_counts != scheduler.get(
        "exit_codes"
    ):
        raise ValueError("incident bundle scheduler summary mismatch")

    accounting_manifest = "".join(
        f"{sha256_file(path / bundled)}  {source}\n"
        for source, bundled in (
            ("frozen.json", "accounting.json"),
            ("sacct.psv", "sacct.psv"),
            ("squeue.psv", "squeue.psv"),
        )
    )
    accounting_manifest_sha256 = sha256_bytes(accounting_manifest.encode())
    terminal_payload = require_dict(event_chain[-1], "payload")
    if (
        accounting_manifest_sha256
        != attempt.get("accounting_manifest_sha256")
        or terminal_payload.get("accounting_manifest_sha256")
        != accounting_manifest_sha256
        or terminal_payload.get("job_id") != attempt.get("job_id")
        or terminal_payload.get("all_tasks_completed")
        != accounting.get("all_tasks_completed")
    ):
        raise ValueError("incident bundle terminal-accounting event mismatch")

    fields = (
        "array_index", "logical_task_id", "path", "size_bytes", "sha256",
        "failure_line",
    )
    with (path / "failure_logs.tsv").open(
        encoding="utf-8", newline=""
    ) as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    if tuple(reader.fieldnames or ()) != fields:
        raise ValueError("incident failure-log index header mismatch")
    expected_rows = [
        {field: str(row[field]) for field in fields}
        for row in value.get("failure_logs", [])
    ]
    if rows != expected_rows or len(rows) != 32:
        raise ValueError("incident failure-log index binding mismatch")

    if execution is not None:
        validate_terminal_accounting_snapshot(
            execution,
            attempt_id=str(attempt["attempt_id"]),
            intent_sha256=str(attempt["intent_sha256"]),
            job_id=str(attempt["job_id"]),
            payload=accounting,
            sacct_psv=sacct_psv,
            squeue_psv=squeue_psv,
        )
        if read_attempt_events(execution, str(attempt["attempt_id"])) != tuple(
            event_chain
        ):
            raise ValueError("incident bundle differs from the live event chain")
    return value


def _existing_incident(
    parent: Path,
    *,
    execution: ManagedExecution,
    attempt_id: str,
    intent_sha256: str,
    job_id: str,
) -> Path | None:
    if not parent.exists():
        return None
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("incident parent is unsafe")
    matches: list[Path] = []
    for path in sorted(parent.glob("bc-s1-*-*")):
        value = validate_incident_bundle(path)
        attempt = require_dict(value, "attempt")
        if (
            require_dict(value, "execution").get("execution_hash")
            == execution.execution_hash
            and attempt.get("attempt_id") == attempt_id
            and attempt.get("intent_sha256") == intent_sha256
            and attempt.get("job_id") == job_id
        ):
            validate_incident_bundle(path, execution=execution)
            matches.append(path)
    if len(matches) > 1:
        raise ValueError("multiple predecessor incident bundles exist")
    return matches[0] if matches else None


def publish_incident_bundle(
    execution: ManagedExecution,
    *,
    repo_root: Path,
    out_parent: Path,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner,
    historical_readiness: dict[str, Any] | None = None,
) -> Path:
    intent, state, readiness = _validate_incident_authority(
        execution,
        repo_root=repo_root,
        attempt_id=attempt_id,
        intent_sha256=intent_sha256,
        actor=actor,
        historical_readiness=historical_readiness,
    )
    job_id = str(state.job_id)
    # Fail before scheduler contact or immutable writes unless the complete
    # local evidence already proves that no simulation output was created.
    _validate_failure_outputs(execution, attempt_id, job_id)
    existing = _existing_incident(
        out_parent,
        execution=execution,
        attempt_id=attempt_id,
        intent_sha256=intent_sha256,
        job_id=job_id,
    )
    if existing is not None:
        if state.status != "terminal-accounting-frozen":
            raise ValueError("published incident lacks the historical terminal event")
        current = collect_incident_evidence(
            execution,
            repo_root=repo_root,
            attempt_id=attempt_id,
            intent_sha256=intent_sha256,
            actor=actor,
            runner=runner,
            historical_readiness=readiness,
        )
        recorded = validate_incident_bundle(existing, execution=execution)
        _validate_recorded_recovery_control_plane(repo_root, recorded)
        if _live_evidence_invariants(recorded) != _live_evidence_invariants(current):
            raise ValueError("published incident no longer matches historical evidence")
        return existing
    if state.status == "released-active":
        freeze_terminal_accounting(
            execution,
            attempt_id=attempt_id,
            intent_sha256=intent_sha256,
            actor=actor,
            runner=runner,
            historical_incident_recovery=_formal_execution_identity(execution),
        )
    evidence = collect_incident_evidence(
        execution,
        repo_root=repo_root,
        attempt_id=attempt_id,
        intent_sha256=intent_sha256,
        actor=actor,
        runner=runner,
        historical_readiness=readiness,
    )
    state = attempt_state(execution, attempt_id)
    if state.status != "terminal-accounting-frozen":
        raise ValueError("incident accounting did not reach its terminal event")
    evidence["attempt"]["terminal_event_sha256"] = state.events[-1]["event_sha256"]
    accounting_dir = execution.directory / "attempts" / attempt_id / "accounting"
    evidence["attempt"]["accounting_manifest_sha256"] = sha256_file(
        accounting_dir / "SHA256SUMS"
    )
    evidence["incident_hash"] = _incident_hash(evidence)
    evidence["incident_id"] = (
        f"sm-v1-bc-s1-pre-simulation-incident-{evidence['incident_hash'][:12]}"
    )
    target = out_parent / f"bc-s1-{evidence['attempt']['job_id']}-{evidence['incident_hash'][:12]}"
    out_parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=out_parent))
    try:
        (temporary / "incident.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        with (temporary / "failure_logs.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            fields = (
                "array_index", "logical_task_id", "path", "size_bytes", "sha256",
                "failure_line",
            )
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(
                {field: row[field] for field in fields} for row in evidence["failure_logs"]
            )
        (temporary / "event_chain.json").write_text(
            json.dumps(read_attempt_events(execution, attempt_id), indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copy2(accounting_dir / "sacct.psv", temporary / "sacct.psv")
        shutil.copy2(accounting_dir / "squeue.psv", temporary / "squeue.psv")
        shutil.copy2(accounting_dir / "frozen.json", temporary / "accounting.json")
        write_recursive_checksums(temporary)
        for path in temporary.iterdir():
            path.chmod(0o444)
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    recorded = validate_incident_bundle(target, execution=execution)
    _validate_recorded_recovery_control_plane(repo_root, recorded)
    return target


def _formal_execution(repo_root: Path) -> ManagedExecution:
    _, phase2a = load_phase2a_lock(repo_root)
    execution_dir = (
        Path(phase2a["canonical_directory"]).resolve().parent / FORMAL_EXECUTION_NAME
    )
    execution = load_historical_closed_execution_for_recovery(
        execution_dir, repo_root=repo_root
    )
    if (
        execution.execution_id != FORMAL_EXECUTION_ID
        or execution.execution_hash != FORMAL_EXECUTION_HASH
    ):
        raise ValueError("formal historical execution identity mismatch")
    return execution


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--seal", action="store_true")
    parser.add_argument("--actor", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if status.returncode or status.stdout:
            raise ValueError("incident sealing requires a clean recovery checkout")
        execution = _formal_execution(repo_root)
        readiness = _historical_readiness_identity(repo_root, execution)
        runner = _historical_scheduler_read_runner(execution, readiness)
        if args.check_only:
            evidence = collect_incident_evidence(
                execution,
                repo_root=repo_root,
                attempt_id=FORMAL_ATTEMPT_ID,
                intent_sha256=FORMAL_INTENT_SHA256,
                actor=args.actor,
                runner=runner,
                historical_readiness=readiness,
            )
            if evidence["attempt"]["job_id"] != FORMAL_JOB_ID:
                raise ValueError("formal incident Slurm job identity mismatch")
            print("steel-module Phase-2B incident preview: PASS")
            print(f"job_id: {evidence['attempt']['job_id']}")
            print("terminal_tasks: 32 FAILED / 1:0")
            print("simulation_started: false")
            print("No file was written and no scheduler command was invoked beyond reads.")
            return 0
        out_parent = execution.directory.parent / "steel-module-production-incidents"
        target = publish_incident_bundle(
            execution,
            repo_root=repo_root,
            out_parent=out_parent,
            attempt_id=FORMAL_ATTEMPT_ID,
            intent_sha256=FORMAL_INTENT_SHA256,
            actor=args.actor,
            runner=runner,
            historical_readiness=readiness,
        )
        incident = load_json(target / "incident.json")
        if require_dict(incident, "attempt").get("job_id") != FORMAL_JOB_ID:
            raise ValueError("sealed incident Slurm job identity mismatch")
        print("steel-module Phase-2B incident sealing: PASS")
        print(f"incident_id: {incident['incident_id']}")
        print(f"incident_hash: {incident['incident_hash']}")
        print(f"output: {target}")
        print("terminal_tasks: 32 FAILED / 1:0")
        print("events_consumed: 0")
        print("production_seeds_consumed: 0")
        print("No production intent was created and no scheduler submission was invoked.")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot seal steel-module Phase-2B incident: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
