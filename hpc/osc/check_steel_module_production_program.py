#!/usr/bin/env python3
"""Deterministic Phase-1 checks for the steel-module production program."""

from __future__ import annotations

import csv
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Callable

from steel_module_campaign_lib import (
    canonical_json,
    environment_identity,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_program_lib import (
    ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT,
    ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE,
    ACCEPTED_EXECUTABLE_SHA256,
    ACCEPTED_PILOT_CAMPAIGN_JSON_SHA256,
    ACCEPTED_PILOT_CAMPAIGN_ID,
    ACCEPTED_PILOT_ENVIRONMENT_IDENTITY,
    ACCEPTED_PILOT_EVENT_AUDIT_SHA256,
    ACCEPTED_PILOT_EXCLUSION_HASH,
    ACCEPTED_PILOT_FINALIZED_SHA256SUMS_SHA256,
    ACCEPTED_PILOT_PLAN_HASH,
    ACCEPTED_PILOT_SOURCE_COMMIT,
    ACCEPTED_PILOT_TASK_INDEX_SHA256,
    ACCEPTED_PILOT_TASKS_TSV_SHA256,
    ACCEPTED_PILOT_VALIDATION_REPORT_SHA256,
    CHILD_ORDER,
    ExcludedSeed,
    ProgramPlan,
    authorization_graph,
    authorization_graph_hash,
    build_program_plan,
    derive_unique_seed,
    load_local_test_pilot,
    load_production_program,
    load_sealed_pilot,
    read_allocation,
    write_program_atomic,
)


PILOT_CAMPAIGN_SEED = 20260717
PROGRAM_SEED = 20260718
EXECUTABLE_BUILD_SOURCE_COMMIT = ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT
PILOT_IMAGE_SHA256 = (
    "6a777120a8ad8ae4959580e5c1e9eaa4dc2a837a95cbfca533f2f07d71d54962"
)
PILOT_G4_DATA_SHA256 = (
    "ef94e0915e1fffd0c24923dec161252da608400f5572f088bac8d5ce77acf807"
)
FINALIZED_FIXTURE_FILES = (
    "analysis_config.json",
    "configuration_summary.csv",
    "event_audit.json",
    "task_index.tsv",
    "validation_report.json",
)


def run(
    args: list[str],
    *,
    cwd: Path,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if expect_success and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}"
        )
    if not expect_success and result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}")
    return result


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_flat_checksums(directory: Path, names: tuple[str, ...]) -> None:
    (directory / "SHA256SUMS").write_text(
        "".join(f"{sha256_file(directory / name)}  {name}\n" for name in sorted(names)),
        encoding="utf-8",
    )


