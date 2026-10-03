#!/usr/bin/env python3
"""Fixed first formal scan: prepare 240 inputs, run one Slurm task, collect receipts.

No scheduler submission, automatic retry, event expansion, or scientific ranking.
prepare must run from the exact published PR commit; worker accepts only its hashes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time

import audit
import audit_geometry
import prepare as inputs
from run_local import run_process

MANIFEST_SCHEMA = "steel-layout-first-scan-analysis-input-v1"
PURPOSE = "independent-first-formal-layout-scan"
PROCESS_SCHEMA = "steel-layout-first-scan-process-task-v1"
ACCEPTED_SCHEMA = "steel-layout-first-scan-accepted-task-v1"
ANALYSIS_SEED = 2026100209
TASKS, EVENTS, TIMEOUT = 240, 100, 3300
LAYOUTS = tuple(inputs.LAYOUTS)
THICKNESSES = inputs.TILE_THICKNESSES_MM
IDENTITY_HASHES = ("executable_sha256", "sif_sha256", "dataset_manifest_sha256")
PATH_KEYS = ("source_dir", "executable", "sif", "dataset_dir", "dataset_manifest", "dataset_preflight")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(path, value):
    with Path(path).open("x") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text())


def located(root, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute(), "relative campaign path required")
    result = (root / relative).resolve()
    require(result.is_relative_to(root.resolve()), "campaign path escapes its directory")
    return result


def validate_identity(identity, commit):
    require(re.fullmatch(r"[0-9a-f]{40}", commit or "") is not None, "explicit full PR commit required")
    require(identity.get("source_commit") == commit, "source commit mismatch")
    require(identity.get("geant4_version") == "11.4.2" and identity.get("architecture") == "x86_64"
            and identity.get("run_manager") == "Serial", "11.4.2 x86_64 Serial identity required")
    for key in IDENTITY_HASHES:
        require(re.fullmatch(r"[0-9a-f]{64}", identity.get(key, "")) is not None, "invalid " + key)


def validate_runtime(runtime, commit):
    validate_identity(runtime["simulation_identity"], commit)
    for key in PATH_KEYS:
        value = runtime["paths"][key]
        require(isinstance(value, str) and Path(value).is_absolute()
                and not any(c in value for c in ("\n", ",", ":")), "invalid absolute runtime path: " + key)
    require(re.fullmatch(r"[0-9a-f]{64}", runtime.get("dataset_preflight_sha256", "")) is not None,
            "dataset preflight receipt hash required")


def source_snapshot(repo, commit):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    require(git("rev-parse", "HEAD") == commit, "prepare HEAD is not the approved PR commit")
    paths = ("test/OpNovice2/OpNovice2.cc", "test/OpNovice2/CMakeLists.txt",
             "test/OpNovice2/include", "test/OpNovice2/src", "tools/layout_study")
    require(not git("status", "--porcelain", "--untracked-files=all", "--", *paths),
            "prepare requires committed, clean simulation and study tools")
    tracked = git("ls-files", "--", *paths).splitlines()
    require("tools/layout_study/scan.py" in tracked, "scan.py must be part of the approved commit")
    return {name: sha(repo / name) for name in tracked}


def allocate_seeds(start, excluded):
    require(type(start) is int and 1 <= start <= inputs.MAX_SEED, "invalid seed start")
    require(excluded and all(type(x) is int and 1 <= x <= inputs.MAX_SEED for x in excluded),
            "nonempty prior-seed registry of integer seed values required")
    reserved, result, value = set(excluded) | {ANALYSIS_SEED}, [], start
    while len(result) < 2 * TASKS:
        require(value <= inputs.MAX_SEED, "not enough unused seed values before engine limit")
        if value not in reserved:
            result.append(value)
        value += 1
    return result


def validate_manifest(manifest):
    require(manifest.get("schema_version") == MANIFEST_SCHEMA and manifest.get("purpose") == PURPOSE,
            "wrong formal scan manifest")
    validate_identity(manifest["simulation_identity"], manifest["approved_source_commit"])
    require(manifest["analysis_seed"] == ANALYSIS_SEED and manifest["bootstrap_replicates"] == 10000,
            "first-scan analysis policy changed")
    excluded = manifest["excluded_simulation_seed_values"]
    require(excluded and all(type(x) is int for x in excluded), "invalid prior-seed registry")
    tasks = manifest["tasks"]
    require(len(tasks) == TASKS, "exactly 240 tasks required")
    sources = manifest.get("source_files_sha256", {})
    require({"test/OpNovice2/OpNovice2.cc", "test/OpNovice2/src/DetectorConstruction.cc",
             "tools/layout_study/scan.py", "tools/layout_study/audit.py",
             "tools/layout_study/audit_geometry.py", "tools/layout_study/run_local.py"} <= set(sources),
            "incomplete committed source snapshot")
    require(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in sources.values()), "invalid source snapshot hash")
    combinations, seeds, ids, paths = set(), [], set(), set()
    for task in tasks:
        t, layout, block = task["tile_thickness_mm"], task["layout"], task["block_id"]
        require(t in THICKNESSES and layout in LAYOUTS and type(block) is int and 0 <= block < 10,
                "invalid thickness/layout/block")
        key = (t, layout, block)
        require(key not in combinations and task["task_id"] == f"t{t:02d}-{layout}-b{block:02d}", "duplicate or wrong task ID")
        require(task["events"] == EVENTS, "each block must contain exactly 100 events")
        require(len(task["seeds"]) == 2 and all(type(s) is int and 1 <= s <= inputs.MAX_SEED for s in task["seeds"]), "invalid task seeds")
        for key_path in ("config_path", "macro_path", "receipt_path"):
            require(task[key_path] not in paths, "duplicate task file path")
            paths.add(task[key_path])
        require(task["receipt_path"] == "accepted/" + task["task_id"] + ".json", "unexpected receipt path")
        combinations.add(key); ids.add(task["task_id"]); seeds.extend(task["seeds"])
    require(len(combinations) == TASKS and len(ids) == TASKS and len(set(seeds)) == 480,
            "tasks and all 480 simulation seed values must be unique")
    require(not set(seeds).intersection(set(excluded) | {ANALYSIS_SEED}), "simulation seed collision")


def prepare_campaign(repo, output, runtime_path, excluded_path, commit, seed_start):
    require(not output.exists(), "refusing existing campaign directory")
    runtime, excluded = read_json(runtime_path), read_json(excluded_path)
    validate_runtime(runtime, commit)
    source_files = source_snapshot(repo, commit)
    seeds = allocate_seeds(seed_start, excluded)
    output.mkdir(parents=True)
    shutil.copyfile(runtime_path, output / "runtime.json")
    shutil.copyfile(excluded_path, output / "excluded-seeds.json")
    tasks = []
    for thickness in THICKNESSES:
        for layout in LAYOUTS:
            for block in range(10):
                index = len(tasks); task_id = f"t{thickness:02d}-{layout}-b{block:02d}"
                pair = seeds[2 * index:2 * index + 2]; directory = output / "inputs" / task_id
                registry = inputs.prepare(repo, directory, events=EVENTS, seeds=tuple(pair),
                                          layouts=(layout,), thickness=thickness)
                for name in ("config.json", "run.mac"):
                    (directory / layout / name).rename(directory / name)
                (directory / layout).rmdir()
                registry.update(stage="formal-inputs-not-accepted", source_commit=commit,
                                seed_policy="one globally unique seed pair per independent 100-event block")
                registry["layouts"][0].update(macro="run.mac", config="config.json")
                (directory / "registry.json").write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
                (directory / "SHA256SUMS").write_text("".join(
                    f"{sha(p)}  {p.name}\n" for p in sorted(directory.iterdir()) if p.name != "SHA256SUMS"))
                tasks.append({"task_id": task_id, "tile_thickness_mm": thickness, "layout": layout,
                              "block_id": block, "events": EVENTS, "seeds": pair,
                              "config_path": f"inputs/{task_id}/config.json", "config_sha256": sha(directory / "config.json"),
                              "macro_path": f"inputs/{task_id}/run.mac", "macro_sha256": sha(directory / "run.mac"),
                              "receipt_path": f"accepted/{task_id}.json"})
    manifest = {"schema_version": MANIFEST_SCHEMA, "purpose": PURPOSE, "approved_source_commit": commit,
                "simulation_identity": runtime["simulation_identity"], "analysis_seed": ANALYSIS_SEED,
                "bootstrap_replicates": 10000, "layout_audit_sha256": sha(repo / "tools/layout_study/audit.py"),
                "geometry_audit_sha256": sha(repo / "tools/layout_study/audit_geometry.py"),
                "excluded_simulation_seed_values": sorted(set(excluded)),
                "excluded_seed_registry_sha256": sha(output / "excluded-seeds.json"),
                "runtime_path": "runtime.json", "runtime_sha256": sha(output / "runtime.json"),
                "source_files_sha256": source_files, "tasks": tasks}
    validate_manifest(manifest)
    write_json(output / "manifest.json", manifest)
    return sha(output / "manifest.json")


def load_campaign(path, expected_hash):
    require(sha(path) == expected_hash, "manifest hash mismatch")
    manifest = read_json(path); validate_manifest(manifest); root = path.resolve().parent
    runtime_path = located(root, manifest["runtime_path"])
    require(sha(runtime_path) == manifest["runtime_sha256"], "runtime file hash mismatch")
    runtime = read_json(runtime_path); validate_runtime(runtime, manifest["approved_source_commit"])
    require(runtime["simulation_identity"] == manifest["simulation_identity"], "runtime identity mismatch")
    require(sha(Path(audit.__file__)) == manifest["layout_audit_sha256"] and
            sha(Path(audit_geometry.__file__)) == manifest["geometry_audit_sha256"], "auditor source hash mismatch")
    return root, manifest, runtime


def verify_inputs(root, manifest, task):
    config_path, macro_path = located(root, task["config_path"]), located(root, task["macro_path"])
    require(sha(config_path) == task["config_sha256"] and sha(macro_path) == task["macro_sha256"], "task input hash mismatch")
    config = read_json(config_path)
    require(config["events"] == EVENTS and config["seeds"] == task["seeds"] and
            config["layout"] == task["layout"] and config["tile_size_mm"][2] == task["tile_thickness_mm"], "config/task mismatch")
    template, _ = inputs.baseline_template(Path(__file__).resolve().parents[2])
    expected = inputs.study_macro(template, task["layout"], EVENTS, tuple(task["seeds"]), task["tile_thickness_mm"])
    require(macro_path.read_text() == expected, "macro differs from packaged physics/layout/seed definition")
    return config_path, macro_path


def verify_runtime(runtime, manifest):
    identity, paths = runtime["simulation_identity"], runtime["paths"]
    for path_key, hash_key in (("executable", "executable_sha256"), ("sif", "sif_sha256"),
                               ("dataset_manifest", "dataset_manifest_sha256")):
        require(sha(paths[path_key]) == identity[hash_key], path_key + " hash mismatch")
    with Path(paths["executable"]).open("rb") as handle:
        header = handle.read(20)
    require(header[:4] == b"\x7fELF" and header[4:6] == b"\x02\x01" and header[18:20] == b">\0", "x86_64 ELF required")
    for name, expected in manifest["source_files_sha256"].items():
        require(sha(located(Path(paths["source_dir"]), name)) == expected, "source snapshot drift: " + name)
    preflight_path = Path(paths["dataset_preflight"])
    require(sha(preflight_path) == runtime["dataset_preflight_sha256"], "dataset preflight receipt hash mismatch")
    preflight = read_json(preflight_path)
    require(preflight.get("schema_version") == "steel-layout-dataset-preflight-v1" and
            preflight.get("status") == "verified" and preflight.get("geant4_version") == "11.4.2" and
            preflight.get("dataset_dir") == paths["dataset_dir"] and
            preflight.get("dataset_manifest_sha256") == identity["dataset_manifest_sha256"] and
            type(preflight.get("verified_file_count")) is int and preflight["verified_file_count"] > 0 and
            str(preflight.get("job_id", "")).isdigit() and preflight.get("node"), "invalid compute-node dataset preflight receipt")
    require(Path(paths["dataset_dir"]).is_dir(), "frozen dataset directory missing")


def slurm_identity(index, env=os.environ):
    require(env.get("SLURM_ARRAY_TASK_ID") == str(index) and env.get("SLURM_ARRAY_TASK_COUNT") == str(TASKS), "wrong Slurm array task")
    require(env.get("SLURM_CPUS_PER_TASK") == "1" and env.get("SLURM_CPUS_ON_NODE") == "1"
            and env.get("SLURM_NTASKS", "1") == "1", "one allocated CPU/task required")
    node = socket.gethostname().split(".")[0]
    require(env.get("SLURMD_NODENAME", "").split(".")[0] == node and node, "worker must run on allocated compute node")
    require(env.get("SLURM_JOB_ID", "").isdigit() and env.get("SLURM_ARRAY_JOB_ID", "").isdigit(), "Slurm job identity required")
    return {"job_id": env["SLURM_JOB_ID"], "array_job_id": env["SLURM_ARRAY_JOB_ID"], "array_index": index, "cpus": 1, "node": node}


def container_command(runtime, root, cwd, arguments):
    p = runtime["paths"]
    return ["apptainer", "exec", "--cleanenv", "--bind", p["source_dir"] + ":" + p["source_dir"] + ":ro",
            "--bind", str(Path(p["executable"]).parent) + ":" + str(Path(p["executable"]).parent) + ":ro",
            "--bind", str(root) + ":" + str(root), "--bind", str(root / "inputs") + ":" + str(root / "inputs") + ":ro",
            "--bind", p["dataset_dir"] + ":/g4data:ro", "--pwd", str(cwd), p["sif"], "bash", "-c",
            'set -e; export GEANT4_DATA_DIR=/g4data; source /opt/geant4/bin/geant4.sh; export G4RUN_MANAGER_TYPE=Serial; exec "$@"',
            "layout-first-scan", *arguments]


def files_for(root, directory):
    return {key: {"path": str((directory / name).relative_to(root)), "sha256": sha(directory / name)}
            for key, name in (("root", "result.root"), ("log", "run.log"), ("summary", "result_summary.csv"))}


def worker(manifest_path, manifest_hash, index):
    root, manifest, runtime = load_campaign(manifest_path, manifest_hash)
    require(0 <= index < TASKS, "array index must be 0..239")
    task = manifest["tasks"][index]; directory = root / "results" / task["task_id"]
    directory.mkdir(parents=True, exist_ok=False)  # an existing attempt is never rerun
    receipt = {"schema_version": PROCESS_SCHEMA, "status": "failed", "task_id": task["task_id"],
               "task_sha256": object_sha(task), "manifest_sha256": manifest_hash, "seeds": task["seeds"],
               "simulation_identity": manifest["simulation_identity"], "exit_code": None,
               "dataset_verification_scope": "manifest and prior compute-node preflight receipt; no per-task data-file rehash"}
    start = time.monotonic()
    try:
        receipt["worker_scheduler"] = slurm_identity(index)
        config_path, macro_path = verify_inputs(root, manifest, task)
        verify_runtime(runtime, manifest)
        observed = subprocess.check_output(container_command(runtime, root, directory,
                   ["bash", "-c", "geant4-config --version; uname -m"]), text=True, timeout=60).splitlines()
        require(observed == ["11.4.2", "x86_64"], "actual container runtime differs from frozen identity")
        receipt["observed_runtime"] = observed
        shutil.copyfile(macro_path, directory / "run.mac")
        command = ["/usr/bin/time", "-v", "-o", str(directory / "time.txt"),
                   *container_command(runtime, root, directory, [runtime["paths"]["executable"], "run.mac"])]
        with (directory / "run.log").open("w") as log:
            code, timeout = run_process(command, cwd=directory, env=dict(os.environ, G4RUN_MANAGER_TYPE="Serial", LC_ALL="C"), log=log, timeout=TIMEOUT)
        receipt.update(exit_code=code, timed_out=timeout)
        require(not timeout and code == 0, "simulation failed or exceeded fixed 3300-second limit")
        require(sha(directory / "run.mac") == task["macro_sha256"], "executed macro changed")
        geometry = audit_geometry.audit_geometry_file(config_path, directory / "run.log", code)
        events = audit.audit_file(directory / "result.root", task["layout"], EVENTS, directory / "run.log", code,
                                  tile_thickness_mm=task["tile_thickness_mm"])
        require(geometry["log_sha256"] == events["log_sha256"], "audits refer to different logs")
        write_json(directory / "geometry-audit.json", geometry); write_json(directory / "event-audit.json", events)
        receipt.update(status="process-verified", files=files_for(root, directory),
                       time_sha256=sha(directory / "time.txt"), dataset_preflight_sha256=runtime["dataset_preflight_sha256"])
    except Exception as error:
        receipt["error"] = str(error)
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - start
        write_json(directory / "process-receipt.json", receipt)
    return 0 if receipt["status"] == "process-verified" else 1


def scheduler_rows(path, array_id):
    rows = list(csv.DictReader(io.StringIO(Path(path).read_text()), delimiter="|"))
    indexed = {}
    for row in rows:
        require(row.get("JobID") not in indexed, "duplicate scheduler allocation")
        indexed[row.get("JobID")] = row
    require(set(indexed) == {f"{array_id}_{i}" for i in range(TASKS)}, "sacct -X must contain exactly 240 task allocations")
    for row in indexed.values():
        require(row.get("State") == "COMPLETED" and row.get("ExitCode") == "0:0" and row.get("AllocCPUS") == "1",
                "all allocations must be COMPLETED / 0:0 / 1 CPU")
        require(row.get("JobIDRaw", "").isdigit() and row.get("NodeList"), "scheduler raw job ID and node required")
    return indexed


def collect(manifest_path, manifest_hash, sacct_path, array_id):
    root, manifest, runtime = load_campaign(manifest_path, manifest_hash)
    require(not (root / "accepted").exists(), "accepted receipts already exist; refusing overwrite")
    rows = scheduler_rows(sacct_path, array_id); accepted = []
    for index, task in enumerate(manifest["tasks"]):
        config, _ = verify_inputs(root, manifest, task)
        directory = root / "results" / task["task_id"]; path = directory / "process-receipt.json"
        process = read_json(path); row = rows[f"{array_id}_{index}"]; scheduler = process["worker_scheduler"]
        require(process.get("schema_version") == PROCESS_SCHEMA and process.get("status") == "process-verified"
                and process.get("exit_code") == 0 and not process.get("timed_out") and
                process.get("task_id") == task["task_id"] and process.get("task_sha256") == object_sha(task) and
                process.get("manifest_sha256") == manifest_hash and process.get("seeds") == task["seeds"] and
                process.get("simulation_identity") == manifest["simulation_identity"], "invalid process receipt")
        require(scheduler == {"job_id": row["JobIDRaw"], "array_job_id": str(array_id), "array_index": index,
                              "cpus": 1, "node": row["NodeList"]}, "scheduler/worker identity mismatch")
        require(process["files"] == files_for(root, directory), "output hash mismatch")
        require(sha(directory / "time.txt") == process.get("time_sha256") and
                process.get("dataset_preflight_sha256") == runtime["dataset_preflight_sha256"] and
                process.get("observed_runtime") == ["11.4.2", "x86_64"], "process runtime/time evidence mismatch")
        geometry = audit_geometry.audit_geometry_file(config, directory / "run.log", 0)
        events = audit.audit_file(directory / "result.root", task["layout"], EVENTS, directory / "run.log", 0,
                                  tile_thickness_mm=task["tile_thickness_mm"])
        require(geometry == read_json(directory / "geometry-audit.json") and events == read_json(directory / "event-audit.json"), "stored audits differ from fresh audit")
        accepted.append({"schema_version": ACCEPTED_SCHEMA, "status": "accepted", "task_id": task["task_id"],
                         "task_sha256": object_sha(task), "manifest_sha256": manifest_hash, "exit_code": 0,
                         "seeds": task["seeds"], "simulation_identity": manifest["simulation_identity"],
                         "scheduler": {"state": row["State"], "exit_code": row["ExitCode"], "cpus": 1, "job_id": row["JobIDRaw"]},
                         "files": process["files"], "process_receipt_sha256": sha(path), "sacct_sha256": sha(sacct_path)})
    stage = Path(tempfile.mkdtemp(prefix=".accepted-stage-", dir=root))
    for task, receipt in zip(manifest["tasks"], accepted):
        write_json(stage / (task["task_id"] + ".json"), receipt)
    stage.rename(root / "accepted")
    return len(accepted)


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    for key in ("runtime", "excluded-seeds", "output-dir"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--source-commit", required=True); p.add_argument("--seed-start", type=int, required=True)
    for name in ("worker", "collect"):
        p = commands.add_parser(name); p.add_argument("--manifest", type=Path, required=True); p.add_argument("--manifest-sha256", required=True)
        if name == "worker": p.add_argument("--task-index", type=int, required=True)
        else:
            p.add_argument("--sacct", type=Path, required=True); p.add_argument("--array-job-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare_campaign(Path(__file__).resolve().parents[2], args.output_dir.resolve(), args.runtime,
                                   args.excluded_seeds, args.source_commit, args.seed_start))
        elif args.command == "worker":
            return worker(args.manifest, args.manifest_sha256, args.task_index)
        else:
            print(f"Accepted {collect(args.manifest, args.manifest_sha256, args.sacct, args.array_job_id)} tasks.")
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"first scan rejected: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
