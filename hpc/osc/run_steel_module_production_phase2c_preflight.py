#!/usr/bin/env python3
"""Run the frozen Phase-2C production-like probe without invoking Geant4."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from steel_module_campaign_lib import resolve_recorded_artifact, sha256_file
from steel_module_production_container_contract import (
    AdditionalBind,
    CONTAINER_CONTROL_ROOT,
    CONTAINER_DATA_ROOT,
    CONTAINER_EXECUTION_ROOT,
    CONTAINER_PREBUILT_ROOT,
    CONTAINER_SIMULATION_ROOT,
    NO_MOUNT_CLASSES,
    ProductionContainerInputs,
    build_production_apptainer_prefix,
    inspect_apptainer_runtime_identity,
    production_apptainer_host_environment,
)
from steel_module_production_phase2c_preflight_lib import (
    CLOSURE_SCHEMA_VERSION,
    CONTAINER_REPORT_KEYS,
    MOUNTINFO_CONTRACT_VERSION,
    RAW_RESULT_SCHEMA_VERSION,
    _raw_directory,
    _fsync_directory,
    _fsync_tree,
    _write_checksum_manifest,
    _write_exclusive_bytes,
    _write_exclusive_json,
    load_formal_context,
    utc_now,
)


CONTAINER_WORKSPACE = "/work/phase2c-preflight"

CONTAINER_SCRIPT = r"""
set -eu
probe="$1"
simulation="$2"
control="$3"
execution="$4"
data="$5"
prebuilt="$6"
challenge="$probe/challenge.bin"
roundtrip="$probe/roundtrip.bin"
report="$probe/container-report.tsv"

mount_rows() {
  awk -v target="$1" '$5 == target { count += 1 } END { print count + 0 }' /proc/self/mountinfo
}

mount_options() {
  awk -v target="$1" '$5 == target {
    separator = 0
    for (i = 7; i <= NF; i += 1) if ($i == "-") { separator = i; break }
    if (separator == 0 || separator + 3 > NF) exit 2
    print $6 "|" $(separator + 3)
  }' /proc/self/mountinfo
}

has_option() {
  options="$(printf '%s' "$1" | tr '|' ',')"
  case ",${options}," in *",$2,"*) return 0;; *) return 1;; esac
}

mount_has_exact_option() {
  target="$1"
  option="$2"
  test "$(mount_rows "$target")" = 1 || return 1
  options="$(mount_options "$target")" || return 1
  has_option "$options" "$option" || return 1
}

create_rejected() {
  target="$1/.phase2c-forbidden-write"
  if (umask 077; : > "$target") 2>/dev/null; then
    rm -f -- "$target" 2>/dev/null || true
    return 1
  fi
  return 0
}

bool_row() { printf '%s\t%s\n' "$1" "$2" >> "$report"; }

: > "$report"
for pair in \
  "simulation_root:$simulation" \
  "control_root:$control" \
  "execution_root:$execution" \
  "data_root:$data" \
  "prebuilt_root:$prebuilt"
do
  label="${pair%%:*}"
  target="${pair#*:}"
  if mount_has_exact_option "$target" ro; then value=true; else value=false; fi
  bool_row "mountinfo_${label}_read_only" "$value"
done
for pair in \
  "simulation_root:$simulation" \
  "control_root:$control" \
  "execution_root:$execution" \
  "data_root:$data" \
  "prebuilt_root:$prebuilt"
do
  label="${pair%%:*}"
  target="${pair#*:}"
  if create_rejected "$target"; then value=true; else value=false; fi
  bool_row "${label}_create_rejected" "$value"
done
if mount_has_exact_option "$probe" rw; then external_mount_rw=true
else external_mount_rw=false; fi
bool_row mountinfo_external_probe_root_read_write "$external_mount_rw"

external_rw=false
if cp -- "$challenge" "$roundtrip" 2>/dev/null; then
  external_rw=true
fi
bool_row external_probe_root_writable "$external_rw"
roundtrip_matches=false
if [ "$external_rw" = true ] && cmp -s -- "$challenge" "$roundtrip"; then
  roundtrip_matches=true
fi
bool_row challenge_roundtrip_matches "$roundtrip_matches"