def refresh_flat_checksum(directory: Path, name: str) -> None:
    path = directory / "SHA256SUMS"
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    output: list[str] = []
    for line in lines:
        _, recorded_name = line.split("  ", 1)
        if recorded_name == name:
            output.append(f"{sha256_file(directory / name)}  {name}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        raise AssertionError(f"checksum manifest does not record {name}: {path}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def generate_sealed_pilot_fixture(repo_root: Path, pilot_dir: Path) -> None:
    run(
        [
            sys.executable,
            "hpc/osc/generate_steel_module_campaign.py",
            "--out-dir",
            str(pilot_dir),
            "--stage",
            "convergence-pilot",
            "--events",
            "250",
            "--blocks",
            "4",
            "--configurations-tsv",
            "hpc/osc/configurations/steel-module-convergence-pilot-v1.tsv",
            "--campaign-seed",
            str(PILOT_CAMPAIGN_SEED),
            "--geant4-version",
            "11.4.2",
            "--allow-dirty",
        ],
        cwd=repo_root,
    )
    manifest_path = pilot_dir / "campaign.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["campaign_id"] == ACCEPTED_PILOT_CAMPAIGN_ID
    assert manifest["plan_hash"] == ACCEPTED_PILOT_PLAN_HASH
    assert manifest["task_count"] == 120

    environment: dict[str, object] = {
        "mode": "osc-production",
        "geant4_version": "11.4.2",
        "accepted_statistical_evidence": True,
        "image": {
            "path": "/fixture/historical/geant4.sif",
            "sha256": PILOT_IMAGE_SHA256,
        },
        "g4_data_manifest": {
            "path": "/fixture/historical/g4-data-manifest.sha256",
            "sha256": PILOT_G4_DATA_SHA256,
        },
        "build_artifact": {
            "path": "/fixture/historical/OpNovice2",
            "sha256": ACCEPTED_EXECUTABLE_SHA256,
        },
    }
    environment["identity_hash"] = environment_identity(environment)
    assert environment["identity_hash"] == ACCEPTED_PILOT_ENVIRONMENT_IDENTITY
    manifest["git"] = {
        "commit": ACCEPTED_PILOT_SOURCE_COMMIT,
        "branch": "fixture-accepted-pilot",
        "dirty": False,
        "dirty_paths": [],
    }
    manifest["local_test_fixture"] = True
    environment["accepted_statistical_evidence"] = False
    environment["identity_hash"] = environment_identity(environment)
    manifest["environment"] = environment
    write_json(manifest_path, manifest)

    finalized = pilot_dir / "finalized"
    finalized.mkdir()
    shutil.copyfile(pilot_dir / "tasks.tsv", finalized / "task_index.tsv")
    (finalized / "configuration_summary.csv").write_text(
        "fixture\n", encoding="utf-8"
    )
    write_json(finalized / "analysis_config.json", {"fixture": True})
    identity = {
        "campaign_id": ACCEPTED_PILOT_CAMPAIGN_ID,
        "plan_hash": ACCEPTED_PILOT_PLAN_HASH,
        "git_commit": ACCEPTED_PILOT_SOURCE_COMMIT,
        "environment_identity": ACCEPTED_PILOT_ENVIRONMENT_IDENTITY,
        "valid": True,
        "accepted_statistical_evidence": False,
    }
    write_json(
        finalized / "event_audit.json",
        {
            **identity,
            "schema_version": "steel-module-event-audit-v1",
            "task_count": 120,
        },
    )
    write_json(
        finalized / "validation_report.json",
        {
            **identity,
            "schema_version": "steel-module-validation-report-v1",
            "expected_tasks": 120,
            "selected_tasks": 120,
            "event_audit_integrated": True,
        },
    )
    write_flat_checksums(finalized, FINALIZED_FIXTURE_FILES)


def program_command(pilot_dir: Path, out_dir: Path) -> list[str]:
    return [
        sys.executable,
        "hpc/osc/generate_steel_module_production_program.py",
        "--out-dir",
        str(out_dir),
        "--program-seed",
        str(PROGRAM_SEED),
        "--sealed-pilot-dir",
        str(pilot_dir),
        "--executable-build-source-commit",
        EXECUTABLE_BUILD_SOURCE_COMMIT,
        "--environment-mode",
        "local-dev",
        "--allow-dirty",
    ]


def fixture_program_source(repo_root: Path, allocation_source: Path) -> dict[str, object]:
    commit = run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    tree = run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo_root).stdout.strip()
    return {
        "git_commit": commit,
        "git_tree": tree,
        "branch": "local-test-fixture",
        "dirty": True,
        "dirty_paths": ["<synthetic-production-program-fixture>"],
        "artifacts": {
            str(allocation_source.relative_to(repo_root)): {
                "sha256": sha256_file(allocation_source)
            }
        },
    }


def fixture_runtime(*, accepted_paths: bool = False) -> dict[str, object]:
    prefix = "/fixture/accepted" if accepted_paths else "historical-pilot-hash-only"
    mode = "osc-production" if accepted_paths else "local-dev"
    return {
        "mode": mode,
        "geant4_version": "11.4.2",
        "environment_identity": ACCEPTED_PILOT_ENVIRONMENT_IDENTITY,
        "artifacts_verified": accepted_paths,
        "image": {
            "path": f"{prefix}/geant4.sif",
            "sha256": PILOT_IMAGE_SHA256,
        },
        "g4_data_manifest": {
            "path": f"{prefix}/g4-data-manifest.sha256",
            "sha256": PILOT_G4_DATA_SHA256,
        },
        "executable": {
            "path": f"{prefix}/OpNovice2",
            "sha256": ACCEPTED_EXECUTABLE_SHA256,
            "build_source_commit": ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT,
            "build_source_tree": ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE,
            "build_provenance": {
                "path": f"{prefix}/build-environment.txt",
                "sha256": sha256_bytes(
                    canonical_json(
                        {
                            "fixture": True,
                            "build_source_commit": ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT,
                            "build_source_tree": ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE,
                            "executable_sha256": ACCEPTED_EXECUTABLE_SHA256,
                        }
                    )
                ),
            },
        },
    }


