#!/usr/bin/env python3
"""No-scheduler checks for the GPFS-lock-safe BC-S1 successor v5."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import steel_module_production_phase2b_lib as phase2b_lib
import steel_module_production_r3_probe_lib as r3_probe_lib
import steel_module_production_successor_lib as successor_lib
from check_steel_module_production_successor import (
    _fixture_r3_failure_identity,
    _make_v3_fixture,
    _scheduler_sentinels,
)
from check_steel_module_production_successor_v4 import _v4_inputs
from check_steel_module_production_r3_probe import (
    _test_spool_copy_safe_launcher,
    _write_raw_workspace,
)
from steel_module_campaign_lib import sha256_file
from steel_module_production_checkpoint_lib import write_recursive_checksums
from steel_module_production_successor_lib import (
    FORMAL_PREWORKSPACE_FAILED_R3_JOB_ID,
    FORMAL_SUCCESSOR_EXECUTION_NAME_V3,
    FORMAL_SUCCESSOR_EXECUTION_NAME_V5,
    SUCCESSOR_EXECUTION_GENERATION_V5,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5,
    load_successor_execution,
    materialize_successor_v3_execution_companion,
    materialize_successor_v4_execution_companion,
    materialize_successor_v5_execution_companion,
    preview_successor_v5_execution,
)


def _assert_rejected(callable_value, message: str) -> None:
    try:
        callable_value()
    except (OSError, RuntimeError, ValueError):
        return
    raise AssertionError(message)


def _make_preworkspace_failure(
    root: Path, predecessor: Any
) -> tuple[Path, dict[str, Any]]:
    root.mkdir()
    inputs = {
        "held_scontrol_sha256": "1" * 64,
        "sacct_sha256": "2" * 64,
        "squeue_sha256": "3" * 64,
        "slurm_output_sha256": "4" * 64,
    }
    payload: dict[str, Any] = {
        "schema_version": (
            "steel-module-production-r3-pre-workspace-failure-evidence-v1"
        ),
        "test_mode": True,
        "accepted_compute_preflight_evidence": False,
        "classification": "portable-exclusive-control-lock-open-failure",
        "failure_stage": "portable-exclusive-control-lock-before-workspace",
        "future_successor_required": True,
        "execution": {
            "directory": str(predecessor.directory),
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
        },
        "scheduler": {
            "job_id": FORMAL_PREWORKSPACE_FAILED_R3_JOB_ID,
            "job_name": "g4sm-r3-fixture-v4",
        },
        "runtime_boundary": {
            "portable_control_lock_operation": "exclusive",
            "workspace_created": False,
            "raw_workspace_created": False,
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "simulation_started": False,
        },
        "consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "inputs": inputs,
        "failure_id": "fixture-pre-workspace-failure",
        "failure_hash": "8" * 64,
    }
    (root / "failure.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (root / "source-evidence.txt").write_text(
        "fixture scheduler evidence\n", encoding="utf-8"
    )
    write_recursive_checksums(root)
    return root, payload


def _failure_validator(payload: dict[str, Any]):
    def validate(
        bundle_dir: Path,
        predecessor: Any,
        test_mode: bool,
        repo_root: Path | None,
    ) -> dict[str, Any]:
        assert bundle_dir.is_dir()
        assert predecessor.directory == Path(payload["execution"]["directory"])
        assert predecessor.execution_id == payload["execution"]["execution_id"]
        assert predecessor.execution_hash == payload["execution"]["execution_hash"]
        assert test_mode is True
        assert repo_root is None or repo_root.is_dir()
        return json.loads(json.dumps(payload))

    return validate


def _make_v5_fixture(
    repo_root: Path, scratch: Path
) -> tuple[Any, Any, Path, dict[str, object]]:
    lineage = scratch / "lineage"
    lineage.mkdir()
    _, v2, _, v3_inputs = _make_v3_fixture(repo_root, lineage)
    v3_target = (
        lineage / "work/campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME_V3
    )
    with _fixture_r3_failure_identity(v2):
        v3 = materialize_successor_v3_execution_companion(
            **v3_inputs, out_dir=v3_target
        )
        v4_inputs, _, _, _ = _v4_inputs(repo_root, scratch, v3, v3_inputs)
        v4 = materialize_successor_v4_execution_companion(
            **v4_inputs, out_dir=scratch / "successor-v4-execution"
        )
    failure, payload = _make_preworkspace_failure(
        scratch / "pre-workspace-failure", v4
    )
    source_root = scratch / "successor-v5-source-inputs"
    simulation = Path(v4_inputs["fixture_sources"][0])
    control = source_root / "control-source"
    shutil.copytree(Path(v4_inputs["fixture_sources"][1]), control)
    launcher = control / "hpc/osc/run_steel_module_production_r3_probe_v5.sbatch"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        repo_root / "hpc/osc/run_steel_module_production_r3_probe_v5.sbatch",
        launcher,
    )
    launcher.chmod(0o755)
    (control / "successor-v5-marker.txt").write_text(
        "updated v5 control plane\n", encoding="utf-8"
    )
    inputs: dict[str, object] = {
        "repo_root": repo_root,
        "managed_child_dir": v4.managed_child.directory,
        "predecessor_dir": v4.directory,
        "preworkspace_failure_dir": failure,
        "pilot_dir": Path(v4.manifest["sealed_pilot"]["directory"]),
        "test_mode": True,
        "fixture_sources": (simulation, control),
        "rejection_validator": v4_inputs["rejection_validator"],
        "preworkspace_failure_validator": _failure_validator(payload),
    }
    return v4, payload, failure, inputs


def test_v5_materialization(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    v4, failure_payload, failure, inputs = _make_v5_fixture(repo_root, scratch)
    failure_before = sha256_file(failure / "SHA256SUMS")
    preview = preview_successor_v5_execution(**inputs)
    assert preview.predecessor.execution_hash == v4.execution_hash
    assert preview.recovery_authority["preworkspace_r3_failure"][
        "failure_hash"
    ] == failure_payload["failure_hash"]
    target_parent = scratch / "active-work/campaigns"
    target_parent.mkdir(parents=True)
    target = target_parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V5
    v5 = materialize_successor_v5_execution_companion(**inputs, out_dir=target)
    assert v5.manifest["schema_version"] == SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5
    assert v5.manifest["execution_generation"] == SUCCESSOR_EXECUTION_GENERATION_V5
    assert v5.manifest["accepted_statistical_evidence"] is False
    assert v5.tasks == v4.tasks
    assert v5.scan_args == v4.scan_args
    assert v5.manifest["runtime"] == v4.manifest["runtime"]
    assert v5.manifest["sources"]["simulation"] == v4.manifest["sources"][
        "simulation"
    ]
    lock = v5.directory / ".control.lock"
    assert lock.stat().st_mode & 0o777 == 0o600
    assert v5.manifest["artifacts"]["control_lock"]["mode"] == 0o600
    with phase2b_lib.execution_lock(v5):
        assert lock.stat().st_mode & 0o777 == 0o600
    for name in ("intents", "attempts", "finalized"):
        assert not any((v5.directory / name).iterdir())
    loaded = load_successor_execution(
        v5.directory,
        allow_test_mode=True,
        verify_live_predecessor=True,
        rejection_validator=inputs["rejection_validator"],
        preworkspace_failure_validator=inputs[
            "preworkspace_failure_validator"
        ],
    )
    assert loaded.execution_hash == v5.execution_hash
    successor_lib.validate_successor_r2_boundary(v5, repo_root=repo_root)
    assert sha256_file(failure / "SHA256SUMS") == failure_before
    _assert_rejected(
        lambda: materialize_successor_v5_execution_companion(
            **inputs, out_dir=target
        ),
        "successor-v5 materializer overwrote an existing target",
    )
    assert FORMAL_SUCCESSOR_EXECUTION_NAME_V5.endswith("execution-v5")
    launcher = v5.directory / successor_lib.R3_COPY_SAFE_LAUNCHER_RELATIVE_V5
    assert launcher.stat().st_mode & 0o777 == 0o555
    assert "execution-v5" in launcher.read_text(encoding="utf-8")
    held_job_id = "70000555"
    held_job_name = r3_probe_lib.expected_r3_job_name(v5.execution_hash)
    held_text = (
        f"JobId={held_job_id} JobName={held_job_name} Account=pas2524 "
        "JobState=PENDING Reason=JobHeldUser Requeue=0 "
        f"Command={launcher} WorkDir={v5.directory} "
        f"StdOut={r3_probe_lib.canonical_r3_evidence_root(v5.directory)}/"
        f"slurm-{held_job_id}.out\n"
    )
    held_identity = r3_probe_lib._held_scheduler_identity(
        held_text,
        job_id=held_job_id,
        job_name=held_job_name,
        formal_execution_dir=v5.directory,
    )
    successor_lib._validate_formal_r3_held_job_paths(
        v5.directory,
        {"job_id": held_job_id, "held_scheduler": held_identity},
    )
    for field in ("command", "work_dir", "stdout"):
        altered = dict(held_identity)
        altered[field] = "/fixture/wrong-" + field
        _assert_rejected(
            lambda altered=altered: successor_lib._validate_formal_r3_held_job_paths(
                v5.directory,
                {"job_id": held_job_id, "held_scheduler": altered},
            ),
            f"formal R3 path verifier accepted wrong {field}",
        )
    (failure / "source-evidence.txt").write_text(
        "tampered fixture scheduler evidence\n", encoding="utf-8"
    )
    _assert_rejected(
        lambda: load_successor_execution(
            v5.directory,
            allow_test_mode=True,
            verify_live_predecessor=True,
            rejection_validator=inputs["rejection_validator"],
            preworkspace_failure_validator=inputs[
                "preworkspace_failure_validator"
            ],
        ),
        "successor-v5 accepted a tampered pre-workspace failure bundle",
    )


def test_v5_fault_cleanup_and_no_scheduler(
    repo_root: Path, scratch: Path
) -> None:
    scratch.mkdir()
    _, _, _, inputs = _make_v5_fixture(repo_root, scratch)
    _, markers, original_path = _scheduler_sentinels(scratch)
    try:
        for fault in ("tasks", "sources", "preworkspace-failure", "pre-publish"):
            target = scratch / f"fault-{fault}"
            _assert_rejected(
                lambda fault=fault, target=target: (
                    materialize_successor_v5_execution_companion(
                        **inputs, out_dir=target, fault_after=fault
                    )
                ),
                f"successor-v5 fault injection did not fail: {fault}",
            )
            assert not target.exists()
            assert not tuple(target.parent.glob(f".{target.name}.tmp-*"))
    finally:
        os.environ["PATH"] = original_path
    assert all(not marker.exists() for marker in markers.values())


def test_v5_success_evidence_chain(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    _, _, _, inputs = _make_v5_fixture(repo_root, scratch)
    active_parent = scratch / "active-work/campaigns"
    active_parent.mkdir(parents=True)
    successor = materialize_successor_v5_execution_companion(
        **inputs,
        out_dir=active_parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V5,
    )
    raw = _write_raw_workspace(scratch / "raw", successor)
    job_id = "70000123"
    job_name = r3_probe_lib.expected_r3_job_name(successor.execution_hash)
    sacct = (
        f"{job_id}|{job_name}|pas2524|COMPLETED|0:0|9\n"
        f"{job_id}.batch|batch|pas2524|COMPLETED|0:0|9\n"
        f"{job_id}.extern|extern|pas2524|COMPLETED|0:0|9\n"
    )
    held = (
        f"JobId={job_id} JobName={job_name} Account=pas2524 "
        "JobState=PENDING Reason=JobHeldUser Requeue=0 "
        "Command=/fixture/v5-launcher WorkDir=/fixture/v5 "
        f"StdOut=/fixture/slurm-{job_id}.out\n"
    )
    accounting_parent = scratch / "accounting"
    accounting_parent.mkdir()
    accounting_root = accounting_parent / job_id
    accounting = r3_probe_lib.write_terminal_accounting_input(
        output_dir=accounting_root,
        job_id=job_id,
        job_name=job_name,
        sacct_text=sacct,
        squeue_text="",
        held_scontrol_text=held,
    )
    evidence_root = scratch / "evidence"
    evidence_root.mkdir()
    with mock.patch.object(
        r3_probe_lib, "load_successor_execution", return_value=successor
    ):
        evidence = r3_probe_lib.seal_r3_probe_evidence(
            raw_workspace=raw,
            terminal_accounting_dir=accounting,
            output_root=evidence_root,
            execution_dir=successor.directory,
            control_root=repo_root,
            test_mode=True,
        )
    validated = r3_probe_lib.validate_r3_probe_evidence(
        evidence, allow_test_mode=True
    )
    assert validated["execution_id"] == successor.execution_id
    assert validated["accepted_compute_preflight_evidence"] is False
    assert validated["geant4_invoked"] is False


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="steel-module-successor-v5-check-"
    ) as raw:
        scratch = Path(raw)
        test_v5_materialization(repo_root, scratch / "materialization")
        test_v5_fault_cleanup_and_no_scheduler(repo_root, scratch / "faults")
        _test_spool_copy_safe_launcher(
            repo_root,
            scratch / "spool-copy",
            canonical_name=FORMAL_SUCCESSOR_EXECUTION_NAME_V5,
            launcher_name="run_steel_module_production_r3_probe_v5.sbatch",
        )
        test_v5_success_evidence_chain(repo_root, scratch / "evidence")
    print("steel-module production successor v5: PASS")
    print("scheduler: forbidden command sentinels; real Slurm calls: 0")
    print("control lock: 0600 + O_RDWR exclusive GPFS contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
