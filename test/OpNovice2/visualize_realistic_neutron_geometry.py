#!/usr/bin/env python3
"""Prepare and optionally open the locked realistic-neutron geometry in Geant4."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tile-thickness-mm", type=int, choices=(4, 16), default=16)
    parser.add_argument(
        "--absorber-transverse-mm", type=int, choices=(200, 300, 500), default=500
    )
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--vis-driver", default="TSGQtZB")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate the exact visual macro without opening a GUI session.",
    )
    return parser.parse_args()


def run(
    args: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise ValueError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    return result


def main() -> int:
    args = parse_args()
    opnovice_dir = Path(__file__).resolve().parent
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else opnovice_dir
        / "scan_runs"
        / "geometry_visuals"
        / (
            f"{timestamp}_realistic_neutron_t{args.tile_thickness_mm:02d}_"
            f"a{args.absorber_transverse_mm:03d}"
        )
    )
    executable = (
        args.executable.expanduser().resolve()
        if args.executable is not None
        else opnovice_dir / "build-gui" / "OpNovice2"
    )
    try:
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite visualization directory: {output_dir}")
        if not args.prepare_only and (
            not executable.is_file() or not os.access(executable, os.X_OK)
        ):
            raise ValueError(
                f"GUI executable is unavailable: {executable}. Build with "
                "cmake -S . -B build-gui -DWITH_GEANT4_UIVIS=ON and "
                "cmake --build build-gui."
            )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            plan_root = temp_dir / "plan"
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
            run(
                [
                    str(opnovice_dir / "run_sipm_cavity_scan.sh"),
                    "full",
                    "custom",
                    "--study-preset",
                    "realistic-neutron-v1",
                    "--tile-thickness-mm",
                    str(args.tile_thickness_mm),
                    "--absorber-transverse-mm",
                    str(args.absorber_transverse_mm),
                    "--x-min",
                    "0",
                    "--x-max",
                    "0",
                    "--y-min",
                    "0",
                    "--y-max",
                    "0",
                    "--step",
                    "5",
                    "--grid-unit",
                    "mm",
                    "--events",
                    "1",
                    "--seed1",
                    "10101",
                    "--seed2",
                    "20202",
                    "--no-root-plots",
                    "--dry-run",
                ],
                cwd=opnovice_dir,
                env=env,
            )
            configs = sorted((plan_root / "runs").glob("*/run_config.json"))
            if len(configs) != 1:
                raise ValueError("visualization planner did not create exactly one run")
            run_dir = configs[0].parent
            with (run_dir / "points.csv").open(
                encoding="utf-8", newline=""
            ) as stream:
                points = list(csv.DictReader(stream))
            if len(points) != 1:
                raise ValueError("visualization planner did not create exactly one point")
            source_macro = Path(points[0]["macro"])
            if not source_macro.is_absolute():
                source_macro = run_dir / source_macro

            fragment = (
                opnovice_dir / "realistic_neutron_geometry_visual.fragment.mac"
            ).read_text(encoding="utf-8")
            fragment = fragment.replace("/vis/open TSGQtZB", f"/vis/open {args.vis_driver}")
            rendered_fragment = temp_dir / "visual.fragment.mac"
            rendered_fragment.write_text(fragment, encoding="utf-8")
            output_macro = temp_dir / "realistic_neutron_geometry_visual.mac"
            run(
                [
                    sys.executable,
                    str(opnovice_dir / "generate_scan_macro.py"),
                    "--template",
                    str(source_macro),
                    "--out",
                    str(output_macro),
                    "--set",
                    "/analysis/setFileName=geometry_visual",
                    "--set",
                    "/run/beamOn=0",
                    "--insert-file-before",
                    f"{rendered_fragment}=/run/beamOn",
                    "--require",
                    "/opnovice2/absorber/enabled",
                    "--require",
                    "/opnovice2/absorber/size",
                    "--require",
                    "/opnovice2/sipm/face",
                    "--require",
                    "/gps/direction",
                    "--require",
                    "/run/beamOn",
                ],
                cwd=opnovice_dir,
            )
            shutil.copy2(configs[0], temp_dir / "run_config.json")
            rendered_fragment.unlink()
            shutil.rmtree(plan_root)
            (temp_dir / "README.txt").write_text(
                "Optional visual sanity check only; not statistical evidence.\n"
                "Blue: EJ-200 tile. Green: centered -Z SiPM. "
                "Gray wireframe: steel absorber.\n"
                "The generated macro retains /gps/direction 0 0 -1.\n",
                encoding="utf-8",
            )
            (temp_dir / "visualization.json").write_text(
                json.dumps(
                    {
                        "schema_version": "realistic-neutron-geometry-visual-v1",
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "tile_thickness_mm": args.tile_thickness_mm,
                        "absorber_full_size_mm": [
                            args.absorber_transverse_mm,
                            args.absorber_transverse_mm,
                            40,
                        ],
                        "steel_tile_gap_mm": 0,
                        "sipm_face": "-Z",
                        "beam_direction": "0 0 -1",
                        "vis_driver": args.vis_driver,
                        "beam_on": 0,
                        "acceptance_gate": False,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temp_dir, output_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot prepare realistic-neutron visualization: {exc}", file=sys.stderr)
        return 1

    macro = output_dir / "realistic_neutron_geometry_visual.mac"
    print(f"Visualization macro: {macro}")
    if args.prepare_only:
        print("Prepared only; GUI was not opened.")
        return 0
    try:
        subprocess.run([str(executable), "-i", str(macro)], cwd=output_dir, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Cannot open Geant4 visualization: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
