#!/usr/bin/env python3
"""Audit one completed steel-module task and write its immutable result marker."""

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
from steel_module_campaign_lib import (
    EVENT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    RUN_CONFIG_SCHEMA_VERSION,
    STUDY_PRESET,
    CampaignBundle,
    CampaignTask,
    load_campaign,
    load_json,
    task_by_logical_id,
)


LAYOUT_CONTRACTS = {
    "back-center": {
        "detector_layout": "single",
        "face": "-Z",
        "sensor_count": 1,
        "positions": [[0, 0, 0]],
    },
    "edge-center": {
        "detector_layout": "single",
        "face": "+X",
        "sensor_count": 1,
        "positions": [[0, 0, 0]],
    },
    "edge-two": {
        "detector_layout": "edge-two",
        "face": "+X",
        "sensor_count": 2,
        "positions": [[-25, 0, 0], [25, 0, 0]],
    },
    "back-four": {
        "detector_layout": "back-four",
        "face": "-Z",
        "sensor_count": 4,
        "positions": [
            [-25, -25, 0],
            [-25, 25, 0],
            [25, -25, 0],
            [25, 25, 0],
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--logical-task-id", required=True)
    parser.add_argument("--task-root", required=True, type=Path)
    return parser.parse_args()


def require_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"run_config.{key} must be an object")
    return value


def validate_run_config(
    config: dict[str, Any],
    task: CampaignTask,
    attempt_id: str,
    bundle: CampaignBundle,
) -> None:
    if config.get("schema_version") != RUN_CONFIG_SCHEMA_VERSION:
        raise ValueError("run_config schema mismatch")
    if config.get("event_schema_version") != EVENT_SCHEMA_VERSION:
        raise ValueError("run_config event schema mismatch")
    if config.get("study_preset") != STUDY_PRESET or config.get("dry_run") is not False:
        raise ValueError("run_config is not a completed steel-module task")

    campaign = require_mapping(config, "campaign")
    expected_campaign = {
        "campaign_id": bundle.campaign_id,
        "stage": task.stage,
        "logical_task_id": task.logical_task_id,
        "configuration_hash": task.configuration_hash,
        "seed_block": task.seed_block,
    }
    for key, expected in expected_campaign.items():
        if campaign.get(key) != expected:
            raise ValueError(
                f"run_config campaign.{key} mismatch: "
                f"{campaign.get(key)!r} != {expected!r}"
            )

    execution = require_mapping(config, "execution")
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
    for key, expected in expected_execution.items():
        if execution.get(key) != expected:
            raise ValueError(
                f"run_config execution.{key} mismatch: "
                f"{execution.get(key)!r} != {expected!r}"
            )

    git = require_mapping(config, "git")
    if git.get("commit") != bundle.git_commit or git.get("dirty") is not False:
        raise ValueError("run_config Git identity is not the frozen campaign commit")

    command = require_mapping(config, "command")
    command_environment = command.get("environment")
    if not isinstance(command_environment, dict):
        raise ValueError("run_config command.environment must be an object")
    if command_environment.get("G4RUN_MANAGER_TYPE") != "Serial":
        raise ValueError("formal campaign task did not use the Serial run manager")
    expected_suffix = f"{attempt_id}_{task.logical_task_id}"
    if command_environment.get("SCAN_RUN_ID_SUFFIX") != expected_suffix:
        raise ValueError("formal campaign task run-directory suffix mismatch")

    simulation = require_mapping(config, "simulation")
    expected_source_z = 0.5 * task.tile_thickness_mm + 40 + 1.5
    expected_simulation = {
        "events_per_point": task.events,
        "primary_particle": "neutron",
        "primary_energy": "1000 MeV",
        "authoritative_source_quantity": "kinetic_energy",
        "authoritative_kinetic_energy_mev": 1000,
        "authoritative_momentum_gev_c": None,
        "gps_kinetic_energy_mev": 1000,
        "beam_direction": "0 0 -1",
        "beam_z": f"{expected_source_z:g} mm",
        "beam_z_inferred": True,
    }
    for key, expected in expected_simulation.items():
        if simulation.get(key) != expected:
            raise ValueError(f"run_config simulation.{key} mismatch")

    beam = require_mapping(config, "beam")
    if (
        beam.get("profile") != "point"
        or beam.get("angular_model") != "pencil"
        or beam.get("direction") != "0 0 -1"
        or beam.get("divergence_mrad") is not None
    ):
        raise ValueError("run_config beam contract mismatch")

    random = require_mapping(config, "random")
    if (
        random.get("explicit_seed_pair") is not True
        or random.get("seed1") != task.seed1
        or random.get("seed2") != task.seed2
    ):
        raise ValueError("run_config random seeds mismatch")

    grid = require_mapping(config, "grid")
    if (
        grid.get("point_count") != 1
        or grid.get("x") != [task.x_mm]
        or grid.get("y") != [task.y_mm]
    ):
        raise ValueError("run_config centered scan coordinates mismatch")

    tank = require_mapping(config, "tank")
    if tank.get("size") != f"100 100 {task.tile_thickness_mm} mm":
        raise ValueError("run_config tile geometry mismatch")

    absorber = require_mapping(config, "absorber")
    expected_size = [
        task.absorber_transverse_mm,
        task.absorber_transverse_mm,
        40,
    ]
    if (
        absorber.get("enabled") is not True
        or absorber.get("full_size_mm") != expected_size
        or absorber.get("material") != "StainlessSteelSAE304"
        or absorber.get("tile_gap_mm") != 0
    ):
        raise ValueError("run_config absorber contract mismatch")

    surface = require_mapping(config, "surface")
    if (
        surface.get("preset") != "polishedfrontpainted"
        or surface.get("reflectivity_model") != "ej510-empirical"
        or surface.get("paint_volume_model") != "optical_surface_proxy"
    ):
        raise ValueError("run_config painted-surface contract mismatch")

    coupling = require_mapping(config, "optical_coupling")
    if (
        coupling.get("model") != "none"
        or coupling.get("geometry_model") != "undimpled-zero-gap-ej550-proxy"
        or coupling.get("grease_enabled") is not False
    ):
        raise ValueError("run_config optical-coupling contract mismatch")

    expected_layout = LAYOUT_CONTRACTS[task.sipm_layout]
    sipm = require_mapping(config, "sipm")
    expected_sipm = {
        "study_layout": task.sipm_layout,
        "detector_layout": expected_layout["detector_layout"],
        "face": expected_layout["face"],
        "sensor_count": expected_layout["sensor_count"],
        "fixed_local_positions_mm": expected_layout["positions"],
        "size": "2.4 2.4 0.5 mm",
    }
    for key, expected in expected_sipm.items():
        if sipm.get(key) != expected:
            raise ValueError(f"run_config sipm.{key} mismatch")


def main() -> int:
    args = parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    task_root = args.task_root.expanduser().resolve()

    try:
        bundle = load_campaign(campaign_dir, verify_external_artifacts=False)
        task = task_by_logical_id(bundle).get(args.logical_task_id)
        if task is None:
            raise ValueError(f"unknown logical task ID: {args.logical_task_id}")
        if not args.attempt_id or any(
            char
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
            for char in args.attempt_id
        ):
            raise ValueError("attempt ID contains unsafe characters")
        expected_task_root = (
            campaign_dir
            / "attempts"
            / args.attempt_id
            / "tasks"
            / task.logical_task_id
        ).resolve()
        if task_root != expected_task_root:
            raise ValueError("task root does not match attempt/task identity")

        run_configs = sorted((task_root / "runs").glob("*/run_config.json"))
        if len(run_configs) != 1:
            raise ValueError(
                f"expected exactly one run_config under {task_root / 'runs'}, "
                f"found {len(run_configs)}"
            )
        run_config_path = run_configs[0]
        run_dir = run_config_path.parent
        config = load_json(run_config_path)
        validate_run_config(config, task, args.attempt_id, bundle)

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
        fatal_markers = (
            "COMMAND NOT FOUND",
            "Fatal Exception",
            "Simulation failed",
            "Overlap is detected",
        )
        if any(marker in log_text for marker in fatal_markers):
            raise ValueError("simulation log contains a fatal or overlap marker")
        if "Checking overlaps for volume SteelAbsorber:0 (G4Box) ... OK!" not in log_text:
            raise ValueError("simulation log lacks the accepted steel overlap check")
        expected_sipm_checks = LAYOUT_CONTRACTS[task.sipm_layout]["sensor_count"]
        if log_text.count("Checking overlaps for volume SiPM:") != expected_sipm_checks:
            raise ValueError("simulation log has an unexpected number of SiPM checks")

        artifacts = {
            "run_config": artifact_record(run_config_path, campaign_dir, checksum=True),
            "points": artifact_record(points_path, campaign_dir, checksum=True),
            "macro": artifact_record(macro_path, campaign_dir, checksum=True),
            "simulation_log": artifact_record(log_path, campaign_dir, checksum=True),
            "root": artifact_record(root_path, campaign_dir, checksum=True),
            "summary": artifact_record(summary_path, campaign_dir, checksum=True),
            "efficiency_map": artifact_record(
                efficiency_path, campaign_dir, checksum=True
            ),
        }
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "attempt_id": args.attempt_id,
            "task_index": task.task_index,
            "logical_task_id": task.logical_task_id,
            "configuration_hash": task.configuration_hash,
            "tile_thickness_mm": task.tile_thickness_mm,
            "sipm_layout": task.sipm_layout,
            "absorber_transverse_mm": task.absorber_transverse_mm,
            "seed1": task.seed1,
            "seed2": task.seed2,
            "events": task.events,
            "slurm": {
                "array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
                "array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
                "job_id": os.environ.get("SLURM_JOB_ID"),
            },
            "artifacts": artifacts,
        }
        marker = task_root / "task_result.json"
        write_exclusive_json(marker, result)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot record steel-module task result: {exc}", file=os.sys.stderr)
        return 1

    print(f"Steel-module task result: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
