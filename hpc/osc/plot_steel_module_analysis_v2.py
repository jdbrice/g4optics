#!/usr/bin/env python3
"""Render checksum-bound, headless figures from steel-module analysis-v2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


ANALYSIS_SCHEMA_VERSION = "steel-module-analysis-v2-config-v1"
PLOT_SCHEMA_VERSION = "steel-module-analysis-v2-figures-v1"
REFERENCE_ABSORBER_MM = 500
LAYOUT_ORDER = ("back-center", "edge-center", "back-four")
LAYOUT_LABELS = {
    "back-center": "Back center",
    "edge-center": "Edge center",
    "back-four": "Back four",
}
REQUIRED_INPUTS = (
    "configuration_estimates.csv",
    "distribution_diagnostics.csv",
    "seed_block_stability.csv",
    "pooled_production.csv",
    "standardized_response.csv",
    "primary_contrasts.csv",
    "absorber_equivalence.csv",
    "analysis_config.json",
)
FIGURE_BASENAMES = (
    "pooled_production_vs_thickness",
    "observed_vs_standardized_net_response",
    "primary_24_over_4_decomposition",
    "absorber_equivalence_forest",
    "tail_and_block_stability",
)

# Explicit, print-safe palette. Series also differ by marker and line style.
INK = "#20262E"
MUTED = "#667085"
GRID = "#D8DEE8"
BLUE = "#2864A6"
BLUE_LIGHT = "#DCE9F7"
ORANGE = "#D97706"
ORANGE_LIGHT = "#FDE7C3"
OLIVE = "#71833A"
OLIVE_LIGHT = "#E7ECCD"
PINK = "#B14E75"
NEUTRAL_LIGHT = "#EEF1F5"


class PlotInputError(ValueError):
    """Raised when an analysis-v2 input cannot support an honest plot."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        required=True,
        type=Path,
        help="Completed finalized/analysis-v2 directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to the analysis directory's analysis-v2-figures sibling.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, object]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except json.JSONDecodeError as exc:
        raise PlotInputError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlotInputError(f"expected a JSON object in {path}")
    return value


def safe_manifest_name(raw_name: str) -> str:
    name = PurePosixPath(raw_name)
    if (
        not raw_name
        or name.is_absolute()
        or str(name) != raw_name
        or any(part in {"", ".", ".."} for part in name.parts)
        or "\\" in raw_name
    ):
        raise PlotInputError(f"unsafe checksum-manifest path: {raw_name!r}")
    return raw_name


def verify_checksum_manifest(
    directory: Path, *, required: Sequence[str] = REQUIRED_INPUTS
) -> dict[str, str]:
    manifest_path = directory / "SHA256SUMS"
    if not manifest_path.is_file():
        raise PlotInputError(f"missing checksum manifest: {manifest_path}")
    recorded: dict[str, str] = {}
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            raise PlotInputError(
                f"blank line {line_number} in checksum manifest {manifest_path}"
            )
        try:
            digest, raw_name = line.split("  ", maxsplit=1)
        except ValueError as exc:
            raise PlotInputError(
                f"invalid checksum row {line_number} in {manifest_path}"
            ) from exc
        name = safe_manifest_name(raw_name)
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise PlotInputError(
                f"invalid SHA-256 on row {line_number} in {manifest_path}"
            )
        if name in recorded:
            raise PlotInputError(f"duplicate checksum entry in {manifest_path}: {name}")
        path = directory.joinpath(*PurePosixPath(name).parts)
        if not path.is_file():
            raise PlotInputError(f"checksum manifest names a missing file: {path}")
        actual = sha256_file(path)
        if actual != digest:
            raise PlotInputError(
                f"checksum mismatch for {path}: expected {digest}, got {actual}"
            )
        recorded[name] = digest

    actual_files = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if set(recorded) != actual_files:
        missing = sorted(actual_files - set(recorded))
        stale = sorted(set(recorded) - actual_files)
        details = []
        if missing:
            details.append(f"unrecorded files={missing}")
        if stale:
            details.append(f"missing recorded files={stale}")
        raise PlotInputError(
            f"checksum manifest does not cover {directory}: " + "; ".join(details)
        )
    absent = [name for name in required if name not in recorded]
    if absent:
        raise PlotInputError(
            "checksum manifest is missing required files: "
            + ", ".join(absent)
        )
    return recorded