test "$(awk -F '\t' '$2 != "true" { count += 1 } END { print count + 0 }' "$report")" = 0
test "$(wc -l < "$report" | tr -d ' ')" = 13
test "$external_mount_rw" = true
test "$external_rw" = true
test "$roundtrip_matches" = true
"""


def _environment_for_twin(twin: Any) -> dict[str, Any]:
    predecessor = getattr(twin, "predecessor", None)
    if predecessor is None:
        raise ValueError("formal Phase-2C twin lacks its validated predecessor")
    value = getattr(predecessor, "environment", None)
    if isinstance(value, dict):
        return value
    managed = getattr(predecessor, "managed_child", None)
    plan = getattr(managed, "plan", None)
    value = getattr(plan, "environment", None)
    if not isinstance(value, dict):
        raise ValueError("formal Phase-2C twin lacks a complete runtime environment")
    return value


def _parse_report(path: Path) -> dict[str, bool]:
    if path.is_symlink() or not path.is_file():
        return {}
    values: dict[str, bool] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        pieces = raw.split("\t")
        if len(pieces) != 2 or pieces[0] in values or pieces[1] not in {"true", "false"}:
            return {}
        values[pieces[0]] = pieces[1] == "true"
    return values if tuple(values) == CONTAINER_REPORT_KEYS else {}


def _scheduler_identity(
    environment: Mapping[str, str], *, expected_job_name: str
) -> tuple[str, str]:
    job_id = environment.get("SLURM_JOB_ID", "")
    job_name = environment.get("SLURM_JOB_NAME", "")
    if not job_id.isdigit() or job_id.startswith("0") or job_name != expected_job_name:
        raise ValueError("Phase-2C probe lacks its exact Slurm identity")
    if any(
        environment.get(name)
        for name in (
            "SLURM_ARRAY_JOB_ID",
            "SLURM_ARRAY_TASK_ID",
            "SLURM_ARRAY_TASK_COUNT",
            "SLURM_ARRAY_TASK_MIN",
            "SLURM_ARRAY_TASK_MAX",
            "SLURM_ARRAY_TASK_STEP",
        )
    ):
        raise ValueError("Phase-2C preflight must be a non-array job")
    if not environment.get("SLURMD_NODENAME"):
        raise ValueError("Phase-2C preflight lacks compute-node identity")
    return job_id, job_name


def _lock_record(context: Any) -> tuple[Path, dict[str, Any]]:
    record = context.manifest.get("portable_lock")
    if not isinstance(record, dict):
        raise ValueError("Phase-2C twin portable-lock record is missing")
    path = context.directory / str(record.get("path", ".control.lock"))
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2C twin portable lock is unsafe")
    status = path.stat()
    if (
        status.st_mode & 0o777 != 0o600
        or status.st_size != 0
        or status.st_nlink != 1
        or sha256_file(path) != hashlib.sha256(b"").hexdigest()
    ):
        raise ValueError("Phase-2C twin portable lock contract mismatch")
    return path, record


def _closure_marker_path(context: Any) -> Path:
    return context.directory / ".phase2c-preflight-closed.json"


def run_preflight(
    *,
    repo_root: Path,
    execution_dir: Path,
    host_environment: Mapping[str, str] | None = None,
    apptainer_path: Path | None = None,
) -> Path:
    context = load_formal_context(repo_root.resolve())
    if context.directory != execution_dir.expanduser().resolve():
        raise ValueError("Phase-2C launcher execution path is not canonical")
    source_environment = os.environ if host_environment is None else host_environment
    job_id, job_name = _scheduler_identity(
        source_environment, expected_job_name=context.job_name
    )
    clean_environment = production_apptainer_host_environment(source_environment)
    workspace = _raw_directory(context)
    if workspace.exists() or workspace.is_symlink():
        raise ValueError("Phase-2C raw preflight workspace already exists")
    if not workspace.parent.is_dir() or workspace.parent.is_symlink():
        raise ValueError("Phase-2C raw preflight parent is missing or unsafe")
    marker_path = _closure_marker_path(context)
    if marker_path.exists() or marker_path.is_symlink():
        raise ValueError("Phase-2C sacrificial twin is already closed")

    lock_path, _record = _lock_record(context)
    descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    workspace_created = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if marker_path.exists() or marker_path.is_symlink():
            raise ValueError("Phase-2C sacrificial twin closed concurrently")
        workspace.mkdir(mode=0o700)
        workspace_created = True
        challenge = workspace / "challenge.bin"
        _write_exclusive_bytes(challenge, secrets.token_bytes(64))
        challenge_sha = sha256_file(challenge)

        environment = _environment_for_twin(context.twin)
        image, image_sha = resolve_recorded_artifact(environment.get("image"), "Apptainer image")
        data_manifest, data_manifest_sha = resolve_recorded_artifact(
            environment.get("g4_data_manifest"), "Geant4 data manifest"
        )
        executable, executable_sha = resolve_recorded_artifact(
            environment.get("build_artifact"), "prebuilt executable"
        )
        recorded_runtime = context.manifest.get("runtime", {})
        if (
            recorded_runtime.get("image_sha256") != image_sha
            or recorded_runtime.get("g4_data_manifest_sha256") != data_manifest_sha
            or recorded_runtime.get("executable_sha256") != executable_sha
        ):
            raise ValueError("Phase-2C runtime hashes differ from the twin")
        data_root = Path.home() / "geant4-data" / "11.4.2"
        if data_root.is_symlink() or not data_root.is_dir():
            raise ValueError("canonical Geant4 data root is missing or unsafe")
        if data_manifest.parent == data_root:
            data_root = data_manifest.parent

        selected = apptainer_path
        if selected is None:
            found = shutil.which("apptainer")
            if found is None:
                raise ValueError("apptainer is unavailable")
            selected = Path(found)
        runtime_identity = inspect_apptainer_runtime_identity(
            selected, environment=clean_environment, cwd=context.directory
        )
        sources = context.manifest.get("sources", {})
        simulation = context.directory / sources["simulation"]["path"]
        control = context.directory / sources["control_plane"]["path"]
        prefix = build_production_apptainer_prefix(
            apptainer=Path(runtime_identity["apptainer_path"]),
            inputs=ProductionContainerInputs(
                image=image,
                simulation_root=simulation,
                control_root=control,
                execution_root=context.directory,
                data_root=data_root,
                executable_directory=executable.parent,
            ),
            additional_binds=(
                AdditionalBind(workspace, CONTAINER_WORKSPACE, writable=True),
            ),
        )
        command = [
            *prefix,
            "sh",
            "-c",
            CONTAINER_SCRIPT,
            "sh",
            CONTAINER_WORKSPACE,
            CONTAINER_SIMULATION_ROOT,
            CONTAINER_CONTROL_ROOT,
            CONTAINER_EXECUTION_ROOT,
            CONTAINER_DATA_ROOT,
            CONTAINER_PREBUILT_ROOT,
        ]
        result = subprocess.run(
            command,
            cwd=control,
            env=clean_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        _write_exclusive_bytes(workspace / "stdout.txt", result.stdout.encode("utf-8"))
        _write_exclusive_bytes(workspace / "stderr.txt", result.stderr.encode("utf-8"))
        report_path = workspace / "container-report.tsv"
        roundtrip_path = workspace / "roundtrip.bin"
        if not report_path.exists():
            _write_exclusive_bytes(report_path, b"")
        if not roundtrip_path.exists():
            _write_exclusive_bytes(roundtrip_path, b"")
        report = _parse_report(report_path)
        roundtrip_sha = sha256_file(roundtrip_path)
        passed = (
            result.returncode == 0
            and report
            and all(report.values())
            and roundtrip_sha == challenge_sha
        )
        payload = {
            "schema_version": RAW_RESULT_SCHEMA_VERSION,
            "created_at_utc": utc_now(),
            "test_mode": context.manifest.get("test_mode") is True,
            "accepted_compute_preflight_evidence": (
                passed and context.manifest.get("test_mode") is False
            ),
            "probe_passed": bool(passed),
            "apptainer_invoked": True,
            "geant4_invoked": False,
            "events_consumed": 0,
            "production_seeds_consumed": 0,
            "twin_execution_closed": True,
            "twin_id": context.twin_id,
            "twin_hash": context.twin_hash,
            "production_equivalence_hash": context.production_equivalence_hash,
            "slurm_job_id": job_id,
            "slurm_job_name": job_name,
            "scheduler_account": source_environment.get(
                "SLURM_JOB_ACCOUNT", "pas2524"
            ),
            "compute_node": {
                "hostname": socket.gethostname(),
                "slurmd_nodename": source_environment.get("SLURMD_NODENAME"),
            },
            "runtime_identity": runtime_identity,
            "container_contract": {
                "cleanenv": True,
                "containall": True,
                "no_home": True,
                "no_mount": NO_MOUNT_CLASSES,
                "fixed_read_only_mounts": 5,
                "external_read_write_mounts": 1,
                "mountinfo_contract_version": MOUNTINFO_CONTRACT_VERSION,
                "command_sha256": hashlib.sha256(
                    "\0".join(command).encode("utf-8")
                ).hexdigest(),
                "report": report,
            },
            "return_code": result.returncode,
            "challenge_sha256": challenge_sha,
            "roundtrip_sha256": roundtrip_sha,
        }
        _write_exclusive_json(workspace / "probe_result.json", payload)
        raw_result_sha = sha256_file(workspace / "probe_result.json")
        closure = {
            "schema_version": CLOSURE_SCHEMA_VERSION,
            "created_at_utc": utc_now(),
            "twin_id": context.twin_id,
            "twin_hash": context.twin_hash,
            "production_equivalence_hash": context.production_equivalence_hash,
            "slurm_job_id": job_id,
            "slurm_job_name": job_name,
            "raw_result_sha256": raw_result_sha,
            "execution_closed": True,
            "future_execution_role": "clean-execution-v6",
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        }
        _write_exclusive_json(marker_path, closure)
        marker_path.chmod(0o400)
        _fsync_directory(context.directory)
        _write_checksum_manifest(workspace)
        for path in workspace.iterdir():
            if path.is_file():
                path.chmod(0o444)
        _fsync_tree(workspace)
        if not passed:
            raise ValueError("Phase-2C container-isolation checks failed")
        return workspace
    except BaseException:
        if workspace_created and workspace.exists():
            # Preserve all created evidence.  Never delete or retry a workspace
            # after the compute job has started.
            pass
        raise
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--execution-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output = run_preflight(
            repo_root=args.repo_root,
            execution_dir=args.execution_dir,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot run steel-module Phase-2C preflight: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2C no-Geant4 preflight: PASS")
    print(f"raw workspace: {output}")
    print("Apptainer invoked: true")
    print("Geant4 invoked: false")
    print("events consumed: 0")
    print("production seeds consumed: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
