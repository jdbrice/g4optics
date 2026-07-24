#!/usr/bin/env python3
"""Immutable Phase-1 plan support for the steel-module production program."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shlex
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from steel_module_campaign_lib import (
    CAMPAIGN_SCHEMA_VERSION,
    SIPM_LAYOUTS,
    STUDY_PRESET,
    TILE_THICKNESSES_MM,
    CampaignTask,
    canonical_json,
    environment_identity,
    load_campaign,
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
    task_configuration_hash,
    verify_finalized_checksums,
)


PROGRAM_SCHEMA_VERSION = "steel-module-production-program-v1"
PROGRAM_IDENTITY_SCHEMA_VERSION = "steel-module-production-program-identity-v1"
CHILD_PLAN_SCHEMA_VERSION = "steel-module-production-child-plan-v1"
PILOT_EXCLUSION_SCHEMA_VERSION = "steel-module-pilot-seed-exclusion-v1"
AUTHORIZATION_GRAPH_SCHEMA_VERSION = (
    "steel-module-production-authorization-graph-v1"
)
PRODUCTION_POLICY_ID = "steel-module-production-sms016-019-v1"
SEED_DERIVATION_DOMAIN = "steel-module-production-seed-v1"
MAX_GEANT4_SEED = 2_147_483_646
EVENTS_PER_TASK = 250
ABSORBER_TRANSVERSE_MM = 500
CHILD_ORDER = ("FIXED", "BC-S1", "BC-S2", "BC-S3", "BC-S4")
CHECKPOINT_ORDER = ("BC-S1", "BC-S2", "BC-S3", "BC-S4")

ACCEPTED_PILOT_CAMPAIGN_ID = "sm-v1-convergence-pilot-4e9ef8618948"
ACCEPTED_PILOT_PLAN_HASH = (
    "4e9ef861894846347cee7c2422383e013f774c74120fe05f5d1e52a980d18413"
)
ACCEPTED_PILOT_SOURCE_COMMIT = "cfd7d974af2c5f40f7d76948172fc87ee70bfa3c"
ACCEPTED_PILOT_ENVIRONMENT_IDENTITY = (
    "6984ac6b9a4b46bc958ca233cf6cddaa02d5b9213cff387f27363536a0f1f709"
)
ACCEPTED_EXECUTABLE_SHA256 = (
    "adbcbc2facf4bb39b4671c7d47a8f9caf9e4f691b9a28a6d5e0d79d354b4cf41"
)
ACCEPTED_PILOT_TASK_COUNT = 120
ACCEPTED_PILOT_CAMPAIGN_JSON_SHA256 = (
    "e14c7494956fd991e9d07c68c2242991a2301e4ea067236c4cd3db09d220a3f2"
)
ACCEPTED_PILOT_TASKS_TSV_SHA256 = (
    "6d2f483804d1ea6146478075d37f91a74890bf57818487d7d569af54ecb37d72"
)
ACCEPTED_PILOT_FINALIZED_SHA256SUMS_SHA256 = (
    "1b5109c5e7953f86c18aae15c34aee1101b2a6bb206f1a0dc6972f60c71a546e"
)
ACCEPTED_PILOT_TASK_INDEX_SHA256 = (
    "d4a6b52d0b3b1277bb47e979d8d7efdd6d08ce00f3551a668690b829136baf9e"
)
ACCEPTED_PILOT_EVENT_AUDIT_SHA256 = (
    "b82e2325472e3296af13f61d6851b7931468dd7bde44bb36a5f5911de6325d2f"
)
ACCEPTED_PILOT_VALIDATION_REPORT_SHA256 = (
    "1a95ea340726cb774501a8ebce2fa02172eea732d473bb9241d4b1993a47f732"
)
ACCEPTED_PILOT_SEED_SET_HASH = (
    "06442dc385148aaab78a458fbf984413f043387cddc12857789f1da1f936de9a"
)
ACCEPTED_PILOT_TASK_SEED_MAPPING_HASH = (
    "e5220b43fed93f37771c5a69e3d01dd12aab53e8f2159efcdb42d882cbb47d08"
)
ACCEPTED_PILOT_EXCLUSION_HASH = (
    "262cb224eab2b4db760a8f41b0ab03bdb69fa9ea57e2ce4c1e97a16e206ed8e6"
)
ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT = (
    "88f15eace17137475310913d585a96908a493c72"
)
ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE = (
    "1d3f2b60b1b6230d3209e7a9ab661ac304f4f458"
)
PILOT_EVENT_AUDIT_SCHEMA_VERSION = "steel-module-event-audit-v1"
PILOT_VALIDATION_SCHEMA_VERSION = "steel-module-validation-report-v1"

ALLOCATION_FIELDS = (
    "child_id",
    "sipm_layout",
    "tile_thickness_mm",
    "absorber_transverse_mm",
    "seed_block_start",
    "seed_block_stop",
    "events_per_block",
)
TASK_SET_FIELDS = ("child_task_index", "parent_task_index", "logical_task_id")
SEED_REGISTRY_FIELDS = (
    "program_task_index",
    "child_id",
    "logical_task_id",
    "seed1",
    "seed1_nonce",
    "seed2",
    "seed2_nonce",
)
EXCLUDED_SEED_FIELDS = ("logical_task_id", "slot", "seed")

ROOT_REQUIRED_FILES = {
    "README.md",
    "allocation.tsv",
    "excluded_seeds.tsv",
    "pilot_exclusion.json",
    "production_program.json",
    "scan_args.txt",
    "seed_registry.tsv",
    "tasks.tsv",
}
CHILD_REQUIRED_FILES = {"child_plan.json", "scan_args.txt", "task_set.tsv"}


@dataclass(frozen=True)
class AllocationRow:
    child_id: str
    sipm_layout: str
    tile_thickness_mm: int
    absorber_transverse_mm: int
    seed_block_start: int
    seed_block_stop: int
    events_per_block: int


@dataclass(frozen=True)
class SeedRecord:
    program_task_index: int
    child_id: str
    logical_task_id: str
    seed1: int
    seed1_nonce: int
    seed2: int
    seed2_nonce: int


@dataclass(frozen=True)
class ExcludedSeed:
    logical_task_id: str
    slot: int
    seed: int


@dataclass(frozen=True)
class ProgramPlan:
    tasks: tuple[CampaignTask, ...]
    child_task_indices: dict[str, tuple[int, ...]]
    seed_records: tuple[SeedRecord, ...]


@dataclass(frozen=True)
class ProductionProgramBundle:
    directory: Path
    manifest: dict[str, Any]
    tasks: tuple[CampaignTask, ...]
    child_tasks: dict[str, tuple[CampaignTask, ...]]
    excluded_seeds: tuple[ExcludedSeed, ...]


def accepted_allocation_rows() -> tuple[AllocationRow, ...]:
    rows: list[AllocationRow] = []
    for thickness in (8, 12, 16, 20):
        rows.append(
            AllocationRow("FIXED", "back-center", thickness, 500, 0, 16, 250)
        )
    for thickness in TILE_THICKNESSES_MM:
        rows.append(
            AllocationRow("FIXED", "edge-center", thickness, 500, 0, 40, 250)
        )
    for thickness in TILE_THICKNESSES_MM:
        rows.append(
            AllocationRow("FIXED", "back-four", thickness, 500, 0, 48, 250)
        )
    for child_id, start, stop in (
        ("BC-S1", 0, 16),
        ("BC-S2", 16, 40),
        ("BC-S3", 40, 80),
        ("BC-S4", 80, 161),
    ):
        for thickness in (4, 24):
            rows.append(
                AllocationRow(
                    child_id,
                    "back-center",
                    thickness,
                    500,
                    start,
                    stop,
                    250,
                )
            )
    return tuple(rows)


def read_allocation(path: Path) -> tuple[AllocationRow, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        if tuple(reader.fieldnames or ()) != ALLOCATION_FIELDS:
            raise ValueError(
                f"invalid production allocation header in {path}: "
                f"{reader.fieldnames!r}"
            )
    parsed: list[AllocationRow] = []
    for number, row in enumerate(rows, start=2):
        try:
            parsed.append(
                AllocationRow(
                    child_id=row["child_id"],
                    sipm_layout=row["sipm_layout"],
                    tile_thickness_mm=int(row["tile_thickness_mm"]),
                    absorber_transverse_mm=int(row["absorber_transverse_mm"]),
                    seed_block_start=int(row["seed_block_start"]),
                    seed_block_stop=int(row["seed_block_stop"]),
                    events_per_block=int(row["events_per_block"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid allocation row {number}: {row}") from exc
    if tuple(parsed) != accepted_allocation_rows():
        raise ValueError(
            "production allocation does not exactly match the accepted "
            "SMS-016/SMS-017 policy"
        )
    return tuple(parsed)


def allocation_policy_hash(rows: Iterable[AllocationRow]) -> str:
    return sha256_bytes(canonical_json([asdict(row) for row in rows]))


def derive_unique_seed(
    *,
    program_seed: int,
    logical_task_id: str,
    slot: int,
    used: set[int],
) -> tuple[int, int]:
    nonce = 0
    while True:
        material = (
            f"{SEED_DERIVATION_DOMAIN}|{program_seed}|{logical_task_id}|"
            f"{slot}|{nonce}"
        ).encode()
        candidate = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        candidate = candidate % MAX_GEANT4_SEED + 1
        if candidate not in used:
            used.add(candidate)
            return candidate, nonce
        nonce += 1


def logical_task_id(row: AllocationRow, block: int) -> str:
    return (
        f"production-l{row.sipm_layout}-t{row.tile_thickness_mm:02d}-"
        f"a{row.absorber_transverse_mm:03d}-b{block:03d}"
    )


def build_program_plan(
    *,
    allocation_rows: tuple[AllocationRow, ...],
    program_seed: int,
    excluded_seeds: Iterable[ExcludedSeed],
    geant4_version: str,
) -> ProgramPlan:
    if allocation_rows != accepted_allocation_rows():
        raise ValueError("refusing to build a non-canonical production allocation")
    if program_seed < 0:
        raise ValueError("program_seed must be non-negative")
    excluded_records = tuple(excluded_seeds)
    excluded_values = [record.seed for record in excluded_records]
    if len(excluded_values) != len(set(excluded_values)):
        raise ValueError("sealed pilot exclusion contains duplicate seeds")
    used = set(excluded_values)
    tasks: list[CampaignTask] = []
    seed_records: list[SeedRecord] = []
    child_indices: dict[str, list[int]] = {child: [] for child in CHILD_ORDER}
    seen_ids: set[str] = set()

    for row in allocation_rows:
        for block in range(row.seed_block_start, row.seed_block_stop):
            task_id = logical_task_id(row, block)
            if task_id in seen_ids:
                raise ValueError(f"duplicate production logical task: {task_id}")
            seen_ids.add(task_id)
            seed1, nonce1 = derive_unique_seed(
                program_seed=program_seed,
                logical_task_id=task_id,
                slot=1,
                used=used,
            )
            seed2, nonce2 = derive_unique_seed(
                program_seed=program_seed,
                logical_task_id=task_id,
                slot=2,
                used=used,
            )
            task_index = len(tasks) + 1
            configuration_hash = task_configuration_hash(
                stage="production",
                tile_thickness_mm=row.tile_thickness_mm,
                sipm_layout=row.sipm_layout,
                absorber_transverse_mm=row.absorber_transverse_mm,
                x_mm=0,
                y_mm=0,
                events=row.events_per_block,
                seed_block=block,
                seed1=seed1,
                seed2=seed2,
                geant4_version=geant4_version,
            )
            task = CampaignTask(
                task_index=task_index,
                logical_task_id=task_id,
                stage="production",
                tile_thickness_mm=row.tile_thickness_mm,
                sipm_layout=row.sipm_layout,
                absorber_transverse_mm=row.absorber_transverse_mm,
                x_mm=0,
                y_mm=0,
                seed_block=block,
                events=row.events_per_block,
                seed1=seed1,
                seed2=seed2,
                configuration_hash=configuration_hash,
            )
            tasks.append(task)
            child_indices[row.child_id].append(task_index)
            seed_records.append(
                SeedRecord(
                    program_task_index=task_index,
                    child_id=row.child_id,
                    logical_task_id=task_id,
                    seed1=seed1,
                    seed1_nonce=nonce1,
                    seed2=seed2,
                    seed2_nonce=nonce2,
                )
            )

    plan = ProgramPlan(
        tasks=tuple(tasks),
        child_task_indices={
            child: tuple(child_indices[child]) for child in CHILD_ORDER
        },
        seed_records=tuple(seed_records),
    )
    validate_program_plan(plan, allocation_rows, excluded_records)
    return plan


def validate_program_plan(
    plan: ProgramPlan,
    allocation_rows: tuple[AllocationRow, ...],
    excluded_seeds: tuple[ExcludedSeed, ...],
) -> None:
    if len(plan.tasks) != 914:
        raise ValueError(f"production program must contain 914 tasks, got {len(plan.tasks)}")
    if sum(task.events for task in plan.tasks) != 228_500:
        raise ValueError("production program event total must be 228500")
    if [task.task_index for task in plan.tasks] != list(range(1, 915)):
        raise ValueError("program task indexes must be contiguous and one-based")
    expected_counts = {
        "FIXED": 592,
        "BC-S1": 32,
        "BC-S2": 48,
        "BC-S3": 80,
        "BC-S4": 162,
    }
    if {
        child: len(plan.child_task_indices.get(child, ())) for child in CHILD_ORDER
    } != expected_counts:
        raise ValueError("production child task counts do not match accepted policy")

    all_indices = [
        index for child in CHILD_ORDER for index in plan.child_task_indices[child]
    ]
    if sorted(all_indices) != list(range(1, 915)) or len(set(all_indices)) != 914:
        raise ValueError("production child task sets are not a disjoint parent partition")
    if len(plan.seed_records) != 914:
        raise ValueError("seed registry must contain one row per production task")
    production_seeds = [
        seed
        for record in plan.seed_records
        for seed in (record.seed1, record.seed2)
    ]
    excluded_values = {record.seed for record in excluded_seeds}
    if len(set(production_seeds)) != 1_828:
        raise ValueError("production seeds are not globally unique")
    if set(production_seeds) & excluded_values:
        raise ValueError("production seeds overlap the sealed pilot exclusion")
    if any(seed <= 0 or seed > MAX_GEANT4_SEED for seed in production_seeds):
        raise ValueError("production seed lies outside the Geant4 range")
    if any(
        task.stage != "production"
        or task.events != EVENTS_PER_TASK
        or task.absorber_transverse_mm != ABSORBER_TRANSVERSE_MM
        or task.x_mm != 0
        or task.y_mm != 0
        for task in plan.tasks
    ):
        raise ValueError("production task violates the fixed geometry/event contract")

    expected_membership: dict[str, set[tuple[str, int, int]]] = {
        child: set() for child in CHILD_ORDER
    }
    for row in allocation_rows:
        expected_membership[row.child_id].update(
            (row.sipm_layout, row.tile_thickness_mm, block)
            for block in range(row.seed_block_start, row.seed_block_stop)
        )
    task_by_index = {task.task_index: task for task in plan.tasks}
    for child in CHILD_ORDER:
        actual = {
            (
                task_by_index[index].sipm_layout,
                task_by_index[index].tile_thickness_mm,
                task_by_index[index].seed_block,
            )
            for index in plan.child_task_indices[child]
        }
        if actual != expected_membership[child]:
            raise ValueError(f"{child} task membership does not match accepted allocation")


def ordered_task_hash(tasks: Iterable[CampaignTask]) -> str:
    return sha256_bytes(canonical_json([asdict(task) for task in tasks]))


def task_set_hash(tasks: Iterable[CampaignTask]) -> str:
    return sha256_bytes(
        canonical_json(sorted(task.logical_task_id for task in tasks))
    )


def seed_pair_registry_hash(tasks: Iterable[CampaignTask]) -> str:
    return sha256_bytes(
        canonical_json(
            [
                {
                    "logical_task_id": task.logical_task_id,
                    "seed1": task.seed1,
                    "seed2": task.seed2,
                }
                for task in tasks
            ]
        )
    )


def seed_set_hash(seeds: Iterable[int]) -> str:
    return sha256_bytes(canonical_json(sorted(seeds)))


def child_tasks(plan: ProgramPlan, child_id: str) -> tuple[CampaignTask, ...]:
    by_index = {task.task_index: task for task in plan.tasks}
    return tuple(by_index[index] for index in plan.child_task_indices[child_id])


def materialized_tasks(tasks: Iterable[CampaignTask]) -> tuple[CampaignTask, ...]:
    return tuple(replace(task, task_index=index) for index, task in enumerate(tasks, 1))


def child_descriptor(plan: ProgramPlan, child_id: str) -> dict[str, Any]:
    tasks = child_tasks(plan, child_id)
    materialized = materialized_tasks(tasks)
    campaign_plan_hash = ordered_task_hash(materialized)
    campaign_id = (
        f"sm-v1-production-{child_id.lower()}-{campaign_plan_hash[:12]}"
    )
    ordinal = CHILD_ORDER.index(child_id)
    predecessor = None if child_id in ("FIXED", "BC-S1") else CHILD_ORDER[ordinal - 1]
    descriptor: dict[str, Any] = {
        "child_id": child_id,
        "ordinal": ordinal,
        "role": "fixed-allocation" if child_id == "FIXED" else "back-center-increment",
        "predecessor": predecessor,
        "materialized_campaign_id": campaign_id,
        "task_count": len(tasks),
        "event_count": sum(task.events for task in tasks),
        "parent_task_plan_hash": ordered_task_hash(tasks),
        "materialized_campaign_plan_hash": campaign_plan_hash,
        "task_set_hash": task_set_hash(tasks),
    }
    return descriptor


def child_plan_hash(descriptor: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(descriptor))


def checkpoint_descriptors(plan: ProgramPlan) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    included = ["FIXED"]
    for checkpoint in CHECKPOINT_ORDER:
        included.append(checkpoint)
        tasks = tuple(
            task
            for child in included
            for task in child_tasks(plan, child)
        )
        descriptors.append(
            {
                "checkpoint_id": checkpoint,
                "included_children": list(included),
                "task_count": len(tasks),
                "event_count": sum(task.events for task in tasks),
                "cumulative_task_set_hash": task_set_hash(tasks),
            }
        )
    return descriptors


def evidence_state_descriptors(plan: ProgramPlan) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    bc_prefix: list[str] = []
    for checkpoint in CHECKPOINT_ORDER:
        bc_prefix.append(checkpoint)
        bc_tasks = tuple(
            task for child in bc_prefix for task in child_tasks(plan, child)
        )
        states.append(
            {
                "state_id": f"BC-ONLY-{checkpoint.removeprefix('BC-')}",
                "evidence_mode": "back-center-only",
                "included_children": list(bc_prefix),
                "task_count": len(bc_tasks),
                "event_count": sum(task.events for task in bc_tasks),
                "task_set_hash": task_set_hash(bc_tasks),
            }
        )
        full_tasks = tuple(child_tasks(plan, "FIXED")) + bc_tasks
        states.append(
            {
                "state_id": checkpoint,
                "evidence_mode": "four-primary-contrast",
                "included_children": ["FIXED", *bc_prefix],
                "task_count": len(full_tasks),
                "event_count": sum(task.events for task in full_tasks),
                "task_set_hash": task_set_hash(full_tasks),
            }
        )
    return states


def authorization_graph() -> dict[str, Any]:
    gates: list[dict[str, Any]] = []
    for ordinal, gate_id in enumerate(CHECKPOINT_ORDER, start=1):
        previous_gate = None if ordinal == 1 else CHECKPOINT_ORDER[ordinal - 2]
        next_gate = None if ordinal == len(CHECKPOINT_ORDER) else CHECKPOINT_ORDER[ordinal]
        source_states = [f"BC-ONLY-S{ordinal}"]
        if ordinal > 1:
            source_states.append(gate_id)
        transitions: list[dict[str, Any]] = [
            {
                "transition_id": f"{gate_id}:pause-review",
                "selected_decision": "pause-review",
                "directly_authorized_children": [],
                "scheduling_eligible_children": [],
                "scheduling_record_required": False,
                "next_gate": None,
                "terminal_kind": "method-review",
            },
            {
                "transition_id": f"{gate_id}:stop-success",
                "selected_decision": "stop-success",
                "directly_authorized_children": ["FIXED"],
                "scheduling_eligible_children": [],
                "scheduling_record_required": False,
                "next_gate": None,
                "terminal_kind": "back-center-complete",
            },
        ]
        if next_gate is not None:
            transitions.append(
                {
                    "transition_id": f"{gate_id}:continue",
                    "selected_decision": "continue",
                    "directly_authorized_children": ["FIXED"],
                    "scheduling_eligible_children": [next_gate],
                    "scheduling_record_required": True,
                    "next_gate": next_gate,
                    "terminal_kind": None,
                }
            )
        gates.append(
            {
                "gate_id": gate_id,
                "ordinal": ordinal,
                "source_evidence_state_ids": source_states,
                "required_prior_decision": None
                if previous_gate is None
                else {
                    "gate_id": previous_gate,
                    "selected_decision": "continue",
                    "previous_decision_hash_required": True,
                },
                "required_scheduling_authorization_for": None
                if ordinal == 1
                else gate_id,
                "transitions": transitions,
            }
        )
    return {
        "schema_version": AUTHORIZATION_GRAPH_SCHEMA_VERSION,
        "semantics": {
            "default_unlisted_child": "denied",
            "eligibility_is_authorization": False,
            "analyzer_can_authorize": False,
            "recorder_can_submit": False,
            "authorization_record_scope": "latest-for-unsubmitted-child",
            "started_attempts_are_not_retroactively_revoked": True,
        },
        "initial": {
            "directly_authorized_children": ["BC-S1"],
            "scheduling_eligible_children": [],
            "default_denied_children": ["FIXED", "BC-S2", "BC-S3", "BC-S4"],
        },
        "review_gates": gates,
        "eligibility_policy": {
            "thresholds": {
                "relative_ci_half_width": 0.10,
                "loo_success": 0.10,
                "loo_continue_ceiling": 0.20,
            },
            "precedence": [
                "invalid-evidence-no-record",
                "material-adverse-tail-pause-only",
                "s4-success-stop-or-pause",
                "s4-otherwise-pause-only",
                "non-ceiling-success-stop-or-pause",
                "non-ceiling-narrowing-continue-or-pause",
                "fallback-pause-only",
            ],
            "human_tail_override": {
                "tail_materially_worsened": True,
                "allowed_decisions": ["pause-review"],
            },
            "ignored_metrics": [
                "ratio_direction",
                "unity_crossing",
                "unity_exclusion",
            ],
        },
    }


def authorization_graph_hash() -> str:
    return sha256_bytes(canonical_json(authorization_graph()))


def scan_args(task: CampaignTask, campaign_id: str) -> list[str]:
    return [
        "full",
        "custom",
        "--study-preset",
        STUDY_PRESET,
        "--tile-thickness-mm",
        str(task.tile_thickness_mm),
        "--sipm-layout",
        task.sipm_layout,
        "--absorber-transverse-mm",
        str(task.absorber_transverse_mm),
        "--x-min",
        "0",
        "--x-max",
        "0",
        "--y-min",
        "0",
        "--y-max",
        "0",
        "--step",
        "5",
        "--grid-unit",
        "mm",
        "--events",
        str(task.events),
        "--seed1",
        str(task.seed1),
        "--seed2",
        str(task.seed2),
        "--campaign-id",
        campaign_id,
        "--campaign-stage",
        task.stage,
        "--logical-task-id",
        task.logical_task_id,
        "--configuration-hash",
        task.configuration_hash,
        "--seed-block",
        str(task.seed_block),
        "--no-root-plots",
    ]


def write_tsv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def write_tasks(path: Path, tasks: Iterable[CampaignTask]) -> None:
    write_tsv(
        path,
        CampaignTask.__dataclass_fields__,
        (asdict(task) for task in tasks),
    )


def write_scan_args(
    path: Path,
    *,
    header: Iterable[str],
    lines: Iterable[list[str]],
) -> None:
    path.write_text(
        "\n".join([*[f"# {value}" for value in header], *[shlex.join(line) for line in lines]])
        + "\n",
        encoding="utf-8",
    )


def write_flat_checksums(directory: Path, filenames: Iterable[str]) -> None:
    names = sorted(filenames)
    (directory / "SHA256SUMS").write_text(
        "".join(f"{sha256_file(directory / name)}  {name}\n" for name in names),
        encoding="utf-8",
    )


def verify_exact_flat_checksums(
    directory: Path,
    required: set[str],
    *,
    allowed_directories: set[str] | None = None,
) -> None:
    allowed_directories = set() if allowed_directories is None else allowed_directories
    checksum_path = directory / "SHA256SUMS"
    if not checksum_path.is_file():
        raise ValueError(f"missing checksum manifest: {checksum_path}")
    recorded: set[str] = set()
    for raw in checksum_path.read_text(encoding="utf-8").splitlines():
        parts = raw.split("  ", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid checksum row: {raw!r}")
        digest, name = parts
        if Path(name).name != name or name in recorded:
            raise ValueError(f"invalid checksum filename: {name!r}")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid checksum digest for {name}")
        path = directory / name
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"checksum mismatch: {path}")
        recorded.add(name)
    entries = tuple(directory.iterdir())
    if any(path.is_symlink() for path in entries):
        raise ValueError(f"symbolic links are forbidden in immutable tree: {directory}")
    actual = {
        path.name
        for path in entries
        if path.is_file() and path.name != "SHA256SUMS"
    }
    actual_directories = {path.name for path in entries if path.is_dir()}
    actual_other = {
        path.name for path in entries if not path.is_file() and not path.is_dir()
    }
    if (
        recorded != required
        or actual != required
        or actual_directories != allowed_directories
        or actual_other
    ):
        raise ValueError(
            f"checksum coverage mismatch in {directory}: "
            f"recorded={sorted(recorded)}, actual={sorted(actual)}, "
            f"directories={sorted(actual_directories)}, other={sorted(actual_other)}"
        )


def _require_matching_identity(
    report: dict[str, Any],
    *,
    label: str,
    campaign_id: str,
    plan_hash: str,
    git_commit: str,
    environment_identity: str,
    accepted_statistical_evidence: bool,
) -> None:
    expected = {
        "campaign_id": campaign_id,
        "plan_hash": plan_hash,
        "git_commit": git_commit,
        "environment_identity": environment_identity,
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise ValueError(f"sealed pilot {label} {key} mismatch")
    if report.get("valid") is not True:
        raise ValueError(f"sealed pilot {label} is not valid")
    if report.get("accepted_statistical_evidence") is not accepted_statistical_evidence:
        raise ValueError(f"sealed pilot {label} evidence-acceptance mismatch")


def _load_pilot_exclusion(
    pilot_dir: Path,
    *,
    local_test_fixture: bool,
) -> tuple[dict[str, Any], tuple[ExcludedSeed, ...]]:
    bundle = load_campaign(pilot_dir, verify_external_artifacts=False)
    if local_test_fixture:
        if bundle.manifest.get("local_test_fixture") is not True:
            raise ValueError("local pilot fixture lacks the explicit test marker")
    elif bundle.manifest.get("local_test_fixture") is not None:
        raise ValueError("formal sealed-pilot input cannot be a local test fixture")
    if bundle.manifest.get("stage") != "convergence-pilot":
        raise ValueError("sealed pilot input is not a convergence-pilot campaign")
    if bundle.campaign_id != ACCEPTED_PILOT_CAMPAIGN_ID:
        raise ValueError("sealed pilot campaign_id is not the accepted pilot")
    if bundle.plan_hash != ACCEPTED_PILOT_PLAN_HASH:
        raise ValueError("sealed pilot plan_hash is not the accepted pilot")
    if bundle.git_commit != ACCEPTED_PILOT_SOURCE_COMMIT:
        raise ValueError("sealed pilot source commit is not the accepted pilot")
    if len(bundle.tasks) != ACCEPTED_PILOT_TASK_COUNT:
        raise ValueError("sealed pilot task count is not 120")
    environment = require_dict(bundle.manifest, "environment")
    expected_accepted = not local_test_fixture
    if environment.get("accepted_statistical_evidence") is not expected_accepted:
        raise ValueError("sealed pilot campaign evidence-acceptance mismatch")
    environment_identity = require_sha256(environment, "identity_hash")
    if environment_identity != ACCEPTED_PILOT_ENVIRONMENT_IDENTITY:
        raise ValueError("sealed pilot environment identity is not accepted")
    executable = require_dict(environment, "build_artifact")
    if require_sha256(executable, "sha256") != ACCEPTED_EXECUTABLE_SHA256:
        raise ValueError("sealed pilot executable SHA-256 is not accepted")

    finalized = bundle.directory / "finalized"
    verify_finalized_checksums(finalized)
    event_audit = load_json(finalized / "event_audit.json")
    validation = load_json(finalized / "validation_report.json")
    if event_audit.get("schema_version") != PILOT_EVENT_AUDIT_SCHEMA_VERSION:
        raise ValueError("sealed pilot event-audit schema mismatch")
    if validation.get("schema_version") != PILOT_VALIDATION_SCHEMA_VERSION:
        raise ValueError("sealed pilot validation schema mismatch")
    for label, report in (("event audit", event_audit), ("validation", validation)):
        _require_matching_identity(
            report,
            label=label,
            campaign_id=bundle.campaign_id,
            plan_hash=bundle.plan_hash,
            git_commit=bundle.git_commit,
            environment_identity=environment_identity,
            accepted_statistical_evidence=expected_accepted,
        )
    if event_audit.get("task_count") != 120:
        raise ValueError("sealed pilot event audit task count is not 120")
    if validation.get("expected_tasks") != 120 or validation.get("selected_tasks") != 120:
        raise ValueError("sealed pilot validation task counts are not 120/120")
    if validation.get("event_audit_integrated") is not True:
        raise ValueError("sealed pilot validation did not integrate the event audit")

    task_index_path = finalized / "task_index.tsv"
    with task_index_path.open(encoding="utf-8", newline="") as stream:
        finalized_rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(finalized_rows) != len(bundle.tasks):
        raise ValueError("sealed pilot finalized task index length mismatch")
    fields = (
        "task_index",
        "logical_task_id",
        "stage",
        "tile_thickness_mm",
        "sipm_layout",
        "absorber_transverse_mm",
        "x_mm",
        "y_mm",
        "seed_block",
        "events",
        "seed1",
        "seed2",
        "configuration_hash",
    )
    for task, row in zip(bundle.tasks, finalized_rows):
        expected = {field: str(getattr(task, field)) for field in fields}
        if any(row.get(field) != value for field, value in expected.items()):
            raise ValueError(
                f"sealed pilot finalized task identity mismatch: {task.logical_task_id}"
            )

    excluded = tuple(
        ExcludedSeed(task.logical_task_id, slot, seed)
        for task in bundle.tasks
        for slot, seed in ((1, task.seed1), (2, task.seed2))
    )
    seeds = [record.seed for record in excluded]
    if len(seeds) != 240 or len(set(seeds)) != 240:
        raise ValueError("sealed pilot must contribute 240 unique exclusion seeds")
    task_seed_mapping = [
        {
            "logical_task_id": task.logical_task_id,
            "seed1": task.seed1,
            "seed2": task.seed2,
        }
        for task in bundle.tasks
    ]
    task_seed_mapping_hash = sha256_bytes(canonical_json(task_seed_mapping))
    pilot_seed_set_hash = seed_set_hash(seeds)
    if not local_test_fixture and (
        task_seed_mapping_hash != ACCEPTED_PILOT_TASK_SEED_MAPPING_HASH
        or pilot_seed_set_hash != ACCEPTED_PILOT_SEED_SET_HASH
    ):
        raise ValueError("sealed pilot seed registry is not the accepted registry")
    selection = require_dict(
        require_dict(bundle.manifest, "artifacts"), "configuration_selection"
    )
    snapshot_hashes = {
        "campaign_json_sha256": sha256_file(bundle.directory / "campaign.json"),
        "tasks_tsv_sha256": sha256_file(bundle.directory / "tasks.tsv"),
        "finalized_sha256s_sha256": sha256_file(finalized / "SHA256SUMS"),
        "finalized_task_index_sha256": sha256_file(task_index_path),
        "event_audit_sha256": sha256_file(finalized / "event_audit.json"),
        "validation_report_sha256": sha256_file(
            finalized / "validation_report.json"
        ),
    }
    accepted_snapshot_hashes = {
        "campaign_json_sha256": ACCEPTED_PILOT_CAMPAIGN_JSON_SHA256,
        "tasks_tsv_sha256": ACCEPTED_PILOT_TASKS_TSV_SHA256,
        "finalized_sha256s_sha256": ACCEPTED_PILOT_FINALIZED_SHA256SUMS_SHA256,
        "finalized_task_index_sha256": ACCEPTED_PILOT_TASK_INDEX_SHA256,
        "event_audit_sha256": ACCEPTED_PILOT_EVENT_AUDIT_SHA256,
        "validation_report_sha256": ACCEPTED_PILOT_VALIDATION_REPORT_SHA256,
    }
    snapshot_verified = snapshot_hashes == accepted_snapshot_hashes
    if not local_test_fixture and not snapshot_verified:
        mismatches = sorted(
            key
            for key, expected in accepted_snapshot_hashes.items()
            if snapshot_hashes[key] != expected
        )
        raise ValueError(
            "sealed pilot does not match the accepted checksum snapshot: "
            + ", ".join(mismatches)
        )

    payload: dict[str, Any] = {
        "schema_version": PILOT_EXCLUSION_SCHEMA_VERSION,
        "accepted_statistical_evidence": expected_accepted,
        "local_test_fixture": local_test_fixture,
        "accepted_snapshot_verified": snapshot_verified,
        "campaign_id": bundle.campaign_id,
        "plan_hash": bundle.plan_hash,
        "campaign_source_commit": bundle.git_commit,
        "environment_identity": environment_identity,
        "executable_sha256": ACCEPTED_EXECUTABLE_SHA256,
        "task_count": len(bundle.tasks),
        "seed_count": len(seeds),
        **snapshot_hashes,
        "configuration_selection_sha256": require_sha256(selection, "sha256"),
        "task_seed_mapping_hash": task_seed_mapping_hash,
        "seed_set_hash": pilot_seed_set_hash,
    }
    payload["pilot_exclusion_hash"] = sha256_bytes(canonical_json(payload))
    if (
        not local_test_fixture
        and payload["pilot_exclusion_hash"] != ACCEPTED_PILOT_EXCLUSION_HASH
    ):
        raise ValueError("sealed pilot exclusion identity is not the accepted identity")
    return payload, excluded


def load_sealed_pilot(
    pilot_dir: Path,
) -> tuple[dict[str, Any], tuple[ExcludedSeed, ...]]:
    """Load the one exact accepted pilot snapshot used by formal generation."""

    return _load_pilot_exclusion(pilot_dir, local_test_fixture=False)


def load_local_test_pilot(
    pilot_dir: Path,
) -> tuple[dict[str, Any], tuple[ExcludedSeed, ...]]:
    """Load an explicit synthetic pilot fixture for unaccepted local checks only."""

    return _load_pilot_exclusion(pilot_dir, local_test_fixture=True)


def _validate_pilot_exclusion_policy(
    pilot_exclusion: dict[str, Any],
    excluded: tuple[ExcludedSeed, ...],
) -> None:
    recorded_exclusion_hash = require_sha256(
        pilot_exclusion, "pilot_exclusion_hash"
    )
    unhashed = dict(pilot_exclusion)
    unhashed.pop("pilot_exclusion_hash")
    if sha256_bytes(canonical_json(unhashed)) != recorded_exclusion_hash:
        raise ValueError("pilot exclusion semantic hash mismatch")
    expected = {
        "campaign_id": ACCEPTED_PILOT_CAMPAIGN_ID,
        "plan_hash": ACCEPTED_PILOT_PLAN_HASH,
        "campaign_source_commit": ACCEPTED_PILOT_SOURCE_COMMIT,
        "environment_identity": ACCEPTED_PILOT_ENVIRONMENT_IDENTITY,
        "executable_sha256": ACCEPTED_EXECUTABLE_SHA256,
        "task_count": ACCEPTED_PILOT_TASK_COUNT,
        "seed_count": 2 * ACCEPTED_PILOT_TASK_COUNT,
    }
    for key, value in expected.items():
        if pilot_exclusion.get(key) != value:
            raise ValueError(f"pilot exclusion violates accepted policy: {key}")

    seeds = [record.seed for record in excluded]
    if (
        len(seeds) != 240
        or len(set(seeds)) != 240
        or any(seed <= 0 or seed > MAX_GEANT4_SEED for seed in seeds)
    ):
        raise ValueError("pilot exclusion must contain 240 unique in-range seeds")

    accepted_pilot = pilot_exclusion.get("accepted_statistical_evidence") is True
    local_fixture = pilot_exclusion.get("local_test_fixture") is True
    snapshot_verified = pilot_exclusion.get("accepted_snapshot_verified") is True
    if accepted_pilot:
        if local_fixture or not snapshot_verified:
            raise ValueError("accepted pilot exclusion lacks the accepted snapshot")
        accepted_snapshot = {
            "campaign_json_sha256": ACCEPTED_PILOT_CAMPAIGN_JSON_SHA256,
            "tasks_tsv_sha256": ACCEPTED_PILOT_TASKS_TSV_SHA256,
            "finalized_sha256s_sha256": ACCEPTED_PILOT_FINALIZED_SHA256SUMS_SHA256,
            "finalized_task_index_sha256": ACCEPTED_PILOT_TASK_INDEX_SHA256,
            "event_audit_sha256": ACCEPTED_PILOT_EVENT_AUDIT_SHA256,
            "validation_report_sha256": ACCEPTED_PILOT_VALIDATION_REPORT_SHA256,
        }
        for key, value in accepted_snapshot.items():
            if pilot_exclusion.get(key) != value:
                raise ValueError(f"accepted pilot snapshot mismatch: {key}")
        if (
            pilot_exclusion.get("seed_set_hash") != ACCEPTED_PILOT_SEED_SET_HASH
            or pilot_exclusion.get("task_seed_mapping_hash")
            != ACCEPTED_PILOT_TASK_SEED_MAPPING_HASH
        ):
            raise ValueError("accepted pilot seed-registry identity mismatch")
        if recorded_exclusion_hash != ACCEPTED_PILOT_EXCLUSION_HASH:
            raise ValueError("accepted pilot exclusion hash mismatch")
    elif not local_fixture or snapshot_verified:
        raise ValueError("unaccepted pilot exclusion is not an explicit local fixture")


def _validate_runtime_policy(runtime: dict[str, Any]) -> None:
    mode = require_string(runtime, "mode")
    if mode not in ("local-dev", "osc-production"):
        raise ValueError("production runtime mode is invalid")
    if require_string(runtime, "geant4_version") != "11.4.2":
        raise ValueError("production runtime must use Geant4 11.4.2")
    executable = require_dict(runtime, "executable")
    if require_sha256(executable, "sha256") != ACCEPTED_EXECUTABLE_SHA256:
        raise ValueError("production runtime executable is not the accepted binary")
    if require_sha1(
        executable, "build_source_commit"
    ) != ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT:
        raise ValueError("production executable build-source commit is not accepted")
    if require_sha1(
        executable, "build_source_tree"
    ) != ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE:
        raise ValueError("production executable build-source tree is not accepted")
    artifacts_verified = runtime.get("artifacts_verified")
    if not isinstance(artifacts_verified, bool):
        raise ValueError("runtime.artifacts_verified must be boolean")
    if artifacts_verified != (mode == "osc-production"):
        raise ValueError("runtime mode and artifact-verification state disagree")


def _derived_program_acceptance(
    *,
    pilot_exclusion: dict[str, Any],
    program_source: dict[str, Any],
    runtime: dict[str, Any],
) -> bool:
    return (
        pilot_exclusion.get("accepted_statistical_evidence") is True
        and pilot_exclusion.get("accepted_snapshot_verified") is True
        and pilot_exclusion.get("local_test_fixture") is False
        and program_source.get("dirty") is False
        and runtime.get("mode") == "osc-production"
        and runtime.get("artifacts_verified") is True
    )


def _stable_runtime_identity(runtime: dict[str, Any]) -> dict[str, Any]:
    executable = require_dict(runtime, "executable")
    return {
        "mode": require_string(runtime, "mode"),
        "geant4_version": require_string(runtime, "geant4_version"),
        "environment_identity": require_sha256(runtime, "environment_identity"),
        "image_sha256": require_sha256(require_dict(runtime, "image"), "sha256"),
        "g4_data_manifest_sha256": require_sha256(
            require_dict(runtime, "g4_data_manifest"), "sha256"
        ),
        "executable_sha256": require_sha256(executable, "sha256"),
        "executable_build_source_commit": require_sha1(
            executable, "build_source_commit"
        ),
        "executable_build_source_tree": require_sha1(
            executable, "build_source_tree"
        ),
        "build_provenance_sha256": require_sha256(
            require_dict(executable, "build_provenance"), "sha256"
        ),
        "artifacts_verified": runtime.get("artifacts_verified") is True,
    }


def _program_identity(
    *,
    plan: ProgramPlan,
    allocation_rows: tuple[AllocationRow, ...],
    program_seed: int,
    pilot_exclusion: dict[str, Any],
    program_source: dict[str, Any],
    runtime: dict[str, Any],
    accepted_statistical_evidence: bool,
) -> dict[str, Any]:
    child_records = []
    for child in CHILD_ORDER:
        descriptor = child_descriptor(plan, child)
        child_records.append(
            {
                "descriptor": descriptor,
                "child_plan_hash": child_plan_hash(descriptor),
            }
        )
    production_seeds = [
        seed
        for task in plan.tasks
        for seed in (task.seed1, task.seed2)
    ]
    identity = {
        "schema_version": PROGRAM_IDENTITY_SCHEMA_VERSION,
        "policy": {
            "policy_id": PRODUCTION_POLICY_ID,
            "allocation_policy_hash": allocation_policy_hash(allocation_rows),
            "events_per_task": EVENTS_PER_TASK,
            "absorber_transverse_mm": ABSORBER_TRANSVERSE_MM,
            "child_order": list(CHILD_ORDER),
            "checkpoint_order": list(CHECKPOINT_ORDER),
            "decision_ids": ["SMS-016", "SMS-017", "SMS-018", "SMS-019"],
            "initial_submission_child": "BC-S1",
            "generic_submission": "forbidden",
            "child_materialization_phase": "phase-2-pending",
        },
        "program_seed": program_seed,
        "seed_derivation": {
            "domain": SEED_DERIVATION_DOMAIN,
            "hash": "SHA-256",
            "candidate_bytes": 8,
            "maximum_seed": MAX_GEANT4_SEED,
            "collision_resolution": "increment nonce until unused",
            "canonical_task_order": "allocation.tsv row order, then ascending seed_block",
        },
        "program_source": program_source,
        "runtime": _stable_runtime_identity(runtime),
        "accepted_statistical_evidence": accepted_statistical_evidence,
        "pilot_exclusion_hash": require_sha256(
            pilot_exclusion, "pilot_exclusion_hash"
        ),
        "parent": {
            "task_count": len(plan.tasks),
            "event_count": sum(task.events for task in plan.tasks),
            "plan_hash": ordered_task_hash(plan.tasks),
            "task_set_hash": task_set_hash(plan.tasks),
            "task_seed_mapping_hash": seed_pair_registry_hash(plan.tasks),
            "seed_set_hash": seed_set_hash(production_seeds),
        },
        "children": child_records,
        "checkpoints": checkpoint_descriptors(plan),
        "evidence_states": evidence_state_descriptors(plan),
        "authorization_graph": authorization_graph(),
        "authorization_graph_hash": authorization_graph_hash(),
    }
    return identity


def _write_program_tree(
    directory: Path,
    *,
    plan: ProgramPlan,
    allocation_source: Path,
    allocation_rows: tuple[AllocationRow, ...],
    program_seed: int,
    pilot_exclusion: dict[str, Any],
    excluded_seeds: tuple[ExcludedSeed, ...],
    program_source: dict[str, Any],
    runtime: dict[str, Any],
    accepted_statistical_evidence: bool,
    created_at_utc: str,
    description: str,
    fault_after_child: str | None,
) -> None:
    directory.mkdir()
    children_dir = directory / "children"
    children_dir.mkdir()
    shutil.copyfile(allocation_source, directory / "allocation.tsv")
    write_tasks(directory / "tasks.tsv", plan.tasks)
    write_tsv(
        directory / "seed_registry.tsv",
        SEED_REGISTRY_FIELDS,
        (asdict(record) for record in plan.seed_records),
    )
    write_tsv(
        directory / "excluded_seeds.tsv",
        EXCLUDED_SEED_FIELDS,
        (asdict(record) for record in excluded_seeds),
    )
    (directory / "pilot_exclusion.json").write_text(
        json.dumps(pilot_exclusion, indent=2) + "\n", encoding="utf-8"
    )

    identity = _program_identity(
        plan=plan,
        allocation_rows=allocation_rows,
        program_seed=program_seed,
        pilot_exclusion=pilot_exclusion,
        program_source=program_source,
        runtime=runtime,
        accepted_statistical_evidence=accepted_statistical_evidence,
    )
    program_hash = sha256_bytes(canonical_json(identity))
    program_id = f"sm-v1-production-program-{program_hash[:12]}"
    child_manifests: dict[str, dict[str, Any]] = {}
    root_scan_lines: list[list[str]] = []

    for child in CHILD_ORDER:
        selected = child_tasks(plan, child)
        descriptor = child_descriptor(plan, child)
        descriptor_hash = child_plan_hash(descriptor)
        child_dir = children_dir / child
        child_dir.mkdir()
        task_set_rows = [
            {
                "child_task_index": local_index,
                "parent_task_index": task.task_index,
                "logical_task_id": task.logical_task_id,
            }
            for local_index, task in enumerate(selected, 1)
        ]
        write_tsv(child_dir / "task_set.tsv", TASK_SET_FIELDS, task_set_rows)
        child_scan_lines = [
            scan_args(task, descriptor["materialized_campaign_id"])
            for task in selected
        ]
        write_scan_args(
            child_dir / "scan_args.txt",
            header=(
                f"schema_version={CHILD_PLAN_SCHEMA_VERSION}",
                f"program_id={program_id}",
                f"child_id={child}",
                f"task_count={len(selected)}",
            ),
            lines=child_scan_lines,
        )
        root_scan_lines.extend(child_scan_lines)
        child_manifest = {
            "schema_version": CHILD_PLAN_SCHEMA_VERSION,
            "program_id": program_id,
            "program_hash": program_hash,
            "parent_plan_hash": ordered_task_hash(plan.tasks),
            "descriptor": descriptor,
            "child_plan_hash": descriptor_hash,
            "submittable": False,
            "materialization_phase": "phase-2-pending",
            "artifacts": {
                "task_set_tsv": {
                    "path": "task_set.tsv",
                    "sha256": sha256_file(child_dir / "task_set.tsv"),
                },
                "scan_args": {
                    "path": "scan_args.txt",
                    "sha256": sha256_file(child_dir / "scan_args.txt"),
                },
            },
        }
        (child_dir / "child_plan.json").write_text(
            json.dumps(child_manifest, indent=2) + "\n", encoding="utf-8"
        )
        write_flat_checksums(child_dir, CHILD_REQUIRED_FILES)
        child_manifests[child] = {
            "path": f"children/{child}/child_plan.json",
            "sha256": sha256_file(child_dir / "child_plan.json"),
            "checksum_manifest_sha256": sha256_file(child_dir / "SHA256SUMS"),
            "child_plan_hash": descriptor_hash,
            "task_set_sha256": sha256_file(child_dir / "task_set.tsv"),
            "scan_args_sha256": sha256_file(child_dir / "scan_args.txt"),
        }
        if fault_after_child == child:
            raise RuntimeError(f"injected write failure after {child}")

    write_scan_args(
        directory / "scan_args.txt",
        header=(
            f"schema_version={PROGRAM_SCHEMA_VERSION}",
            f"program_id={program_id}",
            f"task_count={len(plan.tasks)}",
            "submittable=false",
        ),
        lines=root_scan_lines,
    )
    readme = f"""# {program_id}

