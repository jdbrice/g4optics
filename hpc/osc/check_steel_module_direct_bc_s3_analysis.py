#!/usr/bin/env python3
"""Deterministic checks for cumulative direct BC-S1 + BC-S2 + BC-S3."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import analyze_steel_module_campaign as v1
import analyze_steel_module_direct_bc_s3 as analyzer
import analyze_steel_module_production_checkpoint as core
from check_steel_module_direct_bc_s1 import fixture_tasks as bc_s1_tasks
from check_steel_module_direct_bc_s2 import fixture_tasks as bc_s2_tasks
from check_steel_module_direct_bc_s3 import fixture_tasks as bc_s3_tasks
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
    """Return an approximately 80%-zero sample with deterministic rare tails."""

    if entry < 200:
        generated = 0
        sipm = 0
    else:
        child_offset = {"BC-S1": 0, "BC-S2": 1, "BC-S3": 2}[child_id]
        if thickness == 4:
            generated = 100 + 2 * (block % 5)
            sipm = 9 + child_offset + block % 3
        else:
            generated = 600 + 7 * (block % 5)
            sipm = 15 + child_offset + block % 4
        if child_id == "BC-S2" and thickness == 4 and block == 34 and entry == 249:
            generated = 30_000
            sipm = 500
        if child_id == "BC-S3" and thickness == 24 and block == 67 and entry == 249:
            generated = 50_000
            sipm = 900
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


def fixture_increment(child_id: str) -> analyzer.previous.IncrementData:
    task_factories = {
        "BC-S1": bc_s1_tasks,
        "BC-S2": bc_s2_tasks,
        "BC-S3": bc_s3_tasks,
    }
    campaign_ids = {
        "BC-S1": analyzer.previous.EXPECTED_PREDECESSOR_CAMPAIGN_ID,
        "BC-S2": analyzer.EXPECTED_BC_S2_CAMPAIGN_ID,
        "BC-S3": analyzer.EXPECTED_BC_S3_CAMPAIGN_ID,
    }
    tasks = task_factories[child_id]()
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

    bundle = CampaignBundle(
        Path(f"/fixture/{child_id.lower()}"),
        {
            "campaign_id": campaign_ids[child_id],
            "plan_hash": {"BC-S1": "1", "BC-S2": "2", "BC-S3": "3"}[
                child_id
            ]
            * 64,
            "git": {
                "commit": {"BC-S1": "4", "BC-S2": "5", "BC-S3": "6"}[
                    child_id
                ]
                * 40
            },
            "environment": {"identity_hash": "7" * 64},
            "source_contract": SOURCE_CONTRACT,
        },
        tasks,
        (),
    )
    return analyzer.previous.IncrementData(
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
        job_ids=({"BC-S1": "111", "BC-S2": "222", "BC-S3": "333"}[child_id],),
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
        raise SystemExit("NumPy is required for the direct BC-S3 checker") from exc

    increments = tuple(
        fixture_increment(child_id) for child_id in ("BC-S1", "BC-S2", "BC-S3")
    )
    analyzer.validate_cumulative_contract(increments)
    groups, blocks, located, task_ids = analyzer.combine_increments(increments)
    assert {key: len(value) for key, value in groups.items()} == {
        4: 20_000,
        24: 20_000,
    }
    assert all(set(blocks[thickness]) == set(range(80)) for thickness in (4, 24))
    assert len(task_ids) == 160

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

    assert len(decomposition) == 16
    assert all(
        set(row) == set(analyzer.previous.DECOMPOSITION_FIELDS)
        for row in decomposition
    )
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
    assert len(increment_rows) == 4
    assert all(
        set(row) == set(analyzer.previous.INCREMENT_FIELDS)
        for row in increment_rows
    )
    assert len(consistency) == 3
    assert all(set(row) == set(analyzer.CONSISTENCY_FIELDS) for row in consistency)
    assert {row["comparison_id"] for row in consistency} == {
        "BC-S2-over-BC-S1",
        "BC-S3-over-BC-S2",
        "BC-S3-over-BC-S1",
    }
    assert all(
        row["equivalence_test_performed"] is False
        and row["controls_progression"] is False
        for row in consistency
    )
    assert len(endpoints) == 2
    assert all(set(row) == set(analyzer.previous.ENDPOINT_FIELDS) for row in endpoints)
    assert all(row["blocks"] == 80 and row["events"] == 20_000 for row in endpoints)

    located_samples = {
        **{increment.child_id: increment.located for increment in increments},
        "cumulative": located,
    }
    tails = analyzer.tail_rows(located_samples, np=np)
    assert len(tails) == 24
    assert all(set(row) == set(analyzer.previous.TAIL_FIELDS) for row in tails)
    assert any(
        row["sample_id"] == "BC-S3"
        and row["tile_thickness_mm"] == 24
        and row["metric"] == "sipm"
        and row["max"] == 900
        for row in tails
    )

    loo = analyzer.leave_one_block_out_rows(
        blocks,
        task_ids,
        full_ratio=float(summaries["cumulative"]["net"]["ratio"]),
    )
    assert len(loo) == 160
    assert all(set(row) == set(core.LOO_FIELDS) for row in loo)
    assert {
        (int(row["omitted_tile_thickness_mm"]), int(row["omitted_seed_block"]))
        for row in loo
    } == {(thickness, block) for thickness in (4, 24) for block in range(80)}

    predecessor_primary = {
        "ratio": 1.4,
        "ci95_low": 1.1,
        "ci95_high": 1.8,
        "relative_half_width": 0.25,
    }
    primary, eligibility = analyzer.build_primary_and_eligibility(
        cumulative_net=summaries["cumulative"]["net"],
        predecessor_primary=predecessor_primary,
        loo_rows=loo,
    )
    assert primary["leave_one_block_out_evaluations"] == 160
    assert math.isclose(
        float(primary["projected_bc_s4_relative_half_width"]),
        float(primary["relative_half_width"]) * math.sqrt(20_000 / 40_250),
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
    assert eligibility["automatic_decision"] is False
    assert eligibility["automatic_submission"] is False

    trajectory = analyzer.precision_trajectory_rows(
        [
            {
                "sample_id": "sealed-pilot",
                "events_per_endpoint": "1000",
                "ratio": "1.2",
                "ci95_low": "0.5",
                "ci95_high": "2.0",
                "relative_half_width": "0.625",
            }
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
        "cumulative",
    ]
    assert [row["source_in_cumulative_estimate"] for row in trajectory] == [
        False,
        True,
        True,
        True,
        True,
        True,
    ]
    assert all(
        set(row) == set(analyzer.previous.TRAJECTORY_FIELDS) for row in trajectory
    )

    summary = analyzer.summary_markdown(
        increments=increments,
        decomposition=decomposition,
        primary=primary,
        eligibility=eligibility,
        consistency=consistency,
        tails=tails,
    )
    assert "160` tasks, `40,000` events" in summary
    assert "not equivalence tests" in summary
    assert "No decision or submission is automatic" in summary

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

    bc_s1, bc_s2, bc_s3 = increments
    bad_blocks = {thickness: dict(values) for thickness, values in bc_s3.blocks.items()}
    bad_blocks[4][39] = bad_blocks[4].pop(40)
    try:
        analyzer.validate_cumulative_contract(
            (bc_s1, bc_s2, replace(bc_s3, blocks=bad_blocks))
        )
    except ValueError as exc:
        assert "block registry" in str(exc)
    else:
        raise AssertionError("overlapping cumulative seed blocks were accepted")

    overlapping_tasks = list(bc_s3.bundle.tasks)
    overlapping_tasks[0] = replace(
        overlapping_tasks[0], seed1=bc_s1.bundle.tasks[0].seed1
    )
    overlapping_bundle = replace(bc_s3.bundle, tasks=tuple(overlapping_tasks))
    try:
        analyzer.validate_cumulative_contract(
            (bc_s1, bc_s2, replace(bc_s3, bundle=overlapping_bundle))
        )
    except ValueError as exc:
        assert "seeds" in str(exc)
    else:
        raise AssertionError("overlapping cumulative production seeds were accepted")

    bad_groups = dict(bc_s3.groups)
    bad_groups[4] = bad_groups[4][:-1]
    try:
        analyzer.validate_cumulative_contract(
            (bc_s1, bc_s2, replace(bc_s3, groups=bad_groups))
        )
    except ValueError as exc:
        assert "task or event count" in str(exc)
    else:
        raise AssertionError("incorrect cumulative event count was accepted")

    print(
        "steel-module direct BC-S1+S2+S3 analysis: PASS "
        "(160 tasks, 40k events, decomposition, three increment diagnostics, "
        "160 LOO, decision branches, overlap rejection)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
