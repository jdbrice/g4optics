#!/usr/bin/env python3
"""Fail-closed evidence for the fixed rejected execution-v3 R3 probe.

This module seals externally collected scheduler text and the already-created
raw probe workspace.  It never queries or mutates Slurm, never runs Apptainer,
and never turns a rejected probe into accepted compute-preflight evidence.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from steel_module_campaign_lib import canonical_json, load_json, sha256_bytes, sha256_file
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    recursive_file_records,
    verify_recursive_checksums,
    write_recursive_checksums,
)
from steel_module_production_phase2b_lib import ROOT_STATIC_MANIFEST, fsync_tree
from steel_module_production_r3_probe_lib import (
    RAW_PROBE_SCHEMA_VERSION_V2,
    RAW_WORKSPACE_FILES,
    REPORT_KEYS,
    _execution_snapshot,
    _held_scheduler_identity,
    _parse_accounting_rows,
    canonical_r3_evidence_root,
    expected_r3_job_name,
    validate_rejected_raw_probe_workspace,
)
from steel_module_production_successor_lib import (
    SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3,
    load_successor_execution,
    rejected_successor_v3_mountpoint_binding,
    validate_rejected_successor_v3_boundary,
)


REJECTION_SCHEMA_VERSION = (
    "steel-module-production-r3-container-probe-rejection-evidence-v1"
)
SNAPSHOT_SCHEMA_VERSION = "steel-module-production-r3-execution-snapshot-v2"
FORMAL_EXECUTION_NAME = "steel-module-production-bc-s1-execution-v3"
FORMAL_EXECUTION_ID = "sm-v1-production-bc-s1-execution-v3-e3bd626fe694"
FORMAL_EXECUTION_HASH = (
    "30d01a2a03d97f3cc5bb54cf1364d5259ccabc0fbfe2aeefa04d59e441b9781a"
)
FORMAL_JOB_ID = "50548308"
FORMAL_JOB_NAME = "g4sm-r3-30d01a2a03d9"
FORMAL_PROBE_TOKEN = "48d49c5eac79a554858f9a640c1d8a28"
FORMAL_CONTAINER_PROBE_ROOT = (
    "/work/g4optics-execution/attempts/.r3-probe-" + FORMAL_PROBE_TOKEN
)
FORMAL_ACCOUNT = "PAS2524"
FORMAL_CLASSIFICATION = "container-isolation-writer-open-rejection-failure"
FORMAL_FAILURE_STAGE = "post-apptainer-container-contract-evaluation"
FORMAL_FALSE_REPORT_KEYS = frozenset(
    {"static_writer_open_rejected", "control_lock_writer_open_rejected"}
)
FORMAL_INPUT_SHA256 = {
    "held_scontrol": (
        "4cd17d0100d51b11b17b556d3173206702065f6e0ea241b130fd3052100a3f55"
    ),
    "slurm_output": (
        "4d2f0fc4e62e05c9e2a5bde98a61ca3f2fe41e2452f5e7d5cff7ead15f25ef00"
    ),
    "sacct": "4c44b4cd8b487b82686b9d5fb19b2ad371d8391b69a3e6fb351de812e0e28e7a",
    "squeue": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "probe_result": (
        "d7317c8bea60f2e988c79daaba5f4e70dcb35a4e2ed13719c8c002ea451b3b5a"
    ),
}
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")

REJECTION_BUNDLE_BASE_FILES = {
    "execution_snapshot.json",
    "held_scontrol.txt",
    "rejection.json",
    "sacct.psv",
    "squeue.psv",
    *(f"raw/{name}" for name in RAW_WORKSPACE_FILES),
}


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def _rejection_hash(value: Mapping[str, Any]) -> str:
    unhashed = dict(value)
    unhashed.pop("rejection_id", None)
    unhashed.pop("rejection_hash", None)
    return _canonical_hash(unhashed)


def _require_regular_input(path: Path, label: str) -> Path:
    requested = path.expanduser()
    if requested.is_symlink() or not requested.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    resolved = requested.resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} resolved to an unsafe input")
    return resolved


def _snapshot_payload(
    execution: Path, *, historical_mountpoint: dict[str, Any]
) -> dict[str, Any]:
    records, snapshot_hash = _execution_snapshot(execution)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "execution_directory": str(execution),
        "regular_file_snapshot_scope": "recursive-regular-files-only",
        "regular_file_records": records,
        "regular_file_snapshot_hash": snapshot_hash,
        "regular_file_snapshot_record_count": len(records),
        "historical_mountpoint": json.loads(json.dumps(historical_mountpoint)),
    }


def _stable_snapshot_payload(
    execution: Any,
    *,
    probe_token: str,
    container_probe_root: str,
    repo_root: Path,
    recorded_mountpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Snapshot regular files between two exact mountpoint validations."""

    before = validate_rejected_successor_v3_boundary(
        execution,
        probe_token=probe_token,
        container_probe_root=container_probe_root,
        repo_root=repo_root,
        recorded_mountpoint=recorded_mountpoint,
    )
    snapshot = _snapshot_payload(
        execution.directory, historical_mountpoint=before
    )
    after = validate_rejected_successor_v3_boundary(
        execution,
        probe_token=probe_token,
        container_probe_root=container_probe_root,
        repo_root=repo_root,
        recorded_mountpoint=before,
    )
    if after != before or snapshot["historical_mountpoint"] != after:
        raise ValueError("historical R3 mountpoint changed during snapshot")
    return snapshot


