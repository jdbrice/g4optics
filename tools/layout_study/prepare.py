#!/usr/bin/env python3
"""Prepare one thickness of four-layout stack inputs without running Geant4.

Run each run.mac in a separate process whose working directory is its layout
directory. A packaged, checksum-verified baseline template preserves the
original material and physics commands; Git and Bash are not required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


BASELINE_COMMIT = "a2d05dfe1a6c76df2acd072d7fab8cff366a02f6"
BASELINE_TEMPLATE_SHA256 = "4a323d8dd501eec584e94354515905756dc9ecec4f5780ecbace3f40aeb7607d"
BASELINE_PROVENANCE_SHA256 = "0e8fb86f00d2d7d4a652609f325340a06af4cc4246309afb0a01f6c2b0948733"
LAYOUTS = {
    "back-four": ("-Z", ((-25, -25), (-25, 25), (25, -25), (25, 25))),
    "back-two": ("-Z", ((-25, -25), (25, 25))),
    "back-center": ("-Z", ((0, 0),)),
    "edge-two": ("+X", ((-25, 0), (25, 0))),
}
LAYER_COUNT = 10
TILE_THICKNESS_MM = 4
TILE_THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
INTER_MODULE_GAP_MM = 0.5
MAX_SEED = 2_147_483_646


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command_name(line: str) -> str | None:
    active = line.partition("#")[0].strip()
    return active.split()[0] if active.startswith("/") else None


def replace_once(text: str, command: str, arguments: str) -> str:
    lines = text.splitlines(keepends=True)
    indexes = [i for i, line in enumerate(lines) if command_name(line) == command]
    if len(indexes) != 1:
        raise ValueError(f"expected exactly one {command}, found {len(indexes)}")
    lines[indexes[0]] = f"{command} {arguments}\n".rstrip() + "\n"
    return "".join(lines)


def baseline_template(root: Path) -> tuple[str, dict]:
    """Read the exact packaged baseline and its immutable provenance record.

    Both digests are pinned independently of the JSON contents. Replacing the
    template and merely updating its JSON hash must not silently change physics.
    The historical Git identifiers are provenance, not runtime dependencies.
    """
    templates = root / "tools/layout_study/templates"
    template = (templates / "baseline_template.mac").read_bytes()
    provenance_bytes = (templates / "baseline_template.provenance.json").read_bytes()
    if sha256(template) != BASELINE_TEMPLATE_SHA256:
        raise ValueError("packaged baseline template checksum mismatch")
    if sha256(provenance_bytes) != BASELINE_PROVENANCE_SHA256:
        raise ValueError("packaged baseline provenance checksum mismatch")
    provenance = json.loads(provenance_bytes)
    if (provenance.get("git_commit") != BASELINE_COMMIT or
            provenance.get("template_sha256") != BASELINE_TEMPLATE_SHA256):
        raise ValueError("packaged baseline identity mismatch")
    return template.decode("utf-8"), provenance


def stack_length_mm(thickness: int) -> float:
    if thickness not in TILE_THICKNESSES_MM:
        raise ValueError("tile thickness must be one of 4, 8, 12, 16, 20, 24 mm")
    return LAYER_COUNT * (40 + thickness) + (LAYER_COUNT - 1) * INTER_MODULE_GAP_MM


def layer_geometry(layout: str, thickness: int = TILE_THICKNESS_MM) -> list[dict]:
    face, centers = LAYOUTS[layout]
    length = stack_length_mm(thickness)
    rows = []
    for layer in range(LAYER_COUNT):
        upstream = length / 2 - layer * (40 + thickness + INTER_MODULE_GAP_MM)
        tile_z = upstream - 40 - thickness / 2
        sensors = []
        for sensor, (u, v) in enumerate(centers):
            position = ([u, v, tile_z - thickness / 2 - 0.25]
                        if face == "-Z" else [50.25, u, tile_z + v])
            sensors.append({"local_sensor": sensor, "global_copy": 4 * layer + sensor,
                            "center_mm": position})
        rows.append({"layer": layer, "steel_center_z_mm": upstream - 20,
                     "tile_center_z_mm": tile_z, "sensors": sensors})
    return rows


def study_macro(template: str, layout: str, events: int, seeds: tuple[int, int],
                thickness: int = TILE_THICKNESS_MM) -> str:
    face, _ = LAYOUTS[layout]
    source_z = stack_length_mm(thickness) / 2 + 1.5
    text = template
    for command, value in {
        "/opnovice2/sipm/layout": layout,
        "/opnovice2/sipm/face": face,
        "/gps/pos/centre": f"0 0 {source_z} mm",
        "/random/setSeeds": f"{seeds[0]} {seeds[1]}",
        "/analysis/setFileName": "result",
    }.items():
        text = replace_once(text, command, value)
    # Keep the already-validated 4 mm macro bytes unchanged. The frozen
    # template is 4 mm; other thicknesses change only this geometry command.
    if thickness != TILE_THICKNESS_MM:
        text = replace_once(text, "/opnovice2/tank/size", f"100 100 {thickness} mm")
    text = replace_once(text, "/run/initialize", "").replace(
        "/run/initialize\n", "/opnovice2/stack/layoutStudy true\n/run/initialize\n", 1)
    # Geometry sampling consumes random numbers. Reset afterward so beamOn
    # starts from the recorded seeds regardless of the geometry-test workload.
    tail = (
        "/geometry/test/run\n"
        f"/random/setSeeds {seeds[0]} {seeds[1]}\n"
        + (f"/run/beamOn {events}\n" if events else "")
    )
    lines = text.splitlines(keepends=True)
    beam_indexes = [i for i, line in enumerate(lines) if command_name(line) == "/run/beamOn"]
    if len(beam_indexes) != 1:
        raise ValueError("frozen macro must have exactly one beamOn")
    lines[beam_indexes[0]] = tail
    return "".join(lines)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def prepare(root: Path, output: Path, *, events: int, seeds: tuple[int, int],
            layouts: tuple[str, ...] = tuple(LAYOUTS), thickness: int = 4) -> dict:
    length = stack_length_mm(thickness)
    source_z = length / 2 + 1.5
    if events < 0:
        raise ValueError("events must be nonnegative")
    if len(seeds) != 2 or any(seed < 1 or seed > MAX_SEED for seed in seeds) or seeds[0] == seeds[1]:
        raise ValueError(f"two distinct seeds in 1..{MAX_SEED} are required")
    if not layouts or len(set(layouts)) != len(layouts) or any(layout not in LAYOUTS for layout in layouts):
        raise ValueError("choose unique layouts from back-four, back-two, back-center, edge-two")
    if output.exists():
        raise ValueError(f"refusing to overwrite existing output: {output}")
    template, provenance = baseline_template(root)
    output.mkdir(parents=True)
    (output / "baseline_template.mac").write_text(template)
    registry = {
        "schema_version": "steel-layout-study-inputs-v1",
        "stage": "engineering-validation",
        "tile_thickness_mm": thickness,
        "accepted_statistical_evidence": False,
        "baseline": provenance,
        "events_per_layout": events,
        "seeds": list(seeds),
        "seed_policy": "same requested pair at the start of each independent layout process",
        "geometry_test_seed_reset": True,
        "execution": "one process per layout; cwd is layout directory; run run.mac",
        "layouts": [],
    }
    for layout in layouts:
        directory = output / layout
        directory.mkdir()
        macro = study_macro(template, layout, events, seeds, thickness)
        (directory / "run.mac").write_text(macro)
        face, centers = LAYOUTS[layout]
        configuration = {
            "schema_version": "steel-layout-study-config-v1",
            "baseline_commit": BASELINE_COMMIT,
            "baseline_template_sha256": provenance["template_sha256"],
            "layout": layout,
            "layout_study": True,
            "tile_thickness_mm": thickness,
            "tile_size_mm": [100, 100, thickness],
            "steel_size_mm": [500, 500, 40],
            "layers": LAYER_COUNT,
            "stack_length_mm": length,
            "steel_to_tile_gap_mm": 0,
            "tile_to_next_steel_gap_mm": INTER_MODULE_GAP_MM,
            "sipm_face": face,
            "sipm_size_mm": [2.4, 2.4, 0.5],
            "sipm_local_centers_mm": centers,
            "sensors_per_layer": len(centers),
            "sensor_count": LAYER_COUNT * len(centers),
            "sensor_copy_rule": "4*layer+local_sensor",
            "source": {"particle": "neutron", "kinetic_energy_mev": 1000,
                       "position_mm": [0, 0, source_z], "direction": [0, 0, -1]},
            "events": events,
            "seeds": list(seeds),
            "geometry_test_seed_reset": True,
            "macro": "run.mac",
            "macro_sha256": sha256(macro.encode()),
            "expected_root": "result.root" if events else None,
            "expected_summary": "result_summary.csv" if events else None,
            "layer_geometry": layer_geometry(layout, thickness),
        }
        write_json(directory / "config.json", configuration)
        registry["layouts"].append({
            "layout": layout, "macro": f"{layout}/run.mac", "config": f"{layout}/config.json",
            "macro_sha256": configuration["macro_sha256"],
            "config_sha256": sha256((directory / "config.json").read_bytes()),
        })
    write_json(output / "registry.json", registry)
    files = sorted(path for path in output.rglob("*") if path.is_file())
    (output / "SHA256SUMS").write_text("".join(
        f"{sha256(path.read_bytes())}  {path.relative_to(output).as_posix()}\n" for path in files))
    return registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--events", type=int, required=True)
    parser.add_argument("--seed1", type=int, required=True)
    parser.add_argument("--seed2", type=int, required=True)
    parser.add_argument("--layout", choices=tuple(LAYOUTS), action="append")
    parser.add_argument("--tile-thickness-mm", type=int, choices=TILE_THICKNESSES_MM, default=4)
    args = parser.parse_args()
    try:
        registry = prepare(Path(__file__).resolve().parents[2], args.output_dir.resolve(),
                           events=args.events, seeds=(args.seed1, args.seed2),
                           layouts=tuple(args.layout or LAYOUTS), thickness=args.tile_thickness_mm)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"input preparation failed: {exc}\n")
    print(json.dumps({"registry": str(args.output_dir.resolve() / "registry.json"),
                      "layouts": len(registry["layouts"]), "events_per_layout": args.events}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
