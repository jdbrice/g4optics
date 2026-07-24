#!/usr/bin/env python3
"""Generate the accepted one-array direct edge-two follow-up campaign."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from generate_steel_module_campaign import scan_args, write_tasks
from generate_steel_module_direct_bc_s1_campaign import (
    DIRECT_ROUTE,
    _require_mapping,
    _require_sha256,
    _require_string,
    _task_seeds,
    clean_git_identity,
)
from generate_steel_module_direct_bc_s2_campaign import validate_program_identity
from steel_module_campaign_lib import (
    CAMPAIGN_SCHEMA_VERSION,
    STUDY_PRESET,
    TILE_THICKNESSES_MM,
    CampaignBundle,
    CampaignTask,
    canonical_json,
    environment_identity,
    load_campaign,
    sha256_bytes,
    sha256_file,
    task_configuration_hash,
)
from steel_module_production_program_lib import (
    MAX_GEANT4_SEED,
    ProductionProgramBundle,
    load_production_program,
    seed_pair_registry_hash,
    seed_set_hash,
)


DIRECT_SCHEMA_VERSION = "steel-module-direct-edge-two-v1"
DIRECT_CAMPAIGN_PREFIX = "sm-v1-production-edge-two-direct-"
DIRECT_CHILD_ID = "EDGE-TWO"
SIPM_LAYOUT = "edge-two"
EXPECTED_EVENTS_PER_TASK = 250
EXPECTED_BLOCKS_PER_CONFIGURATION = 40
EXPECTED_CONFIGURATION_COUNT = 6
EXPECTED_TASK_COUNT = 240
EXPECTED_EVENT_COUNT = 60_000
EXPECTED_ARRAY_SPEC = "1-240"
CAMPAIGN_SEED = 20260721
SEED_DERIVATION_DOMAIN = "steel-module-edge-two-production-seed-v1"
EXPECTED_PRIOR_PRODUCTION_TASKS = 914
EXPECTED_PRIOR_PRODUCTION_SEEDS = 1_828
EXPECTED_PILOT_EXCLUDED_SEEDS = 240
EXPECTED_EXCLUDED_SEEDS = (
    EXPECTED_PRIOR_PRODUCTION_SEEDS + EXPECTED_PILOT_EXCLUDED_SEEDS
)
DECISION_DOCUMENT = Path("docs/decisions/steel-module-scan-v1.md")
DECISION_MARKERS = (
    "`40` independent `250`-event blocks for each of the six thicknesses",
    "six configurations, `240` tasks, and `60,000` new events",
    "ordinary `1-240` Slurm array with no preflight",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--program-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--g4-data-manifest", required=True, type=Path)
    parser.add_argument("--build-artifact", required=True, type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Clean checkout whose commit will run the ordinary campaign.",
    )
    return parser.parse_args()


def artifact_record(
    path: Path, label: str, *, executable: bool = False
) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"missing {label}: {resolved}")
    if executable and not (resolved.stat().st_mode & stat.S_IXUSR):
        raise ValueError(f"{label} is not executable: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def accepted_environment(
    *, image: Path, g4_data_manifest: Path, build_artifact: Path
) -> dict[str, Any]:
    environment: dict[str, Any] = {
        "mode": "osc-production",
        "geant4_version": "11.4.2",
        "accepted_statistical_evidence": True,
        "image": artifact_record(image, "Apptainer image"),
        "g4_data_manifest": artifact_record(
            g4_data_manifest, "Geant4 data manifest"
        ),
        "build_artifact": artifact_record(
            build_artifact, "frozen OpNovice2 executable", executable=True
        ),
    }
    environment["identity_hash"] = environment_identity(environment)
    return environment


def prior_seed_set(program: ProductionProgramBundle) -> set[int]:
    if len(program.tasks) != EXPECTED_PRIOR_PRODUCTION_TASKS:
        raise ValueError("accepted production program must contain 914 tasks")
    production = _task_seeds(program.tasks)
    pilot = [record.seed for record in program.excluded_seeds]
    if len(production) != EXPECTED_PRIOR_PRODUCTION_SEEDS:
        raise ValueError("accepted production program must contain 1,828 task seeds")
    if len(pilot) != EXPECTED_PILOT_EXCLUDED_SEEDS:
        raise ValueError("accepted production program must exclude 240 pilot seeds")
    combined = production + pilot
    if len(set(combined)) != EXPECTED_EXCLUDED_SEEDS:
        raise ValueError("accepted pilot and production seed registries overlap")
    return set(combined)


def derive_unique_seed(
    *, logical_task_id: str, slot: int, used: set[int]
) -> int:
    nonce = 0
    while True:
        material = (
            f"{SEED_DERIVATION_DOMAIN}|{CAMPAIGN_SEED}|{logical_task_id}|"
            f"{slot}|{nonce}"
        ).encode()
        candidate = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        candidate = candidate % MAX_GEANT4_SEED + 1
        if candidate not in used:
            used.add(candidate)
            return candidate
        nonce += 1


def build_edge_two_tasks(
    excluded_seeds: Iterable[int], *, geant4_version: str = "11.4.2"
) -> tuple[CampaignTask, ...]:
    used = set(excluded_seeds)
    if len(used) != EXPECTED_EXCLUDED_SEEDS:
        raise ValueError(
            f"edge-two seed allocation requires exactly {EXPECTED_EXCLUDED_SEEDS} "
            "excluded pilot+production seeds"
        )
    tasks: list[CampaignTask] = []
    for thickness in TILE_THICKNESSES_MM:
        for block in range(EXPECTED_BLOCKS_PER_CONFIGURATION):
            logical_task_id = (
                f"production-l{SIPM_LAYOUT}-t{thickness:02d}-a500-b{block:03d}"
            )
            seed1 = derive_unique_seed(
                logical_task_id=logical_task_id, slot=1, used=used
            )
            seed2 = derive_unique_seed(
                logical_task_id=logical_task_id, slot=2, used=used
            )
            configuration_hash = task_configuration_hash(
                stage="production",
                tile_thickness_mm=thickness,
                sipm_layout=SIPM_LAYOUT,
                absorber_transverse_mm=500,
                x_mm=0,
                y_mm=0,
                events=EXPECTED_EVENTS_PER_TASK,
                seed_block=block,
                seed1=seed1,
                seed2=seed2,
                geant4_version=geant4_version,
            )
            tasks.append(
                CampaignTask(
                    task_index=len(tasks) + 1,
                    logical_task_id=logical_task_id,
                    stage="production",
                    tile_thickness_mm=thickness,
                    sipm_layout=SIPM_LAYOUT,
                    absorber_transverse_mm=500,
                    x_mm=0,
                    y_mm=0,
                    seed_block=block,
                    events=EXPECTED_EVENTS_PER_TASK,
                    seed1=seed1,
                    seed2=seed2,
                    configuration_hash=configuration_hash,
                )
            )
    return validate_direct_edge_two_tasks(tasks, geant4_version=geant4_version)


def validate_direct_edge_two_tasks(
    tasks: Iterable[CampaignTask], *, geant4_version: str
) -> tuple[CampaignTask, ...]:
    resolved = tuple(tasks)
    if len(resolved) != EXPECTED_TASK_COUNT:
        raise ValueError("direct edge-two requires exactly 240 tasks")
    if [task.task_index for task in resolved] != list(range(1, 241)):
        raise ValueError(
            "direct edge-two task indices must be contiguous and one-based"
        )
    if any(
        task.stage != "production"
        or task.sipm_layout != SIPM_LAYOUT
        or task.absorber_transverse_mm != 500
        or task.x_mm != 0
        or task.y_mm != 0
        or task.events != EXPECTED_EVENTS_PER_TASK
        for task in resolved
    ):
        raise ValueError(
            "direct edge-two tasks must be centered 500 mm production blocks "
            "with 250 events"
        )
    if sum(task.events for task in resolved) != EXPECTED_EVENT_COUNT:
        raise ValueError("direct edge-two event total must be exactly 60,000")
    if {task.tile_thickness_mm for task in resolved} != set(TILE_THICKNESSES_MM):
        raise ValueError("direct edge-two must contain all six accepted thicknesses")
    for thickness in TILE_THICKNESSES_MM:
        selected = [task for task in resolved if task.tile_thickness_mm == thickness]
        if len(selected) != EXPECTED_BLOCKS_PER_CONFIGURATION or tuple(
            sorted(task.seed_block for task in selected)
        ) != tuple(range(EXPECTED_BLOCKS_PER_CONFIGURATION)):
            raise ValueError(
                f"direct edge-two thickness {thickness} mm must contain blocks 0-39"
            )
    seeds = _task_seeds(resolved)
    if len(seeds) != 480 or len(set(seeds)) != 480:
        raise ValueError("direct edge-two requires exactly 480 unique new seeds")
    if len({task.logical_task_id for task in resolved}) != EXPECTED_TASK_COUNT:
        raise ValueError("direct edge-two logical task IDs are not unique")
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
                f"direct edge-two configuration hash mismatch: {task.logical_task_id}"
            )
    return resolved


def validate_decision_document(repo_root: Path) -> dict[str, str]:
    path = repo_root / DECISION_DOCUMENT
    text = path.read_text(encoding="utf-8")
    normalized = " ".join(text.split())
    missing = [marker for marker in DECISION_MARKERS if marker not in normalized]
    if missing:
        raise ValueError(
            "edge-two decision document lacks the accepted direct policy: "
            + ", ".join(repr(marker) for marker in missing)
        )
    return {"path": DECISION_DOCUMENT.as_posix(), "sha256": sha256_file(path)}


def source_record(
    program: ProductionProgramBundle,
    *,
    excluded_seeds: set[int],
    decision_document: dict[str, str],
) -> dict[str, object]:
    return {
        "production_program_directory": str(program.directory),
        "program_id": _require_string(program.manifest.get("program_id"), "program ID"),
        "program_hash": _require_sha256(
            program.manifest.get("program_hash"), "program hash"
        ),
        "authorization_graph_hash": _require_sha256(
            program.manifest.get("authorization_graph_hash"),
            "authorization graph hash",
        ),
        "production_program_json_sha256": sha256_file(
            program.directory / "production_program.json"
        ),
        "production_program_sha256s_sha256": sha256_file(
            program.directory / "SHA256SUMS"
        ),
        "prior_production_task_count": len(program.tasks),
        "prior_production_seed_count": len(_task_seeds(program.tasks)),
        "pilot_excluded_seed_count": len(program.excluded_seeds),
        "excluded_seed_count": len(excluded_seeds),
        "excluded_seed_set_hash": seed_set_hash(excluded_seeds),
        "decision_document": decision_document,
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
        "child_id": DIRECT_CHILD_ID,
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "configuration_count": EXPECTED_CONFIGURATION_COUNT,
        "blocks_per_configuration": EXPECTED_BLOCKS_PER_CONFIGURATION,
        "array_spec": EXPECTED_ARRAY_SPEC,
        "campaign_seed": CAMPAIGN_SEED,
        "seed_derivation_domain": SEED_DERIVATION_DOMAIN,
        "task_plan_hash": plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "excluded_seed_count": EXPECTED_EXCLUDED_SEEDS,
        "excluded_seed_set_hash": source["excluded_seed_set_hash"],
        "new_seed_overlap_count": 0,
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
    tasks = validate_direct_edge_two_tasks(tasks, geant4_version=geant4_version)
    if (
        environment.get("mode") != "osc-production"
        or environment.get("accepted_statistical_evidence") is not True
        or geant4_version != "11.4.2"
        or environment_identity(environment) != environment.get("identity_hash")
    ):
        raise ValueError("direct edge-two requires accepted OSC Geant4 11.4.2 evidence")
    git_commit = _require_string(git.get("commit"), "submission Git commit")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct edge-two must be generated from a clean checkout")
    plan_hash = sha256_bytes(canonical_json([asdict(task) for task in tasks]))
    marker = _direct_marker(
        tasks=tasks,
        plan_hash=plan_hash,
        environment_identity_hash=_require_sha256(
            environment.get("identity_hash"), "environment identity"
        ),
        git_commit=git_commit,
        source=source,
    )
    campaign_id = DIRECT_CAMPAIGN_PREFIX + str(marker["identity_hash"])[:12]

    tasks_path = directory / "tasks.tsv"
    scan_args_path = directory / "scan_args.txt"
    readme_path = directory / "README.md"
    write_tasks(tasks_path, list(tasks))
    scan_args_path.write_text(
        "\n".join(
            [
                f"# schema_version={CAMPAIGN_SCHEMA_VERSION}",
                f"# campaign_id={campaign_id}",
                "# direct_production_child=EDGE-TWO",
                "# submission_route=ordinary-slurm-array-no-preflight",
                f"# array_spec={EXPECTED_ARRAY_SPEC}",
                f"# task_count={len(tasks)}",
                *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    readme_path.write_text(
        f"""# {campaign_id}

