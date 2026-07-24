#!/usr/bin/env python3
"""Validation helpers for immutable steel-module-scan-v1 campaigns."""

from __future__ import annotations

import csv
import json
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


CAMPAIGN_SCHEMA_VERSION = "steel-module-campaign-v1"
RUN_CONFIG_SCHEMA_VERSION = "opnovice2-run-config-v4"
EVENT_SCHEMA_VERSION = "opnovice2-scan-event-v3"
RESULT_SCHEMA_VERSION = "steel-module-task-result-v1"
STUDY_PRESET = "steel-module-scan-v1"
SIPM_LAYOUTS = ("back-center", "edge-center", "back-four")
SUPPORTED_SIPM_LAYOUTS = (*SIPM_LAYOUTS, "edge-two")
TILE_THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
ABSORBER_SIZES_MM = (200, 300, 500)
FINALIZED_REQUIRED_FILES = (
    "task_index.tsv",
    "configuration_summary.csv",
    "event_audit.json",
    "validation_report.json",
    "analysis_config.json",
)


@dataclass(frozen=True)
class CampaignTask:
    task_index: int
    logical_task_id: str
    stage: str
    tile_thickness_mm: int
    sipm_layout: str
    absorber_transverse_mm: int
    x_mm: int
    y_mm: int
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
    sipm_layout: str,
    absorber_transverse_mm: int,
    x_mm: int,
    y_mm: int,
    events: int,
    seed_block: int,
    seed1: int,
    seed2: int,
    geant4_version: str,
) -> str:
    """Return the runner-bound identity for one steel-module task."""

    resolved = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "study_preset": STUDY_PRESET,
        "stage": stage,
        "tile_full_size_mm": [100, 100, tile_thickness_mm],
        "tile_thickness_mm": tile_thickness_mm,
        "sipm_layout": sipm_layout,
        "sipm_active_size_mm": [2.4, 2.4, 0.5],
        "absorber_material": "StainlessSteelSAE304",
        "absorber_transverse_mm": absorber_transverse_mm,
        "absorber_thickness_mm": 40,
        "absorber_tile_gap_mm": 0,
        "x_mm": x_mm,
        "y_mm": y_mm,
        "events": events,
        "seed_block": seed_block,
        "seed1": seed1,
        "seed2": seed2,
        "authoritative_source_quantity": "kinetic_energy",
        "gps_kinetic_energy_mev": 1000,
        "beam_profile": "point",
        "beam_direction": [0, 0, -1],
        "beam_angular_model": "pencil",
        "surface_preset": "polishedfrontpainted",
        "surface_reflectivity_model": "ej510-empirical",
        "optical_coupling_geometry_model": "undimpled-zero-gap-ej550-proxy",
        "geant4_version": geant4_version,
    }
    return sha256_bytes(canonical_json(resolved))


