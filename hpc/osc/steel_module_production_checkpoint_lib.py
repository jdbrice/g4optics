#!/usr/bin/env python3
"""Immutable BC-only production-checkpoint helpers.

The checkpoint is deliberately a small, content-addressed index.  It never
copies ROOT files: every referenced event file remains in the checksum-valid
managed-child finalization that produced it.
"""

from __future__ import annotations

import csv
import ctypes
import errno
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    load_json,
    require_dict,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_program_lib import (
    load_production_program,
    ordered_task_hash,
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)
from steel_module_production_phase2b_lib import (
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    EXECUTION_SCHEMA_VERSION_V1,
    EXECUTION_SCHEMA_VERSION_V2,
    FORMAL_EXECUTION_NAME,
    READINESS_LOCK_RELATIVE,
    SUCCESSOR_OBJECT_KIND,
    base_slurm_state,
    load_execution_companion,
    load_attempt_intent,
    load_frozen_accounting,
    load_managed_task_result,
    load_phase2a_lock,
)


CHECKPOINT_SCHEMA_VERSION = "steel-module-production-checkpoint-v1"
MANAGED_FINALIZATION_SCHEMA_VERSION = "steel-module-managed-finalization-v1"
RECOVERY_LINEAGE_SCHEMA_VERSION = "steel-module-managed-recovery-lineage-v1"
RECOVERY_LINEAGE_SCHEMA_VERSION_V2 = "steel-module-managed-recovery-lineage-v2"
RECOVERY_LINEAGE_SCHEMA_VERSION_V3 = "steel-module-managed-recovery-lineage-v3"
RECOVERY_LINEAGE_SCHEMA_VERSION_V4 = "steel-module-managed-recovery-lineage-v4"
SUCCESSOR_AUTHORITY_SCHEMA_VERSION = (
    "steel-module-production-successor-authority-v1"
)
SUCCESSOR_AUTHORITY_SCHEMA_VERSION_V2 = (
    "steel-module-production-successor-authority-v2"
)
SUCCESSOR_AUTHORITY_SCHEMA_VERSION_V3 = (
    "steel-module-production-successor-authority-v3"
)
FORMAL_CHECKPOINT_STATE = "BC-ONLY-S1"
FORMAL_EVIDENCE_MODE = "back-center-only"
SUCCESSOR_FINALIZATION_BUNDLE_NAME = "whole-child"
CHECKPOINT_REQUIRED_FILES = {
    "checkpoint.json",
    "configuration_summary.csv",
    "event_audit.json",
    "seed_audit.json",
    "source_children.json",
    "source_finalization.json",
    "task_index.tsv",
}


@dataclass(frozen=True)
class ProductionCheckpoint:
    directory: Path
    manifest: dict[str, Any]
    task_rows: tuple[dict[str, str], ...]
    event_audit: dict[str, Any]
    seed_audit: dict[str, Any]

    @property
    def checkpoint_hash(self) -> str:
        return require_sha256(self.manifest, "checkpoint_hash")


def is_successor_execution_manifest(manifest: dict[str, Any]) -> bool:
    """Return whether a managed-execution manifest is the recovery successor.

    The schema and object-kind checks stay together so that a self-relabelled
    historical execution cannot be routed through the successor authority.
    """

    schema = manifest.get("schema_version")
    object_kind = manifest.get("object_kind")
    # Phase-2C is intentionally kept out of the historical Phase-2B schema
    # module.  Importing its exact schema lazily also avoids a module cycle:
    # the v6 loader reuses these immutable finalization/checkpoint helpers.
    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if schema == EXECUTION_V6_SCHEMA_VERSION:
        # The Phase-2C loader performs the complete object-kind and authority
        # validation.  This predicate is only the fail-closed dispatch point.
        return True
    if schema in {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    }:
        if object_kind != SUCCESSOR_OBJECT_KIND:
            raise ValueError("successor execution schema/object-kind mismatch")
        return True
    if schema in {EXECUTION_SCHEMA_VERSION_V1, EXECUTION_SCHEMA_VERSION_V2}:
        if object_kind == SUCCESSOR_OBJECT_KIND:
            raise ValueError("historical execution cannot claim successor object-kind")
        return False
    raise ValueError("unsupported managed-execution schema")


def managed_finalization_directory(
    execution_dir: Path, manifest: dict[str, Any]
) -> Path:
    """Return the schema-specific whole-child evidence directory.

    Historical execution schemas retain their original ``finalized/`` layout.
    The recovery successor keeps that pre-created directory as its writable
    publication container and atomically installs the evidence beneath the
    fixed ``whole-child`` leaf.  Keeping this routing in one helper prevents
    finalizer/checkpoint consumers from silently disagreeing about authority.
    """

    requested = execution_dir.expanduser()
    if requested.is_symlink():
        raise ValueError("managed execution must not be a symlink")
    directory = requested.resolve()
    finalized = directory / "finalized"
    if is_successor_execution_manifest(manifest):
        return finalized / SUCCESSOR_FINALIZATION_BUNDLE_NAME
    return finalized


def load_execution_for_downstream(
    execution_dir: Path,
    *,
    repo_root: Path | None,
    allow_test_mode: bool = False,
    require_readiness: bool = True,
    verify_runtime: bool = True,
    verify_live_predecessor: bool = True,
) -> Any:
    """Load old or recovery-successor execution with the correct authority.

    Importing the successor loader lazily avoids a module cycle: the successor
    implementation itself reuses this module's immutable evidence helpers.
    """

    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("managed execution must not be a symlink")
    directory = requested.resolve()
    manifest_path = directory / "managed_execution.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("managed execution manifest is missing or unsafe")
    manifest = load_json(manifest_path)
    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if manifest.get("schema_version") == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_lib import load_execution_v6

        return load_execution_v6(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=require_readiness,
            verify_runtime=verify_runtime,
            verify_live_predecessor=verify_live_predecessor,
        )
    if is_successor_execution_manifest(manifest):
        from steel_module_production_successor_lib import load_successor_execution

        return load_successor_execution(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=require_readiness,
            verify_runtime=verify_runtime,
            verify_live_predecessor=verify_live_predecessor,
        )
    return load_execution_companion(
        directory,
        repo_root=repo_root,
        allow_test_mode=allow_test_mode,
        require_readiness=require_readiness,
        verify_runtime=verify_runtime,
    )


def recovery_lineage_from_execution(execution: Any) -> dict[str, Any] | None:
    """Build immutable successor/predecessor provenance for downstream evidence."""

    manifest = execution.manifest
    if not is_successor_execution_manifest(manifest):
        return None
    schema = manifest.get("schema_version")
    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if schema == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_lib import phase2c_recovery_lineage

        return phase2c_recovery_lineage(execution)
    authority = require_dict(manifest, "recovery_authority")
    predecessor = require_dict(authority, "predecessor_execution")
    rejected_r3: dict[str, Any] | None = None
    administrative_r3: dict[str, Any] | None = None
    if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
        lineage_schema = RECOVERY_LINEAGE_SCHEMA_VERSION
        incident = require_dict(authority, "incident")
        failed_r3: dict[str, Any] | None = None
        result_scope = {
            "source_execution_directory": str(execution.directory),
            "selected_results": "successor-successes-only",
            "predecessor_failed_outputs_included": False,
            "predecessor_accounting_included": False,
        }
    elif schema in {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    }:
        lineage_schema = RECOVERY_LINEAGE_SCHEMA_VERSION_V2
        incident = require_dict(authority, "original_production_incident")
        failed_r3 = require_dict(authority, "failed_r3_preflight")
        result_scope = {
            "source_execution_directory": str(execution.directory),
            "selected_results": "successor-successes-only",
            "predecessor_failed_outputs_included": False,
            "predecessor_accounting_included": False,
            "failed_r3_preflight_outputs_included": False,
            "failed_r3_accounting_included": False,
        }
        if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3:
            lineage_schema = RECOVERY_LINEAGE_SCHEMA_VERSION_V3
            rejected_r3 = require_dict(authority, "rejected_r3_preflight")
            administrative_r3 = require_dict(
                authority, "administrative_r3_launch_rejection"
            )
            result_scope.update(
                {
                    "rejected_r3_preflight_outputs_included": False,
                    "rejected_r3_accounting_included": False,
                    "administrative_r3_launch_rejection_included_as_compute": False,
                }
            )
    else:  # guarded by is_successor_execution_manifest
        raise ValueError("unsupported recovery-successor schema")
    payload: dict[str, Any] = {
        "schema_version": lineage_schema,
        "successor_execution": {
            "directory": str(execution.directory),
            "schema_version": manifest.get("schema_version"),
            "object_kind": manifest.get("object_kind"),
            "execution_generation": manifest.get("execution_generation"),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
        },
        # Keep the complete authority record.  Compact incident fields alone
        # would lose the zero-consumption and exact retry-equivalence proof.
        "recovery_authority": json.loads(json.dumps(authority)),
        "predecessor_incident": {
            "directory": incident.get("directory"),
            "incident_id": incident.get("incident_id"),
            "incident_hash": incident.get("incident_hash"),
            "incident_json_sha256": incident.get("incident_json_sha256"),
            "checksum_manifest_sha256": incident.get(
                "checksum_manifest_sha256"
            ),
        },
        "predecessor_execution": {
            "directory": predecessor.get("directory"),
            "execution_id": predecessor.get("execution_id"),
            "execution_hash": predecessor.get("execution_hash"),
        },
        "result_scope": result_scope,
    }
    if failed_r3 is not None:
        payload["failed_r3_preflight"] = json.loads(json.dumps(failed_r3))
    if rejected_r3 is not None:
        payload["rejected_r3_preflight"] = json.loads(json.dumps(rejected_r3))
    if administrative_r3 is not None:
        payload["administrative_r3_launch_rejection"] = json.loads(
            json.dumps(administrative_r3)
        )
    payload["lineage_hash"] = sha256_bytes(canonical_json(payload))
    return payload


