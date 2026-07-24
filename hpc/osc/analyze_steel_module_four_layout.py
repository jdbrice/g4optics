#!/usr/bin/env python3
"""Combine accepted direct production with edge-two into four-layout curves."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import analyze_steel_module_campaign as v1
import analyze_steel_module_campaign_v2 as v2
import analyze_steel_module_direct_final as original
from generate_steel_module_direct_edge_two_campaign import (
    validate_direct_edge_two_bundle,
)
from steel_module_campaign_lib import (
    atomic_write_json,
    canonical_json,
    sha256_bytes,
    sha256_file,
)


SCHEMA_VERSION = "steel-module-four-layout-analysis-v1"
BOOTSTRAP_SEED = original.BOOTSTRAP_SEED
BOOTSTRAP_RESAMPLES = original.BOOTSTRAP_RESAMPLES
CONFIDENCE_LEVEL = original.CONFIDENCE_LEVEL
THICKNESSES = original.THICKNESSES
LAYOUTS = ("back-center", "edge-center", "edge-two", "back-four")
SENSOR_COUNTS = {
    "back-center": 1,
    "edge-center": 1,
    "edge-two": 2,
    "back-four": 4,
}
SENSOR_AREA_MM2 = 2.4 * 2.4
TOTAL_TASKS = 1_154
TOTAL_EVENTS = 288_500
TOTAL_CONFIGURATIONS = 24

EDGE_TWO_SPEC = original.InputSpec(
    "EDGE-TWO",
    "sm-v1-production-edge-two-direct-ee66cf4fcace",
    "50629433",
    240,
    60_000,
    validate_direct_edge_two_bundle,
)

CONFIGURATION_FIELDS = v2.CONFIGURATION_FIELDS
DISTRIBUTION_FIELDS = v2.DISTRIBUTION_FIELDS
PATHWAY_FIELDS = v2.PATHWAY_FIELDS
PER_SENSOR_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events",
    "sensor_index",
    "sensor_count",
    "active_area_mm2",
    "sipm_photons_per_neutron",
    "net_ci95_low",
    "net_ci95_high",
    "net_valid_resamples",
    "collection_sipm_over_generated",
    "collection_ci95_low",
    "collection_ci95_high",
    "collection_valid_resamples",
    "fraction_of_layout_aggregate",
)
LAYOUT_COST_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events",
    "sensor_count",
    "active_area_mm2",
    "aggregate_collection",
    "aggregate_collection_ci95_low",
    "aggregate_collection_ci95_high",
    "collection_per_installed_sensor",
    "collection_per_installed_sensor_ci95_low",
    "collection_per_installed_sensor_ci95_high",
    "collection_per_active_area_mm2",
    "collection_per_active_area_mm2_ci95_low",
    "collection_per_active_area_mm2_ci95_high",
    "aggregate_net",
    "aggregate_net_ci95_low",
    "aggregate_net_ci95_high",
    "net_per_installed_sensor",
    "net_per_installed_sensor_ci95_low",
    "net_per_installed_sensor_ci95_high",
    "net_per_active_area_mm2",
    "net_per_active_area_mm2_ci95_low",
    "net_per_active_area_mm2_ci95_high",
    "collection_valid_resamples",
    "net_valid_resamples",
)
POOLED_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "layouts",
    "layout_count",
    "events",
    "production_weighting",
    "pooled_generated_photons_per_neutron",
    "generated_ci95_low",
    "generated_ci95_high",
    "generated_valid_resamples",
    "pooled_scintillation_photons_per_neutron",
    "scintillation_ci95_low",
    "scintillation_ci95_high",
    "scintillation_valid_resamples",
    "back_center_scintillation_per_neutron",
    "edge_center_scintillation_per_neutron",
    "edge_two_scintillation_per_neutron",
    "back_four_scintillation_per_neutron",
    "scintillation_layout_min",
    "scintillation_layout_max",
    "scintillation_layout_cv",
)
STANDARDIZED_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events",
    "sensor_count",
    "active_area_mm2",
    "direct_observed_net",
    "standardized_net",
    "standardized_ci95_low",
    "standardized_ci95_high",
    "standardized_valid_resamples",
    "standardized_net_per_installed_sensor",
    "standardized_net_per_active_area_mm2",
    "direct_minus_standardized",
    "model_role",
)
RATIO_METRIC_FIELDS = original.RATIO_METRIC_FIELDS
THICKNESS_RATIO_FIELDS = original.THICKNESS_RATIO_FIELDS
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
    "collection_per_installed_sensor_ratio",
    "collection_per_installed_sensor_ci95_low",
    "collection_per_installed_sensor_ci95_high",
    "net_per_installed_sensor_ratio",
    "net_per_installed_sensor_ci95_low",
    "net_per_installed_sensor_ci95_high",
    "net_per_active_area_ratio",
    "net_per_active_area_ci95_low",
    "net_per_active_area_ci95_high",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--fixed-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s1-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s2-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s3-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s4-campaign-dir", required=True, type=Path)
    parser.add_argument("--edge-two-campaign-dir", required=True, type=Path)
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


def sensor_count(layout: str) -> int:
    count = v1.sensor_count(layout)
    if SENSOR_COUNTS.get(layout) != count:
        raise ValueError(f"four-layout sensor-count contract drifted for {layout}")
    return count


def expected_blocks(layout: str, thickness: int) -> set[int]:
    if layout == "edge-two":
        return set(range(40))
    return original.expected_blocks(layout, thickness)


def validate_four_layout_shape(
    loaded: v2.LoadedEvents, *, total_tasks: int, seed_count: int
) -> None:
    expected_keys = {
        ("production", thickness, layout, 500, 0, 0)
        for layout in LAYOUTS
        for thickness in THICKNESSES
    }
    if set(loaded.groups) != expected_keys:
        raise ValueError("four-layout sample is not the exact 24-configuration matrix")
    if total_tasks != TOTAL_TASKS or seed_count != 2 * TOTAL_TASKS:
        raise ValueError("four-layout task/seed total mismatch")
    if len(loaded.task_ids) != TOTAL_TASKS:
        raise ValueError("four-layout task identity total mismatch")
    if sum(len(events) for events in loaded.groups.values()) != TOTAL_EVENTS:
        raise ValueError("four-layout event total mismatch")
    for key in expected_keys:
        _, thickness, layout, absorber, x_mm, y_mm = key
        if (absorber, x_mm, y_mm) != (500, 0, 0):
            raise ValueError("four-layout geometry axes are invalid")
        blocks = expected_blocks(layout, thickness)
        if set(loaded.blocks[key]) != blocks:
            raise ValueError(f"four-layout block registry mismatch: {layout}/{thickness}")
        if len(loaded.groups[key]) != 250 * len(blocks):
            raise ValueError(f"four-layout event count mismatch: {layout}/{thickness}")
        if any(len(events) != 250 for events in loaded.blocks[key].values()):
            raise ValueError(f"four-layout block size mismatch: {layout}/{thickness}")


def combine_inputs(
    original_loaded: v2.LoadedEvents,
    edge_input: original.LoadedInput,
    inputs: Sequence[original.LoadedInput],
) -> v2.LoadedEvents:
    groups = {key: list(events) for key, events in original_loaded.groups.items()}
    blocks = {
        key: {block: list(events) for block, events in by_block.items()}
        for key, by_block in original_loaded.blocks.items()
    }
    task_ids = dict(original_loaded.task_ids)
    for key, events in edge_input.loaded.groups.items():
        if key in groups:
            raise ValueError(f"edge-two overlaps original configuration: {key}")
        groups[key] = list(events)
    for key, by_block in edge_input.loaded.blocks.items():
        if key in blocks:
            raise ValueError(f"edge-two overlaps original block registry: {key}")
        blocks[key] = {block: list(events) for block, events in by_block.items()}
    for identity, logical_id in edge_input.loaded.task_ids.items():
        if identity in task_ids:
            raise ValueError(f"edge-two overlaps original task identity: {identity}")
        task_ids[identity] = logical_id

    tasks = [task for item in inputs for task in item.bundle.tasks]
    logical_ids = [task.logical_task_id for task in tasks]
    seeds = [seed for task in tasks for seed in (task.seed1, task.seed2)]
    if len(set(logical_ids)) != len(logical_ids):
        raise ValueError("four-layout logical task IDs overlap")
    if len(set(seeds)) != len(seeds):
        raise ValueError("four-layout production seeds overlap")
    loaded = v2.LoadedEvents(groups, blocks, task_ids)
    validate_four_layout_shape(
        loaded, total_tasks=len(logical_ids), seed_count=len(seeds)
    )
    return loaded


def build_bootstrap_cache(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]], *, np: object
) -> dict[v1.ConfigurationKey, dict[str, list[float]]]:
    return {
        key: original.bootstrap_configuration(
            events,
            resamples=BOOTSTRAP_RESAMPLES,
            seed=v1.configuration_seed(BOOTSTRAP_SEED, (SCHEMA_VERSION, *key)),
            np=np,
        )
        for key, events in groups.items()
    }


def configuration_rows(
    loaded: v2.LoadedEvents,
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows = v2.configuration_rows(loaded, cache)
    for row in rows:
        layout = str(row["sipm_layout"])
        sensors = sensor_count(layout)
        area = sensors * SENSOR_AREA_MM2
        net = float(row["observed_net_sipm_photons_per_neutron"])
        row["sensor_count"] = sensors
        row["active_area_mm2"] = area
        row["observed_net_per_installed_sensor"] = net / sensors
        row["observed_net_per_active_area_mm2"] = net / area
    return rows


def ratio_draws(
    numerator: Sequence[float], denominator: Sequence[float]
) -> list[float]:
    return [
        float(top) / float(bottom) if float(bottom) > 0 else math.nan
        for top, bottom in zip(numerator, denominator)
    ]


def per_sensor_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (LAYOUTS.index(item[2]), item[1])):
        stage, thickness, layout, absorber, x_mm, y_mm = key
        events = groups[key]
        sensors = sensor_count(layout)
        aggregate = sum(event.sipm for event in events) / len(events)
        for index in range(sensors):
            values = [event.sensors[index] for event in events]
            point = sum(values) / len(values)
            net_low, net_high, net_valid = v1.interval(cache[key][f"sensor_{index}"])
            collection_draws = ratio_draws(
                cache[key][f"sensor_{index}"], cache[key]["generated"]
            )
            collection_low, collection_high, collection_valid = v1.interval(
                collection_draws
            )
            generated = sum(event.generated for event in events)
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
                    "sensor_count": sensors,
                    "active_area_mm2": SENSOR_AREA_MM2,
                    "sipm_photons_per_neutron": point,
                    "net_ci95_low": net_low,
                    "net_ci95_high": net_high,
                    "net_valid_resamples": net_valid,
                    "collection_sipm_over_generated": (
                        sum(values) / generated if generated > 0 else math.nan
                    ),
                    "collection_ci95_low": collection_low,
                    "collection_ci95_high": collection_high,
                    "collection_valid_resamples": collection_valid,
                    "fraction_of_layout_aggregate": (
                        point / aggregate if aggregate > 0 else math.nan
                    ),
                }
            )
    return rows


def layout_cost_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (LAYOUTS.index(item[2]), item[1])):
        stage, thickness, layout, absorber, x_mm, y_mm = key
        sensors = sensor_count(layout)
        area = sensors * SENSOR_AREA_MM2
        point = v2.configuration_estimator(groups[key])
        collection_low, collection_high, collection_valid = v1.interval(
            cache[key]["collection"]
        )
        net_low, net_high, net_valid = v1.interval(cache[key]["net"])
        rows.append(
            {
                "stage": stage,
                "tile_thickness_mm": thickness,
                "sipm_layout": layout,
                "absorber_transverse_mm": absorber,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "events": len(groups[key]),
                "sensor_count": sensors,
                "active_area_mm2": area,
                "aggregate_collection": point["collection"],
                "aggregate_collection_ci95_low": collection_low,
                "aggregate_collection_ci95_high": collection_high,
                "collection_per_installed_sensor": point["collection"] / sensors,
                "collection_per_installed_sensor_ci95_low": collection_low / sensors,
                "collection_per_installed_sensor_ci95_high": collection_high / sensors,
                "collection_per_active_area_mm2": point["collection"] / area,
                "collection_per_active_area_mm2_ci95_low": collection_low / area,
                "collection_per_active_area_mm2_ci95_high": collection_high / area,
                "aggregate_net": point["net"],
                "aggregate_net_ci95_low": net_low,
                "aggregate_net_ci95_high": net_high,
                "net_per_installed_sensor": point["net"] / sensors,
                "net_per_installed_sensor_ci95_low": net_low / sensors,
                "net_per_installed_sensor_ci95_high": net_high / sensors,
                "net_per_active_area_mm2": point["net"] / area,
                "net_per_active_area_mm2_ci95_low": net_low / area,
                "net_per_active_area_mm2_ci95_high": net_high / area,
                "collection_valid_resamples": collection_valid,
                "net_valid_resamples": net_valid,
            }
        )
    return rows


def pooled_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    rows: list[dict[str, object]] = []
    pooled_cache: dict[int, dict[str, object]] = {}
    for thickness in THICKNESSES:
        keys = {
            layout: ("production", thickness, layout, 500, 0, 0)
            for layout in LAYOUTS
        }
        points = {
            layout: v2.configuration_estimator(groups[keys[layout]])
            for layout in LAYOUTS
        }
        generated = sum(float(points[layout]["generated"]) for layout in LAYOUTS) / 4
        scintillation = (
            sum(float(points[layout]["scintillation"]) for layout in LAYOUTS) / 4
        )
        generated_draws = [
            sum(float(cache[keys[layout]]["generated"][index]) for layout in LAYOUTS)
            / 4
            for index in range(BOOTSTRAP_RESAMPLES)
        ]
        scintillation_draws = [
            sum(
                float(cache[keys[layout]]["scintillation"][index])
                for layout in LAYOUTS
            )
            / 4
            for index in range(BOOTSTRAP_RESAMPLES)
        ]
        gen_low, gen_high, gen_valid = v1.interval(generated_draws)
        sci_low, sci_high, sci_valid = v1.interval(scintillation_draws)
        strata = [float(points[layout]["scintillation"]) for layout in LAYOUTS]
        rows.append(
            {
                "stage": "production",
                "tile_thickness_mm": thickness,
                "absorber_transverse_mm": 500,
                "x_mm": 0,
                "y_mm": 0,
                "layouts": ",".join(LAYOUTS),
                "layout_count": 4,
                "events": sum(len(groups[key]) for key in keys.values()),
                "production_weighting": "equal-four-layout-strata",
                "pooled_generated_photons_per_neutron": generated,
                "generated_ci95_low": gen_low,
                "generated_ci95_high": gen_high,
                "generated_valid_resamples": gen_valid,
                "pooled_scintillation_photons_per_neutron": scintillation,
                "scintillation_ci95_low": sci_low,
                "scintillation_ci95_high": sci_high,
                "scintillation_valid_resamples": sci_valid,
                "back_center_scintillation_per_neutron": points["back-center"]["scintillation"],
                "edge_center_scintillation_per_neutron": points["edge-center"]["scintillation"],
                "edge_two_scintillation_per_neutron": points["edge-two"]["scintillation"],
                "back_four_scintillation_per_neutron": points["back-four"]["scintillation"],
                "scintillation_layout_min": min(strata),
                "scintillation_layout_max": max(strata),
                "scintillation_layout_cv": v2.cv(strata),
            }
        )
        pooled_cache[thickness] = {
            "generated_point": generated,
            "generated": generated_draws,
            "scintillation_point": scintillation,
            "scintillation": scintillation_draws,
        }
    return rows, pooled_cache


def standardized_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[int, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (LAYOUTS.index(item[2]), item[1])):
        stage, thickness, layout, absorber, x_mm, y_mm = key
        sensors = sensor_count(layout)
        area = sensors * SENSOR_AREA_MM2
        point = v2.configuration_estimator(groups[key])
        pool = pooled_cache[thickness]
        standardized = float(pool["generated_point"]) * float(point["collection"])
        draws = [
            float(generated) * float(collection)
            for generated, collection in zip(
                pool["generated"], cache[key]["collection"]
            )
        ]
        low, high, valid = v1.interval(draws)
        rows.append(
            {
                "stage": stage,
                "tile_thickness_mm": thickness,
                "sipm_layout": layout,
                "absorber_transverse_mm": absorber,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "events": len(groups[key]),
                "sensor_count": sensors,
                "active_area_mm2": area,
                "direct_observed_net": point["net"],
                "standardized_net": standardized,
                "standardized_ci95_low": low,
                "standardized_ci95_high": high,
                "standardized_valid_resamples": valid,
                "standardized_net_per_installed_sensor": standardized / sensors,
                "standardized_net_per_active_area_mm2": standardized / area,
                "direct_minus_standardized": point["net"] - standardized,
                "model_role": "secondary-production-standardized-diagnostic",
            }
        )
    return rows


def thickness_ratio_rows(
    groups: Mapping[v1.ConfigurationKey, Sequence[v1.Event]],
    cache: Mapping[v1.ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for layout in LAYOUTS:
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
            original.add_metric_ratios(
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
        for reference_layout, compared_layout in itertools.combinations(LAYOUTS, 2):
            reference = ("production", thickness, reference_layout, 500, 0, 0)
            compared = ("production", thickness, compared_layout, 500, 0, 0)
            reference_sensors = sensor_count(reference_layout)
            compared_sensors = sensor_count(compared_layout)
            sensor_scale = reference_sensors / compared_sensors
            row: dict[str, object] = {
                "stage": "production",
                "tile_thickness_mm": thickness,
                "absorber_transverse_mm": 500,
                "x_mm": 0,
                "y_mm": 0,
                "reference_sipm_layout": reference_layout,
                "compared_sipm_layout": compared_layout,
                "reference_events": len(groups[reference]),
                "compared_events": len(groups[compared]),
                "reference_sensor_count": reference_sensors,
                "compared_sensor_count": compared_sensors,
                "active_area_ratio": compared_sensors / reference_sensors,
            }
            original.add_metric_ratios(
                row,
                numerator_key=compared,
                denominator_key=reference,
                groups=groups,
                cache=cache,
            )
            for metric in ("collection", "net"):
                row[f"{metric}_per_installed_sensor_ratio"] = (
                    float(row[f"{metric}_ratio"]) * sensor_scale
                )
                row[f"{metric}_per_installed_sensor_ci95_low"] = (
                    float(row[f"{metric}_ci95_low"]) * sensor_scale
                )
                row[f"{metric}_per_installed_sensor_ci95_high"] = (
                    float(row[f"{metric}_ci95_high"]) * sensor_scale
                )
            row["net_per_active_area_ratio"] = row["net_per_installed_sensor_ratio"]
            row["net_per_active_area_ci95_low"] = row[
                "net_per_installed_sensor_ci95_low"
            ]
            row["net_per_active_area_ci95_high"] = row[
                "net_per_installed_sensor_ci95_high"
            ]
            rows.append(row)
    return rows


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
    sensors: Sequence[Mapping[str, object]],
    layout_ratios: Sequence[Mapping[str, object]],
) -> str:
    by_key = {
        (str(row["sipm_layout"]), int(row["tile_thickness_mm"])): row
        for row in configurations
    }
    side_ratios = {
        int(row["tile_thickness_mm"]): row
        for row in layout_ratios
        if row["reference_sipm_layout"] == "edge-center"
        and row["compared_sipm_layout"] == "edge-two"
    }
    lines = [
        "# Four-layout Steel Module Production Analysis",
        "",
        "This combines the checksum-valid original direct production sample with the checksum-valid single-layer edge-two follow-up. It creates no scheduler action or Geant4 event.",
        "",
        f"- Sample: `{TOTAL_TASKS}` tasks, `{TOTAL_EVENTS:,}` events, `{TOTAL_CONFIGURATIONS}` configurations.",
        "- Layout sensor counts: back-center `1`, edge-center `1`, edge-two `2`, back-four `4`.",
        "- Each SiPM active-volume proxy is `2.4 x 2.4 x 0.5 mm`; one active face area is `5.76 mm^2`.",
        "- Intervals: 10,000 event-bootstrap resamples, seed 20260715, percentile 95% intervals.",
        "- The four-layout pooled-production curve gives each layout stratum equal weight; it is a descriptive reconciliation, not a replacement for the accepted original primary contrasts.",
        "",
        "## Direct configuration curves",
        "",
        "| Layout | Thickness (mm) | Events | Sensors | Scintillation / neutron | Aggregate collection | Aggregate SiPM / neutron | Per sensor | Per active mm2 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for layout in LAYOUTS:
        for thickness in THICKNESSES:
            row = by_key[(layout, thickness)]
            lines.append(
                f"| {layout} | {thickness} | {row['events']} | {row['sensor_count']} | "
                f"{float(row['scintillation_photons_per_neutron']):.6g} | "
                f"{float(row['collection_sipm_over_generated']):.6g} | "
                f"{float(row['observed_net_sipm_photons_per_neutron']):.6g} | "
                f"{float(row['observed_net_per_installed_sensor']):.6g} | "
                f"{float(row['observed_net_per_active_area_mm2']):.6g} |"
            )
    lines.extend(
        [
            "",
            "## Edge-two versus edge-center",
            "",
            "Both layouts use the same +X side face. Aggregate ratios show the total two-sensor response; normalized ratios divide out the twofold installed sensor area.",
            "",
            "| Thickness (mm) | Aggregate collection ratio | Aggregate net ratio | Net ratio per sensor / active area |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for thickness in THICKNESSES:
        row = side_ratios[thickness]
        lines.append(
            f"| {thickness} | {float(row['collection_ratio']):.6g} | "
            f"{float(row['net_ratio']):.6g} | "
            f"{float(row['net_per_installed_sensor_ratio']):.6g} |"
        )
    lines.extend(["", "## Multi-sensor copy diagnostics", ""])
    for layout in ("edge-two", "back-four"):
        for thickness in THICKNESSES:
            selected = [
                row
                for row in sensors
                if row["sipm_layout"] == layout
                and int(row["tile_thickness_mm"]) == thickness
            ]
            values = ", ".join(
                f"copy {int(row['sensor_index'])}: {float(row['collection_sipm_over_generated']):.6g}"
                for row in selected
            )
            lines.append(f"- {layout} / {thickness} mm collection: {values}.")
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "Aggregate response is the directly observed total across all installed SiPM proxy volumes. Per-sensor and per-active-area quantities are secondary layout-cost diagnostics; because every proxy has the same active area, their ratios differ only by the constant 5.76 mm2 per sensor.",
            "",
            "`SiPM photons` are optical photons entering the silicon proxy, not photoelectrons, PDE-folded response, or electronics output. Production-standardized response is model-based and secondary. No result in this analysis automatically selects a detector layout or launches more simulation.",
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
        raise ValueError("analysis checkout changed during four-layout analysis")
    if run_git(repo_root, "status", "--porcelain", "--untracked-files=all") != dirty:
        raise ValueError("analysis worktree changed during four-layout analysis")
    if dirty:
        raise ValueError("accepted four-layout analysis requires a clean checkout")
    for relative, digest in source_hashes.items():
        if sha256_file(repo_root / relative) != digest:
            raise ValueError(f"analysis source changed during execution: {relative}")


def main() -> int:
    args = parse_args()
    repo_root = args.project_root.expanduser().resolve()
    original_paths = (
        args.fixed_campaign_dir,
        args.bc_s1_campaign_dir,
        args.bc_s2_campaign_dir,
        args.bc_s3_campaign_dir,
        args.bc_s4_campaign_dir,
    )
    edge_path = args.edge_two_campaign_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else edge_path / "finalized" / "four-layout-analysis"
    )
    temp_dir: Path | None = None
    try:
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("four-layout production analysis requires NumPy") from exc
        commit = run_git(repo_root, "rev-parse", "HEAD")
        branch = run_git(repo_root, "branch", "--show-current")
        dirty = run_git(repo_root, "status", "--porcelain", "--untracked-files=all")
        if dirty:
            raise ValueError("accepted four-layout analysis requires a clean checkout")
        source_files = (
            "hpc/osc/analyze_steel_module_four_layout.py",
            "hpc/osc/analyze_steel_module_direct_final.py",
            "hpc/osc/analyze_steel_module_campaign.py",
            "hpc/osc/analyze_steel_module_campaign_v2.py",
            "hpc/osc/generate_steel_module_direct_edge_two_campaign.py",
            "hpc/osc/steel_module_campaign_lib.py",
        )
        source_hashes = {
            relative: sha256_file(repo_root / relative) for relative in source_files
        }
        original_inputs = tuple(
            original.load_input(spec, path)
            for spec, path in zip(original.INPUT_SPECS, original_paths)
        )
        original_loaded = original.merge_inputs(original_inputs)
        edge_input = original.load_input(EDGE_TWO_SPEC, edge_path)
        inputs = (*original_inputs, edge_input)
        loaded = combine_inputs(original_loaded, edge_input, inputs)
        cache = build_bootstrap_cache(loaded.groups, np=np)
        configurations = configuration_rows(loaded, cache)
        distributions = v2.distribution_rows(loaded.groups, np=np)
        pathways = v2.pathway_rows(loaded.groups)
        sensors = per_sensor_rows(loaded.groups, cache)
        costs = layout_cost_rows(loaded.groups, cache)
        pooled, pooled_cache = pooled_rows(loaded.groups, cache)
        standardized = standardized_rows(loaded.groups, cache, pooled_cache)
        thickness = thickness_ratio_rows(loaded.groups, cache)
        layouts = layout_ratio_rows(loaded.groups, cache)
        for rows in (
            configurations,
            sensors,
            costs,
            pooled,
            standardized,
            thickness,
            layouts,
        ):
            require_complete_bootstrap(rows)
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite four-layout analysis: {output_dir}")
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
        write_csv(temp_dir / "per_sensor_efficiency.csv", PER_SENSOR_FIELDS, sensors)
        write_csv(temp_dir / "layout_cost_diagnostics.csv", LAYOUT_COST_FIELDS, costs)
        write_csv(temp_dir / "pooled_production.csv", POOLED_FIELDS, pooled)
        write_csv(temp_dir / "standardized_response.csv", STANDARDIZED_FIELDS, standardized)
        write_csv(temp_dir / "thickness_ratios.csv", THICKNESS_RATIO_FIELDS, thickness)
        write_csv(temp_dir / "layout_ratios.csv", LAYOUT_RATIO_FIELDS, layouts)
        (temp_dir / "summary.md").write_text(
            summary_markdown(configurations, sensors, layouts), encoding="utf-8"
        )
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
                "configurations": TOTAL_CONFIGURATIONS,
                "layouts": list(LAYOUTS),
                "sensor_counts": SENSOR_COUNTS,
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
            "production_reconciliation": {
                "weighting": "equal-four-layout-strata",
                "weight_per_layout": 0.25,
                "role": "descriptive follow-up; does not replace accepted original primary contrasts",
            },
            "layout_cost_semantics": {
                "aggregate": "sum across all installed SiPM proxy volumes",
                "per_sensor": "aggregate divided by installed sensor count",
                "per_active_area": "aggregate divided by installed active area in mm2",
                "single_sensor_active_area_mm2": SENSOR_AREA_MM2,
                "role": "secondary diagnostic",
            },
            "automatic_acceptance": False,
            "automatic_additional_simulation": False,
            "outputs": {
                "configuration_estimates": "configuration_estimates.csv",
                "distribution_diagnostics": "distribution_diagnostics.csv",
                "response_pathway": "response_pathway.csv",
                "per_sensor_efficiency": "per_sensor_efficiency.csv",
                "layout_cost_diagnostics": "layout_cost_diagnostics.csv",
                "pooled_production": "pooled_production.csv",
                "standardized_response": "standardized_response.csv",
                "thickness_ratios": "thickness_ratios.csv",
                "layout_ratios": "layout_ratios.csv",
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
        print(f"Cannot analyze four-layout steel-module production: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)
    print(f"Analyzed four-layout production into {output_dir}")
    print(
        f"Tasks: {TOTAL_TASKS}; events: {TOTAL_EVENTS}; configurations: "
        f"{TOTAL_CONFIGURATIONS}"
    )
    print("Layouts: back-center=1, edge-center=1, edge-two=2, back-four=4 SiPMs")
    print("No scheduler action or Geant4 event was created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
