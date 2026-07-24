#!/usr/bin/env python3
"""Manage checksum-bound BC-S1 intents and fail-closed Slurm attempts.

The formal interface has no scheduler, account, project, child, or path
override.  Tests exercise the internal functions with an in-process fake
runner; the command-line interface can invoke only the OSC commands named in
this file.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from steel_module_campaign_lib import load_json, require_dict, sha256_file
from steel_module_production_phase2b_lib import (
    ACCOUNTING_SCHEMA_VERSION_V1,
    ACCOUNTING_SCHEMA_VERSION_V2,
    ACCOUNTING_SCHEMA_VERSION,
    ACTOR_RE,
    EXECUTION_SCHEMA_VERSION_PHASE2C_V6,
    FORMAL_ACCOUNT,
    HISTORICAL_CLOSED_ATTEMPT_ID,
    HISTORICAL_CLOSED_INTENT_SHA256,
    HISTORICAL_CLOSED_JOB_ID,
    SUCCESSOR_OBJECT_KIND,
    TERMINAL_FAILURE_STATES,
    ManagedExecution,
    append_attempt_event,
    attempt_state,
    base_slurm_state,
    execution_lock,
    list_attempt_ids,
    load_attempt_intent,
    load_execution_companion,
    load_historical_closed_execution_for_recovery,
    load_frozen_accounting,
    load_phase2a_lock,
    prepare_attempt_intent,
    require_production_execution_open,
    fsync_tree,
    fsync_directory,
    historical_recovery_execution_lock,
    utc_now,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")
MUTATING_COMMANDS = {
    "prepare-intent", "submit-intent", "cancel-intent", "reconcile",
    "release-intent", "freeze-accounting",
}


def require_manager_command_allowed(
    execution: ManagedExecution, command: str
) -> None:
    if command not in {"status", *MUTATING_COMMANDS}:
        raise ValueError("unknown managed production command")
    if command != "status":
        require_production_execution_open(execution)


def _run(
    command: Sequence[str], *, cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=timeout,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "")},
    )


def _run_with(
    runner: CommandRunner, command: Sequence[str], *, cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return runner(list(command), cwd=cwd, timeout=timeout)


def _formal_execution(repo_root: Path, *, readiness: bool) -> ManagedExecution:
    from steel_module_production_phase2c_lib import (
        FORMAL_EXECUTION_V6_NAME,
        load_execution_v6,
    )

    _, phase2a = load_phase2a_lock(repo_root)
    child = Path(phase2a["canonical_directory"])
    execution_dir = child.parent / FORMAL_EXECUTION_V6_NAME
    return load_execution_v6(
        execution_dir,
        repo_root=repo_root,
        require_readiness=readiness,
    )


def _historical_status_execution(repo_root: Path) -> ManagedExecution:
    """Read the one closed v1 companion without granting mutation authority."""

    _, phase2a = load_phase2a_lock(repo_root)
    child = Path(phase2a["canonical_directory"])
    execution_dir = child.parent / "steel-module-production-bc-s1-execution"
    return load_historical_closed_execution_for_recovery(
        execution_dir, repo_root=repo_root
    )


def _successor_directory(repo_root: Path) -> Path:
    from steel_module_production_phase2c_lib import FORMAL_EXECUTION_V6_NAME

    _, phase2a = load_phase2a_lock(repo_root)
    return Path(phase2a["canonical_directory"]).parent / FORMAL_EXECUTION_V6_NAME


def _verify_successor_readiness(
    execution: ManagedExecution, *, repo_root: Path | None
) -> tuple[Path, dict[str, Any]]:
    from steel_module_production_phase2c_readiness import (
        verify_phase2c_readiness,
    )

    return verify_phase2c_readiness(execution, repo_root=repo_root)


def _readiness_sha(repo_root: Path, execution: ManagedExecution) -> str:
    if execution.manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND:
        raise ValueError("the production manager accepts only the recovery successor")
    path, _ = _verify_successor_readiness(execution, repo_root=repo_root)
    return sha256_file(path)


def _intent_readiness_is_current(
    execution: ManagedExecution, intent: dict[str, Any]
) -> None:
    if execution.manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND:
        return
    path, _ = _verify_successor_readiness(execution, repo_root=None)
    if intent.get("readiness_lock_sha256") != sha256_file(path):
        raise ValueError("intent recovery-readiness digest is stale")


def _verify_intent_hash(intent: dict[str, Any], expected: str) -> None:
    if intent.get("intent_sha256") != expected:
        raise ValueError("explicit intent SHA-256 does not match the frozen intent")


def _validate_phase2c_intent_shape(
    execution: ManagedExecution, intent: dict[str, Any]
) -> None:
    from steel_module_production_phase2b_lib import (
        EXECUTION_SCHEMA_VERSION_PHASE2C_V6,
    )

    if execution.manifest.get("schema_version") != EXECUTION_SCHEMA_VERSION_PHASE2C_V6:
        raise ValueError("formal production manager accepts only execution-v6")
    mode = intent.get("mode")
    if mode not in {"predecessor-retry", "resume", "retry-failed"}:
        raise ValueError("execution-v6 intent mode is not authorized")
    selected = require_dict(intent, "selected")
    selected_ids = selected.get("logical_task_ids")
    if not isinstance(selected_ids, list) or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("execution-v6 intent task selection is invalid")
    all_ids = [task.logical_task_id for task in execution.tasks]
    if mode == "predecessor-retry":
        seeds = [
            seed
            for task in execution.tasks
            for seed in (task.seed1, task.seed2)
        ]
        if (
            selected_ids != all_ids
            or selected.get("task_count") != 32
            or selected.get("event_count") != 8000
            or len(seeds) != 64
            or len(set(seeds)) != 64
        ):
            raise ValueError(
                "execution-v6 predecessor-retry must select exact 32/8000/64"
            )
    elif not any(
        attempt_state(execution, attempt_id).status
        == "terminal-accounting-frozen"
        for attempt_id in list_attempt_ids(execution)
    ):
        raise ValueError(
            "execution-v6 resume/retry-failed requires terminal prior evidence"
        )


def submission_command(execution: ManagedExecution, intent: dict[str, Any]) -> list[str]:
    scheduler = intent["scheduler"]
    attempt_id = intent["attempt_id"]
    wrapper = execution.directory / "intents" / attempt_id / scheduler["job_wrapper"]["path"]
    return [
        "sbatch", "--hold", "--parsable", "--no-requeue", "--export=NONE",
        "--time=01:00:00", "--nodes=1", "--ntasks=1", "--cpus-per-task=1",
        "--mem=2G",
        "--account", FORMAL_ACCOUNT, "--job-name", scheduler["job_name"],
        "--array", scheduler["array_spec"], "--output", scheduler["output_pattern"],
        str(wrapper), str(execution.directory), attempt_id, intent["intent_sha256"],
    ]


def _parse_scontrol(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        row: dict[str, str] = {}
        for token in shlex.split(raw):
            if "=" in token:
                key, value = token.split("=", 1)
                row[key] = value
        if row:
            rows.append(row)
    return rows


def _validate_held_job(
    execution: ManagedExecution,
    intent: dict[str, Any],
    job_id: str,
    *,
    runner: CommandRunner,
    require_held: bool = True,
) -> dict[str, str]:
    result = _run_with(
        runner, ["scontrol", "show", "job", job_id, "-o"], cwd=execution.directory
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "scontrol show job failed")
    rows = _parse_scontrol(result.stdout)
    array_rows = [
        row for row in rows
        if row.get("ArrayJobId") == job_id
        or row.get("JobId") == job_id
        or row.get("JobId", "").startswith(job_id + "_")
    ]
    if not array_rows:
        raise ValueError("scontrol did not return the exact array job")
    scheduler = intent["scheduler"]
    expected = {
        "JobName": scheduler["job_name"],
        "Command": str(
            execution.directory / "intents" / intent["attempt_id"]
            / scheduler["job_wrapper"]["path"]
        ),
        "WorkDir": str(execution.directory),
        "Requeue": "0",
    }
    if execution.manifest.get("schema_version") == EXECUTION_SCHEMA_VERSION_PHASE2C_V6:
        expected.update(
            {
                "TimeLimit": "01:00:00",
                "NumCPUs": "1",
                "NumTasks": "1",
                "CPUs/Task": "1",
                "MinMemoryNode": "2G",
            }
        )
    output_pattern = scheduler["output_pattern"]
    output_regex = re.compile(
        "^" + re.escape(output_pattern)
        .replace(re.escape("%A"), re.escape(job_id))
        .replace(re.escape("%a"), r"(?:[0-9]+|\[[0-9,%-]+\])") + "$"
    )
    observed_indices: set[int] = set()
    account_comparison: str | None = None
    for row in array_rows:
        observed_account = row.get("Account")
        if observed_account == FORMAL_ACCOUNT:
            comparison = "exact"
        elif (
            isinstance(observed_account, str)
            and observed_account.isascii()
            and FORMAL_ACCOUNT.isascii()
            and observed_account == FORMAL_ACCOUNT.lower()
        ):
            comparison = "osc-ascii-lowercase-canonicalization"
        else:
            raise ValueError("Slurm held job Account mismatch")
        if account_comparison is not None and comparison != account_comparison:
            raise ValueError("Slurm held job Account representation changed across rows")
        account_comparison = comparison
        for key, value in expected.items():
            if row.get(key) != value:
                raise ValueError(f"Slurm held job {key} mismatch")
        stdout = row.get("StdOut", "")
        if stdout != output_pattern and not output_regex.fullmatch(stdout):
            raise ValueError("Slurm held job output pattern mismatch")
        if require_held and (
            row.get("JobState") != "PENDING"
            or row.get("Reason") != "JobHeldUser"
        ):
            raise ValueError("Slurm job is not held by the submitting user")
        expression = row.get("ArrayTaskId", "")
        if expression in {"", "N/A"}:
            job_value = row.get("JobId", "")
            if "_" in job_value:
                expression = job_value.split("_", 1)[1].strip("[]")
        expression = expression.split("%", 1)[0].strip("[]")
        for part in expression.split(","):
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                if not start.isdigit() or not end.isdigit():
                    raise ValueError("invalid Slurm array-task expression")
                observed_indices.update(range(int(start), int(end) + 1))
            elif part.isdigit():
                observed_indices.add(int(part))
            else:
                raise ValueError("invalid Slurm array-task expression")
    expected_indices = set(range(1, intent["selected"]["task_count"] + 1))
    if observed_indices != expected_indices:
        raise ValueError("Slurm held job array shape mismatch")
    selected = dict(array_rows[0])
    selected["ExpectedAccount"] = FORMAL_ACCOUNT
    selected["ObservedAccount"] = selected["Account"]
    selected["AccountComparison"] = account_comparison or ""
    return selected


def submit_intent(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner = _run,
    release_after_verification: bool = True,
) -> str:
    """Contact Slurm once, initially held; any uncertainty is quarantined."""

    phase2c_v6 = (
        getattr(execution, "manifest", {}).get("schema_version")
        == EXECUTION_SCHEMA_VERSION_PHASE2C_V6
    )
    if phase2c_v6 and release_after_verification:
        raise ValueError(
            "execution-v6 submit must remain held for explicit release-intent"
        )
    require_production_execution_open(execution)
    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    _intent_readiness_is_current(execution, intent)
    if attempt_state(execution, attempt_id).status != "prepared":
        raise ValueError("only a prepared intent may be submitted")
    if intent.get("readiness_lock_sha256") is None:
        raise ValueError("intent is not bound to a readiness lock")
    command = submission_command(execution, intent)
    with execution_lock(execution):
        current_intent = load_attempt_intent(execution, attempt_id)
        _verify_intent_hash(current_intent, intent_sha256)
        attempt_ids = list_attempt_ids(execution)
        states = {value: attempt_state(execution, value) for value in attempt_ids}
        if states[attempt_id].status != "prepared":
            raise ValueError("attempt state changed concurrently")
        competing = [
            value for value, state in states.items()
            if value != attempt_id
            and state.status not in {"cancelled", "terminal-accounting-frozen"}
        ]
        if competing:
            raise ValueError(
                "another intent is prepared, active, or unresolved; submission is locked"
            )
        append_attempt_event(
            execution, attempt_id, "submission-invoked",
            {"command_sha256": __import__("hashlib").sha256("\0".join(command).encode()).hexdigest(),
             "job_name": intent["scheduler"]["job_name"]}, actor=actor,
            lock_held=True,
        )
    try:
        result = _run_with(runner, command, cwd=execution.directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        append_attempt_event(
            execution, attempt_id, "submission-ambiguous",
            {"reason": type(exc).__name__}, actor=actor,
        )
        raise ValueError("Slurm submission outcome is ambiguous; do not retry") from exc
    raw = result.stdout.strip().split(";", 1)[0]
    if result.returncode != 0 or not JOB_ID_RE.fullmatch(raw):
        append_attempt_event(
            execution, attempt_id, "submission-ambiguous",
            {"return_code": result.returncode, "stdout": result.stdout[-1000:],
             "stderr": result.stderr[-1000:]}, actor=actor,
        )
        raise ValueError("Slurm submission outcome is ambiguous; do not retry")
    job_id = raw
    append_attempt_event(
        execution, attempt_id, "submitted-held", {"job_id": job_id}, actor=actor
    )
    try:
        row = _validate_held_job(execution, intent, job_id, runner=runner)
    except ValueError as exc:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": job_id, "reason": str(exc)}, actor=actor,
        )
        raise ValueError("submitted job could not be verified and remains quarantined") from exc
    append_attempt_event(
        execution, attempt_id, "job-verified",
        {
            "job_id": job_id,
            "job_name": row["JobName"],
            "state": row["JobState"],
            "expected_account": row["ExpectedAccount"],
            "observed_account": row["ObservedAccount"],
            "account_comparison": row["AccountComparison"],
        },
        actor=actor,
    )
    if not release_after_verification:
        return job_id
    try:
        released = _run_with(
            runner, ["scontrol", "release", job_id], cwd=execution.directory
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": job_id, "reason": type(exc).__name__}, actor=actor,
        )
        raise ValueError("Slurm release outcome is ambiguous; do not submit again") from exc
    if released.returncode != 0:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": job_id, "return_code": released.returncode,
             "stderr": released.stderr[-1000:]}, actor=actor,
        )
        raise ValueError("Slurm release outcome is ambiguous; do not submit again")
    append_attempt_event(
        execution, attempt_id, "job-released", {"job_id": job_id}, actor=actor
    )
    return job_id


def release_verified_intent(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner = _run,
) -> str:
    """Revalidate and explicitly release one already verified held v6 array."""

    require_production_execution_open(execution)
    with execution_lock(execution):
        intent = load_attempt_intent(execution, attempt_id)
        _verify_intent_hash(intent, intent_sha256)
        _intent_readiness_is_current(execution, intent)
        state = attempt_state(execution, attempt_id)
        if state.status != "verified-held" or state.job_id is None:
            raise ValueError("explicit release requires one verified held attempt")
        job_id = state.job_id
        try:
            matches, raw = _query_matching_jobs(
                execution, intent, runner=runner
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            append_attempt_event(
                execution,
                attempt_id,
                "release-ambiguous",
                {"job_id": job_id, "reason": type(exc).__name__},
                actor=actor,
                lock_held=True,
            )
            raise ValueError("scheduler identity query failed before release") from exc
        matched_ids = [row["job_id"] for row in matches]
        if len(matches) > 1:
            append_attempt_event(
                execution,
                attempt_id,
                "submission-permanently-ambiguous",
                {
                    "job_id": job_id,
                    "match_count": len(matches),
                    "job_ids": matched_ids,
                    "squeue_psv": raw["squeue"],
                    "sacct_psv": raw["sacct"],
                    "release_blocked": True,
                },
                actor=actor,
                lock_held=True,
            )
            raise ValueError(
                "multiple exact scheduler matches; release is permanently blocked"
            )
        if len(matches) != 1 or matched_ids[0] != job_id:
            append_attempt_event(
                execution,
                attempt_id,
                "release-ambiguous",
                {
                    "job_id": job_id,
                    "reason": "recorded-job-not-unique",
                    "job_ids": matched_ids,
                    "squeue_psv": raw["squeue"],
                    "sacct_psv": raw["sacct"],
                },
                actor=actor,
                lock_held=True,
            )
            raise ValueError("recorded held job is not the unique scheduler match")
        try:
            _validate_held_job(execution, intent, job_id, runner=runner)
        except ValueError as exc:
            append_attempt_event(
                execution,
                attempt_id,
                "release-ambiguous",
                {"job_id": job_id, "reason": str(exc)},
                actor=actor,
                lock_held=True,
            )
            raise ValueError("held production array changed before release") from exc
        append_attempt_event(
            execution,
            attempt_id,
            "release-invoked",
            {
                "job_id": job_id,
                "squeue_sha256": __import__("hashlib").sha256(
                    raw["squeue"].encode()
                ).hexdigest(),
                "sacct_sha256": __import__("hashlib").sha256(
                    raw["sacct"].encode()
                ).hexdigest(),
            },
            actor=actor,
            lock_held=True,
        )
        try:
            released = _run_with(
                runner, ["scontrol", "release", job_id], cwd=execution.directory
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            append_attempt_event(
                execution,
                attempt_id,
                "release-outcome-ambiguous",
                {"job_id": job_id, "reason": type(exc).__name__},
                actor=actor,
                lock_held=True,
            )
            raise ValueError("Slurm release outcome is ambiguous") from exc
        if released.returncode != 0:
            append_attempt_event(
                execution,
                attempt_id,
                "release-outcome-ambiguous",
                {
                    "job_id": job_id,
                    "return_code": released.returncode,
                    "stderr": released.stderr[-1000:],
                },
                actor=actor,
                lock_held=True,
            )
            raise ValueError("Slurm release outcome is ambiguous")
        append_attempt_event(
            execution,
            attempt_id,
            "job-released",
            {"job_id": job_id},
            actor=actor,
            lock_held=True,
        )
        return job_id


def _query_matching_jobs(
    execution: ManagedExecution, intent: dict[str, Any], *, runner: CommandRunner
) -> tuple[list[dict[str, str]], dict[str, str]]:
    token = intent["scheduler"]["job_name"]
    commands = {
        "squeue": ["squeue", "-h", "-o", "%A|%j|%a|%T|%r", "--name", token],
        "sacct": ["sacct", "-n", "-P", "-X", "--name", token,
                  "--format=JobID,JobIDRaw,JobName,State,ExitCode"],
    }
    raw: dict[str, str] = {}
    matches: dict[str, dict[str, str]] = {}
    for name, command in commands.items():
        result = _run_with(runner, command, cwd=execution.directory)
        if result.returncode:
            raise ValueError(f"{name} query failed during reconciliation")
        raw[name] = result.stdout
        for line in result.stdout.splitlines():
            values = line.split("|")
            if name == "squeue":
                if len(values) < 2:
                    continue
                logical_job_id, job_name = values[:2]
            else:
                if len(values) < 3:
                    continue
                logical_job_id, _raw_job_id, job_name = values[:3]
            parent = logical_job_id.split("_", 1)[0].split(".", 1)[0]
            if job_name == token and JOB_ID_RE.fullmatch(parent):
                matches.setdefault(parent, {"job_id": parent, "job_name": job_name})
    return sorted(matches.values(), key=lambda row: int(row["job_id"])), raw


def _terminal_job_from_sacct(
    text: str, *, job_id: str, job_name: str, task_count: int
) -> dict[str, str] | None:
    """Recognize a terminal array with or without a separate parent row.

    OSC reports the logical array identity in ``JobID`` while ``JobIDRaw`` is
    an allocation-specific numeric identifier.  In particular, the last
    task's raw ID may equal the array parent ID, and a separate logical parent
    row need not be present.  Array membership must therefore come exclusively
    from ``JobID``.
    """

    terminal = {"COMPLETED", *TERMINAL_FAILURE_STATES}
    parent_rows: list[dict[str, str]] = []
    task_rows: list[dict[str, str]] = []
    for line in text.splitlines():
        values = line.split("|")
        if len(values) < 5:
            continue
        logical_id, raw_id, observed_name, state, exit_code = values[:5]
        if observed_name != job_name:
            continue
        row = {
            "job_id": logical_id, "job_id_raw": raw_id,
            "job_name": observed_name, "state": state, "exit_code": exit_code,
        }
        if logical_id == job_id:
            parent_rows.append(row)
        elif re.fullmatch(rf"{re.escape(job_id)}_[1-9][0-9]*", logical_id):
            task_rows.append(row)
    if len(parent_rows) > 1:
        raise ValueError("sacct returned duplicate exact array-parent rows")
    if parent_rows and base_slurm_state(parent_rows[0]["state"]) in terminal:
        return {**parent_rows[0], "evidence_source": "array-parent"}

    indexed: dict[int, dict[str, str]] = {}
    for row in task_rows:
        index = int(row["job_id"].split("_", 1)[1])
        if index in indexed:
            raise ValueError("sacct returned duplicate logical array-task rows")
        indexed[index] = row
    if set(indexed) != set(range(1, task_count + 1)):
        return None
    if any(base_slurm_state(row["state"]) not in terminal for row in indexed.values()):
        return None
    states = {base_slurm_state(row["state"]) for row in indexed.values()}
    exit_codes = {row["exit_code"] for row in indexed.values()}
    return {
        "job_id": job_id,
        "job_id_raw": "",
        "job_name": job_name,
        "state": next(iter(states)) if len(states) == 1 else "MIXED_TERMINAL",
        "exit_code": next(iter(exit_codes)) if len(exit_codes) == 1 else "MIXED",
        "evidence_source": "complete-array-task-set",
    }


def reconcile_attempt(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    confirm_no_job: bool = False,
    rationale: str | None = None,
    runner: CommandRunner = _run,
    now_utc: str | None = None,
) -> str:
    require_production_execution_open(execution)
    phase2c_v6 = (
        getattr(execution, "manifest", {}).get("schema_version")
        == EXECUTION_SCHEMA_VERSION_PHASE2C_V6
    )
    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    state = attempt_state(execution, attempt_id)
    recoverable_states = {
        "submission-ambiguous", "submitted-held", "verified-held",
        "release-ambiguous", "release-invoked",
    }
    if state.status not in recoverable_states:
        raise ValueError("attempt is not in a recoverable scheduler state")
    matches, raw = _query_matching_jobs(execution, intent, runner=runner)
    observed_at = now_utc or utc_now()
    if len(matches) > 1:
        append_attempt_event(
            execution, attempt_id, "submission-permanently-ambiguous",
            {"match_count": len(matches), "job_ids": [row["job_id"] for row in matches],
             "squeue_psv": raw["squeue"], "sacct_psv": raw["sacct"]},
            actor=actor,
        )
        raise ValueError("multiple exact scheduler matches; attempt is permanently quarantined")
    if len(matches) == 1:
        job_id = matches[0]["job_id"]
        if state.job_id is not None and job_id != state.job_id:
            raise ValueError("scheduler match conflicts with the recorded job ID")
        require_held = state.status in {"submission-ambiguous", "submitted-held"}
        if phase2c_v6 and state.status == "verified-held":
            require_held = True
        try:
            row = _validate_held_job(
                execution, intent, job_id, runner=runner,
                require_held=require_held,
            )
        except ValueError:
            previously_verified = any(
                event["event_type"] == "job-verified" for event in state.events
            )
            terminal_job = _terminal_job_from_sacct(
                raw["sacct"], job_id=job_id,
                job_name=intent["scheduler"]["job_name"],
                task_count=intent["selected"]["task_count"],
            )
            if (
                state.status in {
                    "verified-held", "release-ambiguous", "release-invoked"
                }
                and previously_verified
                and state.job_id == job_id
                and terminal_job is not None
            ):
                append_attempt_event(
                    execution, attempt_id, "job-released",
                    {
                        "job_id": job_id,
                        "reconciled": True,
                        "recovery_source": "terminal-sacct-"
                        + terminal_job["evidence_source"],
                        "observed_state": terminal_job["state"],
                        "observed_exit_code": terminal_job["exit_code"],
                        "sacct_psv": raw["sacct"],
                    },
                    actor=actor,
                )
                return (
                    f"reconciled-terminal-sacct:{job_id}:"
                    f"{terminal_job['state']}"
                )
            raise
        if state.status == "submission-ambiguous":
            append_attempt_event(
                execution, attempt_id, "submitted-held",
                {"job_id": job_id, "adopted": True,
                 "squeue_psv": raw["squeue"], "sacct_psv": raw["sacct"]}, actor=actor,
            )
            append_attempt_event(
                execution, attempt_id, "job-verified",
                {
                    "job_id": job_id, "job_name": row["JobName"],
                    "state": row["JobState"], "reconciled": True,
                    "expected_account": row["ExpectedAccount"],
                    "observed_account": row["ObservedAccount"],
                    "account_comparison": row["AccountComparison"],
                }, actor=actor,
            )
        elif state.status == "submitted-held":
            append_attempt_event(
                execution, attempt_id, "job-verified",
                {
                    "job_id": job_id, "job_name": row["JobName"],
                    "state": row["JobState"], "reconciled": True,
                    "expected_account": row["ExpectedAccount"],
                    "observed_account": row["ObservedAccount"],
                    "account_comparison": row["AccountComparison"],
                }, actor=actor,
            )
        held = (
            row.get("JobState") == "PENDING"
            and row.get("Reason") == "JobHeldUser"
        )
        if held:
            if state.status in {"release-ambiguous", "release-invoked"}:
                append_attempt_event(
                    execution, attempt_id, "job-verified",
                    {
                        "job_id": job_id, "job_name": row["JobName"],
                        "state": row["JobState"], "reconciled": True,
                        "expected_account": row["ExpectedAccount"],
                        "observed_account": row["ObservedAccount"],
                        "account_comparison": row["AccountComparison"],
                    }, actor=actor,
                )
            if phase2c_v6:
                return f"reconciled-held:{job_id}"
            try:
                released = _run_with(
                    runner, ["scontrol", "release", job_id], cwd=execution.directory
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                append_attempt_event(
                    execution, attempt_id, "release-ambiguous",
                    {"job_id": job_id, "reason": type(exc).__name__,
                     "reconciled": True}, actor=actor,
                )
                raise ValueError("reconciled held job release remains ambiguous") from exc
            if released.returncode:
                append_attempt_event(
                    execution, attempt_id, "release-ambiguous",
                    {"job_id": job_id, "return_code": released.returncode,
                     "stderr": released.stderr[-1000:]}, actor=actor,
                )
                raise ValueError("reconciled held job release remains ambiguous")
        # If the same checksum-bound job is already running or terminal, the
        # original release succeeded.  Record that fact and never call sbatch.
        append_attempt_event(
            execution, attempt_id, "job-released",
            {"job_id": job_id, "reconciled": True,
             "observed_state": row.get("JobState")}, actor=actor,
        )
        return f"reconciled-same-job:{job_id}:{row.get('JobState', 'UNKNOWN')}"

    observation_payload = {
        "observed_at_utc": observed_at, "match_count": 0,
        "squeue_psv": raw["squeue"], "sacct_psv": raw["sacct"],
        "squeue_sha256": __import__("hashlib").sha256(raw["squeue"].encode()).hexdigest(),
        "sacct_sha256": __import__("hashlib").sha256(raw["sacct"].encode()).hexdigest(),
    }
    if state.status in {"submitted-held", "verified-held"}:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": state.job_id, "reason": "scheduler-zero-match",
             "reconciled": True}, actor=actor,
        )
        state = attempt_state(execution, attempt_id)
    if state.status in {"release-ambiguous", "release-invoked"}:
        observation_event = (
            "release-invoked-scheduler-observation"
            if state.status == "release-invoked"
            else "release-scheduler-observation"
        )
        append_attempt_event(
            execution, attempt_id, observation_event,
            {**observation_payload, "job_id": state.job_id}, actor=actor,
        )
        if confirm_no_job:
            raise ValueError("a known submitted job cannot be cleared as never submitted")
        return "release-zero-match-observation-recorded"
    append_attempt_event(
        execution, attempt_id, "scheduler-observation", observation_payload,
        actor=actor,
    )
    if not confirm_no_job:
        return "zero-match-observation-recorded"
    if not rationale or not rationale.strip():
        raise ValueError("confirm-no-job requires a non-empty rationale")
    observations = [
        event for event in attempt_state(execution, attempt_id).events
        if event["event_type"] == "scheduler-observation"
        and event["payload"].get("match_count") == 0
    ]
    if len(observations) < 2:
        raise ValueError("confirm-no-job requires two zero-match observations")
    timestamps = [
        datetime.fromisoformat(event["payload"]["observed_at_utc"])
        for event in observations[-2:]
    ]
    if (timestamps[1] - timestamps[0]).total_seconds() < 600:
        raise ValueError("zero-match observations must be at least 10 minutes apart")
    append_attempt_event(
        execution, attempt_id, "submission-not-found-confirmed",
        {"reviewer": actor, "rationale": rationale.strip(),
         "observation_event_hashes": [event["event_sha256"] for event in observations[-2:]]},
        actor=actor,
    )
    return "no-job-confirmed"


def _parse_sacct_rows(
    text: str,
    names: tuple[str, ...] = (
        "job_id", "job_id_raw", "state", "exit_code", "elapsed_raw",
        "max_rss", "max_vmsize",
    ),
) -> list[dict[str, str]]:
    rows = []
    for raw in text.splitlines():
        values = raw.split("|")
        if len(values) == len(names) + 1 and values[-1] == "":
            values.pop()
        if len(values) != len(names):
            if raw.strip():
                raise ValueError(f"invalid sacct row: {raw!r}")
            continue
        rows.append(dict(zip(names, values)))
    return rows


def validate_terminal_accounting_snapshot(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    job_id: str,
    payload: dict[str, Any],
    sacct_psv: str,
    squeue_psv: str,
) -> None:
    """Cross-reconcile frozen maps with the exact scheduler text."""

    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    task_count = intent["selected"]["task_count"]
    expected_indices = set(range(1, task_count + 1))
    expected_keys = {str(index) for index in expected_indices}
    if squeue_psv.strip():
        raise ValueError("frozen squeue evidence still contains an active job")
    if (
        payload.get("execution_id") != execution.execution_id
        or payload.get("execution_hash") != execution.execution_hash
        or payload.get("attempt_id") != attempt_id
        or payload.get("intent_sha256") != intent_sha256
        or payload.get("job_id") != job_id
        or payload.get("selected_task_count") != task_count
        or payload.get("terminal_task_count") != task_count
        or payload.get("accepted_terminal") is not True
    ):
        raise ValueError("terminal accounting snapshot identity mismatch")

    schema = payload.get("schema_version")
    if schema == ACCOUNTING_SCHEMA_VERSION_V2:
        names = (
            "job_id", "job_id_raw", "state", "exit_code", "elapsed_raw",
            "max_rss", "max_vmsize",
        )
        logical_field = "job_id"
        raw_field = "job_id_raw"
        if (
            payload.get("sacct_field_order")
            != [
                "JobID", "JobIDRaw", "State", "ExitCode", "ElapsedRaw",
                "MaxRSS", "MaxVMSize",
            ]
            or payload.get("array_index_identity_field") != "JobID"
        ):
            raise ValueError("terminal accounting v2 field identity mismatch")
    elif schema == ACCOUNTING_SCHEMA_VERSION_V1:
        names = (
            "job_id_raw", "state", "exit_code", "elapsed_raw", "max_rss",
            "max_vmsize",
        )
        logical_field = "job_id_raw"
        raw_field = "job_id_raw"
    else:
        raise ValueError("unsupported terminal accounting snapshot schema")

    rows = _parse_sacct_rows(sacct_psv, names)
    parent_rows = [row for row in rows if row[logical_field] == job_id]
    task_rows: dict[int, dict[str, str]] = {}
    pattern = re.compile(rf"{re.escape(job_id)}_([1-9][0-9]*)")
    raw_task_count = 0
    for row in rows:
        match = pattern.fullmatch(row[logical_field])
        if match is None:
            continue
        raw_task_count += 1
        index = int(match.group(1))
        if index in task_rows:
            raise ValueError("duplicate logical task in terminal accounting snapshot")
        task_rows[index] = row
    if (
        raw_task_count != task_count
        or set(task_rows) != expected_indices
        or len(parent_rows) > 1
        or (schema == ACCOUNTING_SCHEMA_VERSION_V1 and len(parent_rows) != 1)
    ):
        raise ValueError("terminal accounting snapshot lacks the exact array set")
    terminal = {"COMPLETED", *TERMINAL_FAILURE_STATES}
    if any(base_slurm_state(row["state"]) not in terminal for row in task_rows.values()):
        raise ValueError("terminal accounting snapshot contains a non-terminal task")
    if parent_rows and base_slurm_state(parent_rows[0]["state"]) not in terminal:
        raise ValueError("terminal accounting snapshot parent is not terminal")

    task_states = require_dict(payload, "task_states")
    task_exit_codes = require_dict(payload, "task_exit_codes")
    if set(task_states) != expected_keys or set(task_exit_codes) != expected_keys:
        raise ValueError("terminal accounting snapshot task maps are incomplete")
    for index, row in task_rows.items():
        key = str(index)
        if (
            task_states[key] != row["state"]
            or task_exit_codes[key] != row["exit_code"]
        ):
            raise ValueError("terminal accounting maps disagree with sacct evidence")
    if schema == ACCOUNTING_SCHEMA_VERSION_V2:
        logical_ids = require_dict(payload, "task_job_ids")
        raw_ids = require_dict(payload, "task_job_ids_raw")
        if set(logical_ids) != expected_keys or set(raw_ids) != expected_keys:
            raise ValueError("terminal accounting v2 job-ID maps are incomplete")
        for index, row in task_rows.items():
            key = str(index)
            if (
                not row[raw_field]
                or not raw_ids[key]
                or logical_ids[key] != row[logical_field]
                or raw_ids[key] != row[raw_field]
            ):
                raise ValueError("terminal accounting job-ID maps disagree with sacct")
        if (
            payload.get("array_parent_row_present") != bool(parent_rows)
            or payload.get("array_parent_state")
            != (parent_rows[0]["state"] if parent_rows else None)
            or payload.get("array_parent_exit_code")
            != (parent_rows[0]["exit_code"] if parent_rows else None)
        ):
            raise ValueError("terminal accounting parent metadata disagrees with sacct")
    all_completed = all(
        base_slurm_state(row["state"]) == "COMPLETED" and row["exit_code"] == "0:0"
        for row in task_rows.values()
    )
    if payload.get("all_tasks_completed") is not all_completed:
        raise ValueError("terminal accounting completion flag disagrees with sacct")


def preview_terminal_accounting(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner = _run,
) -> dict[str, Any]:
    """Collect and validate terminal accounting without writing evidence.

    The returned object contains the future ``frozen.json`` payload together
    with the exact scheduler text that would be checksum-frozen.  This is the
    read-only half of the incident-sealing two-step flow.
    """

    if not isinstance(actor, str) or not ACTOR_RE.fullmatch(actor):
        raise ValueError("terminal accounting actor identity is invalid")
    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    state = attempt_state(execution, attempt_id)
    if state.status != "released-active" or state.job_id is None:
        raise ValueError("terminal accounting preview requires one released attempt")
    job_id = state.job_id
    sacct = _run_with(
        runner,
        ["sacct", "-n", "-P", "-j", job_id,
         "--format=JobID,JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS,MaxVMSize"],
        cwd=execution.directory,
    )
    squeue = _run_with(
        runner,
        ["squeue", "-h", "-o", "%A|%j|%a|%T|%r", "--name",
         intent["scheduler"]["job_name"]],
        cwd=execution.directory,
    )
    if sacct.returncode or squeue.returncode:
        raise ValueError("cannot collect complete scheduler accounting")
    if squeue.stdout.strip():
        raise ValueError("Slurm still reports active tasks; accounting is not terminal")
    rows = _parse_sacct_rows(sacct.stdout)
    task_count = intent["selected"]["task_count"]
    parent_rows = [row for row in rows if row["job_id"] == job_id]
    if len(parent_rows) > 1:
        raise ValueError("sacct contains duplicate exact array-parent accounting rows")
    raw_task_rows = [
        row for row in rows
        if re.fullmatch(rf"{re.escape(job_id)}_[1-9][0-9]*", row["job_id"])
    ]
    task_rows = {
        int(row["job_id"].split("_", 1)[1]): row for row in raw_task_rows
    }
    if (
        len(raw_task_rows) != task_count
        or len(task_rows) != task_count
        or set(task_rows) != set(range(1, task_count + 1))
    ):
        raise ValueError("sacct lacks the exact array-task accounting set")
    terminal = {"COMPLETED", *TERMINAL_FAILURE_STATES}
    if any(base_slurm_state(row["state"]) not in terminal for row in task_rows.values()):
        raise ValueError("sacct contains non-terminal array tasks")
    if parent_rows and base_slurm_state(parent_rows[0]["state"]) not in terminal:
        raise ValueError("sacct array parent is not terminal")
    if any(not row["job_id_raw"] for row in task_rows.values()):
        raise ValueError("sacct array task lacks JobIDRaw provenance")
    all_completed = all(
        base_slurm_state(row["state"]) == "COMPLETED" and row["exit_code"] == "0:0"
        for row in task_rows.values()
    )
    payload = {
        "schema_version": ACCOUNTING_SCHEMA_VERSION,
        "created_at_utc": utc_now(), "created_by": actor,
        "execution_id": execution.execution_id, "execution_hash": execution.execution_hash,
        "attempt_id": attempt_id, "intent_sha256": intent_sha256, "job_id": job_id,
        "selected_task_count": task_count, "terminal_task_count": len(task_rows),
        "accepted_terminal": True, "all_tasks_completed": all_completed,
        "sacct_field_order": [
            "JobID", "JobIDRaw", "State", "ExitCode", "ElapsedRaw",
            "MaxRSS", "MaxVMSize",
        ],
        "array_index_identity_field": "JobID",
        "array_parent_row_present": bool(parent_rows),
        "array_parent_state": parent_rows[0]["state"] if parent_rows else None,
        "array_parent_exit_code": parent_rows[0]["exit_code"] if parent_rows else None,
        "task_job_ids": {
            str(index): task_rows[index]["job_id"] for index in sorted(task_rows)
        },
        "task_job_ids_raw": {
            str(index): task_rows[index]["job_id_raw"] for index in sorted(task_rows)
        },
        "task_states": {str(index): task_rows[index]["state"] for index in sorted(task_rows)},
        "task_exit_codes": {
            str(index): task_rows[index]["exit_code"] for index in sorted(task_rows)
        },
    }
    validate_terminal_accounting_snapshot(
        execution,
        attempt_id=attempt_id,
        intent_sha256=intent_sha256,
        job_id=job_id,
        payload=payload,
        sacct_psv=sacct.stdout,
        squeue_psv=squeue.stdout,
    )
    return {
        "payload": payload,
        "sacct_psv": sacct.stdout,
        "squeue_psv": squeue.stdout,
    }


def freeze_terminal_accounting(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner = _run,
    historical_incident_recovery: bool = False,
) -> dict[str, Any]:
    if not isinstance(actor, str) or not ACTOR_RE.fullmatch(actor):
        raise ValueError("terminal accounting actor identity is invalid")
    if not historical_incident_recovery:
        require_production_execution_open(execution)
    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    state = attempt_state(execution, attempt_id)
    if (
        state.status not in {"released-active", "terminal-accounting-frozen"}
        or state.job_id is None
    ):
        raise ValueError("terminal accounting requires one released attempt")
    job_id = state.job_id
    if historical_incident_recovery:
        if (
            attempt_id != HISTORICAL_CLOSED_ATTEMPT_ID
            or intent_sha256 != HISTORICAL_CLOSED_INTENT_SHA256
            or job_id != HISTORICAL_CLOSED_JOB_ID
        ):
            raise ValueError("historical incident accounting authority mismatch")

    def accounting_lock() -> Any:
        if historical_incident_recovery:
            return historical_recovery_execution_lock(execution)
        return execution_lock(execution)

    target = execution.directory / "attempts" / attempt_id / "accounting"
    with accounting_lock():
        current = attempt_state(execution, attempt_id)
        if current.job_id != job_id:
            raise ValueError("attempt job identity changed before terminal accounting")
        if current.status == "terminal-accounting-frozen":
            frozen = load_frozen_accounting(execution, attempt_id)
            if (
                historical_incident_recovery
                and frozen.get("schema_version") != ACCOUNTING_SCHEMA_VERSION_V2
            ):
                raise ValueError(
                    "historical incident requires logical/raw JobID accounting v2"
                )
            return frozen
        if current.status != "released-active":
            raise ValueError("attempt changed before terminal accounting")
        if target.exists() or target.is_symlink():
            if target.is_symlink():
                raise ValueError("terminal accounting path is an unsafe symlink")
            recovered = load_frozen_accounting(
                execution, attempt_id, require_event_binding=False
            )
            task_count = intent["selected"]["task_count"]
            expected_indices = {str(index) for index in range(1, task_count + 1)}
            if (
                (
                    historical_incident_recovery
                    and recovered.get("schema_version")
                    != ACCOUNTING_SCHEMA_VERSION_V2
                )
                or recovered.get("execution_id") != execution.execution_id
                or recovered.get("execution_hash") != execution.execution_hash
                or recovered.get("job_id") != job_id
                or recovered.get("selected_task_count") != task_count
                or recovered.get("terminal_task_count") != task_count
                or recovered.get("accepted_terminal") is not True
                or set(require_dict(recovered, "task_states")) != expected_indices
                or set(require_dict(recovered, "task_exit_codes")) != expected_indices
            ):
                raise ValueError("existing terminal accounting cannot be recovered")
            validate_terminal_accounting_snapshot(
                execution,
                attempt_id=attempt_id,
                intent_sha256=intent_sha256,
                job_id=job_id,
                payload=recovered,
                sacct_psv=(target / "sacct.psv").read_text(encoding="utf-8"),
                squeue_psv=(target / "squeue.psv").read_text(encoding="utf-8"),
            )
            append_attempt_event(
                execution, attempt_id, "terminal-accounting-frozen",
                {
                    "job_id": job_id,
                    "accounting_manifest_sha256": sha256_file(
                        target / "SHA256SUMS"
                    ),
                    "all_tasks_completed": recovered.get("all_tasks_completed"),
                    "recovered_after_publish": True,
                },
                actor=actor,
                lock_held=True,
                historical_incident_recovery=historical_incident_recovery,
            )
            return recovered
        snapshot = preview_terminal_accounting(
            execution,
            attempt_id=attempt_id,
            intent_sha256=intent_sha256,
            actor=actor,
            runner=runner,
        )
        payload = require_dict(snapshot, "payload")
        all_completed = payload["all_tasks_completed"]
        temp = Path(tempfile.mkdtemp(prefix=".accounting-", dir=target.parent))
        try:
            (temp / "sacct.psv").write_text(
                snapshot["sacct_psv"], encoding="utf-8"
            )
            (temp / "squeue.psv").write_text(
                snapshot["squeue_psv"], encoding="utf-8"
            )
            (temp / "frozen.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
            (temp / "SHA256SUMS").write_text(
                "".join(
                    f"{sha256_file(temp / name)}  {name}\n"
                    for name in ("frozen.json", "sacct.psv", "squeue.psv")
                ),
                encoding="utf-8",
            )
            for path in temp.iterdir():
                path.chmod(0o444)
            fsync_tree(temp)
            if target.exists() or target.is_symlink():
                raise ValueError("terminal accounting appeared concurrently")
            os.rename(temp, target)
            fsync_directory(target.parent)
            append_attempt_event(
                execution, attempt_id, "terminal-accounting-frozen",
                {
                    "job_id": job_id,
                    "accounting_manifest_sha256": sha256_file(
                        target / "SHA256SUMS"
                    ),
                    "all_tasks_completed": all_completed,
                },
                actor=actor,
                lock_held=True,
                historical_incident_recovery=historical_incident_recovery,
            )
            return payload
        finally:
            if temp.exists():
                shutil.rmtree(temp)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", allow_abbrev=False)

    prepare = sub.add_parser("prepare-intent", allow_abbrev=False)
    prepare.add_argument("--attempt-id", required=True)
    prepare.add_argument(
        "--mode",
        required=True,
        choices=("predecessor-retry", "resume", "retry-failed"),
    )
    prepare_mode = prepare.add_mutually_exclusive_group(required=True)
    prepare_mode.add_argument("--check-only", action="store_true")
    prepare_mode.add_argument("--write-intent", action="store_true")
    prepare.add_argument("--actor", default=getpass.getuser())

    submit = sub.add_parser("submit-intent", allow_abbrev=False)
    submit.add_argument("--attempt-id", required=True)
    submit.add_argument("--intent-sha256", required=True)
    submit_mode = submit.add_mutually_exclusive_group(required=True)
    submit_mode.add_argument("--check-only", action="store_true")
    submit_mode.add_argument("--submit", action="store_true")
    submit.add_argument("--actor", default=getpass.getuser())

    release = sub.add_parser("release-intent", allow_abbrev=False)
    release.add_argument("--attempt-id", required=True)
    release.add_argument("--intent-sha256", required=True)
    release_mode = release.add_mutually_exclusive_group(required=True)
    release_mode.add_argument("--check-only", action="store_true")
    release_mode.add_argument("--release", action="store_true")
    release.add_argument("--actor", default=getpass.getuser())

    cancel = sub.add_parser("cancel-intent", allow_abbrev=False)
    cancel.add_argument("--attempt-id", required=True)
    cancel.add_argument("--intent-sha256", required=True)
    cancel.add_argument("--actor", default=getpass.getuser())

    reconcile = sub.add_parser("reconcile", allow_abbrev=False)
    reconcile.add_argument("--attempt-id", required=True)
    reconcile.add_argument("--intent-sha256", required=True)
    reconcile.add_argument("--actor", default=getpass.getuser())
    reconcile.add_argument("--confirm-no-job", action="store_true")
    reconcile.add_argument("--rationale-file", type=Path)

    accounting = sub.add_parser("freeze-accounting", allow_abbrev=False)
    accounting.add_argument("--attempt-id", required=True)
    accounting.add_argument("--intent-sha256", required=True)
    accounting.add_argument("--actor", default=getpass.getuser())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "status":
            historical = _historical_status_execution(repo_root)
            successor_dir = _successor_directory(repo_root)
            if successor_dir.exists() or successor_dir.is_symlink():
                if successor_dir.is_symlink() or not successor_dir.is_dir():
                    raise ValueError("canonical recovery-successor path is unsafe")
                from steel_module_production_phase2c_lib import load_execution_v6

                execution = load_execution_v6(
                    successor_dir,
                    repo_root=repo_root,
                    require_readiness=False,
                )
                try:
                    _verify_successor_readiness(
                        execution, repo_root=repo_root
                    )
                except (OSError, RuntimeError, ValueError):
                    submission_ready = False
                else:
                    submission_ready = True
                print(f"execution_id: {execution.execution_id}")
                print(f"execution_hash: {execution.execution_hash}")
                print(f"submission_ready: {str(submission_ready).lower()}")
                print("historical_execution_closed: true")
                print(f"predecessor_execution_id: {historical.execution_id}")
                print("execution_v6_present: true")
                print(f"intents: {len(list_attempt_ids(execution))}")
                for attempt_id in list_attempt_ids(execution):
                    state = attempt_state(execution, attempt_id)
                    print(f"{attempt_id}: {state.status} job={state.job_id or '-'}")
                return 0

            execution = historical
            print(f"execution_id: {execution.execution_id}")
            print(f"execution_hash: {execution.execution_hash}")
            print("submission_ready: false")
            print("historical_execution_closed: true")
            print("execution_v6_present: false")
            print(f"intents: {len(list_attempt_ids(execution))}")
            for attempt_id in list_attempt_ids(execution):
                state = attempt_state(execution, attempt_id)
                print(f"{attempt_id}: {state.status} job={state.job_id or '-'}")
            return 0

        execution = _formal_execution(repo_root, readiness=True)
        require_manager_command_allowed(execution, args.command)
        readiness_sha = _readiness_sha(repo_root, execution)
        if args.command == "prepare-intent":
            if args.mode in {"resume", "retry-failed"} and not any(
                attempt_state(execution, attempt_id).status
                == "terminal-accounting-frozen"
                for attempt_id in list_attempt_ids(execution)
            ):
                raise ValueError(
                    "execution-v6 resume/retry-failed requires terminal prior evidence"
                )
            value = prepare_attempt_intent(
                execution, attempt_id=args.attempt_id, mode=args.mode, actor=args.actor,
                write=args.write_intent, readiness_lock_sha256=readiness_sha,
            )
            _validate_phase2c_intent_shape(execution, value)
            print(f"intent: {'written' if args.write_intent else 'check-only'}")
            print(f"attempt_id: {value['attempt_id']}")
            print(f"intent_sha256: {value['intent_sha256']}")
            print(f"tasks: {value['selected']['task_count']}")
            print("No scheduler command was invoked.")
            return 0

        intent = load_attempt_intent(execution, args.attempt_id)
        _verify_intent_hash(intent, args.intent_sha256)
        _validate_phase2c_intent_shape(execution, intent)
        _intent_readiness_is_current(execution, intent)
        if intent.get("readiness_lock_sha256") != readiness_sha:
            raise ValueError("intent readiness-lock digest is stale")
        if args.command == "submit-intent":
            if args.check_only:
                print("submit-intent check: PASS")
                print("command:", shlex.join(submission_command(execution, intent)))
                print("No scheduler command was invoked.")
            else:
                job_id = submit_intent(
                    execution, attempt_id=args.attempt_id,
                    intent_sha256=args.intent_sha256, actor=args.actor,
                    release_after_verification=False,
                )
                print(f"submitted and verified held Slurm array: {job_id}")
                print("The array remains held; use release-intent separately.")
            return 0
        if args.command == "release-intent":
            state = attempt_state(execution, args.attempt_id)
            if state.status != "verified-held" or state.job_id is None:
                raise ValueError("release check requires one verified held attempt")
            if args.check_only:
                print("release-intent check: PASS")
                print("command:", shlex.join(["scontrol", "release", state.job_id]))
                print("No scheduler command was invoked.")
            else:
                job_id = release_verified_intent(
                    execution,
                    attempt_id=args.attempt_id,
                    intent_sha256=args.intent_sha256,
                    actor=args.actor,
                )
                print(f"released verified held Slurm array: {job_id}")
            return 0
        if args.command == "cancel-intent":
            if attempt_state(execution, args.attempt_id).status != "prepared":
                raise ValueError("only a never-submitted prepared intent may be cancelled")
            append_attempt_event(
                execution, args.attempt_id, "intent-cancelled",
                {"reason": "explicit-user-cancellation"}, actor=args.actor,
            )
            print("intent cancelled; no scheduler command was invoked")
            return 0
        if args.command == "reconcile":
            rationale = None
            if args.rationale_file is not None:
                rationale = args.rationale_file.read_text(encoding="utf-8")
            result = reconcile_attempt(
                execution, attempt_id=args.attempt_id,
                intent_sha256=args.intent_sha256, actor=args.actor,
                confirm_no_job=args.confirm_no_job, rationale=rationale,
            )
            print(result)
            return 0
        if args.command == "freeze-accounting":
            value = freeze_terminal_accounting(
                execution, attempt_id=args.attempt_id,
                intent_sha256=args.intent_sha256, actor=args.actor,
            )
            print("terminal accounting frozen")
            print(f"job_id: {value['job_id']}")
            print(f"all_tasks_completed: {value['all_tasks_completed']}")
            return 0
        raise AssertionError("unreachable command")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Cannot manage steel-module production attempt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
