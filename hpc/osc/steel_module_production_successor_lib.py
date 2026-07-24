#!/usr/bin/env python3
"""Incident-bound Phase-2B recovery-successor primitives.

The closed v2/v3/v4 generations remain immutable history.  The active v5
generation additionally binds the v4 pre-workspace GPFS lock failure, but
materialization itself cannot create an intent, contact a scheduler, or treat
any historical readiness lock as current authority.
"""

from __future__ import annotations

import json
import fcntl
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional

from seal_steel_module_production_phase2b_incident import (
    FORMAL_IMPLEMENTATION_COMMIT,
    FORMAL_READINESS_COMMIT,
    INCIDENT_SCHEMA_VERSION,
    _validate_recorded_recovery_control_plane,
    validate_incident_bundle,
)
from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    load_json,
    parse_campaign_tasks,
    require_dict,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)
from steel_module_managed_production_lib import (
    INITIAL_CHILD_ID,
    load_managed_production_child,
)
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    recursive_file_records,
    verify_recursive_checksums,
)
from steel_module_production_phase2b_lib import (
    CONTROL_LOCK_PROTOCOL_V2,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V4,
    FORMAL_EXECUTION_NAME,
    FORMAL_PILOT_NAME,
    HISTORICAL_CLOSED_ATTEMPT_ID,
    HISTORICAL_CLOSED_EXECUTION_HASH,
    HISTORICAL_CLOSED_EXECUTION_ID,
    HISTORICAL_CLOSED_INTENT_SHA256,
    HISTORICAL_CLOSED_JOB_ID,
    PHASE2A_LOCK_RELATIVE,
    PHASE2A_LOCK_SCHEMA_VERSION,
    READINESS_LOCK_RELATIVE,
    READINESS_LOCK_SCHEMA_VERSION,
    ROOT_STATIC_MANIFEST,
    SUCCESSOR_OBJECT_KIND,
    ManagedExecution,
    _read_scan_args,
    _opened_portable_control_lock,
    _remove_tree,
    _sealed_pilot_identity,
    _semantic_hash,
    _validate_phase2a_binding,
    _verify_source_record,
    _write_execution_tree,
    _write_static_checksums,
    fsync_tree,
    load_execution_companion,
    load_historical_closed_execution_for_recovery,
    load_phase2a_lock,
    selected_task_ids,
    utc_now,
    validate_execution_control_lock,
    verify_execution_static_checksums,
)
from steel_module_production_program_lib import (
    ordered_task_hash,
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)


SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2 = EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1
SUCCESSOR_EXECUTION_GENERATION_V2 = "predecessor-retry-successor-v2"
SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3 = EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2
SUCCESSOR_EXECUTION_GENERATION_V3 = "predecessor-retry-successor-v3"
SUCCESSOR_EXECUTION_SCHEMA_VERSION = EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3
SUCCESSOR_EXECUTION_GENERATION = "predecessor-retry-successor-v4"
SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5 = EXECUTION_SCHEMA_VERSION_SUCCESSOR_V4
SUCCESSOR_EXECUTION_GENERATION_V5 = "predecessor-retry-successor-v5"
SUCCESSOR_AUTHORITY_SCHEMA_VERSION = (
    "steel-module-production-successor-authority-v1"
)
SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V2 = (
    "steel-module-production-phase2b-recovery-readiness-lock-v1"
)
SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V3 = (
    "steel-module-production-phase2b-recovery-readiness-lock-v2"
)
SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION = (
    "steel-module-production-phase2b-recovery-readiness-lock-v3"
)
SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V5 = (
    "steel-module-production-phase2b-recovery-readiness-lock-v4"
)
SUCCESSOR_V3_AUTHORITY_SCHEMA_VERSION = (
    "steel-module-production-successor-authority-v2"
)
SUCCESSOR_V4_AUTHORITY_SCHEMA_VERSION = (
    "steel-module-production-successor-authority-v3"
)
SUCCESSOR_V5_AUTHORITY_SCHEMA_VERSION = (
    "steel-module-production-successor-authority-v4"
)
FORMAL_SUCCESSOR_EXECUTION_NAME_V2 = (
    "steel-module-production-bc-s1-execution-v2"
)
FORMAL_SUCCESSOR_EXECUTION_NAME_V3 = (
    "steel-module-production-bc-s1-execution-v3"
)
FORMAL_SUCCESSOR_EXECUTION_NAME = "steel-module-production-bc-s1-execution-v4"
FORMAL_SUCCESSOR_EXECUTION_NAME_V5 = (
    "steel-module-production-bc-s1-execution-v5"
)
FORMAL_SUCCESSOR_V2_EXECUTION_ID = (
    "sm-v1-production-bc-s1-execution-v2-50aefd35ac58"
)
FORMAL_SUCCESSOR_V2_EXECUTION_HASH = (
    "8352b7949657fb3161ec2e31ddf384997634ae6092f1f3e884ef95f7f22427de"
)
FORMAL_FAILED_R3_JOB_ID = "50544247"
FORMAL_ADMIN_REJECTED_R3_JOB_ID = "50547698"
FORMAL_ADMIN_REJECTED_R3_MANIFEST_SHA256 = (
    "4be9a54fbed81d59aac608a6a601602a79bcd10beb17a16f66555267fa563b77"
)
FORMAL_REJECTED_R3_JOB_ID = "50548308"
FORMAL_PREWORKSPACE_FAILED_R3_JOB_ID = "50558158"
R3_HISTORICAL_MOUNTPOINT_PREFIX = ".r3-probe-"
R3_HISTORICAL_CONTAINER_ATTEMPTS_ROOT = PurePosixPath(
    "/work/g4optics-execution/attempts"
)
R3_HISTORICAL_MOUNTPOINT_MODE = 0o755
RECOVERY_READINESS_LOCK_RELATIVE_V2 = Path(
    "hpc/osc/configurations/steel-module-production-phase2b-recovery-v1.lock.json"
)
RECOVERY_READINESS_LOCK_RELATIVE_V3 = Path(
    "hpc/osc/configurations/steel-module-production-phase2b-recovery-v2.lock.json"
)
RECOVERY_READINESS_LOCK_RELATIVE = Path(
    "hpc/osc/configurations/steel-module-production-phase2b-recovery-v3.lock.json"
)
RECOVERY_READINESS_LOCK_RELATIVE_V5 = Path(
    "hpc/osc/configurations/steel-module-production-phase2b-recovery-v4.lock.json"
)
FORMAL_INCIDENT_ID = "sm-v1-bc-s1-pre-simulation-incident-0d8898f4e1ce"
FORMAL_INCIDENT_HASH = (
    "0d8898f4e1ce5d6f4c6a9fede2e6e612f5c7723c7f59efa66abd481b1250ed9e"
)
FORMAL_INCIDENT_NAME = "bc-s1-50532143-0d8898f4e1ce"

SUCCESSOR_ROOT_ENTRIES = {
    ".control.lock",
    "README.md",
    ROOT_STATIC_MANIFEST,
    "managed_execution.json",
    "scan_args.txt",
    "source_archives.json",
    "tasks.tsv",
    "sources",
    "intents",
    "attempts",
    "finalized",
}
SUCCESSOR_STATIC_FILES = {
    "README.md",
    ROOT_STATIC_MANIFEST,
    "managed_execution.json",
    "scan_args.txt",
    "source_archives.json",
    "tasks.tsv",
}
R3_COPY_SAFE_LAUNCHER_RELATIVE = Path(
    "sources/control/hpc/osc/run_steel_module_production_r3_probe.sbatch"
)
R3_COPY_SAFE_LAUNCHER_RELATIVE_V5 = Path(
    "sources/control/hpc/osc/run_steel_module_production_r3_probe_v5.sbatch"
)


