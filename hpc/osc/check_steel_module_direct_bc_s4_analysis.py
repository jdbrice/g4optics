#!/usr/bin/env python3
"""Deterministic checks for the final direct BC-S1 through BC-S4 analysis."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import analyze_steel_module_campaign as v1
import analyze_steel_module_direct_bc_s4 as analyzer
import analyze_steel_module_production_checkpoint as core
import check_steel_module_direct_bc_s3_analysis as s3_fixture
from check_steel_module_direct_bc_s4 import fixture_tasks as bc_s4_tasks
from steel_module_campaign_lib import CampaignBundle


def event_for(thickness: int, block: int, entry: int) -> v1.Event:
    """Approximately 80%-zero deterministic S4 sample with one rare tail."""

    if entry < 200:
        generated = 0
        sipm = 0
    elif thickness == 4:
        generated = 108 + 2 * (block % 5)
        sipm = 12 + block % 3
    else:
        generated = 628 + 7 * (block % 5)
        sipm = 18 + block % 4
    if thickness == 4 and block == 133 and entry == 249:
        generated = 42_000
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


def fixture_s4_increment() -> analyzer.base.IncrementData:
    tasks = bc_s4_tasks()
    groups: dict[int, list[v1.Event]] = {4: [], 24: []}
    blocks: dict[int, dict[int, list[v1.Event]]] = {4: {}, 24: {}}
    located: dict[int, list[core.LocatedEvent]] = {4: [], 24: []}
    task_ids: dict[tuple[int, int], str] = {}
    for task in tasks:
        events = [
            event_for(task.tile_thickness_mm, task.seed_block, entry)
            for entry in range(250)
        ]
        groups[task.tile_thickness_mm].extend(events)
        blocks[task.tile_thickness_mm][task.seed_block] = events
        located[task.tile_thickness_mm].extend(
            core.LocatedEvent(event, task.logical_task_id, task.seed_block, entry)
            for entry, event in enumerate(events)
        )
        task_ids[(task.tile_thickness_mm, task.seed_block)] = task.logical_task_id
    bundle = CampaignBundle(
        Path("/fixture/bc-s4"),
        {
            "campaign_id": analyzer.EXPECTED_BC_S4_CAMPAIGN_ID,
            "plan_hash": "8" * 64,
            "git": {"commit": "9" * 40},
            "environment": {"identity_hash": "7" * 64},
            "source_contract": s3_fixture.SOURCE_CONTRACT,
        },
        tasks,
        (),
    )
    return analyzer.base.IncrementData(
        child_id="BC-S4",
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
        attempt_ids=("fixture-bc-s4",),
        job_ids=("444",),
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
        raise SystemExit("NumPy is required for the direct BC-S4 checker") from exc

    increments = tuple(
        s3_fixture.fixture_increment(child_id)
        for child_id in ("BC-S1", "BC-S2", "BC-S3")
    ) + (fixture_s4_increment(),)
    analyzer.validate_cumulative_contract(increments)
    groups, blocks, located, task_ids = analyzer.combine_increments(increments)
    assert {key: len(value) for key, value in groups.items()} == {
        4: 40_250,
        24: 40_250,
    }
    assert all(set(blocks[thickness]) == set(range(161)) for thickness in (4, 24))
    assert len(task_ids) == 322

    samples = {
        **{increment.child_id: increment.groups for increment in increments},
        "cumulative": groups,
    }
    original_resamples = analyzer.BOOTSTRAP_RESAMPLES
    analyzer.BOOTSTRAP_RESAMPLES = 200
    try:
        caches = {
            sample_id: analyzer.bootstrap_sample(sample_id, sample, np=np)
            for sample_id, sample in samples.items()
        }
        assert analyzer.bootstrap_sample("cumulative", groups, np=np) == caches[
            "cumulative"
        ]
        decomposition, summaries = analyzer.decomposition_rows(samples, caches)
        increment_rows = analyzer.increment_rows(samples, summaries)
        consistency = analyzer.consistency_rows(summaries)
        endpoints = analyzer.endpoint_rows(groups, caches["cumulative"])
    finally:
        analyzer.BOOTSTRAP_RESAMPLES = original_resamples

    assert len(decomposition) == 20
    assert all(set(row) == set(analyzer.base.DECOMPOSITION_FIELDS) for row in decomposition)
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
    assert len(increment_rows) == 5
    assert all(set(row) == set(analyzer.base.INCREMENT_FIELDS) for row in increment_rows)
    assert len(consistency) == 6
    assert all(set(row) == set(analyzer.CONSISTENCY_FIELDS) for row in consistency)
    assert {row["comparison_id"] for row in consistency} == {
        "BC-S2-over-BC-S1",
        "BC-S3-over-BC-S1",
        "BC-S4-over-BC-S1",
        "BC-S3-over-BC-S2",
        "BC-S4-over-BC-S2",
        "BC-S4-over-BC-S3",
    }
    assert all(
        row["equivalence_test_performed"] is False
        and row["controls_progression"] is False
        for row in consistency
    )
    assert len(endpoints) == 2
    assert all(set(row) == set(analyzer.base.ENDPOINT_FIELDS) for row in endpoints)
    assert all(row["blocks"] == 161 and row["events"] == 40_250 for row in endpoints)

    located_samples = {
        **{increment.child_id: increment.located for increment in increments},
        "cumulative": located,
    }
    tails = analyzer.tail_rows(located_samples, np=np)
    assert len(tails) == 30
    assert all(set(row) == set(analyzer.base.TAIL_FIELDS) for row in tails)
    assert any(
        row["sample_id"] == "BC-S4"
        and row["tile_thickness_mm"] == 4
        and row["metric"] == "sipm"
        and row["max"] == 700
        for row in tails
    )

    loo = analyzer.leave_one_block_out_rows(
        blocks,
        task_ids,
        full_ratio=float(summaries["cumulative"]["net"]["ratio"]),
    )
    assert len(loo) == 322
    assert all(set(row) == set(core.LOO_FIELDS) for row in loo)
    assert {
        (int(row["omitted_tile_thickness_mm"]), int(row["omitted_seed_block"]))
        for row in loo
    } == {(thickness, block) for thickness in (4, 24) for block in range(161)}

    predecessor_primary = {
        "ratio": 1.44,
        "ci95_low": 1.29,
        "ci95_high": 1.61,
        "relative_half_width": 0.11,
    }
    primary, eligibility = analyzer.build_primary_and_eligibility(
        cumulative_net=summaries["cumulative"]["net"],
        predecessor_primary=predecessor_primary,
        loo_rows=loo,
    )
    assert primary["leave_one_block_out_evaluations"] == 322
    assert primary["hard_ceiling"] is True
    assert eligibility["next_child_if_continue"] is None
    assert "continue" not in eligibility["numeric_eligible_decisions"]

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
                "sample_id": "cumulative-through-BC-S2",
                "events_per_endpoint": "10000",
                "ratio": "1.47",
                "ci95_low": "1.24",
                "ci95_high": "1.75",
                "relative_half_width": "0.17",
            },
        ],
        predecessor_primary,
        increment_rows,
    )
    assert [row["sample_id"] for row in trajectory] == [
        "sealed-pilot",
        "BC-S1",
        "BC-S2",
        "cumulative-through-BC-S2",
        "BC-S3",
        "cumulative-through-BC-S3",
        "BC-S4",
        "cumulative",
    ]
    assert all(set(row) == set(analyzer.base.TRAJECTORY_FIELDS) for row in trajectory)

    summary = analyzer.summary_markdown(
        increments=increments,
        decomposition=decomposition,
        primary=primary,
        eligibility=eligibility,
        consistency=consistency,
        tails=tails,
    )
    assert "322` tasks, `80,500` events" in summary
    assert "hard ceiling" in summary
    assert "No decision or submission is automatic" in summary

    _, success = eligibility_fixture(
        half_width=0.08, loo_shift=0.05, predecessor_half_width=0.11
    )
    _, wide = eligibility_fixture(
        half_width=0.12, loo_shift=0.05, predecessor_half_width=0.15
    )
    _, unstable = eligibility_fixture(
        half_width=0.08, loo_shift=0.12, predecessor_half_width=0.11
    )
    assert success["numeric_eligible_decisions"] == ["stop-success", "pause-review"]
    assert wide["numeric_eligible_decisions"] == ["pause-review"]
    assert unstable["numeric_eligible_decisions"] == ["pause-review"]
    assert all(
        "continue" not in record["numeric_eligible_decisions"]
        for record in (success, wide, unstable)
    )

    bc_s1, bc_s2, bc_s3, bc_s4 = increments
    bad_blocks = {thickness: dict(values) for thickness, values in bc_s4.blocks.items()}
    bad_blocks[4][79] = bad_blocks[4].pop(80)
    try:
        analyzer.validate_cumulative_contract(
            (bc_s1, bc_s2, bc_s3, replace(bc_s4, blocks=bad_blocks))
        )
    except ValueError as exc:
        assert "block registry" in str(exc)
    else:
        raise AssertionError("overlapping S4 seed blocks were accepted")

    overlapping_tasks = list(bc_s4.bundle.tasks)
    overlapping_tasks[0] = replace(
        overlapping_tasks[0], seed1=bc_s1.bundle.tasks[0].seed1
    )
    overlapping_bundle = replace(bc_s4.bundle, tasks=tuple(overlapping_tasks))
    try:
        analyzer.validate_cumulative_contract(
            (bc_s1, bc_s2, bc_s3, replace(bc_s4, bundle=overlapping_bundle))
        )
    except ValueError as exc:
        assert "seeds" in str(exc)
    else:
        raise AssertionError("overlapping cumulative production seeds were accepted")

    print(
        "steel-module direct BC-S1+S2+S3+S4 analysis: PASS "
        "(322 tasks, 80.5k events, final decomposition, six increment diagnostics, "
        "322 LOO, hard-ceiling decisions, overlap rejection)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