def generate_local_fixture_program(
    *,
    repo_root: Path,
    pilot_dir: Path,
    out_dir: Path,
) -> object:
    allocation_source = (
        repo_root
        / "hpc/osc/configurations/steel-module-production-allocation-v1.tsv"
    )
    allocation_rows = read_allocation(allocation_source)
    pilot_exclusion, excluded_seeds = load_local_test_pilot(pilot_dir)
    runtime = fixture_runtime()
    plan = build_program_plan(
        allocation_rows=allocation_rows,
        program_seed=PROGRAM_SEED,
        excluded_seeds=excluded_seeds,
        geant4_version="11.4.2",
    )
    return write_program_atomic(
        out_dir,
        plan=plan,
        allocation_source=allocation_source,
        allocation_rows=allocation_rows,
        program_seed=PROGRAM_SEED,
        pilot_exclusion=pilot_exclusion,
        excluded_seeds=excluded_seeds,
        program_source=fixture_program_source(repo_root, allocation_source),
        runtime=runtime,
        created_at_utc="2026-07-17T00:00:00+00:00",
        description="synthetic local test-fixture production program",
    )


def configuration_blocks(
    tasks: tuple[object, ...],
) -> dict[tuple[str, int], set[int]]:
    result: dict[tuple[str, int], set[int]] = defaultdict(set)
    for task in tasks:
        result[(task.sipm_layout, task.tile_thickness_mm)].add(task.seed_block)
    return dict(result)


