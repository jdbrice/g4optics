#!/usr/bin/env python3
"""Focused synthetic checks for managed finalization/checkpoint evidence."""

from __future__ import annotations

import csv
import errno
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import finalize_steel_module_managed_child as finalizer
import record_steel_module_production_task_result as task_result_recorder
import steel_module_production_phase2c_readiness as phase2c_readiness
import steel_module_production_checkpoint_lib as checkpoint_lib
import steel_module_production_phase2b_lib as phase2b_lib
from build_steel_module_production_checkpoint import build_checkpoint
from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_checkpoint_lib import (
    MANAGED_FINALIZATION_SCHEMA_VERSION,
    RECOVERY_LINEAGE_SCHEMA_VERSION_V2,
    RECOVERY_LINEAGE_SCHEMA_VERSION_V3,
    RECOVERY_LINEAGE_SCHEMA_VERSION_V4,
    SUCCESSOR_FINALIZATION_BUNDLE_NAME,
    load_managed_finalization,
    load_execution_for_downstream,
    load_production_checkpoint,
    managed_finalization_directory,
    recovery_lineage_from_execution,
    validate_bc_only_s1_rows,
    validate_recovery_lineage,
    verify_recursive_checksums,
    write_checkpoint_atomic,
    write_recursive_checksums,
)
from steel_module_production_phase2b_lib import (
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
    EXECUTION_SCHEMA_VERSION_V2,
    SUCCESSOR_OBJECT_KIND,
)
from steel_module_production_program_lib import (
    ordered_task_hash,
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)
from steel_module_production_phase2c_lib import (
    EXECUTION_V6_SCHEMA_VERSION,
)
from steel_module_production_successor_lib import (
    _validate_successor_mutable_state_after_intent,
)


HASH = "a" * 64


def fixture_tasks(root: Path) -> tuple[CampaignTask, ...]:
    return tuple(
        CampaignTask(
            task_index=int(row["task_index"]),
            logical_task_id=row["logical_task_id"],
            stage=row["stage"],
            tile_thickness_mm=int(row["tile_thickness_mm"]),
            sipm_layout=row["sipm_layout"],
            absorber_transverse_mm=int(row["absorber_transverse_mm"]),
            x_mm=int(row["x_mm"]),
            y_mm=int(row["y_mm"]),
            seed_block=int(row["seed_block"]),
            events=int(row["events"]),
            seed1=int(row["seed1"]),
            seed2=int(row["seed2"]),
            configuration_hash=row["configuration_hash"],
        )
        for row in fixture_rows(root)
    )


def fixture_task_identity(root: Path) -> dict[str, Any]:
    tasks = fixture_tasks(root)
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
        "task_count": 32,
        "event_count": 8000,
        "seed_count": 64,
    }


def fixture_recovery_authority(root: Path) -> dict[str, Any]:
    identity = fixture_task_identity(root)
    authority: dict[str, Any] = {
        "schema_version": "steel-module-production-successor-authority-v1",
        "predecessor_execution": {
            "directory": str(root / "predecessor"),
            "execution_id": "fixture-predecessor",
            "execution_hash": "2" * 64,
        },
        "predecessor_attempt": {
            "attempt_id": "20260718T175107Z-initial",
            "job_id": "50532143",
        },
        "incident": {
            "directory": str(root / "incident"),
            "incident_id": "fixture-zero-consumption-incident",
            "incident_hash": "3" * 64,
            "incident_json_sha256": "4" * 64,
            "checksum_manifest_sha256": "5" * 64,
        },
        "zero_consumption": {
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
        },
        "retry_equivalence": {**identity, "all_equal": True},
    }
    authority["authority_hash"] = sha256_bytes(canonical_json(authority))
    return authority


def fixture_successor(root: Path) -> SimpleNamespace:
    execution_dir = (root / "successor-execution").resolve()
    manifest = {
        "schema_version": EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        "object_kind": SUCCESSOR_OBJECT_KIND,
        "execution_generation": "predecessor-retry-successor-v2",
        "recovery_authority": fixture_recovery_authority(root),
    }
    return SimpleNamespace(
        directory=execution_dir,
        execution_id="fixture-successor",
        execution_hash="6" * 64,
        manifest=manifest,
    )


def fixture_successor_v3(root: Path) -> SimpleNamespace:
    """A compact but semantically complete downstream v3 authority."""

    execution_dir = (root / "successor-v3-execution").resolve()
    predecessor_authority = fixture_recovery_authority(root)
    identity = fixture_task_identity(root)
    authority: dict[str, Any] = {
        "schema_version": "steel-module-production-successor-authority-v2",
        "predecessor_execution": {
            "directory": str(root / "successor-v2-execution"),
            "schema_version": EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
            "execution_generation": "predecessor-retry-successor-v2",
            "execution_id": "fixture-successor-v2",
            "execution_hash": "7" * 64,
            "managed_execution_sha256": "8" * 64,
            "static_checksums_sha256": "9" * 64,
            "recovery_authority_hash": predecessor_authority["authority_hash"],
        },
        "predecessor_recovery_authority": predecessor_authority,
        "original_production_incident": predecessor_authority["incident"],
        "failed_r3_preflight": {
            "directory": str(root / "failed-r3"),
            "schema_version": "steel-module-production-r3-preflight-failure-evidence-v1",
            "failure_id": "fixture-r3-failure",
            "failure_hash": "b" * 64,
            "failure_json_sha256": "c" * 64,
            "checksum_manifest_sha256": "d" * 64,
            "recursive_record_count": 6,
            "execution": {
                "execution_id": "fixture-successor-v2",
                "execution_hash": "7" * 64,
            },
            "scheduler": {"job_id": "50544247"},
            "runtime_boundary": {
                "apptainer_invoked": False,
                "geant4_invoked": False,
            },
            "consumption": {
                "events_consumed": 0,
                "production_seeds_consumed": 0,
            },
        },
        "zero_consumption": predecessor_authority["zero_consumption"],
        "retry_equivalence": {**identity, "all_equal": True},
    }
    authority["authority_hash"] = sha256_bytes(canonical_json(authority))
    manifest = {
        "schema_version": EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        "object_kind": SUCCESSOR_OBJECT_KIND,
        "execution_generation": "predecessor-retry-successor-v3",
        "recovery_authority": authority,
    }
    return SimpleNamespace(
        directory=execution_dir,
        execution_id="fixture-successor-v3",
        execution_hash="e" * 64,
        manifest=manifest,
    )