def _validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if set(snapshot) != {
        "schema_version",
        "execution_directory",
        "regular_file_snapshot_scope",
        "regular_file_records",
        "regular_file_snapshot_hash",
        "regular_file_snapshot_record_count",
        "historical_mountpoint",
    }:
        raise ValueError("R3 rejection execution snapshot field set mismatch")
    records = snapshot.get("regular_file_records")
    mountpoint = snapshot.get("historical_mountpoint")
    if not isinstance(mountpoint, dict):
        raise ValueError("R3 rejection execution mountpoint snapshot is invalid")
    expected_mountpoint = rejected_successor_v3_mountpoint_binding(
        probe_token=str(mountpoint.get("probe_token", "")),
        container_probe_root=str(mountpoint.get("container_probe_root", "")),
    )
    if (
        snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(snapshot.get("execution_directory"), str)
        or snapshot.get("regular_file_snapshot_scope")
        != "recursive-regular-files-only"
        or not isinstance(records, dict)
        or not records
        or list(records) != sorted(records)
        or any(
            not isinstance(name, str)
            or not name
            or name.startswith("/")
            or ".." in Path(name).parts
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            for name, digest in records.items()
        )
        or snapshot.get("regular_file_snapshot_hash") != _canonical_hash(records)
        or snapshot.get("regular_file_snapshot_record_count") != len(records)
        or mountpoint != expected_mountpoint
    ):
        raise ValueError("R3 rejection execution snapshot is invalid")
    return snapshot


def _load_execution(
    execution_dir: Path, *, repo_root: Path | None, test_mode: bool
) -> Any:
    if not test_mode and repo_root is None:
        raise ValueError(
            "formal R3 rejection validation requires a repository authority root"
        )
    frozen_control = execution_dir.expanduser().resolve() / "sources/control"
    frozen_control_mode = (
        not test_mode
        and repo_root is not None
        and repo_root.expanduser().resolve() == frozen_control
    )
    execution = load_successor_execution(
        execution_dir,
        repo_root=None if test_mode else repo_root,
        allow_test_mode=test_mode,
        require_readiness=False,
        verify_runtime=not test_mode,
        verify_phase2a_control_plane=not frozen_control_mode,
        verify_live_predecessor=not frozen_control_mode,
    )
    if execution.manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3:
        raise ValueError("R3 rejection requires the execution-v3 successor")
    if not test_mode and (
        execution.directory.name != FORMAL_EXECUTION_NAME
        or execution.execution_id != FORMAL_EXECUTION_ID
        or execution.execution_hash != FORMAL_EXECUTION_HASH
    ):
        raise ValueError("formal R3 rejection execution identity mismatch")
    return execution


