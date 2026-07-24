#!/usr/bin/env python3
"""Finalize a steel-module campaign with an integrated event-level audit."""

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

from audit_realistic_neutron_campaign import (
    SUMMARY_FLOAT_FIELDS,
    SUMMARY_INTEGER_FIELDS,
)
from realistic_neutron_event_audit import (
    SIPM_SENSOR_FIELDS,
    read_and_audit_root,
)
from record_realistic_neutron_task_result import (
    integer_field,
    read_single_csv_row,
)
from record_steel_module_task_result import validate_run_config
from steel_module_campaign_lib import (
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


TASK_INDEX_FIELDS = (
    "task_index",
    "logical_task_id",
    "attempt_id",
    "slurm_job_id",
    "slurm_array_index",
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
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
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_blocks",
    "events",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    *SIPM_SENSOR_FIELDS,
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
    "sipm_sensor_0_photons_per_neutron",
    "sipm_sensor_1_photons_per_neutron",
    "sipm_sensor_2_photons_per_neutron",
    "sipm_sensor_3_photons_per_neutron",
    "interaction_fraction",
    "generated_optical_zero_fraction",
    "scintillation_zero_fraction",
    "sipm_detected_zero_fraction",
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
    event_report: dict[str, int | float]


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
    parser.add_argument("--bootstrap-seed", type=int, default=20260715)
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
        raise ValueError(f"attempt task map has non-contiguous indexes: {path}")
    logical_ids = [row["logical_task_id"] for row in rows]
    if len(set(logical_ids)) != len(logical_ids):
        raise ValueError(f"attempt task map contains duplicate tasks: {path}")
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
            "g4_data_manifest_sha256": bundle.environment["g4_data_manifest"][
                "sha256"
            ],
            "executable_sha256": bundle.environment["build_artifact"]["sha256"],
        }
        for key, value in expected.items():
            if attempt.get(key) != value:
                raise ValueError(f"attempt {attempt_id} has mismatched {key}")
        if not attempt["slurm_job_id"].isdigit():
            raise ValueError(f"attempt {attempt_id} has invalid Slurm job ID")

        attempt_record = load_json(
            bundle.directory / "attempts" / attempt_id / "attempt.json"
        )
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
                raise ValueError(
                    f"attempt {attempt_id} has mismatched attempt.json {key}"
                )

        task_map_path = resolve_campaign_path(
            bundle.directory, attempt["attempt_tasks_tsv"]
        )
        rows = read_attempt_task_map(task_map_path)
        if len(rows) != int(attempt["task_count"]):
            raise ValueError(f"attempt {attempt_id} task count mismatch")
        if attempt["array_spec"] != f"1-{len(rows)}":
            raise ValueError(f"attempt {attempt_id} array specification mismatch")
        attempt_scan_args = read_scan_args(
            resolve_campaign_path(bundle.directory, attempt["scan_args_file"])
        )
        expected_scan_args = tuple(
            bundle.scan_args[int(row["task_index"]) - 1] for row in rows
        )
        if attempt_scan_args != expected_scan_args:
            raise ValueError(f"attempt {attempt_id} scan plan mismatch")

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
        selected.append((attempt, rows))
    return selected


def marker_artifact_paths(
    bundle: CampaignBundle, marker: dict[str, Any]
) -> dict[str, Path]:
    artifacts = marker.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("task result artifacts must be an object")
    paths: dict[str, Path] = {}
    for key in (
        "run_config",
        "points",
        "macro",
        "simulation_log",
        "root",
        "summary",
        "efficiency_map",
    ):
        record = artifacts.get(key)
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError(f"invalid task-result artifact {key}")
        path = resolve_campaign_path(bundle.directory, record["path"])
        if not path.is_file() or path.stat().st_size != record.get("size_bytes"):
            raise ValueError(f"missing or changed task-result artifact {key}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or sha256_file(path) != digest:
            raise ValueError(f"task-result artifact checksum mismatch: {key}")
        paths[key] = path
    return paths


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
        "tile_thickness_mm": task.tile_thickness_mm,
        "sipm_layout": task.sipm_layout,
        "absorber_transverse_mm": task.absorber_transverse_mm,
        "seed1": task.seed1,
        "seed2": task.seed2,
        "events": task.events,
    }
    for key, value in expected_marker.items():
        if marker.get(key) != value:
            raise ValueError(f"task-result marker has mismatched {key}")

    paths = marker_artifact_paths(bundle, marker)
    config = load_json(paths["run_config"])
    validate_run_config(config, task, attempt["attempt_id"], bundle)
    _, summary = read_single_csv_row(paths["summary"])
    if integer_field(summary, "events") != task.events:
        raise ValueError("task summary event count mismatch")
    if integer_field(summary, "committed_events") != task.events:
        raise ValueError("task summary committed-event count mismatch")

    _, event_report = read_and_audit_root(
        paths["root"],
        expected_events=task.events,
        context=task.logical_task_id,
    )
    missing_sensor_totals = [
        field for field in SIPM_SENSOR_FIELDS if field not in event_report
    ]
    if missing_sensor_totals:
        raise ValueError(
            "event schema lacks per-SiPM totals: " + ", ".join(missing_sensor_totals)
        )
    for field in SUMMARY_INTEGER_FIELDS:
        if int(summary[field]) != int(event_report[field]):
            raise ValueError(
                f"event audit disagrees with summary integer field {field}"
            )
    for field in SUMMARY_FLOAT_FIELDS:
        if not math.isclose(
            float(summary[field]),
            float(event_report[field]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(f"event audit disagrees with summary field {field}")

    return Candidate(
        task=task,
        attempt=attempt,
        array_index=array_index,
        marker_path=marker_path,
        marker=marker,
        run_config_path=paths["run_config"],
        summary_path=paths["summary"],
        summary=summary,
        event_report=event_report,
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
                raise ValueError("attempt contains an unknown or mismatched task")
            try:
                candidates[logical_id].append(
                    audit_candidate(bundle, task, attempt, int(row["array_index"]))
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                invalid.append(
                    {
                        "logical_task_id": logical_id,
                        "attempt_id": attempt["attempt_id"],
                        "reason": str(exc),
                    }
                )
    return candidates, invalid


def load_selection(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.expanduser().open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    if not {"logical_task_id", "attempt_id"}.issubset(reader.fieldnames or []):
        raise ValueError("selection file needs logical_task_id and attempt_id columns")
    selection: dict[str, str] = {}
    for row in rows:
        logical_id = row["logical_task_id"]
        if not logical_id or logical_id in selection or not row["attempt_id"]:
            raise ValueError("selection file contains empty or duplicate task identities")
        selection[logical_id] = row["attempt_id"]
    return selection


def select_candidates(
    bundle: CampaignBundle,
    candidates: dict[str, list[Candidate]],
    selection: dict[str, str],
) -> list[Candidate]:
    known = task_by_logical_id(bundle)
    unknown = set(selection) - set(known)
    if unknown:
        raise ValueError(f"selection file contains unknown tasks: {sorted(unknown)}")
    selected: list[Candidate] = []
    for task in bundle.tasks:
        valid = candidates.get(task.logical_task_id, [])
        requested = selection.get(task.logical_task_id)
        if requested is not None:
            matches = [item for item in valid if item.attempt["attempt_id"] == requested]
            if len(matches) != 1:
                raise ValueError(
                    f"selected attempt is not a unique valid success for "
                    f"{task.logical_task_id}: {requested}"
                )
            selected.append(matches[0])
        elif len(valid) == 1:
            selected.append(valid[0])
        elif not valid:
            raise ValueError(f"no valid successful attempt for {task.logical_task_id}")
        else:
            attempt_ids = [item.attempt["attempt_id"] for item in valid]
            raise ValueError(
                f"ambiguous duplicate successes for {task.logical_task_id}: "
                f"{attempt_ids}; provide --selection-file"
            )
    return selected


def artifact_path(candidate: Candidate, key: str) -> str:
    return str(candidate.marker["artifacts"][key]["path"])


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
                **asdict(task),
                "run_config": artifact_path(candidate, "run_config"),
                "macro": artifact_path(candidate, "macro"),
                "simulation_log": artifact_path(candidate, "simulation_log"),
                "root": artifact_path(candidate, "root"),
                "summary": artifact_path(candidate, "summary"),
                "efficiency_map": artifact_path(candidate, "efficiency_map"),
            }
        )
    return rows


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else math.nan


def configuration_rows(selected: Iterable[Candidate]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[Candidate]] = defaultdict(list)
    for candidate in selected:
        task = candidate.task
        key = (
            task.stage,
            task.tile_thickness_mm,
            task.sipm_layout,
            task.absorber_transverse_mm,
            task.x_mm,
            task.y_mm,
        )
        groups[key].append(candidate)

    integer_fields = (
        "events",
        "generated_optical_photons",
        "scintillation_photons",
        "sipm_detected_photons",
        *SIPM_SENSOR_FIELDS,
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
    float_fields = ("steel_edep_sum_mev", "tile_edep_sum_mev")
    rows: list[dict[str, object]] = []
    for key in sorted(groups, key=lambda item: (str(item[0]), str(item[2]), int(item[3]), int(item[1]))):
        candidates = groups[key]
        totals: dict[str, float] = {}
        for field in (*integer_fields, *float_fields):
            totals[field] = sum(float(item.event_report[field]) for item in candidates)
        events = totals["events"]
        generated = totals["generated_optical_photons"]
        scintillation = totals["scintillation_photons"]
        sipm = totals["sipm_detected_photons"]
        row: dict[str, object] = {
            "stage": key[0],
            "tile_thickness_mm": key[1],
            "sipm_layout": key[2],
            "absorber_transverse_mm": key[3],
            "x_mm": key[4],
            "y_mm": key[5],
            "seed_blocks": len(candidates),
        }
        for field in integer_fields:
            row[field] = int(totals[field])
        for field in float_fields:
            row[field] = totals[field]
        row.update(
            {
                "production_scint_photons_per_neutron": safe_ratio(
                    scintillation, events
                ),
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
        for index, field in enumerate(SIPM_SENSOR_FIELDS):
            row[f"sipm_sensor_{index}_photons_per_neutron"] = safe_ratio(
                totals[field], events
            )
        rows.append(row)
    return rows


def write_csv(
    path: Path,
    fieldnames: Iterable[str],
    rows: Iterable[dict[str, object]],
) -> None:
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
        write_tsv(temp_dir / "task_index.tsv", TASK_INDEX_FIELDS, index_rows)
        write_csv(
            temp_dir / "configuration_summary.csv",
            CONFIGURATION_FIELDS,
            configurations,
        )
        event_tasks = [
            {
                "logical_task_id": item.task.logical_task_id,
                "tile_thickness_mm": item.task.tile_thickness_mm,
                "sipm_layout": item.task.sipm_layout,
                "absorber_transverse_mm": item.task.absorber_transverse_mm,
                "root": artifact_path(item, "root"),
                "root_sha256": item.marker["artifacts"]["root"]["sha256"],
                "audit": item.event_report,
            }
            for item in selected
        ]
        atomic_write_json(
            temp_dir / "event_audit.json",
            {
                "schema_version": "steel-module-event-audit-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "valid": True,
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "git_commit": bundle.git_commit,
                "environment_identity": bundle.environment.get("identity_hash"),
                "accepted_statistical_evidence": bundle.environment.get(
                    "accepted_statistical_evidence"
                )
                is True,
                "task_count": len(event_tasks),
                "tasks": event_tasks,
            },
        )
        atomic_write_json(
            temp_dir / "analysis_config.json",
            {
                "schema_version": "steel-module-analysis-config-v1",
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "root_tree": "scan",
                "configuration_axes": [
                    "sipm_layout",
                    "tile_thickness_mm",
                    "absorber_transverse_mm",
                ],
                "bootstrap": {
                    "status": "pending_stage_analysis",
                    "seed": bootstrap_seed,
                    "resamples": bootstrap_resamples,
                    "confidence_level": 0.95,
                    "resampling_unit": "event within configuration",
                    "estimators": {
                        "production": (
                            "sum(scintillation_photons) / incident_neutrons"
                        ),
                        "collection": (
                            "sum(sipm_detected_photons) / "
                            "sum(generated_optical_photons)"
                        ),
                        "net_response": (
                            "sum(sipm_detected_photons) / incident_neutrons"
                        ),
                        "per_sensor_response": (
                            "sum(sensor_detected_photons) / incident_neutrons"
                        ),
                    },
                },
                "source_task_index": "task_index.tsv",
                "source_event_audit": "event_audit.json",
            },
        )
        atomic_write_json(
            temp_dir / "validation_report.json",
            {
                "schema_version": "steel-module-validation-report-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "valid": True,
                "event_audit_integrated": True,
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
            },
        )
        write_checksums(temp_dir)
        os.replace(temp_dir, output_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def main() -> int:
    args = parse_args()
    if args.bootstrap_seed < 0 or args.bootstrap_resamples <= 0:
        print(
            "bootstrap seed must be non-negative and resamples must be positive",
            file=sys.stderr,
        )
        return 2
    try:
        bundle = load_campaign(args.campaign_dir, verify_external_artifacts=True)
        if bundle.environment.get("mode") != "osc-production":
            raise ValueError("only an osc-production campaign may be finalized")
        attempts = load_submitted_attempts(bundle)
        candidates, invalid = collect_candidates(bundle, attempts)
        selected = select_candidates(
            bundle, candidates, load_selection(args.selection_file)
        )
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
        print(f"Cannot finalize steel-module campaign: {exc}", file=sys.stderr)
        return 1

    print(f"Finalized and event-audited {len(selected)} tasks into {output_dir}")
    print(f"Ignored invalid attempt results: {len(invalid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
