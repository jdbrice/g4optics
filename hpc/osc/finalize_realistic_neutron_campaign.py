#!/usr/bin/env python3
"""Audit and finalize one complete realistic-neutron campaign."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from realistic_neutron_campaign_lib import (
    RESULT_SCHEMA_VERSION,
    CampaignBundle,
    CampaignTask,
    atomic_write_json,
    load_campaign,
    load_json,
    read_attempt_rows,
    read_scan_args,
    resolve_campaign_path,
    sha256_file,
    task_by_logical_id,
    write_tsv,
)
from record_realistic_neutron_task_result import (
    integer_field,
    read_single_csv_row,
    validate_run_config,
)


TASK_INDEX_FIELDS = (
    "task_index",
    "logical_task_id",
    "attempt_id",
    "slurm_job_id",
    "slurm_array_index",
    "stage",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_block",
    "events",
    "seed1",
    "seed2",
    "configuration_hash",
    "run_config",
    "macro",
    "simulation_log",
    "root",
    "summary",
    "efficiency_map",
)

CONFIGURATION_FIELDS = (
    "stage",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_blocks",
    "events",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "cerenkov_photons",
    "steel_edep_sum_mev",
    "tile_edep_sum_mev",
    "primary_neutron_interaction_events",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "charged_tile_entry_events",
    "charged_tile_entry_count",
    "generated_optical_zero_events",
    "scintillation_zero_events",
    "sipm_detected_zero_events",
    "production_scint_photons_per_neutron",
    "collection_sipm_over_generated",
    "net_sipm_photons_per_neutron",
    "interaction_fraction",
    "generated_optical_zero_fraction",
    "scintillation_zero_fraction",
    "sipm_detected_zero_fraction",
)

RATIO_FIELDS = (
    "stage",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "events_4mm",
    "events_16mm",
    "production_4mm",
    "production_16mm",
    "production_ratio_16_over_4",
    "collection_4mm",
    "collection_16mm",
    "collection_ratio_16_over_4",
    "net_4mm",
    "net_16mm",
    "net_ratio_16_over_4",
    "bootstrap_status",
    "bootstrap_ci_level",
    "bootstrap_resamples",
)


@dataclass(frozen=True)
class Candidate:
    task: CampaignTask
    attempt: dict[str, str]
    array_index: int
    marker_path: Path
    marker: dict[str, Any]
    run_config_path: Path
    summary_path: Path
    summary: dict[str, str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to CAMPAIGN_DIR/finalized; must not already exist.",
    )
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="Optional TSV with logical_task_id and attempt_id for duplicate successes.",
    )
    parser.add_argument("--bootstrap-seed", type=int, default=20260714)
    parser.add_argument("--bootstrap-resamples", type=int, default=10000)
    return parser.parse_args()


def read_attempt_task_map(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    required = {"array_index", "task_index", "logical_task_id"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError(f"invalid attempt task map: {path}")
    if [row["array_index"] for row in rows] != [
        str(index) for index in range(1, len(rows) + 1)
    ]:
        raise ValueError(f"attempt task map has non-contiguous array indexes: {path}")
    logical_ids = [row["logical_task_id"] for row in rows]
    if len(set(logical_ids)) != len(logical_ids):
        raise ValueError(f"attempt task map contains duplicate logical tasks: {path}")
    return rows


def load_submitted_attempts(
    bundle: CampaignBundle,
) -> list[tuple[dict[str, str], list[dict[str, str]]]]:
    selected: list[tuple[dict[str, str], list[dict[str, str]]]] = []
    seen_attempt_ids: set[str] = set()
    for attempt in read_attempt_rows(bundle.directory):
        attempt_id = attempt["attempt_id"]
        if not attempt_id or attempt_id in seen_attempt_ids:
            raise ValueError(f"duplicate or empty attempt ID: {attempt_id!r}")
        seen_attempt_ids.add(attempt_id)
        if attempt["status"] != "submitted":
            continue
        expected = {
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "git_commit": bundle.git_commit,
            "image_sha256": bundle.environment["image"]["sha256"],
            "g4_data_manifest_sha256": bundle.environment["g4_data_manifest"]["sha256"],
            "executable_sha256": bundle.environment["build_artifact"]["sha256"],
        }
        for key, value in expected.items():
            if attempt.get(key) != value:
                raise ValueError(f"attempt {attempt_id} has mismatched {key}")
        if not attempt["slurm_job_id"].isdigit():
            raise ValueError(f"attempt {attempt_id} has invalid Slurm job ID")
        attempt_record_path = (
            bundle.directory / "attempts" / attempt_id / "attempt.json"
        )
        attempt_record = load_json(attempt_record_path)
        expected_record = {
            "attempt_id": attempt_id,
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "mode": attempt["mode"],
            "status": "submitted",
            "task_count": int(attempt["task_count"]),
            "slurm_job_id": attempt["slurm_job_id"],
        }
        for key, value in expected_record.items():
            if attempt_record.get(key) != value:
                raise ValueError(f"attempt {attempt_id} has mismatched attempt.json {key}")
        task_map_path = resolve_campaign_path(
            bundle.directory, attempt["attempt_tasks_tsv"]
        )
        rows = read_attempt_task_map(task_map_path)
        if len(rows) != int(attempt["task_count"]):
            raise ValueError(f"attempt {attempt_id} task count mismatch")
        if attempt["array_spec"] != f"1-{len(rows)}":
            raise ValueError(f"attempt {attempt_id} array specification mismatch")
        known_tasks = task_by_logical_id(bundle)
        for row in rows:
            task = known_tasks.get(row["logical_task_id"])
            if task is None:
                raise ValueError(f"attempt {attempt_id} contains an unknown task")
            for key, value in asdict(task).items():
                if row.get(key) != str(value):
                    raise ValueError(
                        f"attempt {attempt_id} task map has mismatched {key}"
                    )
        attempt_scan_args = read_scan_args(
            resolve_campaign_path(bundle.directory, attempt["scan_args_file"])
        )
        expected_scan_args = tuple(
            bundle.scan_args[int(row["task_index"]) - 1] for row in rows
        )
        if attempt_scan_args != expected_scan_args:
            raise ValueError(f"attempt {attempt_id} scan arguments mismatch")
        selected.append((attempt, rows))
    if not selected:
        raise ValueError("campaign has no submitted attempts")
    return selected


def load_selection(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.expanduser().open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if not {"logical_task_id", "attempt_id"}.issubset(reader.fieldnames or []):
            raise ValueError("selection file requires logical_task_id and attempt_id")
        rows = list(reader)
    selection: dict[str, str] = {}
    for row in rows:
        logical_id = row["logical_task_id"]
        attempt_id = row["attempt_id"]
        if not logical_id or not attempt_id or logical_id in selection:
            raise ValueError(f"invalid duplicate selection row: {row}")
        selection[logical_id] = attempt_id
    return selection


def resolve_artifact(
    campaign_dir: Path,
    marker: dict[str, Any],
    key: str,
    *,
    checksum_required: bool,
) -> Path:
    artifacts = marker.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get(key), dict):
        raise ValueError(f"result marker missing artifact {key}")
    record = artifacts[key]
    path_value = record.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise ValueError(f"result marker artifact {key} has no path")
    path = resolve_campaign_path(campaign_dir, path_value)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty result artifact {key}: {path}")
    if record.get("size_bytes") != path.stat().st_size:
        raise ValueError(f"result artifact size changed for {key}: {path}")
    digest = record.get("sha256")
    if checksum_required:
        if not isinstance(digest, str) or sha256_file(path) != digest:
            raise ValueError(f"result artifact checksum mismatch for {key}: {path}")
    return path


def audit_candidate(
    bundle: CampaignBundle,
    task: CampaignTask,
    attempt: dict[str, str],
    array_index: int,
) -> Candidate:
    marker_path = (
        bundle.directory
        / "attempts"
        / attempt["attempt_id"]
        / "tasks"
        / task.logical_task_id
        / "task_result.json"
    )
    marker = load_json(marker_path)
    expected_marker = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "campaign_id": bundle.campaign_id,
        "plan_hash": bundle.plan_hash,
        "attempt_id": attempt["attempt_id"],
        "task_index": task.task_index,
        "logical_task_id": task.logical_task_id,
        "configuration_hash": task.configuration_hash,
        "seed1": task.seed1,
        "seed2": task.seed2,
        "events": task.events,
    }
    for key, value in expected_marker.items():
        if marker.get(key) != value:
            raise ValueError(f"task result {marker_path} has mismatched {key}")

    slurm = marker.get("slurm")
    if not isinstance(slurm, dict):
        raise ValueError(f"task result {marker_path} has no Slurm identity")
    if slurm.get("array_job_id") != attempt["slurm_job_id"]:
        raise ValueError(f"task result {marker_path} has mismatched Slurm job ID")
    if slurm.get("array_task_id") != str(array_index):
        raise ValueError(f"task result {marker_path} has mismatched array index")

    artifact_paths = {
        key: resolve_artifact(
            bundle.directory,
            marker,
            key,
            checksum_required=True,
        )
        for key in (
            "run_config",
            "points",
            "macro",
            "simulation_log",
            "root",
            "summary",
            "efficiency_map",
        )
    }
    config = load_json(artifact_paths["run_config"])
    validate_run_config(config, task, attempt["attempt_id"], bundle)
    _, summary = read_single_csv_row(artifact_paths["summary"])
    if integer_field(summary, "events") != task.events:
        raise ValueError(f"summary events mismatch for {task.logical_task_id}")
    if integer_field(summary, "committed_events") != task.events:
        raise ValueError(f"summary committed events mismatch for {task.logical_task_id}")
    return Candidate(
        task=task,
        attempt=attempt,
        array_index=array_index,
        marker_path=marker_path,
        marker=marker,
        run_config_path=artifact_paths["run_config"],
        summary_path=artifact_paths["summary"],
        summary=summary,
    )


def collect_candidates(
    bundle: CampaignBundle,
    attempts: list[tuple[dict[str, str], list[dict[str, str]]]],
) -> tuple[dict[str, list[Candidate]], list[dict[str, str]]]:
    tasks = task_by_logical_id(bundle)
    candidates: dict[str, list[Candidate]] = defaultdict(list)
    invalid: list[dict[str, str]] = []
    for attempt, rows in attempts:
        for row in rows:
            logical_id = row["logical_task_id"]
            task = tasks.get(logical_id)
            if task is None or int(row["task_index"]) != task.task_index:
                raise ValueError(
                    f"attempt {attempt['attempt_id']} contains an unknown or mismatched task"
                )
            for key, expected in asdict(task).items():
                if row.get(key) != str(expected):
                    raise ValueError(
                        f"attempt {attempt['attempt_id']} task map has mismatched {key} "
                        f"for {logical_id}"
                    )
            try:
                candidate = audit_candidate(
                    bundle, task, attempt, int(row["array_index"])
                )
                candidates[logical_id].append(candidate)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                invalid.append(
                    {
                        "logical_task_id": logical_id,
                        "attempt_id": attempt["attempt_id"],
                        "reason": str(exc),
                    }
                )
    return candidates, invalid


def select_candidates(
    bundle: CampaignBundle,
    candidates: dict[str, list[Candidate]],
    selection: dict[str, str],
) -> list[Candidate]:
    known = task_by_logical_id(bundle)
    unknown_selection = set(selection) - set(known)
    if unknown_selection:
        raise ValueError(f"selection file contains unknown tasks: {sorted(unknown_selection)}")
    selected: list[Candidate] = []
    for task in bundle.tasks:
        valid = candidates.get(task.logical_task_id, [])
        requested = selection.get(task.logical_task_id)
        if requested is not None:
            matches = [item for item in valid if item.attempt["attempt_id"] == requested]
            if len(matches) != 1:
                raise ValueError(
                    f"selected attempt is not a unique valid success for {task.logical_task_id}: {requested}"
                )
            selected.append(matches[0])
        elif len(valid) == 1:
            selected.append(valid[0])
        elif not valid:
            raise ValueError(f"no valid successful attempt for {task.logical_task_id}")
        else:
            attempt_ids = [item.attempt["attempt_id"] for item in valid]
            raise ValueError(
                f"ambiguous duplicate successes for {task.logical_task_id}: {attempt_ids}; "
                "provide --selection-file"
            )
    return selected


def artifact_path(candidate: Candidate, key: str) -> str:
    return candidate.marker["artifacts"][key]["path"]


def task_index_rows(selected: Iterable[Candidate]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for candidate in selected:
        task = candidate.task
        rows.append(
            {
                "task_index": task.task_index,
                "logical_task_id": task.logical_task_id,
                "attempt_id": candidate.attempt["attempt_id"],
                "slurm_job_id": candidate.attempt["slurm_job_id"],
                "slurm_array_index": candidate.array_index,
                "stage": task.stage,
                "tile_thickness_mm": task.tile_thickness_mm,
                "absorber_transverse_mm": task.absorber_transverse_mm,
                "x_mm": task.x_mm,
                "y_mm": task.y_mm,
                "seed_block": task.seed_block,
                "events": task.events,
                "seed1": task.seed1,
                "seed2": task.seed2,
                "configuration_hash": task.configuration_hash,
                "run_config": artifact_path(candidate, "run_config"),
                "macro": artifact_path(candidate, "macro"),
                "simulation_log": artifact_path(candidate, "simulation_log"),
                "root": artifact_path(candidate, "root"),
                "summary": artifact_path(candidate, "summary"),
                "efficiency_map": artifact_path(candidate, "efficiency_map"),
            }
        )
    return rows


INTEGER_SUM_FIELDS = (
    "events",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "cerenkov_photons",
    "primary_neutron_interaction_events",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "charged_tile_entry_events",
    "charged_tile_entry_count",
    "generated_optical_zero_events",
    "scintillation_zero_events",
    "sipm_detected_zero_events",
)
FLOAT_SUM_FIELDS = ("steel_edep_sum_mev", "tile_edep_sum_mev")


def numeric_field(row: dict[str, str], key: str) -> float:
    try:
        value = float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid summary field {key}: {row.get(key)!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite additive summary field {key}: {value}")
    return value


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else math.nan


def configuration_rows(selected: Iterable[Candidate]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[Candidate]] = defaultdict(list)
    for candidate in selected:
        task = candidate.task
        key = (
            task.stage,
            task.tile_thickness_mm,
            task.absorber_transverse_mm,
            task.x_mm,
            task.y_mm,
        )
        groups[key].append(candidate)

    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda value: (value[0], value[2], value[3], value[4], value[1])):
        candidates = groups[key]
        stage, tile, absorber, x_mm, y_mm = key
        totals: dict[str, float] = {}
        for field in INTEGER_SUM_FIELDS + FLOAT_SUM_FIELDS:
            totals[field] = sum(numeric_field(candidate.summary, field) for candidate in candidates)
        events = totals["events"]
        generated = totals["generated_optical_photons"]
        scintillation = totals["scintillation_photons"]
        sipm = totals["sipm_detected_photons"]
        row: dict[str, object] = {
            "stage": stage,
            "tile_thickness_mm": tile,
            "absorber_transverse_mm": absorber,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "seed_blocks": len(candidates),
        }
        for field in INTEGER_SUM_FIELDS:
            row[field] = int(totals[field])
        for field in FLOAT_SUM_FIELDS:
            row[field] = totals[field]
        row.update(
            {
                "production_scint_photons_per_neutron": safe_ratio(scintillation, events),
                "collection_sipm_over_generated": safe_ratio(sipm, generated),
                "net_sipm_photons_per_neutron": safe_ratio(sipm, events),
                "interaction_fraction": safe_ratio(
                    totals["primary_neutron_interaction_events"], events
                ),
                "generated_optical_zero_fraction": safe_ratio(
                    totals["generated_optical_zero_events"], events
                ),
                "scintillation_zero_fraction": safe_ratio(
                    totals["scintillation_zero_events"], events
                ),
                "sipm_detected_zero_fraction": safe_ratio(
                    totals["sipm_detected_zero_events"], events
                ),
            }
        )
        rows.append(row)
    return rows


def thickness_ratio_rows(
    configurations: list[dict[str, object]], bootstrap_resamples: int
) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], dict[int, dict[str, object]]] = defaultdict(dict)
    for row in configurations:
        key = (row["stage"], row["absorber_transverse_mm"], row["x_mm"], row["y_mm"])
        groups[key][int(row["tile_thickness_mm"])] = row
    ratios: list[dict[str, object]] = []
    for key in sorted(groups):
        pair = groups[key]
        if set(pair) != {4, 16}:
            raise ValueError(f"thickness pair is incomplete for configuration {key}")
        thin = pair[4]
        thick = pair[16]
        production4 = float(thin["production_scint_photons_per_neutron"])
        production16 = float(thick["production_scint_photons_per_neutron"])
        collection4 = float(thin["collection_sipm_over_generated"])
        collection16 = float(thick["collection_sipm_over_generated"])
        net4 = float(thin["net_sipm_photons_per_neutron"])
        net16 = float(thick["net_sipm_photons_per_neutron"])
        ratios.append(
            {
                "stage": key[0],
                "absorber_transverse_mm": key[1],
                "x_mm": key[2],
                "y_mm": key[3],
                "events_4mm": thin["events"],
                "events_16mm": thick["events"],
                "production_4mm": production4,
                "production_16mm": production16,
                "production_ratio_16_over_4": safe_ratio(production16, production4),
                "collection_4mm": collection4,
                "collection_16mm": collection16,
                "collection_ratio_16_over_4": safe_ratio(collection16, collection4),
                "net_4mm": net4,
                "net_16mm": net16,
                "net_ratio_16_over_4": safe_ratio(net16, net4),
                "bootstrap_status": "pending_event_level_analysis",
                "bootstrap_ci_level": 0.95,
                "bootstrap_resamples": bootstrap_resamples,
            }
        )
    return ratios


def write_csv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(output_dir: Path) -> None:
    paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    with (output_dir / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in paths:
            stream.write(f"{sha256_file(path)}  {path.name}\n")


def finalize_outputs(
    bundle: CampaignBundle,
    output_dir: Path,
    selected: list[Candidate],
    invalid: list[dict[str, str]],
    *,
    bootstrap_seed: int,
    bootstrap_resamples: int,
) -> None:
    if output_dir.exists():
        raise ValueError(f"refusing to overwrite finalization directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        index_rows = task_index_rows(selected)
        configurations = configuration_rows(selected)
        ratios = thickness_ratio_rows(configurations, bootstrap_resamples)
        write_tsv(temp_dir / "task_index.tsv", TASK_INDEX_FIELDS, index_rows)
        write_csv(
            temp_dir / "configuration_summary.csv",
            CONFIGURATION_FIELDS,
            configurations,
        )
        write_csv(temp_dir / "thickness_ratios.csv", RATIO_FIELDS, ratios)
        analysis_config = {
            "schema_version": "realistic-neutron-analysis-config-v1",
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "bootstrap": {
                "status": "pending_event_level_analysis",
                "seed": bootstrap_seed,
                "resamples": bootstrap_resamples,
                "confidence_level": 0.95,
                "resampling_unit": "event within tile-thickness configuration",
                "estimators": {
                    "production": "sum(scintillation_photons) / incident_neutrons",
                    "collection": "sum(sipm_detected_photons) / sum(generated_optical_photons)",
                    "net_response": "sum(sipm_detected_photons) / incident_neutrons",
                    "thickness_ratio": "estimator_16mm / estimator_4mm",
                },
            },
            "source_task_index": "task_index.tsv",
            "root_tree": "scan",
        }
        atomic_write_json(temp_dir / "analysis_config.json", analysis_config)
        report = {
            "schema_version": "realistic-neutron-validation-report-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "valid": True,
            "accepted_statistical_evidence": bundle.environment.get(
                "accepted_statistical_evidence"
            ),
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "git_commit": bundle.git_commit,
            "environment_identity": bundle.environment.get("identity_hash"),
            "expected_tasks": len(bundle.tasks),
            "selected_tasks": len(selected),
            "invalid_attempt_results_ignored": invalid,
            "selected_attempts": {
                item.task.logical_task_id: item.attempt["attempt_id"]
                for item in selected
            },
        }
        atomic_write_json(temp_dir / "validation_report.json", report)
        write_checksums(temp_dir)
        os.replace(temp_dir, output_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def main() -> int:
    args = parse_args()
    if args.bootstrap_seed < 0 or args.bootstrap_resamples <= 0:
        print("bootstrap seed must be non-negative and resamples must be positive", file=sys.stderr)
        return 2
    try:
        bundle = load_campaign(args.campaign_dir, verify_external_artifacts=True)
        if bundle.environment.get("mode") != "osc-production":
            raise ValueError("only an osc-production campaign may be finalized")
        attempts = load_submitted_attempts(bundle)
        candidates, invalid = collect_candidates(bundle, attempts)
        selection = load_selection(args.selection_file)
        selected = select_candidates(bundle, candidates, selection)
        output_dir = (
            args.output_dir.expanduser().resolve()
            if args.output_dir is not None
            else bundle.directory / "finalized"
        )
        finalize_outputs(
            bundle,
            output_dir,
            selected,
            invalid,
            bootstrap_seed=args.bootstrap_seed,
            bootstrap_resamples=args.bootstrap_resamples,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot finalize realistic-neutron campaign: {exc}", file=sys.stderr)
        return 1

    print(f"Finalized {len(selected)} tasks into {output_dir}")
    print(f"Ignored invalid attempt results: {len(invalid)}")
    print("Event-level bootstrap remains pending and is pinned in analysis_config.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