def validate_analysis_config(
    analysis_dir: Path, recorded: Mapping[str, str]
) -> dict[str, object]:
    config = load_json(analysis_dir / "analysis_config.json")
    if config.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise PlotInputError(
            "analysis_config.json is not a supported analysis-v2 config: "
            f"expected {ANALYSIS_SCHEMA_VERSION!r}, got "
            f"{config.get('schema_version')!r}"
        )
    for field in ("campaign_id", "plan_hash"):
        value = config.get(field)
        if not isinstance(value, str) or not value:
            raise PlotInputError(f"analysis_config.json lacks valid {field}")
    simulation_commit = next(
        (
            config.get(field)
            for field in ("simulation_git_commit", "simulation_commit", "git_commit")
            if isinstance(config.get(field), str) and config.get(field)
        ),
        None,
    )
    if simulation_commit is None:
        raise PlotInputError(
            "analysis_config.json lacks a simulation commit "
            "(accepted fields: simulation_git_commit, simulation_commit, git_commit)"
        )
    accepted = config.get("accepted_statistical_evidence")
    if not isinstance(accepted, bool):
        raise PlotInputError(
            "analysis_config.json accepted_statistical_evidence must be boolean"
        )
    outputs = config.get("outputs")
    if not isinstance(outputs, dict):
        raise PlotInputError("analysis_config.json lacks the v2 outputs map")
    recorded_names = set(recorded)
    for logical_name, filename in outputs.items():
        if not isinstance(logical_name, str) or not isinstance(filename, str):
            raise PlotInputError("analysis_config.json outputs must map strings to strings")
        if filename not in recorded_names:
            raise PlotInputError(
                f"analysis_config.json output {logical_name!r} is not checksum-bound: "
                f"{filename!r}"
            )
    return config


def analysis_simulation_commit(config: Mapping[str, object]) -> str:
    for field in ("simulation_git_commit", "simulation_commit", "git_commit"):
        value = config.get(field)
        if isinstance(value, str) and value:
            return value
    raise PlotInputError("validated analysis config lost its simulation commit")


def read_csv_table(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = tuple(reader.fieldnames or ())
        if not headers:
            raise PlotInputError(f"CSV has no header: {path}")
        if len(set(headers)) != len(headers):
            raise PlotInputError(f"CSV has duplicate columns: {path}")
        rows = list(reader)
    if not rows:
        raise PlotInputError(f"CSV contains no data rows: {path}")
    return headers, rows


def resolve_columns(
    path: Path,
    headers: Sequence[str],
    requirements: Mapping[str, Sequence[str]],
) -> dict[str, str]:
    available = set(headers)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for logical_name, aliases in requirements.items():
        match = next((name for name in aliases if name in available), None)
        if match is None:
            missing.append(f"{logical_name} (accepted: {', '.join(aliases)})")
        else:
            resolved[logical_name] = match
    if missing:
        raise PlotInputError(
            f"missing required plot columns in {path}: {'; '.join(missing)}. "
            f"Available columns: {', '.join(headers)}"
        )
    return resolved


def text_value(row: Mapping[str, str], column: str, *, context: str) -> str:
    value = row.get(column, "").strip()
    if not value:
        raise PlotInputError(f"empty {column!r} in {context}")
    return value


def finite_float(row: Mapping[str, str], column: str, *, context: str) -> float:
    raw = text_value(row, column, context=context)
    try:
        value = float(raw)
    except ValueError as exc:
        raise PlotInputError(f"non-numeric {column!r}={raw!r} in {context}") from exc
    if not math.isfinite(value):
        raise PlotInputError(f"non-finite {column!r}={raw!r} in {context}")
    return value


def integer_value(row: Mapping[str, str], column: str, *, context: str) -> int:
    value = finite_float(row, column, context=context)
    if value != int(value):
        raise PlotInputError(f"non-integer {column!r}={value!r} in {context}")
    return int(value)


def select_reference_absorber(rows: Sequence[Mapping[str, str]], column: str) -> int:
    values = {
        integer_value(row, column, context=f"row {index + 2}")
        for index, row in enumerate(rows)
    }
    if REFERENCE_ABSORBER_MM in values:
        return REFERENCE_ABSORBER_MM
    raise PlotInputError(
        f"plots require the {REFERENCE_ABSORBER_MM} mm reference absorber; "
        f"available values are {sorted(values)}"
    )


def validate_ci(low: float, high: float) -> None:
    # A percentile-bootstrap interval need not contain the original point estimate.
    if low > high:
        raise PlotInputError(f"invalid confidence interval [{low}, {high}]")


def positive_denominator(value: float, *, context: str) -> float:
    if value <= 0:
        raise PlotInputError(f"cannot form a ratio with non-positive {context}: {value}")
    return value


def configure_matplotlib(matplotlib: object) -> None:
    matplotlib.rcParams.update(  # type: ignore[attr-defined]
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "axes.titlecolor": INK,
            "axes.titlesize": 11,
            "axes.titleweight": "semibold",
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "text.color": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.7,
            "grid.alpha": 0.7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
            "lines.linewidth": 1.8,
            "lines.markersize": 5.5,
            "savefig.facecolor": "white",
        }
    )


def save_figure(fig: object, output_dir: Path, basename: str, *, title: str) -> None:
    metadata = {"Title": title, "Creator": Path(__file__).name}
    fig.savefig(  # type: ignore[attr-defined]
        output_dir / f"{basename}.png",
        dpi=200,
        bbox_inches="tight",
        metadata=metadata,
    )
    fig.savefig(  # type: ignore[attr-defined]
        output_dir / f"{basename}.pdf",
        bbox_inches="tight",
        metadata=metadata,
    )


def style_axis(ax: object, *, x_label: str, y_label: str) -> None:
    ax.set_xlabel(x_label)  # type: ignore[attr-defined]
    ax.set_ylabel(y_label)  # type: ignore[attr-defined]
    ax.grid(True, axis="y", zorder=0)  # type: ignore[attr-defined]
    ax.grid(False, axis="x")  # type: ignore[attr-defined]