def validate_recovery_lineage(
    execution_record: dict[str, Any], lineage: object
) -> dict[str, Any] | None:
    """Validate optional lineage, requiring it for every successor output."""

    schema = execution_record.get("schema_version")
    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if schema == EXECUTION_V6_SCHEMA_VERSION:
        if not isinstance(lineage, dict):
            raise ValueError("execution-v6 evidence lacks Phase-2C lineage")
        from steel_module_production_phase2c_lib import (
            validate_phase2c_recovery_lineage,
        )

        validated = validate_phase2c_recovery_lineage(
            execution_record, lineage
        )
        attempts = validated.get("excluded_attempt_ids")
        jobs = validated.get("excluded_job_ids")
        if (
            not isinstance(attempts, list)
            or not attempts
            or any(not isinstance(value, str) or not value for value in attempts)
            or len(set(attempts)) != len(attempts)
            or not isinstance(jobs, list)
            or not jobs
            or any(not isinstance(value, str) or not value.isdigit() for value in jobs)
            or len(set(jobs)) != len(jobs)
        ):
            raise ValueError("Phase-2C recovery-lineage exclusions are invalid")
        return validated
    if schema not in {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    }:
        if lineage is not None:
            raise ValueError("historical execution must not carry recovery lineage")
        return None
    if not isinstance(lineage, dict):
        raise ValueError("successor evidence lacks recovery lineage")
    expected_lineage_schema = {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1: RECOVERY_LINEAGE_SCHEMA_VERSION,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2: RECOVERY_LINEAGE_SCHEMA_VERSION_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3: RECOVERY_LINEAGE_SCHEMA_VERSION_V3,
    }[schema]
    if lineage.get("schema_version") != expected_lineage_schema:
        raise ValueError("unsupported recovery-lineage schema")
    expected_hash = require_sha256(lineage, "lineage_hash")
    unhashed = dict(lineage)
    unhashed.pop("lineage_hash")
    if sha256_bytes(canonical_json(unhashed)) != expected_hash:
        raise ValueError("recovery-lineage semantic hash mismatch")
    successor = require_dict(lineage, "successor_execution")
    for key in (
        "directory",
        "schema_version",
        "object_kind",
        "execution_generation",
        "execution_id",
        "execution_hash",
    ):
        if successor.get(key) != execution_record.get(key):
            raise ValueError(f"recovery-lineage successor {key} mismatch")
    authority = require_dict(lineage, "recovery_authority")
    authority_hash = require_sha256(authority, "authority_hash")
    unhashed_authority = dict(authority)
    unhashed_authority.pop("authority_hash")
    expected_authority_schema = {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1: SUCCESSOR_AUTHORITY_SCHEMA_VERSION,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2: SUCCESSOR_AUTHORITY_SCHEMA_VERSION_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3: SUCCESSOR_AUTHORITY_SCHEMA_VERSION_V3,
    }[schema]
    if (
        authority.get("schema_version") != expected_authority_schema
        or sha256_bytes(canonical_json(unhashed_authority)) != authority_hash
        or authority_hash != execution_record.get("recovery_authority_hash")
    ):
        raise ValueError("recovery-lineage authority hash mismatch")
    zero = require_dict(authority, "zero_consumption")
    expected_zero = {
        "classification": "pre-simulation-control-plane-failure",
        "root_cause": "cross-node-control-lock-device-or-inode-mismatch",
        "accepted_incident_evidence": True,
        "events_consumed": 0,
        "production_seed_count": 64,
        "production_seeds_consumed": 0,
        "loader_completed": False,
        "apptainer_invoked": False,
        "geant4_invoked": False,
        "task_outputs_created": False,
        "root_outputs_created": False,
        "seed_reuse_authorized": True,
        "reuse_scope": "exact-32-task-phase2a-mapping-only",
    }
    if zero != expected_zero:
        raise ValueError("recovery-lineage does not prove exact zero consumption")
    if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
        incident = require_dict(authority, "incident")
    else:
        predecessor_authority = require_dict(
            authority, "predecessor_recovery_authority"
        )
        predecessor_authority_hash = require_sha256(
            predecessor_authority, "authority_hash"
        )
        unhashed_predecessor_authority = dict(predecessor_authority)
        unhashed_predecessor_authority.pop("authority_hash")
        expected_predecessor_authority_schema = (
            SUCCESSOR_AUTHORITY_SCHEMA_VERSION
            if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2
            else SUCCESSOR_AUTHORITY_SCHEMA_VERSION_V2
        )
        if (
            predecessor_authority.get("schema_version")
            != expected_predecessor_authority_schema
            or sha256_bytes(canonical_json(unhashed_predecessor_authority))
            != predecessor_authority_hash
            or predecessor_authority_hash
            != require_dict(authority, "predecessor_execution").get(
                "recovery_authority_hash"
            )
        ):
            raise ValueError("recovery-lineage predecessor authority mismatch")
        incident = require_dict(authority, "original_production_incident")
        predecessor_incident_key = (
            "incident"
            if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2
            else "original_production_incident"
        )
        if incident != require_dict(
            predecessor_authority, predecessor_incident_key
        ):
            raise ValueError("recovery-lineage original incident changed")
    recorded_incident = require_dict(lineage, "predecessor_incident")
    for key in (
        "directory",
        "incident_id",
        "incident_hash",
        "incident_json_sha256",
        "checksum_manifest_sha256",
    ):
        if recorded_incident.get(key) != incident.get(key):
            raise ValueError(f"recovery-lineage incident {key} mismatch")
    predecessor = require_dict(authority, "predecessor_execution")
    recorded_predecessor = require_dict(lineage, "predecessor_execution")
    for key in ("directory", "execution_id", "execution_hash"):
        if recorded_predecessor.get(key) != predecessor.get(key):
            raise ValueError(f"recovery-lineage predecessor {key} mismatch")
    expected_scope = {
        "source_execution_directory": execution_record.get("directory"),
        "selected_results": "successor-successes-only",
        "predecessor_failed_outputs_included": False,
        "predecessor_accounting_included": False,
    }
    if schema in {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    }:
        failure = require_dict(authority, "failed_r3_preflight")
        if lineage.get("failed_r3_preflight") != failure:
            raise ValueError("recovery-lineage failed-R3 binding changed")
        failure_runtime = require_dict(failure, "runtime_boundary")
        failure_consumption = require_dict(failure, "consumption")
        failure_scheduler = require_dict(failure, "scheduler")
        if (
            failure_runtime.get("apptainer_invoked") is not False
            or failure_runtime.get("geant4_invoked") is not False
            or failure_consumption
            != {"events_consumed": 0, "production_seeds_consumed": 0}
            or not str(failure_scheduler.get("job_id", "")).isdigit()
        ):
            raise ValueError("recovery-lineage failed-R3 boundary is invalid")
        original_authority = require_dict(
            authority, "predecessor_recovery_authority"
        )
        if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3:
            original_authority = require_dict(
                original_authority, "predecessor_recovery_authority"
            )
        original_attempt = require_dict(original_authority, "predecessor_attempt")
        if (
            not isinstance(original_attempt.get("attempt_id"), str)
            or not original_attempt["attempt_id"]
            or not str(original_attempt.get("job_id", "")).isdigit()
        ):
            raise ValueError("recovery-lineage original attempt is invalid")
        expected_scope.update(
            {
                "failed_r3_preflight_outputs_included": False,
                "failed_r3_accounting_included": False,
            }
        )
    if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3:
        rejected = require_dict(authority, "rejected_r3_preflight")
        administrative = require_dict(
            authority, "administrative_r3_launch_rejection"
        )
        if lineage.get("rejected_r3_preflight") != rejected:
            raise ValueError("recovery-lineage rejected-R3 binding changed")
        if lineage.get("administrative_r3_launch_rejection") != administrative:
            raise ValueError(
                "recovery-lineage administrative R3 binding changed"
            )
        rejected_scheduler = require_dict(rejected, "scheduler")
        rejected_probe = require_dict(rejected, "probe")
        rejected_consumption = require_dict(rejected, "consumption")
        if (
            rejected.get("accepted_compute_preflight_evidence") is not False
            or not isinstance(rejected.get("classification"), str)
            or not rejected["classification"]
            or not str(rejected_scheduler.get("job_id", "")).isdigit()
            or rejected_probe.get("apptainer_invoked") is not True
            or rejected_probe.get("geant4_invoked") is not False
            or rejected_probe.get("raw_file_snapshot_scope")
            != "recursive-regular-files-only"
            or rejected_probe.get("raw_file_snapshot_unchanged") is not True
            or rejected_probe.get("historical_mountpoint_present") is not True
            or rejected_probe.get("execution_tree_unchanged") is not False
            or rejected_consumption
            != {"events_consumed": 0, "production_seeds_consumed": 0}
        ):
            raise ValueError("recovery-lineage rejected-R3 boundary is invalid")
        if (
            administrative.get("compute_preflight") is not False
            or administrative.get("apptainer_invoked") is not False
            or administrative.get("geant4_invoked") is not False
            or administrative.get("events_consumed") != 0
            or administrative.get("production_seeds_consumed") != 0
            or not str(administrative.get("job_id", "")).isdigit()
        ):
            raise ValueError(
                "recovery-lineage administrative R3 boundary is invalid"
            )
        expected_scope.update(
            {
                "rejected_r3_preflight_outputs_included": False,
                "rejected_r3_accounting_included": False,
                "administrative_r3_launch_rejection_included_as_compute": False,
            }
        )
    scope = require_dict(lineage, "result_scope")
    if scope != expected_scope:
        raise ValueError("recovery-lineage result scope is unsafe")
    return lineage


