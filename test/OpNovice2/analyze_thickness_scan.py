#!/usr/bin/env python3
"""Analyze Week 6 thickness scans from OpNovice2 efficiency maps."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Iterable

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "g4optics-matplotlib-cache"),
)
os.environ.setdefault(
    "XDG_CACHE_HOME",
    str(Path(tempfile.gettempdir()) / "g4optics-cache"),
)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {
    "x",
    "y",
    "unit",
    "events",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "collection_efficiency",
    "hit_position_events",
    "hit_z_mm",
    "scint_centroid_z_mm",
}

SUMMARY_COLUMNS = [
    "thickness_mm",
    "region",
    "run_id",
    "run_dir",
    "events_per_point",
    "n_points",
    "mean_collection_efficiency",
    "rms_collection_efficiency",
    "rms_over_mean_collection_efficiency",
    "mean_detected_photons_per_event",
    "mean_generated_photons_per_event",
    "mean_scintillation_photons_per_event",
    "zero_generated_points",
    "missing_hit_points",
    "mean_hit_z_mm",
    "mean_scint_centroid_z_mm",
]


def parse_run_spec(spec: str) -> tuple[float, Path]:
    if ":" not in spec:
        raise argparse.ArgumentTypeError(
            f"Invalid --run {spec!r}; expected THICKNESS_MM:RUN_DIR"
        )
    thickness_text, run_dir_text = spec.split(":", 1)
    try:
        thickness = float(thickness_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid thickness in --run {spec!r}: {thickness_text!r}"
        ) from exc
    if thickness <= 0:
        raise argparse.ArgumentTypeError("Thickness must be positive")
    run_dir = Path(run_dir_text)
    return thickness, run_dir


def unit_to_mm(values: pd.Series, units: pd.Series) -> pd.Series:
    unit_values = set(units.dropna().astype(str))
    unsupported = unit_values - {"mm", "cm"}
    if unsupported:
        raise ValueError(f"Unsupported scan coordinate units: {sorted(unsupported)}")
    factors = units.map({"mm": 1.0, "cm": 10.0})
    return values.astype(float) * factors.astype(float)


def load_run(thickness_mm: float, run_dir: Path) -> tuple[pd.DataFrame, dict]:
    efficiency_map = run_dir / "efficiency_map.csv"
    run_config = run_dir / "run_config.json"
    if not efficiency_map.is_file():
        raise FileNotFoundError(f"Missing efficiency map: {efficiency_map}")
    if not run_config.is_file():
        raise FileNotFoundError(f"Missing run config: {run_config}")

    df = pd.read_csv(efficiency_map)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"{efficiency_map} is missing required columns: {sorted(missing)}"
        )

    with run_config.open(encoding="utf-8") as fh:
        config = json.load(fh)

    numeric_columns = [
        "x",
        "y",
        "events",
        "generated_optical_photons",
        "scintillation_photons",
        "sipm_detected_photons",
        "collection_efficiency",
        "hit_position_events",
        "hit_z_mm",
        "scint_centroid_z_mm",
    ]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if df.empty:
        raise ValueError(f"{efficiency_map} has no scan rows")

    df["x_mm"] = unit_to_mm(df["x"], df["unit"])
    df["y_mm"] = unit_to_mm(df["y"], df["unit"])
    df["detected_photons_per_event"] = df["sipm_detected_photons"] / df["events"]
    df["generated_photons_per_event"] = df["generated_optical_photons"] / df["events"]
    df["scintillation_photons_per_event"] = df["scintillation_photons"] / df["events"]
    df["thickness_mm"] = thickness_mm
    df["run_dir"] = str(run_dir)
    df["run_id"] = str(config.get("run_id") or run_dir.name)
    df["events_per_point"] = config.get("simulation", {}).get("events_per_point", np.nan)
    return df, config


def rms(values: pd.Series) -> float:
    clean = values.dropna().astype(float)
    if clean.empty:
        return math.nan
    return float(np.sqrt(np.mean((clean - clean.mean()) ** 2)))


def summarize_region(df: pd.DataFrame, region: str) -> dict:
    if df.empty:
        raise ValueError(f"Region {region!r} has no points")

    mean_eff = float(df["collection_efficiency"].mean())
    rms_eff = rms(df["collection_efficiency"])
    return {
        "thickness_mm": float(df["thickness_mm"].iloc[0]),
        "region": region,
        "run_id": df["run_id"].iloc[0],
        "run_dir": df["run_dir"].iloc[0],
        "events_per_point": int(df["events_per_point"].iloc[0]),
        "n_points": int(len(df)),
        "mean_collection_efficiency": mean_eff,
        "rms_collection_efficiency": rms_eff,
        "rms_over_mean_collection_efficiency": rms_eff / mean_eff if mean_eff else math.nan,
        "mean_detected_photons_per_event": float(df["detected_photons_per_event"].mean()),
        "mean_generated_photons_per_event": float(df["generated_photons_per_event"].mean()),
        "mean_scintillation_photons_per_event": float(
            df["scintillation_photons_per_event"].mean()
        ),
        "zero_generated_points": int((df["generated_optical_photons"] <= 0).sum()),
        "missing_hit_points": int((df["hit_position_events"] < df["events"]).sum()),
        "mean_hit_z_mm": float(df["hit_z_mm"].dropna().mean()),
        "mean_scint_centroid_z_mm": float(df["scint_centroid_z_mm"].dropna().mean()),
    }


def plot_collection_summary(full: pd.DataFrame, fiducial: pd.DataFrame, out_dir: Path) -> None:
    fig, ax_mean = plt.subplots(figsize=(8.0, 5.4))
    ax_rms = ax_mean.twinx()

    for frame, label, marker in [
        (full, "full", "o"),
        (fiducial, "fiducial", "s"),
    ]:
        ordered = frame.sort_values("thickness_mm")
        ax_mean.plot(
            ordered["thickness_mm"],
            ordered["mean_collection_efficiency"],
            marker=marker,
            linewidth=2,
            label=f"mean efficiency ({label})",
        )
        ax_rms.plot(
            ordered["thickness_mm"],
            ordered["rms_collection_efficiency"],
            marker=marker,
            linestyle="--",
            linewidth=2,
            label=f"RMS ({label})",
        )

    ax_mean.set_xlabel("Tile thickness [mm]")
    ax_mean.set_ylabel("Mean collection efficiency")
    ax_rms.set_ylabel("Spatial RMS of collection efficiency")
    ax_mean.grid(True, alpha=0.3)

    lines, labels = ax_mean.get_legend_handles_labels()
    lines_rms, labels_rms = ax_rms.get_legend_handles_labels()
    ax_mean.legend(lines + lines_rms, labels + labels_rms, loc="best")
    fig.tight_layout()
    save_figure(fig, out_dir / "thickness_collection_summary")


def plot_yield_summary(full: pd.DataFrame, fiducial: pd.DataFrame, out_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.0, 5.4))
    metrics = [
        ("mean_detected_photons_per_event", "detected"),
        ("mean_generated_photons_per_event", "generated"),
        ("mean_scintillation_photons_per_event", "scintillation"),
    ]
    for frame, region, linestyle in [
        (full, "full", "-"),
        (fiducial, "fiducial", "--"),
    ]:
        ordered = frame.sort_values("thickness_mm")
        for metric, label in metrics:
            ax.plot(
                ordered["thickness_mm"],
                ordered[metric],
                marker="o",
                linestyle=linestyle,
                linewidth=2,
                label=f"{label} ({region})",
            )

    ax.set_xlabel("Tile thickness [mm]")
    ax.set_ylabel("Mean photons per event")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    save_figure(fig, out_dir / "thickness_yield_summary")


def plot_efficiency_map(df: pd.DataFrame, thickness_mm: float, out_dir: Path) -> None:
    xs = np.sort(df["x_mm"].dropna().unique())
    ys = np.sort(df["y_mm"].dropna().unique())
    pivot = df.pivot_table(
        index="y_mm",
        columns="x_mm",
        values="collection_efficiency",
        aggfunc="mean",
    ).reindex(index=ys, columns=xs)

    fig, ax = plt.subplots(figsize=(7.0, 6.2))
    if len(xs) >= 2 and len(ys) >= 2:
        dx = float(np.median(np.diff(xs)))
        dy = float(np.median(np.diff(ys)))
    else:
        dx = dy = 1.0
    extent = [xs.min() - dx / 2, xs.max() + dx / 2, ys.min() - dy / 2, ys.max() + dy / 2]
    image = ax.imshow(
        pivot.to_numpy(dtype=float),
        origin="lower",
        extent=extent,
        aspect="equal",
        interpolation="nearest",
    )
    fig.colorbar(image, ax=ax, label="Collection efficiency")
    ax.set_xlabel("Scan x [mm]")
    ax.set_ylabel("Scan y [mm]")
    ax.set_title(f"{thickness_mm:g} mm tile collection efficiency")
    fig.tight_layout()
    save_figure(fig, out_dir / f"efficiency_map_{thickness_mm:g}mm")


def save_figure(fig: plt.Figure, stem: Path) -> None:
    fig.savefig(stem.with_suffix(".png"), dpi=180)
    fig.savefig(stem.with_suffix(".pdf"))
    plt.close(fig)


def write_summary(frame: pd.DataFrame, path: Path) -> None:
    frame = frame.sort_values("thickness_mm")
    frame.to_csv(path, index=False, columns=SUMMARY_COLUMNS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze Week 6 tile-thickness efficiency maps."
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        type=parse_run_spec,
        metavar="THICKNESS_MM:RUN_DIR",
        help="Thickness and scan run directory. Repeat once per thickness.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Directory for summary CSVs and plots.",
    )
    parser.add_argument(
        "--fiducial-limit-mm",
        default=45.0,
        type=float,
        help="Keep abs(x_mm) and abs(y_mm) at or below this value for fiducial stats.",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    seen_thicknesses: set[float] = set()
    runs: list[pd.DataFrame] = []
    for thickness_mm, run_dir in args.run:
        if thickness_mm in seen_thicknesses:
            raise ValueError(f"Duplicate thickness: {thickness_mm:g} mm")
        seen_thicknesses.add(thickness_mm)
        df, _config = load_run(thickness_mm, run_dir)
        runs.append(df)

    summaries_full = []
    summaries_fiducial = []
    for df in runs:
        thickness_mm = float(df["thickness_mm"].iloc[0])
        summaries_full.append(summarize_region(df, "full"))
        fiducial = df[
            (df["x_mm"].abs() <= args.fiducial_limit_mm)
            & (df["y_mm"].abs() <= args.fiducial_limit_mm)
        ].copy()
        summaries_fiducial.append(summarize_region(fiducial, "fiducial"))
        plot_efficiency_map(df, thickness_mm, args.out_dir)

    full_summary = pd.DataFrame(summaries_full)
    fiducial_summary = pd.DataFrame(summaries_fiducial)
    write_summary(full_summary, args.out_dir / "thickness_summary_full.csv")
    write_summary(fiducial_summary, args.out_dir / "thickness_summary_fiducial.csv")
    plot_collection_summary(full_summary, fiducial_summary, args.out_dir)
    plot_yield_summary(full_summary, fiducial_summary, args.out_dir)

    print(f"Wrote Week 6 analysis outputs to {args.out_dir}")
    print(full_summary[["thickness_mm", "region", "n_points", "mean_collection_efficiency"]])
    print(fiducial_summary[["thickness_mm", "region", "n_points", "mean_collection_efficiency"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
