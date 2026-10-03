#!/usr/bin/env python3
"""Check six-thickness layout placements printed by Geant4 against frozen inputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re


LAYOUTS = {
    "back-four": ("-Z", [(-25, -25), (-25, 25), (25, -25), (25, 25)]),
    "back-two": ("-Z", [(-25, -25), (25, 25)]),
    "back-center": ("-Z", [(0, 0)]),
    "edge-two": ("+X", [(-25, 0), (25, 0)]),
}
ALLOWED_THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
VECTOR = rf"\(({NUMBER}),\s*({NUMBER}),\s*({NUMBER})\)"
SUMMARY = re.compile(
    rf"Stack layout study: layout=(\S+), layers=(\d+), internal_gaps=(\d+), "
    rf"readout_gap=({NUMBER}) mm, core_length=({NUMBER}) mm, "
    rf"required_source_z=({NUMBER}) mm, sensor_stride=(\d+), sensors_per_layer=(\d+)"
)
PLACEMENT = re.compile(
    rf"Stack (tile|steel) placement: layer=(\d+), world={VECTOR} mm, full_size={VECTOR} mm"
)
SENSOR = re.compile(
    rf"SiPM placement: layout=(\S+), layer=(\d+), local_sensor=(\d+), copy=(\d+), "
    rf"face=(\S+), local={VECTOR} mm, world={VECTOR} mm"
)
AABB = re.compile(
    r"Stack layout geometry: PASS, (\d+) placed boxes, no positive-volume overlap, "
    r"all contained in World; 9 internal gaps of 0\.5 mm, final tile followed by World\."
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def close(actual, expected, label: str) -> None:
    require(math.isfinite(float(actual)) and math.isclose(float(actual), float(expected),
            rel_tol=0, abs_tol=1e-9), f"{label}: expected {expected}, found {actual}")


def vector(actual, expected, label: str) -> None:
    require(len(actual) == len(expected) == 3, f"{label}: expected a three-vector")
    for axis, (found, wanted) in enumerate(zip(actual, expected)):
        close(found, wanted, f"{label} axis {axis}")


def records(log: str, prefix: str, pattern: re.Pattern) -> list[re.Match]:
    found = []
    for line in log.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            match = pattern.fullmatch(line)
            require(match is not None, f"malformed geometry line: {line}")
            found.append(match)
    return found


def unique_rows(rows, label):
    result = {}
    for key, value in rows:
        require(key not in result, f"duplicate {label}: {key}")
        result[key] = value
    return result


def audit_geometry_text(log: str, config: dict) -> dict:
    """Placement checks only; audit_geometry_file also verifies process/log health."""
    require(config.get("schema_version") == "steel-layout-study-config-v1", "wrong config schema")
    layout = config.get("layout")
    require(layout in LAYOUTS, "unknown layout")
    face, local_positions = LAYOUTS[layout]
    count = len(local_positions)
    require(config.get("layout_study") is True, "layout study must be enabled")
    require(config.get("layers") == 10, "config must have ten layers")
    require(config.get("sensors_per_layer") == count and config.get("sensor_count") == 10 * count,
            "wrong config sensor count")
    require(config.get("sensor_copy_rule") == "4*layer+local_sensor", "wrong config copy rule")
    require(config.get("sipm_face") == face, "wrong config face")
    require(config.get("sipm_local_centers_mm") == [list(p) for p in local_positions],
            "wrong config local positions or sensor order")
    tile_size = config["tile_size_mm"]
    require(isinstance(tile_size, (list, tuple)) and len(tile_size) == 3,
            "tile size must contain three dimensions")
    thickness = tile_size[2]
    require(isinstance(thickness, (int, float)) and not isinstance(thickness, bool)
            and thickness in ALLOWED_THICKNESSES_MM,
            "tile thickness must be 4, 8, 12, 16, 20, or 24 mm")
    thickness = int(thickness)
    if "tile_thickness_mm" in config:
        require(config["tile_thickness_mm"] == thickness,
                "config thickness label disagrees with tile dimensions")
    core_length = 10 * (40 + thickness) + 9 * 0.5
    source_z = core_length / 2 + 1.5
    pitch = 40 + thickness + 0.5
    vector(tile_size, [100, 100, thickness], "tile size")
    vector(config["steel_size_mm"], [500, 500, 40], "steel size")
    vector(config["sipm_size_mm"], [2.4, 2.4, 0.5], "SiPM size")
    close(config["stack_length_mm"], core_length, "config core length")
    close(config["steel_to_tile_gap_mm"], 0, "config steel-to-tile gap")
    close(config["tile_to_next_steel_gap_mm"], 0.5, "config inter-module gap")
    vector(config["source"]["position_mm"], [0, 0, source_z], "config source position")

    summaries = records(log, "Stack layout study:", SUMMARY)
    require(len(summaries) == 1, "expected exactly one layout summary")
    summary = summaries[0]
    require(summary[1] == layout and int(summary[2]) == 10 and int(summary[3]) == 9,
            "summary layout/layer/internal-gap mismatch")
    close(summary[4], 0.5, "actual readout gap")
    close(summary[5], core_length, "actual core length")
    close(summary[6], source_z, "required source z")
    require(int(summary[7]) == 4 and int(summary[8]) == count, "summary sensor stride/count mismatch")

    boxes = records(log, "Stack tile placement:", PLACEMENT)
    boxes += records(log, "Stack steel placement:", PLACEMENT)
    placements = unique_rows((((match[1], int(match[2])), match) for match in boxes), "box placement")
    require(set(placements) == {(kind, layer) for kind in ("tile", "steel") for layer in range(10)},
            "missing or unexpected tile/steel placement")
    sensors = unique_rows((((int(match[2]), int(match[3])), match)
                           for match in records(log, "SiPM placement:", SENSOR)), "sensor placement")
    require(set(sensors) == {(layer, sensor) for layer in range(10) for sensor in range(count)},
            "missing or unexpected sensor placement")
    config_layers = unique_rows(((row["layer"], row) for row in config["layer_geometry"]), "config layer")
    require(set(config_layers) == set(range(10)), "missing or unexpected config layer")

    for layer in range(10):
        row = config_layers[layer]
        expected_steel_z = core_length / 2 - 20 - pitch * layer
        expected_tile_z = core_length / 2 - 40 - thickness / 2 - pitch * layer
        close(row["steel_center_z_mm"], expected_steel_z, f"config layer {layer} steel center")
        close(row["tile_center_z_mm"], expected_tile_z, f"config layer {layer} tile center")
        for kind, expected_z, expected_size in (("tile", expected_tile_z, [100, 100, thickness]),
                                                ("steel", expected_steel_z, [500, 500, 40])):
            match = placements[(kind, layer)]
            vector(match.group(3, 4, 5), [0, 0, expected_z], f"actual layer {layer} {kind} center")
            vector(match.group(6, 7, 8), expected_size, f"actual layer {layer} {kind} size")
        config_sensors = unique_rows(((sensor["local_sensor"], sensor) for sensor in row["sensors"]),
                                    f"config layer {layer} sensor")
        require(set(config_sensors) == set(range(count)), f"missing or unexpected config sensors in layer {layer}")
        for sensor, (u, v) in enumerate(local_positions):
            expected_world = ([u, v, expected_tile_z - thickness / 2 - 0.25] if face == "-Z"
                              else [50.25, u, expected_tile_z + v])
            expected_copy = 4 * layer + sensor
            spec = config_sensors[sensor]
            require(spec["global_copy"] == expected_copy, "config sensor copy mismatch")
            vector(spec["center_mm"], expected_world, "config sensor world center")
            match = sensors[(layer, sensor)]
            require(match[1] == layout and match[5] == face, "actual sensor layout/face mismatch")
            require(int(match[4]) == expected_copy, "actual sensor copy mismatch")
            vector(match.group(6, 7, 8), [u, v, 0], "actual sensor local center")
            vector(match.group(9, 10, 11), spec["center_mm"], "actual sensor world center")

    # Infer all interfaces from actual placement centers and sizes. Ten tiles
    # need nine internal readout gaps; the last tile ends at -core_length/2.
    gaps = []
    for layer in range(10):
        steel, tile = placements[("steel", layer)], placements[("tile", layer)]
        steel_downstream = float(steel[5]) - float(steel[8]) / 2
        tile_upstream = float(tile[5]) + float(tile[8]) / 2
        close(steel_downstream - tile_upstream, 0, "actual steel-to-tile interface")
        if layer < 9:
            following = placements[("steel", layer + 1)]
            gap = float(tile[5]) - float(tile[8]) / 2 - (float(following[5]) + float(following[8]) / 2)
            close(gap, 0.5, "actual inter-module gap")
            gaps.append(gap)
    close(float(placements[("steel", 0)][5]) + 20, core_length / 2,
          "actual core upstream extent")
    close(float(placements[("tile", 9)][5]) - thickness / 2, -core_length / 2,
          "actual final tile extent without tenth gap")
    aabb = records(log, "Stack layout geometry:", AABB)
    require(len(aabb) == 1, "missing or repeated deterministic AABB PASS")
    require(int(aabb[0][1]) == 20 + 10 * count, "AABB box count mismatch")
    return {"layout": layout, "tile_thickness_mm": thickness,
            "tile_count": 10, "steel_count": 10, "sensor_count": 10 * count,
            "sensor_stride": 4, "actual_internal_gaps_mm": gaps, "core_length_mm": core_length,
            "required_source_z_mm": source_z, "aabb_check": "passed",
            "source_check_scope": "geometry-required source; event audit checks emitted particle position",
            "scope": f"{thickness} mm placed geometry; no statistical-precision claim"}


def audit_geometry_file(config_path: Path, log_path: Path, exit_code: int) -> dict:
    require(exit_code == 0, f"process exited {exit_code}")
    # Reuse the event auditor's exact exception policy, including its narrowly
    # recognized serial ntuple-merging warning. No ROOT file is needed here.
    spec = importlib.util.spec_from_file_location("layout_event_audit", Path(__file__).with_name("audit.py"))
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    log = audit.audit_log(log_path)
    report = audit_geometry_text(log, json.loads(config_path.read_text()))
    report.update(status="passed", exit_code=exit_code,
                  config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                  log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = audit_geometry_file(args.config, args.log, args.exit_code)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, f"geometry audit failed: {exc}\n")
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
