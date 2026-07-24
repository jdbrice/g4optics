#!/usr/bin/env python3
"""Audit one managed BC-S1 task and create its immutable result marker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from record_realistic_neutron_task_result import (
    artifact_record,
    integer_field,
    read_single_csv_row,
    resolve_run_artifact,
    write_exclusive_json,
)
from record_steel_module_task_result import LAYOUT_CONTRACTS, validate_run_config
from steel_module_campaign_lib import load_json, sha256_file
from steel_module_production_phase2b_lib import (
    TASK_RESULT_SCHEMA_VERSION,
    execution_task_by_id,
    load_attempt_intent,
    validate_worker_scheduler_identity,
)
from steel_module_production_successor_lib import (
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    load_execution_for_frozen_worker as load_legacy_execution_for_frozen_worker,
    verify_successor_recovery_readiness,
)
from steel_module_production_container_contract import (
    production_apptainer_host_environment,
    validate_apptainer_runtime_identity,
)
from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--control-source-root", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--intent-sha256", required=True)
    parser.add_argument("--logical-task-id", required=True)
    parser.add_argument("--task-root", required=True, type=Path)
    return parser.parse_args()


def _artifact(path: Path, execution_dir: Path) -> dict[str, object]:
    return artifact_record(path, execution_dir, checksum=True)


def _load_execution_for_frozen_worker(
    execution_dir: Path, *, control_root: Path
) -> Any:
    manifest = load_json(execution_dir / "managed_execution.json")
    if manifest.get("schema_version") == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_lib import (
            load_execution_v6_for_frozen_worker,
        )

        return load_execution_v6_for_frozen_worker(
            execution_dir, control_root=control_root
        )
    return load_legacy_execution_for_frozen_worker(
        execution_dir, control_root=control_root
    )


def record(args: argparse.Namespace) -> Path:
    execution_dir = args.execution_dir.expanduser().resolve()
    control_root = args.control_source_root.expanduser().resolve()
    execution = _load_execution_for_frozen_worker(
        execution_dir, control_root=control_root
    )
    intent = load_attempt_intent(execution, args.attempt_id)
    readiness: dict[str, Any] | None = None
    execution_schema = execution.manifest.get("schema_version")
    if execution_schema == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_readiness import (
            verify_phase2c_readiness,
        )

        readiness_path, readiness = verify_phase2c_readiness(
            execution, repo_root=None
        )
        if intent.get("readiness_lock_sha256") != sha256_file(readiness_path):
            raise ValueError("execution-v6 intent/R6-readiness checksum mismatch")
    elif execution_schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        readiness_path, readiness = verify_successor_recovery_readiness(
            execution, repo_root=None
        )
        if intent.get("readiness_lock_sha256") != sha256_file(readiness_path):
            raise ValueError("successor intent/additive-readiness checksum mismatch")
    if intent.get("intent_sha256") != args.intent_sha256:
        raise ValueError("worker intent SHA-256 mismatch")
    if readiness is not None:
        raw_apptainer = shutil.which("apptainer")
        preflight = readiness.get("compute_preflight")
        if raw_apptainer is None or not isinstance(preflight, dict):
            raise ValueError("successor recorder lacks R3 Apptainer identity")
        validate_apptainer_runtime_identity(
            Path(raw_apptainer),
            expected={
                name: preflight.get(name)
                for name in (
                    "apptainer_path",
                    "apptainer_sha256",
                    "apptainer_version",
                )
            },
            environment=production_apptainer_host_environment(),
            cwd=control_root,
        )
    if args.logical_task_id not in intent["selected"]["logical_task_ids"]:
        raise ValueError("logical task is outside the frozen intent")
    array_index = intent["selected"]["logical_task_ids"].index(
        args.logical_task_id
    ) + 1
    validate_worker_scheduler_identity(
        execution,
        attempt_id=args.attempt_id,
        intent=intent,
        array_index=array_index,
        environment=os.environ,
    )
    task = execution_task_by_id(execution).get(args.logical_task_id)
    if task is None:
        raise ValueError("unknown managed logical task")
    task_root = args.task_root.expanduser().resolve()
    expected_root = (
        execution_dir / "attempts" / args.attempt_id / "tasks" / task.logical_task_id
    ).resolve()
    if task_root != expected_root:
        raise ValueError("managed task root identity mismatch")
    run_configs = sorted((task_root / "runs").glob("*/run_config.json"))
    if len(run_configs) != 1:
        raise ValueError("managed task must contain exactly one run_config.json")
    run_config_path = run_configs[0]
    run_dir = run_config_path.parent
    config = load_json(run_config_path)
    validate_run_config(config, task, args.attempt_id, execution)

    points_path = run_dir / "points.csv"
    _, point = read_single_csv_row(points_path)
    root_path = resolve_run_artifact(run_dir, point["root"])
    macro_path = resolve_run_artifact(run_dir, point["macro"])
    log_path = resolve_run_artifact(run_dir, point["log"])
    summary_path = root_path.with_name(f"{root_path.stem}_summary.csv")
    efficiency_path = run_dir / "efficiency_map.csv"
    _, summary = read_single_csv_row(summary_path)
    if integer_field(summary, "events") != task.events:
        raise ValueError("summary event count mismatch")
    if integer_field(summary, "committed_events") != task.events:
        raise ValueError("summary committed-event count mismatch")
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if any(
        marker in log_text
        for marker in ("COMMAND NOT FOUND", "Fatal Exception", "Simulation failed", "Overlap is detected")
    ):
        raise ValueError("simulation log contains a fatal marker")
    if "Checking overlaps for volume SteelAbsorber:0 (G4Box) ... OK!" not in log_text:
        raise ValueError("simulation log lacks the accepted steel overlap check")
    if log_text.count("Checking overlaps for volume SiPM:") != LAYOUT_CONTRACTS[task.sipm_layout]["sensor_count"]:
        raise ValueError("simulation log has an unexpected SiPM overlap-check count")

    artifacts = {
        "run_config": _artifact(run_config_path, execution_dir),
        "points": _artifact(points_path, execution_dir),
        "macro": _artifact(macro_path, execution_dir),
        "simulation_log": _artifact(log_path, execution_dir),
        "root": _artifact(root_path, execution_dir),
        "summary": _artifact(summary_path, execution_dir),
        "efficiency_map": _artifact(efficiency_path, execution_dir),
    }
    value: dict[str, Any] = {
        "schema_version": TASK_RESULT_SCHEMA_VERSION,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
        "campaign_id": execution.campaign_id,
        "plan_hash": execution.plan_hash,
        "program_id": execution.manifest["program"]["program_id"],
        "program_hash": execution.manifest["program"]["program_hash"],
        "child_id": "BC-S1",
        "child_plan_hash": execution.manifest["managed_child"]["child_plan_hash"],
        "binding_hash": execution.manifest["managed_child"]["binding_hash"],
        "phase2a_lock_sha256": execution.manifest["phase2a_lock"]["sha256"],
        "readiness_lock_sha256": intent["readiness_lock_sha256"],
        "intent_sha256": intent["intent_sha256"],
        "attempt_id": args.attempt_id,
        "task_index": task.task_index,
        "logical_task_id": task.logical_task_id,
        "configuration_hash": task.configuration_hash,
        "tile_thickness_mm": task.tile_thickness_mm,
        "sipm_layout": task.sipm_layout,
        "absorber_transverse_mm": task.absorber_transverse_mm,
        "seed_block": task.seed_block,
        "events": task.events,
        "seed1": task.seed1,
        "seed2": task.seed2,
        "simulation_source": execution.manifest["sources"]["simulation"],
        "control_plane_source": execution.manifest["sources"]["control_plane"],
        "runtime": execution.manifest["runtime"],
        "artifacts": artifacts,
    }
    marker = task_root / "task_result.json"
    write_exclusive_json(marker, value)
    return marker


def main() -> int:
    args = parse_args()
    try:
        marker = record(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot record managed steel-module task: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded immutable managed task result: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
