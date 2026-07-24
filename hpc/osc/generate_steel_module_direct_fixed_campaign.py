#!/usr/bin/env python3
"""Generate the accepted one-array direct FIXED production campaign."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from generate_steel_module_campaign import scan_args, write_tasks
from generate_steel_module_direct_bc_s1_campaign import (
    DIRECT_ROUTE,
    _require_mapping,
    _require_sha256,
    _require_string,
    _task_seeds,
    clean_git_identity,
    run_git,
)
from generate_steel_module_direct_bc_s2_campaign import (
    EXPECTED_AUTHORIZATION_GRAPH_HASH,
    EXPECTED_PROGRAM_HASH,
    EXPECTED_PROGRAM_ID,
    SIMULATION_RUNTIME_PATHS,
    validate_program_identity,
)
from generate_steel_module_direct_bc_s4_campaign import validate_direct_bc_s4_bundle
from steel_module_campaign_lib import (
    CAMPAIGN_SCHEMA_VERSION,
    STUDY_PRESET,
    CampaignBundle,
    CampaignTask,
    canonical_json,
    environment_identity,
    load_campaign,
    load_json,
    sha256_bytes,
    sha256_file,
    task_configuration_hash,
    verify_checksum_manifest,
    verify_finalized_checksums,
)
from steel_module_production_program_lib import (
    ACCEPTED_PILOT_EXCLUSION_HASH,
    ProductionProgramBundle,
    load_production_program,
    materialized_tasks,
    seed_pair_registry_hash,
    seed_set_hash,
)


DIRECT_SCHEMA_VERSION = "steel-module-direct-fixed-v1"
DIRECT_CAMPAIGN_PREFIX = "sm-v1-production-fixed-direct-"
EXPECTED_TASK_COUNT = 592
EXPECTED_EVENT_COUNT = 148_000
EXPECTED_EVENTS_PER_TASK = 250
EXPECTED_CONFIGURATION_COUNT = 16
OSC_ARRAY_CONCURRENCY_LIMIT = 1_000
EXPECTED_PREDECESSOR_CAMPAIGN_ID = "sm-v1-production-bc-s4-direct-46bf4fa3229b"
EXPECTED_PREDECESSOR_JOB_ID = "50618568"
EXPECTED_ANALYSIS_COMMIT = "9a74465aea6ee8bb482081694b28314918ee9cee"
BASELINE_SIMULATION_COMMIT = "ddea5462c3a01f39a83af3b9cfa964053cbde626"
DECISION_DOCUMENT = Path("docs/decisions/steel-module-direct-bc-s1-execution-v1.md")
DECISION_POLICY_MARKERS = (
    "tail_disposition no-material-worsening",
    "progression_decision stop-success",
    "next_bc_child none",
    "one ordinary `1-592` Slurm array",
    "totaling 148,000 events",
    "concurrency limit is 1,000",
    "There is no preflight and no planned subdivision",
)
ANALYSIS_SCHEMA_VERSION = "steel-module-direct-bc-s4-cumulative-analysis-v1"
PRIMARY_SCHEMA_VERSION = f"{ANALYSIS_SCHEMA_VERSION}-primary-v1"
ELIGIBILITY_SCHEMA_VERSION = f"{ANALYSIS_SCHEMA_VERSION}-numeric-eligibility-v1"
ANALYSIS_REQUIRED_FILES = (
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
)
TILE_THICKNESSES = (4, 8, 12, 16, 20, 24)
EXPECTED_BLOCKS: dict[tuple[str, int], tuple[int, ...]] = {
    **{
        ("back-center", thickness): tuple(range(16))
        for thickness in (8, 12, 16, 20)
    },
    **{
        ("edge-center", thickness): tuple(range(40))
        for thickness in TILE_THICKNESSES
    },
    **{
        ("back-four", thickness): tuple(range(48))
        for thickness in TILE_THICKNESSES
    },
}


def validate_decision_document_text(decision_text: str) -> None:
    """Verify the accepted FIXED policy without depending on Markdown wrapping."""
    normalized = " ".join(decision_text.split())
    missing = [
        marker for marker in DECISION_POLICY_MARKERS if marker not in normalized
    ]
    if missing:
        raise ValueError(
            "final decision document lacks the accepted FIXED policy: "
            + ", ".join(repr(marker) for marker in missing)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--program-dir", required=True, type=Path)
    parser.add_argument("--bc-s4-campaign-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Clean checkout whose commit will run the ordinary campaign.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def validate_direct_fixed_tasks(
    tasks: Iterable[CampaignTask], *, geant4_version: str
) -> tuple[CampaignTask, ...]:
    resolved = tuple(tasks)
    if len(resolved) != EXPECTED_TASK_COUNT:
        raise ValueError("direct FIXED requires exactly 592 tasks")
    if [task.task_index for task in resolved] != list(range(1, 593)):
        raise ValueError("direct FIXED task indices must be contiguous and one-based")
    if any(
        task.stage != "production"
        or task.absorber_transverse_mm != 500
        or task.x_mm != 0
        or task.y_mm != 0
        or task.events != EXPECTED_EVENTS_PER_TASK
        for task in resolved
    ):
        raise ValueError(
            "direct FIXED tasks must be centered 500 mm production blocks "
            "with 250 events"
        )
    if sum(task.events for task in resolved) != EXPECTED_EVENT_COUNT:
        raise ValueError("direct FIXED event total must be exactly 148,000")
    grouped: dict[tuple[str, int], list[int]] = {}
    for task in resolved:
        grouped.setdefault((task.sipm_layout, task.tile_thickness_mm), []).append(
            task.seed_block
        )
    if set(grouped) != set(EXPECTED_BLOCKS):
        raise ValueError("direct FIXED configuration registry is not exact")
    for key, expected in EXPECTED_BLOCKS.items():
        if tuple(sorted(grouped[key])) != expected:
            raise ValueError(f"direct FIXED block registry mismatch: {key}")
    seeds = _task_seeds(resolved)
    if len(seeds) != 1_184 or len(set(seeds)) != 1_184:
        raise ValueError("direct FIXED requires exactly 1,184 unique production seeds")
    logical_ids = [task.logical_task_id for task in resolved]
    if len(set(logical_ids)) != EXPECTED_TASK_COUNT:
        raise ValueError("direct FIXED logical task IDs are not unique")
    for task in resolved:
        expected = task_configuration_hash(
            stage=task.stage,
            tile_thickness_mm=task.tile_thickness_mm,
            sipm_layout=task.sipm_layout,
            absorber_transverse_mm=task.absorber_transverse_mm,
            x_mm=task.x_mm,
            y_mm=task.y_mm,
            events=task.events,
            seed_block=task.seed_block,
            seed1=task.seed1,
            seed2=task.seed2,
            geant4_version=geant4_version,
        )
        if task.configuration_hash != expected:
            raise ValueError(
                f"direct FIXED configuration hash mismatch: {task.logical_task_id}"
            )
    return resolved


def _rounded(value: object, digits: int) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("review metric is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError("review metric is not finite")
    return round(number, digits)


def validate_stop_success_values(
    *,
    primary: Mapping[str, object],
    eligibility: Mapping[str, object],
    decomposition_rows: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    if (
        primary.get("schema_version") != PRIMARY_SCHEMA_VERSION
        or primary.get("state_id") != "BC-ONLY-S4"
        or primary.get("metric") != "observed-net-sipm-response"
        or primary.get("ratio_direction") != "24-mm-over-4-mm"
        or primary.get("reference_events") != 40_250
        or primary.get("compared_events") != 40_250
        or primary.get("valid_resamples") != 10_000
        or primary.get("leave_one_block_out_evaluations") != 322
        or primary.get("maximum_loo_tile_thickness_mm") != 4
        or primary.get("maximum_loo_seed_block") != 34
        or primary.get("maximum_loo_logical_task_id")
        != "production-lback-center-t04-a500-b034"
        or primary.get("interval_narrowing_vs_bc_s3") is not True
        or primary.get("hard_ceiling") is not True
    ):
        raise ValueError("BC-S4 final primary contrast identity is invalid")
    ratio = float(primary.get("ratio", math.nan))
    low = float(primary.get("ci95_low", math.nan))
    high = float(primary.get("ci95_high", math.nan))
    half_width = float(primary.get("relative_half_width", math.nan))
    loo_shift = float(
        primary.get("max_leave_one_block_out_relative_shift", math.nan)
    )
    expected_rounded = (
        _rounded(ratio, 5) == 1.39346
        and _rounded(low, 5) == 1.29104
        and _rounded(high, 5) == 1.50771
        and _rounded(100 * half_width, 2) == 7.77
        and _rounded(100 * loo_shift, 2) == 0.89
    )
    if not (
        expected_rounded
        and 1.0 < low < ratio < high
        and half_width <= 0.10
        and loo_shift <= 0.10
        and primary.get("ci_excludes_unity") is True
    ):
        raise ValueError("BC-S4 final metrics differ from the reviewed evidence")
    if (
        eligibility.get("schema_version") != ELIGIBILITY_SCHEMA_VERSION
        or eligibility.get("state_id") != "BC-ONLY-S4"
        or eligibility.get("valid_evidence") is not True
        or eligibility.get("branch") != "stop-success-eligible"
        or eligibility.get("numeric_eligible_decisions")
        != ["stop-success", "pause-review"]
        or eligibility.get("hard_ceiling") is not True
        or eligibility.get("continue_suppressed_by_hard_ceiling") is not True
        or eligibility.get("next_child_if_continue") is not None
        or eligibility.get("automatic_decision") is not False
        or eligibility.get("automatic_submission") is not False
        or eligibility.get("human_tail_disposition_required") is not True
    ):
        raise ValueError("BC-S4 final eligibility does not permit stop-success")
    cumulative = {
        str(row.get("metric")): row
        for row in decomposition_rows
        if row.get("sample_id") == "cumulative"
    }
    if set(cumulative) != {"generated", "scintillation", "collection", "net"}:
        raise ValueError("BC-S4 final decomposition is incomplete")
    if not math.isclose(
        float(cumulative["net"]["ratio_24_over_4"]),
        ratio,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError("BC-S4 final net decomposition does not reconcile")
    generated = float(cumulative["generated"]["ratio_24_over_4"])
    scintillation = float(cumulative["scintillation"]["ratio_24_over_4"])
    collection = float(cumulative["collection"]["ratio_24_over_4"])
    if not (
        _rounded(generated, 5) == 5.67082
        and _rounded(scintillation, 5) == 5.67082
        and _rounded(collection, 6) == 0.245724
        and math.isclose(generated * collection, ratio, rel_tol=1e-12, abs_tol=1e-12)
    ):
        raise ValueError("BC-S4 final production/collection decomposition is invalid")
    return {
        "generated_optical_ratio_24_over_4": generated,
        "scintillation_ratio_24_over_4": scintillation,
        "collection_ratio_24_over_4": collection,
        "observed_net_ratio_24_over_4": ratio,
        "ci95_low": low,
        "ci95_high": high,
        "relative_half_width": half_width,
        "maximum_leave_one_block_out_shift": loo_shift,
        "maximum_leave_one_block_out_tile_thickness_mm": 4,
        "maximum_leave_one_block_out_seed_block": 34,
        "interval_narrowing": True,
        "numeric_branch": "stop-success-eligible",
        "numeric_eligible_decisions": ["stop-success", "pause-review"],
    }


def load_bc_s4_stop_success(
    campaign_dir: Path, *, repo_root: Path
) -> tuple[CampaignBundle, dict[str, object]]:
    bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
    validate_direct_bc_s4_bundle(bundle)
    if bundle.campaign_id != EXPECTED_PREDECESSOR_CAMPAIGN_ID:
        raise ValueError("FIXED predecessor is not the accepted direct BC-S4")
    finalized = bundle.directory / "finalized"
    verify_finalized_checksums(finalized)
    validation = load_json(finalized / "validation_report.json")
    if (
        validation.get("accepted_statistical_evidence") is not True
        or validation.get("expected_tasks") != 162
        or validation.get("selected_tasks") != 162
        or validation.get("event_audit_integrated") is not True
    ):
        raise ValueError("BC-S4 finalized evidence is not accepted")
    analysis = finalized / "direct-cumulative-analysis"
    verify_checksum_manifest(analysis, required=ANALYSIS_REQUIRED_FILES)
    config = load_json(analysis / "analysis_config.json")
    primary = load_json(analysis / "primary_contrast.json")
    eligibility = load_json(analysis / "numeric_eligibility.json")
    decomposition = read_csv(analysis / "pathway_decomposition.csv")
    analysis_git = config.get("analysis_git")
    sources = config.get("sources")
    bc_s4_source = sources.get("BC-S4") if isinstance(sources, dict) else None
    if (
        config.get("schema_version") != ANALYSIS_SCHEMA_VERSION
        or config.get("route") != DIRECT_ROUTE
        or config.get("state_id") != "BC-ONLY-S4"
        or config.get("included_children") != ["BC-S1", "BC-S2", "BC-S3", "BC-S4"]
        or config.get("task_count") != 322
        or config.get("event_count") != 80_500
        or config.get("events_per_endpoint") != 40_250
        or config.get("accepted_statistical_evidence") is not True
        or not isinstance(analysis_git, dict)
        or analysis_git.get("commit") != EXPECTED_ANALYSIS_COMMIT
        or not isinstance(bc_s4_source, dict)
        or bc_s4_source.get("campaign_id") != bundle.campaign_id
        or bc_s4_source.get("plan_hash") != bundle.plan_hash
        or bc_s4_source.get("simulation_commit") != bundle.git_commit
        or bc_s4_source.get("task_count") != 162
        or bc_s4_source.get("event_count") != 40_500
        or bc_s4_source.get("slurm_job_ids") != [EXPECTED_PREDECESSOR_JOB_ID]
    ):
        raise ValueError("BC-S4 final cumulative-analysis provenance is invalid")
    attempt_ids = bc_s4_source.get("attempt_ids")
    if (
        not isinstance(attempt_ids, list)
        or len(attempt_ids) != 1
        or not isinstance(attempt_ids[0], str)
        or not attempt_ids[0]
    ):
        raise ValueError("BC-S4 predecessor attempt identity is invalid")
    metrics = validate_stop_success_values(
        primary=primary,
        eligibility=eligibility,
        decomposition_rows=decomposition,
    )
    decision_path = (repo_root / DECISION_DOCUMENT).resolve()
    if not decision_path.is_file():
        raise ValueError(f"missing accepted final decision document: {decision_path}")
    decision_text = decision_path.read_text(encoding="utf-8")
    validate_decision_document_text(decision_text)
    authorization: dict[str, object] = {
        "schema_version": "steel-module-direct-fixed-authorization-v1",
        "predecessor_child": "BC-S4",
        "predecessor_campaign_id": bundle.campaign_id,
        "predecessor_plan_hash": bundle.plan_hash,
        "predecessor_simulation_commit": bundle.git_commit,
        "predecessor_attempt_ids": attempt_ids,
        "predecessor_slurm_job_ids": [EXPECTED_PREDECESSOR_JOB_ID],
        "predecessor_finalized_sha256s_sha256": sha256_file(
            finalized / "SHA256SUMS"
        ),
        "predecessor_analysis_sha256s_sha256": sha256_file(
            analysis / "SHA256SUMS"
        ),
        "predecessor_analysis_config_sha256": sha256_file(
            analysis / "analysis_config.json"
        ),
        "metrics": metrics,
        "tail_disposition": "no-material-worsening",
        "decision": "stop-success",
        "authorized_child": "FIXED",
        "next_bc_child": None,
        "decision_date": "2026-07-21",
        "decision_document": {
            "path": DECISION_DOCUMENT.as_posix(),
            "sha256": sha256_file(decision_path),
        },
        "submission_policy": {
            "route": DIRECT_ROUTE,
            "array_spec": "1-592",
            "task_count": EXPECTED_TASK_COUNT,
            "event_count": EXPECTED_EVENT_COUNT,
            "osc_array_concurrency_limit": OSC_ARRAY_CONCURRENCY_LIMIT,
            "preflight_required": False,
            "planned_partitioning": "none",
            "failed_elements_may_retry": True,
        },
        "automatic_submission": False,
    }
    authorization["authorization_hash"] = sha256_bytes(canonical_json(authorization))
    return bundle, authorization


def validate_simulation_source_unchanged(repo_root: Path) -> None:
    changed = run_git(
        repo_root,
        "diff",
        "--name-only",
        f"{BASELINE_SIMULATION_COMMIT}..HEAD",
        "--",
        *SIMULATION_RUNTIME_PATHS,
    )
    if changed:
        raise ValueError(
            "FIXED simulation runtime differs from the successful direct source: "
            + ", ".join(changed.splitlines())
        )


def validate_runtime_match(
    program: ProductionProgramBundle, predecessor: CampaignBundle
) -> None:
    runtime = _require_mapping(program.manifest.get("runtime"), "program runtime")
    environment = predecessor.environment
    if runtime.get("environment_identity") != environment.get("identity_hash"):
        raise ValueError("FIXED program and BC-S4 environment identities differ")
    for runtime_key, environment_key in (
        ("image", "image"),
        ("g4_data_manifest", "g4_data_manifest"),
        ("executable", "build_artifact"),
    ):
        runtime_record = _require_mapping(runtime.get(runtime_key), runtime_key)
        environment_record = _require_mapping(
            environment.get(environment_key), environment_key
        )
        if runtime_record.get("sha256") != environment_record.get("sha256"):
            raise ValueError(f"FIXED runtime artifact mismatch: {runtime_key}")


def program_source_record(
    program: ProductionProgramBundle, tasks: tuple[CampaignTask, ...]
) -> dict[str, object]:
    child_record = _require_mapping(
        _require_mapping(program.manifest.get("children"), "program children").get(
            "FIXED"
        ),
        "program FIXED child",
    )
    child_dir = program.directory / "children" / "FIXED"
    return {
        "production_program_directory": str(program.directory),
        "program_id": EXPECTED_PROGRAM_ID,
        "program_hash": EXPECTED_PROGRAM_HASH,
        "authorization_graph_hash": EXPECTED_AUTHORIZATION_GRAPH_HASH,
        "production_program_json_sha256": sha256_file(
            program.directory / "production_program.json"
        ),
        "production_program_sha256s_sha256": sha256_file(
            program.directory / "SHA256SUMS"
        ),
        "child_id": "FIXED",
        "child_plan_hash": _require_sha256(
            child_record.get("child_plan_hash"), "FIXED child plan hash"
        ),
        "child_plan_json_sha256": sha256_file(child_dir / "child_plan.json"),
        "child_task_set_sha256": sha256_file(child_dir / "task_set.tsv"),
        "child_scan_args_sha256": sha256_file(child_dir / "scan_args.txt"),
        "source_task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "source_seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
    }


def _direct_marker(
    *,
    tasks: tuple[CampaignTask, ...],
    plan_hash: str,
    environment_identity_hash: str,
    git_commit: str,
    source: dict[str, object],
    authorization: dict[str, object],
) -> dict[str, object]:
    marker: dict[str, object] = {
        "schema_version": DIRECT_SCHEMA_VERSION,
        "route": DIRECT_ROUTE,
        "preflight_required": False,
        "managed_execution_required": False,
        "child_id": "FIXED",
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "configuration_count": EXPECTED_CONFIGURATION_COUNT,
        "array_spec": "1-592",
        "osc_array_concurrency_limit": OSC_ARRAY_CONCURRENCY_LIMIT,
        "task_plan_hash": plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
        "environment_identity": environment_identity_hash,
        "submission_git_commit": git_commit,
        "source": source,
        "authorization": authorization,
    }
    marker["identity_hash"] = sha256_bytes(canonical_json(marker))
    return marker


def _write_direct_tree(
    directory: Path,
    *,
    public_directory: Path,
    tasks: tuple[CampaignTask, ...],
    environment: dict[str, Any],
    source: dict[str, object],
    authorization: dict[str, object],
    git: dict[str, object],
    created_at_utc: str,
) -> None:
    directory.mkdir()
    geant4_version = _require_string(
        environment.get("geant4_version"), "environment Geant4 version"
    )
    validate_direct_fixed_tasks(tasks, geant4_version=geant4_version)
    expected_environment_identity = _require_sha256(
        environment.get("identity_hash"), "environment identity"
    )
    if environment_identity(environment) != expected_environment_identity:
        raise ValueError("source environment identity is invalid")
    if (
        environment.get("mode") != "osc-production"
        or environment.get("accepted_statistical_evidence") is not True
        or geant4_version != "11.4.2"
    ):
        raise ValueError("direct FIXED requires accepted OSC Geant4 11.4.2 evidence")
    git_commit = _require_string(git.get("commit"), "submission Git commit")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct FIXED must be generated from a clean checkout")
    plan_hash = sha256_bytes(canonical_json([asdict(task) for task in tasks]))
    marker = _direct_marker(
        tasks=tasks,
        plan_hash=plan_hash,
        environment_identity_hash=expected_environment_identity,
        git_commit=git_commit,
        source=source,
        authorization=authorization,
    )
    campaign_id = DIRECT_CAMPAIGN_PREFIX + str(marker["identity_hash"])[:12]
    tasks_path = directory / "tasks.tsv"
    scan_args_path = directory / "scan_args.txt"
    readme_path = directory / "README.md"
    write_tasks(tasks_path, list(tasks))
    scan_args_path.write_text(
        "\n".join(
            [
                f"# schema_version={CAMPAIGN_SCHEMA_VERSION}",
                f"# campaign_id={campaign_id}",
                "# direct_production_child=FIXED",
                "# predecessor_decision=stop-success",
                "# submission_route=ordinary-slurm-array-no-preflight",
                "# array_spec=1-592",
                f"# task_count={len(tasks)}",
                *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    readme_path.write_text(
        f"""# {campaign_id}

