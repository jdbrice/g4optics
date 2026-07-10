#!/usr/bin/env python3
"""Compare a lab response CSV against one merged simulation efficiency map."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LabRow:
    x: float
    y: float
    response: float
    uncertainty: float


@dataclass(frozen=True)
class SimRow:
    x: float
    y: float
    response: float


@dataclass(frozen=True)
class TileGeometry:
    x_mm: float
    y_mm: float
    thickness_mm: float


def finite(value: float) -> bool:
    return math.isfinite(value)


def parse_float(text: str, column: str) -> float:
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"invalid numeric value in {column}: {text!r}") from exc


def infer_tile_geometry(lab_csv: Path) -> TileGeometry:
    text = f"{lab_csv.parent.name}_{lab_csv.name}"
    tile_match = re.search(r"tile_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", text)
    grid_match = re.search(r"grid_(\d+(?:\.\d+)?)mm", text)
    if not tile_match or not grid_match:
        raise ValueError("could not infer tile size from lab CSV name; pass --tile-size-mm")
    return TileGeometry(
        x_mm=float(tile_match.group(1)),
        y_mm=float(tile_match.group(2)),
        thickness_mm=float(grid_match.group(1)),
    )


def parse_tile_size(value: str | None, lab_csv: Path) -> TileGeometry:
    if value is None:
        return infer_tile_geometry(lab_csv)
    parts = value.split()
    if len(parts) not in (2, 3):
        raise ValueError('--tile-size-mm must be "x y" or "x y thickness"')
    thickness = float("nan") if len(parts) == 2 else float(parts[2])
    return TileGeometry(x_mm=float(parts[0]), y_mm=float(parts[1]), thickness_mm=thickness)


def coord_key(x: float, y: float, precision: int) -> tuple[float, float]:
    return (round(x, precision), round(y, precision))


def combine_uncertainty(response: float, csv_uncertainty: float, relative_uncertainty: float) -> float:
    csv_component = csv_uncertainty if finite(csv_uncertainty) and csv_uncertainty > 0 else 0.0
    relative_component = abs(response) * relative_uncertainty if finite(response) else 0.0
    total = math.sqrt(csv_component * csv_component + relative_component * relative_component)
    if total > 0:
        return total
    return csv_uncertainty


def read_lab_rows(
    path: Path,
    response_column: str,
    uncertainty_column: str,
    relative_uncertainty: float,
    x_offset: float,
    y_offset: float,
) -> list[LabRow]:
    rows: list[LabRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"missing CSV header: {path}")
        required = {"X", "Y", response_column, uncertainty_column}
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            response = parse_float(row[response_column], response_column)
            csv_uncertainty = parse_float(row[uncertainty_column], uncertainty_column)
            rows.append(
                LabRow(
                    x=parse_float(row["X"], "X") + x_offset,
                    y=parse_float(row["Y"], "Y") + y_offset,
                    response=response,
                    uncertainty=combine_uncertainty(response, csv_uncertainty, relative_uncertainty),
                )
            )
    if not rows:
        raise ValueError(f"no lab rows found in {path}")
    return rows


def sim_response(row: dict[str, str], mode: str) -> float:
    if mode == "detected-per-event":
        detected = parse_float(row["sipm_detected_photons"], "sipm_detected_photons")
        events = parse_float(row["events"], "events")
        return detected / events if events else float("nan")
    if mode == "collection-efficiency":
        return parse_float(row["collection_efficiency"], "collection_efficiency")
    if mode == "sipm-detected-photons":
        return parse_float(row["sipm_detected_photons"], "sipm_detected_photons")
    raise ValueError(f"unknown sim response mode: {mode}")


def read_sim_rows(path: Path, response_mode: str) -> list[SimRow]:
    rows: list[SimRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"missing CSV header: {path}")
        required = {"x", "y"}
        if response_mode == "detected-per-event":
            required.update({"sipm_detected_photons", "events"})
        elif response_mode == "collection-efficiency":
            required.add("collection_efficiency")
        elif response_mode == "sipm-detected-photons":
            required.add("sipm_detected_photons")
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
        for row in reader:
            rows.append(
                SimRow(
                    x=parse_float(row["x"], "x"),
                    y=parse_float(row["y"], "y"),
                    response=sim_response(row, response_mode),
                )
            )
    if not rows:
        raise ValueError(f"no simulation rows found in {path}")
    return rows


def infer_divergence_mrad(sim_csv: Path) -> float | None:
    run_config = sim_csv.parent / "run_config.json"
    if not run_config.is_file():
        return None
    try:
        with run_config.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    beam = data.get("beam")
    if not isinstance(beam, dict):
        return None
    value = beam.get("divergence_mrad")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_inside_tile(x: float, y: float, geometry: TileGeometry) -> bool:
    return abs(x) <= 0.5 * geometry.x_mm and abs(y) <= 0.5 * geometry.y_mm


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    return cov / denom if denom else float("nan")


def best_scale(lab: list[float], sim: list[float], unc: list[float]) -> float:
    numerator = 0.0
    denominator = 0.0
    for y_lab, y_sim, sigma in zip(lab, sim, unc):
        if not finite(y_lab) or not finite(y_sim):
            continue
        weight = 1.0 / (sigma * sigma) if finite(sigma) and sigma > 0 else 1.0
        numerator += weight * y_sim * y_lab
        denominator += weight * y_sim * y_sim
    return numerator / denominator if denominator else float("nan")


def normalized_rmse(lab: list[float], sim: list[float]) -> float:
    if not lab or not sim:
        return float("nan")
    lab_scale = max(abs(v) for v in lab if finite(v))
    sim_scale = max(abs(v) for v in sim if finite(v))
    if lab_scale == 0 or sim_scale == 0:
        return float("nan")
    residuals = [(a / lab_scale - b / sim_scale) ** 2 for a, b in zip(lab, sim)]
    return math.sqrt(sum(residuals) / len(residuals))


def summarize_region(
    name: str,
    matched: list[tuple[LabRow, SimRow]],
    geometry: TileGeometry,
) -> dict[str, object]:
    if name == "inside":
        selected = [(lab, sim) for lab, sim in matched if is_inside_tile(lab.x, lab.y, geometry)]
    elif name == "outside":
        selected = [(lab, sim) for lab, sim in matched if not is_inside_tile(lab.x, lab.y, geometry)]
    else:
        selected = matched

    lab_values = [lab.response for lab, _ in selected]
    sim_values = [sim.response for _, sim in selected]
    uncertainties = [lab.uncertainty for lab, _ in selected]
    scale = best_scale(lab_values, sim_values, uncertainties)

    chi2 = 0.0
    chi2_terms = 0
    for y_lab, y_sim, sigma in zip(lab_values, sim_values, uncertainties):
        if not finite(y_lab) or not finite(y_sim) or not finite(scale):
            continue
        denom = sigma if finite(sigma) and sigma > 0 else 1.0
        chi2 += ((y_lab - scale * y_sim) / denom) ** 2
        chi2_terms += 1

    dof = max(chi2_terms - 1, 0)
    return {
        "region": name,
        "matched_points": len(selected),
        "scale_lab_per_sim": scale,
        "chi2": chi2 if chi2_terms else float("nan"),
        "ndf": dof,
        "chi2_ndf": chi2 / dof if dof > 0 else float("nan"),
        "normalized_rmse": normalized_rmse(lab_values, sim_values),
        "pearson_r": pearson(lab_values, sim_values),
        "mean_lab_response": sum(lab_values) / len(lab_values) if lab_values else float("nan"),
        "mean_sim_response": sum(sim_values) / len(sim_values) if sim_values else float("nan"),
    }


def format_value(value: object) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return f"{value:.8g}"
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-csv", required=True)
    parser.add_argument("--sim-csv", required=True)
    parser.add_argument("--out", help="Optional summary CSV path.")
    parser.add_argument(
        "--sim-response",
        default="detected-per-event",
        choices=["detected-per-event", "collection-efficiency", "sipm-detected-photons"],
    )
    parser.add_argument("--lab-response-column", default="Dark_Current_Subtracted_Integral")
    parser.add_argument("--lab-uncertainty-column", default="Uncertainty")
    parser.add_argument(
        "--lab-relative-uncertainty",
        type=float,
        default=0.10,
        help="Additional pointwise relative lab/model uncertainty added in quadrature.",
    )
    parser.add_argument("--tile-size-mm", help='Override inferred tile size, e.g. "50 50 16".')
    parser.add_argument("--x-offset", type=float, default=0.0)
    parser.add_argument("--y-offset", type=float, default=0.0)
    parser.add_argument("--coordinate-precision", type=int, default=6)
    parser.add_argument("--divergence-mrad", type=float)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.lab_relative_uncertainty < 0:
        raise SystemExit("--lab-relative-uncertainty must be non-negative")
    lab_csv = Path(args.lab_csv)
    sim_csv = Path(args.sim_csv)
    geometry = parse_tile_size(args.tile_size_mm, lab_csv)

    lab_rows = read_lab_rows(
        lab_csv,
        args.lab_response_column,
        args.lab_uncertainty_column,
        args.lab_relative_uncertainty,
        args.x_offset,
        args.y_offset,
    )
    sim_rows = read_sim_rows(sim_csv, args.sim_response)
    sim_by_coord = {
        coord_key(row.x, row.y, args.coordinate_precision): row for row in sim_rows
    }

    matched: list[tuple[LabRow, SimRow]] = []
    missing: list[LabRow] = []
    for lab in lab_rows:
        sim = sim_by_coord.get(coord_key(lab.x, lab.y, args.coordinate_precision))
        if sim is None:
            missing.append(lab)
        else:
            matched.append((lab, sim))

    divergence = args.divergence_mrad
    if divergence is None:
        divergence = infer_divergence_mrad(sim_csv)

    summaries = [summarize_region(region, matched, geometry) for region in ("inside", "outside", "all")]
    for row in summaries:
        row["lab_csv"] = str(lab_csv)
        row["sim_csv"] = str(sim_csv)
        row["sim_response"] = args.sim_response
        row["lab_uncertainty_column"] = args.lab_uncertainty_column
        row["lab_relative_uncertainty"] = args.lab_relative_uncertainty
        row["lab_uncertainty_model"] = "csv_plus_relative"
        row["divergence_mrad"] = divergence if divergence is not None else float("nan")
        row["missing_points"] = len(missing)

    fieldnames = [
        "lab_csv",
        "sim_csv",
        "sim_response",
        "lab_uncertainty_column",
        "lab_relative_uncertainty",
        "lab_uncertainty_model",
        "divergence_mrad",
        "region",
        "matched_points",
        "missing_points",
        "scale_lab_per_sim",
        "chi2",
        "ndf",
        "chi2_ndf",
        "normalized_rmse",
        "pearson_r",
        "mean_lab_response",
        "mean_sim_response",
    ]

    print(f"Matched {len(matched)} of {len(lab_rows)} lab points against {sim_csv}")
    if missing:
        print("Missing coordinates:")
        for row in missing[:10]:
            print(f"  x={row.x:g}, y={row.y:g}")
        if len(missing) > 10:
            print(f"  ... {len(missing) - 10} more")

    for row in summaries:
        print(
            "{region}: n={matched_points}, chi2/ndf={chi2_ndf}, "
            "norm_rmse={normalized_rmse}, r={pearson_r}, scale={scale_lab_per_sim}".format(
                **{key: format_value(value) for key, value in row.items()}
            )
        )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows({key: row[key] for key in fieldnames} for row in summaries)
        print(f"Wrote summary CSV: {out_path}")

    return 0 if matched else 1


if __name__ == "__main__":
    raise SystemExit(main())
