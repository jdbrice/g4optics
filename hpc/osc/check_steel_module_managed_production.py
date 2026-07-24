#!/usr/bin/env python3
"""Deterministic, no-Slurm checks for Phase-2A managed BC-S1 materialization."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from check_steel_module_production_program import (
    generate_local_fixture_program,
    generate_sealed_pilot_fixture,
)
from steel_module_campaign_lib import (
    canonical_json,
    environment_identity,
    sha256_bytes,
    sha256_file,
)
from steel_module_managed_production_lib import (
    _validate_formal_child_location,
    _validate_recorded_control_plane,
    load_managed_production_child,
    write_managed_child_atomic,
)
from steel_module_production_program_lib import load_production_program


EXPECTED_ROOT_ENTRIES = {
    "README.md",
    "SHA256SUMS",
    "managed_child.json",
    "program_binding.json",
    "scan_args.txt",
    "tasks.tsv",
}
FORBIDDEN_SUBMISSION_ENTRIES = {
    ".submission.lock",
    "attempts",
    "submission-attempts.tsv",
    "task-results",
}


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


def read_tasks(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def tree_file_hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def replace_checksum(directory: Path, name: str) -> None:
    manifest = directory / "SHA256SUMS"
    output: list[str] = []
    replaced = False
    for line in manifest.read_text(encoding="utf-8").splitlines():
        _, recorded = line.split("  ", 1)
        if recorded == name:
            output.append(f"{sha256_file(directory / name)}  {name}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        raise AssertionError(f"checksum does not record {name}")
    manifest.write_text("\n".join(output) + "\n", encoding="utf-8")


def make_fixture(repo_root: Path, scratch: Path) -> tuple[Path, Path]:
    pilot_dir = scratch / "sealed-pilot"
    program_dir = scratch / "program"
    child_dir = scratch / "managed-bc-s1"
    generate_sealed_pilot_fixture(repo_root, pilot_dir)
    generate_local_fixture_program(
        repo_root=repo_root,
        pilot_dir=pilot_dir,
        out_dir=program_dir,
    )
    program = load_production_program(
        program_dir, verify_runtime_artifacts=False
    )
    parent_before = tree_file_hashes(program_dir)
    managed = write_managed_child_atomic(
        child_dir,
        program=program,
        repo_root=repo_root,
        test_mode=True,
    )
    assert managed.directory == child_dir.resolve()
    assert tree_file_hashes(program_dir) == parent_before
    return program_dir, child_dir


def assert_exact_bc_s1(program_dir: Path, child_dir: Path) -> None:
    program = load_production_program(
        program_dir, verify_runtime_artifacts=False
    )
    managed = load_managed_production_child(
        child_dir,
        verify_runtime_artifacts=False,
        verify_control_plane=False,
        allow_test_mode=True,
    )
    assert managed.child_id == "BC-S1"
    assert managed.binding["test_mode"] is True
    assert managed.binding["accepted_statistical_evidence"] is False
    assert managed.plan.environment["accepted_statistical_evidence"] is False
    assert managed.plan.manifest["program_management"]["submittable"] is False
    assert managed.binding["phase_boundary"] == {
        "phase": "Phase-2A",
        "submittable": False,
        "managed_submit_mode": "check-only",
        "actual_slurm_submission": "locked-until-downstream-closure",
    }

    tasks = managed.plan.tasks
    parent = program.child_tasks["BC-S1"]
    assert len(tasks) == len(parent) == 32
    assert sum(task.events for task in tasks) == 8_000
    assert {task.tile_thickness_mm for task in tasks} == {4, 24}
    assert {task.sipm_layout for task in tasks} == {"back-center"}
    assert {task.absorber_transverse_mm for task in tasks} == {500}
    assert {task.events for task in tasks} == {250}
    for thickness in (4, 24):
        assert {
            task.seed_block
            for task in tasks
            if task.tile_thickness_mm == thickness
        } == set(range(16))
    for local_index, (parent_task, task) in enumerate(
        zip(parent, tasks), start=1
    ):
        assert task.task_index == local_index
        assert replace(task, task_index=parent_task.task_index) == parent_task

    assert {path.name for path in child_dir.iterdir()} == EXPECTED_ROOT_ENTRIES
    assert not (child_dir / "campaign.json").exists()
    assert not ({path.name for path in child_dir.iterdir()} & FORBIDDEN_SUBMISSION_ENTRIES)
    assert not list(child_dir.rglob("slurm-*"))
    assert not list(child_dir.rglob("source-identity.json"))

    rows = read_tasks(child_dir / "tasks.tsv")
    assert len(rows) == 32
    assert [int(row["task_index"]) for row in rows] == list(range(1, 33))

    try:
        load_managed_production_child(
            child_dir,
            verify_runtime_artifacts=False,
            verify_control_plane=False,
        )
    except ValueError as exc:
        assert "refuses test-mode evidence" in str(exc)
    else:
        raise AssertionError("formal loader accepted test-mode managed evidence")


def assert_nonoverwrite_and_atomicity(
    repo_root: Path, program_dir: Path, child_dir: Path, scratch: Path
) -> None:
    program = load_production_program(
        program_dir, verify_runtime_artifacts=False
    )
    plan_sha = sha256_file(child_dir / "managed_child.json")
    try:
        write_managed_child_atomic(
            child_dir,
            program=program,
            repo_root=repo_root,
            test_mode=True,
        )
    except ValueError as exc:
        assert "refusing to overwrite" in str(exc)
    else:
        raise AssertionError("managed child overwrite was accepted")
    assert sha256_file(child_dir / "managed_child.json") == plan_sha

    inside_program = program_dir / "forbidden-managed-child"
    parent_before = tree_file_hashes(program_dir)
    try:
        write_managed_child_atomic(
            inside_program,
            program=program,
            repo_root=repo_root,
            test_mode=True,
        )
    except ValueError as exc:
        assert "inside the sealed program" in str(exc)
    else:
        raise AssertionError("managed child target inside sealed program was accepted")
    assert not inside_program.exists()
    assert tree_file_hashes(program_dir) == parent_before

    empty_target = scratch / "existing-empty-target"
    empty_target.mkdir()
    try:
        write_managed_child_atomic(
            empty_target,
            program=program,
            repo_root=repo_root,
            test_mode=True,
        )
    except ValueError as exc:
        assert "refusing to overwrite" in str(exc)
    else:
        raise AssertionError("existing empty managed-child target was accepted")
    assert not list(empty_target.iterdir())

    symlink_target = scratch / "symlink-target"
    symlink_target.symlink_to(child_dir, target_is_directory=True)
    try:
        write_managed_child_atomic(
            symlink_target,
            program=program,
            repo_root=repo_root,
            test_mode=True,
        )
    except ValueError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("managed-child symlink target was accepted")

    for point in ("tasks", "binding", "checksums"):
        target = scratch / f"fault-{point}"
        try:
            write_managed_child_atomic(
                target,
                program=program,
                repo_root=repo_root,
                test_mode=True,
                fault_after=point,
            )
        except RuntimeError as exc:
            assert "injected managed-child failure" in str(exc)
        else:
            raise AssertionError(f"managed fault injection {point} succeeded")
        assert not target.exists()
        assert not (target.parent / f".{target.name}.materialize.lock").exists()
        assert not list(target.parent.glob(f".{target.name}.tmp-*"))


def assert_tamper_rejection(child_dir: Path, scratch: Path) -> None:
    cases = scratch / "tamper"
    cases.mkdir()

    tasks_case = cases / "tasks"
    shutil.copytree(child_dir, tasks_case)
    with (tasks_case / "tasks.tsv").open("a", encoding="utf-8") as stream:
        stream.write("tampered\n")
    tasks_plan_path = tasks_case / "managed_child.json"
    tasks_plan = json.loads(tasks_plan_path.read_text(encoding="utf-8"))
    tasks_plan["artifacts"]["tasks_tsv"]["sha256"] = sha256_file(
        tasks_case / "tasks.tsv"
    )
    write_json(tasks_plan_path, tasks_plan)
    replace_checksum(tasks_case, "tasks.tsv")
    replace_checksum(tasks_case, "managed_child.json")

    binding_case = cases / "binding"
    shutil.copytree(child_dir, binding_case)
    binding_path = binding_case / "program_binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["child"]["event_count"] = 8_250
    binding.pop("binding_hash")
    binding["binding_hash"] = sha256_bytes(canonical_json(binding))
    write_json(binding_path, binding)
    # Refresh every outer digest so the child-contract check is the failing layer.
    plan_path = binding_case / "managed_child.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    digest = sha256_file(binding_path)
    plan["program_management"]["binding_sha256"] = digest
    plan["program_management"]["binding_hash"] = binding["binding_hash"]
    plan["artifacts"]["program_binding"]["sha256"] = digest
    write_json(plan_path, plan)
    replace_checksum(binding_case, "program_binding.json")
    replace_checksum(binding_case, "managed_child.json")

    boolean_case = cases / "boolean-schema"
    shutil.copytree(child_dir, boolean_case)
    boolean_binding_path = boolean_case / "program_binding.json"
    boolean_binding = json.loads(boolean_binding_path.read_text(encoding="utf-8"))
    boolean_binding["test_mode"] = "true"
    boolean_binding.pop("binding_hash")
    boolean_binding["binding_hash"] = sha256_bytes(canonical_json(boolean_binding))
    write_json(boolean_binding_path, boolean_binding)
    boolean_plan_path = boolean_case / "managed_child.json"
    boolean_plan = json.loads(boolean_plan_path.read_text(encoding="utf-8"))
    boolean_digest = sha256_file(boolean_binding_path)
    boolean_plan["program_management"]["binding_sha256"] = boolean_digest
    boolean_plan["program_management"]["binding_hash"] = boolean_binding[
        "binding_hash"
    ]
    boolean_plan["artifacts"]["program_binding"]["sha256"] = boolean_digest
    write_json(boolean_plan_path, boolean_plan)
    replace_checksum(boolean_case, "program_binding.json")
    replace_checksum(boolean_case, "managed_child.json")

    runtime_case = cases / "runtime"
    shutil.copytree(child_dir, runtime_case)
    runtime_plan_path = runtime_case / "managed_child.json"
    runtime_plan = json.loads(runtime_plan_path.read_text(encoding="utf-8"))
    runtime_plan["environment"]["geant4_version"] = "tampered-version"
    runtime_plan["environment"]["identity_hash"] = environment_identity(
        runtime_plan["environment"]
    )
    runtime_binding_path = runtime_case / "program_binding.json"
    runtime_binding = json.loads(runtime_binding_path.read_text(encoding="utf-8"))
    runtime_binding["runtime"]["environment_identity"] = runtime_plan[
        "environment"
    ]["identity_hash"]
    runtime_binding.pop("binding_hash")
    runtime_binding["binding_hash"] = sha256_bytes(canonical_json(runtime_binding))
    write_json(runtime_binding_path, runtime_binding)
    runtime_digest = sha256_file(runtime_binding_path)
    runtime_plan["program_management"]["binding_sha256"] = runtime_digest
    runtime_plan["program_management"]["binding_hash"] = runtime_binding[
        "binding_hash"
    ]
    runtime_plan["artifacts"]["program_binding"]["sha256"] = runtime_digest
    write_json(runtime_plan_path, runtime_plan)
    replace_checksum(runtime_case, "program_binding.json")
    replace_checksum(runtime_case, "managed_child.json")

    campaign_id_case = cases / "campaign-id"
    shutil.copytree(child_dir, campaign_id_case)
    campaign_id_plan_path = campaign_id_case / "managed_child.json"
    campaign_id_plan = json.loads(
        campaign_id_plan_path.read_text(encoding="utf-8")
    )
    campaign_id_plan["campaign_id"] = "tampered-reserved-campaign-id"
    campaign_id_binding_path = campaign_id_case / "program_binding.json"
    campaign_id_binding = json.loads(
        campaign_id_binding_path.read_text(encoding="utf-8")
    )
    campaign_id_binding["child"]["campaign_id"] = campaign_id_plan[
        "campaign_id"
    ]
    campaign_id_binding.pop("binding_hash")
    campaign_id_binding["binding_hash"] = sha256_bytes(
        canonical_json(campaign_id_binding)
    )
    write_json(campaign_id_binding_path, campaign_id_binding)
    campaign_id_digest = sha256_file(campaign_id_binding_path)
    campaign_id_plan["program_management"]["binding_sha256"] = (
        campaign_id_digest
    )
    campaign_id_plan["program_management"]["binding_hash"] = (
        campaign_id_binding["binding_hash"]
    )
    campaign_id_plan["artifacts"]["program_binding"]["sha256"] = (
        campaign_id_digest
    )
    write_json(campaign_id_plan_path, campaign_id_plan)
    replace_checksum(campaign_id_case, "program_binding.json")
    replace_checksum(campaign_id_case, "managed_child.json")

    metadata_case = cases / "metadata"
    shutil.copytree(child_dir, metadata_case)
    metadata_path = metadata_case / "managed_child.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source_contract"]["kinetic_energy_mev"] = 999
    write_json(metadata_path, metadata)
    replace_checksum(metadata_case, "managed_child.json")

    checksum_case = cases / "checksums"
    shutil.copytree(child_dir, checksum_case)
    checksum_path = checksum_case / "SHA256SUMS"
    rows = checksum_path.read_text(encoding="utf-8").splitlines()
    rows[0] = "0" * 64 + rows[0][64:]
    checksum_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    for case in (
        tasks_case,
        binding_case,
        boolean_case,
        runtime_case,
        campaign_id_case,
        metadata_case,
        checksum_case,
    ):
        try:
            load_managed_production_child(
                case,
                verify_runtime_artifacts=False,
                verify_control_plane=False,
                allow_test_mode=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"tampered managed child was accepted: {case.name}")


def assert_submission_routes(repo_root: Path, child_dir: Path, scratch: Path) -> None:
    generic = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_campaign.py",
            "--campaign-dir",
            str(child_dir),
            "--check-only",
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "dedicated validator" in generic.stdout

    renamed = scratch / "renamed-production-route"
    renamed.mkdir()
    write_json(
        renamed / "campaign.json",
        {
            "schema_version": "steel-module-campaign-v1",
            "stage": "renamed-production",
        },
    )
    (renamed / "tasks.tsv").write_text(
        "stage\tlogical_task_id\nrenamed-production\trenamed-task\n",
        encoding="utf-8",
    )
    renamed_route = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_campaign.py",
            "--campaign-dir",
            str(renamed),
            "--check-only",
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "generic submitter is fail-closed" in renamed_route.stdout

    ordinary_production = scratch / "ordinary-production-campaign"
    run(
        [
            sys.executable,
            "hpc/osc/generate_steel_module_campaign.py",
            "--out-dir",
            str(ordinary_production),
            "--stage",
            "production",
            "--campaign-seed",
            "20260717",
            "--events",
            "1",
            "--blocks",
            "4",
            "--allow-dirty",
        ],
        cwd=repo_root,
    )
    ordinary_route = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_campaign.py",
            "--campaign-dir",
            str(ordinary_production),
            "--check-only",
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "generic submitter is fail-closed" in ordinary_route.stdout
    abbreviated_route = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_campaign.py",
            "--campaign-d",
            str(ordinary_production),
            "--check-only",
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert abbreviated_route.returncode == 2
    assert "required: --campaign-dir" in abbreviated_route.stdout

    duplicate_route = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_campaign.py",
            "--campaign-dir",
            str(scratch),
            "--campaign-dir",
            str(child_dir),
            "--check-only",
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "may be provided only once" in duplicate_route.stdout

    data_root = scratch / "g4-data"
    data_root.mkdir()
    missing_mode = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_production_child.py",
            "--managed-child-dir",
            str(child_dir),
            "--g4-data-root",
            str(data_root),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert missing_mode.returncode == 2
    assert "Phase-2A is check-only" in missing_mode.stdout

    test_evidence = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_production_child.py",
            "--managed-child-dir",
            str(child_dir),
            "--g4-data-root",
            str(data_root),
            "--check-only",
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "Cannot validate managed production child" in test_evidence.stdout
    assert {path.name for path in child_dir.iterdir()} == EXPECTED_ROOT_ENTRIES

    sentinel = scratch / "slurm-command-was-called"
    fake_slurm = scratch / "fake-sbatch"
    fake_slurm.write_text(
        f"#!/bin/sh\ntouch {sentinel}\nexit 99\n", encoding="utf-8"
    )
    fake_slurm.chmod(0o755)
    hidden_submit_option = run(
        [
            sys.executable,
            "hpc/osc/submit_steel_module_production_child.py",
            "--managed-child-dir",
            str(child_dir),
            "--g4-data-root",
            str(data_root),
            "--check-only",
            "--sbatch-command",
            str(fake_slurm),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert hidden_submit_option.returncode == 2
    assert "unrecognized arguments" in hidden_submit_option.stdout
    assert not sentinel.exists()

    forbidden_materializer_override = run(
        [
            sys.executable,
            "hpc/osc/materialize_steel_module_production_child.py",
            "--out-dir",
            str(scratch / "forbidden-materialization"),
            "--program-dir",
            str(scratch / "program-override"),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert forbidden_materializer_override.returncode == 2
    assert "unrecognized arguments" in forbidden_materializer_override.stdout
    assert not (scratch / "forbidden-materialization").exists()


def assert_recorded_control_plane_requires_clean(repo_root: Path) -> None:
    try:
        _validate_recorded_control_plane(
            {
                "control_plane_source": {
                    "dirty": True,
                    "dirty_paths": [" M uncommitted-control-plane.py"],
                }
            },
            repo_root,
        )
    except ValueError as exc:
        assert "must be a clean checkout" in str(exc)
    else:
        raise AssertionError("dirty recorded control-plane source was accepted")


def assert_formal_location_is_canonical(program_dir: Path, child_dir: Path) -> None:
    program = load_production_program(
        program_dir, verify_runtime_artifacts=False
    )
    try:
        _validate_formal_child_location(child_dir.resolve(), program)
    except ValueError as exc:
        assert "must be the canonical path" in str(exc)
    else:
        raise AssertionError("noncanonical formal managed-child path was accepted")
    _validate_formal_child_location(
        (program.directory.parent / "steel-module-production-bc-s1").resolve(),
        program,
    )
    _validate_formal_child_location(
        (
            program.directory.parent
            / ".steel-module-production-bc-s1.tmp-fixture"
        ).resolve(),
        program,
        allow_materialization_staging=True,
    )
    try:
        _validate_formal_child_location(
            (program.directory.parent / ".wrong-name.tmp-fixture").resolve(),
            program,
            allow_materialization_staging=True,
        )
    except ValueError as exc:
        assert "must be the canonical path" in str(exc)
    else:
        raise AssertionError("unrelated formal staging path was accepted")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="steel-module-managed-production-check-"
    ) as raw:
        scratch = Path(raw)
        program_dir, child_dir = make_fixture(repo_root, scratch)
        assert_exact_bc_s1(program_dir, child_dir)
        assert_nonoverwrite_and_atomicity(
            repo_root, program_dir, child_dir, scratch
        )
        assert_tamper_rejection(child_dir, scratch)
        assert_submission_routes(repo_root, child_dir, scratch)
        assert_recorded_control_plane_requires_clean(repo_root)
        assert_formal_location_is_canonical(program_dir, child_dir)

    print(
        "steel-module managed production: PASS "
        "(BC-S1 exact binding, atomic/tamper gates, no-Slurm routes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
