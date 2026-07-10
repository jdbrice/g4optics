#!/usr/bin/env python3
"""Generate point-level scan plans that exactly follow a lab response CSV."""

from __future__ import annotations

import argparse
import csv
import re
import shlex
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


DEFAULT_DIVERGENCES_MRAD = ("25", "35", "45", "55", "65", "75")


@dataclass(frozen=True)
class LabPoint:
    x: Decimal
    y: Decimal


@dataclass(frozen=True)
class TileGeometry:
    x_mm: Decimal
    y_mm: Decimal
    thickness_mm: Decimal

    @property
    def tank_size(self) -> str:
        return (
            f"{format_decimal(self.x_mm)} {format_decimal(self.y_mm)} "
            f"{format_decimal(self.thickness_mm)} mm"
        )


def decimal_arg(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value}") from exc


def format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f")


def shell_line(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def infer_tile_geometry(lab_csv: Path) -> TileGeometry:
    text = f"{lab_csv.parent.name}_{lab_csv.name}"
    tile_match = re.search(r"tile_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", text)
    grid_match = re.search(r"grid_(\d+(?:\.\d+)?)mm", text)
    if not tile_match or not grid_match:
        raise ValueError(
            "could not infer tile geometry from lab CSV name; pass --tank-size explicitly"
        )
    return TileGeometry(
        x_mm=Decimal(tile_match.group(1)),
        y_mm=Decimal(tile_match.group(2)),
        thickness_mm=Decimal(grid_match.group(1)),
    )


def read_lab_points(lab_csv: Path, x_offset: Decimal, y_offset: Decimal) -> list[LabPoint]:
    with lab_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"missing CSV header: {lab_csv}")
        required = {"X", "Y"}
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"{lab_csv} is missing columns: {', '.join(sorted(missing))}")

        points: list[LabPoint] = []
        for row in reader:
            points.append(
                LabPoint(
                    x=Decimal(row["X"]) + x_offset,
                    y=Decimal(row["Y"]) + y_offset,
                )
            )
    if not points:
        raise ValueError(f"no lab points found in {lab_csv}")
    return points


def inside_count(points: list[LabPoint], geometry: TileGeometry) -> int:
    half_x = geometry.x_mm / Decimal("2")
    half_y = geometry.y_mm / Decimal("2")
    return sum(1 for p in points if abs(p.x) <= half_x and abs(p.y) <= half_y)


def build_scan_args(
    *,
    mode: str,
    events: int,
    source_mode: str,
    source_model: str | None,
    tank_size: str,
    point: LabPoint,
    step: Decimal,
    grid_unit: str,
    beam_z: str | None,
    beam_sigma: str | None,
    beam_divergence_mrad: str,
    surface_preset: str | None,
    surface_reflectivity_model: str | None,
    surface_reflectivity: str | None,
    surface_reflectivity_csv: str | None,
    surface_rindex: str | None,
    surface_rindex_csv: str | None,
    optical_coupling: str | None,
    grease_thickness: str | None,
    grease_size: str | None,
    grease_rindex: str | None,
    grease_rindex_csv: str | None,
    grease_absorption_model: str | None,
    grease_transmission_csv: str | None,
    grease_abs_length: str | None,
    sipm_face: str,
    sipm_local_position: str,
    sipm_size: str | None,
) -> list[str]:
    args = [
        mode,
        "custom",
        "--events",
        str(events),
        "--source-mode",
        source_mode,
        "--tank-size",
        tank_size,
        "--x-min",
        format_decimal(point.x),
        "--x-max",
        format_decimal(point.x),
        "--y-min",
        format_decimal(point.y),
        "--y-max",
        format_decimal(point.y),
        "--step",
        format_decimal(step),
        "--grid-unit",
        grid_unit,
    ]
    if beam_z is not None:
        args.extend(["--beam-z", beam_z])
    if beam_sigma is not None:
        args.extend(["--beam-sigma", beam_sigma])
    args.extend(["--beam-divergence-mrad", beam_divergence_mrad])
    if source_model is not None:
        args.extend(["--source-model", source_model])
    if surface_preset is not None:
        args.extend(["--surface-preset", surface_preset])
    if surface_reflectivity_model is not None:
        args.extend(["--surface-reflectivity-model", surface_reflectivity_model])
    if surface_reflectivity is not None:
        args.extend(["--surface-reflectivity", surface_reflectivity])
    if surface_reflectivity_csv is not None:
        args.extend(["--surface-reflectivity-csv", surface_reflectivity_csv])
    if surface_rindex is not None:
        args.extend(["--surface-rindex", surface_rindex])
    if surface_rindex_csv is not None:
        args.extend(["--surface-rindex-csv", surface_rindex_csv])
    if optical_coupling is not None:
        args.extend(["--optical-coupling", optical_coupling])
    if grease_thickness is not None:
        args.extend(["--grease-thickness", grease_thickness])
    if grease_size is not None:
        args.extend(["--grease-size", grease_size])
    if grease_rindex is not None:
        args.extend(["--grease-rindex", grease_rindex])
    if grease_rindex_csv is not None:
        args.extend(["--grease-rindex-csv", grease_rindex_csv])
    if grease_absorption_model is not None:
        args.extend(["--grease-absorption-model", grease_absorption_model])
    if grease_transmission_csv is not None:
        args.extend(["--grease-transmission-csv", grease_transmission_csv])
    if grease_abs_length is not None:
        args.extend(["--grease-abs-length", grease_abs_length])
    args.extend(["--sipm-face", sipm_face])
    args.extend(["--sipm-local-position", sipm_local_position])
    if sipm_size is not None:
        args.extend(["--sipm-size", sipm_size])
    return args


