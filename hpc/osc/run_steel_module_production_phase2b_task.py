#!/usr/bin/env python3
"""Run one checksum-bound managed BC-S1 task through Apptainer."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from record_steel_module_production_task_result import record
from steel_module_campaign_lib import load_json, resolve_recorded_artifact, sha256_file
from steel_module_production_container_contract import (
    AdditionalBind,
    ProductionContainerInputs,
    build_production_apptainer_prefix,
    production_apptainer_host_environment,
    validate_apptainer_runtime_identity,
)
from steel_module_production_phase2b_lib import (
    execution_task_by_id,
    load_attempt_intent,
    validate_worker_scheduler_identity,
)
from steel_module_production_successor_lib import (
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    load_execution_for_frozen_worker as load_legacy_execution_for_frozen_worker,
    verify_successor_recovery_readiness,
)
from steel_module_production_phase2c_lib import EXECUTION_V6_SCHEMA_VERSION


CONTAINER_SCRIPT = r"""
set -euo pipefail
simulation_root="$1"; control_root="$2"; execution_root="$3"
data_root="$4"; executable="$5"; attempt_id="$6"; logical_id="$7"
logical_index="$8"; campaign_id="$9"; plan_hash="${10}"; git_commit="${11}"
environment_mode="${12}"; environment_identity="${13}"; image_sha="${14}"
data_manifest_sha="${15}"; executable_sha="${16}"; shift 16

cd "${simulation_root}/test/OpNovice2"
if [[ -f /opt/geant4/bin/geant4.sh ]]; then source /opt/geant4/bin/geant4.sh; fi
source "${simulation_root}/hpc/osc/configure_geant4_data_env.sh"
configure_geant4_data_env 0
task_root="${execution_root}/attempts/${attempt_id}/tasks/${logical_id}"
if [[ -e "${task_root}/task_result.json" ]]; then
  echo "Refusing to overwrite completed managed task." >&2; exit 1
fi
if [[ -d "${task_root}/runs" ]] && find "${task_root}/runs" -mindepth 1 -print -quit | grep -q .; then
  echo "Refusing to reuse non-empty managed task runs." >&2; exit 1
fi
mkdir -p "${task_root}/runs"
export SCAN_RUNS_DIR="${task_root}/runs"
export LATEST_RUN_LINK="${task_root}/latest"
export LATEST_POINTS_CSV="${task_root}/points.csv"
export LATEST_RUN_CONFIG="${task_root}/run_config.json"
export LATEST_EFFICIENCY_MAP="${task_root}/efficiency_map.csv"
export SCAN_GIT_COMMIT="${git_commit}"
export SCAN_GIT_BRANCH="detached-frozen-simulation-source"
export SCAN_GIT_DIRTY=false
export SCAN_RUN_ID_SUFFIX="${attempt_id}_${logical_id}"
export G4RUN_MANAGER_TYPE=Serial
export RN_ATTEMPT_ID="${attempt_id}"
export RN_LOGICAL_TASK_INDEX="${logical_index}"
export RN_LOGICAL_TASK_ID="${logical_id}"
export RN_PLAN_HASH="${plan_hash}"
export RN_GIT_COMMIT="${git_commit}"
export RN_ENVIRONMENT_MODE="${environment_mode}"
export RN_ENVIRONMENT_IDENTITY="${environment_identity}"
export RN_IMAGE_SHA256="${image_sha}"
export RN_G4_DATA_MANIFEST_SHA256="${data_manifest_sha}"
export RN_EXECUTABLE_SHA256="${executable_sha}"
OPNOVICE2_EXECUTABLE="${executable}" PLOT_WITH_ROOT=0 ./run_sipm_cavity_scan.sh "$@"
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--intent-sha256", required=True)
    parser.add_argument("--array-index", required=True, type=int)
    return parser.parse_args()


def _load_execution_for_frozen_worker(
    execution_dir: Path, *, control_root: Path
):
    """Schema-dispatch before any task output or runtime invocation."""

    manifest = load_json(execution_dir / "managed_execution.json")
    if manifest.get("schema_version") == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_lib import (
            load_execution_v6_for_frozen_worker,
        )

        return load_execution_v6_for_frozen_worker(
            execution_dir, control_root=control_root
        )
    return load_legacy_execution_for_frozen_worker(
        execution_dir, control_root=control_root
    )


