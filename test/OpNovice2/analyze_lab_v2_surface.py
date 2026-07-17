#!/usr/bin/env python3
"""Score lab v2 painted-surface hypotheses at fixed divergence."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from analyze_lab_match import best_scale, finite, read_lab_rows, read_sim_rows
from analyze_lab_v2_divergence import (
    SAMPLES,
    MatchPoint,
    fit_metrics,
    match_sample,
    number_token,
    resolve_path,
    select_region,
    write_csv,
)
DEFAULT_SURFACES = (
    "polishedfrontpainted",
    "groundfrontpainted",
    "polishedbackpainted",
    "groundbackpainted",
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(project_root))
    parser.add_argument(
        "--baseline-scan-dir",
        default="test/OpNovice2/scan_runs/lab_v2_realsetup/divergence_validation",
        help="Directory containing the polishedfrontpainted baseline.",
    )
    parser.add_argument(
        "--surface-scan-dir",
        default=(
            "test/OpNovice2/scan_runs/lab_v2_realsetup/"
            "surface_comparison_75mrad_5000events"
        ),
        help="Directory containing the other surface hypotheses.",
    )
    parser.add_argument(
        "--out-dir",
        default="test/OpNovice2/lab_run_v2/surface_analysis",
    )
    parser.add_argument("--divergence-mrad", type=float, default=75.0)
    parser.add_argument("--events-per-point", type=int, default=5000)
    parser.add_argument("--lab-relative-uncertainty", type=float, default=0.10)
    parser.add_argument(
        "--sample-set",
        choices=("all", "5x5"),
        default="all",
        help="Tile samples included in the equal-weight surface metrics.",
    )
    parser.add_argument(
        "--surface-preset",
        action="append",
        default=[],
        choices=DEFAULT_SURFACES,
        help="Surface to include. Repeat; defaults to all four hypotheses.",
    )
    return parser.parse_args()


def simulation_csv(
    baseline_dir: Path,
    surface_dir: Path,
    sample_id: str,
    surface: str,
    divergence: float,
    events_per_point: int,
) -> Path:
    token = number_token(divergence)
    run_name = (
        f"lab_v2_{sample_id}_{surface}_sigma5mm_"
        f"{token}mrad_{events_per_point}events"
    )
    root = baseline_dir if surface == "polishedfrontpainted" else surface_dir
    return root / run_name / "efficiency_map.csv"


def display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def global_fit_metrics(
    surface: str,
    divergence: float,
    sample_points: dict[str, list[MatchPoint]],
    sample_metrics: list[dict[str, object]],
    region: str,
) -> dict[str, object]:
    selected_by_sample = {
        sample_id: select_region(points, region)
        for sample_id, points in sample_points.items()
    }
    selected = [point for points in selected_by_sample.values() for point in points]
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
        if row["surface_preset"] == surface and row["region"] == region
    ]
    finite_chi2 = [
        float(row["chi2_ndf"])
        for row in relevant
        if finite(float(row["chi2_ndf"]))
    ]
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
        "surface_preset": surface,
        "divergence_mrad": divergence,
        "region": region,
        "matched_points": len(selected),
        "global_scale_lab_per_sim": scale,
        "global_chi2": chi2,
        "global_ndf": ndf,
        "global_chi2_ndf": chi2 / ndf if ndf else math.nan,
        "global_scaled_normalized_rmse": (
            math.sqrt(
                sum(value * value for value in normalized_residuals)
                / len(normalized_residuals)
            )
            if normalized_residuals
            else math.nan
        ),
        "mean_sample_chi2_ndf": (
            sum(finite_chi2) / len(finite_chi2) if finite_chi2 else math.nan
        ),
        "mean_sample_normalized_rmse": (
            sum(finite_rmse) / len(finite_rmse) if finite_rmse else math.nan
        ),
        "mean_sample_scaled_normalized_rmse": (
            sum(finite_scaled_rmse) / len(finite_scaled_rmse)
            if finite_scaled_rmse
            else math.nan
        ),
        "mean_sample_pearson_r": (
            sum(finite_pearson) / len(finite_pearson)
            if finite_pearson
            else math.nan
        ),
    }


def best_surface(
    rows: list[dict[str, object]], region: str, metric: str
) -> str:
    candidates = [
        row
        for row in rows
        if row["region"] == region and finite(float(row[metric]))
    ]
    if not candidates:
        return ""
    return str(min(candidates, key=lambda row: float(row[metric]))["surface_preset"])


def main() -> int:
    args = parse_args()
    if args.divergence_mrad < 0:
        raise SystemExit("--divergence-mrad must be non-negative")
    if args.events_per_point <= 0:
        raise SystemExit("--events-per-point must be positive")
    if args.lab_relative_uncertainty < 0:
        raise SystemExit("--lab-relative-uncertainty must be non-negative")

    project_root = Path(args.project_root).resolve()
    baseline_dir = resolve_path(project_root, args.baseline_scan_dir)
    surface_dir = resolve_path(project_root, args.surface_scan_dir)
    out_dir = resolve_path(project_root, args.out_dir)
    surfaces = tuple(args.surface_preset or DEFAULT_SURFACES)
    samples = (
        tuple(sample for sample in SAMPLES if sample.sample_id.startswith("5x5"))
        if args.sample_set == "5x5"
        else SAMPLES
    )
    divergence_tag = f"{number_token(args.divergence_mrad)}mrad"
    if len(set(surfaces)) != len(surfaces):
        raise SystemExit("--surface-preset values must be unique")

    sample_metrics: list[dict[str, object]] = []
    global_metrics: list[dict[str, object]] = []
    profile_rows: list[dict[str, object]] = []
    all_points: dict[str, dict[str, list[MatchPoint]]] = {}

    for surface in surfaces:
        points_for_surface: dict[str, list[MatchPoint]] = {}
        for sample in samples:
            lab_path = resolve_path(project_root, sample.lab_csv)
            sim_path = simulation_csv(
                baseline_dir,
                surface_dir,
                sample.sample_id,
                surface,
                args.divergence_mrad,
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
            points = match_sample(
                sample,
                args.divergence_mrad,
                lab_rows,
                sim_rows,
            )
            points_for_surface[sample.sample_id] = points

            metrics_by_region: dict[str, dict[str, object]] = {}
            for region in ("all", "inside", "outside"):
                metrics = fit_metrics(points, region)
                metrics.update(
                    {
                        "sample_id": sample.sample_id,
                        "sample_title": sample.title,
                        "surface_preset": surface,
                        "divergence_mrad": args.divergence_mrad,
                        "lab_csv": display_path(lab_path, project_root),
                        "sim_csv": display_path(sim_path, project_root),
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
                        "surface_preset": surface,
                        "divergence_mrad": args.divergence_mrad,
                        "point_index": point.point_index,
                        "x_mm": point.lab.x,
                        "y_mm": point.lab.y,
                        "inside_tile": int(point.inside_tile),
                        "lab_response": point.lab.response,
                        "lab_uncertainty": point.lab.uncertainty,
                        "sim_detected_per_event": point.sim.response,
                        "sample_scale_all": all_metrics["scale_lab_per_sim"],
                        "scaled_sim_sample": (
                            float(all_metrics["scale_lab_per_sim"])
                            * point.sim.response
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
        all_points[surface] = points_for_surface

    for surface in surfaces:
        for region in ("all", "inside", "outside"):
            global_metrics.append(
                global_fit_metrics(
                    surface,
                    args.divergence_mrad,
                    all_points[surface],
                    sample_metrics,
                    region,
                )
            )

    global_scale = {
        (str(row["surface_preset"]), str(row["region"])): float(
            row["global_scale_lab_per_sim"]
        )
        for row in global_metrics
    }
    for row in profile_rows:
        scale = global_scale[(str(row["surface_preset"]), "all")]
        row["global_scale_all"] = scale
        row["scaled_sim_global"] = scale * float(row["sim_detected_per_event"])

    sample_fields = [
        "sample_id",
        "sample_title",
        "surface_preset",
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
        "surface_preset",
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
        "surface_preset",
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
    write_csv(
        out_dir / f"surface_sample_metrics_{divergence_tag}.csv",
        sample_fields,
        sample_metrics,
    )
    write_csv(
        out_dir / f"surface_global_metrics_{divergence_tag}.csv",
        global_fields,
        global_metrics,
    )
    write_csv(
        out_dir / f"surface_profiles_{divergence_tag}.csv",
        profile_fields,
        profile_rows,
    )

    picks = {
        "beam_sigma_mm": 5.0,
        "divergence_mrad": args.divergence_mrad,
        "output_tag": divergence_tag,
        "events_per_point": args.events_per_point,
        "sample_set": args.sample_set,
        "sample_ids": [sample.sample_id for sample in samples],
        "baseline_scan_dir": display_path(baseline_dir, project_root),
        "surface_scan_dir": display_path(surface_dir, project_root),
        "surface_candidates": list(surfaces),
        "lab_relative_uncertainty": args.lab_relative_uncertainty,
        "outside_points_included_in_all": True,
        "primary_metric": "all_mean_sample_scaled_normalized_rmse",
        "best_all_mean_sample_chi2_ndf": best_surface(
            global_metrics, "all", "mean_sample_chi2_ndf"
        ),
        "best_all_mean_sample_normalized_rmse": best_surface(
            global_metrics, "all", "mean_sample_normalized_rmse"
        ),
        "best_all_mean_sample_scaled_normalized_rmse": best_surface(
            global_metrics, "all", "mean_sample_scaled_normalized_rmse"
        ),
        "best_all_global_chi2_ndf": best_surface(
            global_metrics, "all", "global_chi2_ndf"
        ),
        "best_inside_mean_sample_chi2_ndf": best_surface(
            global_metrics, "inside", "mean_sample_chi2_ndf"
        ),
        "best_inside_mean_sample_normalized_rmse": best_surface(
            global_metrics, "inside", "mean_sample_normalized_rmse"
        ),
        "best_inside_mean_sample_scaled_normalized_rmse": best_surface(
            global_metrics, "inside", "mean_sample_scaled_normalized_rmse"
        ),
        "best_inside_global_chi2_ndf": best_surface(
            global_metrics, "inside", "global_chi2_ndf"
        ),
        "caveat": (
            "Painted finishes are Geant4 boundary hypotheses, not explicit paint "
            "volumes. The primary metric averages per-tile scaled normalized RMSE "
            "with equal tile weight and includes outside-tile points. Simulation "
            "statistical uncertainty is not included."
        ),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"best_surface_{divergence_tag}.json").open(
        "w", encoding="utf-8"
    ) as handle:
        json.dump(picks, handle, indent=2, sort_keys=True)
        handle.write("\n")

    print(
        f"Analyzed {len(samples)} samples x {len(surfaces)} surfaces = "
        f"{len(samples) * len(surfaces)} maps."
    )
    print(f"Wrote analysis outputs to {out_dir}")
    print(json.dumps(picks, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
