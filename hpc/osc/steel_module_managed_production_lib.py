#!/usr/bin/env python3
"""Phase-2A materialization and validation for the managed BC-S1 child."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steel_module_campaign_lib import (
    STUDY_PRESET,
    CampaignBundle,
    CampaignTask,
    canonical_json,
    environment_identity,
    load_json,
    parse_campaign_tasks,
    read_scan_args,
    require_dict,
    require_sha1,
    require_sha256,
    require_string,
    resolve_recorded_artifact,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_program_lib import (
    AUTHORIZATION_GRAPH_SCHEMA_VERSION,
    CHILD_PLAN_SCHEMA_VERSION,
    ProductionProgramBundle,
    load_production_program,
    materialized_tasks,
    ordered_task_hash,
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)


MANAGED_BINDING_SCHEMA_VERSION = "steel-module-managed-production-child-v1"
MANAGED_PLAN_SCHEMA_VERSION = "steel-module-managed-production-plan-v1"
FORMAL_LOCK_SCHEMA_VERSION = "steel-module-production-program-lock-v1"
FORMAL_LOCK_RELATIVE = Path(
    "hpc/osc/configurations/steel-module-production-program-v1.lock.json"
)
INITIAL_CHILD_ID = "BC-S1"
FORMAL_CHILD_DIRECTORY_NAME = "steel-module-production-bc-s1"
ROOT_REQUIRED_FILES = {
    "README.md",
    "managed_child.json",
    "program_binding.json",
    "scan_args.txt",
    "tasks.tsv",
}
CONTROL_PLANE_ARTIFACTS = (
    "hpc/osc/configurations/steel-module-production-program-v1.lock.json",
    "hpc/osc/materialize_steel_module_production_child.py",
    "hpc/osc/steel_module_campaign_lib.py",
    "hpc/osc/steel_module_managed_production_lib.py",
    "hpc/osc/steel_module_production_program_lib.py",
    "hpc/osc/submit_steel_module_campaign.py",
    "hpc/osc/submit_steel_module_production_child.py",
)


@dataclass(frozen=True)
class ManagedProductionChild:
    directory: Path
    plan: CampaignBundle
    binding: dict[str, Any]

    @property
    def child_id(self) -> str:
        return require_string(require_dict(self.binding, "child"), "child_id")


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _git_blob_sha256(repo_root: Path, commit: str, relative: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"cannot read recorded control-plane artifact {relative}: {detail}")
    return sha256_bytes(result.stdout)


def _control_plane_identity(
    repo_root: Path, *, require_clean: bool
) -> dict[str, Any]:
    root = repo_root.expanduser().resolve()
    if not (root / ".git").exists():
        raise ValueError(f"control-plane root is not a Git checkout: {root}")
    dirty_output = _run_git(root, "status", "--porcelain", "--untracked-files=all")
    dirty = bool(dirty_output)
    if require_clean and dirty:
        raise ValueError("formal materialization requires a clean control-plane checkout")
    artifacts: dict[str, dict[str, str]] = {}
    for relative in CONTROL_PLANE_ARTIFACTS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"missing control-plane artifact: {relative}")
        artifacts[relative] = {"sha256": sha256_file(path)}
    return {
        "git_commit": _run_git(root, "rev-parse", "HEAD"),
        "git_tree": _run_git(root, "rev-parse", "HEAD^{tree}"),
        "branch": _run_git(root, "branch", "--show-current"),
        "dirty": dirty,
        "dirty_paths": dirty_output.splitlines() if dirty else [],
        "artifacts": artifacts,
    }


def _write_tasks(path: Path, tasks: tuple[CampaignTask, ...]) -> None:
    import csv

    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(CampaignTask.__dataclass_fields__),
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(asdict(task) for task in tasks)


def _write_checksums(directory: Path) -> None:
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{sha256_file(directory / name)}  {name}\n"
            for name in sorted(ROOT_REQUIRED_FILES)
        ),
        encoding="utf-8",
    )


def _verify_exact_checksums(directory: Path) -> None:
    checksum_path = directory / "SHA256SUMS"
    if not checksum_path.is_file():
        raise ValueError("managed child is missing SHA256SUMS")
    recorded: set[str] = set()
    for raw in checksum_path.read_text(encoding="utf-8").splitlines():
        parts = raw.split("  ", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid managed child checksum row: {raw!r}")
        digest, name = parts
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or Path(name).name != name
            or name in recorded
        ):
            raise ValueError(f"invalid managed child checksum identity: {raw!r}")
        path = directory / name
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"managed child checksum mismatch: {name}")
        recorded.add(name)
    if recorded != ROOT_REQUIRED_FILES:
        raise ValueError(
            "managed child checksum set mismatch: "
            f"expected {sorted(ROOT_REQUIRED_FILES)}, got {sorted(recorded)}"
        )
    allowed = ROOT_REQUIRED_FILES | {"SHA256SUMS"}
    entries = {path.name for path in directory.iterdir()}
    if entries != allowed:
        raise ValueError(
            "managed Phase-2A child contains unexpected root entries: "
            f"{sorted(entries - allowed)}"
        )
    if any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError("managed Phase-2A child root must not contain symlinks")


def _formal_lock(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    path = repo_root.expanduser().resolve() / FORMAL_LOCK_RELATIVE
    value = load_json(path)
    if value.get("schema_version") != FORMAL_LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported formal production-program lock schema")
    if value.get("status") != "accepted-formal-phase-1":
        raise ValueError("formal production-program lock is not accepted")
    if value.get("study_preset") != STUDY_PRESET:
        raise ValueError("formal lock study preset mismatch")
    if value.get("policy_id") != "steel-module-production-sms016-019-v1":
        raise ValueError("formal lock production policy mismatch")
    if value.get("accepted_statistical_evidence") is not True:
        raise ValueError("formal lock is not accepted statistical evidence")
    if value.get("submittable") is not False:
        raise ValueError("formal parent lock must remain non-submittable")
    if value.get("initial_managed_child") != INITIAL_CHILD_ID:
        raise ValueError("formal lock does not authorize BC-S1 as the initial child")
    if value.get("task_count") != 914 or value.get("event_count") != 228_500:
        raise ValueError("formal lock parent totals are invalid")
    if value.get("locked_children") != ["FIXED", "BC-S2", "BC-S3", "BC-S4"]:
        raise ValueError("formal lock child authorization boundary is invalid")
    require_string(value, "program_directory")
    require_string(value, "program_id")
    for key in (
        "program_hash",
        "production_program_json_sha256",
        "root_checksum_manifest_sha256",
        "parent_plan_hash",
        "authorization_graph_hash",
    ):
        require_sha256(value, key)
    return path, value


def _validate_program_against_lock(
    bundle: ProductionProgramBundle,
    lock_path: Path,
    lock: dict[str, Any],
) -> None:
    manifest = bundle.manifest
    if bundle.directory != Path(lock["program_directory"]).expanduser().resolve():
        raise ValueError("formal program directory does not match tracked lock")
    if sha256_file(bundle.directory / "production_program.json") != lock[
        "production_program_json_sha256"
    ]:
        raise ValueError("formal production_program.json digest does not match lock")
    if sha256_file(bundle.directory / "SHA256SUMS") != lock[
        "root_checksum_manifest_sha256"
    ]:
        raise ValueError("formal program SHA256SUMS digest does not match lock")
    for key in ("program_id", "program_hash"):
        if manifest.get(key) != lock.get(key):
            raise ValueError(f"formal program {key} does not match tracked lock")
    if manifest.get("accepted_statistical_evidence") is not True:
        raise ValueError("formal program is not accepted statistical evidence")
    if manifest.get("submittable") is not False:
        raise ValueError("formal production parent must remain non-submittable")
    if (
        manifest.get("task_count") != lock.get("task_count")
        or manifest.get("total_events") != lock.get("event_count")
    ):
        raise ValueError("formal program totals do not match tracked lock")
    identity = require_dict(manifest, "identity")
    if require_dict(identity, "parent").get("plan_hash") != lock["parent_plan_hash"]:
        raise ValueError("formal program parent plan hash does not match lock")
    if manifest.get("authorization_graph_hash") != lock["authorization_graph_hash"]:
        raise ValueError("formal authorization graph hash does not match lock")
    graph = require_dict(manifest, "authorization_graph")
    if graph.get("schema_version") != AUTHORIZATION_GRAPH_SCHEMA_VERSION:
        raise ValueError("formal authorization graph schema mismatch")
    initial = require_dict(graph, "initial")
    if initial.get("directly_authorized_children") != [INITIAL_CHILD_ID]:
        raise ValueError("formal graph does not directly authorize only BC-S1")
    if set(initial.get("default_denied_children", ())) != {
        "FIXED",
        "BC-S2",
        "BC-S3",
        "BC-S4",
    }:
        raise ValueError("formal graph locked-child set mismatch")

    source = require_dict(manifest, "program_source")
    locked_source = require_dict(lock, "program_source")
    for manifest_key, lock_key in (
        ("git_commit", "commit"),
        ("git_tree", "tree"),
        ("branch", "branch"),
        ("dirty", "dirty"),
    ):
        if source.get(manifest_key) != locked_source.get(lock_key):
            raise ValueError(f"formal program source {manifest_key} does not match lock")

    runtime = require_dict(manifest, "runtime")
    locked_runtime = require_dict(lock, "runtime")
    if runtime.get("mode") != locked_runtime.get("mode"):
        raise ValueError("formal runtime mode does not match lock")
    if runtime.get("geant4_version") != locked_runtime.get("geant4_version"):
        raise ValueError("formal Geant4 version does not match lock")
    if runtime.get("environment_identity") != locked_runtime.get(
        "environment_identity"
    ):
        raise ValueError("formal runtime environment identity does not match lock")
    if require_dict(runtime, "image").get("sha256") != locked_runtime.get(
        "image_sha256"
    ):
        raise ValueError("formal image digest does not match lock")
    if require_dict(runtime, "g4_data_manifest").get(
        "sha256"
    ) != locked_runtime.get("g4_data_manifest_sha256"):
        raise ValueError("formal Geant4 data-manifest digest does not match lock")
    executable = require_dict(runtime, "executable")
    for manifest_key, lock_key in (
        ("sha256", "executable_sha256"),
        ("build_source_commit", "executable_build_source_commit"),
        ("build_source_tree", "executable_build_source_tree"),
    ):
        if executable.get(manifest_key) != locked_runtime.get(lock_key):
            raise ValueError(f"formal executable {manifest_key} does not match lock")


def load_formal_production_program(
    repo_root: Path, *, verify_runtime_artifacts: bool = True
) -> tuple[ProductionProgramBundle, Path, dict[str, Any]]:
    lock_path, lock = _formal_lock(repo_root)
    bundle = load_production_program(
        Path(lock["program_directory"]),
        verify_runtime_artifacts=verify_runtime_artifacts,
    )
    _validate_program_against_lock(bundle, lock_path, lock)
    return bundle, lock_path, lock


def _environment_from_program(
    program: ProductionProgramBundle, *, require_identity_match: bool
) -> dict[str, Any]:
    runtime = require_dict(program.manifest, "runtime")
    environment: dict[str, Any] = {
        "mode": require_string(runtime, "mode"),
        "geant4_version": require_string(runtime, "geant4_version"),
        "accepted_statistical_evidence": program.manifest.get(
            "accepted_statistical_evidence"
        )
        is True,
        "image": require_dict(runtime, "image"),
        "g4_data_manifest": require_dict(runtime, "g4_data_manifest"),
        "build_artifact": require_dict(runtime, "executable"),
    }
    environment["identity_hash"] = environment_identity(environment)
    if (
        require_identity_match
        and environment["identity_hash"] != runtime.get("environment_identity")
    ):
        raise ValueError("materialized plan environment identity mismatch")
    return environment


def _validate_formal_child_location(
    directory: Path,
    program: ProductionProgramBundle,
    *,
    allow_materialization_staging: bool = False,
) -> None:
    expected = (program.directory.parent / FORMAL_CHILD_DIRECTORY_NAME).resolve()
    if directory == expected:
        return
    staging_prefix = f".{FORMAL_CHILD_DIRECTORY_NAME}.tmp-"
    if (
        allow_materialization_staging
        and directory.parent == expected.parent
        and directory.name.startswith(staging_prefix)
        and len(directory.name) > len(staging_prefix)
    ):
        return
    raise ValueError(
        f"formal managed child directory must be the canonical path: {expected}"
    )


def _child_plan(program: ProductionProgramBundle) -> tuple[dict[str, Any], dict[str, Any]]:
    child_path = program.directory / "children" / INITIAL_CHILD_ID / "child_plan.json"
    child_plan = load_json(child_path)
    if child_plan.get("schema_version") != CHILD_PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported BC-S1 child-plan schema")
    descriptor = require_dict(child_plan, "descriptor")
    if descriptor.get("child_id") != INITIAL_CHILD_ID:
        raise ValueError("child plan is not BC-S1")
    return child_plan, descriptor


def _validate_bc_s1_shape(
    parent_tasks: tuple[CampaignTask, ...],
    materialized: tuple[CampaignTask, ...],
    descriptor: dict[str, Any],
) -> None:
    if len(parent_tasks) != 32 or len(materialized) != 32:
        raise ValueError("BC-S1 must contain exactly 32 tasks")
    if sum(task.events for task in materialized) != 8_000:
        raise ValueError("BC-S1 must contain exactly 8000 events")
    if [task.task_index for task in materialized] != list(range(1, 33)):
        raise ValueError("BC-S1 child-local indexes must be 1..32")
    if any(
        task.stage != "production"
        or task.sipm_layout != "back-center"
        or task.absorber_transverse_mm != 500
        or task.events != 250
        or task.tile_thickness_mm not in (4, 24)
        for task in materialized
    ):
        raise ValueError("BC-S1 task geometry/event shape is invalid")
    for thickness in (4, 24):
        blocks = {
            task.seed_block
            for task in materialized
            if task.tile_thickness_mm == thickness
        }
        if blocks != set(range(16)):
            raise ValueError(f"BC-S1 {thickness} mm blocks must be 0..15")
    for parent, child in zip(parent_tasks, materialized):
        if replace(child, task_index=parent.task_index) != parent:
            raise ValueError("BC-S1 materialization changed a frozen parent task")
    if ordered_task_hash(materialized) != descriptor.get(
        "materialized_campaign_plan_hash"
    ):
        raise ValueError("BC-S1 materialized campaign plan hash mismatch")
    if descriptor.get("task_count") != 32 or descriptor.get("event_count") != 8_000:
        raise ValueError("BC-S1 descriptor totals are invalid")
    if task_set_hash(parent_tasks) != descriptor.get("task_set_hash"):
        raise ValueError("BC-S1 task-set hash mismatch")


def _build_binding(
    *,
    program: ProductionProgramBundle,
    lock_path: Path | None,
    lock: dict[str, Any] | None,
    control_plane: dict[str, Any],
    scan_args_sha256: str,
    runtime_environment_identity: str,
    created_at_utc: str,
    test_mode: bool,
) -> dict[str, Any]:
    child_plan, descriptor = _child_plan(program)
    parent_tasks = program.child_tasks[INITIAL_CHILD_ID]
    production_seeds = [
        seed for task in parent_tasks for seed in (task.seed1, task.seed2)
    ]
    program_manifest_sha = sha256_file(program.directory / "production_program.json")
    program_checksums_sha = sha256_file(program.directory / "SHA256SUMS")
    payload: dict[str, Any] = {
        "schema_version": MANAGED_BINDING_SCHEMA_VERSION,
        "created_at_utc": created_at_utc,
        "accepted_statistical_evidence": (
            program.manifest.get("accepted_statistical_evidence") is True
            and not test_mode
        ),
        "test_mode": test_mode,
        "program": {
            "program_directory": str(program.directory),
            "program_id": program.manifest["program_id"],
            "program_hash": program.manifest["program_hash"],
            "parent_plan_hash": require_dict(
                require_dict(program.manifest, "identity"), "parent"
            )["plan_hash"],
            "authorization_graph_hash": program.manifest[
                "authorization_graph_hash"
            ],
            "production_program_json_sha256": program_manifest_sha,
            "root_checksum_manifest_sha256": program_checksums_sha,
            "formal_lock_path": str(lock_path) if lock_path is not None else None,
            "formal_lock_sha256": sha256_file(lock_path)
            if lock_path is not None
            else None,
        },
        "child": {
            "child_id": INITIAL_CHILD_ID,
            "campaign_id": descriptor["materialized_campaign_id"],
            "campaign_plan_hash": descriptor["materialized_campaign_plan_hash"],
            "child_plan_hash": child_plan["child_plan_hash"],
            "task_set_hash": descriptor["task_set_hash"],
            "parent_task_plan_hash": descriptor["parent_task_plan_hash"],
            "task_seed_mapping_hash": seed_pair_registry_hash(parent_tasks),
            "seed_set_hash": seed_set_hash(production_seeds),
            "task_count": 32,
            "event_count": 8_000,
            "tasks_tsv_sha256": None,
            "scan_args_sha256": scan_args_sha256,
        },
        "authorization": {
            "kind": "initial-program-authorization",
            "source": "authorization_graph.initial.directly_authorized_children",
            "authorization_graph_hash": program.manifest[
                "authorization_graph_hash"
            ],
            "decision_record_hash": None,
            "scheduling_record_hash": None,
        },
        "execution_source": require_dict(program.manifest, "program_source"),
        "control_plane_source": control_plane,
        "runtime": {
            "environment_identity": runtime_environment_identity,
        },
        "phase_boundary": {
            "phase": "Phase-2A",
            "submittable": False,
            "managed_submit_mode": "check-only",
            "actual_slurm_submission": "locked-until-downstream-closure",
        },
    }
    if not test_mode:
        assert lock is not None
        for key in (
            "program_id",
            "program_hash",
            "parent_plan_hash",
            "authorization_graph_hash",
            "production_program_json_sha256",
            "root_checksum_manifest_sha256",
        ):
            if payload["program"][key] != lock[key]:
                raise ValueError(f"binding program {key} does not match formal lock")
    return payload


def _write_managed_tree(
    directory: Path,
    *,
    program: ProductionProgramBundle,
    lock_path: Path | None,
    lock: dict[str, Any] | None,
    control_plane: dict[str, Any],
    created_at_utc: str,
    test_mode: bool,
    fault_after: str | None,
) -> None:
    directory.mkdir()
    child_plan, descriptor = _child_plan(program)
    parent_tasks = program.child_tasks[INITIAL_CHILD_ID]
    tasks = materialized_tasks(parent_tasks)
    _validate_bc_s1_shape(parent_tasks, tasks, descriptor)

    tasks_path = directory / "tasks.tsv"
    scan_args_path = directory / "scan_args.txt"
    readme_path = directory / "README.md"
    binding_path = directory / "program_binding.json"
    plan_path = directory / "managed_child.json"
    _write_tasks(tasks_path, tasks)
    if fault_after == "tasks":
        raise RuntimeError("injected managed-child failure after tasks")

    source_scan_args = program.directory / "children" / INITIAL_CHILD_ID / "scan_args.txt"
    lines = [
        line
        for line in source_scan_args.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(lines) != 32:
        raise ValueError("frozen BC-S1 scan-args count is not 32")
    scan_args_path.write_text(
        "\n".join(
            [
                f"# schema_version={MANAGED_PLAN_SCHEMA_VERSION}",
                f"# campaign_id={descriptor['materialized_campaign_id']}",
                "# managed_child=BC-S1",
                "# submittable=false",
                *lines,
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for task, line in zip(tasks, lines):
        argv = shlex.split(line)
        for flag, expected in (
            ("--logical-task-id", task.logical_task_id),
            ("--seed1", str(task.seed1)),
            ("--seed2", str(task.seed2)),
            ("--configuration-hash", task.configuration_hash),
        ):
            try:
                actual = argv[argv.index(flag) + 1]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"frozen BC-S1 scan args omit {flag}") from exc
            if actual != expected:
                raise ValueError(f"frozen BC-S1 scan args mismatch for {flag}")

    environment = _environment_from_program(
        program, require_identity_match=not test_mode
    )
    binding = _build_binding(
        program=program,
        lock_path=lock_path,
        lock=lock,
        control_plane=control_plane,
        scan_args_sha256=sha256_file(scan_args_path),
        runtime_environment_identity=environment["identity_hash"],
        created_at_utc=created_at_utc,
        test_mode=test_mode,
    )
    binding["child"]["tasks_tsv_sha256"] = sha256_file(tasks_path)
    binding_hash = sha256_bytes(canonical_json(binding))
    binding["binding_hash"] = binding_hash
    binding_path.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
    if fault_after == "binding":
        raise RuntimeError("injected managed-child failure after binding")

    readme_path.write_text(
        f"""# {descriptor['materialized_campaign_id']}