def plot_pooled_production(
    plt: object,
    pooled_path: Path,
    output_dir: Path,
) -> None:
    headers, rows = read_csv_table(pooled_path)
    columns = resolve_columns(
        pooled_path,
        headers,
        {
            "thickness": ("tile_thickness_mm",),
            "absorber": ("absorber_transverse_mm",),
            "generated": ("pooled_generated_photons_per_neutron",),
            "generated_low": ("generated_ci95_low",),
            "generated_high": ("generated_ci95_high",),
            "scintillation": ("pooled_scintillation_photons_per_neutron",),
            "scintillation_low": ("scintillation_ci95_low",),
            "scintillation_high": ("scintillation_ci95_high",),
            "back_center_generated": ("back_center_generated_per_neutron",),
            "edge_center_generated": ("edge_center_generated_per_neutron",),
            "back_four_generated": ("back_four_generated_per_neutron",),
            "back_center_scintillation": (
                "back_center_scintillation_per_neutron",
            ),
            "edge_center_scintillation": (
                "edge_center_scintillation_per_neutron",
            ),
            "back_four_scintillation": ("back_four_scintillation_per_neutron",),
        },
    )
    reference = select_reference_absorber(rows, columns["absorber"])
    absorbers = sorted(
        {
            integer_value(row, columns["absorber"], context=f"row {index + 2}")
            for index, row in enumerate(rows)
        }
    )
    color_by_absorber = {
        value: color
        for value, color in zip(
            sorted(absorbers, reverse=True), (BLUE, ORANGE, OLIVE, PINK)
        )
    }
    linestyle_by_absorber = {
        value: style
        for value, style in zip(
            sorted(absorbers, reverse=True), ("-", "--", ":", "-.")
        )
    }
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.0), constrained_layout=True)
    metric_specs = (
        (
            "Generated optical photons",
            "generated",
            "generated_low",
            "generated_high",
            "generated",
        ),
        (
            "Scintillation photons",
            "scintillation",
            "scintillation_low",
            "scintillation_high",
            "scintillation",
        ),
    )
    layout_markers = {"back-center": "o", "edge-center": "s", "back-four": "^"}
    for ax, (title, value_key, low_key, high_key, suffix) in zip(axes, metric_specs):
        for absorber in sorted(absorbers, reverse=True):
            subset = [
                row
                for row in rows
                if integer_value(row, columns["absorber"], context="pooled row")
                == absorber
            ]
            subset.sort(
                key=lambda row: integer_value(
                    row, columns["thickness"], context="pooled row"
                )
            )
            x = [
                integer_value(row, columns["thickness"], context="pooled row")
                for row in subset
            ]
            y = [
                finite_float(row, columns[value_key], context="pooled row")
                for row in subset
            ]
            low = [
                finite_float(row, columns[low_key], context="pooled row")
                for row in subset
            ]
            high = [
                finite_float(row, columns[high_key], context="pooled row")
                for row in subset
            ]
            for lower, upper in zip(low, high):
                validate_ci(lower, upper)
            color = color_by_absorber[absorber]
            ax.plot(
                x,
                y,
                marker="o",
                color=color,
                linestyle=linestyle_by_absorber[absorber],
                label=f"Pooled, {absorber} mm steel",
                zorder=3,
            )
            ax.fill_between(x, low, high, color=color, alpha=0.11, zorder=1)

        reference_rows = [
            row
            for row in rows
            if integer_value(row, columns["absorber"], context="pooled row")
            == reference
        ]
        offsets = {"back-center": -0.20, "edge-center": 0.0, "back-four": 0.20}
        for layout in LAYOUT_ORDER:
            field = columns[f"{layout.replace('-', '_')}_{suffix}"]
            x = [
                integer_value(row, columns["thickness"], context="pooled row")
                + offsets[layout]
                for row in reference_rows
            ]
            y = [finite_float(row, field, context="pooled row") for row in reference_rows]
            ax.scatter(
                x,
                y,
                s=28,
                marker=layout_markers[layout],
                facecolor="white",
                edgecolor=INK,
                linewidth=0.9,
                label=f"{LAYOUT_LABELS[layout]} unpooled ({reference} mm)",
                zorder=4,
            )
        ax.set_title(title)
        style_axis(
            ax,
            x_label="Tile thickness (mm)",
            y_label="Photons per incident neutron",
        )
        ax.set_ylim(bottom=0)
    axes[0].legend(fontsize=7.6, ncol=1, loc="upper left")
    axes[1].legend(fontsize=7.6, ncol=1, loc="upper left")
    fig.suptitle("Pooled production versus tile thickness", fontsize=14, weight="bold")
    fig.text(
        0.5,
        -0.02,
        "Lines show stratified pooled estimates with 95% bootstrap intervals; "
        f"open markers show the three unpooled layouts for {reference} mm steel.",
        ha="center",
        color=MUTED,
        fontsize=8.5,
    )
    save_figure(
        fig,
        output_dir,
        "pooled_production_vs_thickness",
        title="Pooled production versus tile thickness",
    )
    plt.close(fig)


