#!/usr/bin/env python3
"""Focused no-scheduler checks for the R3 container-isolation probe."""

from __future__ import annotations

import json
import errno
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import steel_module_production_successor_lib as successor_lib
import steel_module_production_r3_probe_lib as r3_probe_lib
import steel_module_production_phase2b_lib as phase2b_lib
from check_steel_module_production_successor import (
    _fixture_r3_failure_identity,
    _make_sealed_incident_fixture,
    _make_v3_fixture,
    _successor_inputs,
)
from check_steel_module_production_successor_v4 import _v4_inputs
from steel_module_campaign_lib import canonical_json, sha256_bytes, sha256_file
from steel_module_production_container_contract import (
    AdditionalBind,
    CONTAINER_EXECUTION_ROOT,
    NO_MOUNT_CLASSES,
    ProductionContainerInputs,
    build_production_apptainer_prefix,
    inspect_apptainer_runtime_identity,
    production_apptainer_host_environment,
    sanitize_apptainer_host_environment,
    validate_apptainer_runtime_identity,
)
from steel_module_production_checkpoint_lib import recursive_file_records
from steel_module_production_r3_probe_lib import (
    CONTAINER_PROBE_PREFIX,
    RAW_PROBE_SCHEMA_VERSION,
    RAW_PROBE_SCHEMA_VERSION_V2,
    REPORT_KEYS,
    WRITER_OPEN_REJECTED_FUNCTION,
    _held_scheduler_identity,
    canonical_r3_evidence_root,
    expected_r3_job_name,
    seal_r3_probe_evidence,
    validate_r3_probe_evidence,
    validate_raw_probe_workspace,
    validate_terminal_accounting_input,
    write_terminal_accounting_input,
)
from steel_module_production_successor_lib import (
    RECOVERY_READINESS_LOCK_RELATIVE_V3,
    _validate_formal_r3_held_job_paths,
    _verify_r4_git_gate,
    build_successor_recovery_readiness_candidate,
    materialize_successor_execution_companion,
    materialize_successor_v3_execution_companion,
    materialize_successor_v4_execution_companion,
    successor_r3_preflight_binding,
)


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def _expect_value_error(callable_value: object, label: str) -> None:
    try:
        callable_value()  # type: ignore[operator]
    except ValueError:
        return
    raise AssertionError(f"R3 checker expected rejection: {label}")


def _test_environment_and_mount_contract(scratch: Path) -> None:
    scratch.mkdir(parents=True)
    paths: dict[str, Path] = {}
    for name in ("simulation", "control", "execution", "data", "prebuilt", "probe"):
        value = scratch / name
        value.mkdir()
        paths[name] = value
    image = scratch / "geant4.sif"
    image.write_bytes(b"fixture image\n")
    apptainer = scratch / "apptainer"
    apptainer.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = --version ]; then "
        "printf '%s\\n' 'apptainer version fixture'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    apptainer.chmod(0o755)
    prefix = build_production_apptainer_prefix(
        apptainer=apptainer,
        inputs=ProductionContainerInputs(
            image=image,
            simulation_root=paths["simulation"],
            control_root=paths["control"],
            execution_root=paths["execution"],
            data_root=paths["data"],
            executable_directory=paths["prebuilt"],
        ),
        additional_binds=(
            AdditionalBind(
                paths["probe"], CONTAINER_PROBE_PREFIX + "1" * 32, writable=True
            ),
        ),
    )
    assert prefix[:9] == [
        str(apptainer.resolve()),
        "exec",
        "--cleanenv",
        "--containall",
        "--no-home",
        "--no-mount",
        NO_MOUNT_CLASSES,
        "--pwd",
        "/",
    ]
    bind_values = [prefix[index + 1] for index, value in enumerate(prefix) if value == "--bind"]
    assert len(bind_values) == 6
    assert sum(value.endswith(":ro") for value in bind_values) == 5
    assert sum(value.endswith(":rw") for value in bind_values) == 1
    assert any(
        value.endswith(f":{CONTAINER_EXECUTION_ROOT}:ro") for value in bind_values
    )
    assert bind_values[-1].endswith(
        f":{CONTAINER_PROBE_PREFIX + '1' * 32}:rw"
    )
    assert prefix[-1] == str(image.resolve())

    benign = {"PATH": "/bin", "TMPDIR": "/tmp"}
    assert production_apptainer_host_environment(benign) == benign
    engine = inspect_apptainer_runtime_identity(
        apptainer, environment=benign, cwd=paths["control"]
    )
    assert engine == {
        "apptainer_path": str(apptainer.resolve()),
        "apptainer_sha256": sha256_file(apptainer),
        "apptainer_version": "apptainer version fixture",
    }
    assert validate_apptainer_runtime_identity(
        apptainer,
        expected=engine,
        environment=benign,
        cwd=paths["control"],
    ) == engine
    tainted_names = (
        "APPTAINER_BIND",
        "APPTAINER_BINDPATH",
        "SINGULARITY_BIND",
        "SINGULARITY_BINDPATH",
        "APPTAINERENV_PATH",
        "SINGULARITYENV_LD_PRELOAD",
        "APPTAINER_MOUNT",
        "APPTAINER_CACHEDIR",
        "SINGULARITY_SHELL",
    )
    for name in tainted_names:
        tainted = {**benign, name: "/unexpected"}
        clean, removed = sanitize_apptainer_host_environment(tainted)
        assert removed == (name,) and name not in clean
        _expect_value_error(
            lambda tainted=tainted: production_apptainer_host_environment(tainted),
            name,
        )

    drifted_engine = {**engine, "apptainer_sha256": "0" * 64}
    _expect_value_error(
        lambda: validate_apptainer_runtime_identity(
            apptainer,
            expected=drifted_engine,
            environment=benign,
            cwd=paths["control"],
        ),
        "production Apptainer engine drift",
    )

    overlap = scratch / "overlap"
    overlap.mkdir()
    _expect_value_error(
        lambda: build_production_apptainer_prefix(
            apptainer=apptainer,
            inputs=ProductionContainerInputs(
                image=image,
                simulation_root=paths["simulation"],
                control_root=paths["control"],
                execution_root=paths["execution"],
                data_root=paths["data"],
                executable_directory=paths["prebuilt"],
            ),
            additional_binds=(
                AdditionalBind(
                    overlap,
                    CONTAINER_EXECUTION_ROOT + "/unexpected",
                    writable=True,
                ),
            ),
        ),
        "non-task execution overlay",
    )


