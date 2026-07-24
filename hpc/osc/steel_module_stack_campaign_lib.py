#!/usr/bin/env python3
"""Identity and validation helpers for steel-module-stack-v1 campaigns."""

from __future__ import annotations

import csv
import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from realistic_neutron_campaign_lib import (
    ATTEMPT_FIELDS,
    ATTEMPT_MANIFEST_NAME,
    atomic_write_json,
    campaign_relative,
    canonical_json,
    environment_identity,
    read_attempt_rows,
    read_scan_args,
    require_dict,
    require_sha1,
    require_sha256,
    require_string,
    resolve_campaign_path,
    resolve_recorded_artifact,
    sha256_bytes,
    sha256_file,
    verify_checksum_manifest,
    write_tsv,
)


CAMPAIGN_SCHEMA_VERSION = "steel-module-stack-campaign-v1"
RUN_CONFIG_SCHEMA_VERSION = "opnovice2-run-config-v5"
EVENT_SCHEMA_VERSION = "opnovice2-stack-event-v1"
RESULT_SCHEMA_VERSION = "steel-module-stack-task-result-v1"
STUDY_PRESET = "steel-module-stack-v1"
TILE_THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
STACK_LAYERS = 10
SENSORS_PER_LAYER = 2
SENSOR_COUNT = STACK_LAYERS * SENSORS_PER_LAYER
FINALIZED_REQUIRED_FILES = (
    "task_index.tsv",
    "configuration_summary.csv",
    "event_audit.json",
    "seed_audit.json",
    "validation_report.json",
)


def artifact_sha(environment: dict[str, Any], key: str) -> str | None:
    record = environment.get(key)
    return record.get("sha256") if isinstance(record, dict) else None


@dataclass(frozen=True)
class CampaignTask:
    task_index: int
    logical_task_id: str
    stage: str
    tile_thickness_mm: int
    seed_block: int
    events: int
    seed1: int
    seed2: int
    configuration_hash: str


@dataclass(frozen=True)
class CampaignBundle:
    directory: Path
    manifest: dict[str, Any]
    tasks: tuple[CampaignTask, ...]
    scan_args: tuple[str, ...]

    @property
    def campaign_id(self) -> str:
        return require_string(self.manifest, "campaign_id")

    @property
    def plan_hash(self) -> str:
        return require_sha256(self.manifest, "plan_hash")

    @property
    def git_commit(self) -> str:
        return require_sha1(require_dict(self.manifest, "git"), "commit")

    @property
    def environment(self) -> dict[str, Any]:
        return require_dict(self.manifest, "environment")


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def task_configuration_hash(
    *,
    stage: str,
    tile_thickness_mm: int,
    seed_block: int,
    events: int,
    seed1: int,
    seed2: int,
    geant4_version: str,
) -> str:
    length = STACK_LAYERS * (40 + tile_thickness_mm)
    resolved = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "study_preset": STUDY_PRESET,
        "stage": stage,
        "tile_full_size_mm": [100, 100, tile_thickness_mm],
        "tile_thickness_mm": tile_thickness_mm,
        "stack_layers": STACK_LAYERS,
        "stack_length_mm": length,
        "module_order": ["steel", "tile"],
        "steel_full_size_mm": [500, 500, 40],
        "steel_material": "StainlessSteelSAE304",
        "steel_tile_gap_mm": 0,
        "sipm_layout": "edge-two",
        "sipm_face": "+X",
        "sipm_active_size_mm": [2.4, 2.4, 0.5],
        "sipm_local_centers_mm": [[-25, 0], [25, 0]],
        "sensor_copy_rule": "2*layer+local_sensor",
        "source": {
            "particle": "neutron",
            "kinetic_energy_mev": 1000,
            "position_mm": [0, 0, length / 2 + 1.5],
            "direction": [0, 0, -1],
            "profile": "point",
        },
        "surface_preset": "polishedfrontpainted",
        "surface_reflectivity_model": "ej510-empirical",
        "coupling": "undimpled-zero-gap-ej550-proxy",
        "optical_origin_rule": "first_tile_layer_with_WLS_inheritance",
        "primary_collection": "same_origin_to_same_layer_ratio_of_sums",
        "event_schema": EVENT_SCHEMA_VERSION,
        "events": events,
        "seed_block": seed_block,
        "seed1": seed1,
        "seed2": seed2,
        "geant4_version": geant4_version,
    }
    return sha256_bytes(canonical_json(resolved))