def recovery_lineage_exclusions(
    lineage: dict[str, Any],
) -> tuple[set[str], set[str]]:
    """Return immutable failed attempt/job identities excluded downstream."""

    if lineage.get("schema_version") == RECOVERY_LINEAGE_SCHEMA_VERSION_V4:
        attempts_raw = lineage.get("excluded_attempt_ids")
        jobs_raw = lineage.get("excluded_job_ids")
        if not isinstance(attempts_raw, list) or not isinstance(jobs_raw, list):
            raise ValueError("Phase-2C recovery-lineage exclusions are missing")
        attempts = set(attempts_raw)
        jobs = set(jobs_raw)
    else:
        authority = require_dict(lineage, "recovery_authority")
    if lineage.get("schema_version") == RECOVERY_LINEAGE_SCHEMA_VERSION:
        attempt = require_dict(authority, "predecessor_attempt")
        attempts = {require_string(attempt, "attempt_id")}
        jobs = {str(attempt.get("job_id", ""))}
    elif lineage.get("schema_version") == RECOVERY_LINEAGE_SCHEMA_VERSION_V2:
        predecessor_authority = require_dict(
            authority, "predecessor_recovery_authority"
        )
        attempt = require_dict(predecessor_authority, "predecessor_attempt")
        failed_r3 = require_dict(authority, "failed_r3_preflight")
        attempts = {require_string(attempt, "attempt_id")}
        jobs = {
            str(attempt.get("job_id", "")),
            str(require_dict(failed_r3, "scheduler").get("job_id", "")),
        }
    elif lineage.get("schema_version") == RECOVERY_LINEAGE_SCHEMA_VERSION_V3:
        predecessor_authority = require_dict(
            authority, "predecessor_recovery_authority"
        )
        original_authority = require_dict(
            predecessor_authority, "predecessor_recovery_authority"
        )
        attempt = require_dict(original_authority, "predecessor_attempt")
        failed_r3 = require_dict(authority, "failed_r3_preflight")
        rejected_r3 = require_dict(authority, "rejected_r3_preflight")
        administrative_r3 = require_dict(
            authority, "administrative_r3_launch_rejection"
        )
        attempts = {require_string(attempt, "attempt_id")}
        jobs = {
            str(attempt.get("job_id", "")),
            str(require_dict(failed_r3, "scheduler").get("job_id", "")),
            str(require_dict(rejected_r3, "scheduler").get("job_id", "")),
            str(administrative_r3.get("job_id", "")),
        }
    elif lineage.get("schema_version") != RECOVERY_LINEAGE_SCHEMA_VERSION_V4:
        raise ValueError("unsupported recovery-lineage exclusion schema")
    if any(not isinstance(value, str) or not value for value in attempts):
        raise ValueError("recovery-lineage excluded attempt is invalid")
    if any(not value or not value.isdigit() for value in jobs):
        raise ValueError("recovery-lineage excluded scheduler job is invalid")
    return attempts, jobs


def _tasks_from_finalized_rows(
    rows: Iterable[dict[str, str]],
) -> tuple[CampaignTask, ...]:
    return tuple(
        CampaignTask(
            task_index=_require_int(row, "task_index"),
            logical_task_id=row["logical_task_id"],
            stage=row["stage"],
            tile_thickness_mm=_require_int(row, "tile_thickness_mm"),
            sipm_layout=row["sipm_layout"],
            absorber_transverse_mm=_require_int(row, "absorber_transverse_mm"),
            x_mm=_require_int(row, "x_mm"),
            y_mm=_require_int(row, "y_mm"),
            seed_block=_require_int(row, "seed_block"),
            events=_require_int(row, "events"),
            seed1=_require_int(row, "seed1"),
            seed2=_require_int(row, "seed2"),
            configuration_hash=row["configuration_hash"],
        )
        for row in rows
    )


def _load_canonical_successor_for_finalization(
    execution_record: dict[str, Any],
) -> Any:
    """Reload the exact frozen successor named by formal finalization evidence."""

    raw_directory = require_string(execution_record, "directory")
    requested = Path(raw_directory)
    if not requested.is_absolute() or requested.is_symlink():
        raise ValueError("formal successor execution directory is unsafe")
    directory = requested.resolve()
    if str(directory) != raw_directory:
        raise ValueError("formal successor execution directory is not canonical")
    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if execution_record.get("schema_version") == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_lib import load_execution_v6

        execution = load_execution_v6(
            directory,
            repo_root=directory / "sources/control",
            allow_test_mode=False,
            require_readiness=False,
            verify_runtime=True,
            verify_live_predecessor=False,
        )
    else:
        from steel_module_production_successor_lib import load_successor_execution

        execution = load_successor_execution(
            directory,
            repo_root=directory / "sources/control",
            allow_test_mode=False,
            require_readiness=False,
            verify_runtime=True,
            verify_phase2a_control_plane=False,
            verify_live_predecessor=False,
            allow_closed_v2=True,
        )
    expected = {
        "directory": str(execution.directory),
        "schema_version": execution.manifest.get("schema_version"),
        "object_kind": execution.manifest.get("object_kind"),
        "execution_generation": execution.manifest.get("execution_generation"),
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
    }
    for key, value in expected.items():
        if execution_record.get(key) != value:
            raise ValueError(f"formal finalization execution {key} mismatch")
    if execution_record.get("schema_version") == EXECUTION_V6_SCHEMA_VERSION:
        phase2c_authority = require_dict(
            execution.manifest, "phase2c_authority"
        )
        if (
            execution_record.get("production_equivalence_hash")
            != execution.manifest.get("production_equivalence_hash")
            or execution_record.get("phase2c_authority_hash")
            != phase2c_authority.get("authority_hash")
        ):
            raise ValueError("formal finalization Phase-2C authority mismatch")
    else:
        authority = require_dict(execution.manifest, "recovery_authority")
        if execution_record.get("recovery_authority_hash") != authority.get(
            "authority_hash"
        ):
            raise ValueError("formal finalization recovery authority mismatch")
    return execution


def _validate_canonical_selected_artifacts(
    execution: Any, rows: tuple[dict[str, str], ...]
) -> None:
    """Rebind every selected row to its immutable canonical task marker."""

    tasks = _tasks_from_finalized_rows(rows)
    if tasks != execution.tasks:
        raise ValueError("formal finalization task rows differ from execution plan")
    for row in rows:
        attempt_id = row.get("attempt_id", "")
        logical_id = row.get("logical_task_id", "")
        if not attempt_id or not logical_id:
            raise ValueError("formal finalization task identity is incomplete")
        marker_path = (
            execution.directory
            / "attempts"
            / attempt_id
            / "tasks"
            / logical_id
            / "task_result.json"
        ).resolve()
        marker = load_managed_task_result(execution, attempt_id, logical_id)
        if (
            row.get("task_result") != str(marker_path)
            or marker_path.is_symlink()
            or not marker_path.is_file()
            or row.get("task_result_sha256") != sha256_file(marker_path)
        ):
            raise ValueError("formal finalization task-result binding mismatch")
        artifacts = require_dict(marker, "artifacts")
        for label in (
            "run_config",
            "macro",
            "simulation_log",
            "root",
            "summary",
            "efficiency_map",
        ):
            record = require_dict(artifacts, label)
            relative = Path(require_string(record, "path"))
            expected_path = (execution.directory / relative).resolve()
            if (
                row.get(label) != str(expected_path)
                or row.get(f"{label}_sha256") != record.get("sha256")
                or expected_path.is_symlink()
                or not expected_path.is_file()
                or sha256_file(expected_path) != record.get("sha256")
            ):
                raise ValueError(
                    f"formal finalization canonical artifact mismatch: {label}"
                )