Managed Phase-2A materialization of the frozen `{INITIAL_CHILD_ID}` child.

- Program: `{program.manifest['program_id']}`
- Program hash: `{program.manifest['program_hash']}`
- Child plan hash: `{child_plan['child_plan_hash']}`
- Tasks: `32`
- Events: `8,000`
- Execution source: `{require_dict(program.manifest, 'program_source')['git_commit']}`
- Control-plane source: `{control_plane['git_commit']}`
- Accepted statistical evidence: `{str(binding['accepted_statistical_evidence']).lower()}`

This object is a managed plan, not a campaign, and contains no `campaign.json`.
Validate only with
`submit_steel_module_production_child.py --check-only`. Actual Slurm submission
remains locked until the downstream finalization, cumulative-analysis, static
review, and progression-record infrastructure is implemented and reviewed.
""",
        encoding="utf-8",
    )
    plan = {
        "schema_version": MANAGED_PLAN_SCHEMA_VERSION,
        "object_kind": "non-submittable-managed-child-plan",
        "campaign_id": descriptor["materialized_campaign_id"],
        "study_preset": STUDY_PRESET,
        "stage": "production",
        "description": "Managed Phase-2A BC-S1 production diagnostic",
        "created_at_utc": created_at_utc,
        "plan_hash": descriptor["materialized_campaign_plan_hash"],
        "campaign_seed": None,
        "events_per_task": 250,
        "seed_blocks_per_configuration": 16,
        "task_count": 32,
        "configuration_axes": {
            "tile_thickness_mm": [4, 24],
            "sipm_layout": ["back-center"],
            "absorber_transverse_mm": [500],
            "x_mm": [0],
            "y_mm": [0],
        },
        "accepted_domain": {
            "tile_thickness_mm": [4, 8, 12, 16, 20, 24],
            "sipm_layout": ["back-center", "edge-center", "back-four"],
            "absorber_transverse_mm": [200, 300, 500],
        },
        "source_contract": {
            "particle": "neutron",
            "authoritative_quantity": "kinetic_energy",
            "kinetic_energy_mev": 1000,
            "profile": "point",
            "direction": [0, 0, -1],
            "angular_model": "pencil",
        },
        "git": {
            "commit": require_sha1(require_dict(program.manifest, "program_source"), "git_commit"),
            "branch": require_string(require_dict(program.manifest, "program_source"), "branch"),
            "dirty": False,
            "dirty_paths": [],
        },
        "environment": environment,
        "program_management": {
            "managed": True,
            "child_id": INITIAL_CHILD_ID,
            "binding_path": "program_binding.json",
            "binding_sha256": sha256_file(binding_path),
            "binding_hash": binding_hash,
            "submittable": False,
            "phase": "Phase-2A",
        },
        "artifacts": {
            "tasks_tsv": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
            "scan_args": {
                "path": "scan_args.txt",
                "sha256": sha256_file(scan_args_path),
            },
            "readme": {"path": "README.md", "sha256": sha256_file(readme_path)},
            "program_binding": {
                "path": "program_binding.json",
                "sha256": sha256_file(binding_path),
            },
        },
    }
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    _write_checksums(directory)
    if fault_after == "checksums":
        raise RuntimeError("injected managed-child failure after checksums")


def write_managed_child_atomic(
    out_dir: Path,
    *,
    program: ProductionProgramBundle,
    repo_root: Path,
    lock_path: Path | None = None,
    lock: dict[str, Any] | None = None,
    test_mode: bool = False,
    fault_after: str | None = None,
) -> ManagedProductionChild:
    """Write one immutable child; explicit test mode is never accepted evidence."""

    if program.manifest.get("accepted_statistical_evidence") is not True and not test_mode:
        raise ValueError("formal materialization requires an accepted production program")
    if not test_mode and (lock_path is None or lock is None):
        raise ValueError("formal materialization requires the tracked program lock")
    control_plane = _control_plane_identity(repo_root, require_clean=not test_mode)
    requested_target = out_dir.expanduser()
    if requested_target.is_symlink():
        raise ValueError(f"refusing symlink managed child target: {requested_target}")
    target = requested_target.resolve()
    program_root = program.directory.resolve()
    control_root = repo_root.expanduser().resolve()
    if target == program_root or program_root in target.parents:
        raise ValueError("managed child target must not be inside the sealed program")
    if not test_mode and (target == control_root or control_root in target.parents):
        raise ValueError("formal managed child target must not be inside the repository")
    if not test_mode:
        canonical_target = program_root.parent / FORMAL_CHILD_DIRECTORY_NAME
        if target != canonical_target:
            raise ValueError(
                f"formal BC-S1 target is fixed at the canonical path: {canonical_target}"
            )
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite managed child target: {target}")
    if test_mode:
        target.parent.mkdir(parents=True, exist_ok=True)
    elif not target.parent.is_dir():
        raise ValueError(f"formal managed-child parent does not exist: {target.parent}")
    lock_dir = target.parent / f".{target.name}.materialize.lock"
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"managed child materialization is already active: {target}") from exc
    temporary: Path | None = None
    try:
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
        )
        temporary.rmdir()
        if target.exists() or target.is_symlink():
            raise ValueError(f"refusing to overwrite managed child target: {target}")
        _write_managed_tree(
            temporary,
            program=program,
            lock_path=lock_path,
            lock=lock,
            control_plane=control_plane,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            test_mode=test_mode,
            fault_after=fault_after,
        )
        load_managed_production_child(
            temporary,
            repo_root=repo_root,
            verify_runtime_artifacts=not test_mode,
            verify_control_plane=not test_mode,
            require_current_control_plane=not test_mode,
            allow_test_mode=test_mode,
            _allow_materialization_staging=not test_mode,
        )
        if target.exists() or target.is_symlink():
            raise ValueError(f"refusing to overwrite managed child target: {target}")
        os.rename(temporary, target)
        return load_managed_production_child(
            target,
            repo_root=repo_root,
            verify_runtime_artifacts=not test_mode,
            verify_control_plane=not test_mode,
            require_current_control_plane=not test_mode,
            allow_test_mode=test_mode,
        )
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass


def materialize_formal_bc_s1(*, repo_root: Path) -> ManagedProductionChild:
    program, lock_path, lock = load_formal_production_program(
        repo_root, verify_runtime_artifacts=True
    )
    return write_managed_child_atomic(
        program.directory.parent / FORMAL_CHILD_DIRECTORY_NAME,
        program=program,
        repo_root=repo_root,
        lock_path=lock_path,
        lock=lock,
        test_mode=False,
    )


def _validate_recorded_control_plane(binding: dict[str, Any], repo_root: Path) -> None:
    recorded = require_dict(binding, "control_plane_source")
    if recorded.get("dirty") is not False or recorded.get("dirty_paths") != []:
        raise ValueError("recorded control-plane source must be a clean checkout")
    root = repo_root.expanduser().resolve()
    commit = require_sha1(recorded, "git_commit")
    tree = require_sha1(recorded, "git_tree")
    if _run_git(root, "rev-parse", f"{commit}^{{tree}}") != tree:
        raise ValueError("recorded control-plane commit/tree identity is unavailable")
    artifacts = require_dict(recorded, "artifacts")
    if set(artifacts) != set(CONTROL_PLANE_ARTIFACTS):
        raise ValueError("recorded control-plane artifact set is invalid")
    for relative in CONTROL_PLANE_ARTIFACTS:
        expected = require_sha256(require_dict(artifacts, relative), "sha256")
        if _git_blob_sha256(root, commit, relative) != expected:
            raise ValueError(f"recorded control-plane artifact mismatch: {relative}")


def _validate_current_control_plane(binding: dict[str, Any], repo_root: Path) -> None:
    recorded = require_dict(binding, "control_plane_source")
    actual = _control_plane_identity(repo_root, require_clean=True)
    if actual != recorded:
        raise ValueError("current control-plane checkout does not match managed binding")


def _load_managed_plan(
    directory: Path, *, verify_runtime_artifacts: bool
) -> CampaignBundle:
    manifest = load_json(directory / "managed_child.json")
    if manifest.get("schema_version") != MANAGED_PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported managed child plan schema")
    if manifest.get("object_kind") != "non-submittable-managed-child-plan":
        raise ValueError("managed child object kind is invalid")
    if manifest.get("study_preset") != STUDY_PRESET:
        raise ValueError("managed child study preset is invalid")
    if manifest.get("stage") != "production":
        raise ValueError("managed child plan stage must be production")
    if (
        manifest.get("campaign_seed") is not None
        or manifest.get("events_per_task") != 250
        or manifest.get("seed_blocks_per_configuration") != 16
    ):
        raise ValueError("managed child fixed event/block contract is invalid")
    if manifest.get("configuration_axes") != {
        "tile_thickness_mm": [4, 24],
        "sipm_layout": ["back-center"],
        "absorber_transverse_mm": [500],
        "x_mm": [0],
        "y_mm": [0],
    }:
        raise ValueError("managed child configuration axes are invalid")
    if manifest.get("accepted_domain") != {
        "tile_thickness_mm": [4, 8, 12, 16, 20, 24],
        "sipm_layout": ["back-center", "edge-center", "back-four"],
        "absorber_transverse_mm": [200, 300, 500],
    }:
        raise ValueError("managed child accepted domain is invalid")
    if manifest.get("source_contract") != {
        "particle": "neutron",
        "authoritative_quantity": "kinetic_energy",
        "kinetic_energy_mev": 1000,
        "profile": "point",
        "direction": [0, 0, -1],
        "angular_model": "pencil",
    }:
        raise ValueError("managed child source contract is invalid")
    require_string(manifest, "campaign_id")
    require_sha256(manifest, "plan_hash")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    scan_args = read_scan_args(directory / "scan_args.txt")
    if len(tasks) != len(scan_args) or manifest.get("task_count") != len(tasks):
        raise ValueError("managed child task counts disagree")
    if ordered_task_hash(tasks) != manifest["plan_hash"]:
        raise ValueError("managed child tasks do not match frozen plan hash")
    artifacts = require_dict(manifest, "artifacts")
    for key, filename in (
        ("tasks_tsv", "tasks.tsv"),
        ("scan_args", "scan_args.txt"),
        ("readme", "README.md"),
        ("program_binding", "program_binding.json"),
    ):
        record = require_dict(artifacts, key)
        if record.get("path") != filename or require_sha256(
            record, "sha256"
        ) != sha256_file(directory / filename):
            raise ValueError(f"managed child artifact mismatch: {filename}")
    environment = require_dict(manifest, "environment")
    expected_identity = require_sha256(environment, "identity_hash")
    if environment_identity(environment) != expected_identity:
        raise ValueError("managed child environment identity mismatch")
    if verify_runtime_artifacts:
        for field, label in (
            ("image", "Apptainer image"),
            ("g4_data_manifest", "Geant4 data manifest"),
            ("build_artifact", "prebuilt executable"),
        ):
            resolve_recorded_artifact(require_dict(environment, field), label)
    return CampaignBundle(directory, manifest, tasks, scan_args)


def load_managed_production_child(
    campaign_dir: Path,
    *,
    repo_root: Path | None = None,
    verify_runtime_artifacts: bool = True,
    verify_control_plane: bool = True,
    require_current_control_plane: bool = False,
    allow_test_mode: bool = False,
    _allow_materialization_staging: bool = False,
    _allow_formal_lock_relocation: bool = False,
) -> ManagedProductionChild:
    requested_directory = campaign_dir.expanduser()
    if requested_directory.is_symlink():
        raise ValueError(f"managed child directory must not be a symlink: {requested_directory}")
    directory = requested_directory.resolve()
    _verify_exact_checksums(directory)
    plan = _load_managed_plan(
        directory, verify_runtime_artifacts=verify_runtime_artifacts
    )
    if plan.manifest.get("stage") != "production":
        raise ValueError("managed child plan stage must be production")
    management = require_dict(plan.manifest, "program_management")
    if management.get("managed") is not True or management.get("child_id") != INITIAL_CHILD_ID:
        raise ValueError("plan is not the managed BC-S1 child")
    if management.get("submittable") is not False or management.get("phase") != "Phase-2A":
        raise ValueError("managed child Phase-2A boundary is invalid")
    if management.get("binding_path") != "program_binding.json":
        raise ValueError("managed child binding path is invalid")
    binding_path = directory / "program_binding.json"
    if sha256_file(binding_path) != require_sha256(management, "binding_sha256"):
        raise ValueError("managed child binding file checksum mismatch")
    binding = load_json(binding_path)
    if binding.get("schema_version") != MANAGED_BINDING_SCHEMA_VERSION:
        raise ValueError("unsupported managed child binding schema")
    binding_hash = require_sha256(binding, "binding_hash")
    unhashed = dict(binding)
    unhashed.pop("binding_hash")
    if sha256_bytes(canonical_json(unhashed)) != binding_hash:
        raise ValueError("managed child binding semantic hash mismatch")
    if management.get("binding_hash") != binding_hash:
        raise ValueError("plan and binding semantic hashes disagree")
    if not isinstance(binding.get("test_mode"), bool) or not isinstance(
        binding.get("accepted_statistical_evidence"), bool
    ):
        raise ValueError("managed child acceptance/test-mode flags must be boolean")
    test_mode = binding["test_mode"]
    if test_mode and not allow_test_mode:
        raise ValueError("formal managed-child loader refuses test-mode evidence")
    accepted = binding["accepted_statistical_evidence"]
    if accepted == test_mode:
        raise ValueError("managed child acceptance/test-mode flags are inconsistent")
    phase = require_dict(binding, "phase_boundary")
    if (
        phase.get("phase") != "Phase-2A"
        or phase.get("submittable") is not False
        or phase.get("managed_submit_mode") != "check-only"
        or phase.get("actual_slurm_submission")
        != "locked-until-downstream-closure"
    ):
        raise ValueError("managed child phase boundary is invalid")

    child = require_dict(binding, "child")
    if (
        child.get("child_id") != INITIAL_CHILD_ID
        or child.get("campaign_id") != plan.campaign_id
        or child.get("campaign_plan_hash") != plan.plan_hash
        or child.get("task_count") != 32
        or child.get("event_count") != 8_000
    ):
        raise ValueError("managed child plan binding is invalid")
    if child.get("tasks_tsv_sha256") != sha256_file(directory / "tasks.tsv"):
        raise ValueError("managed child tasks digest mismatch")
    if child.get("scan_args_sha256") != sha256_file(directory / "scan_args.txt"):
        raise ValueError("managed child scan-args digest mismatch")
    program_record = require_dict(binding, "program")
    program = load_production_program(
        Path(require_string(program_record, "program_directory")),
        verify_runtime_artifacts=verify_runtime_artifacts,
    )
    expected_environment = _environment_from_program(
        program, require_identity_match=not test_mode
    )
    if plan.environment != expected_environment:
        raise ValueError("managed child runtime does not match frozen program runtime")
    if program.manifest.get("program_id") != program_record.get("program_id"):
        raise ValueError("managed child program ID mismatch")
    if program.manifest.get("program_hash") != program_record.get("program_hash"):
        raise ValueError("managed child program hash mismatch")
    if program_record.get("parent_plan_hash") != require_dict(
        require_dict(program.manifest, "identity"), "parent"
    ).get("plan_hash"):
        raise ValueError("managed child parent plan hash mismatch")
    if program_record.get("authorization_graph_hash") != program.manifest.get(
        "authorization_graph_hash"
    ):
        raise ValueError("managed child authorization-graph hash mismatch")
    if sha256_file(program.directory / "production_program.json") != program_record.get(
        "production_program_json_sha256"
    ):
        raise ValueError("managed child program manifest digest mismatch")
    if sha256_file(program.directory / "SHA256SUMS") != program_record.get(
        "root_checksum_manifest_sha256"
    ):
        raise ValueError("managed child program checksum-manifest digest mismatch")
    if not test_mode:
        if repo_root is None:
            raise ValueError("formal managed child validation requires repo_root")
        if _allow_formal_lock_relocation and (
            verify_control_plane or require_current_control_plane
        ):
            raise ValueError(
                "formal-lock relocation is only valid for a frozen control source"
            )
        _validate_formal_child_location(
            directory,
            program,
            allow_materialization_staging=_allow_materialization_staging,
        )
        lock_path, lock = _formal_lock(repo_root)
        _validate_program_against_lock(program, lock_path, lock)
        if (
            not _allow_formal_lock_relocation
            and program_record.get("formal_lock_path") != str(lock_path)
        ):
            raise ValueError("managed child formal lock path mismatch")
        if program_record.get("formal_lock_sha256") != sha256_file(lock_path):
            raise ValueError("managed child formal lock digest mismatch")

    child_plan, descriptor = _child_plan(program)
    if plan.campaign_id != descriptor.get("materialized_campaign_id"):
        raise ValueError("managed child campaign ID does not match frozen child plan")
    parent_tasks = program.child_tasks[INITIAL_CHILD_ID]
    _validate_bc_s1_shape(parent_tasks, plan.tasks, descriptor)
    expected_scan_args = read_scan_args(
        program.directory / "children" / INITIAL_CHILD_ID / "scan_args.txt"
    )
    if plan.scan_args != expected_scan_args:
        raise ValueError("managed child scan args do not match frozen BC-S1 plan")
    if child.get("child_plan_hash") != child_plan.get("child_plan_hash"):
        raise ValueError("managed child plan hash mismatch")
    if child.get("parent_task_plan_hash") != descriptor.get("parent_task_plan_hash"):
        raise ValueError("managed child parent-task plan hash mismatch")
    if child.get("task_set_hash") != descriptor.get("task_set_hash"):
        raise ValueError("managed child task-set hash mismatch")
    if child.get("task_seed_mapping_hash") != seed_pair_registry_hash(parent_tasks):
        raise ValueError("managed child seed-pair mapping hash mismatch")
    seeds = [seed for task in parent_tasks for seed in (task.seed1, task.seed2)]
    if child.get("seed_set_hash") != seed_set_hash(seeds):
        raise ValueError("managed child seed-set hash mismatch")
    authorization = require_dict(binding, "authorization")
    if (
        authorization.get("kind") != "initial-program-authorization"
        or authorization.get("source")
        != "authorization_graph.initial.directly_authorized_children"
        or authorization.get("decision_record_hash") is not None
        or authorization.get("scheduling_record_hash") is not None
        or authorization.get("authorization_graph_hash")
        != program.manifest.get("authorization_graph_hash")
    ):
        raise ValueError("managed BC-S1 initial authorization is invalid")
    execution = require_dict(binding, "execution_source")
    if execution != require_dict(program.manifest, "program_source"):
        raise ValueError("managed execution source does not match frozen program source")
    if plan.git_commit != require_sha1(execution, "git_commit"):
        raise ValueError("plan execution commit does not match program source")
    plan_git = require_dict(plan.manifest, "git")
    if (
        plan_git.get("branch") != execution.get("branch")
        or plan_git.get("dirty") is not False
        or plan_git.get("dirty_paths") != []
    ):
        raise ValueError("plan execution-source metadata is invalid")
    if plan.environment.get("accepted_statistical_evidence") is not accepted:
        raise ValueError("plan and binding evidence acceptance disagree")
    if plan.environment.get("identity_hash") != require_dict(
        binding, "runtime"
    ).get("environment_identity"):
        raise ValueError("managed child runtime identity mismatch")
    if verify_control_plane:
        if repo_root is None:
            raise ValueError("control-plane verification requires repo_root")
        _validate_recorded_control_plane(binding, repo_root)
        if require_current_control_plane:
            _validate_current_control_plane(binding, repo_root)
    return ManagedProductionChild(directory, plan, binding)


__all__ = [
    "FORMAL_LOCK_RELATIVE",
    "INITIAL_CHILD_ID",
    "MANAGED_BINDING_SCHEMA_VERSION",
    "ManagedProductionChild",
    "load_formal_production_program",
    "load_managed_production_child",
    "materialize_formal_bc_s1",
    "write_managed_child_atomic",
]
