#!/usr/bin/env python3
"""Score lab v2 divergence candidates across a selected set of tile samples."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

from analyze_lab_match import (
    LabRow,
    SimRow,
    TileGeometry,
    best_scale,
    coord_key,
    finite,
    is_inside_tile,
    normalized_rmse,
    pearson,
    read_lab_rows,
    read_sim_rows,
)


DEFAULT_DIVERGENCES = (25.0, 35.0, 45.0, 55.0, 65.0, 75.0)
DEFAULT_SCAN_DIR = (
    "test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_calibration"
)


@dataclass(frozen=True)
class SampleSpec:
    sample_id: str
    title: str
    lab_csv: str
    geometry: TileGeometry


@dataclass(frozen=True)
class MatchPoint:
    sample_id: str
    divergence_mrad: float
    point_index: int
    lab: LabRow
    sim: SimRow
    inside_tile: bool


SAMPLES = (
    SampleSpec(
        "11p5x11p5x16",
        "11.5 x 11.5 x 1.6 cm",
        "test/OpNovice2/lab_data/10x10x1.6_painted_undimpled_opticalGrease/"
        "responses_tile_100.000000x100.000000_pattern_eighth_grid_16mm_"
        "painted_undimpled_optical_grease_RERUN_5_SC_1781802404.csv",
        TileGeometry(115.0, 115.0, 16.0),
    ),
    SampleSpec(
        "11p5x11p5x4",
        "11.5 x 11.5 x 0.4 cm",
        "test/OpNovice2/lab_data/10x10x0.4_painted_undimpled_opticalGrease/"
        "responses_tile_100.000000x100.000000_pattern_eighth_grid_4mm_"
        "painted_undimpled_optical_grease_RERUN_2_JW_1781188758.csv",
        TileGeometry(115.0, 115.0, 4.0),
    ),
    SampleSpec(
        "5x5x16",
        "5 x 5 x 1.6 cm",
        "test/OpNovice2/lab_data/5x5x1.6_painted_undimpled_opticalGrease/"
        "responses_tile_50.000000x50.000000_pattern_eighth_grid_16mm_"
        "painted_optical_grease_tile_OM_1776182096.csv",
        TileGeometry(50.0, 50.0, 16.0),
    ),
    SampleSpec(
        "5x5x4",
        "5 x 5 x 0.4 cm",
        "test/OpNovice2/lab_data/5x5x0.4_painted_undimpled_opticalGrease/"
        "responses_tile_50.000000x50.000000_pattern_eighth_grid_4mm_no_dimple_"
        "painted_optical_grease_JW_1776360791.csv",
        TileGeometry(50.0, 50.0, 4.0),
    ),
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(project_root))
    parser.add_argument(
        "--scan-dir",
        action="append",
        default=[],
        help=(
            "Directory containing simulation maps. Repeat to combine scan stages; "
            "each requested sample/divergence must resolve to exactly one map."
        ),
    )
    parser.add_argument(
        "--out-dir",
        default="test/OpNovice2/lab_run_v2/analysis",
    )
    parser.add_argument(
        "--lab-relative-uncertainty",
        type=float,
        default=0.10,
    )
    parser.add_argument(
        "--events-per-point",
        type=int,
        default=500,
        help="Events in each simulation point; used to resolve run directory names.",
    )
    parser.add_argument(
        "--divergence-mrad",
        type=float,
        action="append",
        default=[],
        help="Divergence candidate. Repeat; defaults to 25,35,...,75 mrad.",
    )
    parser.add_argument(
        "--sample-set",
        choices=("all", "5x5"),
        default="all",
        help="Tile samples included in the equal-weight metrics.",
    )
    return parser.parse_args()


def resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_root / path


def number_token(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}".replace(".", "p")


def simulation_csv(
    scan_dirs: tuple[Path, ...],
    sample: SampleSpec,
    divergence: float,
    events_per_point: int,
) -> Path:
    token = number_token(divergence)
    run_name = (
        f"lab_v2_{sample.sample_id}_polishedfrontpainted_sigma5mm_"
        f"{token}mrad_{events_per_point}events"
    )
    candidates = [scan_dir / run_name / "efficiency_map.csv" for scan_dir in scan_dirs]
    matches = [candidate for candidate in candidates if candidate.is_file()]
    if not matches:
        searched = "\n  ".join(str(candidate) for candidate in candidates)
        raise FileNotFoundError(
            f"missing simulation map for {sample.sample_id} at {divergence:g} mrad; "
            f"searched:\n  {searched}"
        )
    if len(matches) > 1:
        found = "\n  ".join(str(match) for match in matches)
        raise ValueError(
            f"ambiguous simulation map for {sample.sample_id} at {divergence:g} mrad; "
            f"found:\n  {found}"
        )
    return matches[0]


def match_sample(
    sample: SampleSpec,
    divergence: float,
    lab_rows: list[LabRow],
    sim_rows: list[SimRow],
) -> list[MatchPoint]:
    sim_by_coord = {coord_key(row.x, row.y, 6): row for row in sim_rows}
    points: list[MatchPoint] = []
    missing: list[tuple[float, float]] = []
    for index, lab in enumerate(lab_rows, start=1):
        sim = sim_by_coord.get(coord_key(lab.x, lab.y, 6))
        if sim is None:
            missing.append((lab.x, lab.y))
            continue
        points.append(
            MatchPoint(
                sample_id=sample.sample_id,
                divergence_mrad=divergence,
                point_index=index,
                lab=lab,
                sim=sim,
                inside_tile=is_inside_tile(lab.x, lab.y, sample.geometry),
            )
        )
    if missing:
        raise ValueError(
            f"{sample.sample_id} at {divergence:g} mrad is missing coordinates: {missing}"
        )
    if len(points) != len(sim_rows):
        raise ValueError(
            f"{sample.sample_id} at {divergence:g} mrad has {len(points)} matched "
            f"lab rows but {len(sim_rows)} simulation rows"
        )
    return points


def select_region(points: list[MatchPoint], region: str) -> list[MatchPoint]:
    if region == "inside":
        return [point for point in points if point.inside_tile]
    if region == "outside":
        return [point for point in points if not point.inside_tile]
    if region == "all":
        return list(points)
    raise ValueError(f"unknown region: {region}")


def fit_metrics(points: list[MatchPoint], region: str) -> dict[str, float | int | str]:
    selected = select_region(points, region)
    lab = [point.lab.response for point in selected]
    sim = [point.sim.response for point in selected]
    unc = [point.lab.uncertainty for point in selected]
    scale = best_scale(lab, sim, unc)
    chi2 = sum(
        ((y_lab - scale * y_sim) / sigma) ** 2
        for y_lab, y_sim, sigma in zip(lab, sim, unc)
        if finite(y_lab)
        and finite(y_sim)
        and finite(scale)
        and finite(sigma)
        and sigma > 0
    )
    ndf = max(len(selected) - 1, 0)
    lab_max = max((abs(value) for value in lab if finite(value)), default=0.0)
    scaled_normalized_rmse = (
        math.sqrt(
            sum((y_lab - scale * y_sim) ** 2 for y_lab, y_sim in zip(lab, sim))
            / len(selected)
        )
        / lab_max
        if selected and lab_max > 0 and finite(scale)
        else math.nan
    )
    return {
        "region": region,
        "matched_points": len(selected),
        "scale_lab_per_sim": scale,
        "chi2": chi2,
        "ndf": ndf,
        "chi2_ndf": chi2 / ndf if ndf else math.nan,
        "normalized_rmse": normalized_rmse(lab, sim),
        "scaled_normalized_rmse": scaled_normalized_rmse,
        "pearson_r": pearson(lab, sim),
    }


def global_fit_metrics(
    divergence: float,
    sample_points: dict[str, list[MatchPoint]],
    sample_metrics: list[dict[str, object]],
    region: str,
) -> dict[str, object]:
    selected_by_sample = {
        sample_id: select_region(points, region)
        for sample_id, points in sample_points.items()
    }
    selected = [
        point
        for points in selected_by_sample.values()
        for point in points
    ]
    lab = [point.lab.response for point in selected]
    sim = [point.sim.response for point in selected]
    unc = [point.lab.uncertainty for point in selected]
    scale = best_scale(lab, sim, unc)
    chi2 = sum(
        ((y_lab - scale * y_sim) / sigma) ** 2
        for y_lab, y_sim, sigma in zip(lab, sim, unc)
        if finite(y_lab)
        and finite(y_sim)
        and finite(scale)
        and finite(sigma)
        and sigma > 0
    )
    ndf = max(len(selected) - 1, 0)

    normalized_residuals: list[float] = []
    for points in selected_by_sample.values():
        lab_max = max((abs(point.lab.response) for point in points), default=0.0)
        if lab_max <= 0:
            continue
        normalized_residuals.extend(
            (point.lab.response - scale * point.sim.response) / lab_max
            for point in points
        )

    relevant = [
        row
        for row in sample_metrics
        if row["divergence_mrad"] == divergence and row["region"] == region
    ]
    finite_chi2 = [float(row["chi2_ndf"]) for row in relevant if finite(float(row["chi2_ndf"]))]
    finite_rmse = [
        float(row["normalized_rmse"])
        for row in relevant
        if finite(float(row["normalized_rmse"]))
    ]
    finite_scaled_rmse = [
        float(row["scaled_normalized_rmse"])
        for row in relevant
        if finite(float(row["scaled_normalized_rmse"]))
    ]
    finite_pearson = [
        float(row["pearson_r"])
        for row in relevant
        if finite(float(row["pearson_r"]))
    ]
    return {
        "divergence_mrad": divergence,
        "region": region,
        "matched_points": len(selected),
        "global_scale_lab_per_sim": scale,
        "global_chi2": chi2,
        "global_ndf": ndf,
        "global_chi2_ndf": chi2 / ndf if ndf else math.nan,
        "global_scaled_normalized_rmse": (
            math.sqrt(sum(value * value for value in normalized_residuals) / len(normalized_residuals))
            if normalized_residuals
            else math.nan
        ),
        "mean_sample_chi2_ndf": sum(finite_chi2) / len(finite_chi2) if finite_chi2 else math.nan,
        "mean_sample_normalized_rmse": (
            sum(finite_rmse) / len(finite_rmse) if finite_rmse else math.nan
        ),
        "mean_sample_scaled_normalized_rmse": (
            sum(finite_scaled_rmse) / len(finite_scaled_rmse)
            if finite_scaled_rmse
            else math.nan
        ),
        "mean_sample_pearson_r": (
            sum(finite_pearson) / len(finite_pearson) if finite_pearson else math.nan
        ),
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def best_divergence(
    rows: list[dict[str, object]], region: str, metric: str
) -> float:
    candidates = [
        row
        for row in rows
        if row["region"] == region and finite(float(row[metric]))
    ]
    if not candidates:
        return math.nan
    return float(min(candidates, key=lambda row: float(row[metric]))["divergence_mrad"])


def main() -> int:
    args = parse_args()
    if args.lab_relative_uncertainty < 0:
        raise SystemExit("--lab-relative-uncertainty must be non-negative")
    if args.events_per_point <= 0:
        raise SystemExit("--events-per-point must be positive")
    project_root = Path(args.project_root).resolve()
    scan_dirs = tuple(
        resolve_path(project_root, value)
        for value in (args.scan_dir or [DEFAULT_SCAN_DIR])
    )
    out_dir = resolve_path(project_root, args.out_dir)
    divergences = tuple(args.divergence_mrad or DEFAULT_DIVERGENCES)
    samples = (
        tuple(sample for sample in SAMPLES if sample.sample_id.startswith("5x5"))
        if args.sample_set == "5x5"
        else SAMPLES
    )

    sample_metrics: list[dict[str, object]] = []
    global_metrics: list[dict[str, object]] = []
    profile_rows: list[dict[str, object]] = []
    all_points: dict[float, dict[str, list[MatchPoint]]] = {}

    for divergence in divergences:
        points_for_divergence: dict[str, list[MatchPoint]] = {}
        for sample in samples:
            lab_path = resolve_path(project_root, sample.lab_csv)
            sim_path = simulation_csv(
                scan_dirs,
                sample,
                divergence,
                args.events_per_point,
            )
            lab_rows = read_lab_rows(
                lab_path,
                "Dark_Current_Subtracted_Integral",
                "Uncertainty",
                args.lab_relative_uncertainty,
                0.0,
                0.0,
            )
            sim_rows = read_sim_rows(sim_path, "detected-per-event")
            points = match_sample(sample, divergence, lab_rows, sim_rows)
            points_for_divergence[sample.sample_id] = points

            metrics_by_region: dict[str, dict[str, object]] = {}
            for region in ("all", "inside", "outside"):
                metrics = fit_metrics(points, region)
                metrics.update(
                    {
                        "sample_id": sample.sample_id,
                        "sample_title": sample.title,
                        "divergence_mrad": divergence,
                        "lab_csv": str(lab_path.relative_to(project_root)),
                        "sim_csv": str(sim_path.relative_to(project_root)),
                        "lab_relative_uncertainty": args.lab_relative_uncertainty,
                        "lab_uncertainty_model": "csv_plus_relative",
                    }
                )
                metrics_by_region[region] = metrics
                sample_metrics.append(metrics)

            all_metrics = metrics_by_region["all"]
            inside_metrics = metrics_by_region["inside"]
            for point in points:
                profile_rows.append(
                    {
                        "sample_id": sample.sample_id,
                        "sample_title": sample.title,
                        "divergence_mrad": divergence,
                        "point_index": point.point_index,
                        "x_mm": point.lab.x,
                        "y_mm": point.lab.y,
                        "inside_tile": int(point.inside_tile),
                        "lab_response": point.lab.response,
                        "lab_uncertainty": point.lab.uncertainty,
                        "sim_detected_per_event": point.sim.response,
                        "sample_scale_all": all_metrics["scale_lab_per_sim"],
                        "scaled_sim_sample": (
                            float(all_metrics["scale_lab_per_sim"]) * point.sim.response
                        ),
                        "all_chi2_ndf": all_metrics["chi2_ndf"],
                        "all_normalized_rmse": all_metrics["normalized_rmse"],
                        "all_scaled_normalized_rmse": all_metrics[
                            "scaled_normalized_rmse"
                        ],
                        "inside_chi2_ndf": inside_metrics["chi2_ndf"],
                        "inside_normalized_rmse": inside_metrics["normalized_rmse"],
                        "inside_scaled_normalized_rmse": inside_metrics[
                            "scaled_normalized_rmse"
                        ],
                    }
                )
        all_points[divergence] = points_for_divergence

    for divergence in divergences:
        for region in ("all", "inside", "outside"):
            global_metrics.append(
                global_fit_metrics(
                    divergence,
                    all_points[divergence],
                    sample_metrics,
                    region,
                )
            )

    global_scale = {
        (float(row["divergence_mrad"]), str(row["region"])): float(
            row["global_scale_lab_per_sim"]
        )
        for row in global_metrics
    }
    for row in profile_rows:
        scale = global_scale[(float(row["divergence_mrad"]), "all")]
        row["global_scale_all"] = scale
        row["scaled_sim_global"] = scale * float(row["sim_detected_per_event"])

    sample_fields = [
        "sample_id",
        "sample_title",
        "divergence_mrad",
        "region",
        "matched_points",
        "scale_lab_per_sim",
        "chi2",
        "ndf",
        "chi2_ndf",
        "normalized_rmse",
        "scaled_normalized_rmse",
        "pearson_r",
        "lab_csv",
        "sim_csv",
        "lab_relative_uncertainty",
        "lab_uncertainty_model",
    ]
    global_fields = [
        "divergence_mrad",
        "region",
        "matched_points",
        "global_scale_lab_per_sim",
        "global_chi2",
        "global_ndf",
        "global_chi2_ndf",
        "global_scaled_normalized_rmse",
        "mean_sample_chi2_ndf",
        "mean_sample_normalized_rmse",
        "mean_sample_scaled_normalized_rmse",
        "mean_sample_pearson_r",
    ]
    profile_fields = [
        "sample_id",
        "sample_title",
        "divergence_mrad",
        "point_index",
        "x_mm",
        "y_mm",
        "inside_tile",
        "lab_response",
        "lab_uncertainty",
        "sim_detected_per_event",
        "sample_scale_all",
        "scaled_sim_sample",
        "global_scale_all",
        "scaled_sim_global",
        "all_chi2_ndf",
        "all_normalized_rmse",
        "all_scaled_normalized_rmse",
        "inside_chi2_ndf",
        "inside_normalized_rmse",
        "inside_scaled_normalized_rmse",
    ]
    write_csv(out_dir / "sample_metrics.csv", sample_fields, sample_metrics)
    write_csv(out_dir / "global_metrics.csv", global_fields, global_metrics)
    write_csv(out_dir / "profiles.csv", profile_fields, profile_rows)

    picks = {
        "surface_preset": "polishedfrontpainted",
        "beam_sigma_mm": 5.0,
        "events_per_point": args.events_per_point,
        "sample_set": args.sample_set,
        "sample_ids": [sample.sample_id for sample in samples],
        "scan_dirs": [
            str(path.relative_to(project_root))
            if path.is_relative_to(project_root)
            else str(path)
            for path in scan_dirs
        ],
        "lab_relative_uncertainty": args.lab_relative_uncertainty,
        "outside_points_included_in_all": True,
        "best_all_mean_sample_chi2_ndf_mrad": best_divergence(
            global_metrics, "all", "mean_sample_chi2_ndf"
        ),
        "best_all_mean_sample_normalized_rmse_mrad": best_divergence(
            global_metrics, "all", "mean_sample_normalized_rmse"
        ),
        "best_all_mean_sample_scaled_normalized_rmse_mrad": best_divergence(
            global_metrics, "all", "mean_sample_scaled_normalized_rmse"
        ),
        "best_all_global_chi2_ndf_mrad": best_divergence(
            global_metrics, "all", "global_chi2_ndf"
        ),
        "best_inside_mean_sample_chi2_ndf_mrad": best_divergence(
            global_metrics, "inside", "mean_sample_chi2_ndf"
        ),
        "best_inside_mean_sample_normalized_rmse_mrad": best_divergence(
            global_metrics, "inside", "mean_sample_normalized_rmse"
        ),
        "best_inside_mean_sample_scaled_normalized_rmse_mrad": best_divergence(
            global_metrics, "inside", "mean_sample_scaled_normalized_rmse"
        ),
        "best_inside_global_chi2_ndf_mrad": best_divergence(
            global_metrics, "inside", "global_chi2_ndf"
        ),
        "caveat": (
            "Shared-scale metrics test cross-sample yield consistency; per-sample metrics "
            "test spatial shape with one independently fitted scale per tile. "
            "scaled_normalized_rmse uses that uncertainty-weighted fitted scale, while "
            "normalized_rmse compares profiles normalized to their own maxima. Simulation "
            "statistical uncertainty is not included."
        ),
    }
    with (out_dir / "best_divergence.json").open("w", encoding="utf-8") as handle:
        json.dump(picks, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(
        f"Analyzed {len(samples)} samples x {len(divergences)} divergences = "
        f"{len(samples) * len(divergences)} maps."
    )
    print(f"Wrote analysis outputs to {out_dir}")
    print(json.dumps(picks, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
