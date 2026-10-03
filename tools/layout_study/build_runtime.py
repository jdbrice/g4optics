#!/usr/bin/env python3
"""Build and freeze an OSC runtime from an explicit clean PR commit.

Run inside a one-CPU, 4 GiB Slurm compute allocation. No downloads, data
installation, scheduler submission, simulation, or output reuse is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import time

SOURCE_PATHS = ("test/OpNovice2", "tools/layout_study")
DATA_VARIABLES = {
    "G4NEUTRONHPDATA", "G4LEDATA", "G4LEVELGAMMADATA", "G4RADIOACTIVEDATA",
    "G4PARTICLEXSDATA", "G4PIIDATA", "G4REALSURFACEDATA", "G4SAIDXSDATA",
    "G4ABLADATA", "G4INCLDATA", "G4ENSDFSTATEDATA", "G4CHANNELINGDATA",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def safe_path(path):
    path = Path(path).resolve()
    require(not any(character in str(path) for character in ("\n", "\r", ",", ":")), "unsupported runtime path")
    return path


def compute_identity(env, hostname):
    node = hostname.split(".")[0]
    require(env.get("SLURM_JOB_ID", "").isdigit(), "Slurm compute job required")
    require(node and not re.search(r"login|head", node, re.I) and
            env.get("SLURMD_NODENAME", "").split(".")[0] == node, "run on the allocated compute node, not login")
    require(env.get("SLURM_CPUS_PER_TASK") == "1" and env.get("SLURM_NTASKS", "1") == "1", "one CPU/task required")
    memory = env.get("SLURM_MEM_PER_NODE", env.get("SLURM_MEM_PER_CPU"))
    require(memory == "4096", "request 4 GiB memory for this runtime build")
    return {"job_id": env["SLURM_JOB_ID"], "node": node, "cpus_per_task": 1,
            "requested_memory_mib": 4096}


def source_snapshot(repo, commit):
    require(re.fullmatch(r"[0-9a-f]{40}", commit or ""), "explicit full 40-character source commit required")
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(repo), *arguments]).decode().strip()
    require(git("rev-parse", "HEAD") == commit, "checkout HEAD differs from requested source commit")
    require(not git("status", "--porcelain", "--untracked-files=all", "--", *SOURCE_PATHS), "application/study sources must be committed and clean")
    names = git("ls-files", "--", *SOURCE_PATHS).splitlines()
    required = {"test/OpNovice2/CMakeLists.txt", "test/OpNovice2/OpNovice2.cc", "tools/layout_study/build_runtime.py"}
    require(required <= set(names), "incomplete committed source checkout")
    for pattern in ("test/OpNovice2/include/*.hh", "test/OpNovice2/src/*.cc"):
        require(all(str(path.relative_to(repo)) in names for path in repo.glob(pattern)), "untracked compilation input, possibly ignored by Git")
    require(all((repo / name).is_file() and not (repo / name).is_symlink() for name in names), "source snapshot requires regular tracked files")
    snapshot = {}
    for name in names:
        data = (repo / name).read_bytes()
        committed = subprocess.check_output(["git", "-C", str(repo), "show", commit + ":" + name])
        require(data == committed, "source bytes differ from requested commit: " + name)
        snapshot[name] = hashlib.sha256(data).hexdigest()
    return snapshot


def dataset_directories(text, root):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        require(len(fields) == 3, "unexpected geant4-config dataset record")
        name, variable, container_path = fields
        require(variable in DATA_VARIABLES and variable not in result, "unknown/duplicate dataset variable")
        relative = PurePosixPath(container_path).relative_to("/g4data")
        require(relative.parts and ".." not in relative.parts, "dataset must be beneath /g4data")
        path = (root / str(relative)).resolve()
        require(root in path.parents and path.is_dir(), "dataset path escapes root or is missing")
        result[variable] = {"name": name, "container_path": container_path, "host_path": str(path)}
    require(set(result) == DATA_VARIABLES and len({item["host_path"] for item in result.values()}) == 12,
            "exactly twelve distinct Geant4 dataset directories required")
    return result


def hash_datasets(root, directories, destination):
    files = []
    for item in directories.values():
        discovered = sorted(Path(item["host_path"]).rglob("*"))
        require(all(not path.is_symlink() for path in discovered), "dataset symlink is not a frozen regular file")
        entries = [path for path in discovered if path.is_file()]
        require(entries, "empty Geant4 dataset directory")
        require(all(not path.is_symlink() and root in path.resolve().parents for path in entries), "dataset symlink/outside-root file")
        files.extend(entries)
    require(len(files) == len(set(files)), "overlapping dataset directories")
    with destination.open("x") as stream:
        for path in sorted(files):
            relative = path.relative_to(root).as_posix()
            require(not any(c in relative for c in ("\n", "\r", "\\")), "unsupported dataset filename")
            before = path.stat()
            checksum = sha(path)
            after = path.stat()
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "dataset changed while hashing")
            stream.write(checksum + "  " + relative + "\n")
    return len(files)


def read_data_manifest(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        checksum, name = line.split(maxsplit=1)
        require(re.fullmatch(r"[0-9a-f]{64}", checksum), "invalid reference SHA256")
        relative = PurePosixPath(name.lstrip("*"))
        if relative.is_absolute():
            relative = relative.relative_to("/g4data")
        require(relative.parts and ".." not in relative.parts and "\\" not in str(relative), "invalid dataset manifest path")
        key = str(relative)
        require(key not in result, "duplicate dataset manifest path")
        result[key] = checksum
    require(result, "empty dataset reference manifest")
    return result


def verify_data_manifest(actual, reference):
    found, expected = read_data_manifest(actual), read_data_manifest(reference)
    require(found.keys() == expected.keys(), "dataset reference file set mismatch (missing or extra files)")
    require(found == expected, "dataset content differs from trusted reference SHA256")


def elf_identity(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    require(header[:6] == b"\x7fELF\x02\x01" and header[18:20] == b">\0", "build did not produce an x86_64 ELF executable")
    require(os.access(path, os.X_OK), "built ELF is not executable")
    return {"format": "ELF64", "architecture": "x86_64", "byte_order": "little", "sha256": sha(path)}


def container_command(source, output, sif, data, shell):
    setup = 'set -euo pipefail\nexport GEANT4_DATA_DIR=/g4data\nsource /opt/geant4/bin/geant4.sh\nexport G4RUN_MANAGER_TYPE=Serial OMP_NUM_THREADS=1\n'
    return ["apptainer", "exec", "--cleanenv", "--bind", str(source) + ":/layout-source:ro",
            "--bind", str(data) + ":/g4data:ro", "--bind", str(output) + ":/layout-build",
            "--pwd", "/layout-build", str(sif), "bash", "-c", setup + shell]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--sif", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-data-manifest", type=Path,
                        help="Trusted complete SHA256 manifest; defaults to data-dir/layout-study-SHA256SUMS")
    parser.add_argument("--timeout-seconds", type=int, default=3300)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2]
    sif, data, output = map(safe_path, (args.sif, args.data_dir, args.output_dir))
    source = safe_path(source)
    require(sif.is_file() and data.is_dir(), "existing SIF and dataset directory required")
    reference = safe_path(args.reference_data_manifest or data / "layout-study-SHA256SUMS")
    require(reference.is_file(), "trusted dataset reference required: supply --reference-data-manifest or install datasets using install_data.sh")
    require(not output.exists() and data not in output.parents and output not in data.parents, "fresh output outside dataset directory required")
    require(0 < args.timeout_seconds <= 7200, "timeout must be 1..7200 seconds")
    scheduler = compute_identity(os.environ, socket.gethostname())
    sources = source_snapshot(source, args.source_commit)
    from run_local import run_process
    output.mkdir(parents=True)
    record = {"schema_version": "steel-layout-runtime-build-v1", "status": "failed", "scheduler": scheduler,
              "source_commit": args.source_commit, "source_files_sha256": sources, "commands": []}
    try:
        sif_hash = sha(sif)
        record["sif_sha256"] = sif_hash
        reference_copy = output / "reference-data-SHA256SUMS"
        reference_copy.write_bytes(reference.read_bytes())
        record.update(reference_manifest_path=str(reference_copy), reference_manifest_sha256=sha(reference_copy))
        read_data_manifest(reference_copy)
        def execute(name, shell, timeout):
            command = container_command(source, output, sif, data, shell)
            log = output / (name + ".log")
            entry = {"phase": name, "command": command, "returncode": None}
            record["commands"].append(entry)
            started = time.monotonic()
            try:
                with log.open("x") as stream:
                    code, timed_out = run_process(command, cwd=source, env=os.environ.copy(), log=stream, timeout=timeout)
                entry.update(returncode=code, timed_out=timed_out)
                require(code == 0 and not timed_out, name + " failed; inspect " + str(log))
            finally:
                entry.update(elapsed_seconds=time.monotonic() - started, log_sha256=sha(log))
        execute("environment", 'geant4-config --version > geant4-version.txt\nuname -m > architecture.txt\nc++ --version > compiler.txt\ncmake --version > cmake.txt\ngeant4-config --datasets > datasets.txt\n', 120)
        version, architecture = (output / "geant4-version.txt").read_text().strip(), (output / "architecture.txt").read_text().strip()
        require(version == "11.4.2" and architecture == "x86_64", "Geant4 11.4.2 x86_64 required")
        directories = dataset_directories((output / "datasets.txt").read_text(), data)
        manifest = output / "dataset-SHA256SUMS"
        count = hash_datasets(data, directories, manifest)
        record.update(dataset_manifest_sha256=sha(manifest), inventoried_file_count=count)
        verify_data_manifest(manifest, reference_copy)
        preflight = output / "dataset-preflight.json"
        write_json(preflight, {"schema_version": "steel-layout-dataset-preflight-v1", "status": "verified",
                              "geant4_version": version, "dataset_dir": str(data), "dataset_manifest_sha256": sha(manifest),
                              "verified_file_count": count, "job_id": scheduler["job_id"], "node": scheduler["node"],
                              "reference_manifest_path": str(reference_copy), "reference_manifest_sha256": sha(reference_copy),
                              "verification_scope": "full-file-set-and-sha256-match"})
        record.update(datasets=directories, compiler=(output / "compiler.txt").read_text(), cmake=(output / "cmake.txt").read_text(),
                      geant4_version=version, architecture=architecture, dataset_manifest_sha256=sha(manifest))
        execute("build", 'cmake -S /layout-source/test/OpNovice2 -B /layout-build/build -DWITH_GEANT4_UIVIS=OFF -DCMAKE_BUILD_TYPE=Release\ncmake --build /layout-build/build -j1\n', args.timeout_seconds)
        require(source_snapshot(source, args.source_commit) == sources, "source changed during build")
        require(sha(sif) == sif_hash, "SIF changed during build")
        executable = output / "build/OpNovice2"
        record["elf"] = elf_identity(executable)
        runtime = {"simulation_identity": {"source_commit": args.source_commit, "geant4_version": version,
                   "architecture": architecture, "run_manager": "Serial", "executable_sha256": record["elf"]["sha256"],
                   "sif_sha256": sif_hash, "dataset_manifest_sha256": sha(manifest)},
                   "paths": {"source_dir": str(source), "executable": str(executable), "sif": str(sif),
                             "dataset_dir": str(data), "dataset_manifest": str(manifest), "dataset_preflight": str(preflight)},
                   "dataset_preflight_sha256": sha(preflight)}
        write_json(output / "runtime.json", runtime)
        record["status"] = "built-and-datasets-verified"
    except BaseException as error:
        record["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        write_json(output / "build-record.json", record)
    print(str(output / "runtime.json"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print("runtime build failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
