#!/usr/bin/env python3
"""Combine the five finalized direct campaigns into the final production curves."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import analyze_steel_module_campaign as v1
import analyze_steel_module_campaign_v2 as v2
from generate_steel_module_direct_bc_s1_campaign import (
    validate_direct_bc_s1_bundle,
)
from generate_steel_module_direct_bc_s2_campaign import (
    validate_direct_bc_s2_bundle,
)
from generate_steel_module_direct_bc_s3_campaign import (
    validate_direct_bc_s3_bundle,
)
from generate_steel_module_direct_bc_s4_campaign import (
    validate_direct_bc_s4_bundle,
)
from generate_steel_module_direct_fixed_campaign import validate_direct_fixed_bundle
from steel_module_campaign_lib import (
    SIPM_LAYOUTS,
    CampaignBundle,
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)


SCHEMA_VERSION = "steel-module-direct-final-analysis-v1"
BOOTSTRAP_SEED = 20260715
BOOTSTRAP_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95
PRECISION_TARGET = 0.10
THICKNESSES = (4, 8, 12, 16, 20, 24)
TOTAL_TASKS = 914
TOTAL_EVENTS = 228_500
SENSOR_AREA_MM2 = 2.4 * 2.4


@dataclass(frozen=True)
class InputSpec:
    label: str
    campaign_id: str
    job_id: str
    tasks: int
    events: int
    validator: Callable[[CampaignBundle], None]


INPUT_SPECS = (
    InputSpec(
        "FIXED",
        "sm-v1-production-fixed-direct-6d97109e0401",
        "50619224",
        592,
        148_000,
        validate_direct_fixed_bundle,
    ),
    InputSpec(
        "BC-S1",
        "sm-v1-production-bc-s1-direct-c89b659643dd",
        "50615141",
        32,
        8_000,
        validate_direct_bc_s1_bundle,
    ),
    InputSpec(
        "BC-S2",
        "sm-v1-production-bc-s2-direct-636265fe741b",
        "50617964",
        48,
        12_000,
        validate_direct_bc_s2_bundle,
    ),
    InputSpec(
        "BC-S3",
        "sm-v1-production-bc-s3-direct-addb00f16a9c",
        "50618229",
        80,
        20_000,
        validate_direct_bc_s3_bundle,
    ),
    InputSpec(
        "BC-S4",
        "sm-v1-production-bc-s4-direct-46bf4fa3229b",
        "50618568",
        162,
        40_500,
        validate_direct_bc_s4_bundle,
    ),
)

CONFIGURATION_FIELDS = v2.CONFIGURATION_FIELDS
DISTRIBUTION_FIELDS = v2.DISTRIBUTION_FIELDS
PATHWAY_FIELDS = v2.PATHWAY_FIELDS
STANDARDIZED_FIELDS = (*v2.STANDARDIZED_FIELDS, "production_weighting")
POOLED_FIELDS = (
    *v2.POOLED_FIELDS[:11],
    "production_weighting",
    "layout_weight_back_center",
    "layout_weight_edge_center",
    "layout_weight_back_four",
    *v2.POOLED_FIELDS[11:],
)
PER_SENSOR_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events",
    "sensor_index",
    "installed",
    "active_area_mm2",
    "sipm_photons_per_neutron",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
)
RATIO_METRIC_FIELDS = tuple(
    field
    for metric in ("production", "collection", "net")
    for field in (
        f"{metric}_ratio",
        f"{metric}_ci95_low",
        f"{metric}_ci95_high",
        f"{metric}_valid_resamples",
        f"{metric}_relative_half_width",
        f"{metric}_ci_excludes_unity",
    )
)
THICKNESS_RATIO_FIELDS = (
    "stage",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "reference_tile_thickness_mm",
    "compared_tile_thickness_mm",
    "reference_events",
    "compared_events",
    *RATIO_METRIC_FIELDS,
)
LAYOUT_RATIO_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "reference_sipm_layout",
    "compared_sipm_layout",
    "reference_events",
    "compared_events",
    "reference_sensor_count",
    "compared_sensor_count",
    "active_area_ratio",
    *RATIO_METRIC_FIELDS,
    "net_per_installed_sensor_ratio",
    "net_per_installed_sensor_ci95_low",
    "net_per_installed_sensor_ci95_high",
)
PRIMARY_FIELDS = (
    "primary_contrast_id",
    "family",
    "metric",
    "sipm_layout",
    "reference_tile_thickness_mm",
    "compared_tile_thickness_mm",
    "reference_events_back_center",
    "reference_events_edge_center",
    "reference_events_back_four",
    "compared_events_back_center",
    "compared_events_edge_center",
    "compared_events_back_four",
    "sample_basis",
    "ratio",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
    "relative_half_width",
    "ci_excludes_unity",
    "precision_target",
    "precision_target_met",
    "automatic_acceptance",
)


@dataclass(frozen=True)
class LoadedInput:
    spec: InputSpec
    directory: Path
    bundle: CampaignBundle
    validation: dict[str, object]
    analysis_config: dict[str, object]
    loaded: v2.LoadedEvents
    task_rows: tuple[dict[str, str], ...]
    input_hashes: dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--fixed-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s1-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s2-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s3-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s4-campaign-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser.parse_args()


def run_git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def capture_input_hashes(campaign_dir: Path) -> dict[str, str]:
    finalized = campaign_dir / "finalized"
    relative_paths = (
        "campaign.json",
        "tasks.tsv",
        "scan_args.txt",
        "finalized/SHA256SUMS",
        "finalized/task_index.tsv",
        "finalized/configuration_summary.csv",
        "finalized/event_audit.json",
        "finalized/validation_report.json",
        "finalized/analysis_config.json",
    )
    return {
        relative: sha256_file(campaign_dir / relative) for relative in relative_paths
    }


def load_input(spec: InputSpec, directory: Path) -> LoadedInput:
    resolved = directory.expanduser().resolve()
    finalized = resolved / "finalized"
    bundle, validation, analysis_config, audited_roots = v1.validate_identity(
        resolved, finalized
    )
    spec.validator(bundle)
    if bundle.campaign_id != spec.campaign_id:
        raise ValueError(f"{spec.label} campaign identity mismatch")
    if len(bundle.tasks) != spec.tasks or sum(task.events for task in bundle.tasks) != spec.events:
        raise ValueError(f"{spec.label} task/event shape mismatch")
    task_rows = tuple(v1.read_tsv(finalized / "task_index.tsv"))
    if len(task_rows) != spec.tasks:
        raise ValueError(f"{spec.label} finalized task count mismatch")
    if {row["slurm_job_id"] for row in task_rows} != {spec.job_id}:
        raise ValueError(f"{spec.label} finalized Slurm job identity mismatch")
    loaded = v2.load_events(resolved, task_rows, None, audited_roots)
    v1.validate_against_summaries(
        finalized,
        loaded.groups,
        {key: len(blocks) for key, blocks in loaded.blocks.items()},
    )
    bootstrap = analysis_config.get("bootstrap")
    if not isinstance(bootstrap, dict) or (
        int(bootstrap.get("seed", -1)) != BOOTSTRAP_SEED
        or int(bootstrap.get("resamples", -1)) != BOOTSTRAP_RESAMPLES
    ):
        raise ValueError(f"{spec.label} finalized bootstrap definition mismatch")
    if validation.get("accepted_statistical_evidence") is not True:
        raise ValueError(f"{spec.label} is not accepted statistical evidence")
    return LoadedInput(
        spec=spec,
        directory=resolved,
        bundle=bundle,
        validation=validation,
        analysis_config=analysis_config,
        loaded=loaded,
        task_rows=task_rows,
        input_hashes=capture_input_hashes(resolved),
    )


def merge_inputs(inputs: Sequence[LoadedInput]) -> v2.LoadedEvents:
    groups: dict[v1.ConfigurationKey, list[v1.Event]] = {}
    blocks: dict[v1.ConfigurationKey, dict[int, list[v1.Event]]] = {}
    task_ids: dict[tuple[v1.ConfigurationKey, int], str] = {}
    logical_ids: set[str] = set()
    seeds: set[int] = set()
    environment_identities = {item.bundle.environment.get("identity_hash") for item in inputs}
    if len(environment_identities) != 1:
        raise ValueError("direct production inputs use different environments")
    for item in inputs:
        for task in item.bundle.tasks:
            if task.logical_task_id in logical_ids:
                raise ValueError(f"duplicate production logical task: {task.logical_task_id}")
            logical_ids.add(task.logical_task_id)
            for seed in (task.seed1, task.seed2):
                if seed in seeds:
                    raise ValueError(f"duplicate production seed: {seed}")
                seeds.add(seed)
        for key, events in item.loaded.groups.items():
            groups.setdefault(key, []).extend(events)
        for key, by_block in item.loaded.blocks.items():
            target = blocks.setdefault(key, {})
            overlap = set(target) & set(by_block)
            if overlap:
                raise ValueError(f"overlapping production blocks for {key}: {sorted(overlap)}")
            target.update(by_block)
        for identity, logical_id in item.loaded.task_ids.items():
            if identity in task_ids:
                raise ValueError(f"duplicate task block identity: {identity}")
            task_ids[identity] = logical_id
    loaded = v2.LoadedEvents(groups, blocks, task_ids)
    validate_final_shape(loaded, total_tasks=len(logical_ids), seed_count=len(seeds))
    return loaded


def expected_blocks(layout: str, thickness: int) -> set[int]:
    if layout == "back-center":
        return set(range(161 if thickness in (4, 24) else 16))
    if layout == "edge-center":
        return set(range(40))
    if layout == "back-four":
        return set(range(48))
    raise ValueError(f"unsupported layout: {layout}")


def expected_events(layout: str, thickness: int) -> int:
    return 250 * len(expected_blocks(layout, thickness))


def validate_final_shape(
    loaded: v2.LoadedEvents, *, total_tasks: int, seed_count: int
) -> None:
    expected_keys = {
        ("production", thickness, layout, 500, 0, 0)
        for layout in SIPM_LAYOUTS
        for thickness in THICKNESSES
    }
    if set(loaded.groups) != expected_keys:
        raise ValueError("final production sample is not the exact 18-configuration matrix")
    if total_tasks != TOTAL_TASKS or seed_count != 2 * TOTAL_TASKS:
        raise ValueError("final production task/seed total mismatch")
    if len(loaded.task_ids) != TOTAL_TASKS:
        raise ValueError("final production task identity total mismatch")
    if sum(len(events) for events in loaded.groups.values()) != TOTAL_EVENTS:
        raise ValueError("final production event total mismatch")
    for key in expected_keys:
        _, thickness, layout, absorber, x_mm, y_mm = key
        if (absorber, x_mm, y_mm) != (500, 0, 0):
            raise ValueError("final production geometry axes are invalid")
        if set(loaded.blocks[key]) != expected_blocks(layout, thickness):
            raise ValueError(f"final production block registry mismatch: {layout}/{thickness}")
        if len(loaded.groups[key]) != expected_events(layout, thickness):
            raise ValueError(f"final production event count mismatch: {layout}/{thickness}")
        if any(len(events) != 250 for events in loaded.blocks[key].values()):
            raise ValueError(f"final production block size mismatch: {layout}/{thickness}")


def bootstrap_configuration(
    events: Sequence[v1.Event], *, resamples: int, seed: int, np: object
) -> dict[str, list[float]]:
    values = np.asarray(
        [
            [event.generated, event.scintillation, event.sipm, *event.sensors]
            for event in events
        ],
        dtype=float,
    )
    count = len(events)
    rng = np.random.Generator(np.random.PCG64(seed))
    metrics = (
        "generated",
        "scintillation",
        "collection",
        "net",
        "sensor_0",
        "sensor_1",
        "sensor_2",
        "sensor_3",
    )
    output = {metric: [] for metric in metrics}
    batch_size = max(1, min(resamples, 1_000_000 // count))
    completed = 0
    while completed < resamples:
        batch = min(batch_size, resamples - completed)
        indices = rng.integers(0, count, size=(batch, count))
        sums = values[indices].sum(axis=1)
        output["generated"].extend(float(value) for value in sums[:, 0] / count)
        output["scintillation"].extend(float(value) for value in sums[:, 1] / count)
        collections = np.divide(
            sums[:, 2],
            sums[:, 0],
            out=np.full(batch, np.nan),
            where=sums[:, 0] > 0,
        )
        output["collection"].extend(float(value) for value in collections)
        output["net"].extend(float(value) for value in sums[:, 2] / count)
        for index in range(4):
            output[f"sensor_{index}"].extend(
                float(value) for value in sums[:, 3 + index] / count
            )
        completed += batch
    return output


def build_bootstrap_cache(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]], *, np: object
) -> dict[v1.ConfigurationKey, dict[str, list[float]]]:
    return {
        key: bootstrap_configuration(
            events,
            resamples=BOOTSTRAP_RESAMPLES,
            seed=v1.configuration_seed(BOOTSTRAP_SEED, (SCHEMA_VERSION, *key)),
            np=np,
        )
        for key, events in groups.items()
    }


def ratio_summary(
    numerator_point: float,
    denominator_point: float,
    numerator_draws: Sequence[float],
    denominator_draws: Sequence[float],
) -> dict[str, object]:
    ratio = numerator_point / denominator_point if denominator_point > 0 else math.nan
    draws = v1.ratio_distribution(numerator_draws, denominator_draws)
    low, high, valid = v1.interval(draws)
    half_width = (
        (high - low) / (2 * abs(ratio))
        if ratio and math.isfinite(ratio) and math.isfinite(low) and math.isfinite(high)
        else math.nan
    )
    return {
        "ratio": ratio,
        "ci95_low": low,
        "ci95_high": high,
        "valid_resamples": valid,
        "relative_half_width": half_width,
        "ci_excludes_unity": math.isfinite(low)
        and math.isfinite(high)
        and (high < 1 or low > 1),
        "draws": draws,
    }


def equal_weight_pooled_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> tuple[list[dict[str, object]], dict[v2.PooledKey, dict[str, object]]]:
    rows: list[dict[str, object]] = []
    pooled_cache: dict[v2.PooledKey, dict[str, object]] = {}
    for thickness in THICKNESSES:
        by_layout = {
            layout: ("production", thickness, layout, 500, 0, 0)
            for layout in SIPM_LAYOUTS
        }
        for key in by_layout.values():
            if key not in groups:
                raise ValueError(f"pooled production input is missing: {key}")
        points = {
            layout: v2.configuration_estimator(groups[key])
            for layout, key in by_layout.items()
        }
        generated = sum(points[layout]["generated"] for layout in SIPM_LAYOUTS) / 3
        scintillation = (
            sum(points[layout]["scintillation"] for layout in SIPM_LAYOUTS) / 3
        )
        generated_draws = [
            sum(cache[by_layout[layout]]["generated"][index] for layout in SIPM_LAYOUTS)
            / 3
            for index in range(BOOTSTRAP_RESAMPLES)
        ]
        scintillation_draws = [
            sum(
                cache[by_layout[layout]]["scintillation"][index]
                for layout in SIPM_LAYOUTS
            )
            / 3
            for index in range(BOOTSTRAP_RESAMPLES)
        ]
        gen_low, gen_high, gen_valid = v1.interval(generated_draws)
        sci_low, sci_high, sci_valid = v1.interval(scintillation_draws)
        generated_values = [points[layout]["generated"] for layout in SIPM_LAYOUTS]
        scint_values = [points[layout]["scintillation"] for layout in SIPM_LAYOUTS]
        pooled_key: v2.PooledKey = ("production", thickness, 500, 0, 0)
        row = {
            "stage": "production",
            "tile_thickness_mm": thickness,
            "absorber_transverse_mm": 500,
            "x_mm": 0,
            "y_mm": 0,
            "layouts": ",".join(SIPM_LAYOUTS),
            "layout_count": 3,
            "events": sum(len(groups[key]) for key in by_layout.values()),
            "events_back_center": len(groups[by_layout["back-center"]]),
            "events_edge_center": len(groups[by_layout["edge-center"]]),
            "events_back_four": len(groups[by_layout["back-four"]]),
            "production_weighting": "equal-layout-strata",
            "layout_weight_back_center": 1 / 3,
            "layout_weight_edge_center": 1 / 3,
            "layout_weight_back_four": 1 / 3,
            "pooled_generated_photons_per_neutron": generated,
            "generated_ci95_low": gen_low,
            "generated_ci95_high": gen_high,
            "generated_valid_resamples": gen_valid,
            "pooled_scintillation_photons_per_neutron": scintillation,
            "scintillation_ci95_low": sci_low,
            "scintillation_ci95_high": sci_high,
            "scintillation_valid_resamples": sci_valid,
            "back_center_generated_per_neutron": points["back-center"]["generated"],
            "edge_center_generated_per_neutron": points["edge-center"]["generated"],
            "back_four_generated_per_neutron": points["back-four"]["generated"],
            "generated_layout_min": min(generated_values),
            "generated_layout_max": max(generated_values),
            "generated_layout_cv": v2.cv(generated_values),
            "back_center_scintillation_per_neutron": points["back-center"]["scintillation"],
            "edge_center_scintillation_per_neutron": points["edge-center"]["scintillation"],
            "back_four_scintillation_per_neutron": points["back-four"]["scintillation"],
            "scintillation_layout_min": min(scint_values),
            "scintillation_layout_max": max(scint_values),
            "scintillation_layout_cv": v2.cv(scint_values),
        }
        rows.append(row)
        pooled_cache[pooled_key] = {
            "by_layout": by_layout,
            "generated_point": generated,
            "scintillation_point": scintillation,
            "generated": generated_draws,
            "scintillation": scintillation_draws,
            "events": row["events"],
        }
    return rows, pooled_cache


def per_sensor_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (item[2], item[1])):
        stage, thickness, layout, absorber, x_mm, y_mm = key
        events = groups[key]
        for index in range(4):
            values = [event.sensors[index] for event in events]
            low, high, valid = v1.interval(cache[key][f"sensor_{index}"])
            installed = layout == "back-four" or index == 0
            rows.append(
                {
                    "stage": stage,
                    "tile_thickness_mm": thickness,
                    "sipm_layout": layout,
                    "absorber_transverse_mm": absorber,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "events": len(events),
                    "sensor_index": index,
                    "installed": installed,
                    "active_area_mm2": SENSOR_AREA_MM2 if installed else 0,
                    "sipm_photons_per_neutron": sum(values) / len(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "valid_resamples": valid,
                }
            )
    return rows


def add_metric_ratios(
    row: dict[str, object],
    *,
    numerator_key: v1.ConfigurationKey,
    denominator_key: v1.ConfigurationKey,
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> None:
    numerator = v2.configuration_estimator(groups[numerator_key])
    denominator = v2.configuration_estimator(groups[denominator_key])
    for output_metric, cache_metric in (
        ("production", "scintillation"),
        ("collection", "collection"),
        ("net", "net"),
    ):
        result = ratio_summary(
            numerator[cache_metric],
            denominator[cache_metric],
            cache[numerator_key][cache_metric],
            cache[denominator_key][cache_metric],
        )
        for name, value in result.items():
            if name != "draws":
                row[f"{output_metric}_{name}"] = value


def thickness_ratio_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for layout in SIPM_LAYOUTS:
        reference = ("production", 4, layout, 500, 0, 0)
        for thickness in THICKNESSES[1:]:
            compared = ("production", thickness, layout, 500, 0, 0)
            row: dict[str, object] = {
                "stage": "production",
                "sipm_layout": layout,
                "absorber_transverse_mm": 500,
                "x_mm": 0,
                "y_mm": 0,
                "reference_tile_thickness_mm": 4,
                "compared_tile_thickness_mm": thickness,
                "reference_events": len(groups[reference]),
                "compared_events": len(groups[compared]),
            }
            add_metric_ratios(
                row,
                numerator_key=compared,
                denominator_key=reference,
                groups=groups,
                cache=cache,
            )
            rows.append(row)
    return rows


def layout_ratio_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for thickness in THICKNESSES:
        reference = ("production", thickness, "back-center", 500, 0, 0)
        for layout in ("edge-center", "back-four"):
            compared = ("production", thickness, layout, 500, 0, 0)
            sensors = 4 if layout == "back-four" else 1
            row: dict[str, object] = {
                "stage": "production",
                "tile_thickness_mm": thickness,
                "absorber_transverse_mm": 500,
                "x_mm": 0,
                "y_mm": 0,
                "reference_sipm_layout": "back-center",
                "compared_sipm_layout": layout,
                "reference_events": len(groups[reference]),
                "compared_events": len(groups[compared]),
                "reference_sensor_count": 1,
                "compared_sensor_count": sensors,
                "active_area_ratio": sensors,
            }
            add_metric_ratios(
                row,
                numerator_key=compared,
                denominator_key=reference,
                groups=groups,
                cache=cache,
            )
            row["net_per_installed_sensor_ratio"] = float(row["net_ratio"]) / sensors
            row["net_per_installed_sensor_ci95_low"] = (
                float(row["net_ci95_low"]) / sensors
            )
            row["net_per_installed_sensor_ci95_high"] = (
                float(row["net_ci95_high"]) / sensors
            )
            rows.append(row)
    return rows


def primary_contrast_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[v2.PooledKey, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    reference_pool = pooled_cache[("production", 4, 500, 0, 0)]
    compared_pool = pooled_cache[("production", 24, 500, 0, 0)]
    pooled = ratio_summary(
        float(compared_pool["scintillation_point"]),
        float(reference_pool["scintillation_point"]),
        compared_pool["scintillation"],
        reference_pool["scintillation"],
    )

    def event_counts(thickness: int) -> dict[str, int]:
        return {
            layout: len(groups[("production", thickness, layout, 500, 0, 0)])
            for layout in SIPM_LAYOUTS
        }

    reference_counts = event_counts(4)
    compared_counts = event_counts(24)

    def row_from_result(
        *, identifier: str, family: str, metric: str, layout: str, basis: str,
        result: Mapping[str, object]
    ) -> dict[str, object]:
        half_width = float(result["relative_half_width"])
        return {
            "primary_contrast_id": identifier,
            "family": family,
            "metric": metric,
            "sipm_layout": layout,
            "reference_tile_thickness_mm": 4,
            "compared_tile_thickness_mm": 24,
            "reference_events_back_center": reference_counts["back-center"],
            "reference_events_edge_center": reference_counts["edge-center"],
            "reference_events_back_four": reference_counts["back-four"],
            "compared_events_back_center": compared_counts["back-center"],
            "compared_events_edge_center": compared_counts["edge-center"],
            "compared_events_back_four": compared_counts["back-four"],
            "sample_basis": basis,
            "ratio": result["ratio"],
            "ci95_low": result["ci95_low"],
            "ci95_high": result["ci95_high"],
            "valid_resamples": result["valid_resamples"],
            "relative_half_width": half_width,
            "ci_excludes_unity": result["ci_excludes_unity"],
            "precision_target": PRECISION_TARGET,
            "precision_target_met": math.isfinite(half_width)
            and half_width <= PRECISION_TARGET,
            "automatic_acceptance": False,
        }

    rows.append(
        row_from_result(
            identifier="pooled-scintillation-production-24-over-4",
            family="pooled-production",
            metric="scintillation",
            layout="pooled-equal-layout-strata",
            basis="equal-one-third-layout-strata",
            result=pooled,
        )
    )
    for layout in SIPM_LAYOUTS:
        reference = ("production", 4, layout, 500, 0, 0)
        compared = ("production", 24, layout, 500, 0, 0)
        result = ratio_summary(
            v2.configuration_estimator(groups[compared])["net"],
            v2.configuration_estimator(groups[reference])["net"],
            cache[compared]["net"],
            cache[reference]["net"],
        )
        rows.append(
            row_from_result(
                identifier=f"observed-net-{layout}-24-over-4",
                family="observed-net",
                metric="net",
                layout=layout,
                basis="direct-observed-configuration-events",
                result=result,
            )
        )
    if len(rows) != 4:
        raise ValueError("final analysis requires exactly four primary contrasts")
    return rows


def standardized_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[v2.PooledKey, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows = v2.standardized_rows(groups, cache, pooled_cache)
    return [{**row, "production_weighting": "equal-layout-strata"} for row in rows]


def require_complete_bootstrap(rows: Sequence[Mapping[str, object]]) -> None:
    for row in rows:
        for field, value in row.items():
            if field.endswith("valid_resamples") and int(value) != BOOTSTRAP_RESAMPLES:
                raise ValueError(f"incomplete bootstrap evidence: {field}={value}")


def write_csv(
    path: Path, fields: Iterable[str], rows: Iterable[Mapping[str, object]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


def summary_markdown(
    configurations: Sequence[Mapping[str, object]],
    primaries: Sequence[Mapping[str, object]],
    distributions: Sequence[Mapping[str, object]],
) -> str:
    by_key = {
        (str(row["sipm_layout"]), int(row["tile_thickness_mm"])): row
        for row in configurations
    }
    lines = [
        "# Final Steel Module Production Analysis",
        "",
        "This combines the five checksum-valid direct production campaigns and creates no scheduler action or Geant4 event.",
        "",
        f"- Sample: `{TOTAL_TASKS}` tasks, `{TOTAL_EVENTS:,}` events, `18` configurations.",
        "- Model: centered 1 GeV kinetic-energy neutron pencil beam and fixed 500 x 500 x 40 mm SAE-304 slab proxy.",
        "- Pooled production: equal one-third weight for each SiPM-layout stratum, regardless of unequal endpoint sample sizes.",
        "- Bootstrap: 10,000 event resamples, seed 20260715, percentile 95% intervals.",
        "",
        "## Four precommitted primary 24/4 contrasts",
        "",
        "| Contrast | Ratio | 95% interval | Relative half-width | 10% target |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in primaries:
        lines.append(
            f"| {row['primary_contrast_id']} | {float(row['ratio']):.6g} | "
            f"[{float(row['ci95_low']):.6g}, {float(row['ci95_high']):.6g}] | "
            f"{100 * float(row['relative_half_width']):.2f}% | "
            f"{'met' if row['precision_target_met'] else 'not met'} |"
        )
    lines.extend(
        [
            "",
            "## Observed configuration curves",
            "",
            "| Layout | Thickness (mm) | Events | Scintillation / neutron | Collection | SiPM / neutron | 95% net interval |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for layout in SIPM_LAYOUTS:
        for thickness in THICKNESSES:
            row = by_key[(layout, thickness)]
            lines.append(
                f"| {layout} | {thickness} | {row['events']} | "
                f"{float(row['scintillation_photons_per_neutron']):.6g} | "
                f"{float(row['collection_sipm_over_generated']):.6g} | "
                f"{float(row['observed_net_sipm_photons_per_neutron']):.6g} | "
                f"[{float(row['net_ci95_low']):.6g}, {float(row['net_ci95_high']):.6g}] |"
            )
    sipm_tail = [row for row in distributions if row["metric"] == "sipm"]
    lines.extend(["", "## SiPM response tails", ""])
    for row in sipm_tail:
        lines.append(
            f"- {row['sipm_layout']} / {row['tile_thickness_mm']} mm: zero "
            f"`{100 * float(row['zero_fraction']):.2f}%`, top 1% "
            f"`{100 * float(row['top_1pct_sum_fraction']):.2f}%`, top 5% "
            f"`{100 * float(row['top_5pct_sum_fraction']):.2f}%`, max `{row['max']}`."
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "Production, optical collection, and observed net response are reported separately. `SiPM photons` are photons entering the silicon proxy, not photoelectrons or electronics response. Back-four aggregate response uses four installed sensors; per-sensor and per-active-area diagnostics are secondary layout-cost comparisons.",
            "",
            "All conclusions are conditional on the fixed 500 mm transverse absorber proxy; this analysis does not establish absorber-size convergence or equivalence to the full ePIC calorimeter geometry. Precision flags are review results and never launch additional simulation automatically.",
            "",
        ]
    )
    return "\n".join(lines)


def verify_checkout_stable(
    repo_root: Path,
    *,
    commit: str,
    dirty: str,
    source_hashes: Mapping[str, str],
) -> None:
    if run_git(repo_root, "rev-parse", "HEAD") != commit:
        raise ValueError("analysis checkout changed during final analysis")
    if run_git(repo_root, "status", "--porcelain", "--untracked-files=all") != dirty:
        raise ValueError("analysis worktree changed during final analysis")
    if dirty:
        raise ValueError("accepted final analysis requires a clean checkout")
    for relative, digest in source_hashes.items():
        if sha256_file(repo_root / relative) != digest:
            raise ValueError(f"analysis source changed during execution: {relative}")


def main() -> int:
    args = parse_args()
    repo_root = args.project_root.expanduser().resolve()
    paths = (
        args.fixed_campaign_dir,
        args.bc_s1_campaign_dir,
        args.bc_s2_campaign_dir,
        args.bc_s3_campaign_dir,
        args.bc_s4_campaign_dir,
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else args.fixed_campaign_dir.expanduser().resolve()
        / "finalized"
        / "direct-final-analysis"
    )
    temp_dir: Path | None = None
    try:
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("final production analysis requires NumPy") from exc
        commit = run_git(repo_root, "rev-parse", "HEAD")
        branch = run_git(repo_root, "branch", "--show-current")
        dirty = run_git(repo_root, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise ValueError("accepted final analysis requires a clean checkout")
        source_files = (
            "hpc/osc/analyze_steel_module_direct_final.py",
            "hpc/osc/analyze_steel_module_campaign.py",
            "hpc/osc/analyze_steel_module_campaign_v2.py",
        )
        source_hashes = {
            relative: sha256_file(repo_root / relative) for relative in source_files
        }
        inputs = tuple(
            load_input(spec, path) for spec, path in zip(INPUT_SPECS, paths)
        )
        loaded = merge_inputs(inputs)
        cache = build_bootstrap_cache(loaded.groups, np=np)
        configurations = v2.configuration_rows(loaded, cache)
        distributions = v2.distribution_rows(loaded.groups, np=np)
        pathways = v2.pathway_rows(loaded.groups)
        sensors = per_sensor_rows(loaded.groups, cache)
        pooled, pooled_cache = equal_weight_pooled_rows(loaded.groups, cache)
        standardized = standardized_rows(loaded.groups, cache, pooled_cache)
        thickness = thickness_ratio_rows(loaded.groups, cache)
        layouts = layout_ratio_rows(loaded.groups, cache)
        primaries = primary_contrast_rows(loaded.groups, cache, pooled_cache)
        for rows in (configurations, sensors, pooled, standardized, thickness, layouts, primaries):
            require_complete_bootstrap(rows)
        completion = {
            "schema_version": "steel-module-direct-final-precision-review-v1",
            "precision_target": PRECISION_TARGET,
            "primary_contrast_count": 4,
            "all_primary_targets_met": all(
                bool(row["precision_target_met"]) for row in primaries
            ),
            "contrasts": [
                {
                    "primary_contrast_id": row["primary_contrast_id"],
                    "ratio": row["ratio"],
                    "ci95_low": row["ci95_low"],
                    "ci95_high": row["ci95_high"],
                    "relative_half_width": row["relative_half_width"],
                    "precision_target_met": row["precision_target_met"],
                }
                for row in primaries
            ],
            "automatic_acceptance": False,
            "automatic_additional_simulation": False,
        }
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite final analysis: {output_dir}")
        verify_checkout_stable(
            repo_root, commit=commit, dirty=dirty, source_hashes=source_hashes
        )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        write_csv(temp_dir / "configuration_estimates.csv", CONFIGURATION_FIELDS, configurations)
        write_csv(temp_dir / "distribution_diagnostics.csv", DISTRIBUTION_FIELDS, distributions)
        write_csv(temp_dir / "response_pathway.csv", PATHWAY_FIELDS, pathways)
        write_csv(temp_dir / "per_sensor_intervals.csv", PER_SENSOR_FIELDS, sensors)
        write_csv(temp_dir / "pooled_production.csv", POOLED_FIELDS, pooled)
        write_csv(temp_dir / "standardized_response.csv", STANDARDIZED_FIELDS, standardized)
        write_csv(temp_dir / "thickness_ratios.csv", THICKNESS_RATIO_FIELDS, thickness)
        write_csv(temp_dir / "layout_ratios.csv", LAYOUT_RATIO_FIELDS, layouts)
        write_csv(temp_dir / "primary_contrasts.csv", PRIMARY_FIELDS, primaries)
        atomic_write_json(temp_dir / "precision_review.json", completion)
        summary = summary_markdown(configurations, primaries, distributions)
        (temp_dir / "summary.md").write_text(summary, encoding="utf-8")
        input_records = [
            {
                "label": item.spec.label,
                "campaign_directory": str(item.directory),
                "campaign_id": item.bundle.campaign_id,
                "plan_hash": item.bundle.plan_hash,
                "simulation_commit": item.bundle.git_commit,
                "slurm_job_id": item.spec.job_id,
                "tasks": item.spec.tasks,
                "events": item.spec.events,
                "input_hashes": item.input_hashes,
            }
            for item in inputs
        ]
        config = {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "accepted_statistical_evidence": True,
            "study_preset": "steel-module-scan-v1",
            "analysis_commit": commit,
            "analysis_branch": branch,
            "analysis_sources": source_hashes,
            "python_version": sys.version.split()[0],
            "numpy_version": str(np.__version__),
            "inputs": input_records,
            "input_set_hash": sha256_bytes(canonical_json(input_records)),
            "sample": {
                "tasks": TOTAL_TASKS,
                "events": TOTAL_EVENTS,
                "configurations": 18,
                "events_per_block": 250,
                "absorber_transverse_mm": 500,
            },
            "bootstrap": {
                "method": "independent-event nonparametric bootstrap within configuration",
                "generator": "numpy.random.Generator(numpy.random.PCG64)",
                "seed": BOOTSTRAP_SEED,
                "resamples": BOOTSTRAP_RESAMPLES,
                "confidence_level": CONFIDENCE_LEVEL,
                "interval": "percentile",
            },
            "pooled_production": {
                "weighting": "equal-layout-strata",
                "weights": {layout: 1 / 3 for layout in SIPM_LAYOUTS},
                "reason": "SMS-016 forbids event-count weighting of unequal endpoint strata",
            },
            "precision": {
                "primary_target": PRECISION_TARGET,
                "primary_contrasts": 4,
                "automatic_acceptance": False,
            },
            "outputs": {
                "configuration_estimates": "configuration_estimates.csv",
                "distribution_diagnostics": "distribution_diagnostics.csv",
                "response_pathway": "response_pathway.csv",
                "per_sensor_intervals": "per_sensor_intervals.csv",
                "pooled_production": "pooled_production.csv",
                "standardized_response": "standardized_response.csv",
                "thickness_ratios": "thickness_ratios.csv",
                "layout_ratios": "layout_ratios.csv",
                "primary_contrasts": "primary_contrasts.csv",
                "precision_review": "precision_review.json",
                "summary": "summary.md",
            },
        }
        atomic_write_json(temp_dir / "analysis_config.json", config)
        write_checksums(temp_dir)
        os.replace(temp_dir, output_dir)
        temp_dir = None
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"Cannot analyze final direct steel-module production: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)

    print(f"Analyzed final direct production into {output_dir}")
    print(f"Tasks: {TOTAL_TASKS}; events: {TOTAL_EVENTS}; configurations: 18")
    for row in primaries:
        print(
            f"{row['primary_contrast_id']}: {float(row['ratio']):.6g} "
            f"[{float(row['ci95_low']):.6g}, {float(row['ci95_high']):.6g}], "
            f"half-width={100 * float(row['relative_half_width']):.2f}%, "
            f"target={'PASS' if row['precision_target_met'] else 'REVIEW'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
