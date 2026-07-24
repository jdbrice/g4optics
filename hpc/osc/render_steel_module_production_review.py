#!/usr/bin/env python3
"""Render an offline, checksum-bound review of BC-ONLY-S1 analysis."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from plot_steel_module_analysis_v2 import (
    sha256_file,
    verify_checksum_manifest,
    write_recursive_checksums,
)


ANALYSIS_SCHEMA = "steel-module-production-checkpoint-analysis-v1"
REVIEW_SCHEMA = "steel-module-production-checkpoint-review-v1"
STATE_ID = "BC-ONLY-S1"
REQUIRED_ANALYSIS = (
    "endpoint_estimates.csv",
    "distribution_diagnostics.csv",
    "pilot_distribution_diagnostics.csv",
    "block_loo.csv",
    "precision_trajectory.csv",
    "primary_contrast.json",
    "numeric_eligibility.json",
    "analysis_config.json",
    "summary.md",
)
FIGURE_NAMES = (
    "precision_trajectory",
    "block_loo_shifts",
    "tail_diagnostics",
    "endpoint_ratio",
)

INK = "#20262E"
BLUE = "#2864A6"
ORANGE = "#D97706"
RED = "#B42318"
GREEN = "#18794E"
GRID = "#D8DEE8"
MUTED = "#667085"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path: Path, value: Mapping[str, object]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def number(value: object) -> float:
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"review input is not finite: {value!r}")
    return converted


def review_payload(
    analysis_dir: Path,
    config: Mapping[str, object],
    primary: Mapping[str, object],
    eligibility: Mapping[str, object],
    endpoints: Sequence[Mapping[str, str]],
    distributions: Sequence[Mapping[str, str]],
    pilot_distributions: Sequence[Mapping[str, str]],
    loo: Sequence[Mapping[str, str]],
    trajectory: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    if len(endpoints) != 2 or len(loo) != 32 or len(trajectory) < 2:
        raise ValueError("analysis does not have the exact BC-ONLY-S1 review shape")
    sipm_rows = [row for row in distributions if row.get("metric") == "sipm"]
    pilot_sipm_rows = [
        row for row in pilot_distributions if row.get("metric") == "sipm"
    ]
    if len(sipm_rows) != 2 or len(pilot_sipm_rows) != 2:
        raise ValueError("analysis lacks current or pilot endpoint SiPM rows")
    if {row.get("tile_thickness_mm") for row in sipm_rows} != {"4", "24"}:
        raise ValueError("current SiPM diagnostics have the wrong endpoints")
    if {row.get("tile_thickness_mm") for row in pilot_sipm_rows} != {"4", "24"}:
        raise ValueError("pilot SiPM diagnostics have the wrong endpoints")
    program = config.get("program")
    source_finalization = config.get("source_finalization")
    if not isinstance(program, dict) or not isinstance(source_finalization, dict):
        raise ValueError("analysis lacks program or finalization identity")
    maximum = max(loo, key=lambda row: number(row["absolute_relative_shift"]))
    return {
        "schema_version": REVIEW_SCHEMA,
        "state_id": STATE_ID,
        "artifact_role": "derived-human-review-aid",
        "accepted_statistical_evidence": False,
        "source_accepted_statistical_evidence": True,
        "source_analysis_directory": str(analysis_dir),
        "source_analysis_sha256s_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
        "task_count": 32,
        "event_count": 8000,
        "checkpoint_identity": {
            "checkpoint_hash": config.get("checkpoint_hash"),
            "task_set_hash": config.get("task_set_hash"),
            "checkpoint_task_index_sha256": config.get(
                "checkpoint_task_index_sha256"
            ),
            "root_set_hash": config.get("root_set_hash"),
            "source_checkpoint_sha256s_sha256": config.get(
                "source_checkpoint_sha256s_sha256"
            ),
        },
        "program_identity": dict(program),
        "source_finalization": dict(source_finalization),
        "source_event_audit_sha256": config.get("source_event_audit_sha256"),
        "primary_contrast": dict(primary),
        "numeric_eligibility": dict(eligibility),
        "endpoint_estimates": [dict(row) for row in endpoints],
        "sipm_tail_diagnostics": [dict(row) for row in sipm_rows],
        "current_sipm_tail_diagnostics": [dict(row) for row in sipm_rows],
        "pilot_sipm_tail_diagnostics": [dict(row) for row in pilot_sipm_rows],
        "maximum_loo": dict(maximum),
        "precision_trajectory": [dict(row) for row in trajectory],
        "tail_disposition_required": True,
        "tail_disposition_choices": [
            "no-material-worsening",
            "material-worsening",
            "unable-to-determine",
        ],
        "decision_boundary": {
            "analysis_does_not_select_decision": True,
            "review_does_not_unlock_child": True,
            "review_does_not_submit_slurm": True,
        },
    }


def acquire_publish_lock(output_dir: Path) -> Path:
    lock = output_dir.with_name(f".{output_dir.name}.publish.lock")
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError(f"review publish lock already exists: {lock}") from exc
    return lock


def style_axis(ax: object) -> None:
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, alpha=0.9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors=INK)


def save_pair(fig: object, figures_dir: Path, basename: str) -> None:
    fig.savefig(figures_dir / f"{basename}.png", dpi=200, bbox_inches="tight")
    fig.savefig(figures_dir / f"{basename}.pdf", bbox_inches="tight")


def build_figures(
    *,
    plt: object,
    payload: Mapping[str, object],
    loo_rows: Sequence[Mapping[str, str]],
    trajectory_rows: Sequence[Mapping[str, str]],
    figures_dir: Path,
    report_pdf: Path,
    PdfPages: object,
) -> None:
    primary = payload["primary_contrast"]
    current_tails = payload["current_sipm_tail_diagnostics"]
    pilot_tails = payload["pilot_sipm_tail_diagnostics"]
    figures: list[object] = []

    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    observed_x = [int(row["events_per_endpoint"]) for row in trajectory_rows]
    observed_y = [100 * number(row["relative_half_width"]) for row in trajectory_rows]
    projected_y = [
        100 * number(row["projected_relative_half_width"])
        for row in trajectory_rows
    ]
    ax.plot(observed_x, observed_y, color=BLUE, marker="o", linewidth=2, label="Observed h")
    ax.plot(
        observed_x,
        projected_y,
        color=ORANGE,
        marker="s",
        linestyle="--",
        linewidth=1.8,
        label="1/sqrt(N) projection",
    )
    ax.axhline(10, color=RED, linestyle=":", linewidth=2, label="10% target")
    ax.set_xlabel("Events per endpoint configuration")
    ax.set_ylabel("Relative 95% CI half-width h (%)")
    ax.set_title("Precision trajectory: sealed pilot to BC-ONLY-S1")
    ax.legend(frameon=False)
    style_axis(ax)
    save_pair(fig, figures_dir, "precision_trajectory")
    figures.append(fig)

    fig, ax = plt.subplots(figsize=(9.0, 5.0))
    for thickness, marker, color in ((4, "o", BLUE), (24, "s", ORANGE)):
        selected = [
            (index, row)
            for index, row in enumerate(loo_rows)
            if int(row["omitted_tile_thickness_mm"]) == thickness
        ]
        ax.scatter(
            [index + 1 for index, _ in selected],
            [100 * number(row["absolute_relative_shift"]) for _, row in selected],
            marker=marker,
            color=color,
            label=f"Omit {thickness} mm block",
            zorder=3,
        )
    ax.axhline(10, color=GREEN, linestyle="--", linewidth=1.8, label="10% success band")
    ax.axhline(20, color=RED, linestyle=":", linewidth=2, label="20% continue ceiling")
    ax.set_xlabel("Leave-one-250-event-block-out evaluation")
    ax.set_ylabel("Absolute relative ratio shift (%)")
    ax.set_title("Maximum leave-one-block-out shift (LOO)")
    ax.legend(frameon=False, ncol=2)
    style_axis(ax)
    save_pair(fig, figures_dir, "block_loo_shifts")
    figures.append(fig)

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    by_sample = {
        "Pilot": {int(row["tile_thickness_mm"]): row for row in pilot_tails},
        "Current": {int(row["tile_thickness_mm"]): row for row in current_tails},
    }
    ordered = [
        (sample, thickness, by_sample[sample][thickness])
        for thickness in (4, 24)
        for sample in ("Pilot", "Current")
    ]
    labels = [f"{sample}\n{thickness} mm" for sample, thickness, _ in ordered]
    x = list(range(len(labels)))
    width = 0.24
    values = {
        "Zero fraction": [100 * number(row["zero_fraction"]) for _, _, row in ordered],
        "Top 1% share": [100 * number(row["top_1pct_sum_fraction"]) for _, _, row in ordered],
        "Top 5% share": [100 * number(row["top_5pct_sum_fraction"]) for _, _, row in ordered],
    }
    for offset, (label, vals, color, hatch) in enumerate(
        zip(values, values.values(), (MUTED, BLUE, ORANGE), ("//", "..", "xx"))
    ):
        ax.bar(
            [value + (offset - 1) * width for value in x],
            vals,
            width,
            label=label,
            color=color,
            hatch=hatch,
            edgecolor="white",
        )
    ax.set_xticks(x, labels)
    ax.set_ylabel("Fraction (%)")
    ax.set_title("Sealed pilot versus new production tail diagnostics")
    ax.legend(frameon=False)
    style_axis(ax)
    save_pair(fig, figures_dir, "tail_diagnostics")
    figures.append(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ratio = number(primary["ratio"])
    low = number(primary["ci95_low"])
    high = number(primary["ci95_high"])
    ax.errorbar(
        [ratio],
        [0],
        xerr=[[ratio - low], [high - ratio]],
        fmt="o",
        color=BLUE,
        ecolor=BLUE,
        capsize=6,
        markersize=8,
        label="Observed net 24/4 (95% CI)",
    )
    ax.axvline(1, color=INK, linestyle="--", linewidth=1.5, label="Unity")
    ax.set_yticks([0], ["Back center"])
    ax.set_xlabel("24 mm / 4 mm observed net SiPM response")
    ax.set_title("BC-ONLY-S1 endpoint response ratio")
    ax.legend(frameon=False, loc="upper right")
    style_axis(ax)
    save_pair(fig, figures_dir, "endpoint_ratio")
    figures.append(fig)

    with PdfPages(report_pdf) as pdf:
        cover = plt.figure(figsize=(8.27, 11.69))
        cover.text(0.08, 0.93, "Steel Module BC-ONLY-S1 Review", fontsize=20, color=INK)
        cover.text(
            0.08,
            0.875,
            "Human review aid — no automatic decision or submission",
            fontsize=11,
            color=MUTED,
        )
        choices = ", ".join(payload["numeric_eligibility"]["numeric_eligible_decisions"])
        maximum = payload["maximum_loo"]
        text = (
            f"Program: {payload['program_identity'].get('program_id')}\n"
            f"Checkpoint: {payload['checkpoint_identity'].get('checkpoint_hash')}\n"
            f"Tasks / events: 32 / 8,000\n\n"
            f"Observed net 24/4: {ratio:.6g}\n"
            f"Bootstrap 95% CI: [{low:.6g}, {high:.6g}]\n"
            f"Relative half-width h: {100 * number(primary['relative_half_width']):.2f}%\n"
            f"Maximum LOO shift: {100 * number(maximum['absolute_relative_shift']):.2f}%\n"
            f"Numeric choices: {choices}\n\n"
            "Maximum leave-one-block-out shift (LOO) means recomputing the 24/4 "
            "ratio after omitting each contributing 250-event block, then taking "
            "the largest absolute relative change. It is a stability diagnostic, "
            "not permission to remove data.\n\n"
            "A reviewer must record whether the newly sampled tail materially worsened."
        )
        cover.text(0.08, 0.79, text, va="top", fontsize=11, color=INK, wrap=True, linespacing=1.5)
        pdf.savefig(cover, bbox_inches="tight")
        plt.close(cover)
        for figure in figures:
            pdf.savefig(figure, bbox_inches="tight")
    for figure in figures:
        plt.close(figure)


def html_document(payload: Mapping[str, object]) -> str:
    primary = payload["primary_contrast"]
    eligibility = payload["numeric_eligibility"]
    maximum = payload["maximum_loo"]
    program = payload["program_identity"]
    checkpoint = payload["checkpoint_identity"]
    choices = ", ".join(eligibility["numeric_eligible_decisions"])
    command = (
        "python3 hpc/osc/record_steel_module_progression.py "
        f"--analysis-dir {payload['source_analysis_directory']} "
        "--review-dir <THIS_REVIEW_DIRECTORY> "
        "--tail-disposition <CHOICE> --decision <CHOICE> "
        "--reviewer <REVIEWER> --rationale-file <FILE> --check-only"
    )
    cards = "".join(
        f'<div class="card"><div class="label">{html.escape(label)}</div><div class="value">{html.escape(value)}</div></div>'
        for label, value in (
            ("Evidence", "Checksum-valid core analysis"),
            ("Program", str(program.get("program_id"))),
            ("Checkpoint", str(checkpoint.get("checkpoint_hash"))[:16] + "…"),
            ("Tasks / events", "32 / 8,000"),
            ("Observed net 24/4", f"{number(primary['ratio']):.6g}"),
            (
                "95% interval",
                f"[{number(primary['ci95_low']):.6g}, {number(primary['ci95_high']):.6g}]",
            ),
            ("Relative half-width h", f"{100 * number(primary['relative_half_width']):.2f}%"),
            ("Numeric choices", choices),
        )
    )
    tail_rows = []
    for sample_key, sample_label in (
        ("pilot_sipm_tail_diagnostics", "Sealed pilot"),
        ("current_sipm_tail_diagnostics", "New production"),
    ):
        for row in sorted(
            payload[sample_key], key=lambda item: int(item["tile_thickness_mm"])
        ):
            tail_rows.append(
                "<tr>"
                f"<td>{html.escape(sample_label)}</td>"
                f"<td>{html.escape(str(row['tile_thickness_mm']))} mm</td>"
                f"<td>{100 * number(row['zero_fraction']):.2f}%</td>"
                f"<td>{100 * number(row['top_1pct_sum_fraction']):.2f}%</td>"
                f"<td>{100 * number(row['top_5pct_sum_fraction']):.2f}%</td>"
                f"<td>{html.escape(str(row['max']))}</td>"
                f"<td><code>{html.escape(str(row['maximum_logical_task_id']))}</code>"
                f" / block {html.escape(str(row['maximum_seed_block']))}"
                f" / entry {html.escape(str(row['maximum_root_entry']))}</td>"
                "</tr>"
            )
    tail_table = "".join(tail_rows)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Steel Module BC-ONLY-S1 Review</title>
<style>
body{{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f5f7fa;color:{INK};line-height:1.5}}
main{{max-width:1050px;margin:auto;padding:32px}} h1{{margin-bottom:4px}} .subtitle{{color:{MUTED};margin-top:0}}
.banner{{background:#fff4e5;border-left:5px solid {ORANGE};padding:16px 20px;margin:24px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}}
.card,section{{background:white;border:1px solid #e1e6ef;border-radius:8px;padding:18px;margin:16px 0}}
.label{{color:{MUTED};font-size:.9rem}} .value{{font-size:1.2rem;font-weight:700;margin-top:4px}}
img{{max-width:100%;height:auto;border:1px solid #e1e6ef}} code{{white-space:pre-wrap;word-break:break-word}}
.definition{{border-left:4px solid {BLUE};padding-left:16px}} .danger{{color:{RED};font-weight:700}}
table{{border-collapse:collapse;width:100%;margin:16px 0}} th,td{{border:1px solid #d9e0ea;padding:8px;text-align:left;vertical-align:top}} th{{background:#eef3f8}}
</style></head><body><main>
<h1>Steel Module BC-ONLY-S1 Review</h1><p class="subtitle">Offline human review aid — it cannot write a decision, unlock a child, or invoke Slurm.</p>
<div class="banner"><strong>Mandatory human step:</strong> classify the new tail as <code>no-material-worsening</code>, <code>material-worsening</code>, or <code>unable-to-determine</code>. The latter two permit only <code>pause-review</code>.</div>
<div class="grid">{cards}</div>
<section><h2>Precision</h2><img src="figures/precision_trajectory.png" alt="Relative confidence interval half-width versus event count, including the ten-percent target"></section>
<section><h2>Block stability</h2><p class="definition"><strong>Maximum leave-one-block-out shift (LOO)</strong> recomputes the 24/4 ratio after omitting each contributing 250-event block and reports the largest absolute relative change. It is not a confidence interval or permission to remove data.</p><p>Maximum observed shift: <strong>{100 * number(maximum['absolute_relative_shift']):.2f}%</strong>, omitting {html.escape(str(maximum['omitted_tile_thickness_mm']))} mm block {html.escape(str(maximum['omitted_seed_block']))}.</p><img src="figures/block_loo_shifts.png" alt="All thirty-two block omission shifts with ten and twenty-percent reference bands"></section>
<section><h2>Sealed pilot versus new production tail</h2><p>The rows below are the mandatory baseline for deciding whether the newly sampled extreme event or tail concentration materially worsened.</p><table><thead><tr><th>Sample</th><th>Endpoint</th><th>Zero</th><th>Top 1% share</th><th>Top 5% share</th><th>Maximum</th><th>Maximum provenance</th></tr></thead><tbody>{tail_table}</tbody></table><img src="figures/tail_diagnostics.png" alt="Side-by-side sealed-pilot and new-production zero fractions and top-tail shares"></section>
<section><h2>Endpoint ratio</h2><img src="figures/endpoint_ratio.png" alt="Observed net response ratio with bootstrap confidence interval and unity line"></section>
<section><h2>Decision checklist</h2><ol><li>Confirm identities and checksums are valid.</li><li>Review every LOO shift and the maximum block.</li><li>Compare zero fractions, top-tail shares, and maximum events with the sealed pilot.</li><li>Record the mandatory tail disposition.</li><li>Select only a decision allowed by the combined numeric and human review.</li></ol><p class="danger">Do not submit FIXED or BC-S2 from this page.</p><h3>Copyable check-only recorder command</h3><code>{html.escape(command)}</code></section>
<section><h2>Provenance</h2><details><summary>Show source identity</summary><pre>{html.escape(json.dumps(payload, indent=2, sort_keys=True))}</pre></details></section>
</main></body></html>"""


