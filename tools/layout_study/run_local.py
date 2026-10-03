#!/usr/bin/env python3
"""Prepare, run, and audit a small layout validation on Docker or OSC.

This runs an already-built executable. Every layout gets a separate process,
its actual return code, hashes, geometry audit, and ROOT audit. It does not
submit scheduler jobs or choose a scientific event allocation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import prepare
import audit
import audit_geometry


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_process(arguments, *, cwd, env, log, timeout, termination_grace_seconds=5):
    """Run an isolated process group and stop its descendants after a timeout.

    Container launchers can outlive their direct parent or leave payloads behind.
    Kill the whole group even when the parent exits immediately after SIGTERM;
    otherwise a child that ignores SIGTERM could keep writing into a failed run.
    """
    process = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout), False
    except BaseException as error:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=termination_grace_seconds)
        except subprocess.TimeoutExpired:
            pass
        # Reaping the parent alone does not prove its payload stopped. Kill
        # any surviving group members, then make sure the parent is reaped.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=termination_grace_seconds)
        if isinstance(error, subprocess.TimeoutExpired):
            return 124, True
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--events", type=int, default=10)
    parser.add_argument("--tile-thickness-mm", type=int, choices=prepare.TILE_THICKNESSES_MM, default=4)
    parser.add_argument("--seed1", type=int, default=20261002)
    parser.add_argument("--seed2", type=int, default=10401)
    parser.add_argument("--layout", choices=tuple(prepare.LAYOUTS), action="append")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--apptainer-image", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    binary, output = args.executable.resolve(), args.output_dir.resolve()
    if args.events <= 0 or args.timeout_seconds <= 0:
        parser.error("events and timeout must be positive; use prepare.py --events 0 for initialization only")
    if not binary.is_file():
        parser.error("build the executable first")
    if bool(args.apptainer_image) != bool(args.data_dir):
        parser.error("--apptainer-image and --data-dir must be provided together")
    # The OSC launcher binds this checkout at the same absolute host path.
    for path in (binary, output):
        try:
            path.relative_to(repo)
        except ValueError:
            parser.error("executable and output directory must be inside this checkout")
    env = dict(os.environ, G4RUN_MANAGER_TYPE="Serial", PYTHONDONTWRITEBYTECODE="1")

    def command(arguments, cwd):
        if not args.apptainer_image:
            return list(arguments)
        return ["apptainer", "exec", "--cleanenv", "--bind", str(repo) + ":" + str(repo),
                "--bind", str(args.data_dir.resolve()) + ":/g4data:ro", "--pwd", str(cwd),
                str(args.apptainer_image.resolve()), "bash", "-c",
                'set -e; export GEANT4_DATA_DIR=/g4data; source /opt/geant4/bin/geant4.sh; export G4RUN_MANAGER_TYPE=Serial; exec "$@"',
                "layout-study", *arguments]

    version = subprocess.check_output(command(["geant4-config", "--version"], repo),
                                      cwd=repo, env=env, text=True).strip()
    if version != "11.4.2":
        parser.error("Geant4 11.4.2 required; found " + version)
    layouts = tuple(args.layout or prepare.LAYOUTS)
    prepare.prepare(repo, output, events=args.events, seeds=(args.seed1, args.seed2),
                    layouts=layouts, thickness=args.tile_thickness_mm)
    runtime = {"geant4": version, "python": sys.version,
               "numpy": importlib.metadata.version("numpy"),
               "uproot": importlib.metadata.version("uproot"),
               "binary": str(binary), "binary_sha256": sha256(binary),
               "run_manager": "Serial", "events_per_layout": args.events,
               "execution_architecture": subprocess.check_output(command(["uname", "-m"], repo),
                                        cwd=repo, env=env, text=True).strip(),
               "tile_thickness_mm": args.tile_thickness_mm,
               "scope": "engineering validation; no statistical precision claim"}
    if args.apptainer_image:
        runtime.update(apptainer_image=str(args.apptainer_image.resolve()),
                       image_sha256=sha256(args.apptainer_image))
    data_root = args.data_dir.resolve() if args.data_dir else Path("/g4data")
    dataset_manifest = data_root / "layout-study-SHA256SUMS"
    if dataset_manifest.is_file():
        runtime["dataset_manifest_sha256"] = sha256(dataset_manifest)
    sources = sorted([repo / "test/OpNovice2/OpNovice2.cc", repo / "test/OpNovice2/CMakeLists.txt"]
                     + list((repo / "test/OpNovice2/include").glob("*.hh"))
                     + list((repo / "test/OpNovice2/src").glob("*.cc"))
                     + list((repo / "tools/layout_study").glob("*.py")))
    runtime["source_sha256"] = {str(p.relative_to(repo)): sha256(p) for p in sources}
    write_json(output / "runtime.json", runtime)
    (output / "python-freeze.txt").write_text(subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze"], text=True))
    results = []
    for layout in layouts:
        directory = output / layout
        log_path = directory / "simulation.log"
        start = time.monotonic()
        with log_path.open("w") as log:
            returncode, timed_out = run_process(
                command([str(binary), "run.mac"], directory), cwd=directory,
                env=env, log=log, timeout=args.timeout_seconds)
        receipt = {"layout": layout, "returncode": returncode, "timed_out": timed_out,
                   "elapsed_seconds": time.monotonic() - start,
                   "binary_sha256": runtime["binary_sha256"],
                   "macro_sha256": sha256(directory / "run.mac"),
                   "config_sha256": sha256(directory / "config.json"),
                   "log_sha256": sha256(log_path)}
        write_json(directory / "receipt.json", receipt)
        try:
            geometry = audit_geometry.audit_geometry_file(directory / "config.json", log_path, returncode)
            write_json(directory / "geometry-audit.json", geometry)
            events = audit.audit_file(directory / "result.root", layout, args.events, log_path,
                                      returncode, tile_thickness_mm=args.tile_thickness_mm)
            write_json(directory / "event-audit.json", events)
            if geometry["log_sha256"] != events["log_sha256"]:
                raise ValueError("geometry/event audit log identities disagree")
            results.append({"layout": layout, "status": "passed", "receipt": receipt,
                            "geometry_audit": geometry, "event_audit": events})
            print(layout + ": passed", flush=True)
        except Exception as error:
            results.append({"layout": layout, "status": "failed", "receipt": receipt,
                            "error": str(error)})
            write_json(output / "run-summary.json", {"status": "failed", "cases": results})
            print(layout + ": failed: " + str(error), file=sys.stderr)
            return 1
    write_json(output / "run-summary.json", {"status": "passed", "cases": results})
    print("All requested layouts passed; results: " + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