def fixture_successor_v4(root: Path) -> SimpleNamespace:
    """A compact execution-v4 authority with both compute-preflight failures."""

    execution_dir = (root / "successor-v4-execution").resolve()
    predecessor = fixture_successor_v3(root)
    predecessor_authority = predecessor.manifest["recovery_authority"]
    identity = fixture_task_identity(root)
    authority: dict[str, Any] = {
        "schema_version": "steel-module-production-successor-authority-v3",
        "predecessor_execution": {
            "directory": str(predecessor.directory),
            "schema_version": predecessor.manifest["schema_version"],
            "execution_generation": predecessor.manifest["execution_generation"],
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
            "managed_execution_sha256": "1" * 64,
            "static_checksums_sha256": "2" * 64,
            "recovery_authority_hash": predecessor_authority["authority_hash"],
        },
        "predecessor_recovery_authority": predecessor_authority,
        "original_production_incident": predecessor_authority[
            "original_production_incident"
        ],
        "failed_r3_preflight": predecessor_authority["failed_r3_preflight"],
        "administrative_r3_launch_rejection": {
            "classification": "pre-probe-administrative-launch-rejection",
            "job_id": "50547698",
            "manifest_path": str(root / "rejected-launch-50547698.sha256"),
            "manifest_sha256": "3" * 64,
            "compute_preflight": False,
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "rejected_r3_preflight": {
            "directory": str(root / "rejected-r3"),
            "schema_version": (
                "steel-module-production-r3-container-probe-rejection-evidence-v1"
            ),
            "rejection_id": "fixture-r3-rejection",
            "rejection_hash": "4" * 64,
            "checksum_manifest_sha256": "5" * 64,
            "recursive_record_count": 8,
            "accepted_compute_preflight_evidence": False,
            "classification": "container-isolation-writer-open-rejection-failure",
            "execution": {
                "execution_id": predecessor.execution_id,
                "execution_hash": predecessor.execution_hash,
            },
            "scheduler": {"job_id": "50548308"},
            "probe": {
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
        },
        "zero_consumption": predecessor_authority["zero_consumption"],
        "retry_equivalence": {**identity, "all_equal": True},
    }
    authority["authority_hash"] = sha256_bytes(canonical_json(authority))
    manifest = {
        "schema_version": EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
        "object_kind": SUCCESSOR_OBJECT_KIND,
        "execution_generation": "predecessor-retry-successor-v4",
        "recovery_authority": authority,
    }
    return SimpleNamespace(
        directory=execution_dir,
        execution_id="fixture-successor-v4",
        execution_hash="f" * 64,
        manifest=manifest,
    )


def fixture_phase2c_successor(root: Path) -> SimpleNamespace:
    execution_dir = (root / "execution-v6").resolve()
    authority: dict[str, Any] = {
        "schema_version": "steel-module-production-phase2c-authority-fixture-v1",
        "production_equivalence_hash": "7" * 64,
        "preflight_evidence_hash": "8" * 64,
        "excluded_attempt_ids": ["20260718T175107Z-initial"],
        "excluded_job_ids": ["50532143", "50561809", "60000000"],
    }
    authority["authority_hash"] = sha256_bytes(canonical_json(authority))
    return SimpleNamespace(
        directory=execution_dir,
        execution_id="fixture-execution-v6",
        execution_hash="6" * 64,
        manifest={
            "schema_version": EXECUTION_V6_SCHEMA_VERSION,
            "object_kind": SUCCESSOR_OBJECT_KIND,
            "execution_generation": "production-ready-execution-v6",
            "phase2c_authority": authority,
            "production_equivalence_hash": "7" * 64,
        },
    )


def expect_failure(function: Any, *args: Any, contains: str | None = None, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if contains is not None and contains not in str(exc):
            raise AssertionError(f"unexpected failure text: {exc}") from exc
    else:
        raise AssertionError("operation unexpectedly succeeded")


def json_write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def rewrite_recursive_checksums(directory: Path) -> None:
    (directory / "SHA256SUMS").unlink()
    write_recursive_checksums(directory)


def reseal_checkpoint(directory: Path) -> Path:
    """Recompute artifact identities and the semantic checkpoint hash."""

    manifest_path = directory / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["task_index_sha256"] = sha256_file(directory / "task_index.tsv")
    manifest["event_audit_sha256"] = sha256_file(directory / "event_audit.json")
    manifest["seed_audit_sha256"] = sha256_file(directory / "seed_audit.json")
    manifest["source_children_sha256"] = sha256_file(
        directory / "source_children.json"
    )
    manifest["source_finalization_sha256"] = sha256_file(
        directory / "source_finalization.json"
    )
    manifest.pop("checkpoint_hash", None)
    manifest["checkpoint_hash"] = sha256_bytes(canonical_json(manifest))
    json_write(manifest_path, manifest)
    target = directory.with_name(
        f"bc-only-s1-{manifest['checkpoint_hash'][:12]}"
    )
    if target != directory:
        if target.exists():
            shutil.rmtree(target)
        directory.rename(target)
        directory = target
    rewrite_recursive_checksums(directory)
    return directory


def concurrent_publish_worker(
    output_root: str,
    barrier: Any,
    queue: Any,
) -> None:
    """Two processes deliberately race the same content-addressed publish."""

    try:
        barrier.wait(timeout=10)
        target = write_checkpoint_atomic(
            Path(output_root),
            manifest_payload={"schema_version": "concurrent-fixture-v1"},
            task_index_text="task_index\n1\n",
            configuration_summary_text="payload\n" + ("x" * 2_000_000),
            event_audit={"valid": True},
            seed_audit={"valid": True},
            source_children={"children": []},
            source_finalization={"fixture": True},
        )
        queue.put(("success", str(target)))
    except BaseException as exc:  # pragma: no cover - child process transport
        queue.put(("failure", f"{type(exc).__name__}: {exc}"))


def fixture_rows(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    task_index = 0
    seed = 1000
    for thickness in (4, 24):
        for block in range(16):
            task_index += 1
            logical_id = f"production-t{thickness:02d}-a500-back-center-b{block:02d}"
            fake_root = root / "unmaterialized-root" / f"{logical_id}.root"
            rows.append(
                {
                    "task_index": task_index,
                    "logical_task_id": logical_id,
                    "attempt_id": "fixture-attempt",
                    "slurm_job_id": "12345",
                    "slurm_array_index": task_index,
                    "stage": "production",
                    "tile_thickness_mm": thickness,
                    "sipm_layout": "back-center",
                    "absorber_transverse_mm": 500,
                    "x_mm": 0,
                    "y_mm": 0,
                    "seed_block": block,
                    "events": 250,
                    "seed1": seed,
                    "seed2": seed + 1,
                    "configuration_hash": HASH,
                    "task_result": str(root / "task_result.json"),
                    "task_result_sha256": HASH,
                    "run_config": str(root / "run_config.json"),
                    "run_config_sha256": HASH,
                    "macro": str(root / "run.mac"),
                    "macro_sha256": HASH,
                    "simulation_log": str(root / "simulation.log"),
                    "simulation_log_sha256": HASH,
                    "root": str(fake_root),
                    "root_sha256": HASH,
                    "summary": str(root / "summary.csv"),
                    "summary_sha256": HASH,
                    "efficiency_map": str(root / "efficiency_map.csv"),
                    "efficiency_map_sha256": HASH,
                }
            )
            seed += 2
    return rows


def write_finalization(root: Path) -> Path:
    finalization = root / "execution" / "finalized"
    finalization.mkdir(parents=True)
    rows = fixture_rows(root)
    with (finalization / "task_index.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    (finalization / "configuration_summary.csv").write_text(
        "stage,tile_thickness_mm\n", encoding="utf-8"
    )
    event_tasks = [
        {
            "logical_task_id": row["logical_task_id"],
            "tile_thickness_mm": row["tile_thickness_mm"],
            "sipm_layout": "back-center",
            "seed_block": row["seed_block"],
            "events": 250,
            "root": row["root"],
            "root_sha256": HASH,
            "audit": {"events": 250},
        }
        for row in rows
    ]
    json_write(
        finalization / "event_audit.json",
        {
            "schema_version": "steel-module-managed-event-audit-v1",
            "valid": True,
            "task_count": 32,
            "event_count": 8000,
            "tasks": event_tasks,
        },
    )
    json_write(
        finalization / "seed_audit.json",
        {
            "schema_version": "steel-module-managed-seed-audit-v1",
            "valid": True,
            "production_seed_count": 64,
            "production_unique_seed_count": 64,
            "sealed_pilot_seed_count": 240,
            "sealed_pilot_unique_seed_count": 240,
            "overlap_count": 0,
        },
    )
    json_write(
        finalization / "selection_record.json",
        {
            "schema_version": "steel-module-managed-selection-v1",
            "selected_task_count": 32,
        },
    )
    readiness = {"required": True, "lock_sha256": "b" * 64}
    pilot = {
        "campaign_directory": "/fixture/pilot",
        "finalized_sha256s_sha256": "c" * 64,
        "analysis_v2_directory": "/fixture/pilot/finalized/analysis-v2",
        "analysis_v2_sha256s_sha256": "d" * 64,
    }
    json_write(
        finalization / "validation_report.json",
        {
            "schema_version": MANAGED_FINALIZATION_SCHEMA_VERSION,
            "valid": True,
            "finalization_hash": "e" * 64,
            "accepted_statistical_evidence": False,
            "test_mode": True,
            "child_id": "BC-S1",
            "task_count": 32,
            "event_count": 8000,
            "event_audit_integrated": True,
            "seed_audit_integrated": True,
            "program": {
                "program_id": "fixture-program",
                "program_hash": "f" * 64,
            },
            "execution": {
                "execution_id": "fixture-execution",
                "execution_hash": "1" * 64,
            },
            "readiness": readiness,
            "pilot_baseline": pilot,
        },
    )
    write_recursive_checksums(finalization)
    return finalization


def check_execution_dispatch(root: Path) -> None:
    old_dir = root / "dispatch-old"
    old_dir.mkdir()
    json_write(
        old_dir / "managed_execution.json",
        {"schema_version": EXECUTION_SCHEMA_VERSION_V2},
    )
    old_sentinel = object()
    with patch.object(
        checkpoint_lib, "load_execution_companion", return_value=old_sentinel
    ) as old_loader:
        assert (
            load_execution_for_downstream(
                old_dir,
                repo_root=root,
                require_readiness=True,
                verify_live_predecessor=True,
            )
            is old_sentinel
        )
        assert old_loader.call_args.kwargs["require_readiness"] is True
    for label, schema in (
        ("v2-history", EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1),
        ("v3-history", EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2),
        ("v4-active", EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3),
    ):
        successor_dir = root / f"dispatch-successor-{label}"
        successor_dir.mkdir()
        json_write(
            successor_dir / "managed_execution.json",
            {
                "schema_version": schema,
                "object_kind": SUCCESSOR_OBJECT_KIND,
            },
        )
        successor_sentinel = object()
        with patch(
            "steel_module_production_successor_lib.load_successor_execution",
            return_value=successor_sentinel,
        ) as successor_loader:
            assert (
                load_execution_for_downstream(
                    successor_dir,
                    repo_root=root,
                    require_readiness=True,
                    verify_live_predecessor=True,
                )
                is successor_sentinel
            )
            kwargs = successor_loader.call_args.kwargs
            assert kwargs["require_readiness"] is True
            assert kwargs["verify_live_predecessor"] is True
    v6_dir = root / "dispatch-execution-v6"
    v6_dir.mkdir()
    json_write(
        v6_dir / "managed_execution.json",
        {
            "schema_version": EXECUTION_V6_SCHEMA_VERSION,
            "object_kind": SUCCESSOR_OBJECT_KIND,
        },
    )
    v6_sentinel = object()
    with patch(
        "steel_module_production_phase2c_lib.load_execution_v6",
        return_value=v6_sentinel,
    ) as v6_loader:
        assert (
            load_execution_for_downstream(
                v6_dir,
                repo_root=root,
                require_readiness=True,
                verify_live_predecessor=True,
            )
            is v6_sentinel
        )
        kwargs = v6_loader.call_args.kwargs
        assert kwargs["require_readiness"] is True
        assert kwargs["verify_live_predecessor"] is True
    worker_sentinel = object()
    with patch(
        "steel_module_production_phase2c_lib.load_execution_v6_for_frozen_worker",
        return_value=worker_sentinel,
    ) as worker_loader:
        assert (
            task_result_recorder._load_execution_for_frozen_worker(
                v6_dir, control_root=root
            )
            is worker_sentinel
        )
        worker_loader.assert_called_once_with(v6_dir, control_root=root)
    legacy_worker_sentinel = object()
    with patch.object(
        task_result_recorder,
        "load_legacy_execution_for_frozen_worker",
        return_value=legacy_worker_sentinel,
    ) as legacy_worker_loader:
        assert (
            task_result_recorder._load_execution_for_frozen_worker(
                old_dir, control_root=root
            )
            is legacy_worker_sentinel
        )
        legacy_worker_loader.assert_called_once_with(
            old_dir, control_root=root
        )


def check_phase2c_lineage_contract(root: Path) -> None:
    """Keep all historical and sacrificial-preflight jobs out of v6 results."""

    authority: dict[str, Any] = {
        "schema_version": "steel-module-production-phase2c-authority-fixture-v1",
        "production_equivalence_hash": "7" * 64,
        "preflight_evidence_hash": "8" * 64,
        "excluded_attempt_ids": ["20260718T175107Z-initial"],
        "excluded_job_ids": ["50532143", "50561809", "60000000"],
    }
    authority["authority_hash"] = sha256_bytes(canonical_json(authority))
    execution = {
        "directory": str((root / "execution-v6").resolve()),
        "schema_version": EXECUTION_V6_SCHEMA_VERSION,
        "object_kind": SUCCESSOR_OBJECT_KIND,
        "execution_generation": "production-ready-execution-v6",
        "execution_id": "fixture-execution-v6",
        "execution_hash": "6" * 64,
        "phase2c_authority_hash": authority["authority_hash"],
        "production_equivalence_hash": "7" * 64,
    }
    lineage: dict[str, Any] = {
        "schema_version": RECOVERY_LINEAGE_SCHEMA_VERSION_V4,
        "successor_execution": {
            key: execution[key]
            for key in (
                "directory",
                "schema_version",
                "object_kind",
                "execution_generation",
                "execution_id",
                "execution_hash",
            )
        },
        "phase2c_authority": authority,
        "production_equivalence_hash": "7" * 64,
        "excluded_attempt_ids": authority["excluded_attempt_ids"],
        "excluded_job_ids": authority["excluded_job_ids"],
        "result_scope": {
            "source_execution_directory": execution["directory"],
            "selected_results": "execution-v6-production-successes-only",
            "historical_execution_outputs_included": False,
            "historical_execution_accounting_included": False,
            "phase2c_preflight_outputs_included": False,
            "phase2c_preflight_accounting_included": False,
        },
    }
    lineage["lineage_hash"] = sha256_bytes(canonical_json(lineage))
    assert validate_recovery_lineage(execution, lineage) == lineage
    assert checkpoint_lib.recovery_lineage_exclusions(lineage) == (
        {"20260718T175107Z-initial"},
        {"50532143", "50561809", "60000000"},
    )
    tampered = json.loads(json.dumps(lineage))
    tampered["excluded_job_ids"].remove("60000000")
    expect_failure(
        validate_recovery_lineage,
        execution,
        tampered,
        contains="semantic hash mismatch",
    )
    resigned = json.loads(json.dumps(lineage))
    resigned["excluded_job_ids"].remove("60000000")
    resigned.pop("lineage_hash")
    resigned["lineage_hash"] = sha256_bytes(canonical_json(resigned))
    expect_failure(
        validate_recovery_lineage,
        execution,
        resigned,
        contains="authority mismatch",
    )
    execution_object = SimpleNamespace(
        directory=Path(execution["directory"]),
        execution_id=execution["execution_id"],
        execution_hash=execution["execution_hash"],
        manifest={
            "schema_version": EXECUTION_V6_SCHEMA_VERSION,
            "object_kind": SUCCESSOR_OBJECT_KIND,
            "execution_generation": execution["execution_generation"],
            "phase2c_authority": authority,
            "production_equivalence_hash": execution[
                "production_equivalence_hash"
            ],
        },
    )
    assert recovery_lineage_from_execution(execution_object) == lineage
    valid = SimpleNamespace(
        attempt={"attempt_id": "20260722T120000Z-predecessor-retry"},
        job_id="70000000",
        marker_path=execution_object.directory / "attempts/new/task_result.json",
        paths={"root": execution_object.directory / "attempts/new/output.root"},
    )
    with patch.object(
        finalizer,
        "load_frozen_accounting",
        return_value={"job_id": valid.job_id},
    ):
        assert finalizer.validate_selected_result_lineage(
            execution_object, [valid], [valid.attempt["attempt_id"]]
        ) == lineage
    for forbidden_job in authority["excluded_job_ids"]:
        forbidden = SimpleNamespace(
            attempt=valid.attempt,
            job_id=forbidden_job,
            marker_path=valid.marker_path,
            paths=valid.paths,
        )
        expect_failure(
            finalizer.validate_selected_result_lineage,
            execution_object,
            [forbidden],
            [],
            contains="selected predecessor scheduler job",
        )
    forbidden_attempt = SimpleNamespace(
        attempt={"attempt_id": authority["excluded_attempt_ids"][0]},
        job_id=valid.job_id,
        marker_path=valid.marker_path,
        paths=valid.paths,
    )
    expect_failure(
        finalizer.validate_selected_result_lineage,
        execution_object,
        [forbidden_attempt],
        [forbidden_attempt.attempt["attempt_id"]],
        contains="predecessor attempt evidence",
    )


def check_phase2c_operational_readiness_snapshot(root: Path) -> None:
    """R6 binds the initial empty snapshot without rejecting later intents."""

    execution_root = root / "phase2c-readiness-after-intent"
    for name in ("intents", "attempts", "finalized"):
        (execution_root / name).mkdir(parents=True)
    (execution_root / "intents" / "fixture-predecessor-retry").mkdir()
    execution = SimpleNamespace(directory=execution_root)
    snapshot = phase2c_readiness._empty_mutable_snapshot(
        execution, require_currently_empty=False
    )
    assert snapshot["records"] == {
        "intents": [],
        "attempts": [],
        "finalized": [],
    }
    expect_failure(
        phase2c_readiness._empty_mutable_snapshot,
        execution,
        require_currently_empty=True,
        contains="mutable root is not empty: intents",
    )


def write_successor_finalization(
    source: Path,
    root: Path,
    *,
    successor: SimpleNamespace | None = None,
) -> tuple[Path, dict[str, Any]]:
    target = (
        root
        / "successor-finalization"
        / "finalized"
        / SUCCESSOR_FINALIZATION_BUNDLE_NAME
    )
    target.parent.mkdir(parents=True)
    shutil.copytree(source, target)
    successor = successor or fixture_successor(root)
    lineage = recovery_lineage_from_execution(successor)
    assert lineage is not None
    identity = fixture_task_identity(root)
    validation_path = target / "validation_report.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    execution_record = {
        "directory": str(successor.directory),
        "schema_version": successor.manifest["schema_version"],
        "object_kind": successor.manifest["object_kind"],
        "execution_generation": successor.manifest["execution_generation"],
        "execution_id": successor.execution_id,
        "execution_hash": successor.execution_hash,
        "task_set_hash": identity["task_set_hash"],
        "task_seed_mapping_hash": identity["task_seed_mapping_hash"],
        "seed_set_hash": identity["seed_set_hash"],
    }
    if lineage["schema_version"] == RECOVERY_LINEAGE_SCHEMA_VERSION_V4:
        execution_record.update(
            {
                "phase2c_authority_hash": lineage["phase2c_authority"][
                    "authority_hash"
                ],
                "production_equivalence_hash": lineage[
                    "production_equivalence_hash"
                ],
            }
        )
    else:
        execution_record["recovery_authority_hash"] = lineage[
            "recovery_authority"
        ]["authority_hash"]
    validation["execution"] = execution_record
    validation["recovery_lineage"] = lineage
    validation["accounting_attempts"] = ["fixture-attempt"]
    json_write(validation_path, validation)

    event_path = target / "event_audit.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["execution_id"] = successor.execution_id
    event["execution_hash"] = successor.execution_hash
    event["recovery_lineage"] = lineage
    json_write(event_path, event)

    tasks = fixture_tasks(root)
    seed_path = target / "seed_audit.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    seed.update(
        {
            "production_seed_set_hash": identity["seed_set_hash"],
            "overlap": [],
            "parent_registry_reconciled": True,
            "phase2a_plan_reconciled": True,
            "run_config_reconciled": True,
            "task_result_reconciled": True,
            "tasks": [
                {
                    "logical_task_id": task.logical_task_id,
                    "seed_block": task.seed_block,
                    "seed1": task.seed1,
                    "seed2": task.seed2,
                }
                for task in tasks
            ],
            "recovery_lineage": lineage,
        }
    )
    json_write(seed_path, seed)

    rows = checkpoint_lib.read_tsv(target / "task_index.tsv")
    selection_path = target / "selection_record.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection.update(
        {
            "explicit_selection": {},
            "selected_attempts": {
                row["logical_task_id"]: row["attempt_id"] for row in rows
            },
            "duplicate_successes_resolved": [],
            "source_execution_id": successor.execution_id,
            "source_execution_directory": str(successor.directory),
            "selected_results": "successor-successes-only",
            "predecessor_failed_outputs_included": False,
            "recovery_lineage": lineage,
        }
    )
    json_write(selection_path, selection)

    accounting = target / "accounting" / "fixture-attempt"
    accounting.mkdir(parents=True)
    json_write(
        accounting / "frozen.json",
        {"attempt_id": "fixture-attempt", "job_id": "12345"},
    )
    rewrite_recursive_checksums(target)
    return target, lineage


def check_canonical_finalization_rebinding(root: Path) -> None:
    """Bind formal rows to canonical task markers, accounting, and pilot seeds."""

    artifact_root = (root / "canonical-artifact-execution").resolve()
    rows = tuple(
        {key: str(value) for key, value in row.items()}
        for row in fixture_rows(root)
    )
    execution = SimpleNamespace(
        directory=artifact_root,
        tasks=checkpoint_lib._tasks_from_finalized_rows(rows),
    )
    markers: dict[str, dict[str, Any]] = {}
    for row in rows:
        logical_id = row["logical_task_id"]
        task_root = (
            artifact_root
            / "attempts"
            / "fixture-attempt"
            / "tasks"
            / logical_id
        )
        task_root.mkdir(parents=True)
        artifacts: dict[str, dict[str, str]] = {}
        for label in (
            "run_config",
            "macro",
            "simulation_log",
            "root",
            "summary",
            "efficiency_map",
        ):
            path = task_root / f"{label}.fixture"
            path.write_text(f"{logical_id}:{label}\n", encoding="utf-8")
            digest = sha256_file(path)
            artifacts[label] = {
                "path": path.relative_to(artifact_root).as_posix(),
                "sha256": digest,
            }
            row[label] = str(path)
            row[f"{label}_sha256"] = digest
        marker_path = task_root / "task_result.json"
        marker = {"artifacts": artifacts}
        json_write(marker_path, marker)
        row["task_result"] = str(marker_path)
        row["task_result_sha256"] = sha256_file(marker_path)
        markers[logical_id] = marker

    def load_marker(
        candidate: Any, attempt_id: str, logical_id: str
    ) -> dict[str, Any]:
        assert candidate is execution
        assert attempt_id == "fixture-attempt"
        return markers[logical_id]

    with patch.object(
        checkpoint_lib,
        "load_managed_task_result",
        side_effect=load_marker,
    ):
        checkpoint_lib._validate_canonical_selected_artifacts(execution, rows)

        # A same-content external ROOT cannot be substituted for the canonical
        # path merely by preserving/re-signing its digest.
        external_root = root / "external-same-content.root"
        shutil.copyfile(Path(rows[0]["root"]), external_root)
        detached = tuple(dict(row) for row in rows)
        detached[0]["root"] = str(external_root.resolve())
        assert detached[0]["root_sha256"] == sha256_file(external_root)
        expect_failure(
            checkpoint_lib._validate_canonical_selected_artifacts,
            execution,
            detached,
            contains="canonical artifact mismatch: root",
        )

        detached_marker = tuple(dict(row) for row in rows)
        external_marker = root / "external-task-result.json"
        shutil.copyfile(Path(rows[0]["task_result"]), external_marker)
        detached_marker[0]["task_result"] = str(external_marker.resolve())
        expect_failure(
            checkpoint_lib._validate_canonical_selected_artifacts,
            execution,
            detached_marker,
            contains="task-result binding mismatch",
        )

        altered_task = tuple(dict(row) for row in rows)
        altered_task[0]["seed1"] = str(int(altered_task[0]["seed1"]) + 1)
        expect_failure(
            checkpoint_lib._validate_canonical_selected_artifacts,
            execution,
            altered_task,
            contains="task rows differ from execution plan",
        )

    attempt_id = "20260721T120000Z-fixture"
    accounting_execution = fixture_successor_v4(
        root / "canonical-accounting-authority"
    )
    canonical_accounting = (
        accounting_execution.directory
        / "attempts"
        / attempt_id
        / "accounting"
    )
    copied_finalization = root / "copied-accounting-finalization"
    copied_accounting = copied_finalization / "accounting" / attempt_id
    canonical_accounting.mkdir(parents=True)
    copied_accounting.mkdir(parents=True)
    accounting_rows = tuple(
        {
            **{key: str(value) for key, value in row.items()},
            "attempt_id": attempt_id,
        }
        for row in fixture_rows(root)
    )
    logical_ids = [row["logical_task_id"] for row in accounting_rows]
    frozen = {
        "attempt_id": attempt_id,
        "job_id": "12345",
        "accepted_terminal": True,
        "task_states": {str(index): "COMPLETED" for index in range(1, 33)},
        "task_exit_codes": {str(index): "0:0" for index in range(1, 33)},
    }
    sacct_text = "".join(
        f"12345_{index}|COMPLETED|0:0|1|||\n" for index in range(1, 33)
    )
    for directory in (canonical_accounting, copied_accounting):
        json_write(directory / "frozen.json", frozen)
        (directory / "sacct.psv").write_text(sacct_text, encoding="utf-8")
        (directory / "squeue.psv").write_text("", encoding="utf-8")
        write_recursive_checksums(directory)

    lineage = recovery_lineage_from_execution(accounting_execution)
    assert lineage is not None
    execution_record = {
        "schema_version": accounting_execution.manifest["schema_version"],
        "execution_id": accounting_execution.execution_id,
        "directory": str(accounting_execution.directory),
    }
    validation = {"accounting_attempts": [attempt_id]}
    selection = {
        "schema_version": "steel-module-managed-selection-v1",
        "selected_task_count": 32,
        "source_execution_id": accounting_execution.execution_id,
        "source_execution_directory": str(accounting_execution.directory),
        "selected_results": "successor-successes-only",
        "predecessor_failed_outputs_included": False,
        "selected_attempts": {
            row["logical_task_id"]: attempt_id
            for row in accounting_rows
        },
        "explicit_selection": {},
        "duplicate_successes_resolved": [],
    }
    intent = {
        "selected": {
            "task_count": 32,
            "logical_task_ids": logical_ids,
        }
    }
    with patch.object(
        checkpoint_lib, "load_frozen_accounting", return_value=frozen
    ), patch.object(
        checkpoint_lib, "load_attempt_intent", return_value=intent
    ):
        checkpoint_lib.validate_successor_selection_and_accounting(
            copied_finalization,
            validation,
            execution_record,
            lineage,
            accounting_rows,
            selection,
            canonical_execution=accounting_execution,
        )

        detached_index = tuple(dict(row) for row in accounting_rows)
        detached_index[0]["slurm_array_index"] = "999"
        expect_failure(
            checkpoint_lib.validate_successor_selection_and_accounting,
            copied_finalization,
            validation,
            execution_record,
            lineage,
            detached_index,
            selection,
            canonical_execution=accounting_execution,
            contains="array index differs from canonical intent",
        )

        # Re-signing the copied snapshot after changing one byte must not make
        # it equivalent to the execution's canonical accounting directory.
        (copied_accounting / "sacct.psv").write_text(
            "12345_1|COMPLETED|0:0|extra\n", encoding="utf-8"
        )
        rewrite_recursive_checksums(copied_accounting)
        expect_failure(
            checkpoint_lib.validate_successor_selection_and_accounting,
            copied_finalization,
            validation,
            execution_record,
            lineage,
            accounting_rows,
            selection,
            canonical_execution=accounting_execution,
            contains="copied accounting differs from canonical evidence",
        )

    # Even with a byte-identical, re-signed accounting copy, a selected row
    # cannot point at an array index whose canonical terminal row failed.
    failed = json.loads(json.dumps(frozen))
    failed["task_states"]["1"] = "FAILED"
    failed["task_exit_codes"]["1"] = "1:0"
    failed_sacct = sacct_text.replace(
        "12345_1|COMPLETED|0:0|1|||\n",
        "12345_1|FAILED|1:0|1|||\n",
        1,
    )
    for directory in (canonical_accounting, copied_accounting):
        if (directory / "SHA256SUMS").exists():
            (directory / "SHA256SUMS").unlink()
        json_write(directory / "frozen.json", failed)
        (directory / "sacct.psv").write_text(failed_sacct, encoding="utf-8")
        write_recursive_checksums(directory)
    with patch.object(
        checkpoint_lib, "load_frozen_accounting", return_value=failed
    ), patch.object(
        checkpoint_lib, "load_attempt_intent", return_value=intent
    ):
        expect_failure(
            checkpoint_lib.validate_successor_selection_and_accounting,
            copied_finalization,
            validation,
            execution_record,
            lineage,
            accounting_rows,
            selection,
            canonical_execution=accounting_execution,
            contains="not a canonical successful array task",
        )

    pilot_tasks = fixture_tasks(root)
    pilot_seeds = tuple(range(20_000, 20_240))
    pilot_execution = SimpleNamespace(
        tasks=pilot_tasks,
        managed_child=SimpleNamespace(
            binding={
                "program": {
                    "program_directory": str((root / "canonical-program").resolve())
                }
            }
        ),
    )
    program = SimpleNamespace(
        child_tasks={"BC-S1": pilot_tasks},
        excluded_seeds=tuple(
            SimpleNamespace(seed=seed) for seed in pilot_seeds
        ),
    )
    seed_audit = {
        "sealed_pilot_seed_count": 240,
        "sealed_pilot_unique_seed_count": 240,
        "sealed_pilot_seed_set_hash": seed_set_hash(pilot_seeds),
    }
    with patch.object(
        checkpoint_lib, "load_production_program", return_value=program
    ):
        checkpoint_lib._validate_canonical_pilot_seed_registry(
            pilot_execution, seed_audit
        )
        resigned_bad_hash = dict(seed_audit)
        resigned_bad_hash["sealed_pilot_seed_set_hash"] = "0" * 64
        expect_failure(
            checkpoint_lib._validate_canonical_pilot_seed_registry,
            pilot_execution,
            resigned_bad_hash,
            contains="sealed-pilot seed registry mismatch",
        )
    drifted_program = SimpleNamespace(
        child_tasks={"BC-S1": pilot_tasks},
        excluded_seeds=tuple(
            SimpleNamespace(seed=seed)
            for seed in (*pilot_seeds[:-1], 99_999)
        ),
    )
    with patch.object(
        checkpoint_lib,
        "load_production_program",
        return_value=drifted_program,
    ):
        expect_failure(
            checkpoint_lib._validate_canonical_pilot_seed_registry,
            pilot_execution,
            seed_audit,
            contains="sealed-pilot seed registry mismatch",
        )


def check_successor_finalization_publish_contract(
    source: Path, root: Path
) -> None:
    """Exercise the real successor publication topology under 0555/0700."""

    manifest = fixture_successor(root).manifest

    def execution_at(directory: Path) -> SimpleNamespace:
        return SimpleNamespace(directory=directory.resolve(), manifest=manifest)

    publish_execution = root / "publish-successor"
    finalized_root = publish_execution / "finalized"
    finalized_root.mkdir(parents=True)
    finalized_root.chmod(0o700)
    publish_execution.chmod(0o555)
    execution = execution_at(publish_execution)
    expected = managed_finalization_directory(publish_execution, manifest)
    assert expected == finalized_root / SUCCESSOR_FINALIZATION_BUNDLE_NAME
    old_manifest = {"schema_version": EXECUTION_SCHEMA_VERSION_V2}
    assert managed_finalization_directory(
        root / "historical-execution", old_manifest
    ) == (root / "historical-execution" / "finalized").resolve()
    try:
        target, staging = finalizer._create_finalization_staging(
            execution, expected
        )
        assert target == expected
        assert staging.parent == finalized_root
        assert staging.name.startswith(".whole-child.tmp-")
        shutil.copytree(source, staging, dirs_exist_ok=True)
        checkpoint_lib.publish_directory_no_replace(staging, target)
        assert not staging.exists()
        validation, rows = load_managed_finalization(
            target, allow_test_mode=True, verify_root_files=False
        )
        assert validation["task_count"] == 32 and len(rows) == 32
        expect_failure(
            finalizer._create_finalization_staging,
            execution,
            expected,
            contains="refusing to overwrite",
        )
    finally:
        publish_execution.chmod(0o755)

    for label, residue_name, make_directory in (
        ("orphan", "orphan.json", False),
        ("staging", ".whole-child.tmp-stale", True),
        ("publish-lock", ".whole-child.publish.lock", False),
    ):
        candidate = root / f"publish-successor-{label}"
        container = candidate / "finalized"
        container.mkdir(parents=True)
        container.chmod(0o700)
        residue = container / residue_name
        if make_directory:
            residue.mkdir()
        else:
            residue.write_text("residue\n", encoding="utf-8")
        candidate.chmod(0o555)
        try:
            candidate_execution = execution_at(candidate)
            expect_failure(
                finalizer._create_finalization_staging,
                candidate_execution,
                managed_finalization_directory(candidate, manifest),
                contains="orphan or staging residue",
            )
        finally:
            candidate.chmod(0o755)

    order: list[str] = []

    def readiness_before_staging(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        order.append("readiness")
        assert not tuple((root / "ordering-successor" / "finalized").iterdir())
        return {"required": True, "lock_sha256": "b" * 64}

    def stop_at_staging(*_args: Any, **_kwargs: Any) -> tuple[Path, Path]:
        order.append("staging")
        raise RuntimeError("ordering sentinel")

    ordering_root = root / "ordering-successor"
    (ordering_root / "finalized").mkdir(parents=True)
    ordering_execution = execution_at(ordering_root)
    with patch.object(
        finalizer, "validate_selected_result_lineage", return_value=None
    ), patch.object(
        finalizer,
        "readiness_identity_for_execution",
        side_effect=readiness_before_staging,
    ), patch.object(
        finalizer,
        "_create_finalization_staging",
        side_effect=stop_at_staging,
    ):
        try:
            finalizer.finalize(
                ordering_execution,
                root,
                managed_finalization_directory(ordering_root, manifest),
                [],
                [],
                [],
                selection={},
                selection_file_sha256=None,
            )
        except RuntimeError as exc:
            assert str(exc) == "ordering sentinel"
        else:
            raise AssertionError("finalization ordering sentinel was not reached")
    assert order == ["readiness", "staging"]


def check_r4_whole_child_mutable_state(source: Path, root: Path) -> None:
    """Require empty finalized/ or exactly one validated whole-child leaf."""

    execution_root = root / "r4-successor"
    (execution_root / "intents" / "fixture-attempt").mkdir(parents=True)
    (execution_root / "attempts" / "fixture-attempt").mkdir(parents=True)
    finalized = execution_root / "finalized"
    finalized.mkdir()
    execution = SimpleNamespace(directory=execution_root.resolve())
    state = SimpleNamespace(status="prepared")
    with patch.object(
        phase2b_lib, "list_attempt_ids", return_value=("fixture-attempt",)
    ), patch.object(phase2b_lib, "attempt_state", return_value=state):
        _validate_successor_mutable_state_after_intent(execution)

        whole_child = finalized / SUCCESSOR_FINALIZATION_BUNDLE_NAME
        shutil.copytree(source, whole_child)
        with patch.object(
            checkpoint_lib,
            "load_managed_finalization",
            return_value=({"valid": True}, ()),
        ) as loader:
            _validate_successor_mutable_state_after_intent(execution)
            loader.assert_called_once_with(
                whole_child, allow_test_mode=False
            )

        orphan = finalized / "orphan.json"
        orphan.write_text("{}\n", encoding="utf-8")
        expect_failure(
            _validate_successor_mutable_state_after_intent,
            execution,
            contains="exact whole-child",
        )
        orphan.unlink()
        shutil.rmtree(whole_child)

        partial = finalized / SUCCESSOR_FINALIZATION_BUNDLE_NAME
        partial.mkdir()
        expect_failure(
            _validate_successor_mutable_state_after_intent,
            execution,
            contains="checksum manifest",
        )
        partial.rmdir()

        partial.symlink_to(source, target_is_directory=True)
        expect_failure(
            _validate_successor_mutable_state_after_intent,
            execution,
            contains="exact whole-child",
        )
        partial.unlink()

        for residue_name, directory in (
            (".whole-child.tmp-stale", True),
            (".whole-child.publish.lock", False),
        ):
            residue = finalized / residue_name
            if directory:
                residue.mkdir()
            else:
                residue.write_text("residue\n", encoding="utf-8")
            expect_failure(
                _validate_successor_mutable_state_after_intent,
                execution,
                contains="exact whole-child",
            )
            if directory:
                residue.rmdir()
            else:
                residue.unlink()


def check_successor_provenance(finalization: Path, root: Path) -> None:
    successor_finalization, lineage = write_successor_finalization(
        finalization, root
    )
    assert successor_finalization.name == SUCCESSOR_FINALIZATION_BUNDLE_NAME
    check_successor_finalization_publish_contract(
        successor_finalization, root / "successor-publish-contract"
    )
    check_r4_whole_child_mutable_state(
        successor_finalization, root / "successor-r4-finalized-state"
    )
    validation, rows = load_managed_finalization(
        successor_finalization,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert validation["recovery_lineage"] == lineage
    successor_checkpoint = build_checkpoint(
        successor_finalization,
        root / "successor-checkpoints",
        allow_test_mode=True,
        verify_root_files=False,
    )
    loaded = load_production_checkpoint(
        successor_checkpoint,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert loaded.manifest["recovery_lineage"] == lineage
    assert loaded.manifest["source_children"]["recovery_lineage"] == lineage
    assert loaded.manifest["source_finalization"]["directory"] == str(
        successor_finalization
    )
    assert len(rows) == 32

    tampered = root / "successor-lineage-tamper"
    shutil.copytree(successor_finalization, tampered)
    selection_path = tampered / "selection_record.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    del selection["recovery_lineage"]
    json_write(selection_path, selection)
    rewrite_recursive_checksums(tampered)
    expect_failure(
        load_managed_finalization,
        tampered,
        allow_test_mode=True,
        verify_root_files=False,
        contains="lost recovery lineage",
    )

    authority_tamper = root / "successor-authority-tamper"
    shutil.copytree(successor_finalization, authority_tamper)
    for name in (
        "validation_report.json",
        "event_audit.json",
        "seed_audit.json",
        "selection_record.json",
    ):
        path = authority_tamper / name
        value = json.loads(path.read_text(encoding="utf-8"))
        changed = value["recovery_lineage"]
        changed["recovery_authority"]["zero_consumption"][
            "events_consumed"
        ] = 999
        unhashed = dict(changed)
        unhashed.pop("lineage_hash")
        changed["lineage_hash"] = sha256_bytes(canonical_json(unhashed))
        json_write(path, value)
    rewrite_recursive_checksums(authority_tamper)
    expect_failure(
        load_managed_finalization,
        authority_tamper,
        allow_test_mode=True,
        verify_root_files=False,
        contains="authority hash mismatch",
    )

    zero_tamper = root / "successor-zero-consumption-tamper"
    shutil.copytree(successor_finalization, zero_tamper)
    replacement_authority_hash: str | None = None
    for name in (
        "validation_report.json",
        "event_audit.json",
        "seed_audit.json",
        "selection_record.json",
    ):
        path = zero_tamper / name
        value = json.loads(path.read_text(encoding="utf-8"))
        changed = value["recovery_lineage"]
        authority = changed["recovery_authority"]
        authority["zero_consumption"]["events_consumed"] = 999
        unhashed_authority = dict(authority)
        unhashed_authority.pop("authority_hash")
        authority["authority_hash"] = sha256_bytes(
            canonical_json(unhashed_authority)
        )
        replacement_authority_hash = authority["authority_hash"]
        unhashed = dict(changed)
        unhashed.pop("lineage_hash")
        changed["lineage_hash"] = sha256_bytes(canonical_json(unhashed))
        if name == "validation_report.json":
            value["execution"]["recovery_authority_hash"] = authority[
                "authority_hash"
            ]
        json_write(path, value)
    assert replacement_authority_hash is not None
    rewrite_recursive_checksums(zero_tamper)
    expect_failure(
        load_managed_finalization,
        zero_tamper,
        allow_test_mode=True,
        verify_root_files=False,
        contains="does not prove exact zero consumption",
    )

    seed_tamper = root / "successor-seed-tamper"
    shutil.copytree(successor_finalization, seed_tamper)
    seed_path = seed_tamper / "seed_audit.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    seed["production_unique_seed_count"] = 1
    seed["production_seed_set_hash"] = "f" * 64
    seed["tasks"][0]["seed1"] = 999999999
    json_write(seed_path, seed)
    rewrite_recursive_checksums(seed_tamper)
    expect_failure(
        load_managed_finalization,
        seed_tamper,
        allow_test_mode=True,
        verify_root_files=False,
        contains="successor seed audit",
    )

    selection_tamper = root / "successor-selection-tamper"
    shutil.copytree(successor_finalization, selection_tamper)
    selection_path = selection_tamper / "selection_record.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["predecessor_failed_outputs_included"] = True
    selection["selected_results"] = "predecessor-failures-allowed"
    json_write(selection_path, selection)
    rewrite_recursive_checksums(selection_tamper)
    expect_failure(
        load_managed_finalization,
        selection_tamper,
        allow_test_mode=True,
        verify_root_files=False,
        contains="selection record is inconsistent",
    )

    accounting_tamper = root / "successor-accounting-tamper"
    shutil.copytree(successor_finalization, accounting_tamper)
    frozen_path = accounting_tamper / "accounting" / "fixture-attempt" / "frozen.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    frozen["job_id"] = "50532143"
    json_write(frozen_path, frozen)
    rewrite_recursive_checksums(accounting_tamper)
    expect_failure(
        load_managed_finalization,
        accounting_tamper,
        allow_test_mode=True,
        verify_root_files=False,
        contains="copied predecessor scheduler accounting",
    )

    successor = fixture_successor(root)
    valid = SimpleNamespace(
        attempt={"attempt_id": "20260719T200000Z-predecessor-retry"},
        job_id="60000000",
        marker_path=successor.directory / "attempts/new/task_result.json",
        paths={"root": successor.directory / "attempts/new/output.root"},
    )
    with patch.object(
        finalizer,
        "load_frozen_accounting",
        return_value={"job_id": "60000000"},
    ):
        assert finalizer.validate_selected_result_lineage(
            successor, [valid], ["20260719T200000Z-predecessor-retry"]
        ) == recovery_lineage_from_execution(successor)
    with patch.object(
        finalizer,
        "load_frozen_accounting",
        return_value={"job_id": "50532143"},
    ):
        expect_failure(
            finalizer.validate_selected_result_lineage,
            successor,
            [valid],
            ["20260719T200000Z-predecessor-retry"],
            contains="copied predecessor scheduler accounting",
        )
    predecessor_job = SimpleNamespace(
        attempt={"attempt_id": "20260719T200000Z-predecessor-retry"},
        job_id="50532143",
        marker_path=successor.directory / "attempts/new/task_result.json",
        paths={"root": successor.directory / "attempts/new/output.root"},
    )
    expect_failure(
        finalizer.validate_selected_result_lineage,
        successor,
        [predecessor_job],
        [],
        contains="selected predecessor scheduler job",
    )
    predecessor = SimpleNamespace(
        attempt={"attempt_id": "20260718T175107Z-initial"},
        job_id="50532143",
        marker_path=successor.directory / "attempts/old/task_result.json",
        paths={},
    )
    expect_failure(
        finalizer.validate_selected_result_lineage,
        successor,
        [predecessor],
        ["20260718T175107Z-initial"],
        contains="predecessor attempt evidence",
    )


def check_successor_v3_provenance(finalization: Path, root: Path) -> None:
    """Exercise the dual-failure v3 lineage through checkpoint publication."""

    successor = fixture_successor_v3(root)
    successor_finalization, lineage = write_successor_finalization(
        finalization,
        root,
        successor=successor,
    )
    assert lineage["schema_version"] == RECOVERY_LINEAGE_SCHEMA_VERSION_V2
    assert lineage["predecessor_incident"]["incident_id"] == (
        "fixture-zero-consumption-incident"
    )
    assert lineage["failed_r3_preflight"]["scheduler"]["job_id"] == "50544247"
    validation, rows = load_managed_finalization(
        successor_finalization,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert validation["recovery_lineage"] == lineage and len(rows) == 32
    checkpoint = build_checkpoint(
        successor_finalization,
        root / "successor-v3-checkpoints",
        allow_test_mode=True,
        verify_root_files=False,
    )
    loaded = load_production_checkpoint(
        checkpoint,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert loaded.manifest["recovery_lineage"] == lineage

    # Re-signing every containing JSON cannot detach the failed-R3 record from
    # the v3 recovery authority.
    tampered = root / "successor-v3-failed-r3-tamper"
    shutil.copytree(successor_finalization, tampered)
    for name in (
        "validation_report.json",
        "event_audit.json",
        "seed_audit.json",
        "selection_record.json",
    ):
        path = tampered / name
        value = json.loads(path.read_text(encoding="utf-8"))
        changed = value["recovery_lineage"]
        changed["failed_r3_preflight"]["scheduler"]["job_id"] = "59999999"
        unhashed = dict(changed)
        unhashed.pop("lineage_hash")
        changed["lineage_hash"] = sha256_bytes(canonical_json(unhashed))
        json_write(path, value)
    rewrite_recursive_checksums(tampered)
    expect_failure(
        load_managed_finalization,
        tampered,
        allow_test_mode=True,
        verify_root_files=False,
        contains="failed-R3 binding changed",
    )

    valid = SimpleNamespace(
        attempt={"attempt_id": "20260720T120000Z-predecessor-retry"},
        job_id="60000000",
        marker_path=successor.directory / "attempts/new/task_result.json",
        paths={"root": successor.directory / "attempts/new/output.root"},
    )
    with patch.object(
        finalizer,
        "load_frozen_accounting",
        return_value={"job_id": "60000000"},
    ):
        assert finalizer.validate_selected_result_lineage(
            successor, [valid], ["20260720T120000Z-predecessor-retry"]
        ) == lineage
    for forbidden_job in ("50532143", "50544247"):
        forbidden = SimpleNamespace(
            attempt={"attempt_id": "20260720T120000Z-predecessor-retry"},
            job_id=forbidden_job,
            marker_path=successor.directory / "attempts/new/task_result.json",
            paths={"root": successor.directory / "attempts/new/output.root"},
        )
        expect_failure(
            finalizer.validate_selected_result_lineage,
            successor,
            [forbidden],
            [],
            contains="selected predecessor scheduler job",
        )


def check_successor_v4_provenance(finalization: Path, root: Path) -> None:
    """Exercise the v4 rejected-probe lineage through finalization/checkpoint."""

    successor = fixture_successor_v4(root)
    successor_finalization, lineage = write_successor_finalization(
        finalization,
        root,
        successor=successor,
    )
    assert lineage["schema_version"] == RECOVERY_LINEAGE_SCHEMA_VERSION_V3
    assert lineage["failed_r3_preflight"]["scheduler"]["job_id"] == "50544247"
    assert lineage["rejected_r3_preflight"]["scheduler"]["job_id"] == "50548308"
    assert (
        lineage["administrative_r3_launch_rejection"]["job_id"] == "50547698"
    )
    assert (
        lineage["administrative_r3_launch_rejection"]["compute_preflight"]
        is False
    )
    validation, rows = load_managed_finalization(
        successor_finalization,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert validation["recovery_lineage"] == lineage and len(rows) == 32
    checkpoint = build_checkpoint(
        successor_finalization,
        root / "successor-v4-checkpoints",
        allow_test_mode=True,
        verify_root_files=False,
    )
    loaded = load_production_checkpoint(
        checkpoint,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert loaded.manifest["recovery_lineage"] == lineage

    # Even after re-signing the containing records, neither rejected compute
    # evidence nor the administrative/non-compute classification may drift.
    for label, mutate, expected in (
        (
            "rejected-job",
            lambda value: value["recovery_lineage"]["rejected_r3_preflight"][
                "scheduler"
            ].__setitem__("job_id", "59999999"),
            "rejected-R3 binding changed",
        ),
        (
            "admin-compute",
            lambda value: value["recovery_lineage"][
                "administrative_r3_launch_rejection"
            ].__setitem__("compute_preflight", True),
            "administrative R3 binding changed",
        ),
    ):
        tampered = root / f"successor-v4-{label}-tamper"
        shutil.copytree(successor_finalization, tampered)
        for name in (
            "validation_report.json",
            "event_audit.json",
            "seed_audit.json",
            "selection_record.json",
        ):
            path = tampered / name
            value = json.loads(path.read_text(encoding="utf-8"))
            mutate(value)
            changed = value["recovery_lineage"]
            unhashed = dict(changed)
            unhashed.pop("lineage_hash")
            changed["lineage_hash"] = sha256_bytes(canonical_json(unhashed))
            json_write(path, value)
        rewrite_recursive_checksums(tampered)
        expect_failure(
            load_managed_finalization,
            tampered,
            allow_test_mode=True,
            verify_root_files=False,
            contains=expected,
        )

    valid = SimpleNamespace(
        attempt={"attempt_id": "20260721T120000Z-predecessor-retry"},
        job_id="60000001",
        marker_path=successor.directory / "attempts/new/task_result.json",
        paths={"root": successor.directory / "attempts/new/output.root"},
    )
    with patch.object(
        finalizer,
        "load_frozen_accounting",
        return_value={"job_id": "60000001"},
    ):
        assert finalizer.validate_selected_result_lineage(
            successor, [valid], ["20260721T120000Z-predecessor-retry"]
        ) == lineage
    for forbidden_job in ("50532143", "50544247", "50547698", "50548308"):
        forbidden = SimpleNamespace(
            attempt={"attempt_id": "20260721T120000Z-predecessor-retry"},
            job_id=forbidden_job,
            marker_path=successor.directory / "attempts/new/task_result.json",
            paths={"root": successor.directory / "attempts/new/output.root"},
        )
        expect_failure(
            finalizer.validate_selected_result_lineage,
            successor,
            [forbidden],
            [],
            contains="selected predecessor scheduler job",
        )


def check_phase2c_v6_provenance(finalization: Path, root: Path) -> None:
    """Run v6 lineage through whole-child finalization and BC-ONLY-S1."""

    successor = fixture_phase2c_successor(root)
    successor_finalization, lineage = write_successor_finalization(
        finalization,
        root,
        successor=successor,
    )
    assert lineage["schema_version"] == RECOVERY_LINEAGE_SCHEMA_VERSION_V4
    validation, rows = load_managed_finalization(
        successor_finalization,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert validation["recovery_lineage"] == lineage
    assert validation["execution"]["production_equivalence_hash"] == "7" * 64
    assert len(rows) == 32
    assert {row["slurm_job_id"] for row in rows} == {"12345"}
    assert not (
        {row["slurm_job_id"] for row in rows}
        & set(lineage["excluded_job_ids"])
    )
    checkpoint = build_checkpoint(
        successor_finalization,
        root / "execution-v6-checkpoints",
        allow_test_mode=True,
        verify_root_files=False,
    )
    loaded = load_production_checkpoint(
        checkpoint,
        allow_test_mode=True,
        verify_root_files=False,
    )
    assert loaded.manifest["execution"]["schema_version"] == (
        EXECUTION_V6_SCHEMA_VERSION
    )
    assert loaded.manifest["recovery_lineage"] == lineage
    assert loaded.manifest["source_children"]["recovery_lineage"] == lineage


def check_candidate_selection(root: Path) -> None:
    tasks = tuple(
        CampaignTask(
            task_index=index,
            logical_task_id=str(row["logical_task_id"]),
            stage="production",
            tile_thickness_mm=int(row["tile_thickness_mm"]),
            sipm_layout="back-center",
            absorber_transverse_mm=500,
            x_mm=0,
            y_mm=0,
            seed_block=int(row["seed_block"]),
            events=250,
            seed1=int(row["seed1"]),
            seed2=int(row["seed2"]),
            configuration_hash=HASH,
        )
        for index, row in enumerate(fixture_rows(root), 1)
    )
    execution = SimpleNamespace(tasks=tasks)

    def candidate(task: CampaignTask, attempt_id: str) -> finalizer.ManagedCandidate:
        return finalizer.ManagedCandidate(
            task=task,
            attempt={"attempt_id": attempt_id},
            array_index=task.task_index,
            job_id="12345",
            marker_path=root / "task_result.json",
            marker={},
            paths={},
            summary={},
            event_report={},
        )

    candidates = {task.logical_task_id: [candidate(task, "attempt-one")] for task in tasks}
    assert len(finalizer.select_candidates(execution, candidates, {})) == 32
    first = tasks[0].logical_task_id
    candidates[first].append(candidate(tasks[0], "attempt-two"))
    expect_failure(
        finalizer.select_candidates,
        execution,
        candidates,
        {},
        contains="duplicate successful",
    )
    selected = finalizer.select_candidates(
        execution, candidates, {first: "attempt-two"}
    )
    assert selected[0].attempt["attempt_id"] == "attempt-two"
    expect_failure(
        finalizer.select_candidates,
        execution,
        candidates,
        {first: "missing-attempt"},
        contains="not one valid success",
    )

    selection_path = root / "selection.tsv"
    selection_path.write_text(
        "logical_task_id\tattempt_id\n"
        f"{first}\tattempt-two\n",
        encoding="utf-8",
    )
    parsed, selection_sha = finalizer.load_selection(selection_path)
    assert parsed == {first: "attempt-two"}
    assert selection_sha == sha256_file(selection_path)
    duplicate_selection = root / "duplicate-selection.tsv"
    duplicate_selection.write_text(
        "logical_task_id\tattempt_id\n"
        f"{first}\tattempt-one\n"
        f"{first}\tattempt-two\n",
        encoding="utf-8",
    )
    expect_failure(
        finalizer.load_selection,
        duplicate_selection,
        contains="empty or duplicate",
    )

    accounting = root / "sacct.psv"
    accounting.write_text(
        "12345_1|COMPLETED|0:0|9|||\n"
        "12345_1.batch|COMPLETED|0:0|9|180000K|0\n",
        encoding="utf-8",
    )
    rows = finalizer._accounting_rows(accounting)
    assert rows[0]["JobIDRaw"] == "12345_1"
    assert rows[0]["State"] == "COMPLETED"
    assert rows[0]["ExitCode"] == "0:0"


def check_recursive_tamper(checkpoint_dir: Path, scratch: Path) -> None:
    digest_case = scratch / "checksum-digest-tamper"
    shutil.copytree(checkpoint_dir, digest_case)
    (digest_case / "event_audit.json").write_text("{}\n", encoding="utf-8")
    expect_failure(
        verify_recursive_checksums,
        digest_case,
        contains="checksum mismatch",
    )

    path_case = scratch / "checksum-path-tamper"
    shutil.copytree(checkpoint_dir, path_case)
    lines = (path_case / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    digest, _ = lines[0].split("  ", 1)
    lines[0] = f"{digest}  ../escape"
    os.chmod(path_case / "SHA256SUMS", 0o644)
    (path_case / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    expect_failure(
        verify_recursive_checksums,
        path_case,
        contains="unsafe checksum-manifest path",
    )

    symlink_case = scratch / "checksum-symlink-tamper"
    shutil.copytree(checkpoint_dir, symlink_case)
    (symlink_case / "symlink.json").symlink_to(
        symlink_case / "event_audit.json"
    )
    expect_failure(
        verify_recursive_checksums,
        symlink_case,
        contains="non-regular evidence file",
    )


def check_checkpoint_semantics(
    checkpoint_dir: Path,
    rows: tuple[dict[str, str], ...],
    scratch: Path,
) -> None:
    gap = [dict(row) for row in rows[:-1]]
    expect_failure(
        validate_bc_only_s1_rows,
        gap,
        verify_root_files=False,
        contains="exactly 32 tasks",
    )

    overlap = [dict(row) for row in rows]
    overlap[-1]["seed_block"] = "14"
    expect_failure(
        validate_bc_only_s1_rows,
        overlap,
        verify_root_files=False,
        contains="duplicate",
    )

    fixed = [dict(row) for row in rows]
    fixed[0]["logical_task_id"] = "fixed-t04-back-center-b00"
    fixed[0]["stage"] = "FIXED"
    expect_failure(
        validate_bc_only_s1_rows,
        fixed,
        verify_root_files=False,
        contains="outside exact",
    )

    wrong_prefix_parent = scratch / "wrong-prefix-parent"
    wrong_prefix_parent.mkdir()
    wrong_prefix = wrong_prefix_parent / f"wrong-prefix-{checkpoint_dir.name}"
    shutil.copytree(checkpoint_dir, wrong_prefix)
    expect_failure(
        load_production_checkpoint,
        wrong_prefix,
        allow_test_mode=True,
        verify_root_files=False,
        contains="directory name",
    )

    wrong_state_parent = scratch / "wrong-state-parent"
    wrong_state_parent.mkdir()
    wrong_state = wrong_state_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, wrong_state)
    manifest = json.loads((wrong_state / "checkpoint.json").read_text(encoding="utf-8"))
    manifest["state_id"] = "FIXED"
    json_write(wrong_state / "checkpoint.json", manifest)
    wrong_state = reseal_checkpoint(wrong_state)
    expect_failure(
        load_production_checkpoint,
        wrong_state,
        allow_test_mode=True,
        verify_root_files=False,
        contains="not exact BC-ONLY-S1",
    )


def check_source_readiness_and_seed_identity(
    checkpoint_dir: Path, scratch: Path
) -> None:
    source_parent = scratch / "source-identity-parent"
    source_parent.mkdir()
    source_case = source_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, source_case)
    source = json.loads(
        (source_case / "source_finalization.json").read_text(encoding="utf-8")
    )
    source["directory"] = "/tampered/finalization"
    json_write(source_case / "source_finalization.json", source)
    rewrite_recursive_checksums(source_case)
    expect_failure(
        load_production_checkpoint,
        source_case,
        allow_test_mode=True,
        verify_root_files=False,
        contains="source-finalization record mismatch",
    )

    readiness_parent = scratch / "readiness-identity-parent"
    readiness_parent.mkdir()
    readiness_case = readiness_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, readiness_case)
    manifest = json.loads(
        (readiness_case / "checkpoint.json").read_text(encoding="utf-8")
    )
    manifest["readiness"] = {"required": False, "lock_sha256": None}
    json_write(readiness_case / "checkpoint.json", manifest)
    readiness_case = reseal_checkpoint(readiness_case)
    expect_failure(
        load_production_checkpoint,
        readiness_case,
        allow_test_mode=True,
        require_readiness=True,
        verify_root_files=False,
        contains="lacks the required Phase-2B readiness identity",
    )

    seed_parent = scratch / "seed-audit-parent"
    seed_parent.mkdir()
    seed_case = seed_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, seed_case)
    seed_audit = json.loads(
        (seed_case / "seed_audit.json").read_text(encoding="utf-8")
    )
    seed_audit["production_seed_count"] = 63
    seed_audit["overlap_count"] = 1
    json_write(seed_case / "seed_audit.json", seed_audit)
    seed_case = reseal_checkpoint(seed_case)
    expect_failure(
        load_production_checkpoint,
        seed_case,
        allow_test_mode=True,
        verify_root_files=False,
        contains="seed audit is invalid",
    )


def check_publish_safety(scratch: Path) -> None:
    failure_root = scratch / "failure-cleanup"
    failure_root.mkdir()
    with patch.object(
        checkpoint_lib,
        "publish_directory_no_replace",
        side_effect=OSError("injected publish failure"),
    ):
        expect_failure(
            write_checkpoint_atomic,
            failure_root,
            manifest_payload={"schema_version": "failure-fixture-v1"},
            task_index_text="task_index\n1\n",
            configuration_summary_text="fixture\n",
            event_audit={"valid": True},
            seed_audit={"valid": True},
            source_children={"children": []},
            source_finalization={"fixture": True},
            contains="injected publish failure",
        )
    assert not list(failure_root.glob(".bc-only-s1.tmp-*"))
    assert not list(failure_root.glob("bc-only-s1-*"))

    # A target that already exists before lock-protected publication is
    # rejected before either the fast path or GPFS fallback can mutate it.
    no_replace_root = scratch / "preexisting-target"
    no_replace_root.mkdir()
    source = no_replace_root / "source"
    target = no_replace_root / "target"
    source.mkdir()
    (source / "sentinel.txt").write_text("source\n", encoding="utf-8")
    target.mkdir()
    expect_failure(
        checkpoint_lib.publish_directory_no_replace,
        source,
        target,
        contains="refusing to overwrite",
    )
    assert source.is_dir() and (source / "sentinel.txt").is_file()
    assert target.is_dir() and not any(target.iterdir())

    concurrency_root = scratch / "concurrent-publish"
    concurrency_root.mkdir()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=concurrent_publish_worker,
            args=(str(concurrency_root), barrier, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
            raise AssertionError("concurrent checkpoint publisher did not terminate")
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    successes = [value for status, value in outcomes if status == "success"]
    failures = [value for status, value in outcomes if status == "failure"]
    assert len(successes) == 1, f"concurrent publish successes: {outcomes}"
    assert len(failures) == 1, f"concurrent publish failures: {outcomes}"
    failure_text = failures[0].lower()
    assert any(
        token in failure_text for token in ("overwrite", "exist", "publication")
    ), failures[0]
    targets = list(concurrency_root.glob("bc-only-s1-*"))
    assert len(targets) == 1
    verify_recursive_checksums(targets[0])
    assert not list(concurrency_root.glob(".bc-only-s1.tmp-*"))


def check_gpfs_publication_fallback(scratch: Path) -> None:
    """Exercise the cooperative exclusive-placeholder Linux fallback."""

    root = scratch / "gpfs-publication-fallback"
    root.mkdir()

    def pair(label: str) -> tuple[Path, Path]:
        case = root / label
        case.mkdir()
        source = case / "source"
        target = case / "target"
        source.mkdir()
        (source / "sentinel.txt").write_text("source\n", encoding="utf-8")
        return source, target

    def unsupported(error: int) -> OSError:
        return OSError(error, os.strerror(error))

    # The supported renameat2/renamex path remains the preferred path and
    # never enters the placeholder fallback.
    source, target = pair("fast-path")

    def fixture_fast_path(temporary: Path, destination: Path) -> None:
        os.rename(temporary, destination)

    with patch.object(
        checkpoint_lib,
        "_rename_directory_no_replace",
        side_effect=fixture_fast_path,
    ), patch.object(
        checkpoint_lib,
        "_publish_directory_with_owned_placeholder",
        side_effect=AssertionError("fast path entered GPFS fallback"),
    ):
        checkpoint_lib.publish_directory_no_replace(source, target)
    assert not source.exists()
    assert (target / "sentinel.txt").read_text(encoding="utf-8") == "source\n"

    # Linux fallback is restricted to the three unsupported-operation errnos.
    for error in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
        source, target = pair(f"unsupported-{error}")
        with patch.object(checkpoint_lib.sys, "platform", "linux"), patch.object(
            checkpoint_lib,
            "_rename_directory_no_replace",
            side_effect=unsupported(error),
        ):
            checkpoint_lib.publish_directory_no_replace(source, target)
        assert not source.exists()
        assert (target / "sentinel.txt").read_text(encoding="utf-8") == "source\n"

    # A target that predates lock acquisition is rejected before either move
    # primitive is called, including when it is an empty directory.
    source, target = pair("preexisting-target")
    target.mkdir()
    with patch.object(
        checkpoint_lib,
        "_rename_directory_no_replace",
        side_effect=AssertionError("preexisting target reached rename"),
    ):
        expect_failure(
            checkpoint_lib.publish_directory_no_replace,
            source,
            target,
            contains="refusing to overwrite",
        )
    assert source.is_dir() and (source / "sentinel.txt").is_file()
    assert target.is_dir() and not any(target.iterdir())

    for kind in ("file", "symlink"):
        source, target = pair(f"preexisting-{kind}")
        if kind == "file":
            target.write_text("preexisting\n", encoding="utf-8")
        else:
            target.symlink_to(source, target_is_directory=True)
        with patch.object(
            checkpoint_lib,
            "_rename_directory_no_replace",
            side_effect=AssertionError("preexisting target reached rename"),
        ):
            expect_failure(
                checkpoint_lib.publish_directory_no_replace,
                source,
                target,
                contains="refusing to overwrite",
            )
        assert source.is_dir() and (source / "sentinel.txt").is_file()
        if kind == "file":
            assert target.read_text(encoding="utf-8") == "preexisting\n"
        else:
            assert target.is_symlink()

    original_plain_rename = checkpoint_lib._plain_directory_rename

    # An uncooperative writer making our placeholder nonempty forces rename
    # to fail.  Both the source and mutated placeholder remain as evidence.
    source, target = pair("nonempty-placeholder")

    def mutate_placeholder_directory_then_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        placeholder_fd = os.open(
            destination_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            dir_fd=destination_parent_fd,
        )
        try:
            descriptor = os.open(
                "intruder.txt",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=placeholder_fd,
            )
            os.close(descriptor)
        finally:
            os.close(placeholder_fd)
        original_plain_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
        )

    with patch.object(checkpoint_lib.sys, "platform", "linux"), patch.object(
        checkpoint_lib,
        "_rename_directory_no_replace",
        side_effect=unsupported(errno.EINVAL),
    ), patch.object(
        checkpoint_lib,
        "_plain_directory_rename",
        side_effect=mutate_placeholder_directory_then_rename,
    ):
        expect_failure(checkpoint_lib.publish_directory_no_replace, source, target)
    assert source.is_dir() and (source / "sentinel.txt").is_file()
    assert (target / "intruder.txt").is_file()

    # If the source name is swapped in the final syscall race, the replacement
    # may move but cannot pass the recorded source-inode validation.
    source, target = pair("source-swap")
    detached = source.with_name("detached-original")

    def swap_source_then_rename(
        source_parent_fd: int,
        source_name: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        os.rename(
            source_name,
            detached.name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=source_parent_fd,
        )
        os.mkdir(source_name, 0o700, dir_fd=source_parent_fd)
        original_plain_rename(
            source_parent_fd,
            source_name,
            destination_parent_fd,
            destination_name,
        )

    with patch.object(checkpoint_lib.sys, "platform", "linux"), patch.object(
        checkpoint_lib,
        "_rename_directory_no_replace",
        side_effect=unsupported(errno.EINVAL),
    ), patch.object(
        checkpoint_lib,
        "_plain_directory_rename",
        side_effect=swap_source_then_rename,
    ):
        expect_failure(
            checkpoint_lib.publish_directory_no_replace,
            source,
            target,
            contains="not the original source",
        )
    assert (detached / "sentinel.txt").is_file()
    assert target.is_dir() and not any(target.iterdir())

    # An arbitrary failure of the plain rename is never retried or rolled
    # back; the owned empty placeholder remains and blocks later publication.
    source, target = pair("plain-rename-failure")
    with patch.object(checkpoint_lib.sys, "platform", "linux"), patch.object(
        checkpoint_lib,
        "_rename_directory_no_replace",
        side_effect=unsupported(errno.EINVAL),
    ), patch.object(
        checkpoint_lib,
        "_plain_directory_rename",
        side_effect=OSError(errno.EIO, os.strerror(errno.EIO)),
    ):
        expect_failure(checkpoint_lib.publish_directory_no_replace, source, target)
    assert source.is_dir() and (source / "sentinel.txt").is_file()
    assert target.is_dir() and not any(target.iterdir())

    # Errors other than the explicit unsupported-operation set never enter
    # the fallback and therefore create no target placeholder.
    source, target = pair("arbitrary-fast-path-error")
    with patch.object(checkpoint_lib.sys, "platform", "linux"), patch.object(
        checkpoint_lib,
        "_rename_directory_no_replace",
        side_effect=unsupported(errno.EIO),
    ), patch.object(
        checkpoint_lib,
        "_publish_directory_with_owned_placeholder",
        side_effect=AssertionError("arbitrary errno entered GPFS fallback"),
    ):
        expect_failure(checkpoint_lib.publish_directory_no_replace, source, target)
    assert source.is_dir() and not target.exists()

    # Collision errnos from the fast primitive are never interpreted as an
    # unsupported filesystem feature and never create a placeholder.
    for error in (errno.EEXIST, errno.ENOTEMPTY):
        source, target = pair(f"collision-errno-{error}")
        with patch.object(checkpoint_lib.sys, "platform", "linux"), patch.object(
            checkpoint_lib,
            "_rename_directory_no_replace",
            side_effect=unsupported(error),
        ), patch.object(
            checkpoint_lib,
            "_publish_directory_with_owned_placeholder",
            side_effect=AssertionError("collision errno entered GPFS fallback"),
        ):
            expect_failure(checkpoint_lib.publish_directory_no_replace, source, target)
        assert source.is_dir() and not target.exists()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="steel-module-finalize-check-") as raw:
        scratch = Path(raw).resolve()
        finalization = write_finalization(scratch)
        check_candidate_selection(scratch)
        check_execution_dispatch(scratch)
        check_phase2c_lineage_contract(scratch)
        check_phase2c_operational_readiness_snapshot(scratch)
        check_successor_provenance(finalization, scratch)
        check_successor_v3_provenance(
            finalization, scratch / "successor-v3-provenance"
        )
        check_successor_v4_provenance(
            finalization, scratch / "successor-v4-provenance"
        )
        check_phase2c_v6_provenance(
            finalization, scratch / "execution-v6-provenance"
        )
        check_canonical_finalization_rebinding(
            scratch / "canonical-finalization-rebinding"
        )

        validation, rows = load_managed_finalization(
            finalization, allow_test_mode=True, verify_root_files=False
        )
        assert validation["task_count"] == 32 and len(rows) == 32
        expect_failure(
            load_managed_finalization,
            finalization,
            verify_root_files=False,
            contains="refuses test-mode",
        )

        output_root = scratch / "checkpoints"
        checkpoint_dir = build_checkpoint(
            finalization,
            output_root,
            allow_test_mode=True,
            verify_root_files=False,
        )
        checkpoint = load_production_checkpoint(
            checkpoint_dir,
            allow_test_mode=True,
            verify_root_files=False,
        )
        assert checkpoint.manifest["state_id"] == "BC-ONLY-S1"
        assert checkpoint.manifest["task_count"] == 32
        assert checkpoint.manifest["event_count"] == 8000
        assert checkpoint.manifest["accepted_statistical_evidence"] is False
        assert checkpoint.manifest["test_mode"] is True
        assert len(checkpoint.task_rows) == 32
        assert not list(checkpoint_dir.rglob("*.root"))
        verify_recursive_checksums(checkpoint_dir)
        expect_failure(
            load_production_checkpoint,
            checkpoint_dir,
            verify_root_files=False,
            contains="refuses a test-mode",
        )
        expect_failure(
            build_checkpoint,
            finalization,
            output_root,
            allow_test_mode=True,
            verify_root_files=False,
            contains="refusing to overwrite",
        )
        check_recursive_tamper(checkpoint_dir, scratch)
        check_checkpoint_semantics(checkpoint_dir, rows, scratch)
        check_source_readiness_and_seed_identity(checkpoint_dir, scratch)

        unexpected_case = scratch / "unexpected-file-tamper"
        shutil.copytree(checkpoint_dir, unexpected_case)
        (unexpected_case / "unexpected.txt").write_text("tamper\n", encoding="utf-8")
        expect_failure(
            verify_recursive_checksums,
            unexpected_case,
            contains="file set mismatch",
        )
        check_publish_safety(scratch)
        check_gpfs_publication_fallback(scratch)
        assert sha256_file(checkpoint_dir / "SHA256SUMS")

    print(
        "steel-module managed finalization/checkpoint: PASS "
        "(32 tasks, 8000 events, v1-v6 successor lineage, "
        "GPFS publication fallback, tamper/concurrency/BC-ONLY-S1 gates)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
