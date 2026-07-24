#!/usr/bin/env python3
"""Run pinned event-level bootstrap analysis for a finalized neutron campaign."""

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
from typing import Iterable

from realistic_neutron_campaign_lib import (
    atomic_write_json,
    load_campaign,
    load_json,
    resolve_campaign_path,
    sha256_file,
    verify_finalized_checksums,
)
from realistic_neutron_event_audit import read_and_audit_root


EVENT_FIELDS = (
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
)

RATIO_FIELDS = (
    "stage",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events_4mm",
    "events_16mm",
    "production_ratio_16_over_4",
    "production_ci95_low",
    "production_ci95_high",
    "production_valid_resamples",
    "collection_ratio_16_over_4",
    "collection_ci95_low",
    "collection_ci95_high",
    "collection_valid_resamples",
    "net_ratio_16_over_4",
    "net_ci95_low",
    "net_ci95_high",
    "net_valid_resamples",
)

FRACTION_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events",
    "interaction_fraction",
    "interaction_wilson95_low",
    "interaction_wilson95_high",
    "scintillation_zero_fraction",
    "scintillation_zero_wilson95_low",
    "scintillation_zero_wilson95_high",
    "sipm_zero_fraction",
    "sipm_zero_wilson95_low",
    "sipm_zero_wilson95_high",
)

ABSORBER_CONVERGENCE_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "x_mm",
    "y_mm",
    "events_300mm",
    "events_500mm",
    "production_ratio_300_over_500",
    "production_ci95_low",
    "production_ci95_high",
    "production_valid_resamples",
    "production_equivalent_0p95_1p05",
    "net_ratio_300_over_500",
    "net_ci95_low",
    "net_ci95_high",
    "net_valid_resamples",
    "net_equivalent_0p95_1p05",
    "passes_absorber_convergence",
)


