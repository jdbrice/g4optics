#!/usr/bin/env python3
"""Deterministically audit the steel-module-scan-v1 runner contract."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
LAYOUTS = {
    "back-center": {
        "detector_layout": "single",
        "face": "-Z",
        "sensor_count": 1,
        "positions": [[0, 0, 0]],
        "copy_numbers": [0],
    },
    "edge-center": {
        "detector_layout": "single",
        "face": "+X",
        "sensor_count": 1,
        "positions": [[0, 0, 0]],
        "copy_numbers": [0],
    },
    "edge-two": {
        "detector_layout": "edge-two",
        "face": "+X",
        "sensor_count": 2,
        "positions": [[-25, 0, 0], [25, 0, 0]],
        "copy_numbers": [0, 1],
    },
    "back-four": {
        "detector_layout": "back-four",
        "face": "-Z",
        "sensor_count": 4,
        "positions": [
            [-25, -25, 0],
            [-25, 25, 0],
            [25, -25, 0],
            [25, 25, 0],
        ],
        "copy_numbers": [0, 1, 2, 3],
    },
}


def command_arguments(path: Path) -> dict[str, str]:
    commands: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        active = raw.split("#", 1)[0].strip()
        if not active.startswith("/"):
            continue
        command, _, arguments = active.partition(" ")
        commands[command] = arguments.strip()
    return commands


def base_args(*, thickness: int, layout: str) -> list[str]:
    return [
        "full",
        "custom",
        "--study-preset",
        "steel-module-scan-v1",
        "--tile-thickness-mm",
        str(thickness),
        "--sipm-layout",
        layout,
        "--absorber-transverse-mm",
        "500",
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
        "1001",
        "--seed2",
        "2002",
        "--dry-run",
        "--no-root-plots",
    ]


def run_runner(
    runner: Path,
    repo_root: Path,
    scratch: Path,
    args: list[str],
    *,
    expect_success: bool,
    label: str,
) -> subprocess.CompletedProcess[str]:
    case_root = scratch / label
    case_root.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "SCAN_RUNS_DIR": str(case_root / "runs"),
            "LATEST_RUN_LINK": str(case_root / "latest"),
            "LATEST_POINTS_CSV": str(case_root / "points.csv"),
            "LATEST_RUN_CONFIG": str(case_root / "run_config.json"),
            "LATEST_EFFICIENCY_MAP": str(case_root / "efficiency.csv"),
            "PLOT_WITH_ROOT": "0",
            "SCAN_RUN_ID_SUFFIX": label,
        }
    )
    result = subprocess.run(
        [str(runner), *args],
        cwd=repo_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if expect_success and result.returncode != 0:
        raise AssertionError(
            f"{label} unexpectedly failed ({result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    if not expect_success and result.returncode == 0:
        raise AssertionError(f"{label} unexpectedly succeeded")
    return result


def audit_success(case_root: Path, *, thickness: int, layout: str) -> None:
    with (case_root / "run_config.json").open(encoding="utf-8") as stream:
        config = json.load(stream)
    expected = LAYOUTS[layout]
    expected_source_z = 0.5 * thickness + 40 + 1.5

    assert config["schema_version"] == "opnovice2-run-config-v4"
    assert config["event_schema_version"] == "opnovice2-scan-event-v3"
    assert config["study_preset"] == "steel-module-scan-v1"
    simulation = config["simulation"]
    assert simulation["authoritative_source_quantity"] == "kinetic_energy"
    assert simulation["authoritative_kinetic_energy_mev"] == 1000
    assert simulation["authoritative_momentum_gev_c"] is None
    assert simulation["derived_momentum_gev_c"] == 1.696800177
    assert simulation["derived_total_energy_gev"] == 1.939565421
    assert simulation["gps_kinetic_energy_mev"] == 1000
    assert simulation["primary_particle"] == "neutron"
    assert simulation["primary_energy"] == "1000 MeV"
    assert simulation["beam_z"] == f"{expected_source_z:g} mm"
    assert config["beam"]["profile"] == "point"
    assert config["beam"]["angular_model"] == "pencil"
    assert config["beam"]["divergence_mrad"] is None
    assert config["tank"]["size"] == f"100 100 {thickness} mm"
    assert config["absorber"]["full_size_mm"] == [500, 500, 40]
    assert config["absorber"]["tile_gap_mm"] == 0
    assert config["surface"]["preset"] == "polishedfrontpainted"
    assert config["surface"]["reflectivity_model"] == "ej510-empirical"
    coupling = config["optical_coupling"]
    assert coupling["model"] == "none"
    assert coupling["geometry_model"] == "undimpled-zero-gap-ej550-proxy"
    sipm = config["sipm"]
    assert sipm["study_layout"] == layout
    assert sipm["detector_layout"] == expected["detector_layout"]
    assert sipm["face"] == expected["face"]
    assert sipm["sensor_count"] == expected["sensor_count"]
    assert sipm["copy_number_order"] == expected["copy_numbers"]
    assert sipm["fixed_local_positions_mm"] == expected["positions"]
    assert sipm["size"] == "2.4 2.4 0.5 mm"

    with (case_root / "points.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    macro = Path(rows[0]["macro"])
    commands = command_arguments(macro)
    assert commands["/gps/particle"] == "neutron"
    assert commands["/gps/energy"] == "1000 MeV"
    assert commands["/gps/pos/centre"] == f"0 0 {expected_source_z:g} mm"
    assert commands["/gps/direction"] == "0 0 -1"
    assert "/gps/ang/type" not in commands
    assert commands["/opnovice2/tank/size"] == f"100 100 {thickness} mm"
    assert commands["/opnovice2/absorber/enabled"] == "true"
    assert commands["/opnovice2/absorber/size"] == "500 500 40 mm"
    assert commands["/opnovice2/sipm/layout"] == expected["detector_layout"]
    assert commands["/opnovice2/sipm/face"] == expected["face"]
    assert commands["/opnovice2/sipm/localPosition"] == "0 0 0 mm"
    assert commands["/opnovice2/sipm/size"] == "2.4 2.4 0.5 mm"


def audit_visualizations(repo_root: Path, scratch: Path) -> None:
    output_dir = scratch / "visualizations"
    result = subprocess.run(
        [
            sys.executable,
            str(
                repo_root
                / "test/OpNovice2/visualize_steel_module_scan_geometry.py"
            ),
            "--tile-thickness-mm",
            "16",
            "--absorber-transverse-mm",
            "500",
            "--output-dir",
            str(output_dir),
            "--prepare-only",
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"visualization preparation failed ({result.returncode})\n{result.stdout}"
        )

    for layout, expected in LAYOUTS.items():
        layout_dir = output_dir / layout
        macro = layout_dir / "steel_module_scan_geometry_visual.mac"
        commands = command_arguments(macro)
        assert commands["/opnovice2/tank/size"] == "100 100 16 mm"
        assert commands["/opnovice2/absorber/size"] == "500 500 40 mm"
        assert commands["/opnovice2/sipm/layout"] == expected["detector_layout"]
        assert commands["/opnovice2/sipm/face"] == expected["face"]
        assert commands["/gps/particle"] == "neutron"
        assert commands["/gps/energy"] == "1000 MeV"
        assert commands["/gps/pos/centre"] == "0 0 49.5 mm"
        assert commands["/gps/direction"] == "0 0 -1"
        assert commands["/run/beamOn"] == "0"
        macro_text = macro.read_text(encoding="utf-8")
        assert "/vis/geometry/set/forceWireframe SteelAbsorber 0 true" in macro_text
        assert macro_text.count("/vis/viewer/flush") == 2

        metadata = json.loads(
            (layout_dir / "visualization.json").read_text(encoding="utf-8")
        )
        assert metadata["study_preset"] == "steel-module-scan-v1"
        assert metadata["study_layout"] == layout
        assert metadata["sensor_centers_local_mm"] == expected["positions"]
        assert metadata["copy_numbers"] == expected["copy_numbers"]
        assert metadata["beam_on"] == 0


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "test/OpNovice2/run_sipm_cavity_scan.sh"
    with tempfile.TemporaryDirectory(prefix="steel-module-scan-check-") as raw:
        scratch = Path(raw)
        for thickness in THICKNESSES_MM:
            for layout in LAYOUTS:
                label = f"t{thickness:02d}-{layout}"
                run_runner(
                    runner,
                    repo_root,
                    scratch,
                    base_args(thickness=thickness, layout=layout),
                    expect_success=True,
                    label=label,
                )
                audit_success(scratch / label, thickness=thickness, layout=layout)

        off_center_args = base_args(thickness=4, layout="back-center")
        off_center_args[off_center_args.index("--x-min") + 1] = "5"
        off_center_args[off_center_args.index("--x-max") + 1] = "5"
        negative_cases = {
            "missing-layout": [
                arg
                for index, arg in enumerate(base_args(thickness=4, layout="back-center"))
                if index not in (6, 7)
            ],
            "invalid-layout": [
                "invalid" if arg == "back-center" else arg
                for arg in base_args(thickness=4, layout="back-center")
            ],
            "invalid-thickness": [
                "6" if arg == "4" else arg
                for arg in base_args(thickness=4, layout="back-center")
            ],
            "energy-override": [
                *base_args(thickness=4, layout="back-center"),
                "--primary-energy",
                "2 GeV",
            ],
            "divergence-override": [
                *base_args(thickness=4, layout="back-center"),
                "--beam-divergence-mrad",
                "55",
            ],
            "manual-z": [
                *base_args(thickness=4, layout="back-center"),
                "--beam-z",
                "50",
            ],
            "off-center": off_center_args,
            "explicit-grease": [
                *base_args(thickness=4, layout="back-center"),
                "--optical-coupling",
                "ej550-grease",
            ],
        }
        for label, args in negative_cases.items():
            run_runner(
                runner,
                repo_root,
                scratch,
                args,
                expect_success=False,
                label=label,
            )

        old_label = "old-v1-compatibility"
        old_args = base_args(thickness=4, layout="back-center")
        old_args[old_args.index("steel-module-scan-v1")] = "realistic-neutron-v1"
        layout_index = old_args.index("--sipm-layout")
        del old_args[layout_index : layout_index + 2]
        run_runner(
            runner,
            repo_root,
            scratch,
            old_args,
            expect_success=True,
            label=old_label,
        )
        with (scratch / old_label / "run_config.json").open(encoding="utf-8") as stream:
            old_config = json.load(stream)
        assert old_config["schema_version"] == "opnovice2-run-config-v3"
        assert old_config["event_schema_version"] == "opnovice2-scan-event-v2"
        assert old_config["simulation"]["gps_kinetic_energy_mev"] == 432.58
        assert old_config["beam"]["divergence_mrad"] == 55

        audit_visualizations(repo_root, scratch)

    print(
        "steel-module-scan-v1 preset contract: "
        "PASS (24 configurations, 4 visualizations)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
