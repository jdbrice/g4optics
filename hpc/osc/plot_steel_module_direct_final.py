#!/usr/bin/env python3
"""Render static PNG/PDF figures from the checksum-valid final production analysis."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from analyze_steel_module_direct_final import SCHEMA_VERSION, THICKNESSES
from realistic_neutron_campaign_lib import verify_checksum_manifest
from steel_module_campaign_lib import atomic_write_json, sha256_file


FIGURE_SCHEMA_VERSION = "steel-module-direct-final-figures-v1"
LAYOUTS = ("back-center", "edge-center", "back-four")
LABELS = {
    "back-center": "Back center (1 SiPM)",
    "edge-center": "Edge center (1 SiPM)",
    "back-four": "Back four (4 SiPMs)",
}
COLORS = {
    "back-center": "#276FBF",
    "edge-center": "#D97706",
    "back-four": "#6B7D2A",
}
MARKERS = {"back-center": "o", "edge-center": "s", "back-four": "^"}
LINESTYLES = {"back-center": "-", "edge-center": "--", "back-four": "-."}
REQUIRED_CORE = {
    "configuration_estimates.csv",
    "pooled_production.csv",
    "standardized_response.csv",
    "thickness_ratios.csv",
    "layout_ratios.csv",
    "primary_contrasts.csv",
    "precision_review.json",
    "summary.md",
    "analysis_config.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def configure(matplotlib: object) -> None:
    matplotlib.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "axes.edgecolor": "#4B5563",
            "axes.linewidth": 0.8,
            "axes.grid": True,
            "grid.color": "#D1D5DB",
            "grid.linewidth": 0.6,
            "grid.alpha": 0.65,
            "legend.frameon": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
        }
    )


def errorbar(
    ax: object,
    *,
    x: Sequence[float],
    y: Sequence[float],
    low: Sequence[float],
    high: Sequence[float],
    label: str,
    color: str,
    marker: str,
    linestyle: str,
) -> None:
    lower = [max(0.0, point - bound) for point, bound in zip(y, low)]
    upper = [max(0.0, bound - point) for point, bound in zip(y, high)]
    ax.errorbar(
        x,
        y,
        yerr=[lower, upper],
        label=label,
        color=color,
        marker=marker,
        linestyle=linestyle,
        linewidth=1.8,
        markersize=5.5,
        capsize=3,
        markerfacecolor="white" if linestyle != "-" else color,
        markeredgewidth=1.2,
    )


def finish_axis(ax: object, *, ylabel: str, subtitle: str) -> None:
    ax.set_xlabel("Tile thickness (mm)")
    ax.set_ylabel(ylabel)
    ax.set_xticks(THICKNESSES)
    ax.set_xlim(3, 25)
    ax.set_title(subtitle, fontsize=9.5, color="#4B5563", pad=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def save_figure(fig: object, output_dir: Path, stem: str) -> list[str]:
    names = [f"{stem}.png", f"{stem}.pdf"]
    fig.savefig(output_dir / names[0], dpi=220, bbox_inches="tight")
    fig.savefig(output_dir / names[1], bbox_inches="tight")
    return names


def plot_pooled_production(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "pooled_production.csv")
    if len(rows) != 6:
        raise ValueError("pooled production figure requires six thickness rows")
    rows.sort(key=lambda row: int(row["tile_thickness_mm"]))
    x = [int(row["tile_thickness_mm"]) for row in rows]
    pooled = [float(row["pooled_scintillation_photons_per_neutron"]) for row in rows]
    low = [float(row["scintillation_ci95_low"]) for row in rows]
    high = [float(row["scintillation_ci95_high"]) for row in rows]
    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    errorbar(
        ax,
        x=x,
        y=pooled,
        low=low,
        high=high,
        label="Equal-weight pooled production",
        color="#1F2937",
        marker="D",
        linestyle="-",
    )
    layout_fields = {
        "back-center": "back_center_scintillation_per_neutron",
        "edge-center": "edge_center_scintillation_per_neutron",
        "back-four": "back_four_scintillation_per_neutron",
    }
    for layout in LAYOUTS:
        ax.scatter(
            x,
            [float(row[layout_fields[layout]]) for row in rows],
            label=f"{LABELS[layout]} stratum",
            color=COLORS[layout],
            marker=MARKERS[layout],
            facecolors="none" if layout != "back-center" else COLORS[layout],
            linewidths=1.2,
            s=38,
            zorder=3,
        )
    fig.suptitle("Scintillation production versus tile thickness", fontsize=13)
    finish_axis(
        ax,
        ylabel="Scintillation photons per incident neutron",
        subtitle="Equal 1/3 layout-stratum weighting; error bars are 95% event-bootstrap intervals",
    )
    ax.legend(ncol=2, fontsize=8.8)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, "pooled_production_vs_thickness")
    plt.close(fig)
    return names


def plot_configuration_metric(
    plt: object,
    analysis_dir: Path,
    output_dir: Path,
    *,
    point_field: str,
    low_field: str,
    high_field: str,
    ylabel: str,
    title: str,
    stem: str,
) -> list[str]:
    rows = read_csv(analysis_dir / "configuration_estimates.csv")
    if len(rows) != 18:
        raise ValueError(f"{title} requires 18 configuration rows")
    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    for layout in LAYOUTS:
        selected = sorted(
            (row for row in rows if row["sipm_layout"] == layout),
            key=lambda row: int(row["tile_thickness_mm"]),
        )
        errorbar(
            ax,
            x=[int(row["tile_thickness_mm"]) for row in selected],
            y=[float(row[point_field]) for row in selected],
            low=[float(row[low_field]) for row in selected],
            high=[float(row[high_field]) for row in selected],
            label=LABELS[layout],
            color=COLORS[layout],
            marker=MARKERS[layout],
            linestyle=LINESTYLES[layout],
        )
    fig.suptitle(title, fontsize=13)
    finish_axis(
        ax,
        ylabel=ylabel,
        subtitle="Direct configuration estimates; error bars are 95% event-bootstrap intervals",
    )
    ax.legend(fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, stem)
    plt.close(fig)
    return names


def plot_standardized(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "standardized_response.csv")
    if len(rows) != 18:
        raise ValueError("standardized response figure requires 18 rows")
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.5), sharex=True)
    for ax, layout in zip(axes, LAYOUTS):
        selected = sorted(
            (row for row in rows if row["sipm_layout"] == layout),
            key=lambda row: int(row["tile_thickness_mm"]),
        )
        x = [int(row["tile_thickness_mm"]) for row in selected]
        direct = [float(row["direct_observed_net"]) for row in selected]
        standard = [float(row["standardized_net"]) for row in selected]
        ax.plot(
            x,
            direct,
            color=COLORS[layout],
            marker=MARKERS[layout],
            linewidth=1.8,
            label="Direct observed",
        )
        ax.plot(
            x,
            standard,
            color="#1F2937",
            marker="D",
            markerfacecolor="white",
            linestyle="--",
            linewidth=1.5,
            label="Standardized (secondary)",
        )
        ax.set_title(LABELS[layout])
        ax.set_xticks(THICKNESSES)
        ax.set_xlabel("Tile thickness (mm)")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
    axes[0].set_ylabel("SiPM photons per incident neutron")
    axes[0].legend(fontsize=8.5)
    fig.suptitle(
        "Observed and production-standardized response", fontsize=13, y=0.995
    )
    fig.text(
        0.5,
        0.925,
        "Standardized curves use equal-weight pooled production and layout-specific collection",
        ha="center",
        fontsize=9.5,
        color="#4B5563",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.87))
    names = save_figure(fig, output_dir, "observed_vs_standardized_response")
    plt.close(fig)
    return names


def plot_relative_net(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "thickness_ratios.csv")
    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    for layout in LAYOUTS:
        selected = sorted(
            (row for row in rows if row["sipm_layout"] == layout),
            key=lambda row: int(row["compared_tile_thickness_mm"]),
        )
        x = [4] + [int(row["compared_tile_thickness_mm"]) for row in selected]
        y = [1.0] + [float(row["net_ratio"]) for row in selected]
        low = [1.0] + [float(row["net_ci95_low"]) for row in selected]
        high = [1.0] + [float(row["net_ci95_high"]) for row in selected]
        errorbar(
            ax,
            x=x,
            y=y,
            low=low,
            high=high,
            label=LABELS[layout],
            color=COLORS[layout],
            marker=MARKERS[layout],
            linestyle=LINESTYLES[layout],
        )
    ax.axhline(1.0, color="#374151", linewidth=1.0, linestyle=":", label="4 mm reference")
    fig.suptitle("Observed net response relative to 4 mm", fontsize=13)
    finish_axis(
        ax,
        ylabel="SiPM response ratio (thickness / 4 mm)",
        subtitle="Within-layout ratios; unity is the fixed 4 mm reference",
    )
    ax.legend(fontsize=8.8, ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, "net_response_relative_to_4mm")
    plt.close(fig)
    return names


def plot_primary_forest(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "primary_contrasts.csv")
    if len(rows) != 4:
        raise ValueError("primary forest plot requires four contrasts")
    labels = [
        "Pooled scintillation",
        "Back-center net",
        "Edge-center net",
        "Back-four aggregate net",
    ]
    ordered_ids = [
        "pooled-scintillation-production-24-over-4",
        "observed-net-back-center-24-over-4",
        "observed-net-edge-center-24-over-4",
        "observed-net-back-four-24-over-4",
    ]
    by_id = {row["primary_contrast_id"]: row for row in rows}
    ordered = [by_id[identifier] for identifier in ordered_ids]
    points = [float(row["ratio"]) for row in ordered]
    lows = [float(row["ci95_low"]) for row in ordered]
    highs = [float(row["ci95_high"]) for row in ordered]
    y = list(reversed(range(4)))
    colors = ["#1F2937", COLORS["back-center"], COLORS["edge-center"], COLORS["back-four"]]
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    for index, (point, low, high, color) in enumerate(zip(points, lows, highs, colors)):
        ax.errorbar(
            point,
            y[index],
            xerr=[[point - low], [high - point]],
            fmt="o",
            color=color,
            capsize=4,
            markersize=6,
            linewidth=1.8,
        )
        ax.text(high + 0.03 * max(highs), y[index], f"{point:.3f}", va="center", fontsize=9)
    ax.axvline(1.0, color="#374151", linestyle=":", linewidth=1.1)
    ax.set_yticks(y, labels)
    ax.set_xlabel("24 mm / 4 mm ratio")
    fig.suptitle("Precommitted primary production contrasts", fontsize=13)
    ax.set_title(
        "Points and 95% event-bootstrap intervals; vertical line marks unity",
        fontsize=9.5,
        color="#4B5563",
        pad=9,
    )
    ax.set_xlim(min(0.0, min(lows) * 0.9), max(highs) * 1.17)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, "primary_contrasts_forest")
    plt.close(fig)
    return names


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


def main() -> int:
    args = parse_args()
    analysis_dir = args.analysis_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else analysis_dir.with_name(f"{analysis_dir.name}-figures")
    )
    temp_dir: Path | None = None
    try:
        recorded = verify_checksum_manifest(analysis_dir, required=REQUIRED_CORE)
        if not REQUIRED_CORE.issubset(recorded):
            raise ValueError("final analysis checksum manifest is incomplete")
        config = json.loads(
            (analysis_dir / "analysis_config.json").read_text(encoding="utf-8")
        )
        sample = config.get("sample")
        if (
            config.get("schema_version") != SCHEMA_VERSION
            or config.get("accepted_statistical_evidence") is not True
            or not isinstance(sample, dict)
            or sample.get("configurations") != 18
        ):
            raise ValueError("final analysis identity is invalid")
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite final figures: {output_dir}")
        try:
            import matplotlib  # type: ignore[import-not-found]

            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("final production plotting requires matplotlib") from exc
        configure(matplotlib)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        files: list[str] = []
        files.extend(plot_pooled_production(plt, analysis_dir, temp_dir))
        files.extend(
            plot_configuration_metric(
                plt,
                analysis_dir,
                temp_dir,
                point_field="collection_sipm_over_generated",
                low_field="collection_ci95_low",
                high_field="collection_ci95_high",
                ylabel="Aggregate optical collection (SiPM / generated)",
                title="Optical collection versus tile thickness",
                stem="collection_vs_thickness",
            )
        )
        files.extend(
            plot_configuration_metric(
                plt,
                analysis_dir,
                temp_dir,
                point_field="observed_net_sipm_photons_per_neutron",
                low_field="net_ci95_low",
                high_field="net_ci95_high",
                ylabel="SiPM photons per incident neutron",
                title="Observed net response versus tile thickness",
                stem="observed_net_vs_thickness",
            )
        )
        files.extend(plot_standardized(plt, analysis_dir, temp_dir))
        files.extend(plot_relative_net(plt, analysis_dir, temp_dir))
        files.extend(plot_primary_forest(plt, analysis_dir, temp_dir))
        provenance = {
            "schema_version": FIGURE_SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "analysis_directory": str(analysis_dir),
            "analysis_config_sha256": sha256_file(analysis_dir / "analysis_config.json"),
            "analysis_sha256s_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
            "matplotlib_version": str(matplotlib.__version__),
            "backend": "Agg",
            "figures": files,
            "visual_contract": {
                "ordered_axis": "tile thickness in mm",
                "uncertainty": "95% event-bootstrap intervals",
                "palette": COLORS,
                "non_color_encoding": {
                    "markers": MARKERS,
                    "line_styles": LINESTYLES,
                },
                "ratio_reference": "unity",
            },
            "chart_map": [
                {
                    "figure": "pooled_production_vs_thickness",
                    "question": "How does neutron-induced scintillation production vary with thickness?",
                    "family": "ordered line with interval and stratum points",
                    "fields": "thickness, equal-weight pooled scintillation, 95% interval, layout strata",
                },
                {
                    "figure": "collection_vs_thickness",
                    "question": "How does aggregate optical collection vary within each layout?",
                    "family": "multi-series ordered line with intervals",
                    "fields": "thickness, collection, layout, 95% interval",
                },
                {
                    "figure": "observed_net_vs_thickness",
                    "question": "What is the directly observed net SiPM response curve?",
                    "family": "multi-series ordered line with intervals",
                    "fields": "thickness, observed net, layout, 95% interval",
                },
                {
                    "figure": "observed_vs_standardized_response",
                    "question": "How much do finite-sample production differences move each layout curve?",
                    "family": "faceted paired ordered lines",
                    "fields": "thickness, direct net, standardized net, layout",
                },
                {
                    "figure": "net_response_relative_to_4mm",
                    "question": "How does each within-layout response compare with its 4 mm reference?",
                    "family": "multi-series ratio line with intervals",
                    "fields": "thickness, net ratio, layout, 95% interval, unity",
                },
                {
                    "figure": "primary_contrasts_forest",
                    "question": "What are the four precommitted 24/4 production contrasts?",
                    "family": "faceted dot-and-interval comparison",
                    "fields": "contrast, ratio, 95% interval, unity",
                },
            ],
        }
        atomic_write_json(temp_dir / "figure_provenance.json", provenance)
        write_checksums(temp_dir)
        os.replace(temp_dir, output_dir)
        temp_dir = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot plot final direct steel-module production: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)
    print(f"Rendered final production figures into {output_dir}")
    print("Figures: 6 x PNG/PDF; core analysis unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
