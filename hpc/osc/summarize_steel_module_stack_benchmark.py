#!/usr/bin/env python3
"""Freeze resources and choose the event block for a stack endpoint benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from realistic_neutron_campaign_lib import resolve_campaign_path, sha256_file, verify_checksum_manifest
from steel_module_stack_campaign_lib import FINALIZED_REQUIRED_FILES, load_campaign
from summarize_realistic_neutron_benchmark import accounting_for_task, parse_sacct, query_sacct


BLOCK_CHOICES = (250, 100, 50, 25, 10)
SAFETY_FACTOR = 1.5
WALL_TARGET_SECONDS = 2700
GIB = 1024**3


def select_block_size(seconds_per_event: float) -> int | None:
    if not math.isfinite(seconds_per_event) or seconds_per_event <= 0:
        raise ValueError("benchmark seconds per event must be finite and positive")
    return next(
        (block for block in BLOCK_CHOICES if SAFETY_FACTOR * seconds_per_event * block <= WALL_TARGET_SECONDS),
        None,
    )


def select_memory_gib(max_rss_bytes: int) -> int:
    if max_rss_bytes <= 0:
        raise ValueError("benchmark MaxRSS must be positive")
    return max(2, math.ceil(SAFETY_FACTOR * max_rss_bytes / GIB))


def artifact_sha(environment: dict[str, object], key: str) -> str | None:
    record = environment.get(key)
    return record.get("sha256") if isinstance(record, dict) else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--sacct-input", type=Path)
    parser.add_argument("--sacct-command", default="sacct")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(item for item in directory.iterdir() if item.is_file() and item.name != "SHA256SUMS"):
            stream.write(f"{sha256_file(path)}  {path.name}\n")


def main() -> int:
    args = parse_args()
    try:
        bundle = load_campaign(args.campaign_dir, verify_external_artifacts=False)
        if bundle.manifest.get("stage") != "benchmark" or len(bundle.tasks) != 2:
            raise ValueError("stack benchmark must contain exactly the 4/24 mm endpoint tasks")
        finalized = bundle.directory / "finalized"
        verify_checksum_manifest(finalized, required=FINALIZED_REQUIRED_FILES)
        validation = json.loads((finalized / "validation_report.json").read_text(encoding="utf-8"))
        if validation.get("valid") is not True or validation.get("selected_tasks") != 2:
            raise ValueError("stack benchmark finalization is not complete")
        index = read_tsv(finalized / "task_index.tsv")
        if {int(row["tile_thickness_mm"]) for row in index} != {4, 24}:
            raise ValueError("stack benchmark endpoints are not 4 and 24 mm")
        job_ids = sorted({row["slurm_job_id"] for row in index})
        if args.sacct_input:
            raw = args.sacct_input.expanduser().resolve().read_text(encoding="utf-8")
        else:
            raw = query_sacct(args.sacct_command, job_ids, bundle.directory)
        accounting = parse_sacct(raw)
        rows: list[dict[str, object]] = []
        for row in index:
            job_id = row["slurm_job_id"]
            array_index = row["slurm_array_index"]
            record = accounting_for_task(accounting, job_id, array_index, f"{job_id}_{array_index}")
            events = int(row["events"])
            root = resolve_campaign_path(bundle.directory, row["root"])
            root_bytes = root.stat().st_size
            rows.append(
                {
                    "logical_task_id": row["logical_task_id"],
                    "tile_thickness_mm": int(row["tile_thickness_mm"]),
                    "events": events,
                    "slurm_job_id": job_id,
                    "slurm_array_index": array_index,
                    "slurm_state": record["state"],
                    "slurm_exit_code": record["exit_code"],
                    "elapsed_seconds": int(record["elapsed_seconds"]),
                    "wall_seconds_per_event": int(record["elapsed_seconds"]) / events,
                    "max_rss_bytes": int(record["max_rss_bytes"]),
                    "root_output_bytes": root_bytes,
                    "root_output_bytes_per_event": root_bytes / events,
                }
            )
        slowest = max(float(row["wall_seconds_per_event"]) for row in rows)
        selected = select_block_size(slowest)
        max_rss = max(int(row["max_rss_bytes"]) for row in rows)
        memory_gib = select_memory_gib(max_rss)
        report = {
            "schema_version": "steel-module-stack-benchmark-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "campaign_id": bundle.campaign_id,
            "plan_hash": bundle.plan_hash,
            "git_commit": bundle.git_commit,
            "environment_identity": bundle.environment.get("identity_hash"),
            "image_sha256": artifact_sha(bundle.environment, "image"),
            "g4_data_manifest_sha256": artifact_sha(bundle.environment, "g4_data_manifest"),
            "executable_sha256": artifact_sha(bundle.environment, "build_artifact"),
            "accepted_statistical_evidence": bundle.environment.get("accepted_statistical_evidence") is True,
            "task_count": 2,
            "event_count": sum(int(row["events"]) for row in rows),
            "slowest_wall_seconds_per_event": slowest,
            "block_candidates": list(BLOCK_CHOICES),
            "block_selection_rule": "largest B with 1.5 * slowest_seconds_per_event * B <= 2700 seconds",
            "selected_events_per_task": selected,
            "selected_block_projected_seconds": None if selected is None else SAFETY_FACTOR * slowest * selected,
            "pilot_permitted_for_human_review": selected is not None,
            "maximum_rss_bytes": max_rss,
            "production_memory_gib": memory_gib,
            "production_memory_rule": "max(2 GiB, ceil(1.5 * benchmark MaxRSS / GiB))",
            "tasks": rows,
        }
        output = args.output_dir.expanduser().resolve() if args.output_dir else finalized / "benchmark"
        if output.exists():
            raise ValueError(f"refusing to overwrite benchmark report: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        try:
            (temp / "sacct.psv").write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
            with (temp / "benchmark_tasks.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            (temp / "benchmark_report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            write_checksums(temp)
            os.replace(temp, output)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot summarize steel-module-stack benchmark: {exc}", file=os.sys.stderr)
        return 1
    print(f"Stack benchmark report: {output}")
    if selected is None:
        print("No block size satisfies the 45-minute guarded runtime; stop before pilot.")
    else:
        print(f"Selected pilot block: {selected} events; production memory: {memory_gib} GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
