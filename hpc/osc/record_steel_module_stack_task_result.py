#!/usr/bin/env python3
"""Validate one completed stack task and write its immutable result marker."""

from __future__ import annotations

import argparse
import json
import os
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
from steel_module_stack_campaign_lib import (
    EVENT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    RUN_CONFIG_SCHEMA_VERSION,
    SENSOR_COUNT,
    STACK_LAYERS,
    STUDY_PRESET,
    CampaignBundle,
    CampaignTask,
    load_campaign,
    load_json,
    task_by_logical_id,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--logical-task-id", required=True)
    parser.add_argument("--task-root", required=True, type=Path)
    return parser.parse_args()


def mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"run_config.{key} must be an object")
    return value


def validate_run_config(
    config: dict[str, Any], task: CampaignTask, attempt_id: str, bundle: CampaignBundle
) -> None:
    if config.get("schema_version") != RUN_CONFIG_SCHEMA_VERSION:
        raise ValueError("stack run_config schema mismatch")
    if config.get("event_schema_version") != EVENT_SCHEMA_VERSION:
        raise ValueError("stack event schema mismatch")
    if config.get("study_preset") != STUDY_PRESET or config.get("dry_run") is not False:
        raise ValueError("run_config is not a completed stack task")
    expected_campaign = {
        "campaign_id": bundle.campaign_id,
        "stage": task.stage,
        "logical_task_id": task.logical_task_id,
        "configuration_hash": task.configuration_hash,
        "seed_block": task.seed_block,
    }
    campaign = mapping(config, "campaign")
    if any(campaign.get(key) != value for key, value in expected_campaign.items()):
        raise ValueError("run_config campaign identity mismatch")
    execution = mapping(config, "execution")
    environment = bundle.environment
    expected_execution = {
        "formal_campaign_task": True,
        "attempt_id": attempt_id,
        "logical_task_index": task.task_index,
        "plan_hash": bundle.plan_hash,
        "git_commit": bundle.git_commit,
        "environment_mode": environment.get("mode"),
        "environment_identity": environment.get("identity_hash"),
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
    }
    if any(execution.get(key) != value for key, value in expected_execution.items()):
        raise ValueError("run_config execution identity mismatch")
    git = mapping(config, "git")
    if git.get("commit") != bundle.git_commit or git.get("dirty") is not False:
        raise ValueError("run_config Git identity mismatch")
    command_environment = mapping(mapping(config, "command"), "environment")
    if command_environment.get("G4RUN_MANAGER_TYPE") != "Serial":
        raise ValueError("stack task did not use the Serial run manager")
    if command_environment.get("SCAN_RUN_ID_SUFFIX") != f"{attempt_id}_{task.logical_task_id}":
        raise ValueError("stack run-directory suffix mismatch")
    length = STACK_LAYERS * (40 + task.tile_thickness_mm)
    simulation = mapping(config, "simulation")
    expected_simulation = {
        "events_per_point": task.events,
        "primary_particle": "neutron",
        "primary_energy": "1000 MeV",
        "authoritative_source_quantity": "kinetic_energy",
        "authoritative_kinetic_energy_mev": 1000,
        "gps_kinetic_energy_mev": 1000,
        "beam_direction": "0 0 -1",
        "beam_z": f"{length / 2 + 1.5:g} mm",
        "beam_z_inferred": True,
    }
    if any(simulation.get(key) != value for key, value in expected_simulation.items()):
        raise ValueError("stack simulation contract mismatch")
    random = mapping(config, "random")
    if random.get("seed1") != task.seed1 or random.get("seed2") != task.seed2:
        raise ValueError("stack random seed mismatch")
    if random.get("explicit_seed_pair") is not True:
        raise ValueError("stack task lacks explicit seeds")
    grid = mapping(config, "grid")
    if grid.get("point_count") != 1 or grid.get("x") != [0] or grid.get("y") != [0]:
        raise ValueError("stack source is not centered")
    tank = mapping(config, "tank")
    if tank.get("size") != f"100 100 {task.tile_thickness_mm} mm":
        raise ValueError("stack tile geometry mismatch")
    absorber = mapping(config, "absorber")
    if (
        absorber.get("enabled") is not True
        or absorber.get("full_size_mm") != [500, 500, 40]
        or absorber.get("material") != "StainlessSteelSAE304"
        or absorber.get("tile_gap_mm") != 0
    ):
        raise ValueError("stack steel contract mismatch")
    stack = mapping(config, "stack")
    if (
        stack.get("enabled") is not True
        or stack.get("schema_version") != STUDY_PRESET
        or stack.get("layers") != STACK_LAYERS
        or stack.get("module_order") != ["steel", "tile"]
        or stack.get("stack_length_mm") != length
        or stack.get("source_z_mm") != length / 2 + 1.5
        or stack.get("unknown_origin_allowed_for_accepted_evidence") is not False
    ):
        raise ValueError("stack geometry/causal contract mismatch")
    sipm = mapping(config, "sipm")
    if (
        sipm.get("study_layout") != "edge-two"
        or sipm.get("detector_layout") != "edge-two"
        or sipm.get("face") != "+X"
        or sipm.get("sensor_count") != SENSOR_COUNT
        or sipm.get("copy_number_order") != list(range(SENSOR_COUNT))
        or sipm.get("fixed_local_positions_mm") != [[-25, 0, 0], [25, 0, 0]]
        or sipm.get("size") != "2.4 2.4 0.5 mm"
    ):
        raise ValueError("stack SiPM contract mismatch")
    surface = mapping(config, "surface")
    if surface.get("preset") != "polishedfrontpainted" or surface.get("reflectivity_model") != "ej510-empirical":
        raise ValueError("stack surface contract mismatch")
    coupling = mapping(config, "optical_coupling")
    if coupling.get("geometry_model") != "undimpled-zero-gap-ej550-proxy" or coupling.get("grease_enabled") is not False:
        raise ValueError("stack coupling contract mismatch")


