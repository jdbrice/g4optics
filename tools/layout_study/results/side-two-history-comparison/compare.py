#!/usr/bin/env python3
"""Replot the published descriptive side-two overlay from its frozen JSON only.

Example: python compare.py --comparison comparison.json --comparison-sha256 SHA --output new-figures
Uses the existing point estimates and interval endpoints. No ROOT, NPZ,
bootstrap calculation, simulation, or original absolute source paths are used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
import tempfile

SAMPLES = ("July zero-gap", "Current 0.5-mm gap")
THICKNESSES = (4, 8, 12, 16, 20, 24)
METRICS = ("production", "collection", "net_response")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate(comparison):
    rows = comparison.get("rows", [])
    require(len(rows) == 12, "exactly two samples at all six thicknesses required")
    require(type(comparison.get("historical_raw_ROOT_rechecked_here")) is bool,
            "explicit historical raw-evidence status required")
    seen = set()
    for row in rows:
        key = (row["sample"], row["thickness_mm"])
        require(key[0] in SAMPLES and type(key[1]) is int and key[1] in THICKNESSES and key not in seen,
                "duplicate or unknown sample/thickness")
        seen.add(key)
        expected_n = 1000 if key[0] == SAMPLES[1] else 600 if key[1] in (4, 12, 20) else 400
        require(type(row.get("events")) is int and row["events"] == expected_n, "unexpected sample size")
        for metric in METRICS:
            values = row.get(metric)
            require(isinstance(values, list) and len(values) == 3 and all(finite(x) and x >= 0 for x in values),
                    "finite nonnegative estimate/lower/upper required: " + metric)
            require(values[1] <= values[2], "inverted interval: " + metric)
        require(row["production"][0] > 0 and all(x <= 1 for x in row["collection"]), "invalid collection denominator or fraction")
        for metric, limits in (("production", (0,255000)), ("collection", (.007,.021)), ("net_response", (0,2350))):
            require(all(limits[0] <= x <= limits[1] for x in row[metric]), "value outside published axis range: " + metric)
        require(math.isclose(row["production"][0] * row["collection"][0], row["net_response"][0], rel_tol=1e-12),
                "net-response point identity P times eta equals R failed")
    require(seen == {(sample, t) for sample in SAMPLES for t in THICKNESSES}, "incomplete overlay")
    for field in ("historical_csv_sha256", "current_result_sha256"):
        value = comparison.get(field, "")
        require(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value), "invalid " + field)
    return sorted(rows, key=lambda row: (row["thickness_mm"], SAMPLES.index(row["sample"])))


def replot(source, expected_hash, output):
    require(not output.exists(), "refusing to overwrite output directory")
    require(sha256(source) == expected_hash, "comparison JSON SHA-256 mismatch")
    comparison = json.loads(source.read_text()); rows = validate(comparison)
    output.mkdir(parents=True)
    shutil.copyfile(source, output / "comparison.json")
    with (output / "comparison.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["sample", "thickness_mm", "events", "G_per_neutron", "G_ci95_low", "G_ci95_high",
                         "collection", "collection_ci95_low", "collection_ci95_high",
                         "local_D_per_neutron", "local_D_ci95_low", "local_D_ci95_high"])
        for row in rows:
            writer.writerow([row["sample"], row["thickness_mm"], row["events"], *row["production"], *row["collection"], *row["net_response"]])
    with tempfile.TemporaryDirectory(prefix="side-two-replot-") as cache:
        os.environ["MPLCONFIGDIR"] = cache
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import PercentFormatter, FuncFormatter
        with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10.5,
                             "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42}):
            fig, axes = plt.subplots(1, 3, figsize=(16, 5.6))
            fig.subplots_adjust(left=.065, right=.985, wspace=.29, bottom=.255, top=.76)
            for ax, metric, title, ylabel in zip(axes, METRICS, ("Production", "Collection", "Net response"),
                    ("Generated photons / neutron", "Same-layer collected / generated", "Same-layer collected photons / neutron")):
                for sample, color, marker, line, caption in (
                        (SAMPLES[0], "#646464", "D", "--", "July: zero gap; N = 400 or 600 per thickness"),
                        (SAMPLES[1], "#0072B2", "o", "-", "Current: 0.5 mm gap; N = 1,000 per thickness")):
                    selected = [row for row in rows if row["sample"] == sample]
                    x = [row["thickness_mm"] for row in selected]
                    point, low, high = ([row[metric][i] for row in selected] for i in range(3))
                    # Draw recorded endpoints directly; a percentile interval
                    # is not required to contain its point estimate.
                    ax.vlines(x, low, high, color=color, linewidth=1.5)
                    for position, lower, upper in zip(x, low, high):
                        ax.hlines([lower, upper], position-.15, position+.15, color=color, linewidth=1.5)
                    ax.plot(x, point, color=color, marker=marker, linestyle=line, linewidth=1.5, markersize=5.5,
                            markerfacecolor="white" if sample == SAMPLES[0] else color, label=caption)
                ax.set_title(title, fontsize=13, pad=10)
                ax.set(xlabel="Scintillator thickness (mm)", ylabel=ylabel, xticks=THICKNESSES, xlim=(3,25))
                ax.grid(axis="y", alpha=.22); ax.set_axisbelow(True)
            axes[0].set_ylim(0,255000); axes[0].yaxis.set_major_formatter(FuncFormatter(lambda x,pos:f"{x/1000:g}k" if x else "0"))
            axes[1].set_ylim(.007,.021); axes[1].yaxis.set_major_formatter(PercentFormatter(1,decimals=1)); axes[2].set_ylim(0,2350)
            fig.suptitle("Side-two thickness scan: historical and current samples", fontsize=17, y=.985)
            handles, labels = axes[0].get_legend_handles_labels()
            fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(.5,.925), ncol=2, frameon=False, fontsize=11)
            fig.text(.065,.14,"Error bars: 95% whole-neutron-event bootstrap intervals (10,000 replicates). Historical N: 600 at 4/12/20 mm, 400 at 8/16/24 mm.",fontsize=9)
            fig.text(.065,.098,"Different geometries and seed cohorts: this overlay does not isolate the effect of the air gap. Both summaries report zero cross-layer collection.",fontsize=9)
            fig.text(.065,.047,f"Historical CSV SHA256 {comparison['historical_csv_sha256'][:12]}  |  current result {comparison['current_result_sha256'][:12]}  |  comparison {expected_hash[:12]}",fontsize=8.5,color="#555555")
            fig.savefig(output / "side-two-comparison.png", dpi=190)
            fig.savefig(output / "side-two-comparison.pdf", metadata={"CreationDate":None,"ModDate":None})
            plt.close(fig)
        runtime = {"python":platform.python_version(), "matplotlib":matplotlib.__version__,
                   "numpy":importlib.metadata.version("numpy"), "backend":"Agg", "font":"DejaVu Sans"}
    metadata = {"schema_version":"steel-layout-side-two-replot-v1", "comparison_sha256":expected_hash,
                "program_sha256":sha256(__file__), "runtime":runtime,
                "scope":"replot existing descriptive summary and interval endpoints only; no statistical recomputation",
                "files":{path.name:sha256(path) for path in sorted(output.iterdir()) if path.is_file()}}
    (output / "render-metadata.json").write_text(json.dumps(metadata,indent=2,sort_keys=True)+"\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--comparison-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        replot(args.comparison, args.comparison_sha256, args.output)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, "comparison replot rejected: " + str(error) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