def plot_observed_vs_standardized(
    plt: object,
    standardized_path: Path,
    output_dir: Path,
) -> None:
    headers, rows = read_csv_table(standardized_path)
    columns = resolve_columns(
        standardized_path,
        headers,
        {
            "thickness": ("tile_thickness_mm",),
            "layout": ("sipm_layout",),
            "absorber": ("absorber_transverse_mm",),
            "observed": ("direct_observed_net", "observed_net"),
            "observed_low": ("direct_net_ci95_low", "observed_net_ci95_low"),
            "observed_high": ("direct_net_ci95_high", "observed_net_ci95_high"),
            "standardized": ("standardized_net",),
            "standardized_low": ("standardized_net_ci95_low",),
            "standardized_high": ("standardized_net_ci95_high",),
        },
    )
    reference = select_reference_absorber(rows, columns["absorber"])
    fig, axes = plt.subplots(1, 3, figsize=(15.2, 4.8), constrained_layout=True)
    for ax, layout in zip(axes, LAYOUT_ORDER):
        subset = [
            row
            for row in rows
            if text_value(row, columns["layout"], context="standardized row")
            == layout
            and integer_value(row, columns["absorber"], context="standardized row")
            == reference
        ]
        subset.sort(
            key=lambda row: integer_value(
                row, columns["thickness"], context="standardized row"
            )
        )
        if not subset:
            raise PlotInputError(
                f"standardized_response.csv has no {layout} rows for {reference} mm"
            )
        x = [
            integer_value(row, columns["thickness"], context="standardized row")
            for row in subset
        ]
        series = (
            (
                "Direct observed",
                "observed",
                "observed_low",
                "observed_high",
                BLUE,
                "o",
                "-",
            ),
            (
                "Standardized (model-based)",
                "standardized",
                "standardized_low",
                "standardized_high",
                ORANGE,
                "s",
                "--",
            ),
        )
        for label, value_key, low_key, high_key, color, marker, linestyle in series:
            y = [
                finite_float(row, columns[value_key], context="standardized row")
                for row in subset
            ]
            low = [
                finite_float(row, columns[low_key], context="standardized row")
                for row in subset
            ]
            high = [
                finite_float(row, columns[high_key], context="standardized row")
                for row in subset
            ]
            for lower, upper in zip(low, high):
                validate_ci(lower, upper)
            ax.vlines(x, low, high, color=color, linewidth=1.0, alpha=0.8, zorder=2)
            ax.plot(
                x,
                y,
                color=color,
                marker=marker,
                linestyle=linestyle,
                label=label,
                zorder=3,
            )
        ax.set_title(LAYOUT_LABELS[layout])
        style_axis(
            ax,
            x_label="Tile thickness (mm)",
            y_label="SiPM photons per incident neutron",
        )
        ax.set_ylim(bottom=0)
    axes[0].legend(fontsize=8, loc="upper left")
    fig.suptitle(
        f"Observed and standardized net response ({reference} mm steel)",
        fontsize=14,
        weight="bold",
    )
    fig.text(
        0.5,
        -0.02,
        "Points show means with 95% bootstrap intervals. Standardized response uses "
        "pooled generated production and remains a secondary diagnostic.",
        ha="center",
        color=MUTED,
        fontsize=8.5,
    )
    save_figure(
        fig,
        output_dir,
        "observed_vs_standardized_net_response",
        title="Observed and standardized net response",
    )
    plt.close(fig)


def primary_label(row: Mapping[str, str], columns: Mapping[str, str]) -> str:
    identifier = text_value(row, columns["id"], context="primary contrast row")
    layout = row.get(columns["layout"], "").strip()
    if layout in LAYOUT_LABELS:
        return f"{LAYOUT_LABELS[layout]} observed net"
    if "production" in identifier or "scintillation" in identifier:
        return "Pooled scintillation production"
    return identifier.replace("_", " ").replace("-", " ")


def reference_rows_by_layout(
    rows: Sequence[Mapping[str, str]],
    columns: Mapping[str, str],
    reference: int,
) -> dict[tuple[str, int], Mapping[str, str]]:
    selected: dict[tuple[str, int], Mapping[str, str]] = {}
    for row in rows:
        absorber = integer_value(row, columns["absorber"], context="configuration row")
        if absorber != reference:
            continue
        layout = text_value(row, columns["layout"], context="configuration row")
        thickness = integer_value(row, columns["thickness"], context="configuration row")
        key = (layout, thickness)
        if key in selected:
            raise PlotInputError(f"duplicate configuration row for {key}, {reference} mm")
        selected[key] = row
    return selected


