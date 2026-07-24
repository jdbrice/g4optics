#!/usr/bin/env python3
"""Run the pinned second-pass analysis for a finalized steel-module pilot."""

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
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import analyze_steel_module_campaign as v1
from realistic_neutron_campaign_lib import verify_checksum_manifest
from steel_module_campaign_lib import (
    SIPM_LAYOUTS,
    atomic_write_json,
    resolve_campaign_path,
    sha256_file,
)


ANALYSIS_SCHEMA_VERSION = "steel-module-analysis-v2-config-v1"
PINNED_BOOTSTRAP_SEED = 20260715
PINNED_BOOTSTRAP_RESAMPLES = 10_000
PINNED_CONFIDENCE_LEVEL = 0.95
PINNED_PRODUCTION_BLOCK_EVENTS = 250
PRECISION_TARGETS = (0.05, 0.10)
EQUIVALENCE_HALF_WIDTHS = (0.05, 0.10)
PROJECTION_SAFETY_FACTOR = 1.25
SENSOR_AREA_MM2 = 2.4 * 2.4
METRICS = ("generated", "scintillation", "collection", "net")
DISTRIBUTION_METRICS = ("generated", "scintillation", "sipm")
ZERO_FRACTION_METRICS = (
    "generated_zero_fraction",
    "scintillation_zero_fraction",
    "sipm_zero_fraction",
)

ConfigurationKey = v1.ConfigurationKey
Event = v1.Event
PooledKey = tuple[str, int, int, int, int]


CONFIGURATION_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_blocks",
    "events",
    "sensor_count",
    "active_area_mm2",
    "generated_optical_photons_per_neutron",
    "generated_ci95_low",
    "generated_ci95_high",
    "generated_valid_resamples",
    "scintillation_photons_per_neutron",
    "scintillation_ci95_low",
    "scintillation_ci95_high",
    "scintillation_valid_resamples",
    "collection_sipm_over_generated",
    "collection_ci95_low",
    "collection_ci95_high",
    "collection_valid_resamples",
    "observed_net_sipm_photons_per_neutron",
    "net_ci95_low",
    "net_ci95_high",
    "net_valid_resamples",
    "observed_net_per_installed_sensor",
    "observed_net_per_active_area_mm2",
    "interaction_fraction",
    "interaction_wilson95_low",
    "interaction_wilson95_high",
    "generated_zero_fraction",
    "generated_zero_wilson95_low",
    "generated_zero_wilson95_high",
    "scintillation_zero_fraction",
    "scintillation_zero_wilson95_low",
    "scintillation_zero_wilson95_high",
    "sipm_zero_fraction",
    "sipm_zero_wilson95_low",
    "sipm_zero_wilson95_high",
)

DISTRIBUTION_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "metric",
    "events",
    "positive_events",
    "zero_events",
    "zero_fraction",
    "sum",
    "mean",
    "rms",
    "population_stddev",
    "standard_error",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "max",
    "positive_mean",
    "positive_rms",
    "positive_population_stddev",
    "positive_standard_error",
    "positive_p50",
    "positive_p75",
    "positive_p90",
    "positive_p95",
    "positive_p99",
    "positive_max",
    "top_1pct_event_count",
    "top_1pct_sum_fraction",
    "top_1pct_fraction_valid",
    "top_5pct_event_count",
    "top_5pct_sum_fraction",
    "top_5pct_fraction_valid",
)

PATHWAY_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "incident_events",
    "interaction_events",
    "generated_positive_events",
    "sipm_positive_events",
    "interaction_fraction",
    "generated_positive_given_interaction",
    "generated_positive_given_interaction_valid",
    "sipm_positive_given_generated",
    "sipm_positive_given_generated_valid",
    "sipm_positive_given_interaction",
    "sipm_positive_given_interaction_valid",
    "scintillation_per_interaction",
    "scintillation_per_interaction_valid",
    "sipm_per_generated_positive_event",
    "sipm_per_generated_positive_event_valid",
    "sipm_per_sipm_positive_event",
    "sipm_per_sipm_positive_event_valid",
)

BLOCK_STABILITY_FIELDS = (
    "scope",
    "estimate_variant",
    "primary_contrast_id",
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_block",
    "omitted_arm",
    "events",
    "full_events",
    "generated",
    "full_generated",
    "generated_relative_shift",
    "generated_absolute_relative_shift",
    "generated_shift_valid",
    "generated_within_10pct",
    "generated_within_20pct",
    "scintillation",
    "full_scintillation",
    "scintillation_relative_shift",
    "scintillation_absolute_relative_shift",
    "scintillation_shift_valid",
    "scintillation_within_10pct",
    "scintillation_within_20pct",
    "collection",
    "full_collection",
    "collection_relative_shift",
    "collection_absolute_relative_shift",
    "collection_shift_valid",
    "collection_within_10pct",
    "collection_within_20pct",
    "net",
    "full_net",
    "net_relative_shift",
    "net_absolute_relative_shift",
    "net_shift_valid",
    "net_within_10pct",
    "net_within_20pct",
    "generated_zero_fraction",
    "full_generated_zero_fraction",
    "generated_zero_fraction_relative_shift",
    "generated_zero_fraction_absolute_relative_shift",
    "generated_zero_fraction_shift_valid",
    "generated_zero_fraction_within_10pct",
    "generated_zero_fraction_within_20pct",
    "scintillation_zero_fraction",
    "full_scintillation_zero_fraction",
    "scintillation_zero_fraction_relative_shift",
    "scintillation_zero_fraction_absolute_relative_shift",
    "scintillation_zero_fraction_shift_valid",
    "scintillation_zero_fraction_within_10pct",
    "scintillation_zero_fraction_within_20pct",
    "sipm_zero_fraction",
    "full_sipm_zero_fraction",
    "sipm_zero_fraction_relative_shift",
    "sipm_zero_fraction_absolute_relative_shift",
    "sipm_zero_fraction_shift_valid",
    "sipm_zero_fraction_within_10pct",
    "sipm_zero_fraction_within_20pct",
    "contrast_ratio",
    "full_contrast_ratio",
    "contrast_relative_shift",
    "contrast_absolute_relative_shift",
    "contrast_shift_valid",
    "contrast_within_10pct",
    "contrast_within_20pct",
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
    "events_back_center",
    "events_edge_center",
    "events_back_four",
    "pooled_generated_photons_per_neutron",
    "generated_ci95_low",
    "generated_ci95_high",
    "generated_valid_resamples",
    "pooled_scintillation_photons_per_neutron",
    "scintillation_ci95_low",
    "scintillation_ci95_high",
    "scintillation_valid_resamples",
    "back_center_generated_per_neutron",
    "edge_center_generated_per_neutron",
    "back_four_generated_per_neutron",
    "generated_layout_min",
    "generated_layout_max",
    "generated_layout_cv",
    "back_center_scintillation_per_neutron",
    "edge_center_scintillation_per_neutron",
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
    "direct_observed_events",
    "layout_collection_events",
    "pooled_production_events_total",
    "sensor_count",
    "active_area_mm2",
    "pooled_generated_photons_per_neutron",
    "collection_sipm_over_generated",
    "direct_observed_net",
    "direct_net_ci95_low",
    "direct_net_ci95_high",
    "direct_net_per_installed_sensor",
    "direct_net_per_active_area_mm2",
    "standardized_net",
    "standardized_net_ci95_low",
    "standardized_net_ci95_high",
    "standardized_net_valid_resamples",
    "standardized_minus_direct",
    "difference_ci95_low",
    "difference_ci95_high",
    "difference_valid_resamples",
    "standardized_over_direct",
    "standardized_net_per_installed_sensor",
    "standardized_net_per_active_area_mm2",
    "interpretation",
)

PRIMARY_FIELDS = (
    "primary_contrast_id",
    "family",
    "metric",
    "sipm_layout",
    "reference_tile_thickness_mm",
    "compared_tile_thickness_mm",
    "absorber_transverse_mm",
    "reference_events_total",
    "compared_events_total",
    "sample_basis",
    "reference_layout_collection_events",
    "compared_layout_collection_events",
    "reference_pooled_production_events_total",
    "compared_pooled_production_events_total",
    "reference_events_per_configuration",
    "compared_events_per_configuration",
    "ratio",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
    "relative_half_width",
    "ci_excludes_unity",
    "max_leave_one_block_out_relative_shift",
    "leave_one_block_out_attempts",
    "leave_one_block_out_valid_evaluations",
    "leave_one_block_out_evaluations",
    "block_shift_within_10pct",
    "block_shift_within_20pct",
    "automatic_acceptance",
)

SECONDARY_FIELDS = (
    "contrast_family",
    "metric",
    "sipm_layout",
    "reference_tile_thickness_mm",
    "compared_tile_thickness_mm",
    "absorber_transverse_mm",
    "reference_events_total",
    "compared_events_total",
    "sample_basis",
    "reference_layout_collection_events",
    "compared_layout_collection_events",
    "reference_pooled_production_events_total",
    "compared_pooled_production_events_total",
    "ratio",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
    "relative_half_width",
    "ci_excludes_unity",
    "controls_production_sizing",
)

ABSORBER_FIELDS = (
    "scope",
    "metric",
    "sipm_layout",
    "tile_thickness_mm",
    "candidate_absorber_transverse_mm",
    "reference_absorber_transverse_mm",
    "candidate_events_total",
    "reference_events_total",
    "ratio",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
    "difference_detected",
    "equivalent_within_5pct",
    "equivalent_within_10pct",
    "review_status_5pct",
    "review_status_10pct",
    "equivalence_rule",
    "automatic_acceptance",
)


@dataclass(frozen=True)
class LoadedEvents:
    groups: dict[ConfigurationKey, list[Event]]
    blocks: dict[ConfigurationKey, dict[int, list[Event]]]
    task_ids: dict[tuple[ConfigurationKey, int], str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--finalized-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--event-fixture-dir", type=Path)
    parser.add_argument("--testing-allow-event-fixtures", action="store_true")
    parser.add_argument("--testing-allow-dirty-analysis", action="store_true")
    parser.add_argument("--production-block-events", type=int)
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


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def verify_analysis_checkout_stable(
    *,
    repo_root: Path,
    commit: str,
    branch: str,
    dirty_text: str,
    analyzer_path: Path,
    analyzer_hash: str,
    v1_path: Path,
    v1_hash: str,
) -> None:
    if (
        run_git(repo_root, "rev-parse", "HEAD") != commit
        or run_git(repo_root, "branch", "--show-current") != branch
        or run_git(repo_root, "status", "--porcelain", "--untracked-files=all")
        != dirty_text
        or dirty_text
        or sha256_file(analyzer_path) != analyzer_hash
        or sha256_file(v1_path) != v1_hash
    ):
        raise ValueError(
            "analysis checkout or analyzer sources changed during analysis-v2"
        )


def capture_input_hashes(
    campaign_dir: Path, finalized_dir: Path
) -> dict[str, str]:
    paths = {
        "campaign/campaign.json": campaign_dir / "campaign.json",
        "campaign/tasks.tsv": campaign_dir / "tasks.tsv",
        "campaign/scan_args.txt": campaign_dir / "scan_args.txt",
        "finalized/SHA256SUMS": finalized_dir / "SHA256SUMS",
        "finalized/validation_report.json": finalized_dir
        / "validation_report.json",
        "finalized/event_audit.json": finalized_dir / "event_audit.json",
        "finalized/analysis_config.json": finalized_dir / "analysis_config.json",
        "finalized/task_index.tsv": finalized_dir / "task_index.tsv",
    }
    v1_analysis = finalized_dir / "analysis"
    if v1_analysis.is_dir():
        paths.update(
            {
                "v1-analysis/SHA256SUMS": v1_analysis / "SHA256SUMS",
                "v1-analysis/analysis_config.json": v1_analysis
                / "analysis_config.json",
            }
        )
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"analysis-v2 input snapshot has missing files: {missing}")
    return {name: sha256_file(path) for name, path in paths.items()}


