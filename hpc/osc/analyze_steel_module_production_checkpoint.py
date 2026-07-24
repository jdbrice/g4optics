#!/usr/bin/env python3
"""Analyze one checksum-sealed steel-module production checkpoint."""

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import analyze_steel_module_campaign as v1
import analyze_steel_module_campaign_v2 as v2
from plot_steel_module_analysis_v2 import (
    sha256_file,
    verify_checksum_manifest,
    write_recursive_checksums,
)
from steel_module_campaign_lib import (
    load_campaign as load_steel_module_campaign,
    verify_finalized_checksums,
)
from steel_module_production_checkpoint_lib import load_production_checkpoint


SCHEMA_VERSION = "steel-module-production-checkpoint-analysis-v1"
CHECKPOINT_SCHEMA_VERSION = "steel-module-production-checkpoint-v1"
STATE_ID = "BC-ONLY-S1"
BOOTSTRAP_SEED = 20260715
BOOTSTRAP_RESAMPLES = 10_000
PRECISION_TARGET = 0.10
LOO_SUCCESS = 0.10
LOO_CONTINUE = 0.20
EXPECTED_TASKS = 32
EXPECTED_EVENTS = 8_000
EXPECTED_EVENTS_PER_ENDPOINT = 4_000
EXPECTED_BLOCKS_PER_ENDPOINT = 16
METRICS = ("generated", "scintillation", "sipm")

REQUIRED_CHECKPOINT_FILES = (
    "checkpoint.json",
    "source_children.json",
    "source_finalization.json",
    "task_index.tsv",
    "configuration_summary.csv",
    "event_audit.json",
    "seed_audit.json",
)

ENDPOINT_FIELDS = (
    "state_id",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "blocks",
    "events",
    "generated_optical_photons_per_neutron",
    "scintillation_photons_per_neutron",
    "observed_net_sipm_photons_per_neutron",
    "net_ci95_low",
    "net_ci95_high",
    "net_valid_resamples",
    "sipm_zero_events",
    "sipm_zero_fraction",
    "sipm_positive_events",
)

DISTRIBUTION_FIELDS = (
    "state_id",
    "tile_thickness_mm",
    "metric",
    "events",
    "positive_events",
    "zero_events",
    "zero_fraction",
    "sum",
    "mean",
    "rms",
    "population_stddev",
    "standard_error",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "max",
    "positive_mean",
    "positive_rms",
    "positive_population_stddev",
    "positive_standard_error",
    "positive_p50",
    "positive_p75",
    "positive_p90",
    "positive_p95",
    "positive_p99",
    "positive_max",
    "top_1pct_event_count",
    "top_1pct_sum_fraction",
    "top_1pct_fraction_valid",
    "top_5pct_event_count",
    "top_5pct_sum_fraction",
    "top_5pct_fraction_valid",
    "maximum_logical_task_id",
    "maximum_seed_block",
    "maximum_root_entry",
)

LOO_FIELDS = (
    "state_id",
    "omitted_tile_thickness_mm",
    "omitted_seed_block",
    "omitted_logical_task_id",
    "reference_events_remaining",
    "compared_events_remaining",
    "full_ratio",
    "leave_one_block_out_ratio",
    "absolute_relative_shift",
    "within_10pct",
    "within_20pct",
)

TRAJECTORY_FIELDS = (
    "sample_id",
    "sample_role",
    "events_per_endpoint",
    "ratio",
    "ci95_low",
    "ci95_high",
    "relative_half_width",
    "projected_relative_half_width",
    "source_in_production_estimate",
)


@dataclass(frozen=True)
class LocatedEvent:
    event: v1.Event
    logical_task_id: str
    seed_block: int
    root_entry: int