def parse_campaign_tasks(path: Path) -> tuple[CampaignTask, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        expected_fields = list(CampaignTask.__dataclass_fields__)
        if list(reader.fieldnames or []) != expected_fields:
            raise ValueError(
                f"unexpected steel-module task header in {path}: "
                f"{reader.fieldnames!r}"
            )

    tasks: list[CampaignTask] = []
    for row in rows:
        try:
            task = CampaignTask(
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
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid steel-module campaign task row: {row}") from exc
        tasks.append(task)

    if not tasks:
        raise ValueError("tasks.tsv contains no tasks")
    if [task.task_index for task in tasks] != list(range(1, len(tasks) + 1)):
        raise ValueError("task_index must be contiguous and one-based")
    logical_ids = [task.logical_task_id for task in tasks]
    if any(not value for value in logical_ids) or len(set(logical_ids)) != len(logical_ids):
        raise ValueError("logical_task_id values must be non-empty and unique")
    if any(task.tile_thickness_mm not in TILE_THICKNESSES_MM for task in tasks):
        raise ValueError("task contains an unsupported tile thickness")
    if any(task.sipm_layout not in SUPPORTED_SIPM_LAYOUTS for task in tasks):
        raise ValueError("task contains an unsupported SiPM layout")
    if any(task.absorber_transverse_mm not in ABSORBER_SIZES_MM for task in tasks):
        raise ValueError("task contains an unsupported absorber transverse size")
    if any(task.x_mm != 0 or task.y_mm != 0 for task in tasks):
        raise ValueError("steel-module campaign tasks must use the centered source")
    if any(task.events <= 0 or task.seed_block < 0 for task in tasks):
        raise ValueError("task events must be positive and seed blocks non-negative")
    hashes = [task.configuration_hash for task in tasks]
    if any(
        len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for value in hashes
    ):
        raise ValueError("configuration_hash values must be lowercase SHA-256 digests")
    seeds = [seed for task in tasks for seed in (task.seed1, task.seed2)]
    if any(seed <= 0 or seed >= 2_147_483_647 for seed in seeds):
        raise ValueError("campaign task seeds are outside the Geant4 range")
    if len(set(seeds)) != len(seeds):
        raise ValueError("campaign task seeds must be globally unique")
    return tuple(tasks)


def verify_finalized_checksums(finalized_dir: Path) -> set[str]:
    return verify_checksum_manifest(
        finalized_dir,
        required=FINALIZED_REQUIRED_FILES,
    )


def load_campaign(
    campaign_dir: Path, *, verify_external_artifacts: bool = True
) -> CampaignBundle:
    directory = campaign_dir.expanduser().resolve()
    manifest_path = directory / "campaign.json"
    tasks_path = directory / "tasks.tsv"
    scan_args_path = directory / "scan_args.txt"
    readme_path = directory / "README.md"
    for path in (manifest_path, tasks_path, scan_args_path, readme_path):
        if not path.is_file():
            raise ValueError(f"missing campaign file: {path}")

    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported steel-module campaign schema: "
            f"{manifest.get('schema_version')!r}"
        )
    if manifest.get("study_preset") != STUDY_PRESET:
        raise ValueError(f"campaign study_preset must be {STUDY_PRESET}")
    campaign_id = require_string(manifest, "campaign_id")
    if any(
        char
        not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        for char in campaign_id
    ):
        raise ValueError("campaign_id contains unsafe characters")
    require_sha256(manifest, "plan_hash")
    git = require_dict(manifest, "git")
    require_sha1(git, "commit")
    if not isinstance(git.get("dirty"), bool):
        raise ValueError("git.dirty must be boolean")

    tasks = parse_campaign_tasks(tasks_path)
    scan_args = read_scan_args(scan_args_path)
    if len(tasks) != len(scan_args) or manifest.get("task_count") != len(tasks):
        raise ValueError("campaign task counts disagree")
    calculated_plan_hash = sha256_bytes(
        canonical_json([asdict(task) for task in tasks])
    )
    if calculated_plan_hash != manifest["plan_hash"]:
        raise ValueError(
            "tasks.tsv does not match campaign plan_hash: "
            f"expected {manifest['plan_hash']}, got {calculated_plan_hash}"
        )

    artifacts = require_dict(manifest, "artifacts")
    for key, path in (
        ("tasks_tsv", tasks_path),
        ("scan_args", scan_args_path),
        ("readme", readme_path),
    ):
        record = require_dict(artifacts, key)
        expected = require_sha256(record, "sha256")
        if sha256_file(path) != expected:
            raise ValueError(f"campaign artifact checksum mismatch: {path}")

    selection_record = artifacts.get("configuration_selection")
    if manifest.get("stage") == "convergence-pilot":
        if not isinstance(selection_record, dict):
            raise ValueError(
                "convergence-pilot campaign lacks configuration_selection artifact"
            )
        selection_name = require_string(selection_record, "path")
        selection_path = resolve_campaign_path(directory, selection_name)
        expected_selection_sha = require_sha256(selection_record, "sha256")
        if not selection_path.is_file() or sha256_file(selection_path) != expected_selection_sha:
            raise ValueError("configuration_selection artifact checksum mismatch")
        with selection_path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            selection_rows = list(reader)
        expected_header = [
            "tile_thickness_mm",
            "sipm_layout",
            "absorber_transverse_mm",
        ]
        if list(reader.fieldnames or []) != expected_header:
            raise ValueError("configuration_selection artifact has an invalid header")
        try:
            selected_configurations = {
                (
                    int(row["tile_thickness_mm"]),
                    row["sipm_layout"],
                    int(row["absorber_transverse_mm"]),
                )
                for row in selection_rows
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("configuration_selection artifact has an invalid row") from exc
        task_configurations = {
            (
                task.tile_thickness_mm,
                task.sipm_layout,
                task.absorber_transverse_mm,
            )
            for task in tasks
        }
        if (
            not selected_configurations
            or len(selected_configurations) != len(selection_rows)
            or selected_configurations != task_configurations
        ):
            raise ValueError(
                "configuration_selection does not match convergence task rows"
            )
    elif selection_record is not None:
        raise ValueError(
            "configuration_selection is valid only for convergence-pilot"
        )

    environment = require_dict(manifest, "environment")
    expected_identity = require_sha256(environment, "identity_hash")
    actual_identity = environment_identity(environment)
    if actual_identity != expected_identity:
        raise ValueError(
            "campaign environment identity mismatch: "
            f"expected {expected_identity}, got {actual_identity}"
        )
    if verify_external_artifacts:
        for field, label in (
            ("image", "Apptainer image"),
            ("g4_data_manifest", "Geant4 data manifest"),
            ("build_artifact", "prebuilt executable"),
        ):
            record = environment.get(field)
            if record is not None:
                resolve_recorded_artifact(record, label)

    return CampaignBundle(directory, manifest, tasks, scan_args)


def task_by_logical_id(bundle: CampaignBundle) -> dict[str, CampaignTask]:
    return {task.logical_task_id: task for task in bundle.tasks}


__all__ = [
    "ABSORBER_SIZES_MM",
    "ATTEMPT_FIELDS",
    "ATTEMPT_MANIFEST_NAME",
    "CAMPAIGN_SCHEMA_VERSION",
    "EVENT_SCHEMA_VERSION",
    "RESULT_SCHEMA_VERSION",
    "RUN_CONFIG_SCHEMA_VERSION",
    "SIPM_LAYOUTS",
    "SUPPORTED_SIPM_LAYOUTS",
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
    "read_scan_args",
    "resolve_campaign_path",
    "resolve_recorded_artifact",
    "sha256_bytes",
    "sha256_file",
    "task_by_logical_id",
    "task_configuration_hash",
    "verify_finalized_checksums",
    "write_tsv",
]
