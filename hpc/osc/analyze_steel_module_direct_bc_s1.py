#!/usr/bin/env python3
"""Analyze the finalized direct BC-S1 campaign without a managed checkpoint."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import analyze_steel_module_campaign as v1
import analyze_steel_module_production_checkpoint as core
from generate_steel_module_direct_bc_s1_campaign import (
    DIRECT_ROUTE,
    validate_direct_bc_s1_bundle,
)
from plot_steel_module_analysis_v2 import (
    sha256_file,
    verify_checksum_manifest,
    write_recursive_checksums,
)
from steel_module_campaign_lib import CampaignBundle, resolve_campaign_path


SCHEMA_VERSION = "steel-module-direct-bc-s1-analysis-v1"
PILOT_ANALYSIS_SCHEMA_VERSION = "steel-module-analysis-v2-config-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--pilot-campaign-dir", required=True, type=Path)
    parser.add_argument(
        "--finalized-dir", type=Path, help="Defaults to CAMPAIGN_DIR/finalized."
    )
    parser.add_argument(
        "--pilot-analysis-dir",
        type=Path,
        help="Defaults to PILOT_CAMPAIGN_DIR/finalized/analysis-v2.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to FINALIZED_DIR/direct-analysis; existing output is refused.",
    )
    return parser.parse_args()


def read_table(path: Path, *, delimiter: str) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def write_json(path: Path, value: Mapping[str, object]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def clean_analysis_identity(repo_root: Path) -> dict[str, object]:
    dirty = git_output(repo_root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise ValueError(
            "direct BC-S1 analysis requires a clean checkout so its analyzer "
            "identity is reproducible"
        )
    return {
        "commit": git_output(repo_root, "rev-parse", "HEAD"),
        "tree": git_output(repo_root, "rev-parse", "HEAD^{tree}"),
        "branch": git_output(repo_root, "branch", "--show-current") or None,
        "dirty": False,
        "dirty_paths": [],
    }


def pilot_baseline_record(
    pilot_bundle: CampaignBundle, analysis_dir: Path
) -> dict[str, object]:
    verify_checksum_manifest(
        analysis_dir, required=("primary_contrasts.csv", "analysis_config.json")
    )
    config = core.load_json(analysis_dir / "analysis_config.json")
    analysis_git = config.get("analysis_git")
    reconciliation = config.get("v1_reconciliation")
    if (
        config.get("schema_version") != PILOT_ANALYSIS_SCHEMA_VERSION
        or config.get("accepted_statistical_evidence") is not True
        or config.get("campaign_id") != pilot_bundle.campaign_id
        or config.get("plan_hash") != pilot_bundle.plan_hash
        or config.get("simulation_git_commit") != pilot_bundle.git_commit
        or not isinstance(analysis_git, dict)
        or not isinstance(analysis_git.get("commit"), str)
        or not isinstance(reconciliation, dict)
        or reconciliation.get("status") != "passed"
    ):
        raise ValueError("sealed pilot analysis-v2 identity is invalid")
    finalized_dir = pilot_bundle.directory / "finalized"
    v1.verify_finalized_checksums(finalized_dir)
    return {
        "campaign_directory": str(pilot_bundle.directory),
        "analysis_v2_directory": str(analysis_dir),
        "campaign_id": pilot_bundle.campaign_id,
        "plan_hash": pilot_bundle.plan_hash,
        "simulation_commit": pilot_bundle.git_commit,
        "finalized_sha256s_sha256": sha256_file(finalized_dir / "SHA256SUMS"),
        "analysis_v2_sha256s_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
        "analysis_v2_config_sha256": sha256_file(
            analysis_dir / "analysis_config.json"
        ),
        "analysis_commit": analysis_git["commit"],
    }


def pseudo_checkpoint() -> dict[str, object]:
    """Describe the BC-S1 sample shape for the already-tested core validator."""

    return {
        "schema_version": core.CHECKPOINT_SCHEMA_VERSION,
        "state_id": core.STATE_ID,
        "evidence_mode": "back-center-only",
        "included_children": ["BC-S1"],
        "task_count": core.EXPECTED_TASKS,
        "event_count": core.EXPECTED_EVENTS,
    }


def validate_direct_task_index(
    bundle: CampaignBundle,
    task_rows: Sequence[Mapping[str, str]],
    audited_roots: Mapping[str, v1.AuditedRoot],
) -> None:
    core.validate_checkpoint_shape(pseudo_checkpoint(), task_rows)
    expected = {task.logical_task_id: task for task in bundle.tasks}
    if set(expected) != {row.get("logical_task_id") for row in task_rows}:
        raise ValueError("direct task index does not match the campaign task set")
    if set(audited_roots) != set(expected):
        raise ValueError("direct event audit does not match the campaign task set")
    for row in task_rows:
        logical_id = row["logical_task_id"]
        task = expected[logical_id]
        for field, value in asdict(task).items():
            if row.get(field) != str(value):
                raise ValueError(f"direct task index disagrees with {field}: {logical_id}")
        audited = audited_roots[logical_id]
        if (
            audited.path != row["root"]
            or audited.tile_thickness_mm != task.tile_thickness_mm
            or audited.sipm_layout != task.sipm_layout
            or audited.absorber_transverse_mm != task.absorber_transverse_mm
        ):
            raise ValueError(f"direct task index disagrees with event audit: {logical_id}")


def load_direct_events(
    campaign_dir: Path,
    finalized_dir: Path,
    bundle: CampaignBundle,
    task_rows: Sequence[Mapping[str, str]],
    audited_roots: Mapping[str, v1.AuditedRoot],
) -> tuple[
    dict[int, list[v1.Event]],
    dict[int, dict[int, list[v1.Event]]],
    dict[int, list[core.LocatedEvent]],
    dict[tuple[int, int], str],
]:
    validate_direct_task_index(bundle, task_rows, audited_roots)
    groups: dict[int, list[v1.Event]] = defaultdict(list)
    blocks: dict[int, dict[int, list[v1.Event]]] = defaultdict(dict)
    located: dict[int, list[core.LocatedEvent]] = {4: [], 24: []}
    task_ids: dict[tuple[int, int], str] = {}
    summary_groups: dict[v1.ConfigurationKey, list[v1.Event]] = defaultdict(list)
    block_counts: dict[v1.ConfigurationKey, int] = defaultdict(int)

    for row in sorted(task_rows, key=lambda item: int(item["task_index"])):
        logical_id = row["logical_task_id"]
        audited = audited_roots[logical_id]
        root_path = resolve_campaign_path(campaign_dir, row["root"])
        if sha256_file(root_path) != audited.sha256:
            raise ValueError(f"audited ROOT checksum mismatch: {logical_id}")
        events = v1.read_root_events(root_path, int(row["events"]))
        if any(event.sensor1 or event.sensor2 or event.sensor3 for event in events):
            raise ValueError(f"back-center task records inactive SiPM counts: {logical_id}")
        thickness = int(row["tile_thickness_mm"])
        block = int(row["seed_block"])
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

    if {key: len(value) for key, value in groups.items()} != {4: 4000, 24: 4000}:
        raise ValueError("direct BC-S1 endpoint totals are not 4,000 events each")
    v1.validate_against_summaries(finalized_dir, summary_groups, block_counts)
    return (
        dict(groups),
        {key: dict(value) for key, value in blocks.items()},
        located,
        task_ids,
    )


def pathway_decomposition(
    groups: Mapping[int, Sequence[v1.Event]],
) -> dict[str, object]:
    estimates = {thickness: v1.estimator(groups[thickness]) for thickness in (4, 24)}
    for thickness in (4, 24):
        estimates[thickness]["generated_optical_photons_per_neutron"] = sum(
            event.generated for event in groups[thickness]
        ) / len(groups[thickness])
    generated = (
        estimates[24]["generated_optical_photons_per_neutron"]
        / estimates[4]["generated_optical_photons_per_neutron"]
    )
    scintillation = estimates[24]["production"] / estimates[4]["production"]
    collection = estimates[24]["collection"] / estimates[4]["collection"]
    net = estimates[24]["net"] / estimates[4]["net"]
    return {
        "schema_version": f"{SCHEMA_VERSION}-pathway-v1",
        "ratio_direction": "24-mm-over-4-mm",
        "metric_identity": {
            "generated_production": "generated optical photons per incident neutron",
            "scintillation_production": "scintillation photons per incident neutron",
            "collection": "aggregate SiPM entries divided by generated optical photons",
            "net": "aggregate SiPM entries per incident neutron",
        },
        "endpoint_estimates": {str(key): value for key, value in estimates.items()},
        "generated_optical_production_ratio_24_over_4": generated,
        "scintillation_production_ratio_24_over_4": scintillation,
        "collection_ratio_24_over_4": collection,
        "observed_net_ratio_24_over_4": net,
        "factorization_product": generated * collection,
        "factorization_relative_residual": generated * collection / net - 1.0,
    }


def root_set_hash(audited_roots: Mapping[str, v1.AuditedRoot]) -> str:
    payload = json.dumps(
        {key: value.sha256 for key, value in sorted(audited_roots.items())},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def direct_summary_markdown(
    *,
    bundle: CampaignBundle,
    attempt_ids: Sequence[str],
    job_ids: Sequence[str],
    result: core.AnalysisResult,
    pilot_distribution_rows: Sequence[Mapping[str, object]],
    pathway: Mapping[str, object],
) -> str:
    prefix = [
        "# Direct BC-S1 Production Analysis",
        "",
        (
            "This reads the ordinary finalized campaign and creates no scheduler "
            "action or Geant4 event."
        ),
        "",
        f"- Campaign: `{bundle.campaign_id}`.",
        f"- Simulation commit: `{bundle.git_commit}`.",
        f"- Attempt IDs: `{', '.join(attempt_ids)}`.",
        f"- Slurm job IDs: `{', '.join(job_ids)}`.",
        "- Finalized sample: `32` tasks, `8,000` events; `4,000` per endpoint.",
        "",
        "## Production / collection / net decomposition",
        "",
        (
            "- Generated optical production 24/4: "
            f"`{float(pathway['generated_optical_production_ratio_24_over_4']):.6g}`."
        ),
        (
            "- Scintillation production 24/4: "
            f"`{float(pathway['scintillation_production_ratio_24_over_4']):.6g}`."
        ),
        f"- Optical collection 24/4: `{float(pathway['collection_ratio_24_over_4']):.6g}`.",
        (
            "- Observed net SiPM response 24/4: "
            f"`{float(pathway['observed_net_ratio_24_over_4']):.6g}`."
        ),
        "",
        "The production gain exceeds the collection loss in this fixed model.",
        "",
    ]
    core_lines = core.summary_markdown(result, pilot_distribution_rows).splitlines()
    if core_lines and core_lines[0].startswith("# "):
        core_lines = core_lines[2:]
    return "\n".join([*prefix, *core_lines, ""])


def main() -> int:
    args = parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    finalized_dir = (
        args.finalized_dir.expanduser().resolve()
        if args.finalized_dir is not None
        else campaign_dir / "finalized"
    )
    pilot_dir = args.pilot_campaign_dir.expanduser().resolve()
    pilot_analysis_dir = (
        args.pilot_analysis_dir.expanduser().resolve()
        if args.pilot_analysis_dir is not None
        else pilot_dir / "finalized" / "analysis-v2"
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else finalized_dir / "direct-analysis"
    )
    temp_dir: Path | None = None
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
            raise ValueError("direct BC-S1 analysis requires NumPy and uproot") from exc

        bundle, validation, _, audited_roots = v1.validate_identity(
            campaign_dir, finalized_dir
        )
        validate_direct_bc_s1_bundle(bundle)
        if validation.get("accepted_statistical_evidence") is not True:
            raise ValueError("direct BC-S1 analysis refuses non-accepted evidence")
        pilot_bundle = v1.load_campaign(pilot_dir, verify_external_artifacts=True)
        pilot_record = pilot_baseline_record(pilot_bundle, pilot_analysis_dir)
        pilot, pilot_distribution_rows = core.load_pilot_baseline(
            {"pilot_baseline": pilot_record}, np
        )

        task_rows = read_table(finalized_dir / "task_index.tsv", delimiter="\t")
        groups, blocks, located, task_ids = load_direct_events(
            campaign_dir, finalized_dir, bundle, task_rows, audited_roots
        )
        result = core.analyze_event_groups(
            groups, blocks, located, task_ids, pilot=pilot, np=np
        )
        result = core.AnalysisResult(
            result.endpoint_rows,
            result.distribution_rows,
            result.loo_rows,
            result.trajectory_rows,
            {
                **result.primary,
                "schema_version": f"{SCHEMA_VERSION}-primary-v1",
                "source_route": DIRECT_ROUTE,
            },
            {
                **result.eligibility,
                "schema_version": f"{SCHEMA_VERSION}-numeric-eligibility-v1",
                "source_route": DIRECT_ROUTE,
            },
        )
        pathway = pathway_decomposition(groups)
        if not math.isclose(
            float(pathway["observed_net_ratio_24_over_4"]),
            float(result.primary["ratio"]),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("pathway and primary ratio do not reconcile")
        attempt_ids = sorted({row["attempt_id"] for row in task_rows})
        job_ids = sorted({row["slurm_job_id"] for row in task_rows})

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        core.write_csv(
            temp_dir / "endpoint_estimates.csv", core.ENDPOINT_FIELDS, result.endpoint_rows
        )
        core.write_csv(
            temp_dir / "distribution_diagnostics.csv",
            core.DISTRIBUTION_FIELDS,
            result.distribution_rows,
        )
        core.write_csv(
            temp_dir / "pilot_distribution_diagnostics.csv",
            core.DISTRIBUTION_FIELDS,
            pilot_distribution_rows,
        )
        core.write_csv(temp_dir / "block_loo.csv", core.LOO_FIELDS, result.loo_rows)
        core.write_csv(
            temp_dir / "precision_trajectory.csv",
            core.TRAJECTORY_FIELDS,
            result.trajectory_rows,
        )
        write_json(temp_dir / "primary_contrast.json", result.primary)
        write_json(temp_dir / "numeric_eligibility.json", result.eligibility)
        write_json(temp_dir / "pathway_decomposition.json", pathway)
        analysis_config = {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "route": DIRECT_ROUTE,
            "campaign_id": bundle.campaign_id,
            "campaign_directory": str(campaign_dir),
            "plan_hash": bundle.plan_hash,
            "simulation_commit": bundle.git_commit,
            "environment_identity": bundle.environment.get("identity_hash"),
            "direct_production_identity": bundle.manifest["direct_production"][
                "identity_hash"
            ],
            "task_count": len(task_rows),
            "event_count": sum(int(row["events"]) for row in task_rows),
            "attempt_ids": attempt_ids,
            "slurm_job_ids": job_ids,
            "input_identity": {
                "finalized_sha256s_sha256": sha256_file(
                    finalized_dir / "SHA256SUMS"
                ),
                "task_index_sha256": sha256_file(finalized_dir / "task_index.tsv"),
                "event_audit_sha256": sha256_file(finalized_dir / "event_audit.json"),
                "audited_root_set_sha256": root_set_hash(audited_roots),
                "pilot": pilot_record,
            },
            "accepted_statistical_evidence": True,
            "analysis_git": analysis_git,
            "analyzer": {
                "implementation": "hpc/osc/analyze_steel_module_direct_bc_s1.py",
                "sha256": sha256_file(Path(__file__).resolve()),
                "production_core_sha256": sha256_file(Path(core.__file__).resolve()),
                "production_core_schema": core.SCHEMA_VERSION,
                "python_version": sys.version.split()[0],
                "numpy_version": str(np.__version__),
                "uproot_version": str(uproot.__version__),
            },
            "statistics": {
                "bootstrap_seed": core.BOOTSTRAP_SEED,
                "bootstrap_resamples": core.BOOTSTRAP_RESAMPLES,
                "generator": "numpy.random.Generator(numpy.random.PCG64)",
                "confidence_interval": "95% event-bootstrap percentile interval",
                "leave_one_block_out_evaluations": 32,
                "relative_half_width_target": core.PRECISION_TARGET,
                "loo_success_threshold": core.LOO_SUCCESS,
                "loo_continue_ceiling": core.LOO_CONTINUE,
            },
            "policy": {
                "pilot_events_in_production_estimate": False,
                "automatic_decision": False,
                "automatic_submission": False,
                "human_tail_review_required": True,
            },
            "outputs": {
                "endpoint_estimates": "endpoint_estimates.csv",
                "distribution_diagnostics": "distribution_diagnostics.csv",
                "pilot_distribution_diagnostics": "pilot_distribution_diagnostics.csv",
                "block_loo": "block_loo.csv",
                "precision_trajectory": "precision_trajectory.csv",
                "primary_contrast": "primary_contrast.json",
                "numeric_eligibility": "numeric_eligibility.json",
                "pathway_decomposition": "pathway_decomposition.json",
                "summary": "summary.md",
            },
        }
        write_json(temp_dir / "analysis_config.json", analysis_config)
        (temp_dir / "summary.md").write_text(
            direct_summary_markdown(
                bundle=bundle,
                attempt_ids=attempt_ids,
                job_ids=job_ids,
                result=result,
                pilot_distribution_rows=pilot_distribution_rows,
                pathway=pathway,
            ),
            encoding="utf-8",
        )
        write_recursive_checksums(temp_dir)
        verify_checksum_manifest(
            temp_dir,
            required=(
                "endpoint_estimates.csv",
                "distribution_diagnostics.csv",
                "pilot_distribution_diagnostics.csv",
                "block_loo.csv",
                "precision_trajectory.csv",
                "primary_contrast.json",
                "numeric_eligibility.json",
                "pathway_decomposition.json",
                "analysis_config.json",
                "summary.md",
            ),
        )
        publish_lock = core.acquire_publish_lock(output_dir)
        if output_dir.exists() or output_dir.is_symlink():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        os.rename(temp_dir, output_dir)
        temp_dir = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot analyze direct BC-S1 campaign: {exc}", file=sys.stderr)
        return 1
    finally:
        if publish_lock is not None and publish_lock.exists():
            publish_lock.rmdir()
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)

    print(f"Analyzed direct BC-S1 into {output_dir}")
    print(
        "Observed net 24/4: "
        f"{float(result.primary['ratio']):.6g} "
        f"[{float(result.primary['ci95_low']):.6g}, "
        f"{float(result.primary['ci95_high']):.6g}]"
    )
    print(
        f"Relative half-width: {100 * float(result.primary['relative_half_width']):.2f}%; "
        "maximum LOO shift: "
        f"{100 * float(result.primary['max_leave_one_block_out_relative_shift']):.2f}%"
    )
    print(
        "Numeric review choices: "
        + ", ".join(result.eligibility["numeric_eligible_decisions"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
