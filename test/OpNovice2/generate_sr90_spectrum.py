#!/usr/bin/env python3
"""Generate the Week 10 Sr-90/Y-90 beta spectrum table and GPS fragment."""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path


ELECTRON_MASS_MEV = 0.51099895
ALPHA = 1.0 / 137.035999084


@dataclass(frozen=True)
class Branch:
    name: str
    endpoint_mev: float
    daughter_z: int
    activity_weight: float


BRANCHES = (
    Branch("Sr90_to_Y90", 0.546, 39, 1.0),
    Branch("Y90_to_Zr90", 2.28, 40, 1.0),
)


def fermi_function_beta_minus(daughter_z: int, kinetic_mev: float) -> float:
    total_mev = kinetic_mev + ELECTRON_MASS_MEV
    w = total_mev / ELECTRON_MASS_MEV
    p = math.sqrt(max(w * w - 1.0, 0.0))
    if p <= 0.0:
        return 1.0
    eta = ALPHA * daughter_z * w / p
    x = 2.0 * math.pi * eta
    if x > 80.0:
        return x
    return x / (1.0 - math.exp(-x))


def allowed_beta_density(branch: Branch, kinetic_mev: float, use_fermi: bool) -> float:
    if kinetic_mev <= 0.0 or kinetic_mev >= branch.endpoint_mev:
        return 0.0
    total_mev = kinetic_mev + ELECTRON_MASS_MEV
    momentum_mev = math.sqrt(max(total_mev * total_mev - ELECTRON_MASS_MEV * ELECTRON_MASS_MEV, 0.0))
    density = momentum_mev * total_mev * (branch.endpoint_mev - kinetic_mev) ** 2
    if use_fermi:
        density *= fermi_function_beta_minus(branch.daughter_z, kinetic_mev)
    return density


def trapezoid_area(xs: list[float], ys: list[float]) -> float:
    return sum(0.5 * (ys[i] + ys[i + 1]) * (xs[i + 1] - xs[i]) for i in range(len(xs) - 1))


def build_rows(bin_width_mev: float, use_fermi: bool) -> list[dict[str, float]]:
    max_endpoint = max(branch.endpoint_mev for branch in BRANCHES)
    n_steps = int(round(max_endpoint / bin_width_mev))
    energies = [i * bin_width_mev for i in range(n_steps + 1)]
    if energies[-1] < max_endpoint:
        energies.append(max_endpoint)
    else:
        energies[-1] = max_endpoint

    branch_pdfs: dict[str, list[float]] = {}
    for branch in BRANCHES:
        raw = [allowed_beta_density(branch, energy, use_fermi) for energy in energies]
        area = trapezoid_area(energies, raw)
        branch_pdfs[branch.name] = [value / area if area > 0.0 else 0.0 for value in raw]

    total_activity = sum(branch.activity_weight for branch in BRANCHES)
    combined_raw = []
    for i, _energy in enumerate(energies):
        combined_raw.append(
            sum(branch.activity_weight * branch_pdfs[branch.name][i] for branch in BRANCHES)
            / total_activity
        )
    max_combined = max(combined_raw)
    combined_relative = [value / max_combined if max_combined > 0.0 else 0.0 for value in combined_raw]

    rows = []
    for i, energy in enumerate(energies):
        rows.append(
            {
                "energy_mev": energy,
                "sr90_pdf": branch_pdfs["Sr90_to_Y90"][i],
                "y90_pdf": branch_pdfs["Y90_to_Zr90"][i],
                "combined_pdf": combined_raw[i],
                "combined_relative": combined_relative[i],
            }
        )
    return rows


def write_csv(rows: list[dict[str, float]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "energy_mev",
                "sr90_pdf",
                "y90_pdf",
                "combined_pdf",
                "combined_relative",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: f"{value:.12g}" for key, value in row.items()})


def write_gps_fragment(rows: list[dict[str, float]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        handle.write("# Sr-90/Y-90 allowed beta spectrum, model sr90_allowed_beta_v1\n")
        handle.write("# Energies are kinetic energy in MeV; weights are relative combined PDF.\n")
        handle.write("/gps/ene/type Arb\n")
        handle.write("/gps/hist/type arb\n")
        for row in rows:
            handle.write(f"/gps/hist/point {row['energy_mev']:.12g} {row['combined_relative']:.12g}\n")
        handle.write("/gps/hist/inter Lin\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("spectra/sr90_allowed_beta_v1.csv"),
        help="Output CSV spectrum table.",
    )
    parser.add_argument(
        "--gps-macro",
        type=Path,
        default=Path("spectra/sr90_allowed_beta_v1_gps.mac"),
        help="Output GPS histogram macro fragment.",
    )
    parser.add_argument("--bin-width-mev", type=float, default=0.01)
    parser.add_argument("--no-fermi", action="store_true", help="Disable point-charge Fermi correction.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.bin_width_mev <= 0.0:
        raise SystemExit("--bin-width-mev must be positive")
    rows = build_rows(args.bin_width_mev, use_fermi=not args.no_fermi)
    write_csv(rows, args.csv)
    write_gps_fragment(rows, args.gps_macro)
    print(f"Wrote {len(rows)} spectrum rows to {args.csv}")
    print(f"Wrote GPS histogram fragment to {args.gps_macro}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
