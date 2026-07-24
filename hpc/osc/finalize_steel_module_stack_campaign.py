#!/usr/bin/env python3
"""Finalize and causally audit one complete steel-module-stack-v1 campaign."""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import finalize_steel_module_campaign as base
from audit_realistic_neutron_campaign import SUMMARY_FLOAT_FIELDS, SUMMARY_INTEGER_FIELDS
from record_realistic_neutron_task_result import integer_field, read_single_csv_row
from record_steel_module_stack_task_result import validate_run_config
from steel_module_stack_campaign_lib import (
    RESULT_SCHEMA_VERSION,
    STACK_LAYERS,
    CampaignBundle,
    CampaignTask,
    atomic_write_json,
    load_campaign,
    load_json,
    read_attempt_rows,
    resolve_campaign_path,
    sha256_file,
    task_by_logical_id,
    write_tsv,
)
from steel_module_stack_event_audit import audit_root_file


def audit_candidate(
    bundle: CampaignBundle,
    task: CampaignTask,
    attempt: dict[str, str],
    array_index: int,
) -> base.Candidate:
    marker_path = (
        bundle.directory / "attempts" / attempt["attempt_id"] / "tasks"
        / task.logical_task_id / "task_result.json"
    )
    marker = load_json(marker_path)
    expected = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "campaign_id": bundle.campaign_id,
        "plan_hash": bundle.plan_hash,
        "attempt_id": attempt["attempt_id"],
        "task_index": task.task_index,
        "logical_task_id": task.logical_task_id,
        "configuration_hash": task.configuration_hash,
        "tile_thickness_mm": task.tile_thickness_mm,
        "seed_block": task.seed_block,
        "seed1": task.seed1,
        "seed2": task.seed2,
        "events": task.events,
        "stack_layers": STACK_LAYERS,
        "sensor_count": 20,
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise ValueError(f"stack task-result has mismatched {key}")
    paths = base.marker_artifact_paths(bundle, marker)
    config = load_json(paths["run_config"])
    validate_run_config(config, task, attempt["attempt_id"], bundle)
    _, summary = read_single_csv_row(paths["summary"])
    if integer_field(summary, "events") != task.events or integer_field(summary, "committed_events") != task.events:
        raise ValueError("stack summary event count mismatch")
    report = audit_root_file(str(paths["root"]), task.events, context=task.logical_task_id)
    scan_report = report["scan_audit"]
    for field in SUMMARY_INTEGER_FIELDS:
        if int(summary[field]) != int(scan_report[field]):
            raise ValueError(f"stack event audit disagrees with summary integer field {field}")
    for field in SUMMARY_FLOAT_FIELDS:
        if not math.isclose(float(summary[field]), float(scan_report[field]), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"stack event audit disagrees with summary field {field}")
    return base.Candidate(
        task=task,
        attempt=attempt,
        array_index=array_index,
        marker_path=marker_path,
        marker=marker,
        run_config_path=paths["run_config"],
        summary_path=paths["summary"],
        summary=summary,
        event_report=report,
    )


def artifact_path(candidate: base.Candidate, key: str) -> str:
    return str(candidate.marker["artifacts"][key]["path"])


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(item for item in directory.iterdir() if item.is_file() and item.name != "SHA256SUMS"):
            stream.write(f"{sha256_file(path)}  {path.name}\n")


def seed_registry(bundle: CampaignBundle) -> set[int]:
    record = bundle.manifest.get("artifacts", {}).get("excluded_seed_registry")
    if not isinstance(record, dict):
        return set()
    path = resolve_campaign_path(bundle.directory, record["path"])
    value = load_json(path)
    seeds = value.get("excluded_seeds", [])
    if not isinstance(seeds, list) or any(not isinstance(seed, int) for seed in seeds):
        raise ValueError("excluded seed registry lacks explicit integer seeds")
    return set(seeds)


def configuration_rows(selected: list[base.Candidate]) -> list[dict[str, object]]:
    groups: dict[int, list[base.Candidate]] = {}
    for candidate in selected:
        groups.setdefault(candidate.task.tile_thickness_mm, []).append(candidate)
    rows: list[dict[str, object]] = []
    for thickness in sorted(groups):
        candidates = groups[thickness]
        events = sum(item.event_report["events"] for item in candidates)
        generated = sum(item.event_report["generated_optical_photons"] for item in candidates)
        local = sum(item.event_report["local_origin_detected_photons"] for item in candidates)
        all_origin = sum(item.event_report["all_origin_detected_photons"] for item in candidates)
        cross = sum(item.event_report["cross_layer_detected_photons"] for item in candidates)
        unknown = sum(item.event_report["unknown_origin_detected_photons"] for item in candidates)
        rows.append(
            {
                "stage": candidates[0].task.stage,
                "tile_thickness_mm": thickness,
                "seed_blocks": len(candidates),
                "events": events,
                "generated_optical_photons": generated,
                "local_origin_detected_photons": local,
                "all_origin_detected_photons": all_origin,
                "cross_layer_detected_photons": cross,
                "unknown_origin_detected_photons": unknown,
                "generated_photons_per_neutron": generated / events,
                "local_collection": local / generated if generated else math.nan,
                "local_net_photons_per_neutron": local / events,
                "all_origin_collection": all_origin / generated if generated else math.nan,
                "all_origin_net_photons_per_neutron": all_origin / events,
                "cross_layer_fraction_of_detected": cross / all_origin if all_origin else math.nan,
            }
        )
    return rows


