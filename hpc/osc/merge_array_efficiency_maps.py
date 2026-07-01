#!/usr/bin/env python3
"""Merge efficiency_map.csv files produced by a point-level Slurm array."""

from __future__ import annotations

import argparse
import csv
import json
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
    parser.add_argument(
        "--out",
        help=(
            "Merged output path. If it ends in .csv, write that file. Otherwise treat it "
            "as a run directory and write efficiency_map.csv inside it. Defaults to an "
            "auto-named directory under test/OpNovice2/scan_runs."
        ),
    )
    parser.add_argument(
        "--label",
        help=(
            "Run-directory label used when --out is omitted, e.g. "
            "week9_2mm_thickness_1mm_beam_sigma."
        ),
    )
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


def read_run_config(run_dir: Path) -> dict[str, object]:
    run_config = run_dir / "run_config.json"
    if not run_config.is_file():
        return {}
    try:
        with run_config.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}


def number_token(value: object) -> str | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return str(int(number))
    text = f"{number:g}"
    return text.replace(".", "p").replace("-", "m")


def thickness_from_config(run_config: dict[str, object]) -> tuple[str, str] | None:
    tank = run_config.get("tank")
    if not isinstance(tank, dict):
        return None
    size = tank.get("size")
    if not isinstance(size, str):
        return None
    parts = size.split()
    if len(parts) < 4:
        return None
    thickness = number_token(parts[2])
    unit = parts[3]
    if thickness is None or not unit:
        return None
    return thickness, unit


def beam_sigma_from_config(run_config: dict[str, object]) -> tuple[str, str] | None:
    beam = run_config.get("beam")
    if not isinstance(beam, dict):
        return None
    if beam.get("profile") != "gaussian":
        return None
    sigma = number_token(beam.get("sigma"))
    unit = beam.get("unit")
    if sigma is None or not isinstance(unit, str) or not unit:
        return None
    return sigma, unit


def primary_energy_from_config(run_config: dict[str, object]) -> tuple[str, str] | None:
    simulation = run_config.get("simulation")
    if not isinstance(simulation, dict):
        return None
    energy = simulation.get("primary_energy")
    if not isinstance(energy, str):
        return None
    parts = energy.split()
    if len(parts) != 2:
        return None
    value = number_token(parts[0])
    unit = parts[1]
    if value is None or not unit:
        return None
    return value, unit


def electron_energy_mode_from_config(run_config: dict[str, object]) -> str | None:
    simulation = run_config.get("simulation")
    if not isinstance(simulation, dict):
        return None
    mode = simulation.get("electron_energy_mode")
    if not isinstance(mode, str) or not mode or mode == "fixed":
        return None
    return re.sub(r"[^A-Za-z0-9]+", "_", mode).strip("_")


def source_model_from_config(run_config: dict[str, object]) -> str | None:
    simulation = run_config.get("simulation")
    if not isinstance(simulation, dict):
        return None
    model = simulation.get("source_model")
    if not isinstance(model, str) or not model or model == "fixed-electron":
        return None
    return re.sub(r"[^A-Za-z0-9]+", "_", model).strip("_")


def auto_output_label(job_id: str, run_configs: list[dict[str, object]]) -> str:
    for run_config in run_configs:
        thickness = thickness_from_config(run_config)
        beam_sigma = beam_sigma_from_config(run_config)
        source_model = source_model_from_config(run_config)
        if thickness and source_model:
            thickness_value, thickness_unit = thickness
            if beam_sigma:
                sigma_value, sigma_unit = beam_sigma
                return (
                    f"week10_{thickness_value}{thickness_unit}_thickness_"
                    f"{sigma_value}{sigma_unit}_beam_sigma_{source_model}_source"
                )
            return (
                f"week10_{thickness_value}{thickness_unit}_thickness_"
                f"{source_model}_source"
            )
        electron_energy_mode = electron_energy_mode_from_config(run_config)
        if thickness and electron_energy_mode:
            thickness_value, thickness_unit = thickness
            if beam_sigma:
                sigma_value, sigma_unit = beam_sigma
                return (
                    f"week10_{thickness_value}{thickness_unit}_thickness_"
                    f"{sigma_value}{sigma_unit}_beam_sigma_{electron_energy_mode}_source"
                )
            return (
                f"week10_{thickness_value}{thickness_unit}_thickness_"
                f"{electron_energy_mode}_source"
            )
        if thickness and beam_sigma:
            thickness_value, thickness_unit = thickness
            sigma_value, sigma_unit = beam_sigma
            return (
                f"week9_{thickness_value}{thickness_unit}_thickness_"
                f"{sigma_value}{sigma_unit}_beam_sigma"
            )
        primary_energy = primary_energy_from_config(run_config)
        if thickness and primary_energy:
            thickness_value, thickness_unit = thickness
            energy_value, energy_unit = primary_energy
            return (
                f"week9_{thickness_value}{thickness_unit}_thickness_"
                f"{energy_value}{energy_unit}_energy"
            )
    return f"array_{job_id}"


def output_path_from_args(args: argparse.Namespace, project_root: Path, label: str) -> Path:
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = project_root / out_path
        if out_path.suffix.lower() == ".csv":
            return out_path
        return out_path / "efficiency_map.csv"

    return (
        project_root
        / "test"
        / "OpNovice2"
        / "scan_runs"
        / label
        / "efficiency_map.csv"
    )


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    slurm_dir = Path(args.slurm_dir).resolve()

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
    run_configs: list[dict[str, object]] = []

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

            run_config = read_run_config(run_dir)
            if run_config:
                run_configs.append(run_config)

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

    label = args.label or auto_output_label(args.job_id, run_configs)
    output_path = output_path_from_args(args, project_root, label)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["array_job_id", "array_task_id", "source_run_dir"] + (original_fields or [])
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged_rows)

    print(f"Wrote {len(merged_rows)} rows to {output_path}")
    if output_path.name == "efficiency_map.csv":
        print(f"Run directory: {output_path.parent}")
    if missing:
        print(f"Warning: skipped {len(missing)} incomplete inputs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
