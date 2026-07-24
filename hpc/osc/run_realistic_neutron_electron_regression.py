#!/usr/bin/env python3
"""Run a fixed-seed absorber-disabled electron regression across two executables."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from realistic_neutron_campaign_lib import atomic_write_json, sha256_file


INTEGER_FIELDS = (
    "events",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "shoot_position_events",
    "hit_position_events",
    "scint_centroid_events",
    "primary_energy_events",
)

FLOAT_FIELDS = (
    "collection_efficiency",
    "shoot_x_mm",
    "shoot_y_mm",
    "shoot_z_mm",
    "hit_x_mm",
    "hit_y_mm",
    "hit_z_mm",
    "scint_centroid_x_mm",
    "scint_centroid_y_mm",
    "scint_centroid_z_mm",
    "primary_energy_mean_mev",
    "primary_energy_rms_mev",
    "primary_energy_min_mev",
    "primary_energy_max_mev",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-executable", required=True, type=Path)
    parser.add_argument("--candidate-executable", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--seed1", type=int, default=17012026)
    parser.add_argument("--seed2", type=int, default=24072026)
    parser.add_argument("--geant4-version", default="11.4.2")
    parser.add_argument("--baseline-label", default="practice")
    parser.add_argument("--candidate-label", default="realistic-detector")
    parser.add_argument("--float-rel-tol", type=float, default=1e-12)
    parser.add_argument("--float-abs-tol", type=float, default=1e-12)
    return parser.parse_args()


def run_command(
    args: list[str], *, cwd: Path, env: dict[str, str] | None = None, log: Path | None = None
) -> None:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if log is not None:
        log.write_text(result.stdout, encoding="utf-8")
    if result.returncode != 0:
        raise ValueError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )


def read_single_csv_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"expected one summary row in {path}, found {len(rows)}")
    return rows[0]


def compare_summaries(
    baseline: dict[str, str],
    candidate: dict[str, str],
    *,
    rel_tol: float,
    abs_tol: float,
) -> list[dict[str, object]]:
    comparisons: list[dict[str, object]] = []
    for field in INTEGER_FIELDS:
        baseline_value = int(baseline[field])
        candidate_value = int(candidate[field])
        comparisons.append(
            {
                "field": field,
                "kind": "integer_exact",
                "baseline": baseline_value,
                "candidate": candidate_value,
                "passed": baseline_value == candidate_value,
            }
        )
    for field in FLOAT_FIELDS:
        baseline_value = float(baseline[field])
        candidate_value = float(candidate[field])
        if math.isnan(baseline_value) or math.isnan(candidate_value):
            passed = math.isnan(baseline_value) and math.isnan(candidate_value)
        else:
            passed = math.isclose(
                baseline_value,
                candidate_value,
                rel_tol=rel_tol,
                abs_tol=abs_tol,
            )
        comparisons.append(
            {
                "field": field,
                "kind": "float_close",
                "baseline": baseline_value,
                "candidate": candidate_value,
                "passed": passed,
            }
        )
    return comparisons


def patch_output_macro(
    generator: Path,
    template: Path,
    output: Path,
    output_base: str,
    cwd: Path,
    *,
    remove_commands: tuple[str, ...] = (),
) -> None:
    command = [
        sys.executable,
        str(generator),
        "--template",
        str(template),
        "--out",
        str(output),
        "--set",
        f"/analysis/setFileName={output_base}",
    ]
    for macro_command in remove_commands:
        command.extend(("--remove", macro_command))
    command.extend(
        (
            "--require",
            "/analysis/setFileName",
            "--require",
            "/random/setSeeds",
            "--require",
            "/run/beamOn",
        )
    )
    run_command(command, cwd=cwd)


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


def validate_regression_run_config(config: dict[str, object], events: int) -> None:
    expected = {
        "study_preset": None,
        "dry_run": True,
        "source_mode": "gps",
    }
    for key, value in expected.items():
        if config.get(key) != value:
            raise ValueError(f"electron regression planner has unexpected {key}")
    nested = (
        ("simulation", "events_per_point", events),
        ("simulation", "source_model", "fixed-electron"),
        ("simulation", "primary_particle", "e-"),
        ("simulation", "primary_energy", "1 MeV"),
        ("simulation", "electron_energy_mode", "fixed"),
        ("simulation", "beam_direction", "0 0 -1"),
        ("tank", "size", "50 50 4 mm"),
        ("absorber", "enabled", False),
        ("surface", "preset", "polishedfrontpainted"),
        ("surface", "reflectivity_model", "ej510-empirical"),
        ("sipm", "face", "-Z"),
        ("sipm", "local_position", "0 0 0 mm"),
        ("sipm", "size", "2.4 2.4 0.5 mm"),
        ("random", "explicit_seed_pair", True),
    )
    for section, key, expected_value in nested:
        value = config.get(section)
        if not isinstance(value, dict) or value.get(key) != expected_value:
            raise ValueError(
                f"electron regression planner has unexpected {section}.{key}"
            )


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    opnovice_dir = repo_root / "test" / "OpNovice2"
    baseline_executable = args.baseline_executable.expanduser().resolve()
    candidate_executable = args.candidate_executable.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    try:
        if args.events <= 0:
            raise ValueError("--events must be positive")
        if args.seed1 <= 0 or args.seed2 <= 0 or args.seed1 == args.seed2:
            raise ValueError("--seed1 and --seed2 must be distinct positive integers")
        for executable in (baseline_executable, candidate_executable):
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise ValueError(f"missing executable: {executable}")
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite regression directory: {output_dir}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            with tempfile.TemporaryDirectory(prefix="rn-electron-plan-") as raw_plan:
                plan_root = Path(raw_plan)
                env = os.environ.copy()
                env.update(
                    {
                        "SCAN_RUNS_DIR": str(plan_root / "runs"),
                        "LATEST_RUN_LINK": str(plan_root / "latest"),
                        "LATEST_POINTS_CSV": str(plan_root / "points.csv"),
                        "LATEST_RUN_CONFIG": str(plan_root / "run_config.json"),
                        "LATEST_EFFICIENCY_MAP": str(plan_root / "efficiency_map.csv"),
                    }
                )
                run_command(
                    [
                        str(opnovice_dir / "run_sipm_cavity_scan.sh"),
                        "full",
                        "custom",
                        "--events",
                        str(args.events),
                        "--source-mode",
                        "gps",
                        "--source-model",
                        "fixed-electron",
                        "--primary-energy",
                        "1 MeV",
                        "--electron-energy-mode",
                        "fixed",
                        "--tank-size",
                        "50 50 4 mm",
                        "--x-min",
                        "0",
                        "--x-max",
                        "0",
                        "--y-min",
                        "0",
                        "--y-max",
                        "0",
                        "--step",
                        "1",
                        "--grid-unit",
                        "mm",
                        "--surface-preset",
                        "polishedfrontpainted",
                        "--surface-reflectivity-model",
                        "ej510-empirical",
                        "--sipm-face",
                        "-Z",
                        "--sipm-local-position",
                        "0 0 0 mm",
                        "--sipm-size",
                        "2.4 2.4 0.5 mm",
                        "--seed1",
                        str(args.seed1),
                        "--seed2",
                        str(args.seed2),
                        "--no-root-plots",
                        "--dry-run",
                    ],
                    cwd=opnovice_dir,
                    env=env,
                )
                configs = sorted((plan_root / "runs").glob("*/run_config.json"))
                if len(configs) != 1:
                    raise ValueError("electron regression planner did not create one run")
                with configs[0].open(encoding="utf-8") as stream:
                    planned_config = json.load(stream)
                if not isinstance(planned_config, dict):
                    raise ValueError("electron regression run_config is not an object")
                validate_regression_run_config(planned_config, args.events)
                run_dir = configs[0].parent
                with (run_dir / "points.csv").open(
                    encoding="utf-8", newline=""
                ) as stream:
                    points = list(csv.DictReader(stream))
                if len(points) != 1:
                    raise ValueError("electron regression planner did not create one point")
                macro_path = Path(points[0]["macro"])
                if not macro_path.is_absolute():
                    macro_path = run_dir / macro_path
                shutil.copy2(configs[0], temp_dir / "run_config.json")
                patch_output_macro(
                    opnovice_dir / "generate_scan_macro.py",
                    macro_path,
                    temp_dir / "baseline.mac",
                    "baseline",
                    opnovice_dir,
                    # This command was introduced after practice@50ec06d4. The
                    # candidate default is already the equivalent single-SiPM
                    # layout, so omitting the candidate-only no-op keeps the
                    # differential macro semantically identical and executable
                    # by the frozen baseline.
                    remove_commands=("/opnovice2/sipm/layout",),
                )
                patch_output_macro(
                    opnovice_dir / "generate_scan_macro.py",
                    macro_path,
                    temp_dir / "candidate.mac",
                    "candidate",
                    opnovice_dir,
                )

            run_command(
                [str(baseline_executable), "baseline.mac"],
                cwd=temp_dir,
                log=temp_dir / "baseline.log",
            )
            run_command(
                [str(candidate_executable), "candidate.mac"],
                cwd=temp_dir,
                log=temp_dir / "candidate.log",
            )
            for path in (temp_dir / "baseline.root", temp_dir / "candidate.root"):
                if not path.is_file() or path.stat().st_size <= 0:
                    raise ValueError(f"electron regression did not create {path.name}")
            baseline = read_single_csv_row(temp_dir / "baseline_summary.csv")
            candidate = read_single_csv_row(temp_dir / "candidate_summary.csv")
            comparisons = compare_summaries(
                baseline,
                candidate,
                rel_tol=args.float_rel_tol,
                abs_tol=args.float_abs_tol,
            )
            failed = [item for item in comparisons if item["passed"] is not True]
            report = {
                "schema_version": "realistic-neutron-electron-regression-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "valid": not failed,
                "purpose": (
                    "Absorber-disabled fixed-seed differential regression against "
                    "the practice baseline before neutron statistical interpretation."
                ),
                "geant4_version": args.geant4_version,
                "configuration": {
                    "events": args.events,
                    "seed1": args.seed1,
                    "seed2": args.seed2,
                    "particle": "e-",
                    "kinetic_energy": "1 MeV",
                    "tile": "50 x 50 x 4 mm",
                    "position": "center",
                    "surface": "polishedfrontpainted / EJ-510 empirical",
                    "sipm": "2.4 x 2.4 x 0.5 mm centered on -Z",
                    "absorber_enabled": False,
                },
                "baseline": {
                    "label": args.baseline_label,
                    "executable": str(baseline_executable),
                    "sha256": sha256_file(baseline_executable),
                },
                "candidate": {
                    "label": args.candidate_label,
                    "executable": str(candidate_executable),
                    "sha256": sha256_file(candidate_executable),
                },
                "float_rel_tol": args.float_rel_tol,
                "float_abs_tol": args.float_abs_tol,
                "comparisons": comparisons,
                "failed_fields": [item["field"] for item in failed],
            }
            atomic_write_json(temp_dir / "regression_report.json", report)
            write_checksums(temp_dir)
            os.replace(temp_dir, output_dir)
            if failed:
                failed_names = ", ".join(str(item["field"]) for item in failed)
                raise ValueError(f"electron regression failed fields: {failed_names}")
        finally:
            if temp_dir.exists():
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                failure_dir = output_dir.with_name(
                    f"{output_dir.name}.failed-{timestamp}-{os.getpid()}"
                )
                os.replace(temp_dir, failure_dir)
                print(
                    f"Electron regression failure artifacts: {failure_dir}",
                    file=sys.stderr,
                )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot run electron regression: {exc}", file=sys.stderr)
        return 1

    print(f"Electron regression passed: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
