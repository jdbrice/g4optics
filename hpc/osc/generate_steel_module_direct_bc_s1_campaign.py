#!/usr/bin/env python3
"""Convert the frozen BC-S1 child into one ordinary Slurm-array campaign."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from generate_steel_module_campaign import scan_args, write_tasks
from steel_module_campaign_lib import (
    CAMPAIGN_SCHEMA_VERSION,
    STUDY_PRESET,
    CampaignBundle,
    CampaignTask,
    canonical_json,
    environment_identity,
    load_campaign,
    sha256_bytes,
    sha256_file,
    task_configuration_hash,
)
from steel_module_managed_production_lib import (
    INITIAL_CHILD_ID,
    ManagedProductionChild,
    load_managed_production_child,
)
from steel_module_production_program_lib import (
    ACCEPTED_PILOT_EXCLUSION_HASH,
    seed_pair_registry_hash,
    seed_set_hash,
)


DIRECT_SCHEMA_VERSION = "steel-module-direct-bc-s1-v1"
DIRECT_ROUTE = "ordinary-slurm-array-no-preflight"
DIRECT_CAMPAIGN_PREFIX = "sm-v1-production-bc-s1-direct-"
EXPECTED_TASK_COUNT = 32
EXPECTED_EVENT_COUNT = 8_000
EXPECTED_EVENTS_PER_TASK = 250
EXPECTED_BLOCKS = tuple(range(16))
EXPECTED_THICKNESSES_MM = (4, 24)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--managed-child-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Clean checkout whose commit will run the ordinary campaign.",
    )
    return parser.parse_args()


def run_git(repo_root: Path, *args: str) -> str:
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


def clean_git_identity(repo_root: Path) -> dict[str, object]:
    root = repo_root.expanduser().resolve()
    if not (root / ".git").exists():
        raise ValueError(f"project root is not a Git checkout: {root}")
    dirty = run_git(root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise ValueError(
            "direct BC-S1 campaign generation requires a clean checkout; "
            "commit the intended campaign tooling first"
        )
    commit = run_git(root, "rev-parse", "HEAD")
    branch = run_git(root, "branch", "--show-current")
    if len(commit) != 40:
        raise ValueError("current Git commit is not a full SHA-1 identity")
    return {
        "commit": commit,
        "branch": branch or "detached",
        "dirty": False,
        "dirty_paths": [],
    }


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_sha256(value: object, label: str) -> str:
    text = _require_string(value, label)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _task_seeds(tasks: Iterable[CampaignTask]) -> list[int]:
    return [seed for task in tasks for seed in (task.seed1, task.seed2)]


def validate_direct_bc_s1_tasks(
    tasks: Iterable[CampaignTask], *, geant4_version: str
) -> tuple[CampaignTask, ...]:
    resolved = tuple(tasks)
    if len(resolved) != EXPECTED_TASK_COUNT:
        raise ValueError(
            f"direct BC-S1 requires exactly {EXPECTED_TASK_COUNT} tasks"
        )
    if [task.task_index for task in resolved] != list(
        range(1, EXPECTED_TASK_COUNT + 1)
    ):
        raise ValueError("direct BC-S1 task indices must be contiguous and one-based")
    if any(
        task.stage != "production"
        or task.sipm_layout != "back-center"
        or task.absorber_transverse_mm != 500
        or task.x_mm != 0
        or task.y_mm != 0
        or task.events != EXPECTED_EVENTS_PER_TASK
        for task in resolved
    ):
        raise ValueError(
            "direct BC-S1 tasks must be centered 500 mm back-center production "
            "blocks with 250 events"
        )
    if sum(task.events for task in resolved) != EXPECTED_EVENT_COUNT:
        raise ValueError("direct BC-S1 event total must be exactly 8,000")
    for thickness in EXPECTED_THICKNESSES_MM:
        selected = [task for task in resolved if task.tile_thickness_mm == thickness]
        if len(selected) != 16 or tuple(sorted(task.seed_block for task in selected)) != (
            EXPECTED_BLOCKS
        ):
            raise ValueError(
                f"direct BC-S1 thickness {thickness} mm must contain blocks 0-15"
            )
    if {task.tile_thickness_mm for task in resolved} != set(
        EXPECTED_THICKNESSES_MM
    ):
        raise ValueError("direct BC-S1 permits only the 4 mm and 24 mm endpoints")
    seeds = _task_seeds(resolved)
    if len(seeds) != 64 or len(set(seeds)) != 64:
        raise ValueError("direct BC-S1 requires exactly 64 unique production seeds")
    for task in resolved:
        expected = task_configuration_hash(
            stage=task.stage,
            tile_thickness_mm=task.tile_thickness_mm,
            sipm_layout=task.sipm_layout,
            absorber_transverse_mm=task.absorber_transverse_mm,
            x_mm=task.x_mm,
            y_mm=task.y_mm,
            events=task.events,
            seed_block=task.seed_block,
            seed1=task.seed1,
            seed2=task.seed2,
            geant4_version=geant4_version,
        )
        if task.configuration_hash != expected:
            raise ValueError(
                f"direct BC-S1 configuration hash mismatch: {task.logical_task_id}"
            )
    return resolved


def source_record(managed: ManagedProductionChild) -> dict[str, object]:
    binding = managed.binding
    child = _require_mapping(binding.get("child"), "managed child binding.child")
    program = _require_mapping(
        binding.get("program"), "managed child binding.program"
    )
    return {
        "managed_child_directory": str(managed.directory),
        "managed_child_campaign_id": managed.plan.campaign_id,
        "managed_child_plan_hash": managed.plan.plan_hash,
        "managed_child_binding_sha256": sha256_file(
            managed.directory / "program_binding.json"
        ),
        "managed_child_binding_hash": _require_sha256(
            binding.get("binding_hash"), "managed child binding hash"
        ),
        "child_id": _require_string(child.get("child_id"), "managed child ID"),
        "child_plan_hash": _require_sha256(
            child.get("child_plan_hash"), "managed child plan hash"
        ),
        "source_task_seed_mapping_hash": _require_sha256(
            child.get("task_seed_mapping_hash"), "managed child seed mapping hash"
        ),
        "source_seed_set_hash": _require_sha256(
            child.get("seed_set_hash"), "managed child seed-set hash"
        ),
        "program_id": _require_string(program.get("program_id"), "program ID"),
        "program_hash": _require_sha256(program.get("program_hash"), "program hash"),
        "authorization_graph_hash": _require_sha256(
            program.get("authorization_graph_hash"), "authorization graph hash"
        ),
        "source_tasks_tsv_sha256": sha256_file(managed.directory / "tasks.tsv"),
        "source_scan_args_sha256": sha256_file(
            managed.directory / "scan_args.txt"
        ),
    }


def _direct_marker(
    *,
    tasks: tuple[CampaignTask, ...],
    plan_hash: str,
    environment_identity_hash: str,
    git_commit: str,
    source: dict[str, object],
) -> dict[str, object]:
    marker: dict[str, object] = {
        "schema_version": DIRECT_SCHEMA_VERSION,
        "route": DIRECT_ROUTE,
        "preflight_required": False,
        "managed_execution_required": False,
        "child_id": INITIAL_CHILD_ID,
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "seed_blocks_per_configuration": 16,
        "task_plan_hash": plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
        "environment_identity": environment_identity_hash,
        "submission_git_commit": git_commit,
        "source": source,
    }
    marker["identity_hash"] = sha256_bytes(canonical_json(marker))
    return marker


def _write_direct_tree(
    directory: Path,
    *,
    public_directory: Path,
    tasks: tuple[CampaignTask, ...],
    environment: dict[str, Any],
    source: dict[str, object],
    git: dict[str, object],
    created_at_utc: str,
) -> None:
    directory.mkdir()
    geant4_version = _require_string(
        environment.get("geant4_version"), "environment Geant4 version"
    )
    validate_direct_bc_s1_tasks(tasks, geant4_version=geant4_version)
    expected_environment_identity = _require_sha256(
        environment.get("identity_hash"), "environment identity"
    )
    if environment_identity(environment) != expected_environment_identity:
        raise ValueError("source environment identity is invalid")
    if (
        environment.get("mode") != "osc-production"
        or environment.get("accepted_statistical_evidence") is not True
        or geant4_version != "11.4.2"
    ):
        raise ValueError(
            "direct BC-S1 requires the accepted OSC Geant4 11.4.2 environment"
        )
    git_commit = _require_string(git.get("commit"), "submission Git commit")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct BC-S1 must be generated from a clean checkout")

    plan_hash = sha256_bytes(canonical_json([asdict(task) for task in tasks]))
    marker = _direct_marker(
        tasks=tasks,
        plan_hash=plan_hash,
        environment_identity_hash=expected_environment_identity,
        git_commit=git_commit,
        source=source,
    )
    campaign_id = DIRECT_CAMPAIGN_PREFIX + str(marker["identity_hash"])[:12]

    tasks_path = directory / "tasks.tsv"
    scan_args_path = directory / "scan_args.txt"
    readme_path = directory / "README.md"
    campaign_path = directory / "campaign.json"
    write_tasks(tasks_path, list(tasks))
    scan_args_path.write_text(
        "\n".join(
            [
                f"# schema_version={CAMPAIGN_SCHEMA_VERSION}",
                f"# campaign_id={campaign_id}",
                "# direct_production_child=BC-S1",
                "# submission_route=ordinary-slurm-array-no-preflight",
                f"# task_count={len(tasks)}",
                *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    readme_path.write_text(
        f"""# {campaign_id}

