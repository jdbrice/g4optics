#!/usr/bin/env python3
"""Deterministic end-to-end checks for steel-module analysis-v2."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import analyze_steel_module_campaign as v1_analyzer
from check_steel_module_campaign_infrastructure import (
    assert_checksum_manifest,
    empty_summary_report,
    finalize_fixture,
    generate,
    make_production_fixture,
    read_tasks,
    run,
)
from realistic_neutron_event_audit import SIPM_SENSOR_FIELDS
from steel_module_campaign_lib import resolve_campaign_path, sha256_file


LAYOUTS = ("back-center", "edge-center", "back-four")
THICKNESSES_MM = (4, 24)
ABSORBERS_MM = (200, 300, 500)
EVENTS_PER_BLOCK = 25
BLOCKS = 4
CORE_OUTPUT_NAMES = {
    "configuration_estimates.csv",
    "distribution_diagnostics.csv",
    "response_pathway.csv",
    "seed_block_stability.csv",
    "pooled_production.csv",
    "standardized_response.csv",
    "primary_contrasts.csv",
    "secondary_contrasts.csv",
    "absorber_equivalence.csv",
    "production_sizing_v2.json",
    "summary.md",
    "analysis_config.json",
    "SHA256SUMS",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def value(row: Mapping[str, str], *names: str) -> str:
    """Read one semantic field while keeping checker/schema coupling localized."""
    for name in names:
        if name in row:
            return row[name]
    raise AssertionError(f"missing expected field {names!r}; got {sorted(row)}")


def truth(row: Mapping[str, str], *names: str) -> bool:
    return value(row, *names).strip().lower() in {"1", "true", "yes"}


def number(row: Mapping[str, str], *names: str) -> float:
    return float(value(row, *names))


def integer(row: Mapping[str, str], *names: str) -> int:
    return int(float(value(row, *names)))


def configuration_matches(
    row: Mapping[str, str], thickness: int, layout: str, absorber: int
) -> bool:
    return (
        integer(row, "tile_thickness_mm") == thickness
        and value(row, "sipm_layout") == layout
        and integer(row, "absorber_transverse_mm") == absorber
    )


def fixture_selection(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(
            ("tile_thickness_mm", "sipm_layout", "absorber_transverse_mm")
        )
        for layout in LAYOUTS:
            for thickness in THICKNESSES_MM:
                for absorber in ABSORBERS_MM:
                    writer.writerow((thickness, layout, absorber))


def fixture_event_rows(task: Mapping[str, str]) -> list[dict[str, int]]:
    """Create 80% SiPM-zero events plus one deterministic tail event per block."""
    thickness_scale = {4: 1, 24: 5}[int(task["tile_thickness_mm"])]
    absorber_scale = {200: 98, 300: 92, 500: 100}[
        int(task["absorber_transverse_mm"])
    ]
    collection_scale = {"back-center": 1, "edge-center": 2, "back-four": 4}[
        task["sipm_layout"]
    ]
    generated = 100 * thickness_scale * absorber_scale
    scintillation = generated * 4 // 5
    rows: list[dict[str, int]] = []
    for index in range(EVENTS_PER_BLOCK):
        positive = index >= 20
        sipm = generated * collection_scale // 100 if positive else 0
        if index == EVENTS_PER_BLOCK - 1:
            sipm *= 10
        sensor_values = [sipm, 0, 0, 0]
        if task["sipm_layout"] == "back-four":
            assert sipm % 4 == 0
            sensor_values = [sipm // 4] * 4
        row = {field: 0 for field in v1_analyzer.EVENT_FIELDS}
        row.update(
            {
                "generated_optical_photons": generated,
                "scintillation_photons": scintillation,
                "sipm_detected_photons": sipm,
                "primary_neutron_elastic_count": int(positive),
            }
        )
        row.update(dict(zip(SIPM_SENSOR_FIELDS, sensor_values)))
        rows.append(row)
    return rows


def fixture_report(task: Mapping[str, str]) -> dict[str, int | float]:
    rows = fixture_event_rows(task)
    report = empty_summary_report()
    generated = sum(row["generated_optical_photons"] for row in rows)
    scintillation = sum(row["scintillation_photons"] for row in rows)
    sipm = sum(row["sipm_detected_photons"] for row in rows)
    interactions = sum(
        bool(
            row["primary_neutron_elastic_count"]
            or row["primary_neutron_inelastic_count"]
            or row["primary_neutron_capture_count"]
        )
        for row in rows
    )
    report.update(
        {
            "events": EVENTS_PER_BLOCK,
            "committed_events": EVENTS_PER_BLOCK,
            "shoot_position_events": EVENTS_PER_BLOCK,
            "primary_energy_events": EVENTS_PER_BLOCK,
            "generated_optical_photons": generated,
            "scintillation_photons": scintillation,
            "sipm_detected_photons": sipm,
            "cerenkov_photons": generated - scintillation,
            "steel_edep_nonzero_events": interactions,
            "primary_neutron_interaction_events": interactions,
            "primary_neutron_elastic_count": interactions,
            "primary_neutron_elastic_events": interactions,
            "charged_tile_entry_events": interactions,
            "charged_tile_entry_count": interactions,
            "tile_edep_nonzero_events": interactions,
            "generated_optical_zero_events": sum(
                row["generated_optical_photons"] == 0 for row in rows
            ),
            "scintillation_zero_events": sum(
                row["scintillation_photons"] == 0 for row in rows
            ),
            "sipm_detected_zero_events": sum(
                row["sipm_detected_photons"] == 0 for row in rows
            ),
            "steel_edep_sum_mev": float(interactions),
            "tile_edep_sum_mev": 0.5 * interactions,
        }
    )
    for field in SIPM_SENSOR_FIELDS:
        report[field] = sum(row[field] for row in rows)
    return report


def write_event_fixtures(directory: Path, tasks: Sequence[Mapping[str, str]]) -> None:
    directory.mkdir(parents=True)
    for task in tasks:
        path = directory / f'{task["logical_task_id"]}.csv'
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=v1_analyzer.EVENT_FIELDS)
            writer.writeheader()
            writer.writerows(fixture_event_rows(task))


def find_row(
    rows: Iterable[dict[str, str]],
    *,
    thickness: int,
    layout: str,
    absorber: int,
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if configuration_matches(row, thickness, layout, absorber)
    ]
    assert len(matches) == 1, (thickness, layout, absorber, len(matches))
    return matches[0]


def sizing_entries(document: Any) -> list[Mapping[str, Any]]:
    """Locate terminal target projections without fixing their container schema."""
    found: list[Mapping[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            has_target = any(
                name in node
                for name in (
                    "target_relative_half_width",
                    "target_precision",
                    "precision_target",
                )
            )
            has_projection = any(
                name in node
                for name in (
                    "rounded_events_per_configuration",
                    "events_per_configuration",
                    "recommended_events_per_configuration",
                    "projected_events_before_safety_factor",
                    "projected_events_before_safety_factor_per_endpoint_total",
                )
            )
            if has_target and has_projection:
                found.append(node)
                return
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(document)
    return found


def replace_checksum_entry(manifest: Path, name: str, digest: str) -> None:
    lines = manifest.read_text(encoding="utf-8").splitlines()
    replaced = False
    output: list[str] = []
    for line in lines:
        _old_digest, recorded_name = line.split("  ", maxsplit=1)
        if recorded_name == name:
            output.append(f"{digest}  {name}")
            replaced = True
        else:
            output.append(line)
    assert replaced, f"checksum manifest does not record {name}"
    manifest.write_text("\n".join(output) + "\n", encoding="utf-8")


def assert_no_partial_output(finalized: Path) -> None:
    assert not (finalized / "analysis-v2").exists()
    assert not list(finalized.glob(".analysis-v2.*"))


def assert_core_outputs(output: Path) -> None:
    assert {path.name for path in output.iterdir() if path.is_file()} == CORE_OUTPUT_NAMES
    assert_checksum_manifest(output)

    configurations = read_csv(output / "configuration_estimates.csv")
    assert len(configurations) == 18
    reference = find_row(
        configurations, thickness=4, layout="back-center", absorber=500
    )
    assert integer(reference, "events", "incident_events") == 100
    assert math.isclose(
        number(
            reference,
            "generated_optical_photons_per_neutron",
            "generated_optical_mean",
            "generated_optical_per_neutron",
        ),
        10_000.0,
    )
    assert math.isclose(
        number(
            reference,
            "scintillation_photons_per_neutron",
            "scintillation_mean",
            "scintillation_per_neutron",
        ),
        8_000.0,
    )
    assert math.isclose(
        number(
            reference,
            "observed_net_sipm_photons_per_neutron",
            "sipm_detected_mean",
            "observed_net_response",
        ),
        56.0,
    )
    assert math.isclose(
        number(reference, "sipm_zero_fraction", "sipm_detected_zero_fraction"),
        0.8,
    )
    assert integer(reference, "sensor_count") == 1
    assert math.isclose(number(reference, "active_area_mm2"), 5.76)
    assert math.isclose(
        number(reference, "observed_net_per_installed_sensor"), 56.0
    )
    assert math.isclose(
        number(reference, "observed_net_per_active_area_mm2"), 56.0 / 5.76
    )
    back_four_configuration = find_row(
        configurations, thickness=4, layout="back-four", absorber=500
    )
    assert integer(back_four_configuration, "sensor_count") == 4
    assert math.isclose(number(back_four_configuration, "active_area_mm2"), 23.04)
    assert math.isclose(
        number(back_four_configuration, "observed_net_per_installed_sensor"),
        56.0,
    )
    assert math.isclose(
        number(back_four_configuration, "observed_net_per_active_area_mm2"),
        224.0 / 23.04,
    )

    distributions = read_csv(output / "distribution_diagnostics.csv")
    assert len(distributions) == 18 * 3
    reference_distributions = [
        row
        for row in distributions
        if configuration_matches(row, 4, "back-center", 500)
    ]
    assert len(reference_distributions) == 3
    for metric, expected in (("generated", 10_000.0), ("scintillation", 8_000.0)):
        row = next(
            item
            for item in reference_distributions
            if value(item, "metric", "quantity") == metric
        )
        assert integer(row, "events") == 100
        assert integer(row, "positive_events") == 100
        assert math.isclose(number(row, "zero_fraction"), 0.0)
        for field in ("p50", "p75", "p90", "p95", "p99", "max"):
            assert math.isclose(number(row, field), expected)
        assert math.isclose(number(row, "positive_mean"), expected)
        assert math.isclose(number(row, "positive_rms"), expected)
        assert math.isclose(number(row, "positive_population_stddev"), 0.0)
        assert math.isclose(number(row, "positive_standard_error"), 0.0)
        assert math.isclose(number(row, "top_1pct_sum_fraction"), 0.01)
        assert math.isclose(number(row, "top_5pct_sum_fraction"), 0.05)
    sipm = next(
        row
        for row in reference_distributions
        if value(row, "metric", "quantity")
        in {"sipm", "sipm_detected", "sipm_detected_photons"}
    )
    assert integer(sipm, "events", "all_event_count") == 100
    assert integer(sipm, "positive_events", "positive_event_count") == 20
    assert math.isclose(number(sipm, "zero_fraction"), 0.8)
    assert math.isclose(number(sipm, "max", "maximum"), 1_000.0)
    expected_quantiles = {
        "p50": 0.0,
        "p75": 0.0,
        "p90": 100.0,
        "p95": 100.0,
        "p99": 1_000.0,
    }
    for field, expected in expected_quantiles.items():
        assert math.isclose(number(sipm, field), expected)
    assert math.isclose(number(sipm, "positive_mean"), 280.0)
    assert math.isclose(number(sipm, "positive_rms"), math.sqrt(208_000.0))
    assert math.isclose(number(sipm, "positive_population_stddev"), 360.0)
    assert math.isclose(
        number(sipm, "positive_standard_error"), 360.0 / math.sqrt(20.0)
    )
    for field, expected in {
        "positive_p50": 100.0,
        "positive_p75": 100.0,
        "positive_p90": 1_000.0,
        "positive_p95": 1_000.0,
        "positive_p99": 1_000.0,
    }.items():
        assert math.isclose(number(sipm, field), expected)
    assert math.isclose(
        number(
            sipm,
            "top_1pct_sum_fraction",
            "top_1pct_share",
            "top_1_percent_share",
        ),
        1_000 / 5_600,
        rel_tol=1e-12,
    )
    assert math.isclose(
        number(
            sipm,
            "top_5pct_sum_fraction",
            "top_5pct_share",
            "top_5_percent_share",
        ),
        4_100 / 5_600,
        rel_tol=1e-12,
    )

    pathways = read_csv(output / "response_pathway.csv")
    assert len(pathways) == 18
    pathway = find_row(pathways, thickness=4, layout="back-center", absorber=500)
    assert integer(pathway, "incident_events", "events") == 100
    assert integer(pathway, "interaction_events", "primary_interaction_events") == 20
    assert integer(pathway, "generated_positive_events") == 100
    assert integer(pathway, "sipm_positive_events") == 20
    assert math.isclose(number(pathway, "interaction_fraction"), 0.2)
    assert math.isclose(
        number(
            pathway,
            "generated_positive_given_interaction",
            "p_generated_positive_given_interaction",
        ),
        1.0,
    )
    assert math.isclose(
        number(
            pathway,
            "sipm_positive_given_generated",
            "sipm_positive_given_generated_positive",
            "p_sipm_positive_given_generated_positive",
        ),
        0.2,
    )
    assert truth(pathway, "generated_positive_given_interaction_valid")
    assert truth(pathway, "sipm_positive_given_generated_valid")
    assert math.isclose(number(pathway, "scintillation_per_interaction"), 8_000.0)
    assert truth(pathway, "scintillation_per_interaction_valid")
    assert math.isclose(number(pathway, "sipm_per_generated_positive_event"), 56.0)
    assert truth(pathway, "sipm_per_generated_positive_event_valid")
    assert math.isclose(number(pathway, "sipm_per_sipm_positive_event"), 280.0)
    assert truth(pathway, "sipm_per_sipm_positive_event_valid")

    block_rows = read_csv(output / "seed_block_stability.csv")
    assert len(block_rows) >= 18 * BLOCKS * 2
    configuration_block_rows = [
        row
        for row in block_rows
        if value(row, "scope") == "configuration"
    ]
    primary_block_rows = [
        row for row in block_rows if value(row, "scope") == "primary-contrast"
    ]
    assert len(configuration_block_rows) == 18 * BLOCKS * 2
    assert len(primary_block_rows) == 24 + 3 * 8
    modes = {
        value(row, "estimate_variant", "estimate_kind", "mode", "stability_kind")
        for row in configuration_block_rows
    }
    assert modes == {"single-block", "leave-one-block-out"}
    stable_reference_blocks = [
        row
        for row in configuration_block_rows
        if configuration_matches(row, 4, "back-center", 500)
    ]
    assert len(stable_reference_blocks) == BLOCKS * 2
    for row in stable_reference_blocks:
        assert math.isclose(number(row, "net_relative_shift"), 0.0)
        assert math.isclose(number(row, "net_absolute_relative_shift"), 0.0)
        assert truth(row, "net_shift_valid")
        assert truth(row, "net_within_10pct")
        assert truth(row, "net_within_20pct")
        assert math.isclose(number(row, "generated_zero_fraction"), 0.0)
        assert not truth(row, "generated_zero_fraction_shift_valid")
        assert math.isclose(number(row, "scintillation_zero_fraction"), 0.0)
        assert not truth(row, "scintillation_zero_fraction_shift_valid")
        assert math.isclose(number(row, "sipm_zero_fraction"), 0.8)
        assert math.isclose(
            number(row, "sipm_zero_fraction_relative_shift"), 0.0
        )
        assert truth(row, "sipm_zero_fraction_shift_valid")
        assert truth(row, "sipm_zero_fraction_within_10pct")
        assert truth(row, "sipm_zero_fraction_within_20pct")
    for row in primary_block_rows:
        assert math.isclose(number(row, "contrast_relative_shift"), 0.0)
        assert math.isclose(number(row, "contrast_absolute_relative_shift"), 0.0)
        assert truth(row, "contrast_shift_valid")
        assert truth(row, "contrast_within_10pct")
        assert truth(row, "contrast_within_20pct")

    pooled = read_csv(output / "pooled_production.csv")
    # The endpoint-only fixture has 2 thicknesses x 3 absorbers.  The real
    # 30-configuration pilot adds four intermediate 500 mm rows (10 total).
    assert len(pooled) == 6
    assert {
        (
            integer(row, "tile_thickness_mm"),
            integer(row, "absorber_transverse_mm"),
        )
        for row in pooled
    } == {
        (thickness, absorber)
        for thickness in THICKNESSES_MM
        for absorber in ABSORBERS_MM
    }
    pooled_reference = next(
        row
        for row in pooled
        if integer(row, "tile_thickness_mm") == 4
        and integer(row, "absorber_transverse_mm") == 500
    )
    assert integer(pooled_reference, "events", "pooled_events") == 300
    assert math.isclose(
        number(
            pooled_reference,
            "pooled_scintillation_photons_per_neutron",
            "pooled_scintillation_mean",
            "scintillation_mean",
            "pooled_scintillation_per_neutron",
        ),
        8_000.0,
    )
    assert math.isclose(
        number(pooled_reference, "pooled_generated_photons_per_neutron"),
        10_000.0,
    )
    for metric, expected in (("generated", 10_000.0), ("scintillation", 8_000.0)):
        assert math.isclose(number(pooled_reference, f"{metric}_ci95_low"), expected)
        assert math.isclose(number(pooled_reference, f"{metric}_ci95_high"), expected)
        assert integer(pooled_reference, f"{metric}_valid_resamples") == 100

    standardized = read_csv(output / "standardized_response.csv")
    assert len(standardized) == 18
    standardized_reference = find_row(
        standardized, thickness=4, layout="back-center", absorber=500
    )
    assert integer(standardized_reference, "direct_observed_events") == 100
    assert integer(standardized_reference, "layout_collection_events") == 100
    assert integer(standardized_reference, "pooled_production_events_total") == 300
    assert math.isclose(
        number(
            standardized_reference,
            "direct_observed_net",
            "observed_net_response",
        ),
        56.0,
    )
    assert math.isclose(
        number(standardized_reference, "standardized_net", "standardized_response"),
        56.0,
    )
    assert integer(standardized_reference, "sensor_count") == 1
    assert math.isclose(number(standardized_reference, "active_area_mm2"), 5.76)
    assert math.isclose(
        number(standardized_reference, "direct_net_per_installed_sensor"), 56.0
    )
    assert math.isclose(
        number(standardized_reference, "direct_net_per_active_area_mm2"),
        56.0 / 5.76,
    )
    assert math.isclose(
        number(standardized_reference, "standardized_net_per_installed_sensor"),
        56.0,
    )
    assert math.isclose(
        number(standardized_reference, "standardized_net_per_active_area_mm2"),
        56.0 / 5.76,
    )
    standardized_back_four = find_row(
        standardized, thickness=4, layout="back-four", absorber=500
    )
    assert integer(standardized_back_four, "direct_observed_events") == 100
    assert integer(standardized_back_four, "layout_collection_events") == 100
    assert integer(standardized_back_four, "pooled_production_events_total") == 300
    assert integer(standardized_back_four, "sensor_count") == 4
    assert math.isclose(number(standardized_back_four, "active_area_mm2"), 23.04)
    assert math.isclose(
        number(standardized_back_four, "direct_net_per_installed_sensor"), 56.0
    )
    assert math.isclose(
        number(standardized_back_four, "direct_net_per_active_area_mm2"),
        224.0 / 23.04,
    )
    assert math.isclose(
        number(standardized_back_four, "standardized_net_per_installed_sensor"),
        56.0,
    )
    assert math.isclose(
        number(standardized_back_four, "standardized_net_per_active_area_mm2"),
        224.0 / 23.04,
    )

    contrasts = read_csv(output / "primary_contrasts.csv")
    assert len(contrasts) == 4
    assert sum(value(row, "family") == "pooled-production" for row in contrasts) == 1
    assert sum(value(row, "family") == "observed-net" for row in contrasts) == 3
    assert all(
        integer(
            row,
            "reference_tile_thickness_mm",
            "denominator_tile_thickness_mm",
        )
        == 4
        and integer(
            row,
            "compared_tile_thickness_mm",
            "numerator_tile_thickness_mm",
        )
        == 24
        for row in contrasts
    )
    assert all(math.isclose(number(row, "ratio", "point_ratio"), 5.0) for row in contrasts)
    pooled_contrast = next(
        row for row in contrasts if value(row, "family") == "pooled-production"
    )
    assert integer(pooled_contrast, "leave_one_block_out_attempts") == 24
    assert integer(pooled_contrast, "leave_one_block_out_valid_evaluations") == 24
    assert integer(pooled_contrast, "leave_one_block_out_evaluations") == 24
    assert math.isclose(
        number(pooled_contrast, "max_leave_one_block_out_relative_shift"), 0.0
    )
    assert truth(pooled_contrast, "block_shift_within_10pct")
    assert truth(pooled_contrast, "block_shift_within_20pct")
    assert all(
        integer(row, "leave_one_block_out_attempts") == 8
        and integer(row, "leave_one_block_out_valid_evaluations") == 8
        and integer(row, "leave_one_block_out_evaluations") == 8
        for row in contrasts
        if value(row, "family") == "observed-net"
    )
    for row in contrasts:
        assert math.isclose(
            number(row, "max_leave_one_block_out_relative_shift"), 0.0
        )
        assert truth(row, "block_shift_within_10pct")
        assert truth(row, "block_shift_within_20pct")

    secondary = read_csv(output / "secondary_contrasts.csv")
    assert len(secondary) == 28
    family_counts: dict[str, int] = {}
    for row in secondary:
        family = value(row, "contrast_family")
        family_counts[family] = family_counts.get(family, 0) + 1
    assert family_counts == {
        "pooled-production-thickness-over-4": 2,
        "observed-thickness-over-4": 9,
        "standardized-thickness-over-4": 3,
        "pooled-production-adjacent-thickness": 2,
        "observed-adjacent-thickness": 9,
        "standardized-adjacent-thickness": 3,
    }
    assert all(not truth(row, "controls_production_sizing") for row in secondary)

    absorber_rows = read_csv(output / "absorber_equivalence.csv")
    pooled_scintillation = [
        row
        for row in absorber_rows
        if value(row, "metric", "quantity")
        in {"scintillation", "pooled_scintillation", "pooled_scintillation_production"}
        and value(row, "scope") == "pooled-production"
    ]
    assert pooled_scintillation
    within_five = [
        row
        for row in pooled_scintillation
        if integer(row, "candidate_absorber_transverse_mm", "candidate_absorber_mm")
        == 200
    ]
    within_ten = [
        row
        for row in pooled_scintillation
        if integer(row, "candidate_absorber_transverse_mm", "candidate_absorber_mm")
        == 300
    ]
    assert within_five and within_ten
    assert all(truth(row, "equivalent_within_5pct") for row in within_five)
    assert all(truth(row, "equivalent_within_10pct") for row in within_five)
    assert all(truth(row, "difference_detected") for row in within_five)
    assert all(math.isclose(number(row, "ratio"), 0.98) for row in within_five)
    assert all(value(row, "review_status_5pct") == "equivalent" for row in within_five)
    assert all(value(row, "review_status_10pct") == "equivalent" for row in within_five)
    assert all(not truth(row, "equivalent_within_5pct") for row in within_ten)
    assert all(truth(row, "equivalent_within_10pct") for row in within_ten)
    assert all(truth(row, "difference_detected") for row in within_ten)
    assert all(math.isclose(number(row, "ratio"), 0.92) for row in within_ten)
    assert all(
        value(row, "review_status_5pct")
        == "difference_detected_not_equivalent"
        for row in within_ten
    )
    assert all(value(row, "review_status_10pct") == "equivalent" for row in within_ten)

    sizing = json.loads(
        (output / "production_sizing_v2.json").read_text(encoding="utf-8")
    )
    assert sizing["automatic_acceptance"] is False
    assert sizing["events_per_execution_block"] == 250
    assert sizing["minimum_blocks_per_configuration"] == 4
    assert sizing["global_recommendation"] is None
    entries = sizing_entries(sizing)
    assert len(entries) == 8
    for entry in entries:
        assert float(
            entry.get(
                "safety_factor", sizing.get("safety_factor", 0.0)
            )
        ) == 1.25
        blocks = int(
            entry.get(
                "blocks_per_configuration",
                entry.get("recommended_blocks_per_configuration", 0),
            )
        )
        events = int(
            entry.get(
                "events_per_configuration",
                entry.get(
                    "recommended_events_per_configuration",
                    entry.get("rounded_events_per_configuration", 0),
                ),
            )
        )
        assert blocks >= 4
        assert events == blocks * 250
        point = float(entry["observed_ratio"])
        low = float(entry["observed_ci95_low"])
        high = float(entry["observed_ci95_high"])
        target = float(entry["target_relative_half_width"])
        current = int(entry["current_events_per_endpoint_total"])
        relative_half_width = (high - low) / (2.0 * abs(point))
        assert math.isclose(
            float(entry["observed_relative_half_width"]), relative_half_width
        )
        projected = max(
            current,
            math.ceil(
                current * (relative_half_width / target) ** 2
                - 1e-12
                * max(1.0, abs(current * (relative_half_width / target) ** 2))
            ),
        )
        buffered = math.ceil(
            projected * 1.25 - 1e-12 * max(1.0, abs(projected * 1.25))
        )
        assert (
            int(entry["projected_events_before_safety_factor_per_endpoint_total"])
            == projected
        )
        assert int(entry["events_after_safety_factor_per_endpoint_total"]) == buffered
        layout_count = int(entry["layout_configurations_per_endpoint"])
        expected_per_configuration = max(
            4 * 250, 250 * math.ceil(buffered / (layout_count * 250))
        )
        assert events == expected_per_configuration
        assert (
            int(entry["recommended_events_per_endpoint_total"])
            == layout_count * events
        )
        assert (
            int(entry["recommended_events_for_both_endpoints"])
            == 2 * layout_count * events
        )

    config = json.loads((output / "analysis_config.json").read_text(encoding="utf-8"))
    assert config["accepted_statistical_evidence"] is False
    assert config["event_source"] == "fixture_csv"
    assert config["analyzer"]["sha256"] == sha256_file(
        Path(__file__).with_name("analyze_steel_module_campaign_v2.py")
    )
    reconciliation = config.get("v1_reconciliation", {})
    assert reconciliation.get("status") == "passed"


def assert_plot_outputs(repo_root: Path, analysis_output: Path) -> None:
    plotter = repo_root / "hpc/osc/plot_steel_module_analysis_v2.py"
    if not plotter.exists():
        print("steel-module analysis-v2 plots: SKIP (plotter is absent)")
        return
    if importlib.util.find_spec("matplotlib") is None:
        print("steel-module analysis-v2 plots: SKIP (matplotlib is unavailable)")
        return
    run(
        [sys.executable, str(plotter), "--analysis-dir", str(analysis_output)],
        cwd=repo_root,
    )
    figures = analysis_output.with_name("analysis-v2-figures")
    assert_checksum_manifest(figures)
    pngs = sorted(figures.glob("*.png"))
    pdfs = sorted(figures.glob("*.pdf"))
    assert len(pngs) == 5 and len(pdfs) == 5
    assert all(path.stat().st_size > 0 for path in (*pngs, *pdfs))
    assert any(path.suffix == ".json" for path in figures.iterdir())

    tampered = analysis_output.with_name("analysis-v2-tampered")
    shutil.copytree(analysis_output, tampered)
    with (tampered / "configuration_estimates.csv").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("\n")
    rejected = run(
        [sys.executable, str(plotter), "--analysis-dir", str(tampered)],
        cwd=repo_root,
        expect_success=False,
    )
    assert "checksum mismatch" in rejected.stdout


def main() -> int:
    missing_dependencies = [
        name for name in ("numpy", "uproot") if importlib.util.find_spec(name) is None
    ]
    if missing_dependencies:
        print(
            "Cannot check steel-module analysis-v2: activate the analysis "
            "environment with NumPy and uproot (missing: "
            + ", ".join(missing_dependencies)
            + ")",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parents[2]
    analyzer_path = repo_root / "hpc/osc/analyze_steel_module_campaign_v2.py"
    if not analyzer_path.is_file():
        raise SystemExit(f"missing analysis-v2 implementation: {analyzer_path}")

    with tempfile.TemporaryDirectory(prefix="steel-module-analysis-v2-check-") as raw:
        scratch = Path(raw)
        selection = scratch / "configurations.tsv"
        fixture_selection(selection)
        campaign = scratch / "campaign"
        generate(
            repo_root,
            campaign,
            stage="convergence-pilot",
            extra=(
                "--events",
                str(EVENTS_PER_BLOCK),
                "--blocks",
                str(BLOCKS),
                "--configurations-tsv",
                str(selection),
            ),
        )
        tasks = read_tasks(campaign / "tasks.tsv")
        assert len(tasks) == 18 * BLOCKS
        _, root_reports, _ = make_production_fixture(
            repo_root,
            campaign,
            scratch,
            report_factory=fixture_report,
        )
        finalized = campaign / "finalized"
        finalize_fixture(campaign, root_reports, finalized)
        fixtures = scratch / "event-fixtures"
        write_event_fixtures(fixtures, tasks)

        # Existing v1 evidence makes v2 reconciliation a required tested path.
        v1_command = [
            sys.executable,
            "hpc/osc/analyze_steel_module_campaign.py",
            "--campaign-dir",
            str(campaign),
            "--event-fixture-dir",
            str(fixtures),
            "--testing-allow-event-fixtures",
            "--production-block-events",
            "250",
        ]
        run(v1_command, cwd=repo_root)
        assert_checksum_manifest(finalized / "analysis")

        base_command = [
            sys.executable,
            "hpc/osc/analyze_steel_module_campaign_v2.py",
            "--campaign-dir",
            str(campaign),
            "--event-fixture-dir",
            str(fixtures),
            "--testing-allow-event-fixtures",
            "--testing-allow-dirty-analysis",
            "--production-block-events",
            "250",
        ]

        missing_fixture_opt_in = run(
            [
                sys.executable,
                "hpc/osc/analyze_steel_module_campaign_v2.py",
                "--campaign-dir",
                str(campaign),
                "--event-fixture-dir",
                str(fixtures),
                "--testing-allow-dirty-analysis",
            ],
            cwd=repo_root,
            expect_success=False,
        )
        assert (
            "requires --testing-allow-event-fixtures" in missing_fixture_opt_in.stdout
        ), missing_fixture_opt_in.stdout
        assert_no_partial_output(finalized)

        missing_fixture_dir = run(
            [
                sys.executable,
                "hpc/osc/analyze_steel_module_campaign_v2.py",
                "--campaign-dir",
                str(campaign),
                "--testing-allow-event-fixtures",
                "--testing-allow-dirty-analysis",
            ],
            cwd=repo_root,
            expect_success=False,
        )
        assert "requires --event-fixture-dir" in missing_fixture_dir.stdout, (
            missing_fixture_dir.stdout
        )
        assert_no_partial_output(finalized)

        dirty_mode_without_fixture = run(
            [
                sys.executable,
                "hpc/osc/analyze_steel_module_campaign_v2.py",
                "--campaign-dir",
                str(campaign),
                "--testing-allow-dirty-analysis",
            ],
            cwd=repo_root,
            expect_success=False,
        )
        assert "test-fixture-only" in dirty_mode_without_fixture.stdout
        assert_no_partial_output(finalized)

        task_index = read_tasks(finalized / "task_index.tsv")
        audited_root = resolve_campaign_path(campaign, task_index[0]["root"])
        original_root = audited_root.read_bytes()
        audited_root.write_bytes(original_root + b"tampered\n")
        root_rejected = run(base_command, cwd=repo_root, expect_success=False)
        assert "audited ROOT checksum mismatch" in root_rejected.stdout, (
            root_rejected.stdout
        )
        assert_no_partial_output(finalized)
        audited_root.write_bytes(original_root)

        validation_path = finalized / "validation_report.json"
        checksum_path = finalized / "SHA256SUMS"
        original_validation = validation_path.read_bytes()
        original_checksums = checksum_path.read_bytes()
        validation = json.loads(original_validation)
        validation["plan_hash"] = "0" * 64
        validation_path.write_text(
            json.dumps(validation, indent=2) + "\n", encoding="utf-8"
        )
        replace_checksum_entry(
            checksum_path,
            "validation_report.json",
            sha256_file(validation_path),
        )
        identity_rejected = run(base_command, cwd=repo_root, expect_success=False)
        assert "finalized validation identity mismatch: plan_hash" in identity_rejected.stdout
        assert_no_partial_output(finalized)
        validation_path.write_bytes(original_validation)
        checksum_path.write_bytes(original_checksums)
        assert_checksum_manifest(finalized)

        run(base_command, cwd=repo_root)
        output = finalized / "analysis-v2"
        assert_core_outputs(output)

        overwrite = run(base_command, cwd=repo_root, expect_success=False)
        assert "refusing to overwrite analysis-v2 directory" in overwrite.stdout
        assert_core_outputs(output)
        assert_plot_outputs(repo_root, output)

    print(
        "steel-module analysis-v2: PASS "
        "(18 configurations, tail/block diagnostics, pooling, sizing, plots)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