def assert_program_shape(bundle: object) -> None:
    assert bundle.manifest["submittable"] is False
    assert bundle.manifest["accepted_statistical_evidence"] is False
    assert bundle.manifest["task_count"] == 914
    assert bundle.manifest["total_events"] == 228_500
    assert bundle.manifest["authorization_graph"] == authorization_graph()
    assert bundle.manifest["authorization_graph_hash"] == authorization_graph_hash()
    assert len(bundle.tasks) == 914
    assert sum(task.events for task in bundle.tasks) == 228_500
    assert all(
        task.stage == "production"
        and task.events == 250
        and task.absorber_transverse_mm == 500
        and task.x_mm == 0
        and task.y_mm == 0
        for task in bundle.tasks
    )

    expected_counts = {
        "FIXED": 592,
        "BC-S1": 32,
        "BC-S2": 48,
        "BC-S3": 80,
        "BC-S4": 162,
    }
    expected_events = {
        "FIXED": 148_000,
        "BC-S1": 8_000,
        "BC-S2": 12_000,
        "BC-S3": 20_000,
        "BC-S4": 40_500,
    }
    assert tuple(bundle.child_tasks) == CHILD_ORDER
    assert {
        child: len(bundle.child_tasks[child]) for child in CHILD_ORDER
    } == expected_counts
    assert {
        child: sum(task.events for task in bundle.child_tasks[child])
        for child in CHILD_ORDER
    } == expected_events

    fixed_expected = {
        **{("back-center", thickness): set(range(16)) for thickness in (8, 12, 16, 20)},
        **{("edge-center", thickness): set(range(40)) for thickness in (4, 8, 12, 16, 20, 24)},
        **{("back-four", thickness): set(range(48)) for thickness in (4, 8, 12, 16, 20, 24)},
    }
    assert configuration_blocks(bundle.child_tasks["FIXED"]) == fixed_expected
    for child, start, stop in (
        ("BC-S1", 0, 16),
        ("BC-S2", 16, 40),
        ("BC-S3", 40, 80),
        ("BC-S4", 80, 161),
    ):
        assert configuration_blocks(bundle.child_tasks[child]) == {
            ("back-center", 4): set(range(start, stop)),
            ("back-center", 24): set(range(start, stop)),
        }

    parent_ids = [task.logical_task_id for task in bundle.tasks]
    child_ids = [
        task.logical_task_id
        for child in CHILD_ORDER
        for task in bundle.child_tasks[child]
    ]
    assert len(set(parent_ids)) == 914
    assert len(set(child_ids)) == 914
    assert set(child_ids) == set(parent_ids)

    checkpoints = bundle.manifest["checkpoints"]
    assert [
        (
            row["checkpoint_id"],
            row["included_children"],
            row["task_count"],
            row["event_count"],
        )
        for row in checkpoints
    ] == [
        ("BC-S1", ["FIXED", "BC-S1"], 624, 156_000),
        ("BC-S2", ["FIXED", "BC-S1", "BC-S2"], 672, 168_000),
        (
            "BC-S3",
            ["FIXED", "BC-S1", "BC-S2", "BC-S3"],
            752,
            188_000,
        ),
        (
            "BC-S4",
            ["FIXED", "BC-S1", "BC-S2", "BC-S3", "BC-S4"],
            914,
            228_500,
        ),
    ]
    assert [
        (
            row["state_id"],
            row["evidence_mode"],
            row["included_children"],
            row["task_count"],
            row["event_count"],
        )
        for row in bundle.manifest["evidence_states"]
    ] == [
        ("BC-ONLY-S1", "back-center-only", ["BC-S1"], 32, 8_000),
        (
            "BC-S1",
            "four-primary-contrast",
            ["FIXED", "BC-S1"],
            624,
            156_000,
        ),
        (
            "BC-ONLY-S2",
            "back-center-only",
            ["BC-S1", "BC-S2"],
            80,
            20_000,
        ),
        (
            "BC-S2",
            "four-primary-contrast",
            ["FIXED", "BC-S1", "BC-S2"],
            672,
            168_000,
        ),
        (
            "BC-ONLY-S3",
            "back-center-only",
            ["BC-S1", "BC-S2", "BC-S3"],
            160,
            40_000,
        ),
        (
            "BC-S3",
            "four-primary-contrast",
            ["FIXED", "BC-S1", "BC-S2", "BC-S3"],
            752,
            188_000,
        ),
        (
            "BC-ONLY-S4",
            "back-center-only",
            ["BC-S1", "BC-S2", "BC-S3", "BC-S4"],
            322,
            80_500,
        ),
        (
            "BC-S4",
            "four-primary-contrast",
            ["FIXED", "BC-S1", "BC-S2", "BC-S3", "BC-S4"],
            914,
            228_500,
        ),
    ]

    production_seeds = [
        seed for task in bundle.tasks for seed in (task.seed1, task.seed2)
    ]
    excluded_seeds = [record.seed for record in bundle.excluded_seeds]
    assert len(production_seeds) == 1_828
    assert len(set(production_seeds)) == 1_828
    assert len(excluded_seeds) == 240
    assert len(set(excluded_seeds)) == 240
    assert set(production_seeds).isdisjoint(excluded_seeds)
    assert all(0 < seed < 2_147_483_647 for seed in production_seeds)

    graph = bundle.manifest["authorization_graph"]
    assert graph["semantics"] == {
        "default_unlisted_child": "denied",
        "eligibility_is_authorization": False,
        "analyzer_can_authorize": False,
        "recorder_can_submit": False,
        "authorization_record_scope": "latest-for-unsubmitted-child",
        "started_attempts_are_not_retroactively_revoked": True,
    }
    assert graph["eligibility_policy"]["thresholds"] == {
        "relative_ci_half_width": 0.10,
        "loo_success": 0.10,
        "loo_continue_ceiling": 0.20,
    }
    assert graph["eligibility_policy"]["precedence"] == [
        "invalid-evidence-no-record",
        "material-adverse-tail-pause-only",
        "s4-success-stop-or-pause",
        "s4-otherwise-pause-only",
        "non-ceiling-success-stop-or-pause",
        "non-ceiling-narrowing-continue-or-pause",
        "fallback-pause-only",
    ]
    assert graph["eligibility_policy"]["ignored_metrics"] == [
        "ratio_direction",
        "unity_crossing",
        "unity_exclusion",
    ]
    assert graph["eligibility_policy"]["human_tail_override"] == {
        "tail_materially_worsened": True,
        "allowed_decisions": ["pause-review"],
    }
    assert graph["initial"] == {
        "directly_authorized_children": ["BC-S1"],
        "scheduling_eligible_children": [],
        "default_denied_children": ["FIXED", "BC-S2", "BC-S3", "BC-S4"],
    }
    gates = graph["review_gates"]
    assert [gate["gate_id"] for gate in gates] == ["BC-S1", "BC-S2", "BC-S3", "BC-S4"]
    assert [gate["source_evidence_state_ids"] for gate in gates] == [
        ["BC-ONLY-S1"],
        ["BC-ONLY-S2", "BC-S2"],
        ["BC-ONLY-S3", "BC-S3"],
        ["BC-ONLY-S4", "BC-S4"],
    ]
    assert gates[0]["required_prior_decision"] is None
    assert gates[0]["required_scheduling_authorization_for"] is None
    for ordinal, gate in enumerate(gates, 1):
        transitions = {
            transition["selected_decision"]: transition
            for transition in gate["transitions"]
        }
        decisions = set(transitions)
        assert "pause-review" in decisions and "stop-success" in decisions
        pause = transitions["pause-review"]
        assert pause["directly_authorized_children"] == []
        assert pause["scheduling_eligible_children"] == []
        assert pause["next_gate"] is None
        assert pause["terminal_kind"] == "method-review"
        stop = transitions["stop-success"]
        assert stop["directly_authorized_children"] == ["FIXED"]
        assert stop["scheduling_eligible_children"] == []
        assert stop["next_gate"] is None
        assert stop["terminal_kind"] == "back-center-complete"
        if ordinal < 4:
            assert "continue" in decisions
            continuation = transitions["continue"]
            assert continuation["scheduling_eligible_children"] == [f"BC-S{ordinal + 1}"]
            assert continuation["directly_authorized_children"] == ["FIXED"]
            assert continuation["scheduling_record_required"] is True
        else:
            assert "continue" not in decisions
        if ordinal > 1:
            assert gate["required_prior_decision"] == {
                "gate_id": f"BC-S{ordinal - 1}",
                "selected_decision": "continue",
                "previous_decision_hash_required": True,
            }
            assert gate["required_scheduling_authorization_for"] == f"BC-S{ordinal}"