def main() -> int:
    args = parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    task_root = args.task_root.expanduser().resolve()
    try:
        bundle = load_campaign(campaign_dir, verify_external_artifacts=False)
        task = task_by_logical_id(bundle).get(args.logical_task_id)
        if task is None:
            raise ValueError(f"unknown stack task: {args.logical_task_id}")
        if not args.attempt_id or any(not (char.isalnum() or char in "._-") for char in args.attempt_id):
            raise ValueError("unsafe attempt ID")
        expected_root = (campaign_dir / "attempts" / args.attempt_id / "tasks" / task.logical_task_id).resolve()
        if task_root != expected_root:
            raise ValueError("task root does not match attempt/task identity")
        configs = sorted((task_root / "runs").glob("*/run_config.json"))
        if len(configs) != 1:
            raise ValueError("expected exactly one stack run_config")
        config_path = configs[0]
        run_dir = config_path.parent
        config = load_json(config_path)
        validate_run_config(config, task, args.attempt_id, bundle)
        points_path = run_dir / "points.csv"
        _, point = read_single_csv_row(points_path)
        root_path = resolve_run_artifact(run_dir, point["root"])
        macro_path = resolve_run_artifact(run_dir, point["macro"])
        log_path = resolve_run_artifact(run_dir, point["log"])
        summary_path = root_path.with_name(f"{root_path.stem}_summary.csv")
        efficiency_path = run_dir / "efficiency_map.csv"
        _, summary = read_single_csv_row(summary_path)
        if integer_field(summary, "events") != task.events or integer_field(summary, "committed_events") != task.events:
            raise ValueError("stack summary event count mismatch")
        log = log_path.read_text(encoding="utf-8", errors="replace")
        if any(marker in log for marker in ("COMMAND NOT FOUND", "Fatal Exception", "Simulation failed", "Overlap is detected")):
            raise ValueError("stack log contains fatal/overlap marker")
        if log.count("Checking overlaps for volume Tank:") != STACK_LAYERS:
            raise ValueError("stack log lacks ten tile overlap checks")
        if log.count("Checking overlaps for volume SteelAbsorber:") != STACK_LAYERS:
            raise ValueError("stack log lacks ten steel overlap checks")
        if log.count("Checking overlaps for volume SiPM:") != SENSOR_COUNT:
            raise ValueError("stack log lacks twenty SiPM overlap checks")
        artifacts = {
            "run_config": artifact_record(config_path, campaign_dir, checksum=True),
            "points": artifact_record(points_path, campaign_dir, checksum=True),
            "macro": artifact_record(macro_path, campaign_dir, checksum=True),
            "simulation_log": artifact_record(log_path, campaign_dir, checksum=True),
            "root": artifact_record(root_path, campaign_dir, checksum=True),
            "summary": artifact_record(summary_path, campaign_dir, checksum=True),
            "efficiency_map": artifact_record(efficiency_path, campaign_dir, checksum=True),
        }
        marker = task_root / "task_result.json"
        write_exclusive_json(
            marker,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "attempt_id": args.attempt_id,
                "task_index": task.task_index,
                "logical_task_id": task.logical_task_id,
                "configuration_hash": task.configuration_hash,
                "tile_thickness_mm": task.tile_thickness_mm,
                "seed_block": task.seed_block,
                "seed1": task.seed1,
                "seed2": task.seed2,
                "events": task.events,
                "stack_layers": STACK_LAYERS,
                "sensor_count": SENSOR_COUNT,
                "slurm": {"array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"), "array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"), "job_id": os.environ.get("SLURM_JOB_ID")},
                "artifacts": artifacts,
            },
        )
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot record steel-module-stack task result: {exc}", file=os.sys.stderr)
        return 1
    print(f"Steel-module-stack task result: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