@dataclass(frozen=True)
class AnalysisResult:
    endpoint_rows: tuple[dict[str, object], ...]
    distribution_rows: tuple[dict[str, object], ...]
    loo_rows: tuple[dict[str, object], ...]
    trajectory_rows: tuple[dict[str, object], ...]
    primary: dict[str, object]
    eligibility: dict[str, object]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--checkpoint-dir", required=True, type=Path)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_table(path: Path, *, delimiter: str) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Mapping[str, object]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def _snapshot_digest(snapshot: Mapping[str, str]) -> str:
    payload = json.dumps(
        dict(sorted(snapshot.items())),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _record_snapshot_file(
    snapshot: dict[str, str], label: str, path: Path, *, expected: str | None = None
) -> None:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"evidence input must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise ValueError(f"missing regular evidence input: {resolved}")
    digest = sha256_file(resolved)
    if expected is not None and digest != expected:
        raise ValueError(f"evidence input checksum changed: {label}")
    if label in snapshot:
        raise ValueError(f"duplicate evidence snapshot label: {label}")
    snapshot[label] = digest


def evidence_identity_snapshot(
    checkpoint_dir: Path,
    checkpoint: Mapping[str, object],
    task_rows: Sequence[Mapping[str, str]],
) -> dict[str, str]:
    """Freeze every external identity consumed by the formal analysis.

    This intentionally covers both checksum manifests and their recorded files.
    ROOT files are included separately because neither managed finalization nor
    the sealed-pilot finalized directory copies event data into its own tree.
    """

    snapshot: dict[str, str] = {}
    checkpoint_records = verify_checksum_manifest(
        checkpoint_dir, required=REQUIRED_CHECKPOINT_FILES
    )
    _record_snapshot_file(
        snapshot, "checkpoint/SHA256SUMS", checkpoint_dir / "SHA256SUMS"
    )
    for name, digest in sorted(checkpoint_records.items()):
        _record_snapshot_file(
            snapshot, f"checkpoint/{name}", checkpoint_dir / name, expected=digest
        )

    source = checkpoint.get("source_finalization")
    if not isinstance(source, dict) or not isinstance(source.get("directory"), str):
        raise ValueError("checkpoint lacks a source-finalization directory")
    source_dir = Path(str(source["directory"])).expanduser().resolve()
    source_records = verify_checksum_manifest(
        source_dir,
        required=(
            "task_index.tsv",
            "event_audit.json",
            "seed_audit.json",
            "validation_report.json",
        ),
    )
    _record_snapshot_file(
        snapshot,
        "source-finalization/SHA256SUMS",
        source_dir / "SHA256SUMS",
        expected=str(source.get("checksum_manifest_sha256", "")),
    )
    for name, digest in sorted(source_records.items()):
        _record_snapshot_file(
            snapshot,
            f"source-finalization/{name}",
            source_dir / name,
            expected=digest,
        )

    for row in task_rows:
        logical_id = row["logical_task_id"]
        _record_snapshot_file(
            snapshot,
            f"production-root/{logical_id}",
            Path(row["root"]),
            expected=row["root_sha256"],
        )

    pilot_record = checkpoint.get("pilot_baseline")
    if not isinstance(pilot_record, dict):
        raise ValueError("checkpoint lacks the sealed-pilot identity")
    pilot_raw = pilot_record.get("campaign_directory")
    analysis_raw = pilot_record.get("analysis_v2_directory")
    if not isinstance(pilot_raw, str) or not isinstance(analysis_raw, str):
        raise ValueError("checkpoint pilot paths are invalid")
    pilot_dir = Path(pilot_raw).expanduser().resolve()
    pilot_bundle = load_steel_module_campaign(
        pilot_dir, verify_external_artifacts=True
    )
    _record_snapshot_file(
        snapshot, "pilot-campaign/campaign.json", pilot_dir / "campaign.json"
    )
    pilot_artifacts = pilot_bundle.manifest.get("artifacts")
    if not isinstance(pilot_artifacts, dict):
        raise ValueError("sealed-pilot campaign lacks artifact identities")
    for key, filename in (
        ("tasks_tsv", "tasks.tsv"),
        ("scan_args", "scan_args.txt"),
        ("readme", "README.md"),
    ):
        record = pilot_artifacts.get(key)
        if not isinstance(record, dict):
            raise ValueError(f"sealed-pilot campaign lacks {key}")
        _record_snapshot_file(
            snapshot,
            f"pilot-campaign/{filename}",
            pilot_dir / filename,
            expected=str(record.get("sha256", "")),
        )
    selection = pilot_artifacts.get("configuration_selection")
    if not isinstance(selection, dict) or not isinstance(selection.get("path"), str):
        raise ValueError("sealed-pilot campaign lacks configuration selection")
    selection_path = (pilot_dir / str(selection["path"])).resolve()
    try:
        selection_path.relative_to(pilot_dir)
    except ValueError as exc:
        raise ValueError("sealed-pilot selection escapes campaign directory") from exc
    _record_snapshot_file(
        snapshot,
        "pilot-campaign/configuration-selection",
        selection_path,
        expected=str(selection.get("sha256", "")),
    )
    environment = pilot_bundle.manifest.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("sealed-pilot campaign lacks environment identity")
    for field in ("image", "g4_data_manifest", "build_artifact"):
        record = environment.get(field)
        if record is None:
            continue
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError(f"sealed-pilot {field} identity is invalid")
        _record_snapshot_file(
            snapshot,
            f"pilot-runtime/{field}",
            Path(str(record["path"])),
            expected=str(record.get("sha256", "")),
        )
    pilot_finalized = pilot_dir / "finalized"
    pilot_files = verify_finalized_checksums(pilot_finalized)
    _record_snapshot_file(
        snapshot,
        "pilot-finalized/SHA256SUMS",
        pilot_finalized / "SHA256SUMS",
        expected=str(pilot_record.get("finalized_sha256s_sha256", "")),
    )
    for name in sorted(pilot_files):
        _record_snapshot_file(
            snapshot, f"pilot-finalized/{name}", pilot_finalized / name
        )

    analysis_dir = Path(analysis_raw).expanduser().resolve()
    analysis_records = verify_checksum_manifest(
        analysis_dir, required=("primary_contrasts.csv", "analysis_config.json")
    )
    _record_snapshot_file(
        snapshot,
        "pilot-analysis-v2/SHA256SUMS",
        analysis_dir / "SHA256SUMS",
        expected=str(pilot_record.get("analysis_v2_sha256s_sha256", "")),
    )
    for name, digest in sorted(analysis_records.items()):
        _record_snapshot_file(
            snapshot,
            f"pilot-analysis-v2/{name}",
            analysis_dir / name,
            expected=digest,
        )

    pilot_rows, pilot_audited = _pilot_task_rows(pilot_dir)
    for row in pilot_rows:
        logical_id = row["logical_task_id"]
        audit = pilot_audited.get(logical_id)
        if audit is None:
            raise ValueError(f"pilot task absent from event audit: {logical_id}")
        _record_snapshot_file(
            snapshot,
            f"pilot-root/{logical_id}",
            pilot_dir / row["root"],
            expected=str(audit.get("root_sha256", "")),
        )
    return dict(sorted(snapshot.items()))


def acquire_publish_lock(output_dir: Path) -> Path:
    lock = output_dir.with_name(f".{output_dir.name}.publish.lock")
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError(f"analysis publish lock already exists: {lock}") from exc
    return lock


def validate_checkpoint_shape(
    checkpoint: Mapping[str, object], task_rows: Sequence[Mapping[str, str]]
) -> None:
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported production checkpoint schema")
    if checkpoint.get("state_id") != STATE_ID:
        raise ValueError("Phase-2B analyzer accepts only BC-ONLY-S1")
    if checkpoint.get("evidence_mode") != "back-center-only":
        raise ValueError("BC-ONLY-S1 checkpoint has the wrong evidence mode")
    if checkpoint.get("included_children") != ["BC-S1"]:
        raise ValueError("BC-ONLY-S1 must contain only BC-S1")
    if checkpoint.get("task_count") != EXPECTED_TASKS:
        raise ValueError("BC-ONLY-S1 must contain 32 tasks")
    if checkpoint.get("event_count") != EXPECTED_EVENTS:
        raise ValueError("BC-ONLY-S1 must contain 8,000 events")
    if len(task_rows) != EXPECTED_TASKS:
        raise ValueError("checkpoint task index does not contain 32 rows")
    seen_ids: set[str] = set()
    by_thickness: dict[int, set[int]] = defaultdict(set)
    total_events = 0
    for row in task_rows:
        logical_id = row["logical_task_id"]
        if logical_id in seen_ids:
            raise ValueError(f"duplicate checkpoint task: {logical_id}")
        seen_ids.add(logical_id)
        thickness = int(row["tile_thickness_mm"])
        if (
            thickness not in (4, 24)
            or row["sipm_layout"] != "back-center"
            or int(row["absorber_transverse_mm"]) != 500
            or int(row["x_mm"]) != 0
            or int(row["y_mm"]) != 0
            or int(row["events"]) != 250
        ):
            raise ValueError(f"task lies outside BC-ONLY-S1: {logical_id}")
        block = int(row["seed_block"])
        if block in by_thickness[thickness]:
            raise ValueError(f"duplicate endpoint block: {thickness} mm / {block}")
        by_thickness[thickness].add(block)
        total_events += int(row["events"])
    if by_thickness != {4: set(range(16)), 24: set(range(16))}:
        raise ValueError("BC-ONLY-S1 endpoint block ranges must be 0-15")
    if total_events != EXPECTED_EVENTS:
        raise ValueError("checkpoint task event total is not 8,000")


def _source_execution_directory(
    checkpoint: Mapping[str, object], source_children: Mapping[str, object]
) -> Path:
    candidates = (
        checkpoint.get("source_execution_directory"),
        source_children.get("execution_directory"),
    )
    children = source_children.get("children")
    if isinstance(children, list) and len(children) == 1 and isinstance(children[0], dict):
        candidates = (*candidates, children[0].get("execution_directory"))
    for value in candidates:
        if isinstance(value, str) and value:
            return Path(value).expanduser().resolve()
    raise ValueError("checkpoint does not identify its source execution directory")


def _resolve_root(source_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    path = candidate.resolve() if candidate.is_absolute() else (source_root / candidate).resolve()
    if not candidate.is_absolute():
        try:
            path.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"ROOT path escapes source execution: {raw_path}") from exc
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or unsafe ROOT file: {path}")
    return path


def reconcile_task_audit(
    row: Mapping[str, str], audit: Mapping[str, object]
) -> tuple[str, str]:
    logical_id = row["logical_task_id"]
    expected = {
        "tile_thickness_mm": int(row["tile_thickness_mm"]),
        "sipm_layout": row["sipm_layout"],
        "seed_block": int(row["seed_block"]),
        "events": int(row["events"]),
        "root": row["root"],
        "root_sha256": row["root_sha256"],
    }
    if any(audit.get(key) != value for key, value in expected.items()):
        raise ValueError(
            f"checkpoint task index and event audit disagree: {logical_id}"
        )
    return row["root"], row["root_sha256"]


def load_checkpoint_events(
    checkpoint_dir: Path,
    checkpoint: Mapping[str, object],
    task_rows: Sequence[Mapping[str, str]],
    event_audit: Mapping[str, object],
    source_children: Mapping[str, object],
) -> tuple[dict[int, list[v1.Event]], dict[int, dict[int, list[v1.Event]]], list[LocatedEvent]]:
    audit_tasks = event_audit.get("tasks")
    if not isinstance(audit_tasks, list):
        raise ValueError("checkpoint event audit has no task list")
    by_id: dict[str, dict[str, object]] = {}
    for raw in audit_tasks:
        if not isinstance(raw, dict) or not isinstance(raw.get("logical_task_id"), str):
            raise ValueError("invalid checkpoint event-audit task")
        logical_id = str(raw["logical_task_id"])
        if logical_id in by_id:
            raise ValueError(f"duplicate task in event audit: {logical_id}")
        by_id[logical_id] = raw
    if set(by_id) != {row["logical_task_id"] for row in task_rows}:
        raise ValueError("checkpoint task index and event audit disagree")
    source_root = _source_execution_directory(checkpoint, source_children)
    groups: dict[int, list[v1.Event]] = defaultdict(list)
    blocks: dict[int, dict[int, list[v1.Event]]] = defaultdict(dict)
    located: list[LocatedEvent] = []
    for row in sorted(task_rows, key=lambda item: int(item["task_index"])):
        logical_id = row["logical_task_id"]
        audit = by_id[logical_id]
        raw_root, expected_sha = reconcile_task_audit(row, audit)
        root_path = _resolve_root(source_root, raw_root)
        if len(expected_sha) != 64 or sha256_file(root_path) != expected_sha:
            raise ValueError(f"checkpoint ROOT checksum mismatch: {logical_id}")
        expected_events = int(row["events"])
        events = v1.read_root_events(root_path, expected_events)
        thickness = int(row["tile_thickness_mm"])
        block = int(row["seed_block"])
        groups[thickness].extend(events)
        blocks[thickness][block] = events
        located.extend(
            LocatedEvent(event, logical_id, block, entry)
            for entry, event in enumerate(events)
        )
    if {key: len(value) for key, value in groups.items()} != {
        4: EXPECTED_EVENTS_PER_ENDPOINT,
        24: EXPECTED_EVENTS_PER_ENDPOINT,
    }:
        raise ValueError("checkpoint endpoint event totals are not 4,000 each")
    return dict(groups), {key: dict(value) for key, value in blocks.items()}, located


def _bootstrap(groups: Mapping[int, Sequence[v1.Event]], np: object) -> dict[int, dict[str, list[float]]]:
    cache: dict[int, dict[str, list[float]]] = {}
    for thickness in (4, 24):
        seed = v1.configuration_seed(
            BOOTSTRAP_SEED,
            (SCHEMA_VERSION, STATE_ID, thickness, "back-center", 500, 0, 0),
        )
        cache[thickness] = v2.bootstrap_configuration(
            groups[thickness],
            resamples=BOOTSTRAP_RESAMPLES,
            seed=seed,
            np=np,
        )
    return cache


def ratio_summary(
    groups: Mapping[int, Sequence[v1.Event]],
    cache: Mapping[int, Mapping[str, Sequence[float]]],
) -> dict[str, object]:
    points = {thickness: v1.estimator(groups[thickness])["net"] for thickness in (4, 24)}
    if points[4] <= 0 or points[24] <= 0:
        raise ValueError("endpoint response cannot support the 24/4 ratio")
    ratio = points[24] / points[4]
    draws = v1.ratio_distribution(cache[24]["net"], cache[4]["net"])
    low, high, valid = v1.interval(draws)
    if valid != BOOTSTRAP_RESAMPLES or not all(
        math.isfinite(value) for value in (ratio, low, high)
    ):
        raise ValueError("production ratio bootstrap did not yield 10,000 valid draws")
    h = (high - low) / (2.0 * abs(ratio))
    return {
        "ratio": ratio,
        "ci95_low": low,
        "ci95_high": high,
        "valid_resamples": valid,
        "relative_half_width": h,
        "ci_excludes_unity": high < 1.0 or low > 1.0,
    }


def endpoint_rows(
    groups: Mapping[int, Sequence[v1.Event]],
    cache: Mapping[int, Mapping[str, Sequence[float]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for thickness in (4, 24):
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
                "blocks": EXPECTED_BLOCKS_PER_ENDPOINT,
                "events": len(events),
                "generated_optical_photons_per_neutron": sum(event.generated for event in events) / len(events),
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


def build_distribution_rows(
    located_by_thickness: Mapping[int, Sequence[LocatedEvent]], np: object
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for thickness in (4, 24):
        selected = list(located_by_thickness[thickness])
        for metric in METRICS:
            values = [int(getattr(item.event, metric)) for item in selected]
            summary = v2.distribution_summary(values, np=np)
            maximum = sorted(
                selected,
                key=lambda item: (
                    -int(getattr(item.event, metric)),
                    item.logical_task_id,
                    item.root_entry,
                ),
            )[0]
            rows.append(
                {
                    "state_id": STATE_ID,
                    "tile_thickness_mm": thickness,
                    "metric": metric,
                    **summary,
                    "maximum_logical_task_id": maximum.logical_task_id,
                    "maximum_seed_block": maximum.seed_block,
                    "maximum_root_entry": maximum.root_entry,
                }
            )
    return rows


def leave_one_block_out_rows(
    groups: Mapping[int, Sequence[v1.Event]],
    blocks: Mapping[int, Mapping[int, Sequence[v1.Event]]],
    task_ids: Mapping[tuple[int, int], str],
    full_ratio: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for thickness in (4, 24):
        for block in range(16):
            reduced = {
                arm: list(events)
                for arm, events in groups.items()
            }
            omitted = list(blocks[thickness][block])
            retained = list(groups[thickness])
            if len(omitted) != 250:
                raise ValueError("LOO requires exact 250-event blocks")
            start = block * 250
            if retained[start : start + 250] == omitted:
                del retained[start : start + 250]
            else:
                retained = [
                    event
                    for candidate_block, values in sorted(blocks[thickness].items())
                    if candidate_block != block
                    for event in values
                ]
            reduced[thickness] = retained
            denominator = v1.estimator(reduced[4])["net"]
            numerator = v1.estimator(reduced[24])["net"]
            if denominator <= 0:
                raise ValueError("LOO denominator is zero")
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
    if len(rows) != 32:
        raise ValueError("BC-ONLY-S1 must yield exactly 32 LOO evaluations")
    return rows


def _pilot_task_rows(pilot_dir: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, object]]]:
    finalized = pilot_dir / "finalized"
    verify_finalized_checksums(finalized)
    task_rows = read_table(finalized / "task_index.tsv", delimiter="\t")
    event_audit = load_json(finalized / "event_audit.json")
    raw_tasks = event_audit.get("tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError("sealed pilot event audit lacks tasks")
    audited = {
        str(row["logical_task_id"]): row
        for row in raw_tasks
        if isinstance(row, dict) and isinstance(row.get("logical_task_id"), str)
    }
    selected = [
        row
        for row in task_rows
        if int(row["tile_thickness_mm"]) in (4, 24)
        and row["sipm_layout"] == "back-center"
        and int(row["absorber_transverse_mm"]) == 500
        and int(row["x_mm"]) == 0
        and int(row["y_mm"]) == 0
    ]
    if len(selected) != 8:
        raise ValueError("sealed pilot baseline must contain eight endpoint blocks")
    return selected, audited


def load_pilot_baseline(
    checkpoint: Mapping[str, object], np: object
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    record = checkpoint.get("pilot_baseline")
    if not isinstance(record, dict):
        raise ValueError("checkpoint does not bind a sealed-pilot baseline")
    pilot_raw = record.get("campaign_directory")
    analysis_raw = record.get("analysis_v2_directory")
    if not isinstance(pilot_raw, str) or not isinstance(analysis_raw, str):
        raise ValueError("checkpoint pilot baseline paths are invalid")
    pilot_dir = Path(pilot_raw).expanduser().resolve()
    analysis_dir = Path(analysis_raw).expanduser().resolve()
    pilot_bundle = load_steel_module_campaign(
        pilot_dir, verify_external_artifacts=True
    )
    expected_pilot_identity = {
        "campaign_id": pilot_bundle.campaign_id,
        "plan_hash": pilot_bundle.plan_hash,
        "simulation_commit": pilot_bundle.git_commit,
    }
    for key, actual in expected_pilot_identity.items():
        if record.get(key) != actual:
            raise ValueError(f"sealed pilot {key} identity changed")
    if sha256_file(pilot_dir / "finalized" / "SHA256SUMS") != record.get(
        "finalized_sha256s_sha256"
    ):
        raise ValueError("sealed pilot finalized identity changed")
    verify_checksum_manifest(
        analysis_dir,
        required=("primary_contrasts.csv", "analysis_config.json"),
    )
    if sha256_file(analysis_dir / "SHA256SUMS") != record.get(
        "analysis_v2_sha256s_sha256"
    ):
        raise ValueError("sealed pilot analysis-v2 identity changed")
    if sha256_file(analysis_dir / "analysis_config.json") != record.get(
        "analysis_v2_config_sha256"
    ):
        raise ValueError("sealed pilot analysis-v2 config identity changed")
    analysis_config = load_json(analysis_dir / "analysis_config.json")
    analysis_git = analysis_config.get("analysis_git")
    if (
        analysis_config.get("accepted_statistical_evidence") is not True
        or analysis_config.get("campaign_id") != pilot_bundle.campaign_id
        or analysis_config.get("plan_hash") != pilot_bundle.plan_hash
        or analysis_config.get("simulation_git_commit") != pilot_bundle.git_commit
        or not isinstance(analysis_git, dict)
        or analysis_git.get("commit") != record.get("analysis_commit")
    ):
        raise ValueError("sealed pilot analysis-v2 provenance mismatch")
    task_rows, audited = _pilot_task_rows(pilot_dir)
    groups: dict[int, list[v1.Event]] = defaultdict(list)
    located: dict[int, list[LocatedEvent]] = {4: [], 24: []}
    for row in sorted(task_rows, key=lambda item: int(item["task_index"])):
        logical_id = row["logical_task_id"]
        audit = audited.get(logical_id)
        if audit is None:
            raise ValueError(f"pilot task absent from event audit: {logical_id}")
        root_path = (pilot_dir / str(row["root"])).resolve()
        expected_sha = str(audit.get("root_sha256", ""))
        if sha256_file(root_path) != expected_sha:
            raise ValueError(f"pilot ROOT checksum changed: {logical_id}")
        thickness = int(row["tile_thickness_mm"])
        seed_block = int(row["seed_block"])
        events = v1.read_root_events(root_path, int(row["events"]))
        groups[thickness].extend(events)
        located[thickness].extend(
            LocatedEvent(event, logical_id, seed_block, entry)
            for entry, event in enumerate(events)
        )
    if {key: len(value) for key, value in groups.items()} != {4: 1000, 24: 1000}:
        raise ValueError("sealed pilot baseline endpoint totals are not 1,000 each")
    cache: dict[int, dict[str, list[float]]] = {}
    for thickness in (4, 24):
        key = ("convergence-pilot", thickness, "back-center", 500, 0, 0)
        cache[thickness] = v2.bootstrap_configuration(
            groups[thickness],
            resamples=BOOTSTRAP_RESAMPLES,
            seed=v1.configuration_seed(BOOTSTRAP_SEED, ("analysis-v2", *key)),
            np=np,
        )
    summary = ratio_summary(groups, cache)
    rows = read_table(analysis_dir / "primary_contrasts.csv", delimiter=",")
    formal = [
        row
        for row in rows
        if row.get("primary_contrast_id") == "observed-net-back-center-24-over-4"
    ]
    if len(formal) != 1:
        raise ValueError("formal analysis-v2 lacks the back-center primary row")
    for field in ("ratio", "ci95_low", "ci95_high", "relative_half_width"):
        if not math.isclose(
            float(formal[0][field]), float(summary[field]), rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError(f"sealed pilot baseline does not reconcile: {field}")
    pilot_distribution_rows = []
    for row in build_distribution_rows(located, np):
        pilot_row = dict(row)
        pilot_row["state_id"] = "sealed-pilot"
        pilot_distribution_rows.append(pilot_row)
    return (
        {
            **summary,
            "events_per_endpoint": 1000,
            "campaign_id": record.get("campaign_id"),
            "reconciliation": "passed",
        },
        tuple(pilot_distribution_rows),
    )


def numeric_eligibility(
    *, relative_half_width: float, maximum_loo_shift: float, narrowing: bool
) -> tuple[list[str], str]:
    if relative_half_width <= PRECISION_TARGET and maximum_loo_shift <= LOO_SUCCESS:
        return ["stop-success", "pause-review"], "precision-success"
    if (
        relative_half_width > PRECISION_TARGET
        and maximum_loo_shift <= LOO_CONTINUE
        and narrowing
    ):
        return ["continue", "pause-review"], "continue-eligible"
    return ["pause-review"], "pause-only"


def analyze_event_groups(
    groups: Mapping[int, Sequence[v1.Event]],
    blocks: Mapping[int, Mapping[int, Sequence[v1.Event]]],
    located_by_thickness: Mapping[int, Sequence[LocatedEvent]],
    task_ids: Mapping[tuple[int, int], str],
    *,
    pilot: Mapping[str, object],
    np: object,
) -> AnalysisResult:
    if {key: len(value) for key, value in groups.items()} != {4: 4000, 24: 4000}:
        raise ValueError("analysis requires exactly 4,000 events per endpoint")
    cache = _bootstrap(groups, np)
    primary = ratio_summary(groups, cache)
    loo = leave_one_block_out_rows(groups, blocks, task_ids, float(primary["ratio"]))
    maximum = max(float(row["absolute_relative_shift"]) for row in loo)
    h = float(primary["relative_half_width"])
    pilot_h = float(pilot["relative_half_width"])
    narrowing = h < pilot_h
    numeric, branch = numeric_eligibility(
        relative_half_width=h,
        maximum_loo_shift=maximum,
        narrowing=narrowing,
    )
    primary = {
        "schema_version": f"{SCHEMA_VERSION}-primary-v1",
        "state_id": STATE_ID,
        "metric": "observed-net-sipm-response",
        "ratio_direction": "24-mm-over-4-mm",
        "reference_tile_thickness_mm": 4,
        "compared_tile_thickness_mm": 24,
        "reference_events": len(groups[4]),
        "compared_events": len(groups[24]),
        **primary,
        "max_leave_one_block_out_relative_shift": maximum,
        "leave_one_block_out_evaluations": len(loo),
        "precision_target": PRECISION_TARGET,
        "loo_success_threshold": LOO_SUCCESS,
        "loo_continue_ceiling": LOO_CONTINUE,
        "pilot_relative_half_width": pilot_h,
        "interval_narrowing": narrowing,
        "unity_diagnostics_control_eligibility": False,
    }
    eligibility = {
        "schema_version": f"{SCHEMA_VERSION}-numeric-eligibility-v1",
        "state_id": STATE_ID,
        "valid_evidence": True,
        "branch": branch,
        "numeric_eligible_decisions": numeric,
        "automatic_decision": False,
        "automatic_submission": False,
        "human_tail_disposition_required": True,
    }
    trajectory = [
        {
            "sample_id": "sealed-pilot",
            "sample_role": "width-and-tail-baseline-only",
            "events_per_endpoint": int(pilot["events_per_endpoint"]),
            "ratio": pilot["ratio"],
            "ci95_low": pilot["ci95_low"],
            "ci95_high": pilot["ci95_high"],
            "relative_half_width": pilot_h,
            "projected_relative_half_width": pilot_h,
            "source_in_production_estimate": False,
        },
        {
            "sample_id": STATE_ID,
            "sample_role": "new-production-events-only",
            "events_per_endpoint": EXPECTED_EVENTS_PER_ENDPOINT,
            "ratio": primary["ratio"],
            "ci95_low": primary["ci95_low"],
            "ci95_high": primary["ci95_high"],
            "relative_half_width": h,
            "projected_relative_half_width": pilot_h
            * math.sqrt(int(pilot["events_per_endpoint"]) / EXPECTED_EVENTS_PER_ENDPOINT),
            "source_in_production_estimate": True,
        },
    ]
    return AnalysisResult(
        tuple(endpoint_rows(groups, cache)),
        tuple(build_distribution_rows(located_by_thickness, np)),
        tuple(loo),
        tuple(trajectory),
        primary,
        eligibility,
    )


def summary_markdown(
    result: AnalysisResult,
    pilot_distribution_rows: Sequence[Mapping[str, object]],
) -> str:
    primary = result.primary
    maximum = max(result.loo_rows, key=lambda row: float(row["absolute_relative_shift"]))
    sipm_rows = [row for row in result.distribution_rows if row["metric"] == "sipm"]
    pilot_sipm_rows = [
        row for row in pilot_distribution_rows if row["metric"] == "sipm"
    ]
    lines = [
        "# Steel Module BC-ONLY-S1 Production Review Summary",
        "",
        "This is core numerical evidence. It does not select a progression decision or submit work.",
        "",
        f"- Observed net 24/4 ratio: `{float(primary['ratio']):.6g}`.",
        f"- Bootstrap 95% interval: `[{float(primary['ci95_low']):.6g}, {float(primary['ci95_high']):.6g}]`.",
        f"- Relative half-width: `{100 * float(primary['relative_half_width']):.2f}%` (target `10%`).",
        f"- Maximum leave-one-block-out shift: `{100 * float(primary['max_leave_one_block_out_relative_shift']):.2f}%`, from `{maximum['omitted_tile_thickness_mm']} mm` block `{maximum['omitted_seed_block']}`.",
        f"- Interval narrowing versus sealed pilot: `{str(primary['interval_narrowing']).lower()}`.",
        f"- Numeric choices: `{', '.join(result.eligibility['numeric_eligible_decisions'])}`.",
        "",
        "## Tail diagnostics: sealed pilot versus new production",
        "",
    ]
    for sample, rows in (("sealed pilot", pilot_sipm_rows), ("new production", sipm_rows)):
        lines.append(f"### {sample}")
        lines.append("")
        for row in rows:
            lines.append(
                f"- {row['tile_thickness_mm']} mm: zero `{100 * float(row['zero_fraction']):.2f}%`, "
                f"top 1% share `{100 * float(row['top_1pct_sum_fraction']):.2f}%`, "
                f"top 5% share `{100 * float(row['top_5pct_sum_fraction']):.2f}%`, "
                f"max `{row['max']}` photons at `{row['maximum_logical_task_id']}` "
                f"entry `{row['maximum_root_entry']}`."
            )
        lines.append("")
    lines.extend(
        [
            "",
            "A human must review whether the newly sampled tail materially worsened before recording `stop-success`, `continue`, or `pause-review`.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    checkpoint_dir = args.checkpoint_dir.expanduser().resolve()
    output_dir = checkpoint_dir.with_name(f"{checkpoint_dir.name}-analysis")
    temp_dir: Path | None = None
    publish_lock: Path | None = None
    try:
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        recorded = verify_checksum_manifest(
            checkpoint_dir, required=REQUIRED_CHECKPOINT_FILES
        )
        checkpoint = load_json(checkpoint_dir / "checkpoint.json")
        source_children = load_json(checkpoint_dir / "source_children.json")
        event_audit = load_json(checkpoint_dir / "event_audit.json")
        task_rows = read_table(checkpoint_dir / "task_index.tsv", delimiter="\t")
        validate_checkpoint_shape(checkpoint, task_rows)
        if checkpoint.get("accepted_statistical_evidence") is not True:
            raise ValueError("formal analyzer refuses non-accepted checkpoint evidence")
        repo_root = Path(__file__).resolve().parents[2]
        if git_output(repo_root, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("formal production analysis requires a clean checkout")
        loaded_checkpoint = load_production_checkpoint(
            checkpoint_dir,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_root_files=True,
        )
        if task_rows != list(loaded_checkpoint.task_rows):
            raise ValueError("checkpoint loader and analyzer task index disagree")
        try:
            import numpy as np  # type: ignore[import-not-found]
            import uproot  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("formal production analysis requires NumPy and uproot") from exc
        before = evidence_identity_snapshot(checkpoint_dir, checkpoint, task_rows)
        groups, blocks, located = load_checkpoint_events(
            checkpoint_dir, checkpoint, task_rows, event_audit, source_children
        )
        located_by_thickness: dict[int, list[LocatedEvent]] = {4: [], 24: []}
        task_ids: dict[tuple[int, int], str] = {}
        by_identity = {
            row["logical_task_id"]: (
                int(row["tile_thickness_mm"]), int(row["seed_block"])
            )
            for row in task_rows
        }
        for item in located:
            thickness, block = by_identity[item.logical_task_id]
            located_by_thickness[thickness].append(item)
            task_ids[(thickness, block)] = item.logical_task_id
        pilot, pilot_distribution_rows = load_pilot_baseline(checkpoint, np)
        result = analyze_event_groups(
            groups,
            blocks,
            located_by_thickness,
            task_ids,
            pilot=pilot,
            np=np,
        )
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        write_csv(temp_dir / "endpoint_estimates.csv", ENDPOINT_FIELDS, result.endpoint_rows)
        write_csv(
            temp_dir / "distribution_diagnostics.csv",
            DISTRIBUTION_FIELDS,
            result.distribution_rows,
        )
        write_csv(
            temp_dir / "pilot_distribution_diagnostics.csv",
            DISTRIBUTION_FIELDS,
            pilot_distribution_rows,
        )
        write_csv(temp_dir / "block_loo.csv", LOO_FIELDS, result.loo_rows)
        write_csv(
            temp_dir / "precision_trajectory.csv",
            TRAJECTORY_FIELDS,
            result.trajectory_rows,
        )
        write_json(temp_dir / "primary_contrast.json", result.primary)
        write_json(temp_dir / "numeric_eligibility.json", result.eligibility)
        analysis_config = {
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "state_id": STATE_ID,
            "checkpoint_hash": checkpoint.get("checkpoint_hash"),
            "source_checkpoint_sha256s_sha256": sha256_file(checkpoint_dir / "SHA256SUMS"),
            "source_checkpoint_files": dict(sorted(recorded.items())),
            "program": checkpoint.get("program"),
            "task_set_hash": checkpoint.get("task_set_hash"),
            "checkpoint_task_index_sha256": checkpoint.get("task_index_sha256"),
            "root_set_hash": checkpoint.get("root_set_hash"),
            "task_count": checkpoint.get("task_count"),
            "event_count": checkpoint.get("event_count"),
            "source_finalization": checkpoint.get("source_finalization"),
            "source_event_audit_sha256": checkpoint.get("event_audit_sha256"),
            "input_identity_snapshot": {
                "algorithm": "sha256(canonical-json(logical-name-to-sha256))",
                "file_count": len(before),
                "sha256": _snapshot_digest(before),
            },
            "accepted_statistical_evidence": True,
            "simulation_commit": checkpoint.get("simulation_commit"),
            "analysis_commit": git_output(repo_root, "rev-parse", "HEAD"),
            "analysis_tree": git_output(repo_root, "rev-parse", "HEAD^{tree}"),
            "analyzer_sha256": sha256_file(Path(__file__).resolve()),
            "python_version": sys.version.split()[0],
            "numpy_version": str(np.__version__),
            "uproot_version": str(uproot.__version__),
            "bootstrap": {
                "seed": BOOTSTRAP_SEED,
                "resamples": BOOTSTRAP_RESAMPLES,
                "generator": "numpy.random.Generator(numpy.random.PCG64)",
                "confidence_interval": "95% event-bootstrap percentile interval",
            },
            "policy": {
                "relative_half_width_target": PRECISION_TARGET,
                "loo_success_threshold": LOO_SUCCESS,
                "loo_continue_ceiling": LOO_CONTINUE,
                "pilot_events_in_production_estimate": False,
                "automatic_decision": False,
                "automatic_submission": False,
            },
            "pilot_baseline": pilot,
        }
        write_json(temp_dir / "analysis_config.json", analysis_config)
        (temp_dir / "summary.md").write_text(
            summary_markdown(result, pilot_distribution_rows), encoding="utf-8"
        )
        write_recursive_checksums(temp_dir)
        publish_lock = acquire_publish_lock(output_dir)
        if output_dir.exists() or output_dir.is_symlink():
            raise ValueError(f"refusing to overwrite analysis directory: {output_dir}")
        after = evidence_identity_snapshot(checkpoint_dir, checkpoint, task_rows)
        if before != after:
            raise ValueError("production, finalization, or pilot inputs changed during analysis")
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
                "analysis_config.json",
                "summary.md",
            ),
        )
        os.rename(temp_dir, output_dir)
        temp_dir = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot analyze steel-module production checkpoint: {exc}", file=sys.stderr)
        return 1
    finally:
        if publish_lock is not None and publish_lock.exists():
            publish_lock.rmdir()
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)
    print(f"Analyzed {STATE_ID} into {output_dir}")
    print("Bootstrap: 10000 resamples, seed 20260715; LOO evaluations: 32")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
