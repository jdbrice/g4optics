#!/usr/bin/env python3
"""Focused scheduler-free checks for the four-layout analysis and figures."""

from __future__ import annotations

import copy
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

import analyze_steel_module_campaign as v1
import analyze_steel_module_campaign_v2 as v2
import analyze_steel_module_four_layout as four
import check_steel_module_direct_final as original_check
from realistic_neutron_campaign_lib import verify_checksum_manifest
from steel_module_campaign_lib import atomic_write_json


def event(*, generated: int, sensors: tuple[int, int, int, int]) -> v1.Event:
    sipm = sum(sensors)
    return v1.Event(
        generated=generated,
        scintillation=generated,
        sipm=sipm,
        elastic=1 if generated else 0,
        inelastic=0,
        capture=0,
        sensor0=sensors[0],
        sensor1=sensors[1],
        sensor2=sensors[2],
        sensor3=sensors[3],
    )


def loader_sensor_admission_check(root: Path) -> None:
    campaign = root / "loader-campaign"
    fixtures = root / "loader-fixtures"
    root_relative = "roots/edge-two.root"
    root_path = campaign / root_relative
    root_path.parent.mkdir(parents=True)
    fixtures.mkdir()
    root_path.write_bytes(b"edge-two-loader-fixture")

    logical_id = "production-ledge-two-t04-a500-b000"
    task = {
        "logical_task_id": logical_id,
        "root": root_relative,
        "stage": "production",
        "tile_thickness_mm": "4",
        "sipm_layout": "edge-two",
        "absorber_transverse_mm": "500",
        "x_mm": "0",
        "y_mm": "0",
        "events": "1",
        "seed_block": "0",
    }
    audited = {
        logical_id: v1.AuditedRoot(
            path=root_relative,
            sha256=v1.sha256_file(root_path),
            tile_thickness_mm=4,
            sipm_layout="edge-two",
            absorber_transverse_mm=500,
        )
    }

    def write_event(sensors: tuple[int, int, int, int]) -> None:
        values: dict[str, int] = {
            "generated_optical_photons": 10,
            "scintillation_photons": 10,
            "sipm_detected_photons": sum(sensors),
            "primary_neutron_elastic_count": 1,
            "primary_neutron_inelastic_count": 0,
            "primary_neutron_capture_count": 0,
        }
        values.update(dict(zip(v1.SIPM_SENSOR_FIELDS, sensors)))
        four.write_csv(fixtures / f"{logical_id}.csv", v1.EVENT_FIELDS, [values])

    write_event((2, 3, 0, 0))
    loaded = v2.load_events(campaign, [task], fixtures, audited)
    key = ("production", 4, "edge-two", 500, 0, 0)
    assert loaded.groups[key][0].sensors == (2, 3, 0, 0)
    assert v2.sensor_count("edge-two") == 2

    write_event((2, 3, 1, 0))
    try:
        v2.load_events(campaign, [task], fixtures, audited)
    except ValueError as exc:
        assert "edge-two layout has counts in inactive sensor columns" in str(exc)
    else:
        raise AssertionError("edge-two sensor 2 was accepted as active")

    try:
        v1.validate_active_sensor_columns(
            "edge-center", [event(generated=10, sensors=(2, 1, 0, 0))], logical_id
        )
    except ValueError as exc:
        assert "edge-center layout has counts in inactive sensor columns" in str(exc)
    else:
        raise AssertionError("edge-center sensor 1 was accepted as active")


def formal_shape() -> v2.LoadedEvents:
    original = original_check.formal_shape()
    groups = {key: list(events) for key, events in original.groups.items()}
    blocks = {
        key: {block: list(events) for block, events in by_block.items()}
        for key, by_block in original.blocks.items()
    }
    task_ids = dict(original.task_ids)
    template = event(generated=10, sensors=(1, 1, 0, 0))
    for thickness in four.THICKNESSES:
        key = ("production", thickness, "edge-two", 500, 0, 0)
        by_block: dict[int, list[v1.Event]] = {}
        for block in range(40):
            by_block[block] = [template] * 250
            task_ids[(key, block)] = f"fixture-edge-two-{thickness}-{block}"
        blocks[key] = by_block
        groups[key] = [item for values in by_block.values() for item in values]
    return v2.LoadedEvents(groups, blocks, task_ids)


def positive_sensors(layout: str, thickness: int, index: int) -> tuple[int, int, int, int]:
    base = max(1, round((12 + thickness) * (1 + 0.03 * (index % 3))))
    if layout in ("back-center", "edge-center"):
        return (base, 0, 0, 0)
    if layout == "edge-two":
        return (base, base + 3, 0, 0)
    if layout == "back-four":
        return (base, base + 1, base + 2, base + 3)
    raise ValueError(layout)


