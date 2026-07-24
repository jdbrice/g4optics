#!/usr/bin/env python3
"""Capture Slurm resources and physics diagnostics for a neutron benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from realistic_neutron_campaign_lib import (
    atomic_write_json,
    load_campaign,
    load_json,
    resolve_campaign_path,
    sha256_file,
    verify_finalized_checksums,
)


REPORT_FIELDS = (
    "logical_task_id",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "events",
    "slurm_job_id",
    "slurm_array_index",
    "slurm_state",
    "slurm_exit_code",
    "elapsed_seconds",
    "wall_seconds_per_event",
    "max_rss_bytes",
    "max_vmsize_bytes",
    "root_output_bytes",
    "root_output_bytes_per_event",
    "recorded_artifact_bytes",
    "recorded_artifact_bytes_per_event",
    "generated_optical_mean",
    "generated_optical_rms",
    "scintillation_mean",
    "scintillation_rms",
    "sipm_detected_mean",
    "sipm_detected_rms",
    "interaction_fraction",
    "generated_optical_zero_fraction",
    "scintillation_zero_fraction",
    "sipm_detected_zero_fraction",
)

SACCT_FIELDS = (
    "JobID",
    "JobIDRaw",
    "State",
    "ExitCode",
    "ElapsedRaw",
    "MaxRSS",
    "MaxVMSize",
)

LEGACY_SACCT_FIELDS = tuple(field for field in SACCT_FIELDS if field != "JobID")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--finalized-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--sacct-input", type=Path)
    parser.add_argument("--sacct-command", default="sacct")
    parser.add_argument("--task-time-target-seconds", type=int, default=3600)
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def read_single_csv_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"expected one summary row in {path}, found {len(rows)}")
    return rows[0]


def normalize_state(value: str) -> str:
    return value.strip().split()[0].rstrip("+") if value.strip() else ""


def parse_memory_bytes(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGTPE]?)", text, re.IGNORECASE)
    if match is None:
        raise ValueError(f"cannot parse Slurm memory value: {value!r}")
    amount = float(match.group(1))
    suffix = match.group(2).upper()
    exponent = "KMGTPE".find(suffix) + 1 if suffix else 0
    return int(round(amount * (1024**exponent)))


def query_sacct(command: str, job_ids: list[str], cwd: Path) -> str:
    result = subprocess.run(
        [
            command,
            "--noheader",
            "--parsable2",
            "--array",
            "--jobs",
            ",".join(job_ids),
            "--format=" + ",".join(SACCT_FIELDS),
        ],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise ValueError(f"sacct failed ({result.returncode}): {result.stderr.strip()}")
    if not result.stdout.strip():
        raise ValueError("sacct returned no accounting rows")
    return result.stdout


def parse_sacct(raw: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        values = line.split("|")
        if values == [*SACCT_FIELDS, ""] or values == [*LEGACY_SACCT_FIELDS, ""]:
            continue
        if len(values) == len(SACCT_FIELDS) + 1 and values[-1] == "":
            values.pop()
        elif (
            len(values) == len(LEGACY_SACCT_FIELDS) + 1
            and values[-1] == ""
            and re.fullmatch(r"\d+:\d+", values[2].strip())
        ):
            values.pop()
        if values == list(SACCT_FIELDS) or values == list(LEGACY_SACCT_FIELDS):
            continue
        if len(values) == len(SACCT_FIELDS):
            rows.append(dict(zip(SACCT_FIELDS, values)))
        elif len(values) == len(LEGACY_SACCT_FIELDS):
            legacy = dict(zip(LEGACY_SACCT_FIELDS, values))
            rows.append({"JobID": "", **legacy})
        else:
            raise ValueError(f"invalid sacct row: {line!r}")
    if not rows:
        raise ValueError("sacct accounting input has no data rows")
    return rows


def accounting_for_task(
    rows: list[dict[str, str]],
    job_id: str,
    array_index: str,
    element_job_id: str,
) -> dict[str, int | str | None]:
    base = f"{job_id}_{array_index}"
    parent = [row for row in rows if row["JobID"] == base]
    identity_field = "JobID"
    identity = base
    if not parent:
        raw_identities = {base, element_job_id}
        parent = [
            row
            for row in rows
            if not row["JobID"] and row["JobIDRaw"] in raw_identities
        ]
        identity_field = "JobIDRaw"
        identity = parent[0]["JobIDRaw"] if len(parent) == 1 else element_job_id
    if len(parent) != 1:
        available = sorted(
            {
                row["JobID"] or row["JobIDRaw"]
                for row in rows
                if row["JobID"] or row["JobIDRaw"]
            }
        )
        raise ValueError(
            f"expected one sacct parent row for {base} "
            f"(element job {element_job_id}), found {len(parent)}; "
            f"available IDs: {available}"
        )
    parent_row = parent[0]
    state = normalize_state(parent_row["State"])
    exit_code = parent_row["ExitCode"].strip()
    if state != "COMPLETED" or exit_code != "0:0":
        raise ValueError(f"benchmark task {base} is not a clean success: {state} {exit_code}")
    try:
        elapsed = int(parent_row["ElapsedRaw"])
    except ValueError as exc:
        raise ValueError(f"invalid ElapsedRaw for {base}") from exc
    if elapsed <= 0:
        raise ValueError(f"ElapsedRaw must be positive for {base}")

    related = [
        row
        for row in rows
        if row[identity_field] == identity
        or row[identity_field].startswith(identity + ".")
    ]
    rss_values = [
        value
        for row in related
        if (value := parse_memory_bytes(row["MaxRSS"])) is not None
    ]
    vm_values = [
        value
        for row in related
        if (value := parse_memory_bytes(row["MaxVMSize"])) is not None
    ]
    if not rss_values or max(rss_values) <= 0:
        raise ValueError(f"sacct did not report MaxRSS for {base}")
    return {
        "state": state,
        "exit_code": exit_code,
        "elapsed_seconds": elapsed,
        "max_rss_bytes": max(rss_values),
        "max_vmsize_bytes": max(vm_values) if vm_values else None,
    }


def convergence_block_recommendation(
    benchmark_rows: list[dict[str, object]], target_seconds: int
) -> dict[str, object]:
    if target_seconds <= 0:
        raise ValueError("--task-time-target-seconds must be positive")
    limiting = max(benchmark_rows, key=lambda row: float(row["wall_seconds_per_event"]))
    seconds_per_event = float(limiting["wall_seconds_per_event"])
    maximum_events = math.floor(target_seconds / seconds_per_event)
    candidates = [
        events
        for events in range(1, 251)
        if 1000 % events == 0 and 1000 // events >= 4
    ]
    eligible = [events for events in candidates if events <= maximum_events]
    events_per_task = max(eligible) if eligible else 1
    return {
        "aggregate_events_per_configuration": 1000,
        "task_time_target_seconds": target_seconds,
        "limiting_tile_thickness_mm": int(limiting["tile_thickness_mm"]),
        "limiting_wall_seconds_per_event": seconds_per_event,
        "projected_250_event_seconds": 250 * seconds_per_event,
        "default_250_event_block_within_target": (
            250 * seconds_per_event <= target_seconds
        ),
        "recommended_events_per_task": events_per_task,
        "recommended_blocks_per_configuration": 1000 // events_per_task,
        "recommended_block_within_target": (
            events_per_task * seconds_per_event <= target_seconds
        ),
        "target_feasible_at_one_event": seconds_per_event <= target_seconds,
        "requires_review_before_convergence_pilot": True,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


def main() -> int:
    args = parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    finalized_dir = (
        args.finalized_dir.expanduser().resolve()
        if args.finalized_dir is not None
        else campaign_dir / "finalized"
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else finalized_dir / "benchmark"
    )
    try:
        bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
        verify_finalized_checksums(finalized_dir)
        if bundle.manifest.get("stage") != "benchmark":
            raise ValueError("benchmark summarizer requires a benchmark campaign")
        validation = load_json(finalized_dir / "validation_report.json")
        if validation.get("valid") is not True:
            raise ValueError("finalized validation report is not valid")
        if (
            validation.get("campaign_id") != bundle.campaign_id
            or validation.get("plan_hash") != bundle.plan_hash
            or validation.get("git_commit") != bundle.git_commit
            or validation.get("environment_identity")
            != bundle.environment.get("identity_hash")
        ):
            raise ValueError("finalized identity does not match campaign")
        task_rows = read_tsv(finalized_dir / "task_index.tsv")
        if len(task_rows) != 2 or {int(row["tile_thickness_mm"]) for row in task_rows} != {4, 16}:
            raise ValueError("benchmark must contain exactly the 4 mm and 16 mm tasks")
        if any(int(row["absorber_transverse_mm"]) != 500 for row in task_rows):
            raise ValueError("benchmark tasks must use the 500 mm absorber")

        job_ids = sorted({row["slurm_job_id"] for row in task_rows})
        raw_sacct = (
            args.sacct_input.expanduser().read_text(encoding="utf-8")
            if args.sacct_input is not None
            else query_sacct(args.sacct_command, job_ids, Path.cwd())
        )
        sacct_rows = parse_sacct(raw_sacct)

        report_rows: list[dict[str, object]] = []
        for row in task_rows:
            marker_path = (
                campaign_dir
                / "attempts"
                / row["attempt_id"]
                / "tasks"
                / row["logical_task_id"]
                / "task_result.json"
            )
            marker = load_json(marker_path)
            slurm = marker.get("slurm")
            if not isinstance(slurm, dict):
                raise ValueError(f"task result has no Slurm identity: {row['logical_task_id']}")
            if (
                slurm.get("array_job_id") != row["slurm_job_id"]
                or slurm.get("array_task_id") != row["slurm_array_index"]
            ):
                raise ValueError(
                    f"task result has mismatched Slurm array identity: "
                    f"{row['logical_task_id']}"
                )
            element_job_id = str(slurm.get("job_id") or "")
            if not element_job_id:
                raise ValueError(
                    f"task result has no Slurm element job ID: {row['logical_task_id']}"
                )
            accounting = accounting_for_task(
                sacct_rows,
                row["slurm_job_id"],
                row["slurm_array_index"],
                element_job_id,
            )
            events = int(row["events"])
            summary_path = resolve_campaign_path(campaign_dir, row["summary"])
            summary = read_single_csv_row(summary_path)
            if int(summary["events"]) != events:
                raise ValueError(f"summary event mismatch for {row['logical_task_id']}")
            root_path = resolve_campaign_path(campaign_dir, row["root"])
            artifacts = marker.get("artifacts")
            if not isinstance(artifacts, dict):
                raise ValueError(f"task result has no artifacts: {row['logical_task_id']}")
            root_record = artifacts.get("root")
            if (
                not isinstance(root_record, dict)
                or root_record.get("path") != row["root"]
                or root_record.get("sha256") != sha256_file(root_path)
            ):
                raise ValueError(f"ROOT identity mismatch: {row['logical_task_id']}")
            recorded_artifact_bytes = sum(
                int(record["size_bytes"])
                for record in artifacts.values()
                if isinstance(record, dict) and "size_bytes" in record
            )
            elapsed = int(accounting["elapsed_seconds"])
            report_rows.append(
                {
                    "logical_task_id": row["logical_task_id"],
                    "tile_thickness_mm": int(row["tile_thickness_mm"]),
                    "absorber_transverse_mm": int(row["absorber_transverse_mm"]),
                    "events": events,
                    "slurm_job_id": row["slurm_job_id"],
                    "slurm_array_index": row["slurm_array_index"],
                    "slurm_state": accounting["state"],
                    "slurm_exit_code": accounting["exit_code"],
                    "elapsed_seconds": elapsed,
                    "wall_seconds_per_event": elapsed / events,
                    "max_rss_bytes": accounting["max_rss_bytes"],
                    "max_vmsize_bytes": accounting["max_vmsize_bytes"],
                    "root_output_bytes": root_path.stat().st_size,
                    "root_output_bytes_per_event": root_path.stat().st_size / events,
                    "recorded_artifact_bytes": recorded_artifact_bytes,
                    "recorded_artifact_bytes_per_event": recorded_artifact_bytes / events,
                    "generated_optical_mean": float(summary["generated_optical_mean"]),
                    "generated_optical_rms": float(summary["generated_optical_rms"]),
                    "scintillation_mean": float(summary["scintillation_mean"]),
                    "scintillation_rms": float(summary["scintillation_rms"]),
                    "sipm_detected_mean": float(summary["sipm_detected_mean"]),
                    "sipm_detected_rms": float(summary["sipm_detected_rms"]),
                    "interaction_fraction": (
                        int(summary["primary_neutron_interaction_events"]) / events
                    ),
                    "generated_optical_zero_fraction": float(
                        summary["generated_optical_zero_fraction"]
                    ),
                    "scintillation_zero_fraction": float(
                        summary["scintillation_zero_fraction"]
                    ),
                    "sipm_detected_zero_fraction": float(
                        summary["sipm_detected_zero_fraction"]
                    ),
                }
            )

        recommendation = convergence_block_recommendation(
            report_rows, args.task_time_target_seconds
        )
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite benchmark directory: {output_dir}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            (temp_dir / "sacct.psv").write_text(raw_sacct, encoding="utf-8")
            write_csv(temp_dir / "benchmark_report.csv", report_rows)
            atomic_write_json(
                temp_dir / "benchmark_report.json",
                {
                    "schema_version": "realistic-neutron-benchmark-report-v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "campaign_id": bundle.campaign_id,
                    "plan_hash": bundle.plan_hash,
                    "git_commit": bundle.git_commit,
                    "accepted_statistical_evidence": (
                        validation.get("accepted_statistical_evidence") is True
                    ),
                    "output_bytes_definition": (
                        "root_output_bytes is the audited ROOT block size; "
                        "recorded_artifact_bytes also includes config, macro, log, and summaries."
                    ),
                    "tasks": report_rows,
                    "convergence_pilot_block_recommendation": recommendation,
                },
            )
            write_checksums(temp_dir)
            os.replace(temp_dir, output_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot summarize realistic-neutron benchmark: {exc}", file=sys.stderr)
        return 1

    print(f"Benchmark report: {output_dir}")
    print(
        "Recommended convergence pilot shape: "
        f"{recommendation['recommended_events_per_task']} events x "
        f"{recommendation['recommended_blocks_per_configuration']} blocks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