def _validate_canonical_pilot_seed_registry(
    execution: Any, seed_audit: dict[str, Any]
) -> None:
    """Recompute the sealed-pilot exclusion set from the canonical program."""

    program_record = require_dict(execution.managed_child.binding, "program")
    program_dir = Path(require_string(program_record, "program_directory"))
    program = load_production_program(
        program_dir, verify_runtime_artifacts=False
    )
    if program.child_tasks.get("BC-S1") != execution.tasks:
        raise ValueError("formal finalization differs from canonical BC-S1 registry")
    pilot_seeds = tuple(record.seed for record in program.excluded_seeds)
    if (
        len(pilot_seeds) != 240
        or len(set(pilot_seeds)) != 240
        or seed_audit.get("sealed_pilot_seed_count") != 240
        or seed_audit.get("sealed_pilot_unique_seed_count") != 240
        or seed_audit.get("sealed_pilot_seed_set_hash")
        != seed_set_hash(pilot_seeds)
    ):
        raise ValueError("formal finalization sealed-pilot seed registry mismatch")


def _canonical_successful_array_indexes(
    execution: Any,
    attempt_id: str,
    intent: dict[str, Any],
    accounting: dict[str, Any],
) -> set[int]:
    """Recompute successful array indexes from canonical intent/accounting bytes."""

    selected = require_dict(intent, "selected")
    logical_ids = selected.get("logical_task_ids")
    task_count = selected.get("task_count")
    if (
        not isinstance(logical_ids, list)
        or not all(isinstance(value, str) and value for value in logical_ids)
        or not isinstance(task_count, int)
        or isinstance(task_count, bool)
        or task_count < 1
        or len(logical_ids) != task_count
        or len(set(logical_ids)) != task_count
    ):
        raise ValueError("canonical intent selected-task order is invalid")
    job_id = str(accounting.get("job_id", ""))
    if not job_id.isdigit() or accounting.get("accepted_terminal") is not True:
        raise ValueError("canonical accounting is not accepted terminal evidence")
    expected_indexes = set(range(1, task_count + 1))
    expected_keys = {str(index) for index in expected_indexes}
    task_states = accounting.get("task_states")
    task_exit_codes = accounting.get("task_exit_codes")
    if (
        not isinstance(task_states, dict)
        or not isinstance(task_exit_codes, dict)
        or set(task_states) != expected_keys
        or set(task_exit_codes) != expected_keys
    ):
        raise ValueError("canonical accounting task maps are incomplete")

    field_order = accounting.get("sacct_field_order")
    if field_order == [
        "JobID",
        "JobIDRaw",
        "State",
        "ExitCode",
        "ElapsedRaw",
        "MaxRSS",
        "MaxVMSize",
    ]:
        fields = tuple(field_order)
        logical_field = "JobID"
        raw_field = "JobIDRaw"
        task_job_ids = accounting.get("task_job_ids")
        task_job_ids_raw = accounting.get("task_job_ids_raw")
        if (
            not isinstance(task_job_ids, dict)
            or not isinstance(task_job_ids_raw, dict)
            or set(task_job_ids) != expected_keys
            or set(task_job_ids_raw) != expected_keys
        ):
            raise ValueError("canonical accounting job-ID maps are incomplete")
    elif field_order is None:
        fields = (
            "JobIDRaw",
            "State",
            "ExitCode",
            "ElapsedRaw",
            "MaxRSS",
            "MaxVMSize",
        )
        logical_field = "JobIDRaw"
        raw_field = "JobIDRaw"
        task_job_ids = task_job_ids_raw = None
    else:
        raise ValueError("canonical accounting sacct field order is unsupported")

    sacct_path = (
        execution.directory / "attempts" / attempt_id / "accounting" / "sacct.psv"
    )
    if sacct_path.is_symlink() or not sacct_path.is_file():
        raise ValueError("canonical accounting sacct evidence is missing")
    rows: list[dict[str, str]] = []
    for number, raw in enumerate(
        sacct_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw:
            continue
        values = raw.split("|")
        if values and values[-1] == "":
            values.pop()
        if len(values) != len(fields):
            raise ValueError(f"invalid canonical sacct row {number}: {raw!r}")
        rows.append(dict(zip(fields, values)))
    task_pattern = re.compile(rf"{re.escape(job_id)}_([1-9][0-9]*)")
    task_rows: dict[int, dict[str, str]] = {}
    for row in rows:
        match = task_pattern.fullmatch(row[logical_field])
        if match is None:
            continue
        index = int(match.group(1))
        if index in task_rows:
            raise ValueError("canonical accounting has duplicate array-task rows")
        task_rows[index] = row
    if set(task_rows) != expected_indexes:
        raise ValueError("canonical accounting lacks the exact array-task set")

    successful: set[int] = set()
    for index, row in task_rows.items():
        key = str(index)
        if task_states[key] != row["State"] or task_exit_codes[key] != row["ExitCode"]:
            raise ValueError("canonical accounting maps disagree with sacct rows")
        if field_order is not None and (
            task_job_ids[key] != row[logical_field]
            or task_job_ids_raw[key] != row[raw_field]
        ):
            raise ValueError("canonical accounting job IDs disagree with sacct rows")
        if base_slurm_state(row["State"]) == "COMPLETED" and row["ExitCode"] == "0:0":
            successful.add(index)
    return successful


def _successor_task_identity(tasks: tuple[CampaignTask, ...]) -> dict[str, Any]:
    seeds = tuple(seed for task in tasks for seed in (task.seed1, task.seed2))
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


def validate_successor_seed_lineage(
    execution_record: dict[str, Any],
    lineage: dict[str, Any] | None,
    rows: Iterable[dict[str, str]],
    seed_audit: dict[str, Any],
    *,
    checkpoint_record: dict[str, Any] | None = None,
) -> None:
    """Recompute every successor task/seed identity from the selected rows."""

    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    schema = execution_record.get("schema_version")
    if schema not in {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
        EXECUTION_V6_SCHEMA_VERSION,
    }:
        return
    if lineage is None:
        raise ValueError("successor seed audit lacks recovery lineage")
    tasks = _tasks_from_finalized_rows(rows)
    identity = _successor_task_identity(tasks)
    if schema == EXECUTION_V6_SCHEMA_VERSION:
        if lineage.get("schema_version") != RECOVERY_LINEAGE_SCHEMA_VERSION_V4:
            raise ValueError("execution-v6 seed audit lacks Phase-2C lineage")
    else:
        authority = require_dict(lineage, "recovery_authority")
        retry = require_dict(authority, "retry_equivalence")
        for key, expected in identity.items():
            if retry.get(key) != expected:
                raise ValueError(f"successor retry-equivalence {key} mismatch")
        if retry.get("all_equal") is not True:
            raise ValueError("successor retry-equivalence is not accepted")
    for key in ("task_set_hash", "task_seed_mapping_hash", "seed_set_hash"):
        if execution_record.get(key) != identity[key]:
            raise ValueError(f"successor execution {key} mismatch")
    if checkpoint_record is not None:
        for checkpoint_key, identity_key in (
            ("task_set_hash", "task_set_hash"),
            ("task_seed_mapping_hash", "task_seed_mapping_hash"),
            ("production_seed_set_hash", "seed_set_hash"),
        ):
            if checkpoint_record.get(checkpoint_key) != identity[identity_key]:
                raise ValueError(f"checkpoint {checkpoint_key} mismatch")
    expected_seed_tasks = [
        {
            "logical_task_id": task.logical_task_id,
            "seed_block": task.seed_block,
            "seed1": task.seed1,
            "seed2": task.seed2,
        }
        for task in tasks
    ]
    expected_seed_fields = {
        "schema_version": "steel-module-managed-seed-audit-v1",
        "valid": True,
        "production_seed_count": 64,
        "production_unique_seed_count": 64,
        "production_seed_set_hash": identity["seed_set_hash"],
        "sealed_pilot_seed_count": 240,
        "sealed_pilot_unique_seed_count": 240,
        "overlap_count": 0,
        "overlap": [],
        "parent_registry_reconciled": True,
        "phase2a_plan_reconciled": True,
        "run_config_reconciled": True,
        "task_result_reconciled": True,
        "tasks": expected_seed_tasks,
    }
    for key, expected in expected_seed_fields.items():
        if seed_audit.get(key) != expected:
            raise ValueError(f"successor seed audit {key} mismatch")


def validate_successor_selection_and_accounting(
    directory: Path,
    validation: dict[str, Any],
    execution_record: dict[str, Any],
    lineage: dict[str, Any] | None,
    rows: tuple[dict[str, str], ...],
    selection: dict[str, Any],
    *,
    canonical_execution: Any | None = None,
) -> None:
    """Bind selected rows and every copied accounting snapshot to the successor."""

    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if execution_record.get("schema_version") not in {
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
        EXECUTION_V6_SCHEMA_VERSION,
    }:
        return
    if lineage is None:
        raise ValueError("successor selection lacks recovery lineage")
    forbidden_attempts, forbidden_jobs = recovery_lineage_exclusions(lineage)
    expected_selected = {
        row["logical_task_id"]: row["attempt_id"] for row in rows
    }
    explicit = selection.get("explicit_selection")
    if not isinstance(explicit, dict):
        raise ValueError("successor selection explicit map is invalid")
    if (
        selection.get("schema_version") != "steel-module-managed-selection-v1"
        or selection.get("selected_task_count") != 32
        or selection.get("source_execution_id") != execution_record.get("execution_id")
        or selection.get("source_execution_directory")
        != execution_record.get("directory")
        or selection.get("selected_results") != "successor-successes-only"
        or selection.get("predecessor_failed_outputs_included") is not False
        or selection.get("selected_attempts") != expected_selected
        or selection.get("duplicate_successes_resolved") != sorted(explicit)
    ):
        raise ValueError("successor selection record is inconsistent")
    selected_attempts = set(expected_selected.values())
    if selected_attempts & forbidden_attempts:
        raise ValueError("successor selection contains predecessor attempt")
    selected_jobs_by_attempt: dict[str, set[str]] = {}
    selected_rows_by_attempt: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        attempt_id = row["attempt_id"]
        job_id = row["slurm_job_id"]
        if job_id in forbidden_jobs:
            raise ValueError("successor selection contains predecessor scheduler job")
        selected_jobs_by_attempt.setdefault(attempt_id, set()).add(job_id)
        selected_rows_by_attempt.setdefault(attempt_id, []).append(row)
    accounting_attempts = validation.get("accounting_attempts")
    if (
        not isinstance(accounting_attempts, list)
        or any(not isinstance(value, str) or not value for value in accounting_attempts)
        or len(set(accounting_attempts)) != len(accounting_attempts)
        or not selected_attempts.issubset(set(accounting_attempts))
        or bool(set(accounting_attempts) & forbidden_attempts)
    ):
        raise ValueError("successor accounting-attempt registry is inconsistent")
    accounting_root = directory / "accounting"
    if accounting_root.is_symlink() or not accounting_root.is_dir():
        raise ValueError("successor finalization lacks accounting evidence")
    actual_attempts = {
        path.name
        for path in accounting_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    if actual_attempts != set(accounting_attempts):
        raise ValueError("successor copied accounting set is inconsistent")
    for attempt_id in accounting_attempts:
        copied_accounting = accounting_root / attempt_id
        frozen = load_json(copied_accounting / "frozen.json")
        job_id = str(frozen.get("job_id", ""))
        if frozen.get("attempt_id") != attempt_id or not job_id.isdigit():
            raise ValueError("successor copied accounting identity is invalid")
        if job_id in forbidden_jobs:
            raise ValueError("successor copied predecessor scheduler accounting")
        expected_jobs = selected_jobs_by_attempt.get(attempt_id)
        if expected_jobs is not None and expected_jobs != {job_id}:
            raise ValueError("successor selected rows/accounting job mismatch")
        if canonical_execution is not None:
            intent = load_attempt_intent(canonical_execution, attempt_id)
            selected = require_dict(intent, "selected")
            logical_ids = selected.get("logical_task_ids")
            if not isinstance(logical_ids, list) or not all(
                isinstance(value, str) and value for value in logical_ids
            ):
                raise ValueError("canonical intent logical-task order is invalid")
            array_index_by_id = {
                logical_id: index
                for index, logical_id in enumerate(logical_ids, start=1)
            }
            if len(array_index_by_id) != len(logical_ids):
                raise ValueError("canonical intent logical-task order has duplicates")
            canonical = load_frozen_accounting(
                canonical_execution, attempt_id, require_event_binding=True
            )
            canonical_accounting = (
                canonical_execution.directory
                / "attempts"
                / attempt_id
                / "accounting"
            )
            verify_recursive_checksums(
                copied_accounting,
                required={"frozen.json", "sacct.psv", "squeue.psv"},
            )
            if (
                frozen != canonical
                or recursive_file_records(copied_accounting, exclude=())
                != recursive_file_records(canonical_accounting, exclude=())
            ):
                raise ValueError(
                    "successor copied accounting differs from canonical evidence"
                )
            successful_indexes = _canonical_successful_array_indexes(
                canonical_execution, attempt_id, intent, canonical
            )
            for row in selected_rows_by_attempt.get(attempt_id, ()):
                logical_id = row["logical_task_id"]
                expected_index = array_index_by_id.get(logical_id)
                recorded_index = _require_int(row, "slurm_array_index")
                if expected_index is None or recorded_index != expected_index:
                    raise ValueError(
                        "successor row array index differs from canonical intent"
                    )
                if recorded_index not in successful_indexes:
                    raise ValueError(
                        "successor selected row is not a canonical successful array task"
                    )


def readiness_identity_for_execution(
    execution: Any, *, repo_root: Path
) -> dict[str, Any]:
    """Return the readiness identity appropriate to this execution generation."""

    if execution.manifest.get("test_mode") is True:
        return {"required": False, "lock_sha256": None}
    from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION

    if execution.manifest.get("schema_version") == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_readiness import (
            verify_phase2c_readiness,
        )

        path, value = verify_phase2c_readiness(execution, repo_root=repo_root)
        return {
            "required": True,
            "authority": "phase2c-r6",
            "path": str(path),
            "schema_version": value.get("schema_version"),
            "lock_sha256": sha256_file(path),
            "readiness_hash": value.get("readiness_hash"),
            "production_equivalence_hash": execution.manifest.get(
                "production_equivalence_hash"
            ),
        }
    if is_successor_execution_manifest(execution.manifest):
        from steel_module_production_successor_lib import (
            verify_successor_recovery_readiness,
        )

        path, value = verify_successor_recovery_readiness(
            execution, repo_root=repo_root
        )
        authority = require_dict(execution.manifest, "recovery_authority")
        schema = execution.manifest.get("schema_version")
        incident = require_dict(
            authority,
            "incident"
            if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1
            else "original_production_incident",
        )
        identity = {
            "required": True,
            "authority": "recovery-successor",
            "path": str(path),
            "schema_version": value.get("schema_version"),
            "lock_sha256": sha256_file(path),
            "readiness_hash": value.get("readiness_hash"),
            "recovery_authority_hash": authority.get("authority_hash"),
            "predecessor_incident_id": incident.get("incident_id"),
            "predecessor_incident_hash": incident.get("incident_hash"),
        }
        if schema in {
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
        }:
            failed_r3 = require_dict(authority, "failed_r3_preflight")
            identity.update(
                {
                    "failed_r3_id": failed_r3.get("failure_id"),
                    "failed_r3_hash": failed_r3.get("failure_hash"),
                    "failed_r3_job_id": require_dict(
                        failed_r3, "scheduler"
                    ).get("job_id"),
                }
            )
        if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3:
            rejected_r3 = require_dict(authority, "rejected_r3_preflight")
            administrative_r3 = require_dict(
                authority, "administrative_r3_launch_rejection"
            )
            identity.update(
                {
                    "rejected_r3_id": rejected_r3.get("rejection_id"),
                    "rejected_r3_hash": rejected_r3.get("rejection_hash"),
                    "rejected_r3_job_id": require_dict(
                        rejected_r3, "scheduler"
                    ).get("job_id"),
                    "administrative_r3_job_id": administrative_r3.get(
                        "job_id"
                    ),
                    "administrative_r3_compute_preflight": False,
                }
            )
        return identity
    path = repo_root.resolve() / READINESS_LOCK_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("formal finalization lacks a regular readiness lock")
    return {
        "required": True,
        "path": str(path),
        "lock_sha256": sha256_file(path),
    }


def _safe_relative_name(raw: str) -> Path:
    relative = Path(raw)
    if (
        not raw
        or relative.is_absolute()
        or ".." in relative.parts
        or str(relative) != raw
    ):
        raise ValueError(f"unsafe checksum-manifest path: {raw!r}")
    return relative


def recursive_file_records(
    directory: Path, *, exclude: Iterable[str] = ("SHA256SUMS",)
) -> dict[str, str]:
    """Return the exact recursive regular-file set, rejecting all symlinks."""

    root = directory.resolve()
    excluded = set(exclude)
    records: dict[str, str] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"symlink is forbidden in evidence tree: {path}")
        for name in files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in excluded:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"non-regular evidence file: {path}")
            records[relative] = sha256_file(path)
    return dict(sorted(records.items()))


