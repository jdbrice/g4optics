#!/usr/bin/env python3
"""Deterministic, scheduler-free checks for cumulative direct BC-S1 + BC-S2."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import analyze_steel_module_campaign as v1
import analyze_steel_module_direct_bc_s2 as analyzer
import analyze_steel_module_production_checkpoint as core
from check_steel_module_direct_bc_s1 import fixture_tasks as bc_s1_tasks
from check_steel_module_direct_bc_s2 import fixture_tasks as bc_s2_tasks
from generate_steel_module_direct_bc_s2_campaign import (
    EXPECTED_PREDECESSOR_CAMPAIGN_ID,
)
from steel_module_campaign_lib import CampaignBundle


SOURCE_CONTRACT = {
    "particle": "neutron",
    "authoritative_quantity": "kinetic_energy",
    "kinetic_energy_mev": 1000,
    "profile": "point",
    "direction": [0, 0, -1],
    "angular_model": "pencil",
}


def event_for(child_id: str, thickness: int, block: int, entry: int) -> v1.Event:
    """Return an approximately 80%-zero sample with one deterministic rare tail."""

    if entry < 200:
        generated = 0
        sipm = 0
    else:
        child_offset = 0 if child_id == "BC-S1" else 1
        if thickness == 4:
            generated = 100 + 2 * (block % 5)
            sipm = 9 + child_offset + block % 3
        else:
            generated = 600 + 7 * (block % 5)
            sipm = 15 + child_offset + block % 4
        if child_id == "BC-S2" and thickness == 24 and block == 22 and entry == 249:
            generated = 30_000
            sipm = 700
    return v1.Event(
        generated=generated,
        scintillation=generated,
        sipm=sipm,
        elastic=int(generated > 0),
        inelastic=0,
        capture=0,
        sensor0=sipm,
        sensor1=0,
        sensor2=0,
        sensor3=0,
    )


def fixture_increment(child_id: str) -> analyzer.IncrementData:
    tasks = bc_s1_tasks() if child_id == "BC-S1" else bc_s2_tasks()
    groups: dict[int, list[v1.Event]] = {4: [], 24: []}
    blocks: dict[int, dict[int, list[v1.Event]]] = {4: {}, 24: {}}
    located: dict[int, list[core.LocatedEvent]] = {4: [], 24: []}
    task_ids: dict[tuple[int, int], str] = {}
    for task in tasks:
        events = [
            event_for(child_id, task.tile_thickness_mm, task.seed_block, entry)
            for entry in range(250)
        ]
        groups[task.tile_thickness_mm].extend(events)
        blocks[task.tile_thickness_mm][task.seed_block] = events
        located[task.tile_thickness_mm].extend(
            core.LocatedEvent(
                event,
                task.logical_task_id,
                task.seed_block,
                entry,
            )
            for entry, event in enumerate(events)
        )
        task_ids[(task.tile_thickness_mm, task.seed_block)] = task.logical_task_id

    campaign_id = (
        EXPECTED_PREDECESSOR_CAMPAIGN_ID
        if child_id == "BC-S1"
        else "sm-v1-production-bc-s2-direct-fixture"
    )
    bundle = CampaignBundle(
        Path(f"/fixture/{child_id.lower()}"),
        {
            "campaign_id": campaign_id,
            "plan_hash": ("1" if child_id == "BC-S1" else "2") * 64,
            "git": {"commit": ("3" if child_id == "BC-S1" else "4") * 40},
            "environment": {"identity_hash": "5" * 64},
            "source_contract": SOURCE_CONTRACT,
        },
        tasks,
        (),
    )
    return analyzer.IncrementData(
        child_id=child_id,
        campaign_dir=bundle.directory,
        finalized_dir=bundle.directory / "finalized",
        bundle=bundle,
        validation={"accepted_statistical_evidence": True},
        audited_roots={},
        task_rows=(),
        groups=groups,
        blocks=blocks,
        located=located,
        task_ids=task_ids,
        attempt_ids=(f"fixture-{child_id.lower()}",),
        job_ids=("111" if child_id == "BC-S1" else "222",),
    )


def eligibility_fixture(
    *, half_width: float, loo_shift: float, predecessor_half_width: float
) -> tuple[dict[str, object], dict[str, object]]:
    cumulative = {
        "ratio": 1.5,
        "ci95_low": 1.5 * (1.0 - half_width),
        "ci95_high": 1.5 * (1.0 + half_width),
        "valid_resamples": analyzer.BOOTSTRAP_RESAMPLES,
        "relative_half_width": half_width,
        "ci_excludes_unity": True,
    }
    predecessor = {"relative_half_width": predecessor_half_width}
    loo = [
        {
            "absolute_relative_shift": loo_shift,
            "omitted_tile_thickness_mm": 4,
            "omitted_seed_block": 0,
            "omitted_logical_task_id": "fixture",
        }
    ]
    return analyzer.build_primary_and_eligibility(
        cumulative_net=cumulative,
        predecessor_primary=predecessor,
        loo_rows=loo,
    )


def main() -> int:
    try:
        import numpy as np
    except ImportError as exc:
        raise SystemExit("NumPy is required for the direct cumulative checker") from exc

    bc_s1 = fixture_increment("BC-S1")
    bc_s2 = fixture_increment("BC-S2")
    analyzer.validate_cumulative_contract(bc_s1, bc_s2)
    groups, blocks, located, task_ids = analyzer.combine_increments(bc_s1, bc_s2)
    assert {key: len(value) for key, value in groups.items()} == {4: 10_000, 24: 10_000}
    assert all(set(blocks[thickness]) == set(range(40)) for thickness in (4, 24))
    assert len(task_ids) == 80

    samples = {"BC-S1": bc_s1.groups, "BC-S2": bc_s2.groups, "cumulative": groups}
    original_resamples = analyzer.BOOTSTRAP_RESAMPLES
    analyzer.BOOTSTRAP_RESAMPLES = 200
    try:
        caches = {
            sample_id: analyzer.bootstrap_sample(sample_id, sample, np=np)
            for sample_id, sample in samples.items()
        }
        repeated = analyzer.bootstrap_sample("cumulative", groups, np=np)
        assert repeated == caches["cumulative"]
        decomposition, summaries = analyzer.decomposition_rows(samples, caches)
        increments = analyzer.increment_rows(samples, summaries)
        consistency = analyzer.increment_consistency(summaries)
        endpoints = analyzer.endpoint_rows(groups, caches["cumulative"])
    finally:
        analyzer.BOOTSTRAP_RESAMPLES = original_resamples

    assert len(decomposition) == 12
    for sample_id in analyzer.SAMPLE_ORDER:
        metrics = {
            row["metric"]: row
            for row in decomposition
            if row["sample_id"] == sample_id
        }
        factorized = float(metrics["generated"]["ratio_24_over_4"]) * float(
            metrics["collection"]["ratio_24_over_4"]
        )
        assert math.isclose(
            factorized,
            float(metrics["net"]["ratio_24_over_4"]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    assert len(increments) == 3
    assert consistency["controls_progression"] is False
    assert consistency["equivalence_test_performed"] is False
    assert len(endpoints) == 2
    assert all(row["blocks"] == 40 and row["events"] == 10_000 for row in endpoints)

    tails = analyzer.tail_rows(
        {"BC-S1": bc_s1.located, "BC-S2": bc_s2.located, "cumulative": located},
        np=np,
    )
    assert len(tails) == 18
    assert all(set(row) == set(analyzer.TAIL_FIELDS) for row in tails)
    assert any(
        row["sample_id"] == "BC-S2"
        and row["tile_thickness_mm"] == 24
        and row["metric"] == "sipm"
        and row["max"] == 700
        for row in tails
    )

    loo = analyzer.leave_one_block_out_rows(
        groups,
        blocks,
        task_ids,
        full_ratio=float(summaries["cumulative"]["net"]["ratio"]),
    )
    assert len(loo) == 80
    assert {
        (int(row["omitted_tile_thickness_mm"]), int(row["omitted_seed_block"]))
        for row in loo
    } == {(thickness, block) for thickness in (4, 24) for block in range(40)}
    primary, eligibility = analyzer.build_primary_and_eligibility(
        cumulative_net=summaries["cumulative"]["net"],
        predecessor_primary={"relative_half_width": 0.75},
        loo_rows=loo,
    )
    summary = analyzer.analysis_summary(
        bc_s1=bc_s1,
        bc_s2=bc_s2,
        decomposition=decomposition,
        primary=primary,
        eligibility=eligibility,
        consistency=consistency,
        tails=tails,
    )
    assert "80` tasks, `20,000` events" in summary
    assert "not an equivalence test" in summary
    assert "No decision or submission is automatic" in summary

    s1_ratio = next(
        float(row["observed_net_ratio_24_over_4"])
        for row in increments
        if row["sample_id"] == "BC-S1"
    )
    trajectory = analyzer.precision_trajectory_rows(
        [
            {
                "sample_id": "sealed-pilot",
                "events_per_endpoint": "1000",
                "ratio": "1.2",
                "ci95_low": "0.5",
                "ci95_high": "2.0",
                "relative_half_width": "0.625",
            },
            {
                "sample_id": "BC-ONLY-S1",
                "events_per_endpoint": "4000",
                "ratio": str(s1_ratio),
                "ci95_low": "1.0",
                "ci95_high": "2.0",
                "relative_half_width": "0.3",
            },
        ],
        increments,
    )
    assert [row["source_in_cumulative_estimate"] for row in trajectory] == [
        False,
        True,
        True,
        True,
    ]
    try:
        analyzer.precision_trajectory_rows(
            [
                {
                    "sample_id": "sealed-pilot",
                    "events_per_endpoint": "1000",
                    "ratio": "1.2",
                    "ci95_low": "0.5",
                    "ci95_high": "2.0",
                    "relative_half_width": "0.625",
                },
                {
                    "sample_id": "BC-ONLY-S1",
                    "events_per_endpoint": "4000",
                    "ratio": str(s1_ratio + 0.1),
                    "ci95_low": "1.0",
                    "ci95_high": "2.0",
                    "relative_half_width": "0.3",
                },
            ],
            increments,
        )
    except ValueError as exc:
        assert "does not reconcile" in str(exc)
    else:
        raise AssertionError("mismatched BC-S1 trajectory unexpectedly reconciled")

    _, success = eligibility_fixture(
        half_width=0.08, loo_shift=0.05, predecessor_half_width=0.20
    )
    _, continuing = eligibility_fixture(
        half_width=0.15, loo_shift=0.05, predecessor_half_width=0.25
    )
    _, paused = eligibility_fixture(
        half_width=0.15, loo_shift=0.25, predecessor_half_width=0.25
    )
    assert success["numeric_eligible_decisions"] == ["stop-success", "pause-review"]
    assert continuing["numeric_eligible_decisions"] == ["continue", "pause-review"]
    assert paused["numeric_eligible_decisions"] == ["pause-review"]

    bad_blocks = {
        thickness: dict(values) for thickness, values in bc_s2.blocks.items()
    }
    bad_blocks[4][15] = bad_blocks[4].pop(16)
    try:
        analyzer.validate_cumulative_contract(bc_s1, replace(bc_s2, blocks=bad_blocks))
    except ValueError as exc:
        assert "block registry" in str(exc)
    else:
        raise AssertionError("overlapping cumulative seed blocks were accepted")

    overlapping_tasks = list(bc_s2.bundle.tasks)
    overlapping_tasks[0] = replace(
        overlapping_tasks[0], seed1=bc_s1.bundle.tasks[0].seed1
    )
    overlapping_bundle = replace(bc_s2.bundle, tasks=tuple(overlapping_tasks))
    try:
        analyzer.validate_cumulative_contract(
            bc_s1, replace(bc_s2, bundle=overlapping_bundle)
        )
    except ValueError as exc:
        assert "seeds" in str(exc)
    else:
        raise AssertionError("overlapping cumulative production seeds were accepted")

    print(
        "steel-module direct BC-S1+S2 analysis: PASS "
        "(80 tasks, cumulative decomposition, independent increments, 80 LOO, "
        "decision branches, overlap rejection)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