def run_task(args: argparse.Namespace) -> Path:
    execution_dir = args.execution_dir.expanduser().resolve()
    control_root = Path(__file__).resolve().parents[2]
    execution = _load_execution_for_frozen_worker(
        execution_dir, control_root=control_root
    )
    intent = load_attempt_intent(execution, args.attempt_id)
    readiness: dict[str, object] | None = None
    execution_schema = execution.manifest.get("schema_version")
    if execution_schema == EXECUTION_V6_SCHEMA_VERSION:
        from steel_module_production_phase2c_readiness import (
            verify_phase2c_readiness,
        )

        readiness_path, readiness = verify_phase2c_readiness(
            execution, repo_root=None
        )
        if intent.get("readiness_lock_sha256") != sha256_file(readiness_path):
            raise ValueError("execution-v6 intent/R6-readiness checksum mismatch")
    elif execution_schema == SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        readiness_path, readiness = verify_successor_recovery_readiness(
            execution, repo_root=None
        )
        if intent.get("readiness_lock_sha256") != sha256_file(readiness_path):
            raise ValueError("successor intent/additive-readiness checksum mismatch")
    if intent.get("intent_sha256") != args.intent_sha256:
        raise ValueError("worker intent SHA-256 mismatch")
    validate_worker_scheduler_identity(
        execution,
        attempt_id=args.attempt_id,
        intent=intent,
        array_index=args.array_index,
        environment=os.environ,
    )
    selected = intent["selected"]["logical_task_ids"]
    if args.array_index < 1 or args.array_index > len(selected):
        raise ValueError("Slurm array index is outside the frozen intent")
    logical_id = selected[args.array_index - 1]
    task = execution_task_by_id(execution)[logical_id]
    intent_dir = execution_dir / "intents" / args.attempt_id
    with (intent_dir / "tasks.tsv").open(encoding="utf-8") as stream:
        lines = stream.read().splitlines()
    if len(lines) != len(selected) + 1:
        raise ValueError("intent task-map row count mismatch")
    scan_lines = (intent_dir / "scan_args.txt").read_text(encoding="utf-8").splitlines()
    if len(scan_lines) != len(selected):
        raise ValueError("intent scan-args row count mismatch")
    scan_args = shlex.split(scan_lines[args.array_index - 1])

    source_records = execution.manifest["sources"]
    simulation_root = execution_dir / source_records["simulation"]["path"]
    control_source = execution_dir / source_records["control_plane"]["path"]
    if control_source.resolve() != control_root:
        raise ValueError("worker is not running from the frozen control-plane source")
    environment = execution.environment
    image, image_sha = resolve_recorded_artifact(environment["image"], "Apptainer image")
    _, data_manifest_sha = resolve_recorded_artifact(
        environment["g4_data_manifest"], "Geant4 data manifest"
    )
    executable, executable_sha = resolve_recorded_artifact(
        environment["build_artifact"], "prebuilt executable"
    )
    if not os.access(executable, os.X_OK):
        raise ValueError("frozen prebuilt executable is not executable")
    data_root = Path.home() / "geant4-data" / "11.4.2"
    if not data_root.is_dir():
        raise ValueError(f"missing canonical Geant4 data root: {data_root}")
    apptainer = shutil.which("apptainer")
    if apptainer is None:
        raise ValueError("apptainer is not available")
    container_environment = production_apptainer_host_environment()
    if readiness is not None:
        preflight = readiness.get("compute_preflight")
        if not isinstance(preflight, dict):
            raise ValueError("successor readiness lacks R3 compute-preflight identity")
        validate_apptainer_runtime_identity(
            Path(apptainer),
            expected={
                name: preflight.get(name)
                for name in (
                    "apptainer_path",
                    "apptainer_sha256",
                    "apptainer_version",
                )
            },
            environment=container_environment,
            cwd=control_source,
        )
    attempt_root = execution_dir / "attempts" / args.attempt_id
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        raise ValueError("managed attempt output root is missing or unsafe")
    tasks_root = attempt_root / "tasks"
    tasks_root.mkdir(exist_ok=True)
    if tasks_root.is_symlink() or not tasks_root.is_dir():
        raise ValueError("managed tasks output root is unsafe")
    task_root = tasks_root / logical_id
    try:
        task_root.mkdir()
    except FileExistsError as exc:
        raise ValueError("managed task output directory already exists") from exc
    if task_root.is_symlink() or task_root.resolve().parent != tasks_root.resolve():
        raise ValueError("managed task output directory escaped its attempt")
    container_task_root = (
        "/work/g4optics-execution/attempts/"
        f"{args.attempt_id}/tasks/{logical_id}"
    )

    command = [
        *build_production_apptainer_prefix(
            apptainer=Path(apptainer),
            inputs=ProductionContainerInputs(
                image=image,
                simulation_root=simulation_root,
                control_root=control_source,
                execution_root=execution_dir,
                data_root=data_root,
                executable_directory=executable.parent,
            ),
            additional_binds=(
                AdditionalBind(task_root, container_task_root, writable=True),
            ),
        ),
        "bash", "-lc", CONTAINER_SCRIPT, "bash",
        "/work/g4optics-simulation", "/work/g4optics-control",
        "/work/g4optics-execution", "/opt/geant4-data",
        f"/work/g4optics-prebuilt/{executable.name}", args.attempt_id,
        logical_id, str(task.task_index), execution.campaign_id, execution.plan_hash,
        execution.git_commit, environment["mode"], environment["identity_hash"],
        image_sha, data_manifest_sha, executable_sha, *scan_args,
    ]
    result = subprocess.run(
        command,
        cwd=control_source,
        env=container_environment,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"managed Apptainer task failed with exit code {result.returncode}")
    recorder_args = argparse.Namespace(
        execution_dir=execution_dir, control_source_root=control_root,
        attempt_id=args.attempt_id, intent_sha256=args.intent_sha256,
        logical_task_id=logical_id, task_root=task_root,
    )
    return record(recorder_args)


def main() -> int:
    args = parse_args()
    try:
        marker = run_task(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot run managed steel-module production task: {exc}", file=sys.stderr)
        return 1
    print(f"Managed task complete: {marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
