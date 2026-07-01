#!/usr/bin/env python3
"""Generate one-point-per-line scan plans for OSC Slurm arrays."""

from __future__ import annotations

import argparse
import itertools
import shlex
from decimal import Decimal, InvalidOperation
from pathlib import Path


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


def inclusive_axis(min_value: Decimal, max_value: Decimal, step: Decimal) -> list[Decimal]:
    if step <= 0:
        raise ValueError("--step must be greater than 0")
    if max_value < min_value:
        raise ValueError("axis max must be greater than or equal to min")

    span = max_value - min_value
    count = span / step
    if count != count.to_integral_value():
        raise ValueError("axis range must be evenly divisible by --step")

    n_steps = int(count)
    return [min_value + step * i for i in range(n_steps + 1)]


def shell_line(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def build_scan_args(
    *,
    mode: str,
    events: int,
    source_mode: str,
    tank_size: str,
    x: Decimal,
    y: Decimal,
    step: Decimal,
    grid_unit: str,
    primary_energy: str | None,
    surface_preset: str | None,
    dimple: bool,
    dimple_radius: str | None,
    dimple_unit: str,
    dimple_sipm_mode: str,
    beam_z: str | None,
    beam_sigma: str | None,
    electron_energy_mode: str | None,
    sipm_face: str | None,
    sipm_local_position: str | None,
) -> list[str]:
    x_str = format_decimal(x)
    y_str = format_decimal(y)
    step_str = format_decimal(step)

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
        x_str,
        "--x-max",
        x_str,
        "--y-min",
        y_str,
        "--y-max",
        y_str,
        "--step",
        step_str,
        "--grid-unit",
        grid_unit,
    ]

    if beam_z is not None:
        args.extend(["--beam-z", beam_z])
    if beam_sigma is not None:
        args.extend(["--beam-sigma", beam_sigma])
    if primary_energy is not None:
        args.extend(["--primary-energy", primary_energy])
    if electron_energy_mode is not None:
        args.extend(["--electron-energy-mode", electron_energy_mode])
    if sipm_face is not None:
        args.extend(["--sipm-face", sipm_face])
    if sipm_local_position is not None:
        args.extend(["--sipm-local-position", sipm_local_position])
    if surface_preset is not None:
        args.extend(["--surface-preset", surface_preset])
    if dimple:
        if not dimple_radius:
            raise ValueError("--dimple requires --dimple-radius")
        args.extend(
            [
                "--dimple",
                "--dimple-radius",
                dimple_radius,
                "--dimple-unit",
                dimple_unit,
                "--dimple-sipm-mode",
                dimple_sipm_mode,
            ]
        )

    return args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Slurm array plan where each line runs one scan point."
    )
    parser.add_argument("--out", required=True, help="Plan file to write.")
    parser.add_argument("--mode", default="full", help="Scan mode passed to run_sipm_cavity_scan.sh.")
    parser.add_argument("--events", type=int, required=True, help="Events per grid point.")
    parser.add_argument("--source-mode", default="gps", choices=["auto", "gun", "gps"])
    parser.add_argument(
        "--tank-size",
        action="append",
        required=True,
        help='Tank size string, e.g. "100 100 5 mm". Repeat for Week 6 sweeps.',
    )
    parser.add_argument("--x-min", type=decimal_arg, required=True)
    parser.add_argument("--x-max", type=decimal_arg, required=True)
    parser.add_argument("--y-min", type=decimal_arg, required=True)
    parser.add_argument("--y-max", type=decimal_arg, required=True)
    parser.add_argument("--step", type=decimal_arg, required=True)
    parser.add_argument("--grid-unit", default="mm", choices=["mm", "cm"])
    parser.add_argument(
        "--primary-energy",
        help='Optional primary energy override passed to each point, e.g. "0.5 MeV".',
    )
    parser.add_argument(
        "--surface-preset",
        action="append",
        choices=["polished", "ground", "wrapped"],
        help="Surface preset. Repeat for Week 7 sweeps.",
    )
    parser.add_argument("--dimple", action="store_true", help="Enable dimple scan arguments.")
    parser.add_argument("--dimple-radius", help="Dimple radius value.")
    parser.add_argument("--dimple-unit", default="mm", choices=["mm", "cm"])
    parser.add_argument("--dimple-sipm-mode", default="surface", choices=["surface", "opening"])
    parser.add_argument("--beam-z", help="Optional beam z value passed to each point.")
    parser.add_argument("--beam-sigma", help="Optional circular GPS beam sigma passed to each point.")
    parser.add_argument(
        "--electron-energy-mode",
        choices=["fixed", "sr90Beta", "sr90"],
        help="Optional electron energy mode override passed to each point.",
    )
    parser.add_argument(
        "--sipm-face",
        choices=["+X", "-X", "+Y", "-Y", "+Z", "-Z", "bottomCavity"],
        help="Optional SiPM face override passed to each point.",
    )
    parser.add_argument(
        "--sipm-local-position",
        help='Optional SiPM local position override, e.g. "0 0 0 cm".',
    )
    parser.add_argument("--description", help="Comment written at the top of the plan.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.events <= 0:
        raise SystemExit("--events must be greater than 0")
    if args.beam_sigma is not None and args.source_mode != "gps":
        raise SystemExit("--beam-sigma requires --source-mode gps.")

    xs = inclusive_axis(args.x_min, args.x_max, args.step)
    ys = inclusive_axis(args.y_min, args.y_max, args.step)
    surface_presets = args.surface_preset or [None]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Generated by hpc/osc/generate_scan_plan.py")
    if args.description:
        lines.append(f"# {args.description}")
    lines.append(f"# points_per_configuration={len(xs) * len(ys)}")
    lines.append(
        f"# configurations={len(args.tank_size) * len(surface_presets) * (1 if args.dimple else 1)}"
    )
    lines.append("")

    task_count = 0
    for tank_size, surface_preset, x, y in itertools.product(
        args.tank_size, surface_presets, xs, ys
    ):
        scan_args = build_scan_args(
            mode=args.mode,
            events=args.events,
            source_mode=args.source_mode,
            tank_size=tank_size,
            x=x,
            y=y,
            step=args.step,
            grid_unit=args.grid_unit,
            primary_energy=args.primary_energy,
            surface_preset=surface_preset,
            dimple=args.dimple,
            dimple_radius=args.dimple_radius,
            dimple_unit=args.dimple_unit,
            dimple_sipm_mode=args.dimple_sipm_mode,
            beam_z=args.beam_z,
            beam_sigma=args.beam_sigma,
            electron_energy_mode=args.electron_energy_mode,
            sipm_face=args.sipm_face,
            sipm_local_position=args.sipm_local_position,
        )
        lines.append(shell_line(scan_args))
        task_count += 1

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {task_count} scan tasks to {out_path}")
    print(f"Submit with: sbatch --array=1-{task_count} hpc/osc/submit_scan.sbatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