Direct BC-S1 steel-module production campaign.

- Shape: `4/24 mm back-center`, 16 blocks per endpoint
- Tasks: `32`
- Events: `8,000` (`250` per task)
- Seeds: the `64` production seeds frozen in the accepted BC-S1 child
- Submission: ordinary steel-module Slurm array; no held preflight
- Plan hash: `{plan_hash}`

Validate before submission:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \\
  --campaign-dir {public_directory} --check-only
```

Submit only after the check-only output reports exactly 32 tasks.
""",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "study_preset": STUDY_PRESET,
        "stage": "production",
        "description": "Direct BC-S1 production: 4/24 mm back-center endpoints",
        "created_at_utc": created_at_utc,
        "plan_hash": plan_hash,
        "campaign_seed": None,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "seed_blocks_per_configuration": 16,
        "task_count": EXPECTED_TASK_COUNT,
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
        "git": git,
        "environment": environment,
        "direct_production": marker,
        "artifacts": {
            "tasks_tsv": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
            "scan_args": {
                "path": "scan_args.txt",
                "sha256": sha256_file(scan_args_path),
            },
            "readme": {"path": "README.md", "sha256": sha256_file(readme_path)},
        },
    }
    campaign_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_direct_campaign_atomic(
    out_dir: Path,
    *,
    tasks: Iterable[CampaignTask],
    environment: dict[str, Any],
    source: dict[str, object],
    git: dict[str, object],
    created_at_utc: str | None = None,
) -> CampaignBundle:
    """Publish a self-contained standard campaign without overwriting a target."""

    requested = out_dir.expanduser()
    if requested.is_symlink():
        raise ValueError(f"refusing symlink campaign target: {requested}")
    target = requested.resolve()
    if target.exists():
        raise ValueError(f"refusing to overwrite campaign target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    try:
        temporary.rmdir()
        _write_direct_tree(
            temporary,
            public_directory=target,
            tasks=tuple(tasks),
            environment=copy.deepcopy(environment),
            source=copy.deepcopy(source),
            git=copy.deepcopy(git),
            created_at_utc=created_at_utc
            or datetime.now(timezone.utc).isoformat(),
        )
        staged = load_campaign(temporary, verify_external_artifacts=True)
        validate_direct_bc_s1_bundle(staged)
        if target.exists():
            raise ValueError(f"campaign target appeared during publish: {target}")
        os.rename(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    bundle = load_campaign(target, verify_external_artifacts=True)
    validate_direct_bc_s1_bundle(bundle)
    return bundle


def validate_direct_bc_s1_bundle(bundle: CampaignBundle) -> None:
    """Admit only the one frozen, direct BC-S1 production campaign shape."""

    manifest = bundle.manifest
    if manifest.get("stage") != "production":
        raise ValueError("direct BC-S1 campaign stage must be production")
    if any(key in manifest for key in ("program_management", "production_management")):
        raise ValueError("direct BC-S1 campaign must not contain managed-route markers")
    environment = _require_mapping(manifest.get("environment"), "environment")
    geant4_version = _require_string(
        environment.get("geant4_version"), "environment Geant4 version"
    )
    if (
        environment.get("mode") != "osc-production"
        or environment.get("accepted_statistical_evidence") is not True
        or geant4_version != "11.4.2"
    ):
        raise ValueError("direct BC-S1 requires accepted OSC Geant4 11.4.2 evidence")
    tasks = validate_direct_bc_s1_tasks(
        bundle.tasks, geant4_version=geant4_version
    )
    if manifest.get("events_per_task") != 250:
        raise ValueError("direct BC-S1 events_per_task must be 250")
    if manifest.get("seed_blocks_per_configuration") != 16:
        raise ValueError("direct BC-S1 seed-block count must be 16")
    if manifest.get("campaign_seed") is not None:
        raise ValueError("direct BC-S1 must reuse frozen seeds, not derive a campaign seed")
    if manifest.get("configuration_axes") != {
        "tile_thickness_mm": [4, 24],
        "sipm_layout": ["back-center"],
        "absorber_transverse_mm": [500],
        "x_mm": [0],
        "y_mm": [0],
    }:
        raise ValueError("direct BC-S1 configuration axes are not exact")
    source_contract = _require_mapping(
        manifest.get("source_contract"), "source contract"
    )
    if (
        source_contract.get("particle") != "neutron"
        or source_contract.get("authoritative_quantity") != "kinetic_energy"
        or source_contract.get("kinetic_energy_mev") != 1000
        or source_contract.get("profile") != "point"
        or source_contract.get("direction") != [0, 0, -1]
        or source_contract.get("angular_model") != "pencil"
    ):
        raise ValueError("direct BC-S1 neutron source contract is not exact")
    git = _require_mapping(manifest.get("git"), "Git identity")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct BC-S1 campaign Git identity must be clean")

    marker = _require_mapping(
        manifest.get("direct_production"), "direct production marker"
    )
    if marker.get("schema_version") != DIRECT_SCHEMA_VERSION:
        raise ValueError("unsupported direct BC-S1 marker schema")
    if marker.get("route") != DIRECT_ROUTE:
        raise ValueError("direct BC-S1 must use the ordinary no-preflight route")
    if marker.get("preflight_required") is not False:
        raise ValueError("direct BC-S1 preflight flag must be false")
    if marker.get("managed_execution_required") is not False:
        raise ValueError("direct BC-S1 managed-execution flag must be false")
    if marker.get("child_id") != INITIAL_CHILD_ID:
        raise ValueError("direct campaign is not BC-S1")
    expected_scalars = {
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "seed_blocks_per_configuration": 16,
        "task_plan_hash": bundle.plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
        "environment_identity": environment.get("identity_hash"),
        "submission_git_commit": git.get("commit"),
    }
    for key, expected in expected_scalars.items():
        if marker.get(key) != expected:
            raise ValueError(f"direct BC-S1 marker mismatch: {key}")
    source = _require_mapping(marker.get("source"), "direct production source")
    if source.get("child_id") != INITIAL_CHILD_ID:
        raise ValueError("direct BC-S1 source child identity mismatch")
    for key in (
        "managed_child_plan_hash",
        "managed_child_binding_sha256",
        "managed_child_binding_hash",
        "child_plan_hash",
        "source_task_seed_mapping_hash",
        "source_seed_set_hash",
        "program_hash",
        "authorization_graph_hash",
        "source_tasks_tsv_sha256",
        "source_scan_args_sha256",
    ):
        _require_sha256(source.get(key), f"direct production source {key}")
    for key in ("managed_child_campaign_id", "program_id"):
        _require_string(source.get(key), f"direct production source {key}")
    if source.get("source_task_seed_mapping_hash") != seed_pair_registry_hash(tasks):
        raise ValueError("direct BC-S1 tasks do not match the frozen seed mapping")
    if source.get("source_seed_set_hash") != seed_set_hash(_task_seeds(tasks)):
        raise ValueError("direct BC-S1 tasks do not match the frozen seed set")
    identity = _require_sha256(marker.get("identity_hash"), "direct identity hash")
    unhashed = dict(marker)
    unhashed.pop("identity_hash")
    if sha256_bytes(canonical_json(unhashed)) != identity:
        raise ValueError("direct BC-S1 marker identity hash mismatch")
    if bundle.campaign_id != DIRECT_CAMPAIGN_PREFIX + identity[:12]:
        raise ValueError("direct BC-S1 campaign ID does not match its identity")
    expected_scan_args = tuple(
        shlex.join(scan_args(task, bundle.campaign_id)) for task in tasks
    )
    if bundle.scan_args != expected_scan_args:
        raise ValueError("direct BC-S1 scan arguments do not match the frozen tasks")


def main() -> int:
    args = parse_args()
    repo_root = args.project_root.expanduser().resolve()
    try:
        git = clean_git_identity(repo_root)
        managed = load_managed_production_child(
            args.managed_child_dir,
            repo_root=repo_root,
            verify_runtime_artifacts=True,
            verify_control_plane=True,
            require_current_control_plane=False,
        )
        bundle = write_direct_campaign_atomic(
            args.out_dir,
            tasks=managed.plan.tasks,
            environment=managed.plan.environment,
            source=source_record(managed),
            git=git,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot generate direct BC-S1 campaign: {exc}", file=sys.stderr)
        return 1
    print(f"Campaign: {bundle.campaign_id}")
    print("Stage: production (direct BC-S1)")
    print("Tasks: 32")
    print("Events: 8000")
    print("Preflight: none")
    print(f"Output: {bundle.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