def _require_exact_keys(
    value: dict[str, Any], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        raise ValueError(
            f"{label} field set mismatch: "
            f"unexpected={sorted(set(value) - expected)}, "
            f"missing={sorted(expected - set(value))}"
        )


def _r3_launcher_identity(directory: Path) -> dict[str, Any]:
    path = directory / R3_COPY_SAFE_LAUNCHER_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("successor-v3 copy-safe R3 launcher is missing or unsafe")
    mode = path.stat().st_mode & 0o777
    if mode != 0o555:
        raise ValueError("successor-v3 copy-safe R3 launcher mode is not frozen")
    return {
        "path": R3_COPY_SAFE_LAUNCHER_RELATIVE.as_posix(),
        "sha256": sha256_file(path),
        "mode": mode,
    }


def _r3_launcher_identity_v5(directory: Path) -> dict[str, Any]:
    path = directory / R3_COPY_SAFE_LAUNCHER_RELATIVE_V5
    if path.is_symlink() or not path.is_file():
        raise ValueError("successor-v5 copy-safe R3 launcher is missing or unsafe")
    mode = path.stat().st_mode & 0o777
    if mode != 0o555:
        raise ValueError("successor-v5 copy-safe R3 launcher mode is not frozen")
    return {
        "path": R3_COPY_SAFE_LAUNCHER_RELATIVE_V5.as_posix(),
        "sha256": sha256_file(path),
        "mode": mode,
    }


@dataclass(frozen=True)
class SuccessorPreview:
    repo_root: Path
    managed_child: Any
    phase2a_path: Path
    phase2a_lock: dict[str, Any]
    predecessor: ManagedExecution
    incident_path: Path
    incident: dict[str, Any]
    recovery_authority: dict[str, Any]
    pilot_dir: Path
    test_mode: bool


@dataclass(frozen=True)
class SuccessorV3Preview:
    repo_root: Path
    managed_child: Any
    phase2a_path: Path
    phase2a_lock: dict[str, Any]
    predecessor: ManagedExecution
    failure_path: Path
    failure: dict[str, Any]
    recovery_authority: dict[str, Any]
    pilot_dir: Path
    test_mode: bool


@dataclass(frozen=True)
class SuccessorV4Preview:
    repo_root: Path
    managed_child: Any
    phase2a_path: Path
    phase2a_lock: dict[str, Any]
    predecessor: ManagedExecution
    administrative_rejection_manifest: Path
    rejection_path: Path
    rejection: dict[str, Any]
    recovery_authority: dict[str, Any]
    pilot_dir: Path
    test_mode: bool


@dataclass(frozen=True)
class SuccessorV5Preview:
    repo_root: Path
    managed_child: Any
    phase2a_path: Path
    phase2a_lock: dict[str, Any]
    predecessor: ManagedExecution
    preworkspace_failure_path: Path
    preworkspace_failure: dict[str, Any]
    recovery_authority: dict[str, Any]
    pilot_dir: Path
    test_mode: bool


def canonical_successor_directory(managed_child: Any) -> Path:
    return managed_child.directory.parent / FORMAL_SUCCESSOR_EXECUTION_NAME


def canonical_v5_successor_directory(managed_child: Any) -> Path:
    return managed_child.directory.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V5


def canonical_v3_successor_directory(managed_child: Any) -> Path:
    return managed_child.directory.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V3


def canonical_v2_successor_directory(managed_child: Any) -> Path:
    return managed_child.directory.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V2


def canonical_incident_directory(managed_child: Any) -> Path:
    return (
        managed_child.directory.parent
        / "steel-module-production-incidents"
        / FORMAL_INCIDENT_NAME
    )


def _safe_requested_directory(path: Path, label: str) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = requested.resolve()
    if resolved.parent.is_symlink() or not resolved.parent.is_dir():
        raise ValueError(f"{label} parent is unsafe")
    return resolved


def _seed_values(tasks: tuple[CampaignTask, ...]) -> tuple[int, ...]:
    return tuple(seed for task in tasks for seed in (task.seed1, task.seed2))


def _task_identity(tasks: tuple[CampaignTask, ...]) -> dict[str, Any]:
    seeds = _seed_values(tasks)
    return {
        "ordered_task_hash": ordered_task_hash(tasks),
        "task_set_hash": task_set_hash(tasks),
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(seeds),
        "ordered_seed_pairs_sha256": sha256_bytes(
            canonical_json(
                [
                    {
                        "logical_task_id": task.logical_task_id,
                        "seed1": task.seed1,
                        "seed2": task.seed2,
                    }
                    for task in tasks
                ]
            )
        ),
        "ordered_production_seeds": list(seeds),
        "task_count": len(tasks),
        "event_count": sum(task.events for task in tasks),
        "seed_count": len(seeds),
    }


def _formal_incident_candidate_gate(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("formal incident parent is missing or unsafe")
    candidates = {
        candidate.name
        for candidate in parent.glob("bc-s1-50532143-*")
        if candidate.exists() or candidate.is_symlink()
    }
    if candidates != {FORMAL_INCIDENT_NAME}:
        raise ValueError(
            "formal predecessor incident set is not unique and exact: "
            f"{sorted(candidates)}"
        )


def _validate_zero_consumption_incident(
    incident: dict[str, Any], *, formal: bool
) -> None:
    disposition = require_dict(incident, "simulation_disposition")
    scheduler = require_dict(incident, "scheduler")
    if (
        incident.get("classification") != "pre-simulation-control-plane-failure"
        or incident.get("root_cause")
        != "cross-node-control-lock-device-or-inode-mismatch"
        or disposition.get("loader_completed") is not False
        or disposition.get("apptainer_invoked") is not False
        or disposition.get("geant4_invoked") is not False
        or disposition.get("task_outputs_created") is not False
        or disposition.get("root_outputs_created") is not False
        or disposition.get("events_consumed") != 0
        or disposition.get("production_seed_count") != 64
        or disposition.get("production_seeds_consumed") != 0
        or disposition.get("seed_reuse_requires_successor_authority") is not True
        or scheduler.get("array_task_count") != 32
        or scheduler.get("states") != {"FAILED": 32}
        or scheduler.get("exit_codes") != {"1:0": 32}
    ):
        raise ValueError("incident does not prove exact zero-consumption failure")
    if formal and incident.get("accepted_incident_evidence") is not True:
        raise ValueError("formal successor requires accepted incident evidence")


def _load_predecessor(
    *, repo_root: Path, directory: Path, test_mode: bool
) -> ManagedExecution:
    if test_mode:
        return load_execution_companion(
            directory,
            allow_test_mode=True,
            require_readiness=False,
            verify_runtime=False,
        )
    return load_historical_closed_execution_for_recovery(
        directory, repo_root=repo_root
    )


def _build_recovery_authority(
    *,
    repo_root: Path,
    managed: Any,
    predecessor: ManagedExecution,
    incident_path: Path,
    incident: dict[str, Any],
    test_mode: bool,
) -> dict[str, Any]:
    incident_records = verify_recursive_checksums(incident_path)
    incident_execution = require_dict(incident, "execution")
    readiness = require_dict(incident, "historical_readiness")
    attempt = require_dict(incident, "attempt")
    disposition = require_dict(incident, "simulation_disposition")
    predecessor_task_identity = _task_identity(predecessor.tasks)
    managed_task_identity = _task_identity(tuple(managed.plan.tasks))
    if predecessor.tasks != managed.plan.tasks:
        raise ValueError("predecessor task rows differ from Phase-2A")
    if predecessor.scan_args != managed.plan.scan_args:
        raise ValueError("predecessor scan arguments differ from Phase-2A")
    child_binding = require_dict(managed.binding, "child")
    if (
        predecessor_task_identity != managed_task_identity
        or child_binding.get("task_set_hash")
        != predecessor_task_identity["task_set_hash"]
        or child_binding.get("task_seed_mapping_hash")
        != predecessor_task_identity["task_seed_mapping_hash"]
        or child_binding.get("seed_set_hash")
        != predecessor_task_identity["seed_set_hash"]
        or incident.get("task_seed_mapping_hash")
        != predecessor_task_identity["task_seed_mapping_hash"]
    ):
        raise ValueError("Phase-2A/predecessor/incident task-seed identity mismatch")
    if (
        incident.get("source_identities") != predecessor.manifest.get("sources")
        or incident.get("runtime_identity") != predecessor.manifest.get("runtime")
    ):
        raise ValueError("incident source/runtime identity differs from predecessor")
    if (
        incident_execution.get("execution_id") != predecessor.execution_id
        or incident_execution.get("execution_hash") != predecessor.execution_hash
        or incident_execution.get("directory") != str(predecessor.directory)
        or incident_execution.get("managed_execution_sha256")
        != sha256_file(predecessor.directory / "managed_execution.json")
        or incident_execution.get("static_checksums_sha256")
        != sha256_file(predecessor.directory / ROOT_STATIC_MANIFEST)
    ):
        raise ValueError("incident predecessor execution binding mismatch")
    if not test_mode:
        readiness_path = repo_root / READINESS_LOCK_RELATIVE
        if (
            readiness_path.is_symlink()
            or not readiness_path.is_file()
            or readiness.get("path") != str(readiness_path.resolve())
            or readiness.get("sha256") != sha256_file(readiness_path)
            or readiness.get("schema_version") != READINESS_LOCK_SCHEMA_VERSION
            or readiness.get("implementation_commit")
            != FORMAL_IMPLEMENTATION_COMMIT
            or readiness.get("readiness_commit") != FORMAL_READINESS_COMMIT
        ):
            raise ValueError("historical readiness identity differs from incident")

    authority: dict[str, Any] = {
        "schema_version": SUCCESSOR_AUTHORITY_SCHEMA_VERSION,
        "predecessor_execution": {
            "directory": str(predecessor.directory),
            "schema_version": predecessor.manifest.get("schema_version"),
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
            "managed_execution_sha256": sha256_file(
                predecessor.directory / "managed_execution.json"
            ),
            "static_checksums_sha256": sha256_file(
                predecessor.directory / ROOT_STATIC_MANIFEST
            ),
        },
        "predecessor_readiness": {
            key: readiness.get(key)
            for key in (
                "path",
                "sha256",
                "schema_version",
                "status",
                "implementation_commit",
                "readiness_commit",
                "readiness_commit_parent",
                "commit_blob_sha256",
            )
        },
        "predecessor_attempt": {
            key: attempt.get(key)
            for key in (
                "attempt_id",
                "intent_sha256",
                "job_id",
                "job_name",
                "event_count",
                "event_chain_sha256",
                "terminal_event_sha256",
                "accounting_manifest_sha256",
            )
        },
        "incident": {
            "directory": str(incident_path),
            "schema_version": incident.get("schema_version"),
            "incident_id": incident.get("incident_id"),
            "incident_hash": incident.get("incident_hash"),
            "incident_json_sha256": sha256_file(incident_path / "incident.json"),
            "checksum_manifest_sha256": sha256_file(
                incident_path / "SHA256SUMS"
            ),
            "recursive_record_count": len(incident_records),
        },
        "zero_consumption": {
            "classification": incident.get("classification"),
            "root_cause": incident.get("root_cause"),
            "accepted_incident_evidence": incident.get(
                "accepted_incident_evidence"
            ),
            "events_consumed": disposition.get("events_consumed"),
            "production_seed_count": disposition.get("production_seed_count"),
            "production_seeds_consumed": disposition.get(
                "production_seeds_consumed"
            ),
            "loader_completed": disposition.get("loader_completed"),
            "apptainer_invoked": disposition.get("apptainer_invoked"),
            "geant4_invoked": disposition.get("geant4_invoked"),
            "task_outputs_created": disposition.get("task_outputs_created"),
            "root_outputs_created": disposition.get("root_outputs_created"),
            "seed_reuse_authorized": True,
            "reuse_scope": "exact-32-task-phase2a-mapping-only",
        },
        "retry_equivalence": {
            **predecessor_task_identity,
            "tasks_tsv_sha256": sha256_file(predecessor.directory / "tasks.tsv"),
            "scan_args_sha256": sha256_file(
                predecessor.directory / "scan_args.txt"
            ),
            "phase2a_tasks_sha256": sha256_file(managed.directory / "tasks.tsv"),
            "phase2a_scan_args_sha256": sha256_file(
                managed.directory / "scan_args.txt"
            ),
            "simulation_source": require_dict(
                predecessor.manifest, "sources"
            )["simulation"],
            "runtime": predecessor.manifest["runtime"],
            "all_equal": True,
        },
        "authority_hash": None,
    }
    authority["authority_hash"] = _semantic_hash(authority, "authority_hash")
    return authority


def preview_successor_execution(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    incident_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
) -> SuccessorPreview:
    # Accepted only for API symmetry with the test-only materializer.  Preview
    # never reads or archives successor source inputs.
    del fixture_sources
    root = repo_root.expanduser().resolve()
    phase2a_path, phase2a = load_phase2a_lock(root)
    formal_child = Path(require_string(phase2a, "canonical_directory"))
    requested_child = managed_child_dir or formal_child
    if requested_child.is_symlink() or requested_child.parent.is_symlink():
        raise ValueError("managed child must not be a symlink")
    child_path = requested_child.expanduser().resolve()
    if not test_mode and child_path != formal_child:
        raise ValueError("formal successor managed-child path is fixed")
    expected_predecessor = child_path.parent / FORMAL_EXECUTION_NAME
    requested_predecessor = predecessor_dir or expected_predecessor
    if (
        requested_predecessor.is_symlink()
        or requested_predecessor.parent.is_symlink()
    ):
        raise ValueError("predecessor execution must not be a symlink")
    predecessor_path = requested_predecessor.expanduser().resolve()
    if not test_mode and predecessor_path != expected_predecessor:
        raise ValueError("formal predecessor execution path is fixed")
    predecessor = _load_predecessor(
        repo_root=root, directory=predecessor_path, test_mode=test_mode
    )
    managed = predecessor.managed_child
    if managed.directory != child_path:
        raise ValueError("predecessor is not bound to the requested Phase-2A child")
    if not test_mode:
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
        if (
            predecessor.execution_id != HISTORICAL_CLOSED_EXECUTION_ID
            or predecessor.execution_hash != HISTORICAL_CLOSED_EXECUTION_HASH
        ):
            raise ValueError("formal predecessor identity is not the closed execution")

    expected_incident = canonical_incident_directory(managed)
    requested_incident = incident_dir or expected_incident
    if requested_incident.is_symlink() or requested_incident.parent.is_symlink():
        raise ValueError("incident bundle must not be a symlink")
    incident_path = requested_incident.expanduser().resolve()
    if not test_mode:
        if incident_path != expected_incident:
            raise ValueError("formal incident path is fixed")
        _formal_incident_candidate_gate(incident_path)
    incident = validate_incident_bundle(incident_path, execution=predecessor)
    _validate_zero_consumption_incident(incident, formal=not test_mode)
    if not test_mode:
        if (
            incident.get("schema_version") != INCIDENT_SCHEMA_VERSION
            or incident.get("incident_id") != FORMAL_INCIDENT_ID
            or incident.get("incident_hash") != FORMAL_INCIDENT_HASH
            or require_dict(incident, "attempt").get("attempt_id")
            != HISTORICAL_CLOSED_ATTEMPT_ID
            or require_dict(incident, "attempt").get("intent_sha256")
            != HISTORICAL_CLOSED_INTENT_SHA256
            or require_dict(incident, "attempt").get("job_id")
            != HISTORICAL_CLOSED_JOB_ID
        ):
            raise ValueError("formal incident immutable identity mismatch")
        _validate_recorded_recovery_control_plane(root, incident)

    pilot = (
        pilot_dir
        or Path(require_string(require_dict(predecessor.manifest, "sealed_pilot"), "directory"))
    ).expanduser().resolve()
    if not test_mode and pilot != child_path.parent / FORMAL_PILOT_NAME:
        raise ValueError("formal successor sealed-pilot path is fixed")
    authority = _build_recovery_authority(
        repo_root=root,
        managed=managed,
        predecessor=predecessor,
        incident_path=incident_path,
        incident=incident,
        test_mode=test_mode,
    )
    return SuccessorPreview(
        root,
        managed,
        phase2a_path,
        phase2a,
        predecessor,
        incident_path,
        incident,
        authority,
        pilot,
        test_mode,
    )


def _v2_successor_execution_id(
    binding_hash: str,
    control_commit: str,
    incident_hash: str,
    predecessor_execution_hash: str,
) -> str:
    suffix = sha256_bytes(
        canonical_json(
            {
                "binding_hash": binding_hash,
                "control_commit": control_commit,
                "incident_hash": incident_hash,
                "predecessor_execution_hash": predecessor_execution_hash,
            }
        )
    )[:12]
    return f"sm-v1-production-bc-s1-execution-v2-{suffix}"


def _transform_successor_manifest(
    directory: Path, preview: SuccessorPreview
) -> None:
    lock_path = directory / ".control.lock"
    lock_path.chmod(0o400)
    manifest_path = directory / "managed_execution.json"
    payload = load_json(manifest_path)
    require_dict(require_dict(payload, "artifacts"), "control_lock")["mode"] = 0o400
    sources = require_dict(payload, "sources")
    if (
        require_dict(sources, "simulation")
        != require_dict(preview.predecessor.manifest, "sources")["simulation"]
        or payload.get("runtime") != preview.predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor changed frozen simulation/runtime identity")
    retry = require_dict(preview.recovery_authority, "retry_equivalence")
    if (
        retry.get("tasks_tsv_sha256") != sha256_file(directory / "tasks.tsv")
        or retry.get("scan_args_sha256") != sha256_file(directory / "scan_args.txt")
    ):
        raise ValueError("successor task/scan bytes differ from predecessor")
    payload.update(
        {
            "schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2,
            "object_kind": SUCCESSOR_OBJECT_KIND,
            "execution_generation": SUCCESSOR_EXECUTION_GENERATION_V2,
            "phase": "Phase-2B-R2",
            "recovery_authority": preview.recovery_authority,
            "readiness_gate": {
                "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE_V2.as_posix(),
                "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V2,
                "required_for_scheduler_contact": True,
                "compute_node_container_preflight_required": True,
                "automatic_submission": False,
                "r2_submission_ready": False,
            },
            "submission_policy": {
                "account": "PAS2524",
                "two_step_required": True,
                "initially_held": True,
                "no_requeue": True,
                "export_mode": "NONE",
                "formal_child": INITIAL_CHILD_ID,
                "formal_initial_mode": "predecessor-retry",
                "predecessor_retry_task_count": 32,
                "predecessor_retry_event_count": 8000,
                "r2_submittable": False,
            },
        }
    )
    control_commit = require_string(require_dict(sources, "control_plane"), "git_commit")
    payload["execution_id"] = _v2_successor_execution_id(
        require_string(require_dict(payload, "managed_child"), "binding_hash"),
        control_commit,
        require_sha256(preview.incident, "incident_hash"),
        preview.predecessor.execution_hash,
    )
    payload["execution_hash"] = None
    payload["execution_hash"] = _semantic_hash(payload, "execution_hash")
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (directory / "README.md").write_text(
        f"# {payload['execution_id']}\n\n"
        "Incident-bound BC-S1 recovery successor, frozen at Phase-2B R2.\n"
        "It is not a campaign. It has no production intent, no scheduler "
        "authority, and cannot become submittable until the separately "
        "reviewed compute-node preflight and additive recovery readiness.\n",
        encoding="utf-8",
    )
    _write_static_checksums(directory)


def _harden_successor_tree(directory: Path) -> None:
    """Make host-visible authority static while retaining isolated journals."""

    for name in SUCCESSOR_STATIC_FILES:
        (directory / name).chmod(0o444)
    for name in ("intents", "attempts", "finalized"):
        (directory / name).chmod(0o700)
    (directory / "sources").chmod(0o555)
    # The generation transform decides whether its token-bound lock is
    # historical 0400 evidence or active 0600 mutation authority.  The
    # container still sees the whole execution root read-only and only one
    # exact probe output RW.
    directory.chmod(0o555)


def _validate_successor_tree_modes(
    directory: Path, *, control_lock_mode: int = 0o400
) -> None:
    if directory.stat().st_mode & 0o222:
        raise ValueError("successor execution root is unexpectedly writable")
    for name in SUCCESSOR_STATIC_FILES:
        path = directory / name
        if path.stat().st_mode & 0o222:
            raise ValueError(f"successor static artifact is writable: {name}")
    for name in ("intents", "attempts", "finalized"):
        path = directory / name
        if path.stat().st_mode & 0o777 != 0o700:
            raise ValueError(f"successor mutable root mode is unsafe: {name}")
    if (directory / ".control.lock").stat().st_mode & 0o777 != control_lock_mode:
        raise ValueError(
            "successor portable control lock mode differs from its generation"
        )


def canonical_r3_failure_parent(managed_child: Any) -> Path:
    return (
        managed_child.directory.parent.parent
        / "evidence/steel-module-production-r3/failures"
    )


def _resolve_r3_failure_path(
    managed_child: Any, requested: Path | None, *, test_mode: bool
) -> Path:
    if requested is not None:
        if requested.is_symlink() or requested.parent.is_symlink():
            raise ValueError("R3 failure bundle must not be a symlink")
        resolved = requested.expanduser().resolve()
        if not test_mode and resolved.parent != canonical_r3_failure_parent(managed_child):
            raise ValueError("formal R3 failure bundle parent is fixed")
        return resolved
    if test_mode:
        raise ValueError("test successor-v3 requires an explicit R3 failure bundle")
    parent = canonical_r3_failure_parent(managed_child)
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("formal R3 failure bundle parent is missing or unsafe")
    candidates = tuple(
        path
        for path in sorted(parent.glob(f"r3-preflight-{FORMAL_FAILED_R3_JOB_ID}-*"))
        if path.exists() or path.is_symlink()
    )
    if len(candidates) != 1:
        raise ValueError(
            "formal failed-R3 bundle set is not unique and exact: "
            f"{[path.name for path in candidates]}"
        )
    if candidates[0].is_symlink() or not candidates[0].is_dir():
        raise ValueError("formal failed-R3 bundle is unsafe")
    return candidates[0].resolve()


def _validated_r3_failure(
    path: Path,
    *,
    predecessor: ManagedExecution,
    test_mode: bool,
    require_current_execution: bool,
) -> dict[str, Any]:
    import steel_module_production_r3_failure_lib as failure_lib

    def validate() -> dict[str, Any]:
        return failure_lib.validate_r3_failure_bundle(
            path,
            execution_dir=predecessor.directory,
            require_current_execution=require_current_execution,
            allow_test_mode=test_mode,
            preloaded_execution=(
                predecessor if require_current_execution else None
            ),
        )

    if not test_mode:
        return validate()
    # Synthetic predecessors intentionally have content-addressed fixture
    # identities.  Scope the formal constants to that exact fixture while the
    # bundle is validated; production evidence never enters this branch.
    original_id = failure_lib.FORMAL_EXECUTION_ID
    original_hash = failure_lib.FORMAL_EXECUTION_HASH
    failure_lib.FORMAL_EXECUTION_ID = predecessor.execution_id
    failure_lib.FORMAL_EXECUTION_HASH = predecessor.execution_hash
    try:
        return validate()
    finally:
        failure_lib.FORMAL_EXECUTION_ID = original_id
        failure_lib.FORMAL_EXECUTION_HASH = original_hash


def _failure_binding(path: Path, failure: dict[str, Any]) -> dict[str, Any]:
    records = verify_recursive_checksums(path)
    execution = require_dict(failure, "execution")
    scheduler = require_dict(failure, "scheduler")
    boundary = require_dict(failure, "runtime_boundary")
    consumption = require_dict(failure, "consumption")
    return {
        "directory": str(path),
        "schema_version": require_string(failure, "schema_version"),
        "failure_id": require_string(failure, "failure_id"),
        "failure_hash": require_sha256(failure, "failure_hash"),
        "failure_json_sha256": sha256_file(path / "failure.json"),
        "checksum_manifest_sha256": sha256_file(path / "SHA256SUMS"),
        "recursive_record_count": len(records),
        "execution": execution,
        "scheduler": scheduler,
        "runtime_boundary": boundary,
        "consumption": consumption,
    }


def _build_v3_recovery_authority(
    *, predecessor: ManagedExecution, failure_path: Path, failure: dict[str, Any]
) -> dict[str, Any]:
    predecessor_authority = require_dict(
        predecessor.manifest, "recovery_authority"
    )
    original_incident = require_dict(predecessor_authority, "incident")
    retry = _task_identity(predecessor.tasks)
    value: dict[str, Any] = {
        "schema_version": SUCCESSOR_V3_AUTHORITY_SCHEMA_VERSION,
        "predecessor_execution": {
            "directory": str(predecessor.directory),
            "schema_version": predecessor.manifest.get("schema_version"),
            "execution_generation": predecessor.manifest.get(
                "execution_generation"
            ),
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
            "managed_execution_sha256": sha256_file(
                predecessor.directory / "managed_execution.json"
            ),
            "static_checksums_sha256": sha256_file(
                predecessor.directory / ROOT_STATIC_MANIFEST
            ),
            "recovery_authority_hash": predecessor_authority.get(
                "authority_hash"
            ),
        },
        # Preserve the complete already-validated v2 recovery authority so
        # downstream whole-child evidence can exclude the original failed
        # production attempt without consulting mutable external state.
        "predecessor_recovery_authority": json.loads(
            json.dumps(predecessor_authority)
        ),
        "original_production_incident": original_incident,
        "failed_r3_preflight": _failure_binding(failure_path, failure),
        "zero_consumption": require_dict(
            predecessor_authority, "zero_consumption"
        ),
        "retry_equivalence": {
            **retry,
            "tasks_tsv_sha256": sha256_file(predecessor.directory / "tasks.tsv"),
            "scan_args_sha256": sha256_file(
                predecessor.directory / "scan_args.txt"
            ),
            "phase2a_tasks_sha256": sha256_file(
                predecessor.managed_child.directory / "tasks.tsv"
            ),
            "phase2a_scan_args_sha256": sha256_file(
                predecessor.managed_child.directory / "scan_args.txt"
            ),
            "simulation_source": require_dict(
                predecessor.manifest, "sources"
            )["simulation"],
            "runtime": predecessor.manifest["runtime"],
            "all_equal": True,
        },
        "authority_hash": None,
    }
    value["authority_hash"] = _semantic_hash(value, "authority_hash")
    return value


def preview_successor_v3_execution(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    failure_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
) -> SuccessorV3Preview:
    del fixture_sources
    root = repo_root.expanduser().resolve()
    phase2a_path, phase2a = load_phase2a_lock(root)
    formal_child = Path(require_string(phase2a, "canonical_directory"))
    requested_child = managed_child_dir or formal_child
    if requested_child.is_symlink() or requested_child.parent.is_symlink():
        raise ValueError("managed child must not be a symlink")
    child_path = requested_child.expanduser().resolve()
    if not test_mode and child_path != formal_child:
        raise ValueError("formal successor-v3 managed-child path is fixed")
    expected_predecessor = child_path.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V2
    requested_predecessor = predecessor_dir or expected_predecessor
    if requested_predecessor.is_symlink() or requested_predecessor.parent.is_symlink():
        raise ValueError("successor-v2 predecessor must not be a symlink")
    predecessor_path = requested_predecessor.expanduser().resolve()
    if not test_mode and predecessor_path != expected_predecessor:
        raise ValueError("formal successor-v2 predecessor path is fixed")
    predecessor = _load_v2_successor_execution(
        predecessor_path,
        repo_root=root if not test_mode else None,
        allow_test_mode=test_mode,
        require_readiness=False,
        verify_runtime=not test_mode,
        verify_live_predecessor=not test_mode,
    )
    if predecessor.managed_child.directory != child_path:
        raise ValueError("successor-v2 is not bound to the requested child")
    if not test_mode:
        if (
            predecessor.execution_id != FORMAL_SUCCESSOR_V2_EXECUTION_ID
            or predecessor.execution_hash != FORMAL_SUCCESSOR_V2_EXECUTION_HASH
        ):
            raise ValueError("formal successor-v2 immutable identity mismatch")
        _validate_phase2a_binding(
            predecessor.managed_child, phase2a_path, phase2a
        )
    for name in ("intents", "attempts", "finalized"):
        path = predecessor.directory / name
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ValueError(f"closed successor-v2 contains mutable {name} state")
    failure_path = _resolve_r3_failure_path(
        predecessor.managed_child, failure_dir, test_mode=test_mode
    )
    failure = _validated_r3_failure(
        failure_path,
        predecessor=predecessor,
        test_mode=test_mode,
        require_current_execution=True,
    )
    pilot = (
        pilot_dir
        or Path(
            require_string(
                require_dict(predecessor.manifest, "sealed_pilot"), "directory"
            )
        )
    ).expanduser().resolve()
    if not test_mode and pilot != child_path.parent / FORMAL_PILOT_NAME:
        raise ValueError("formal successor-v3 sealed-pilot path is fixed")
    authority = _build_v3_recovery_authority(
        predecessor=predecessor,
        failure_path=failure_path,
        failure=failure,
    )
    return SuccessorV3Preview(
        root,
        predecessor.managed_child,
        phase2a_path,
        phase2a,
        predecessor,
        failure_path,
        failure,
        authority,
        pilot,
        test_mode,
    )


def _v3_successor_execution_id(
    binding_hash: str,
    control_commit: str,
    original_incident_hash: str,
    predecessor_execution_hash: str,
    failure_hash: str,
) -> str:
    suffix = sha256_bytes(
        canonical_json(
            {
                "binding_hash": binding_hash,
                "control_commit": control_commit,
                "original_incident_hash": original_incident_hash,
                "predecessor_execution_hash": predecessor_execution_hash,
                "failed_r3_hash": failure_hash,
            }
        )
    )[:12]
    return f"sm-v1-production-bc-s1-execution-v3-{suffix}"


def _transform_v3_manifest(
    directory: Path, preview: SuccessorV3Preview
) -> None:
    lock_path = directory / ".control.lock"
    lock_path.chmod(0o400)
    manifest_path = directory / "managed_execution.json"
    payload = load_json(manifest_path)
    require_dict(require_dict(payload, "artifacts"), "control_lock")["mode"] = 0o400
    sources = require_dict(payload, "sources")
    predecessor_sources = require_dict(preview.predecessor.manifest, "sources")
    if (
        require_dict(sources, "simulation")
        != require_dict(predecessor_sources, "simulation")
        or payload.get("runtime") != preview.predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor-v3 changed frozen simulation/runtime identity")
    retry = require_dict(preview.recovery_authority, "retry_equivalence")
    if (
        retry.get("tasks_tsv_sha256") != sha256_file(directory / "tasks.tsv")
        or retry.get("scan_args_sha256")
        != sha256_file(directory / "scan_args.txt")
    ):
        raise ValueError("successor-v3 task/scan bytes differ from successor-v2")
    payload.update(
        {
            "schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3,
            "object_kind": SUCCESSOR_OBJECT_KIND,
            "execution_generation": SUCCESSOR_EXECUTION_GENERATION_V3,
            "phase": "Phase-2B-R2-v3",
            "recovery_authority": preview.recovery_authority,
            "readiness_gate": {
                "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE_V3.as_posix(),
                "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V3,
                "required_for_scheduler_contact": True,
                "compute_node_container_preflight_required": True,
                "automatic_submission": False,
                "r2_submission_ready": False,
            },
            "submission_policy": {
                "account": "PAS2524",
                "two_step_required": True,
                "initially_held": True,
                "no_requeue": True,
                "export_mode": "NONE",
                "formal_child": INITIAL_CHILD_ID,
                "formal_initial_mode": "predecessor-retry",
                "predecessor_retry_task_count": 32,
                "predecessor_retry_event_count": 8000,
                "r2_submittable": False,
            },
        }
    )
    control_commit = require_string(require_dict(sources, "control_plane"), "git_commit")
    incident = require_dict(
        preview.recovery_authority, "original_production_incident"
    )
    failed = require_dict(preview.recovery_authority, "failed_r3_preflight")
    payload["execution_id"] = _v3_successor_execution_id(
        require_string(require_dict(payload, "managed_child"), "binding_hash"),
        control_commit,
        require_sha256(incident, "incident_hash"),
        preview.predecessor.execution_hash,
        require_sha256(failed, "failure_hash"),
    )
    payload["execution_hash"] = None
    payload["execution_hash"] = _semantic_hash(payload, "execution_hash")
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (directory / "README.md").write_text(
        f"# {payload['execution_id']}\n\n"
        "Incident-bound BC-S1 recovery successor generation v3. It preserves "
        "the closed v2/R3 failure lineage and remains non-submittable until a "
        "new compute-node preflight and additive recovery readiness are frozen.\n",
        encoding="utf-8",
    )
    _write_static_checksums(directory)


R3RejectionValidator = Callable[
    [Path, ManagedExecution, bool, Optional[Path]], dict[str, Any]
]


def canonical_r3_rejection_parent(managed_child: Any) -> Path:
    return (
        managed_child.directory.parent.parent
        / "evidence/steel-module-production-r3/failures"
    )


def canonical_r3_administrative_rejection_manifest(
    managed_child: Any,
) -> Path:
    return (
        managed_child.directory.parent.parent
        / "evidence/steel-module-production-r3/"
        f"rejected-launch-{FORMAL_ADMIN_REJECTED_R3_JOB_ID}.sha256"
    )


def _resolve_r3_administrative_rejection_manifest(
    managed_child: Any,
    requested: Path | None,
    *,
    test_mode: bool,
) -> Path:
    expected = canonical_r3_administrative_rejection_manifest(managed_child)
    candidate = requested or expected
    if candidate.is_symlink() or candidate.parent.is_symlink():
        raise ValueError("R3 administrative-rejection manifest is unsafe")
    path = candidate.expanduser().resolve()
    if not test_mode and path != expected:
        raise ValueError("formal R3 administrative-rejection path is fixed")
    if not path.is_file():
        raise ValueError("R3 administrative-rejection manifest is missing")
    if (
        not test_mode
        and sha256_file(path) != FORMAL_ADMIN_REJECTED_R3_MANIFEST_SHA256
    ):
        raise ValueError("formal R3 administrative-rejection digest changed")
    return path


def _resolve_r3_rejection_path(
    managed_child: Any, requested: Path | None, *, test_mode: bool
) -> Path:
    if requested is not None:
        if requested.is_symlink() or requested.parent.is_symlink():
            raise ValueError("R3 probe-rejection bundle must not be a symlink")
        resolved = requested.expanduser().resolve()
        if (
            not test_mode
            and resolved.parent != canonical_r3_rejection_parent(managed_child)
        ):
            raise ValueError("formal R3 probe-rejection bundle parent is fixed")
        return resolved
    if test_mode:
        raise ValueError("test successor-v4 requires an explicit R3 rejection bundle")
    parent = canonical_r3_rejection_parent(managed_child)
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("formal R3 probe-rejection parent is missing or unsafe")
    candidates = tuple(
        path
        for path in sorted(
            parent.glob(
                "r3-container-probe-rejection-"
                f"{FORMAL_REJECTED_R3_JOB_ID}-*"
            )
        )
        if path.exists() or path.is_symlink()
    )
    if len(candidates) != 1:
        raise ValueError(
            "formal R3 probe-rejection bundle set is not unique and exact: "
            f"{[path.name for path in candidates]}"
        )
    if candidates[0].is_symlink() or not candidates[0].is_dir():
        raise ValueError("formal R3 probe-rejection bundle is unsafe")
    return candidates[0].resolve()


def _validated_r3_rejection(
    path: Path,
    *,
    predecessor: ManagedExecution,
    test_mode: bool,
    repo_root: Path | None,
    validator: R3RejectionValidator | None,
) -> dict[str, Any]:
    if validator is not None and not test_mode:
        raise ValueError(
            "formal R3 rejection validation cannot use a custom validator"
        )
    if validator is not None:
        value = validator(path, predecessor, test_mode, repo_root)
    else:
        # Keep the successor core importable while the independently owned
        # rejection sealer is integrated.  Formal materialization still fails
        # closed unless that validator is present and accepts the exact bundle.
        try:
            from steel_module_production_r3_probe_rejection_lib import (
                validate_r3_probe_rejection_bundle,
            )
        except ImportError as exc:
            raise ValueError(
                "R3 probe-rejection validator is not installed"
            ) from exc
        value = validate_r3_probe_rejection_bundle(
            path,
            execution_dir=predecessor.directory,
            require_current_execution=True,
            allow_test_mode=test_mode,
            require_canonical_location=not test_mode,
            repo_root=repo_root,
        )
    if not isinstance(value, dict):
        raise ValueError("R3 probe-rejection validator returned a non-object")
    return value


def _rejection_binding(
    path: Path,
    rejection: dict[str, Any],
    *,
    predecessor: ManagedExecution,
    test_mode: bool,
) -> dict[str, Any]:
    records = verify_recursive_checksums(path)
    execution = require_dict(rejection, "execution")
    scheduler = require_dict(rejection, "scheduler")
    probe = require_dict(rejection, "probe")
    consumption = require_dict(rejection, "consumption")
    false_keys = probe.get("false_report_keys")
    expected_false = [
        "control_lock_writer_open_rejected",
        "static_writer_open_rejected",
    ]
    if (
        rejection.get("test_mode") is not test_mode
        or rejection.get("accepted_compute_preflight_evidence") is not False
        or not require_string(rejection, "classification")
        or execution.get("directory") != str(predecessor.directory)
        or execution.get("execution_id") != predecessor.execution_id
        or execution.get("execution_hash") != predecessor.execution_hash
        or scheduler.get("job_id") != FORMAL_REJECTED_R3_JOB_ID
        or not isinstance(false_keys, list)
        or not all(isinstance(value, str) for value in false_keys)
        or sorted(false_keys) != expected_false
        or probe.get("container_return_code") != 0
        or probe.get("apptainer_invoked") is not True
        or probe.get("geant4_invoked") is not False
        or probe.get("raw_file_snapshot_scope")
        != "recursive-regular-files-only"
        or probe.get("raw_file_snapshot_unchanged") is not True
        or probe.get("historical_mountpoint_present") is not True
        or probe.get("execution_tree_unchanged") is not False
        or consumption
        != {"events_consumed": 0, "production_seeds_consumed": 0}
    ):
        raise ValueError("R3 probe-rejection boundary is not exact")
    return {
        "directory": str(path),
        "schema_version": require_string(rejection, "schema_version"),
        "accepted_compute_preflight_evidence": False,
        "classification": require_string(rejection, "classification"),
        "rejection_id": require_string(rejection, "rejection_id"),
        "rejection_hash": require_sha256(rejection, "rejection_hash"),
        "checksum_manifest_sha256": sha256_file(path / "SHA256SUMS"),
        "recursive_record_count": len(records),
        "execution": json.loads(json.dumps(execution)),
        "scheduler": json.loads(json.dumps(scheduler)),
        "probe": json.loads(json.dumps(probe)),
        "consumption": json.loads(json.dumps(consumption)),
    }


def _build_v4_recovery_authority(
    *,
    predecessor: ManagedExecution,
    administrative_rejection_manifest: Path,
    rejection_path: Path,
    rejection: dict[str, Any],
    test_mode: bool,
) -> dict[str, Any]:
    predecessor_authority = require_dict(
        predecessor.manifest, "recovery_authority"
    )
    retry = _task_identity(predecessor.tasks)
    value: dict[str, Any] = {
        "schema_version": SUCCESSOR_V4_AUTHORITY_SCHEMA_VERSION,
        "predecessor_execution": {
            "directory": str(predecessor.directory),
            "schema_version": predecessor.manifest.get("schema_version"),
            "execution_generation": predecessor.manifest.get(
                "execution_generation"
            ),
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
            "managed_execution_sha256": sha256_file(
                predecessor.directory / "managed_execution.json"
            ),
            "static_checksums_sha256": sha256_file(
                predecessor.directory / ROOT_STATIC_MANIFEST
            ),
            "recovery_authority_hash": predecessor_authority.get(
                "authority_hash"
            ),
        },
        "predecessor_recovery_authority": json.loads(
            json.dumps(predecessor_authority)
        ),
        "original_production_incident": json.loads(
            json.dumps(
                require_dict(predecessor_authority, "original_production_incident")
            )
        ),
        "failed_r3_preflight": json.loads(
            json.dumps(require_dict(predecessor_authority, "failed_r3_preflight"))
        ),
        "administrative_r3_launch_rejection": {
            "classification": "pre-probe-administrative-launch-rejection",
            "job_id": FORMAL_ADMIN_REJECTED_R3_JOB_ID,
            "manifest_path": str(administrative_rejection_manifest),
            "manifest_sha256": sha256_file(administrative_rejection_manifest),
            "compute_preflight": False,
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "rejected_r3_preflight": _rejection_binding(
            rejection_path,
            rejection,
            predecessor=predecessor,
            test_mode=test_mode,
        ),
        "zero_consumption": json.loads(
            json.dumps(require_dict(predecessor_authority, "zero_consumption"))
        ),
        "retry_equivalence": {
            **retry,
            "tasks_tsv_sha256": sha256_file(predecessor.directory / "tasks.tsv"),
            "scan_args_sha256": sha256_file(
                predecessor.directory / "scan_args.txt"
            ),
            "phase2a_tasks_sha256": sha256_file(
                predecessor.managed_child.directory / "tasks.tsv"
            ),
            "phase2a_scan_args_sha256": sha256_file(
                predecessor.managed_child.directory / "scan_args.txt"
            ),
            "simulation_source": require_dict(
                predecessor.manifest, "sources"
            )["simulation"],
            "runtime": predecessor.manifest["runtime"],
            "all_equal": True,
        },
        "authority_hash": None,
    }
    value["authority_hash"] = _semantic_hash(value, "authority_hash")
    return value


def preview_successor_v4_execution(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    rejection_dir: Path | None = None,
    administrative_rejection_manifest: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    rejection_validator: R3RejectionValidator | None = None,
) -> SuccessorV4Preview:
    del fixture_sources
    root = repo_root.expanduser().resolve()
    phase2a_path, phase2a = load_phase2a_lock(root)
    formal_child = Path(require_string(phase2a, "canonical_directory"))
    requested_child = managed_child_dir or formal_child
    if requested_child.is_symlink() or requested_child.parent.is_symlink():
        raise ValueError("managed child must not be a symlink")
    child_path = requested_child.expanduser().resolve()
    if not test_mode and child_path != formal_child:
        raise ValueError("formal successor-v4 managed-child path is fixed")
    expected_predecessor = child_path.parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V3
    requested_predecessor = predecessor_dir or expected_predecessor
    if requested_predecessor.is_symlink() or requested_predecessor.parent.is_symlink():
        raise ValueError("successor-v3 predecessor must not be a symlink")
    predecessor_path = requested_predecessor.expanduser().resolve()
    if not test_mode and predecessor_path != expected_predecessor:
        raise ValueError("formal successor-v3 predecessor path is fixed")
    predecessor = _load_v3_successor_execution(
        predecessor_path,
        repo_root=root if not test_mode else None,
        allow_test_mode=test_mode,
        require_readiness=False,
        verify_runtime=not test_mode,
        verify_live_predecessor=not test_mode,
    )
    if predecessor.managed_child.directory != child_path:
        raise ValueError("successor-v3 is not bound to the requested child")
    if not test_mode:
        _validate_phase2a_binding(
            predecessor.managed_child, phase2a_path, phase2a
        )
    rejection_path = _resolve_r3_rejection_path(
        predecessor.managed_child, rejection_dir, test_mode=test_mode
    )
    administrative_rejection = _resolve_r3_administrative_rejection_manifest(
        predecessor.managed_child,
        administrative_rejection_manifest,
        test_mode=test_mode,
    )
    rejection = _validated_r3_rejection(
        rejection_path,
        predecessor=predecessor,
        test_mode=test_mode,
        repo_root=root,
        validator=rejection_validator,
    )
    rejection_probe = require_dict(rejection, "probe")
    validate_rejected_successor_v3_boundary_stable(
        predecessor,
        probe_token=require_string(rejection_probe, "probe_token"),
        container_probe_root=require_string(
            rejection_probe, "container_probe_root"
        ),
        repo_root=root,
        recorded_mountpoint=require_dict(
            rejection_probe, "historical_mountpoint"
        ),
    )
    pilot = (
        pilot_dir
        or Path(
            require_string(
                require_dict(predecessor.manifest, "sealed_pilot"), "directory"
            )
        )
    ).expanduser().resolve()
    if not test_mode and pilot != child_path.parent / FORMAL_PILOT_NAME:
        raise ValueError("formal successor-v4 sealed-pilot path is fixed")
    authority = _build_v4_recovery_authority(
        predecessor=predecessor,
        administrative_rejection_manifest=administrative_rejection,
        rejection_path=rejection_path,
        rejection=rejection,
        test_mode=test_mode,
    )
    return SuccessorV4Preview(
        root,
        predecessor.managed_child,
        phase2a_path,
        phase2a,
        predecessor,
        administrative_rejection,
        rejection_path,
        rejection,
        authority,
        pilot,
        test_mode,
    )


def _v4_successor_execution_id(
    binding_hash: str,
    control_commit: str,
    predecessor_execution_hash: str,
    rejection_hash: str,
) -> str:
    suffix = sha256_bytes(
        canonical_json(
            {
                "binding_hash": binding_hash,
                "control_commit": control_commit,
                "predecessor_execution_hash": predecessor_execution_hash,
                "rejected_r3_hash": rejection_hash,
            }
        )
    )[:12]
    return f"sm-v1-production-bc-s1-execution-v4-{suffix}"


def _transform_v4_manifest(
    directory: Path, preview: SuccessorV4Preview
) -> None:
    lock_path = directory / ".control.lock"
    lock_path.chmod(0o400)
    manifest_path = directory / "managed_execution.json"
    payload = load_json(manifest_path)
    require_dict(require_dict(payload, "artifacts"), "control_lock")["mode"] = 0o400
    sources = require_dict(payload, "sources")
    predecessor_sources = require_dict(preview.predecessor.manifest, "sources")
    if (
        require_dict(sources, "simulation")
        != require_dict(predecessor_sources, "simulation")
        or payload.get("runtime") != preview.predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor-v4 changed frozen simulation/runtime identity")
    retry = require_dict(preview.recovery_authority, "retry_equivalence")
    if (
        retry.get("tasks_tsv_sha256") != sha256_file(directory / "tasks.tsv")
        or retry.get("scan_args_sha256")
        != sha256_file(directory / "scan_args.txt")
    ):
        raise ValueError("successor-v4 task/scan bytes differ from successor-v3")
    payload.update(
        {
            "schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION,
            "object_kind": SUCCESSOR_OBJECT_KIND,
            "execution_generation": SUCCESSOR_EXECUTION_GENERATION,
            "phase": "Phase-2B-R2-v4",
            "recovery_authority": preview.recovery_authority,
            "readiness_gate": {
                "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE.as_posix(),
                "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION,
                "required_for_scheduler_contact": True,
                "compute_node_container_preflight_required": True,
                "automatic_submission": False,
                "r2_submission_ready": False,
            },
            "submission_policy": {
                "account": "PAS2524",
                "two_step_required": True,
                "initially_held": True,
                "no_requeue": True,
                "export_mode": "NONE",
                "formal_child": INITIAL_CHILD_ID,
                "formal_initial_mode": "predecessor-retry",
                "predecessor_retry_task_count": 32,
                "predecessor_retry_event_count": 8000,
                "r2_submittable": False,
            },
        }
    )
    control_commit = require_string(
        require_dict(sources, "control_plane"), "git_commit"
    )
    rejected = require_dict(preview.recovery_authority, "rejected_r3_preflight")
    payload["execution_id"] = _v4_successor_execution_id(
        require_string(require_dict(payload, "managed_child"), "binding_hash"),
        control_commit,
        preview.predecessor.execution_hash,
        require_sha256(rejected, "rejection_hash"),
    )
    payload["execution_hash"] = None
    payload["execution_hash"] = _semantic_hash(payload, "execution_hash")
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (directory / "README.md").write_text(
        f"# {payload['execution_id']}\n\n"
        "Incident-bound BC-S1 recovery successor generation v4. It preserves "
        "the immutable v3 and rejected compute-preflight lineage and remains "
        "non-submittable until a new compute-node preflight and additive "
        "recovery readiness are frozen.\n",
        encoding="utf-8",
    )
    _write_static_checksums(directory)


R3PreworkspaceFailureValidator = Callable[
    [Path, ManagedExecution, bool, Optional[Path]], dict[str, Any]
]


def canonical_r3_preworkspace_failure_parent(managed_child: Any) -> Path:
    return (
        managed_child.directory.parent.parent
        / "evidence/steel-module-production-r3/failures"
    )


def _resolve_r3_preworkspace_failure_path(
    managed_child: Any, requested: Path | None, *, test_mode: bool
) -> Path:
    parent = canonical_r3_preworkspace_failure_parent(managed_child)
    if requested is not None:
        if requested.is_symlink() or requested.parent.is_symlink():
            raise ValueError("R3 pre-workspace failure bundle must not be a symlink")
        resolved = requested.expanduser().resolve()
        if not test_mode and resolved.parent != parent:
            raise ValueError("formal R3 pre-workspace failure parent is fixed")
        return resolved
    if test_mode:
        raise ValueError(
            "test successor-v5 requires an explicit pre-workspace failure bundle"
        )
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("formal R3 pre-workspace failure parent is missing or unsafe")
    candidates = tuple(
        path
        for path in sorted(
            parent.glob(f"pre-workspace-{FORMAL_PREWORKSPACE_FAILED_R3_JOB_ID}-*")
        )
        if path.exists() or path.is_symlink()
    )
    if len(candidates) != 1:
        raise ValueError(
            "formal R3 pre-workspace failure bundle set is not unique and exact: "
            f"{[path.name for path in candidates]}"
        )
    if candidates[0].is_symlink() or not candidates[0].is_dir():
        raise ValueError("formal R3 pre-workspace failure bundle is unsafe")
    return candidates[0].resolve()


def _validated_r3_preworkspace_failure(
    path: Path,
    *,
    predecessor: ManagedExecution,
    test_mode: bool,
    repo_root: Path | None,
    validator: R3PreworkspaceFailureValidator | None,
    require_current_execution: bool,
) -> dict[str, Any]:
    if validator is not None and not test_mode:
        raise ValueError(
            "formal R3 pre-workspace failure cannot use a custom validator"
        )
    if validator is not None:
        value = validator(path, predecessor, test_mode, repo_root)
    else:
        try:
            from steel_module_production_r3_preworkspace_failure_lib import (
                validate_r3_preworkspace_failure_bundle,
            )
        except ImportError as exc:
            raise ValueError(
                "R3 pre-workspace failure validator is not installed"
            ) from exc
        value = validate_r3_preworkspace_failure_bundle(
            path,
            execution_dir=predecessor.directory,
            require_current_execution=require_current_execution,
            allow_test_mode=test_mode,
            require_canonical_location=not test_mode,
            repo_root=repo_root,
        )
    if not isinstance(value, dict):
        raise ValueError(
            "R3 pre-workspace failure validator returned a non-object"
        )
    return value


def _preworkspace_failure_binding(
    path: Path,
    failure: dict[str, Any],
    *,
    predecessor: ManagedExecution,
    test_mode: bool,
) -> dict[str, Any]:
    records = verify_recursive_checksums(path)
    execution = require_dict(failure, "execution")
    scheduler = require_dict(failure, "scheduler")
    runtime = require_dict(failure, "runtime_boundary")
    inputs = require_dict(failure, "inputs")
    consumption = require_dict(failure, "consumption")
    if (
        failure.get("schema_version")
        != "steel-module-production-r3-pre-workspace-failure-evidence-v1"
        or failure.get("test_mode") is not test_mode
        or failure.get("accepted_compute_preflight_evidence") is not False
        or failure.get("classification")
        != "portable-exclusive-control-lock-open-failure"
        or failure.get("failure_stage")
        != "portable-exclusive-control-lock-before-workspace"
        or failure.get("future_successor_required") is not True
        or execution.get("directory") != str(predecessor.directory)
        or execution.get("execution_id") != predecessor.execution_id
        or execution.get("execution_hash") != predecessor.execution_hash
        or scheduler.get("job_id") != FORMAL_PREWORKSPACE_FAILED_R3_JOB_ID
        or not require_string(scheduler, "job_name")
        or runtime.get("portable_control_lock_operation") != "exclusive"
        or runtime.get("workspace_created") is not False
        or runtime.get("raw_workspace_created") is not False
        or runtime.get("apptainer_invoked") is not False
        or runtime.get("geant4_invoked") is not False
        or runtime.get("simulation_started") is not False
        or consumption
        != {"events_consumed": 0, "production_seeds_consumed": 0}
        or set(inputs)
        != {
            "held_scontrol_sha256",
            "sacct_sha256",
            "squeue_sha256",
            "slurm_output_sha256",
        }
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in inputs.values()
        )
    ):
        raise ValueError("R3 pre-workspace failure boundary is not exact")
    return {
        "directory": str(path),
        "schema_version": require_string(failure, "schema_version"),
        "failure_id": require_string(failure, "failure_id"),
        "failure_hash": require_sha256(failure, "failure_hash"),
        "failure_json_sha256": sha256_file(path / "failure.json"),
        "checksum_manifest_sha256": sha256_file(path / "SHA256SUMS"),
        "recursive_record_count": len(records),
        "accepted_compute_preflight_evidence": False,
        "classification": require_string(failure, "classification"),
        "failure_stage": require_string(failure, "failure_stage"),
        "future_successor_required": True,
        "execution": json.loads(json.dumps(execution)),
        "scheduler": json.loads(json.dumps(scheduler)),
        "runtime_boundary": json.loads(json.dumps(runtime)),
        "inputs": json.loads(json.dumps(inputs)),
        "consumption": json.loads(json.dumps(consumption)),
    }


def _build_v5_recovery_authority(
    *,
    predecessor: ManagedExecution,
    failure_path: Path,
    failure: dict[str, Any],
    test_mode: bool,
) -> dict[str, Any]:
    predecessor_authority = require_dict(
        predecessor.manifest, "recovery_authority"
    )
    retry = _task_identity(predecessor.tasks)
    value: dict[str, Any] = {
        "schema_version": SUCCESSOR_V5_AUTHORITY_SCHEMA_VERSION,
        "predecessor_execution": {
            "directory": str(predecessor.directory),
            "schema_version": predecessor.manifest.get("schema_version"),
            "execution_generation": predecessor.manifest.get(
                "execution_generation"
            ),
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
            "managed_execution_sha256": sha256_file(
                predecessor.directory / "managed_execution.json"
            ),
            "static_checksums_sha256": sha256_file(
                predecessor.directory / ROOT_STATIC_MANIFEST
            ),
            "recovery_authority_hash": predecessor_authority.get(
                "authority_hash"
            ),
        },
        "predecessor_recovery_authority": json.loads(
            json.dumps(predecessor_authority)
        ),
        "preworkspace_r3_failure": _preworkspace_failure_binding(
            failure_path,
            failure,
            predecessor=predecessor,
            test_mode=test_mode,
        ),
        "zero_consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
            "seed_reuse_authorized": True,
        },
        "retry_equivalence": {
            **retry,
            "tasks_tsv_sha256": sha256_file(predecessor.directory / "tasks.tsv"),
            "scan_args_sha256": sha256_file(
                predecessor.directory / "scan_args.txt"
            ),
            "phase2a_tasks_sha256": sha256_file(
                predecessor.managed_child.directory / "tasks.tsv"
            ),
            "phase2a_scan_args_sha256": sha256_file(
                predecessor.managed_child.directory / "scan_args.txt"
            ),
            "simulation_source": require_dict(
                predecessor.manifest, "sources"
            )["simulation"],
            "runtime": predecessor.manifest["runtime"],
            "all_equal": True,
        },
        "authority_hash": None,
    }
    value["authority_hash"] = _semantic_hash(value, "authority_hash")
    return value


def preview_successor_v5_execution(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    preworkspace_failure_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    rejection_validator: R3RejectionValidator | None = None,
    preworkspace_failure_validator: R3PreworkspaceFailureValidator | None = None,
) -> SuccessorV5Preview:
    del fixture_sources
    root = repo_root.expanduser().resolve()
    phase2a_path, phase2a = load_phase2a_lock(root)
    formal_child = Path(require_string(phase2a, "canonical_directory"))
    requested_child = managed_child_dir or formal_child
    if requested_child.is_symlink() or requested_child.parent.is_symlink():
        raise ValueError("managed child must not be a symlink")
    child_path = requested_child.expanduser().resolve()
    if not test_mode and child_path != formal_child:
        raise ValueError("formal successor-v5 managed-child path is fixed")
    expected_predecessor = child_path.parent / FORMAL_SUCCESSOR_EXECUTION_NAME
    requested_predecessor = predecessor_dir or expected_predecessor
    if requested_predecessor.is_symlink() or requested_predecessor.parent.is_symlink():
        raise ValueError("successor-v4 predecessor must not be a symlink")
    predecessor_path = requested_predecessor.expanduser().resolve()
    if not test_mode and predecessor_path != expected_predecessor:
        raise ValueError("formal successor-v4 predecessor path is fixed")
    predecessor = _load_v4_successor_execution(
        predecessor_path,
        repo_root=root if not test_mode else None,
        allow_test_mode=test_mode,
        require_readiness=False,
        verify_runtime=not test_mode,
        verify_live_predecessor=not test_mode,
        rejection_validator=rejection_validator,
    )
    if predecessor.managed_child.directory != child_path:
        raise ValueError("successor-v4 is not bound to the requested child")
    if not test_mode:
        _validate_phase2a_binding(
            predecessor.managed_child, phase2a_path, phase2a
        )
    failure_path = _resolve_r3_preworkspace_failure_path(
        predecessor.managed_child,
        preworkspace_failure_dir,
        test_mode=test_mode,
    )
    failure = _validated_r3_preworkspace_failure(
        failure_path,
        predecessor=predecessor,
        test_mode=test_mode,
        repo_root=root,
        validator=preworkspace_failure_validator,
        require_current_execution=True,
    )
    pilot = (
        pilot_dir
        or Path(
            require_string(
                require_dict(predecessor.manifest, "sealed_pilot"), "directory"
            )
        )
    ).expanduser().resolve()
    if not test_mode and pilot != child_path.parent / FORMAL_PILOT_NAME:
        raise ValueError("formal successor-v5 sealed-pilot path is fixed")
    authority = _build_v5_recovery_authority(
        predecessor=predecessor,
        failure_path=failure_path,
        failure=failure,
        test_mode=test_mode,
    )
    return SuccessorV5Preview(
        root,
        predecessor.managed_child,
        phase2a_path,
        phase2a,
        predecessor,
        failure_path,
        failure,
        authority,
        pilot,
        test_mode,
    )


def _v5_successor_execution_id(
    binding_hash: str,
    control_commit: str,
    predecessor_execution_hash: str,
    preworkspace_failure_hash: str,
) -> str:
    suffix = sha256_bytes(
        canonical_json(
            {
                "binding_hash": binding_hash,
                "control_commit": control_commit,
                "predecessor_execution_hash": predecessor_execution_hash,
                "preworkspace_failure_hash": preworkspace_failure_hash,
            }
        )
    )[:12]
    return f"sm-v1-production-bc-s1-execution-v5-{suffix}"


def _transform_v5_manifest(
    directory: Path, preview: SuccessorV5Preview
) -> None:
    # Keep the portable lock at its creation mode 0600.  GPFS requires an
    # O_RDWR descriptor for LOCK_EX; host read-only 0400 evidence can only be
    # validated with LOCK_SH and must never be promoted in place.
    lock_path = directory / ".control.lock"
    if lock_path.stat().st_mode & 0o777 != 0o600:
        raise ValueError("successor-v5 portable control lock was not created 0600")
    manifest_path = directory / "managed_execution.json"
    payload = load_json(manifest_path)
    lock_record = require_dict(require_dict(payload, "artifacts"), "control_lock")
    if lock_record.get("mode") != 0o600:
        raise ValueError("successor-v5 portable control-lock record is not 0600")
    sources = require_dict(payload, "sources")
    predecessor_sources = require_dict(preview.predecessor.manifest, "sources")
    if (
        require_dict(sources, "simulation")
        != require_dict(predecessor_sources, "simulation")
        or payload.get("runtime") != preview.predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor-v5 changed frozen simulation/runtime identity")
    retry = require_dict(preview.recovery_authority, "retry_equivalence")
    if (
        retry.get("tasks_tsv_sha256") != sha256_file(directory / "tasks.tsv")
        or retry.get("scan_args_sha256")
        != sha256_file(directory / "scan_args.txt")
    ):
        raise ValueError("successor-v5 task/scan bytes differ from successor-v4")
    payload.update(
        {
            "schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5,
            "object_kind": SUCCESSOR_OBJECT_KIND,
            "execution_generation": SUCCESSOR_EXECUTION_GENERATION_V5,
            "phase": "Phase-2B-R2-v5",
            "recovery_authority": preview.recovery_authority,
            "readiness_gate": {
                "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE_V5.as_posix(),
                "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V5,
                "required_for_scheduler_contact": True,
                "compute_node_container_preflight_required": True,
                "automatic_submission": False,
                "r2_submission_ready": False,
            },
            "submission_policy": {
                "account": "PAS2524",
                "two_step_required": True,
                "initially_held": True,
                "no_requeue": True,
                "export_mode": "NONE",
                "formal_child": INITIAL_CHILD_ID,
                "formal_initial_mode": "predecessor-retry",
                "predecessor_retry_task_count": 32,
                "predecessor_retry_event_count": 8000,
                "r2_submittable": False,
            },
        }
    )
    control_commit = require_string(
        require_dict(sources, "control_plane"), "git_commit"
    )
    failure = require_dict(preview.recovery_authority, "preworkspace_r3_failure")
    payload["execution_id"] = _v5_successor_execution_id(
        require_string(require_dict(payload, "managed_child"), "binding_hash"),
        control_commit,
        preview.predecessor.execution_hash,
        require_sha256(failure, "failure_hash"),
    )
    payload["execution_hash"] = None
    payload["execution_hash"] = _semantic_hash(payload, "execution_hash")
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (directory / "README.md").write_text(
        f"# {payload['execution_id']}\n\n"
        "Incident-bound BC-S1 recovery successor generation v5. It preserves "
        "the immutable v4 pre-workspace GPFS lock failure and exposes only a "
        "fresh 0600 portable lock plus empty mutable roots. It remains "
        "non-submittable until a new compute-node preflight is accepted.\n",
        encoding="utf-8",
    )
    _write_static_checksums(directory)


def _recovery_readiness_contract(
    execution: ManagedExecution,
) -> tuple[Path, str, list[str]]:
    schema = execution.manifest.get("schema_version")
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3:
        return (
            RECOVERY_READINESS_LOCK_RELATIVE_V3,
            SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V3,
            [FORMAL_FAILED_R3_JOB_ID],
        )
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        return (
            RECOVERY_READINESS_LOCK_RELATIVE,
            SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION,
            [FORMAL_FAILED_R3_JOB_ID, FORMAL_REJECTED_R3_JOB_ID],
        )
    raise ValueError("execution generation cannot satisfy recovery readiness")


def successor_recovery_readiness_path(
    execution: ManagedExecution, *, repo_root: Path | None
) -> Path:
    """Locate the future tracked R4 lock from live or recorded repository root."""

    relative, _, _ = _recovery_readiness_contract(execution)
    if repo_root is not None:
        candidate = repo_root.expanduser().resolve() / relative
        if candidate.exists() or candidate.is_symlink():
            return candidate
    recorded_phase2a = Path(
        require_string(require_dict(execution.manifest, "phase2a_lock"), "path")
    )
    suffix = PHASE2A_LOCK_RELATIVE.parts
    if tuple(recorded_phase2a.parts[-len(suffix):]) != suffix:
        raise ValueError("successor recorded Phase-2A path cannot locate live repository")
    live_root = Path(*recorded_phase2a.parts[:-len(suffix)])
    return live_root / relative


def _validate_formal_r3_held_job_paths(
    execution_directory: Path, accounting: dict[str, Any]
) -> None:
    """Re-derive held-job paths independently of the accounting writer."""

    from steel_module_production_r3_probe_lib import canonical_r3_evidence_root

    execution = execution_directory.expanduser().resolve()
    schema = load_json(execution / "managed_execution.json").get("schema_version")
    if (
        schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5
        and execution.name == FORMAL_SUCCESSOR_EXECUTION_NAME_V5
    ):
        launcher = R3_COPY_SAFE_LAUNCHER_RELATIVE_V5
    elif (
        schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION
        and execution.name == FORMAL_SUCCESSOR_EXECUTION_NAME
    ):
        launcher = R3_COPY_SAFE_LAUNCHER_RELATIVE
    else:
        raise ValueError("formal R3 held-job execution schema is unsupported")
    held = require_dict(accounting, "held_scheduler")
    expected = {
        "command": str(execution / launcher),
        "work_dir": str(execution),
        "stdout": str(
            canonical_r3_evidence_root(execution)
            / f"slurm-{require_string(accounting, 'job_id')}.out"
        ),
    }
    if any(held.get(name) != value for name, value in expected.items()):
        raise ValueError("formal R3 held-job path identity mismatch")


def successor_r3_preflight_binding(
    execution: ManagedExecution,
    evidence_dir: Path,
    *,
    allow_test_mode: bool = False,
    require_current_snapshot: bool = False,
) -> dict[str, Any]:
    """Re-derive the exact R3 evidence binding from immutable source files."""

    # Local imports avoid successor -> R3 -> successor initialization cycles.
    from steel_module_production_r3_probe_lib import (
        EVIDENCE_SCHEMA_VERSION,
        RAW_RETIREMENT_MARKER_COPY_NAME,
        RETIREMENT_MARKER_SCHEMA_VERSION,
        canonical_r3_evidence_root,
        validate_closed_successor_v4_probe_boundary,
        validate_r3_probe_evidence,
        validate_raw_probe_workspace,
        validate_terminal_accounting_input,
    )

    requested = evidence_dir.expanduser()
    if requested.is_symlink() or not requested.is_dir():
        raise ValueError("successor R3 evidence must be a regular directory")
    directory = requested.resolve()
    evidence = validate_r3_probe_evidence(
        directory, allow_test_mode=allow_test_mode
    )
    raw = validate_raw_probe_workspace(
        directory / "raw",
        allow_test_mode=allow_test_mode,
        require_recorded_location=False,
    )
    accounting = validate_terminal_accounting_input(directory / "accounting")
    test_mode = execution.manifest.get("test_mode")
    if (
        not isinstance(test_mode, bool)
        or evidence.get("test_mode") is not test_mode
        or raw.get("test_mode") is not test_mode
        or (test_mode and not allow_test_mode)
        or evidence.get("accepted_compute_preflight_evidence")
        is not (not test_mode)
    ):
        raise ValueError("successor R3 evidence mode is not admissible")
    if not test_mode:
        expected = (
            canonical_r3_evidence_root(execution.directory)
            / "evidence"
            / require_string(evidence, "evidence_id")
        )
        if directory != expected:
            raise ValueError("formal successor R3 evidence path is not canonical")
        _validate_formal_r3_held_job_paths(execution.directory, accounting)
    if (
        raw.get("execution_id") != execution.execution_id
        or raw.get("execution_hash") != execution.execution_hash
        or raw.get("execution_directory") != str(execution.directory)
        or evidence.get("execution_id") != execution.execution_id
        or evidence.get("execution_hash") != execution.execution_hash
        or evidence.get("job_id") != accounting.get("job_id")
    ):
        raise ValueError("successor R3 evidence execution identity mismatch")
    expected_runtime = require_dict(execution.manifest, "runtime")
    raw_runtime = require_dict(raw, "runtime")
    for key in (
        "image_sha256",
        "g4_data_manifest_sha256",
        "executable_sha256",
    ):
        if raw_runtime.get(key) != expected_runtime.get(key):
            raise ValueError(f"successor R3 runtime identity mismatch: {key}")
    lock_record = require_dict(require_dict(execution.manifest, "artifacts"), "control_lock")
    lock_diagnostics = require_dict(raw, "portable_lock_diagnostics")
    for raw_key, manifest_key in (
        ("protocol", "protocol"),
        ("creation_device", "creation_device"),
        ("creation_inode", "creation_inode"),
        ("mode", "mode"),
        ("size_bytes", "size_bytes"),
        ("sha256", "sha256"),
        ("link_count", "link_count"),
    ):
        if lock_diagnostics.get(raw_key) != lock_record.get(manifest_key):
            raise ValueError(
                f"successor R3 portable-lock diagnostic mismatch: {raw_key}"
            )
    recorded_snapshot = require_sha256(
        raw, "execution_snapshot_before_sha256"
    )
    if raw.get("execution_snapshot_after_sha256") != recorded_snapshot:
        raise ValueError("successor changed during the R3 compute-node probe")
    if require_current_snapshot:
        lifecycle = require_dict(raw, "mountpoint_lifecycle")
        marker_payload = {
            "schema_version": RETIREMENT_MARKER_SCHEMA_VERSION,
            "probe_token": require_string(raw, "probe_token"),
            "execution_id": raw.get("execution_id"),
            "execution_hash": raw.get("execution_hash"),
            "challenge_sha256": raw.get("challenge_sha256"),
            "source_relative_path": (
                "attempts/.r3-probe-" + require_string(raw, "probe_token")
            ),
            "retirement_mechanism": "in-place-retained-mountpoint",
            "raw_marker_copy_name": RAW_RETIREMENT_MARKER_COPY_NAME,
            "rename_performed": False,
            "directory_entry_removed": False,
            "deletion_performed": False,
            "execution_closed": True,
            "future_successor_required": True,
        }
        validate_closed_successor_v4_probe_boundary(
            execution.directory,
            marker_payload=marker_payload,
            expected_marker_sha256=require_sha256(
                lifecycle, "retirement_marker_sha256"
            ),
        )
        current_records = recursive_file_records(execution.directory, exclude=())
        current_snapshot = sha256_bytes(canonical_json(current_records))
        if (
            raw.get("closed_execution_snapshot_sha256") != current_snapshot
            or raw.get("closed_execution_snapshot_record_count")
            != len(current_records)
        ):
            raise ValueError("successor changed since the R3 compute-node probe")
    records = verify_recursive_checksums(directory)
    return {
        "directory": str(directory),
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": require_string(evidence, "evidence_id"),
        "evidence_hash": require_sha256(evidence, "evidence_hash"),
        "evidence_json_sha256": sha256_file(directory / "evidence.json"),
        "checksum_manifest_sha256": sha256_file(directory / "SHA256SUMS"),
        "recursive_record_count": len(records),
        "job_id": require_string(accounting, "job_id"),
        "sacct_sha256": sha256_file(directory / "accounting" / "sacct.psv"),
        "held_scontrol_sha256": sha256_file(
            directory / "accounting" / "held_scontrol.txt"
        ),
        "held_command": require_string(
            require_dict(accounting, "held_scheduler"), "command"
        ),
        "held_work_dir": require_string(
            require_dict(accounting, "held_scheduler"), "work_dir"
        ),
        "held_stdout": require_string(
            require_dict(accounting, "held_scheduler"), "stdout"
        ),
        "terminal_accounting_sha256": sha256_file(
            directory / "accounting" / "terminal_accounting.json"
        ),
        "raw_result_sha256": sha256_file(directory / "raw" / "probe_result.json"),
        "execution_snapshot_sha256": recorded_snapshot,
        "closed_execution_snapshot_sha256": require_sha256(
            raw, "closed_execution_snapshot_sha256"
        ),
        "execution_closed": True,
        "future_successor_required": True,
        "apptainer_path": require_string(raw_runtime, "apptainer_path"),
        "apptainer_sha256": require_sha256(raw_runtime, "apptainer_sha256"),
        "apptainer_version": require_string(raw_runtime, "apptainer_version"),
    }


def _validate_successor_mutable_state_after_intent(
    execution: ManagedExecution,
) -> None:
    """Reject unexplained top-level mutable state after the first intent.

    Before the first intent, R4 requires byte-for-byte equality with the R3
    pristine snapshot.  Once a valid intent exists, journals and task leaves
    necessarily change; at that point every top-level attempt must still be
    explained by a checksum-valid intent/event chain, and any finalized bundle
    must pass its independent recursive validation.
    """

    from steel_module_production_phase2b_lib import (
        attempt_state,
        list_attempt_ids,
    )

    intent_ids = list_attempt_ids(execution)
    if not intent_ids:
        raise ValueError("successor mutable-state validation requires an intent")
    attempts_root = execution.directory / "attempts"
    attempt_entries = tuple(sorted(attempts_root.iterdir(), key=lambda path: path.name))
    for path in attempt_entries:
        if path.is_symlink() or not path.is_dir() or path.name not in intent_ids:
            raise ValueError("successor contains an orphan or unsafe attempt")
        attempt_state(execution, path.name)
    finalized = execution.directory / "finalized"
    finalized_entries = tuple(finalized.iterdir())
    if finalized_entries:
        from steel_module_production_checkpoint_lib import (
            SUCCESSOR_FINALIZATION_BUNDLE_NAME,
            load_managed_finalization,
        )

        if (
            len(finalized_entries) != 1
            or finalized_entries[0].name
            != SUCCESSOR_FINALIZATION_BUNDLE_NAME
            or finalized_entries[0].is_symlink()
            or not finalized_entries[0].is_dir()
        ):
            raise ValueError(
                "successor finalized state is not the exact whole-child bundle"
            )
        load_managed_finalization(
            finalized_entries[0], allow_test_mode=False
        )


def build_successor_recovery_readiness_candidate(
    execution: ManagedExecution,
    *,
    preflight_evidence_dir: Path,
) -> dict[str, Any]:
    """Build the exact prospective R4 lock; it is inert until tracked alone."""

    if execution.manifest.get("test_mode") is not False:
        raise ValueError("formal recovery readiness cannot use test evidence")
    attempts_root = execution.directory / "attempts"
    if not attempts_root.is_symlink() and attempts_root.is_dir():
        attempt_entries = tuple(attempts_root.iterdir())
        if len(attempt_entries) == 1:
            retained = attempt_entries[0]
            # A successful R3 preflight deliberately seals and retains its
            # exact leased mountpoint.  That is a terminal execution-v4 state:
            # never reinterpret it as a reusable readiness boundary.  The
            # authenticated marker and evidence are validated by the R3
            # binding path; this structural guard makes the policy explicit
            # before the generic non-empty-state rejection.
            from steel_module_production_r3_probe_lib import (
                RETIREMENT_MARKER_NAME,
            )

            if (
                retained.name.startswith(".r3-probe-")
                and not retained.is_symlink()
                and retained.is_dir()
                and (retained / RETIREMENT_MARKER_NAME).is_file()
            ):
                raise ValueError(
                    "closed successor-v4 requires a future successor; "
                    "recovery readiness cannot reuse this execution"
                )
    for name in ("intents", "attempts", "finalized"):
        root = execution.directory / name
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise ValueError(f"recovery readiness requires empty {name}/")
    relative, readiness_schema, failed_job_ids = _recovery_readiness_contract(
        execution
    )
    preflight = successor_r3_preflight_binding(
        execution,
        preflight_evidence_dir,
        allow_test_mode=False,
        require_current_snapshot=True,
    )
    authority = require_dict(execution.manifest, "recovery_authority")
    incident = require_dict(authority, "original_production_incident")
    failed_r3 = require_dict(authority, "failed_r3_preflight")
    value: dict[str, Any] = {
        "schema_version": readiness_schema,
        "status": "accepted-formal-phase2b-recovery-ready",
        "created_at_utc": utc_now(),
        "readiness_hash": "",
        "managed_submission_ready": True,
        "automatic_submission": False,
        "real_successor_submission_performed": False,
        "successor_intent_count": 0,
        "successor_slurm_job_ids": [],
        "predecessor_slurm_job_ids": [HISTORICAL_CLOSED_JOB_ID],
        "failed_compute_preflight_slurm_job_ids": failed_job_ids,
        "compute_preflight_slurm_job_ids": [preflight["job_id"]],
        "all_compute_preflight_slurm_job_ids": [
            *failed_job_ids,
            preflight["job_id"],
        ],
        "accepted_statistical_evidence": True,
        "successor_execution": {
            "directory": str(execution.directory),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "managed_execution_sha256": sha256_file(
                execution.directory / "managed_execution.json"
            ),
            "static_checksums_sha256": sha256_file(
                execution.directory / ROOT_STATIC_MANIFEST
            ),
            "control_lock": require_dict(
                require_dict(execution.manifest, "artifacts"), "control_lock"
            ),
        },
        "recovery_authority": {
            "authority_hash": authority.get("authority_hash"),
            "incident_id": incident.get("incident_id"),
            "incident_hash": incident.get("incident_hash"),
            "incident_checksum_manifest_sha256": incident.get(
                "checksum_manifest_sha256"
            ),
            "failed_r3_id": failed_r3.get("failure_id"),
            "failed_r3_hash": failed_r3.get("failure_hash"),
            "failed_r3_checksum_manifest_sha256": failed_r3.get(
                "checksum_manifest_sha256"
            ),
        },
        "compute_preflight": preflight,
        "r3_copy_safe_launcher": _r3_launcher_identity(execution.directory),
        "control_plane_source": require_dict(
            require_dict(execution.manifest, "sources"), "control_plane"
        ),
    }
    if execution.manifest.get("schema_version") == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        rejected = require_dict(authority, "rejected_r3_preflight")
        value["recovery_authority"].update(
            {
                "rejected_r3_id": rejected.get("rejection_id"),
                "rejected_r3_hash": rejected.get("rejection_hash"),
                "rejected_r3_checksum_manifest_sha256": rejected.get(
                    "checksum_manifest_sha256"
                ),
            }
        )
    value["readiness_hash"] = _semantic_hash(value, "readiness_hash")
    return value


def _verify_r4_git_gate(
    *,
    path: Path,
    live_root: Path,
    control_commit: str,
    source: dict[str, Any],
    relative: Path,
) -> None:
    """Require one clean, non-merge, lock-only commit after frozen control."""

    relative_text = relative.as_posix()

    def run_text(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=live_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    status = run_text("status", "--porcelain", "--untracked-files=all")
    parent = run_text("rev-list", "--parents", "-n", "1", "HEAD")
    count = run_text("rev-list", "--count", f"{control_commit}..HEAD")
    delta = run_text(
        "diff", "--name-status", "--no-renames", f"{control_commit}..HEAD"
    )
    tree_entry = run_text("ls-tree", "HEAD", "--", relative_text)
    frozen_tree = run_text("rev-parse", f"{control_commit}^{{tree}}")
    blob = subprocess.run(
        ["git", "show", f"HEAD:{relative_text}"],
        cwd=live_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    parent_parts = parent.stdout.strip().split()
    expected_delta = f"A\t{relative_text}"
    tree_parts = tree_entry.stdout.strip().split(None, 3)
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_mode & 0o777 != 0o644
        or status.returncode
        or status.stdout
        or parent.returncode
        or len(parent_parts) != 2
        or parent_parts[1] != control_commit
        or count.returncode
        or count.stdout.strip() != "1"
        or delta.returncode
        or delta.stdout.strip() != expected_delta
        or tree_entry.returncode
        or len(tree_parts) != 4
        or tree_parts[0] != "100644"
        or tree_parts[1] != "blob"
        or tree_parts[3] != relative_text
        or frozen_tree.returncode
        or frozen_tree.stdout.strip() != source.get("git_tree")
        or blob.returncode
        or blob.stdout != path.read_bytes()
    ):
        raise ValueError(
            "successor readiness must be one clean, non-merge R4 lock-only commit"
        )


def verify_successor_recovery_readiness(
    execution: ManagedExecution, *, repo_root: Path | None
) -> tuple[Path, dict[str, Any]]:
    """Validate the minimal frozen-worker contract of the future R4 lock."""

    if execution.manifest.get("test_mode") is not False:
        raise ValueError("test successor can never satisfy recovery readiness")
    relative, readiness_schema, failed_job_ids = _recovery_readiness_contract(
        execution
    )
    path = successor_recovery_readiness_path(execution, repo_root=repo_root)
    if path.is_symlink() or not path.is_file():
        raise ValueError("successor additive recovery readiness is missing")
    live_root = path
    for _ in relative.parts:
        live_root = live_root.parent
    control_commit = require_string(
        require_dict(require_dict(execution.manifest, "sources"), "control_plane"),
        "git_commit",
    )
    source = require_dict(require_dict(execution.manifest, "sources"), "control_plane")
    _verify_r4_git_gate(
        path=path,
        live_root=live_root,
        control_commit=control_commit,
        source=source,
        relative=relative,
    )
    value = load_json(path)
    _require_exact_keys(
        value,
        {
            "schema_version",
            "status",
            "created_at_utc",
            "readiness_hash",
            "managed_submission_ready",
            "automatic_submission",
            "real_successor_submission_performed",
            "successor_intent_count",
            "successor_slurm_job_ids",
            "predecessor_slurm_job_ids",
            "failed_compute_preflight_slurm_job_ids",
            "compute_preflight_slurm_job_ids",
            "all_compute_preflight_slurm_job_ids",
            "accepted_statistical_evidence",
            "successor_execution",
            "recovery_authority",
            "compute_preflight",
            "r3_copy_safe_launcher",
            "control_plane_source",
        },
        "successor recovery readiness",
    )
    if (
        value.get("schema_version") != readiness_schema
        or value.get("status") != "accepted-formal-phase2b-recovery-ready"
        or not require_string(value, "created_at_utc")
        or value.get("readiness_hash")
        != _semantic_hash(value, "readiness_hash")
        or value.get("managed_submission_ready") is not True
        or value.get("automatic_submission") is not False
        or value.get("real_successor_submission_performed") is not False
        or value.get("successor_intent_count") != 0
        or value.get("successor_slurm_job_ids") != []
        or value.get("predecessor_slurm_job_ids")
        != [HISTORICAL_CLOSED_JOB_ID]
        or value.get("failed_compute_preflight_slurm_job_ids") != failed_job_ids
        or value.get("accepted_statistical_evidence") is not True
    ):
        raise ValueError("successor recovery readiness policy is not accepted")
    bound_execution = require_dict(value, "successor_execution")
    expected_execution = {
        "directory": str(execution.directory),
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
        "managed_execution_sha256": sha256_file(
            execution.directory / "managed_execution.json"
        ),
        "static_checksums_sha256": sha256_file(
            execution.directory / ROOT_STATIC_MANIFEST
        ),
        "control_lock": require_dict(
            require_dict(execution.manifest, "artifacts"), "control_lock"
        ),
    }
    if bound_execution != expected_execution:
        raise ValueError("successor recovery readiness execution binding mismatch")
    authority = require_dict(execution.manifest, "recovery_authority")
    incident = require_dict(authority, "original_production_incident")
    failed_r3 = require_dict(authority, "failed_r3_preflight")
    expected_authority = {
        "authority_hash": authority.get("authority_hash"),
        "incident_id": incident.get("incident_id"),
        "incident_hash": incident.get("incident_hash"),
        "incident_checksum_manifest_sha256": incident.get(
            "checksum_manifest_sha256"
        ),
        "failed_r3_id": failed_r3.get("failure_id"),
        "failed_r3_hash": failed_r3.get("failure_hash"),
        "failed_r3_checksum_manifest_sha256": failed_r3.get(
            "checksum_manifest_sha256"
        ),
    }
    if execution.manifest.get("schema_version") == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        rejected = require_dict(authority, "rejected_r3_preflight")
        expected_authority.update(
            {
                "rejected_r3_id": rejected.get("rejection_id"),
                "rejected_r3_hash": rejected.get("rejection_hash"),
                "rejected_r3_checksum_manifest_sha256": rejected.get(
                    "checksum_manifest_sha256"
                ),
            }
        )
    if require_dict(value, "recovery_authority") != expected_authority:
        raise ValueError("successor recovery readiness incident binding mismatch")
    if require_dict(value, "control_plane_source") != source:
        raise ValueError("successor recovery readiness frozen-source mismatch")
    if require_dict(value, "r3_copy_safe_launcher") != _r3_launcher_identity(
        execution.directory
    ):
        raise ValueError("successor recovery readiness R3 launcher mismatch")
    recorded_preflight = require_dict(value, "compute_preflight")
    evidence_path = Path(require_string(recorded_preflight, "directory"))
    intent_root = execution.directory / "intents"
    if intent_root.is_symlink() or not intent_root.is_dir():
        raise ValueError("successor intent root is unsafe")
    has_intent_state = any(intent_root.iterdir())
    current_preflight = successor_r3_preflight_binding(
        execution,
        evidence_path,
        allow_test_mode=False,
        require_current_snapshot=not has_intent_state,
    )
    if has_intent_state:
        _validate_successor_mutable_state_after_intent(execution)
    if (
        recorded_preflight != current_preflight
        or value.get("compute_preflight_slurm_job_ids")
        != [current_preflight["job_id"]]
        or value.get("all_compute_preflight_slurm_job_ids")
        != [*failed_job_ids, current_preflight["job_id"]]
    ):
        raise ValueError("successor R3 compute-preflight binding mismatch")
    return path.resolve(), value


def _validate_authority_against_live_incident(
    execution: ManagedExecution,
    *,
    repo_root: Path | None,
    verify_recorded_recovery_git: bool,
    verify_live_predecessor: bool,
) -> None:
    authority = require_dict(execution.manifest, "recovery_authority")
    _require_exact_keys(
        authority,
        {
            "schema_version",
            "predecessor_execution",
            "predecessor_readiness",
            "predecessor_attempt",
            "incident",
            "zero_consumption",
            "retry_equivalence",
            "authority_hash",
        },
        "successor recovery authority",
    )
    if (
        authority.get("schema_version") != SUCCESSOR_AUTHORITY_SCHEMA_VERSION
        or authority.get("authority_hash")
        != _semantic_hash(authority, "authority_hash")
    ):
        raise ValueError("successor recovery-authority identity mismatch")
    incident_record = require_dict(authority, "incident")
    _require_exact_keys(
        incident_record,
        {
            "directory",
            "schema_version",
            "incident_id",
            "incident_hash",
            "incident_json_sha256",
            "checksum_manifest_sha256",
            "recursive_record_count",
        },
        "successor incident authority",
    )
    incident_path = Path(require_string(incident_record, "directory"))
    if incident_path.is_symlink() or not incident_path.is_dir():
        raise ValueError("successor incident bundle is missing or unsafe")
    incident_path = incident_path.resolve()
    incident = validate_incident_bundle(incident_path)
    _validate_zero_consumption_incident(
        incident, formal=execution.manifest.get("test_mode") is False
    )
    if (
        incident_record.get("schema_version") != incident.get("schema_version")
        or incident_record.get("incident_id") != incident.get("incident_id")
        or incident_record.get("incident_hash") != incident.get("incident_hash")
        or incident_record.get("incident_json_sha256")
        != sha256_file(incident_path / "incident.json")
        or incident_record.get("checksum_manifest_sha256")
        != sha256_file(incident_path / "SHA256SUMS")
        or incident_record.get("recursive_record_count")
        != len(verify_recursive_checksums(incident_path))
    ):
        raise ValueError("successor incident checksum authority mismatch")
    predecessor_record = require_dict(authority, "predecessor_execution")
    _require_exact_keys(
        predecessor_record,
        {
            "directory",
            "schema_version",
            "execution_id",
            "execution_hash",
            "managed_execution_sha256",
            "static_checksums_sha256",
        },
        "successor predecessor execution",
    )
    incident_execution = require_dict(incident, "execution")
    if predecessor_record.get("schema_version") not in {
        "steel-module-production-managed-execution-v1",
        "steel-module-production-managed-execution-v2",
    }:
        raise ValueError("successor predecessor schema is invalid")
    for key in (
        "directory",
        "execution_id",
        "execution_hash",
        "managed_execution_sha256",
        "static_checksums_sha256",
    ):
        if predecessor_record.get(key) != incident_execution.get(key):
            raise ValueError(f"successor predecessor {key} differs from incident")
    attempt_record = require_dict(authority, "predecessor_attempt")
    _require_exact_keys(
        attempt_record,
        {
            "attempt_id",
            "intent_sha256",
            "job_id",
            "job_name",
            "event_count",
            "event_chain_sha256",
            "terminal_event_sha256",
            "accounting_manifest_sha256",
        },
        "successor predecessor attempt",
    )
    incident_attempt = require_dict(incident, "attempt")
    if attempt_record != {
        key: incident_attempt.get(key)
        for key in attempt_record
    }:
        raise ValueError("successor predecessor attempt differs from incident")
    readiness_record = require_dict(authority, "predecessor_readiness")
    _require_exact_keys(
        readiness_record,
        {
            "path",
            "sha256",
            "schema_version",
            "status",
            "implementation_commit",
            "readiness_commit",
            "readiness_commit_parent",
            "commit_blob_sha256",
        },
        "successor predecessor readiness",
    )
    incident_readiness = require_dict(incident, "historical_readiness")
    if readiness_record != {
        key: incident_readiness.get(key)
        for key in readiness_record
    }:
        raise ValueError("successor predecessor readiness differs from incident")
    zero = require_dict(authority, "zero_consumption")
    _require_exact_keys(
        zero,
        {
            "classification",
            "root_cause",
            "accepted_incident_evidence",
            "events_consumed",
            "production_seed_count",
            "production_seeds_consumed",
            "loader_completed",
            "apptainer_invoked",
            "geant4_invoked",
            "task_outputs_created",
            "root_outputs_created",
            "seed_reuse_authorized",
            "reuse_scope",
        },
        "successor zero-consumption authority",
    )
    disposition = require_dict(incident, "simulation_disposition")
    if (
        zero.get("classification") != incident.get("classification")
        or zero.get("root_cause") != incident.get("root_cause")
        or zero.get("accepted_incident_evidence")
        != incident.get("accepted_incident_evidence")
        or zero.get("events_consumed") != disposition.get("events_consumed")
        or zero.get("production_seeds_consumed")
        != disposition.get("production_seeds_consumed")
        or zero.get("production_seed_count")
        != disposition.get("production_seed_count")
        or zero.get("seed_reuse_authorized") is not True
        or zero.get("reuse_scope") != "exact-32-task-phase2a-mapping-only"
        or zero.get("loader_completed") != disposition.get("loader_completed")
        or zero.get("apptainer_invoked") != disposition.get("apptainer_invoked")
        or zero.get("geant4_invoked") != disposition.get("geant4_invoked")
        or zero.get("task_outputs_created")
        != disposition.get("task_outputs_created")
        or zero.get("root_outputs_created")
        != disposition.get("root_outputs_created")
    ):
        raise ValueError("successor zero-consumption authority mismatch")
    retry = require_dict(authority, "retry_equivalence")
    _require_exact_keys(
        retry,
        {
            "ordered_task_hash",
            "task_set_hash",
            "task_seed_mapping_hash",
            "seed_set_hash",
            "ordered_seed_pairs_sha256",
            "ordered_production_seeds",
            "task_count",
            "event_count",
            "seed_count",
            "tasks_tsv_sha256",
            "scan_args_sha256",
            "phase2a_tasks_sha256",
            "phase2a_scan_args_sha256",
            "simulation_source",
            "runtime",
            "all_equal",
        },
        "successor retry equivalence",
    )
    current = _task_identity(execution.tasks)
    for key, value in current.items():
        if retry.get(key) != value:
            raise ValueError(f"successor retry task identity mismatch: {key}")
    if (
        retry.get("tasks_tsv_sha256")
        != sha256_file(execution.directory / "tasks.tsv")
        or retry.get("scan_args_sha256")
        != sha256_file(execution.directory / "scan_args.txt")
        or retry.get("phase2a_tasks_sha256")
        != sha256_file(execution.managed_child.directory / "tasks.tsv")
        or retry.get("phase2a_scan_args_sha256")
        != sha256_file(execution.managed_child.directory / "scan_args.txt")
        or retry.get("simulation_source")
        != require_dict(execution.manifest, "sources")["simulation"]
        or retry.get("runtime") != execution.manifest.get("runtime")
        or retry.get("all_equal") is not True
        or incident.get("task_seed_mapping_hash")
        != current["task_seed_mapping_hash"]
        # The recovery successor intentionally freezes a new control-plane
        # source.  Only the simulation source belongs to retry equivalence;
        # requiring the complete dual-source record to equal the predecessor
        # would reject every real successor even though its physics inputs are
        # unchanged.  The predecessor control source is independently bound by
        # the checksum-valid incident, while the successor control source is
        # validated as its own frozen archive and by the R4 readiness gate.
        or require_dict(incident, "source_identities").get("simulation")
        != require_dict(execution.manifest, "sources").get("simulation")
        or incident.get("runtime_identity") != execution.manifest.get("runtime")
    ):
        raise ValueError("successor retry-equivalence evidence mismatch")
    formal = execution.manifest.get("test_mode") is False
    if formal:
        expected_incident = canonical_incident_directory(execution.managed_child)
        if (
            incident_path != expected_incident
            or incident.get("incident_id") != FORMAL_INCIDENT_ID
            or incident.get("incident_hash") != FORMAL_INCIDENT_HASH
            or predecessor_record.get("directory")
            != str(execution.managed_child.directory.parent / FORMAL_EXECUTION_NAME)
            or predecessor_record.get("execution_id")
            != HISTORICAL_CLOSED_EXECUTION_ID
            or predecessor_record.get("execution_hash")
            != HISTORICAL_CLOSED_EXECUTION_HASH
            or predecessor_record.get("schema_version")
            != "steel-module-production-managed-execution-v1"
        ):
            raise ValueError("formal successor recovery lineage is not exact")
        if verify_recorded_recovery_git:
            if repo_root is None:
                raise ValueError("recorded recovery Git validation needs repo_root")
            _validate_recorded_recovery_control_plane(repo_root, incident)
    if verify_live_predecessor:
        if formal and repo_root is None:
            raise ValueError("live predecessor validation needs repo_root")
        predecessor_dir = Path(require_string(predecessor_record, "directory"))
        predecessor = _load_predecessor(
            repo_root=repo_root or Path.cwd(),
            directory=predecessor_dir,
            test_mode=not formal,
        )
        validate_incident_bundle(incident_path, execution=predecessor)
        if (
            incident.get("source_identities")
            != predecessor.manifest.get("sources")
            or incident.get("runtime_identity")
            != predecessor.manifest.get("runtime")
            or predecessor.tasks != execution.tasks
            or predecessor.scan_args != execution.scan_args
        ):
            raise ValueError("live predecessor retry identity changed")


def _load_v2_successor_execution(
    execution_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = False,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
    allow_staging: bool = False,
    verify_live_predecessor: bool = False,
) -> ManagedExecution:
    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("successor execution must not be a symlink")
    directory = requested.resolve()
    verify_execution_static_checksums(directory)
    _validate_successor_tree_modes(directory)
    actual_root = {path.name for path in directory.iterdir()}
    if actual_root != SUCCESSOR_ROOT_ENTRIES or any(
        path.is_symlink() for path in directory.iterdir()
    ):
        raise ValueError("successor execution root entry set is unsafe")
    if (directory / "campaign.json").exists():
        raise ValueError("successor execution must never contain campaign.json")
    manifest = load_json(directory / "managed_execution.json")
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "object_kind",
            "execution_generation",
            "created_at_utc",
            "test_mode",
            "accepted_statistical_evidence",
            "execution_id",
            "execution_hash",
            "phase",
            "managed_child",
            "program",
            "phase2a_lock",
            "readiness_gate",
            "shape",
            "runtime",
            "sources",
            "sealed_pilot",
            "artifacts",
            "submission_policy",
            "recovery_authority",
        },
        "successor execution manifest",
    )
    if (
        manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2
        or manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND
        or manifest.get("execution_generation") != SUCCESSOR_EXECUTION_GENERATION_V2
        or manifest.get("phase") != "Phase-2B-R2"
        or manifest.get("execution_hash")
        != _semantic_hash(manifest, "execution_hash")
    ):
        raise ValueError("unsupported or invalid successor execution manifest")
    require_string(manifest, "created_at_utc")
    test_mode = manifest.get("test_mode")
    accepted = manifest.get("accepted_statistical_evidence")
    if (
        not isinstance(test_mode, bool)
        or not isinstance(accepted, bool)
        or accepted == test_mode
        or (test_mode and not allow_test_mode)
    ):
        raise ValueError("successor test/evidence flags are invalid")
    if require_readiness and test_mode:
        raise ValueError("test successor can never satisfy recovery readiness")
    if not test_mode and repo_root is None:
        raise ValueError("formal successor validation requires repo_root")
    root = repo_root.expanduser().resolve() if repo_root is not None else None
    sources = require_dict(manifest, "sources")
    _require_exact_keys(
        sources,
        {"schema_version", "simulation", "control_plane"},
        "successor source archives",
    )
    if sources.get("schema_version") != "steel-module-production-dual-source-v1":
        raise ValueError("successor source-archive schema mismatch")
    frozen_control_mode = not test_mode and not verify_phase2a_control_plane
    if frozen_control_mode:
        control = require_dict(sources, "control_plane")
        expected_control = (directory / require_string(control, "path")).resolve()
        if root != expected_control:
            raise ValueError("frozen successor loader must run from control archive")
        _verify_source_record(directory, control)
    child_record = require_dict(manifest, "managed_child")
    _require_exact_keys(
        child_record,
        {
            "directory",
            "campaign_id",
            "plan_hash",
            "binding_hash",
            "child_id",
            "child_plan_hash",
            "task_set_hash",
            "parent_task_plan_hash",
            "task_seed_mapping_hash",
            "seed_set_hash",
            "tasks_sha256",
            "scan_args_sha256",
        },
        "successor managed child",
    )
    child_dir = Path(require_string(child_record, "directory"))
    managed = load_managed_production_child(
        child_dir,
        repo_root=root if not test_mode else None,
        verify_runtime_artifacts=verify_runtime and not test_mode,
        verify_control_plane=not test_mode and verify_phase2a_control_plane,
        allow_test_mode=test_mode,
        _allow_formal_lock_relocation=frozen_control_mode,
    )
    if (
        child_dir != managed.directory
        or child_record.get("child_id") != INITIAL_CHILD_ID
        or managed.plan.campaign_id != child_record.get("campaign_id")
        or managed.plan.plan_hash != child_record.get("plan_hash")
        or managed.binding.get("binding_hash") != child_record.get("binding_hash")
    ):
        raise ValueError("successor managed-child identity mismatch")
    binding_child = require_dict(managed.binding, "child")
    for key in (
        "child_plan_hash",
        "task_set_hash",
        "parent_task_plan_hash",
        "task_seed_mapping_hash",
        "seed_set_hash",
    ):
        if child_record.get(key) != binding_child.get(key):
            raise ValueError(f"successor managed-child {key} mismatch")
    expected_program = {
        key: managed.binding["program"][key]
        for key in (
            "program_id",
            "program_hash",
            "parent_plan_hash",
            "authorization_graph_hash",
        )
    }
    if require_dict(manifest, "program") != expected_program:
        raise ValueError("successor production-program identity mismatch")
    environment = managed.plan.environment
    expected_runtime = {
        "environment_identity": environment["identity_hash"],
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
    }
    if require_dict(manifest, "runtime") != expected_runtime:
        raise ValueError("successor runtime identity differs from Phase-2A")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    scan_args = _read_scan_args(directory / "scan_args.txt")
    if tasks != managed.plan.tasks or scan_args != managed.plan.scan_args:
        raise ValueError("successor task/scan plan differs from Phase-2A")
    if (
        child_record.get("tasks_sha256")
        != sha256_file(managed.directory / "tasks.tsv")
        or child_record.get("scan_args_sha256")
        != sha256_file(managed.directory / "scan_args.txt")
    ):
        raise ValueError("successor managed-child task/scan bytes changed")
    artifacts = require_dict(manifest, "artifacts")
    _require_exact_keys(
        artifacts,
        {"tasks", "scan_args", "source_archives", "control_lock"},
        "successor artifacts",
    )
    for key, filename in (
        ("tasks", "tasks.tsv"),
        ("scan_args", "scan_args.txt"),
        ("source_archives", "source_archives.json"),
    ):
        record = require_dict(artifacts, key)
        if (
            record.get("path") != filename
            or record.get("sha256") != sha256_file(directory / filename)
        ):
            raise ValueError(f"successor artifact binding mismatch: {filename}")
    shape = require_dict(manifest, "shape")
    seeds = _seed_values(tasks)
    if (
        len(tasks) != 32
        or sum(task.events for task in tasks) != 8000
        or len(seeds) != 64
        or len(set(seeds)) != 64
        or shape.get("task_count") != 32
        or shape.get("event_count") != 8000
        or shape.get("seed_count") != 64
    ):
        raise ValueError("successor shape is not exact BC-S1")
    expected_shape = {
        "task_count": 32,
        "event_count": 8000,
        "events_per_task": 250,
        "seed_count": 64,
        "tile_thickness_mm": [4, 24],
        "sipm_layout": "back-center",
        "absorber_transverse_mm": 500,
        "seed_block_range_inclusive": [0, 15],
    }
    if shape != expected_shape:
        raise ValueError("successor shape metadata is not exact BC-S1")
    if load_json(directory / "source_archives.json") != sources:
        raise ValueError("successor source archive manifest mismatch")
    _verify_source_record(directory, require_dict(sources, "simulation"))
    _verify_source_record(directory, require_dict(sources, "control_plane"))
    validate_execution_control_lock(directory, manifest)
    recorded_pilot = require_dict(manifest, "sealed_pilot")
    current_pilot = _sealed_pilot_identity(
        Path(require_string(recorded_pilot, "directory")), allow_fixture=test_mode
    )
    for key, value in current_pilot.items():
        if recorded_pilot.get(key) != value:
            raise ValueError(f"successor sealed-pilot {key} mismatch")
    execution = ManagedExecution(directory, managed, manifest, tasks, scan_args)
    recorded_phase2a = require_dict(manifest, "phase2a_lock")
    _require_exact_keys(
        recorded_phase2a,
        {"path", "sha256", "schema_version"},
        "successor Phase-2A lock",
    )
    if recorded_phase2a.get("schema_version") != PHASE2A_LOCK_SCHEMA_VERSION:
        raise ValueError("successor Phase-2A lock schema mismatch")
    if not test_mode:
        assert root is not None
        phase2a_path, phase2a = load_phase2a_lock(root)
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
        if (
            recorded_phase2a.get("sha256") != sha256_file(phase2a_path)
            or (
                not frozen_control_mode
                and recorded_phase2a.get("path") != str(phase2a_path)
            )
        ):
            raise ValueError("successor Phase-2A lock identity mismatch")
        expected = canonical_v2_successor_directory(managed)
        staging_prefix = f".{expected.name}.tmp-"
        if directory != expected and not (
            allow_staging
            and directory.parent == expected.parent
            and directory.name.startswith(staging_prefix)
        ):
            raise ValueError(f"formal successor path must be canonical: {expected}")
    _validate_authority_against_live_incident(
        execution,
        repo_root=root,
        verify_recorded_recovery_git=(
            not test_mode and verify_phase2a_control_plane
        ),
        verify_live_predecessor=verify_live_predecessor,
    )
    authority = require_dict(manifest, "recovery_authority")
    expected_execution_id = _v2_successor_execution_id(
        require_string(child_record, "binding_hash"),
        require_string(require_dict(sources, "control_plane"), "git_commit"),
        require_sha256(require_dict(authority, "incident"), "incident_hash"),
        require_sha256(
            require_dict(authority, "predecessor_execution"), "execution_hash"
        ),
    )
    if manifest.get("execution_id") != expected_execution_id:
        raise ValueError("successor execution ID does not match its lineage")
    policy = require_dict(manifest, "submission_policy")
    gate = require_dict(manifest, "readiness_gate")
    expected_policy = {
        "account": "PAS2524",
        "two_step_required": True,
        "initially_held": True,
        "no_requeue": True,
        "export_mode": "NONE",
        "formal_child": INITIAL_CHILD_ID,
        "formal_initial_mode": "predecessor-retry",
        "predecessor_retry_task_count": 32,
        "predecessor_retry_event_count": 8000,
        "r2_submittable": False,
    }
    expected_gate = {
        "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE_V2.as_posix(),
        "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V2,
        "required_for_scheduler_contact": True,
        "compute_node_container_preflight_required": True,
        "automatic_submission": False,
        "r2_submission_ready": False,
    }
    if policy != expected_policy or gate != expected_gate:
        raise ValueError("successor R2 readiness/submission boundary mismatch")
    if require_readiness:
        verify_successor_recovery_readiness(execution, repo_root=root)
    return execution


def _validate_v3_authority(
    execution: ManagedExecution,
    *,
    repo_root: Path | None,
    verify_phase2a_control_plane: bool,
    verify_live_predecessor: bool,
) -> None:
    authority = require_dict(execution.manifest, "recovery_authority")
    _require_exact_keys(
        authority,
        {
            "schema_version",
            "predecessor_execution",
            "predecessor_recovery_authority",
            "original_production_incident",
            "failed_r3_preflight",
            "zero_consumption",
            "retry_equivalence",
            "authority_hash",
        },
        "successor-v3 recovery authority",
    )
    if (
        authority.get("schema_version") != SUCCESSOR_V3_AUTHORITY_SCHEMA_VERSION
        or authority.get("authority_hash")
        != _semantic_hash(authority, "authority_hash")
    ):
        raise ValueError("successor-v3 recovery authority identity mismatch")
    predecessor_record = require_dict(authority, "predecessor_execution")
    _require_exact_keys(
        predecessor_record,
        {
            "directory",
            "schema_version",
            "execution_generation",
            "execution_id",
            "execution_hash",
            "managed_execution_sha256",
            "static_checksums_sha256",
            "recovery_authority_hash",
        },
        "successor-v3 predecessor execution",
    )
    formal = execution.manifest.get("test_mode") is False
    predecessor_path = Path(require_string(predecessor_record, "directory"))
    if predecessor_path.is_symlink() or not predecessor_path.is_dir():
        raise ValueError("successor-v3 predecessor is missing or unsafe")
    predecessor_repo_root = repo_root
    if formal and not verify_phase2a_control_plane:
        predecessor_repo_root = predecessor_path / "sources/control"
    predecessor = _load_v2_successor_execution(
        predecessor_path,
        repo_root=predecessor_repo_root if formal else None,
        allow_test_mode=not formal,
        require_readiness=False,
        verify_runtime=formal,
        verify_phase2a_control_plane=verify_phase2a_control_plane,
        verify_live_predecessor=verify_live_predecessor,
    )
    predecessor_authority = require_dict(
        predecessor.manifest, "recovery_authority"
    )
    if require_dict(authority, "predecessor_recovery_authority") != (
        predecessor_authority
    ):
        raise ValueError("successor-v3 predecessor recovery authority changed")
    expected_predecessor = {
        "directory": str(predecessor.directory),
        "schema_version": predecessor.manifest.get("schema_version"),
        "execution_generation": predecessor.manifest.get("execution_generation"),
        "execution_id": predecessor.execution_id,
        "execution_hash": predecessor.execution_hash,
        "managed_execution_sha256": sha256_file(
            predecessor.directory / "managed_execution.json"
        ),
        "static_checksums_sha256": sha256_file(
            predecessor.directory / ROOT_STATIC_MANIFEST
        ),
        "recovery_authority_hash": predecessor_authority.get("authority_hash"),
    }
    if predecessor_record != expected_predecessor:
        raise ValueError("successor-v3 predecessor binding mismatch")
    if formal and (
        predecessor.directory
        != execution.managed_child.directory.parent
        / FORMAL_SUCCESSOR_EXECUTION_NAME_V2
        or predecessor.execution_id != FORMAL_SUCCESSOR_V2_EXECUTION_ID
        or predecessor.execution_hash != FORMAL_SUCCESSOR_V2_EXECUTION_HASH
    ):
        raise ValueError("formal successor-v3 predecessor identity is not exact")
    for name in ("intents", "attempts", "finalized"):
        root = predecessor.directory / name
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise ValueError(f"closed successor-v2 contains mutable {name} state")
    original = require_dict(authority, "original_production_incident")
    if original != require_dict(predecessor_authority, "incident"):
        raise ValueError("successor-v3 original production incident changed")
    zero = require_dict(authority, "zero_consumption")
    if zero != require_dict(predecessor_authority, "zero_consumption"):
        raise ValueError("successor-v3 original zero-consumption authority changed")
    failure_record = require_dict(authority, "failed_r3_preflight")
    _require_exact_keys(
        failure_record,
        {
            "directory",
            "schema_version",
            "failure_id",
            "failure_hash",
            "failure_json_sha256",
            "checksum_manifest_sha256",
            "recursive_record_count",
            "execution",
            "scheduler",
            "runtime_boundary",
            "consumption",
        },
        "successor-v3 failed-R3 binding",
    )
    failure_path = Path(require_string(failure_record, "directory"))
    if failure_path.is_symlink() or not failure_path.is_dir():
        raise ValueError("successor-v3 failed-R3 bundle is missing or unsafe")
    failure = _validated_r3_failure(
        failure_path.resolve(),
        predecessor=predecessor,
        test_mode=not formal,
        require_current_execution=True,
    )
    if failure_record != _failure_binding(failure_path.resolve(), failure):
        raise ValueError("successor-v3 failed-R3 bundle binding mismatch")
    failed_execution = require_dict(failure, "execution")
    if (
        failed_execution.get("directory") != str(predecessor.directory)
        or failed_execution.get("execution_id") != predecessor.execution_id
        or failed_execution.get("execution_hash") != predecessor.execution_hash
        or failed_execution.get("managed_execution_sha256")
        != expected_predecessor["managed_execution_sha256"]
        or failed_execution.get("static_manifest_sha256")
        != expected_predecessor["static_checksums_sha256"]
    ):
        raise ValueError("successor-v3 failed-R3 execution identity mismatch")
    if (
        require_dict(failure, "consumption").get("events_consumed") != 0
        or require_dict(failure, "consumption").get("production_seeds_consumed")
        != 0
        or require_dict(failure, "runtime_boundary").get("apptainer_invoked")
        is not False
        or require_dict(failure, "runtime_boundary").get("geant4_invoked")
        is not False
    ):
        raise ValueError("successor-v3 failed-R3 evidence is not zero-consumption")
    retry = require_dict(authority, "retry_equivalence")
    expected_retry = {
        **_task_identity(execution.tasks),
        "tasks_tsv_sha256": sha256_file(execution.directory / "tasks.tsv"),
        "scan_args_sha256": sha256_file(execution.directory / "scan_args.txt"),
        "phase2a_tasks_sha256": sha256_file(
            execution.managed_child.directory / "tasks.tsv"
        ),
        "phase2a_scan_args_sha256": sha256_file(
            execution.managed_child.directory / "scan_args.txt"
        ),
        "simulation_source": require_dict(execution.manifest, "sources")[
            "simulation"
        ],
        "runtime": execution.manifest["runtime"],
        "all_equal": True,
    }
    if retry != expected_retry:
        raise ValueError("successor-v3 retry-equivalence evidence mismatch")
    if (
        execution.tasks != predecessor.tasks
        or execution.scan_args != predecessor.scan_args
        or require_dict(execution.manifest, "sources")["simulation"]
        != require_dict(predecessor.manifest, "sources")["simulation"]
        or execution.manifest.get("runtime") != predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor-v3 changed frozen physics/runtime inputs")


def _load_v3_successor_execution(
    execution_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = False,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
    allow_staging: bool = False,
    verify_live_predecessor: bool = False,
) -> ManagedExecution:
    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("successor-v3 execution must not be a symlink")
    directory = requested.resolve()
    verify_execution_static_checksums(directory)
    _validate_successor_tree_modes(directory)
    actual_root = {path.name for path in directory.iterdir()}
    if actual_root != SUCCESSOR_ROOT_ENTRIES or any(
        path.is_symlink() for path in directory.iterdir()
    ):
        raise ValueError("successor-v3 execution root entry set is unsafe")
    manifest = load_json(directory / "managed_execution.json")
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "object_kind",
            "execution_generation",
            "created_at_utc",
            "test_mode",
            "accepted_statistical_evidence",
            "execution_id",
            "execution_hash",
            "phase",
            "managed_child",
            "program",
            "phase2a_lock",
            "readiness_gate",
            "shape",
            "runtime",
            "sources",
            "sealed_pilot",
            "artifacts",
            "submission_policy",
            "recovery_authority",
        },
        "successor-v3 execution manifest",
    )
    if (
        manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3
        or manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND
        or manifest.get("execution_generation") != SUCCESSOR_EXECUTION_GENERATION_V3
        or manifest.get("phase") != "Phase-2B-R2-v3"
        or manifest.get("execution_hash")
        != _semantic_hash(manifest, "execution_hash")
    ):
        raise ValueError("unsupported or invalid successor-v3 manifest")
    require_string(manifest, "created_at_utc")
    test_mode = manifest.get("test_mode")
    accepted = manifest.get("accepted_statistical_evidence")
    if (
        not isinstance(test_mode, bool)
        or not isinstance(accepted, bool)
        or accepted == test_mode
        or (test_mode and not allow_test_mode)
    ):
        raise ValueError("successor-v3 test/evidence flags are invalid")
    if not test_mode and repo_root is None:
        raise ValueError("formal successor-v3 validation requires repo_root")
    root = repo_root.expanduser().resolve() if repo_root is not None else None
    sources = require_dict(manifest, "sources")
    _require_exact_keys(
        sources,
        {"schema_version", "simulation", "control_plane"},
        "successor-v3 source archives",
    )
    if sources.get("schema_version") != "steel-module-production-dual-source-v1":
        raise ValueError("successor-v3 source-archive schema mismatch")
    frozen_control_mode = not test_mode and not verify_phase2a_control_plane
    if frozen_control_mode:
        control = require_dict(sources, "control_plane")
        expected_control = (directory / require_string(control, "path")).resolve()
        if root != expected_control:
            raise ValueError("frozen successor-v3 loader must use control archive")
        _verify_source_record(directory, control)
    child_record = require_dict(manifest, "managed_child")
    child_dir = Path(require_string(child_record, "directory"))
    managed = load_managed_production_child(
        child_dir,
        repo_root=root if not test_mode else None,
        verify_runtime_artifacts=verify_runtime and not test_mode,
        verify_control_plane=not test_mode and verify_phase2a_control_plane,
        allow_test_mode=test_mode,
        _allow_formal_lock_relocation=frozen_control_mode,
    )
    expected_child_keys = {
        "directory", "campaign_id", "plan_hash", "binding_hash", "child_id",
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash", "tasks_sha256",
        "scan_args_sha256",
    }
    _require_exact_keys(child_record, expected_child_keys, "successor-v3 child")
    if (
        child_dir != managed.directory
        or child_record.get("child_id") != INITIAL_CHILD_ID
        or managed.plan.campaign_id != child_record.get("campaign_id")
        or managed.plan.plan_hash != child_record.get("plan_hash")
        or managed.binding.get("binding_hash") != child_record.get("binding_hash")
    ):
        raise ValueError("successor-v3 managed-child identity mismatch")
    binding_child = require_dict(managed.binding, "child")
    for key in (
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash",
    ):
        if child_record.get(key) != binding_child.get(key):
            raise ValueError(f"successor-v3 managed-child {key} mismatch")
    expected_program = {
        key: managed.binding["program"][key]
        for key in (
            "program_id", "program_hash", "parent_plan_hash",
            "authorization_graph_hash",
        )
    }
    if require_dict(manifest, "program") != expected_program:
        raise ValueError("successor-v3 production-program identity mismatch")
    environment = managed.plan.environment
    expected_runtime = {
        "environment_identity": environment["identity_hash"],
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
    }
    if require_dict(manifest, "runtime") != expected_runtime:
        raise ValueError("successor-v3 runtime identity differs from Phase-2A")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    scan_args = _read_scan_args(directory / "scan_args.txt")
    if tasks != managed.plan.tasks or scan_args != managed.plan.scan_args:
        raise ValueError("successor-v3 task/scan plan differs from Phase-2A")
    if (
        child_record.get("tasks_sha256")
        != sha256_file(managed.directory / "tasks.tsv")
        or child_record.get("scan_args_sha256")
        != sha256_file(managed.directory / "scan_args.txt")
    ):
        raise ValueError("successor-v3 child task/scan bytes changed")
    artifacts = require_dict(manifest, "artifacts")
    _require_exact_keys(
        artifacts,
        {"tasks", "scan_args", "source_archives", "control_lock"},
        "successor-v3 artifacts",
    )
    for key, filename in (
        ("tasks", "tasks.tsv"),
        ("scan_args", "scan_args.txt"),
        ("source_archives", "source_archives.json"),
    ):
        record = require_dict(artifacts, key)
        if (
            record.get("path") != filename
            or record.get("sha256") != sha256_file(directory / filename)
        ):
            raise ValueError(f"successor-v3 artifact mismatch: {filename}")
    shape = require_dict(manifest, "shape")
    expected_shape = {
        "task_count": 32,
        "event_count": 8000,
        "events_per_task": 250,
        "seed_count": 64,
        "tile_thickness_mm": [4, 24],
        "sipm_layout": "back-center",
        "absorber_transverse_mm": 500,
        "seed_block_range_inclusive": [0, 15],
    }
    seeds = _seed_values(tasks)
    if shape != expected_shape or len(seeds) != 64 or len(set(seeds)) != 64:
        raise ValueError("successor-v3 shape is not exact BC-S1")
    if load_json(directory / "source_archives.json") != sources:
        raise ValueError("successor-v3 source manifest mismatch")
    _verify_source_record(directory, require_dict(sources, "simulation"))
    _verify_source_record(directory, require_dict(sources, "control_plane"))
    _r3_launcher_identity(directory)
    lock_record = require_dict(artifacts, "control_lock")
    with _opened_portable_control_lock(
        directory / ".control.lock", lock_record, operation=fcntl.LOCK_SH
    ):
        pass
    recorded_pilot = require_dict(manifest, "sealed_pilot")
    current_pilot = _sealed_pilot_identity(
        Path(require_string(recorded_pilot, "directory")), allow_fixture=test_mode
    )
    for key, value in current_pilot.items():
        if recorded_pilot.get(key) != value:
            raise ValueError(f"successor-v3 sealed-pilot {key} mismatch")
    execution = ManagedExecution(directory, managed, manifest, tasks, scan_args)
    recorded_phase2a = require_dict(manifest, "phase2a_lock")
    _require_exact_keys(
        recorded_phase2a,
        {"path", "sha256", "schema_version"},
        "successor-v3 Phase-2A lock",
    )
    if recorded_phase2a.get("schema_version") != PHASE2A_LOCK_SCHEMA_VERSION:
        raise ValueError("successor-v3 Phase-2A lock schema mismatch")
    if not test_mode:
        assert root is not None
        phase2a_path, phase2a = load_phase2a_lock(root)
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
        if (
            recorded_phase2a.get("sha256") != sha256_file(phase2a_path)
            or (
                not frozen_control_mode
                and recorded_phase2a.get("path") != str(phase2a_path)
            )
        ):
            raise ValueError("successor-v3 Phase-2A identity mismatch")
        expected = canonical_v3_successor_directory(managed)
        staging_prefix = f".{expected.name}.tmp-"
        if directory != expected and not (
            allow_staging
            and directory.parent == expected.parent
            and directory.name.startswith(staging_prefix)
        ):
            raise ValueError(f"formal successor-v3 path must be canonical: {expected}")
    _validate_v3_authority(
        execution,
        repo_root=root,
        verify_phase2a_control_plane=verify_phase2a_control_plane,
        verify_live_predecessor=verify_live_predecessor,
    )
    authority = require_dict(manifest, "recovery_authority")
    expected_execution_id = _v3_successor_execution_id(
        require_string(child_record, "binding_hash"),
        require_string(require_dict(sources, "control_plane"), "git_commit"),
        require_sha256(
            require_dict(authority, "original_production_incident"),
            "incident_hash",
        ),
        require_sha256(
            require_dict(authority, "predecessor_execution"), "execution_hash"
        ),
        require_sha256(
            require_dict(authority, "failed_r3_preflight"), "failure_hash"
        ),
    )
    if manifest.get("execution_id") != expected_execution_id:
        raise ValueError("successor-v3 execution ID does not match lineage")
    expected_policy = {
        "account": "PAS2524", "two_step_required": True,
        "initially_held": True, "no_requeue": True, "export_mode": "NONE",
        "formal_child": INITIAL_CHILD_ID,
        "formal_initial_mode": "predecessor-retry",
        "predecessor_retry_task_count": 32,
        "predecessor_retry_event_count": 8000,
        "r2_submittable": False,
    }
    expected_gate = {
        "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE_V3.as_posix(),
        "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V3,
        "required_for_scheduler_contact": True,
        "compute_node_container_preflight_required": True,
        "automatic_submission": False,
        "r2_submission_ready": False,
    }
    if (
        require_dict(manifest, "submission_policy") != expected_policy
        or require_dict(manifest, "readiness_gate") != expected_gate
    ):
        raise ValueError("successor-v3 readiness/submission boundary mismatch")
    if require_readiness:
        verify_successor_recovery_readiness(execution, repo_root=root)
    return execution


def _validate_v4_authority(
    execution: ManagedExecution,
    *,
    repo_root: Path | None,
    verify_phase2a_control_plane: bool,
    verify_live_predecessor: bool,
    rejection_validator: R3RejectionValidator | None,
) -> None:
    authority = require_dict(execution.manifest, "recovery_authority")
    _require_exact_keys(
        authority,
        {
            "schema_version",
            "predecessor_execution",
            "predecessor_recovery_authority",
            "original_production_incident",
            "failed_r3_preflight",
            "administrative_r3_launch_rejection",
            "rejected_r3_preflight",
            "zero_consumption",
            "retry_equivalence",
            "authority_hash",
        },
        "successor-v4 recovery authority",
    )
    if (
        authority.get("schema_version") != SUCCESSOR_V4_AUTHORITY_SCHEMA_VERSION
        or authority.get("authority_hash")
        != _semantic_hash(authority, "authority_hash")
    ):
        raise ValueError("successor-v4 recovery authority identity mismatch")
    predecessor_record = require_dict(authority, "predecessor_execution")
    _require_exact_keys(
        predecessor_record,
        {
            "directory",
            "schema_version",
            "execution_generation",
            "execution_id",
            "execution_hash",
            "managed_execution_sha256",
            "static_checksums_sha256",
            "recovery_authority_hash",
        },
        "successor-v4 predecessor execution",
    )
    formal = execution.manifest.get("test_mode") is False
    predecessor_path = Path(require_string(predecessor_record, "directory"))
    if predecessor_path.is_symlink() or not predecessor_path.is_dir():
        raise ValueError("successor-v4 predecessor is missing or unsafe")
    predecessor_repo_root = repo_root
    if formal and not verify_phase2a_control_plane:
        predecessor_repo_root = predecessor_path / "sources/control"
    predecessor = _load_v3_successor_execution(
        predecessor_path,
        repo_root=predecessor_repo_root if formal else None,
        allow_test_mode=not formal,
        require_readiness=False,
        verify_runtime=formal,
        verify_phase2a_control_plane=verify_phase2a_control_plane,
        verify_live_predecessor=verify_live_predecessor,
    )
    predecessor_authority = require_dict(
        predecessor.manifest, "recovery_authority"
    )
    expected_predecessor = {
        "directory": str(predecessor.directory),
        "schema_version": predecessor.manifest.get("schema_version"),
        "execution_generation": predecessor.manifest.get("execution_generation"),
        "execution_id": predecessor.execution_id,
        "execution_hash": predecessor.execution_hash,
        "managed_execution_sha256": sha256_file(
            predecessor.directory / "managed_execution.json"
        ),
        "static_checksums_sha256": sha256_file(
            predecessor.directory / ROOT_STATIC_MANIFEST
        ),
        "recovery_authority_hash": predecessor_authority.get("authority_hash"),
    }
    if predecessor_record != expected_predecessor:
        raise ValueError("successor-v4 predecessor binding mismatch")
    if require_dict(authority, "predecessor_recovery_authority") != (
        predecessor_authority
    ):
        raise ValueError("successor-v4 predecessor recovery authority changed")
    if formal and predecessor.directory != canonical_v3_successor_directory(
        execution.managed_child
    ):
        raise ValueError("formal successor-v4 predecessor path is not exact")
    for key in (
        "original_production_incident",
        "failed_r3_preflight",
        "zero_consumption",
    ):
        if require_dict(authority, key) != require_dict(predecessor_authority, key):
            raise ValueError(f"successor-v4 inherited authority changed: {key}")
    administrative = require_dict(
        authority, "administrative_r3_launch_rejection"
    )
    _require_exact_keys(
        administrative,
        {
            "classification",
            "job_id",
            "manifest_path",
            "manifest_sha256",
            "compute_preflight",
            "apptainer_invoked",
            "geant4_invoked",
            "events_consumed",
            "production_seeds_consumed",
        },
        "successor-v4 administrative R3 rejection",
    )
    administrative_path = Path(require_string(administrative, "manifest_path"))
    expected_administrative_path = _resolve_r3_administrative_rejection_manifest(
        execution.managed_child,
        administrative_path,
        test_mode=not formal,
    )
    if (
        administrative.get("classification")
        != "pre-probe-administrative-launch-rejection"
        or administrative.get("job_id") != FORMAL_ADMIN_REJECTED_R3_JOB_ID
        or administrative.get("manifest_path")
        != str(expected_administrative_path)
        or administrative.get("manifest_sha256")
        != sha256_file(expected_administrative_path)
        or administrative.get("compute_preflight") is not False
        or administrative.get("apptainer_invoked") is not False
        or administrative.get("geant4_invoked") is not False
        or administrative.get("events_consumed") != 0
        or administrative.get("production_seeds_consumed") != 0
    ):
        raise ValueError("successor-v4 administrative R3 rejection changed")
    rejected_record = require_dict(authority, "rejected_r3_preflight")
    rejection_path = Path(require_string(rejected_record, "directory"))
    if rejection_path.is_symlink() or not rejection_path.is_dir():
        raise ValueError("successor-v4 R3 rejection bundle is missing or unsafe")
    rejection = _validated_r3_rejection(
        rejection_path.resolve(),
        predecessor=predecessor,
        test_mode=not formal,
        # A live admission validates the historical rejection against the
        # live checkout.  A frozen worker instead uses the predecessor-v3
        # control archive selected above; the active v4 archive is not a valid
        # repository identity for loading that immutable predecessor.
        repo_root=predecessor_repo_root,
        validator=rejection_validator,
    )
    rejection_probe = require_dict(rejection, "probe")
    validate_rejected_successor_v3_boundary_stable(
        predecessor,
        probe_token=require_string(rejection_probe, "probe_token"),
        container_probe_root=require_string(
            rejection_probe, "container_probe_root"
        ),
        repo_root=(
            predecessor_repo_root
            if predecessor_repo_root is not None
            else predecessor.directory / "sources/control"
        ),
        recorded_mountpoint=require_dict(
            rejection_probe, "historical_mountpoint"
        ),
    )
    if rejected_record != _rejection_binding(
        rejection_path.resolve(),
        rejection,
        predecessor=predecessor,
        test_mode=not formal,
    ):
        raise ValueError("successor-v4 R3 rejection bundle binding mismatch")
    retry = require_dict(authority, "retry_equivalence")
    expected_retry = {
        **_task_identity(execution.tasks),
        "tasks_tsv_sha256": sha256_file(execution.directory / "tasks.tsv"),
        "scan_args_sha256": sha256_file(execution.directory / "scan_args.txt"),
        "phase2a_tasks_sha256": sha256_file(
            execution.managed_child.directory / "tasks.tsv"
        ),
        "phase2a_scan_args_sha256": sha256_file(
            execution.managed_child.directory / "scan_args.txt"
        ),
        "simulation_source": require_dict(execution.manifest, "sources")[
            "simulation"
        ],
        "runtime": execution.manifest["runtime"],
        "all_equal": True,
    }
    if retry != expected_retry:
        raise ValueError("successor-v4 retry-equivalence evidence mismatch")
    if (
        execution.tasks != predecessor.tasks
        or execution.scan_args != predecessor.scan_args
        or require_dict(execution.manifest, "sources")["simulation"]
        != require_dict(predecessor.manifest, "sources")["simulation"]
        or execution.manifest.get("runtime") != predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor-v4 changed frozen physics/runtime inputs")


def _load_v4_successor_execution(
    execution_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = False,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
    allow_staging: bool = False,
    verify_live_predecessor: bool = False,
    rejection_validator: R3RejectionValidator | None = None,
) -> ManagedExecution:
    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("successor-v4 execution must not be a symlink")
    directory = requested.resolve()
    verify_execution_static_checksums(directory)
    _validate_successor_tree_modes(directory)
    actual_root = {path.name for path in directory.iterdir()}
    if actual_root != SUCCESSOR_ROOT_ENTRIES or any(
        path.is_symlink() for path in directory.iterdir()
    ):
        raise ValueError("successor-v4 execution root entry set is unsafe")
    manifest = load_json(directory / "managed_execution.json")
    _require_exact_keys(
        manifest,
        {
            "schema_version", "object_kind", "execution_generation",
            "created_at_utc", "test_mode", "accepted_statistical_evidence",
            "execution_id", "execution_hash", "phase", "managed_child",
            "program", "phase2a_lock", "readiness_gate", "shape", "runtime",
            "sources", "sealed_pilot", "artifacts", "submission_policy",
            "recovery_authority",
        },
        "successor-v4 execution manifest",
    )
    if (
        manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION
        or manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND
        or manifest.get("execution_generation") != SUCCESSOR_EXECUTION_GENERATION
        or manifest.get("phase") != "Phase-2B-R2-v4"
        or manifest.get("execution_hash")
        != _semantic_hash(manifest, "execution_hash")
    ):
        raise ValueError("unsupported or invalid successor-v4 manifest")
    require_string(manifest, "created_at_utc")
    test_mode = manifest.get("test_mode")
    accepted = manifest.get("accepted_statistical_evidence")
    if (
        not isinstance(test_mode, bool)
        or not isinstance(accepted, bool)
        or accepted == test_mode
        or (test_mode and not allow_test_mode)
    ):
        raise ValueError("successor-v4 test/evidence flags are invalid")
    if not test_mode and repo_root is None:
        raise ValueError("formal successor-v4 validation requires repo_root")
    root = repo_root.expanduser().resolve() if repo_root is not None else None
    sources = require_dict(manifest, "sources")
    _require_exact_keys(
        sources,
        {"schema_version", "simulation", "control_plane"},
        "successor-v4 source archives",
    )
    if sources.get("schema_version") != "steel-module-production-dual-source-v1":
        raise ValueError("successor-v4 source-archive schema mismatch")
    frozen_control_mode = not test_mode and not verify_phase2a_control_plane
    if frozen_control_mode:
        control = require_dict(sources, "control_plane")
        expected_control = (directory / require_string(control, "path")).resolve()
        if root != expected_control:
            raise ValueError("frozen successor-v4 loader must use control archive")
        _verify_source_record(directory, control)
    child_record = require_dict(manifest, "managed_child")
    child_dir = Path(require_string(child_record, "directory"))
    managed = load_managed_production_child(
        child_dir,
        repo_root=root if not test_mode else None,
        verify_runtime_artifacts=verify_runtime and not test_mode,
        verify_control_plane=not test_mode and verify_phase2a_control_plane,
        allow_test_mode=test_mode,
        _allow_formal_lock_relocation=frozen_control_mode,
    )
    expected_child_keys = {
        "directory", "campaign_id", "plan_hash", "binding_hash", "child_id",
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash", "tasks_sha256",
        "scan_args_sha256",
    }
    _require_exact_keys(child_record, expected_child_keys, "successor-v4 child")
    if (
        child_dir != managed.directory
        or child_record.get("child_id") != INITIAL_CHILD_ID
        or managed.plan.campaign_id != child_record.get("campaign_id")
        or managed.plan.plan_hash != child_record.get("plan_hash")
        or managed.binding.get("binding_hash") != child_record.get("binding_hash")
    ):
        raise ValueError("successor-v4 managed-child identity mismatch")
    binding_child = require_dict(managed.binding, "child")
    for key in (
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash",
    ):
        if child_record.get(key) != binding_child.get(key):
            raise ValueError(f"successor-v4 managed-child {key} mismatch")
    expected_program = {
        key: managed.binding["program"][key]
        for key in (
            "program_id", "program_hash", "parent_plan_hash",
            "authorization_graph_hash",
        )
    }
    if require_dict(manifest, "program") != expected_program:
        raise ValueError("successor-v4 production-program identity mismatch")
    environment = managed.plan.environment
    expected_runtime = {
        "environment_identity": environment["identity_hash"],
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
    }
    if require_dict(manifest, "runtime") != expected_runtime:
        raise ValueError("successor-v4 runtime identity differs from Phase-2A")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    scan_args = _read_scan_args(directory / "scan_args.txt")
    if tasks != managed.plan.tasks or scan_args != managed.plan.scan_args:
        raise ValueError("successor-v4 task/scan plan differs from Phase-2A")
    if (
        child_record.get("tasks_sha256")
        != sha256_file(managed.directory / "tasks.tsv")
        or child_record.get("scan_args_sha256")
        != sha256_file(managed.directory / "scan_args.txt")
    ):
        raise ValueError("successor-v4 child task/scan bytes changed")
    artifacts = require_dict(manifest, "artifacts")
    _require_exact_keys(
        artifacts,
        {"tasks", "scan_args", "source_archives", "control_lock"},
        "successor-v4 artifacts",
    )
    for key, filename in (
        ("tasks", "tasks.tsv"),
        ("scan_args", "scan_args.txt"),
        ("source_archives", "source_archives.json"),
    ):
        record = require_dict(artifacts, key)
        if (
            record.get("path") != filename
            or record.get("sha256") != sha256_file(directory / filename)
        ):
            raise ValueError(f"successor-v4 artifact mismatch: {filename}")
    expected_shape = {
        "task_count": 32, "event_count": 8000, "events_per_task": 250,
        "seed_count": 64, "tile_thickness_mm": [4, 24],
        "sipm_layout": "back-center", "absorber_transverse_mm": 500,
        "seed_block_range_inclusive": [0, 15],
    }
    seeds = _seed_values(tasks)
    if (
        require_dict(manifest, "shape") != expected_shape
        or len(seeds) != 64
        or len(set(seeds)) != 64
    ):
        raise ValueError("successor-v4 shape is not exact BC-S1")
    if load_json(directory / "source_archives.json") != sources:
        raise ValueError("successor-v4 source manifest mismatch")
    _verify_source_record(directory, require_dict(sources, "simulation"))
    _verify_source_record(directory, require_dict(sources, "control_plane"))
    _r3_launcher_identity(directory)
    lock_record = require_dict(artifacts, "control_lock")
    with _opened_portable_control_lock(
        directory / ".control.lock", lock_record, operation=fcntl.LOCK_SH
    ):
        pass
    recorded_pilot = require_dict(manifest, "sealed_pilot")
    current_pilot = _sealed_pilot_identity(
        Path(require_string(recorded_pilot, "directory")), allow_fixture=test_mode
    )
    for key, value in current_pilot.items():
        if recorded_pilot.get(key) != value:
            raise ValueError(f"successor-v4 sealed-pilot {key} mismatch")
    execution = ManagedExecution(directory, managed, manifest, tasks, scan_args)
    recorded_phase2a = require_dict(manifest, "phase2a_lock")
    _require_exact_keys(
        recorded_phase2a,
        {"path", "sha256", "schema_version"},
        "successor-v4 Phase-2A lock",
    )
    if recorded_phase2a.get("schema_version") != PHASE2A_LOCK_SCHEMA_VERSION:
        raise ValueError("successor-v4 Phase-2A lock schema mismatch")
    if not test_mode:
        assert root is not None
        phase2a_path, phase2a = load_phase2a_lock(root)
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
        if (
            recorded_phase2a.get("sha256") != sha256_file(phase2a_path)
            or (
                not frozen_control_mode
                and recorded_phase2a.get("path") != str(phase2a_path)
            )
        ):
            raise ValueError("successor-v4 Phase-2A identity mismatch")
        expected = canonical_successor_directory(managed)
        staging_prefix = f".{expected.name}.tmp-"
        if directory != expected and not (
            allow_staging
            and directory.parent == expected.parent
            and directory.name.startswith(staging_prefix)
        ):
            raise ValueError(f"formal successor-v4 path must be canonical: {expected}")
    _validate_v4_authority(
        execution,
        repo_root=root,
        verify_phase2a_control_plane=verify_phase2a_control_plane,
        verify_live_predecessor=verify_live_predecessor,
        rejection_validator=rejection_validator,
    )
    authority = require_dict(manifest, "recovery_authority")
    expected_execution_id = _v4_successor_execution_id(
        require_string(child_record, "binding_hash"),
        require_string(require_dict(sources, "control_plane"), "git_commit"),
        require_sha256(
            require_dict(authority, "predecessor_execution"), "execution_hash"
        ),
        require_sha256(
            require_dict(authority, "rejected_r3_preflight"), "rejection_hash"
        ),
    )
    if manifest.get("execution_id") != expected_execution_id:
        raise ValueError("successor-v4 execution ID does not match lineage")
    expected_policy = {
        "account": "PAS2524", "two_step_required": True,
        "initially_held": True, "no_requeue": True, "export_mode": "NONE",
        "formal_child": INITIAL_CHILD_ID,
        "formal_initial_mode": "predecessor-retry",
        "predecessor_retry_task_count": 32,
        "predecessor_retry_event_count": 8000,
        "r2_submittable": False,
    }
    expected_gate = {
        "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE.as_posix(),
        "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION,
        "required_for_scheduler_contact": True,
        "compute_node_container_preflight_required": True,
        "automatic_submission": False,
        "r2_submission_ready": False,
    }
    if (
        require_dict(manifest, "submission_policy") != expected_policy
        or require_dict(manifest, "readiness_gate") != expected_gate
    ):
        raise ValueError("successor-v4 readiness/submission boundary mismatch")
    if require_readiness:
        verify_successor_recovery_readiness(execution, repo_root=root)
    return execution


def _validate_v5_authority(
    execution: ManagedExecution,
    *,
    repo_root: Path | None,
    verify_phase2a_control_plane: bool,
    verify_live_predecessor: bool,
    rejection_validator: R3RejectionValidator | None,
    preworkspace_failure_validator: R3PreworkspaceFailureValidator | None,
) -> None:
    authority = require_dict(execution.manifest, "recovery_authority")
    _require_exact_keys(
        authority,
        {
            "schema_version",
            "predecessor_execution",
            "predecessor_recovery_authority",
            "preworkspace_r3_failure",
            "zero_consumption",
            "retry_equivalence",
            "authority_hash",
        },
        "successor-v5 recovery authority",
    )
    if (
        authority.get("schema_version") != SUCCESSOR_V5_AUTHORITY_SCHEMA_VERSION
        or authority.get("authority_hash")
        != _semantic_hash(authority, "authority_hash")
    ):
        raise ValueError("successor-v5 recovery authority identity mismatch")
    predecessor_record = require_dict(authority, "predecessor_execution")
    _require_exact_keys(
        predecessor_record,
        {
            "directory",
            "schema_version",
            "execution_generation",
            "execution_id",
            "execution_hash",
            "managed_execution_sha256",
            "static_checksums_sha256",
            "recovery_authority_hash",
        },
        "successor-v5 predecessor execution",
    )
    formal = execution.manifest.get("test_mode") is False
    predecessor_path = Path(require_string(predecessor_record, "directory"))
    if predecessor_path.is_symlink() or not predecessor_path.is_dir():
        raise ValueError("successor-v5 predecessor is missing or unsafe")
    predecessor_repo_root = repo_root
    if formal and not verify_phase2a_control_plane:
        predecessor_repo_root = predecessor_path / "sources/control"
    predecessor = _load_v4_successor_execution(
        predecessor_path,
        repo_root=predecessor_repo_root if formal else None,
        allow_test_mode=not formal,
        require_readiness=False,
        verify_runtime=formal,
        verify_phase2a_control_plane=verify_phase2a_control_plane,
        verify_live_predecessor=verify_live_predecessor,
        rejection_validator=rejection_validator,
    )
    predecessor_authority = require_dict(
        predecessor.manifest, "recovery_authority"
    )
    expected_predecessor = {
        "directory": str(predecessor.directory),
        "schema_version": predecessor.manifest.get("schema_version"),
        "execution_generation": predecessor.manifest.get("execution_generation"),
        "execution_id": predecessor.execution_id,
        "execution_hash": predecessor.execution_hash,
        "managed_execution_sha256": sha256_file(
            predecessor.directory / "managed_execution.json"
        ),
        "static_checksums_sha256": sha256_file(
            predecessor.directory / ROOT_STATIC_MANIFEST
        ),
        "recovery_authority_hash": predecessor_authority.get("authority_hash"),
    }
    if predecessor_record != expected_predecessor:
        raise ValueError("successor-v5 predecessor binding mismatch")
    if require_dict(authority, "predecessor_recovery_authority") != (
        predecessor_authority
    ):
        raise ValueError("successor-v5 predecessor recovery authority changed")
    if formal and predecessor.directory != canonical_successor_directory(
        execution.managed_child
    ):
        raise ValueError("formal successor-v5 predecessor path is not exact")
    failure_record = require_dict(authority, "preworkspace_r3_failure")
    failure_path = Path(require_string(failure_record, "directory"))
    if failure_path.is_symlink() or not failure_path.is_dir():
        raise ValueError("successor-v5 pre-workspace failure is missing or unsafe")
    failure = _validated_r3_preworkspace_failure(
        failure_path.resolve(),
        predecessor=predecessor,
        test_mode=not formal,
        repo_root=predecessor_repo_root,
        validator=preworkspace_failure_validator,
        require_current_execution=verify_live_predecessor,
    )
    if failure_record != _preworkspace_failure_binding(
        failure_path.resolve(),
        failure,
        predecessor=predecessor,
        test_mode=not formal,
    ):
        raise ValueError("successor-v5 pre-workspace failure binding mismatch")
    if require_dict(authority, "zero_consumption") != {
        "events_consumed": 0,
        "production_seeds_consumed": 0,
        "seed_reuse_authorized": True,
    }:
        raise ValueError("successor-v5 zero-consumption boundary changed")
    retry = require_dict(authority, "retry_equivalence")
    expected_retry = {
        **_task_identity(execution.tasks),
        "tasks_tsv_sha256": sha256_file(execution.directory / "tasks.tsv"),
        "scan_args_sha256": sha256_file(execution.directory / "scan_args.txt"),
        "phase2a_tasks_sha256": sha256_file(
            execution.managed_child.directory / "tasks.tsv"
        ),
        "phase2a_scan_args_sha256": sha256_file(
            execution.managed_child.directory / "scan_args.txt"
        ),
        "simulation_source": require_dict(execution.manifest, "sources")[
            "simulation"
        ],
        "runtime": execution.manifest["runtime"],
        "all_equal": True,
    }
    if retry != expected_retry:
        raise ValueError("successor-v5 retry-equivalence evidence mismatch")
    if (
        execution.tasks != predecessor.tasks
        or execution.scan_args != predecessor.scan_args
        or require_dict(execution.manifest, "sources")["simulation"]
        != require_dict(predecessor.manifest, "sources")["simulation"]
        or execution.manifest.get("runtime") != predecessor.manifest.get("runtime")
    ):
        raise ValueError("successor-v5 changed frozen physics/runtime inputs")


def _load_v5_successor_execution(
    execution_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = False,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
    allow_staging: bool = False,
    verify_live_predecessor: bool = False,
    rejection_validator: R3RejectionValidator | None = None,
    preworkspace_failure_validator: R3PreworkspaceFailureValidator | None = None,
) -> ManagedExecution:
    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("successor-v5 execution must not be a symlink")
    directory = requested.resolve()
    verify_execution_static_checksums(directory)
    _validate_successor_tree_modes(directory, control_lock_mode=0o600)
    actual_root = {path.name for path in directory.iterdir()}
    if actual_root != SUCCESSOR_ROOT_ENTRIES or any(
        path.is_symlink() for path in directory.iterdir()
    ):
        raise ValueError("successor-v5 execution root entry set is unsafe")
    manifest = load_json(directory / "managed_execution.json")
    _require_exact_keys(
        manifest,
        {
            "schema_version", "object_kind", "execution_generation",
            "created_at_utc", "test_mode", "accepted_statistical_evidence",
            "execution_id", "execution_hash", "phase", "managed_child",
            "program", "phase2a_lock", "readiness_gate", "shape", "runtime",
            "sources", "sealed_pilot", "artifacts", "submission_policy",
            "recovery_authority",
        },
        "successor-v5 execution manifest",
    )
    if (
        manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5
        or manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND
        or manifest.get("execution_generation") != SUCCESSOR_EXECUTION_GENERATION_V5
        or manifest.get("phase") != "Phase-2B-R2-v5"
        or manifest.get("execution_hash")
        != _semantic_hash(manifest, "execution_hash")
    ):
        raise ValueError("unsupported or invalid successor-v5 manifest")
    require_string(manifest, "created_at_utc")
    test_mode = manifest.get("test_mode")
    accepted = manifest.get("accepted_statistical_evidence")
    if (
        not isinstance(test_mode, bool)
        or not isinstance(accepted, bool)
        or accepted == test_mode
        or (test_mode and not allow_test_mode)
    ):
        raise ValueError("successor-v5 test/evidence flags are invalid")
    if not test_mode and repo_root is None:
        raise ValueError("formal successor-v5 validation requires repo_root")
    root = repo_root.expanduser().resolve() if repo_root is not None else None
    sources = require_dict(manifest, "sources")
    _require_exact_keys(
        sources,
        {"schema_version", "simulation", "control_plane"},
        "successor-v5 source archives",
    )
    if sources.get("schema_version") != "steel-module-production-dual-source-v1":
        raise ValueError("successor-v5 source-archive schema mismatch")
    frozen_control_mode = not test_mode and not verify_phase2a_control_plane
    if frozen_control_mode:
        control = require_dict(sources, "control_plane")
        expected_control = (directory / require_string(control, "path")).resolve()
        if root != expected_control:
            raise ValueError("frozen successor-v5 loader must use control archive")
        _verify_source_record(directory, control)
    child_record = require_dict(manifest, "managed_child")
    child_dir = Path(require_string(child_record, "directory"))
    managed = load_managed_production_child(
        child_dir,
        repo_root=root if not test_mode else None,
        verify_runtime_artifacts=verify_runtime and not test_mode,
        verify_control_plane=not test_mode and verify_phase2a_control_plane,
        allow_test_mode=test_mode,
        _allow_formal_lock_relocation=frozen_control_mode,
    )
    expected_child_keys = {
        "directory", "campaign_id", "plan_hash", "binding_hash", "child_id",
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash", "tasks_sha256",
        "scan_args_sha256",
    }
    _require_exact_keys(child_record, expected_child_keys, "successor-v5 child")
    if (
        child_dir != managed.directory
        or child_record.get("child_id") != INITIAL_CHILD_ID
        or managed.plan.campaign_id != child_record.get("campaign_id")
        or managed.plan.plan_hash != child_record.get("plan_hash")
        or managed.binding.get("binding_hash") != child_record.get("binding_hash")
    ):
        raise ValueError("successor-v5 managed-child identity mismatch")
    binding_child = require_dict(managed.binding, "child")
    for key in (
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash",
    ):
        if child_record.get(key) != binding_child.get(key):
            raise ValueError(f"successor-v5 managed-child {key} mismatch")
    expected_program = {
        key: managed.binding["program"][key]
        for key in (
            "program_id", "program_hash", "parent_plan_hash",
            "authorization_graph_hash",
        )
    }
    if require_dict(manifest, "program") != expected_program:
        raise ValueError("successor-v5 production-program identity mismatch")
    environment = managed.plan.environment
    expected_runtime = {
        "environment_identity": environment["identity_hash"],
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
    }
    if require_dict(manifest, "runtime") != expected_runtime:
        raise ValueError("successor-v5 runtime identity differs from Phase-2A")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    scan_args = _read_scan_args(directory / "scan_args.txt")
    if tasks != managed.plan.tasks or scan_args != managed.plan.scan_args:
        raise ValueError("successor-v5 task/scan plan differs from Phase-2A")
    if (
        child_record.get("tasks_sha256")
        != sha256_file(managed.directory / "tasks.tsv")
        or child_record.get("scan_args_sha256")
        != sha256_file(managed.directory / "scan_args.txt")
    ):
        raise ValueError("successor-v5 child task/scan bytes changed")
    artifacts = require_dict(manifest, "artifacts")
    _require_exact_keys(
        artifacts,
        {"tasks", "scan_args", "source_archives", "control_lock"},
        "successor-v5 artifacts",
    )
    for key, filename in (
        ("tasks", "tasks.tsv"),
        ("scan_args", "scan_args.txt"),
        ("source_archives", "source_archives.json"),
    ):
        record = require_dict(artifacts, key)
        if (
            record.get("path") != filename
            or record.get("sha256") != sha256_file(directory / filename)
        ):
            raise ValueError(f"successor-v5 artifact mismatch: {filename}")
    expected_shape = {
        "task_count": 32, "event_count": 8000, "events_per_task": 250,
        "seed_count": 64, "tile_thickness_mm": [4, 24],
        "sipm_layout": "back-center", "absorber_transverse_mm": 500,
        "seed_block_range_inclusive": [0, 15],
    }
    seeds = _seed_values(tasks)
    if (
        require_dict(manifest, "shape") != expected_shape
        or len(seeds) != 64
        or len(set(seeds)) != 64
    ):
        raise ValueError("successor-v5 shape is not exact BC-S1")
    if load_json(directory / "source_archives.json") != sources:
        raise ValueError("successor-v5 source manifest mismatch")
    _verify_source_record(directory, require_dict(sources, "simulation"))
    _verify_source_record(directory, require_dict(sources, "control_plane"))
    _r3_launcher_identity_v5(directory)
    lock_record = require_dict(artifacts, "control_lock")
    if lock_record.get("mode") != 0o600:
        raise ValueError("successor-v5 control-lock authority is not writable")
    with _opened_portable_control_lock(
        directory / ".control.lock", lock_record, operation=fcntl.LOCK_SH
    ):
        pass
    recorded_pilot = require_dict(manifest, "sealed_pilot")
    current_pilot = _sealed_pilot_identity(
        Path(require_string(recorded_pilot, "directory")), allow_fixture=test_mode
    )
    for key, value in current_pilot.items():
        if recorded_pilot.get(key) != value:
            raise ValueError(f"successor-v5 sealed-pilot {key} mismatch")
    execution = ManagedExecution(directory, managed, manifest, tasks, scan_args)
    recorded_phase2a = require_dict(manifest, "phase2a_lock")
    _require_exact_keys(
        recorded_phase2a,
        {"path", "sha256", "schema_version"},
        "successor-v5 Phase-2A lock",
    )
    if recorded_phase2a.get("schema_version") != PHASE2A_LOCK_SCHEMA_VERSION:
        raise ValueError("successor-v5 Phase-2A lock schema mismatch")
    if not test_mode:
        assert root is not None
        phase2a_path, phase2a = load_phase2a_lock(root)
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
        if (
            recorded_phase2a.get("sha256") != sha256_file(phase2a_path)
            or (
                not frozen_control_mode
                and recorded_phase2a.get("path") != str(phase2a_path)
            )
        ):
            raise ValueError("successor-v5 Phase-2A identity mismatch")
        expected = canonical_v5_successor_directory(managed)
        staging_prefix = f".{expected.name}.tmp-"
        if directory != expected and not (
            allow_staging
            and directory.parent == expected.parent
            and directory.name.startswith(staging_prefix)
        ):
            raise ValueError(f"formal successor-v5 path must be canonical: {expected}")
    _validate_v5_authority(
        execution,
        repo_root=root,
        verify_phase2a_control_plane=verify_phase2a_control_plane,
        verify_live_predecessor=verify_live_predecessor,
        rejection_validator=rejection_validator,
        preworkspace_failure_validator=preworkspace_failure_validator,
    )
    authority = require_dict(manifest, "recovery_authority")
    expected_execution_id = _v5_successor_execution_id(
        require_string(child_record, "binding_hash"),
        require_string(require_dict(sources, "control_plane"), "git_commit"),
        require_sha256(
            require_dict(authority, "predecessor_execution"), "execution_hash"
        ),
        require_sha256(
            require_dict(authority, "preworkspace_r3_failure"), "failure_hash"
        ),
    )
    if manifest.get("execution_id") != expected_execution_id:
        raise ValueError("successor-v5 execution ID does not match lineage")
    expected_policy = {
        "account": "PAS2524", "two_step_required": True,
        "initially_held": True, "no_requeue": True, "export_mode": "NONE",
        "formal_child": INITIAL_CHILD_ID,
        "formal_initial_mode": "predecessor-retry",
        "predecessor_retry_task_count": 32,
        "predecessor_retry_event_count": 8000,
        "r2_submittable": False,
    }
    expected_gate = {
        "tracked_path": RECOVERY_READINESS_LOCK_RELATIVE_V5.as_posix(),
        "schema_version": SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V5,
        "required_for_scheduler_contact": True,
        "compute_node_container_preflight_required": True,
        "automatic_submission": False,
        "r2_submission_ready": False,
    }
    if (
        require_dict(manifest, "submission_policy") != expected_policy
        or require_dict(manifest, "readiness_gate") != expected_gate
    ):
        raise ValueError("successor-v5 readiness/submission boundary mismatch")
    if require_readiness:
        raise ValueError("successor-v5 has no production readiness authority")
    return execution


def load_successor_execution(
    execution_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = False,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
    allow_staging: bool = False,
    verify_live_predecessor: bool = False,
    allow_closed_v2: bool = False,
    rejection_validator: R3RejectionValidator | None = None,
    preworkspace_failure_validator: R3PreworkspaceFailureValidator | None = None,
) -> ManagedExecution:
    directory = execution_dir.expanduser().resolve()
    manifest = load_json(directory / "managed_execution.json")
    schema = manifest.get("schema_version")
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2:
        value = _load_v2_successor_execution(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=False,
            verify_runtime=verify_runtime,
            verify_phase2a_control_plane=verify_phase2a_control_plane,
            allow_staging=allow_staging,
            verify_live_predecessor=verify_live_predecessor,
        )
        if value.manifest.get("test_mode") is False and not allow_closed_v2:
            raise ValueError(
                "successor-v2 is permanently closed after failed R3 preflight"
            )
        if require_readiness:
            raise ValueError("successor-v2 can never satisfy recovery readiness")
        return value
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3:
        value = _load_v3_successor_execution(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=False,
            verify_runtime=verify_runtime,
            verify_phase2a_control_plane=verify_phase2a_control_plane,
            allow_staging=allow_staging,
            verify_live_predecessor=verify_live_predecessor,
        )
        if require_readiness:
            raise ValueError(
                "successor-v3 is permanently closed after rejected R3 preflight"
            )
        return value
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        return _load_v4_successor_execution(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=require_readiness,
            verify_runtime=verify_runtime,
            verify_phase2a_control_plane=verify_phase2a_control_plane,
            allow_staging=allow_staging,
            verify_live_predecessor=verify_live_predecessor,
            rejection_validator=rejection_validator,
        )
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5:
        return _load_v5_successor_execution(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=require_readiness,
            verify_runtime=verify_runtime,
            verify_phase2a_control_plane=verify_phase2a_control_plane,
            allow_staging=allow_staging,
            verify_live_predecessor=verify_live_predecessor,
            rejection_validator=rejection_validator,
            preworkspace_failure_validator=preworkspace_failure_validator,
        )
    raise ValueError("unsupported recovery-successor execution schema")


def validate_successor_r2_boundary(
    execution: ManagedExecution, *, repo_root: Path
) -> None:
    for name in ("intents", "attempts", "finalized"):
        path = execution.directory / name
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ValueError(f"pre-readiness successor requires empty {name}/")
    schema = execution.manifest.get("schema_version")
    if schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2:
        readiness_relative = RECOVERY_READINESS_LOCK_RELATIVE_V2
    elif schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3:
        readiness_relative = RECOVERY_READINESS_LOCK_RELATIVE_V3
    elif schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        readiness_relative = RECOVERY_READINESS_LOCK_RELATIVE
    elif schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5:
        readiness_relative = RECOVERY_READINESS_LOCK_RELATIVE_V5
    else:
        raise ValueError("unsupported pre-readiness successor generation")
    readiness = repo_root.expanduser().resolve() / readiness_relative
    if readiness.exists() or readiness.is_symlink():
        raise ValueError(
            "pre-readiness successor cannot coexist with recovery readiness"
        )
    if successor_predecessor_retry_task_ids(execution) != tuple(
        task.logical_task_id for task in execution.tasks
    ):
        raise ValueError("successor predecessor-retry selection is not exact")


def rejected_successor_v3_mountpoint_binding(
    *, probe_token: str, container_probe_root: str
) -> dict[str, Any]:
    """Return the semantic record for one authenticated R3 bind target."""

    if (
        len(probe_token) != 32
        or probe_token.lower() != probe_token
        or any(character not in "0123456789abcdef" for character in probe_token)
    ):
        raise ValueError("historical R3 mountpoint probe token is invalid")
    mountpoint_name = R3_HISTORICAL_MOUNTPOINT_PREFIX + probe_token
    expected_container = (
        R3_HISTORICAL_CONTAINER_ATTEMPTS_ROOT / mountpoint_name
    ).as_posix()
    if container_probe_root != expected_container:
        raise ValueError("historical R3 container mountpoint identity mismatch")
    return {
        "relative_path": f"attempts/{mountpoint_name}",
        "probe_token": probe_token,
        "container_probe_root": container_probe_root,
        "mode": R3_HISTORICAL_MOUNTPOINT_MODE,
        "link_count": 2,
        "empty": True,
        "attempts_entry_count": 1,
    }


def validate_rejected_successor_v3_boundary(
    execution: ManagedExecution,
    *,
    probe_token: str,
    container_probe_root: str,
    repo_root: Path,
    recorded_mountpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the one empty bind target left by the rejected v3 R3 probe.

    This is deliberately separate from :func:`validate_successor_r2_boundary`.
    The normal pre-readiness boundary continues to require a completely empty
    ``attempts/`` directory.  Only a rejection validator that has already
    authenticated the raw R3 probe token may call this historical exception.
    """

    if execution.manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3:
        raise ValueError("historical R3 mountpoint requires successor-v3")
    binding = rejected_successor_v3_mountpoint_binding(
        probe_token=probe_token,
        container_probe_root=container_probe_root,
    )
    mountpoint_name = Path(binding["relative_path"]).name

    for name in ("intents", "finalized"):
        path = execution.directory / name
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ValueError(f"rejected successor-v3 requires empty {name}/")
    attempts = execution.directory / "attempts"
    if attempts.is_symlink() or not attempts.is_dir():
        raise ValueError("rejected successor-v3 attempts/ is unsafe")
    entries = tuple(attempts.iterdir())
    if len(entries) != 1:
        raise ValueError(
            "rejected successor-v3 requires exactly one historical R3 mountpoint"
        )
    mountpoint = entries[0]
    if mountpoint.name != mountpoint_name:
        raise ValueError("historical R3 mountpoint name/token mismatch")
    if mountpoint.is_symlink() or not mountpoint.is_dir():
        raise ValueError("historical R3 mountpoint must be a regular directory")
    stat_result = mountpoint.lstat()
    mode = stat_result.st_mode & 0o777
    if mode != R3_HISTORICAL_MOUNTPOINT_MODE:
        raise ValueError("historical R3 mountpoint mode mismatch")
    if any(mountpoint.iterdir()):
        raise ValueError("historical R3 mountpoint is not empty")

    if mode != binding["mode"]:
        raise ValueError("historical R3 mountpoint semantic mode mismatch")
    if stat_result.st_nlink != binding["link_count"]:
        raise ValueError("historical R3 mountpoint link-count mismatch")
    if recorded_mountpoint is not None and recorded_mountpoint != binding:
        raise ValueError("recorded historical R3 mountpoint binding mismatch")

    readiness = (
        repo_root.expanduser().resolve() / RECOVERY_READINESS_LOCK_RELATIVE_V3
    )
    if readiness.exists() or readiness.is_symlink():
        raise ValueError(
            "rejected successor-v3 cannot coexist with recovery readiness"
        )
    if successor_predecessor_retry_task_ids(execution) != tuple(
        task.logical_task_id for task in execution.tasks
    ):
        raise ValueError("successor predecessor-retry selection is not exact")
    return binding


def validate_rejected_successor_v3_boundary_stable(
    execution: ManagedExecution,
    *,
    probe_token: str,
    container_probe_root: str,
    repo_root: Path,
    recorded_mountpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the historical mountpoint on both sides of a file snapshot."""

    before = validate_rejected_successor_v3_boundary(
        execution,
        probe_token=probe_token,
        container_probe_root=container_probe_root,
        repo_root=repo_root,
        recorded_mountpoint=recorded_mountpoint,
    )
    recursive_file_records(execution.directory, exclude=())
    after = validate_rejected_successor_v3_boundary(
        execution,
        probe_token=probe_token,
        container_probe_root=container_probe_root,
        repo_root=repo_root,
        recorded_mountpoint=before,
    )
    if after != before:
        raise ValueError("historical R3 mountpoint changed during validation")
    return before


def successor_predecessor_retry_task_ids(
    execution: ManagedExecution,
) -> tuple[str, ...]:
    if execution.manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND:
        raise ValueError("predecessor-retry selection requires a successor")
    return selected_task_ids(execution, "predecessor-retry")


def materialize_successor_execution_companion(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    incident_dir: Path | None = None,
    out_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    fault_after: str | None = None,
) -> ManagedExecution:
    if not test_mode:
        raise ValueError(
            "formal successor-v2 materialization is retired and permanently closed"
        )
    preview = preview_successor_execution(
        repo_root=repo_root,
        managed_child_dir=managed_child_dir,
        predecessor_dir=predecessor_dir,
        incident_dir=incident_dir,
        pilot_dir=pilot_dir,
        test_mode=test_mode,
    )
    expected = canonical_v2_successor_directory(preview.managed_child)
    requested_target = out_dir or expected
    if requested_target.is_symlink() or requested_target.parent.is_symlink():
        raise ValueError("successor output must not be a symlink")
    target = requested_target.expanduser().resolve()
    if not test_mode and target != expected:
        raise ValueError(f"formal successor path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite successor execution: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("successor output parent is unsafe")
    for protected in (preview.predecessor.directory, preview.incident_path):
        if target == protected or protected in target.parents or target in protected.parents:
            raise ValueError("successor output overlaps predecessor evidence")
    readiness = preview.repo_root / RECOVERY_READINESS_LOCK_RELATIVE_V2
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("R2 materialization requires recovery readiness to be absent")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_execution_tree(
            temporary,
            managed=preview.managed_child,
            repo_root=preview.repo_root,
            phase2a_path=preview.phase2a_path,
            phase2a_lock=preview.phase2a_lock,
            pilot_dir=preview.pilot_dir,
            test_mode=test_mode,
            fixture_sources=fixture_sources,
            fault_after=fault_after
            if fault_after in {"tasks", "sources", "checksums"}
            else None,
            control_lock_protocol=CONTROL_LOCK_PROTOCOL_V2,
        )
        if fault_after in {"predecessor", "recovery-authority"}:
            raise RuntimeError("injected successor failure before recovery authority")
        _transform_successor_manifest(temporary, preview)
        _harden_successor_tree(temporary)
        loaded = _load_v2_successor_execution(
            temporary,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            allow_staging=True,
            verify_live_predecessor=not test_mode,
        )
        validate_successor_r2_boundary(loaded, repo_root=preview.repo_root)
        if fault_after == "pre-publish":
            raise RuntimeError("injected successor failure before publication")
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        result = _load_v2_successor_execution(
            target,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            verify_live_predecessor=not test_mode,
        )
        validate_successor_r2_boundary(result, repo_root=preview.repo_root)
        return result
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def materialize_successor_v3_execution_companion(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    failure_dir: Path | None = None,
    out_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    fault_after: str | None = None,
) -> ManagedExecution:
    preview = preview_successor_v3_execution(
        repo_root=repo_root,
        managed_child_dir=managed_child_dir,
        predecessor_dir=predecessor_dir,
        failure_dir=failure_dir,
        pilot_dir=pilot_dir,
        test_mode=test_mode,
    )
    expected = canonical_v3_successor_directory(preview.managed_child)
    requested_target = out_dir or expected
    if requested_target.is_symlink() or requested_target.parent.is_symlink():
        raise ValueError("successor-v3 output must not be a symlink")
    target = requested_target.expanduser().resolve()
    if not test_mode and target != expected:
        raise ValueError(f"formal successor-v3 path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite successor-v3 execution: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("successor-v3 output parent is unsafe")
    for protected in (preview.predecessor.directory, preview.failure_path):
        if target == protected or protected in target.parents or target in protected.parents:
            raise ValueError("successor-v3 output overlaps immutable predecessor evidence")
    readiness = preview.repo_root / RECOVERY_READINESS_LOCK_RELATIVE_V3
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("successor-v3 materialization requires readiness to be absent")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_execution_tree(
            temporary,
            managed=preview.managed_child,
            repo_root=preview.repo_root,
            phase2a_path=preview.phase2a_path,
            phase2a_lock=preview.phase2a_lock,
            pilot_dir=preview.pilot_dir,
            test_mode=test_mode,
            fixture_sources=fixture_sources,
            fault_after=(
                fault_after
                if fault_after in {"tasks", "sources", "checksums"}
                else None
            ),
            control_lock_protocol=CONTROL_LOCK_PROTOCOL_V2,
        )
        if fault_after in {"predecessor", "failure", "recovery-authority"}:
            raise RuntimeError("injected successor-v3 lineage failure")
        _transform_v3_manifest(temporary, preview)
        _harden_successor_tree(temporary)
        loaded = _load_v3_successor_execution(
            temporary,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            allow_staging=True,
            verify_live_predecessor=not test_mode,
        )
        validate_successor_r2_boundary(loaded, repo_root=preview.repo_root)
        if fault_after == "pre-publish":
            raise RuntimeError("injected successor-v3 failure before publication")
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        result = _load_v3_successor_execution(
            target,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            verify_live_predecessor=not test_mode,
        )
        validate_successor_r2_boundary(result, repo_root=preview.repo_root)
        return result
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def materialize_successor_v4_execution_companion(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    rejection_dir: Path | None = None,
    administrative_rejection_manifest: Path | None = None,
    out_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    fault_after: str | None = None,
    rejection_validator: R3RejectionValidator | None = None,
) -> ManagedExecution:
    preview = preview_successor_v4_execution(
        repo_root=repo_root,
        managed_child_dir=managed_child_dir,
        predecessor_dir=predecessor_dir,
        rejection_dir=rejection_dir,
        administrative_rejection_manifest=administrative_rejection_manifest,
        pilot_dir=pilot_dir,
        test_mode=test_mode,
        rejection_validator=rejection_validator,
    )
    expected = canonical_successor_directory(preview.managed_child)
    requested_target = out_dir or expected
    if requested_target.is_symlink() or requested_target.parent.is_symlink():
        raise ValueError("successor-v4 output must not be a symlink")
    target = requested_target.expanduser().resolve()
    if not test_mode and target != expected:
        raise ValueError(f"formal successor-v4 path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite successor-v4 execution: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("successor-v4 output parent is unsafe")
    for protected in (
        preview.predecessor.directory,
        preview.administrative_rejection_manifest,
        preview.rejection_path,
    ):
        if target == protected or protected in target.parents or target in protected.parents:
            raise ValueError("successor-v4 output overlaps immutable predecessor evidence")
    readiness = preview.repo_root / RECOVERY_READINESS_LOCK_RELATIVE
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("successor-v4 materialization requires readiness to be absent")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_execution_tree(
            temporary,
            managed=preview.managed_child,
            repo_root=preview.repo_root,
            phase2a_path=preview.phase2a_path,
            phase2a_lock=preview.phase2a_lock,
            pilot_dir=preview.pilot_dir,
            test_mode=test_mode,
            fixture_sources=fixture_sources,
            fault_after=(
                fault_after
                if fault_after in {"tasks", "sources", "checksums"}
                else None
            ),
            control_lock_protocol=CONTROL_LOCK_PROTOCOL_V2,
        )
        if fault_after in {
            "predecessor",
            "administrative-rejection",
            "rejection",
            "recovery-authority",
        }:
            raise RuntimeError("injected successor-v4 lineage failure")
        _transform_v4_manifest(temporary, preview)
        _harden_successor_tree(temporary)
        loaded = _load_v4_successor_execution(
            temporary,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            allow_staging=True,
            verify_live_predecessor=not test_mode,
            rejection_validator=rejection_validator,
        )
        validate_successor_r2_boundary(loaded, repo_root=preview.repo_root)
        if fault_after == "pre-publish":
            raise RuntimeError("injected successor-v4 failure before publication")
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        result = _load_v4_successor_execution(
            target,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            verify_live_predecessor=not test_mode,
            rejection_validator=rejection_validator,
        )
        validate_successor_r2_boundary(result, repo_root=preview.repo_root)
        return result
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def materialize_successor_v5_execution_companion(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    predecessor_dir: Path | None = None,
    preworkspace_failure_dir: Path | None = None,
    out_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    fault_after: str | None = None,
    rejection_validator: R3RejectionValidator | None = None,
    preworkspace_failure_validator: R3PreworkspaceFailureValidator | None = None,
) -> ManagedExecution:
    preview = preview_successor_v5_execution(
        repo_root=repo_root,
        managed_child_dir=managed_child_dir,
        predecessor_dir=predecessor_dir,
        preworkspace_failure_dir=preworkspace_failure_dir,
        pilot_dir=pilot_dir,
        test_mode=test_mode,
        rejection_validator=rejection_validator,
        preworkspace_failure_validator=preworkspace_failure_validator,
    )
    expected = canonical_v5_successor_directory(preview.managed_child)
    requested_target = out_dir or expected
    if requested_target.is_symlink() or requested_target.parent.is_symlink():
        raise ValueError("successor-v5 output must not be a symlink")
    target = requested_target.expanduser().resolve()
    if not test_mode and target != expected:
        raise ValueError(f"formal successor-v5 path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite successor-v5 execution: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("successor-v5 output parent is unsafe")
    for protected in (
        preview.predecessor.directory,
        preview.preworkspace_failure_path,
    ):
        if target == protected or protected in target.parents or target in protected.parents:
            raise ValueError("successor-v5 output overlaps immutable predecessor evidence")
    readiness = (
        preview.repo_root
        / RECOVERY_READINESS_LOCK_RELATIVE_V5
    )
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("successor-v5 materialization requires readiness to be absent")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_execution_tree(
            temporary,
            managed=preview.managed_child,
            repo_root=preview.repo_root,
            phase2a_path=preview.phase2a_path,
            phase2a_lock=preview.phase2a_lock,
            pilot_dir=preview.pilot_dir,
            test_mode=test_mode,
            fixture_sources=fixture_sources,
            fault_after=(
                fault_after
                if fault_after in {"tasks", "sources", "checksums"}
                else None
            ),
            control_lock_protocol=CONTROL_LOCK_PROTOCOL_V2,
        )
        if fault_after in {
            "predecessor",
            "preworkspace-failure",
            "recovery-authority",
        }:
            raise RuntimeError("injected successor-v5 lineage failure")
        _transform_v5_manifest(temporary, preview)
        _harden_successor_tree(temporary)
        loaded = _load_v5_successor_execution(
            temporary,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            allow_staging=True,
            verify_live_predecessor=not test_mode,
            rejection_validator=rejection_validator,
            preworkspace_failure_validator=preworkspace_failure_validator,
        )
        validate_successor_r2_boundary(loaded, repo_root=preview.repo_root)
        if fault_after == "pre-publish":
            raise RuntimeError("injected successor-v5 failure before publication")
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        result = _load_v5_successor_execution(
            target,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            verify_runtime=not test_mode,
            verify_live_predecessor=not test_mode,
            rejection_validator=rejection_validator,
            preworkspace_failure_validator=preworkspace_failure_validator,
        )
        validate_successor_r2_boundary(result, repo_root=preview.repo_root)
        return result
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def load_execution_for_frozen_worker(
    execution_dir: Path, *, control_root: Path
) -> ManagedExecution:
    """Dispatch a frozen worker without weakening the historical v1 loader.

    The successor's control archive is deliberately frozen at R2 and therefore
    cannot contain the later lock-only R4 commit (or a ``.git`` directory).
    First validate the execution against that exact frozen archive, then locate
    and validate R4 through the Phase-2A path recorded in the manifest.  Keeping
    those roots separate prevents the compute worker from treating an archive
    as a live Git checkout while still executing only frozen control code.
    """

    manifest = load_json(execution_dir.expanduser().resolve() / "managed_execution.json")
    if manifest.get("schema_version") in {
        SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2,
        SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3,
    }:
        raise ValueError(
            "historical recovery successor is permanently closed and cannot "
            "be a worker target"
        )
    if manifest.get("schema_version") == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        execution = load_successor_execution(
            execution_dir,
            repo_root=control_root,
            require_readiness=False,
            verify_phase2a_control_plane=False,
            verify_live_predecessor=False,
        )
        verify_successor_recovery_readiness(execution, repo_root=None)
        return execution
    if manifest.get("schema_version") == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5:
        return load_successor_execution(
            execution_dir,
            repo_root=control_root,
            require_readiness=False,
            verify_phase2a_control_plane=False,
            verify_live_predecessor=False,
        )
    execution = load_execution_companion(
        execution_dir,
        repo_root=control_root,
        require_readiness=False,
        verify_phase2a_control_plane=False,
    )
    from steel_module_production_phase2b_lib import require_production_execution_open

    require_production_execution_open(execution)
    return execution


__all__ = [
    "FORMAL_ADMIN_REJECTED_R3_JOB_ID",
    "FORMAL_ADMIN_REJECTED_R3_MANIFEST_SHA256",
    "FORMAL_FAILED_R3_JOB_ID",
    "FORMAL_INCIDENT_HASH",
    "FORMAL_INCIDENT_ID",
    "FORMAL_INCIDENT_NAME",
    "FORMAL_PREWORKSPACE_FAILED_R3_JOB_ID",
    "FORMAL_REJECTED_R3_JOB_ID",
    "FORMAL_SUCCESSOR_EXECUTION_NAME",
    "FORMAL_SUCCESSOR_EXECUTION_NAME_V2",
    "FORMAL_SUCCESSOR_EXECUTION_NAME_V3",
    "FORMAL_SUCCESSOR_EXECUTION_NAME_V5",
    "FORMAL_SUCCESSOR_V2_EXECUTION_HASH",
    "FORMAL_SUCCESSOR_V2_EXECUTION_ID",
    "RECOVERY_READINESS_LOCK_RELATIVE",
    "RECOVERY_READINESS_LOCK_RELATIVE_V2",
    "RECOVERY_READINESS_LOCK_RELATIVE_V3",
    "RECOVERY_READINESS_LOCK_RELATIVE_V5",
    "R3_COPY_SAFE_LAUNCHER_RELATIVE_V5",
    "SUCCESSOR_AUTHORITY_SCHEMA_VERSION",
    "SUCCESSOR_EXECUTION_GENERATION",
    "SUCCESSOR_EXECUTION_GENERATION_V2",
    "SUCCESSOR_EXECUTION_GENERATION_V3",
    "SUCCESSOR_EXECUTION_GENERATION_V5",
    "SUCCESSOR_EXECUTION_SCHEMA_VERSION",
    "SUCCESSOR_EXECUTION_SCHEMA_VERSION_V2",
    "SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3",
    "SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5",
    "SUCCESSOR_OBJECT_KIND",
    "SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION",
    "SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V2",
    "SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V3",
    "SUCCESSOR_RECOVERY_READINESS_SCHEMA_VERSION_V5",
    "SUCCESSOR_V3_AUTHORITY_SCHEMA_VERSION",
    "SUCCESSOR_V4_AUTHORITY_SCHEMA_VERSION",
    "SUCCESSOR_V5_AUTHORITY_SCHEMA_VERSION",
    "SuccessorPreview",
    "SuccessorV3Preview",
    "SuccessorV4Preview",
    "SuccessorV5Preview",
    "build_successor_recovery_readiness_candidate",
    "canonical_incident_directory",
    "canonical_successor_directory",
    "canonical_v2_successor_directory",
    "canonical_v3_successor_directory",
    "canonical_v5_successor_directory",
    "load_execution_for_frozen_worker",
    "load_successor_execution",
    "materialize_successor_execution_companion",
    "materialize_successor_v3_execution_companion",
    "materialize_successor_v4_execution_companion",
    "materialize_successor_v5_execution_companion",
    "preview_successor_execution",
    "preview_successor_v3_execution",
    "preview_successor_v4_execution",
    "preview_successor_v5_execution",
    "rejected_successor_v3_mountpoint_binding",
    "successor_predecessor_retry_task_ids",
    "successor_r3_preflight_binding",
    "successor_recovery_readiness_path",
    "validate_successor_r2_boundary",
    "validate_rejected_successor_v3_boundary",
    "validate_rejected_successor_v3_boundary_stable",
    "verify_successor_recovery_readiness",
]