def verify_input_snapshot(
    *,
    campaign_dir: Path,
    finalized_dir: Path,
    expected_hashes: Mapping[str, str],
    expected_campaign_id: str,
    expected_plan_hash: str,
    expected_simulation_commit: str,
    expected_environment_identity: object,
    audited_roots: Mapping[str, v1.AuditedRoot],
) -> None:
    current_hashes = capture_input_hashes(campaign_dir, finalized_dir)
    if current_hashes != dict(expected_hashes):
        raise ValueError("campaign or finalized inputs changed during analysis-v2")
    v1_analysis = finalized_dir / "analysis"
    if v1_analysis.is_dir():
        verify_checksum_manifest(
            v1_analysis,
            required=(
                "configuration_intervals.csv",
                "thickness_ratios.csv",
                "analysis_config.json",
            ),
        )
    bundle, _validation, _config, current_roots = v1.validate_identity(
        campaign_dir, finalized_dir
    )
    if (
        bundle.campaign_id != expected_campaign_id
        or bundle.plan_hash != expected_plan_hash
        or bundle.git_commit != expected_simulation_commit
        or bundle.environment.get("identity_hash") != expected_environment_identity
        or set(current_roots) != set(audited_roots)
    ):
        raise ValueError("campaign identity changed during analysis-v2")
    for logical_id, frozen in audited_roots.items():
        current = current_roots[logical_id]
        if current != frozen:
            raise ValueError(f"event-audit ROOT identity changed: {logical_id}")
        root_path = resolve_campaign_path(campaign_dir, frozen.path)
        if sha256_file(root_path) != frozen.sha256:
            raise ValueError(f"audited ROOT changed during analysis-v2: {root_path}")


def safe_divide(numerator: float, denominator: float) -> tuple[float, bool]:
    if denominator <= 0:
        return math.nan, False
    return numerator / denominator, True


def sensor_count(layout: str) -> int:
    return v1.sensor_count(layout)


def key_identity(key: ConfigurationKey) -> dict[str, object]:
    stage, thickness, layout, absorber, x_mm, y_mm = key
    return {
        "stage": stage,
        "tile_thickness_mm": thickness,
        "sipm_layout": layout,
        "absorber_transverse_mm": absorber,
        "x_mm": x_mm,
        "y_mm": y_mm,
    }


def pooled_key(key: ConfigurationKey) -> PooledKey:
    stage, thickness, _layout, absorber, x_mm, y_mm = key
    return (stage, thickness, absorber, x_mm, y_mm)


def configuration_estimator(events: Sequence[Event]) -> dict[str, float]:
    count = len(events)
    if count <= 0:
        raise ValueError("cannot estimate an empty event collection")
    generated = sum(event.generated for event in events)
    scintillation = sum(event.scintillation for event in events)
    sipm = sum(event.sipm for event in events)
    return {
        "generated": generated / count,
        "scintillation": scintillation / count,
        "collection": sipm / generated if generated > 0 else math.nan,
        "net": sipm / count,
    }


def load_events(
    campaign_dir: Path,
    task_rows: Sequence[Mapping[str, str]],
    fixture_dir: Path | None,
    audited_roots: Mapping[str, v1.AuditedRoot],
) -> LoadedEvents:
    groups: dict[ConfigurationKey, list[Event]] = defaultdict(list)
    blocks: dict[ConfigurationKey, dict[int, list[Event]]] = defaultdict(dict)
    task_ids: dict[tuple[ConfigurationKey, int], str] = {}
    seen_tasks: set[str] = set()
    for row in task_rows:
        logical_id = row["logical_task_id"]
        if logical_id in seen_tasks:
            raise ValueError(f"duplicate task in task_index.tsv: {logical_id}")
        seen_tasks.add(logical_id)
        frozen = audited_roots.get(logical_id)
        if frozen is None:
            raise ValueError(f"task is absent from frozen event audit: {logical_id}")
        if (
            frozen.path != row["root"]
            or frozen.tile_thickness_mm != int(row["tile_thickness_mm"])
            or frozen.sipm_layout != row["sipm_layout"]
            or frozen.absorber_transverse_mm
            != int(row["absorber_transverse_mm"])
        ):
            raise ValueError(f"task index disagrees with event audit: {logical_id}")
        root_path = resolve_campaign_path(campaign_dir, row["root"])
        if sha256_file(root_path) != frozen.sha256:
            raise ValueError(f"audited ROOT checksum mismatch: {root_path}")
        expected_events = int(row["events"])
        events = (
            v1.read_fixture_events(fixture_dir / f"{logical_id}.csv")
            if fixture_dir is not None
            else v1.read_root_events(root_path, expected_events)
        )
        if len(events) != expected_events:
            raise ValueError(
                f"event count mismatch for {logical_id}: "
                f"{len(events)} != {expected_events}"
            )
        v1.validate_active_sensor_columns(row["sipm_layout"], events, logical_id)
        key = v1.configuration_key(row)
        block = int(row["seed_block"])
        if block in blocks[key]:
            raise ValueError(f"duplicate seed block {block} for {key}")
        blocks[key][block] = list(events)
        groups[key].extend(events)
        task_ids[(key, block)] = logical_id
    if set(audited_roots) != seen_tasks:
        raise ValueError("frozen event audit task set disagrees with task_index.tsv")
    return LoadedEvents(dict(groups), {k: dict(v) for k, v in blocks.items()}, task_ids)


def validate_pilot_shape(loaded: LoadedEvents, *, testing: bool) -> None:
    if any(key[0] != "convergence-pilot" for key in loaded.groups):
        raise ValueError("analysis-v2 accepts only convergence-pilot campaigns")
    if any(set(by_block) != {0, 1, 2, 3} for by_block in loaded.blocks.values()):
        raise ValueError("analysis-v2 requires seed blocks 0,1,2,3 per configuration")
    if testing:
        return
    expected = {
        ("convergence-pilot", thickness, layout, absorber, 0, 0)
        for layout in SIPM_LAYOUTS
        for thickness in (4, 24)
        for absorber in (200, 300, 500)
    } | {
        ("convergence-pilot", thickness, layout, 500, 0, 0)
        for layout in SIPM_LAYOUTS
        for thickness in (8, 12, 16, 20)
    }
    if set(loaded.groups) != expected:
        raise ValueError("accepted analysis-v2 requires the frozen 30-configuration pilot")
    if any(len(events) != 1000 for events in loaded.groups.values()):
        raise ValueError("accepted analysis-v2 requires 1000 events per configuration")
    if any(
        len(events) != 250
        for by_block in loaded.blocks.values()
        for events in by_block.values()
    ):
        raise ValueError("accepted analysis-v2 requires 250 events per seed block")


