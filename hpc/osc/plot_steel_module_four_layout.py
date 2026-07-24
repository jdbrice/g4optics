#!/usr/bin/env python3
"""Render checksum-bound PNG/PDF figures for the four-layout analysis."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from analyze_steel_module_four_layout import LAYOUTS, SCHEMA_VERSION, THICKNESSES
from realistic_neutron_campaign_lib import verify_checksum_manifest
from steel_module_campaign_lib import atomic_write_json, sha256_file


FIGURE_SCHEMA_VERSION = "steel-module-four-layout-figures-v1"
LABELS = {
    "back-center": "Back center (1 SiPM)",
    "edge-center": "Edge center (1 SiPM)",
    "edge-two": "Edge two (2 SiPMs)",
    "back-four": "Back four (4 SiPMs)",
}
COLORS = {
    "back-center": "#276FBF",
    "edge-center": "#D97706",
    "edge-two": "#8B5CF6",
    "back-four": "#4F772D",
}
MARKERS = {
    "back-center": "o",
    "edge-center": "s",
    "edge-two": "D",
    "back-four": "^",
}
LINESTYLES = {
    "back-center": "-",
    "edge-center": "--",
    "edge-two": ":",
    "back-four": "-.",
}
SENSOR_COLORS = ("#2563EB", "#DC2626", "#059669", "#D97706")
REQUIRED_CORE = {
    "configuration_estimates.csv",
    "distribution_diagnostics.csv",
    "response_pathway.csv",
    "per_sensor_efficiency.csv",
    "layout_cost_diagnostics.csv",
    "pooled_production.csv",
    "standardized_response.csv",
    "thickness_ratios.csv",
    "layout_ratios.csv",
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
            "axes.titlesize": 12,
            "axes.labelsize": 10.5,
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
    ax.errorbar(
        x,
        y,
        yerr=[
            [max(0.0, point - bound) for point, bound in zip(y, low)],
            [max(0.0, bound - point) for point, bound in zip(y, high)],
        ],
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


def style_axis(ax: object, *, ylabel: str, subtitle: str = "") -> None:
    ax.set_xlabel("Tile thickness (mm)")
    ax.set_ylabel(ylabel)
    ax.set_xticks(THICKNESSES)
    ax.set_xlim(3, 25)
    if subtitle:
        ax.set_title(subtitle, fontsize=9.2, color="#4B5563", pad=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def save_figure(fig: object, output_dir: Path, stem: str) -> list[str]:
    names = [f"{stem}.png", f"{stem}.pdf"]
    fig.savefig(output_dir / names[0], dpi=220, bbox_inches="tight")
    fig.savefig(output_dir / names[1], bbox_inches="tight")
    return names


def configuration_metric(
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
    if len(rows) != 24:
        raise ValueError(f"{title} requires 24 configuration rows")
    fig, ax = plt.subplots(figsize=(8.5, 5.3))
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
    style_axis(
        ax,
        ylabel=ylabel,
        subtitle="Aggregate across installed SiPMs; error bars are 95% event-bootstrap intervals",
    )
    ax.legend(fontsize=8.7, ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, stem)
    plt.close(fig)
    return names


def plot_production(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "pooled_production.csv")
    if len(rows) != 6:
        raise ValueError("production figure requires six thickness rows")
    rows.sort(key=lambda row: int(row["tile_thickness_mm"]))
    x = [int(row["tile_thickness_mm"]) for row in rows]
    fig, ax = plt.subplots(figsize=(8.5, 5.3))
    errorbar(
        ax,
        x=x,
        y=[float(row["pooled_scintillation_photons_per_neutron"]) for row in rows],
        low=[float(row["scintillation_ci95_low"]) for row in rows],
        high=[float(row["scintillation_ci95_high"]) for row in rows],
        label="Equal-weight four-layout pool",
        color="#111827",
        marker="P",
        linestyle="-",
    )
    fields = {
        "back-center": "back_center_scintillation_per_neutron",
        "edge-center": "edge_center_scintillation_per_neutron",
        "edge-two": "edge_two_scintillation_per_neutron",
        "back-four": "back_four_scintillation_per_neutron",
    }
    for layout in LAYOUTS:
        ax.scatter(
            x,
            [float(row[fields[layout]]) for row in rows],
            label=f"{LABELS[layout]} stratum",
            color=COLORS[layout],
            marker=MARKERS[layout],
            facecolors="none",
            linewidths=1.2,
            s=40,
            zorder=3,
        )
    fig.suptitle("Scintillation production versus tile thickness", fontsize=13)
    style_axis(
        ax,
        ylabel="Scintillation photons per incident neutron",
        subtitle="Equal 1/4 layout-stratum reconciliation; pooled error bars are 95% intervals",
    )
    ax.legend(fontsize=8.2, ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, "production_vs_thickness")
    plt.close(fig)
    return names


def plot_layout_cost(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "layout_cost_diagnostics.csv")
    if len(rows) != 24:
        raise ValueError("layout-cost figure requires 24 rows")
    panels = (
        (
            "aggregate_net",
            "aggregate_net_ci95_low",
            "aggregate_net_ci95_high",
            "Aggregate",
            "SiPM photons / neutron",
        ),
        (
            "net_per_installed_sensor",
            "net_per_installed_sensor_ci95_low",
            "net_per_installed_sensor_ci95_high",
            "Per installed sensor",
            "SiPM photons / neutron / sensor",
        ),
        (
            "net_per_active_area_mm2",
            "net_per_active_area_mm2_ci95_low",
            "net_per_active_area_mm2_ci95_high",
            "Per active area",
            "SiPM photons / neutron / mm2",
        ),
    )
    fig, axes = plt.subplots(1, 3, figsize=(15.2, 4.8), sharex=True)
    for ax, (point, low, high, title, ylabel) in zip(axes, panels):
        for layout in LAYOUTS:
            selected = sorted(
                (row for row in rows if row["sipm_layout"] == layout),
                key=lambda row: int(row["tile_thickness_mm"]),
            )
            errorbar(
                ax,
                x=[int(row["tile_thickness_mm"]) for row in selected],
                y=[float(row[point]) for row in selected],
                low=[float(row[low]) for row in selected],
                high=[float(row[high]) for row in selected],
                label=LABELS[layout],
                color=COLORS[layout],
                marker=MARKERS[layout],
                linestyle=LINESTYLES[layout],
            )
        style_axis(ax, ylabel=ylabel)
        ax.set_title(title)
    axes[0].legend(fontsize=7.8)
    fig.suptitle("Observed response with sensor-count and active-area normalization", fontsize=13)
    fig.text(
        0.5,
        0.925,
        "Per-sensor and per-area panels are secondary layout-cost diagnostics; all proxies have 5.76 mm2 active area",
        ha="center",
        fontsize=9.2,
        color="#4B5563",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    names = save_figure(fig, output_dir, "layout_cost_normalization")
    plt.close(fig)
    return names


def plot_per_sensor_collection(
    plt: object, analysis_dir: Path, output_dir: Path
) -> list[str]:
    rows = read_csv(analysis_dir / "per_sensor_efficiency.csv")
    expected = 6 * sum((1, 1, 2, 4))
    if len(rows) != expected:
        raise ValueError("per-sensor figure has an invalid installed-sensor matrix")
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.8), sharex=True)
    for ax, layout in zip(axes, ("edge-two", "back-four")):
        selected_layout = [row for row in rows if row["sipm_layout"] == layout]
        sensor_indices = sorted({int(row["sensor_index"]) for row in selected_layout})
        for sensor_index in sensor_indices:
            selected = sorted(
                (
                    row
                    for row in selected_layout
                    if int(row["sensor_index"]) == sensor_index
                ),
                key=lambda row: int(row["tile_thickness_mm"]),
            )
            errorbar(
                ax,
                x=[int(row["tile_thickness_mm"]) for row in selected],
                y=[float(row["collection_sipm_over_generated"]) for row in selected],
                low=[float(row["collection_ci95_low"]) for row in selected],
                high=[float(row["collection_ci95_high"]) for row in selected],
                label=f"Sensor copy {sensor_index}",
                color=SENSOR_COLORS[sensor_index],
                marker=("o", "s", "^", "D")[sensor_index],
                linestyle=("-", "--", "-.", ":")[sensor_index],
            )
        style_axis(ax, ylabel="Per-sensor collection (sensor / generated)")
        ax.set_title(LABELS[layout])
        ax.legend(fontsize=8.2)
    fig.suptitle("Individual SiPM collection efficiency in multi-sensor layouts", fontsize=13)
    fig.text(
        0.5,
        0.925,
        "Sensor-copy curves use correlated event-bootstrap intervals within each configuration",
        ha="center",
        fontsize=9.2,
        color="#4B5563",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    names = save_figure(fig, output_dir, "multi_sensor_collection_by_copy")
    plt.close(fig)
    return names


def plot_standardized(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "standardized_response.csv")
    if len(rows) != 24:
        raise ValueError("standardized response figure requires 24 rows")
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.0), sharex=True)
    for ax, layout in zip(axes.flat, LAYOUTS):
        selected = sorted(
            (row for row in rows if row["sipm_layout"] == layout),
            key=lambda row: int(row["tile_thickness_mm"]),
        )
        x = [int(row["tile_thickness_mm"]) for row in selected]
        ax.plot(
            x,
            [float(row["direct_observed_net"]) for row in selected],
            color=COLORS[layout],
            marker=MARKERS[layout],
            linewidth=1.8,
            label="Direct observed",
        )
        ax.plot(
            x,
            [float(row["standardized_net"]) for row in selected],
            color="#111827",
            marker="P",
            markerfacecolor="white",
            linestyle="--",
            linewidth=1.5,
            label="Production-standardized",
        )
        style_axis(ax, ylabel="SiPM photons / neutron")
        ax.set_title(LABELS[layout])
    axes[0, 0].legend(fontsize=8.2)
    fig.suptitle("Direct and production-standardized net response", fontsize=13)
    fig.text(
        0.5,
        0.945,
        "Standardized response is a model-based secondary diagnostic using equal-weight four-layout production",
        ha="center",
        fontsize=9.2,
        color="#4B5563",
    )
    fig.tight_layout(rect=(0, 0, 1, 0.91))
    names = save_figure(fig, output_dir, "observed_vs_standardized_response")
    plt.close(fig)
    return names


def plot_relative_net(plt: object, analysis_dir: Path, output_dir: Path) -> list[str]:
    rows = read_csv(analysis_dir / "thickness_ratios.csv")
    if len(rows) != 20:
        raise ValueError("relative-response figure requires 20 thickness ratios")
    fig, ax = plt.subplots(figsize=(8.5, 5.3))
    for layout in LAYOUTS:
        selected = sorted(
            (row for row in rows if row["sipm_layout"] == layout),
            key=lambda row: int(row["compared_tile_thickness_mm"]),
        )
        errorbar(
            ax,
            x=[4] + [int(row["compared_tile_thickness_mm"]) for row in selected],
            y=[1.0] + [float(row["net_ratio"]) for row in selected],
            low=[1.0] + [float(row["net_ci95_low"]) for row in selected],
            high=[1.0] + [float(row["net_ci95_high"]) for row in selected],
            label=LABELS[layout],
            color=COLORS[layout],
            marker=MARKERS[layout],
            linestyle=LINESTYLES[layout],
        )
    ax.axhline(1.0, color="#374151", linewidth=1.0, linestyle=":")
    fig.suptitle("Observed net response relative to each layout's 4 mm tile", fontsize=13)
    style_axis(
        ax,
        ylabel="Net response ratio (thickness / 4 mm)",
        subtitle="Within-layout ratios; unity is the fixed 4 mm reference",
    )
    ax.legend(fontsize=8.7, ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save_figure(fig, output_dir, "net_response_relative_to_4mm")
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
            raise ValueError("four-layout analysis checksum manifest is incomplete")
        config = json.loads(
            (analysis_dir / "analysis_config.json").read_text(encoding="utf-8")
        )
        sample = config.get("sample")
        if (
            config.get("schema_version") != SCHEMA_VERSION
            or config.get("accepted_statistical_evidence") is not True
            or not isinstance(sample, dict)
            or sample.get("configurations") != 24
            or tuple(sample.get("layouts", ())) != LAYOUTS
        ):
            raise ValueError("four-layout analysis identity is invalid")
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite four-layout figures: {output_dir}")
        try:
            import matplotlib  # type: ignore[import-not-found]

            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("four-layout plotting requires matplotlib") from exc
        configure(matplotlib)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        files: list[str] = []
        files.extend(plot_production(plt, analysis_dir, temp_dir))
        files.extend(
            configuration_metric(
                plt,
                analysis_dir,
                temp_dir,
                point_field="collection_sipm_over_generated",
                low_field="collection_ci95_low",
                high_field="collection_ci95_high",
                ylabel="Aggregate optical collection (SiPM / generated)",
                title="Aggregate optical collection versus tile thickness",
                stem="aggregate_collection_vs_thickness",
            )
        )
        files.extend(
            configuration_metric(
                plt,
                analysis_dir,
                temp_dir,
                point_field="observed_net_sipm_photons_per_neutron",
                low_field="net_ci95_low",
                high_field="net_ci95_high",
                ylabel="Aggregate SiPM photons per incident neutron",
                title="Aggregate observed net response versus tile thickness",
                stem="aggregate_net_vs_thickness",
            )
        )
        files.extend(plot_layout_cost(plt, analysis_dir, temp_dir))
        files.extend(plot_per_sensor_collection(plt, analysis_dir, temp_dir))
        files.extend(plot_standardized(plt, analysis_dir, temp_dir))
        files.extend(plot_relative_net(plt, analysis_dir, temp_dir))
        provenance = {
            "schema_version": FIGURE_SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "analysis_directory": str(analysis_dir),
            "analysis_config_sha256": sha256_file(analysis_dir / "analysis_config.json"),
            "analysis_sha256s_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
            "plotter_sha256": sha256_file(Path(__file__).resolve()),
            "matplotlib_version": str(matplotlib.__version__),
            "backend": "Agg",
            "figures": files,
            "layout_order": list(LAYOUTS),
            "palette": COLORS,
            "non_color_encoding": {
                "markers": MARKERS,
                "line_styles": LINESTYLES,
            },
            "uncertainty": "95% event-bootstrap intervals",
            "normalization_caveat": (
                "per-sensor and per-active-area curves are secondary layout-cost "
                "diagnostics; all sensors use the same 5.76 mm2 proxy area"
            ),
        }
        atomic_write_json(temp_dir / "figure_provenance.json", provenance)
        write_checksums(temp_dir)
        os.replace(temp_dir, output_dir)
        temp_dir = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot plot four-layout steel-module production: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)
    print(f"Rendered four-layout figures into {output_dir}")
    print("Figures: 7 x PNG/PDF; core analysis unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