def main() -> int:
    args = parse_args()
    analysis_dir = args.analysis_dir.expanduser().resolve()
    output_dir = analysis_dir.with_name(f"{analysis_dir.name}-review")
    temp_dir: Path | None = None
    publish_lock: Path | None = None
    try:
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite review directory: {output_dir}")
        recorded = verify_checksum_manifest(analysis_dir, required=REQUIRED_ANALYSIS)
        source_manifest_before = sha256_file(analysis_dir / "SHA256SUMS")
        config = load_json(analysis_dir / "analysis_config.json")
        primary = load_json(analysis_dir / "primary_contrast.json")
        eligibility = load_json(analysis_dir / "numeric_eligibility.json")
        if config.get("schema_version") != ANALYSIS_SCHEMA:
            raise ValueError("unsupported production analysis schema")
        if config.get("state_id") != STATE_ID or config.get("accepted_statistical_evidence") is not True:
            raise ValueError("review requires accepted BC-ONLY-S1 core evidence")
        if eligibility.get("state_id") != STATE_ID or eligibility.get("valid_evidence") is not True:
            raise ValueError("numeric eligibility is not valid BC-ONLY-S1 evidence")
        endpoints = read_csv(analysis_dir / "endpoint_estimates.csv")
        distributions = read_csv(analysis_dir / "distribution_diagnostics.csv")
        pilot_distributions = read_csv(
            analysis_dir / "pilot_distribution_diagnostics.csv"
        )
        loo = read_csv(analysis_dir / "block_loo.csv")
        trajectory = read_csv(analysis_dir / "precision_trajectory.csv")
        payload = review_payload(
            analysis_dir,
            config,
            primary,
            eligibility,
            endpoints,
            distributions,
            pilot_distributions,
            loo,
            trajectory,
        )
        try:
            import matplotlib  # type: ignore[import-not-found]

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt  # type: ignore[import-not-found]
            from matplotlib.backends.backend_pdf import PdfPages  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("review rendering requires matplotlib") from exc
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        figures_dir = temp_dir / "figures"
        figures_dir.mkdir()
        build_figures(
            plt=plt,
            payload=payload,
            loo_rows=loo,
            trajectory_rows=trajectory,
            figures_dir=figures_dir,
            report_pdf=temp_dir / "review_report.pdf",
            PdfPages=PdfPages,
        )
        write_json(temp_dir / "review_data.json", payload)
        provenance = {
            "schema_version": f"{REVIEW_SCHEMA}-provenance-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_analysis_sha256s_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
            "source_analysis_files": dict(sorted(recorded.items())),
            "renderer_sha256": sha256_file(Path(__file__).resolve()),
            "python_version": sys.version.split()[0],
            "matplotlib_version": str(matplotlib.__version__),
            "backend": str(matplotlib.get_backend()),
            "network_resources": False,
            "write_controls": False,
            "automatic_submission": False,
        }
        write_json(temp_dir / "review_provenance.json", provenance)
        (temp_dir / "index.html").write_text(html_document(payload), encoding="utf-8")
        write_recursive_checksums(temp_dir)
        required = (
            "index.html",
            "review_report.pdf",
            "review_data.json",
            "review_provenance.json",
            *tuple(
                f"figures/{name}.{suffix}"
                for name in FIGURE_NAMES
                for suffix in ("png", "pdf")
            ),
        )
        verify_checksum_manifest(temp_dir, required=required)
        if any((temp_dir / name).stat().st_size == 0 for name in required):
            raise ValueError("review renderer produced an empty artifact")
        publish_lock = acquire_publish_lock(output_dir)
        if output_dir.exists() or output_dir.is_symlink():
            raise ValueError(f"refusing to overwrite review directory: {output_dir}")
        recorded_after = verify_checksum_manifest(
            analysis_dir, required=REQUIRED_ANALYSIS
        )
        if (
            recorded_after != recorded
            or sha256_file(analysis_dir / "SHA256SUMS") != source_manifest_before
        ):
            raise ValueError("core analysis changed during review rendering")
        os.rename(temp_dir, output_dir)
        temp_dir = None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot render steel-module production review: {exc}", file=sys.stderr)
        return 1
    finally:
        if publish_lock is not None and publish_lock.exists():
            publish_lock.rmdir()
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)
    print(f"Rendered offline BC-ONLY-S1 review into {output_dir}")
    print("Artifacts: index.html, review_report.pdf, 4 PNG/PDF figure pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