def bootstrap_configuration(
    events: Sequence[Event], *, resamples: int, seed: int, np: object
) -> dict[str, list[float]]:
    array = np.asarray(
        [[event.generated, event.scintillation, event.sipm] for event in events],
        dtype=float,
    )
    count = len(events)
    rng = np.random.Generator(np.random.PCG64(seed))
    output = {metric: [] for metric in METRICS}
    batch_size = max(1, min(resamples, 1_000_000 // count))
    completed = 0
    while completed < resamples:
        batch = min(batch_size, resamples - completed)
        indices = rng.integers(0, count, size=(batch, count))
        sums = array[indices].sum(axis=1)
        output["generated"].extend(float(value) for value in sums[:, 0] / count)
        output["scintillation"].extend(float(value) for value in sums[:, 1] / count)
        collection = np.divide(
            sums[:, 2],
            sums[:, 0],
            out=np.full(batch, np.nan),
            where=sums[:, 0] > 0,
        )
        output["collection"].extend(float(value) for value in collection)
        output["net"].extend(float(value) for value in sums[:, 2] / count)
        completed += batch
    return output


def build_bootstrap_cache(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    *,
    bootstrap_seed: int,
    resamples: int,
    np: object,
) -> dict[ConfigurationKey, dict[str, list[float]]]:
    cache: dict[ConfigurationKey, dict[str, list[float]]] = {}
    for key, events in groups.items():
        seed = v1.configuration_seed(bootstrap_seed, ("analysis-v2", *key))
        cache[key] = bootstrap_configuration(
            events, resamples=resamples, seed=seed, np=np
        )
    return cache


def interval(values: Sequence[float]) -> tuple[float, float, int]:
    return v1.interval(values)


def relative_shift(value: float, reference: float) -> tuple[float, bool]:
    if not math.isfinite(value) or not math.isfinite(reference) or reference == 0:
        return math.nan, False
    return (value - reference) / abs(reference), True


def write_csv(
    path: Path, fieldnames: Iterable[str], rows: Iterable[Mapping[str, object]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def fraction_interval(successes: int, total: int) -> tuple[float, float, float]:
    low, high = v1.wilson_interval(successes, total)
    return successes / total, low, high


def configuration_rows(
    loaded: LoadedEvents,
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(
        loaded.groups, key=lambda item: (item[3], item[2], item[1], item[4], item[5])
    ):
        events = loaded.groups[key]
        point = configuration_estimator(events)
        count = len(events)
        sensors = sensor_count(key[2])
        area = sensors * SENSOR_AREA_MM2
        row: dict[str, object] = {
            **key_identity(key),
            "seed_blocks": len(loaded.blocks[key]),
            "events": count,
            "sensor_count": sensors,
            "active_area_mm2": area,
            "generated_optical_photons_per_neutron": point["generated"],
            "scintillation_photons_per_neutron": point["scintillation"],
            "collection_sipm_over_generated": point["collection"],
            "observed_net_sipm_photons_per_neutron": point["net"],
            "observed_net_per_installed_sensor": point["net"] / sensors,
            "observed_net_per_active_area_mm2": point["net"] / area,
        }
        for metric in METRICS:
            low, high, valid = interval(cache[key][metric])
            row[f"{metric}_ci95_low"] = low
            row[f"{metric}_ci95_high"] = high
            row[f"{metric}_valid_resamples"] = valid
        counts = {
            "interaction": sum(event.interacted for event in events),
            "generated_zero": sum(event.generated == 0 for event in events),
            "scintillation_zero": sum(
                event.scintillation == 0 for event in events
            ),
            "sipm_zero": sum(event.sipm == 0 for event in events),
        }
        for name, successes in counts.items():
            fraction, low, high = fraction_interval(successes, count)
            row[f"{name}_fraction"] = fraction
            row[f"{name}_wilson95_low"] = low
            row[f"{name}_wilson95_high"] = high
        rows.append(row)
    return rows


def quantile(np: object, values: Sequence[float], probability: float) -> float:
    if not values:
        return math.nan
    return float(np.quantile(np.asarray(values, dtype=float), probability, method="linear"))


def distribution_summary(
    values: Sequence[int], *, np: object
) -> dict[str, object]:
    count = len(values)
    positives = [value for value in values if value > 0]
    total = sum(values)
    mean = total / count
    rms = math.sqrt(sum(value * value for value in values) / count)
    variance = sum((value - mean) ** 2 for value in values) / count
    stddev = math.sqrt(variance)
    positive_mean = sum(positives) / len(positives) if positives else math.nan
    positive_rms = (
        math.sqrt(sum(value * value for value in positives) / len(positives))
        if positives
        else math.nan
    )
    positive_variance = (
        sum((value - positive_mean) ** 2 for value in positives) / len(positives)
        if positives
        else math.nan
    )
    positive_stddev = (
        math.sqrt(positive_variance) if positives else math.nan
    )
    result: dict[str, object] = {
        "events": count,
        "positive_events": len(positives),
        "zero_events": count - len(positives),
        "zero_fraction": (count - len(positives)) / count,
        "sum": total,
        "mean": mean,
        "rms": rms,
        "population_stddev": stddev,
        "standard_error": stddev / math.sqrt(count),
        "max": max(values),
        "positive_mean": positive_mean,
        "positive_rms": positive_rms,
        "positive_population_stddev": positive_stddev,
        "positive_standard_error": (
            positive_stddev / math.sqrt(len(positives)) if positives else math.nan
        ),
        "positive_max": max(positives) if positives else math.nan,
    }
    for label, probability in (
        ("p50", 0.50),
        ("p75", 0.75),
        ("p90", 0.90),
        ("p95", 0.95),
        ("p99", 0.99),
    ):
        result[label] = quantile(np, values, probability)
        result[f"positive_{label}"] = quantile(np, positives, probability)
    ordered = sorted(values, reverse=True)
    for label, fraction in (("1pct", 0.01), ("5pct", 0.05)):
        selected = max(1, math.ceil(fraction * count))
        result[f"top_{label}_event_count"] = selected
        result[f"top_{label}_sum_fraction"] = (
            sum(ordered[:selected]) / total if total > 0 else math.nan
        )
        result[f"top_{label}_fraction_valid"] = total > 0
    return result


def distribution_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]], *, np: object
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (item[3], item[2], item[1])):
        events = groups[key]
        values_by_metric = {
            "generated": [event.generated for event in events],
            "scintillation": [event.scintillation for event in events],
            "sipm": [event.sipm for event in events],
        }
        for metric in DISTRIBUTION_METRICS:
            rows.append(
                {
                    **key_identity(key),
                    "metric": metric,
                    **distribution_summary(values_by_metric[metric], np=np),
                }
            )
    return rows


def pathway_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (item[3], item[2], item[1])):
        events = groups[key]
        count = len(events)
        interaction = [event for event in events if event.interacted]
        generated = [event for event in events if event.generated > 0]
        sipm = [event for event in events if event.sipm > 0]
        generated_interaction = sum(
            event.generated > 0 for event in interaction
        )
        sipm_generated = sum(event.sipm > 0 for event in generated)
        sipm_interaction = sum(event.sipm > 0 for event in interaction)
        generated_given_interaction, generated_given_interaction_valid = safe_divide(
            generated_interaction, len(interaction)
        )
        sipm_given_generated, sipm_given_generated_valid = safe_divide(
            sipm_generated, len(generated)
        )
        sipm_given_interaction, sipm_given_interaction_valid = safe_divide(
            sipm_interaction, len(interaction)
        )
        scint_per_interaction, scint_per_interaction_valid = safe_divide(
            sum(event.scintillation for event in interaction), len(interaction)
        )
        sipm_per_generated, sipm_per_generated_valid = safe_divide(
            sum(event.sipm for event in generated), len(generated)
        )
        sipm_per_positive, sipm_per_positive_valid = safe_divide(
            sum(event.sipm for event in sipm), len(sipm)
        )
        rows.append(
            {
                **key_identity(key),
                "incident_events": count,
                "interaction_events": len(interaction),
                "generated_positive_events": len(generated),
                "sipm_positive_events": len(sipm),
                "interaction_fraction": len(interaction) / count,
                "generated_positive_given_interaction": generated_given_interaction,
                "generated_positive_given_interaction_valid": generated_given_interaction_valid,
                "sipm_positive_given_generated": sipm_given_generated,
                "sipm_positive_given_generated_valid": sipm_given_generated_valid,
                "sipm_positive_given_interaction": sipm_given_interaction,
                "sipm_positive_given_interaction_valid": sipm_given_interaction_valid,
                "scintillation_per_interaction": scint_per_interaction,
                "scintillation_per_interaction_valid": scint_per_interaction_valid,
                "sipm_per_generated_positive_event": sipm_per_generated,
                "sipm_per_generated_positive_event_valid": sipm_per_generated_valid,
                "sipm_per_sipm_positive_event": sipm_per_positive,
                "sipm_per_sipm_positive_event_valid": sipm_per_positive_valid,
            }
        )
    return rows


def group_by_pooled_key(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
) -> dict[PooledKey, dict[str, ConfigurationKey]]:
    output: dict[PooledKey, dict[str, ConfigurationKey]] = defaultdict(dict)
    for key in groups:
        if key[2] in output[pooled_key(key)]:
            raise ValueError(f"duplicate layout for pooled key {pooled_key(key)}")
        output[pooled_key(key)][key[2]] = key
    for key, by_layout in output.items():
        if set(by_layout) != set(SIPM_LAYOUTS):
            raise ValueError(f"pooled production requires all layouts for {key}")
    return dict(output)


def cv(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    if mean == 0:
        return math.nan
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / abs(mean)


def pooled_point(
    by_layout: Mapping[str, ConfigurationKey],
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    metric: str,
) -> float:
    total_events = sum(len(groups[key]) for key in by_layout.values())
    if metric == "generated":
        total = sum(event.generated for key in by_layout.values() for event in groups[key])
    elif metric == "scintillation":
        total = sum(
            event.scintillation for key in by_layout.values() for event in groups[key]
        )
    else:
        raise ValueError(f"unsupported pooled metric: {metric}")
    return total / total_events


def pooled_distribution(
    by_layout: Mapping[str, ConfigurationKey],
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
    metric: str,
) -> list[float]:
    total_events = sum(len(groups[key]) for key in by_layout.values())
    resamples = {len(cache[key][metric]) for key in by_layout.values()}
    if len(resamples) != 1:
        raise ValueError("pooled bootstrap inputs have different lengths")
    count = resamples.pop()
    return [
        sum(
            cache[key][metric][index] * len(groups[key])
            for key in by_layout.values()
        )
        / total_events
        for index in range(count)
    ]


def pooled_rows_and_cache(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> tuple[
    list[dict[str, object]],
    dict[PooledKey, dict[str, object]],
]:
    pooled_groups = group_by_pooled_key(groups)
    rows: list[dict[str, object]] = []
    pooled_cache: dict[PooledKey, dict[str, object]] = {}
    for key in sorted(pooled_groups, key=lambda item: (item[2], item[1])):
        by_layout = pooled_groups[key]
        generated_draws = pooled_distribution(
            by_layout, groups, cache, "generated"
        )
        scintillation_draws = pooled_distribution(
            by_layout, groups, cache, "scintillation"
        )
        generated = pooled_point(by_layout, groups, "generated")
        scintillation = pooled_point(by_layout, groups, "scintillation")
        generated_low, generated_high, generated_valid = interval(generated_draws)
        scint_low, scint_high, scint_valid = interval(scintillation_draws)
        generated_by_layout = {
            layout: configuration_estimator(groups[by_layout[layout]])["generated"]
            for layout in SIPM_LAYOUTS
        }
        scint_by_layout = {
            layout: configuration_estimator(groups[by_layout[layout]])[
                "scintillation"
            ]
            for layout in SIPM_LAYOUTS
        }
        stage, thickness, absorber, x_mm, y_mm = key
        row = {
            "stage": stage,
            "tile_thickness_mm": thickness,
            "absorber_transverse_mm": absorber,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "layouts": ",".join(SIPM_LAYOUTS),
            "layout_count": len(SIPM_LAYOUTS),
            "events": sum(len(groups[value]) for value in by_layout.values()),
            "events_back_center": len(groups[by_layout["back-center"]]),
            "events_edge_center": len(groups[by_layout["edge-center"]]),
            "events_back_four": len(groups[by_layout["back-four"]]),
            "pooled_generated_photons_per_neutron": generated,
            "generated_ci95_low": generated_low,
            "generated_ci95_high": generated_high,
            "generated_valid_resamples": generated_valid,
            "pooled_scintillation_photons_per_neutron": scintillation,
            "scintillation_ci95_low": scint_low,
            "scintillation_ci95_high": scint_high,
            "scintillation_valid_resamples": scint_valid,
            "back_center_generated_per_neutron": generated_by_layout["back-center"],
            "edge_center_generated_per_neutron": generated_by_layout["edge-center"],
            "back_four_generated_per_neutron": generated_by_layout["back-four"],
            "generated_layout_min": min(generated_by_layout.values()),
            "generated_layout_max": max(generated_by_layout.values()),
            "generated_layout_cv": cv(list(generated_by_layout.values())),
            "back_center_scintillation_per_neutron": scint_by_layout["back-center"],
            "edge_center_scintillation_per_neutron": scint_by_layout["edge-center"],
            "back_four_scintillation_per_neutron": scint_by_layout["back-four"],
            "scintillation_layout_min": min(scint_by_layout.values()),
            "scintillation_layout_max": max(scint_by_layout.values()),
            "scintillation_layout_cv": cv(list(scint_by_layout.values())),
        }
        rows.append(row)
        pooled_cache[key] = {
            "by_layout": by_layout,
            "generated_point": generated,
            "scintillation_point": scintillation,
            "generated": generated_draws,
            "scintillation": scintillation_draws,
            "events": row["events"],
        }
    return rows, pooled_cache


def standardized_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[PooledKey, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (item[3], item[2], item[1])):
        point = configuration_estimator(groups[key])
        pooled = pooled_cache[pooled_key(key)]
        standardized = float(pooled["generated_point"]) * point["collection"]
        standardized_draws = [
            generated * collection
            for generated, collection in zip(
                pooled["generated"], cache[key]["collection"]
            )
        ]
        difference_draws = [
            standard - direct
            for standard, direct in zip(standardized_draws, cache[key]["net"])
        ]
        std_low, std_high, std_valid = interval(standardized_draws)
        diff_low, diff_high, diff_valid = interval(difference_draws)
        direct_low, direct_high, _ = interval(cache[key]["net"])
        sensors = sensor_count(key[2])
        area = sensors * SENSOR_AREA_MM2
        rows.append(
            {
                **key_identity(key),
                "direct_observed_events": len(groups[key]),
                "layout_collection_events": len(groups[key]),
                "pooled_production_events_total": int(pooled["events"]),
                "sensor_count": sensors,
                "active_area_mm2": area,
                "pooled_generated_photons_per_neutron": pooled["generated_point"],
                "collection_sipm_over_generated": point["collection"],
                "direct_observed_net": point["net"],
                "direct_net_ci95_low": direct_low,
                "direct_net_ci95_high": direct_high,
                "direct_net_per_installed_sensor": point["net"] / sensors,
                "direct_net_per_active_area_mm2": point["net"] / area,
                "standardized_net": standardized,
                "standardized_net_ci95_low": std_low,
                "standardized_net_ci95_high": std_high,
                "standardized_net_valid_resamples": std_valid,
                "standardized_minus_direct": standardized - point["net"],
                "difference_ci95_low": diff_low,
                "difference_ci95_high": diff_high,
                "difference_valid_resamples": diff_valid,
                "standardized_over_direct": (
                    standardized / point["net"] if point["net"] > 0 else math.nan
                ),
                "standardized_net_per_installed_sensor": standardized / sensors,
                "standardized_net_per_active_area_mm2": standardized / area,
                "interpretation": "model-based-secondary-diagnostic",
            }
        )
    return rows


def ratio_summary(
    numerator_point: float,
    denominator_point: float,
    numerator_draws: Sequence[float],
    denominator_draws: Sequence[float],
) -> dict[str, object]:
    ratio = numerator_point / denominator_point if denominator_point > 0 else math.nan
    draws = v1.ratio_distribution(numerator_draws, denominator_draws)
    low, high, valid = interval(draws)
    complete = valid == len(draws) and valid > 0
    relative_half_width = (
        (high - low) / (2.0 * abs(ratio))
        if complete
        and math.isfinite(ratio)
        and ratio != 0
        and math.isfinite(low)
        and math.isfinite(high)
        else math.nan
    )
    return {
        "ratio": ratio,
        "ci95_low": low,
        "ci95_high": high,
        "valid_resamples": valid,
        "relative_half_width": relative_half_width,
        "ci_excludes_unity": (
            complete
            and math.isfinite(low)
            and math.isfinite(high)
            and (high < 1 or low > 1)
        ),
        "draws": draws,
    }


def primary_contrasts(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[PooledKey, Mapping[str, object]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    stage = next(iter(groups))[0]
    reference_pool_key = (stage, 4, 500, 0, 0)
    compared_pool_key = (stage, 24, 500, 0, 0)
    if reference_pool_key not in pooled_cache or compared_pool_key not in pooled_cache:
        raise ValueError("primary pooled 24/4 production contrast is unavailable")
    reference_pool = pooled_cache[reference_pool_key]
    compared_pool = pooled_cache[compared_pool_key]
    details: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []

    pooled_result = ratio_summary(
        float(compared_pool["scintillation_point"]),
        float(reference_pool["scintillation_point"]),
        compared_pool["scintillation"],
        reference_pool["scintillation"],
    )
    pooled_id = "pooled-scintillation-production-24-over-4"
    pooled_by_layout_reference = reference_pool["by_layout"]
    pooled_by_layout_compared = compared_pool["by_layout"]
    details[pooled_id] = {
        "family": "pooled-production",
        "metric": "scintillation",
        "layout": "pooled",
        "reference_keys": tuple(pooled_by_layout_reference.values()),
        "compared_keys": tuple(pooled_by_layout_compared.values()),
        "full_ratio": pooled_result["ratio"],
        "draws": pooled_result["draws"],
    }
    rows.append(
        {
            "primary_contrast_id": pooled_id,
            "family": "pooled-production",
            "metric": "scintillation",
            "sipm_layout": "pooled",
            "reference_tile_thickness_mm": 4,
            "compared_tile_thickness_mm": 24,
            "absorber_transverse_mm": 500,
            "reference_events_total": reference_pool["events"],
            "compared_events_total": compared_pool["events"],
            "sample_basis": "stratified-pooled-production-events",
            "reference_layout_collection_events": "",
            "compared_layout_collection_events": "",
            "reference_pooled_production_events_total": reference_pool["events"],
            "compared_pooled_production_events_total": compared_pool["events"],
            "reference_events_per_configuration": len(
                groups[next(iter(pooled_by_layout_reference.values()))]
            ),
            "compared_events_per_configuration": len(
                groups[next(iter(pooled_by_layout_compared.values()))]
            ),
            **{key: value for key, value in pooled_result.items() if key != "draws"},
            "max_leave_one_block_out_relative_shift": math.nan,
            "leave_one_block_out_attempts": 0,
            "leave_one_block_out_valid_evaluations": 0,
            "leave_one_block_out_evaluations": 0,
            "block_shift_within_10pct": False,
            "block_shift_within_20pct": False,
            "automatic_acceptance": False,
        }
    )

    for layout in SIPM_LAYOUTS:
        reference_key = (stage, 4, layout, 500, 0, 0)
        compared_key = (stage, 24, layout, 500, 0, 0)
        if reference_key not in groups or compared_key not in groups:
            raise ValueError(f"primary observed-net contrast is unavailable: {layout}")
        reference_point = configuration_estimator(groups[reference_key])["net"]
        compared_point = configuration_estimator(groups[compared_key])["net"]
        result = ratio_summary(
            compared_point,
            reference_point,
            cache[compared_key]["net"],
            cache[reference_key]["net"],
        )
        identifier = f"observed-net-{layout}-24-over-4"
        details[identifier] = {
            "family": "observed-net",
            "metric": "net",
            "layout": layout,
            "reference_keys": (reference_key,),
            "compared_keys": (compared_key,),
            "full_ratio": result["ratio"],
            "draws": result["draws"],
        }
        rows.append(
            {
                "primary_contrast_id": identifier,
                "family": "observed-net",
                "metric": "net",
                "sipm_layout": layout,
                "reference_tile_thickness_mm": 4,
                "compared_tile_thickness_mm": 24,
                "absorber_transverse_mm": 500,
                "reference_events_total": len(groups[reference_key]),
                "compared_events_total": len(groups[compared_key]),
                "sample_basis": "direct-observed-configuration-events",
                "reference_layout_collection_events": len(groups[reference_key]),
                "compared_layout_collection_events": len(groups[compared_key]),
                "reference_pooled_production_events_total": "",
                "compared_pooled_production_events_total": "",
                "reference_events_per_configuration": len(groups[reference_key]),
                "compared_events_per_configuration": len(groups[compared_key]),
                **{key: value for key, value in result.items() if key != "draws"},
                "max_leave_one_block_out_relative_shift": math.nan,
                "leave_one_block_out_attempts": 0,
                "leave_one_block_out_valid_evaluations": 0,
                "leave_one_block_out_evaluations": 0,
                "block_shift_within_10pct": False,
                "block_shift_within_20pct": False,
                "automatic_acceptance": False,
            }
        )
    if len(rows) != 4:
        raise ValueError("analysis-v2 must define exactly four primary contrasts")
    return rows, details


def secondary_contrast_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[PooledKey, Mapping[str, object]],
    standardized: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    stage = next(iter(groups))[0]
    rows: list[dict[str, object]] = []
    available_thicknesses = sorted(
        {
            key[1]
            for key in groups
            if key[3:] == (500, 0, 0)
        }
    )
    compared_thicknesses = [
        thickness for thickness in available_thicknesses if thickness != 4
    ]
    if 4 not in available_thicknesses or 24 not in available_thicknesses:
        raise ValueError(
            "secondary contrasts require 4 mm and 24 mm endpoints at 500 mm"
        )
    standardized_by_key = {
        (
            int(row["tile_thickness_mm"]),
            str(row["sipm_layout"]),
            int(row["absorber_transverse_mm"]),
        ): row
        for row in standardized
    }

    pairs_by_family = {
        "thickness-over-4": [(4, thickness) for thickness in compared_thicknesses],
        "adjacent-thickness": list(
            zip(available_thicknesses[:-1], available_thicknesses[1:])
        ),
    }
    for family, pairs in pairs_by_family.items():
        for reference_thickness, compared_thickness in pairs:
            reference_pool = pooled_cache[
                (stage, reference_thickness, 500, 0, 0)
            ]
            compared_pool = pooled_cache[
                (stage, compared_thickness, 500, 0, 0)
            ]
            for metric in ("generated", "scintillation"):
                result = ratio_summary(
                    float(compared_pool[f"{metric}_point"]),
                    float(reference_pool[f"{metric}_point"]),
                    compared_pool[metric],
                    reference_pool[metric],
                )
                rows.append(
                    {
                        "contrast_family": f"pooled-production-{family}",
                        "metric": metric,
                        "sipm_layout": "pooled",
                        "reference_tile_thickness_mm": reference_thickness,
                        "compared_tile_thickness_mm": compared_thickness,
                        "absorber_transverse_mm": 500,
                        "reference_events_total": reference_pool["events"],
                        "compared_events_total": compared_pool["events"],
                        "sample_basis": "stratified-pooled-production-events",
                        "reference_layout_collection_events": "",
                        "compared_layout_collection_events": "",
                        "reference_pooled_production_events_total": reference_pool[
                            "events"
                        ],
                        "compared_pooled_production_events_total": compared_pool[
                            "events"
                        ],
                        **{
                            key: value
                            for key, value in result.items()
                            if key != "draws"
                        },
                        "controls_production_sizing": False,
                    }
                )

            for layout in SIPM_LAYOUTS:
                reference_key = (
                    stage,
                    reference_thickness,
                    layout,
                    500,
                    0,
                    0,
                )
                compared_key = (
                    stage,
                    compared_thickness,
                    layout,
                    500,
                    0,
                    0,
                )
                reference_point = configuration_estimator(groups[reference_key])
                compared_point = configuration_estimator(groups[compared_key])
                for metric in ("scintillation", "collection", "net"):
                    result = ratio_summary(
                        compared_point[metric],
                        reference_point[metric],
                        cache[compared_key][metric],
                        cache[reference_key][metric],
                    )
                    rows.append(
                        {
                            "contrast_family": f"observed-{family}",
                            "metric": metric,
                            "sipm_layout": layout,
                            "reference_tile_thickness_mm": reference_thickness,
                            "compared_tile_thickness_mm": compared_thickness,
                            "absorber_transverse_mm": 500,
                            "reference_events_total": len(groups[reference_key]),
                            "compared_events_total": len(groups[compared_key]),
                            "sample_basis": "single-layout-direct-events",
                            "reference_layout_collection_events": len(
                                groups[reference_key]
                            ),
                            "compared_layout_collection_events": len(
                                groups[compared_key]
                            ),
                            "reference_pooled_production_events_total": "",
                            "compared_pooled_production_events_total": "",
                            **{
                                key: value
                                for key, value in result.items()
                                if key != "draws"
                            },
                            "controls_production_sizing": False,
                        }
                    )

                reference_standardized = standardized_by_key[
                    (reference_thickness, layout, 500)
                ]
                compared_standardized = standardized_by_key[
                    (compared_thickness, layout, 500)
                ]
                reference_draws = [
                    generated * collection
                    for generated, collection in zip(
                        reference_pool["generated"],
                        cache[reference_key]["collection"],
                    )
                ]
                compared_draws = [
                    generated * collection
                    for generated, collection in zip(
                        compared_pool["generated"],
                        cache[compared_key]["collection"],
                    )
                ]
                result = ratio_summary(
                    float(compared_standardized["standardized_net"]),
                    float(reference_standardized["standardized_net"]),
                    compared_draws,
                    reference_draws,
                )
                rows.append(
                    {
                        "contrast_family": f"standardized-{family}",
                        "metric": "standardized-net",
                        "sipm_layout": layout,
                        "reference_tile_thickness_mm": reference_thickness,
                        "compared_tile_thickness_mm": compared_thickness,
                        "absorber_transverse_mm": 500,
                        "reference_events_total": "",
                        "compared_events_total": "",
                        "sample_basis": (
                            "pooled-production-plus-layout-collection-events"
                        ),
                        "reference_layout_collection_events": len(
                            groups[reference_key]
                        ),
                        "compared_layout_collection_events": len(
                            groups[compared_key]
                        ),
                        "reference_pooled_production_events_total": reference_pool[
                            "events"
                        ],
                        "compared_pooled_production_events_total": compared_pool[
                            "events"
                        ],
                        **{
                            key: value
                            for key, value in result.items()
                            if key != "draws"
                        },
                        "controls_production_sizing": False,
                    }
                )
    return rows


def block_metric_row(
    *,
    key: ConfigurationKey,
    block: int,
    variant: str,
    events: Sequence[Event],
    full_events: Sequence[Event],
) -> dict[str, object]:
    point = configuration_estimator(events)
    full = configuration_estimator(full_events)
    zero_points = {
        "generated_zero_fraction": sum(event.generated == 0 for event in events)
        / len(events),
        "scintillation_zero_fraction": sum(
            event.scintillation == 0 for event in events
        )
        / len(events),
        "sipm_zero_fraction": sum(event.sipm == 0 for event in events)
        / len(events),
    }
    zero_full = {
        "generated_zero_fraction": sum(
            event.generated == 0 for event in full_events
        )
        / len(full_events),
        "scintillation_zero_fraction": sum(
            event.scintillation == 0 for event in full_events
        )
        / len(full_events),
        "sipm_zero_fraction": sum(event.sipm == 0 for event in full_events)
        / len(full_events),
    }
    row: dict[str, object] = {
        "scope": "configuration",
        "estimate_variant": variant,
        "primary_contrast_id": "",
        **key_identity(key),
        "seed_block": block,
        "omitted_arm": "",
        "events": len(events),
        "full_events": len(full_events),
        "contrast_ratio": math.nan,
        "full_contrast_ratio": math.nan,
        "contrast_relative_shift": math.nan,
        "contrast_absolute_relative_shift": math.nan,
        "contrast_shift_valid": False,
        "contrast_within_10pct": False,
        "contrast_within_20pct": False,
    }
    for metric in METRICS:
        shift, valid = relative_shift(point[metric], full[metric])
        row[metric] = point[metric]
        row[f"full_{metric}"] = full[metric]
        row[f"{metric}_relative_shift"] = shift
        row[f"{metric}_absolute_relative_shift"] = abs(shift) if valid else math.nan
        row[f"{metric}_shift_valid"] = valid
        row[f"{metric}_within_10pct"] = valid and abs(shift) <= 0.10
        row[f"{metric}_within_20pct"] = valid and abs(shift) <= 0.20
    for metric in ZERO_FRACTION_METRICS:
        shift, valid = relative_shift(zero_points[metric], zero_full[metric])
        row[metric] = zero_points[metric]
        row[f"full_{metric}"] = zero_full[metric]
        row[f"{metric}_relative_shift"] = shift
        row[f"{metric}_absolute_relative_shift"] = (
            abs(shift) if valid else math.nan
        )
        row[f"{metric}_shift_valid"] = valid
        row[f"{metric}_within_10pct"] = valid and abs(shift) <= 0.10
        row[f"{metric}_within_20pct"] = valid and abs(shift) <= 0.20
    return row


def pooled_point_with_override(
    keys: Sequence[ConfigurationKey],
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    override_key: ConfigurationKey,
    override_events: Sequence[Event],
    *,
    full_weights: Mapping[ConfigurationKey, float],
) -> float:
    # Preserve the accepted full-design layout weights in leave-one-block-out
    # diagnostics so block influence is not mixed with a changed layout mixture.
    total = 0.0
    for key in keys:
        events = override_events if key == override_key else groups[key]
        total += full_weights[key] * configuration_estimator(events)["scintillation"]
    return total


def contrast_point_with_omission(
    detail: Mapping[str, object],
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    override_key: ConfigurationKey,
    override_events: Sequence[Event],
) -> float:
    reference_keys = detail["reference_keys"]
    compared_keys = detail["compared_keys"]
    if detail["family"] == "pooled-production":
        all_keys = (*reference_keys, *compared_keys)
        weights_by_arm: dict[ConfigurationKey, float] = {}
        for keys in (reference_keys, compared_keys):
            total = sum(len(groups[key]) for key in keys)
            for key in keys:
                weights_by_arm[key] = len(groups[key]) / total
        reference = pooled_point_with_override(
            reference_keys,
            groups,
            override_key,
            override_events,
            full_weights=weights_by_arm,
        )
        compared = pooled_point_with_override(
            compared_keys,
            groups,
            override_key,
            override_events,
            full_weights=weights_by_arm,
        )
        if override_key not in all_keys:
            raise ValueError("pooled block omission is outside the primary contrast")
    else:
        reference_key = reference_keys[0]
        compared_key = compared_keys[0]
        reference_events = override_events if override_key == reference_key else groups[reference_key]
        compared_events = override_events if override_key == compared_key else groups[compared_key]
        reference = configuration_estimator(reference_events)["net"]
        compared = configuration_estimator(compared_events)["net"]
    return compared / reference if reference > 0 else math.nan


def block_stability_rows(
    loaded: LoadedEvents,
    primary_rows: list[dict[str, object]],
    primary_details: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(loaded.groups, key=lambda item: (item[3], item[2], item[1])):
        full_events = loaded.groups[key]
        for block in sorted(loaded.blocks[key]):
            single = loaded.blocks[key][block]
            remaining = [
                event
                for other, events in loaded.blocks[key].items()
                if other != block
                for event in events
            ]
            rows.append(
                block_metric_row(
                    key=key,
                    block=block,
                    variant="single-block",
                    events=single,
                    full_events=full_events,
                )
            )
            rows.append(
                block_metric_row(
                    key=key,
                    block=block,
                    variant="leave-one-block-out",
                    events=remaining,
                    full_events=full_events,
                )
            )

    primary_shifts: dict[str, list[float]] = defaultdict(list)
    primary_attempts: dict[str, int] = defaultdict(int)
    for identifier, detail in primary_details.items():
        full_ratio = float(detail["full_ratio"])
        for arm_name, keys in (
            ("reference", detail["reference_keys"]),
            ("compared", detail["compared_keys"]),
        ):
            for key in keys:
                for block in sorted(loaded.blocks[key]):
                    primary_attempts[identifier] += 1
                    remaining = [
                        event
                        for other, events in loaded.blocks[key].items()
                        if other != block
                        for event in events
                    ]
                    ratio = contrast_point_with_omission(
                        detail, loaded.groups, key, remaining
                    )
                    shift, valid = relative_shift(ratio, full_ratio)
                    if valid:
                        primary_shifts[identifier].append(abs(shift))
                    rows.append(
                        {
                            "scope": "primary-contrast",
                            "estimate_variant": "leave-one-block-out",
                            "primary_contrast_id": identifier,
                            **key_identity(key),
                            "seed_block": block,
                            "omitted_arm": arm_name,
                            "events": len(remaining),
                            "full_events": len(loaded.groups[key]),
                            **{
                                metric: math.nan
                                for metric in (
                                    "generated",
                                    "full_generated",
                                    "generated_relative_shift",
                                    "generated_absolute_relative_shift",
                                    "scintillation",
                                    "full_scintillation",
                                    "scintillation_relative_shift",
                                    "scintillation_absolute_relative_shift",
                                    "collection",
                                    "full_collection",
                                    "collection_relative_shift",
                                    "collection_absolute_relative_shift",
                                    "net",
                                    "full_net",
                                    "net_relative_shift",
                                    "net_absolute_relative_shift",
                                )
                            },
                            **{
                                field: math.nan
                                for metric in ZERO_FRACTION_METRICS
                                for field in (
                                    metric,
                                    f"full_{metric}",
                                    f"{metric}_relative_shift",
                                    f"{metric}_absolute_relative_shift",
                                )
                            },
                            **{
                                field: False
                                for metric in (*METRICS, *ZERO_FRACTION_METRICS)
                                for field in (
                                    f"{metric}_shift_valid",
                                    f"{metric}_within_10pct",
                                    f"{metric}_within_20pct",
                                )
                            },
                            "contrast_ratio": ratio,
                            "full_contrast_ratio": full_ratio,
                            "contrast_relative_shift": shift,
                            "contrast_absolute_relative_shift": abs(shift)
                            if valid
                            else math.nan,
                            "contrast_shift_valid": valid,
                            "contrast_within_10pct": valid and abs(shift) <= 0.10,
                            "contrast_within_20pct": valid and abs(shift) <= 0.20,
                        }
                    )

    for row in primary_rows:
        identifier = str(row["primary_contrast_id"])
        shifts = primary_shifts[identifier]
        attempts = primary_attempts[identifier]
        expected_attempts = 24 if row["family"] == "pooled-production" else 8
        if attempts != expected_attempts:
            raise ValueError(
                f"primary contrast {identifier} has {attempts} block omissions; "
                f"expected {expected_attempts}"
            )
        complete = len(shifts) == attempts
        maximum = max(shifts) if shifts else math.nan
        row["max_leave_one_block_out_relative_shift"] = maximum
        row["leave_one_block_out_attempts"] = attempts
        row["leave_one_block_out_valid_evaluations"] = len(shifts)
        row["leave_one_block_out_evaluations"] = len(shifts)
        row["block_shift_within_10pct"] = complete and maximum <= 0.10
        row["block_shift_within_20pct"] = complete and maximum <= 0.20
    return rows


def equivalence_status(
    *,
    low: float,
    high: float,
    valid: int,
    expected_resamples: int,
    half_width: float,
) -> tuple[bool, str]:
    if (
        valid != expected_resamples
        or valid <= 0
        or not math.isfinite(low)
        or not math.isfinite(high)
    ):
        return False, "not_evaluable"
    equivalent = low >= 1.0 - half_width and high <= 1.0 + half_width
    difference = high < 1.0 or low > 1.0
    if equivalent:
        return True, "equivalent"
    if difference:
        return False, "difference_detected_not_equivalent"
    return False, "inconclusive"


def absorber_row(
    *,
    scope: str,
    metric: str,
    layout: str,
    thickness: int,
    candidate: int,
    candidate_events: int,
    reference_events: int,
    result: Mapping[str, object],
) -> dict[str, object]:
    low = float(result["ci95_low"])
    high = float(result["ci95_high"])
    valid = int(result["valid_resamples"])
    expected_resamples = len(result["draws"])
    equivalent_5, status_5 = equivalence_status(
        low=low,
        high=high,
        valid=valid,
        expected_resamples=expected_resamples,
        half_width=0.05,
    )
    equivalent_10, status_10 = equivalence_status(
        low=low,
        high=high,
        valid=valid,
        expected_resamples=expected_resamples,
        half_width=0.10,
    )
    return {
        "scope": scope,
        "metric": metric,
        "sipm_layout": layout,
        "tile_thickness_mm": thickness,
        "candidate_absorber_transverse_mm": candidate,
        "reference_absorber_transverse_mm": 500,
        "candidate_events_total": candidate_events,
        "reference_events_total": reference_events,
        "ratio": result["ratio"],
        "ci95_low": low,
        "ci95_high": high,
        "valid_resamples": valid,
        "difference_detected": (
            valid == expected_resamples
            and valid > 0
            and math.isfinite(low)
            and math.isfinite(high)
            and (high < 1.0 or low > 1.0)
        ),
        "equivalent_within_5pct": equivalent_5,
        "equivalent_within_10pct": equivalent_10,
        "review_status_5pct": status_5,
        "review_status_10pct": status_10,
        "equivalence_rule": "95pct-bootstrap-ci-containment;conservative-review-not-TOST",
        "automatic_acceptance": False,
    }


def absorber_equivalence_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
    pooled_cache: Mapping[PooledKey, Mapping[str, object]],
) -> list[dict[str, object]]:
    stage = next(iter(groups))[0]
    rows: list[dict[str, object]] = []
    for thickness in (4, 24):
        reference_pool = pooled_cache[(stage, thickness, 500, 0, 0)]
        for candidate in (200, 300):
            candidate_pool = pooled_cache[(stage, thickness, candidate, 0, 0)]
            for metric in ("generated", "scintillation"):
                result = ratio_summary(
                    float(candidate_pool[f"{metric}_point"]),
                    float(reference_pool[f"{metric}_point"]),
                    candidate_pool[metric],
                    reference_pool[metric],
                )
                rows.append(
                    absorber_row(
                        scope="pooled-production",
                        metric=metric,
                        layout="pooled",
                        thickness=thickness,
                        candidate=candidate,
                        candidate_events=int(candidate_pool["events"]),
                        reference_events=int(reference_pool["events"]),
                        result=result,
                    )
                )
            for layout in SIPM_LAYOUTS:
                reference_key = (stage, thickness, layout, 500, 0, 0)
                candidate_key = (stage, thickness, layout, candidate, 0, 0)
                reference_point = configuration_estimator(groups[reference_key])["net"]
                candidate_point = configuration_estimator(groups[candidate_key])["net"]
                result = ratio_summary(
                    candidate_point,
                    reference_point,
                    cache[candidate_key]["net"],
                    cache[reference_key]["net"],
                )
                rows.append(
                    absorber_row(
                        scope="per-layout-observed-net",
                        metric="net",
                        layout=layout,
                        thickness=thickness,
                        candidate=candidate,
                        candidate_events=len(groups[candidate_key]),
                        reference_events=len(groups[reference_key]),
                        result=result,
                    )
                )
    return rows


def tolerant_ceil(value: float) -> int:
    return math.ceil(value - 1e-12 * max(1.0, abs(value)))


def sizing_report(
    primary_rows: Sequence[Mapping[str, object]],
    *,
    block_events: int,
    expected_resamples: int,
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for row in primary_rows:
        point = float(row["ratio"])
        low = float(row["ci95_low"])
        high = float(row["ci95_high"])
        valid = int(row["valid_resamples"])
        current_total = int(row["reference_events_total"])
        if (
            valid != expected_resamples
            or not all(math.isfinite(value) for value in (point, low, high))
            or point == 0
        ):
            for target in PRECISION_TARGETS:
                entries.append(
                    {
                        "primary_contrast_id": row["primary_contrast_id"],
                        "target_relative_half_width": target,
                        "status": "not_evaluable",
                    }
                )
            continue
        half_width = (high - low) / (2.0 * abs(point))
        pooled = row["family"] == "pooled-production"
        layout_count = 3 if pooled else 1
        for target in PRECISION_TARGETS:
            projected = max(
                current_total,
                tolerant_ceil(current_total * (half_width / target) ** 2),
            )
            buffered = tolerant_ceil(projected * PROJECTION_SAFETY_FACTOR)
            per_configuration = max(
                4 * block_events,
                block_events
                * math.ceil(buffered / (layout_count * block_events)),
            )
            per_endpoint_total = layout_count * per_configuration
            entries.append(
                {
                    "primary_contrast_id": row["primary_contrast_id"],
                    "family": row["family"],
                    "metric": row["metric"],
                    "sipm_layout": row["sipm_layout"],
                    "target_relative_half_width": target,
                    "status": "projection_requires_review",
                    "observed_ratio": point,
                    "observed_ci95_low": low,
                    "observed_ci95_high": high,
                    "observed_relative_half_width": half_width,
                    "current_events_per_endpoint_total": current_total,
                    "layout_configurations_per_endpoint": layout_count,
                    "projected_events_before_safety_factor_per_endpoint_total": projected,
                    "events_after_safety_factor_per_endpoint_total": buffered,
                    "recommended_events_per_configuration": per_configuration,
                    "recommended_blocks_per_configuration": per_configuration
                    // block_events,
                    "recommended_events_per_endpoint_total": per_endpoint_total,
                    "recommended_events_for_both_endpoints": 2
                    * per_endpoint_total,
                    "events_per_execution_block": block_events,
                    "automatic_acceptance": False,
                }
            )
    if len(entries) != 8:
        raise ValueError("analysis-v2 sizing must contain exactly eight projections")
    return {
        "schema_version": "steel-module-production-sizing-v2",
        "method": "bootstrap_ci_width_sqrt_n_projection",
        "precision_targets": list(PRECISION_TARGETS),
        "safety_factor": PROJECTION_SAFETY_FACTOR,
        "events_per_execution_block": block_events,
        "minimum_blocks_per_configuration": 4,
        "automatic_acceptance": False,
        "global_recommendation": None,
        "scaling_assumption": "95% CI relative half-width scales as 1/sqrt(N)",
        "heavy_tail_caveat": (
            "Zero inflation and rare showers can violate the finite-sample scaling; "
            "review distribution and seed-block diagnostics before selecting N."
        ),
        "entries": entries,
    }


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-9)


def require_complete_intervals(
    rows: Sequence[Mapping[str, object]],
    valid_fields: Sequence[str],
    *,
    expected_resamples: int,
    label: str,
) -> None:
    for row_number, row in enumerate(rows, 1):
        for field in valid_fields:
            if int(row[field]) != expected_resamples:
                raise ValueError(
                    f"accepted analysis-v2 requires complete {label} intervals: "
                    f"row {row_number} {field}={row[field]} != {expected_resamples}"
                )


def reconcile_v1(
    finalized_dir: Path,
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    *,
    campaign_id: str,
    plan_hash: str,
    testing: bool,
    bootstrap_seed: int,
    bootstrap_resamples: int,
    confidence_level: float,
) -> dict[str, object]:
    analysis_dir = finalized_dir / "analysis"
    if not analysis_dir.is_dir():
        return {
            "status": "not_available",
            "directory": str(analysis_dir),
            "configuration_rows_compared": 0,
            "endpoint_ratio_rows_compared": 0,
        }
    verify_checksum_manifest(
        analysis_dir,
        required=(
            "configuration_intervals.csv",
            "thickness_ratios.csv",
            "analysis_config.json",
        ),
    )
    v1_config = v1.load_json(analysis_dir / "analysis_config.json")
    expected_event_source = "fixture_csv" if testing else "audited_root_scan_tree"
    expected_accepted = not testing
    if (
        v1_config.get("schema_version") != "steel-module-analysis-config-v1"
        or v1_config.get("campaign_id") != campaign_id
        or v1_config.get("plan_hash") != plan_hash
        or v1_config.get("event_source") != expected_event_source
        or v1_config.get("accepted_statistical_evidence") is not expected_accepted
    ):
        raise ValueError("v1 reconciliation analysis identity mismatch")
    v1_bootstrap = v1_config.get("bootstrap")
    if not isinstance(v1_bootstrap, dict) or (
        v1_bootstrap.get("status") != "complete"
        or int(v1_bootstrap.get("seed", -1)) != bootstrap_seed
        or int(v1_bootstrap.get("resamples", -1)) != bootstrap_resamples
        or float(v1_bootstrap.get("confidence_level", math.nan))
        != confidence_level
        or v1_bootstrap.get("resampling_unit") != "event within configuration"
    ):
        raise ValueError("v1 reconciliation bootstrap identity mismatch")
    v1_analyzer = v1_config.get("analyzer")
    if not isinstance(v1_analyzer, dict) or (
        v1_analyzer.get("implementation")
        != "hpc/osc/analyze_steel_module_campaign.py"
        or v1_analyzer.get("sha256") != sha256_file(Path(v1.__file__).resolve())
    ):
        raise ValueError("v1 reconciliation analyzer identity mismatch")
    config_rows = v1.read_csv(analysis_dir / "configuration_intervals.csv")
    expected = {v1.configuration_key(row): row for row in config_rows}
    if len(expected) != len(config_rows) or set(expected) != set(groups):
        raise ValueError("v1 reconciliation configuration set mismatch")
    for key, events in groups.items():
        point = configuration_estimator(events)
        row = expected[key]
        checks = {
            "production_scint_photons_per_neutron": point["scintillation"],
            "collection_sipm_over_generated": point["collection"],
            "net_sipm_photons_per_neutron": point["net"],
        }
        for field, value in checks.items():
            if not close(float(row[field]), value):
                raise ValueError(f"v1 reconciliation failed for {field}: {key}")
    thickness_rows = v1.read_csv(analysis_dir / "thickness_ratios.csv")
    endpoint_rows = [
        row
        for row in thickness_rows
        if int(row["absorber_transverse_mm"]) == 500
        and int(row["reference_tile_thickness_mm"]) == 4
        and int(row["compared_tile_thickness_mm"]) == 24
    ]
    if (
        len(endpoint_rows) != len(SIPM_LAYOUTS)
        or {row["sipm_layout"] for row in endpoint_rows} != set(SIPM_LAYOUTS)
    ):
        raise ValueError("v1 reconciliation lacks the three endpoint ratios")
    stage = next(iter(groups))[0]
    for row in endpoint_rows:
        layout = row["sipm_layout"]
        reference = configuration_estimator(groups[(stage, 4, layout, 500, 0, 0)])
        compared = configuration_estimator(groups[(stage, 24, layout, 500, 0, 0)])
        for metric in ("production", "collection", "net"):
            local_metric = "scintillation" if metric == "production" else metric
            ratio = compared[local_metric] / reference[local_metric]
            if not close(float(row[f"{metric}_ratio"]), ratio):
                raise ValueError(
                    f"v1 endpoint ratio reconciliation failed: {layout} {metric}"
                )
    return {
        "status": "passed",
        "directory": str(analysis_dir),
        "checksum_manifest_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
        "configuration_rows_compared": len(config_rows),
        "endpoint_ratio_rows_compared": len(endpoint_rows),
    }


def format_number(value: object, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "N/A"
    return f"{number:.{digits}g}"


def build_summary(
    *,
    accepted: bool,
    primary_rows: Sequence[Mapping[str, object]],
    distribution: Sequence[Mapping[str, object]],
    absorber: Sequence[Mapping[str, object]],
    sizing: Mapping[str, object],
) -> str:
    sipm_tail = [
        row
        for row in distribution
        if row["metric"] == "sipm" and int(row["absorber_transverse_mm"]) == 500
    ]
    def finite_max(field: str) -> float:
        return max(
            (
                float(row[field])
                for row in sipm_tail
                if math.isfinite(float(row[field]))
            ),
            default=math.nan,
        )

    maximum_zero = finite_max("zero_fraction")
    maximum_tail_1 = finite_max("top_1pct_sum_fraction")
    maximum_tail_5 = finite_max("top_5pct_sum_fraction")
    maximum_block = max(
        (
            float(row["max_leave_one_block_out_relative_shift"])
            for row in primary_rows
            if math.isfinite(float(row["max_leave_one_block_out_relative_shift"]))
        ),
        default=math.nan,
    )
    lines = [
        "# Steel Module Analysis-v2 Summary",
        "",
        f"Accepted statistical evidence: `{str(accepted).lower()}`",
        "",
        "## Four primary 24/4 contrasts",
        "",
        "| Contrast | Ratio | Bootstrap 95% interval | Max LOO shift |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in primary_rows:
        lines.append(
            "| "
            + str(row["primary_contrast_id"])
            + " | "
            + format_number(row["ratio"])
            + " | ["
            + format_number(row["ci95_low"])
            + ", "
            + format_number(row["ci95_high"])
            + "] | "
            + format_number(row["max_leave_one_block_out_relative_shift"])
            + " |"
        )
    lines.extend(
        [
            "",
            "## Zero/heavy-tail and block diagnostics",
            "",
            f"- Largest 500 mm SiPM zero-response fraction: `{format_number(maximum_zero)}`.",
            f"- Largest 500 mm SiPM top-1% share: `{format_number(maximum_tail_1)}`.",
            f"- Largest 500 mm SiPM top-5% share: `{format_number(maximum_tail_5)}`.",
            f"- Largest primary-contrast leave-one-block-out shift: `{format_number(maximum_block)}`.",
            "- The 10% and 20% block flags are review diagnostics only; they do not auto-fail the analysis.",
            "",
            "## Absorber review",
            "",
            "| Scope | Metric/layout | Thickness | Candidate/500 | 95% interval | 5% | 10% |",
            "| --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in absorber:
        label = (
            str(row["metric"])
            if row["sipm_layout"] == "pooled"
            else f"{row['metric']} / {row['sipm_layout']}"
        )
        lines.append(
            f"| {row['scope']} | {label} | {row['tile_thickness_mm']} | "
            f"{row['candidate_absorber_transverse_mm']}/500 = {format_number(row['ratio'])} | "
            f"[{format_number(row['ci95_low'])}, {format_number(row['ci95_high'])}] | "
            f"{row['review_status_5pct']} | {row['review_status_10pct']} |"
        )
    lines.extend(
        [
            "",
            "## Contrast-specific sizing projections",
            "",
            "| Contrast | Target | Events/config | Blocks/config | Both-endpoint events |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for entry in sizing["entries"]:
        if entry["status"] != "projection_requires_review":
            lines.append(
                f"| {entry['primary_contrast_id']} | "
                f"{100 * float(entry['target_relative_half_width']):g}% | N/A | N/A | N/A |"
            )
            continue
        lines.append(
            f"| {entry['primary_contrast_id']} | "
            f"{100 * float(entry['target_relative_half_width']):g}% | "
            f"{entry['recommended_events_per_configuration']} | "
            f"{entry['recommended_blocks_per_configuration']} | "
            f"{entry['recommended_events_for_both_endpoints']} |"
        )
    lines.extend(
        [
            "",
            "## Decision boundary",
            "",
            "Supported: thicker-tile production and per-layout net endpoint contrasts can be reviewed with pooled and direct estimates kept separate.",
            "",
            "Not automatically supported: absorber convergence, a universal production event count, a sensor-area-independent layout ranking, or production authorization.",
            "",
            "All intervals are per-contrast event-bootstrap intervals; they do not provide familywise multiple-comparison coverage or detector-model systematic uncertainty.",
            "",
            "Next checkpoint: human review of tail concentration, block sensitivity, absorber equivalence flags, and the eight separate sizing projections.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.event_fixture_dir is not None and not args.testing_allow_event_fixtures:
        print(
            "--event-fixture-dir requires --testing-allow-event-fixtures",
            file=sys.stderr,
        )
        return 2
    if args.testing_allow_event_fixtures and args.event_fixture_dir is None:
        print(
            "--testing-allow-event-fixtures requires --event-fixture-dir",
            file=sys.stderr,
        )
        return 2
    if args.testing_allow_dirty_analysis and args.event_fixture_dir is None:
        print(
            "--testing-allow-dirty-analysis is test-fixture-only",
            file=sys.stderr,
        )
        return 2

    campaign_dir = args.campaign_dir.expanduser().resolve()
    finalized_dir = (
        args.finalized_dir.expanduser().resolve()
        if args.finalized_dir is not None
        else campaign_dir / "finalized"
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else finalized_dir / "analysis-v2"
    )
    fixture_dir = (
        args.event_fixture_dir.expanduser().resolve()
        if args.event_fixture_dir is not None
        else None
    )
    testing = fixture_dir is not None

    try:
        # Refuse an existing destination before any ROOT I/O or bootstrap work.
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite analysis-v2 directory: {output_dir}")
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("analysis-v2 requires NumPy") from exc
        try:
            import uproot  # type: ignore[import-not-found]
        except ImportError as exc:
            if not testing:
                raise ValueError("accepted analysis-v2 requires uproot") from exc
            uproot_version: str | None = None
        else:
            uproot_version = str(uproot.__version__)

        repo_root = Path(__file__).resolve().parents[2]
        analyzer_path = Path(__file__).resolve()
        v1_path = Path(v1.__file__).resolve()
        analyzer_hash = sha256_file(analyzer_path)
        v1_hash = sha256_file(v1_path)
        if not testing and is_within(output_dir, repo_root):
            raise ValueError(
                "accepted analysis-v2 output must be outside the analysis checkout"
            )
        if is_within(output_dir, finalized_dir / "analysis"):
            raise ValueError(
                "analysis-v2 output must not be inside the checksum-bound v1 analysis"
            )
        analysis_commit = run_git(repo_root, "rev-parse", "HEAD")
        branch_text = run_git(repo_root, "branch", "--show-current")
        dirty_text = run_git(
            repo_root, "status", "--porcelain", "--untracked-files=all"
        )
        dirty = bool(dirty_text)
        if dirty and not (testing and args.testing_allow_dirty_analysis):
            raise ValueError(
                "accepted analysis-v2 requires a clean analysis checkout; "
                "dirty checkout is allowed only for explicit fixture testing"
            )

        bundle, validation, finalized_analysis_config, audited_roots = v1.validate_identity(
            campaign_dir, finalized_dir
        )
        if bundle.manifest.get("stage") != "convergence-pilot":
            raise ValueError("analysis-v2 accepts only convergence-pilot campaigns")
        if not testing and validation.get("accepted_statistical_evidence") is not True:
            raise ValueError("real analysis-v2 requires accepted statistical evidence")
        bootstrap = finalized_analysis_config.get("bootstrap")
        if not isinstance(bootstrap, dict):
            raise ValueError("finalized analysis config has no bootstrap definition")
        bootstrap_seed = int(bootstrap["seed"])
        resamples = int(bootstrap["resamples"])
        confidence_level = float(bootstrap.get("confidence_level", math.nan))
        bootstrap_definition_valid = (
            bootstrap_seed >= 0
            and resamples > 0
            and confidence_level == PINNED_CONFIDENCE_LEVEL
            and bootstrap.get("resampling_unit") == "event within configuration"
        )
        if not bootstrap_definition_valid:
            raise ValueError("invalid finalized event-bootstrap definition")
        if not testing and (
            bootstrap_seed != PINNED_BOOTSTRAP_SEED
            or resamples != PINNED_BOOTSTRAP_RESAMPLES
        ):
            raise ValueError(
                "analysis-v2 requires the pinned 10,000-resample, seed-20260715, "
                "95% event-bootstrap definition"
            )

        input_hashes = capture_input_hashes(campaign_dir, finalized_dir)
        task_rows = v1.read_tsv(finalized_dir / "task_index.tsv")
        loaded = load_events(campaign_dir, task_rows, fixture_dir, audited_roots)
        validate_pilot_shape(loaded, testing=testing)
        v1.validate_against_summaries(
            finalized_dir,
            loaded.groups,
            {key: len(blocks) for key, blocks in loaded.blocks.items()},
        )
        reconciliation = reconcile_v1(
            finalized_dir,
            loaded.groups,
            campaign_id=bundle.campaign_id,
            plan_hash=bundle.plan_hash,
            testing=testing,
            bootstrap_seed=bootstrap_seed,
            bootstrap_resamples=resamples,
            confidence_level=confidence_level,
        )
        if not testing and reconciliation["status"] != "passed":
            raise ValueError("accepted analysis-v2 requires passed v1 reconciliation")

        cache = build_bootstrap_cache(
            loaded.groups,
            bootstrap_seed=bootstrap_seed,
            resamples=resamples,
            np=np,
        )
        configurations = configuration_rows(loaded, cache)
        distributions = distribution_rows(loaded.groups, np=np)
        pathways = pathway_rows(loaded.groups)
        pooled, pooled_cache = pooled_rows_and_cache(loaded.groups, cache)
        standardized = standardized_rows(loaded.groups, cache, pooled_cache)
        primary, primary_details = primary_contrasts(
            loaded.groups, cache, pooled_cache
        )
        block_stability = block_stability_rows(
            loaded, primary, primary_details
        )
        secondary = secondary_contrast_rows(
            loaded.groups, cache, pooled_cache, standardized
        )
        absorber = absorber_equivalence_rows(
            loaded.groups, cache, pooled_cache
        )
        if not testing:
            require_complete_intervals(
                configurations,
                tuple(f"{metric}_valid_resamples" for metric in METRICS),
                expected_resamples=resamples,
                label="configuration",
            )
            require_complete_intervals(
                pooled,
                ("generated_valid_resamples", "scintillation_valid_resamples"),
                expected_resamples=resamples,
                label="pooled-production",
            )
            require_complete_intervals(
                standardized,
                (
                    "standardized_net_valid_resamples",
                    "difference_valid_resamples",
                ),
                expected_resamples=resamples,
                label="standardized-response",
            )
            for label, rows in (
                ("primary-contrast", primary),
                ("secondary-contrast", secondary),
                ("absorber-equivalence", absorber),
            ):
                require_complete_intervals(
                    rows,
                    ("valid_resamples",),
                    expected_resamples=resamples,
                    label=label,
                )
        block_events = (
            args.production_block_events
            if args.production_block_events is not None
            else int(bundle.manifest["events_per_task"])
        )
        if block_events <= 0:
            raise ValueError("--production-block-events must be positive")
        if not testing and block_events != PINNED_PRODUCTION_BLOCK_EVENTS:
            raise ValueError(
                "accepted analysis-v2 requires 250-event production blocks"
            )
        sizing = sizing_report(
            primary,
            block_events=block_events,
            expected_resamples=resamples,
        )
        accepted = bool(
            validation.get("accepted_statistical_evidence") is True
            and not testing
            and not dirty
            and reconciliation["status"] == "passed"
        )

        if not testing:
            verify_analysis_checkout_stable(
                repo_root=repo_root,
                commit=analysis_commit,
                branch=branch_text,
                dirty_text=dirty_text,
                analyzer_path=analyzer_path,
                analyzer_hash=analyzer_hash,
                v1_path=v1_path,
                v1_hash=v1_hash,
            )
        verify_input_snapshot(
            campaign_dir=campaign_dir,
            finalized_dir=finalized_dir,
            expected_hashes=input_hashes,
            expected_campaign_id=bundle.campaign_id,
            expected_plan_hash=bundle.plan_hash,
            expected_simulation_commit=bundle.git_commit,
            expected_environment_identity=bundle.environment.get("identity_hash"),
            audited_roots=audited_roots,
        )

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            write_csv(
                temp_dir / "configuration_estimates.csv",
                CONFIGURATION_FIELDS,
                configurations,
            )
            write_csv(
                temp_dir / "distribution_diagnostics.csv",
                DISTRIBUTION_FIELDS,
                distributions,
            )
            write_csv(
                temp_dir / "response_pathway.csv", PATHWAY_FIELDS, pathways
            )
            write_csv(
                temp_dir / "seed_block_stability.csv",
                BLOCK_STABILITY_FIELDS,
                block_stability,
            )
            write_csv(
                temp_dir / "pooled_production.csv", POOLED_FIELDS, pooled
            )
            write_csv(
                temp_dir / "standardized_response.csv",
                STANDARDIZED_FIELDS,
                standardized,
            )
            write_csv(
                temp_dir / "primary_contrasts.csv", PRIMARY_FIELDS, primary
            )
            write_csv(
                temp_dir / "secondary_contrasts.csv",
                SECONDARY_FIELDS,
                secondary,
            )
            write_csv(
                temp_dir / "absorber_equivalence.csv",
                ABSORBER_FIELDS,
                absorber,
            )
            atomic_write_json(temp_dir / "production_sizing_v2.json", sizing)
            (temp_dir / "summary.md").write_text(
                build_summary(
                    accepted=accepted,
                    primary_rows=primary,
                    distribution=distributions,
                    absorber=absorber,
                    sizing=sizing,
                ),
                encoding="utf-8",
            )
            completed_config = {
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "campaign_directory": str(campaign_dir),
                "finalized_directory": str(finalized_dir),
                "simulation_git_commit": bundle.git_commit,
                "analysis_git": {
                    "commit": analysis_commit,
                    "branch": branch_text or None,
                    "dirty": dirty,
                    "dirty_paths": dirty_text.splitlines() if dirty else [],
                    "verified_unchanged_at_completion": not testing,
                },
                "environment_identity": bundle.environment.get("identity_hash"),
                "event_source": "fixture_csv" if testing else "audited_root_scan_tree",
                "accepted_statistical_evidence": accepted,
                "task_count": len(task_rows),
                "configuration_count": len(loaded.groups),
                "v1_reconciliation": reconciliation,
                "input_identity": {
                    "frozen_file_sha256s": input_hashes,
                    "audited_root_count": len(audited_roots),
                    "v1_reconciliation": reconciliation,
                },
                "analyzer": {
                    "implementation": "hpc/osc/analyze_steel_module_campaign_v2.py",
                    "sha256": analyzer_hash,
                    "v1_helper_sha256": v1_hash,
                    "python_version": sys.version.split()[0],
                    "numpy_version": str(np.__version__),
                    "uproot_version": uproot_version,
                },
                "bootstrap": {
                    "seed": bootstrap_seed,
                    "resamples": resamples,
                    "confidence_level": confidence_level,
                    "resampling_unit": "event within configuration",
                    "method": "stratified-independent-event-percentile-bootstrap",
                    "generator": "numpy.random.Generator(numpy.random.PCG64)",
                    "within_configuration_metric_coupling": True,
                    "configuration_seed_derivation": (
                        "first 64 bits of sha256(base_seed|analysis-v2|configuration-key)"
                    ),
                    "pooled_design_weights": "original event counts within layout strata",
                },
                "metric_definitions": {
                    "generated": "generated optical photons per incident neutron",
                    "scintillation": "scintillation photons per incident neutron",
                    "collection": "aggregate SiPM entries divided by generated optical photons",
                    "net": "aggregate SiPM entries per incident neutron",
                    "standardized_net": "pooled generated optical production times layout collection",
                },
                "distribution_diagnostics": {
                    "quantile_method": "numpy linear",
                    "tail_event_count": "ceil(fraction * all incident events)",
                    "tail_share_denominator": "all-event metric sum",
                    "rms": "sqrt(mean(x^2))",
                    "standard_deviation": "population",
                },
                "response_pathway": {
                    "conditional_probabilities": "intersection count divided by named condition count",
                    "zero_denominators": "NaN with validity=false",
                },
                "primary_contrasts": [row["primary_contrast_id"] for row in primary],
                "equivalence_review": {
                    "half_widths": list(EQUIVALENCE_HALF_WIDTHS),
                    "rule": "95% bootstrap CI containment; conservative review; not TOST",
                    "automatic_acceptance": False,
                },
                "precision_projection": {
                    "targets": list(PRECISION_TARGETS),
                    "safety_factor": PROJECTION_SAFETY_FACTOR,
                    "events_per_execution_block": block_events,
                    "automatic_acceptance": False,
                    "global_recommendation": None,
                },
                "block_stability": {
                    "diagnostic_thresholds": [0.10, 0.20],
                    "automatic_acceptance": False,
                    "pooled_leave_one_out_weights": "fixed-full-design-layout-weights",
                },
                "outputs": {
                    "configuration_estimates": "configuration_estimates.csv",
                    "distribution_diagnostics": "distribution_diagnostics.csv",
                    "response_pathway": "response_pathway.csv",
                    "seed_block_stability": "seed_block_stability.csv",
                    "pooled_production": "pooled_production.csv",
                    "standardized_response": "standardized_response.csv",
                    "primary_contrasts": "primary_contrasts.csv",
                    "secondary_contrasts": "secondary_contrasts.csv",
                    "absorber_equivalence": "absorber_equivalence.csv",
                    "production_sizing": "production_sizing_v2.json",
                    "summary": "summary.md",
                },
            }
            atomic_write_json(temp_dir / "analysis_config.json", completed_config)
            v1.write_checksums(temp_dir)
            if not testing:
                verify_analysis_checkout_stable(
                    repo_root=repo_root,
                    commit=analysis_commit,
                    branch=branch_text,
                    dirty_text=dirty_text,
                    analyzer_path=analyzer_path,
                    analyzer_hash=analyzer_hash,
                    v1_path=v1_path,
                    v1_hash=v1_hash,
                )
            verify_input_snapshot(
                campaign_dir=campaign_dir,
                finalized_dir=finalized_dir,
                expected_hashes=input_hashes,
                expected_campaign_id=bundle.campaign_id,
                expected_plan_hash=bundle.plan_hash,
                expected_simulation_commit=bundle.git_commit,
                expected_environment_identity=bundle.environment.get(
                    "identity_hash"
                ),
                audited_roots=audited_roots,
            )
            os.replace(temp_dir, output_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"Cannot analyze steel-module campaign v2: {exc}", file=sys.stderr)
        return 1

    print(f"Analyzed {len(task_rows)} steel-module tasks into {output_dir}")
    print(
        f"Configurations: {len(loaded.groups)}; bootstrap: {resamples} "
        f"resamples, seed {bootstrap_seed}"
    )
    print("Primary contrasts: 4; sizing projections: 8; automatic acceptance: false")
    print(f"Accepted statistical evidence: {str(accepted).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