def plot_primary_decomposition(
    plt: object,
    primary_path: Path,
    configuration_path: Path,
    pooled_path: Path,
    output_dir: Path,
) -> None:
    primary_headers, primary_rows = read_csv_table(primary_path)
    primary_columns = resolve_columns(
        primary_path,
        primary_headers,
        {
            "id": ("primary_contrast_id", "contrast_id"),
            "layout": ("sipm_layout",),
            "ratio": ("ratio", "point_ratio"),
            "low": ("ci95_low",),
            "high": ("ci95_high",),
        },
    )
    if len(primary_rows) != 4:
        raise PlotInputError(
            f"primary_contrasts.csv must contain exactly four rows, found {len(primary_rows)}"
        )

    configuration_headers, configuration_rows = read_csv_table(configuration_path)
    configuration_columns = resolve_columns(
        configuration_path,
        configuration_headers,
        {
            "thickness": ("tile_thickness_mm",),
            "layout": ("sipm_layout",),
            "absorber": ("absorber_transverse_mm",),
            "collection": ("collection_sipm_over_generated",),
            "net": (
                "observed_net_sipm_photons_per_neutron",
                "direct_observed_net",
            ),
        },
    )
    reference = select_reference_absorber(
        configuration_rows, configuration_columns["absorber"]
    )
    configurations = reference_rows_by_layout(
        configuration_rows, configuration_columns, reference
    )

    pooled_headers, pooled_rows = read_csv_table(pooled_path)
    pooled_columns = resolve_columns(
        pooled_path,
        pooled_headers,
        {
            "thickness": ("tile_thickness_mm",),
            "absorber": ("absorber_transverse_mm",),
            "scintillation": ("pooled_scintillation_photons_per_neutron",),
        },
    )
    pooled_reference = {
        integer_value(row, pooled_columns["thickness"], context="pooled row"): row
        for row in pooled_rows
        if integer_value(row, pooled_columns["absorber"], context="pooled row")
        == reference
    }
    for thickness in (4, 24):
        if thickness not in pooled_reference:
            raise PlotInputError(
                f"pooled_production.csv lacks {thickness} mm at {reference} mm absorber"
            )
    production_ratio = finite_float(
        pooled_reference[24], pooled_columns["scintillation"], context="pooled 24 mm"
    ) / positive_denominator(
        finite_float(
            pooled_reference[4],
            pooled_columns["scintillation"],
            context="pooled 4 mm",
        ),
        context="pooled 4 mm scintillation production",
    )

    fig, axes = plt.subplots(1, 2, figsize=(14.3, 5.7), constrained_layout=True)
    ax = axes[0]
    ordered = sorted(
        primary_rows,
        key=lambda row: (
            0 if not row.get(primary_columns["layout"], "").strip() else 1,
            LAYOUT_ORDER.index(row.get(primary_columns["layout"], "").strip())
            if row.get(primary_columns["layout"], "").strip() in LAYOUT_ORDER
            else 99,
        ),
    )
    y_positions = list(range(len(ordered)))[::-1]
    primary_scale_values: list[float] = []
    for y, row in zip(y_positions, ordered):
        point = finite_float(row, primary_columns["ratio"], context="primary contrast row")
        low = finite_float(row, primary_columns["low"], context="primary contrast row")
        high = finite_float(row, primary_columns["high"], context="primary contrast row")
        validate_ci(low, high)
        primary_scale_values.extend((point, low, high))
        ax.hlines(y, low, high, color=BLUE, linewidth=1.5, zorder=2)
        ax.scatter(
            point,
            y,
            marker="o",
            edgecolor=BLUE,
            facecolor="white",
            linewidth=1.3,
            s=34,
            zorder=3,
        )
    ax.axvline(1.0, color=INK, linestyle="--", linewidth=1.1, zorder=1)
    ax.set_yticks(y_positions)
    ax.set_yticklabels([primary_label(row, primary_columns) for row in ordered])
    if min(primary_scale_values) > 0:
        ax.set_xscale("log")
        ax.set_xlabel("24 mm / 4 mm ratio (log scale)")
    else:
        ax.set_xlabel("24 mm / 4 mm ratio")
    ax.set_title("Primary contrasts with 95% intervals")
    ax.grid(True, axis="x")
    ax.grid(False, axis="y")

    ax = axes[1]
    offsets = {"production": 0.22, "collection": 0.0, "net": -0.22}
    component_style = {
        "production": (BLUE, "o"),
        "collection": (ORANGE, "s"),
        "net": (OLIVE, "^"),
    }
    decomposition_values: list[float] = []
    for index, layout in enumerate(LAYOUT_ORDER):
        try:
            row4 = configurations[(layout, 4)]
            row24 = configurations[(layout, 24)]
        except KeyError as exc:
            raise PlotInputError(
                f"configuration_estimates.csv lacks a 4/24 mm endpoint for {layout}"
            ) from exc
        collection_ratio = finite_float(
            row24, configuration_columns["collection"], context=f"{layout} 24 mm"
        ) / positive_denominator(
            finite_float(
                row4,
                configuration_columns["collection"],
                context=f"{layout} 4 mm",
            ),
            context=f"{layout} 4 mm collection",
        )
        net_ratio = finite_float(
            row24, configuration_columns["net"], context=f"{layout} 24 mm"
        ) / positive_denominator(
            finite_float(
                row4, configuration_columns["net"], context=f"{layout} 4 mm"
            ),
            context=f"{layout} 4 mm observed net",
        )
        values = {
            "production": production_ratio,
            "collection": collection_ratio,
            "net": net_ratio,
        }
        decomposition_values.extend(values.values())
        for component, value in values.items():
            color, marker = component_style[component]
            ax.scatter(
                value,
                index + offsets[component],
                color=color,
                marker=marker,
                facecolor="white" if component != "net" else color,
                linewidth=1.2,
                s=46,
                label=component.capitalize() if index == 0 else None,
                zorder=3,
            )
    ax.axvline(1.0, color=INK, linestyle="--", linewidth=1.1, zorder=1)
    ax.set_yticks(range(len(LAYOUT_ORDER)))
    ax.set_yticklabels([LAYOUT_LABELS[item] for item in LAYOUT_ORDER])
    ax.invert_yaxis()
    if min(decomposition_values) > 0:
        ax.set_xscale("log")
        ax.set_xlabel("24 mm / 4 mm point ratio (log scale)")
    else:
        ax.set_xlabel("24 mm / 4 mm point ratio")
    ax.set_title("Production / collection / observed-net decomposition")
    ax.grid(True, axis="x")
    ax.grid(False, axis="y")
    ax.legend(loc="lower right", fontsize=8)

    fig.suptitle(
        f"Primary 24 mm / 4 mm evidence ({reference} mm steel)",
        fontsize=14,
        weight="bold",
    )
    fig.text(
        0.5,
        -0.02,
        "The decomposition panel shows point estimates; interval evidence is shown in "
        "the primary-contrast forest panel.",
        ha="center",
        color=MUTED,
        fontsize=8.5,
    )
    save_figure(
        fig,
        output_dir,
        "primary_24_over_4_decomposition",
        title="Primary 24 mm over 4 mm contrasts and decomposition",
    )
    plt.close(fig)