def write_recursive_checksums(directory: Path) -> None:
    records = recursive_file_records(directory)
    text = "".join(f"{digest}  {name}\n" for name, digest in records.items())
    path = directory / "SHA256SUMS"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o444)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _rename_directory_no_replace(temporary: Path, target: Path) -> None:
    """Atomically rename one directory while refusing an existing target.

    Python's :func:`os.rename` may replace an empty destination directory, so
    an existence check followed by ``os.rename`` is not a no-replace
    primitive.  Linux/OSC provides ``renameat2(RENAME_NOREPLACE)`` and macOS
    provides ``renamex_np(RENAME_EXCL)``.  Unsupported platforms fail closed.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(temporary)
    destination = os.fsencode(target)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, source, -100, destination, 1)
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(source, destination, 0x00000004)
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-replace directory publication is unsupported",
            str(target),
        )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error, os.strerror(error), str(target))
    raise OSError(error, os.strerror(error), str(target))


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _directory_identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


def _stat_name_no_follow(parent_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)


def _require_name_absent(parent_fd: int, name: str, *, target: Path) -> None:
    try:
        _stat_name_no_follow(parent_fd, name)
    except FileNotFoundError:
        return
    raise ValueError(f"refusing to overwrite evidence directory: {target}")


def _authenticated_directory_identity(
    parent_fd: int, name: str, *, label: str
) -> tuple[int, int]:
    result = _stat_name_no_follow(parent_fd, name)
    if not stat.S_ISDIR(result.st_mode):
        raise ValueError(f"{label} is not a directory")
    identity = _directory_identity(result)
    descriptor = os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_identity(opened) != identity
        ):
            raise ValueError(f"{label} changed while it was opened")
    finally:
        os.close(descriptor)
    return identity


def _plain_directory_rename(
    source_parent_fd: int,
    source_name: str,
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    """POSIX rename seam used only by the guarded GPFS fallback."""

    os.rename(
        source_name,
        destination_name,
        src_dir_fd=source_parent_fd,
        dst_dir_fd=destination_parent_fd,
    )


def _validate_published_directory(
    parent_fd: int,
    *,
    source_name: str,
    target_name: str,
    source_identity: tuple[int, int],
) -> int:
    """Return an authenticated target fd after proving the source moved."""

    try:
        _stat_name_no_follow(parent_fd, source_name)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("published source directory still exists")
    target_result = _stat_name_no_follow(parent_fd, target_name)
    if (
        not stat.S_ISDIR(target_result.st_mode)
        or _directory_identity(target_result) != source_identity
    ):
        raise ValueError("published target is not the original source directory")
    target_fd = os.open(target_name, _directory_open_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(target_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_identity(opened) != source_identity
        ):
            raise ValueError("published target changed while it was opened")
    except BaseException:
        os.close(target_fd)
        raise
    return target_fd


def _publish_directory_with_owned_placeholder(
    parent_fd: int,
    *,
    temporary: Path,
    target: Path,
    source_identity: tuple[int, int],
) -> None:
    """GPFS fallback for an unsupported ``RENAME_NOREPLACE`` flag.

    The caller holds the exclusive sibling publication lock.  We create the
    target placeholder with ``mkdir`` (the kernel no-replace primitive that
    GPFS does support), authenticate both source and placeholder through the
    pinned parent descriptor, and then atomically replace only that owned,
    empty placeholder with POSIX ``rename``.

    This is intentionally a cooperative-writer protocol.  A process running
    as the same Unix identity that ignores the sibling lock could swap an
    empty placeholder in the final syscall race; GPFS offers no directory
    compare-and-swap primitive to exclude that actor.  Any detectable race or
    failure leaves residue in place and is never rolled back.
    """

    if not sys.platform.startswith("linux"):
        raise OSError(
            errno.ENOTSUP,
            "exclusive-placeholder publication is Linux-only",
            str(target),
        )
    _require_name_absent(parent_fd, target.name, target=target)
    os.mkdir(target.name, 0o700, dir_fd=parent_fd)
    placeholder_fd: int | None = None
    try:
        placeholder_result = _stat_name_no_follow(parent_fd, target.name)
        placeholder_identity = _directory_identity(placeholder_result)
        if not stat.S_ISDIR(placeholder_result.st_mode):
            raise ValueError("publication placeholder is not a directory")
        placeholder_fd = os.open(
            target.name, _directory_open_flags(), dir_fd=parent_fd
        )
        opened_placeholder = os.fstat(placeholder_fd)
        if (
            not stat.S_ISDIR(opened_placeholder.st_mode)
            or _directory_identity(opened_placeholder) != placeholder_identity
            or os.listdir(placeholder_fd)
        ):
            raise ValueError("publication placeholder is not the owned empty directory")

        current_source = _stat_name_no_follow(parent_fd, temporary.name)
        if (
            not stat.S_ISDIR(current_source.st_mode)
            or _directory_identity(current_source) != source_identity
        ):
            raise ValueError("publication source changed before GPFS fallback")
        current_placeholder = _stat_name_no_follow(parent_fd, target.name)
        if (
            not stat.S_ISDIR(current_placeholder.st_mode)
            or _directory_identity(current_placeholder) != placeholder_identity
            or os.listdir(placeholder_fd)
        ):
            raise ValueError("publication placeholder changed before GPFS fallback")

        os.fsync(placeholder_fd)
        os.fsync(parent_fd)
        _plain_directory_rename(
            parent_fd,
            temporary.name,
            parent_fd,
            target.name,
        )
    finally:
        if placeholder_fd is not None:
            # GPFS may report ESTALE for this descriptor after its empty inode
            # has been replaced.  Its pre-rename identity is the ownership
            # proof; post-rename correctness is established from the source
            # inode at the target path below.
            os.close(placeholder_fd)


def publish_directory_no_replace(temporary: Path, target: Path) -> None:
    """Publish a completed sibling tree without an internal writer race.

    The exclusive sibling lock makes concurrent Phase-2B writers fail closed.
    A stale lock is intentionally not guessed away after a process crash.

    Linux filesystems that reject ``RENAME_NOREPLACE`` with an unsupported-
    operation errno use an exclusive-placeholder fallback.  That fallback is
    safe for cooperating writers that honor this lock; it cannot provide a
    kernel no-overwrite guarantee against an uncooperative process with the
    same Unix identity.  Failures leave visible residue and are not rolled
    back.
    """

    if temporary.parent != target.parent:
        raise ValueError("atomic publication requires sibling directories")
    lock = target.parent / f".{target.name}.publish.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"publication is already active or quarantined: {lock}") from exc
    try:
        parent_fd = os.open(target.parent, _directory_open_flags())
        try:
            parent_result = os.fstat(parent_fd)
            visible_parent = target.parent.lstat()
            if (
                not stat.S_ISDIR(parent_result.st_mode)
                or _directory_identity(parent_result)
                != _directory_identity(visible_parent)
            ):
                raise ValueError("publication parent changed while it was opened")
            _require_name_absent(parent_fd, target.name, target=target)
            source_identity = _authenticated_directory_identity(
                parent_fd,
                temporary.name,
                label="publication source",
            )
            try:
                _rename_directory_no_replace(temporary, target)
            except FileExistsError as exc:
                raise ValueError(
                    f"refusing to overwrite evidence directory: {target}"
                ) from exc
            except OSError as exc:
                unsupported = {
                    errno.EINVAL,
                    errno.ENOSYS,
                    errno.EOPNOTSUPP,
                }
                if (
                    not sys.platform.startswith("linux")
                    or exc.errno not in unsupported
                ):
                    raise
                _publish_directory_with_owned_placeholder(
                    parent_fd,
                    temporary=temporary,
                    target=target,
                    source_identity=source_identity,
                )

            target_fd = _validate_published_directory(
                parent_fd,
                source_name=temporary.name,
                target_name=target.name,
                source_identity=source_identity,
            )
            try:
                os.fsync(target_fd)
            finally:
                os.close(target_fd)
            os.fsync(parent_fd)
            final_visible_parent = target.parent.lstat()
            if _directory_identity(final_visible_parent) != _directory_identity(
                parent_result
            ):
                raise ValueError("publication parent changed after publication")
            final_fd = _validate_published_directory(
                parent_fd,
                source_name=temporary.name,
                target_name=target.name,
                source_identity=source_identity,
            )
            os.close(final_fd)
        finally:
            os.close(parent_fd)
    finally:
        lock.rmdir()


def verify_recursive_checksums(
    directory: Path, *, required: Iterable[str] = ()
) -> dict[str, str]:
    """Verify both digest values and the complete recursive file set."""

    root = directory.expanduser()
    if root.is_symlink():
        raise ValueError(f"evidence directory must not be a symlink: {root}")
    root = root.resolve()
    manifest_path = root / "SHA256SUMS"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"missing regular checksum manifest: {manifest_path}")
    recorded: dict[str, str] = {}
    for line_number, raw in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        pieces = raw.split("  ", 1)
        if len(pieces) != 2:
            raise ValueError(f"invalid SHA256SUMS row {line_number}: {raw!r}")
        digest, name = pieces
        relative = _safe_relative_name(name)
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or name in recorded
            or name == "SHA256SUMS"
        ):
            raise ValueError(f"invalid SHA256SUMS identity: {raw!r}")
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"checksum mismatch: {name}")
        recorded[name] = digest
    actual = recursive_file_records(root)
    if actual != recorded:
        missing = sorted(set(recorded) - set(actual))
        unexpected = sorted(set(actual) - set(recorded))
        raise ValueError(
            "recursive checksum file set mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )
    absent = sorted(set(required) - set(recorded))
    if absent:
        raise ValueError(f"checksum manifest lacks required files: {absent}")
    return recorded


def read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = tuple(reader)
    if not reader.fieldnames:
        raise ValueError(f"TSV has no header: {path}")
    return rows


def _require_int(row: dict[str, str], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"task index has invalid {key}: {row.get(key)!r}") from exc


def validate_bc_only_s1_rows(
    rows: Iterable[dict[str, str]], *, verify_root_files: bool
) -> tuple[dict[str, str], ...]:
    """Enforce the exact formal BC-S1 prefix and all referenced ROOT hashes."""

    task_rows = tuple(rows)
    if len(task_rows) != 32:
        raise ValueError(f"BC-ONLY-S1 requires exactly 32 tasks, got {len(task_rows)}")
    logical_ids = [row.get("logical_task_id", "") for row in task_rows]
    if any(not value for value in logical_ids) or len(set(logical_ids)) != 32:
        raise ValueError("checkpoint logical task IDs must be non-empty and unique")
    if [ _require_int(row, "task_index") for row in task_rows ] != list(range(1, 33)):
        raise ValueError("BC-ONLY-S1 task indexes must be contiguous 1..32")

    seen_config_blocks: set[tuple[int, int]] = set()
    production_seeds: list[int] = []
    for row in task_rows:
        thickness = _require_int(row, "tile_thickness_mm")
        block = _require_int(row, "seed_block")
        if (
            row.get("stage") != "production"
            or row.get("sipm_layout") != "back-center"
            or _require_int(row, "absorber_transverse_mm") != 500
            or _require_int(row, "x_mm") != 0
            or _require_int(row, "y_mm") != 0
            or _require_int(row, "events") != 250
            or thickness not in (4, 24)
            or block not in range(16)
        ):
            raise ValueError(
                f"task is outside exact BC-ONLY-S1 shape: {row.get('logical_task_id')}"
            )
        key = (thickness, block)
        if key in seen_config_blocks:
            raise ValueError(f"duplicate thickness/block in checkpoint: {key}")
        seen_config_blocks.add(key)
        production_seeds.extend(
            (_require_int(row, "seed1"), _require_int(row, "seed2"))
        )
        for hash_key in (
            "configuration_hash",
            "task_result_sha256",
            "run_config_sha256",
            "root_sha256",
            "summary_sha256",
        ):
            digest = row.get(hash_key, "")
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError(f"invalid {hash_key} for {row.get('logical_task_id')}")
        root_path = Path(row.get("root", ""))
        if not root_path.is_absolute():
            raise ValueError("checkpoint ROOT references must be absolute")
        if verify_root_files:
            if root_path.is_symlink() or not root_path.is_file():
                raise ValueError(f"missing regular ROOT evidence: {root_path}")
            if sha256_file(root_path) != row["root_sha256"]:
                raise ValueError(f"ROOT checksum mismatch: {root_path}")

    expected = {(thickness, block) for thickness in (4, 24) for block in range(16)}
    if seen_config_blocks != expected:
        raise ValueError("BC-ONLY-S1 contains a block gap or unexpected block")
    if len(production_seeds) != 64 or len(set(production_seeds)) != 64:
        raise ValueError("BC-ONLY-S1 must contain exactly 64 unique production seeds")
    return task_rows


def _semantic_hash(record: dict[str, Any], field: str) -> str:
    expected = require_sha256(record, field)
    unhashed = dict(record)
    unhashed.pop(field)
    if sha256_bytes(canonical_json(unhashed)) != expected:
        raise ValueError(f"{field} semantic hash mismatch")
    return expected


def load_managed_finalization(
    finalized_dir: Path, *, allow_test_mode: bool = False, verify_root_files: bool = True
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    directory = finalized_dir.expanduser()
    if directory.is_symlink():
        raise ValueError("managed finalization directory must not be a symlink")
    directory = directory.resolve()
    required = {
        "task_index.tsv",
        "configuration_summary.csv",
        "event_audit.json",
        "seed_audit.json",
        "selection_record.json",
        "validation_report.json",
    }
    verify_recursive_checksums(directory, required=required)
    validation = load_json(directory / "validation_report.json")
    if validation.get("schema_version") != MANAGED_FINALIZATION_SCHEMA_VERSION:
        raise ValueError("unsupported managed finalization schema")
    if validation.get("valid") is not True:
        raise ValueError("managed finalization is not valid")
    test_mode = validation.get("test_mode") is True
    accepted = validation.get("accepted_statistical_evidence") is True
    if accepted == test_mode:
        raise ValueError("managed finalization acceptance/test-mode flags disagree")
    if test_mode and not allow_test_mode:
        raise ValueError("formal checkpoint loader refuses test-mode finalization")
    if (
        validation.get("child_id") != "BC-S1"
        or validation.get("task_count") != 32
        or validation.get("event_count") != 8_000
        or validation.get("event_audit_integrated") is not True
        or validation.get("seed_audit_integrated") is not True
    ):
        raise ValueError("managed finalization is not the complete BC-S1 child")
    execution_record = require_dict(validation, "execution")
    lineage = validate_recovery_lineage(
        execution_record, validation.get("recovery_lineage")
    )
    event_audit = load_json(directory / "event_audit.json")
    seed_audit = load_json(directory / "seed_audit.json")
    selection = load_json(directory / "selection_record.json")
    canonical_execution = None
    if lineage is not None:
        for name, value in (
            ("event_audit.json", event_audit),
            ("seed_audit.json", seed_audit),
            ("selection_record.json", selection),
        ):
            if value.get("recovery_lineage") != lineage:
                raise ValueError(f"managed finalization {name} lost recovery lineage")
        if (
            event_audit.get("execution_id") != execution_record.get("execution_id")
            or event_audit.get("execution_hash")
            != execution_record.get("execution_hash")
        ):
            raise ValueError("successor event audit execution identity mismatch")
        if not test_mode:
            canonical_execution = _load_canonical_successor_for_finalization(
                execution_record
            )
    rows = validate_bc_only_s1_rows(
        read_tsv(directory / "task_index.tsv"),
        verify_root_files=verify_root_files,
    )
    validate_successor_seed_lineage(
        execution_record, lineage, rows, seed_audit
    )
    if canonical_execution is not None:
        if recovery_lineage_from_execution(canonical_execution) != lineage:
            raise ValueError("formal finalization recovery lineage changed")
        _validate_canonical_selected_artifacts(canonical_execution, rows)
        _validate_canonical_pilot_seed_registry(
            canonical_execution, seed_audit
        )
    validate_successor_selection_and_accounting(
        directory,
        validation,
        execution_record,
        lineage,
        rows,
        selection,
        canonical_execution=canonical_execution,
    )
    return validation, rows


def load_production_checkpoint(
    checkpoint_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = True,
    verify_root_files: bool = True,
) -> ProductionCheckpoint:
    """Load a formal or explicit test checkpoint.

    Formal evidence revalidates the tracked readiness lock directly.  The
    copied lock digest is provenance, not a substitute for tracked authority.
    """
    directory = checkpoint_dir.expanduser()
    if directory.is_symlink():
        raise ValueError("production checkpoint directory must not be a symlink")
    directory = directory.resolve()
    checksums = verify_recursive_checksums(
        directory, required=CHECKPOINT_REQUIRED_FILES
    )
    manifest = load_json(directory / "checkpoint.json")
    if manifest.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported production checkpoint schema")
    checkpoint_hash = _semantic_hash(manifest, "checkpoint_hash")
    if directory.name != f"bc-only-s1-{checkpoint_hash[:12]}":
        raise ValueError("checkpoint directory name does not match checkpoint hash")
    if (
        manifest.get("state_id") != FORMAL_CHECKPOINT_STATE
        or manifest.get("evidence_mode") != FORMAL_EVIDENCE_MODE
        or manifest.get("child_ids") != ["BC-S1"]
        or manifest.get("task_count") != 32
        or manifest.get("event_count") != 8_000
        or manifest.get("root_tree") != "scan"
    ):
        raise ValueError("checkpoint is not exact BC-ONLY-S1 evidence")
    test_mode = manifest.get("test_mode") is True
    accepted = manifest.get("accepted_statistical_evidence") is True
    if accepted == test_mode:
        raise ValueError("checkpoint acceptance/test-mode flags disagree")
    if test_mode and not allow_test_mode:
        raise ValueError("formal loader refuses a test-mode checkpoint")
    readiness = require_dict(manifest, "readiness")
    execution = require_dict(manifest, "execution")
    lineage = validate_recovery_lineage(
        execution, manifest.get("recovery_lineage")
    )
    if require_readiness and (
        readiness.get("required") is not True
        or not isinstance(readiness.get("lock_sha256"), str)
        or len(readiness["lock_sha256"]) != 64
    ):
        raise ValueError("checkpoint lacks the required Phase-2B readiness identity")
    if require_readiness and not test_mode:
        if repo_root is None:
            raise ValueError("formal checkpoint validation requires repo_root")
        _, phase2a = load_phase2a_lock(repo_root)
        canonical_child = Path(require_string(phase2a, "canonical_directory")).resolve()
        from steel_module_production_phase2c_lib import (
            EXECUTION_V6_SCHEMA_VERSION,
            FORMAL_EXECUTION_V6_NAME,
        )

        successor = execution.get("schema_version") in {
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
            EXECUTION_V6_SCHEMA_VERSION,
        }
        if successor:
            if execution.get("object_kind") != SUCCESSOR_OBJECT_KIND:
                raise ValueError("checkpoint successor schema/object-kind mismatch")
            from steel_module_production_successor_lib import (
                FORMAL_SUCCESSOR_EXECUTION_NAME,
                FORMAL_SUCCESSOR_EXECUTION_NAME_V2,
                FORMAL_SUCCESSOR_EXECUTION_NAME_V3,
            )

            execution_name = {
                EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
                    FORMAL_SUCCESSOR_EXECUTION_NAME_V2,
                EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2:
                    FORMAL_SUCCESSOR_EXECUTION_NAME_V3,
                EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3:
                    FORMAL_SUCCESSOR_EXECUTION_NAME,
                EXECUTION_V6_SCHEMA_VERSION: FORMAL_EXECUTION_V6_NAME,
            }[execution.get("schema_version")]
        else:
            execution_name = FORMAL_EXECUTION_NAME
        canonical_execution = canonical_child.parent / execution_name
        canonical_root = canonical_child.parent / "steel-module-production-checkpoints"
        if directory.parent != canonical_root:
            raise ValueError(f"formal checkpoint path must be under {canonical_root}")
        if execution.get("directory") != str(canonical_execution):
            raise ValueError("checkpoint execution directory is not canonical")
        loaded_execution = load_execution_for_downstream(
            canonical_execution,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_runtime=True,
            verify_live_predecessor=True,
        )
        current_readiness = readiness_identity_for_execution(
            loaded_execution, repo_root=repo_root
        )
        if readiness != current_readiness:
            raise ValueError("checkpoint readiness identity is stale")
        for key, actual in (
            ("execution_id", loaded_execution.execution_id),
            ("execution_hash", loaded_execution.execution_hash),
        ):
            if execution.get(key) != actual:
                raise ValueError(f"checkpoint managed execution {key} mismatch")
        expected_lineage = recovery_lineage_from_execution(loaded_execution)
        if lineage != expected_lineage:
            raise ValueError("checkpoint recovery lineage changed")
        managed_child = require_dict(loaded_execution.manifest, "managed_child")
        for checkpoint_key, child_key in (
            ("task_set_hash", "task_set_hash"),
            ("task_seed_mapping_hash", "task_seed_mapping_hash"),
            ("production_seed_set_hash", "seed_set_hash"),
        ):
            if require_sha256(manifest, checkpoint_key) != require_sha256(
                managed_child, child_key
            ):
                raise ValueError(f"checkpoint {checkpoint_key} mismatch")
    rows = validate_bc_only_s1_rows(
        read_tsv(directory / "task_index.tsv"), verify_root_files=verify_root_files
    )
    if sha256_file(directory / "task_index.tsv") != manifest.get(
        "task_index_sha256"
    ):
        raise ValueError("checkpoint task-index identity mismatch")
    event_audit = load_json(directory / "event_audit.json")
    seed_audit = load_json(directory / "seed_audit.json")
    if event_audit.get("valid") is not True or event_audit.get("task_count") != 32:
        raise ValueError("checkpoint event audit is invalid")
    if (
        seed_audit.get("valid") is not True
        or seed_audit.get("production_seed_count") != 64
        or seed_audit.get("sealed_pilot_seed_count") != 240
        or seed_audit.get("overlap_count") != 0
    ):
        raise ValueError("checkpoint seed audit is invalid")
    if lineage is not None and (
        event_audit.get("recovery_lineage") != lineage
        or seed_audit.get("recovery_lineage") != lineage
    ):
        raise ValueError("checkpoint audits lost recovery lineage")
    validate_successor_seed_lineage(
        execution,
        lineage,
        rows,
        seed_audit,
        checkpoint_record=manifest,
    )
    source = load_json(directory / "source_finalization.json")
    if source != require_dict(manifest, "source_finalization"):
        raise ValueError("checkpoint source-finalization record mismatch")
    if require_readiness and not test_mode:
        finalized = managed_finalization_directory(
            canonical_execution, loaded_execution.manifest
        )
        if source.get("directory") != str(finalized):
            raise ValueError("checkpoint source finalization is not canonical")
        validation, source_rows = load_managed_finalization(
            finalized,
            allow_test_mode=False,
            verify_root_files=verify_root_files,
        )
        if (
            require_dict(validation, "execution") != execution
            or validation.get("recovery_lineage") != lineage
        ):
            raise ValueError("checkpoint/source finalization lineage mismatch")
        if source_rows != rows:
            raise ValueError("checkpoint rows differ from canonical source finalization")
        if (
            source.get("checksum_manifest_sha256")
            != sha256_file(finalized / "SHA256SUMS")
            or source.get("finalization_hash") != validation.get("finalization_hash")
        ):
            raise ValueError("checkpoint source-finalization identity changed")
    source_children = load_json(directory / "source_children.json")
    if source_children != require_dict(manifest, "source_children"):
        raise ValueError("checkpoint source-children record mismatch")
    if source_children.get("recovery_lineage") != lineage:
        raise ValueError("checkpoint source-children recovery lineage mismatch")
    for name, digest in (
        ("event_audit.json", manifest.get("event_audit_sha256")),
        ("seed_audit.json", manifest.get("seed_audit_sha256")),
        ("source_children.json", manifest.get("source_children_sha256")),
        ("source_finalization.json", manifest.get("source_finalization_sha256")),
    ):
        if checksums.get(name) != digest:
            raise ValueError(f"checkpoint embedded artifact identity mismatch: {name}")
    return ProductionCheckpoint(directory, manifest, rows, event_audit, seed_audit)


def write_checkpoint_atomic(
    output_root: Path,
    *,
    manifest_payload: dict[str, Any],
    task_index_text: str,
    configuration_summary_text: str,
    event_audit: dict[str, Any],
    seed_audit: dict[str, Any],
    source_children: dict[str, Any],
    source_finalization: dict[str, Any],
) -> Path:
    """Write one non-overwritable content-addressed checkpoint directory."""

    requested_root = output_root.expanduser()
    if requested_root.is_symlink():
        raise ValueError("checkpoint output root must not be a symlink")
    root = requested_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    payload = dict(manifest_payload)
    if "checkpoint_hash" in payload:
        raise ValueError("checkpoint payload must not pre-populate checkpoint_hash")
    payload["checkpoint_hash"] = sha256_bytes(canonical_json(payload))
    target = root / f"bc-only-s1-{payload['checkpoint_hash'][:12]}"
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite checkpoint: {target}")
    temp = Path(tempfile.mkdtemp(prefix=".bc-only-s1.tmp-", dir=root))
    try:
        (temp / "task_index.tsv").write_text(task_index_text, encoding="utf-8")
        (temp / "configuration_summary.csv").write_text(
            configuration_summary_text, encoding="utf-8"
        )
        for name, value in (
            ("event_audit.json", event_audit),
            ("seed_audit.json", seed_audit),
            ("source_children.json", source_children),
            ("source_finalization.json", source_finalization),
            ("checkpoint.json", payload),
        ):
            (temp / name).write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        write_recursive_checksums(temp)
        publish_directory_no_replace(temp, target)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return target


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "FORMAL_CHECKPOINT_STATE",
    "MANAGED_FINALIZATION_SCHEMA_VERSION",
    "RECOVERY_LINEAGE_SCHEMA_VERSION",
    "RECOVERY_LINEAGE_SCHEMA_VERSION_V2",
    "RECOVERY_LINEAGE_SCHEMA_VERSION_V3",
    "RECOVERY_LINEAGE_SCHEMA_VERSION_V4",
    "SUCCESSOR_FINALIZATION_BUNDLE_NAME",
    "ProductionCheckpoint",
    "is_successor_execution_manifest",
    "load_execution_for_downstream",
    "load_managed_finalization",
    "load_production_checkpoint",
    "managed_finalization_directory",
    "publish_directory_no_replace",
    "read_tsv",
    "recursive_file_records",
    "readiness_identity_for_execution",
    "recovery_lineage_exclusions",
    "recovery_lineage_from_execution",
    "validate_bc_only_s1_rows",
    "validate_recovery_lineage",
    "verify_recursive_checksums",
    "write_checkpoint_atomic",
    "write_recursive_checksums",
]
