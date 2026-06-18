#!/usr/bin/env python3
"""Analyze Week 7 surface-finish scans from OpNovice2 efficiency maps."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
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
    "surface_label",
    "region",
    "run_id",
    "run_dir",
    "events_per_point",
    "surface_preset",
    "surface_model",
    "surface_type",
    "surface_finish",
    "surface_sigma_alpha",
    "surface_reflectivity",
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

SURFACE_ORDER = {"polished": 0, "ground": 1, "wrapped": 2}


def parse_run_spec(spec: str) -> tuple[str, Path]:
    if ":" not in spec:
        raise argparse.ArgumentTypeError(
            f"Invalid --run {spec!r}; expected SURFACE_LABEL:RUN_DIR"
        )
    label, run_dir_text = spec.split(":", 1)
    label = label.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        raise argparse.ArgumentTypeError(
            f"Invalid surface label {label!r}; use letters, numbers, dot, underscore, or dash"
        )
    return label, Path(run_dir_text)


def unit_to_mm(values: pd.Series, units: pd.Series) -> pd.Series:
    unit_values = set(units.dropna().astype(str))
    unsupported = unit_values - {"mm", "cm"}
    if unsupported:
        raise ValueError(f"Unsupported scan coordinate units: {sorted(unsupported)}")
    factors = units.map({"mm": 1.0, "cm": 10.0})
    return values.astype(float) * factors.astype(float)


def load_run(surface_label: str, run_dir: Path) -> pd.DataFrame:
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
    if df.empty:
        raise ValueError(f"{efficiency_map} has no scan rows")

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

    surface = config.get("surface", {})
    df["x_mm"] = unit_to_mm(df["x"], df["unit"])
    df["y_mm"] = unit_to_mm(df["y"], df["unit"])
    df["detected_photons_per_event"] = df["sipm_detected_photons"] / df["events"]
    df["generated_photons_per_event"] = df["generated_optical_photons"] / df["events"]
    df["scintillation_photons_per_event"] = df["scintillation_photons"] / df["events"]
    df["surface_label"] = surface_label
    df["run_dir"] = str(run_dir)
    df["run_id"] = str(config.get("run_id") or run_dir.name)
    df["events_per_point"] = config.get("simulation", {}).get("events_per_point", np.nan)
    df["surface_preset"] = surface.get("preset")
    df["surface_model"] = surface.get("model")
    df["surface_type"] = surface.get("type")
    df["surface_finish"] = surface.get("finish")
    df["surface_sigma_alpha"] = surface.get("sigma_alpha")
    df["surface_reflectivity"] = surface.get("reflectivity")
    return df


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
        "surface_label": df["surface_label"].iloc[0],
        "region": region,
        "run_id": df["run_id"].iloc[0],
        "run_dir": df["run_dir"].iloc[0],
        "events_per_point": int(df["events_per_point"].iloc[0]),
        "surface_preset": df["surface_preset"].iloc[0],
        "surface_model": df["surface_model"].iloc[0],
        "surface_type": df["surface_type"].iloc[0],
        "surface_finish": df["surface_finish"].iloc[0],
        "surface_sigma_alpha": df["surface_sigma_alpha"].iloc[0],
        "surface_reflectivity": df["surface_reflectivity"].iloc[0],
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


def surface_sort_key(label: str) -> tuple[int, str]:
    return SURFACE_ORDER.get(str(label), 100), str(label)


def ordered_summary(frame: pd.DataFrame) -> pd.DataFrame:
    order = frame["surface_label"].map(lambda label: surface_sort_key(str(label)))
    return frame.assign(_order=order).sort_values("_order").drop(columns="_order")


def plot_collection_summary(full: pd.DataFrame, fiducial: pd.DataFrame, out_dir: Path) -> None:
    fig, ax_mean = plt.subplots(figsize=(8.4, 5.4))
    ax_rms = ax_mean.twinx()

    labels = [str(label) for label in ordered_summary(full)["surface_label"]]
    x = np.arange(len(labels), dtype=float)
    for frame, label, marker, offset in [
        (full, "full", "o", -0.06),
        (fiducial, "fiducial", "s", 0.06),
    ]:
        ordered = ordered_summary(frame)
        ax_mean.plot(
            x + offset,
            ordered["mean_collection_efficiency"],
            marker=marker,
            linewidth=2,
            label=f"mean efficiency ({label})",
        )
        ax_rms.plot(
            x + offset,
            ordered["rms_collection_efficiency"],
            marker=marker,
            linestyle="--",
            linewidth=2,
            label=f"RMS ({label})",
        )

    ax_mean.set_xticks(x)
    ax_mean.set_xticklabels(labels)
    ax_mean.set_xlabel("Surface condition")
    ax_mean.set_ylabel("Mean collection efficiency")
    ax_rms.set_ylabel("Spatial RMS of collection efficiency")
    ax_mean.grid(True, axis="y", alpha=0.3)

    lines, legend_labels = ax_mean.get_legend_handles_labels()
    lines_rms, labels_rms = ax_rms.get_legend_handles_labels()
    ax_mean.legend(lines + lines_rms, legend_labels + labels_rms, loc="best")
    fig.tight_layout()
    save_figure(fig, out_dir / "surface_collection_summary")


def plot_yield_summary(full: pd.DataFrame, fiducial: pd.DataFrame, out_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 5.4))
    metrics = [
        ("mean_detected_photons_per_event", "detected"),
        ("mean_generated_photons_per_event", "generated"),
        ("mean_scintillation_photons_per_event", "scintillation"),
    ]
    labels = [str(label) for label in ordered_summary(full)["surface_label"]]
    x = np.arange(len(labels), dtype=float)
    for frame, region, linestyle in [
        (full, "full", "-"),
        (fiducial, "fiducial", "--"),
    ]:
        ordered = ordered_summary(frame)
        for metric, label in metrics:
            ax.plot(
                x,
                ordered[metric],
                marker="o",
                linestyle=linestyle,
                linewidth=2,
                label=f"{label} ({region})",
            )

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Surface condition")
    ax.set_ylabel("Mean photons per event")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    save_figure(fig, out_dir / "surface_yield_summary")


def plot_efficiency_map(df: pd.DataFrame, surface_label: str, out_dir: Path) -> None:
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
    ax.set_title(f"{surface_label} surface collection efficiency")
    fig.tight_layout()
    save_figure(fig, out_dir / f"efficiency_map_{surface_label}")


def save_figure(fig: plt.Figure, stem: Path) -> None:
    fig.savefig(stem.with_suffix(".png"), dpi=180)
    fig.savefig(stem.with_suffix(".pdf"))
    plt.close(fig)


def write_summary(frame: pd.DataFrame, path: Path) -> None:
    ordered_summary(frame).to_csv(path, index=False, columns=SUMMARY_COLUMNS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze Week 7 surface-condition efficiency maps."
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        type=parse_run_spec,
        metavar="SURFACE_LABEL:RUN_DIR",
        help="Surface label and scan run directory. Repeat once per surface condition.",
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

    seen_labels: set[str] = set()
    runs: list[pd.DataFrame] = []
    for surface_label, run_dir in args.run:
        if surface_label in seen_labels:
            raise ValueError(f"Duplicate surface label: {surface_label}")
        seen_labels.add(surface_label)
        runs.append(load_run(surface_label, run_dir))

    summaries_full = []
    summaries_fiducial = []
    for df in runs:
        surface_label = str(df["surface_label"].iloc[0])
        summaries_full.append(summarize_region(df, "full"))
        fiducial = df[
            (df["x_mm"].abs() <= args.fiducial_limit_mm)
            & (df["y_mm"].abs() <= args.fiducial_limit_mm)
        ].copy()
        summaries_fiducial.append(summarize_region(fiducial, "fiducial"))
        plot_efficiency_map(df, surface_label, args.out_dir)

    full_summary = pd.DataFrame(summaries_full)
    fiducial_summary = pd.DataFrame(summaries_fiducial)
    write_summary(full_summary, args.out_dir / "surface_summary_full.csv")
    write_summary(fiducial_summary, args.out_dir / "surface_summary_fiducial.csv")
    plot_collection_summary(full_summary, fiducial_summary, args.out_dir)
    plot_yield_summary(full_summary, fiducial_summary, args.out_dir)

    print(f"Wrote Week 7 analysis outputs to {args.out_dir}")
    print(full_summary[["surface_label", "region", "n_points", "mean_collection_efficiency"]])
    print(fiducial_summary[["surface_label", "region", "n_points", "mean_collection_efficiency"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
