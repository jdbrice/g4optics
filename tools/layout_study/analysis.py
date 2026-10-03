#!/usr/bin/env python3
"""Analyze exactly the approved 24-configuration first layout scan; never run jobs.

The direct manifest/receipt contract is printed by --print-schema. Freeze the
formal campaign manifest after identifying the final draft-PR source commit.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import re
import shutil

import numpy as np
import uproot

LAYOUTS = ("back-four", "back-two", "back-center", "edge-two")
SENSORS = {"back-four": 4, "back-two": 2, "back-center": 1, "edge-two": 2}
THICKNESSES = (4, 8, 12, 16, 20, 24)
REPLICATES = 10000
ANALYSIS_SEED = 2026100209
PRIMARY_TAIL = 0.05 / (2 * 18)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def task_sha256(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def local_path(base, value):
    base = Path(base).resolve()
    path = (base / value).resolve()
    require(path.is_relative_to(base), f"input path escapes campaign directory: {value}")
    require(path.is_file(), f"missing input file: {value}")
    return path


def checked_file(base, item):
    path = local_path(base, item["path"])
    require(sha256(path) == item["sha256"], f"checksum mismatch: {item['path']}")
    return path


def read_json(path):
    return json.loads(Path(path).read_text())


def validate_manifest(manifest):
    require(manifest["schema_version"] == "steel-layout-first-scan-analysis-input-v1", "wrong manifest schema")
    require(manifest["purpose"] == "independent-first-formal-layout-scan", "wrong campaign purpose")
    identity = manifest["simulation_identity"]
    approved_commit = manifest["approved_source_commit"]
    require(re.fullmatch(r"[0-9a-f]{40}", approved_commit) is not None, "approved source commit must be a full SHA")
    require(identity["source_commit"] == approved_commit, "simulation source differs from approved PR commit")
    require(identity["geant4_version"] == "11.4.2" and identity["architecture"] == "x86_64"
            and identity["run_manager"] == "Serial", "simulation environment changed")
    for field in ("executable_sha256", "sif_sha256", "dataset_manifest_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", identity[field]) is not None, f"invalid {field}")
    require(manifest["analysis_seed"] == ANALYSIS_SEED and manifest["bootstrap_replicates"] == REPLICATES,
            "analysis randomization differs from preregistered plan")
    excluded = manifest["excluded_simulation_seed_values"]
    require(isinstance(excluded, list) and excluded and all(type(x) is int for x in excluded),
            "prior engineering/benchmark seed values must be supplied")
    tasks = manifest["tasks"]
    require(len(tasks) == 240, "must register exactly 240 tasks")
    ids, configurations, values = set(), set(), []
    for task in tasks:
        require(task["task_id"] not in ids, "duplicate task ID")
        ids.add(task["task_id"])
        key = (task["tile_thickness_mm"], task["layout"], task["block_id"])
        require(key not in configurations, "duplicate configuration block")
        configurations.add(key)
        require(task["events"] == 100, "each task must contain exactly 100 events")
        seeds = task["seeds"]
        require(len(seeds) == 2 and all(type(s) is int and 1 <= s <= 2147483646 for s in seeds),
                "invalid registered seeds")
        values.extend(seeds)
    expected = {(t, layout, block) for t in THICKNESSES for layout in LAYOUTS for block in range(10)}
    require(configurations == expected, "incomplete or unexpected 24-configuration matrix")
    require(len(set(values)) == 480, "all 480 simulation seed values must be distinct")
    require(not set(values).intersection(excluded), "formal seeds overlap prior engineering/benchmark seeds")
    return sorted(tasks, key=lambda task: (task["tile_thickness_mm"], LAYOUTS.index(task["layout"]), task["block_id"]))


def audit_task(base, manifest, manifest_hash, task, audit_module):
    receipt = read_json(local_path(base, task["receipt_path"]))
    require(receipt["schema_version"] == "steel-layout-first-scan-accepted-task-v1", "wrong receipt schema")
    require(receipt["status"] == "accepted" and receipt["exit_code"] == 0, "task not accepted or abnormal exit")
    require(receipt["manifest_sha256"] == manifest_hash and receipt["task_sha256"] == task_sha256(task),
            "receipt is not bound to frozen manifest/task")
    require(receipt["task_id"] == task["task_id"] and receipt["seeds"] == task["seeds"],
            "receipt task or seed mismatch")
    require(receipt["simulation_identity"] == manifest["simulation_identity"], "actual simulation identity mismatch")
    scheduler = receipt["scheduler"]
    require(scheduler["state"] == "COMPLETED" and scheduler["exit_code"] == "0:0"
            and scheduler["cpus"] == 1 and str(scheduler["job_id"]), "task lacks normal scheduler completion")
    config_path = checked_file(base, {"path": task["config_path"], "sha256": task["config_sha256"]})
    macro_path = checked_file(base, {"path": task["macro_path"], "sha256": task["macro_sha256"]})
    config = read_json(config_path)
    require(config["layout"] == task["layout"] and config["tile_size_mm"] == [100, 100, task["tile_thickness_mm"]]
            and config["events"] == 100 and config["seeds"] == task["seeds"], "frozen task configuration mismatch")
    require(config["tile_to_next_steel_gap_mm"] == 0.5 and config["layers"] == 10,
            "geometry differs from approved scan")
    require(config["macro_sha256"] == task["macro_sha256"], "config does not bind execution macro")
    commands = [line.partition("#")[0].split() for line in macro_path.read_text().splitlines()]
    commands = [command for command in commands if command]
    seed_commands = [command[1:] for command in commands if command[0] == "/random/setSeeds"]
    require(seed_commands and all([int(x) for x in command] == task["seeds"] for command in seed_commands),
            "actual macro seed commands do not match registered task")
    require([command[1:] for command in commands if command[0] == "/run/beamOn"] == [["100"]],
            "execution macro must have exactly one 100-event beamOn")
    files = {name: checked_file(base, receipt["files"][name]) for name in ("root", "log", "summary")}
    expected_summary = files["root"].with_name(files["root"].stem + "_summary.csv")
    require(files["summary"] == expected_summary, "summary path differs from audited ROOT companion")
    audit_result = audit_module.audit_file(files["root"], task["layout"], 100,
                                          files["log"], 0, tile_thickness_mm=task["tile_thickness_mm"])
    require(audit_result["status"] == "passed", "fresh layout audit failed")
    return files["root"], {"task_id": task["task_id"], "seeds": task["seeds"],
                           "scheduler": scheduler, "files": receipt["files"],
                           "receipt_sha256": sha256(local_path(base, task["receipt_path"])),
                           "audit": audit_result}


def event_matrix(root_path, layout):
    with uproot.open(root_path) as root:
        scan = root["scan"].arrays(library="np")
        layers = root["layout_layers"].arrays(library="np")
        sensors = root["layout_sensors"].arrays(library="np")
    order = np.argsort(scan["event_id"])
    data, labels = [], []
    for label, field in (("D", "sipm_detected_photons"), ("G", "generated_optical_photons"),
                         ("scintillation", "scintillation_photons"), ("cerenkov", "cerenkov_photons"),
                         ("steel_edep_mev", "steel_edep_mev"), ("tile_edep_mev", "tile_edep_mev")):
        data.append(scan[field][order].astype(float))
        labels.append(label)
    data.append((data[0] == 0).astype(float))
    labels.append("zero_D")
    # Subtract integer counts within each event before averaging/resampling.
    # Subtracting separately rounded means can invent tiny cross-layer counts.
    local_totals = np.zeros(100, dtype=np.int64)
    np.add.at(local_totals, layers["event_id"],
              layers["local_origin_detected_photons"].astype(np.int64))
    cross_counts = scan["sipm_detected_photons"][order].astype(np.int64) - local_totals
    data.append(cross_counts)
    labels.append("cross_D")
    for prefix, field in (("layer_D", "all_origin_detected_photons"), ("layer_local_D", "local_origin_detected_photons"),
                          ("layer_G", "generated_optical_photons"), ("layer_steel_edep_mev", "steel_edep_mev"),
                          ("layer_tile_edep_mev", "tile_edep_mev")):
        matrix = np.zeros((100, 10))
        matrix[layers["event_id"], layers["layer"]] = layers[field]
        for layer in range(10):
            data.append(matrix[:, layer])
            labels.append(f"{prefix}_{layer}")
    n = SENSORS[layout]
    for prefix, field in (("sensor_D", "all_origin_detected_photons"), ("sensor_local_D", "local_origin_detected_photons")):
        matrix = np.zeros((100, 10 * n))
        matrix[sensors["event_id"], n*sensors["layer"] + sensors["local_sensor"]] = sensors[field]
        for layer in range(10):
            for sensor in range(n):
                data.append(matrix[:, n*layer+sensor])
                labels.append(f"{prefix}_{4*layer+sensor}")
    return np.column_stack(data), labels


def bootstrap_means(matrix, rng):
    n = len(matrix)
    means = np.empty((REPLICATES, matrix.shape[1]))
    # Multinomial weights are exactly whole-event sampling with replacement.
    # Chunking bounds memory while sharing weights across all event quantities.
    for start in range(0, REPLICATES, 100):
        count = min(100, REPLICATES-start)
        weights = rng.multinomial(n, np.full(n, 1.0/n), size=count)
        means[start:start+count] = weights @ matrix / n
    return means


def interval(point, samples, tail=0.025):
    invalid = int(np.count_nonzero(~np.isfinite(samples)))
    return {"estimate": float(point) if np.isfinite(point) else None,
            "percentile_interval": None if invalid else [float(x) for x in np.quantile(samples, [tail, 1-tail])],
            "nominal_individual_coverage": 1-2*tail, "invalid_bootstrap_replicates": invalid}


def ratio(numerator, denominator):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denominator != 0, numerator / denominator, np.nan)


def summarize(matrix, labels, bootstrap, layout):
    positions = {label: i for i, label in enumerate(labels)}
    means = matrix.mean(axis=0)
    result = {label: interval(means[i], bootstrap[:, i]) for i, label in enumerate(labels)}
    d, g = positions["D"], positions["G"]
    result["legacy_D_over_G"] = interval(ratio(means[d], means[g]), ratio(bootstrap[:, d], bootstrap[:, g]))
    area = 10 * SENSORS[layout] * 2.4 * 2.4
    result["nominal_total_collection_face_area_mm2"] = area
    result["D_per_neutron_per_mm2"] = interval(means[d]/area, bootstrap[:, d]/area)
    cross_point = means[positions["cross_D"]]
    cross_bootstrap = bootstrap[:, positions["cross_D"]]
    result["cross_layer_D_per_neutron"] = interval(cross_point, cross_bootstrap)
    result["cross_layer_collection_share"] = interval(ratio(cross_point, means[d]), ratio(cross_bootstrap, bootstrap[:, d]))
    result["sensor_share_of_D"] = {
        str(copy): interval(ratio(means[positions[f"sensor_D_{copy}"]], means[d]),
                            ratio(bootstrap[:, positions[f"sensor_D_{copy}"]], bootstrap[:, d]))
        for layer in range(10) for sensor in range(SENSORS[layout]) for copy in [4*layer+sensor]}
    values = matrix[:, d]
    ordered = np.sort(values)
    total = values.sum()
    result["response_distribution_descriptive"] = {
        "median": float(np.median(values)), "p90": float(np.quantile(values, .9)),
        "p99": float(np.quantile(values, .99)), "maximum": float(values.max()),
        "sample_std": float(values.std(ddof=1)),
        "coefficient_of_variation": float(values.std(ddof=1)/means[d]) if means[d] else None,
        "maximum_event_share": float(ordered[-1]/total) if total else None,
        "top_1_percent_event_share": float(ordered[-10:].sum()/total) if total else None,
        "block_means_D": [float(x) for x in values.reshape(10, 100).mean(axis=1)],
        "zero_events": int(np.count_nonzero(values == 0)),
    }
    return result


def analyze(manifest_path, manifest_hash, audit_path, audit_hash, output):
    manifest_path = Path(manifest_path).resolve()
    base = manifest_path.parent
    require(not output.exists(), "refusing to overwrite analysis output")
    require(sha256(manifest_path) == manifest_hash, "frozen manifest SHA mismatch")
    require(sha256(audit_path) == audit_hash, "frozen audit module SHA mismatch")
    manifest = read_json(manifest_path)
    require(manifest["layout_audit_sha256"] == audit_hash, "manifest uses different audit module")
    tasks = validate_manifest(manifest)
    spec = importlib.util.spec_from_file_location("frozen_layout_audit", audit_path)
    audit_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit_module)
    runs, inventory = {}, []
    # Do not calculate means or intervals until every registered task passes.
    for task in tasks:
        path, evidence = audit_task(base, manifest, manifest_hash, task, audit_module)
        key = (task["tile_thickness_mm"], task["layout"])
        runs.setdefault(key, []).append(path)
        inventory.append(evidence)
    require(len(inventory) == 240, "all 240 tasks must pass before analysis")
    rng_streams = np.random.SeedSequence(ANALYSIS_SEED).spawn(24)
    results, arrays, means_by_config, bootstrap_by_config = [], {}, {}, {}
    for stream, (key, paths) in zip(rng_streams, runs.items()):
        thickness, layout = key
        blocks = [event_matrix(path, layout) for path in paths]
        labels = blocks[0][1]
        require(all(other == labels for _, other in blocks), "inconsistent event feature schema")
        matrix = np.concatenate([block for block, _ in blocks])
        require(len(matrix) == 1000, "configuration must have exactly 1000 independent events")
        bootstrap = bootstrap_means(matrix, np.random.default_rng(stream))
        name = f"t{thickness:02d}_{layout}"
        arrays[name] = bootstrap
        means_by_config[key] = float(matrix[:, 0].mean())
        bootstrap_by_config[key] = bootstrap[:, 0]
        results.append({"tile_thickness_mm": thickness, "layout": layout, "events": 1000,
                        "bootstrap_array_key": name, "bootstrap_columns": labels,
                        "statistics": summarize(matrix, labels, bootstrap, layout)})
    contrasts = []
    for thickness in THICKNESSES:
        reference = (thickness, "edge-two")
        for layout in LAYOUTS[:-1]:
            key = (thickness, layout)
            difference = means_by_config[key] - means_by_config[reference]
            replicated = bootstrap_by_config[key] - bootstrap_by_config[reference]
            relative = ratio(means_by_config[key], means_by_config[reference]) - 1
            relative_replicated = ratio(bootstrap_by_config[key], bootstrap_by_config[reference]) - 1
            contrasts.append({"tile_thickness_mm": thickness, "layout": layout, "reference": "edge-two",
                              "absolute_difference_D": interval(difference, replicated, PRIMARY_TAIL),
                              "absolute_difference_D_descriptive_95": interval(difference, replicated),
                              "relative_difference_descriptive_95": interval(relative, relative_replicated)})
    report = {"schema_version": "steel-layout-first-scan-result-v1", "status": "complete-fixed-first-sample",
              "events": 24000, "accepted_tasks": 240, "manifest_sha256": manifest_hash,
              "approved_source_commit": manifest["approved_source_commit"],
              "simulation_identity": manifest["simulation_identity"], "analysis_seed": ANALYSIS_SEED,
              "bootstrap_replicates": REPLICATES, "primary_contrasts": 18,
              "interval_scope": "18 absolute primary contrasts: nominal Bonferroni family-wise 95%; bootstrap coverage approximate; all other intervals descriptive 95%",
              "precision_guaranteed": False, "additional_events_authorized_by_analysis": False,
              "NoRINDEX_scope": "No NoRINDEX report in logs; inherited first-boundary observation is not a full-trajectory check",
              "analysis_program_sha256": sha256(__file__), "layout_audit_sha256": audit_hash,
              "dependencies": {"python": platform.python_version(), "numpy": np.__version__, "uproot": uproot.__version__},
              "configurations": results, "comparisons": contrasts, "input_inventory": inventory}
    output.mkdir(parents=True)
    (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    np.savez_compressed(output / "bootstrap_means.npz", **arrays)
    shutil.copyfile(__file__, output / "analysis_program.py")
    shutil.copyfile(audit_path, output / "layout_audit.py")
    files = {path.name: sha256(path) for path in output.iterdir() if path.is_file()}
    (output / "artifact-index.json").write_text(json.dumps(files, indent=2) + "\n")
    return {"status": report["status"], "events": 24000, "accepted_tasks": 240, "output": str(output)}


def print_schema():
    identity = {"source_commit": "<final draft PR commit, 40 hex>", "geant4_version": "11.4.2", "architecture": "x86_64", "run_manager": "Serial",
                "executable_sha256": "<64 hex>", "sif_sha256": "<64 hex>", "dataset_manifest_sha256": "<64 hex>"}
    task = {"task_id": "t04-back-four-b00", "tile_thickness_mm": 4, "layout": "back-four", "block_id": 0,
            "events": 100, "seeds": [100001, 100002], "config_path": "inputs/t04-back-four-b00/config.json",
            "config_sha256": "<64 hex>", "macro_path": "inputs/t04-back-four-b00/run.mac", "macro_sha256": "<64 hex>",
            "receipt_path": "accepted/t04-back-four-b00.json"}
    manifest = {"schema_version": "steel-layout-first-scan-analysis-input-v1", "purpose": "independent-first-formal-layout-scan",
                "approved_source_commit": identity["source_commit"], "simulation_identity": identity,
                "analysis_seed": ANALYSIS_SEED, "bootstrap_replicates": REPLICATES,
                "layout_audit_sha256": "<64 hex>", "excluded_simulation_seed_values": ["all prior engineering/benchmark seed values"],
                "tasks": [task, "... exactly 240 unique 100-event tasks ..."]}
    receipt = {"schema_version": "steel-layout-first-scan-accepted-task-v1", "status": "accepted", "task_id": task["task_id"],
               "task_sha256": "<canonical sorted compact JSON SHA of the whole task object>", "manifest_sha256": "<exact manifest file SHA>",
               "exit_code": 0, "seeds": task["seeds"], "simulation_identity": identity,
               "scheduler": {"state": "COMPLETED", "exit_code": "0:0", "cpus": 1, "job_id": "<actual allocation>"},
               "files": {name: {"path": f"results/{task['task_id']}/{filename}", "sha256": "<64 hex>"}
                         for name, filename in (("root", "result.root"), ("log", "run.log"), ("summary", "result_summary.csv"))}}
    print(json.dumps({"manifest": manifest, "accepted_receipt": receipt}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-schema", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--audit-module", type=Path)
    parser.add_argument("--audit-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.print_schema:
        print_schema()
        return
    require(all(value is not None for value in (args.manifest, args.manifest_sha256, args.audit_module,
                                               args.audit_sha256, args.output)), "all manifest/audit/output arguments are required")
    print(json.dumps(analyze(args.manifest, args.manifest_sha256, args.audit_module, args.audit_sha256, args.output), indent=2))


if __name__ == "__main__":
    main()