def absorber_row_label(row: Mapping[str, str], columns: Mapping[str, str]) -> str:
    candidate = integer_value(row, columns["candidate"], context="absorber row")
    reference = integer_value(row, columns["reference"], context="absorber row")
    thickness = integer_value(row, columns["thickness"], context="absorber row")
    scope = text_value(row, columns["scope"], context="absorber row")
    metric = text_value(row, columns["metric"], context="absorber row")
    layout = row.get(columns["layout"], "").strip()
    detail = LAYOUT_LABELS.get(layout, scope.replace("_", " ").replace("-", " "))
    return f"{candidate}/{reference} · {thickness} mm · {detail} {metric}"


def plot_absorber_equivalence(
    plt: object,
    absorber_path: Path,
    output_dir: Path,
) -> None:
    headers, rows = read_csv_table(absorber_path)
    columns = resolve_columns(
        absorber_path,
        headers,
        {
            "scope": ("scope",),
            "metric": ("metric",),
            "layout": ("sipm_layout",),
            "thickness": ("tile_thickness_mm",),
            "candidate": ("candidate_absorber_transverse_mm",),
            "reference": ("reference_absorber_transverse_mm",),
            "ratio": ("ratio",),
            "low": ("ci95_low",),
            "high": ("ci95_high",),
            "equivalent_5": ("equivalent_within_5pct",),
            "equivalent_10": ("equivalent_within_10pct",),
        },
    )
    rows = sorted(
        rows,
        key=lambda row: (
            integer_value(row, columns["candidate"], context="absorber row"),
            integer_value(row, columns["thickness"], context="absorber row"),
            text_value(row, columns["scope"], context="absorber row"),
            row.get(columns["layout"], ""),
            text_value(row, columns["metric"], context="absorber row"),
        ),
    )
    height = max(7.0, 0.34 * len(rows) + 2.8)
    fig, ax = plt.subplots(figsize=(12.8, height), constrained_layout=True)
    ax.axvspan(0.90, 1.10, color=ORANGE_LIGHT, alpha=0.45, zorder=0)
    ax.axvspan(0.95, 1.05, color=BLUE_LIGHT, alpha=0.85, zorder=0)
    ax.axvline(1.0, color=INK, linestyle="--", linewidth=1.1, zorder=1)
    y_positions = list(range(len(rows)))[::-1]
    bounds: list[float] = [0.90, 1.10]
    for y, row in zip(y_positions, rows):
        point = finite_float(row, columns["ratio"], context="absorber row")
        low = finite_float(row, columns["low"], context="absorber row")
        high = finite_float(row, columns["high"], context="absorber row")
        validate_ci(low, high)
        scope = text_value(row, columns["scope"], context="absorber row")
        color = BLUE if "pooled" in scope else ORANGE
        marker = "o" if "pooled" in scope else "s"
        ax.hlines(y, low, high, color=color, linewidth=1.3, zorder=2)
        ax.scatter(
            point,
            y,
            marker=marker,
            edgecolor=color,
            facecolor="white",
            linewidth=1.1,
            s=30,
            zorder=3,
        )
        bounds.extend((low, high))
    ax.set_yticks(y_positions)
    ax.set_yticklabels([absorber_row_label(row, columns) for row in rows], fontsize=7.7)
    ax.set_xlabel("Candidate / 500 mm ratio")
    ax.set_title("Absorber-size equivalence review (95% bootstrap intervals)")
    ax.grid(True, axis="x")
    ax.grid(False, axis="y")
    minimum, maximum = min(bounds), max(bounds)
    padding = max(0.04, 0.06 * (maximum - minimum))
    ax.set_xlim(max(0.0, minimum - padding), maximum + padding)
    from matplotlib.patches import Patch  # type: ignore[import-not-found]

    ax.legend(
        handles=[
            Patch(facecolor=BLUE_LIGHT, edgecolor="none", label="±5% review band"),
            Patch(facecolor=ORANGE_LIGHT, edgecolor="none", label="±10% review band"),
        ],
        loc="lower right",
        fontsize=8,
    )
    fig.text(
        0.5,
        -0.01,
        "Band containment is a conservative review rule, not a formal TOST and not "
        "automatic acceptance.",
        ha="center",
        color=MUTED,
        fontsize=8.5,
    )
    save_figure(
        fig,
        output_dir,
        "absorber_equivalence_forest",
        title="Absorber-size equivalence review",
    )
    plt.close(fig)