Direct FIXED steel-module production campaign.

- Back-center: 8/12/16/20 mm, 16 x 250-event blocks each
- Edge-center: all six thicknesses, 40 x 250-event blocks each
- Back-four: all six thicknesses, 48 x 250-event blocks each
- Tasks: `592`
- Events: `148,000`
- Submission: one ordinary `1-592` Slurm array; no preflight
- Failure policy: retry only failed array elements
- Plan hash: `{plan_hash}`

Validate before submission:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir {public_directory} --check-only
```

Submit only after check-only reports exactly 592 tasks, zero submitted, and
zero complete.
""",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "study_preset": STUDY_PRESET,
        "stage": "production",
        "description": "Direct FIXED production: all remaining non-adaptive curves",
        "created_at_utc": created_at_utc,
        "plan_hash": plan_hash,
        "campaign_seed": None,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "task_count": EXPECTED_TASK_COUNT,
        "configuration_axes": {
            "tile_thickness_mm": list(TILE_THICKNESSES),
            "sipm_layout": ["back-center", "edge-center", "back-four"],
            "absorber_transverse_mm": [500],
            "x_mm": [0],
            "y_mm": [0],
        },
        "configuration_groups": {
            "back-center-intermediate": {
                "tile_thickness_mm": [8, 12, 16, 20],
                "blocks_per_configuration": 16,
            },
            "edge-center": {
                "tile_thickness_mm": list(TILE_THICKNESSES),
                "blocks_per_configuration": 40,
            },
            "back-four": {
                "tile_thickness_mm": list(TILE_THICKNESSES),
                "blocks_per_configuration": 48,
            },
        },
        "accepted_domain": {
            "tile_thickness_mm": list(TILE_THICKNESSES),
            "sipm_layout": ["back-center", "edge-center", "back-four"],
            "absorber_transverse_mm": [200, 300, 500],
        },
        "source_contract": {
            "particle": "neutron",
            "authoritative_quantity": "kinetic_energy",
            "kinetic_energy_mev": 1000,
            "profile": "point",
            "direction": [0, 0, -1],
            "angular_model": "pencil",
        },
        "git": git,
        "environment": environment,
        "direct_production": marker,
        "artifacts": {
            "tasks_tsv": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
            "scan_args": {
                "path": "scan_args.txt",
                "sha256": sha256_file(scan_args_path),
            },
            "readme": {"path": "README.md", "sha256": sha256_file(readme_path)},
        },
    }
    (directory / "campaign.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def write_direct_fixed_campaign_atomic(
    out_dir: Path,
    *,
    tasks: Iterable[CampaignTask],
    environment: dict[str, Any],
    source: dict[str, object],
    authorization: dict[str, object],
    git: dict[str, object],
    created_at_utc: str | None = None,
) -> CampaignBundle:
    requested = out_dir.expanduser()
    if requested.is_symlink():
        raise ValueError(f"refusing symlink campaign target: {requested}")
    target = requested.resolve()
    if target.exists():
        raise ValueError(f"refusing to overwrite campaign target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        temporary.rmdir()
        _write_direct_tree(
            temporary,
            public_directory=target,
            tasks=tuple(tasks),
            environment=copy.deepcopy(environment),
            source=copy.deepcopy(source),
            authorization=copy.deepcopy(authorization),
            git=copy.deepcopy(git),
            created_at_utc=created_at_utc or datetime.now(timezone.utc).isoformat(),
        )
        staged = load_campaign(temporary, verify_external_artifacts=True)
        validate_direct_fixed_bundle(staged)
        if target.exists():
            raise ValueError(f"campaign target appeared during publish: {target}")
        os.rename(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    bundle = load_campaign(target, verify_external_artifacts=True)
    validate_direct_fixed_bundle(bundle)
    return bundle


def validate_direct_fixed_bundle(bundle: CampaignBundle) -> None:
    manifest = bundle.manifest
    if manifest.get("stage") != "production":
        raise ValueError("direct FIXED campaign stage must be production")
    if any(key in manifest for key in ("program_management", "production_management")):
        raise ValueError("direct FIXED campaign must not contain managed-route markers")
    environment = _require_mapping(manifest.get("environment"), "environment")
    geant4_version = _require_string(
        environment.get("geant4_version"), "environment Geant4 version"
    )
    if (
        environment.get("mode") != "osc-production"
        or environment.get("accepted_statistical_evidence") is not True
        or geant4_version != "11.4.2"
        or environment_identity(environment) != environment.get("identity_hash")
    ):
        raise ValueError("direct FIXED requires accepted OSC Geant4 11.4.2 evidence")
    tasks = validate_direct_fixed_tasks(bundle.tasks, geant4_version=geant4_version)
    if (
        manifest.get("events_per_task") != 250
        or manifest.get("campaign_seed") is not None
        or manifest.get("task_count") != EXPECTED_TASK_COUNT
    ):
        raise ValueError("direct FIXED task/event policy is invalid")
    if manifest.get("configuration_groups") != {
        "back-center-intermediate": {
            "tile_thickness_mm": [8, 12, 16, 20],
            "blocks_per_configuration": 16,
        },
        "edge-center": {
            "tile_thickness_mm": list(TILE_THICKNESSES),
            "blocks_per_configuration": 40,
        },
        "back-four": {
            "tile_thickness_mm": list(TILE_THICKNESSES),
            "blocks_per_configuration": 48,
        },
    }:
        raise ValueError("direct FIXED configuration groups are not exact")
    if manifest.get("source_contract") != {
        "particle": "neutron",
        "authoritative_quantity": "kinetic_energy",
        "kinetic_energy_mev": 1000,
        "profile": "point",
        "direction": [0, 0, -1],
        "angular_model": "pencil",
    }:
        raise ValueError("direct FIXED neutron source contract is not exact")
    git = _require_mapping(manifest.get("git"), "Git identity")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct FIXED campaign Git identity must be clean")
    marker = _require_mapping(manifest.get("direct_production"), "direct marker")
    if (
        marker.get("schema_version") != DIRECT_SCHEMA_VERSION
        or marker.get("route") != DIRECT_ROUTE
        or marker.get("preflight_required") is not False
        or marker.get("managed_execution_required") is not False
        or marker.get("child_id") != "FIXED"
        or marker.get("array_spec") != "1-592"
        or marker.get("osc_array_concurrency_limit") != OSC_ARRAY_CONCURRENCY_LIMIT
    ):
        raise ValueError("direct FIXED marker identity is invalid")
    expected_scalars = {
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "configuration_count": EXPECTED_CONFIGURATION_COUNT,
        "task_plan_hash": bundle.plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
        "environment_identity": environment.get("identity_hash"),
        "submission_git_commit": git.get("commit"),
    }
    for key, expected in expected_scalars.items():
        if marker.get(key) != expected:
            raise ValueError(f"direct FIXED marker mismatch: {key}")
    source = _require_mapping(marker.get("source"), "direct production source")
    if (
        source.get("program_id") != EXPECTED_PROGRAM_ID
        or source.get("program_hash") != EXPECTED_PROGRAM_HASH
        or source.get("authorization_graph_hash") != EXPECTED_AUTHORIZATION_GRAPH_HASH
        or source.get("child_id") != "FIXED"
        or source.get("accepted_pilot_exclusion_hash")
        != ACCEPTED_PILOT_EXCLUSION_HASH
        or source.get("source_task_seed_mapping_hash") != seed_pair_registry_hash(tasks)
        or source.get("source_seed_set_hash") != seed_set_hash(_task_seeds(tasks))
    ):
        raise ValueError("direct FIXED source does not match the frozen program")
    for key in (
        "production_program_json_sha256",
        "production_program_sha256s_sha256",
        "child_plan_hash",
        "child_plan_json_sha256",
        "child_task_set_sha256",
        "child_scan_args_sha256",
        "source_task_seed_mapping_hash",
        "source_seed_set_hash",
    ):
        _require_sha256(source.get(key), f"direct FIXED source {key}")
    authorization = _require_mapping(marker.get("authorization"), "authorization")
    policy = _require_mapping(authorization.get("submission_policy"), "submission policy")
    if (
        authorization.get("schema_version")
        != "steel-module-direct-fixed-authorization-v1"
        or authorization.get("predecessor_child") != "BC-S4"
        or authorization.get("predecessor_campaign_id")
        != EXPECTED_PREDECESSOR_CAMPAIGN_ID
        or authorization.get("predecessor_slurm_job_ids")
        != [EXPECTED_PREDECESSOR_JOB_ID]
        or authorization.get("tail_disposition") != "no-material-worsening"
        or authorization.get("decision") != "stop-success"
        or authorization.get("authorized_child") != "FIXED"
        or authorization.get("next_bc_child") is not None
        or authorization.get("automatic_submission") is not False
        or policy.get("route") != DIRECT_ROUTE
        or policy.get("array_spec") != "1-592"
        or policy.get("task_count") != EXPECTED_TASK_COUNT
        or policy.get("event_count") != EXPECTED_EVENT_COUNT
        or policy.get("osc_array_concurrency_limit") != OSC_ARRAY_CONCURRENCY_LIMIT
        or policy.get("preflight_required") is not False
        or policy.get("planned_partitioning") != "none"
        or policy.get("failed_elements_may_retry") is not True
    ):
        raise ValueError("direct FIXED authorization is invalid")
    for key in (
        "predecessor_plan_hash",
        "predecessor_finalized_sha256s_sha256",
        "predecessor_analysis_sha256s_sha256",
        "predecessor_analysis_config_sha256",
        "authorization_hash",
    ):
        _require_sha256(authorization.get(key), f"FIXED authorization {key}")
    decision = _require_mapping(
        authorization.get("decision_document"), "authorization decision document"
    )
    if decision.get("path") != DECISION_DOCUMENT.as_posix():
        raise ValueError("direct FIXED decision-document path is invalid")
    _require_sha256(decision.get("sha256"), "direct FIXED decision-document hash")
    metrics = _require_mapping(authorization.get("metrics"), "authorization metrics")
    if (
        float(metrics.get("relative_half_width", math.inf)) > 0.10
        or float(metrics.get("maximum_leave_one_block_out_shift", math.inf)) > 0.10
        or metrics.get("numeric_branch") != "stop-success-eligible"
        or metrics.get("numeric_eligible_decisions")
        != ["stop-success", "pause-review"]
    ):
        raise ValueError("direct FIXED authorization metrics are invalid")
    unhashed_authorization = dict(authorization)
    authorization_hash = unhashed_authorization.pop("authorization_hash")
    if sha256_bytes(canonical_json(unhashed_authorization)) != authorization_hash:
        raise ValueError("direct FIXED authorization hash mismatch")
    identity = _require_sha256(marker.get("identity_hash"), "direct identity hash")
    unhashed = dict(marker)
    unhashed.pop("identity_hash")
    if sha256_bytes(canonical_json(unhashed)) != identity:
        raise ValueError("direct FIXED marker identity hash mismatch")
    if bundle.campaign_id != DIRECT_CAMPAIGN_PREFIX + identity[:12]:
        raise ValueError("direct FIXED campaign ID does not match its identity")
    expected_scan_args = tuple(
        shlex.join(scan_args(task, bundle.campaign_id)) for task in tasks
    )
    if bundle.scan_args != expected_scan_args:
        raise ValueError("direct FIXED scan arguments do not match the frozen tasks")


def main() -> int:
    args = parse_args()
    repo_root = args.project_root.expanduser().resolve()
    try:
        git = clean_git_identity(repo_root)
        validate_simulation_source_unchanged(repo_root)
        predecessor, authorization = load_bc_s4_stop_success(
            args.bc_s4_campaign_dir, repo_root=repo_root
        )
        program = load_production_program(
            args.program_dir, verify_runtime_artifacts=True
        )
        validate_program_identity(program)
        validate_runtime_match(program, predecessor)
        tasks = materialized_tasks(program.child_tasks["FIXED"])
        validate_direct_fixed_tasks(
            tasks,
            geant4_version=_require_string(
                predecessor.environment.get("geant4_version"),
                "environment Geant4 version",
            ),
        )
        source = program_source_record(program, tasks)
        bundle = write_direct_fixed_campaign_atomic(
            args.out_dir,
            tasks=tasks,
            environment=predecessor.environment,
            source=source,
            authorization=authorization,
            git=git,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot generate direct FIXED campaign: {exc}", file=sys.stderr)
        return 1
    print(f"Campaign: {bundle.campaign_id}")
    print("Stage: production (direct FIXED)")
    print("Tasks: 592")
    print("Events: 148000")
    print("Configurations: 16")
    print("Array: 1-592 (confirmed concurrency limit: 1000)")
    print("Progression: stop-success / no-material-worsening")
    print("Preflight: none")
    print(f"Output: {bundle.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