def _parse_failed_accounting(
    text: str, *, job_id: str, job_name: str
) -> list[dict[str, str]]:
    rows = _parse_accounting_rows(text)
    expected = (
        (job_id, job_name, "FAILED", "1:0"),
        (f"{job_id}.batch", "batch", "FAILED", "1:0"),
        (f"{job_id}.extern", "extern", "COMPLETED", "0:0"),
    )
    if len(rows) != len(expected):
        raise ValueError("R3 rejection accounting must contain parent, batch, extern")
    for row, (expected_id, expected_name, state, exit_code) in zip(rows, expected):
        if (
            row["job_id"] != expected_id
            or row["job_name"] != expected_name
            or row["account"].casefold() != FORMAL_ACCOUNT.casefold()
            or row["state"] != state
            or row["exit_code"] != exit_code
            or not row["elapsed_raw"].isdigit()
        ):
            raise ValueError("R3 rejection terminal accounting identity mismatch")
    return rows


def _expected_log(raw_workspace: Path) -> str:
    return (
        "Cannot run steel-module R3 container probe: R3 container isolation "
        f"probe failed; evidence: {raw_workspace}\n"
    )


def _validate_log(text: str, *, raw_workspace: Path) -> None:
    if text != _expected_log(raw_workspace):
        raise ValueError("R3 rejection log is not the exact probe logic failure")


def _formal_source_paths(execution: Path) -> dict[str, Path]:
    r3_root = canonical_r3_evidence_root(execution)
    return {
        "held_scontrol": r3_root / f"held-scontrol-{FORMAL_JOB_ID}.txt",
        "sacct": r3_root / f"sacct-{FORMAL_JOB_ID}.psv",
        "squeue": r3_root / f"squeue-{FORMAL_JOB_ID}.psv",
        "slurm_output": r3_root / f"slurm-{FORMAL_JOB_ID}.out",
        "raw_workspace": r3_root / "raw" / FORMAL_JOB_NAME,
    }


