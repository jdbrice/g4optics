#!/usr/bin/env python3
"""Generate the accepted direct BC-S3 ordinary Slurm-array campaign."""

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
    validate_direct_bc_s2_bundle,
    validate_program_identity,
)
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


DIRECT_SCHEMA_VERSION = "steel-module-direct-bc-s3-v1"
DIRECT_CAMPAIGN_PREFIX = "sm-v1-production-bc-s3-direct-"
EXPECTED_TASK_COUNT = 80
EXPECTED_EVENT_COUNT = 20_000
EXPECTED_EVENTS_PER_TASK = 250
EXPECTED_BLOCKS = tuple(range(40, 80))
EXPECTED_THICKNESSES_MM = (4, 24)
EXPECTED_PREDECESSOR_CAMPAIGN_ID = (
    "sm-v1-production-bc-s2-direct-636265fe741b"
)
EXPECTED_PREDECESSOR_SIMULATION_COMMIT = (
    "8add11ffcb13edef63ca085ee91cfc1849b43820"
)
EXPECTED_PREDECESSOR_JOB_ID = "50617964"
EXPECTED_ANALYSIS_COMMIT = "8a297905922a4a9f7ce30ed1afc3cf04e6d6750d"
BASELINE_SIMULATION_COMMIT = "ddea5462c3a01f39a83af3b9cfa964053cbde626"
DECISION_DOCUMENT = Path(
    "docs/decisions/steel-module-direct-bc-s1-execution-v1.md"
)
ANALYSIS_SCHEMA_VERSION = "steel-module-direct-bc-s2-cumulative-analysis-v1"
PRIMARY_SCHEMA_VERSION = f"{ANALYSIS_SCHEMA_VERSION}-primary-v1"
ELIGIBILITY_SCHEMA_VERSION = f"{ANALYSIS_SCHEMA_VERSION}-numeric-eligibility-v1"
ANALYSIS_REQUIRED_FILES = (
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
EXPECTED_PRIMARY = {
    "ratio": 1.4745856608651495,
    "ci95_low": 1.246354895092067,
    "ci95_high": 1.7456002911764497,
    "relative_half_width": 0.16928328049503474,
    "max_leave_one_block_out_relative_shift": 0.03942411222716191,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--program-dir", required=True, type=Path)
    parser.add_argument("--bc-s2-campaign-dir", required=True, type=Path)
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


def validate_direct_bc_s3_tasks(
    tasks: Iterable[CampaignTask], *, geant4_version: str
) -> tuple[CampaignTask, ...]:
    resolved = tuple(tasks)
    if len(resolved) != EXPECTED_TASK_COUNT:
        raise ValueError("direct BC-S3 requires exactly 80 tasks")
    if [task.task_index for task in resolved] != list(range(1, 81)):
        raise ValueError("direct BC-S3 task indices must be contiguous and one-based")
    if any(
        task.stage != "production"
        or task.sipm_layout != "back-center"
        or task.absorber_transverse_mm != 500
        or task.x_mm != 0
        or task.y_mm != 0
        or task.events != EXPECTED_EVENTS_PER_TASK
        for task in resolved
    ):
        raise ValueError(
            "direct BC-S3 tasks must be centered 500 mm back-center production "
            "blocks with 250 events"
        )
    if sum(task.events for task in resolved) != EXPECTED_EVENT_COUNT:
        raise ValueError("direct BC-S3 event total must be exactly 20,000")
    for thickness in EXPECTED_THICKNESSES_MM:
        selected = [task for task in resolved if task.tile_thickness_mm == thickness]
        if len(selected) != 40 or tuple(sorted(task.seed_block for task in selected)) != (
            EXPECTED_BLOCKS
        ):
            raise ValueError(
                f"direct BC-S3 thickness {thickness} mm must contain blocks 40-79"
            )
    if {task.tile_thickness_mm for task in resolved} != set(
        EXPECTED_THICKNESSES_MM
    ):
        raise ValueError("direct BC-S3 permits only the 4 mm and 24 mm endpoints")
    seeds = _task_seeds(resolved)
    if len(seeds) != 160 or len(set(seeds)) != 160:
        raise ValueError("direct BC-S3 requires exactly 160 unique production seeds")
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
                f"direct BC-S3 configuration hash mismatch: {task.logical_task_id}"
            )
    return resolved


def validate_progression_values(
    *,
    primary: Mapping[str, object],
    eligibility: Mapping[str, object],
    decomposition_rows: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    if (
        primary.get("schema_version") != PRIMARY_SCHEMA_VERSION
        or primary.get("state_id") != "BC-ONLY-S2"
        or primary.get("metric") != "observed-net-sipm-response"
        or primary.get("ratio_direction") != "24-mm-over-4-mm"
        or primary.get("reference_events") != 10_000
        or primary.get("compared_events") != 10_000
        or primary.get("valid_resamples") != 10_000
        or primary.get("leave_one_block_out_evaluations") != 80
        or primary.get("maximum_loo_tile_thickness_mm") != 4
        or primary.get("maximum_loo_seed_block") != 34
    ):
        raise ValueError("BC-S2 cumulative primary contrast identity is invalid")
    try:
        values = {key: float(primary[key]) for key in EXPECTED_PRIMARY}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("BC-S2 cumulative progression metrics are invalid") from exc
    if any(
        not math.isclose(
            values[key], expected, rel_tol=1e-12, abs_tol=1e-12
        )
        for key, expected in EXPECTED_PRIMARY.items()
    ):
        raise ValueError("BC-S2 cumulative metrics differ from the reviewed evidence")
    if not (
        values["ratio"] > 0
        and values["ci95_low"] < values["ratio"] < values["ci95_high"]
        and values["relative_half_width"] > 0.10
        and values["max_leave_one_block_out_relative_shift"] <= 0.20
        and primary.get("interval_narrowing_vs_bc_s1") is True
    ):
        raise ValueError("BC-S2 cumulative metrics do not permit continue")
    if (
        eligibility.get("schema_version") != ELIGIBILITY_SCHEMA_VERSION
        or eligibility.get("state_id") != "BC-ONLY-S2"
        or eligibility.get("valid_evidence") is not True
        or eligibility.get("branch") != "continue-eligible"
        or eligibility.get("numeric_eligible_decisions")
        != ["continue", "pause-review"]
        or eligibility.get("next_child_if_continue") != "BC-S3"
        or eligibility.get("automatic_decision") is not False
        or eligibility.get("automatic_submission") is not False
        or eligibility.get("human_tail_disposition_required") is not True
    ):
        raise ValueError("BC-S2 cumulative numeric eligibility does not permit continue")
    matching = [
        row
        for row in decomposition_rows
        if row.get("sample_id") == "cumulative" and row.get("metric") == "net"
    ]
    if len(matching) != 1 or not math.isclose(
        float(matching[0]["ratio_24_over_4"]),
        values["ratio"],
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError("BC-S2 cumulative net decomposition does not reconcile")
    return {
        "observed_net_ratio_24_over_4": values["ratio"],
        "ci95_low": values["ci95_low"],
        "ci95_high": values["ci95_high"],
        "relative_half_width": values["relative_half_width"],
        "maximum_leave_one_block_out_shift": values[
            "max_leave_one_block_out_relative_shift"
        ],
        "maximum_leave_one_block_out_tile_thickness_mm": 4,
        "maximum_leave_one_block_out_seed_block": 34,
        "interval_narrowing": True,
        "numeric_branch": "continue-eligible",
        "numeric_eligible_decisions": ["continue", "pause-review"],
    }


def load_bc_s2_progression(
    campaign_dir: Path, *, repo_root: Path
) -> tuple[CampaignBundle, dict[str, object]]:
    bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
    validate_direct_bc_s2_bundle(bundle)
    if (
        bundle.campaign_id != EXPECTED_PREDECESSOR_CAMPAIGN_ID
        or bundle.git_commit != EXPECTED_PREDECESSOR_SIMULATION_COMMIT
    ):
        raise ValueError("BC-S2 predecessor is not the accepted direct campaign")
    finalized = bundle.directory / "finalized"
    verify_finalized_checksums(finalized)
    validation = load_json(finalized / "validation_report.json")
    if (
        validation.get("accepted_statistical_evidence") is not True
        or validation.get("expected_tasks") != 48
        or validation.get("selected_tasks") != 48
        or validation.get("event_audit_integrated") is not True
    ):
        raise ValueError("BC-S2 finalized evidence is not accepted")

    analysis = finalized / "direct-cumulative-analysis"
    verify_checksum_manifest(analysis, required=ANALYSIS_REQUIRED_FILES)
    config = load_json(analysis / "analysis_config.json")
    primary = load_json(analysis / "primary_contrast.json")
    eligibility = load_json(analysis / "numeric_eligibility.json")
    decomposition = read_csv(analysis / "pathway_decomposition.csv")
    analysis_git = config.get("analysis_git")
    sources = config.get("sources")
    bc_s2_source = sources.get("BC-S2") if isinstance(sources, dict) else None
    if (
        config.get("schema_version") != ANALYSIS_SCHEMA_VERSION
        or config.get("route") != DIRECT_ROUTE
        or config.get("state_id") != "BC-ONLY-S2"
        or config.get("included_children") != ["BC-S1", "BC-S2"]
        or config.get("task_count") != 80
        or config.get("event_count") != 20_000
        or config.get("events_per_endpoint") != 10_000
        or config.get("accepted_statistical_evidence") is not True
        or not isinstance(analysis_git, dict)
        or analysis_git.get("commit") != EXPECTED_ANALYSIS_COMMIT
        or not isinstance(bc_s2_source, dict)
        or bc_s2_source.get("campaign_id") != bundle.campaign_id
        or bc_s2_source.get("plan_hash") != bundle.plan_hash
        or bc_s2_source.get("simulation_commit") != bundle.git_commit
        or bc_s2_source.get("task_count") != 48
        or bc_s2_source.get("event_count") != 12_000
        or bc_s2_source.get("slurm_job_ids") != [EXPECTED_PREDECESSOR_JOB_ID]
    ):
        raise ValueError("BC-S2 cumulative-analysis provenance is invalid")
    attempt_ids = bc_s2_source.get("attempt_ids")
    if (
        not isinstance(attempt_ids, list)
        or len(attempt_ids) != 1
        or not isinstance(attempt_ids[0], str)
        or not attempt_ids[0]
    ):
        raise ValueError("BC-S2 predecessor attempt identity is invalid")
    metrics = validate_progression_values(
        primary=primary,
        eligibility=eligibility,
        decomposition_rows=decomposition,
    )
    decision_path = (repo_root / DECISION_DOCUMENT).resolve()
    if not decision_path.is_file():
        raise ValueError(f"missing accepted progression document: {decision_path}")
    progression: dict[str, object] = {
        "schema_version": "steel-module-direct-progression-v1",
        "predecessor_child": "BC-S2",
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
        "decision": "continue",
        "next_child": "BC-S3",
        "decision_date": "2026-07-21",
        "decision_document": {
            "path": DECISION_DOCUMENT.as_posix(),
            "sha256": sha256_file(decision_path),
        },
        "automatic_submission": False,
    }
    progression["progression_hash"] = sha256_bytes(canonical_json(progression))
    return bundle, progression


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
            "BC-S3 simulation runtime differs from the successful direct source: "
            + ", ".join(changed.splitlines())
        )


def validate_runtime_match(
    program: ProductionProgramBundle, predecessor: CampaignBundle
) -> None:
    runtime = _require_mapping(program.manifest.get("runtime"), "program runtime")
    environment = predecessor.environment
    if runtime.get("environment_identity") != environment.get("identity_hash"):
        raise ValueError("BC-S3 program and BC-S2 environment identities differ")
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
            raise ValueError(f"BC-S3 runtime artifact mismatch: {runtime_key}")


def program_source_record(
    program: ProductionProgramBundle, tasks: tuple[CampaignTask, ...]
) -> dict[str, object]:
    child_record = _require_mapping(
        _require_mapping(program.manifest.get("children"), "program children").get(
            "BC-S3"
        ),
        "program BC-S3 child",
    )
    child_dir = program.directory / "children" / "BC-S3"
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
        "child_id": "BC-S3",
        "child_plan_hash": _require_sha256(
            child_record.get("child_plan_hash"), "BC-S3 child plan hash"
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
    progression: dict[str, object],
) -> dict[str, object]:
    marker: dict[str, object] = {
        "schema_version": DIRECT_SCHEMA_VERSION,
        "route": DIRECT_ROUTE,
        "preflight_required": False,
        "managed_execution_required": False,
        "child_id": "BC-S3",
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "seed_blocks_per_configuration": 40,
        "seed_block_range": [40, 79],
        "task_plan_hash": plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
        "environment_identity": environment_identity_hash,
        "submission_git_commit": git_commit,
        "source": source,
        "progression": progression,
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
    progression: dict[str, object],
    git: dict[str, object],
    created_at_utc: str,
) -> None:
    directory.mkdir()
    geant4_version = _require_string(
        environment.get("geant4_version"), "environment Geant4 version"
    )
    validate_direct_bc_s3_tasks(tasks, geant4_version=geant4_version)
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
        raise ValueError("direct BC-S3 requires accepted OSC Geant4 11.4.2 evidence")
    git_commit = _require_string(git.get("commit"), "submission Git commit")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct BC-S3 must be generated from a clean checkout")

    plan_hash = sha256_bytes(canonical_json([asdict(task) for task in tasks]))
    marker = _direct_marker(
        tasks=tasks,
        plan_hash=plan_hash,
        environment_identity_hash=expected_environment_identity,
        git_commit=git_commit,
        source=source,
        progression=progression,
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
                "# direct_production_child=BC-S3",
                "# predecessor_decision=continue",
                "# submission_route=ordinary-slurm-array-no-preflight",
                f"# task_count={len(tasks)}",
                *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    readme_path.write_text(
        f"""# {campaign_id}

Direct BC-S3 steel-module production campaign.

- Shape: `4/24 mm back-center`, blocks `40-79` per endpoint
- Tasks: `80`
- Events: `20,000` (`250` per task)
- Cumulative after completion: `20,000` events per endpoint
- Progression: BC-S2 `continue`, tail `no-material-worsening`
- Submission: ordinary steel-module Slurm array; no preflight
- Plan hash: `{plan_hash}`

Validate before submission:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir {public_directory} --check-only
```

Submit only after check-only reports exactly 80 tasks and 20,000 events.
""",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "study_preset": STUDY_PRESET,
        "stage": "production",
        "description": "Direct BC-S3 production: 4/24 mm back-center blocks 40-79",
        "created_at_utc": created_at_utc,
        "plan_hash": plan_hash,
        "campaign_seed": None,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "seed_blocks_per_configuration": 40,
        "task_count": EXPECTED_TASK_COUNT,
        "configuration_axes": {
            "tile_thickness_mm": [4, 24],
            "sipm_layout": ["back-center"],
            "absorber_transverse_mm": [500],
            "x_mm": [0],
            "y_mm": [0],
        },
        "accepted_domain": {
            "tile_thickness_mm": [4, 8, 12, 16, 20, 24],
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


def write_direct_bc_s3_campaign_atomic(
    out_dir: Path,
    *,
    tasks: Iterable[CampaignTask],
    environment: dict[str, Any],
    source: dict[str, object],
    progression: dict[str, object],
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
            progression=copy.deepcopy(progression),
            git=copy.deepcopy(git),
            created_at_utc=created_at_utc or datetime.now(timezone.utc).isoformat(),
        )
        staged = load_campaign(temporary, verify_external_artifacts=True)
        validate_direct_bc_s3_bundle(staged)
        if target.exists():
            raise ValueError(f"campaign target appeared during publish: {target}")
        os.rename(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    bundle = load_campaign(target, verify_external_artifacts=True)
    validate_direct_bc_s3_bundle(bundle)
    return bundle


def validate_direct_bc_s3_bundle(bundle: CampaignBundle) -> None:
    manifest = bundle.manifest
    if manifest.get("stage") != "production":
        raise ValueError("direct BC-S3 campaign stage must be production")
    if any(key in manifest for key in ("program_management", "production_management")):
        raise ValueError("direct BC-S3 campaign must not contain managed-route markers")
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
        raise ValueError("direct BC-S3 requires accepted OSC Geant4 11.4.2 evidence")
    tasks = validate_direct_bc_s3_tasks(bundle.tasks, geant4_version=geant4_version)
    if (
        manifest.get("events_per_task") != 250
        or manifest.get("seed_blocks_per_configuration") != 40
        or manifest.get("campaign_seed") is not None
    ):
        raise ValueError("direct BC-S3 block/event policy is invalid")
    if manifest.get("configuration_axes") != {
        "tile_thickness_mm": [4, 24],
        "sipm_layout": ["back-center"],
        "absorber_transverse_mm": [500],
        "x_mm": [0],
        "y_mm": [0],
    }:
        raise ValueError("direct BC-S3 configuration axes are not exact")
    if manifest.get("source_contract") != {
        "particle": "neutron",
        "authoritative_quantity": "kinetic_energy",
        "kinetic_energy_mev": 1000,
        "profile": "point",
        "direction": [0, 0, -1],
        "angular_model": "pencil",
    }:
        raise ValueError("direct BC-S3 neutron source contract is not exact")
    git = _require_mapping(manifest.get("git"), "Git identity")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct BC-S3 campaign Git identity must be clean")

    marker = _require_mapping(manifest.get("direct_production"), "direct marker")
    if (
        marker.get("schema_version") != DIRECT_SCHEMA_VERSION
        or marker.get("route") != DIRECT_ROUTE
        or marker.get("preflight_required") is not False
        or marker.get("managed_execution_required") is not False
        or marker.get("child_id") != "BC-S3"
        or marker.get("seed_block_range") != [40, 79]
    ):
        raise ValueError("direct BC-S3 marker identity is invalid")
    expected_scalars = {
        "task_count": 80,
        "event_count": 20_000,
        "events_per_task": 250,
        "seed_blocks_per_configuration": 40,
        "task_plan_hash": bundle.plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
        "environment_identity": environment.get("identity_hash"),
        "submission_git_commit": git.get("commit"),
    }
    for key, expected in expected_scalars.items():
        if marker.get(key) != expected:
            raise ValueError(f"direct BC-S3 marker mismatch: {key}")
    source = _require_mapping(marker.get("source"), "direct production source")
    if (
        source.get("program_id") != EXPECTED_PROGRAM_ID
        or source.get("program_hash") != EXPECTED_PROGRAM_HASH
        or source.get("authorization_graph_hash")
        != EXPECTED_AUTHORIZATION_GRAPH_HASH
        or source.get("child_id") != "BC-S3"
        or source.get("accepted_pilot_exclusion_hash")
        != ACCEPTED_PILOT_EXCLUSION_HASH
        or source.get("source_task_seed_mapping_hash")
        != seed_pair_registry_hash(tasks)
        or source.get("source_seed_set_hash") != seed_set_hash(_task_seeds(tasks))
    ):
        raise ValueError("direct BC-S3 source does not match the frozen program")
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
        _require_sha256(source.get(key), f"direct BC-S3 source {key}")

    progression = _require_mapping(marker.get("progression"), "progression")
    attempt_ids = progression.get("predecessor_attempt_ids")
    if (
        progression.get("schema_version") != "steel-module-direct-progression-v1"
        or progression.get("predecessor_child") != "BC-S2"
        or progression.get("predecessor_campaign_id")
        != EXPECTED_PREDECESSOR_CAMPAIGN_ID
        or progression.get("predecessor_simulation_commit")
        != EXPECTED_PREDECESSOR_SIMULATION_COMMIT
        or not isinstance(attempt_ids, list)
        or len(attempt_ids) != 1
        or not isinstance(attempt_ids[0], str)
        or not attempt_ids[0]
        or progression.get("predecessor_slurm_job_ids")
        != [EXPECTED_PREDECESSOR_JOB_ID]
        or progression.get("tail_disposition") != "no-material-worsening"
        or progression.get("decision") != "continue"
        or progression.get("next_child") != "BC-S3"
        or progression.get("automatic_submission") is not False
    ):
        raise ValueError("direct BC-S3 progression decision is invalid")
    for key in (
        "predecessor_plan_hash",
        "predecessor_finalized_sha256s_sha256",
        "predecessor_analysis_sha256s_sha256",
        "predecessor_analysis_config_sha256",
        "progression_hash",
    ):
        _require_sha256(progression.get(key), f"progression {key}")
    decision_document = _require_mapping(
        progression.get("decision_document"), "progression decision document"
    )
    if decision_document.get("path") != DECISION_DOCUMENT.as_posix():
        raise ValueError("direct BC-S3 progression document path is invalid")
    _require_sha256(
        decision_document.get("sha256"), "progression decision document sha256"
    )
    metrics = _require_mapping(progression.get("metrics"), "progression metrics")
    try:
        half_width = float(metrics["relative_half_width"])
        loo_shift = float(metrics["maximum_leave_one_block_out_shift"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("direct BC-S3 progression metrics are invalid") from exc
    if (
        half_width <= 0.10
        or loo_shift > 0.20
        or metrics.get("interval_narrowing") is not True
        or metrics.get("numeric_branch") != "continue-eligible"
        or metrics.get("numeric_eligible_decisions")
        != ["continue", "pause-review"]
    ):
        raise ValueError("direct BC-S3 progression metrics do not permit continue")
    unhashed_progression = dict(progression)
    progression_hash = unhashed_progression.pop("progression_hash")
    if sha256_bytes(canonical_json(unhashed_progression)) != progression_hash:
        raise ValueError("direct BC-S3 progression hash mismatch")
    identity = _require_sha256(marker.get("identity_hash"), "direct identity hash")
    unhashed = dict(marker)
    unhashed.pop("identity_hash")
    if sha256_bytes(canonical_json(unhashed)) != identity:
        raise ValueError("direct BC-S3 marker identity hash mismatch")
    if bundle.campaign_id != DIRECT_CAMPAIGN_PREFIX + identity[:12]:
        raise ValueError("direct BC-S3 campaign ID does not match its identity")
    expected_scan_args = tuple(
        shlex.join(scan_args(task, bundle.campaign_id)) for task in tasks
    )
    if bundle.scan_args != expected_scan_args:
        raise ValueError("direct BC-S3 scan arguments do not match the frozen tasks")


def main() -> int:
    args = parse_args()
    repo_root = args.project_root.expanduser().resolve()
    try:
        git = clean_git_identity(repo_root)
        validate_simulation_source_unchanged(repo_root)
        predecessor, progression = load_bc_s2_progression(
            args.bc_s2_campaign_dir, repo_root=repo_root
        )
        program = load_production_program(
            args.program_dir, verify_runtime_artifacts=True
        )
        validate_program_identity(program)
        validate_runtime_match(program, predecessor)
        tasks = materialized_tasks(program.child_tasks["BC-S3"])
        validate_direct_bc_s3_tasks(
            tasks,
            geant4_version=_require_string(
                predecessor.environment.get("geant4_version"),
                "environment Geant4 version",
            ),
        )
        source = program_source_record(program, tasks)
        bundle = write_direct_bc_s3_campaign_atomic(
            args.out_dir,
            tasks=tasks,
            environment=predecessor.environment,
            source=source,
            progression=progression,
            git=git,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot generate direct BC-S3 campaign: {exc}", file=sys.stderr)
        return 1
    print(f"Campaign: {bundle.campaign_id}")
    print("Stage: production (direct BC-S3)")
    print("Tasks: 80")
    print("Events: 20000")
    print("Blocks: 40-79 per endpoint")
    print("Progression: continue / no-material-worsening")
    print("Preflight: none")
    print(f"Output: {bundle.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
