#!/usr/bin/env python3
"""Run pinned event-level analysis for a finalized steel-module campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from realistic_neutron_event_audit import SIPM_SENSOR_FIELDS, read_and_audit_root
from steel_module_campaign_lib import (
    CampaignBundle,
    SIPM_LAYOUTS,
    atomic_write_json,
    load_campaign,
    load_json,
    resolve_campaign_path,
    sha256_file,
    verify_finalized_checksums,
)


BASE_EVENT_FIELDS = (
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
)
EVENT_FIELDS = (*BASE_EVENT_FIELDS, *SIPM_SENSOR_FIELDS)
COMPARISON_METRICS = ("production", "collection", "net")
SENSOR_METRICS = tuple(f"sensor_{index}" for index in range(4))
ALL_BOOTSTRAP_METRICS = (*COMPARISON_METRICS, *SENSOR_METRICS)
PRECISION_TARGETS = (0.05, 0.10)
PROJECTION_SAFETY_FACTOR = 1.25
LAYOUT_SENSOR_COUNTS = {
    "back-center": 1,
    "edge-center": 1,
    "edge-two": 2,
    "back-four": 4,
}

ConfigurationKey = tuple[str, int, str, int, int, int]

CONFIGURATION_INTERVAL_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_blocks",
    "events",
    "production_scint_photons_per_neutron",
    "production_ci95_low",
    "production_ci95_high",
    "production_valid_resamples",
    "collection_sipm_over_generated",
    "collection_ci95_low",
    "collection_ci95_high",
    "collection_valid_resamples",
    "net_sipm_photons_per_neutron",
    "net_ci95_low",
    "net_ci95_high",
    "net_valid_resamples",
    "interaction_fraction",
    "interaction_wilson95_low",
    "interaction_wilson95_high",
    "generated_optical_zero_fraction",
    "generated_optical_zero_wilson95_low",
    "generated_optical_zero_wilson95_high",
    "scintillation_zero_fraction",
    "scintillation_zero_wilson95_low",
    "scintillation_zero_wilson95_high",
    "sipm_detected_zero_fraction",
    "sipm_detected_zero_wilson95_low",
    "sipm_detected_zero_wilson95_high",
)

PER_SENSOR_INTERVAL_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events",
    "sensor_index",
    "sensor_field",
    "sipm_photons_per_neutron",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
)

RATIO_METRIC_FIELDS = tuple(
    field
    for metric in COMPARISON_METRICS
    for field in (
        f"{metric}_ratio",
        f"{metric}_ci95_low",
        f"{metric}_ci95_high",
        f"{metric}_valid_resamples",
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
    *RATIO_METRIC_FIELDS,
)

ABSORBER_CONVERGENCE_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "x_mm",
    "y_mm",
    "reference_absorber_transverse_mm",
    "candidate_absorber_transverse_mm",
    "reference_events",
    "candidate_events",
    *tuple(
        field
        for metric in COMPARISON_METRICS
        for field in (
            f"{metric}_ratio",
            f"{metric}_ci95_low",
            f"{metric}_ci95_high",
            f"{metric}_valid_resamples",
            f"{metric}_point_within_0p95_1p05",
            f"{metric}_ci_within_0p95_1p05",
        )
    ),
)


@dataclass(frozen=True)
class Event:
    generated: int
    scintillation: int
    sipm: int
    elastic: int
    inelastic: int
    capture: int
    sensor0: int
    sensor1: int
    sensor2: int
    sensor3: int

    @property
    def interacted(self) -> bool:
        return self.elastic > 0 or self.inelastic > 0 or self.capture > 0

    @property
    def sensors(self) -> tuple[int, int, int, int]:
        return (self.sensor0, self.sensor1, self.sensor2, self.sensor3)


def sensor_count(layout: str) -> int:
    try:
        return LAYOUT_SENSOR_COUNTS[layout]
    except KeyError as exc:
        raise ValueError(f"unsupported SiPM layout: {layout}") from exc


def validate_active_sensor_columns(
    layout: str, events: Sequence[Event], logical_id: str
) -> None:
    active_sensors = sensor_count(layout)
    if any(any(event.sensors[active_sensors:]) for event in events):
        raise ValueError(
            f"{layout} layout has counts in inactive sensor columns: {logical_id}"
        )


@dataclass(frozen=True)
class AuditedRoot:
    path: str
    sha256: str
    tile_thickness_mm: int
    sipm_layout: str
    absorber_transverse_mm: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--finalized-dir", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to FINALIZED_DIR/analysis; must not already exist.",
    )
    parser.add_argument(
        "--event-fixture-dir",
        type=Path,
        help="Test-only CSV event source keyed by logical_task_id; production reads ROOT.",
    )
    parser.add_argument(
        "--testing-allow-event-fixtures",
        action="store_true",
        help="Explicitly mark fixture analysis as non-scientific infrastructure testing.",
    )
    parser.add_argument(
        "--production-block-events",
        type=int,
        help="Execution block size for production-N projections; defaults to campaign events per task.",
    )
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def integer(value: object, field: str) -> int:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"event field {field} is not numeric: {value!r}") from exc
    if not math.isfinite(converted) or converted < 0 or not converted.is_integer():
        raise ValueError(f"event field {field} must be a finite non-negative integer")
    return int(converted)


def event_from_row(row: Mapping[str, object]) -> Event:
    values = {field: integer(row.get(field), field) for field in EVENT_FIELDS}
    sensors = tuple(values[field] for field in SIPM_SENSOR_FIELDS)
    if values["sipm_detected_photons"] != sum(sensors):
        raise ValueError("event aggregate SiPM count does not equal per-sensor sum")
    if values["scintillation_photons"] > values["generated_optical_photons"]:
        raise ValueError("event scintillation count exceeds generated optical photons")
    if values["sipm_detected_photons"] > values["generated_optical_photons"]:
        raise ValueError("event SiPM count exceeds generated optical photons")
    return Event(
        generated=values["generated_optical_photons"],
        scintillation=values["scintillation_photons"],
        sipm=values["sipm_detected_photons"],
        elastic=values["primary_neutron_elastic_count"],
        inelastic=values["primary_neutron_inelastic_count"],
        capture=values["primary_neutron_capture_count"],
        sensor0=sensors[0],
        sensor1=sensors[1],
        sensor2=sensors[2],
        sensor3=sensors[3],
    )


def read_fixture_events(path: Path) -> list[Event]:
    rows = read_csv(path)
    events = [event_from_row(row) for row in rows]
    if not events:
        raise ValueError(f"event fixture is empty: {path}")
    return events


def read_root_events(path: Path, expected_events: int) -> list[Event]:
    arrays, _ = read_and_audit_root(
        path,
        expected_events=expected_events,
        context=str(path),
    )
    missing = [field for field in EVENT_FIELDS if field not in arrays]
    if missing:
        raise ValueError(f"ROOT scan tree lacks analysis fields: {missing}")
    count = len(arrays[EVENT_FIELDS[0]])
    events = [
        event_from_row({field: arrays[field][index] for field in EVENT_FIELDS})
        for index in range(count)
    ]
    if not events:
        raise ValueError(f"ROOT scan tree is empty: {path}")
    return events


def configuration_key(row: Mapping[str, object]) -> ConfigurationKey:
    return (
        str(row["stage"]),
        int(row["tile_thickness_mm"]),
        str(row["sipm_layout"]),
        int(row["absorber_transverse_mm"]),
        int(row["x_mm"]),
        int(row["y_mm"]),
    )


def load_events(
    campaign_dir: Path,
    task_rows: list[dict[str, str]],
    fixture_dir: Path | None,
    audited_roots: Mapping[str, AuditedRoot],
) -> tuple[dict[ConfigurationKey, list[Event]], dict[ConfigurationKey, int]]:
    groups: dict[ConfigurationKey, list[Event]] = defaultdict(list)
    block_counts: dict[ConfigurationKey, int] = defaultdict(int)
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
            raise ValueError(f"task index disagrees with frozen event audit: {logical_id}")
        root_path = resolve_campaign_path(campaign_dir, row["root"])
        if sha256_file(root_path) != frozen.sha256:
            raise ValueError(f"audited ROOT checksum mismatch: {root_path}")
        expected_events = int(row["events"])
        if fixture_dir is not None:
            events = read_fixture_events(fixture_dir / f"{logical_id}.csv")
        else:
            events = read_root_events(root_path, expected_events)
        if len(events) != expected_events:
            raise ValueError(
                f"event count mismatch for {logical_id}: {len(events)} != {expected_events}"
            )
        validate_active_sensor_columns(row["sipm_layout"], events, logical_id)
        key = configuration_key(row)
        groups[key].extend(events)
        block_counts[key] += 1
    if set(audited_roots) != seen_tasks:
        raise ValueError("frozen event audit task set disagrees with task_index.tsv")
    return dict(groups), dict(block_counts)


def estimator(events: Sequence[Event]) -> dict[str, float]:
    count = len(events)
    generated = sum(event.generated for event in events)
    scintillation = sum(event.scintillation for event in events)
    sipm = sum(event.sipm for event in events)
    result = {
        "production": scintillation / count,
        "collection": sipm / generated if generated > 0 else math.nan,
        "net": sipm / count,
    }
    for index in range(4):
        result[f"sensor_{index}"] = sum(event.sensors[index] for event in events) / count
    return result


def configuration_seed(base_seed: int, key: tuple[object, ...]) -> int:
    payload = f"{base_seed}|{'|'.join(map(str, key))}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def empty_bootstrap() -> dict[str, list[float]]:
    return {name: [] for name in ALL_BOOTSTRAP_METRICS}


def bootstrap_configuration_python(
    events: Sequence[Event], *, resamples: int, seed: int
) -> dict[str, list[float]]:
    """Test-fixture fallback; production analysis requires NumPy PCG64."""
    rng = random.Random(seed)
    output = empty_bootstrap()
    count = len(events)
    for _ in range(resamples):
        generated = 0
        scintillation = 0
        sipm = 0
        sensors = [0, 0, 0, 0]
        for _ in range(count):
            event = events[rng.randrange(count)]
            generated += event.generated
            scintillation += event.scintillation
            sipm += event.sipm
            for index, value in enumerate(event.sensors):
                sensors[index] += value
        output["production"].append(scintillation / count)
        output["collection"].append(
            sipm / generated if generated > 0 else math.nan
        )
        output["net"].append(sipm / count)
        for index, value in enumerate(sensors):
            output[f"sensor_{index}"].append(value / count)
    return output


def bootstrap_configuration(
    events: Sequence[Event], *, resamples: int, seed: int
) -> dict[str, list[float]]:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        return bootstrap_configuration_python(events, resamples=resamples, seed=seed)

    values = np.asarray(
        [
            [event.generated, event.scintillation, event.sipm, *event.sensors]
            for event in events
        ],
        dtype=float,
    )
    count = len(events)
    rng = np.random.Generator(np.random.PCG64(seed))
    output = empty_bootstrap()
    max_indices = 1_000_000
    batch_size = max(1, min(resamples, max_indices // count))
    completed = 0
    while completed < resamples:
        batch = min(batch_size, resamples - completed)
        indices = rng.integers(0, count, size=(batch, count))
        sums = values[indices].sum(axis=1)
        output["production"].extend(float(value) for value in sums[:, 1] / count)
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
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    *,
    bootstrap_seed: int,
    resamples: int,
) -> dict[ConfigurationKey, dict[str, list[float]]]:
    return {
        key: bootstrap_configuration(
            events,
            resamples=resamples,
            seed=configuration_seed(bootstrap_seed, key),
        )
        for key, events in groups.items()
    }


def percentile(values: Sequence[float], probability: float) -> float:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return math.nan
    position = probability * (len(finite) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return finite[lower]
    weight = position - lower
    return finite[lower] * (1 - weight) + finite[upper] * weight


def interval(values: Sequence[float]) -> tuple[float, float, int]:
    return (
        percentile(values, 0.025),
        percentile(values, 0.975),
        sum(math.isfinite(value) for value in values),
    )


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else math.nan


def ratio_distribution(
    numerator: Sequence[float], denominator: Sequence[float]
) -> list[float]:
    if len(numerator) != len(denominator):
        raise ValueError("bootstrap distributions have different lengths")
    return [safe_ratio(a, b) for a, b in zip(numerator, denominator)]


def wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - half), min(1.0, center + half)


def configuration_interval_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    block_counts: Mapping[ConfigurationKey, int],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (item[0], item[2], item[3], item[1])):
        stage, thickness, layout, absorber, x_mm, y_mm = key
        events = groups[key]
        count = len(events)
        point = estimator(events)
        row: dict[str, object] = {
            "stage": stage,
            "tile_thickness_mm": thickness,
            "sipm_layout": layout,
            "absorber_transverse_mm": absorber,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "seed_blocks": block_counts[key],
            "events": count,
            "production_scint_photons_per_neutron": point["production"],
            "collection_sipm_over_generated": point["collection"],
            "net_sipm_photons_per_neutron": point["net"],
        }
        for metric in COMPARISON_METRICS:
            low, high, valid = interval(cache[key][metric])
            row[f"{metric}_ci95_low"] = low
            row[f"{metric}_ci95_high"] = high
            row[f"{metric}_valid_resamples"] = valid

        fraction_counts = {
            "interaction": sum(event.interacted for event in events),
            "generated_optical_zero": sum(event.generated == 0 for event in events),
            "scintillation_zero": sum(event.scintillation == 0 for event in events),
            "sipm_detected_zero": sum(event.sipm == 0 for event in events),
        }
        for name, successes in fraction_counts.items():
            low, high = wilson_interval(successes, count)
            row[f"{name}_fraction"] = successes / count
            row[f"{name}_wilson95_low"] = low
            row[f"{name}_wilson95_high"] = high
        rows.append(row)
    return rows


def per_sensor_interval_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (item[0], item[2], item[3], item[1])):
        stage, thickness, layout, absorber, x_mm, y_mm = key
        point = estimator(groups[key])
        for index, field in enumerate(SIPM_SENSOR_FIELDS):
            metric = f"sensor_{index}"
            low, high, valid = interval(cache[key][metric])
            rows.append(
                {
                    "stage": stage,
                    "tile_thickness_mm": thickness,
                    "sipm_layout": layout,
                    "absorber_transverse_mm": absorber,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "events": len(groups[key]),
                    "sensor_index": index,
                    "sensor_field": field,
                    "sipm_photons_per_neutron": point[metric],
                    "ci95_low": low,
                    "ci95_high": high,
                    "valid_resamples": valid,
                }
            )
    return rows


def add_ratio_metrics(
    row: dict[str, object],
    *,
    numerator_key: ConfigurationKey,
    denominator_key: ConfigurationKey,
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> None:
    numerator_point = estimator(groups[numerator_key])
    denominator_point = estimator(groups[denominator_key])
    for metric in COMPARISON_METRICS:
        values = ratio_distribution(
            cache[numerator_key][metric], cache[denominator_key][metric]
        )
        low, high, valid = interval(values)
        row[f"{metric}_ratio"] = safe_ratio(
            numerator_point[metric], denominator_point[metric]
        )
        row[f"{metric}_ci95_low"] = low
        row[f"{metric}_ci95_high"] = high
        row[f"{metric}_valid_resamples"] = valid


def thickness_ratio_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    by_context: dict[tuple[object, ...], dict[int, ConfigurationKey]] = defaultdict(dict)
    for key in groups:
        stage, thickness, layout, absorber, x_mm, y_mm = key
        by_context[(stage, layout, absorber, x_mm, y_mm)][thickness] = key
    rows: list[dict[str, object]] = []
    for context in sorted(by_context):
        by_thickness = by_context[context]
        reference = by_thickness.get(4)
        if reference is None:
            continue
        for thickness in sorted(value for value in by_thickness if value != 4):
            compared = by_thickness[thickness]
            row: dict[str, object] = {
                "stage": context[0],
                "sipm_layout": context[1],
                "absorber_transverse_mm": context[2],
                "x_mm": context[3],
                "y_mm": context[4],
                "reference_tile_thickness_mm": 4,
                "compared_tile_thickness_mm": thickness,
                "reference_events": len(groups[reference]),
                "compared_events": len(groups[compared]),
            }
            add_ratio_metrics(
                row,
                numerator_key=compared,
                denominator_key=reference,
                groups=groups,
                cache=cache,
            )
            rows.append(row)
    return rows


def layout_ratio_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    by_context: dict[tuple[object, ...], dict[str, ConfigurationKey]] = defaultdict(dict)
    for key in groups:
        stage, thickness, layout, absorber, x_mm, y_mm = key
        by_context[(stage, thickness, absorber, x_mm, y_mm)][layout] = key
    rows: list[dict[str, object]] = []
    for context in sorted(by_context):
        by_layout = by_context[context]
        reference = by_layout.get("back-center")
        if reference is None:
            continue
        for layout in SIPM_LAYOUTS:
            if layout == "back-center" or layout not in by_layout:
                continue
            compared = by_layout[layout]
            row: dict[str, object] = {
                "stage": context[0],
                "tile_thickness_mm": context[1],
                "absorber_transverse_mm": context[2],
                "x_mm": context[3],
                "y_mm": context[4],
                "reference_sipm_layout": "back-center",
                "compared_sipm_layout": layout,
                "reference_events": len(groups[reference]),
                "compared_events": len(groups[compared]),
            }
            add_ratio_metrics(
                row,
                numerator_key=compared,
                denominator_key=reference,
                groups=groups,
                cache=cache,
            )
            rows.append(row)
    return rows


def absorber_convergence_rows(
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    cache: Mapping[ConfigurationKey, Mapping[str, Sequence[float]]],
    *,
    equivalence_half_width: float = 0.05,
) -> list[dict[str, object]]:
    by_context: dict[tuple[object, ...], dict[int, ConfigurationKey]] = defaultdict(dict)
    for key in groups:
        stage, thickness, layout, absorber, x_mm, y_mm = key
        by_context[(stage, thickness, layout, x_mm, y_mm)][absorber] = key
    lower_bound = 1.0 - equivalence_half_width
    upper_bound = 1.0 + equivalence_half_width
    rows: list[dict[str, object]] = []
    for context in sorted(by_context):
        by_absorber = by_context[context]
        reference = by_absorber.get(500)
        if reference is None:
            continue
        for absorber in (200, 300):
            candidate = by_absorber.get(absorber)
            if candidate is None:
                continue
            row: dict[str, object] = {
                "stage": context[0],
                "tile_thickness_mm": context[1],
                "sipm_layout": context[2],
                "x_mm": context[3],
                "y_mm": context[4],
                "reference_absorber_transverse_mm": 500,
                "candidate_absorber_transverse_mm": absorber,
                "reference_events": len(groups[reference]),
                "candidate_events": len(groups[candidate]),
            }
            add_ratio_metrics(
                row,
                numerator_key=candidate,
                denominator_key=reference,
                groups=groups,
                cache=cache,
            )
            for metric in COMPARISON_METRICS:
                point = float(row[f"{metric}_ratio"])
                low = float(row[f"{metric}_ci95_low"])
                high = float(row[f"{metric}_ci95_high"])
                row[f"{metric}_point_within_0p95_1p05"] = (
                    math.isfinite(point) and lower_bound <= point <= upper_bound
                )
                row[f"{metric}_ci_within_0p95_1p05"] = (
                    math.isfinite(low)
                    and math.isfinite(high)
                    and low >= lower_bound
                    and high <= upper_bound
                )
            rows.append(row)
    return rows


def projection_requirement(
    *,
    point: float,
    low: float,
    high: float,
    current_events: int,
    target: float,
    block_events: int,
    safety_factor: float,
) -> dict[str, float | int] | None:
    if (
        not all(math.isfinite(value) for value in (point, low, high))
        or point == 0
        or current_events <= 0
    ):
        return None
    relative_half_width = (high - low) / (2.0 * abs(point))
    def tolerant_ceil(value: float) -> int:
        # Avoid adding an entire execution block when an exact mathematical
        # boundary is represented a few ulps above its integer value.
        return math.ceil(value - 1e-12 * max(1.0, abs(value)))

    projected = max(
        current_events,
        tolerant_ceil(current_events * (relative_half_width / target) ** 2),
    )
    buffered = tolerant_ceil(projected * safety_factor)
    rounded = max(
        4 * block_events,
        math.ceil(buffered / block_events) * block_events,
    )
    return {
        "observed_relative_half_width": relative_half_width,
        "projected_events_before_safety_factor": projected,
        "events_after_safety_factor": buffered,
        "rounded_events_per_configuration": rounded,
    }


def production_statistics_recommendation(
    thickness_rows: Sequence[Mapping[str, object]],
    layout_rows: Sequence[Mapping[str, object]],
    *,
    stage: str,
    block_events: int,
    targets: Sequence[float],
    safety_factor: float,
) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "steel-module-production-statistics-v1",
        "stage": stage,
        "method": "bootstrap_ci_width_sqrt_n_projection",
        "precision_targets": list(targets),
        "safety_factor": safety_factor,
        "minimum_seed_blocks": 4,
        "events_per_execution_block": block_events,
        "automatic_acceptance": False,
        "caveat": (
            "These 5% and 10% projections are review aids, not accepted precision "
            "targets. They use 1/sqrt(N), add the fixed safety factor, round to "
            "complete execution blocks, and require new independent production seeds."
        ),
    }
    if stage != "convergence-pilot":
        return {**base, "status": "not_applicable_outside_convergence_pilot"}

    comparisons: list[tuple[str, Mapping[str, object], tuple[str, ...]]] = []
    comparisons.extend(
        ("thickness", row, COMPARISON_METRICS)
        for row in thickness_rows
        if int(row["absorber_transverse_mm"]) == 500
    )
    comparisons.extend(
        ("layout", row, ("collection", "net"))
        for row in layout_rows
        if int(row["absorber_transverse_mm"]) == 500
    )
    if not comparisons:
        return {**base, "status": "cannot_project_without_500mm_comparisons"}

    target_reports: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    for target in targets:
        requirements: list[dict[str, object]] = []
        for family, row, metrics in comparisons:
            reference_events = int(row["reference_events"])
            compared_events = int(row["compared_events"])
            if reference_events != compared_events:
                issues.append(
                    {
                        "target_relative_half_width": target,
                        "comparison_family": family,
                        "reason": "unequal event counts",
                        "reference_events": reference_events,
                        "compared_events": compared_events,
                    }
                )
                continue
            for metric in metrics:
                requirement = projection_requirement(
                    point=float(row[f"{metric}_ratio"]),
                    low=float(row[f"{metric}_ci95_low"]),
                    high=float(row[f"{metric}_ci95_high"]),
                    current_events=reference_events,
                    target=target,
                    block_events=block_events,
                    safety_factor=safety_factor,
                )
                identity = {
                    "comparison_family": family,
                    "metric": metric,
                    "reference_events": reference_events,
                    "compared_events": compared_events,
                    "comparison": {
                        key: row[key]
                        for key in (
                            (
                                "sipm_layout",
                                "reference_tile_thickness_mm",
                                "compared_tile_thickness_mm",
                            )
                            if family == "thickness"
                            else (
                                "tile_thickness_mm",
                                "reference_sipm_layout",
                                "compared_sipm_layout",
                            )
                        )
                    },
                }
                if requirement is None:
                    issues.append(
                        {
                            "target_relative_half_width": target,
                            **identity,
                            "reason": "non-finite or zero ratio interval",
                        }
                    )
                    continue
                requirements.append(
                    {
                        **identity,
                        "ratio": float(row[f"{metric}_ratio"]),
                        "ci95_low": float(row[f"{metric}_ci95_low"]),
                        "ci95_high": float(row[f"{metric}_ci95_high"]),
                        **requirement,
                    }
                )
        if not requirements:
            target_reports.append(
                {
                    "target_relative_half_width": target,
                    "status": "cannot_project",
                }
            )
            continue
        limiting = max(
            requirements,
            key=lambda item: int(item["rounded_events_per_configuration"]),
        )
        recommended_events = int(limiting["rounded_events_per_configuration"])
        target_reports.append(
            {
                "target_relative_half_width": target,
                "status": "projection_requires_review",
                "recommended_events_per_task": block_events,
                "recommended_blocks_per_configuration": (
                    recommended_events // block_events
                ),
                "recommended_total_events_per_configuration": recommended_events,
                "limiting_requirement": limiting,
                "evaluated_requirements": requirements,
            }
        )
    if all(report["status"] == "cannot_project" for report in target_reports):
        overall_status = "cannot_project"
    elif issues:
        overall_status = "projection_requires_review_with_issues"
    else:
        overall_status = "projection_requires_review"
    return {
        **base,
        "status": overall_status,
        "targets": target_reports,
        "issues": issues,
    }


def validate_against_summaries(
    finalized_dir: Path,
    groups: Mapping[ConfigurationKey, Sequence[Event]],
    block_counts: Mapping[ConfigurationKey, int],
) -> None:
    expected_rows = read_csv(finalized_dir / "configuration_summary.csv")
    expected = {configuration_key(row): row for row in expected_rows}
    if len(expected) != len(expected_rows):
        raise ValueError("configuration summary contains duplicate configurations")
    if set(expected) != set(groups):
        raise ValueError("event groups do not match finalized configuration summary")
    for key, events in groups.items():
        row = expected[key]
        comparisons = {
            "seed_blocks": block_counts[key],
            "events": len(events),
            "generated_optical_photons": sum(event.generated for event in events),
            "scintillation_photons": sum(event.scintillation for event in events),
            "sipm_detected_photons": sum(event.sipm for event in events),
            "primary_neutron_interaction_events": sum(
                event.interacted for event in events
            ),
            "primary_neutron_elastic_count": sum(event.elastic for event in events),
            "primary_neutron_inelastic_count": sum(
                event.inelastic for event in events
            ),
            "primary_neutron_capture_count": sum(event.capture for event in events),
            "generated_optical_zero_events": sum(
                event.generated == 0 for event in events
            ),
            "scintillation_zero_events": sum(
                event.scintillation == 0 for event in events
            ),
            "sipm_detected_zero_events": sum(event.sipm == 0 for event in events),
        }
        for index, field in enumerate(SIPM_SENSOR_FIELDS):
            comparisons[field] = sum(event.sensors[index] for event in events)
        for field, value in comparisons.items():
            if int(row[field]) != int(value):
                raise ValueError(f"event data disagree with {field} summary for {key}")


def write_csv(
    path: Path,
    fieldnames: Iterable[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(output_dir: Path) -> None:
    with (output_dir / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


def validate_identity(
    campaign_dir: Path, finalized_dir: Path
) -> tuple[
    CampaignBundle,
    dict[str, object],
    dict[str, object],
    dict[str, AuditedRoot],
]:
    bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
    verify_finalized_checksums(finalized_dir)
    validation = load_json(finalized_dir / "validation_report.json")
    if validation.get("valid") is not True:
        raise ValueError("finalized validation report is not valid")
    expected_identity = {
        "campaign_id": bundle.campaign_id,
        "plan_hash": bundle.plan_hash,
        "git_commit": bundle.git_commit,
        "environment_identity": bundle.environment.get("identity_hash"),
    }
    for field, value in expected_identity.items():
        if validation.get(field) != value:
            raise ValueError(f"finalized validation identity mismatch: {field}")
    accepted_evidence = (
        bundle.environment.get("accepted_statistical_evidence") is True
    )
    if validation.get("accepted_statistical_evidence") is not accepted_evidence:
        raise ValueError("finalized validation evidence class disagrees with campaign")
    analysis_config = load_json(finalized_dir / "analysis_config.json")
    if analysis_config.get("schema_version") != "steel-module-analysis-config-v1":
        raise ValueError("finalized analysis config is not steel-module-v1")
    if (
        analysis_config.get("campaign_id") != bundle.campaign_id
        or analysis_config.get("plan_hash") != bundle.plan_hash
        or analysis_config.get("root_tree") != "scan"
        or analysis_config.get("configuration_axes")
        != ["sipm_layout", "tile_thickness_mm", "absorber_transverse_mm"]
    ):
        raise ValueError("finalized analysis configuration identity mismatch")
    event_audit = load_json(finalized_dir / "event_audit.json")
    if (
        event_audit.get("schema_version") != "steel-module-event-audit-v1"
        or event_audit.get("valid") is not True
    ):
        raise ValueError("finalized event audit is not a valid steel-module audit")
    for field, value in expected_identity.items():
        if event_audit.get(field) != value:
            raise ValueError(f"finalized event audit identity mismatch: {field}")
    if event_audit.get("accepted_statistical_evidence") is not accepted_evidence:
        raise ValueError("finalized event audit evidence class disagrees with campaign")
    raw_tasks = event_audit.get("tasks")
    if not isinstance(raw_tasks, list) or event_audit.get("task_count") != len(raw_tasks):
        raise ValueError("finalized event audit task count is invalid")
    audited_roots: dict[str, AuditedRoot] = {}
    for raw_task in raw_tasks:
        if not isinstance(raw_task, dict):
            raise ValueError("finalized event audit contains a non-object task")
        logical_id = raw_task.get("logical_task_id")
        root_path = raw_task.get("root")
        root_sha256 = raw_task.get("root_sha256")
        layout = raw_task.get("sipm_layout")
        if (
            not isinstance(logical_id, str)
            or not logical_id
            or logical_id in audited_roots
            or not isinstance(root_path, str)
            or not root_path
            or not isinstance(root_sha256, str)
            or len(root_sha256) != 64
            or any(char not in "0123456789abcdef" for char in root_sha256)
            or not isinstance(layout, str)
        ):
            raise ValueError("finalized event audit contains an invalid task identity")
        audited_roots[logical_id] = AuditedRoot(
            path=root_path,
            sha256=root_sha256,
            tile_thickness_mm=int(raw_task["tile_thickness_mm"]),
            sipm_layout=layout,
            absorber_transverse_mm=int(raw_task["absorber_transverse_mm"]),
        )
    return bundle, validation, analysis_config, audited_roots


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
    targets = PRECISION_TARGETS

    campaign_dir = args.campaign_dir.expanduser().resolve()
    finalized_dir = (
        args.finalized_dir.expanduser().resolve()
        if args.finalized_dir is not None
        else campaign_dir / "finalized"
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else finalized_dir / "analysis"
    )
    fixture_dir = (
        args.event_fixture_dir.expanduser().resolve()
        if args.event_fixture_dir is not None
        else None
    )
    try:
        bundle, validation, analysis_config, audited_roots = validate_identity(
            campaign_dir, finalized_dir
        )
        bootstrap = analysis_config.get("bootstrap")
        if not isinstance(bootstrap, dict):
            raise ValueError("finalized analysis config has no bootstrap definition")
        bootstrap_seed = int(bootstrap["seed"])
        resamples = int(bootstrap["resamples"])
        if bootstrap_seed < 0 or resamples <= 0:
            raise ValueError("invalid pinned bootstrap definition")
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            if fixture_dir is None:
                raise ValueError(
                    "production steel-module bootstrap analysis requires NumPy"
                ) from exc
            numpy_version: str | None = None
            bootstrap_generator = "random.Random(test-fixture-only)"
        else:
            numpy_version = str(np.__version__)
            bootstrap_generator = (
                "numpy.random.Generator(numpy.random.PCG64)"
            )
        analyzer_path = Path(__file__).resolve()
        task_rows = read_tsv(finalized_dir / "task_index.tsv")
        groups, block_counts = load_events(
            campaign_dir, task_rows, fixture_dir, audited_roots
        )
        validate_against_summaries(finalized_dir, groups, block_counts)
        cache = build_bootstrap_cache(
            groups,
            bootstrap_seed=bootstrap_seed,
            resamples=resamples,
        )
        configuration_intervals = configuration_interval_rows(
            groups, block_counts, cache
        )
        per_sensor_intervals = per_sensor_interval_rows(groups, cache)
        thickness_ratios = thickness_ratio_rows(groups, cache)
        layout_ratios = layout_ratio_rows(groups, cache)
        absorber_convergence = absorber_convergence_rows(groups, cache)
        block_events = (
            args.production_block_events
            if args.production_block_events is not None
            else int(bundle.manifest["events_per_task"])
        )
        if block_events <= 0:
            raise ValueError("--production-block-events must be positive")
        production_statistics = production_statistics_recommendation(
            thickness_ratios,
            layout_ratios,
            stage=str(bundle.manifest["stage"]),
            block_events=block_events,
            targets=targets,
            safety_factor=PROJECTION_SAFETY_FACTOR,
        )
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            write_csv(
                temp_dir / "configuration_intervals.csv",
                CONFIGURATION_INTERVAL_FIELDS,
                configuration_intervals,
            )
            write_csv(
                temp_dir / "per_sensor_intervals.csv",
                PER_SENSOR_INTERVAL_FIELDS,
                per_sensor_intervals,
            )
            write_csv(
                temp_dir / "thickness_ratios.csv",
                THICKNESS_RATIO_FIELDS,
                thickness_ratios,
            )
            write_csv(
                temp_dir / "layout_ratios.csv",
                LAYOUT_RATIO_FIELDS,
                layout_ratios,
            )
            write_csv(
                temp_dir / "absorber_convergence.csv",
                ABSORBER_CONVERGENCE_FIELDS,
                absorber_convergence,
            )
            atomic_write_json(
                temp_dir / "production_statistics.json", production_statistics
            )
            completed_config = {
                **analysis_config,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "campaign_directory": str(campaign_dir),
                "finalized_directory": str(finalized_dir),
                "event_source": (
                    "fixture_csv" if fixture_dir is not None else "audited_root_scan_tree"
                ),
                "accepted_statistical_evidence": (
                    validation.get("accepted_statistical_evidence") is True
                    and fixture_dir is None
                ),
                "analyzer": {
                    "implementation": "hpc/osc/analyze_steel_module_campaign.py",
                    "sha256": sha256_file(analyzer_path),
                    "python_version": sys.version.split()[0],
                    "numpy_version": numpy_version,
                },
                "bootstrap": {
                    **bootstrap,
                    "status": "complete",
                    "generator": bootstrap_generator,
                    "numpy_version": numpy_version,
                },
                "precision_projection": {
                    "targets": list(targets),
                    "safety_factor": PROJECTION_SAFETY_FACTOR,
                    "automatic_acceptance": False,
                },
                "outputs": {
                    "configuration_intervals": "configuration_intervals.csv",
                    "per_sensor_intervals": "per_sensor_intervals.csv",
                    "thickness_ratios": "thickness_ratios.csv",
                    "layout_ratios": "layout_ratios.csv",
                    "absorber_convergence": "absorber_convergence.csv",
                    "production_statistics": "production_statistics.json",
                },
            }
            atomic_write_json(temp_dir / "analysis_config.json", completed_config)
            write_checksums(temp_dir)
            os.replace(temp_dir, output_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot analyze steel-module campaign: {exc}", file=sys.stderr)
        return 1

    print(f"Analyzed {len(task_rows)} steel-module tasks into {output_dir}")
    print(f"Configurations: {len(groups)}; bootstrap: {resamples} resamples, seed {bootstrap_seed}")
    labels = ", ".join(f"{100 * target:g}%" for target in targets)
    print(f"Precision projections: {labels} review aids; no target auto-accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