def parse_tank_size(tank_size: str | None, lab_csv: Path) -> tuple[str, TileGeometry]:
    if tank_size is None:
        geometry = infer_tile_geometry(lab_csv)
        return geometry.tank_size, geometry

    parts = tank_size.split()
    if len(parts) != 4 or parts[3] != "mm":
        raise ValueError('--tank-size must use quoted "x y z mm" form for lab plan metadata')
    geometry = TileGeometry(
        x_mm=Decimal(parts[0]),
        y_mm=Decimal(parts[1]),
        thickness_mm=Decimal(parts[2]),
    )
    return tank_size, geometry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate one scan-plan file per requested lab-data beam divergence."
    )
    parser.add_argument("--lab-csv", required=True, help="Lab responses_*.csv file.")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory where one plan file per divergence will be written.",
    )
    parser.add_argument("--label", required=True, help="Stable label used in output filenames.")
    parser.add_argument("--events", type=int, default=500, help="Events per lab point.")
    parser.add_argument("--mode", default="full")
    parser.add_argument("--source-mode", default="gps", choices=["gps"])
    parser.add_argument(
        "--source-model",
        default="sr90-spectrum",
        choices=["fixed-electron", "sr90-spectrum", "sr90-empirical"],
    )
    parser.add_argument(
        "--surface-preset",
        default="wrapped",
        choices=[
            "polished",
            "ground",
            "wrapped",
            "polishedfrontpainted",
            "groundfrontpainted",
            "polishedbackpainted",
            "groundbackpainted",
            "none",
        ],
        help="Use none to omit --surface-preset.",
    )
    parser.add_argument(
        "--surface-reflectivity-model",
        choices=["ej510-empirical", "constant", "none"],
    )
    parser.add_argument("--surface-reflectivity")
    parser.add_argument("--surface-reflectivity-csv")
    parser.add_argument("--surface-rindex")
    parser.add_argument("--surface-rindex-csv")
    parser.add_argument("--optical-coupling", choices=["none", "ej550-grease"])
    parser.add_argument("--grease-thickness")
    parser.add_argument("--grease-size")
    parser.add_argument("--grease-rindex")
    parser.add_argument("--grease-rindex-csv")
    parser.add_argument(
        "--grease-absorption-model",
        choices=["transparent", "constant", "ej550-transmission-derived"],
    )
    parser.add_argument("--grease-transmission-csv")
    parser.add_argument("--grease-abs-length")
    parser.add_argument("--tank-size", help='Override inferred tank size, e.g. "50 50 16 mm".')
    parser.add_argument("--step", type=decimal_arg, default=Decimal("1"))
    parser.add_argument("--grid-unit", default="mm", choices=["mm"])
    parser.add_argument("--beam-z", help="Optional beam z override in grid units.")
    parser.add_argument(
        "--beam-sigma",
        help="Optional transverse GPS beam sigma in grid units; omitted for point spot.",
    )
    parser.add_argument(
        "--divergence-mrad",
        action="append",
        default=[],
        help="Beam divergence candidate. Repeat, or omit for 25,35,...,75 mrad.",
    )
    parser.add_argument("--sipm-face", default="-Z", choices=["-Z", "+Z", "-X", "+X", "-Y", "+Y"])
    parser.add_argument("--sipm-local-position", default="0 0 0 cm")
    parser.add_argument("--sipm-size", help='Override SiPM size, e.g. "2.4 2.4 0.5 mm".')
    parser.add_argument(
        "--x-offset",
        type=decimal_arg,
        default=Decimal("0"),
        help="Additive lab-to-sim x offset in grid units.",
    )
    parser.add_argument(
        "--y-offset",
        type=decimal_arg,
        default=Decimal("0"),
        help="Additive lab-to-sim y offset in grid units.",
    )
    parser.add_argument("--description", help="Extra comment written into every plan.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.events <= 0:
        raise SystemExit("--events must be greater than 0")

    lab_csv = Path(args.lab_csv)
    tank_size, geometry = parse_tank_size(args.tank_size, lab_csv)
    points = read_lab_points(lab_csv, args.x_offset, args.y_offset)
    divergences = args.divergence_mrad or list(DEFAULT_DIVERGENCES_MRAD)
    surface_preset = None if args.surface_preset == "none" else args.surface_preset

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_inside = inside_count(points, geometry)
    written = 0
    for divergence in divergences:
        decimal_arg(divergence)
        suffix = f"{args.label}_{format_decimal(Decimal(divergence))}mrad_{args.events}events.txt"
        out_path = out_dir / suffix
        lines = [
            "# Generated by hpc/osc/generate_lab_scan_plan.py",
            f"# lab_csv={lab_csv}",
            f"# label={args.label}",
            f"# tank_size={tank_size}",
            f"# points={len(points)}",
            f"# inside_nominal_tile={n_inside}",
            f"# outside_nominal_tile={len(points) - n_inside}",
            f"# beam_divergence_mrad={format_decimal(Decimal(divergence))}",
            f"# beam_sigma={args.beam_sigma if args.beam_sigma is not None else 'point'}",
            f"# sipm_size={args.sipm_size if args.sipm_size is not None else 'template'}",
            f"# surface_reflectivity_model={args.surface_reflectivity_model or 'runner-default'}",
            f"# surface_rindex={args.surface_rindex or 'runner-default'}",
            f"# surface_rindex_csv={args.surface_rindex_csv or 'none'}",
            f"# optical_coupling={args.optical_coupling or 'runner-default'}",
            f"# grease_thickness={args.grease_thickness or 'none'}",
            f"# grease_absorption_model={args.grease_absorption_model or 'runner-default'}",
            f"# grease_transmission_csv={args.grease_transmission_csv or 'runner-default'}",
        ]
        if args.description:
            lines.append(f"# {args.description}")
        lines.append("")

        for point in points:
            scan_args = build_scan_args(
                mode=args.mode,
                events=args.events,
                source_mode=args.source_mode,
                source_model=args.source_model,
                tank_size=tank_size,
                point=point,
                step=args.step,
                grid_unit=args.grid_unit,
                beam_z=args.beam_z,
                beam_sigma=args.beam_sigma,
                beam_divergence_mrad=format_decimal(Decimal(divergence)),
                surface_preset=surface_preset,
                surface_reflectivity_model=args.surface_reflectivity_model,
                surface_reflectivity=args.surface_reflectivity,
                surface_reflectivity_csv=args.surface_reflectivity_csv,
                surface_rindex=args.surface_rindex,
                surface_rindex_csv=args.surface_rindex_csv,
                optical_coupling=args.optical_coupling,
                grease_thickness=args.grease_thickness,
                grease_size=args.grease_size,
                grease_rindex=args.grease_rindex,
                grease_rindex_csv=args.grease_rindex_csv,
                grease_absorption_model=args.grease_absorption_model,
                grease_transmission_csv=args.grease_transmission_csv,
                grease_abs_length=args.grease_abs_length,
                sipm_face=args.sipm_face,
                sipm_local_position=args.sipm_local_position,
                sipm_size=args.sipm_size,
            )
            lines.append(shell_line(scan_args))

        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {len(points)} tasks to {out_path}")
        print(f"Submit with: sbatch --array=1-{len(points)} hpc/osc/submit_scan.sbatch")
        written += 1

    print(f"Wrote {written} lab scan plan files in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
