#!/usr/bin/env python3
"""Render checksum-bound headless figures for a ten-layer stack analysis."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from realistic_neutron_campaign_lib import sha256_file, verify_checksum_manifest


THICKNESSES = (4, 8, 12, 16, 20, 24)
COLORS = ("#2563EB", "#0891B2", "#059669", "#D97706", "#DC2626", "#7C3AED")
REQUIRED = {
    "thickness_summary.csv", "layer_sensor_estimates.csv", "longitudinal_profiles.csv",
    "transfer_matrix.csv", "distribution_diagnostics.csv", "loo_diagnostics.csv",
    "summary.md", "analysis_config.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str) -> float:
    return float(row[key])


def errorbar(ax: object, rows: list[dict[str, str]], point: str, low: str, high: str, ylabel: str) -> None:
    x = [int(row["tile_thickness_mm"]) for row in rows]
    y = [number(row, point) for row in rows]
    lo = [number(row, low) for row in rows]
    hi = [number(row, high) for row in rows]
    ax.errorbar(
        x, y,
        yerr=([max(0.0, value - bound) for value, bound in zip(y, lo)],
              [max(0.0, bound - value) for value, bound in zip(y, hi)]),
        marker="o", color="#1D4ED8", linewidth=1.7, capsize=3,
    )
    ax.set_xlabel("Tile thickness (mm)")
    ax.set_ylabel(ylabel)
    ax.set_xticks(THICKNESSES)


def save(fig: object, directory: Path, stem: str) -> list[str]:
    names = [f"{stem}.png", f"{stem}.pdf"]
    fig.savefig(directory / names[0], dpi=220, bbox_inches="tight")
    fig.savefig(directory / names[1], bbox_inches="tight")
    return names


def full_stack(plt: object, analysis: Path, output: Path) -> list[str]:
    rows = sorted(read_csv(analysis / "thickness_summary.csv"), key=lambda row: int(row["tile_thickness_mm"]))
    if len(rows) != 6:
        raise ValueError("full-stack figure requires six thickness rows")
    fields = (
        ("generated_photons_per_neutron", "generated_ci95_low", "generated_ci95_high", "Generated photons / neutron", "Production"),
        ("local_collection", "local_collection_ci95_low", "local_collection_ci95_high", "Same-layer causal collection", "Collection"),
        ("local_net_photons_per_neutron", "local_net_ci95_low", "local_net_ci95_high", "Local SiPM photons / neutron", "Net response"),
    )
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for ax, (point, low, high, ylabel, title) in zip(axes, fields):
        errorbar(ax, rows, point, low, high, ylabel)
        ax.set_title(title)
    fig.suptitle("Ten-layer stack response versus uniform tile thickness")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save(fig, output, "full_stack_vs_thickness")
    plt.close(fig)
    return names


def layer_collection(plt: object, analysis: Path, output: Path) -> list[str]:
    rows = read_csv(analysis / "layer_sensor_estimates.csv")
    if len(rows) != 120:
        raise ValueError("layer collection figure requires 120 layer/sensor rows")
    fig, axes = plt.subplots(2, 3, figsize=(14, 8), sharex=True)
    for ax, thickness in zip(axes.flat, THICKNESSES):
        for sensor, color, marker in ((0, "#2563EB", "o"), (1, "#DC2626", "s")):
            selected = sorted(
                (row for row in rows if int(row["tile_thickness_mm"]) == thickness and int(row["local_sensor"]) == sensor),
                key=lambda row: int(row["layer"]),
            )
            ax.errorbar(
                [int(row["layer"]) for row in selected],
                [number(row, "local_collection") for row in selected],
                yerr=(
                    [max(0.0, number(row, "local_collection") - number(row, "local_collection_ci95_low")) for row in selected],
                    [max(0.0, number(row, "local_collection_ci95_high") - number(row, "local_collection")) for row in selected],
                ),
                color=color, marker=marker, linewidth=1.2, capsize=2, label=f"Sensor {sensor}",
            )
        ax.set_title(f"{thickness} mm")
        ax.set_xticks(range(10))
        ax.set_xlabel("Layer (0 upstream)")
        ax.set_ylabel("Same-layer collection")
    axes.flat[0].legend()
    fig.suptitle("Per-layer, per-SiPM causal collection (95% bootstrap intervals)")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    names = save(fig, output, "layer_sensor_collection")
    plt.close(fig)
    return names


def longitudinal(plt: object, analysis: Path, output: Path) -> list[str]:
    rows = read_csv(analysis / "longitudinal_profiles.csv")
    if len(rows) != 60:
        raise ValueError("longitudinal figure requires 60 rows")
    fields = (
        ("generated_photons_per_neutron", "Generated photons / neutron"),
        ("steel_edep_mev_per_neutron", "Steel energy deposit (MeV / neutron)"),
        ("tile_edep_mev_per_neutron", "Tile energy deposit (MeV / neutron)"),
        ("charged_entries_per_neutron", "Charged tile entries / neutron"),
    )
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    for ax, (field, ylabel) in zip(axes.flat, fields):
        for thickness, color in zip(THICKNESSES, COLORS):
            selected = sorted((row for row in rows if int(row["tile_thickness_mm"]) == thickness), key=lambda row: int(row["layer"]))
            ax.plot(range(10), [number(row, field) for row in selected], marker="o", linewidth=1.25, color=color, label=f"{thickness} mm")
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Layer (0 upstream)")
        ax.set_xticks(range(10))
    axes.flat[0].legend(ncol=2, fontsize=8)
    fig.suptitle("Longitudinal shower and light profiles")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    names = save(fig, output, "longitudinal_profiles")
    plt.close(fig)
    return names


def local_vs_all(plt: object, analysis: Path, output: Path) -> list[str]:
    rows = sorted(read_csv(analysis / "thickness_summary.csv"), key=lambda row: int(row["tile_thickness_mm"]))
    x = [int(row["tile_thickness_mm"]) for row in rows]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    axes[0].plot(x, [number(row, "local_collection") for row in rows], marker="o", label="Same-layer causal")
    axes[0].plot(x, [number(row, "all_origin_collection") for row in rows], marker="s", linestyle="--", label="All origins")
    axes[0].set_ylabel("Detected / generated photons")
    axes[0].set_title("Collection")
    axes[1].plot(x, [number(row, "local_net_photons_per_neutron") for row in rows], marker="o", label="Same-layer causal")
    axes[1].plot(x, [number(row, "all_origin_net_photons_per_neutron") for row in rows], marker="s", linestyle="--", label="All origins")
    axes[1].set_ylabel("Detected photons / incident neutron")
    axes[1].set_title("Net response")
    for ax in axes:
        ax.set_xlabel("Tile thickness (mm)")
        ax.set_xticks(THICKNESSES)
        ax.legend()
    fig.suptitle("Local-origin and all-origin stack response")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    names = save(fig, output, "local_origin_vs_all_origin")
    plt.close(fig)
    return names


def transfer_heatmaps(plt: object, analysis: Path, output: Path) -> list[str]:
    rows = read_csv(analysis / "transfer_matrix.csv")
    if len(rows) != 1200:
        raise ValueError("transfer heatmap requires 1,200 origin/destination/sensor rows")

    # Build the two-SiPM matrices before choosing the shared color scale.  The
    # previous implementation found the maximum from individual sensor rows
    # and then plotted their sum, which saturated much of the diagonal.
    matrices: dict[int, np.ndarray] = {}
    for thickness in THICKNESSES:
        matrix = np.zeros((10, 10), dtype=float)
        for row in rows:
            if int(row["tile_thickness_mm"]) == thickness:
                value = number(row, "per_origin_generated_photon")
                origin = int(row["origin_layer"])
                destination = int(row["destination_layer"])
                if np.isfinite(value):
                    matrix[origin, destination] += value
                else:
                    matrix[origin, destination] = np.nan
        matrices[thickness] = matrix

    finite_values = np.concatenate(
        [matrix[np.isfinite(matrix)] for matrix in matrices.values()]
    )
    maximum = (
        float(np.max(finite_values))
        if finite_values.size and np.max(finite_values) > 0
        else 1.0
    )
    off_diagonal_detected = sum(
        int(row["detected_photons"])
        for row in rows
        if row["same_layer"].lower() != "true"
    )

    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    image = None
    for ax, thickness in zip(axes.flat, THICKNESSES):
        matrix = matrices[thickness]
        image = ax.imshow(matrix, origin="upper", vmin=0, vmax=maximum, cmap="magma", aspect="equal")
        ax.set_title(f"{thickness} mm")
        ax.set_xlabel("Destination layer")
        ax.set_ylabel("Origin layer")
        ax.set_xticks(range(10))
        ax.set_yticks(range(10))

    if image is None:
        raise ValueError("transfer heatmap did not create an image")
    # Reserve a dedicated strip outside the six panels so the shared colorbar
    # cannot cover the 12 mm and 24 mm matrices.
    fig.subplots_adjust(left=0.06, right=0.87, bottom=0.08, top=0.84, wspace=0.3, hspace=0.34)
    colorbar_ax = fig.add_axes((0.90, 0.17, 0.018, 0.60))
    colorbar = fig.colorbar(image, cax=colorbar_ax)
    colorbar.set_label("Detected photons / generated photon at origin")
    fig.suptitle("Optical origin-to-destination response (two destination SiPMs combined)")
    fig.text(
        0.5,
        0.895,
        f"Off-diagonal cross-layer detected photons: {off_diagonal_detected:,}; diagonal cells are same-layer collection",
        ha="center",
        va="center",
        fontsize=10,
    )
    names = save(fig, output, "cross_layer_transfer_heatmap")
    plt.close(fig)
    return names


def stability(plt: object, analysis: Path, output: Path) -> list[str]:
    distributions = read_csv(analysis / "distribution_diagnostics.csv")
    loo = read_csv(analysis / "loo_diagnostics.csv")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for metric, marker in (("local_origin_detected_photons", "o"), ("all_origin_detected_photons", "s")):
        selected = sorted((row for row in distributions if row["metric"] == metric), key=lambda row: int(row["tile_thickness_mm"]))
        axes[0].plot([int(row["tile_thickness_mm"]) for row in selected], [number(row, "top_1pct_share") for row in selected], marker=marker, label=metric.replace("_", " "))
    axes[0].set_ylabel("Top 1% contribution to total")
    axes[0].set_xlabel("Tile thickness (mm)")
    axes[0].set_xticks(THICKNESSES)
    axes[0].legend(fontsize=8)
    metrics = ("local_collection", "local_net_photons_per_neutron")
    width = 0.34
    x = np.arange(len(THICKNESSES))
    for offset, metric in ((-width / 2, metrics[0]), (width / 2, metrics[1])):
        maxima = [max(number(row, "relative_shift") for row in loo if row["metric"] == metric and int(row["tile_thickness_mm"]) == thickness) for thickness in THICKNESSES]
        axes[1].bar(x + offset, maxima, width, label=metric.replace("_", " "))
    axes[1].axhline(0.10, color="#D97706", linestyle="--", label="10% review line")
    axes[1].axhline(0.20, color="#DC2626", linestyle=":", label="20% pilot ceiling")
    axes[1].set_xticks(x, THICKNESSES)
    axes[1].set_xlabel("Tile thickness (mm)")
    axes[1].set_ylabel("Maximum leave-one-task-out shift")
    axes[1].legend(fontsize=8)
    fig.suptitle("Heavy-tail concentration and task-block stability")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    names = save(fig, output, "tail_and_loo_diagnostics")
    plt.close(fig)
    return names


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(item for item in directory.iterdir() if item.is_file() and item.name != "SHA256SUMS"):
            stream.write(f"{sha256_file(path)}  {path.name}\n")


def main() -> int:
    args = parse_args()
    analysis = args.analysis_dir.expanduser().resolve()
    output = args.output_dir.expanduser().resolve() if args.output_dir else analysis.with_name(f"{analysis.name}-figures")
    temp: Path | None = None
    try:
        verify_checksum_manifest(analysis, required=REQUIRED)
        config = json.loads((analysis / "analysis_config.json").read_text(encoding="utf-8"))
        if (
            config.get("schema_version") != "steel-module-stack-analysis-v1"
            or config.get("accepted_statistical_evidence") is not True
            or config.get("configuration_count") != 6
            or config.get("finalized_additive_totals_reconciliation") != "passed"
            or config.get("unknown_origin_detection_audit") != "passed_zero"
        ):
            raise ValueError("stack core analysis is not accepted statistical evidence")
        if output.exists():
            raise ValueError(f"refusing to overwrite stack figures: {output}")
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise ValueError("stack plotting requires matplotlib") from exc
        matplotlib.rcParams.update({"font.family": "DejaVu Sans", "axes.grid": True, "grid.alpha": 0.35, "figure.facecolor": "white"})
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        files: list[str] = []
        files.extend(full_stack(plt, analysis, temp))
        files.extend(layer_collection(plt, analysis, temp))
        files.extend(longitudinal(plt, analysis, temp))
        files.extend(local_vs_all(plt, analysis, temp))
        files.extend(transfer_heatmaps(plt, analysis, temp))
        files.extend(stability(plt, analysis, temp))
        provenance = {
            "schema_version": "steel-module-stack-figures-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "analysis_directory": str(analysis),
            "analysis_config_sha256": sha256_file(analysis / "analysis_config.json"),
            "analysis_sha256s_sha256": sha256_file(analysis / "SHA256SUMS"),
            "plotter_sha256": sha256_file(Path(__file__).resolve()),
            "matplotlib_version": matplotlib.__version__,
            "backend": "Agg",
            "figures": files,
            "uncertainty": "95% event-bootstrap percentile intervals",
            "primary_collection": "same-origin tile to same-layer two-SiPM ratio of sums",
        }
        (temp / "figure_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        write_checksums(temp)
        os.replace(temp, output)
        temp = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot plot steel-module stack analysis: {exc}", file=os.sys.stderr)
        return 1
    finally:
        if temp is not None and temp.exists():
            shutil.rmtree(temp)
    print(f"Rendered stack figures into {output}")
    print("Figures: 6 x PNG/PDF; core analysis unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
