#!/usr/bin/env python3
"""Focused scheduler-free checks for final direct production analysis."""

from __future__ import annotations

import copy
import math
import tempfile
from pathlib import Path

import numpy as np

import analyze_steel_module_campaign as v1
import analyze_steel_module_campaign_v2 as v2
import analyze_steel_module_direct_final as final
from realistic_neutron_campaign_lib import verify_checksum_manifest
from steel_module_campaign_lib import SIPM_LAYOUTS


def event(*, generated: int, sipm: int, layout: str) -> v1.Event:
    if layout == "back-four":
        base, remainder = divmod(sipm, 4)
        sensors = tuple(base + (index < remainder) for index in range(4))
    else:
        sensors = (sipm, 0, 0, 0)
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


def formal_shape() -> v2.LoadedEvents:
    groups: dict[v1.ConfigurationKey, list[v1.Event]] = {}
    blocks: dict[v1.ConfigurationKey, dict[int, list[v1.Event]]] = {}
    task_ids: dict[tuple[v1.ConfigurationKey, int], str] = {}
    template = event(generated=10, sipm=1, layout="back-center")
    for layout in SIPM_LAYOUTS:
        for thickness in final.THICKNESSES:
            key = ("production", thickness, layout, 500, 0, 0)
            by_block: dict[int, list[v1.Event]] = {}
            for block in final.expected_blocks(layout, thickness):
                by_block[block] = [template] * 250
                task_ids[(key, block)] = f"fixture-{layout}-{thickness}-{block}"
            blocks[key] = by_block
            groups[key] = [item for values in by_block.values() for item in values]
    return v2.LoadedEvents(groups, blocks, task_ids)


def synthetic_shape() -> v2.LoadedEvents:
    groups: dict[v1.ConfigurationKey, list[v1.Event]] = {}
    blocks: dict[v1.ConfigurationKey, dict[int, list[v1.Event]]] = {}
    task_ids: dict[tuple[v1.ConfigurationKey, int], str] = {}
    counts = {"back-center": 60, "edge-center": 40, "back-four": 50}
    offsets = {"back-center": 0, "edge-center": 120, "back-four": 260}
    collection = {"back-center": 0.010, "edge-center": 0.017, "back-four": 0.045}
    for layout in SIPM_LAYOUTS:
        for thickness in final.THICKNESSES:
            values: list[v1.Event] = []
            for index in range(counts[layout]):
                if index % 2:
                    values.append(event(generated=0, sipm=0, layout=layout))
                    continue
                generated = 900 + 45 * thickness + offsets[layout] + 7 * (index % 9)
                fraction = collection[layout] / (1 + 0.015 * thickness)
                values.append(
                    event(
                        generated=generated,
                        sipm=max(1, round(generated * fraction)),
                        layout=layout,
                    )
                )
            key = ("production", thickness, layout, 500, 0, 0)
            groups[key] = values
            blocks[key] = {0: values}
            task_ids[(key, 0)] = f"synthetic-{layout}-{thickness}"
    return v2.LoadedEvents(groups, blocks, task_ids)


def main() -> int:
    formal = formal_shape()
    final.validate_final_shape(formal, total_tasks=914, seed_count=1828)
    tampered_blocks = copy.deepcopy(formal.blocks)
    tampered_blocks[("production", 4, "edge-center", 500, 0, 0)].pop(39)
    try:
        final.validate_final_shape(
            v2.LoadedEvents(formal.groups, tampered_blocks, formal.task_ids),
            total_tasks=914,
            seed_count=1828,
        )
    except ValueError as exc:
        assert "block registry" in str(exc)
    else:
        raise AssertionError("incomplete final block registry was accepted")

    loaded = synthetic_shape()
    cache = final.build_bootstrap_cache(loaded.groups, np=np)
    configurations = v2.configuration_rows(loaded, cache)
    distributions = v2.distribution_rows(loaded.groups, np=np)
    pathways = v2.pathway_rows(loaded.groups)
    sensors = final.per_sensor_rows(loaded.groups, cache)
    pooled, pooled_cache = final.equal_weight_pooled_rows(loaded.groups, cache)
    standardized = final.standardized_rows(loaded.groups, cache, pooled_cache)
    thickness = final.thickness_ratio_rows(loaded.groups, cache)
    layouts = final.layout_ratio_rows(loaded.groups, cache)
    primaries = final.primary_contrast_rows(loaded.groups, cache, pooled_cache)

    assert len(configurations) == 18
    assert len(distributions) == 54
    assert len(pathways) == 18
    assert len(sensors) == 72
    assert len(pooled) == 6
    assert len(standardized) == 18
    assert len(thickness) == 15
    assert len(layouts) == 12
    assert len(primaries) == 4
    final.require_complete_bootstrap(
        [*configurations, *sensors, *pooled, *standardized, *thickness, *layouts, *primaries]
    )

    first = pooled[0]
    keys = {
        layout: ("production", 4, layout, 500, 0, 0) for layout in SIPM_LAYOUTS
    }
    points = {
        layout: v2.configuration_estimator(loaded.groups[key])["scintillation"]
        for layout, key in keys.items()
    }
    equal_weight = sum(points.values()) / 3
    event_weight = sum(
        points[layout] * len(loaded.groups[keys[layout]]) for layout in SIPM_LAYOUTS
    ) / sum(len(loaded.groups[key]) for key in keys.values())
    assert math.isclose(
        float(first["pooled_scintillation_photons_per_neutron"]),
        equal_weight,
        rel_tol=0,
        abs_tol=1e-12,
    )
    assert not math.isclose(equal_weight, event_weight, rel_tol=0, abs_tol=1e-6)
    assert first["production_weighting"] == "equal-layout-strata"
    assert {row["primary_contrast_id"] for row in primaries} == {
        "pooled-scintillation-production-24-over-4",
        "observed-net-back-center-24-over-4",
        "observed-net-edge-center-24-over-4",
        "observed-net-back-four-24-over-4",
    }
    assert all(float(row["precision_target"]) == 0.10 for row in primaries)
    assert all(row["automatic_acceptance"] is False for row in primaries)
    summary = final.summary_markdown(configurations, primaries, distributions)
    assert "228,500" in summary
    assert "equal one-third" in summary
    assert "not photoelectrons" in summary

    with tempfile.TemporaryDirectory(prefix="steel-module-direct-final-") as raw:
        output = Path(raw)
        final.write_csv(
            output / "primary_contrasts.csv", final.PRIMARY_FIELDS, primaries
        )
        (output / "summary.md").write_text(summary, encoding="utf-8")
        final.write_checksums(output)
        assert verify_checksum_manifest(
            output, required={"primary_contrasts.csv", "summary.md"}
        ) == {"primary_contrasts.csv", "summary.md"}

    print(
        "steel-module direct final analysis: PASS "
        "(914 tasks, 228500 events, 18 configurations, equal-stratum pooled "
        "production, four primary contrasts, no scheduler contact)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