@dataclass(frozen=True)
class Event:
    generated: float
    scintillation: float
    sipm: float
    elastic: float
    inelastic: float
    capture: float

    @property
    def interacted(self) -> bool:
        return self.elastic > 0 or self.inelastic > 0 or self.capture > 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--finalized-dir", required=True, type=Path)
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
        help=(
            "Execution block size used when rounding the convergence-pilot "
            "production-N recommendation; defaults to campaign events_per_task."
        ),
    )
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def event_from_row(row: dict[str, object]) -> Event:
    values: list[float] = []
    for field in EVENT_FIELDS:
        try:
            value = float(row[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid event field {field}: {row.get(field)!r}") from exc
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            raise ValueError(
                f"event field {field} must be a finite non-negative integer"
            )
        values.append(value)
    return Event(*values)


def read_fixture_events(path: Path) -> list[Event]:
    events = [event_from_row(row) for row in read_csv(path)]
    if not events:
        raise ValueError(f"event fixture is empty: {path}")
    return events


def read_root_events(path: Path, expected_events: int) -> list[Event]:
    arrays, _ = read_and_audit_root(
        path,
        expected_events=expected_events,
        context=str(path),
    )
    count = len(arrays[EVENT_FIELDS[0]])
    events = [
        event_from_row({field: arrays[field][index] for field in EVENT_FIELDS})
        for index in range(count)
    ]
    if not events:
        raise ValueError(f"ROOT scan tree is empty: {path}")
    return events


def load_events(
    campaign_dir: Path,
    task_rows: list[dict[str, str]],
    fixture_dir: Path | None,
) -> dict[tuple[object, ...], list[Event]]:
    groups: dict[tuple[object, ...], list[Event]] = defaultdict(list)
    seen_tasks: set[str] = set()
    for row in task_rows:
        logical_id = row["logical_task_id"]
        if logical_id in seen_tasks:
            raise ValueError(f"duplicate task in task_index.tsv: {logical_id}")
        seen_tasks.add(logical_id)
        root_path = resolve_campaign_path(campaign_dir, row["root"])
        marker_path = (
            campaign_dir
            / "attempts"
            / row["attempt_id"]
            / "tasks"
            / logical_id
            / "task_result.json"
        )
        marker = load_json(marker_path)
        artifacts = marker.get("artifacts")
        root_record = artifacts.get("root") if isinstance(artifacts, dict) else None
        if not isinstance(root_record, dict):
            raise ValueError(f"task result has no ROOT identity: {logical_id}")
        if root_record.get("path") != row["root"]:
            raise ValueError(f"task index ROOT path disagrees with task result: {logical_id}")
        root_digest = root_record.get("sha256")
        if not isinstance(root_digest, str) or sha256_file(root_path) != root_digest:
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
        key = (
            row["stage"],
            int(row["tile_thickness_mm"]),
            int(row["absorber_transverse_mm"]),
            int(row["x_mm"]),
            int(row["y_mm"]),
        )
        groups[key].extend(events)
    return groups


def sums(events: list[Event]) -> tuple[float, float, float]:
    return (
        sum(event.generated for event in events),
        sum(event.scintillation for event in events),
        sum(event.sipm for event in events),
    )


def estimator(events: list[Event]) -> dict[str, float]:
    generated, scintillation, sipm = sums(events)
    count = len(events)
    return {
        "production": scintillation / count,
        "collection": sipm / generated if generated > 0 else math.nan,
        "net": sipm / count,
    }


def ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else math.nan


def percentile(values: list[float], probability: float) -> float:
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


def configuration_seed(base_seed: int, key: tuple[object, ...]) -> int:
    payload = f"{base_seed}|{'|'.join(map(str, key))}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def bootstrap_ratios_python(
    thin: list[Event],
    thick: list[Event],
    *,
    resamples: int,
    seed: int,
) -> dict[str, list[float]]:
    rng = random.Random(seed)
    values = {"production": [], "collection": [], "net": []}
    for _ in range(resamples):
        sample4 = [thin[rng.randrange(len(thin))] for _ in range(len(thin))]
        sample16 = [thick[rng.randrange(len(thick))] for _ in range(len(thick))]
        estimate4 = estimator(sample4)
        estimate16 = estimator(sample16)
        for name in values:
            values[name].append(ratio(estimate16[name], estimate4[name]))
    return values


def bootstrap_ratios_numpy(
    thin: list[Event],
    thick: list[Event],
    *,
    resamples: int,
    seed: int,
) -> dict[str, list[float]]:
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        return bootstrap_ratios_python(
            thin, thick, resamples=resamples, seed=seed
        )

    arrays4 = np.asarray(
        [[event.generated, event.scintillation, event.sipm] for event in thin],
        dtype=float,
    )
    arrays16 = np.asarray(
        [[event.generated, event.scintillation, event.sipm] for event in thick],
        dtype=float,
    )
    rng = np.random.default_rng(seed)
    results = {"production": [], "collection": [], "net": []}
    max_indices = 2_000_000
    batch_size = max(1, min(resamples, max_indices // max(len(thin), len(thick))))
    completed = 0
    while completed < resamples:
        batch = min(batch_size, resamples - completed)
        sample4 = arrays4[rng.integers(0, len(thin), size=(batch, len(thin)))].sum(axis=1)
        sample16 = arrays16[
            rng.integers(0, len(thick), size=(batch, len(thick)))
        ].sum(axis=1)
        production4 = sample4[:, 1] / len(thin)
        production16 = sample16[:, 1] / len(thick)
        collection4 = np.divide(
            sample4[:, 2], sample4[:, 0], out=np.full(batch, np.nan), where=sample4[:, 0] > 0
        )
        collection16 = np.divide(
            sample16[:, 2], sample16[:, 0], out=np.full(batch, np.nan), where=sample16[:, 0] > 0
        )
        net4 = sample4[:, 2] / len(thin)
        net16 = sample16[:, 2] / len(thick)
        for name, numerator, denominator in (
            ("production", production16, production4),
            ("collection", collection16, collection4),
            ("net", net16, net4),
        ):
            ratio_values = np.divide(
                numerator,
                denominator,
                out=np.full(batch, np.nan),
                where=denominator > 0,
            )
            results[name].extend(float(value) for value in ratio_values)
        completed += batch
    return results


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
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


def ratio_rows(
    groups: dict[tuple[object, ...], list[Event]],
    *,
    bootstrap_seed: int,
    resamples: int,
) -> list[dict[str, object]]:
    pairs: dict[tuple[object, ...], dict[int, list[Event]]] = defaultdict(dict)
    for key, events in groups.items():
        stage, tile, absorber, x_mm, y_mm = key
        pairs[(stage, absorber, x_mm, y_mm)][int(tile)] = events
    rows: list[dict[str, object]] = []
    for key in sorted(pairs):
        pair = pairs[key]
        if set(pair) != {4, 16}:
            raise ValueError(f"incomplete thickness pair: {key}")
        thin, thick = pair[4], pair[16]
        point4, point16 = estimator(thin), estimator(thick)
        bootstraps = bootstrap_ratios_numpy(
            thin,
            thick,
            resamples=resamples,
            seed=configuration_seed(bootstrap_seed, key),
        )
        row: dict[str, object] = {
            "stage": key[0],
            "absorber_transverse_mm": key[1],
            "x_mm": key[2],
            "y_mm": key[3],
            "events_4mm": len(thin),
            "events_16mm": len(thick),
        }
        for name in ("production", "collection", "net"):
            values = bootstraps[name]
            finite_count = sum(math.isfinite(value) for value in values)
            row[f"{name}_ratio_16_over_4"] = ratio(point16[name], point4[name])
            row[f"{name}_ci95_low"] = percentile(values, 0.025)
            row[f"{name}_ci95_high"] = percentile(values, 0.975)
            row[f"{name}_valid_resamples"] = finite_count
        rows.append(row)
    return rows


def absorber_convergence_rows(
    groups: dict[tuple[object, ...], list[Event]],
    *,
    bootstrap_seed: int,
    resamples: int,
) -> list[dict[str, object]]:
    """Compare the 300 mm absorber against the 500 mm production candidate."""

    configurations: dict[tuple[object, ...], dict[int, list[Event]]] = defaultdict(dict)
    for key, events in groups.items():
        stage, tile, absorber, x_mm, y_mm = key
        configurations[(stage, tile, x_mm, y_mm)][int(absorber)] = events

    rows: list[dict[str, object]] = []
    for key in sorted(configurations):
        by_absorber = configurations[key]
        if 300 not in by_absorber or 500 not in by_absorber:
            continue
        candidate = by_absorber[300]
        reference = by_absorber[500]
        candidate_point = estimator(candidate)
        reference_point = estimator(reference)
        bootstraps = bootstrap_ratios_numpy(
            reference,
            candidate,
            resamples=resamples,
            seed=configuration_seed(bootstrap_seed, (*key, "absorber-300-over-500")),
        )
        row: dict[str, object] = {
            "stage": key[0],
            "tile_thickness_mm": key[1],
            "x_mm": key[2],
            "y_mm": key[3],
            "events_300mm": len(candidate),
            "events_500mm": len(reference),
        }
        passed: list[bool] = []
        for name in ("production", "net"):
            values = bootstraps[name]
            low = percentile(values, 0.025)
            high = percentile(values, 0.975)
            equivalent = (
                math.isfinite(low)
                and math.isfinite(high)
                and low >= 0.95
                and high <= 1.05
            )
            row[f"{name}_ratio_300_over_500"] = ratio(
                candidate_point[name], reference_point[name]
            )
            row[f"{name}_ci95_low"] = low
            row[f"{name}_ci95_high"] = high
            row[f"{name}_valid_resamples"] = sum(
                math.isfinite(value) for value in values
            )
            row[f"{name}_equivalent_0p95_1p05"] = equivalent
            passed.append(equivalent)
        row["passes_absorber_convergence"] = all(passed)
        rows.append(row)
    return rows


def production_statistics_recommendation(
    ratios: list[dict[str, object]],
    *,
    stage: str,
    block_events: int,
    target_relative_half_width: float = 0.05,
    safety_factor: float = 1.25,
) -> dict[str, object]:
    """Project and round a reviewable production N from the 500 mm pilot CI."""

    base: dict[str, object] = {
        "schema_version": "realistic-neutron-production-statistics-v1",
        "stage": stage,
        "method": "bootstrap_ci_width_sqrt_n_projection",
        "target_relative_half_width": target_relative_half_width,
        "safety_factor": safety_factor,
        "minimum_seed_blocks": 4,
        "events_per_execution_block": block_events,
        "caveat": (
            "The recommendation projects the observed bootstrap CI width with "
            "1/sqrt(N), adds the fixed safety factor, and requires human review "
            "before a center-production campaign is generated."
        ),
    }
    if stage != "convergence-pilot":
        return {**base, "status": "not_applicable_outside_convergence_pilot"}
    if block_events <= 0:
        raise ValueError("--production-block-events must be positive")

    center = [
        row
        for row in ratios
        if int(row["absorber_transverse_mm"]) == 500
        and int(row["x_mm"]) == 0
        and int(row["y_mm"]) == 0
    ]
    if len(center) != 1:
        raise ValueError("convergence pilot must contain one centered 500 mm ratio row")
    row = center[0]
    events4 = int(row["events_4mm"])
    events16 = int(row["events_16mm"])
    if events4 != events16:
        raise ValueError("production-N derivation requires equal 4 mm and 16 mm events")
    current_events = events4

    requirements: dict[str, dict[str, object]] = {}
    rounded_totals: list[int] = []
    for name in ("production", "collection", "net"):
        point = float(row[f"{name}_ratio_16_over_4"])
        low = float(row[f"{name}_ci95_low"])
        high = float(row[f"{name}_ci95_high"])
        if not all(math.isfinite(value) for value in (point, low, high)) or point == 0:
            return {
                **base,
                "status": "cannot_recommend_from_nonfinite_or_zero_ratio",
                "failed_metric": name,
            }
        relative_half_width = (high - low) / (2.0 * abs(point))
        projected = math.ceil(
            current_events
            * (relative_half_width / target_relative_half_width) ** 2
        )
        pilot_derived = max(current_events, projected)
        buffered = math.ceil(pilot_derived * safety_factor)
        rounded = max(
            4 * block_events,
            math.ceil(buffered / block_events) * block_events,
        )
        requirements[name] = {
            "ratio": point,
            "ci95_low": low,
            "ci95_high": high,
            "observed_relative_half_width": relative_half_width,
            "pilot_events_per_thickness": current_events,
            "projected_events_before_safety_factor": pilot_derived,
            "events_after_safety_factor": buffered,
            "rounded_events_per_thickness": rounded,
        }
        rounded_totals.append(rounded)

    recommended_total = max(rounded_totals)
    return {
        **base,
        "status": "recommendation_requires_review",
        "metric_requirements": requirements,
        "recommended_events_per_task": block_events,
        "recommended_blocks_per_thickness": recommended_total // block_events,
        "recommended_total_events_per_thickness": recommended_total,
    }


def fraction_rows(
    groups: dict[tuple[object, ...], list[Event]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda value: (value[0], value[2], value[3], value[4], value[1])):
        stage, tile, absorber, x_mm, y_mm = key
        events = groups[key]
        total = len(events)
        interaction_count = sum(event.interacted for event in events)
        scint_zero = sum(event.scintillation == 0 for event in events)
        sipm_zero = sum(event.sipm == 0 for event in events)
        interaction_ci = wilson_interval(interaction_count, total)
        scint_ci = wilson_interval(scint_zero, total)
        sipm_ci = wilson_interval(sipm_zero, total)
        rows.append(
            {
                "stage": stage,
                "tile_thickness_mm": tile,
                "absorber_transverse_mm": absorber,
                "x_mm": x_mm,
                "y_mm": y_mm,
                "events": total,
                "interaction_fraction": interaction_count / total,
                "interaction_wilson95_low": interaction_ci[0],
                "interaction_wilson95_high": interaction_ci[1],
                "scintillation_zero_fraction": scint_zero / total,
                "scintillation_zero_wilson95_low": scint_ci[0],
                "scintillation_zero_wilson95_high": scint_ci[1],
                "sipm_zero_fraction": sipm_zero / total,
                "sipm_zero_wilson95_low": sipm_ci[0],
                "sipm_zero_wilson95_high": sipm_ci[1],
            }
        )
    return rows


def validate_against_summaries(
    finalized_dir: Path,
    groups: dict[tuple[object, ...], list[Event]],
) -> None:
    expected_rows = read_csv(finalized_dir / "configuration_summary.csv")
    expected: dict[tuple[object, ...], dict[str, str]] = {}
    for row in expected_rows:
        key = (
            row["stage"],
            int(row["tile_thickness_mm"]),
            int(row["absorber_transverse_mm"]),
            int(row["x_mm"]),
            int(row["y_mm"]),
        )
        expected[key] = row
    if set(expected) != set(groups):
        raise ValueError("event groups do not match finalized configuration summary")
    for key, events in groups.items():
        generated, scintillation, sipm = sums(events)
        row = expected[key]
        comparisons = {
            "events": len(events),
            "generated_optical_photons": generated,
            "scintillation_photons": scintillation,
            "sipm_detected_photons": sipm,
            "primary_neutron_interaction_events": sum(
                event.interacted for event in events
            ),
            "primary_neutron_elastic_count": sum(event.elastic for event in events),
            "primary_neutron_inelastic_count": sum(
                event.inelastic for event in events
            ),
            "primary_neutron_capture_count": sum(event.capture for event in events),
        }
        for field, value in comparisons.items():
            if not math.isclose(float(row[field]), float(value), rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError(f"event data disagree with {field} summary for {key}")


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(output_dir: Path) -> None:
    with (output_dir / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(output_dir.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


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
    campaign_dir = args.campaign_dir.expanduser().resolve()
    finalized_dir = args.finalized_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else finalized_dir / "analysis"
    )
    try:
        bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
        verify_finalized_checksums(finalized_dir)
        report = load_json(finalized_dir / "validation_report.json")
        if report.get("valid") is not True:
            raise ValueError("finalized validation report is not valid")
        if (
            report.get("campaign_id") != bundle.campaign_id
            or report.get("plan_hash") != bundle.plan_hash
            or report.get("git_commit") != bundle.git_commit
            or report.get("environment_identity")
            != bundle.environment.get("identity_hash")
        ):
            raise ValueError("finalized validation identity does not match campaign")
        analysis_config = load_json(finalized_dir / "analysis_config.json")
        if (
            analysis_config.get("campaign_id") != bundle.campaign_id
            or analysis_config.get("plan_hash") != bundle.plan_hash
            or analysis_config.get("root_tree") != "scan"
        ):
            raise ValueError("finalized analysis configuration identity mismatch")
        bootstrap = analysis_config.get("bootstrap")
        if not isinstance(bootstrap, dict):
            raise ValueError("finalized analysis_config has no bootstrap definition")
        bootstrap_seed = int(bootstrap["seed"])
        resamples = int(bootstrap["resamples"])
        if resamples <= 0 or bootstrap_seed < 0:
            raise ValueError("invalid pinned bootstrap seed or resample count")
        task_rows = read_tsv(finalized_dir / "task_index.tsv")
        fixture_dir = (
            args.event_fixture_dir.expanduser().resolve()
            if args.event_fixture_dir is not None
            else None
        )
        groups = load_events(campaign_dir, task_rows, fixture_dir)
        validate_against_summaries(finalized_dir, groups)
        ratios = ratio_rows(
            groups, bootstrap_seed=bootstrap_seed, resamples=resamples
        )
        fractions = fraction_rows(groups)
        convergence = absorber_convergence_rows(
            groups, bootstrap_seed=bootstrap_seed, resamples=resamples
        )
        block_events = (
            args.production_block_events
            if args.production_block_events is not None
            else int(bundle.manifest["events_per_task"])
        )
        if block_events <= 0:
            raise ValueError("--production-block-events must be positive")
        production_statistics = production_statistics_recommendation(
            ratios,
            stage=str(bundle.manifest["stage"]),
            block_events=block_events,
        )
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            write_csv(temp_dir / "thickness_ratios.csv", RATIO_FIELDS, ratios)
            write_csv(temp_dir / "configuration_intervals.csv", FRACTION_FIELDS, fractions)
            write_csv(
                temp_dir / "absorber_convergence.csv",
                ABSORBER_CONVERGENCE_FIELDS,
                convergence,
            )
            atomic_write_json(
                temp_dir / "production_statistics.json", production_statistics
            )
            completed_config = {
                **analysis_config,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "campaign_directory": str(campaign_dir),
                "finalized_directory": str(finalized_dir),
                "event_source": "fixture_csv" if fixture_dir is not None else "audited_root_scan_tree",
                "accepted_statistical_evidence": (
                    report.get("accepted_statistical_evidence") is True
                    and fixture_dir is None
                ),
                "bootstrap": {**bootstrap, "status": "complete"},
                "outputs": {
                    "thickness_ratios": "thickness_ratios.csv",
                    "configuration_intervals": "configuration_intervals.csv",
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
        print(f"Cannot analyze realistic-neutron campaign: {exc}", file=sys.stderr)
        return 1

    print(f"Analyzed {len(task_rows)} tasks into {output_dir}")
    print(f"Bootstrap: {resamples} resamples, seed {bootstrap_seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
