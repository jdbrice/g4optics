#!/usr/bin/env python3
"""Export neutral PNG/PDF figures from the completed, accepted first-scan result.

Usage: python plot.py --result analysis/result.json --result-sha256 SHA256 --output figures
Install the separate requirements-plot.txt in an isolated plotting environment.
This program never reads ROOT files, resamples events, or reruns the analysis.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile

LAYOUTS = ("back-four", "back-two", "back-center", "edge-two")
THICKNESSES = (4, 8, 12, 16, 20, 24)
SENSORS = {"back-four": 4, "back-two": 2, "back-center": 1, "edge-two": 2}
PRIMARY_COVERAGE = 1 - .05 / 18
STYLE = {
    "back-four": ("#0072B2", "o", "-", "Back: four"),
    "back-two": ("#B8860B", "s", "--", "Back: diagonal two"),
    "back-center": ("#D55E00", "^", "-.", "Back: center one"),
    "edge-two": ("#4A4A4A", "D", ":", "Side: two (+X)"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def hex_hash(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def interval(metric, coverage, label, nonnegative=False, fraction=False):
    require(isinstance(metric, dict), label + ": interval record missing")
    point, bounds = metric.get("estimate"), metric.get("percentile_interval")
    require(finite(point) and isinstance(bounds, list) and len(bounds) == 2
            and all(finite(value) for value in bounds), label + ": finite estimate and bounds required")
    require(bounds[0] <= bounds[1], label + ": inverted interval")
    require(metric.get("invalid_bootstrap_replicates") == 0, label + ": invalid bootstrap replicates")
    require(finite(metric.get("nominal_individual_coverage")) and
            math.isclose(metric["nominal_individual_coverage"], coverage, rel_tol=0, abs_tol=1e-12),
            label + ": wrong interval coverage")
    values = [point, *bounds]
    if nonnegative or fraction:
        require(all(value >= 0 for value in values), label + ": negative value")
    if fraction:
        require(all(value <= 1 for value in values), label + ": fraction exceeds one")
    # Percentile intervals need not contain their point estimate. Drawing uses
    # the endpoints themselves, never an invented symmetric or clipped error.
    return point, bounds[0], bounds[1]


def reject_nonfinite(value):
    if isinstance(value, float):
        require(math.isfinite(value), "nonfinite numeric value in result")
    elif isinstance(value, dict):
        for child in value.values(): reject_nonfinite(child)
    elif isinstance(value, list):
        for child in value: reject_nonfinite(child)


def validate_result(result):
    require(result.get("schema_version") == "steel-layout-first-scan-result-v1" and
            result.get("status") == "complete-fixed-first-sample", "completed first-scan result required")
    require(result.get("events") == 24000 and result.get("accepted_tasks") == 240 and
            result.get("primary_contrasts") == 18, "complete 24,000-event / 240-task / 18-contrast scan required")
    require(result.get("bootstrap_replicates") == 10000 and result.get("analysis_seed") == 2026100209,
            "unexpected frozen bootstrap policy")
    require(result.get("precision_guaranteed") is False and
            result.get("additional_events_authorized_by_analysis") is False, "unexpected precision/authorization claim")
    require(hex_hash(result.get("approved_source_commit"), 40), "full simulation source commit required")
    identity = result.get("simulation_identity", {})
    require(identity.get("source_commit") == result["approved_source_commit"] and
            identity.get("geant4_version") == "11.4.2" and identity.get("architecture") == "x86_64" and
            identity.get("run_manager") == "Serial", "simulation identity mismatch")
    for key in ("executable_sha256", "sif_sha256", "dataset_manifest_sha256"):
        require(hex_hash(identity.get(key)), "invalid simulation identity hash: " + key)
    for key in ("manifest_sha256", "analysis_program_sha256", "layout_audit_sha256"):
        require(hex_hash(result.get(key)), "invalid result provenance hash: " + key)
    reject_nonfinite(result)
    configurations = result.get("configurations", [])
    require(len(configurations) == 24, "exactly 24 configurations required")
    by_config = {}
    for row in configurations:
        thickness, layout = row["tile_thickness_mm"], row["layout"]
        key = (thickness, layout)
        require(type(thickness) is int and thickness in THICKNESSES and layout in LAYOUTS and
                key not in by_config and row.get("events") == 1000, "duplicate/incomplete configuration")
        stats = row["statistics"]
        mean_d = interval(stats["D"], .95, "D", nonnegative=True)
        area_d = interval(stats["D_per_neutron_per_mm2"], .95, "area-normalized D", nonnegative=True)
        zero_d = interval(stats["zero_D"], .95, "zero response", fraction=True)
        area = 10 * SENSORS[layout] * 2.4 * 2.4
        require(finite(stats.get("nominal_total_collection_face_area_mm2")) and
                math.isclose(stats["nominal_total_collection_face_area_mm2"], area, rel_tol=1e-12),
                "wrong nominal SiPM face area")
        require(all(math.isclose(x / area, y, rel_tol=1e-11, abs_tol=1e-12)
                    for x, y in zip(mean_d, area_d)), "area-normalized interval differs from source D / area")
        distribution = stats["response_distribution_descriptive"]
        fields = ("median", "p90", "p99", "maximum", "sample_std", "coefficient_of_variation",
                  "maximum_event_share", "top_1_percent_event_share")
        require(all(finite(distribution.get(name)) and distribution[name] >= 0 for name in fields),
                "invalid response-distribution diagnostics")
        require(distribution["median"] <= distribution["p90"] <= distribution["p99"] <= distribution["maximum"],
                "unordered response quantiles")
        require(0 <= distribution["maximum_event_share"] <= distribution["top_1_percent_event_share"] <= 1,
                "invalid tail shares")
        zeros = distribution.get("zero_events")
        require(type(zeros) is int and 0 <= zeros <= 1000 and math.isclose(zeros / 1000, zero_d[0], abs_tol=1e-12),
                "zero-response event count disagrees with its estimate")
        blocks = distribution.get("block_means_D", [])
        require(len(blocks) == 10 and all(finite(x) and x >= 0 for x in blocks) and
                math.isclose(sum(blocks) / 10, mean_d[0], rel_tol=1e-11, abs_tol=1e-12), "invalid 10-block means")
        by_config[key] = row
    expected = {(t, layout) for t in THICKNESSES for layout in LAYOUTS}
    require(set(by_config) == expected, "incomplete configuration matrix")
    comparisons = result.get("comparisons", [])
    require(len(comparisons) == 18, "exactly 18 primary contrasts required")
    by_comparison = {}
    for row in comparisons:
        key = (row["tile_thickness_mm"], row["layout"])
        require(key in expected and key[1] != "edge-two" and key not in by_comparison
                and row.get("reference") == "edge-two", "duplicate or wrong primary contrast")
        contrast = interval(row["absolute_difference_D"], PRIMARY_COVERAGE, "primary contrast")
        descriptive = interval(row["absolute_difference_D_descriptive_95"], .95, "descriptive contrast")
        relative = interval(row["relative_difference_descriptive_95"], .95, "relative descriptive contrast")
        back = by_config[key]["statistics"]["D"]["estimate"]
        edge = by_config[(key[0], "edge-two")]["statistics"]["D"]["estimate"]
        require(math.isclose(contrast[0], back-edge, rel_tol=1e-11, abs_tol=1e-10) and
                math.isclose(descriptive[0], contrast[0], rel_tol=1e-11, abs_tol=1e-10), "contrast estimate mismatch")
        require(edge > 0 and math.isclose(relative[0], back/edge-1, rel_tol=1e-11, abs_tol=1e-12),
                "relative contrast estimate mismatch")
        by_comparison[key] = row
    inventory = result.get("input_inventory", [])
    require(len(inventory) == 240, "240 accepted input evidence records required")
    ids, seed_values = set(), []
    expected_ids = {f"t{t:02d}-{layout}-b{block:02d}" for t in THICKNESSES for layout in LAYOUTS for block in range(10)}
    for record in inventory:
        task_id = record.get("task_id")
        require(task_id in expected_ids and task_id not in ids, "duplicate or missing accepted task")
        ids.add(task_id)
        seeds = record.get("seeds", [])
        require(len(seeds) == 2 and all(type(seed) is int and 1 <= seed <= 2147483646 for seed in seeds), "invalid task seeds")
        seed_values.extend(seeds)
        scheduler = record.get("scheduler", {})
        require(scheduler.get("state") == "COMPLETED" and scheduler.get("exit_code") == "0:0" and
                scheduler.get("cpus") == 1 and str(scheduler.get("job_id", "")), "task lacks accepted scheduler completion")
        evidence = record.get("audit", {})
        require(evidence.get("status") == "passed" and evidence.get("events") == 100, "task lacks passed 100-event audit")
        require(hex_hash(record.get("receipt_sha256")), "missing accepted receipt hash")
        for name in ("root", "log", "summary"):
            require(hex_hash(record.get("files", {}).get(name, {}).get("sha256")), "missing accepted artifact hash")
    require(ids == expected_ids and len(set(seed_values)) == 480, "accepted inventory or seed coverage mismatch")
    return by_config, by_comparison


def load_result(path, expected_hash):
    require(hex_hash(expected_hash), "explicit 64-character result SHA-256 required")
    require(sha256(path) == expected_hash, "result SHA-256 mismatch")
    result = json.loads(Path(path).read_text())
    validate_result(result)
    return result


def export_tables(result, output):
    summary, blocks = [], []
    configurations = sorted(result["configurations"], key=lambda row: (row["tile_thickness_mm"], LAYOUTS.index(row["layout"])))
    for row in configurations:
        stats = row["statistics"]
        record = {"tile_thickness_mm": row["tile_thickness_mm"], "layout": row["layout"], "events": row["events"],
                  "nominal_total_collection_face_area_mm2": stats["nominal_total_collection_face_area_mm2"]}
        for name in ("D", "D_per_neutron_per_mm2", "zero_D"):
            metric = stats[name]
            record.update({name: metric["estimate"], name+"_lower_95": metric["percentile_interval"][0],
                           name+"_upper_95": metric["percentile_interval"][1]})
        distribution = stats["response_distribution_descriptive"]
        record.update({key: value for key, value in distribution.items() if key != "block_means_D"})
        summary.append(record)
        blocks.extend({"tile_thickness_mm": row["tile_thickness_mm"], "layout": row["layout"],
                       "block_id": block, "events": 100, "mean_D": value}
                      for block, value in enumerate(distribution["block_means_D"]))
    contrasts = []
    for row in sorted(result["comparisons"], key=lambda row: (row["tile_thickness_mm"], LAYOUTS.index(row["layout"]))):
        record = {"tile_thickness_mm": row["tile_thickness_mm"], "layout": row["layout"], "reference": row["reference"]}
        for name in ("absolute_difference_D", "absolute_difference_D_descriptive_95", "relative_difference_descriptive_95"):
            metric = row[name]
            record.update({name: metric["estimate"], name+"_lower": metric["percentile_interval"][0],
                           name+"_upper": metric["percentile_interval"][1], name+"_coverage": metric["nominal_individual_coverage"]})
        contrasts.append(record)
    for name, records in (("configuration-summary.csv", summary), ("primary-contrasts.csv", contrasts), ("block-means.csv", blocks)):
        with (output / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(records)


def draw_intervals(ax, positions, metrics, style, horizontal=False, connect=True):
    color, marker, line, label = style
    points = [metric["estimate"] for metric in metrics]
    lower = [metric["percentile_interval"][0] for metric in metrics]
    upper = [metric["percentile_interval"][1] for metric in metrics]
    if horizontal:
        ax.hlines(positions, lower, upper, color=color, linewidth=1.5)
        ax.plot(points, positions, linestyle="none", marker=marker, color=color, markersize=6.5)
    else:
        ax.vlines(positions, lower, upper, color=color, linewidth=1.35)
        for x, low, high in zip(positions, lower, upper):
            ax.hlines([low, high], x-.11, x+.11, color=color, linewidth=1.35)
        ax.plot(positions, points, color=color, marker=marker, markersize=6.5,
                linewidth=1.5, linestyle=line if connect else "none", label=label)


def render(result, result_hash, output):
    # A temporary cache keeps plotting state out of both the source and artifacts.
    with tempfile.TemporaryDirectory(prefix="layout-matplotlib-") as cache:
        os.environ["MPLCONFIGDIR"] = cache
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.text import Text
        from matplotlib.ticker import PercentFormatter, MultipleLocator
        from matplotlib import ft2font
        import numpy
        configs = {(row["tile_thickness_mm"], row["layout"]): row for row in result["configurations"]}
        comparisons = {(row["tile_thickness_mm"], row["layout"]): row for row in result["comparisons"]}
        provenance = (f"Source {result['approved_source_commit'][:12]}  |  manifest {result['manifest_sha256'][:12]}"
                      f"  |  result {result_hash[:12]}")
        sample = "N = 1,000 neutrons/configuration (10 x 100); 24,000 total. Pooled whole-event percentile bootstrap, B = 10,000."
        settings = {"font.family": "DejaVu Sans", "font.size": 10.5, "axes.labelsize": 11,
                    "axes.titlesize": 13, "axes.spines.top": False, "axes.spines.right": False,
                    "axes.edgecolor": "#555555", "axes.labelcolor": "#222222", "text.color": "#222222",
                    "xtick.color": "#333333", "ytick.color": "#333333", "figure.facecolor": "white",
                    "axes.facecolor": "white", "savefig.facecolor": "white", "pdf.fonttype": 42}

        def decorate(fig, title, subtitle, footer):
            fig.suptitle(title, x=.5, y=.978, fontsize=16, fontweight="semibold")
            fig.text(.5, .923, subtitle, ha="center", fontsize=10.5)
            fig.text(.08, .075, sample, fontsize=8)
            fig.text(.08, .048, footer, fontsize=8)
            fig.text(.08, .021, provenance, fontsize=8, color="#555555")

        def save(fig, name):
            fig.canvas.draw()
            renderer = fig.canvas.get_renderer(); width, height = fig.canvas.get_width_height()
            # Locators retain tick objects beyond the axis view. Matplotlib does
            # not draw those labels, so they are not canvas clipping failures.
            outside_tick_labels = set()
            for axes in fig.axes:
                for axis in (axes.xaxis, axes.yaxis):
                    low, high = sorted(axis.get_view_interval())
                    for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                        if not low <= tick.get_loc() <= high:
                            outside_tick_labels.update((id(tick.label1), id(tick.label2)))
            for text in fig.findobj(match=Text):
                if id(text) not in outside_tick_labels and text.get_visible() and text.get_text().strip():
                    bounds = text.get_window_extent(renderer)
                    require(bounds.x0 >= -1 and bounds.y0 >= -1 and bounds.x1 <= width+1 and bounds.y1 <= height+1,
                            "figure text exceeds canvas: " + text.get_text())
            fig.savefig(output / (name + ".png"), dpi=220, metadata={"Software": "steel-layout-study plot.py"})
            fig.savefig(output / (name + ".pdf"), metadata={"Creator": "steel-layout-study plot.py", "CreationDate": None, "ModDate": None})
            plt.close(fig)

        with plt.rc_context(settings):
            for metric, name, title, ylabel, note in (
                ("D", "response", "Collected light per incident neutron", "Collected photons / neutron",
                 "95% descriptive confidence intervals. SiPM proxy entries; no PDE. Lines connect sampled thicknesses."),
                ("D_per_neutron_per_mm2", "area-normalized-response", "Response per nominal SiPM face area",
                 "Collected photons / neutron\nper nominal SiPM face area (mm²)",
                 "95% descriptive intervals. Area = 10 x sensors/layer x 2.4² mm²; not PDE or a causal placement adjustment.")):
                fig, ax = plt.subplots(figsize=(10.4, 7.0)); fig.subplots_adjust(left=.13, right=.975, bottom=.19, top=.78)
                for layout in LAYOUTS:
                    draw_intervals(ax, THICKNESSES, [configs[(t, layout)]["statistics"][metric] for t in THICKNESSES], STYLE[layout])
                ax.set(xlabel="Scintillator tile thickness (mm)", ylabel=ylabel, xticks=THICKNESSES, xlim=(3,25), ylim=(0,None))
                ax.grid(axis="y", color="#E5E5E5", linewidth=.65); ax.set_axisbelow(True)
                ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(.5,1.015), frameon=False, fontsize=10)
                decorate(fig, title, "Ten steel/tile modules; four layouts; nine fixed 0.5 mm inter-module gaps", note)
                save(fig, name)

            fig, ax = plt.subplots(figsize=(10.4, 9.1)); fig.subplots_adjust(left=.255, right=.975, bottom=.15, top=.875)
            labels = []
            for i, thickness in enumerate(THICKNESSES):
                if i % 2 == 0: ax.axhspan(3*i-.5, 3*i+2.5, color="#F5F5F5", zorder=0)
                for j, layout in enumerate(LAYOUTS[:-1]):
                    position = 3*i+j; labels.append(f"{thickness:2d} mm  |  {STYLE[layout][3]}")
                    draw_intervals(ax, [position], [comparisons[(thickness,layout)]["absolute_difference_D"]], STYLE[layout], horizontal=True)
            ax.axvline(0, color="#333333", linewidth=1.1, linestyle="--", zorder=1)
            ax.set(yticks=range(18), yticklabels=labels, ylim=(17.6,-.6), xlabel="Difference in collected photons / neutron (back layout − side-two)")
            ax.tick_params(axis="y", length=0, labelsize=10); ax.grid(axis="x", color="#E5E5E5", linewidth=.65); ax.set_axisbelow(True)
            low, high = ax.get_xlim(); ax.set_xlim(min(low,0), max(high,0))
            decorate(fig, "Primary contrasts against side-two", "18 preregistered absolute contrasts; positive values favor the named back layout",
                     "99.7222% individual intervals; Bonferroni nominal family-wise 95%. Bootstrap coverage is approximate.")
            save(fig, "primary-contrasts")

            fig, axes = plt.subplots(1,2,figsize=(12.3,7.0)); fig.subplots_adjust(left=.08,right=.975,bottom=.20,top=.72,wspace=.30)
            for layout in LAYOUTS:
                draw_intervals(axes[0], THICKNESSES, [configs[(t,layout)]["statistics"]["zero_D"] for t in THICKNESSES], STYLE[layout])
                color, marker, line, label = STYLE[layout]
                shares = [configs[(t,layout)]["statistics"]["response_distribution_descriptive"]["top_1_percent_event_share"] for t in THICKNESSES]
                axes[1].plot(THICKNESSES,shares,color=color,marker=marker,linestyle=line,linewidth=1.5,markersize=6.5,label=label)
            axes[0].set_title("Zero-response events\n95% descriptive intervals", fontsize=12)
            axes[1].set_title("Collected light from the largest 1% of events\nDescriptive share; no confidence interval", fontsize=12)
            maxima = [max(configs[(t, layout)]["statistics"]["zero_D"]["percentile_interval"][1]
                          for t in THICKNESSES for layout in LAYOUTS),
                      max(configs[(t, layout)]["statistics"]["response_distribution_descriptive"]["top_1_percent_event_share"]
                          for t in THICKNESSES for layout in LAYOUTS)]
            for ax, maximum in zip(axes, maxima):
                upper = min(1, max(.05, math.ceil(maximum * 1.1 / .05) * .05))
                ax.set(xlabel="Tile thickness (mm)", xticks=THICKNESSES,xlim=(3,25),ylim=(0,upper))
                ax.yaxis.set_major_locator(MultipleLocator(.05 if upper <= .35 else .1))
                ax.yaxis.set_major_formatter(PercentFormatter(xmax=1)); ax.grid(axis="y",color="#E5E5E5",linewidth=.65); ax.set_axisbelow(True)
            axes[0].set_ylabel("Fraction of incident neutrons"); axes[1].set_ylabel("Share of total collected photons")
            handles, labels = axes[0].get_legend_handles_labels(); fig.legend(handles,labels,ncol=2,loc="upper center",bbox_to_anchor=(.5,.882),frameon=False,fontsize=10)
            decorate(fig, "Response distribution diagnostics", "Zero responses and the contribution of the ten largest events in each 1,000-event configuration",
                     "Separate percentage scales, both starting at zero. Right panel is descriptive; no observations are omitted or synthesized.")
            save(fig, "response-diagnostics")
        return {"python": platform.python_version(), "platform": platform.platform(), "matplotlib": matplotlib.__version__,
                "numpy": numpy.__version__, "freetype": ft2font.__freetype_version__, "backend": "Agg", "font": "DejaVu Sans"}


def export(result_path, result_hash, output):
    output = Path(output)
    require(not output.exists(), "refusing to overwrite figure output")
    result = load_result(result_path, result_hash)
    output.mkdir(parents=True)
    shutil.copyfile(result_path, output / "source-result.json")
    shutil.copyfile(__file__, output / "plot_program.py")
    requirements = Path(__file__).with_name("requirements-plot.txt")
    if requirements.is_file():
        shutil.copyfile(requirements, output / "requirements-plot.txt")
    export_tables(result, output)
    runtime = render(result, result_hash, output)
    notes = ("Figures use only source-result.json; no events were resampled by the plotter.\n"
             "response: mean collected photons/neutron with descriptive 95% percentile intervals.\n"
             "primary-contrasts: 18 back-minus-side mean differences, individual 99.7222% intervals, nominal Bonferroni family-wise 95%.\n"
             "area-normalized-response: collected photons divided by nominal total SiPM collection-face area; not PDE, intrinsic efficiency, or causal placement separation.\n"
             "response-diagnostics: zero-event fraction (95% interval) and top-ten-event light share (descriptive only).\n"
             "All configurations contain 1,000 events in ten independent 100-event blocks; the frozen analysis pools whole events for 10,000 bootstrap replicates.\n"
             "Bootstrap coverage is approximate; target precision and full optical-history closure are not asserted.\n"
             "Reproduce in an isolated environment using requirements-plot.txt, then:\n"
             f"python plot_program.py --result source-result.json --result-sha256 {result_hash} --output reproduced-figures\n")
    (output / "FIGURES.txt").write_text(notes)
    index = {"schema_version": "steel-layout-first-scan-figures-v1", "status": "rendered-awaiting-visual-review",
             "source_result_sha256": result_hash, "source_commit": result["approved_source_commit"],
             "manifest_sha256": result["manifest_sha256"], "analysis_program_sha256": result["analysis_program_sha256"],
             "plot_program_sha256": sha256(__file__), "events": 24000, "configurations": 24,
             "runtime": runtime, "colors_markers": {layout: list(STYLE[layout]) for layout in LAYOUTS},
             "files": {path.name: sha256(path) for path in sorted(output.iterdir()) if path.is_file()}}
    (output / "artifact-index.json").write_text(json.dumps(index,indent=2,sort_keys=True,allow_nan=False)+"\n")
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result",type=Path,required=True); parser.add_argument("--result-sha256",required=True)
    parser.add_argument("--output",type=Path,required=True); args=parser.parse_args()
    try:
        result = export(args.result,args.result_sha256,args.output)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, "figure export rejected: " + str(error) + "\n")
    print(json.dumps({"status": result["status"],"output":str(args.output),"source_result_sha256":args.result_sha256},indent=2))


if __name__ == "__main__":
    main()