{description}

- Schema: `{PROGRAM_SCHEMA_VERSION}`
- Study preset: `{STUDY_PRESET}`
- Tasks: `{len(plan.tasks)}`
- Events: `{sum(task.events for task in plan.tasks)}`
- Children: `{', '.join(CHILD_ORDER)}`
- Program hash: `{program_hash}`
- Accepted statistical evidence: `{str(accepted_statistical_evidence).lower()}`

This is a Phase-1 immutable plan, not a campaign. It intentionally contains no
`campaign.json`, submission journal, or Slurm entry point. Do not submit its
root or child directories. Phase 2 must materialize and authorize one exact
child through the managed production gate.
"""
    (directory / "README.md").write_text(readme, encoding="utf-8")

    manifest = {
        "schema_version": PROGRAM_SCHEMA_VERSION,
        "program_id": program_id,
        "program_hash": program_hash,
        "study_preset": STUDY_PRESET,
        "stage": "production",
        "description": description,
        "created_at_utc": created_at_utc,
        "submittable": False,
        "accepted_statistical_evidence": accepted_statistical_evidence,
        "identity": identity,
        "program_source": program_source,
        "runtime": runtime,
        "pilot_exclusion": pilot_exclusion,
        "task_count": len(plan.tasks),
        "total_events": sum(task.events for task in plan.tasks),
        "events_per_task": EVENTS_PER_TASK,
        "child_order": list(CHILD_ORDER),
        "children": child_manifests,
        "checkpoints": identity["checkpoints"],
        "evidence_states": identity["evidence_states"],
        "authorization_graph": identity["authorization_graph"],
        "authorization_graph_hash": identity["authorization_graph_hash"],
        "artifacts": {
            "tasks_tsv": {
                "path": "tasks.tsv",
                "sha256": sha256_file(directory / "tasks.tsv"),
            },
            "scan_args": {
                "path": "scan_args.txt",
                "sha256": sha256_file(directory / "scan_args.txt"),
            },
            "allocation": {
                "path": "allocation.tsv",
                "sha256": sha256_file(directory / "allocation.tsv"),
                "source_sha256": sha256_file(allocation_source),
            },
            "seed_registry": {
                "path": "seed_registry.tsv",
                "sha256": sha256_file(directory / "seed_registry.tsv"),
            },
            "excluded_seeds": {
                "path": "excluded_seeds.tsv",
                "sha256": sha256_file(directory / "excluded_seeds.tsv"),
            },
            "pilot_exclusion": {
                "path": "pilot_exclusion.json",
                "sha256": sha256_file(directory / "pilot_exclusion.json"),
            },
            "readme": {
                "path": "README.md",
                "sha256": sha256_file(directory / "README.md"),
            },
        },
    }
    (directory / "production_program.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    write_flat_checksums(directory, ROOT_REQUIRED_FILES)


def write_program_atomic(
    out_dir: Path,
    *,
    plan: ProgramPlan,
    allocation_source: Path,
    allocation_rows: tuple[AllocationRow, ...],
    program_seed: int,
    pilot_exclusion: dict[str, Any],
    excluded_seeds: tuple[ExcludedSeed, ...],
    program_source: dict[str, Any],
    runtime: dict[str, Any],
    created_at_utc: str,
    description: str,
    fault_after_child: str | None = None,
) -> ProductionProgramBundle:
    _validate_pilot_exclusion_policy(pilot_exclusion, excluded_seeds)
    _validate_runtime_policy(runtime)
    accepted_statistical_evidence = _derived_program_acceptance(
        pilot_exclusion=pilot_exclusion,
        program_source=program_source,
        runtime=runtime,
    )
    target = out_dir.expanduser().resolve()
    if target.exists():
        raise ValueError(f"refusing to overwrite production program target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_program_tree(
            temporary,
            plan=plan,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=program_seed,
            pilot_exclusion=pilot_exclusion,
            excluded_seeds=excluded_seeds,
            program_source=program_source,
            runtime=runtime,
            accepted_statistical_evidence=accepted_statistical_evidence,
            created_at_utc=created_at_utc,
            description=description,
            fault_after_child=fault_after_child,
        )
        load_production_program(
            temporary,
            verify_runtime_artifacts=accepted_statistical_evidence,
        )
        os.replace(temporary, target)
        return load_production_program(
            target,
            verify_runtime_artifacts=accepted_statistical_evidence,
        )
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _read_excluded_seeds(path: Path) -> tuple[ExcludedSeed, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        if tuple(reader.fieldnames or ()) != EXCLUDED_SEED_FIELDS:
            raise ValueError("excluded_seeds.tsv has an invalid header")
    try:
        return tuple(
            ExcludedSeed(row["logical_task_id"], int(row["slot"]), int(row["seed"]))
            for row in rows
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("excluded_seeds.tsv has an invalid row") from exc


def _read_seed_records(path: Path) -> tuple[SeedRecord, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        if tuple(reader.fieldnames or ()) != SEED_REGISTRY_FIELDS:
            raise ValueError("seed_registry.tsv has an invalid header")
    try:
        return tuple(
            SeedRecord(
                program_task_index=int(row["program_task_index"]),
                child_id=row["child_id"],
                logical_task_id=row["logical_task_id"],
                seed1=int(row["seed1"]),
                seed1_nonce=int(row["seed1_nonce"]),
                seed2=int(row["seed2"]),
                seed2_nonce=int(row["seed2_nonce"]),
            )
            for row in rows
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("seed_registry.tsv has an invalid row") from exc


def _recorded_artifact_sha(manifest: dict[str, Any], key: str, path: Path) -> None:
    record = require_dict(require_dict(manifest, "artifacts"), key)
    expected = require_sha256(record, "sha256")
    if require_string(record, "path") != path.name or sha256_file(path) != expected:
        raise ValueError(f"production program artifact mismatch: {key}")


def load_production_program(
    program_dir: Path,
    *,
    verify_runtime_artifacts: bool = False,
) -> ProductionProgramBundle:
    directory = program_dir.expanduser().resolve()
    if (directory / "campaign.json").exists():
        raise ValueError("Phase-1 production program must not contain campaign.json")
    verify_exact_flat_checksums(
        directory,
        ROOT_REQUIRED_FILES,
        allowed_directories={"children"},
    )
    manifest = load_json(directory / "production_program.json")
    if manifest.get("schema_version") != PROGRAM_SCHEMA_VERSION:
        raise ValueError("unsupported production program schema")
    if manifest.get("study_preset") != STUDY_PRESET or manifest.get("stage") != "production":
        raise ValueError("production program identity is invalid")
    if manifest.get("submittable") is not False:
        raise ValueError("Phase-1 production program must be non-submittable")
    program_id = require_string(manifest, "program_id")
    program_hash = require_sha256(manifest, "program_hash")
    if program_id != f"sm-v1-production-program-{program_hash[:12]}":
        raise ValueError("production program_id does not match program_hash")

    artifact_paths = {
        "tasks_tsv": directory / "tasks.tsv",
        "scan_args": directory / "scan_args.txt",
        "allocation": directory / "allocation.tsv",
        "seed_registry": directory / "seed_registry.tsv",
        "excluded_seeds": directory / "excluded_seeds.tsv",
        "pilot_exclusion": directory / "pilot_exclusion.json",
        "readme": directory / "README.md",
    }
    for key, path in artifact_paths.items():
        _recorded_artifact_sha(manifest, key, path)

    allocation_rows = read_allocation(directory / "allocation.tsv")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    excluded = _read_excluded_seeds(directory / "excluded_seeds.tsv")
    pilot_exclusion = load_json(directory / "pilot_exclusion.json")
    if pilot_exclusion.get("schema_version") != PILOT_EXCLUSION_SCHEMA_VERSION:
        raise ValueError("invalid pilot exclusion schema")
    accepted_pilot_identity = {
        "campaign_id": ACCEPTED_PILOT_CAMPAIGN_ID,
        "plan_hash": ACCEPTED_PILOT_PLAN_HASH,
        "campaign_source_commit": ACCEPTED_PILOT_SOURCE_COMMIT,
        "environment_identity": ACCEPTED_PILOT_ENVIRONMENT_IDENTITY,
        "executable_sha256": ACCEPTED_EXECUTABLE_SHA256,
        "task_count": ACCEPTED_PILOT_TASK_COUNT,
        "seed_count": 240,
    }
    for key, value in accepted_pilot_identity.items():
        if pilot_exclusion.get(key) != value:
            raise ValueError(f"pilot exclusion does not bind accepted {key}")
    embedded_pilot = require_dict(manifest, "pilot_exclusion")
    if pilot_exclusion != embedded_pilot:
        raise ValueError("pilot exclusion file does not match program manifest")
    recorded_exclusion_hash = require_sha256(
        pilot_exclusion, "pilot_exclusion_hash"
    )
    unhashed_pilot = dict(pilot_exclusion)
    unhashed_pilot.pop("pilot_exclusion_hash")
    if sha256_bytes(canonical_json(unhashed_pilot)) != recorded_exclusion_hash:
        raise ValueError("pilot exclusion semantic hash mismatch")
    if pilot_exclusion.get("seed_count") != len(excluded):
        raise ValueError("pilot exclusion seed count mismatch")
    if seed_set_hash(record.seed for record in excluded) != pilot_exclusion.get(
        "seed_set_hash"
    ):
        raise ValueError("pilot exclusion seed-set hash mismatch")
    mapping: list[dict[str, Any]] = []
    if len(excluded) % 2:
        raise ValueError("pilot exclusion seed registry is not paired")
    for index in range(0, len(excluded), 2):
        first, second = excluded[index : index + 2]
        if (
            first.logical_task_id != second.logical_task_id
            or first.slot != 1
            or second.slot != 2
        ):
            raise ValueError("pilot exclusion task/slot mapping is invalid")
        mapping.append(
            {
                "logical_task_id": first.logical_task_id,
                "seed1": first.seed,
                "seed2": second.seed,
            }
        )
    if sha256_bytes(canonical_json(mapping)) != pilot_exclusion.get(
        "task_seed_mapping_hash"
    ):
        raise ValueError("pilot exclusion task-seed mapping hash mismatch")
    _validate_pilot_exclusion_policy(pilot_exclusion, excluded)

    seed_records = _read_seed_records(directory / "seed_registry.tsv")
    child_index_map: dict[str, list[int]] = {child: [] for child in CHILD_ORDER}
    for record in seed_records:
        if record.child_id not in child_index_map:
            raise ValueError(f"unknown child in seed registry: {record.child_id}")
        child_index_map[record.child_id].append(record.program_task_index)
    plan = ProgramPlan(
        tasks=tasks,
        child_task_indices={
            child: tuple(child_index_map[child]) for child in CHILD_ORDER
        },
        seed_records=seed_records,
    )
    validate_program_plan(plan, allocation_rows, excluded)

    runtime = require_dict(manifest, "runtime")
    _validate_runtime_policy(runtime)
    geant4_version = require_string(runtime, "geant4_version")
    for task in tasks:
        expected_configuration_hash = task_configuration_hash(
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
        if task.configuration_hash != expected_configuration_hash:
            raise ValueError(
                f"production task configuration hash mismatch: {task.logical_task_id}"
            )

    program_seed = require_dict(manifest, "identity").get("program_seed")
    if not isinstance(program_seed, int) or program_seed < 0:
        raise ValueError("production program seed is invalid")
    expected_plan = build_program_plan(
        allocation_rows=allocation_rows,
        program_seed=program_seed,
        excluded_seeds=excluded,
        geant4_version=geant4_version,
    )
    if plan.tasks != expected_plan.tasks:
        raise ValueError("production tasks do not match the canonical rebuilt plan")
    if plan.seed_records != expected_plan.seed_records:
        raise ValueError("production seed registry does not match canonical derivation")
    if plan.child_task_indices != expected_plan.child_task_indices:
        raise ValueError("production child partition does not match canonical allocation")
    used = {record.seed for record in excluded}
    if len(seed_records) != len(tasks):
        raise ValueError("production seed registry length mismatch")
    for task, record in zip(tasks, seed_records):
        if (
            record.program_task_index != task.task_index
            or record.logical_task_id != task.logical_task_id
            or record.seed1 != task.seed1
            or record.seed2 != task.seed2
        ):
            raise ValueError("production seed registry task mapping mismatch")
        seed1, nonce1 = derive_unique_seed(
            program_seed=program_seed,
            logical_task_id=task.logical_task_id,
            slot=1,
            used=used,
        )
        seed2, nonce2 = derive_unique_seed(
            program_seed=program_seed,
            logical_task_id=task.logical_task_id,
            slot=2,
            used=used,
        )
        if (seed1, nonce1, seed2, nonce2) != (
            record.seed1,
            record.seed1_nonce,
            record.seed2,
            record.seed2_nonce,
        ):
            raise ValueError("production seed derivation does not reproduce registry")

    children_record = require_dict(manifest, "children")
    if tuple(children_record) != CHILD_ORDER or tuple(manifest.get("child_order", ())) != CHILD_ORDER:
        raise ValueError("production child order is invalid")
    children_root = directory / "children"
    if not children_root.is_dir():
        raise ValueError("production children directory is missing")
    children_entries = tuple(children_root.iterdir())
    if any(path.is_symlink() for path in children_entries) or {
        path.name for path in children_entries
    } != set(CHILD_ORDER) or any(not path.is_dir() for path in children_entries):
        raise ValueError("production child directory set is invalid")
    loaded_children: dict[str, tuple[CampaignTask, ...]] = {}
    parent_by_index = {task.task_index: task for task in tasks}
    parent_scan_lines: list[str] = []
    for child in CHILD_ORDER:
        child_dir = children_root / child
        if (child_dir / "campaign.json").exists():
            raise ValueError("Phase-1 child plan must not contain campaign.json")
        verify_exact_flat_checksums(child_dir, CHILD_REQUIRED_FILES)
        child_manifest = load_json(child_dir / "child_plan.json")
        if child_manifest.get("schema_version") != CHILD_PLAN_SCHEMA_VERSION:
            raise ValueError(f"invalid child plan schema: {child}")
        if child_manifest.get("submittable") is not False:
            raise ValueError(f"Phase-1 child must be non-submittable: {child}")
        if child_manifest.get("materialization_phase") != "phase-2-pending":
            raise ValueError(f"child materialization phase is invalid: {child}")
        if child_manifest.get("program_id") != program_id or child_manifest.get(
            "program_hash"
        ) != program_hash:
            raise ValueError(f"child program identity mismatch: {child}")
        if child_manifest.get("parent_plan_hash") != ordered_task_hash(tasks):
            raise ValueError(f"child parent-plan hash mismatch: {child}")
        descriptor = child_descriptor(plan, child)
        if child_manifest.get("descriptor") != descriptor:
            raise ValueError(f"child descriptor mismatch: {child}")
        descriptor_hash = child_plan_hash(descriptor)
        if child_manifest.get("child_plan_hash") != descriptor_hash:
            raise ValueError(f"child semantic hash mismatch: {child}")
        parent_record = require_dict(children_record, child)
        if require_sha256(parent_record, "child_plan_hash") != descriptor_hash:
            raise ValueError(f"parent child-plan hash mismatch: {child}")
        if sha256_file(child_dir / "child_plan.json") != require_sha256(
            parent_record, "sha256"
        ):
            raise ValueError(f"parent child-plan file checksum mismatch: {child}")
        if sha256_file(child_dir / "SHA256SUMS") != require_sha256(
            parent_record, "checksum_manifest_sha256"
        ):
            raise ValueError(f"parent child checksum-manifest mismatch: {child}")
        if parent_record.get("path") != f"children/{child}/child_plan.json":
            raise ValueError(f"parent child path mismatch: {child}")
        if sha256_file(child_dir / "task_set.tsv") != require_sha256(
            parent_record, "task_set_sha256"
        ):
            raise ValueError(f"parent child task-set checksum mismatch: {child}")
        if sha256_file(child_dir / "scan_args.txt") != require_sha256(
            parent_record, "scan_args_sha256"
        ):
            raise ValueError(f"parent child scan-args checksum mismatch: {child}")
        child_artifacts = require_dict(child_manifest, "artifacts")
        for key, filename in (
            ("task_set_tsv", "task_set.tsv"),
            ("scan_args", "scan_args.txt"),
        ):
            record = require_dict(child_artifacts, key)
            if require_string(record, "path") != filename or require_sha256(
                record, "sha256"
            ) != sha256_file(child_dir / filename):
                raise ValueError(f"child artifact checksum mismatch: {child}/{filename}")

        with (child_dir / "task_set.tsv").open(
            encoding="utf-8", newline=""
        ) as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            rows = list(reader)
            if tuple(reader.fieldnames or ()) != TASK_SET_FIELDS:
                raise ValueError(f"invalid child task-set header: {child}")
        selected: list[CampaignTask] = []
        for local_index, row in enumerate(rows, 1):
            try:
                child_index = int(row["child_task_index"])
                parent_index = int(row["parent_task_index"])
                logical_id = row["logical_task_id"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid child task-set row: {child}") from exc
            task = parent_by_index.get(parent_index)
            if child_index != local_index or task is None or task.logical_task_id != logical_id:
                raise ValueError(f"child task-set identity mismatch: {child}")
            selected.append(task)
        if tuple(selected) != child_tasks(plan, child):
            raise ValueError(f"child task-set membership/order mismatch: {child}")
        loaded_children[child] = tuple(selected)

        expected_lines = tuple(
            shlex.join(scan_args(task, descriptor["materialized_campaign_id"]))
            for task in selected
        )
        actual_lines = read_scan_args(child_dir / "scan_args.txt")
        if actual_lines != expected_lines:
            raise ValueError(f"child scan args mismatch: {child}")
        parent_scan_lines.extend(actual_lines)
    if read_scan_args(directory / "scan_args.txt") != tuple(parent_scan_lines):
        raise ValueError("parent scan args are not the ordered child concatenation")

    expected_identity = _program_identity(
        plan=plan,
        allocation_rows=allocation_rows,
        program_seed=program_seed,
        pilot_exclusion=pilot_exclusion,
        program_source=require_dict(manifest, "program_source"),
        runtime=require_dict(manifest, "runtime"),
        accepted_statistical_evidence=manifest.get("accepted_statistical_evidence")
        is True,
    )
    if require_dict(manifest, "identity") != expected_identity:
        raise ValueError("production program identity payload mismatch")
    if sha256_bytes(canonical_json(expected_identity)) != program_hash:
        raise ValueError("production program semantic hash mismatch")
    if manifest.get("task_count") != 914 or manifest.get("total_events") != 228_500:
        raise ValueError("production program top-level totals are invalid")
    if manifest.get("checkpoints") != checkpoint_descriptors(plan):
        raise ValueError("production checkpoint descriptors are invalid")
    if manifest.get("evidence_states") != evidence_state_descriptors(plan):
        raise ValueError("production evidence-state graph is invalid")
    if manifest.get("authorization_graph") != authorization_graph():
        raise ValueError("production authorization graph is invalid")
    if manifest.get("authorization_graph_hash") != authorization_graph_hash():
        raise ValueError("production authorization graph hash is invalid")

    accepted = manifest.get("accepted_statistical_evidence") is True
    source = require_dict(manifest, "program_source")
    require_sha1(source, "git_commit")
    require_sha1(source, "git_tree")
    if not isinstance(source.get("dirty"), bool):
        raise ValueError("program_source.dirty must be boolean")
    source_artifacts = require_dict(source, "artifacts")
    allocation_source_record = require_dict(
        source_artifacts,
        "hpc/osc/configurations/steel-module-production-allocation-v1.tsv",
    )
    allocation_artifact = require_dict(
        require_dict(manifest, "artifacts"), "allocation"
    )
    if require_sha256(allocation_source_record, "sha256") != require_sha256(
        allocation_artifact, "source_sha256"
    ):
        raise ValueError("production allocation source identity mismatch")
    runtime_environment = {
        "mode": "osc-production",
        "geant4_version": require_string(runtime, "geant4_version"),
        "image": require_dict(runtime, "image"),
        "g4_data_manifest": require_dict(runtime, "g4_data_manifest"),
        "build_artifact": require_dict(runtime, "executable"),
    }
    actual_runtime_identity = environment_identity(runtime_environment)
    if actual_runtime_identity != require_sha256(runtime, "environment_identity"):
        raise ValueError("production runtime environment identity mismatch")
    if actual_runtime_identity != ACCEPTED_PILOT_ENVIRONMENT_IDENTITY:
        raise ValueError("production runtime is not the accepted pilot environment")
    if require_sha256(
        require_dict(runtime, "executable"), "sha256"
    ) != ACCEPTED_EXECUTABLE_SHA256:
        raise ValueError("production runtime executable is not the accepted binary")
    derived_accepted = _derived_program_acceptance(
        pilot_exclusion=pilot_exclusion,
        program_source=source,
        runtime=runtime,
    )
    if accepted != derived_accepted:
        raise ValueError("production evidence acceptance is not provenance-derived")
    if verify_runtime_artifacts:
        for field, label in (
            ("image", "container image"),
            ("g4_data_manifest", "Geant4 data manifest"),
            ("executable", "frozen executable"),
        ):
            resolve_recorded_artifact(runtime.get(field), label)
        executable = require_dict(runtime, "executable")
        resolve_recorded_artifact(
            executable.get("build_provenance"), "build provenance"
        )

    return ProductionProgramBundle(
        directory=directory,
        manifest=manifest,
        tasks=tasks,
        child_tasks=loaded_children,
        excluded_seeds=excluded,
    )


__all__ = [
    "ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT",
    "ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE",
    "ACCEPTED_EXECUTABLE_SHA256",
    "ACCEPTED_PILOT_CAMPAIGN_JSON_SHA256",
    "ACCEPTED_PILOT_CAMPAIGN_ID",
    "ACCEPTED_PILOT_ENVIRONMENT_IDENTITY",
    "ACCEPTED_PILOT_EVENT_AUDIT_SHA256",
    "ACCEPTED_PILOT_EXCLUSION_HASH",
    "ACCEPTED_PILOT_FINALIZED_SHA256SUMS_SHA256",
    "ACCEPTED_PILOT_PLAN_HASH",
    "ACCEPTED_PILOT_SEED_SET_HASH",
    "ACCEPTED_PILOT_SOURCE_COMMIT",
    "ACCEPTED_PILOT_TASK_INDEX_SHA256",
    "ACCEPTED_PILOT_TASKS_TSV_SHA256",
    "ACCEPTED_PILOT_TASK_SEED_MAPPING_HASH",
    "ACCEPTED_PILOT_VALIDATION_REPORT_SHA256",
    "AllocationRow",
    "CHILD_ORDER",
    "CHECKPOINT_ORDER",
    "EVENTS_PER_TASK",
    "ExcludedSeed",
    "ProgramPlan",
    "ProductionProgramBundle",
    "SeedRecord",
    "accepted_allocation_rows",
    "allocation_policy_hash",
    "authorization_graph",
    "authorization_graph_hash",
    "build_program_plan",
    "child_descriptor",
    "child_plan_hash",
    "child_tasks",
    "derive_unique_seed",
    "evidence_state_descriptors",
    "load_production_program",
    "load_local_test_pilot",
    "load_sealed_pilot",
    "ordered_task_hash",
    "read_allocation",
    "scan_args",
    "seed_pair_registry_hash",
    "seed_set_hash",
    "task_set_hash",
    "validate_program_plan",
    "write_program_atomic",
]