def rejected_tamper(
    source: Path,
    scratch: Path,
    name: str,
    mutate: Callable[[Path], None],
) -> None:
    target = scratch / f"tampered-{name}"
    shutil.copytree(source, target)
    mutate(target)
    try:
        load_production_program(target, verify_runtime_artifacts=False)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return
    raise AssertionError(f"tampered production program was accepted: {name}")


def bind_root_artifact(root: Path, key: str, filename: str) -> None:
    refresh_flat_checksum(root, filename)
    manifest_path = root / "production_program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][key]["sha256"] = sha256_file(root / filename)
    write_json(manifest_path, manifest)
    refresh_flat_checksum(root, "production_program.json")


def tamper_child_task_set(root: Path) -> None:
    child_dir = root / "children/FIXED"
    task_set = child_dir / "task_set.tsv"
    with task_set.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    rows[0]["logical_task_id"] = rows[1]["logical_task_id"]
    with task_set.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    refresh_flat_checksum(child_dir, "task_set.tsv")

    manifest_path = root / "production_program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    child = manifest["children"]["FIXED"]
    child["task_set_sha256"] = sha256_file(task_set)
    child["checksum_manifest_sha256"] = sha256_file(child_dir / "SHA256SUMS")
    write_json(manifest_path, manifest)
    refresh_flat_checksum(root, "production_program.json")


def tamper_allocation(root: Path) -> None:
    path = root / "allocation.tsv"
    lines = path.read_text(encoding="utf-8").splitlines()
    fields = lines[1].split("\t")
    fields[3] = "300"
    lines[1] = "\t".join(fields)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bind_root_artifact(root, "allocation", "allocation.tsv")


def tamper_seed_registry(root: Path) -> None:
    path = root / "seed_registry.tsv"
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    rows[0]["seed1"] = rows[1]["seed1"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    bind_root_artifact(root, "seed_registry", "seed_registry.tsv")


def tamper_pilot_exclusion(root: Path) -> None:
    path = root / "pilot_exclusion.json"
    exclusion = json.loads(path.read_text(encoding="utf-8"))
    exclusion["campaign_id"] = "tampered-pilot"
    write_json(path, exclusion)
    refresh_flat_checksum(root, "pilot_exclusion.json")

    manifest_path = root / "production_program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pilot_exclusion"] = exclusion
    manifest["artifacts"]["pilot_exclusion"]["sha256"] = sha256_file(path)
    write_json(manifest_path, manifest)
    refresh_flat_checksum(root, "production_program.json")


def tamper_program_identity(root: Path) -> None:
    path = root / "production_program.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["identity"]["policy"]["policy_id"] = "tampered-policy"
    write_json(path, manifest)
    refresh_flat_checksum(root, "production_program.json")


def tamper_authorization_graph(root: Path) -> None:
    path = root / "production_program.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["authorization_graph"]["initial"]["directly_authorized_children"] = [
        "FIXED"
    ]
    write_json(path, manifest)
    refresh_flat_checksum(root, "production_program.json")


