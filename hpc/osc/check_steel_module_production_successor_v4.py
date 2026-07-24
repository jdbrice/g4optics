#!/usr/bin/env python3
"""No-scheduler checks for the rejected-probe-bound BC-S1 successor v4."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import steel_module_production_phase2b_lib as phase2b_lib
import steel_module_production_r3_probe_lib as probe_lib
import steel_module_production_successor_lib as successor_lib
from check_steel_module_production_successor import (
    _assert_no_scheduler_contact,
    _fixture_r3_failure_identity,
    _make_v3_fixture,
    _scheduler_sentinels,
    _tree_snapshot,
)
from steel_module_campaign_lib import sha256_file
from steel_module_production_checkpoint_lib import write_recursive_checksums
from steel_module_production_successor_lib import (
    FORMAL_ADMIN_REJECTED_R3_JOB_ID,
    FORMAL_REJECTED_R3_JOB_ID,
    FORMAL_SUCCESSOR_EXECUTION_NAME_V3,
    RECOVERY_READINESS_LOCK_RELATIVE,
    SUCCESSOR_EXECUTION_GENERATION,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3,
    build_successor_recovery_readiness_candidate,
    load_execution_for_frozen_worker,
    load_successor_execution,
    materialize_successor_v3_execution_companion,
    materialize_successor_v4_execution_companion,
    preview_successor_v4_execution,
)


def _make_rejection_fixture(
    root: Path, predecessor: Any
) -> tuple[Path, dict[str, Any]]:
    root.mkdir()
    probe_token = "1" * 32
    mountpoint = predecessor.directory / "attempts" / (
        ".r3-probe-" + probe_token
    )
    mountpoint.mkdir(mode=0o755)
    container_probe_root = (
        "/work/g4optics-execution/attempts/.r3-probe-" + probe_token
    )
    payload: dict[str, Any] = {
        "schema_version": (
            "steel-module-production-r3-container-probe-rejection-evidence-v1"
        ),
        "test_mode": True,
        "accepted_compute_preflight_evidence": False,
        "classification": "container-isolation-writer-open-rejection-failure",
        "execution": {
            "directory": str(predecessor.directory),
            "execution_id": predecessor.execution_id,
            "execution_hash": predecessor.execution_hash,
        },
        "scheduler": {
            "job_id": FORMAL_REJECTED_R3_JOB_ID,
            "job_name": "fixture-rejected-r3",
        },
        "probe": {
            "probe_token": probe_token,
            "container_probe_root": container_probe_root,
            "historical_mountpoint": {
                "relative_path": f"attempts/{mountpoint.name}",
                "probe_token": probe_token,
                "container_probe_root": container_probe_root,
                "mode": 0o755,
                "link_count": 2,
                "empty": True,
                "attempts_entry_count": 1,
            },
            "false_report_keys": [
                "static_writer_open_rejected",
                "control_lock_writer_open_rejected",
            ],
            "container_return_code": 0,
            "apptainer_invoked": True,
            "geant4_invoked": False,
            "raw_file_snapshot_scope": "recursive-regular-files-only",
            "raw_file_snapshot_unchanged": True,
            "historical_mountpoint_present": True,
            "execution_tree_unchanged": False,
        },
        "consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "rejection_id": "fixture-r3-probe-rejection",
        "rejection_hash": "7" * 64,
    }
    (root / "rejection.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    write_recursive_checksums(root)
    return root, payload


def _validator_for(payload: dict[str, Any], expected_repo_root: Path):
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
        # Test-mode staging loads intentionally have no live-repository
        # authority.  Preview uses the supplied root; the dedicated
        # formal-shaped test below requires it exactly.
        assert repo_root in {None, expected_repo_root.resolve()}
        return json.loads(json.dumps(payload))

    return validate


def _v4_inputs(
    repo_root: Path,
    scratch: Path,
    v3: Any,
    v3_inputs: dict[str, object],
) -> tuple[dict[str, object], Path, Path, dict[str, Any]]:
    rejection, payload = _make_rejection_fixture(
        scratch / "rejected-r3-probe", v3
    )
    administrative = scratch / "rejected-launch-50547698.sha256"
    administrative.write_text(
        "fixture administrative rejection; not compute evidence\n",
        encoding="utf-8",
    )
    source_root = scratch / "successor-v4-source-inputs"
    simulation = Path(v3_inputs["fixture_sources"][0])
    control = source_root / "control-source"
    shutil.copytree(Path(v3_inputs["fixture_sources"][1]), control)
    (control / "successor-v4-marker.txt").write_text(
        "updated v4 control plane\n", encoding="utf-8"
    )
    inputs: dict[str, object] = {
        "repo_root": repo_root,
        "managed_child_dir": v3.managed_child.directory,
        "predecessor_dir": v3.directory,
        "rejection_dir": rejection,
        "administrative_rejection_manifest": administrative,
        "pilot_dir": Path(v3.manifest["sealed_pilot"]["directory"]),
        "test_mode": True,
        "fixture_sources": (simulation, control),
        "rejection_validator": _validator_for(payload, repo_root),
    }
    return inputs, rejection, administrative, payload


def _assert_rejected(callable_value, message: str) -> None:
    try:
        callable_value()
    except (OSError, RuntimeError, ValueError):
        return
    raise AssertionError(message)


def test_v4_materialization_and_lineage(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
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
        inputs, rejection, administrative, payload = _v4_inputs(
            repo_root, scratch, v3, v3_inputs
        )
        before_v3 = _tree_snapshot(v3.directory)
        before_rejection = _tree_snapshot(rejection)
        preview = preview_successor_v4_execution(**inputs)
        assert preview.predecessor.execution_hash == v3.execution_hash
        assert preview.recovery_authority["administrative_r3_launch_rejection"][
            "job_id"
        ] == FORMAL_ADMIN_REJECTED_R3_JOB_ID
        assert preview.recovery_authority["rejected_r3_preflight"][
            "scheduler"
        ]["job_id"] == FORMAL_REJECTED_R3_JOB_ID

        v4 = materialize_successor_v4_execution_companion(
            **inputs, out_dir=scratch / "successor-v4-execution"
        )
        assert v4.manifest["schema_version"] == SUCCESSOR_EXECUTION_SCHEMA_VERSION
        assert v4.manifest["execution_generation"] == SUCCESSOR_EXECUTION_GENERATION
        assert v4.manifest["accepted_statistical_evidence"] is False
        assert v4.tasks == v3.tasks
        assert v4.scan_args == v3.scan_args
        assert v4.manifest["runtime"] == v3.manifest["runtime"]
        assert v4.manifest["sources"]["simulation"] == v3.manifest["sources"][
            "simulation"
        ]
        assert v4.manifest["sources"]["control_plane"] != v3.manifest["sources"][
            "control_plane"
        ]
        assert v4.manifest["artifacts"]["control_lock"]["content_token"] != (
            v3.manifest["artifacts"]["control_lock"]["content_token"]
        )
        authority = v4.manifest["recovery_authority"]
        assert authority["predecessor_execution"]["execution_id"] == v3.execution_id
        assert authority["predecessor_execution"]["schema_version"] == (
            SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3
        )
        assert authority["predecessor_recovery_authority"] == v3.manifest[
            "recovery_authority"
        ]
        assert authority["administrative_r3_launch_rejection"]["job_id"] == (
            FORMAL_ADMIN_REJECTED_R3_JOB_ID
        )
        assert authority["rejected_r3_preflight"]["rejection_hash"] == payload[
            "rejection_hash"
        ]
        assert authority["retry_equivalence"]["all_equal"] is True
        for name in ("intents", "attempts", "finalized"):
            assert not any((v4.directory / name).iterdir())
        assert _tree_snapshot(v3.directory) == before_v3
        assert _tree_snapshot(rejection) == before_rejection

        loaded = load_successor_execution(
            v4.directory,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
            rejection_validator=inputs["rejection_validator"],
        )
        assert loaded.execution_hash == v4.execution_hash
        successor_lib.validate_successor_r2_boundary(v4, repo_root=repo_root)
        active_orphan = v4.directory / "attempts" / (
            ".r3-probe-" + "3" * 32
        )
        active_orphan.mkdir()
        try:
            _assert_rejected(
                lambda: successor_lib.validate_successor_r2_boundary(
                    v4, repo_root=repo_root
                ),
                "normal successor-v4 boundary accepted a mountpoint exception",
            )
        finally:
            active_orphan.rmdir()

        predecessor_mountpoint = v3.directory / "attempts" / (
            ".r3-probe-" + "1" * 32
        )
        predecessor_mountpoint.chmod(0o700)
        try:
            _assert_rejected(
                lambda: load_successor_execution(
                    v4.directory,
                    allow_test_mode=True,
                    require_readiness=False,
                    verify_live_predecessor=True,
                    rejection_validator=inputs["rejection_validator"],
                ),
                "successor-v4 authority accepted changed predecessor mountpoint",
            )
        finally:
            predecessor_mountpoint.chmod(0o755)
        assert load_successor_execution(
            v3.directory,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        ).execution_hash == v3.execution_hash
        _assert_rejected(
            lambda: load_successor_execution(
                v3.directory,
                allow_test_mode=True,
                require_readiness=True,
            ),
            "closed successor-v3 was accepted for readiness",
        )
        _assert_rejected(
            lambda: load_execution_for_frozen_worker(
                v3.directory, control_root=v3.directory / "sources/control"
            ),
            "closed successor-v3 was accepted as a frozen worker",
        )

        administrative.write_text("tampered\n", encoding="utf-8")
        _assert_rejected(
            lambda: load_successor_execution(
                v4.directory,
                allow_test_mode=True,
                rejection_validator=inputs["rejection_validator"],
            ),
            "successor-v4 accepted administrative-rejection tamper",
        )


def test_v4_r4_job_lineage(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    lineage = scratch / "lineage"
    lineage.mkdir()
    _, v2, _, v3_inputs = _make_v3_fixture(repo_root, lineage)
    with _fixture_r3_failure_identity(v2):
        v3 = materialize_successor_v3_execution_companion(
            **v3_inputs,
            out_dir=(
                lineage / "work/campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME_V3
            ),
        )
        inputs, _, _, _ = _v4_inputs(repo_root, scratch, v3, v3_inputs)
        v4 = materialize_successor_v4_execution_companion(
            **inputs, out_dir=scratch / "successor-v4-execution"
        )
    manifest = json.loads(json.dumps(v4.manifest))
    manifest["test_mode"] = False
    manifest["accepted_statistical_evidence"] = True
    formal_view = phase2b_lib.ManagedExecution(
        v4.directory,
        v4.managed_child,
        manifest,
        v4.tasks,
        v4.scan_args,
    )
    evidence = scratch / "accepted-r3-evidence"
    evidence.mkdir()
    accepted_job = "70000992"
    original = successor_lib.successor_r3_preflight_binding

    def fixture_binding(
        execution: Any,
        evidence_dir: Path,
        *,
        allow_test_mode: bool = False,
        require_current_snapshot: bool = False,
    ) -> dict[str, str]:
        assert execution is formal_view
        assert evidence_dir.resolve() == evidence.resolve()
        assert allow_test_mode is False
        assert require_current_snapshot is True
        return {"directory": str(evidence.resolve()), "job_id": accepted_job}

    successor_lib.successor_r3_preflight_binding = fixture_binding
    try:
        candidate = build_successor_recovery_readiness_candidate(
            formal_view, preflight_evidence_dir=evidence
        )
    finally:
        successor_lib.successor_r3_preflight_binding = original
    assert candidate["failed_compute_preflight_slurm_job_ids"] == [
        "50544247",
        FORMAL_REJECTED_R3_JOB_ID,
    ]
    assert candidate["compute_preflight_slurm_job_ids"] == [accepted_job]
    assert candidate["all_compute_preflight_slurm_job_ids"] == [
        "50544247",
        FORMAL_REJECTED_R3_JOB_ID,
        accepted_job,
    ]
    assert FORMAL_ADMIN_REJECTED_R3_JOB_ID not in candidate[
        "all_compute_preflight_slurm_job_ids"
    ]
    assert candidate["successor_execution"]["execution_id"] == v4.execution_id
    assert candidate["schema_version"].endswith("readiness-lock-v3")
    assert candidate["recovery_authority"]["rejected_r3_hash"] == (
        v4.manifest["recovery_authority"]["rejected_r3_preflight"][
            "rejection_hash"
        ]
    )
    assert v4.manifest["readiness_gate"]["tracked_path"] == (
        RECOVERY_READINESS_LOCK_RELATIVE.as_posix()
    )


def test_formal_rejection_rejects_custom_validator(
    repo_root: Path, scratch: Path
) -> None:
    """A formal rejection must never admit a fixture validator override."""

    scratch.mkdir()
    lineage = scratch / "lineage"
    lineage.mkdir()
    _, v2, _, v3_inputs = _make_v3_fixture(repo_root, lineage)
    with _fixture_r3_failure_identity(v2):
        v3 = materialize_successor_v3_execution_companion(
            **v3_inputs,
            out_dir=(
                lineage / "work/campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME_V3
            ),
        )
    rejection, payload = _make_rejection_fixture(
        scratch / "formal-shaped-rejection", v3
    )
    payload["test_mode"] = False
    calls: list[tuple[Path, Any, bool, Path | None]] = []

    def validator(
        path: Path,
        predecessor: Any,
        test_mode: bool,
        live_root: Path | None,
    ) -> dict[str, Any]:
        calls.append((path, predecessor, test_mode, live_root))
        return payload

    _assert_rejected(
        lambda: successor_lib._validated_r3_rejection(
            rejection,
            predecessor=v3,
            test_mode=False,
            repo_root=repo_root.resolve(),
            validator=validator,
        ),
        "formal R3 rejection accepted a custom validator",
    )
    assert calls == []


def test_success_sealer_rejects_closed_v3(scratch: Path) -> None:
    """A historical v3 raw success can never become accepted evidence."""

    scratch.mkdir()
    original_common = probe_lib._validate_raw_probe_workspace_common
    probe_lib._validate_raw_probe_workspace_common = lambda *args, **kwargs: {
        "test_mode": True,
        "accepted_compute_preflight_evidence": False,
        "probe_passed": True,
        "execution_schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3,
        "container_report": {name: True for name in probe_lib.REPORT_KEYS},
    }
    try:
        _assert_rejected(
            lambda: probe_lib.validate_raw_probe_workspace(
                scratch / "raw-v3", allow_test_mode=True
            ),
            "successful raw validator accepted execution-v3",
        )
    finally:
        probe_lib._validate_raw_probe_workspace_common = original_common

    sentinel_execution = type(
        "Execution",
        (),
        {
            "manifest": {"schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3},
            "directory": scratch / "execution-v3",
        },
    )()
    originals = (
        probe_lib.validate_raw_probe_workspace,
        probe_lib.validate_terminal_accounting_input,
        probe_lib.load_successor_execution,
        probe_lib.validate_successor_r2_boundary,
    )
    boundary_calls: list[object] = []
    probe_lib.validate_raw_probe_workspace = lambda *args, **kwargs: {}
    probe_lib.validate_terminal_accounting_input = lambda *args, **kwargs: {}
    probe_lib.load_successor_execution = lambda *args, **kwargs: sentinel_execution
    probe_lib.validate_successor_r2_boundary = (
        lambda *args, **kwargs: boundary_calls.append((args, kwargs))
    )
    try:
        _assert_rejected(
            lambda: probe_lib.seal_r3_probe_evidence(
                raw_workspace=scratch / "raw",
                terminal_accounting_dir=scratch / "accounting",
                output_root=scratch / "evidence",
                execution_dir=sentinel_execution.directory,
                control_root=scratch / "control",
                test_mode=True,
            ),
            "success sealer accepted permanently closed execution-v3",
        )
    finally:
        (
            probe_lib.validate_raw_probe_workspace,
            probe_lib.validate_terminal_accounting_input,
            probe_lib.load_successor_execution,
            probe_lib.validate_successor_r2_boundary,
        ) = originals
    assert boundary_calls == []


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(
        prefix="steel-module-production-successor-v4-"
    ) as temporary:
        scratch = Path(temporary)
        _, markers, original_path = _scheduler_sentinels(scratch)
        try:
            test_v4_materialization_and_lineage(repo_root, scratch / "materialize")
            test_v4_r4_job_lineage(repo_root, scratch / "readiness")
            test_formal_rejection_rejects_custom_validator(
                repo_root, scratch / "formal-root"
            )
            test_success_sealer_rejects_closed_v3(scratch / "success-sealer")
            _assert_no_scheduler_contact(markers)
        finally:
            os.environ["PATH"] = original_path
    print("steel-module production successor v4: PASS")
    print("scheduler: forbidden command sentinels; real Slurm calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