def _test_writer_open_rejection_shell_contract(scratch: Path) -> None:
    """Exercise the exact Bash helper that previously masked a denied open."""

    scratch.mkdir(parents=True)
    locked = scratch / "locked"
    writable = scratch / "writable"
    inherited_fd = scratch / "inherited-fd"
    missing_parent_target = scratch / "missing-parent" / "target"
    locked.write_bytes(b"locked\n")
    writable.write_bytes(b"writable\n")
    inherited_fd.write_bytes(b"")
    locked.chmod(0o400)
    writable.chmod(0o600)
    before_locked = locked.read_bytes()
    before_writable = writable.read_bytes()
    bash = shutil.which("bash")
    assert bash is not None
    script = (
        "set -euo pipefail\n"
        + WRITER_OPEN_REJECTED_FUNCTION
        + "\n"
        + 'exec 9>>"$4"\n'
        + 'writer_open_rejected "$1"\n'
        + 'writer_open_rejected "$2"\n'
        + 'if writer_open_rejected "$3"; then exit 41; fi\n'
        + 'printf preserved >&9\n'
    )
    result = subprocess.run(
        [
            bash,
            "-c",
            script,
            "bash",
            str(missing_parent_target),
            str(locked),
            str(writable),
            str(inherited_fd),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert locked.read_bytes() == before_locked
    assert writable.read_bytes() == before_writable
    assert inherited_fd.read_bytes() == b"preserved"


def _write_raw_workspace(
    workspace: Path,
    successor: object,
    *,
    validate_success: bool = True,
    schema_version: str = RAW_PROBE_SCHEMA_VERSION,
) -> Path:
    if schema_version not in {
        RAW_PROBE_SCHEMA_VERSION,
        RAW_PROBE_SCHEMA_VERSION_V2,
    }:
        raise ValueError("unsupported fixture raw schema")
    workspace.mkdir()
    challenge = workspace / "challenge.bin"
    roundtrip = workspace / "container-roundtrip.bin"
    challenge.write_bytes(b"deterministic-r3-fixture\n")
    roundtrip.write_bytes(challenge.read_bytes())
    report = {name: True for name in REPORT_KEYS}
    (workspace / "container-report.tsv").write_text(
        "".join(f"{name}\ttrue\n" for name in REPORT_KEYS), encoding="utf-8"
    )
    (workspace / "stdout.txt").write_text("fixture stdout\n", encoding="utf-8")
    (workspace / "stderr.txt").write_text("", encoding="utf-8")
    runtime = successor.manifest["runtime"]
    lock_record = successor.manifest["artifacts"]["control_lock"]
    lock_path = successor.directory / lock_record["path"]
    lock_stat = lock_path.lstat()
    execution_records = recursive_file_records(successor.directory, exclude=())
    execution_snapshot = _canonical_hash(execution_records)
    payload = {
        "schema_version": schema_version,
        "created_at_utc": "2026-07-19T12:00:00+00:00",
        "test_mode": True,
        "accepted_compute_preflight_evidence": False,
        "probe_passed": True,
        "scheduler_submission_performed_by_tool": False,
        "scheduler_contact_performed_by_tool": False,
        "apptainer_invoked": True,
        "geant4_invoked": False,
        "execution_id": successor.execution_id,
        "execution_hash": successor.execution_hash,
        "execution_schema_version": successor.manifest["schema_version"],
        "execution_directory": str(successor.directory),
        "slurm_job_id": "70000123",
        "slurm_job_name": expected_r3_job_name(successor.execution_hash),
        "probe_token": "1" * 32,
        "probe_workspace": str(workspace.resolve()),
        "container_probe_root": CONTAINER_PROBE_PREFIX + "1" * 32,
        "container_adjacent_root": CONTAINER_PROBE_PREFIX + "adjacent-" + "1" * 32,
        "container_original_execution_path": str(successor.directory),
        "container_contract": {
            "cleanenv": True,
            "containall": True,
            "no_home": True,
            "no_mount": NO_MOUNT_CLASSES,
            "fixed_ro_mount_count": 5,
            "external_rw_mount_count": 1,
            "execution_root_read_only": True,
            "probe_root_read_write": True,
            "mountinfo_contract_version": "linux-proc-mountinfo-v1",
            "command_sha256": "2" * 64,
        },
        "runtime": {
            "image_sha256": runtime["image_sha256"],
            "g4_data_manifest_sha256": runtime["g4_data_manifest_sha256"],
            "executable_sha256": runtime["executable_sha256"],
            "apptainer_path": "/fixture/apptainer",
            "apptainer_sha256": "4" * 64,
            "apptainer_version": "apptainer version fixture",
        },
        "compute_node": {
            "hostname": "fixture-compute-node",
            "slurmd_nodename": "",
        },
        "portable_lock_diagnostics": {
            "protocol": lock_record["protocol"],
            "creation_device": lock_record["creation_device"],
            "creation_inode": lock_record["creation_inode"],
            "compute_device": lock_stat.st_dev,
            "compute_inode": lock_stat.st_ino,
            "mode": lock_stat.st_mode & 0o777,
            "size_bytes": lock_stat.st_size,
            "sha256": sha256_file(lock_path),
            "link_count": lock_stat.st_nlink,
            "cross_node_numeric_identity_required": False,
            "portable_identity_matches": True,
        },
        "host_environment": {
            "forbidden_variable_count": 0,
            "sanitized_environment_used": True,
        },
        "container_report": report,
        "return_code": 0,
        "challenge_sha256": sha256_file(challenge),
        "roundtrip_sha256": sha256_file(roundtrip),
        "stdout_sha256": sha256_file(workspace / "stdout.txt"),
        "stderr_sha256": sha256_file(workspace / "stderr.txt"),
        "execution_snapshot_before_sha256": execution_snapshot,
        "execution_snapshot_after_sha256": execution_snapshot,
        "execution_snapshot_record_count": len(execution_records),
        "execution_snapshot_unchanged": True,
        "adjacent_host_sibling_absent": True,
    }
    if schema_version == RAW_PROBE_SCHEMA_VERSION:
        token_name = ".r3-probe-" + "1" * 32
        baseline = r3_probe_lib._mutable_tree_snapshot(successor.directory)
        mounted = json.loads(json.dumps(baseline))
        mounted["records"] = r3_probe_lib._mountpoint_mutable_records(token_name)
        mounted.pop("snapshot_hash")
        mounted["snapshot_hash"] = _canonical_hash(mounted)
        marker_payload = {
            "schema_version": r3_probe_lib.RETIREMENT_MARKER_SCHEMA_VERSION,
            "probe_token": "1" * 32,
            "execution_id": successor.execution_id,
            "execution_hash": successor.execution_hash,
            "challenge_sha256": sha256_file(challenge),
            "source_relative_path": f"attempts/{token_name}",
            "retirement_mechanism": "in-place-retained-mountpoint",
            "raw_marker_copy_name": (
                r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
            ),
            "rename_performed": False,
            "directory_entry_removed": False,
            "deletion_performed": False,
            "execution_closed": True,
            "future_successor_required": True,
        }
        mountpoint = successor.directory / "attempts" / token_name
        mountpoint.mkdir(mode=0o700)
        marker = mountpoint / r3_probe_lib.RETIREMENT_MARKER_NAME
        marker.write_text(
            json.dumps(marker_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        marker.chmod(0o400)
        raw_marker = (
            workspace / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
        )
        raw_marker.write_bytes(marker.read_bytes())
        raw_marker.chmod(0o400)
        closed = r3_probe_lib._mutable_tree_snapshot(successor.directory)
        closed_records = recursive_file_records(successor.directory, exclude=())
        closed_snapshot = _canonical_hash(closed_records)
        payload.update(
            {
                "closed_execution_snapshot_sha256": closed_snapshot,
                "closed_execution_snapshot_record_count": len(closed_records),
                "mutable_tree_snapshot_baseline": baseline,
                "mutable_tree_snapshot_before_container": mounted,
                "mutable_tree_snapshot_after_container": json.loads(
                    json.dumps(mounted)
                ),
                "mutable_tree_snapshot_after_retirement": closed,
                "mountpoint_lifecycle": {
                    "relative_path": f"attempts/{token_name}",
                    "mode": 0o700,
                    "parent_opened_with_no_follow": True,
                    "created_with_parent_dirfd": True,
                    "created_exclusively": True,
                    "same_process_identity_only": True,
                    "retirement_same_identity_verified": True,
                    "retirement_regular_directory_verified": True,
                    "retirement_mode_verified": True,
                    "retirement_initially_empty_verified": True,
                    "retirement_no_sibling_verified": True,
                    "retirement_mechanism": "in-place-retained-mountpoint",
                    "rename_performed": False,
                    "directory_entry_removed": False,
                    "path_identity_verified_after_marker": True,
                    "retirement_marker_created_through_leaf_fd": True,
                    "retirement_marker_sha256": sha256_file(marker),
                    "retirement_marker_size_bytes": marker.stat().st_size,
                    "raw_marker_copy_name": (
                        r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
                    ),
                    "raw_marker_copy_sha256": sha256_file(raw_marker),
                    "retirement_retained_in_place": True,
                    "execution_closed": True,
                    "future_successor_required": True,
                    "deletion_performed": False,
                    "identity_persisted": False,
                },
                "closed_v4_boundary_validated_after_retirement": True,
            }
        )
    payload["raw_result_hash"] = _canonical_hash(payload)
    (workspace / "probe_result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if validate_success:
        validate_raw_probe_workspace(workspace, allow_test_mode=True)
    return workspace


def _make_active_v4_fixture(repo_root: Path, root: Path) -> object:
    (root / "lineage").mkdir(parents=True)
    _, predecessor_v2, _, inputs = _make_v3_fixture(
        repo_root, root / "lineage"
    )
    with _fixture_r3_failure_identity(predecessor_v2):
        predecessor_v3 = materialize_successor_v3_execution_companion(
            **inputs, out_dir=root / "successor-v3"
        )
        v4_inputs, _, _, _ = _v4_inputs(repo_root, root, predecessor_v3, inputs)
        target = (
            root
            / "active-work/campaigns/steel-module-production-bc-s1-execution-v4"
        )
        target.parent.mkdir(parents=True)
        return materialize_successor_v4_execution_companion(
            **v4_inputs, out_dir=target
        )


def _test_mountpoint_lease_adversaries(scratch: Path) -> None:
    with patch.object(r3_probe_lib.sys, "platform", "linux"):
        assert r3_probe_lib._expected_mountpoint_link_count(entry_count=0) == 2
        assert r3_probe_lib._expected_mountpoint_link_count(entry_count=1) == 2
    with patch.object(r3_probe_lib.sys, "platform", "darwin"):
        assert r3_probe_lib._expected_mountpoint_link_count(entry_count=0) == 2
        assert r3_probe_lib._expected_mountpoint_link_count(entry_count=1) == 3

    token = "a" * 32
    name = ".r3-probe-" + token
    case_index = 0

    def fresh() -> tuple[Path, Path]:
        nonlocal case_index
        case_index += 1
        root = scratch / f"case-{case_index}"
        execution = root / "execution"
        for mutable in ("intents", "attempts", "finalized"):
            (execution / mutable).mkdir(parents=True, mode=0o700)
        workspace = root / "raw"
        workspace.mkdir(mode=0o700)
        return execution, workspace

    def marker_payload() -> dict[str, object]:
        return {
            "schema_version": r3_probe_lib.RETIREMENT_MARKER_SCHEMA_VERSION,
            "probe_token": token,
            "execution_id": "fixture-execution",
            "execution_hash": "1" * 64,
            "challenge_sha256": "2" * 64,
            "source_relative_path": f"attempts/{name}",
            "retirement_mechanism": "in-place-retained-mountpoint",
            "raw_marker_copy_name": (
                r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
            ),
            "rename_performed": False,
            "directory_entry_removed": False,
            "deletion_performed": False,
            "execution_closed": True,
            "future_successor_required": True,
        }

    def seal(execution: Path, workspace: Path) -> dict[str, object]:
        lease = r3_probe_lib._create_probe_mountpoint(execution, token=token)
        return r3_probe_lib._seal_probe_mountpoint_in_place(
            lease,
            workspace=workspace,
            marker_payload=marker_payload(),
        )

    execution, workspace = fresh()
    forbidden = AssertionError("in-place retirement attempted namespace removal")
    with (
        patch.object(r3_probe_lib.os, "rename", side_effect=forbidden),
        patch.object(r3_probe_lib.os, "rmdir", side_effect=forbidden),
        patch.object(r3_probe_lib.os, "unlink", side_effect=forbidden),
    ):
        outcome = seal(execution, workspace)
    assert outcome["retained_in_place"] is True, outcome
    marker = execution / "attempts" / name / r3_probe_lib.RETIREMENT_MARKER_NAME
    raw_copy = workspace / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
    assert marker.read_bytes() == raw_copy.read_bytes()
    assert outcome["rename_performed"] is False
    assert outcome["directory_entry_removed"] is False
    assert outcome["deletion_performed"] is False
    r3_probe_lib.validate_closed_successor_v4_probe_boundary(
        execution,
        marker_payload=marker_payload(),
        expected_marker_sha256=sha256_file(marker),
    )
    closed_records = recursive_file_records(execution, exclude=())
    closed_view = SimpleNamespace(
        manifest={"test_mode": False}, directory=execution
    )
    with patch.object(
        successor_lib,
        "successor_r3_preflight_binding",
        side_effect=AssertionError("closed v4 reached readiness binding"),
    ):
        try:
            build_successor_recovery_readiness_candidate(
                closed_view, preflight_evidence_dir=workspace
            )
        except ValueError as exc:
            assert "requires a future successor" in str(exc)
        else:
            raise AssertionError("closed v4 became recovery-ready")
    assert recursive_file_records(execution, exclude=()) == closed_records
    assert not any((execution / "intents").iterdir())

    execution, workspace = fresh()
    collision = execution / "attempts" / name
    collision.mkdir(mode=0o700)
    _expect_value_error(
        lambda: r3_probe_lib._create_probe_mountpoint(execution, token=token),
        "collision",
    )

    execution, workspace = fresh()
    lease = r3_probe_lib._create_probe_mountpoint(execution, token=token)
    sibling = execution / "attempts/unexpected-sibling"
    sibling.mkdir(mode=0o700)
    outcome = r3_probe_lib._seal_probe_mountpoint_in_place(
        lease, workspace=workspace, marker_payload=marker_payload()
    )
    assert outcome["retained_in_place"] is False and sibling.exists()
    assert not (workspace / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME).exists()

    execution, workspace = fresh()
    lease = r3_probe_lib._create_probe_mountpoint(execution, token=token)
    residue = execution / "attempts" / name / "residue"
    residue.write_text("residue\n", encoding="utf-8")
    outcome = r3_probe_lib._seal_probe_mountpoint_in_place(
        lease, workspace=workspace, marker_payload=marker_payload()
    )
    assert outcome["retained_in_place"] is False and residue.exists()

    execution, workspace = fresh()
    lease = r3_probe_lib._create_probe_mountpoint(execution, token=token)
    detached = execution.parent / "detached-original"
    (execution / "attempts" / name).rename(detached)
    (execution / "attempts" / name).mkdir(mode=0o700)
    outcome = r3_probe_lib._seal_probe_mountpoint_in_place(
        lease, workspace=workspace, marker_payload=marker_payload()
    )
    assert outcome["retained_in_place"] is False and detached.is_dir()

    execution, workspace = fresh()
    lease = r3_probe_lib._create_probe_mountpoint(execution, token=token)
    original_writer = r3_probe_lib._write_retirement_marker

    def swap_after_marker(leaf_fd: int, payload: dict[str, object]) -> str:
        digest = original_writer(leaf_fd, payload)
        detached_after = execution.parent / "detached-after-marker"
        (execution / "attempts" / name).rename(detached_after)
        (execution / "attempts" / name).mkdir(mode=0o700)
        return digest

    with patch.object(
        r3_probe_lib,
        "_write_retirement_marker",
        side_effect=swap_after_marker,
    ):
        outcome = r3_probe_lib._seal_probe_mountpoint_in_place(
            lease, workspace=workspace, marker_payload=marker_payload()
        )
    assert outcome["retained_in_place"] is False
    assert (execution.parent / "detached-after-marker").is_dir()
    assert not (workspace / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME).exists()

    execution, workspace = fresh()
    lease = r3_probe_lib._create_probe_mountpoint(execution, token=token)
    with patch.object(
        r3_probe_lib,
        "_write_retirement_marker",
        side_effect=OSError(errno.EIO, "fixture marker failure"),
    ):
        outcome = r3_probe_lib._seal_probe_mountpoint_in_place(
            lease, workspace=workspace, marker_payload=marker_payload()
        )
    assert outcome["failure"] == f"oserror-{errno.EIO}"
    assert outcome["retained_in_place"] is False
    assert (execution / "attempts" / name).is_dir()
    assert not (workspace / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME).exists()


def _test_active_probe_failure_cleanup(repo_root: Path, scratch: Path) -> None:
    successor = _make_active_v4_fixture(repo_root, scratch / "fixture")
    # execution-v4 is now immutable 0400 failure evidence.  Exercise the
    # active GPFS/O_RDWR lifecycle through a test-only v5 view while preserving
    # the historical v4 fixture for validation elsewhere in this checker.
    lock_path = successor.directory / ".control.lock"
    lock_path.chmod(0o600)
    active_manifest = json.loads(json.dumps(successor.manifest))
    active_manifest["schema_version"] = (
        r3_probe_lib.SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5
    )
    active_manifest["artifacts"]["control_lock"]["mode"] = 0o600
    successor = phase2b_lib.ManagedExecution(
        successor.directory,
        successor.managed_child,
        active_manifest,
        successor.tasks,
        successor.scan_args,
    )
    home = scratch / "home"
    (home / "geant4-data/11.4.2").mkdir(parents=True)
    apptainer = scratch / "apptainer"
    apptainer.write_text("fixture apptainer\n", encoding="utf-8")
    apptainer.chmod(0o755)
    runtime_root = scratch / "runtime"
    runtime_root.mkdir()
    runtime_paths = {
        "Apptainer image": runtime_root / "geant4.sif",
        "Geant4 data manifest": runtime_root / "g4-data-manifest.sha256",
        "prebuilt executable": runtime_root / "build/OpNovice2",
    }
    for path in runtime_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture runtime artifact\n", encoding="utf-8")
    job_name = expected_r3_job_name(successor.execution_hash)
    raw_parent = canonical_r3_evidence_root(successor.directory) / "raw"
    raw_parent.mkdir(parents=True)
    environment = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "SLURM_JOB_ID": "70000456",
        "SLURM_JOB_NAME": job_name,
        "SLURMD_NODENAME": "fixture-compute-02",
    }
    token = "c" * 32
    original_marker_writer = r3_probe_lib._write_retirement_marker
    original_raw_copy_writer = r3_probe_lib._write_raw_marker_copy

    def invoke(case: str, *, expect_success: bool = False) -> Path:
        workspace = raw_parent / job_name
        if workspace.exists():
            shutil.rmtree(workspace)

        def fake_container(
            arguments: list[str], **_: object
        ) -> subprocess.CompletedProcess[str]:
            (workspace / "container-roundtrip.bin").write_bytes(
                (workspace / "challenge.bin").read_bytes()
            )
            report = "".join(f"{name}\ttrue\n" for name in REPORT_KEYS)
            if case == "parse-failure":
                report = "malformed\n"
            (workspace / "container-report.tsv").write_text(
                report, encoding="utf-8"
            )
            if case == "write-failure":
                (workspace / "stdout.txt").write_text(
                    "collision\n", encoding="utf-8"
                )
            attempts = successor.directory / "attempts"
            mountpoint = next(attempts.glob(".r3-probe-*"))
            if case == "extra-sibling":
                (attempts / "unexpected-sibling").mkdir(mode=0o700)
            elif case == "nonempty":
                (mountpoint / "residue").write_text("x\n", encoding="utf-8")
            elif case == "inode-replacement":
                detached = scratch / "container-detached"
                mountpoint.rename(detached)
                mountpoint.mkdir(mode=0o700)
            elif case == "raw-marker-copy-collision":
                collision = (
                    workspace / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
                )
                collision.write_text("collision\n", encoding="utf-8")
            return subprocess.CompletedProcess(
                arguments,
                9 if case == "container-nonzero" else 0,
                stdout="fixture stdout\n",
                stderr="fixture stderr\n",
            )

        engine = {
            "apptainer_path": str(apptainer.resolve()),
            "apptainer_sha256": sha256_file(apptainer),
            "apptainer_version": "apptainer version fixture",
        }

        def fake_resolve(record: dict[str, object], label: str) -> tuple[Path, str]:
            return runtime_paths[label].resolve(), str(record["sha256"])

        def active_marker_writer(
            leaf_fd: int, payload: dict[str, object]
        ) -> str:
            if case == "marker-write-failure":
                raise OSError(errno.EIO, "fixture marker write failure")
            digest = original_marker_writer(leaf_fd, payload)
            if case == "final-swap":
                attempts = successor.directory / "attempts"
                source = next(attempts.glob(".r3-probe-*"))
                detached = scratch / "active-final-swap-original"
                source.rename(detached)
                source.mkdir(mode=0o700)
            return digest

        def active_raw_copy_writer(
            workspace_fd: int, *, marker_content: bytes
        ) -> str:
            if case == "raw-marker-copy-failure":
                raise OSError(errno.EIO, "fixture raw marker copy failure")
            return original_raw_copy_writer(
                workspace_fd, marker_content=marker_content
            )

        with (
            patch.object(
                r3_probe_lib, "load_successor_execution", return_value=successor
            ),
            patch.object(
                r3_probe_lib,
                "inspect_apptainer_runtime_identity",
                return_value=engine,
            ),
            patch.object(
                r3_probe_lib,
                "resolve_recorded_artifact",
                side_effect=fake_resolve,
            ),
            patch.object(
                r3_probe_lib,
                "build_production_apptainer_prefix",
                return_value=[str(apptainer), "exec", "fixture.sif"],
            ),
            patch.object(r3_probe_lib.subprocess, "run", side_effect=fake_container),
            patch.object(
                r3_probe_lib,
                "_write_retirement_marker",
                side_effect=active_marker_writer,
            ),
            patch.object(
                r3_probe_lib,
                "_write_raw_marker_copy",
                side_effect=active_raw_copy_writer,
            ),
            patch.object(r3_probe_lib.Path, "home", return_value=home),
            patch.object(r3_probe_lib.secrets, "token_hex", return_value=token),
        ):
            if expect_success:
                result = r3_probe_lib.run_r3_container_probe(
                    execution_dir=successor.directory,
                    control_root=repo_root,
                    workspace=workspace,
                    host_environment=environment,
                    apptainer_path=apptainer,
                )
                validate_raw_probe_workspace(result)
                return result
            try:
                r3_probe_lib.run_r3_container_probe(
                    execution_dir=successor.directory,
                    control_root=repo_root,
                    workspace=workspace,
                    host_environment=environment,
                    apptainer_path=apptainer,
                )
            except (OSError, ValueError):
                pass
            else:
                raise AssertionError(f"active probe failure was accepted: {case}")
        assert not (workspace / "probe_result.json").exists()
        return workspace

    success = invoke("success", expect_success=True)
    assert (success / "probe_result.json").is_file()
    success_payload = validate_raw_probe_workspace(success)
    success_marker_payload = r3_probe_lib._expected_retirement_marker_payload(
        success_payload
    )
    r3_probe_lib.validate_closed_successor_v4_probe_boundary(
        successor.directory,
        marker_payload=success_marker_payload,
        expected_marker_sha256=success_payload["mountpoint_lifecycle"][
            "retirement_marker_sha256"
        ],
    )
    attempts = successor.directory / "attempts"
    shutil.rmtree(next(attempts.iterdir()))
    shutil.rmtree(success)
    for case in ("container-nonzero", "parse-failure", "write-failure"):
        workspace = invoke(case)
        assert not (workspace / "probe_result.json").exists()
        entries = tuple((successor.directory / "attempts").iterdir())
        assert len(entries) == 1
        assert (entries[0] / r3_probe_lib.RETIREMENT_MARKER_NAME).is_file()
        shutil.rmtree(entries[0])
        shutil.rmtree(workspace)

    for case in (
        "extra-sibling",
        "nonempty",
        "inode-replacement",
        "final-swap",
        "marker-write-failure",
        "raw-marker-copy-collision",
        "raw-marker-copy-failure",
    ):
        workspace = invoke(case)
        assert not (workspace / "probe_result.json").exists()
        _expect_value_error(
            lambda: successor_lib.validate_successor_r2_boundary(
                successor, repo_root=repo_root
            ),
            "strict boundary residue",
        )
        attempts = successor.directory / "attempts"
        for entry in list(attempts.iterdir()):
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
        detached = scratch / "container-detached"
        if detached.exists():
            shutil.rmtree(detached)
        detached = scratch / "active-final-swap-original"
        if detached.exists():
            shutil.rmtree(detached)
        shutil.rmtree(workspace)
        successor_lib.validate_successor_r2_boundary(successor, repo_root=repo_root)


def _test_accounting_and_evidence(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir(parents=True)
    (scratch / "lineage").mkdir()
    _, predecessor_v2, _, inputs = _make_v3_fixture(
        repo_root, scratch / "lineage"
    )
    with _fixture_r3_failure_identity(predecessor_v2):
        predecessor_v3 = materialize_successor_v3_execution_companion(
            **inputs, out_dir=scratch / "successor-v3"
        )
        v4_inputs, _, _, _ = _v4_inputs(
            repo_root, scratch, predecessor_v3, inputs
        )
        v4_parent = scratch / "active-work" / "campaigns"
        v4_parent.mkdir(parents=True)
        successor = materialize_successor_v4_execution_companion(
            **v4_inputs,
            out_dir=v4_parent / "steel-module-production-bc-s1-execution-v4",
        )
    raw = _write_raw_workspace(scratch / "raw", successor)

    # A fully re-signed historical raw-v2 shape can never become an active
    # success merely because its report rows are all true.
    raw_v2 = scratch / "raw-v2-success"
    shutil.copytree(raw, raw_v2)
    raw_v2_result = raw_v2 / "probe_result.json"
    raw_v2_payload = json.loads(raw_v2_result.read_text(encoding="utf-8"))
    for name in (
        "closed_execution_snapshot_sha256",
        "closed_execution_snapshot_record_count",
        "mutable_tree_snapshot_baseline",
        "mutable_tree_snapshot_before_container",
        "mutable_tree_snapshot_after_container",
        "mutable_tree_snapshot_after_retirement",
        "mountpoint_lifecycle",
        "closed_v4_boundary_validated_after_retirement",
    ):
        raw_v2_payload.pop(name)
    (raw_v2 / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME).unlink()
    raw_v2_payload["schema_version"] = RAW_PROBE_SCHEMA_VERSION_V2
    raw_v2_payload.pop("raw_result_hash")
    raw_v2_payload["raw_result_hash"] = _canonical_hash(raw_v2_payload)
    raw_v2_result.write_text(
        json.dumps(raw_v2_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _expect_value_error(
        lambda: validate_raw_probe_workspace(raw_v2, allow_test_mode=True),
        "raw-v2 active success",
    )

    def resign_raw_v3(
        label: str, mutator: object
    ) -> None:
        target = scratch / f"raw-v3-tampered-{label}"
        shutil.copytree(raw, target)
        result_path = target / "probe_result.json"
        value = json.loads(result_path.read_text(encoding="utf-8"))
        mutator(value)  # type: ignore[operator]
        value.pop("raw_result_hash")
        value["raw_result_hash"] = _canonical_hash(value)
        result_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _expect_value_error(
            lambda: validate_raw_probe_workspace(target, allow_test_mode=True),
            f"re-signed raw-v3 field tamper: {label}",
        )

    resign_raw_v3(
        "cleanup",
        lambda value: value["mountpoint_lifecycle"].__setitem__(
            "retirement_retained_in_place", False
        ),
    )
    resign_raw_v3(
        "persisted-identity",
        lambda value: value["mountpoint_lifecycle"].__setitem__(
            "identity_persisted", True
        ),
    )
    resign_raw_v3(
        "closed-boundary",
        lambda value: value.__setitem__(
            "closed_v4_boundary_validated_after_retirement", False
        ),
    )

    marker_tamper = scratch / "raw-v3-tampered-marker-copy"
    shutil.copytree(raw, marker_tamper)
    marker_copy = marker_tamper / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
    marker_copy.chmod(0o600)
    marker_copy.write_text("tampered\n", encoding="utf-8")
    _expect_value_error(
        lambda: validate_raw_probe_workspace(
            marker_tamper, allow_test_mode=True
        ),
        "raw-v3 marker-copy tamper",
    )

    def tamper_snapshot(value: dict[str, object]) -> None:
        snapshot = value["mutable_tree_snapshot_after_retirement"]
        assert isinstance(snapshot, dict)
        records = snapshot["records"]
        assert isinstance(records, list)
        records.append(
            {"path": "attempts/residue", "type": "directory", "mode": 0o700}
        )
        records.sort(key=lambda record: str(record["path"]))
        snapshot.pop("snapshot_hash")
        snapshot["snapshot_hash"] = _canonical_hash(snapshot)

    resign_raw_v3("snapshot-residue", tamper_snapshot)
    job_id = "70000123"
    job_name = expected_r3_job_name(successor.execution_hash)
    sacct = (
        f"{job_id}|{job_name}|pas2524|COMPLETED|0:0|7\n"
        f"{job_id}.batch|batch|pas2524|COMPLETED|0:0|7\n"
        f"{job_id}.extern|extern|pas2524|COMPLETED|0:0|7\n"
    )
    held_scontrol = (
        f"JobId={job_id} JobName={job_name} Account=pas2524 "
        "JobState=PENDING Reason=JobHeldUser Requeue=0 "
        "Command=/fixture/frozen-r3-runner.py WorkDir=/fixture/execution "
        f"StdOut=/fixture/slurm-{job_id}.out\n"
    )
    formal_execution = (
        scratch
        / "formal-work/campaigns/steel-module-production-bc-s1-execution-v4"
    )
    formal_execution.mkdir(parents=True)
    formal_execution = formal_execution.resolve()
    (formal_execution / "managed_execution.json").write_text(
        json.dumps(
            {"schema_version": r3_probe_lib.SUCCESSOR_EXECUTION_SCHEMA_VERSION}
        )
        + "\n",
        encoding="utf-8",
    )
    formal_command = (
        formal_execution
        / "sources/control/hpc/osc/"
        "run_steel_module_production_r3_probe.sbatch"
    )
    formal_held = (
        f"JobId={job_id} JobName={job_name} Account=PAS2524 "
        "JobState=PENDING Reason=JobHeldUser Requeue=0 "
        f"Command={formal_command} WorkDir={formal_execution} "
        f"StdOut={canonical_r3_evidence_root(formal_execution)}/"
        f"slurm-{job_id}.out\n"
    )
    assert _held_scheduler_identity(
        formal_held,
        job_id=job_id,
        job_name=job_name,
        formal_execution_dir=formal_execution,
    )["non_array"] is True
    formal_accounting = {
        "job_id": job_id,
        "held_scheduler": _held_scheduler_identity(
            formal_held,
            job_id=job_id,
            job_name=job_name,
            formal_execution_dir=None,
        ),
    }
    _validate_formal_r3_held_job_paths(formal_execution, formal_accounting)
    wrong_accounting = json.loads(json.dumps(formal_accounting))
    wrong_accounting["held_scheduler"]["command"] = "/wrong/command"
    _expect_value_error(
        lambda: _validate_formal_r3_held_job_paths(
            formal_execution, wrong_accounting
        ),
        "R4 rejects direct-library held path bypass",
    )
    _expect_value_error(
        lambda: _held_scheduler_identity(
            formal_held.replace(str(formal_command), "/wrong/command"),
            job_id=job_id,
            job_name=job_name,
            formal_execution_dir=formal_execution,
        ),
        "formal R3 held-job command drift",
    )
    live_mountpoint = (
        successor.directory / "attempts" / (".r3-probe-" + "1" * 32)
    )
    live_marker_bytes = (
        raw / r3_probe_lib.RAW_RETIREMENT_MARKER_COPY_NAME
    ).read_bytes()
    shutil.rmtree(live_mountpoint)
    successor_lib.validate_successor_r2_boundary(
        successor, repo_root=repo_root
    )
    successor_held = (
        f"JobId={job_id} JobName={job_name} Account=pas2524 "
        "JobState=PENDING Reason=JobHeldUser Requeue=0 "
        f"Command={successor.directory}/sources/control/hpc/osc/"
        "run_steel_module_production_r3_probe.sbatch "
        f"WorkDir={successor.directory} "
        f"StdOut={canonical_r3_evidence_root(successor.directory)}/"
        f"slurm-{job_id}.out\n"
    )
    held_path = scratch / "held-before-release.txt"
    held_path.write_text(successor_held, encoding="utf-8")
    original_successor_loader = r3_probe_lib.load_successor_execution

    def held_fixture_loader(*args: object, **kwargs: object) -> object:
        kwargs["rejection_validator"] = v4_inputs["rejection_validator"]
        return original_successor_loader(*args, **kwargs)

    r3_probe_lib.load_successor_execution = held_fixture_loader
    try:
        held_identity = r3_probe_lib.validate_held_r3_job_snapshot(
            execution_dir=successor.directory,
            control_root=repo_root,
            held_scontrol_input=held_path,
            job_id=job_id,
            job_name=job_name,
            test_mode=True,
        )
        assert held_identity["scheduler_contact_performed_by_tool"] is False
        assert held_identity["release_performed_by_tool"] is False
        held_path.write_text(
            successor_held.replace("Reason=JobHeldUser", "Reason=None"),
            encoding="utf-8",
        )
        _expect_value_error(
            lambda: r3_probe_lib.validate_held_r3_job_snapshot(
                execution_dir=successor.directory,
                control_root=repo_root,
                held_scontrol_input=held_path,
                job_id=job_id,
                job_name=job_name,
                test_mode=True,
            ),
            "pre-release held-state drift",
        )
    finally:
        r3_probe_lib.load_successor_execution = original_successor_loader
    live_mountpoint.mkdir(mode=0o700)
    restored_marker = live_mountpoint / r3_probe_lib.RETIREMENT_MARKER_NAME
    restored_marker.write_bytes(live_marker_bytes)
    restored_marker.chmod(0o400)
    accounting = write_terminal_accounting_input(
        output_dir=scratch / "accounting",
        job_id=job_id,
        job_name=job_name,
        sacct_text=sacct,
        squeue_text="",
        held_scontrol_text=held_scontrol,
    )
    accounting_value = validate_terminal_accounting_input(accounting)
    assert accounting_value["accepted_terminal"] is True
    assert accounting_value["scheduler_query_performed_by_tool"] is False
    output_root = scratch / "evidence"
    output_root.mkdir()
    original_successor_loader = r3_probe_lib.load_successor_execution

    def fixture_successor_loader(*args: object, **kwargs: object) -> object:
        kwargs["rejection_validator"] = v4_inputs["rejection_validator"]
        return original_successor_loader(*args, **kwargs)

    r3_probe_lib.load_successor_execution = fixture_successor_loader
    try:
        evidence = seal_r3_probe_evidence(
            raw_workspace=raw,
            terminal_accounting_dir=accounting,
            output_root=output_root,
            execution_dir=successor.directory,
            control_root=repo_root,
            test_mode=True,
        )
    finally:
        r3_probe_lib.load_successor_execution = original_successor_loader
    value = validate_r3_probe_evidence(evidence, allow_test_mode=True)
    assert value["accepted_compute_preflight_evidence"] is False
    assert value["test_mode"] is True
    assert evidence.name.endswith(value["evidence_hash"][:12])
    binding = successor_r3_preflight_binding(
        successor, evidence, allow_test_mode=True
    )
    assert successor_r3_preflight_binding(
        successor,
        evidence,
        allow_test_mode=True,
        require_current_snapshot=True,
    )["execution_snapshot_sha256"] == binding["execution_snapshot_sha256"]
    unexplained = successor.directory / "finalized" / "unexplained-proposal.json"
    unexplained.write_text("{}\n", encoding="utf-8")
    _expect_value_error(
        lambda: successor_r3_preflight_binding(
            successor,
            evidence,
            allow_test_mode=True,
            require_current_snapshot=True,
        ),
        "pre-intent successor snapshot pollution",
    )
    unexplained.unlink()
    assert binding["job_id"] == job_id
    assert binding["evidence_hash"] == value["evidence_hash"]
    assert binding["execution_snapshot_sha256"] == (
        json.loads((raw / "probe_result.json").read_text(encoding="utf-8"))[
            "execution_snapshot_before_sha256"
        ]
    )
    _expect_value_error(
        lambda: seal_r3_probe_evidence(
            raw_workspace=raw,
            terminal_accounting_dir=accounting,
            output_root=output_root,
            execution_dir=successor.directory,
            control_root=repo_root,
            test_mode=True,
        ),
        "evidence overwrite",
    )

    tampered = scratch / "evidence-tampered"
    shutil.copytree(evidence, tampered)
    (tampered / "raw" / "stdout.txt").chmod(0o644)
    (tampered / "raw" / "stdout.txt").write_text("tampered\n", encoding="utf-8")
    _expect_value_error(
        lambda: validate_r3_probe_evidence(tampered, allow_test_mode=True),
        "recursive evidence tamper",
    )

    _expect_value_error(
        lambda: write_terminal_accounting_input(
            output_dir=scratch / "failed-accounting",
            job_id=job_id,
            job_name=job_name,
            sacct_text=sacct.replace("COMPLETED|0:0", "FAILED|1:0", 1),
            squeue_text="",
            held_scontrol_text=held_scontrol,
        ),
        "failed terminal job",
    )
    _expect_value_error(
        lambda: write_terminal_accounting_input(
            output_dir=scratch / "active-accounting",
            job_id=job_id,
            job_name=job_name,
            sacct_text=sacct,
            squeue_text=f"{job_id}|{job_name}|RUNNING\n",
            held_scontrol_text=held_scontrol,
        ),
        "active job",
    )
    _expect_value_error(
        lambda: write_terminal_accounting_input(
            output_dir=scratch / "unheld-accounting",
            job_id=job_id,
            job_name=job_name,
            sacct_text=sacct,
            squeue_text="",
            held_scontrol_text=held_scontrol.replace(
                "Reason=JobHeldUser", "Reason=None"
            ),
        ),
        "unverified held R3 job",
    )

    _expect_value_error(
        lambda: successor_lib.validate_successor_r2_boundary(
            successor, repo_root=repo_root
        ),
        "closed v4 can never return to the pristine pre-probe boundary",
    )


def _test_source_boundaries(repo_root: Path) -> None:
    worker = (repo_root / "hpc/osc/run_steel_module_production_phase2b_task.py").read_text(
        encoding="utf-8"
    )
    assert "build_production_apptainer_prefix" in worker
    assert "production_apptainer_host_environment" in worker
    assert '"hostfs,cwd"' not in worker
    for name in (
        "run_steel_module_production_r3_container_probe.py",
        "validate_steel_module_production_r3_held_job.py",
        "freeze_steel_module_production_r3_probe_accounting.py",
        "seal_steel_module_production_r3_probe_evidence.py",
        "generate_steel_module_production_successor_readiness.py",
    ):
        text = (repo_root / "hpc/osc" / name).read_text(encoding="utf-8")
        assert "sbatch" not in text
        assert '["scontrol"' not in text and "['scontrol'" not in text
    launcher_path = repo_root / "hpc/osc/run_steel_module_production_r3_probe.sbatch"
    assert launcher_path.stat().st_mode & 0o111
    launcher = launcher_path.read_text(encoding="utf-8")
    assert "BASH_SOURCE" not in launcher
    assert "PYTHONPATH" not in launcher
    assert "/usr/bin/env -i" in launcher
    assert '[[ "$#" -eq 0 ]]' in launcher
    assert "steel-module-production-bc-s1-execution-v4" in launcher
    assert "run_steel_module_production_r3_container_probe.py" in launcher


def _test_spool_copy_safe_launcher(
    repo_root: Path,
    scratch: Path,
    *,
    canonical_name: str = "steel-module-production-bc-s1-execution-v4",
    launcher_name: str = "run_steel_module_production_r3_probe.sbatch",
) -> None:
    """Prove the Slurm-copied shell trampoline does not follow its own path."""

    scratch.mkdir(parents=True)
    scratch = scratch.resolve()
    work_root = scratch / "users/PAS2524/fixture/g4optics-rn"
    execution = work_root / "campaigns" / canonical_name
    control = execution / "sources/control/hpc/osc"
    raw_parent = work_root / "evidence/steel-module-production-r3/raw"
    control.mkdir(parents=True)
    raw_parent.mkdir(parents=True)
    execution_hash = "a1b2c3d4e5f6" + "7" * 52
    job_name = "g4sm-r3-" + execution_hash[:12]
    (execution / "managed_execution.json").write_text(
        json.dumps(
            {
                "execution_hash": execution_hash,
                "recovery_authority": {
                    "predecessor_execution": {"execution_hash": "f" * 64},
                    "failed_r3_preflight": {
                        "execution": {"execution_hash": "e" * 64}
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    frozen_runner = control / "run_steel_module_production_r3_container_probe.py"
    frozen_runner.write_text(
        "raise SystemExit('fake python must intercept')\n", encoding="utf-8"
    )

    spool = scratch / "var/spool/slurmd/job70000123"
    spool.mkdir(parents=True)
    launcher_source = repo_root / "hpc/osc" / launcher_name
    launcher = spool / "slurm_script"
    shutil.copyfile(launcher_source, launcher)
    launcher.chmod(0o755)

    fake_bin = scratch / "fake-bin"
    fake_bin.mkdir()
    capture = scratch / "python-invocation.txt"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = -I ]; then exec /usr/bin/python3 \"$@\"; fi\n"
        "{\n"
        "  printf 'argc=%s\\n' \"$#\"\n"
        "  index=0\n"
        "  for value in \"$@\"; do printf 'arg%s=%s\\n' \"$index\" \"$value\"; index=$((index + 1)); done\n"
        "  /usr/bin/env | /usr/bin/sort\n"
        f"}} > {shlex.quote(str(capture))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_apptainer = fake_bin / "apptainer"
    apptainer_invoked = scratch / "forbidden-apptainer-was-invoked"
    fake_apptainer.write_text(
        "#!/bin/sh\n"
        f"printf invoked > {shlex.quote(str(apptainer_invoked))}\n"
        "exit 99\n",
        encoding="utf-8",
    )
    fake_apptainer.chmod(0o755)

    scheduler_environment = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "PYTHONPATH": "/forbidden/import/override",
        "APPTAINER_BIND": "/forbidden/bind",
        "UNRELATED_INHERITED_SECRET": "must-not-cross-exec",
        "SLURM_JOB_ID": "70000123",
        "SLURM_JOB_NAME": job_name,
        "SLURMD_NODENAME": "fixture-compute-01",
        "SLURM_SUBMIT_DIR": str(scratch / "submit-origin"),
    }
    result = subprocess.run(
        [str(launcher)],
        cwd=execution,
        env=scheduler_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    invocation = capture.read_text(encoding="utf-8")
    expected_workspace = raw_parent / job_name
    expected_arguments = (
        str(frozen_runner),
        "--execution-dir",
        str(execution),
        "--workspace",
        str(expected_workspace),
    )
    assert "argc=5\n" in invocation
    for index, value in enumerate(expected_arguments):
        assert f"arg{index}={value}\n" in invocation
    assert f"HOME={work_root.parent}\n" in invocation
    assert f"SLURM_JOB_ID=70000123\n" in invocation
    assert f"SLURM_JOB_NAME={job_name}\n" in invocation
    assert "SLURM_SUBMIT_DIR=" not in invocation
    assert "PYTHONPATH=" not in invocation
    assert "APPTAINER_BIND=" not in invocation
    assert "UNRELATED_INHERITED_SECRET=" not in invocation
    assert str(spool) not in invocation
    assert not apptainer_invoked.exists()

    # The copied launcher must preserve the frozen runner's nonzero exit code.
    # This prevents a failed probe from being mistaken for COMPLETED / 0:0.
    fake_python.write_text(fake_python.read_text(encoding="utf-8") + "exit 1\n")
    propagated = subprocess.run(
        [str(launcher)],
        cwd=execution,
        env=scheduler_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert propagated.returncode == 1
    assert not apptainer_invoked.exists()

    valid_manifest = (execution / "managed_execution.json").read_text(
        encoding="utf-8"
    )
    capture.unlink()
    (execution / "managed_execution.json").write_text(
        json.dumps(
            {"recovery_authority": {"execution_hash": execution_hash}},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    missing_top_level = subprocess.run(
        [str(launcher)],
        cwd=execution,
        env=scheduler_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert missing_top_level.returncode == 2
    assert not capture.exists()
    (execution / "managed_execution.json").write_text(
        valid_manifest, encoding="utf-8"
    )

    # This reproduces the original failure mode: Slurm copies a directly
    # submitted Python script to its spool, so sibling imports and __file__-
    # relative control-root discovery both lose the frozen source directory.
    copied_python = spool / "direct-python-slurm-script"
    shutil.copyfile(
        repo_root / "hpc/osc/run_steel_module_production_r3_container_probe.py",
        copied_python,
    )
    direct = subprocess.run(
        [sys.executable, str(copied_python), "--help"],
        cwd=execution,
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert direct.returncode != 0
    assert "ModuleNotFoundError" in direct.stderr
    assert "steel_module_production_r3_probe_lib" in direct.stderr

    wrong_workdir = execution.with_name("wrong")
    wrong_workdir.mkdir()
    rejected_cases = (
        (
            {
                **scheduler_environment,
                "SLURM_JOB_NAME": "g4sm-r3-000000000000",
            },
            execution,
            "job-name drift",
        ),
        (
            {**scheduler_environment, "SLURM_ARRAY_JOB_ID": "70000123"},
            execution,
            "array job",
        ),
        (
            scheduler_environment,
            wrong_workdir,
            "workdir drift",
        ),
    )
    for environment, workdir, label in rejected_cases:
        rejected = subprocess.run(
            [str(launcher)],
            cwd=workdir,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert rejected.returncode == 2, (label, rejected.stderr)
        assert not capture.exists(), label
    with_argument = subprocess.run(
        [str(launcher), str(execution)],
        cwd=execution,
        env=scheduler_environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert with_argument.returncode == 2
    assert not capture.exists()
    assert not apptainer_invoked.exists()


def _test_r4_git_gate(scratch: Path) -> None:
    scratch.mkdir()

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=scratch,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.name", "R4 Fixture")
    git("config", "user.email", "r4-fixture@example.invalid")
    (scratch / "frozen.txt").write_text("R2\n", encoding="utf-8")
    git("add", "frozen.txt")
    git("commit", "-q", "-m", "R2")
    control_commit = git("rev-parse", "HEAD")
    control_tree = git("rev-parse", "HEAD^{tree}")
    lock = scratch / RECOVERY_READINESS_LOCK_RELATIVE_V3
    lock.parent.mkdir(parents=True)
    lock.write_text("{}\n", encoding="utf-8")
    lock.chmod(0o644)
    git("add", RECOVERY_READINESS_LOCK_RELATIVE_V3.as_posix())
    git("commit", "-q", "-m", "R4 lock only")
    _verify_r4_git_gate(
        path=lock,
        live_root=scratch,
        control_commit=control_commit,
        source={"git_tree": control_tree},
        relative=RECOVERY_READINESS_LOCK_RELATIVE_V3,
    )
    dirty = scratch / "untracked"
    dirty.write_text("dirty\n", encoding="utf-8")
    _expect_value_error(
        lambda: _verify_r4_git_gate(
            path=lock,
            live_root=scratch,
            control_commit=control_commit,
            source={"git_tree": control_tree},
            relative=RECOVERY_READINESS_LOCK_RELATIVE_V3,
        ),
        "dirty R4 checkout",
    )
    dirty.unlink()
    (scratch / "extra.txt").write_text("extra\n", encoding="utf-8")
    git("add", "extra.txt")
    git("commit", "-q", "-m", "forbidden extra R4 commit")
    _expect_value_error(
        lambda: _verify_r4_git_gate(
            path=lock,
            live_root=scratch,
            control_commit=control_commit,
            source={"git_tree": control_tree},
            relative=RECOVERY_READINESS_LOCK_RELATIVE_V3,
        ),
        "multi-commit R4 history",
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-r3-probe-check-") as raw:
        scratch = Path(raw)
        _test_environment_and_mount_contract(scratch / "contract")
        _test_writer_open_rejection_shell_contract(scratch / "writer-open")
        _test_mountpoint_lease_adversaries(scratch / "mountpoint-lifecycle")
        _test_active_probe_failure_cleanup(
            repo_root, scratch / "active-probe-lifecycle"
        )
        _test_accounting_and_evidence(repo_root, scratch / "evidence-case")
        _test_r4_git_gate(scratch / "r4-git")
        _test_source_boundaries(repo_root)
        _test_spool_copy_safe_launcher(repo_root, scratch / "spool-copy")
    print(
        "steel-module production R3 container probe: PASS "
        "(raw-v3 retained in-place closure, adversarial lifecycle, exact mount "
        "contract, terminal accounting, content-addressed test evidence)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