def synthetic_shape() -> v2.LoadedEvents:
    groups: dict[v1.ConfigurationKey, list[v1.Event]] = {}
    blocks: dict[v1.ConfigurationKey, dict[int, list[v1.Event]]] = {}
    task_ids: dict[tuple[v1.ConfigurationKey, int], str] = {}
    production_offset = {
        "back-center": 0,
        "edge-center": 80,
        "edge-two": 160,
        "back-four": 240,
    }
    for layout in four.LAYOUTS:
        for thickness in four.THICKNESSES:
            values: list[v1.Event] = []
            for index in range(100):
                if index % 5:
                    values.append(event(generated=0, sensors=(0, 0, 0, 0)))
                    continue
                generated = (
                    900
                    + 40 * thickness
                    + production_offset[layout]
                    + 7 * index
                )
                values.append(
                    event(
                        generated=generated,
                        sensors=positive_sensors(layout, thickness, index),
                    )
                )
            key = ("production", thickness, layout, 500, 0, 0)
            groups[key] = values
            blocks[key] = {0: values}
            task_ids[(key, 0)] = f"synthetic-{layout}-{thickness}"
    return v2.LoadedEvents(groups, blocks, task_ids)


def write_core_fixture(
    output: Path,
    *,
    configurations: list[dict[str, object]],
    distributions: list[dict[str, object]],
    pathways: list[dict[str, object]],
    sensors: list[dict[str, object]],
    costs: list[dict[str, object]],
    pooled: list[dict[str, object]],
    standardized: list[dict[str, object]],
    thickness: list[dict[str, object]],
    layouts: list[dict[str, object]],
) -> None:
    output.mkdir()
    four.write_csv(
        output / "configuration_estimates.csv",
        four.CONFIGURATION_FIELDS,
        configurations,
    )
    four.write_csv(
        output / "distribution_diagnostics.csv",
        four.DISTRIBUTION_FIELDS,
        distributions,
    )
    four.write_csv(output / "response_pathway.csv", four.PATHWAY_FIELDS, pathways)
    four.write_csv(
        output / "per_sensor_efficiency.csv", four.PER_SENSOR_FIELDS, sensors
    )
    four.write_csv(
        output / "layout_cost_diagnostics.csv", four.LAYOUT_COST_FIELDS, costs
    )
    four.write_csv(output / "pooled_production.csv", four.POOLED_FIELDS, pooled)
    four.write_csv(
        output / "standardized_response.csv",
        four.STANDARDIZED_FIELDS,
        standardized,
    )
    four.write_csv(
        output / "thickness_ratios.csv", four.THICKNESS_RATIO_FIELDS, thickness
    )
    four.write_csv(output / "layout_ratios.csv", four.LAYOUT_RATIO_FIELDS, layouts)
    (output / "summary.md").write_text(
        four.summary_markdown(configurations, sensors, layouts), encoding="utf-8"
    )
    atomic_write_json(
        output / "analysis_config.json",
        {
            "schema_version": four.SCHEMA_VERSION,
            "accepted_statistical_evidence": True,
            "sample": {
                "tasks": four.TOTAL_TASKS,
                "events": four.TOTAL_EVENTS,
                "configurations": four.TOTAL_CONFIGURATIONS,
                "layouts": list(four.LAYOUTS),
                "sensor_counts": four.SENSOR_COUNTS,
            },
        },
    )
    four.write_checksums(output)