def finalize_outputs(
    bundle: CampaignBundle,
    output_dir: Path,
    selected: list[base.Candidate],
    invalid: list[dict[str, str]],
    *,
    bootstrap_seed: int,
    bootstrap_resamples: int,
) -> None:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite finalization directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        index_rows: list[dict[str, object]] = []
        for item in selected:
            index_rows.append(
                {
                    **asdict(item.task),
                    "attempt_id": item.attempt["attempt_id"],
                    "slurm_job_id": item.attempt["slurm_job_id"],
                    "slurm_array_index": item.array_index,
                    "run_config": artifact_path(item, "run_config"),
                    "macro": artifact_path(item, "macro"),
                    "simulation_log": artifact_path(item, "simulation_log"),
                    "root": artifact_path(item, "root"),
                    "root_sha256": item.marker["artifacts"]["root"]["sha256"],
                    "summary": artifact_path(item, "summary"),
                }
            )
        write_tsv(temp / "task_index.tsv", list(index_rows[0]), index_rows)
        configurations = configuration_rows(selected)
        write_csv(temp / "configuration_summary.csv", list(configurations[0]), configurations)
        event_tasks = [
            {
                "logical_task_id": item.task.logical_task_id,
                "tile_thickness_mm": item.task.tile_thickness_mm,
                "seed_block": item.task.seed_block,
                "root": artifact_path(item, "root"),
                "root_sha256": item.marker["artifacts"]["root"]["sha256"],
                "audit": item.event_report,
            }
            for item in selected
        ]
        atomic_write_json(
            temp / "event_audit.json",
            {
                "schema_version": "steel-module-stack-event-audit-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "valid": True,
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "git_commit": bundle.git_commit,
                "accepted_statistical_evidence": bundle.environment.get("accepted_statistical_evidence") is True,
                "task_count": len(selected),
                "event_count": sum(item.task.events for item in selected),
                "unknown_origin_detected_photons": 0,
                "tasks": event_tasks,
            },
        )
        production_seeds = [seed for item in selected for seed in (item.task.seed1, item.task.seed2)]
        excluded = seed_registry(bundle)
        overlap = sorted(set(production_seeds) & excluded)
        if overlap:
            raise ValueError(f"stack production seeds overlap excluded registry: {overlap[:4]}")
        atomic_write_json(
            temp / "seed_audit.json",
            {
                "schema_version": "steel-module-stack-seed-audit-v1",
                "valid": True,
                "campaign_id": bundle.campaign_id,
                "seed_count": len(production_seeds),
                "unique_seed_count": len(set(production_seeds)),
                "excluded_seed_count": len(excluded),
                "overlap_count": len(overlap),
                "derivation_domain": "steel-module-stack-v1",
            },
        )
        atomic_write_json(
            temp / "validation_report.json",
            {
                "schema_version": "steel-module-stack-validation-report-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "valid": True,
                "event_audit_integrated": True,
                "seed_audit_integrated": True,
                "accepted_statistical_evidence": bundle.environment.get("accepted_statistical_evidence"),
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "git_commit": bundle.git_commit,
                "expected_tasks": len(bundle.tasks),
                "selected_tasks": len(selected),
                "invalid_attempt_results_ignored": invalid,
                "bootstrap_contract": {"seed": bootstrap_seed, "resamples": bootstrap_resamples, "generator": "NumPy PCG64", "confidence_level": 0.95},
            },
        )
        write_checksums(temp)
        os.replace(temp, output_dir)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


# Reuse the mature ordinary-attempt/duplicate-selection machinery, replacing
# only the stack-specific identities, event audit, and final output contract.
base.CampaignBundle = CampaignBundle
base.CampaignTask = CampaignTask
base.RESULT_SCHEMA_VERSION = RESULT_SCHEMA_VERSION
base.load_campaign = load_campaign
base.load_json = load_json
base.read_attempt_rows = read_attempt_rows
base.resolve_campaign_path = resolve_campaign_path
base.task_by_logical_id = task_by_logical_id
base.validate_run_config = validate_run_config
base.audit_candidate = audit_candidate
base.finalize_outputs = finalize_outputs


if __name__ == "__main__":
    raise SystemExit(base.main())
