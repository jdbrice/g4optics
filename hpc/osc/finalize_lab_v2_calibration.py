#!/usr/bin/env python3
"""Validate and merge the latest lab v2 divergence-calibration submission."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from merge_array_efficiency_maps import (
    slurm_file_info,
    task_id_from_slurm_file,
)


DEFAULT_MANIFEST = Path(
    "hpc/osc/generated/lab_v2_realsetup/divergence_calibration/"
    "submission-manifest.tsv"
)
DEFAULT_OUTPUT_DIR = Path(
    "test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_calibration"
)


@dataclass(frozen=True)
class Submission:
    job_id: str
    task_count: int
    plan: str


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Validate task logs and merge the latest complete lab v2 calibration "
            "attempt recorded in its submission manifest."
        )
    )
    parser.add_argument(
        "--project-root",
        default=str(project_root),
        help="Repository root. Defaults to the root containing this script.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Submission manifest path, relative to the project root by default.",
    )
    parser.add_argument(
        "--slurm-dir",
        default=".",
        help="Directory containing Slurm output files, relative to the project root.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Merged-output directory, relative to the project root by default.",
    )
    parser.add_argument(
        "--expected-plans",
        type=int,
        default=24,
        help="Number of manifest rows in one submission attempt.",
    )
    parser.add_argument(
        "--expected-tasks",
        type=int,
        default=516,
        help="Expected total task count in the selected attempt.",
    )
    return parser.parse_args()


def resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / path


def load_latest_attempt(manifest: Path, expected_plans: int) -> list[Submission]:
    with manifest.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) < expected_plans:
        raise ValueError(
            f"manifest has {len(rows)} submissions; need at least {expected_plans}"
        )

    selected = rows[-expected_plans:]
    submissions: list[Submission] = []
    for row in selected:
        try:
            job_id = row["job_id"].strip()
            task_count = int(row["task_count"])
            plan = row["plan"].strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid manifest row: {row}") from exc
        if not job_id.isdigit() or task_count <= 0 or not plan:
            raise ValueError(f"invalid manifest row: {row}")
        submissions.append(Submission(job_id, task_count, plan))

    plans = [submission.plan for submission in submissions]
    if len(set(plans)) != len(plans):
        raise ValueError("latest manifest block contains duplicate plans")
    return submissions


def count_plan_tasks(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(
            1
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        )


def audit_submission(
    project_root: Path, slurm_dir: Path, submission: Submission
) -> list[str]:
    errors: list[str] = []
    plan_path = resolve_path(project_root, submission.plan)
    if not plan_path.is_file():
        errors.append(f"{submission.job_id}: missing plan {plan_path}")
    else:
        plan_tasks = count_plan_tasks(plan_path)
        if plan_tasks != submission.task_count:
            errors.append(
                f"{submission.job_id}: plan has {plan_tasks} tasks, "
                f"manifest records {submission.task_count}"
            )

    slurm_files = list(
        slurm_dir.glob(f"slurm-g4optics-scan-{submission.job_id}_*.out")
    )
    task_files: dict[int, Path] = {}
    for slurm_file in slurm_files:
        task_id = task_id_from_slurm_file(slurm_file, submission.job_id)
        if task_id in task_files:
            errors.append(
                f"{submission.job_id}: duplicate task log {task_id}: "
                f"{task_files[task_id]} and {slurm_file}"
            )
        task_files[task_id] = slurm_file

    expected_ids = set(range(1, submission.task_count + 1))
    actual_ids = set(task_files)
    missing_ids = sorted(expected_ids - actual_ids)
    extra_ids = sorted(actual_ids - expected_ids)
    if missing_ids:
        errors.append(f"{submission.job_id}: missing task logs {missing_ids}")
    if extra_ids:
        errors.append(f"{submission.job_id}: unexpected task logs {extra_ids}")

    for task_id in sorted(expected_ids & actual_ids):
        slurm_file = task_files[task_id]
        run_dirs, scan_complete = slurm_file_info(slurm_file)
        if not run_dirs:
            errors.append(f"{slurm_file.name}: no Run directory line")
        if not scan_complete:
            errors.append(f"{slurm_file.name}: no Scan complete line")
    return errors


def relative_display(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def merge_submission(
    project_root: Path,
    slurm_dir: Path,
    output_dir: Path,
    submission: Submission,
) -> tuple[Path, list[str], list[dict[str, str]]]:
    merge_script = Path(__file__).with_name("merge_array_efficiency_maps.py")
    output_csv = output_dir / Path(submission.plan).stem / "efficiency_map.csv"
    command = [
        sys.executable,
        str(merge_script),
        "--job-id",
        submission.job_id,
        "--project-root",
        str(project_root),
        "--slurm-dir",
        str(slurm_dir),
        "--out",
        str(output_csv),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"merge failed for job {submission.job_id}:\n{detail}")

    with output_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if len(rows) != submission.task_count:
        raise RuntimeError(
            f"job {submission.job_id}: merged {len(rows)} rows, "
            f"expected {submission.task_count}"
        )
    return output_csv, fields, rows


def write_outputs(
    project_root: Path,
    output_dir: Path,
    summaries: list[dict[str, str]],
    combined_fields: list[str],
    combined_rows: list[dict[str, str]],
) -> None:
    summary_path = output_dir / "finalization_summary.tsv"
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            delimiter="\t",
            fieldnames=["job_id", "task_count", "plan", "merged_rows", "output_csv"],
        )
        writer.writeheader()
        writer.writerows(summaries)

    combined_path = output_dir / "all_efficiency_maps.csv"
    with combined_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["plan"] + combined_fields)
        writer.writeheader()
        writer.writerows(combined_rows)

    print(f"Summary: {relative_display(summary_path, project_root)}")
    print(f"Combined CSV: {relative_display(combined_path, project_root)}")


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    manifest = resolve_path(project_root, args.manifest)
    slurm_dir = resolve_path(project_root, args.slurm_dir)
    output_dir = resolve_path(project_root, args.output_dir)

    try:
        submissions = load_latest_attempt(manifest, args.expected_plans)
    except (OSError, ValueError) as exc:
        print(f"Cannot load latest submission attempt: {exc}", file=sys.stderr)
        return 1

    total_tasks = sum(submission.task_count for submission in submissions)
    if total_tasks != args.expected_tasks:
        print(
            f"Latest attempt has {total_tasks} tasks; expected {args.expected_tasks}.",
            file=sys.stderr,
        )
        return 1

    audit_errors: list[str] = []
    for submission in submissions:
        audit_errors.extend(audit_submission(project_root, slurm_dir, submission))
    if audit_errors:
        print("Cannot finalize latest submission attempt:", file=sys.stderr)
        for error in audit_errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, str]] = []
    combined_fields: list[str] | None = None
    combined_rows: list[dict[str, str]] = []
    try:
        for submission in submissions:
            output_csv, fields, rows = merge_submission(
                project_root, slurm_dir, output_dir, submission
            )
            if combined_fields is None:
                combined_fields = fields
            elif fields != combined_fields:
                raise RuntimeError(
                    f"job {submission.job_id}: merged CSV header does not match prior jobs"
                )
            combined_rows.extend(
                {"plan": submission.plan, **row}
                for row in rows
            )
            summaries.append(
                {
                    "job_id": submission.job_id,
                    "task_count": str(submission.task_count),
                    "plan": submission.plan,
                    "merged_rows": str(len(rows)),
                    "output_csv": relative_display(output_csv, project_root),
                }
            )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Cannot merge latest submission attempt: {exc}", file=sys.stderr)
        return 1

    write_outputs(
        project_root,
        output_dir,
        summaries,
        combined_fields or [],
        combined_rows,
    )
    print(
        f"Finalized {len(submissions)} arrays, {total_tasks} tasks, "
        f"and {len(combined_rows)} merged rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