def run_plotter(repo_root: Path, analysis: Path, figures: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "hpc/osc/plot_steel_module_four_layout.py",
            "--analysis-dir",
            str(analysis),
            "--output-dir",
            str(figures),
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-loader-admission-") as raw:
        loader_sensor_admission_check(Path(raw))
    formal = formal_shape()
    four.validate_four_layout_shape(
        formal, total_tasks=four.TOTAL_TASKS, seed_count=2 * four.TOTAL_TASKS
    )
    tampered = copy.deepcopy(formal.blocks)
    tampered[("production", 12, "edge-two", 500, 0, 0)].pop(39)
    try:
        four.validate_four_layout_shape(
            v2.LoadedEvents(formal.groups, tampered, formal.task_ids),
            total_tasks=four.TOTAL_TASKS,
            seed_count=2 * four.TOTAL_TASKS,
        )
    except ValueError as exc:
        assert "block registry" in str(exc)
    else:
        raise AssertionError("incomplete edge-two block registry was accepted")

    loaded = synthetic_shape()
    cache = four.build_bootstrap_cache(loaded.groups, np=np)
    configurations = four.configuration_rows(loaded, cache)
    distributions = v2.distribution_rows(loaded.groups, np=np)
    pathways = v2.pathway_rows(loaded.groups)
    sensors = four.per_sensor_rows(loaded.groups, cache)
    costs = four.layout_cost_rows(loaded.groups, cache)
    pooled, pooled_cache = four.pooled_rows(loaded.groups, cache)
    standardized = four.standardized_rows(loaded.groups, cache, pooled_cache)
    thickness = four.thickness_ratio_rows(loaded.groups, cache)
    layouts = four.layout_ratio_rows(loaded.groups, cache)

    assert len(configurations) == 24
    assert len(distributions) == 72
    assert len(pathways) == 24
    assert len(sensors) == 48
    assert len(costs) == 24
    assert len(pooled) == 6
    assert len(standardized) == 24
    assert len(thickness) == 20
    assert len(layouts) == 36
    four.require_complete_bootstrap(
        [
            *configurations,
            *sensors,
            *costs,
            *pooled,
            *standardized,
            *thickness,
            *layouts,
        ]
    )

    by_layout = {
        row["sipm_layout"]: row
        for row in configurations
        if int(row["tile_thickness_mm"]) == 4
    }
    assert {layout: int(row["sensor_count"]) for layout, row in by_layout.items()} == four.SENSOR_COUNTS
    for layout, row in by_layout.items():
        sensors_count = four.SENSOR_COUNTS[layout]
        assert math.isclose(
            float(row["observed_net_per_installed_sensor"]),
            float(row["observed_net_sipm_photons_per_neutron"]) / sensors_count,
        )
        assert math.isclose(
            float(row["observed_net_per_active_area_mm2"]),
            float(row["observed_net_sipm_photons_per_neutron"])
            / (sensors_count * four.SENSOR_AREA_MM2),
        )

    for layout in ("edge-two", "back-four"):
        selected = [
            row
            for row in sensors
            if row["sipm_layout"] == layout
            and int(row["tile_thickness_mm"]) == 12
        ]
        assert len(selected) == four.SENSOR_COUNTS[layout]
        assert math.isclose(
            sum(float(row["fraction_of_layout_aggregate"]) for row in selected),
            1.0,
            abs_tol=1e-12,
        )

    side = next(
        row
        for row in layouts
        if int(row["tile_thickness_mm"]) == 16
        and row["reference_sipm_layout"] == "edge-center"
        and row["compared_sipm_layout"] == "edge-two"
    )
    assert math.isclose(
        float(side["net_per_installed_sensor_ratio"]),
        float(side["net_ratio"]) / 2,
        rel_tol=0,
        abs_tol=1e-12,
    )
    assert math.isclose(
        float(side["net_per_active_area_ratio"]),
        float(side["net_per_installed_sensor_ratio"]),
        rel_tol=0,
        abs_tol=1e-12,
    )
    assert all(row["production_weighting"] == "equal-four-layout-strata" for row in pooled)
    assert all(
        row["model_role"] == "secondary-production-standardized-diagnostic"
        for row in standardized
    )
    summary = four.summary_markdown(configurations, sensors, layouts)
    assert "288,500" in summary
    assert "edge-two `2`" in summary
    assert "back-four `4`" in summary
    assert "secondary layout-cost diagnostics" in summary

    with tempfile.TemporaryDirectory(prefix="steel-module-four-layout-") as raw:
        scratch = Path(raw)
        analysis = scratch / "analysis"
        figures = scratch / "figures"
        write_core_fixture(
            analysis,
            configurations=configurations,
            distributions=distributions,
            pathways=pathways,
            sensors=sensors,
            costs=costs,
            pooled=pooled,
            standardized=standardized,
            thickness=thickness,
            layouts=layouts,
        )
        recorded = verify_checksum_manifest(analysis, required=four_required_core())
        assert four_required_core().issubset(recorded)
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            plot_status = "plot fixture skipped: matplotlib unavailable"
        else:
            run_plotter(repo_root, analysis, figures)
            figure_files = verify_checksum_manifest(
                figures, required={"figure_provenance.json"}
            )
            assert len([name for name in figure_files if name.endswith(".png")]) == 7
            assert len([name for name in figure_files if name.endswith(".pdf")]) == 7
            assert all((figures / name).stat().st_size > 0 for name in figure_files)
            failed = subprocess.run(
                [
                    sys.executable,
                    "hpc/osc/plot_steel_module_four_layout.py",
                    "--analysis-dir",
                    str(analysis),
                    "--output-dir",
                    str(figures),
                ],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert failed.returncode == 1
            assert "refusing to overwrite" in failed.stderr

            tampered_analysis = scratch / "tampered-analysis"
            shutil.copytree(analysis, tampered_analysis)
            with (tampered_analysis / "configuration_estimates.csv").open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write("tamper\n")
            rejected = subprocess.run(
                [
                    sys.executable,
                    "hpc/osc/plot_steel_module_four_layout.py",
                    "--analysis-dir",
                    str(tampered_analysis),
                    "--output-dir",
                    str(scratch / "tampered-figures"),
                ],
                cwd=repo_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert rejected.returncode == 1
            plot_status = "7 PNG/PDF figure pairs and tamper rejection"

    print(
        "steel-module four-layout analysis: PASS "
        "(24 configurations, 1/1/2/4 sensors, aggregate/per-sensor/per-area, "
        f"individual copies, {plot_status}, no scheduler contact)"
    )
    return 0


def four_required_core() -> set[str]:
    return {
        "configuration_estimates.csv",
        "distribution_diagnostics.csv",
        "response_pathway.csv",
        "per_sensor_efficiency.csv",
        "layout_cost_diagnostics.csv",
        "pooled_production.csv",
        "standardized_response.csv",
        "thickness_ratios.csv",
        "layout_ratios.csv",
        "summary.md",
        "analysis_config.json",
    }


if __name__ == "__main__":
    raise SystemExit(main())