def collect_r3_probe_rejection_evidence(
    *,
    execution_dir: Path,
    raw_workspace: Path,
    held_scontrol_input: Path,
    sacct_input: Path,
    squeue_input: Path,
    slurm_output_input: Path,
    repo_root: Path | None = None,
    test_mode: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the fixed rejection and return deterministic evidence values."""

    execution = _load_execution(
        execution_dir, repo_root=repo_root, test_mode=test_mode
    )
    initial_records, initial_hash = _execution_snapshot(execution.directory)
    raw_requested = raw_workspace.expanduser()
    if raw_requested.is_symlink() or not raw_requested.is_dir():
        raise ValueError("R3 rejection raw workspace must be a regular directory")
    raw_path = raw_requested.resolve()
    raw = validate_rejected_raw_probe_workspace(
        raw_path,
        expected_false_keys=FORMAL_FALSE_REPORT_KEYS,
        allow_test_mode=test_mode,
    )
    job_id = str(raw["slurm_job_id"])
    job_name = str(raw["slurm_job_name"])
    if (
        not JOB_ID_RE.fullmatch(job_id)
        or job_name != expected_r3_job_name(execution.execution_hash)
        or raw.get("test_mode") is not test_mode
        or raw.get("execution_id") != execution.execution_id
        or raw.get("execution_hash") != execution.execution_hash
        or raw.get("execution_directory") != str(execution.directory)
        or raw.get("execution_snapshot_before_sha256") != initial_hash
        or raw.get("execution_snapshot_record_count") != len(initial_records)
    ):
        raise ValueError("R3 rejection raw/execution identity mismatch")
    if not test_mode and (
        job_id != FORMAL_JOB_ID
        or job_name != FORMAL_JOB_NAME
        or raw.get("probe_token") != FORMAL_PROBE_TOKEN
        or raw.get("container_probe_root") != FORMAL_CONTAINER_PROBE_ROOT
    ):
        raise ValueError("formal R3 rejection job/probe identity mismatch")
    authority_root = (
        repo_root
        if repo_root is not None
        else execution.directory / "sources/control"
    )
    before = _stable_snapshot_payload(
        execution,
        probe_token=str(raw["probe_token"]),
        container_probe_root=str(raw["container_probe_root"]),
        repo_root=authority_root,
    )
    historical_mountpoint = before["historical_mountpoint"]
    if (
        before["regular_file_records"] != initial_records
        or before["regular_file_snapshot_hash"] != initial_hash
    ):
        raise ValueError("execution-v3 changed while binding the R3 rejection")

    inputs = {
        "held_scontrol": _require_regular_input(
            held_scontrol_input, "held scontrol input"
        ),
        "sacct": _require_regular_input(sacct_input, "sacct input"),
        "squeue": _require_regular_input(squeue_input, "squeue input"),
        "slurm_output": _require_regular_input(
            slurm_output_input, "Slurm output input"
        ),
    }
    if len(set(inputs.values())) != len(inputs):
        raise ValueError("R3 rejection scheduler inputs must be distinct")
    if not test_mode:
        formal = _formal_source_paths(execution.directory)
        if (
            raw_path != formal["raw_workspace"]
            or any(inputs[name] != formal[name] for name in inputs)
        ):
            raise ValueError("formal R3 rejection inputs are outside canonical paths")
        actual_hashes = {name: sha256_file(path) for name, path in inputs.items()}
        if actual_hashes != {
            name: FORMAL_INPUT_SHA256[name] for name in inputs
        } or sha256_file(raw_path / "probe_result.json") != FORMAL_INPUT_SHA256[
            "probe_result"
        ]:
            raise ValueError("formal R3 rejection input SHA-256 identity mismatch")

    held_text = inputs["held_scontrol"].read_text(encoding="utf-8")
    sacct_text = inputs["sacct"].read_text(encoding="utf-8")
    squeue_text = inputs["squeue"].read_text(encoding="utf-8")
    log_text = inputs["slurm_output"].read_text(encoding="utf-8")
    held = _held_scheduler_identity(
        held_text,
        job_id=job_id,
        job_name=job_name,
        formal_execution_dir=execution.directory,
    )
    rows = _parse_failed_accounting(sacct_text, job_id=job_id, job_name=job_name)
    if squeue_text.strip():
        raise ValueError("R3 rejection job remains active in supplied squeue")
    _validate_log(log_text, raw_workspace=raw_path)
    after = _stable_snapshot_payload(
        execution,
        probe_token=str(raw["probe_token"]),
        container_probe_root=str(raw["container_probe_root"]),
        repo_root=authority_root,
        recorded_mountpoint=historical_mountpoint,
    )
    if before != after:
        raise ValueError("execution-v3 changed while collecting R3 rejection")

    raw_hashes = recursive_file_records(raw_path, exclude=())
    payload: dict[str, Any] = {
        "schema_version": REJECTION_SCHEMA_VERSION,
        "test_mode": test_mode,
        "accepted_compute_preflight_evidence": False,
        "classification": FORMAL_CLASSIFICATION,
        "execution": {
            "directory": str(execution.directory),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "managed_execution_sha256": sha256_file(
                execution.directory / "managed_execution.json"
            ),
            "static_manifest_sha256": sha256_file(
                execution.directory / ROOT_STATIC_MANIFEST
            ),
            "regular_file_snapshot_scope": before[
                "regular_file_snapshot_scope"
            ],
            "regular_file_snapshot_hash": before[
                "regular_file_snapshot_hash"
            ],
            "regular_file_snapshot_record_count": before[
                "regular_file_snapshot_record_count"
            ],
            "historical_mountpoint": historical_mountpoint,
        },
        "scheduler": {
            "job_id": job_id,
            "job_name": job_name,
            "account": held["account"],
            "held_identity": held,
            "terminal_rows": rows,
            "squeue_terminal_empty": True,
        },
        "probe": {
            "raw_schema_version": RAW_PROBE_SCHEMA_VERSION_V2,
            "raw_result_hash": raw["raw_result_hash"],
            "probe_token": raw["probe_token"],
            "container_probe_root": raw["container_probe_root"],
            "historical_mountpoint": historical_mountpoint,
            "false_report_keys": [
                name for name in REPORT_KEYS if name in FORMAL_FALSE_REPORT_KEYS
            ],
            "container_return_code": 0,
            "apptainer_invoked": True,
            "geant4_invoked": False,
            "raw_file_snapshot_scope": "recursive-regular-files-only",
            "raw_file_snapshot_unchanged": True,
            "historical_mountpoint_present": True,
            "execution_tree_unchanged": False,
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
            "raw_workspace_files_sha256": raw_hashes,
        },
        "scheduler_contact_performed_by_tool": False,
        "simulation_started": False,
    }
    payload["rejection_hash"] = _rejection_hash(payload)
    payload["rejection_id"] = (
        f"sm-v1-r3-container-probe-rejection-{job_id}-"
        f"{payload['rejection_hash'][:12]}"
    )
    return payload, before


def _validate_rejection_payload(
    payload: dict[str, Any], snapshot: dict[str, Any], raw: dict[str, Any]
) -> None:
    expected = {
        "schema_version",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "classification",
        "execution",
        "scheduler",
        "probe",
        "consumption",
        "inputs",
        "scheduler_contact_performed_by_tool",
        "simulation_started",
        "rejection_hash",
        "rejection_id",
    }
    if set(payload) != expected:
        raise ValueError("R3 rejection payload field set mismatch")
    execution = payload.get("execution")
    scheduler = payload.get("scheduler")
    probe = payload.get("probe")
    consumption = payload.get("consumption")
    inputs = payload.get("inputs")
    if not all(
        isinstance(value, dict)
        for value in (execution, scheduler, probe, consumption, inputs)
    ):
        raise ValueError("R3 rejection nested payload is invalid")
    if set(execution) != {
        "directory",
        "execution_id",
        "execution_hash",
        "managed_execution_sha256",
        "static_manifest_sha256",
        "regular_file_snapshot_scope",
        "regular_file_snapshot_hash",
        "regular_file_snapshot_record_count",
        "historical_mountpoint",
    } or set(scheduler) != {
        "job_id",
        "job_name",
        "account",
        "held_identity",
        "terminal_rows",
        "squeue_terminal_empty",
    } or set(probe) != {
        "raw_schema_version",
        "raw_result_hash",
        "probe_token",
        "container_probe_root",
        "historical_mountpoint",
        "false_report_keys",
        "container_return_code",
        "apptainer_invoked",
        "geant4_invoked",
        "raw_file_snapshot_scope",
        "raw_file_snapshot_unchanged",
        "historical_mountpoint_present",
        "execution_tree_unchanged",
    } or set(consumption) != {"events_consumed", "production_seeds_consumed"} or set(
        inputs
    ) != {
        "held_scontrol_sha256",
        "sacct_sha256",
        "squeue_sha256",
        "slurm_output_sha256",
        "raw_workspace_files_sha256",
    }:
        raise ValueError("R3 rejection nested field set mismatch")
    rejection_hash = _rejection_hash(payload)
    job_id = str(scheduler.get("job_id", ""))
    expected_false = [name for name in REPORT_KEYS if name in FORMAL_FALSE_REPORT_KEYS]
    expected_mountpoint = rejected_successor_v3_mountpoint_binding(
        probe_token=str(raw.get("probe_token", "")),
        container_probe_root=str(raw.get("container_probe_root", "")),
    )
    test_mode = payload.get("test_mode")
    if (
        payload.get("schema_version") != REJECTION_SCHEMA_VERSION
        or not isinstance(test_mode, bool)
        or payload.get("accepted_compute_preflight_evidence") is not False
        or payload.get("classification") != FORMAL_CLASSIFICATION
        or payload.get("scheduler_contact_performed_by_tool") is not False
        or payload.get("simulation_started") is not False
        or payload.get("rejection_hash") != rejection_hash
        or payload.get("rejection_id")
        != f"sm-v1-r3-container-probe-rejection-{job_id}-{rejection_hash[:12]}"
        or execution.get("directory") != snapshot.get("execution_directory")
        or execution.get("regular_file_snapshot_scope")
        != snapshot.get("regular_file_snapshot_scope")
        or execution.get("regular_file_snapshot_hash")
        != snapshot.get("regular_file_snapshot_hash")
        or execution.get("regular_file_snapshot_record_count")
        != snapshot.get("regular_file_snapshot_record_count")
        or execution.get("historical_mountpoint")
        != snapshot.get("historical_mountpoint")
        or scheduler.get("squeue_terminal_empty") is not True
        or probe
        != {
            "raw_schema_version": RAW_PROBE_SCHEMA_VERSION_V2,
            "raw_result_hash": raw.get("raw_result_hash"),
            "probe_token": raw.get("probe_token"),
            "container_probe_root": raw.get("container_probe_root"),
            "historical_mountpoint": expected_mountpoint,
            "false_report_keys": expected_false,
            "container_return_code": 0,
            "apptainer_invoked": True,
            "geant4_invoked": False,
            "raw_file_snapshot_scope": "recursive-regular-files-only",
            "raw_file_snapshot_unchanged": True,
            "historical_mountpoint_present": True,
            "execution_tree_unchanged": False,
        }
        or consumption != {"events_consumed": 0, "production_seeds_consumed": 0}
    ):
        raise ValueError("R3 rejection payload semantic validation failed")
    if not test_mode and (
        execution.get("execution_id") != FORMAL_EXECUTION_ID
        or execution.get("execution_hash") != FORMAL_EXECUTION_HASH
        or job_id != FORMAL_JOB_ID
        or scheduler.get("job_name") != FORMAL_JOB_NAME
        or probe.get("probe_token") != FORMAL_PROBE_TOKEN
        or probe.get("container_probe_root") != FORMAL_CONTAINER_PROBE_ROOT
    ):
        raise ValueError("formal R3 rejection payload identity mismatch")


def seal_r3_probe_rejection_evidence(
    *,
    execution_dir: Path,
    raw_workspace: Path,
    held_scontrol_input: Path,
    sacct_input: Path,
    squeue_input: Path,
    slurm_output_input: Path,
    repo_root: Path | None = None,
    test_mode: bool = False,
) -> Path:
    payload, snapshot = collect_r3_probe_rejection_evidence(
        execution_dir=execution_dir,
        raw_workspace=raw_workspace,
        held_scontrol_input=held_scontrol_input,
        sacct_input=sacct_input,
        squeue_input=squeue_input,
        slurm_output_input=slurm_output_input,
        repo_root=repo_root,
        test_mode=test_mode,
    )
    execution = Path(payload["execution"]["directory"])
    failures = canonical_r3_evidence_root(execution) / "failures"
    if failures.is_symlink():
        raise ValueError("R3 rejection output root must not be a symlink")
    failures.mkdir(mode=0o700, exist_ok=True)
    if not failures.is_dir():
        raise ValueError("R3 rejection output root is not a directory")
    target = failures / (
        f"r3-container-probe-rejection-{payload['scheduler']['job_id']}-"
        f"{payload['rejection_hash'][:12]}"
    )
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite R3 rejection bundle: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=failures))
    try:
        shutil.copytree(raw_workspace.resolve(), temporary / "raw")
        sources = {
            "held_scontrol.txt": held_scontrol_input,
            "sacct.psv": sacct_input,
            "squeue.psv": squeue_input,
            f"slurm-{payload['scheduler']['job_id']}.out": slurm_output_input,
        }
        for name, source in sources.items():
            shutil.copyfile(_require_regular_input(Path(source), name), temporary / name)
        (temporary / "execution_snapshot.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "rejection.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_recursive_checksums(temporary)
        validate_r3_probe_rejection_bundle(
            temporary,
            execution_dir=execution,
            require_current_execution=True,
            allow_test_mode=test_mode,
            require_canonical_location=False,
            repo_root=repo_root,
        )
        for path in temporary.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        for path in sorted(
            (path for path in temporary.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            path.chmod(0o555)
        fsync_tree(temporary)
        temporary.chmod(0o555)
        publish_directory_no_replace(temporary, target)
        temporary = None
        validate_r3_probe_rejection_bundle(
            target,
            execution_dir=execution,
            require_current_execution=True,
            allow_test_mode=test_mode,
            repo_root=repo_root,
        )
        return target
    finally:
        if temporary is not None and temporary.exists():
            for path in temporary.rglob("*"):
                if path.is_dir():
                    path.chmod(0o700)
                elif path.is_file():
                    path.chmod(0o600)
            temporary.chmod(0o700)
            shutil.rmtree(temporary)


def validate_r3_probe_rejection_bundle(
    bundle_dir: Path,
    *,
    execution_dir: Path | None = None,
    require_current_execution: bool = False,
    allow_test_mode: bool = False,
    require_canonical_location: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate a sealed rejection bundle without scheduler contact."""

    requested = bundle_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink() or not requested.is_dir():
        raise ValueError("R3 rejection bundle must be a regular directory")
    root = requested.resolve()
    records = verify_recursive_checksums(
        root, required=REJECTION_BUNDLE_BASE_FILES
    )
    payload = load_json(root / "rejection.json")
    scheduler_value = payload.get("scheduler")
    if not isinstance(scheduler_value, dict):
        raise ValueError("R3 rejection scheduler payload is invalid")
    recorded_job_id = str(scheduler_value.get("job_id", ""))
    expected_records = REJECTION_BUNDLE_BASE_FILES | {
        f"slurm-{recorded_job_id}.out"
    }
    if not JOB_ID_RE.fullmatch(recorded_job_id) or set(records) != expected_records:
        raise ValueError("R3 rejection bundle file set mismatch")
    snapshot = _validate_snapshot(load_json(root / "execution_snapshot.json"))
    raw = validate_rejected_raw_probe_workspace(
        root / "raw",
        expected_false_keys=FORMAL_FALSE_REPORT_KEYS,
        allow_test_mode=allow_test_mode,
        require_recorded_location=False,
    )
    _validate_rejection_payload(payload, snapshot, raw)
    test_mode = payload["test_mode"]
    if test_mode and not allow_test_mode:
        raise ValueError("test R3 rejection is not formal evidence")
    execution = Path(payload["execution"]["directory"])
    if execution_dir is not None and execution != execution_dir.expanduser().resolve():
        raise ValueError("R3 rejection execution directory mismatch")
    expected = canonical_r3_evidence_root(execution) / "failures" / (
        f"r3-container-probe-rejection-{payload['scheduler']['job_id']}-"
        f"{payload['rejection_hash'][:12]}"
    )
    if require_canonical_location and root != expected:
        raise ValueError("R3 rejection bundle is outside its content address")
    if require_canonical_location and (
        root.stat().st_mode & 0o222
        or any(path.stat().st_mode & 0o222 for path in root.rglob("*"))
    ):
        raise ValueError("canonical R3 rejection bundle is writable")

    job_id = str(payload["scheduler"]["job_id"])
    job_name = str(payload["scheduler"]["job_name"])
    held_text = (root / "held_scontrol.txt").read_text(encoding="utf-8")
    sacct_text = (root / "sacct.psv").read_text(encoding="utf-8")
    squeue_text = (root / "squeue.psv").read_text(encoding="utf-8")
    log_text = (root / f"slurm-{job_id}.out").read_text(encoding="utf-8")
    held = _held_scheduler_identity(
        held_text,
        job_id=job_id,
        job_name=job_name,
        formal_execution_dir=execution,
    )
    rows = _parse_failed_accounting(sacct_text, job_id=job_id, job_name=job_name)
    if squeue_text.strip():
        raise ValueError("sealed R3 rejection squeue is not empty")
    source_raw = Path(str(raw["probe_workspace"]))
    _validate_log(log_text, raw_workspace=source_raw)
    raw_hashes = recursive_file_records(root / "raw", exclude=())
    actual_inputs = {
        "held_scontrol_sha256": sha256_file(root / "held_scontrol.txt"),
        "sacct_sha256": sha256_file(root / "sacct.psv"),
        "squeue_sha256": sha256_file(root / "squeue.psv"),
        "slurm_output_sha256": sha256_file(root / f"slurm-{job_id}.out"),
        "raw_workspace_files_sha256": raw_hashes,
    }
    if (
        payload["scheduler"]["held_identity"] != held
        or payload["scheduler"]["terminal_rows"] != rows
        or payload["scheduler"]["account"] != held["account"]
        or payload["inputs"] != actual_inputs
        or payload["execution"]["regular_file_snapshot_hash"]
        != raw["execution_snapshot_before_sha256"]
    ):
        raise ValueError("sealed R3 rejection source identity mismatch")
    if not test_mode:
        actual_formal = {
            "held_scontrol": actual_inputs["held_scontrol_sha256"],
            "sacct": actual_inputs["sacct_sha256"],
            "squeue": actual_inputs["squeue_sha256"],
            "slurm_output": actual_inputs["slurm_output_sha256"],
            "probe_result": raw_hashes["probe_result.json"],
        }
        if actual_formal != FORMAL_INPUT_SHA256:
            raise ValueError("sealed formal R3 rejection input SHA-256 mismatch")
    if require_current_execution:
        current = _load_execution(execution, repo_root=repo_root, test_mode=test_mode)
        authority_root = (
            repo_root
            if repo_root is not None
            else current.directory / "sources/control"
        )
        current_snapshot = _stable_snapshot_payload(
            current,
            probe_token=str(raw["probe_token"]),
            container_probe_root=str(raw["container_probe_root"]),
            repo_root=authority_root,
            recorded_mountpoint=payload["probe"]["historical_mountpoint"],
        )
        if current_snapshot != snapshot:
            raise ValueError("execution-v3 changed since R3 rejection capture")
        if (
            payload["execution"]["managed_execution_sha256"]
            != sha256_file(current.directory / "managed_execution.json")
            or payload["execution"]["static_manifest_sha256"]
            != sha256_file(current.directory / ROOT_STATIC_MANIFEST)
        ):
            raise ValueError("current execution-v3 identity mismatch")
        if source_raw.is_symlink() or not source_raw.is_dir():
            raise ValueError("source R3 rejection raw workspace is missing or unsafe")
        if recursive_file_records(source_raw, exclude=()) != raw_hashes:
            raise ValueError("source R3 rejection raw workspace changed")
    return payload


__all__ = [
    "FORMAL_FALSE_REPORT_KEYS",
    "FORMAL_INPUT_SHA256",
    "FORMAL_JOB_ID",
    "FORMAL_JOB_NAME",
    "FORMAL_PROBE_TOKEN",
    "REJECTION_SCHEMA_VERSION",
    "collect_r3_probe_rejection_evidence",
    "seal_r3_probe_rejection_evidence",
    "validate_r3_probe_rejection_bundle",
]
