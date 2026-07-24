#!/usr/bin/env python3
"""Audit one completed realistic-neutron task and write its immutable result marker."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from realistic_neutron_campaign_lib import (
    EVENT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    RUN_CONFIG_SCHEMA_VERSION,
    STUDY_PRESET,
    CampaignTask,
    campaign_relative,
    load_campaign,
    load_json,
    sha256_file,
    task_by_logical_id,
)


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


def read_single_csv_row(path: Path) -> tuple[list[str], dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not fields or len(rows) != 1:
        raise ValueError(f"expected exactly one data row: {path}")
    return fields, rows[0]


def integer_field(row: dict[str, str], key: str) -> int:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer summary field {key}") from exc
    if not math.isfinite(value) or not value.is_integer():
        raise ValueError(f"invalid integer summary field {key}: {value}")
    return int(value)


def validate_run_config(
    config: dict[str, Any], task: CampaignTask, attempt_id: str, bundle: Any
) -> None:
    if config.get("schema_version") != RUN_CONFIG_SCHEMA_VERSION:
        raise ValueError("run_config schema mismatch")
    if config.get("event_schema_version") != EVENT_SCHEMA_VERSION:
        raise ValueError("run_config event schema mismatch")
    if config.get("study_preset") != STUDY_PRESET or config.get("dry_run") is not False:
        raise ValueError("run_config is not a completed realistic-neutron task")

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
                f"run_config campaign.{key} mismatch: {campaign.get(key)!r} != {expected!r}"
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
                f"run_config execution.{key} mismatch: {execution.get(key)!r} != {expected!r}"
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
    if simulation.get("events_per_point") != task.events:
        raise ValueError("run_config event count mismatch")
    if simulation.get("primary_particle") != "neutron":
        raise ValueError("run_config primary particle is not neutron")

    random = require_mapping(config, "random")
    if random.get("seed1") != task.seed1 or random.get("seed2") != task.seed2:
        raise ValueError("run_config random seeds mismatch")

    grid = require_mapping(config, "grid")
    if grid.get("point_count") != 1 or grid.get("x") != [task.x_mm] or grid.get("y") != [task.y_mm]:
        raise ValueError("run_config scan coordinates mismatch")

    tank = require_mapping(config, "tank")
    if tank.get("size") != f"50 50 {task.tile_thickness_mm} mm":
        raise ValueError("run_config tile geometry mismatch")

    absorber = require_mapping(config, "absorber")
    expected_size = [
        task.absorber_transverse_mm,
        task.absorber_transverse_mm,
        40,
    ]
    if absorber.get("enabled") is not True or absorber.get("full_size_mm") != expected_size:
        raise ValueError("run_config absorber geometry mismatch")


def artifact_record(path: Path, campaign_dir: Path, *, checksum: bool) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty result artifact: {path}")
    record: dict[str, object] = {
        "path": campaign_relative(path, campaign_dir),
        "size_bytes": path.stat().st_size,
    }
    if checksum:
        record["sha256"] = sha256_file(path)
    return record


def resolve_run_artifact(run_dir: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    resolved = path.resolve()
    try:
        resolved.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"points.csv artifact escapes run directory: {resolved}") from exc
    return resolved


def write_exclusive_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError as exc:
            raise ValueError(f"refusing to overwrite task result marker: {path}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()


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
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
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
            raise ValueError(
                f"task root does not match attempt/task identity: {task_root}"
            )

        run_configs = sorted((task_root / "runs").glob("*/run_config.json"))
        if len(run_configs) != 1:
            raise ValueError(
                f"expected exactly one run_config under {task_root / 'runs'}, found {len(run_configs)}"
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
        fatal_markers = ("COMMAND NOT FOUND", "Fatal Exception", "Simulation failed")
        if any(marker in log_text for marker in fatal_markers):
            raise ValueError("simulation log contains a fatal marker")

        artifacts = {
            "run_config": artifact_record(run_config_path, campaign_dir, checksum=True),
            "points": artifact_record(points_path, campaign_dir, checksum=True),
            "macro": artifact_record(macro_path, campaign_dir, checksum=True),
            "simulation_log": artifact_record(log_path, campaign_dir, checksum=True),
            "root": artifact_record(root_path, campaign_dir, checksum=True),
            "summary": artifact_record(summary_path, campaign_dir, checksum=True),
            "efficiency_map": artifact_record(efficiency_path, campaign_dir, checksum=True),
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
        print(f"Cannot record realistic-neutron task result: {exc}", file=os.sys.stderr)
        return 1

    print(f"Realistic-neutron task result: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
