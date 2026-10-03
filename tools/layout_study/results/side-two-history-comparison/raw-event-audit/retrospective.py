#!/usr/bin/env python3
"""Retrospective July ROOT diagnostics; never changes original data/results.

Newly retrospective whole-neutron bootstrap: seed 2026100307, 10,000
replicates, independent pooled event samples within each old thickness.
The original July intervals and seed 20260715 are preserved as historical
results, not relabeled as the output of this new method.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import sys

import numpy as np
import uproot

THICKNESSES = (4, 8, 12, 16, 20, 24)
OLD_COUNTS = {4: 600, 8: 400, 12: 600, 16: 400, 20: 600, 24: 400}
SEED = 2026100307
BOOTSTRAPS = 10000
CORE = ("G", "D", "localD", "tile_edep_mev", "steel_edep_mev")
EXTRA = ("electron_tile_edep_mev", "proton_tile_edep_mev", "other_charged_tile_edep_mev",
         "neutral_tile_edep_mev", "charged_tile_entry_count", "charged_tile_entry_ke_mev",
         "primary_neutron_elastic_count", "primary_neutron_inelastic_count", "primary_neutron_capture_count")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def close(a, b, message):
    require(np.allclose(a, b, rtol=1e-11, atol=1e-9), message)


def scalar(value):
    return float(value) if np.isfinite(value) else None


def interval(point, replicates):
    invalid = int(np.count_nonzero(~np.isfinite(replicates)))
    return {"estimate": scalar(point), "descriptive_95_percentile_interval": None if invalid else np.quantile(replicates, [.025, .975]).tolist(),
            "invalid_replicates": invalid}


def ratio(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.asarray(b) != 0, np.asarray(a)/np.asarray(b), np.nan)


def block_data(path, identity, modern=False):
    with uproot.open(path) as root:
        scan = root["scan"].arrays(library="np")
        layer = root["layout_layers" if modern else "stack_layers"].arrays(library="np")
    ordering = np.argsort(scan["event_id"])
    require(np.array_equal(scan["event_id"][ordering], np.arange(100)), "not a 100-event task: "+identity)
    scan = {name: values[ordering] for name, values in scan.items()}
    addresses = 10*layer["event_id"] + layer["layer"]
    ordering = np.argsort(addresses)
    require(np.array_equal(addresses[ordering], np.arange(1000)), "layer event identities: "+identity)
    layer = {name: values[ordering].reshape(100, 10) for name, values in layer.items()}
    generated = layer["generated_optical_photons"].astype(np.int64)
    if modern:
        detected = layer["all_origin_detected_photons"].astype(np.int64)
        local = layer["local_origin_detected_photons"].astype(np.int64)
    else:
        detected = sum(layer[f"sensor_{s}_all_origin_detected_photons"].astype(np.int64) for s in (0, 1))
        local = sum(layer[f"sensor_{s}_local_origin_detected_photons"].astype(np.int64) for s in (0, 1))
    values = {"G": generated.sum(axis=1), "D": detected.sum(axis=1), "localD": local.sum(axis=1),
              "tile_edep_mev": scan["tile_edep_mev"], "steel_edep_mev": scan["steel_edep_mev"]}
    require(np.array_equal(values["G"], scan["generated_optical_photons"]), "layer/scan generation mismatch: "+identity)
    require(np.array_equal(values["D"], scan["sipm_detected_photons"]), "layer/scan detection mismatch: "+identity)
    require(np.all(values["localD"] <= values["D"]), "local detection exceeds all-origin: "+identity)
    close(layer["tile_edep_mev"].sum(axis=1), values["tile_edep_mev"], "tile energy closure: "+identity)
    close(layer["steel_edep_mev"].sum(axis=1), values["steel_edep_mev"], "steel energy closure: "+identity)
    for field in EXTRA:
        require(field in scan, "missing shower diagnostic column "+field)
        values[field] = scan[field]
    require(all(np.all(np.isfinite(v)) and np.all(v >= 0) for v in values.values()), "invalid event observable: "+identity)
    return {"id": identity, "root_path": str(path), "root_sha256": sha(path), "values": values,
            "layers": {"G": generated, "D": detected, "localD": local,
                       "tile_edep_mev": layer["tile_edep_mev"], "steel_edep_mev": layer["steel_edep_mev"]}}


def combine(blocks):
    return {name: np.concatenate([block["values"][name] for block in blocks]) for name in blocks[0]["values"]}


def describe(values):
    n = len(values)
    ordered, total = np.sort(values), float(values.sum())
    maximum = int(np.argmax(values))
    mean = float(values.mean())
    without_max = float((total-values[maximum])/(n-1))
    return {"events": n, "sum": total, "mean": mean, "zero_events": int(np.count_nonzero(values == 0)),
            "sample_std": float(values.std(ddof=1)), "median": float(np.median(values)),
            "p90": float(np.quantile(values, .9)), "p95": float(np.quantile(values, .95)),
            "p99": float(np.quantile(values, .99)), "maximum": float(values[maximum]),
            "maximum_event_index": maximum, "maximum_share": float(values[maximum]/total) if total else None,
            "top_1_percent_events": int(math.ceil(n*.01)),
            "top_1_percent_share": float(ordered[-math.ceil(n*.01):].sum()/total) if total else None,
            "top_5_percent_share": float(ordered[-math.ceil(n*.05):].sum()/total) if total else None,
            "leave_max_event_out_mean_sensitivity_only": without_max,
            "leave_max_event_out_relative_shift_sensitivity_only": (without_max/mean-1) if mean else None}


def diagnostics(blocks):
    values = combine(blocks)
    n = len(values["D"])
    totals = {name: float(v.sum()) for name, v in values.items()}
    means = {name: total/n for name, total in totals.items()}
    block_results = []
    for block in blocks:
        b = block["values"]
        block_results.append({"id": block["id"], "events": 100, "root_sha256": block["root_sha256"],
                              "means": {name: float(v.mean()) for name, v in b.items()},
                              "local_collection": float(b["localD"].sum()/b["G"].sum()),
                              "localD_zero_events": int(np.count_nonzero(b["localD"] == 0)),
                              "localD_maximum": int(b["localD"].max()),
                              "leave_block_out_means_sensitivity_only": {name: (totals[name]-float(v.sum()))/(n-100) for name, v in b.items()},
                              "leave_block_out_local_collection_sensitivity_only": (totals["localD"]-float(b["localD"].sum()))/(totals["G"]-float(b["G"].sum()))})
    top_events = []
    for index in np.argsort(values["localD"])[-5:][::-1]:
        block_index, event_id = divmod(int(index), 100)
        block = blocks[block_index]
        top_events.append({"logical_task_id": block["id"], "event_id": event_id,
                           "observables": {name: float(v[index]) for name, v in values.items()},
                           "per_layer": {name: v[event_id].tolist() for name, v in block["layers"].items()}})
    correlations = {}
    for a, b in (("G", "localD"), ("G", "tile_edep_mev"), ("localD", "tile_edep_mev"), ("localD", "steel_edep_mev")):
        correlations[a+"_vs_"+b] = scalar(np.corrcoef(values[a], values[b])[0, 1])
    return {"events": n, "blocks": len(blocks), "means": means,
            "local_collection": totals["localD"]/totals["G"], "all_origin_collection": totals["D"]/totals["G"],
            "cross_layer_detected_photons": int(totals["D"]-totals["localD"]),
            "distributions": {name: describe(values[name]) for name in CORE},
            "event_correlations_descriptive": correlations, "block_diagnostics": block_results,
            "top_five_localD_events": top_events}, values


def bootstrap(values, rng):
    matrix = np.column_stack([values[field] for field in CORE])
    n = len(matrix)
    result = np.empty((BOOTSTRAPS, len(CORE)))
    for offset in range(0, BOOTSTRAPS, 100):
        weights = rng.multinomial(n, np.full(n, 1/n), size=100)
        # Explicit contraction avoids platform BLAS floating-status warnings;
        # the multinomial draws and whole-event estimator are unchanged.
        result[offset:offset+100] = np.einsum("bi,ij->bj", weights, matrix, optimize=False)/n
    require(np.all(np.isfinite(result)), "nonfinite retrospective bootstrap replicate")
    require(np.all(result >= matrix.min(axis=0)-1e-9) and np.all(result <= matrix.max(axis=0)+1e-9),
            "bootstrap mean outside observed range")
    return result


def contrast(left_points, right_points, left_draws, right_draws):
    result = {}
    for field in CORE+("local_collection",):
        lp, rp = left_points[field], right_points[field]
        ld, rd = left_draws[field], right_draws[field]
        result[field] = {"difference": interval(lp-rp, ld-rd),
                         "relative_difference": interval(ratio(lp, rp)-1, ratio(ld, rd)-1)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--old-summary", type=Path, required=True)
    parser.add_argument("--old-index", type=Path, action="append", default=[])
    parser.add_argument("--current-campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.with_suffix(".npz").exists(), "refusing to overwrite retrospective output")
    groups = {t: [] for t in THICKNESSES}
    inventory, seen, expected_hashes = [], set(), {}
    for index_path in args.old_index:
        with index_path.open() as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                expected_hashes[row["logical_task_id"]] = row["root_sha256"]
    for path in sorted(args.old_root.rglob("*.root")):
        matches = re.findall(r"stack-(pilot|production)-t(\d+)-b(\d+)", str(path))
        if not matches:
            continue
        identities = set(matches)
        require(len(identities) == 1, "ambiguous task identity in path")
        stage, thickness, block_id = matches[0]
        thickness, block_id = int(thickness), int(block_id)
        identity = f"stack-{stage}-t{thickness:02d}-b{block_id:03d}"
        require(identity not in seen and thickness in groups, "duplicate or unexpected old task")
        seen.add(identity)
        data = block_data(path, identity)
        if expected_hashes:
            require(expected_hashes.get(identity) == data["root_sha256"], "old task checksum not bound to supplied index: "+identity)
        data["stage"], data["block_id"] = stage, block_id
        groups[thickness].append(data)
        inventory.append({"logical_task_id": identity, "stage": stage, "tile_thickness_mm": thickness,
                          "block_id": block_id, "path": str(path), "sha256": data["root_sha256"]})
    require(len(seen) == 30, "must find exactly 30 unique pilot/production ROOT files")
    for t in THICKNESSES:
        groups[t].sort(key=lambda b: b["block_id"])
        require(100*len(groups[t]) == OLD_COUNTS[t], "incorrect historical event allocation")
    with args.old_summary.open() as handle:
        historical_rows = {int(row["tile_thickness_mm"]): row for row in csv.DictReader(handle)}
    old, old_values, old_replicates, old_draws, old_points = {}, {}, {}, {}, {}
    streams = np.random.SeedSequence(SEED).spawn(6)
    for t, stream in zip(THICKNESSES, streams):
        old[t], old_values[t] = diagnostics(groups[t])
        h = historical_rows[t]
        require(int(h["events"]) == old[t]["events"] and int(h["tasks"]) == old[t]["blocks"], "historical CSV allocation mismatch")
        for csv_name, computed in (("generated_photons_per_neutron", old[t]["means"]["G"]),
                                  ("local_net_photons_per_neutron", old[t]["means"]["localD"]),
                                  ("all_origin_net_photons_per_neutron", old[t]["means"]["D"]),
                                  ("local_collection", old[t]["local_collection"]),
                                  ("all_origin_collection", old[t]["all_origin_collection"])):
            close(float(h[csv_name]), computed, "historical CSV estimate mismatch: "+csv_name)
        old[t]["original_csv_row_unchanged"] = h
        old_replicates[f"old_t{t:02d}"] = bootstrap(old_values[t], np.random.default_rng(stream))
        old_draws[t] = {field: old_replicates[f"old_t{t:02d}"][:, i] for i, field in enumerate(CORE)}
        old_draws[t]["local_collection"] = ratio(old_draws[t]["localD"], old_draws[t]["G"])
        old_points[t] = {field: old[t]["means"][field] for field in CORE}
        old_points[t]["local_collection"] = old[t]["local_collection"]
        old[t]["new_retrospective_intervals"] = {field: interval(old_points[t][field], old_draws[t][field]) for field in old_draws[t]}
    campaign = args.current_campaign.resolve()
    manifest, report = read_json(campaign/"manifest.json"), read_json(campaign/"analysis/result.json")
    new_groups = {t: [] for t in THICKNESSES}
    for task in sorted(manifest["tasks"], key=lambda x: (x["tile_thickness_mm"], x["block_id"])):
        if task["layout"] != "edge-two":
            continue
        receipt = read_json(campaign/task["receipt_path"])
        require(receipt["status"] == "accepted" and receipt["seeds"] == task["seeds"], "current task not accepted")
        root_item = receipt["files"]["root"]
        path = campaign/root_item["path"]
        require(sha(path) == root_item["sha256"], "current ROOT checksum mismatch")
        new_groups[task["tile_thickness_mm"]].append(block_data(path, task["task_id"], modern=True))
    new = {t: diagnostics(new_groups[t])[0] for t in THICKNESSES}
    require(all(new[t]["events"] == 1000 for t in THICKNESSES), "current edge-two allocation mismatch")
    current16 = next(c for c in report["configurations"] if c["layout"] == "edge-two" and c["tile_thickness_mm"] == 16)
    with np.load(campaign/"analysis/bootstrap_means.npz", allow_pickle=False) as stored:
        b = stored[current16["bootstrap_array_key"]]
        columns = current16["bootstrap_columns"]
        current_draws = {field: b[:, columns.index(field)] for field in ("G", "D", "tile_edep_mev", "steel_edep_mev")}
        current_draws["localD"] = current_draws["D"]-b[:, columns.index("cross_D")]
        current_draws["local_collection"] = ratio(current_draws["localD"], current_draws["G"])
        current_points = {field: new[16]["means"][field] for field in CORE}
        current_points["local_collection"] = new[16]["local_collection"]
        old16_vs_current16 = contrast(old_points[16], current_points, old_draws[16], current_draws)
    influences = []
    for block in old[16]["block_diagnostics"]:
        estimate = block["leave_block_out_means_sensitivity_only"]["localD"]
        influences.append({"omitted_block": block["id"], "remaining_events": 300, "remaining_16mm_localD_mean": estimate,
                           "difference_to_full_old20_mean": estimate-old[20]["means"]["localD"],
                           "difference_to_full_old24_mean": estimate-old[24]["means"]["localD"]})
    maximum_removed = old[16]["distributions"]["localD"]["leave_max_event_out_mean_sensitivity_only"]
    result = {"schema_version": "july-retrospective-event-diagnostic-v1", "status": "completed-existing-data-only",
              "method": {"classification": "new retrospective exploratory diagnostics; no confirmatory or causal identification claim",
                         "seed": SEED, "replicates": BOOTSTRAPS, "bootstrap": "independently resample pooled whole neutron events within each old thickness, sharing event weights across observables",
                         "interval": "unadjusted descriptive percentile 95%; selected after observing July 16 mm maximum",
                         "original_july_analysis_seed": 20260715, "original_results_modified": False,
                         "new_simulation_events": 0, "sensitivity": "leave-one-event/block calculations do not exclude observations from reported estimates",
                         "cross_campaign_comparison": "old vs current includes independently sampled events and different model/runtime identities; cannot isolate a statistical or physical cause"},
              "software": {"python": platform.python_version(), "numpy": np.__version__, "uproot": uproot.__version__, "script_sha256": sha(__file__)},
              "inputs": {"old_summary_path": str(args.old_summary), "old_summary_sha256": sha(args.old_summary),
                         "current_result_sha256": sha(campaign/"analysis/result.json"), "current_source_commit": report["approved_source_commit"],
                         "old_root_inventory": inventory}, "old": old, "current_edge_two": new,
              "old_16_minus_20": contrast(old_points[16], old_points[20], old_draws[16], old_draws[20]),
              "old_16_minus_24": contrast(old_points[16], old_points[24], old_draws[16], old_draws[24]),
              "old_16_minus_current_16": old16_vs_current16,
              "old16_block_removal_sensitivity_not_reanalysis": influences,
              "old16_max_event_removal_sensitivity_not_reanalysis": {"remaining_events": 399, "remaining_localD_mean": maximum_removed,
                                                                    "difference_to_full_old20_mean": maximum_removed-old[20]["means"]["localD"],
                                                                    "difference_to_full_old24_mean": maximum_removed-old[24]["means"]["localD"]},
              "retrospective_bootstrap_columns": CORE}
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    np.savez_compressed(args.output.with_suffix(".npz"), **old_replicates)
    print(json.dumps({"status": result["status"], "old_events": 3000, "current_edge_two_events": 6000,
                      "output": str(args.output), "retrospective_bootstrap": str(args.output.with_suffix('.npz'))}))


if __name__ == "__main__":
    main()
