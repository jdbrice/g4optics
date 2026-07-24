#!/usr/bin/env python3
"""Prepare and optionally open steel-module-stack-v1 geometries."""

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


THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
LAYERS = 10
STEEL_THICKNESS_MM = 40


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tile-thickness-mm",
        type=int,
        action="append",
        choices=THICKNESSES_MM,
        dest="tile_thicknesses_mm",
        help="Thickness to prepare; repeat as needed. Default: all six.",
    )
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--vis-driver", default="TSGQtZB")
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args()


def run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode:
        raise ValueError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    return result.stdout


def layer_geometry(thickness_mm: int) -> list[dict[str, object]]:
    length = LAYERS * (STEEL_THICKNESS_MM + thickness_mm)
    rows: list[dict[str, object]] = []
    for layer in range(LAYERS):
        steel_z = length / 2 - layer * (STEEL_THICKNESS_MM + thickness_mm) - 20
        tile_z = (
            length / 2
            - layer * (STEEL_THICKNESS_MM + thickness_mm)
            - STEEL_THICKNESS_MM
            - thickness_mm / 2
        )
        rows.append(
            {
                "layer": layer,
                "steel_center_z_mm": steel_z,
                "tile_center_z_mm": tile_z,
                "sensor_global_copies": [2 * layer, 2 * layer + 1],
            }
        )
    return rows


def prepare_thickness(
    *,
    opnovice_dir: Path,
    group_temp_dir: Path,
    thickness_mm: int,
    vis_driver: str,
) -> Path:
    thickness_dir = group_temp_dir / f"t{thickness_mm:02d}"
    thickness_dir.mkdir()
    plan_root = thickness_dir / "plan"
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
            "steel-module-stack-v1",
            "--tile-thickness-mm",
            str(thickness_mm),
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
            "--events",
            "1",
            "--seed1",
            str(71000 + thickness_mm),
            "--seed2",
            str(72000 + thickness_mm),
            "--no-root-plots",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=env,
    )
    configs = sorted((plan_root / "runs").glob("*/run_config.json"))
    if len(configs) != 1:
        raise ValueError(f"t={thickness_mm}: expected exactly one planned run")
    config = json.loads(configs[0].read_text(encoding="utf-8"))
    if config.get("study_preset") != "steel-module-stack-v1":
        raise ValueError(f"t={thickness_mm}: wrong study preset")
    if config.get("stack", {}).get("layers") != LAYERS:
        raise ValueError(f"t={thickness_mm}: wrong stack layer count")
    if config.get("sipm", {}).get("sensor_count") != 20:
        raise ValueError(f"t={thickness_mm}: wrong sensor count")

    run_dir = configs[0].parent
    with (run_dir / "points.csv").open(encoding="utf-8", newline="") as stream:
        points = list(csv.DictReader(stream))
    if len(points) != 1:
        raise ValueError(f"t={thickness_mm}: expected one visual source point")
    source_macro = Path(points[0]["macro"])
    if not source_macro.is_absolute():
        source_macro = opnovice_dir / source_macro

    fragment = (opnovice_dir / "steel_module_stack_geometry_visual.fragment.mac").read_text(
        encoding="utf-8"
    )
    rendered_fragment = thickness_dir / "visual.fragment.mac"
    rendered_fragment.write_text(
        fragment.replace("/vis/open TSGQtZB", f"/vis/open {vis_driver}"),
        encoding="utf-8",
    )
    output_macro = thickness_dir / "steel_module_stack_geometry_visual.mac"
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
            "/opnovice2/stack/enabled",
            "--require",
            "/opnovice2/stack/layers",
            "--require",
            "/opnovice2/absorber/enabled",
            "--require",
            "/opnovice2/sipm/layout",
            "--require",
            "/gps/particle",
            "--require",
            "/gps/energy",
            "--require",
            "/gps/direction",
            "--require",
            "/run/beamOn",
        ],
        cwd=opnovice_dir,
    )
    shutil.copy2(configs[0], thickness_dir / "run_config.json")
    rendered_fragment.unlink()
    shutil.rmtree(plan_root)

    length = LAYERS * (STEEL_THICKNESS_MM + thickness_mm)
    (thickness_dir / "README.txt").write_text(
        "Optional visual sanity check only; not statistical evidence.\n"
        f"Ten [40 mm steel][{thickness_mm} mm tile] modules, total length {length} mm.\n"
        "Layer 0 is upstream (+Z); layer 9 is downstream (-Z).\n"
        "Each blue tile has two green SiPMs on +X, local centers (-25,0) and "
        "(+25,0) mm; global copies are 2*layer + local_sensor.\n"
        "Gray wireframes are SAE-304 steel. The retained neutron source is centered, "
        "1 GeV kinetic energy, and directed along -Z; beamOn is zero.\n",
        encoding="utf-8",
    )
    (thickness_dir / "visualization.json").write_text(
        json.dumps(
            {
                "schema_version": "steel-module-stack-geometry-visual-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "study_preset": "steel-module-stack-v1",
                "tile_full_size_mm": [100, 100, thickness_mm],
                "steel_full_size_mm": [500, 500, 40],
                "layers": LAYERS,
                "stack_length_mm": length,
                "source_z_mm": length / 2 + 1.5,
                "steel_tile_gap_mm": 0,
                "sipm_layout": "edge-two",
                "sipm_face": "+X",
                "sipm_size_mm": [2.4, 2.4, 0.5],
                "sensor_centers_local_mm": [[-25, 0], [25, 0]],
                "layer_geometry": layer_geometry(thickness_mm),
                "vis_driver": vis_driver,
                "beam_on": 0,
                "acceptance_gate": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output_macro


def main() -> int:
    args = parse_args()
    thicknesses = list(dict.fromkeys(args.tile_thicknesses_mm or THICKNESSES_MM))
    opnovice_dir = Path(__file__).resolve().parent
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else opnovice_dir / "scan_runs" / "geometry_visuals" / f"{timestamp}_steel_module_stack"
    )
    executable = (
        args.executable.expanduser().resolve()
        if args.executable
        else opnovice_dir / "build-gui" / "OpNovice2"
    )
    try:
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite visualization directory: {output_dir}")
        if not args.prepare_only and (
            not executable.is_file() or not os.access(executable, os.X_OK)
        ):
            raise ValueError(f"GUI executable is unavailable: {executable}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
        try:
            macros = [
                prepare_thickness(
                    opnovice_dir=opnovice_dir,
                    group_temp_dir=temp_dir,
                    thickness_mm=thickness,
                    vis_driver=args.vis_driver,
                )
                for thickness in thicknesses
            ]
            os.replace(temp_dir, output_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot prepare steel-module-stack visualization: {exc}", file=sys.stderr)
        return 1

    macros = [output_dir / f"t{t:02d}" / macro.name for t, macro in zip(thicknesses, macros)]
    print(f"Visualization group: {output_dir}")
    for thickness, macro in zip(thicknesses, macros):
        print(f"{thickness} mm: {macro}")
    if args.prepare_only:
        print("Prepared only; no GUI was opened.")
        for macro in macros:
            print(f"Open with: {executable} -i {macro}")
        return 0
    for thickness, macro in zip(thicknesses, macros):
        print(f"Opening {thickness} mm; close this session to continue.")
        subprocess.run([str(executable), "-i", str(macro)], cwd=macro.parent, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