Direct single-layer edge-two steel-module follow-up campaign.

- Layout: two SiPMs on the same `+X` face at local `u = +/-25 mm`
- Thicknesses: `4, 8, 12, 16, 20, 24 mm`
- Sampling: `40 x 250-event` blocks per thickness
- Tasks: `240`
- Events: `60,000`
- Submission: one ordinary `1-240` Slurm array; no preflight
- Seeds: `480` new values, disjoint from the sealed pilot and original
  production program
- Plan hash: `{plan_hash}`

Validate before submission:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \
  --campaign-dir {public_directory} --check-only
```

Submit only after check-only reports exactly 240 tasks, zero submitted, and
zero complete.
""",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "study_preset": STUDY_PRESET,
        "stage": "production",
        "description": "Direct edge-two follow-up: complete six-thickness curve",
        "created_at_utc": created_at_utc,
        "plan_hash": plan_hash,
        "campaign_seed": CAMPAIGN_SEED,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "seed_blocks_per_configuration": EXPECTED_BLOCKS_PER_CONFIGURATION,
        "task_count": EXPECTED_TASK_COUNT,
        "configuration_axes": {
            "tile_thickness_mm": list(TILE_THICKNESSES_MM),
            "sipm_layout": [SIPM_LAYOUT],
            "absorber_transverse_mm": [500],
            "x_mm": [0],
            "y_mm": [0],
        },
        "configuration_groups": {
            SIPM_LAYOUT: {
                "tile_thickness_mm": list(TILE_THICKNESSES_MM),
                "blocks_per_configuration": EXPECTED_BLOCKS_PER_CONFIGURATION,
            }
        },
        "accepted_domain": {
            "tile_thickness_mm": list(TILE_THICKNESSES_MM),
            "sipm_layout": [
                "back-center",
                "edge-center",
                "edge-two",
                "back-four",
            ],
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
    (directory / "campaign.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def write_direct_edge_two_campaign_atomic(
    out_dir: Path,
    *,
    tasks: Iterable[CampaignTask],
    environment: dict[str, Any],
    source: dict[str, object],
    git: dict[str, object],
    created_at_utc: str | None = None,
) -> CampaignBundle:
    requested = out_dir.expanduser()
    if requested.is_symlink():
        raise ValueError(f"refusing symlink campaign target: {requested}")
    target = requested.resolve()
    if target.exists():
        raise ValueError(f"refusing to overwrite campaign target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        temporary.rmdir()
        _write_direct_tree(
            temporary,
            public_directory=target,
            tasks=tuple(tasks),
            environment=copy.deepcopy(environment),
            source=copy.deepcopy(source),
            git=copy.deepcopy(git),
            created_at_utc=created_at_utc or datetime.now(timezone.utc).isoformat(),
        )
        staged = load_campaign(temporary, verify_external_artifacts=True)
        validate_direct_edge_two_bundle(staged)
        if target.exists():
            raise ValueError(f"campaign target appeared during publish: {target}")
        os.rename(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    bundle = load_campaign(target, verify_external_artifacts=True)
    validate_direct_edge_two_bundle(bundle)
    return bundle


def validate_direct_edge_two_bundle(bundle: CampaignBundle) -> None:
    manifest = bundle.manifest
    if manifest.get("stage") != "production":
        raise ValueError("direct edge-two campaign stage must be production")
    if any(key in manifest for key in ("program_management", "production_management")):
        raise ValueError(
            "direct edge-two campaign must not contain managed-route markers"
        )
    environment = _require_mapping(manifest.get("environment"), "environment")
    geant4_version = _require_string(
        environment.get("geant4_version"), "environment Geant4 version"
    )
    if (
        environment.get("mode") != "osc-production"
        or environment.get("accepted_statistical_evidence") is not True
        or geant4_version != "11.4.2"
        or environment_identity(environment) != environment.get("identity_hash")
    ):
        raise ValueError("direct edge-two requires accepted OSC Geant4 11.4.2 evidence")
    tasks = validate_direct_edge_two_tasks(bundle.tasks, geant4_version=geant4_version)
    if (
        manifest.get("events_per_task") != EXPECTED_EVENTS_PER_TASK
        or manifest.get("seed_blocks_per_configuration")
        != EXPECTED_BLOCKS_PER_CONFIGURATION
        or manifest.get("campaign_seed") != CAMPAIGN_SEED
        or manifest.get("task_count") != EXPECTED_TASK_COUNT
    ):
        raise ValueError("direct edge-two task/event policy is invalid")
    expected_axes = {
        "tile_thickness_mm": list(TILE_THICKNESSES_MM),
        "sipm_layout": [SIPM_LAYOUT],
        "absorber_transverse_mm": [500],
        "x_mm": [0],
        "y_mm": [0],
    }
    if manifest.get("configuration_axes") != expected_axes:
        raise ValueError("direct edge-two configuration axes are not exact")
    if manifest.get("configuration_groups") != {
        SIPM_LAYOUT: {
            "tile_thickness_mm": list(TILE_THICKNESSES_MM),
            "blocks_per_configuration": EXPECTED_BLOCKS_PER_CONFIGURATION,
        }
    }:
        raise ValueError("direct edge-two configuration group is not exact")
    if manifest.get("source_contract") != {
        "particle": "neutron",
        "authoritative_quantity": "kinetic_energy",
        "kinetic_energy_mev": 1000,
        "profile": "point",
        "direction": [0, 0, -1],
        "angular_model": "pencil",
    }:
        raise ValueError("direct edge-two neutron source contract is not exact")
    git = _require_mapping(manifest.get("git"), "Git identity")
    if git.get("dirty") is not False or git.get("dirty_paths") != []:
        raise ValueError("direct edge-two campaign Git identity must be clean")

    marker = _require_mapping(manifest.get("direct_production"), "direct marker")
    if (
        marker.get("schema_version") != DIRECT_SCHEMA_VERSION
        or marker.get("route") != DIRECT_ROUTE
        or marker.get("preflight_required") is not False
        or marker.get("managed_execution_required") is not False
        or marker.get("child_id") != DIRECT_CHILD_ID
        or marker.get("array_spec") != EXPECTED_ARRAY_SPEC
        or marker.get("seed_derivation_domain") != SEED_DERIVATION_DOMAIN
        or marker.get("new_seed_overlap_count") != 0
    ):
        raise ValueError("direct edge-two marker identity is invalid")
    expected_scalars = {
        "task_count": EXPECTED_TASK_COUNT,
        "event_count": EXPECTED_EVENT_COUNT,
        "events_per_task": EXPECTED_EVENTS_PER_TASK,
        "configuration_count": EXPECTED_CONFIGURATION_COUNT,
        "blocks_per_configuration": EXPECTED_BLOCKS_PER_CONFIGURATION,
        "campaign_seed": CAMPAIGN_SEED,
        "task_plan_hash": bundle.plan_hash,
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(_task_seeds(tasks)),
        "excluded_seed_count": EXPECTED_EXCLUDED_SEEDS,
        "environment_identity": environment.get("identity_hash"),
        "submission_git_commit": git.get("commit"),
    }
    for key, expected in expected_scalars.items():
        if marker.get(key) != expected:
            raise ValueError(f"direct edge-two marker mismatch: {key}")
    source = _require_mapping(marker.get("source"), "direct edge-two source")
    if (
        source.get("prior_production_task_count") != EXPECTED_PRIOR_PRODUCTION_TASKS
        or source.get("prior_production_seed_count")
        != EXPECTED_PRIOR_PRODUCTION_SEEDS
        or source.get("pilot_excluded_seed_count")
        != EXPECTED_PILOT_EXCLUDED_SEEDS
        or source.get("excluded_seed_count") != EXPECTED_EXCLUDED_SEEDS
        or marker.get("excluded_seed_set_hash")
        != source.get("excluded_seed_set_hash")
    ):
        raise ValueError("direct edge-two source seed exclusion is invalid")
    for key in (
        "program_hash",
        "authorization_graph_hash",
        "production_program_json_sha256",
        "production_program_sha256s_sha256",
        "excluded_seed_set_hash",
    ):
        _require_sha256(source.get(key), f"direct edge-two source {key}")
    _require_string(source.get("program_id"), "direct edge-two source program ID")
    decision = _require_mapping(
        source.get("decision_document"), "direct edge-two decision document"
    )
    if decision.get("path") != DECISION_DOCUMENT.as_posix():
        raise ValueError("direct edge-two decision-document path is invalid")
    _require_sha256(decision.get("sha256"), "direct edge-two decision-document hash")

    identity = _require_sha256(marker.get("identity_hash"), "direct identity hash")
    unhashed = dict(marker)
    unhashed.pop("identity_hash")
    if sha256_bytes(canonical_json(unhashed)) != identity:
        raise ValueError("direct edge-two marker identity hash mismatch")
    if bundle.campaign_id != DIRECT_CAMPAIGN_PREFIX + identity[:12]:
        raise ValueError("direct edge-two campaign ID does not match its identity")
    expected_scan_args = tuple(
        shlex.join(scan_args(task, bundle.campaign_id)) for task in tasks
    )
    if bundle.scan_args != expected_scan_args:
        raise ValueError("direct edge-two scan arguments do not match the frozen tasks")


def main() -> int:
    args = parse_args()
    repo_root = args.project_root.expanduser().resolve()
    try:
        git = clean_git_identity(repo_root)
        decision = validate_decision_document(repo_root)
        program = load_production_program(
            args.program_dir, verify_runtime_artifacts=False
        )
        validate_program_identity(program)
        excluded = prior_seed_set(program)
        tasks = build_edge_two_tasks(excluded)
        if set(_task_seeds(tasks)) & excluded:
            raise ValueError("new edge-two seeds overlap prior accepted evidence")
        environment = accepted_environment(
            image=args.image,
            g4_data_manifest=args.g4_data_manifest,
            build_artifact=args.build_artifact,
        )
        bundle = write_direct_edge_two_campaign_atomic(
            args.out_dir,
            tasks=tasks,
            environment=environment,
            source=source_record(
                program,
                excluded_seeds=excluded,
                decision_document=decision,
            ),
            git=git,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot generate direct edge-two campaign: {exc}", file=sys.stderr)
        return 1
    print(f"Campaign: {bundle.campaign_id}")
    print("Stage: production (direct edge-two)")
    print("Tasks: 240")
    print("Events: 60000")
    print("Configurations: 6")
    print("Blocks: 40 x 250 events per thickness")
    print("Array: 1-240")
    print("Preflight: none")
    print(f"Output: {bundle.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
