#!/usr/bin/env python3
"""Prepare and optionally open all steel-module-scan-v1 SiPM geometries."""

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
LAYOUTS = {
    "back-center": {
        "detector_layout": "single",
        "face": "-Z",
        "sensor_centers_local_mm": [[0, 0, 0]],
        "copy_numbers": [0],
    },
    "edge-center": {
        "detector_layout": "single",
        "face": "+X",
        "sensor_centers_local_mm": [[0, 0, 0]],
        "copy_numbers": [0],
    },
    "edge-two": {
        "detector_layout": "edge-two",
        "face": "+X",
        "sensor_centers_local_mm": [[-25, 0, 0], [25, 0, 0]],
        "copy_numbers": [0, 1],
    },
    "back-four": {
        "detector_layout": "back-four",
        "face": "-Z",
        "sensor_centers_local_mm": [
            [-25, -25, 0],
            [-25, 25, 0],
            [25, -25, 0],
            [25, 25, 0],
        ],
        "copy_numbers": [0, 1, 2, 3],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tile-thickness-mm", type=int, choices=THICKNESSES_MM, default=16
    )
    parser.add_argument(
        "--sipm-layout",
        action="append",
        choices=tuple(LAYOUTS),
        dest="sipm_layouts",
        help=(
            "Layout to prepare; repeat for multiple layouts. "
            "Omitting this option prepares all four accepted layouts."
        ),
    )
    parser.add_argument(
        "--absorber-transverse-mm", type=int, choices=(200, 300, 500), default=500
    )
    parser.add_argument("--executable", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Group directory; one subdirectory is created per selected layout.",
    )
    parser.add_argument("--vis-driver", default="TSGQtZB")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate exact visual macros without opening GUI sessions.",
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


def prepare_layout(
    *,
    opnovice_dir: Path,
    group_temp_dir: Path,
    layout: str,
    tile_thickness_mm: int,
    absorber_transverse_mm: int,
    vis_driver: str,
) -> Path:
    expected = LAYOUTS[layout]
    layout_dir = group_temp_dir / layout
    layout_dir.mkdir()
    plan_root = layout_dir / "plan"
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
            "steel-module-scan-v1",
            "--tile-thickness-mm",
            str(tile_thickness_mm),
            "--sipm-layout",
            layout,
            "--absorber-transverse-mm",
            str(absorber_transverse_mm),
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
        raise ValueError(
            f"{layout}: visualization planner did not create exactly one run"
        )
    run_dir = configs[0].parent
    config = json.loads(configs[0].read_text(encoding="utf-8"))
    if config.get("study_preset") != "steel-module-scan-v1":
        raise ValueError(f"{layout}: planner resolved the wrong study preset")
    if config.get("sipm", {}).get("study_layout") != layout:
        raise ValueError(f"{layout}: planner resolved the wrong SiPM layout")

    with (run_dir / "points.csv").open(encoding="utf-8", newline="") as stream:
        points = list(csv.DictReader(stream))
    if len(points) != 1:
        raise ValueError(f"{layout}: planner did not create exactly one point")
    source_macro = Path(points[0]["macro"])
    if not source_macro.is_absolute():
        source_macro = run_dir / source_macro

    fragment = (
        opnovice_dir / "steel_module_scan_geometry_visual.fragment.mac"
    ).read_text(encoding="utf-8")
    fragment = fragment.replace("/vis/open TSGQtZB", f"/vis/open {vis_driver}")
    rendered_fragment = layout_dir / "visual.fragment.mac"
    rendered_fragment.write_text(fragment, encoding="utf-8")
    output_macro = layout_dir / "steel_module_scan_geometry_visual.mac"
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
            "/opnovice2/sipm/layout",
            "--require",
            "/opnovice2/sipm/face",
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
    shutil.copy2(configs[0], layout_dir / "run_config.json")
    rendered_fragment.unlink()
    shutil.rmtree(plan_root)

    sensor_description = ", ".join(
        f"copy {copy}: {position} mm"
        for copy, position in zip(
            expected["copy_numbers"], expected["sensor_centers_local_mm"]
        )
    )
    (layout_dir / "README.txt").write_text(
        "Optional visual sanity check only; not statistical evidence.\n"
        f"Study layout: {layout}; detector face: {expected['face']}.\n"
        f"Sensor identities: {sensor_description}.\n"
        "Blue: 100 x 100 mm EJ-200 tile. Green: SiPM volume(s). "
        "Gray wireframe: steel absorber.\n"
        "The camera starts on the downstream/-Z and +X side. The macro first "
        "renders a close SiPM detail and finishes on a wider steel overview; "
        "zoom or rotate as needed to inspect contact surfaces.\n"
        "The generated macro retains a centered 1 GeV kinetic-energy neutron "
        "source directed along 0 0 -1, but /run/beamOn is set to 0.\n",
        encoding="utf-8",
    )
    (layout_dir / "visualization.json").write_text(
        json.dumps(
            {
                "schema_version": "steel-module-scan-geometry-visual-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "study_preset": "steel-module-scan-v1",
                "study_layout": layout,
                "detector_layout": expected["detector_layout"],
                "tile_full_size_mm": [100, 100, tile_thickness_mm],
                "absorber_full_size_mm": [
                    absorber_transverse_mm,
                    absorber_transverse_mm,
                    40,
                ],
                "steel_tile_gap_mm": 0,
                "sipm_face": expected["face"],
                "sensor_centers_local_mm": expected["sensor_centers_local_mm"],
                "copy_numbers": expected["copy_numbers"],
                "neutron_kinetic_energy_mev": 1000,
                "beam_center_mm": [0, 0],
                "beam_direction": "0 0 -1",
                "vis_driver": vis_driver,
                "initial_viewpoint_theta_phi_deg": [120, 20],
                "detail_zoom": 3,
                "final_overview_zoom": 1.5,
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
    layouts = (
        list(dict.fromkeys(args.sipm_layouts))
        if args.sipm_layouts
        else list(LAYOUTS)
    )
    opnovice_dir = Path(__file__).resolve().parent
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else opnovice_dir
        / "scan_runs"
        / "geometry_visuals"
        / (
            f"{timestamp}_steel_module_scan_t{args.tile_thickness_mm:02d}_"
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
            raise ValueError(
                f"refusing to overwrite visualization directory: {output_dir}"
            )
        if not args.prepare_only and (
            not executable.is_file() or not os.access(executable, os.X_OK)
        ):
            raise ValueError(
                f"GUI executable is unavailable: {executable}. Build with "
                "cmake -S . -B build-gui -DWITH_GEANT4_UIVIS=ON and "
                "cmake --build build-gui."
            )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        group_temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            for layout in layouts:
                prepare_layout(
                    opnovice_dir=opnovice_dir,
                    group_temp_dir=group_temp_dir,
                    layout=layout,
                    tile_thickness_mm=args.tile_thickness_mm,
                    absorber_transverse_mm=args.absorber_transverse_mm,
                    vis_driver=args.vis_driver,
                )
            os.replace(group_temp_dir, output_dir)
        finally:
            if group_temp_dir.exists():
                shutil.rmtree(group_temp_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Cannot prepare steel-module-scan visualization: {exc}",
            file=sys.stderr,
        )
        return 1

    macros = [
        output_dir / layout / "steel_module_scan_geometry_visual.mac"
        for layout in layouts
    ]
    print(f"Visualization group: {output_dir}")
    for layout, macro in zip(layouts, macros):
        print(f"{layout}: {macro}")
    if args.prepare_only:
        print("Prepared only; no GUI was opened.")
        for macro in macros:
            print(f"Open with: {executable} -i {macro}")
        return 0

    for layout, macro in zip(layouts, macros):
        print(
            f"Opening {layout}. Close this Geant4 session to continue to the next layout."
        )
        try:
            subprocess.run([str(executable), "-i", str(macro)], cwd=macro.parent, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(
                f"Cannot open {layout} Geant4 visualization: {exc}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