def collect_block_shift_maxima(
    path: Path,
) -> tuple[list[str], list[float]]:
    headers, rows = read_csv_table(path)
    columns = resolve_columns(
        path,
        headers,
        {
            "scope": ("scope",),
            "variant": ("estimate_variant", "stability_variant"),
            "generated_shift": ("generated_relative_shift",),
            "scintillation_shift": ("scintillation_relative_shift",),
            "collection_shift": ("collection_relative_shift",),
            "net_shift": ("net_relative_shift",),
            "contrast_shift": ("contrast_relative_shift",),
        },
    )
    maxima = {
        "Generated": 0.0,
        "Scintillation": 0.0,
        "Collection": 0.0,
        "Observed net": 0.0,
        "Primary contrast": 0.0,
    }
    seen = {name: False for name in maxima}
    field_map = {
        "Generated": "generated_shift",
        "Scintillation": "scintillation_shift",
        "Collection": "collection_shift",
        "Observed net": "net_shift",
        "Primary contrast": "contrast_shift",
    }
    for row in rows:
        variant = row.get(columns["variant"], "").strip().lower()
        if "leave" not in variant:
            continue
        scope = row.get(columns["scope"], "").strip().lower()
        for label, logical_field in field_map.items():
            if label == "Primary contrast" and "primary" not in scope:
                continue
            if label != "Primary contrast" and "configuration" not in scope:
                continue
            raw = row.get(columns[logical_field], "").strip()
            if not raw or raw.lower() == "nan":
                continue
            try:
                value = float(raw)
            except ValueError as exc:
                raise PlotInputError(
                    f"non-numeric {columns[logical_field]}={raw!r} in {path}"
                ) from exc
            if not math.isfinite(value):
                continue
            maxima[label] = max(maxima[label], abs(value))
            seen[label] = True
    missing = [label for label, present in seen.items() if not present]
    if missing:
        raise PlotInputError(
            f"seed_block_stability.csv has no leave-one-block-out shifts for: "
            f"{', '.join(missing)}"
        )
    labels = list(maxima)
    return labels, [maxima[label] for label in labels]


