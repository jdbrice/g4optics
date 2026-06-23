#!/usr/bin/env python3
"""Merge efficiency_map.csv files produced by a point-level Slurm array."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


RUN_DIR_RE = re.compile(r"^Run directory:\s*(?P<path>\S.*)$")
SCAN_COMPLETE_RE = re.compile(r"^Scan complete\.$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge point-level scan outputs by reading Slurm .out files."
    )
    parser.add_argument("--job-id", required=True, help="Slurm array job id, e.g. 49601448.")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Repository root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--slurm-dir",
        default=".",
        help="Directory containing slurm-g4optics-scan-<job>_<task>.out files.",
    )
    parser.add_argument("--out", help="Merged CSV path. Defaults under test/OpNovice2/scan_runs.")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Write completed rows even if some array tasks are missing.",
    )
    return parser.parse_args()


def task_id_from_slurm_file(path: Path, job_id: str) -> int:
    match = re.search(rf"{re.escape(job_id)}_(\d+)\.out$", path.name)
    if not match:
        return 0
    return int(match.group(1))


def slurm_file_info(path: Path) -> tuple[list[str], bool]:
    run_dirs: list[str] = []
    scan_complete = False
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            match = RUN_DIR_RE.match(stripped)
            if match:
                run_dirs.append(match.group("path"))
            if SCAN_COMPLETE_RE.match(stripped):
                scan_complete = True
    return run_dirs, scan_complete


def resolve_run_dir(project_root: Path, run_dir: str) -> Path:
    path = Path(run_dir)
    if path.is_absolute():
        return path
    if run_dir.startswith("test/OpNovice2/"):
        return project_root / path
    return project_root / "test" / "OpNovice2" / path


def find_existing_run_dir(project_root: Path, run_dir_text: str) -> Path | None:
    resolved = resolve_run_dir(project_root, run_dir_text)
    if resolved.is_dir():
        return resolved

    run_name = Path(run_dir_text).name
    search_roots = [
        project_root / "test" / "OpNovice2" / "scan_runs",
        project_root / "scan_runs",
    ]
    for search_root in search_roots:
        candidate = search_root / run_name
        if candidate.is_dir():
            return candidate

    for candidate in (project_root / "test" / "OpNovice2").glob(f"**/{run_name}"):
        if candidate.is_dir():
            return candidate
    return None


def read_efficiency_rows(efficiency_map: Path) -> tuple[list[str], list[dict[str, str]]]:
    with efficiency_map.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if reader.fieldnames is None:
            raise ValueError(f"missing CSV header: {efficiency_map}")
        return list(reader.fieldnames), rows


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    slurm_dir = Path(args.slurm_dir).resolve()
    output_path = (
        Path(args.out)
        if args.out
        else project_root
        / "test"
        / "OpNovice2"
        / "scan_runs"
        / f"array_{args.job_id}_efficiency_map.csv"
    )
    if not output_path.is_absolute():
        output_path = project_root / output_path

    slurm_files = sorted(
        slurm_dir.glob(f"slurm-g4optics-scan-{args.job_id}_*.out"),
        key=lambda p: task_id_from_slurm_file(p, args.job_id),
    )
    if not slurm_files:
        print(f"No Slurm output files found for job {args.job_id} in {slurm_dir}", file=sys.stderr)
        return 1

    merged_rows: list[dict[str, str]] = []
    original_fields: list[str] | None = None
    missing: list[str] = []

    for slurm_file in slurm_files:
        task_id = task_id_from_slurm_file(slurm_file, args.job_id)
        run_dirs, scan_complete = slurm_file_info(slurm_file)
        if not run_dirs:
            missing.append(f"{slurm_file.name}: no Run directory line")
            continue
        if not scan_complete:
            missing.append(f"{slurm_file.name}: no Scan complete line; task may still be running or failed")

        for run_dir_text in run_dirs:
            run_dir = find_existing_run_dir(project_root, run_dir_text)
            if run_dir is None:
                expected = resolve_run_dir(project_root, run_dir_text)
                missing.append(f"{slurm_file.name}: missing run directory {expected}")
                continue
            efficiency_map = run_dir / "efficiency_map.csv"
            if not efficiency_map.is_file():
                if scan_complete:
                    missing.append(f"{slurm_file.name}: Scan complete but missing {efficiency_map}")
                else:
                    missing.append(f"{slurm_file.name}: incomplete task missing {efficiency_map}")
                continue

            fields, rows = read_efficiency_rows(efficiency_map)
            if original_fields is None:
                original_fields = fields
            elif fields != original_fields:
                missing.append(f"{slurm_file.name}: header mismatch in {efficiency_map}")
                continue

            for row in rows:
                merged = {
                    "array_job_id": args.job_id,
                    "array_task_id": str(task_id),
                    "source_run_dir": run_dir_text,
                }
                merged.update(row)
                merged_rows.append(merged)

    if missing and not args.allow_missing:
        print("Cannot merge all array tasks:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1

    if not merged_rows:
        print("No efficiency rows found.", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["array_job_id", "array_task_id", "source_run_dir"] + (original_fields or [])
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged_rows)

    print(f"Wrote {len(merged_rows)} rows to {output_path}")
    if missing:
        print(f"Warning: skipped {len(missing)} incomplete inputs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