def expected_scan_args(task: CampaignTask, campaign_id: str) -> list[str]:
    return [
        "full", "custom", "--study-preset", STUDY_PRESET,
        "--tile-thickness-mm", str(task.tile_thickness_mm),
        "--x-min", "0", "--x-max", "0", "--y-min", "0", "--y-max", "0",
        "--step", "1", "--grid-unit", "mm", "--events", str(task.events),
        "--seed1", str(task.seed1), "--seed2", str(task.seed2),
        "--campaign-id", campaign_id, "--campaign-stage", task.stage,
        "--logical-task-id", task.logical_task_id,
        "--configuration-hash", task.configuration_hash,
        "--seed-block", str(task.seed_block), "--no-root-plots",
    ]


def parse_campaign_tasks(path: Path) -> tuple[CampaignTask, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    fields = list(CampaignTask.__dataclass_fields__)
    if list(reader.fieldnames or []) != fields:
        raise ValueError(f"unexpected stack task header in {path}")
    tasks: list[CampaignTask] = []
    for row in rows:
        try:
            tasks.append(
                CampaignTask(
                    task_index=int(row["task_index"]),
                    logical_task_id=row["logical_task_id"],
                    stage=row["stage"],
                    tile_thickness_mm=int(row["tile_thickness_mm"]),
                    seed_block=int(row["seed_block"]),
                    events=int(row["events"]),
                    seed1=int(row["seed1"]),
                    seed2=int(row["seed2"]),
                    configuration_hash=row["configuration_hash"],
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid stack campaign task row: {row}") from exc
    if not tasks:
        raise ValueError("tasks.tsv contains no tasks")
    if [task.task_index for task in tasks] != list(range(1, len(tasks) + 1)):
        raise ValueError("task_index must be contiguous and one-based")
    ids = [task.logical_task_id for task in tasks]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        raise ValueError("logical task IDs must be non-empty and unique")
    if any(task.tile_thickness_mm not in TILE_THICKNESSES_MM for task in tasks):
        raise ValueError("unsupported stack tile thickness")
    if any(task.events <= 0 or task.seed_block < 0 for task in tasks):
        raise ValueError("invalid stack event/block count")
    hashes = [task.configuration_hash for task in tasks]
    if any(len(value) != 64 or set(value) - set("0123456789abcdef") for value in hashes):
        raise ValueError("configuration hashes must be lowercase SHA-256")
    seeds = [seed for task in tasks for seed in (task.seed1, task.seed2)]
    if len(seeds) != len(set(seeds)):
        raise ValueError("stack seeds must be globally unique")
    if any(seed <= 0 or seed >= 2_147_483_647 for seed in seeds):
        raise ValueError("stack seed outside the Geant4 range")
    return tuple(tasks)


def load_campaign(
    campaign_dir: Path, *, verify_external_artifacts: bool = True
) -> CampaignBundle:
    directory = campaign_dir.expanduser().resolve()
    paths = {
        "manifest": directory / "campaign.json",
        "tasks_tsv": directory / "tasks.tsv",
        "scan_args": directory / "scan_args.txt",
        "readme": directory / "README.md",
    }
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"missing campaign file: {path}")
    manifest = load_json(paths["manifest"])
    if manifest.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
        raise ValueError("unsupported stack campaign schema")
    if manifest.get("study_preset") != STUDY_PRESET:
        raise ValueError("stack campaign study preset mismatch")
    campaign_id = require_string(manifest, "campaign_id")
    if any(not (char.isalnum() or char in "._-") for char in campaign_id):
        raise ValueError("unsafe stack campaign ID")
    require_sha256(manifest, "plan_hash")
    git = require_dict(manifest, "git")
    require_sha1(git, "commit")
    if not isinstance(git.get("dirty"), bool):
        raise ValueError("git.dirty must be boolean")
    tasks = parse_campaign_tasks(paths["tasks_tsv"])
    scan_args = read_scan_args(paths["scan_args"])
    if len(tasks) != len(scan_args) or manifest.get("task_count") != len(tasks):
        raise ValueError("stack campaign task counts disagree")
    if sha256_bytes(canonical_json([asdict(task) for task in tasks])) != manifest["plan_hash"]:
        raise ValueError("tasks.tsv does not match stack plan hash")
    stage = manifest.get("stage")
    if stage not in {"geometry-smoke", "benchmark", "pilot", "production"}:
        raise ValueError("invalid stack campaign stage")
    if any(task.stage != stage for task in tasks):
        raise ValueError("stack task stage mismatch")
    if manifest.get("events_per_task") != tasks[0].events or any(task.events != tasks[0].events for task in tasks):
        raise ValueError("stack task event counts disagree")
    if manifest.get("total_events") != sum(task.events for task in tasks):
        raise ValueError("stack total event count mismatch")
    observed_shape = {(task.tile_thickness_mm, task.seed_block) for task in tasks}
    if stage == "geometry-smoke" and (tasks[0].events != 1 or observed_shape != {(value, 0) for value in TILE_THICKNESSES_MM}):
        raise ValueError("invalid stack geometry-smoke shape")
    if stage == "benchmark" and (tasks[0].events != 25 or observed_shape != {(4, 0), (24, 0)}):
        raise ValueError("invalid stack benchmark shape")
    if stage == "pilot" and observed_shape != {(value, block) for value in TILE_THICKNESSES_MM for block in range(4)}:
        raise ValueError("invalid stack pilot shape")
    if stage == "production" and any(task.seed_block < 4 for task in tasks):
        raise ValueError("stack production overlaps pilot seed-block identities")
    geometry = require_dict(manifest, "geometry_contract")
    if geometry != {
        "layers": 10, "module_order": ["steel", "tile"],
        "steel_full_size_mm": [500, 500, 40], "tile_transverse_mm": [100, 100],
        "tile_gap_mm": 0, "sipm_layout": "edge-two", "sensor_count": 20,
    }:
        raise ValueError("stack geometry contract mismatch")
    source = require_dict(manifest, "source_contract")
    if source != {
        "particle": "neutron", "kinetic_energy_mev": 1000, "profile": "point",
        "direction": [0, 0, -1], "source_z_formula_mm": "5*(40+t)+1.5",
    }:
        raise ValueError("stack source contract mismatch")
    causal = require_dict(manifest, "causal_contract")
    if causal != {
        "origin": "first_tile_layer_with_WLS_inheritance",
        "primary_collection": "same_origin_to_same_layer_ratio_of_sums",
        "unknown_origin_accepted": False,
    }:
        raise ValueError("stack causal contract mismatch")
    environment = require_dict(manifest, "environment")
    geant4_version = require_string(environment, "geant4_version")
    for task, line in zip(tasks, scan_args):
        expected_hash = task_configuration_hash(
            stage=task.stage, tile_thickness_mm=task.tile_thickness_mm,
            seed_block=task.seed_block, events=task.events, seed1=task.seed1,
            seed2=task.seed2, geant4_version=geant4_version,
        )
        if task.configuration_hash != expected_hash:
            raise ValueError(f"stack task configuration hash mismatch: {task.logical_task_id}")
        if shlex.split(line) != expected_scan_args(task, campaign_id):
            raise ValueError(f"stack scan argument mismatch: {task.logical_task_id}")
    artifacts = require_dict(manifest, "artifacts")
    for key in ("tasks_tsv", "scan_args", "readme"):
        record = require_dict(artifacts, key)
        if sha256_file(paths[key]) != require_sha256(record, "sha256"):
            raise ValueError(f"stack campaign artifact checksum mismatch: {key}")
    for key in ("sizing_plan", "benchmark_report", "excluded_seed_registry"):
        record = artifacts.get(key)
        if record is None:
            continue
        if not isinstance(record, dict):
            raise ValueError(f"invalid stack artifact record: {key}")
        path = resolve_campaign_path(directory, require_string(record, "path"))
        if not path.is_file() or sha256_file(path) != require_sha256(record, "sha256"):
            raise ValueError(f"stack campaign artifact checksum mismatch: {key}")
    accepted = environment.get("accepted_statistical_evidence") is True
    registry_record = artifacts.get("excluded_seed_registry")
    if accepted and stage in {"pilot", "production"} and not isinstance(registry_record, dict):
        raise ValueError("formal stack pilot/production lacks an excluded-seed registry")
    if isinstance(registry_record, dict):
        registry_path = resolve_campaign_path(directory, require_string(registry_record, "path"))
        registry = load_json(registry_path)
        excluded = registry.get("excluded_seeds")
        if not isinstance(excluded, list) or any(not isinstance(seed, int) for seed in excluded):
            raise ValueError("stack excluded-seed registry is invalid")
        current = {seed for task in tasks for seed in (task.seed1, task.seed2)}
        if current & set(excluded):
            raise ValueError("stack task seed overlaps the excluded registry")
    sizing_record = artifacts.get("sizing_plan")
    scheduler = require_dict(manifest, "scheduler_contract")
    if (
        scheduler.get("nodes") != 1
        or scheduler.get("ntasks") != 1
        or scheduler.get("cpus_per_task") != 1
        or scheduler.get("time_seconds") != 3600
        or not isinstance(scheduler.get("memory_gib"), int)
        or scheduler["memory_gib"] < 2
    ):
        raise ValueError("invalid stack scheduler contract")
    if stage in {"geometry-smoke", "benchmark"} and scheduler["memory_gib"] != 2:
        raise ValueError("stack smoke/benchmark memory must be 2 GiB")
    benchmark_record = artifacts.get("benchmark_report")
    if stage == "pilot":
        if not isinstance(benchmark_record, dict):
            raise ValueError("stack pilot lacks its benchmark report")
        benchmark_path = resolve_campaign_path(directory, require_string(benchmark_record, "path"))
        benchmark = load_json(benchmark_path)
        if (
            benchmark.get("schema_version") != "steel-module-stack-benchmark-v1"
            or benchmark.get("accepted_statistical_evidence") is not True
            or benchmark.get("pilot_permitted_for_human_review") is not True
            or benchmark.get("selected_events_per_task") != tasks[0].events
            or benchmark.get("production_memory_gib") != scheduler["memory_gib"]
            or benchmark.get("git_commit") != manifest["git"]["commit"]
            or benchmark.get("environment_identity") != environment["identity_hash"]
            or benchmark.get("image_sha256") != artifact_sha(environment, "image")
            or benchmark.get("g4_data_manifest_sha256") != artifact_sha(environment, "g4_data_manifest")
            or benchmark.get("executable_sha256") != artifact_sha(environment, "build_artifact")
        ):
            raise ValueError("stack pilot benchmark binding is invalid")
    if stage == "production":
        if not isinstance(sizing_record, dict):
            raise ValueError("stack production lacks its sizing plan")
        sizing_path = resolve_campaign_path(directory, require_string(sizing_record, "path"))
        sizing = load_json(sizing_path)
        if (
            sizing.get("schema_version") != "steel-module-stack-sizing-v1"
            or sizing.get("automatic_acceptance") is not False
            or sizing.get("production_plan_permitted_for_human_review") is not True
            or sizing.get("events_per_task") != tasks[0].events
            or sizing.get("scheduler_contract") != scheduler
        ):
            raise ValueError("stack production sizing identity is invalid")
        expected_shape: set[tuple[int, int]] = set()
        for row in sizing.get("thicknesses", []):
            if not isinstance(row, dict):
                raise ValueError("invalid stack sizing thickness row")
            thickness = row.get("tile_thickness_mm")
            first = row.get("first_additional_seed_block")
            count = row.get("additional_blocks")
            if thickness not in TILE_THICKNESSES_MM or not isinstance(first, int) or not isinstance(count, int):
                raise ValueError("invalid stack sizing task shape")
            expected_shape.update((thickness, block) for block in range(first, first + count))
        if observed_shape != expected_shape:
            raise ValueError("stack production tasks disagree with sizing plan")
    if environment_identity(environment) != require_sha256(environment, "identity_hash"):
        raise ValueError("stack campaign environment identity mismatch")
    if verify_external_artifacts:
        for field, label in (
            ("image", "Apptainer image"),
            ("g4_data_manifest", "Geant4 data manifest"),
            ("build_artifact", "prebuilt executable"),
        ):
            if environment.get(field) is not None:
                resolve_recorded_artifact(environment[field], label)
    return CampaignBundle(directory, manifest, tasks, scan_args)


def task_by_logical_id(bundle: CampaignBundle) -> dict[str, CampaignTask]:
    return {task.logical_task_id: task for task in bundle.tasks}


def verify_finalized_checksums(finalized_dir: Path) -> set[str]:
    return verify_checksum_manifest(finalized_dir, required=FINALIZED_REQUIRED_FILES)


__all__ = [
    "ATTEMPT_FIELDS",
    "ATTEMPT_MANIFEST_NAME",
    "CAMPAIGN_SCHEMA_VERSION",
    "EVENT_SCHEMA_VERSION",
    "FINALIZED_REQUIRED_FILES",
    "RESULT_SCHEMA_VERSION",
    "RUN_CONFIG_SCHEMA_VERSION",
    "SENSOR_COUNT",
    "SENSORS_PER_LAYER",
    "STACK_LAYERS",
    "STUDY_PRESET",
    "TILE_THICKNESSES_MM",
    "CampaignBundle",
    "CampaignTask",
    "atomic_write_json",
    "campaign_relative",
    "canonical_json",
    "environment_identity",
    "load_campaign",
    "load_json",
    "read_attempt_rows",
    "resolve_campaign_path",
    "sha256_bytes",
    "sha256_file",
    "task_by_logical_id",
    "task_configuration_hash",
    "verify_finalized_checksums",
    "write_tsv",
]
