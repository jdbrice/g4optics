#!/usr/bin/env python3
"""Analyze cumulative direct BC-S1 + BC-S2 + BC-S3 production evidence."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import analyze_steel_module_campaign as v1
import analyze_steel_module_campaign_v2 as v2
import analyze_steel_module_direct_bc_s2 as previous
import analyze_steel_module_production_checkpoint as core
from analyze_steel_module_direct_bc_s1 import clean_analysis_identity
from generate_steel_module_direct_bc_s1_campaign import DIRECT_ROUTE
from generate_steel_module_direct_bc_s3_campaign import (
    EXPECTED_PREDECESSOR_CAMPAIGN_ID as EXPECTED_BC_S2_CAMPAIGN_ID,
    validate_direct_bc_s3_bundle,
)
from plot_steel_module_analysis_v2 import (
    sha256_file,
    verify_checksum_manifest,
    write_recursive_checksums,
)
from steel_module_campaign_lib import CampaignBundle, resolve_campaign_path


SCHEMA_VERSION = "steel-module-direct-bc-s3-cumulative-analysis-v1"
STATE_ID = "BC-ONLY-S3"
BOOTSTRAP_SEED = 20260715
BOOTSTRAP_RESAMPLES = 10_000
PRECISION_TARGET = 0.10
LOO_SUCCESS = 0.10
LOO_CONTINUE = 0.20
THICKNESSES = (4, 24)
EXPECTED_BC_S3_CAMPAIGN_ID = "sm-v1-production-bc-s3-direct-addb00f16a9c"
EXPECTED_BLOCKS = {
    "BC-S1": tuple(range(0, 16)),
    "BC-S2": tuple(range(16, 40)),
    "BC-S3": tuple(range(40, 80)),
}
EXPECTED_EVENTS_PER_ENDPOINT = {"BC-S1": 4_000, "BC-S2": 6_000, "BC-S3": 10_000}
EXPECTED_TASKS = {"BC-S1": 32, "BC-S2": 48, "BC-S3": 80}
SAMPLE_ORDER = ("BC-S1", "BC-S2", "BC-S3", "cumulative")
METRICS = ("generated", "scintillation", "collection", "net")
PREDECESSOR_REQUIRED_FILES = (
    "endpoint_estimates.csv",
    "pathway_decomposition.csv",
    "increment_comparison.csv",
    "increment_consistency.json",
    "distribution_diagnostics.csv",
    "increment_tail_diagnostics.csv",
    "block_loo.csv",
    "precision_trajectory.csv",
    "primary_contrast.json",
    "numeric_eligibility.json",
    "analysis_config.json",
    "summary.md",
)
CONSISTENCY_FIELDS = (
    "comparison_id",
    "numerator_increment",
    "denominator_increment",
    "metric",
    "ratio_of_ratios",
    "ci95_low",
    "ci95_high",
    "valid_resamples",
    "difference_detected",
    "equivalence_test_performed",
    "controls_progression",
    "interpretation",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--bc-s1-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s2-campaign-dir", required=True, type=Path)
    parser.add_argument("--bc-s3-campaign-dir", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Defaults to BC_S3_CAMPAIGN/finalized/direct-cumulative-analysis; "
            "an existing target is refused."
        ),
    )
    return parser.parse_args()


def validate_s3_task_index(
    *,
    bundle: CampaignBundle,
    task_rows: Sequence[Mapping[str, str]],
    audited_roots: Mapping[str, v1.AuditedRoot],
) -> None:
    expected = {task.logical_task_id: task for task in bundle.tasks}
    if len(task_rows) != 80 or len(expected) != 80:
        raise ValueError("BC-S3 finalized task count is not 80")
    if (
        set(expected) != {row.get("logical_task_id") for row in task_rows}
        or set(audited_roots) != set(expected)
    ):
        raise ValueError("BC-S3 task/finalization identities disagree")
    blocks: dict[int, set[int]] = defaultdict(set)
    for row in task_rows:
        logical_id = row["logical_task_id"]
        task = expected[logical_id]
        for field, value in asdict(task).items():
            if row.get(field) != str(value):
                raise ValueError(f"BC-S3 task index disagrees with {field}: {logical_id}")
        audited = audited_roots[logical_id]
        if (
            audited.path != row["root"]
            or audited.tile_thickness_mm != task.tile_thickness_mm
            or audited.sipm_layout != task.sipm_layout
            or audited.absorber_transverse_mm != task.absorber_transverse_mm
        ):
            raise ValueError(f"BC-S3 event audit disagrees with task: {logical_id}")
        if (
            task.tile_thickness_mm not in THICKNESSES
            or task.sipm_layout != "back-center"
            or task.absorber_transverse_mm != 500
            or task.x_mm != 0
            or task.y_mm != 0
            or task.events != 250
        ):
            raise ValueError("BC-S3 task lies outside the endpoint contract")
        blocks[task.tile_thickness_mm].add(task.seed_block)
    expected_blocks = set(range(40, 80))
    if blocks != {4: expected_blocks, 24: expected_blocks}:
        raise ValueError("BC-S3 endpoint block range is invalid")


def load_s3_increment(campaign_dir: Path) -> previous.IncrementData:
    directory = campaign_dir.expanduser().resolve()
    finalized = directory / "finalized"
    bundle, validation, _, audited_roots = v1.validate_identity(directory, finalized)
    validate_direct_bc_s3_bundle(bundle)
    if validation.get("accepted_statistical_evidence") is not True:
        raise ValueError("BC-S3 finalized evidence is not accepted")
    task_rows = previous.read_table(finalized / "task_index.tsv", delimiter="\t")
    validate_s3_task_index(
        bundle=bundle,
        task_rows=task_rows,
        audited_roots=audited_roots,
    )

    groups: dict[int, list[v1.Event]] = defaultdict(list)
    blocks: dict[int, dict[int, list[v1.Event]]] = defaultdict(dict)
    located: dict[int, list[core.LocatedEvent]] = {4: [], 24: []}
    task_ids: dict[tuple[int, int], str] = {}
    summary_groups: dict[v1.ConfigurationKey, list[v1.Event]] = defaultdict(list)
    block_counts: dict[v1.ConfigurationKey, int] = defaultdict(int)
    for row in sorted(task_rows, key=lambda item: int(item["task_index"])):
        logical_id = row["logical_task_id"]
        audited = audited_roots[logical_id]
        root_path = resolve_campaign_path(directory, row["root"])
        if sha256_file(root_path) != audited.sha256:
            raise ValueError(f"BC-S3 audited ROOT checksum mismatch: {logical_id}")
        events = v1.read_root_events(root_path, int(row["events"]))
        if any(event.sensor1 or event.sensor2 or event.sensor3 for event in events):
            raise ValueError(f"BC-S3 inactive SiPM columns are nonzero: {logical_id}")
        thickness = int(row["tile_thickness_mm"])
        block = int(row["seed_block"])
        if block in blocks[thickness]:
            raise ValueError(f"duplicate BC-S3 endpoint block: {thickness}/{block}")
        groups[thickness].extend(events)
        blocks[thickness][block] = events
        located[thickness].extend(
            core.LocatedEvent(event, logical_id, block, entry)
            for entry, event in enumerate(events)
        )
        task_ids[(thickness, block)] = logical_id
        key = v1.configuration_key(row)
        summary_groups[key].extend(events)
        block_counts[key] += 1
    if {key: len(value) for key, value in groups.items()} != {4: 10_000, 24: 10_000}:
        raise ValueError("BC-S3 endpoint event totals are invalid")
    v1.validate_against_summaries(finalized, summary_groups, block_counts)
    return previous.IncrementData(
        child_id="BC-S3",
        campaign_dir=directory,
        finalized_dir=finalized,
        bundle=bundle,
        validation=validation,
        audited_roots=audited_roots,
        task_rows=tuple(task_rows),
        groups=dict(groups),
        blocks={key: dict(value) for key, value in blocks.items()},
        located=located,
        task_ids=task_ids,
        attempt_ids=tuple(sorted({row["attempt_id"] for row in task_rows})),
        job_ids=tuple(sorted({row["slurm_job_id"] for row in task_rows})),
    )


def load_predecessor_analysis(
    bc_s2: previous.IncrementData,
    bc_s3: previous.IncrementData,
) -> tuple[dict[str, object], list[dict[str, str]], Path]:
    marker = bc_s3.bundle.manifest.get("direct_production")
    progression = marker.get("progression") if isinstance(marker, dict) else None
    if not isinstance(progression, dict):
        raise ValueError("BC-S3 does not contain a progression record")
    analysis = bc_s2.finalized_dir / "direct-cumulative-analysis"
    verify_checksum_manifest(analysis, required=PREDECESSOR_REQUIRED_FILES)
    if (
        progression.get("predecessor_campaign_id") != bc_s2.bundle.campaign_id
        or progression.get("predecessor_plan_hash") != bc_s2.bundle.plan_hash
        or progression.get("predecessor_simulation_commit") != bc_s2.bundle.git_commit
        or progression.get("predecessor_finalized_sha256s_sha256")
        != sha256_file(bc_s2.finalized_dir / "SHA256SUMS")
        or progression.get("predecessor_analysis_sha256s_sha256")
        != sha256_file(analysis / "SHA256SUMS")
        or progression.get("predecessor_analysis_config_sha256")
        != sha256_file(analysis / "analysis_config.json")
        or progression.get("tail_disposition") != "no-material-worsening"
        or progression.get("decision") != "continue"
        or progression.get("next_child") != "BC-S3"
    ):
        raise ValueError("BC-S3 progression does not bind the supplied BC-S2 evidence")
    config = core.load_json(analysis / "analysis_config.json")
    primary = core.load_json(analysis / "primary_contrast.json")
    eligibility = core.load_json(analysis / "numeric_eligibility.json")
    trajectory = previous.read_table(analysis / "precision_trajectory.csv", delimiter=",")
    metrics = progression.get("metrics")
    if (
        config.get("schema_version") != previous.SCHEMA_VERSION
        or config.get("state_id") != "BC-ONLY-S2"
        or config.get("included_children") != ["BC-S1", "BC-S2"]
        or config.get("task_count") != 80
        or config.get("event_count") != 20_000
        or config.get("accepted_statistical_evidence") is not True
        or primary.get("state_id") != "BC-ONLY-S2"
        or primary.get("reference_events") != 10_000
        or primary.get("compared_events") != 10_000
        or primary.get("leave_one_block_out_evaluations") != 80
        or eligibility.get("branch") != "continue-eligible"
        or eligibility.get("numeric_eligible_decisions")
        != ["continue", "pause-review"]
        or not isinstance(metrics, dict)
        or not math.isclose(
            float(metrics.get("observed_net_ratio_24_over_4", math.nan)),
            float(primary.get("ratio", math.nan)),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    ):
        raise ValueError("BC-S2 predecessor analysis is not the accepted continue evidence")
    return primary, trajectory, analysis


def validate_cumulative_contract(
    increments: Sequence[previous.IncrementData],
) -> None:
    by_child = {increment.child_id: increment for increment in increments}
    if set(by_child) != {"BC-S1", "BC-S2", "BC-S3"}:
        raise ValueError("cumulative analysis requires exactly BC-S1, BC-S2 and BC-S3")
    if by_child["BC-S1"].bundle.campaign_id != previous.EXPECTED_PREDECESSOR_CAMPAIGN_ID:
        raise ValueError("cumulative analysis requires the accepted direct BC-S1")
    if by_child["BC-S2"].bundle.campaign_id != EXPECTED_BC_S2_CAMPAIGN_ID:
        raise ValueError("cumulative analysis requires the accepted direct BC-S2")
    if by_child["BC-S3"].bundle.campaign_id != EXPECTED_BC_S3_CAMPAIGN_ID:
        raise ValueError("cumulative analysis requires the accepted direct BC-S3")
    environment_ids = {
        increment.bundle.environment.get("identity_hash") for increment in increments
    }
    source_contracts = {
        json.dumps(increment.bundle.manifest.get("source_contract"), sort_keys=True)
        for increment in increments
    }
    if len(environment_ids) != 1 or len(source_contracts) != 1:
        raise ValueError("direct production physics/runtime identities differ")
    for child_id, increment in by_child.items():
        expected_events = EXPECTED_EVENTS_PER_ENDPOINT[child_id]
        if (
            len(increment.bundle.tasks) != EXPECTED_TASKS[child_id]
            or {key: len(value) for key, value in increment.groups.items()}
            != {4: expected_events, 24: expected_events}
        ):
            raise ValueError(f"{child_id} task or event count is invalid")
    all_tasks = tuple(task for increment in increments for task in increment.bundle.tasks)
    logical_ids = [task.logical_task_id for task in all_tasks]
    seeds = [seed for task in all_tasks for seed in (task.seed1, task.seed2)]
    if len(logical_ids) != 160 or len(set(logical_ids)) != 160:
        raise ValueError("cumulative direct task registry has overlap or gaps")
    if len(seeds) != 320 or len(set(seeds)) != 320:
        raise ValueError("cumulative production seeds are not unique")
    for thickness in THICKNESSES:
        seen: set[int] = set()
        for child_id in ("BC-S1", "BC-S2", "BC-S3"):
            child_blocks = set(by_child[child_id].blocks[thickness])
            if child_blocks != set(EXPECTED_BLOCKS[child_id]) or seen & child_blocks:
                raise ValueError("cumulative endpoint block registry is invalid")
            seen |= child_blocks
        if seen != set(range(80)):
            raise ValueError("cumulative endpoint block registry is not exact 0-79")


def combine_increments(
    increments: Sequence[previous.IncrementData],
) -> tuple[
    dict[int, list[v1.Event]],
    dict[int, dict[int, list[v1.Event]]],
    dict[int, list[core.LocatedEvent]],
    dict[tuple[int, int], str],
]:
    groups = {
        thickness: [
            event
            for increment in increments
            for event in increment.groups[thickness]
        ]
        for thickness in THICKNESSES
    }
    blocks = {
        thickness: {
            block: events
            for increment in increments
            for block, events in increment.blocks[thickness].items()
        }
        for thickness in THICKNESSES
    }
    located = {
        thickness: [
            event
            for increment in increments
            for event in increment.located[thickness]
        ]
        for thickness in THICKNESSES
    }
    task_ids = {
        identity: logical_id
        for increment in increments
        for identity, logical_id in increment.task_ids.items()
    }
    if {key: len(value) for key, value in groups.items()} != {4: 20_000, 24: 20_000}:
        raise ValueError("cumulative endpoint totals are not 20,000 events each")
    return groups, blocks, located, task_ids


def bootstrap_sample(
    sample_id: str,
    groups: Mapping[int, Sequence[v1.Event]],
    *,
    np: object,
) -> dict[int, dict[str, list[float]]]:
    cache: dict[int, dict[str, list[float]]] = {}
    for thickness in THICKNESSES:
        seed = v1.configuration_seed(
            BOOTSTRAP_SEED,
            (SCHEMA_VERSION, sample_id, thickness, "back-center", 500, 0, 0),
        )
        cache[thickness] = v2.bootstrap_configuration(
            groups[thickness],
            resamples=BOOTSTRAP_RESAMPLES,
            seed=seed,
            np=np,
        )
    return cache


def metric_ratio(
    groups: Mapping[int, Sequence[v1.Event]],
    cache: Mapping[int, Mapping[str, Sequence[float]]],
    metric: str,
) -> dict[str, object]:
    denominator = previous.metric_point(groups[4], metric)
    numerator = previous.metric_point(groups[24], metric)
    if denominator <= 0 or numerator <= 0:
        raise ValueError(f"{metric} cannot support the 24/4 ratio")
    ratio = numerator / denominator
    draws = v1.ratio_distribution(cache[24][metric], cache[4][metric])
    low, high, valid = v1.interval(draws)
    if valid != BOOTSTRAP_RESAMPLES or not all(
        math.isfinite(value) for value in (ratio, low, high)
    ):
        raise ValueError(f"{metric} bootstrap did not yield all valid ratios")
    return {
        "ratio": ratio,
        "ci95_low": low,
        "ci95_high": high,
        "valid_resamples": valid,
        "relative_half_width": (high - low) / (2.0 * abs(ratio)),
        "ci_excludes_unity": high < 1.0 or low > 1.0,
        "draws": draws,
    }


def sample_roles() -> dict[str, str]:
    return {
        "BC-S1": "first-independent-production-increment",
        "BC-S2": "second-independent-production-increment",
        "BC-S3": "third-independent-production-increment",
        "cumulative": "primary-cumulative-production-estimate-through-BC-S3",
    }


def decomposition_rows(
    samples: Mapping[str, Mapping[int, Sequence[v1.Event]]],
    caches: Mapping[str, Mapping[int, Mapping[str, Sequence[float]]]],
) -> tuple[list[dict[str, object]], dict[str, dict[str, dict[str, object]]]]:
    rows: list[dict[str, object]] = []
    summaries: dict[str, dict[str, dict[str, object]]] = {}
    roles = sample_roles()
    for sample_id in SAMPLE_ORDER:
        summaries[sample_id] = {}
        groups = samples[sample_id]
        for metric in METRICS:
            summary = metric_ratio(groups, caches[sample_id], metric)
            summaries[sample_id][metric] = summary
            rows.append(
                {
                    "sample_id": sample_id,
                    "sample_role": roles[sample_id],
                    "events_4mm": len(groups[4]),
                    "events_24mm": len(groups[24]),
                    "metric": metric,
                    "ratio_24_over_4": summary["ratio"],
                    "ci95_low": summary["ci95_low"],
                    "ci95_high": summary["ci95_high"],
                    "valid_resamples": summary["valid_resamples"],
                }
            )
    return rows, summaries


def increment_rows(
    samples: Mapping[str, Mapping[int, Sequence[v1.Event]]],
    summaries: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> list[dict[str, object]]:
    roles = sample_roles()
    block_counts = {"BC-S1": 16, "BC-S2": 24, "BC-S3": 40, "cumulative": 80}
    rows: list[dict[str, object]] = []
    for sample_id in SAMPLE_ORDER:
        groups = samples[sample_id]
        net = summaries[sample_id]["net"]
        rows.append(
            {
                "sample_id": sample_id,
                "sample_role": roles[sample_id],
                "events_per_endpoint": len(groups[4]),
                "blocks_per_endpoint": block_counts[sample_id],
                "observed_net_ratio_24_over_4": net["ratio"],
                "ci95_low": net["ci95_low"],
                "ci95_high": net["ci95_high"],
                "valid_resamples": net["valid_resamples"],
                "relative_half_width": net["relative_half_width"],
                "ci_excludes_unity": net["ci_excludes_unity"],
                "zero_fraction_4mm": sum(event.sipm == 0 for event in groups[4])
                / len(groups[4]),
                "zero_fraction_24mm": sum(event.sipm == 0 for event in groups[24])
                / len(groups[24]),
            }
        )
    return rows


def consistency_rows(
    summaries: Mapping[str, Mapping[str, Mapping[str, object]]]
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for numerator_id, denominator_id in (
        ("BC-S2", "BC-S1"),
        ("BC-S3", "BC-S2"),
        ("BC-S3", "BC-S1"),
    ):
        numerator = summaries[numerator_id]["net"]
        denominator = summaries[denominator_id]["net"]
        point = float(numerator["ratio"]) / float(denominator["ratio"])
        draws = v1.ratio_distribution(numerator["draws"], denominator["draws"])
        low, high, valid = v1.interval(draws)
        if valid != BOOTSTRAP_RESAMPLES:
            raise ValueError("increment consistency bootstrap has invalid draws")
        rows.append(
            {
                "comparison_id": f"{numerator_id}-over-{denominator_id}",
                "numerator_increment": numerator_id,
                "denominator_increment": denominator_id,
                "metric": "observed-net-sipm-response-24-over-4",
                "ratio_of_ratios": point,
                "ci95_low": low,
                "ci95_high": high,
                "valid_resamples": valid,
                "difference_detected": high < 1.0 or low > 1.0,
                "equivalence_test_performed": False,
                "controls_progression": False,
                "interpretation": (
                    "Diagnostic comparison of independent increments; failure to "
                    "detect a difference is not proof of equivalence."
                ),
            }
        )
    return rows


def endpoint_rows(
    groups: Mapping[int, Sequence[v1.Event]],
    cache: Mapping[int, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for thickness in THICKNESSES:
        events = groups[thickness]
        point = v1.estimator(events)
        low, high, valid = v1.interval(cache[thickness]["net"])
        zero = sum(event.sipm == 0 for event in events)
        rows.append(
            {
                "state_id": STATE_ID,
                "tile_thickness_mm": thickness,
                "sipm_layout": "back-center",
                "absorber_transverse_mm": 500,
                "blocks": 80,
                "events": len(events),
                "generated_optical_photons_per_neutron": sum(
                    event.generated for event in events
                )
                / len(events),
                "scintillation_photons_per_neutron": point["production"],
                "observed_net_sipm_photons_per_neutron": point["net"],
                "net_ci95_low": low,
                "net_ci95_high": high,
                "net_valid_resamples": valid,
                "sipm_zero_events": zero,
                "sipm_zero_fraction": zero / len(events),
                "sipm_positive_events": len(events) - zero,
            }
        )
    return rows


def tail_rows(
    located_samples: Mapping[str, Mapping[int, Sequence[core.LocatedEvent]]],
    *,
    np: object,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sample_id in SAMPLE_ORDER:
        for row in core.build_distribution_rows(located_samples[sample_id], np):
            cleaned = dict(row)
            cleaned.pop("state_id", None)
            rows.append({"sample_id": sample_id, **cleaned})
    return rows


def leave_one_block_out_rows(
    blocks: Mapping[int, Mapping[int, Sequence[v1.Event]]],
    task_ids: Mapping[tuple[int, int], str],
    *,
    full_ratio: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for thickness in THICKNESSES:
        for block in range(80):
            omitted = blocks[thickness].get(block)
            if omitted is None or len(omitted) != 250:
                raise ValueError("cumulative LOO requires every exact 250-event block")
            reduced = {
                arm: [
                    event
                    for candidate_block, values in sorted(blocks[arm].items())
                    if not (arm == thickness and candidate_block == block)
                    for event in values
                ]
                for arm in THICKNESSES
            }
            denominator = v1.estimator(reduced[4])["net"]
            numerator = v1.estimator(reduced[24])["net"]
            if denominator <= 0:
                raise ValueError("cumulative LOO denominator is zero")
            ratio = numerator / denominator
            shift = abs(ratio / full_ratio - 1.0)
            rows.append(
                {
                    "state_id": STATE_ID,
                    "omitted_tile_thickness_mm": thickness,
                    "omitted_seed_block": block,
                    "omitted_logical_task_id": task_ids[(thickness, block)],
                    "reference_events_remaining": len(reduced[4]),
                    "compared_events_remaining": len(reduced[24]),
                    "full_ratio": full_ratio,
                    "leave_one_block_out_ratio": ratio,
                    "absolute_relative_shift": shift,
                    "within_10pct": shift <= LOO_SUCCESS,
                    "within_20pct": shift <= LOO_CONTINUE,
                }
            )
    if len(rows) != 160:
        raise ValueError("BC-ONLY-S3 must yield exactly 160 LOO evaluations")
    return rows


def precision_trajectory_rows(
    predecessor_trajectory: Sequence[Mapping[str, str]],
    predecessor_primary: Mapping[str, object],
    increments: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    by_sample = {str(row["sample_id"]): row for row in increments}
    if set(by_sample) != set(SAMPLE_ORDER):
        raise ValueError("cumulative trajectory sample registry is invalid")
    pilot = [
        row
        for row in predecessor_trajectory
        if row.get("sample_id") == "sealed-pilot"
    ]
    if len(pilot) != 1:
        raise ValueError("BC-S2 trajectory lacks one sealed-pilot row")
    rows: list[dict[str, object]] = [
        {
            "sample_id": "sealed-pilot",
            "sample_role": "width-and-tail-baseline-only",
            "events_per_endpoint": int(pilot[0]["events_per_endpoint"]),
            "ratio": float(pilot[0]["ratio"]),
            "ci95_low": float(pilot[0]["ci95_low"]),
            "ci95_high": float(pilot[0]["ci95_high"]),
            "relative_half_width": float(pilot[0]["relative_half_width"]),
            "source_in_cumulative_estimate": False,
        }
    ]
    roles = sample_roles()
    for sample_id in ("BC-S1", "BC-S2"):
        source = by_sample[sample_id]
        rows.append(
            {
                "sample_id": sample_id,
                "sample_role": roles[sample_id],
                "events_per_endpoint": source["events_per_endpoint"],
                "ratio": source["observed_net_ratio_24_over_4"],
                "ci95_low": source["ci95_low"],
                "ci95_high": source["ci95_high"],
                "relative_half_width": source["relative_half_width"],
                "source_in_cumulative_estimate": True,
            }
        )
    rows.append(
        {
            "sample_id": "cumulative-through-BC-S2",
            "sample_role": "previous-cumulative-production-estimate",
            "events_per_endpoint": 10_000,
            "ratio": predecessor_primary["ratio"],
            "ci95_low": predecessor_primary["ci95_low"],
            "ci95_high": predecessor_primary["ci95_high"],
            "relative_half_width": predecessor_primary["relative_half_width"],
            "source_in_cumulative_estimate": True,
        }
    )
    for sample_id in ("BC-S3", "cumulative"):
        source = by_sample[sample_id]
        rows.append(
            {
                "sample_id": sample_id,
                "sample_role": roles[sample_id],
                "events_per_endpoint": source["events_per_endpoint"],
                "ratio": source["observed_net_ratio_24_over_4"],
                "ci95_low": source["ci95_low"],
                "ci95_high": source["ci95_high"],
                "relative_half_width": source["relative_half_width"],
                "source_in_cumulative_estimate": True,
            }
        )
    return rows


def build_primary_and_eligibility(
    *,
    cumulative_net: Mapping[str, object],
    predecessor_primary: Mapping[str, object],
    loo_rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], dict[str, object]]:
    maximum = max(loo_rows, key=lambda row: float(row["absolute_relative_shift"]))
    max_shift = float(maximum["absolute_relative_shift"])
    half_width = float(cumulative_net["relative_half_width"])
    predecessor_half_width = float(predecessor_primary["relative_half_width"])
    narrowing = half_width < predecessor_half_width
    numeric, branch = core.numeric_eligibility(
        relative_half_width=half_width,
        maximum_loo_shift=max_shift,
        narrowing=narrowing,
    )
    primary = {
        "schema_version": f"{SCHEMA_VERSION}-primary-v1",
        "state_id": STATE_ID,
        "metric": "observed-net-sipm-response",
        "ratio_direction": "24-mm-over-4-mm",
        "reference_tile_thickness_mm": 4,
        "compared_tile_thickness_mm": 24,
        "reference_events": 20_000,
        "compared_events": 20_000,
        "ratio": cumulative_net["ratio"],
        "ci95_low": cumulative_net["ci95_low"],
        "ci95_high": cumulative_net["ci95_high"],
        "valid_resamples": cumulative_net["valid_resamples"],
        "relative_half_width": half_width,
        "ci_excludes_unity": cumulative_net["ci_excludes_unity"],
        "max_leave_one_block_out_relative_shift": max_shift,
        "maximum_loo_tile_thickness_mm": maximum["omitted_tile_thickness_mm"],
        "maximum_loo_seed_block": maximum["omitted_seed_block"],
        "maximum_loo_logical_task_id": maximum["omitted_logical_task_id"],
        "leave_one_block_out_evaluations": 160,
        "precision_target": PRECISION_TARGET,
        "loo_success_threshold": LOO_SUCCESS,
        "loo_continue_ceiling": LOO_CONTINUE,
        "predecessor_relative_half_width": predecessor_half_width,
        "interval_narrowing_vs_bc_s2": narrowing,
        "projected_bc_s4_relative_half_width": half_width
        * math.sqrt(20_000 / 40_250),
        "unity_diagnostics_control_eligibility": False,
    }
    eligibility = {
        "schema_version": f"{SCHEMA_VERSION}-numeric-eligibility-v1",
        "state_id": STATE_ID,
        "valid_evidence": True,
        "branch": branch,
        "numeric_eligible_decisions": numeric,
        "next_child_if_continue": "BC-S4",
        "automatic_decision": False,
        "automatic_submission": False,
        "human_tail_disposition_required": True,
        "increment_consistency_controls_eligibility": False,
    }
    return primary, eligibility


def summary_markdown(
    *,
    increments: Sequence[previous.IncrementData],
    decomposition: Sequence[Mapping[str, object]],
    primary: Mapping[str, object],
    eligibility: Mapping[str, object],
    consistency: Sequence[Mapping[str, object]],
    tails: Sequence[Mapping[str, object]],
) -> str:
    by_child = {increment.child_id: increment for increment in increments}
    cumulative = {
        str(row["metric"]): row
        for row in decomposition
        if row["sample_id"] == "cumulative"
    }
    lines = [
        "# Direct BC-S1 + BC-S2 + BC-S3 Cumulative Production Analysis",
        "",
        "This reads three finalized ordinary campaigns and creates no scheduler action or Geant4 event.",
        "",
        *[
            f"- {child}: `{by_child[child].bundle.campaign_id}`, jobs "
            f"`{', '.join(by_child[child].job_ids)}`."
            for child in ("BC-S1", "BC-S2", "BC-S3")
        ],
        "- Cumulative sample: `160` tasks, `40,000` events; `20,000` per endpoint.",
        "",
        "## Cumulative production / collection / net decomposition",
        "",
        f"- Generated optical production 24/4: `{float(cumulative['generated']['ratio_24_over_4']):.6g}`.",
        f"- Scintillation production 24/4: `{float(cumulative['scintillation']['ratio_24_over_4']):.6g}`.",
        f"- Optical collection 24/4: `{float(cumulative['collection']['ratio_24_over_4']):.6g}`.",
        f"- Observed net SiPM response 24/4: `{float(primary['ratio']):.6g}`.",
        "",
        "## Primary precision and stability",
        "",
        f"- Bootstrap 95% interval: `[{float(primary['ci95_low']):.6g}, {float(primary['ci95_high']):.6g}]`.",
        f"- Relative half-width: `{100 * float(primary['relative_half_width']):.2f}%` (target `10%`).",
        f"- Maximum leave-one-block-out shift: `{100 * float(primary['max_leave_one_block_out_relative_shift']):.2f}%`, from `{primary['maximum_loo_tile_thickness_mm']} mm` block `{primary['maximum_loo_seed_block']}`.",
        f"- Narrower than cumulative BC-S2: `{str(primary['interval_narrowing_vs_bc_s2']).lower()}`.",
        f"- Numeric choices: `{', '.join(eligibility['numeric_eligible_decisions'])}`.",
        "",
        "## Independent-increment consistency diagnostics",
        "",
    ]
    for row in consistency:
        lines.append(
            f"- {row['comparison_id']}: `{float(row['ratio_of_ratios']):.6g}` "
            f"[`{float(row['ci95_low']):.6g}`, `{float(row['ci95_high']):.6g}`]; "
            f"difference detected `{str(row['difference_detected']).lower()}`."
        )
    lines.extend(
        [
            "- These are diagnostics, not equivalence tests, and do not control progression.",
            "",
            "## SiPM tail diagnostics",
            "",
        ]
    )
    for row in tails:
        if row["metric"] != "sipm":
            continue
        lines.append(
            f"- {row['sample_id']} / {row['tile_thickness_mm']} mm: zero "
            f"`{100 * float(row['zero_fraction']):.2f}%`, top 1% "
            f"`{100 * float(row['top_1pct_sum_fraction']):.2f}%`, top 5% "
            f"`{100 * float(row['top_5pct_sum_fraction']):.2f}%`, max "
            f"`{row['max']}` at `{row['maximum_logical_task_id']}` entry "
            f"`{row['maximum_root_entry']}`."
        )
    lines.extend(
        [
            "",
            "This is checksum-bound core evidence. A human must review the BC-S3 increment and cumulative tail before selecting `stop-success`, `continue`, or `pause-review`. No decision or submission is automatic.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    bc_s1_dir = args.bc_s1_campaign_dir.expanduser().resolve()
    bc_s2_dir = args.bc_s2_campaign_dir.expanduser().resolve()
    bc_s3_dir = args.bc_s3_campaign_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else bc_s3_dir / "finalized" / "direct-cumulative-analysis"
    )
    temporary: Path | None = None
    publish_lock: Path | None = None
    try:
        if output_dir.exists() or output_dir.is_symlink():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        repo_root = Path(__file__).resolve().parents[2]
        analysis_git = clean_analysis_identity(repo_root)
        try:
            import numpy as np  # type: ignore[import-not-found]
            import uproot  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("cumulative direct BC-S3 analysis requires NumPy and uproot") from exc

        bc_s1 = previous.load_increment(bc_s1_dir, child_id="BC-S1")
        bc_s2 = previous.load_increment(bc_s2_dir, child_id="BC-S2")
        bc_s3 = load_s3_increment(bc_s3_dir)
        increments = (bc_s1, bc_s2, bc_s3)
        validate_cumulative_contract(increments)
        predecessor_primary, predecessor_trajectory, predecessor_analysis = (
            load_predecessor_analysis(bc_s2, bc_s3)
        )
        groups, blocks, located, task_ids = combine_increments(increments)
        predecessor_groups = {
            thickness: [*bc_s1.groups[thickness], *bc_s2.groups[thickness]]
            for thickness in THICKNESSES
        }
        predecessor_point = (
            v1.estimator(predecessor_groups[24])["net"]
            / v1.estimator(predecessor_groups[4])["net"]
        )
        if not math.isclose(
            predecessor_point,
            float(predecessor_primary["ratio"]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("recomputed cumulative BC-S2 point estimate does not reconcile")

        samples: dict[str, Mapping[int, Sequence[v1.Event]]] = {
            "BC-S1": bc_s1.groups,
            "BC-S2": bc_s2.groups,
            "BC-S3": bc_s3.groups,
            "cumulative": groups,
        }
        located_samples: dict[str, Mapping[int, Sequence[core.LocatedEvent]]] = {
            "BC-S1": bc_s1.located,
            "BC-S2": bc_s2.located,
            "BC-S3": bc_s3.located,
            "cumulative": located,
        }
        caches = {
            sample_id: bootstrap_sample(sample_id, sample, np=np)
            for sample_id, sample in samples.items()
        }
        decomposition, summaries = decomposition_rows(samples, caches)
        increment_comparison = increment_rows(samples, summaries)
        consistency = consistency_rows(summaries)
        endpoints = endpoint_rows(groups, caches["cumulative"])
        tails = tail_rows(located_samples, np=np)
        loo = leave_one_block_out_rows(
            blocks,
            task_ids,
            full_ratio=float(summaries["cumulative"]["net"]["ratio"]),
        )
        trajectory = precision_trajectory_rows(
            predecessor_trajectory,
            predecessor_primary,
            increment_comparison,
        )
        primary, eligibility = build_primary_and_eligibility(
            cumulative_net=summaries["cumulative"]["net"],
            predecessor_primary=predecessor_primary,
            loo_rows=loo,
        )

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        previous.write_csv(
            temporary / "endpoint_estimates.csv", previous.ENDPOINT_FIELDS, endpoints
        )
        previous.write_csv(
            temporary / "pathway_decomposition.csv",
            previous.DECOMPOSITION_FIELDS,
            decomposition,
        )
        previous.write_csv(
            temporary / "increment_comparison.csv",
            previous.INCREMENT_FIELDS,
            increment_comparison,
        )
        previous.write_csv(
            temporary / "increment_consistency.csv", CONSISTENCY_FIELDS, consistency
        )
        cumulative_distribution = [
            {
                "state_id": STATE_ID,
                **{
                    key: value
                    for key, value in row.items()
                    if key != "sample_id"
                },
            }
            for row in tails
            if row["sample_id"] == "cumulative"
        ]
        previous.write_csv(
            temporary / "distribution_diagnostics.csv",
            core.DISTRIBUTION_FIELDS,
            cumulative_distribution,
        )
        previous.write_csv(
            temporary / "increment_tail_diagnostics.csv", previous.TAIL_FIELDS, tails
        )
        previous.write_csv(temporary / "block_loo.csv", core.LOO_FIELDS, loo)
        previous.write_csv(
            temporary / "precision_trajectory.csv",
            previous.TRAJECTORY_FIELDS,
            trajectory,
        )
        previous.write_json(temporary / "primary_contrast.json", primary)
        previous.write_json(temporary / "numeric_eligibility.json", eligibility)
        analysis_config = {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "route": DIRECT_ROUTE,
            "state_id": STATE_ID,
            "included_children": ["BC-S1", "BC-S2", "BC-S3"],
            "task_count": 160,
            "event_count": 40_000,
            "events_per_endpoint": 20_000,
            "accepted_statistical_evidence": True,
            "sources": {
                increment.child_id: previous.source_record(increment)
                for increment in increments
            },
            "predecessor_analysis": {
                "directory": str(predecessor_analysis),
                "sha256s_sha256": sha256_file(predecessor_analysis / "SHA256SUMS"),
                "analysis_config_sha256": sha256_file(
                    predecessor_analysis / "analysis_config.json"
                ),
            },
            "analysis_git": analysis_git,
            "analyzer": {
                "implementation": "hpc/osc/analyze_steel_module_direct_bc_s3.py",
                "sha256": sha256_file(Path(__file__).resolve()),
                "previous_adapter_sha256": sha256_file(
                    Path(previous.__file__).resolve()
                ),
                "v1_core_sha256": sha256_file(Path(v1.__file__).resolve()),
                "v2_core_sha256": sha256_file(Path(v2.__file__).resolve()),
                "distribution_core_sha256": sha256_file(Path(core.__file__).resolve()),
                "python_version": sys.version.split()[0],
                "numpy_version": str(np.__version__),
                "uproot_version": str(uproot.__version__),
            },
            "statistics": {
                "bootstrap_seed": BOOTSTRAP_SEED,
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "generator": "numpy.random.Generator(numpy.random.PCG64)",
                "confidence_interval": "95% event-bootstrap percentile interval",
                "leave_one_block_out_evaluations": 160,
                "precision_target": PRECISION_TARGET,
                "loo_success_threshold": LOO_SUCCESS,
                "loo_continue_ceiling": LOO_CONTINUE,
            },
            "policy": {
                "pilot_events_in_production_estimate": False,
                "cumulative_children": ["BC-S1", "BC-S2", "BC-S3"],
                "independent_increment_comparisons": "diagnostic-only",
                "automatic_decision": False,
                "automatic_submission": False,
                "human_tail_review_required": True,
            },
            "outputs": {
                "endpoint_estimates": "endpoint_estimates.csv",
                "pathway_decomposition": "pathway_decomposition.csv",
                "increment_comparison": "increment_comparison.csv",
                "increment_consistency": "increment_consistency.csv",
                "distribution_diagnostics": "distribution_diagnostics.csv",
                "increment_tail_diagnostics": "increment_tail_diagnostics.csv",
                "block_loo": "block_loo.csv",
                "precision_trajectory": "precision_trajectory.csv",
                "primary_contrast": "primary_contrast.json",
                "numeric_eligibility": "numeric_eligibility.json",
                "summary": "summary.md",
            },
        }
        previous.write_json(temporary / "analysis_config.json", analysis_config)
        (temporary / "summary.md").write_text(
            summary_markdown(
                increments=increments,
                decomposition=decomposition,
                primary=primary,
                eligibility=eligibility,
                consistency=consistency,
                tails=tails,
            ),
            encoding="utf-8",
        )
        write_recursive_checksums(temporary)
        verify_checksum_manifest(
            temporary,
            required=(
                "endpoint_estimates.csv",
                "pathway_decomposition.csv",
                "increment_comparison.csv",
                "increment_consistency.csv",
                "distribution_diagnostics.csv",
                "increment_tail_diagnostics.csv",
                "block_loo.csv",
                "precision_trajectory.csv",
                "primary_contrast.json",
                "numeric_eligibility.json",
                "analysis_config.json",
                "summary.md",
            ),
        )
        publish_lock = core.acquire_publish_lock(output_dir)
        if output_dir.exists() or output_dir.is_symlink():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        os.rename(temporary, output_dir)
        temporary = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot analyze cumulative direct BC-S3 campaign: {exc}", file=sys.stderr)
        return 1
    finally:
        if publish_lock is not None and publish_lock.exists():
            publish_lock.rmdir()
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)

    print(f"Analyzed cumulative direct BC-S1 + BC-S2 + BC-S3 into {output_dir}")
    print(
        "Observed net 24/4: "
        f"{float(primary['ratio']):.6g} "
        f"[{float(primary['ci95_low']):.6g}, {float(primary['ci95_high']):.6g}]"
    )
    print(
        f"Relative half-width: {100 * float(primary['relative_half_width']):.2f}%; "
        "maximum LOO shift: "
        f"{100 * float(primary['max_leave_one_block_out_relative_shift']):.2f}%"
    )
    print(
        "Numeric review choices: "
        + ", ".join(eligibility["numeric_eligible_decisions"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