def assert_tamper_rejection(program_dir: Path, scratch: Path) -> None:
    rejected_tamper(program_dir, scratch, "child-task-set", tamper_child_task_set)
    rejected_tamper(program_dir, scratch, "allocation", tamper_allocation)
    rejected_tamper(program_dir, scratch, "seed-registry", tamper_seed_registry)
    rejected_tamper(
        program_dir, scratch, "pilot-exclusion", tamper_pilot_exclusion
    )
    rejected_tamper(
        program_dir, scratch, "program-identity", tamper_program_identity
    )
    rejected_tamper(
        program_dir, scratch, "authorization-graph", tamper_authorization_graph
    )

    def checksum_only(root: Path) -> None:
        with (root / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("tampered\n")

    rejected_tamper(program_dir, scratch, "flat-checksum", checksum_only)

    rejected_tamper(
        program_dir,
        scratch,
        "extra-root-file",
        lambda root: (root / "UNRECORDED").write_text("x\n", encoding="utf-8"),
    )
    rejected_tamper(
        program_dir,
        scratch,
        "extra-root-directory",
        lambda root: (root / "unrecorded-directory").mkdir(),
    )
    rejected_tamper(
        program_dir,
        scratch,
        "extra-children-file",
        lambda root: (root / "children/UNRECORDED").write_text(
            "x\n", encoding="utf-8"
        ),
    )
    rejected_tamper(
        program_dir,
        scratch,
        "extra-child-directory",
        lambda root: (root / "children/FIXED/unrecorded-directory").mkdir(),
    )


def assert_atomic_failure_cleanup(
    *,
    repo_root: Path,
    scratch: Path,
    pilot_dir: Path,
    valid_bundle: object,
) -> None:
    allocation_source = (
        repo_root
        / "hpc/osc/configurations/steel-module-production-allocation-v1.tsv"
    )
    allocation_rows = read_allocation(allocation_source)
    pilot_exclusion, excluded_seeds = load_local_test_pilot(pilot_dir)
    plan = build_program_plan(
        allocation_rows=allocation_rows,
        program_seed=PROGRAM_SEED,
        excluded_seeds=excluded_seeds,
        geant4_version="11.4.2",
    )
    fault_target = scratch / "fault-injected-program"
    try:
        write_program_atomic(
            fault_target,
            plan=plan,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=PROGRAM_SEED,
            pilot_exclusion=pilot_exclusion,
            excluded_seeds=excluded_seeds,
            program_source=valid_bundle.manifest["program_source"],
            runtime=valid_bundle.manifest["runtime"],
            created_at_utc="2026-07-17T00:00:00+00:00",
            description="fault-injection fixture",
            fault_after_child="BC-S2",
        )
    except RuntimeError as exc:
        assert "injected write failure after BC-S2" in str(exc)
    else:
        raise AssertionError("fault injection unexpectedly completed")
    assert not fault_target.exists()
    assert not list(scratch.glob(f".{fault_target.name}.tmp-*"))

    collision_used: set[int] = set()
    first_seed, first_nonce = derive_unique_seed(
        program_seed=PROGRAM_SEED,
        logical_task_id="collision-fixture",
        slot=1,
        used=collision_used,
    )
    assert first_nonce == 0
    second_seed, second_nonce = derive_unique_seed(
        program_seed=PROGRAM_SEED,
        logical_task_id="collision-fixture",
        slot=1,
        used={first_seed},
    )
    assert second_nonce == 1 and second_seed != first_seed

    duplicate_exclusion = (
        ExcludedSeed("fixture-a", 1, 123),
        ExcludedSeed("fixture-b", 2, 123),
    )
    try:
        build_program_plan(
            allocation_rows=allocation_rows,
            program_seed=PROGRAM_SEED,
            excluded_seeds=duplicate_exclusion,
            geant4_version="11.4.2",
        )
    except ValueError as exc:
        assert "duplicate seeds" in str(exc)
    else:
        raise AssertionError("duplicate pilot exclusion seed was accepted")


def accepted_pilot_payload_from_fixture(payload: dict[str, object]) -> dict[str, object]:
    accepted = copy.deepcopy(payload)
    accepted.update(
        {
            "accepted_statistical_evidence": True,
            "local_test_fixture": False,
            "accepted_snapshot_verified": True,
            "campaign_json_sha256": ACCEPTED_PILOT_CAMPAIGN_JSON_SHA256,
            "tasks_tsv_sha256": ACCEPTED_PILOT_TASKS_TSV_SHA256,
            "finalized_sha256s_sha256": ACCEPTED_PILOT_FINALIZED_SHA256SUMS_SHA256,
            "finalized_task_index_sha256": ACCEPTED_PILOT_TASK_INDEX_SHA256,
            "event_audit_sha256": ACCEPTED_PILOT_EVENT_AUDIT_SHA256,
            "validation_report_sha256": ACCEPTED_PILOT_VALIDATION_REPORT_SHA256,
        }
    )
    accepted.pop("pilot_exclusion_hash", None)
    accepted["pilot_exclusion_hash"] = sha256_bytes(canonical_json(accepted))
    assert accepted["pilot_exclusion_hash"] == ACCEPTED_PILOT_EXCLUSION_HASH
    return accepted


def assert_canonical_and_provenance_gates(
    *,
    repo_root: Path,
    scratch: Path,
    pilot_dir: Path,
    valid_bundle: object,
) -> None:
    allocation_source = (
        repo_root
        / "hpc/osc/configurations/steel-module-production-allocation-v1.tsv"
    )
    allocation_rows = read_allocation(allocation_source)
    pilot_exclusion, excluded = load_local_test_pilot(pilot_dir)
    canonical = build_program_plan(
        allocation_rows=allocation_rows,
        program_seed=PROGRAM_SEED,
        excluded_seeds=excluded,
        geant4_version="11.4.2",
    )
    source = valid_bundle.manifest["program_source"]
    runtime = valid_bundle.manifest["runtime"]

    reordered_tasks = list(canonical.tasks)
    reordered_tasks[0], reordered_tasks[1] = (
        replace(reordered_tasks[1], task_index=1),
        replace(reordered_tasks[0], task_index=2),
    )
    reordered_records = list(canonical.seed_records)
    reordered_records[0], reordered_records[1] = (
        replace(reordered_records[1], program_task_index=1),
        replace(reordered_records[0], program_task_index=2),
    )
    noncanonical = ProgramPlan(
        tasks=tuple(reordered_tasks),
        child_task_indices=canonical.child_task_indices,
        seed_records=tuple(reordered_records),
    )
    noncanonical_target = scratch / "noncanonical-program"
    try:
        write_program_atomic(
            noncanonical_target,
            plan=noncanonical,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=PROGRAM_SEED,
            pilot_exclusion=pilot_exclusion,
            excluded_seeds=excluded,
            program_source=source,
            runtime=runtime,
            created_at_utc="2026-07-17T00:00:00+00:00",
            description="noncanonical fixture",
        )
    except ValueError as exc:
        assert "canonical" in str(exc)
    else:
        raise AssertionError("self-consistent noncanonical task order was accepted")
    assert not noncanonical_target.exists()

    wrong_runtime = copy.deepcopy(runtime)
    wrong_runtime["executable"]["build_source_commit"] = "0" * 40
    wrong_build_target = scratch / "wrong-build-source"
    try:
        write_program_atomic(
            wrong_build_target,
            plan=canonical,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=PROGRAM_SEED,
            pilot_exclusion=pilot_exclusion,
            excluded_seeds=excluded,
            program_source=source,
            runtime=wrong_runtime,
            created_at_utc="2026-07-17T00:00:00+00:00",
            description="wrong build-source fixture",
        )
    except ValueError as exc:
        assert "build-source commit" in str(exc)
    else:
        raise AssertionError("wrong executable build-source commit was accepted")
    assert not wrong_build_target.exists()

    accepted_pilot = accepted_pilot_payload_from_fixture(pilot_exclusion)
    clean_source = copy.deepcopy(source)
    clean_source["dirty"] = False
    clean_source["dirty_paths"] = []
    forged_target = scratch / "forged-accepted-runtime"
    try:
        write_program_atomic(
            forged_target,
            plan=canonical,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=PROGRAM_SEED,
            pilot_exclusion=accepted_pilot,
            excluded_seeds=excluded,
            program_source=clean_source,
            runtime=fixture_runtime(accepted_paths=True),
            created_at_utc="2026-07-17T00:00:00+00:00",
            description="forged accepted-runtime fixture",
        )
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("accepted writer trusted unverified runtime paths")
    assert not forged_target.exists()
    assert not list(scratch.glob(f".{forged_target.name}.tmp-*"))

    changed_excluded = list(excluded)
    replacement = max(record.seed for record in excluded) + 1
    assert replacement < 2_147_483_647
    changed_excluded[0] = replace(changed_excluded[0], seed=replacement)
    changed_tuple = tuple(changed_excluded)
    changed_mapping = []
    for index in range(0, len(changed_tuple), 2):
        first, second = changed_tuple[index : index + 2]
        changed_mapping.append(
            {
                "logical_task_id": first.logical_task_id,
                "seed1": first.seed,
                "seed2": second.seed,
            }
        )
    changed_pilot = copy.deepcopy(accepted_pilot)
    changed_pilot["seed_set_hash"] = sha256_bytes(
        canonical_json(sorted(record.seed for record in changed_tuple))
    )
    changed_pilot["task_seed_mapping_hash"] = sha256_bytes(
        canonical_json(changed_mapping)
    )
    changed_pilot.pop("pilot_exclusion_hash", None)
    changed_pilot["pilot_exclusion_hash"] = sha256_bytes(
        canonical_json(changed_pilot)
    )
    changed_target = scratch / "changed-pilot-seed-registry"
    try:
        write_program_atomic(
            changed_target,
            plan=canonical,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=PROGRAM_SEED,
            pilot_exclusion=changed_pilot,
            excluded_seeds=changed_tuple,
            program_source=clean_source,
            runtime=fixture_runtime(accepted_paths=True),
            created_at_utc="2026-07-17T00:00:00+00:00",
            description="changed pilot seed-registry fixture",
        )
    except ValueError as exc:
        assert "seed-registry identity" in str(exc)
    else:
        raise AssertionError("self-consistent changed pilot seed registry was accepted")
    assert not changed_target.exists()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="steel-module-production-program-check-"
    ) as raw:
        scratch = Path(raw)
        pilot_dir = scratch / "sealed-pilot"
        generate_sealed_pilot_fixture(repo_root, pilot_dir)
        pilot_exclusion, excluded = load_local_test_pilot(pilot_dir)
        assert pilot_exclusion["campaign_id"] == ACCEPTED_PILOT_CAMPAIGN_ID
        assert pilot_exclusion["accepted_statistical_evidence"] is False
        assert pilot_exclusion["local_test_fixture"] is True
        assert len(excluded) == 240
        try:
            load_sealed_pilot(pilot_dir)
        except ValueError:
            pass
        else:
            raise AssertionError("formal sealed-pilot loader accepted a local fixture")

        formal_target = scratch / "formal-cli-fixture-output"
        formal = run(
            program_command(pilot_dir, formal_target),
            cwd=repo_root,
            expect_success=False,
        )
        assert "Cannot generate steel-module production program" in formal.stdout
        assert not formal_target.exists()

        program_dir = scratch / "program"
        generate_local_fixture_program(
            repo_root=repo_root,
            pilot_dir=pilot_dir,
            out_dir=program_dir,
        )
        bundle = load_production_program(
            program_dir, verify_runtime_artifacts=False
        )
        assert_program_shape(bundle)
        assert not list(program_dir.rglob("campaign.json"))

        second_dir = scratch / "program-determinism"
        generate_local_fixture_program(
            repo_root=repo_root,
            pilot_dir=pilot_dir,
            out_dir=second_dir,
        )
        second = load_production_program(second_dir, verify_runtime_artifacts=False)
        assert second.manifest["program_hash"] == bundle.manifest["program_hash"]
        assert second.manifest["program_id"] == bundle.manifest["program_id"]
        assert second.tasks == bundle.tasks
        assert second.child_tasks == bundle.child_tasks
        assert (second_dir / "seed_registry.tsv").read_bytes() == (
            program_dir / "seed_registry.tsv"
        ).read_bytes()

        for candidate in (
            program_dir,
            *(program_dir / "children" / child for child in CHILD_ORDER),
        ):
            rejected = run(
                [
                    sys.executable,
                    "hpc/osc/submit_steel_module_campaign.py",
                    "--campaign-dir",
                    str(candidate),
                    "--check-only",
                ],
                cwd=repo_root,
                expect_success=False,
            )
            assert (
                "Cannot load campaign" in rejected.stdout
                or "managed/checksum-bound steel-module objects" in rejected.stdout
            )

        manifest_sha = sha256_file(program_dir / "production_program.json")
        try:
            generate_local_fixture_program(
                repo_root=repo_root,
                pilot_dir=pilot_dir,
                out_dir=program_dir,
            )
        except ValueError as exc:
            assert "refusing to overwrite" in str(exc)
        else:
            raise AssertionError("production program overwrite was accepted")
        assert sha256_file(program_dir / "production_program.json") == manifest_sha
        load_production_program(program_dir, verify_runtime_artifacts=False)

        invalid_pilot = scratch / "invalid-pilot"
        shutil.copytree(pilot_dir, invalid_pilot)
        invalid_manifest_path = invalid_pilot / "campaign.json"
        invalid_manifest = json.loads(
            invalid_manifest_path.read_text(encoding="utf-8")
        )
        invalid_manifest["campaign_id"] = "sm-v1-convergence-pilot-invalid"
        write_json(invalid_manifest_path, invalid_manifest)
        try:
            load_local_test_pilot(invalid_pilot)
        except ValueError as exc:
            assert "campaign_id is not the accepted pilot" in str(exc)
        else:
            raise AssertionError("invalid local pilot fixture was accepted")

        assert_atomic_failure_cleanup(
            repo_root=repo_root,
            scratch=scratch,
            pilot_dir=pilot_dir,
            valid_bundle=bundle,
        )
        assert_canonical_and_provenance_gates(
            repo_root=repo_root,
            scratch=scratch,
            pilot_dir=pilot_dir,
            valid_bundle=bundle,
        )
        assert_tamper_rejection(program_dir, scratch)

    print(
        "steel-module production program: PASS "
        "(914-task plan, five children, pilot-disjoint seeds, atomic/tamper gates)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