def plot_tail_and_block_stability(
    plt: object,
    distribution_path: Path,
    block_path: Path,
    output_dir: Path,
) -> None:
    headers, rows = read_csv_table(distribution_path)
    columns = resolve_columns(
        distribution_path,
        headers,
        {
            "metric": ("metric",),
            "layout": ("sipm_layout",),
            "thickness": ("tile_thickness_mm",),
            "absorber": ("absorber_transverse_mm",),
            "zero_fraction": ("zero_fraction",),
            "top_1": ("top_1pct_sum_fraction",),
            "top_5": ("top_5pct_sum_fraction",),
        },
    )
    sipm_rows = [
        row
        for row in rows
        if text_value(row, columns["metric"], context="distribution row").lower()
        in {"sipm", "sipm_detected", "sipm_detected_photons"}
    ]
    if not sipm_rows:
        raise PlotInputError(
            "distribution_diagnostics.csv has no SiPM metric rows for tail plotting"
        )
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.2), constrained_layout=True)
    ax = axes[0]
    for row in sipm_rows:
        zero = finite_float(row, columns["zero_fraction"], context="distribution row")
        top1 = finite_float(row, columns["top_1"], context="distribution row")
        top5 = finite_float(row, columns["top_5"], context="distribution row")
        ax.plot([zero, zero], [top1, top5], color=GRID, linewidth=0.7, zorder=1)
        ax.scatter(
            zero,
            top1,
            color=BLUE,
            marker="o",
            facecolor="white",
            linewidth=1.0,
            s=30,
            zorder=3,
        )
        ax.scatter(zero, top5, color=ORANGE, marker="s", s=28, zorder=3)
    ax.scatter([], [], color=BLUE, marker="o", facecolor="white", label="Top 1% share")
    ax.scatter([], [], color=ORANGE, marker="s", label="Top 5% share")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("SiPM tail concentration across configurations")
    style_axis(ax, x_label="Zero-response fraction", y_label="Share of total SiPM response")
    ax.legend(loc="lower right", fontsize=8)

    labels, maxima = collect_block_shift_maxima(block_path)
    ax = axes[1]
    positions = list(range(len(labels)))
    ax.bar(
        positions,
        [100.0 * value for value in maxima],
        color=BLUE_LIGHT,
        edgecolor=BLUE,
        linewidth=1.0,
        zorder=3,
    )
    ax.axhline(10.0, color=ORANGE, linestyle="--", linewidth=1.1, label="10% review flag")
    ax.axhline(20.0, color=INK, linestyle=":", linewidth=1.1, label="20% review flag")
    for x, value in zip(positions, maxima):
        ax.text(
            x,
            100.0 * value,
            f"{100.0 * value:.1f}%",
            ha="center",
            va="bottom",
            fontsize=7.6,
            color=INK,
        )
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=24, ha="right")
    ax.set_title("Maximum leave-one-block-out relative shift")
    style_axis(ax, x_label="Metric", y_label="Maximum absolute shift (%)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=8)
    fig.suptitle("Tail concentration and seed-block stability", fontsize=14, weight="bold")
    fig.text(
        0.5,
        -0.02,
        "Each point is a nominal configuration. Stability thresholds are review aids "
        "and do not alter evidence acceptance.",
        ha="center",
        color=MUTED,
        fontsize=8.5,
    )
    save_figure(
        fig,
        output_dir,
        "tail_and_block_stability",
        title="Tail concentration and seed-block stability",
    )
    plt.close(fig)


def write_json(path: Path, value: Mapping[str, object]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def write_recursive_checksums(directory: Path) -> None:
    manifest_path = directory / "SHA256SUMS"
    paths = sorted(
        (
            path
            for path in directory.rglob("*")
            if path.is_file() and path != manifest_path
        ),
        key=lambda path: path.relative_to(directory).as_posix(),
    )
    with manifest_path.open("w", encoding="utf-8") as stream:
        for path in paths:
            name = path.relative_to(directory).as_posix()
            stream.write(f"{sha256_file(path)}  {name}\n")


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


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
        if not analysis_dir.is_dir():
            raise PlotInputError(f"analysis directory does not exist: {analysis_dir}")
        if output_dir.exists():
            raise PlotInputError(f"refusing to overwrite figure directory: {output_dir}")
        if is_within(output_dir, analysis_dir):
            raise PlotInputError(
                "figure output must be outside the checksum-bound analysis directory"
            )
        recorded = verify_checksum_manifest(analysis_dir)
        analysis_config = validate_analysis_config(analysis_dir, recorded)

        try:
            import matplotlib  # type: ignore[import-not-found]

            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PlotInputError(
                "plotting analysis-v2 requires matplotlib; the core analysis remains "
                "valid without this optional plotting dependency"
            ) from exc
        configure_matplotlib(matplotlib)

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        plot_pooled_production(
            plt, analysis_dir / "pooled_production.csv", temp_dir
        )
        plot_observed_vs_standardized(
            plt, analysis_dir / "standardized_response.csv", temp_dir
        )
        plot_primary_decomposition(
            plt,
            analysis_dir / "primary_contrasts.csv",
            analysis_dir / "configuration_estimates.csv",
            analysis_dir / "pooled_production.csv",
            temp_dir,
        )
        plot_absorber_equivalence(
            plt, analysis_dir / "absorber_equivalence.csv", temp_dir
        )
        plot_tail_and_block_stability(
            plt,
            analysis_dir / "distribution_diagnostics.csv",
            analysis_dir / "seed_block_stability.csv",
            temp_dir,
        )

        figure_outputs = {
            basename: {"png": f"{basename}.png", "pdf": f"{basename}.pdf"}
            for basename in FIGURE_BASENAMES
        }
        plot_config = {
            "schema_version": PLOT_SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_analysis_directory": str(analysis_dir),
            "source_analysis_schema_version": analysis_config["schema_version"],
            "source_analysis_sha256s_sha256": sha256_file(
                analysis_dir / "SHA256SUMS"
            ),
            "source_analysis_files": dict(sorted(recorded.items())),
            "campaign_id": analysis_config["campaign_id"],
            "plan_hash": analysis_config["plan_hash"],
            "simulation_git_commit": analysis_simulation_commit(analysis_config),
            "accepted_statistical_evidence": analysis_config[
                "accepted_statistical_evidence"
            ],
            "plotter": {
                "implementation": "hpc/osc/plot_steel_module_analysis_v2.py",
                "sha256": sha256_file(Path(__file__).resolve()),
                "python_version": sys.version.split()[0],
                "matplotlib_version": str(matplotlib.__version__),
                "backend": str(matplotlib.get_backend()),
            },
            "rendering": {
                "formats": ["png", "pdf"],
                "png_dpi": 200,
                "palette": {
                    "blue": BLUE,
                    "orange": ORANGE,
                    "olive": OLIVE,
                    "pink": PINK,
                    "ink": INK,
                },
                "reference_absorber_mm": REFERENCE_ABSORBER_MM,
                "confidence_interval": "95% bootstrap percentile interval",
                "equivalence_review_bands": [0.05, 0.10],
            },
            "outputs": figure_outputs,
        }
        write_json(temp_dir / "plot_config.json", plot_config)
        write_recursive_checksums(temp_dir)
        expected_figure_files = tuple(
            filename
            for basename in FIGURE_BASENAMES
            for filename in (f"{basename}.png", f"{basename}.pdf")
        ) + ("plot_config.json",)
        verify_checksum_manifest(temp_dir, required=expected_figure_files)
        os.replace(temp_dir, output_dir)
        temp_dir = None
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        print(f"Cannot plot steel-module analysis-v2: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)

    print(f"Rendered steel-module analysis-v2 figures into {output_dir}")
    print("Formats: PNG and PDF; input and figure checksums verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
