#!/usr/bin/env python3
"""No-Slurm adversarial checks for the Phase-2B managed control plane."""

from __future__ import annotations

import json
import hashlib
import errno
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest import mock

import manage_steel_module_production_attempt as phase2b_manage
import seal_steel_module_production_phase2b_incident as phase2b_seal
import steel_module_production_phase2b_lib as phase2b_lib
from check_steel_module_managed_production import make_fixture
from finalize_steel_module_managed_child import _successful_array_indexes
from manage_steel_module_production_attempt import (
    freeze_terminal_accounting,
    reconcile_attempt,
    require_manager_command_allowed,
    submit_intent,
    validate_terminal_accounting_snapshot,
)
from seal_steel_module_production_phase2b_incident import (
    EXPECTED_FAILURE_LINE,
    _existing_incident,
    collect_incident_evidence,
    publish_incident_bundle,
    validate_incident_bundle,
)
from steel_module_campaign_lib import sha256_file
from steel_module_production_phase2b_lib import (
    CONTROL_LOCK_PROTOCOL_V1,
    CONTROL_LOCK_PROTOCOL_V2,
    ACCOUNTING_SCHEMA_VERSION_V1,
    EXECUTION_SCHEMA_VERSION_V1,
    EXECUTION_SCHEMA_VERSION_V2,
    READINESS_CRITICAL_ARTIFACTS,
    append_attempt_event,
    attempt_state,
    load_attempt_intent,
    load_execution_companion,
    load_frozen_accounting,
    materialize_execution_companion,
    prepare_attempt_intent,
    read_attempt_events,
    require_pristine_readiness_workspace,
    require_production_execution_open,
    selected_task_ids,
    verify_readiness_protected_artifacts,
)


class FakeSlurm:
    def __init__(self, execution: object, attempt_id: str, *, mode: str = "success") -> None:
        self.execution = execution
        self.attempt_id = attempt_id
        self.mode = mode
        self.calls: list[tuple[str, ...]] = []
        self.job_id = "9001"
        self.accounting_query_seen = False

    def _intent(self) -> dict[str, object]:
        return json.loads(
            (self.execution.directory / "intents" / self.attempt_id / "intent.json").read_text()
        )

    def _scontrol(self, *, state: str = "PENDING", reason: str = "JobHeldUser") -> str:
        intent = self._intent()
        scheduler = intent["scheduler"]
        wrapper = self.execution.directory / "intents" / self.attempt_id / "job-wrapper.sh"
        array = "31" if self.mode == "bad-array" else scheduler["array_spec"].split("-", 1)[1]
        account = "pas2524" if self.mode == "lowercase-account" else (
            "DIFFERENT" if self.mode == "wrong-account" else "PAS2524"
        )
        return (
            f"JobId={self.job_id}_[1-{array}] ArrayJobId={self.job_id} "
            f"ArrayTaskId=1-{array} Account={account} JobName={scheduler['job_name']} "
            f"JobState={state} Reason={reason} Requeue=0 Command={wrapper} "
            f"WorkDir={self.execution.directory} StdOut={scheduler['output_pattern']}\n"
        )

    def _array_accounting_rows(self, *, include_job_name: bool) -> str:
        state = "CANCELLED by 1234" if self.mode == "cancelled-accounting" else "COMPLETED"
        exit_code = "0:15" if self.mode == "cancelled-accounting" else "0:0"
        if self.mode == "all-failed-accounting":
            state, exit_code = "FAILED", "1:0"
        token = self._intent()["scheduler"]["job_name"]
        rows = []
        for index in range(1, 33):
            if self.mode == "missing-accounting" and index == 17:
                continue
            # Reproduce OSC: JobID is the logical array identity, whereas
            # JobIDRaw is a numeric allocation identity.  Task 32 may reuse
            # the parent number as its raw ID without being a parent row.
            raw_id = self.job_id if index == 32 else str(9100 + index)
            values = [f"{self.job_id}_{index}", raw_id]
            if include_job_name:
                values.append(token)
            values.extend([state, exit_code])
            if not include_job_name:
                values.extend(["10", "100K", "200K"])
            rows.append("|".join(values) + "|\n")
        if self.mode == "duplicate-accounting":
            values = [f"{self.job_id}_1", "9999"]
            if include_job_name:
                values.append(token)
            values.extend(["COMPLETED", "0:0"])
            if not include_job_name:
                values.extend(["10", "100K", "200K"])
            rows.append("|".join(values) + "|\n")
        return "".join(rows)

    def __call__(self, command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(command))
        name = Path(command[0]).name
        if name == "sbatch":
            if self.mode in {"timeout", "accepted-timeout", "multi", "zero"}:
                raise subprocess.TimeoutExpired(command, timeout)
            if self.mode == "non-numeric":
                return subprocess.CompletedProcess(command, 0, "not-a-job\n", "")
            if self.mode == "non-zero":
                return subprocess.CompletedProcess(command, 17, "", "scheduler rejected")
            return subprocess.CompletedProcess(command, 0, self.job_id + "\n", "")
        if name == "scontrol" and command[1:3] == ["show", "job"]:
            if self.mode == "scontrol-missing-terminal":
                return subprocess.CompletedProcess(
                    command, 1, "", "job no longer visible to scontrol"
                )
            if self.mode == "released-reconcile":
                return subprocess.CompletedProcess(command, 0, self._scontrol(state="RUNNING", reason="None"), "")
            if self.mode == "not-user-held":
                return subprocess.CompletedProcess(
                    command, 0,
                    self._scontrol(reason="DependencyNeverSatisfied"), "",
                )
            return subprocess.CompletedProcess(command, 0, self._scontrol(), "")
        if name == "scontrol" and command[1] == "release":
            if self.mode == "release-timeout":
                self.mode = "released-reconcile"
                raise subprocess.TimeoutExpired(command, timeout)
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "squeue" and "--name" in command:
            intent = self._intent()
            token = intent["scheduler"]["job_name"]
            if self.accounting_query_seen:
                text = ""
            elif self.mode == "multi":
                text = f"9001|{token}|1-32|PENDING|JobHeldUser\n9002|{token}|1-32|PENDING|JobHeldUser\n"
            elif self.mode in {"zero", "timeout"}:
                text = ""
            else:
                text = f"{self.job_id}|{token}|1-32|RUNNING|None\n"
            return subprocess.CompletedProcess(command, 0, text, "")
        if name == "sacct" and "--name" in command:
            if self.mode == "scontrol-missing-terminal":
                return subprocess.CompletedProcess(
                    command, 0, self._array_accounting_rows(include_job_name=True), "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "squeue":
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "sacct":
            self.accounting_query_seen = True
            return subprocess.CompletedProcess(
                command, 0, self._array_accounting_rows(include_job_name=False), ""
            )
        raise AssertionError(f"unexpected fake Slurm command: {command}")


def _pilot_analysis_fixture(pilot: Path) -> None:
    analysis = pilot / "finalized" / "analysis-v2"
    analysis.mkdir()
    config = {
        "schema_version": "steel-module-analysis-v2-config-v1",
        "accepted_statistical_evidence": False,
        "analysis_commit": "f" * 40,
    }
    (analysis / "analysis_config.json").write_text(json.dumps(config, indent=2) + "\n")
    (analysis / "fixture.csv").write_text("value\n1\n")
    (analysis / "SHA256SUMS").write_text(
        "".join(
            f"{sha256_file(analysis / name)}  {name}\n"
            for name in ("analysis_config.json", "fixture.csv")
        )
    )


def _fixture_sources(root: Path) -> tuple[Path, Path]:
    simulation = root / "simulation-source"
    control = root / "control-source"
    (simulation / "test/OpNovice2").mkdir(parents=True)
    (simulation / "test/OpNovice2/run_sipm_cavity_scan.sh").write_text("#!/bin/sh\nexit 0\n")
    (control / "hpc/osc").mkdir(parents=True)
    batch = control / "hpc/osc/submit_steel_module_production_phase2b.sbatch"
    batch.write_text("#!/bin/sh\nexit 0\n")
    batch.chmod(0o755)
    return simulation, control


def make_execution(
    repo_root: Path,
    scratch: Path,
    suffix: str,
    *,
    control_lock_protocol: str = CONTROL_LOCK_PROTOCOL_V2,
) -> object:
    fixture = scratch / suffix
    fixture.mkdir()
    _, child = make_fixture(repo_root, fixture)
    pilot = fixture / "sealed-pilot"
    _pilot_analysis_fixture(pilot)
    sources = _fixture_sources(fixture)
    execution = materialize_execution_companion(
        repo_root=repo_root, managed_child_dir=child,
        out_dir=fixture / "execution", pilot_dir=pilot,
        test_mode=True, fixture_sources=sources,
        control_lock_protocol=control_lock_protocol,
    )
    assert execution.manifest["accepted_statistical_evidence"] is False
    assert execution.manifest["test_mode"] is True
    assert not (execution.directory / "campaign.json").exists()
    lock_record = execution.manifest["artifacts"]["control_lock"]
    if control_lock_protocol == CONTROL_LOCK_PROTOCOL_V2:
        assert execution.manifest["schema_version"] == EXECUTION_SCHEMA_VERSION_V2
        assert lock_record["protocol"] == CONTROL_LOCK_PROTOCOL_V2
        assert lock_record["creation_inode"] == (
            execution.directory / ".control.lock"
        ).stat().st_ino
        assert lock_record["link_count"] == 1
        assert lock_record["size_bytes"] > 0
    else:
        assert execution.manifest["schema_version"] == EXECUTION_SCHEMA_VERSION_V1
        assert lock_record["inode"] == (
            execution.directory / ".control.lock"
        ).stat().st_ino
    assert not (execution.directory / "sources" / "control" / ".git").exists()
    return execution


def _rewrite_execution_manifest(
    execution: object, mutate: Callable[[dict[str, Any]], None]
) -> None:
    manifest_path = execution.directory / "managed_execution.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(value)
    value["execution_hash"] = phase2b_lib._semantic_hash(value, "execution_hash")
    manifest_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    phase2b_lib._write_static_checksums(execution.directory)


def _expect_lock_rejection(execution: object, marker: str) -> None:
    try:
        load_execution_companion(
            execution.directory, allow_test_mode=True, require_readiness=False
        )
    except ValueError as exc:
        assert marker in str(exc), str(exc)
    else:
        raise AssertionError(f"control-lock tamper was accepted: {marker}")


def test_portable_control_lock_v2(repo_root: Path, scratch: Path) -> None:
    cross_node = make_execution(repo_root, scratch, "lock-v2-cross-node")
    original = cross_node.manifest["artifacts"]["control_lock"]

    def change_creation_diagnostics(value: dict[str, Any]) -> None:
        record = value["artifacts"]["control_lock"]
        record["creation_device"] = original["creation_device"] + 1000003
        record["creation_inode"] = original["creation_inode"] + 1000033

    _rewrite_execution_manifest(cross_node, change_creation_diagnostics)
    loaded = load_execution_companion(
        cross_node.directory, allow_test_mode=True, require_readiness=False
    )
    assert loaded.manifest["artifacts"]["control_lock"]["creation_device"] != (
        loaded.directory / ".control.lock"
    ).stat().st_dev
    assert loaded.manifest["artifacts"]["control_lock"]["creation_inode"] != (
        loaded.directory / ".control.lock"
    ).stat().st_ino

    # Model the GPFS contract observed on OSC: LOCK_EX on an O_RDONLY
    # descriptor raises EBADF, while the same lock on O_RDWR succeeds.  The
    # production helper must choose the latter without weakening any token,
    # path, link-count, mode, or hash check.
    gpfs = make_execution(repo_root, scratch, "lock-v2-gpfs-exclusive")
    real_flock = phase2b_lib.fcntl.flock
    exclusive_access_modes: list[int] = []

    def gpfs_flock(descriptor: int, operation: int) -> object:
        if operation & phase2b_lib.fcntl.LOCK_EX:
            access_mode = (
                phase2b_lib.fcntl.fcntl(descriptor, phase2b_lib.fcntl.F_GETFL)
                & os.O_ACCMODE
            )
            exclusive_access_modes.append(access_mode)
            if access_mode == os.O_RDONLY:
                raise OSError(errno.EBADF, "GPFS rejects read-only LOCK_EX")
        return real_flock(descriptor, operation)

    with mock.patch.object(
        phase2b_lib.fcntl, "flock", side_effect=gpfs_flock
    ):
        with phase2b_lib.execution_lock(gpfs):
            pass
    assert exclusive_access_modes == [os.O_RDWR]

    # A read-only successor lock is historical evidence, not writable
    # authority.  It must be rejected before flock and remain byte/mode exact.
    read_only = make_execution(repo_root, scratch, "lock-v2-read-only-exclusive")
    read_only_path = read_only.directory / ".control.lock"
    read_only_path.chmod(0o400)

    def record_read_only(value: dict[str, Any]) -> None:
        value["artifacts"]["control_lock"]["mode"] = 0o400

    _rewrite_execution_manifest(read_only, record_read_only)
    loaded_read_only = load_execution_companion(
        read_only.directory, allow_test_mode=True, require_readiness=False
    )
    read_only_content = read_only_path.read_bytes()
    flock_calls: list[int] = []

    def record_flock(descriptor: int, operation: int) -> object:
        flock_calls.append(operation)
        return real_flock(descriptor, operation)

    try:
        with (
            mock.patch.object(
                phase2b_lib.os, "open", wraps=phase2b_lib.os.open
            ) as open_call,
            mock.patch.object(
                phase2b_lib.fcntl, "flock", side_effect=record_flock
            ),
        ):
            with phase2b_lib.execution_lock(loaded_read_only):
                pass
    except ValueError as exc:
        assert "recorded mode 0600" in str(exc), str(exc)
    else:
        raise AssertionError("0400 portable lock obtained an exclusive lease")
    assert open_call.call_count == 0
    assert flock_calls == []
    assert read_only_path.stat().st_mode & 0o777 == 0o400
    assert read_only_path.read_bytes() == read_only_content

    mode = make_execution(repo_root, scratch, "lock-v2-mode")
    (mode.directory / ".control.lock").chmod(0o640)
    _expect_lock_rejection(mode, "mode mismatch")

    same_size_content = make_execution(repo_root, scratch, "lock-v2-content")
    content_path = same_size_content.directory / ".control.lock"
    content = bytearray(content_path.read_bytes())
    content[-2] = ord("0") if content[-2] != ord("0") else ord("1")
    content_path.write_bytes(content)
    _expect_lock_rejection(same_size_content, "content-token/hash mismatch")

    size = make_execution(repo_root, scratch, "lock-v2-size")
    size_path = size.directory / ".control.lock"
    size_path.write_bytes(size_path.read_bytes() + b"x")
    _expect_lock_rejection(size, "size mismatch")

    linked = make_execution(repo_root, scratch, "lock-v2-link")
    os.link(linked.directory / ".control.lock", scratch / "lock-v2-extra-link")
    _expect_lock_rejection(linked, "link-count mismatch")

    symlinked = make_execution(repo_root, scratch, "lock-v2-symlink")
    symlink_path = symlinked.directory / ".control.lock"
    symlink_target = scratch / "lock-v2-symlink-target"
    symlink_target.write_bytes(symlink_path.read_bytes())
    symlink_path.unlink()
    symlink_path.symlink_to(symlink_target)
    _expect_lock_rejection(symlinked, "root must not contain symlinks")

    nonregular = make_execution(repo_root, scratch, "lock-v2-nonregular")
    nonregular_path = nonregular.directory / ".control.lock"
    nonregular_path.unlink()
    os.mkfifo(nonregular_path, mode=0o600)
    _expect_lock_rejection(nonregular, "not a regular file")

    acquiring = make_execution(repo_root, scratch, "lock-v2-acquire-race")
    acquiring_path = acquiring.directory / ".control.lock"
    replacement = scratch / "lock-v2-acquire-replacement"
    replacement.write_bytes(acquiring_path.read_bytes())
    replacement.chmod(0o600)
    real_flock = phase2b_lib.fcntl.flock
    replaced = False

    def replace_after_flock(descriptor: int, operation: int) -> object:
        nonlocal replaced
        result = real_flock(descriptor, operation)
        if operation == phase2b_lib.fcntl.LOCK_SH and not replaced:
            os.replace(replacement, acquiring_path)
            replaced = True
        return result

    with mock.patch.object(
        phase2b_lib.fcntl, "flock", side_effect=replace_after_flock
    ):
        _expect_lock_rejection(acquiring, "changed while acquiring flock")
    assert replaced

    held = make_execution(repo_root, scratch, "lock-v2-held-replacement")
    held_path = held.directory / ".control.lock"
    held_replacement = scratch / "lock-v2-held-replacement-file"
    held_replacement.write_bytes(held_path.read_bytes())
    held_replacement.chmod(0o600)
    try:
        with phase2b_lib.execution_lock(held):
            os.replace(held_replacement, held_path)
    except ValueError as exc:
        assert "changed while held" in str(exc)
    else:
        raise AssertionError("same-content control-lock replacement was accepted while held")

    exceptional = make_execution(repo_root, scratch, "lock-v2-exception-replacement")
    exceptional_path = exceptional.directory / ".control.lock"
    exceptional_replacement = scratch / "lock-v2-exception-replacement-file"
    exceptional_replacement.write_bytes(exceptional_path.read_bytes())
    exceptional_replacement.chmod(0o600)
    try:
        with phase2b_lib.execution_lock(exceptional):
            os.replace(exceptional_replacement, exceptional_path)
            raise RuntimeError("protected body failed after replacing the lock")
    except ValueError as exc:
        assert "changed while held" in str(exc)
    else:
        raise AssertionError("exception path hid a held control-lock replacement")


def test_inode_bound_control_lock_v1(repo_root: Path, scratch: Path) -> None:
    legacy = make_execution(
        repo_root, scratch, "lock-v1-legacy",
        control_lock_protocol=CONTROL_LOCK_PROTOCOL_V1,
    )

    def simulate_cross_node(value: dict[str, Any]) -> None:
        record = value["artifacts"]["control_lock"]
        record["device"] += 1
        record["inode"] += 1

    _rewrite_execution_manifest(legacy, simulate_cross_node)
    _expect_lock_rejection(legacy, "inode identity mismatch")

    legacy_race = make_execution(
        repo_root, scratch, "lock-v1-acquire-race",
        control_lock_protocol=CONTROL_LOCK_PROTOCOL_V1,
    )
    legacy_path = legacy_race.directory / ".control.lock"
    replacement = scratch / "lock-v1-acquire-replacement"
    replacement.write_bytes(legacy_path.read_bytes())
    replacement.chmod(0o600)
    real_flock = phase2b_lib.fcntl.flock
    replaced = False

    def replace_after_flock(descriptor: int, operation: int) -> object:
        nonlocal replaced
        result = real_flock(descriptor, operation)
        if operation == phase2b_lib.fcntl.LOCK_EX and not replaced:
            os.replace(replacement, legacy_path)
            replaced = True
        return result

    try:
        with mock.patch.object(
            phase2b_lib.fcntl, "flock", side_effect=replace_after_flock
        ):
            with phase2b_lib.execution_lock(legacy_race):
                pass
    except ValueError as exc:
        assert "changed while acquiring flock" in str(exc)
    else:
        raise AssertionError("legacy control-lock open/flock replacement was accepted")
    assert replaced


def test_exact_historical_v1_recovery_lock(
    repo_root: Path, scratch: Path
) -> None:
    """Exercise the one closed v1 companion's additive recovery authority.

    The fixture deliberately records a device/inode pair that differs from the
    live empty lock file, as happens when the same OSC path is viewed from a
    different login node.  The ordinary v1 validator must remain strict; only
    the exact historical-recovery validator and lock may ignore that frozen
    cross-node diagnostic.
    """

    base = make_execution(
        repo_root,
        scratch,
        "lock-v1-exact-historical-recovery",
        control_lock_protocol=CONTROL_LOCK_PROTOCOL_V1,
    )
    live_lock = base.directory / ".control.lock"
    live_stat = live_lock.stat()

    def make_formal_historical(value: dict[str, Any]) -> None:
        value["test_mode"] = False
        value["accepted_statistical_evidence"] = True
        value["execution_id"] = (
            "sm-v1-production-bc-s1-execution-historical-fixture"
        )
        record = value["artifacts"]["control_lock"]
        record["device"] = live_stat.st_dev + 1000003
        record["inode"] = live_stat.st_ino + 1000033

    _rewrite_execution_manifest(base, make_formal_historical)
    manifest = json.loads(
        (base.directory / "managed_execution.json").read_text(encoding="utf-8")
    )
    historical = phase2b_lib.ManagedExecution(
        base.directory,
        base.managed_child,
        manifest,
        base.tasks,
        base.scan_args,
    )
    record = manifest["artifacts"]["control_lock"]
    assert (record["device"], record["inode"]) != (
        live_lock.stat().st_dev,
        live_lock.stat().st_ino,
    )

    # Prepare a realistic released attempt before assigning the synthetic
    # identity the closed historical authority.  The setup bypasses the
    # deliberately mismatched legacy lock; the behavior under test below does
    # not.
    attempt_id = "20260718T175107Z-historical-fixture"
    with mock.patch.object(
        phase2b_lib,
        "execution_lock",
        side_effect=lambda *_args, **_kwargs: nullcontext(),
    ):
        intent = _prepare(historical, attempt_id)
    job_id = "9001"
    append_attempt_event(
        historical,
        attempt_id,
        "submission-invoked",
        {"command_sha256": "a" * 64, "job_name": intent["scheduler"]["job_name"]},
        actor="fixture-reviewer",
        lock_held=True,
    )
    append_attempt_event(
        historical,
        attempt_id,
        "submitted-held",
        {"job_id": job_id},
        actor="fixture-reviewer",
        lock_held=True,
    )
    append_attempt_event(
        historical,
        attempt_id,
        "job-verified",
        {"job_id": job_id},
        actor="fixture-reviewer",
        lock_held=True,
    )
    append_attempt_event(
        historical,
        attempt_id,
        "job-released",
        {"job_id": job_id},
        actor="fixture-reviewer",
        lock_held=True,
    )
    assert attempt_state(historical, attempt_id).status == "released-active"

    with ExitStack() as authority:
        for module, name, value in (
            (phase2b_lib, "HISTORICAL_CLOSED_EXECUTION_ID", historical.execution_id),
            (
                phase2b_lib,
                "HISTORICAL_CLOSED_EXECUTION_HASH",
                historical.execution_hash,
            ),
            (phase2b_lib, "HISTORICAL_CLOSED_LOCK_DEVICE", record["device"]),
            (phase2b_lib, "HISTORICAL_CLOSED_LOCK_INODE", record["inode"]),
            (phase2b_lib, "HISTORICAL_CLOSED_ATTEMPT_ID", attempt_id),
            (
                phase2b_lib,
                "HISTORICAL_CLOSED_INTENT_SHA256",
                intent["intent_sha256"],
            ),
            (phase2b_lib, "HISTORICAL_CLOSED_JOB_ID", job_id),
            (phase2b_manage, "HISTORICAL_CLOSED_ATTEMPT_ID", attempt_id),
            (
                phase2b_manage,
                "HISTORICAL_CLOSED_INTENT_SHA256",
                intent["intent_sha256"],
            ),
            (phase2b_manage, "HISTORICAL_CLOSED_JOB_ID", job_id),
            (phase2b_seal, "FORMAL_ATTEMPT_ID", attempt_id),
            (phase2b_seal, "FORMAL_JOB_ID", job_id),
        ):
            authority.enter_context(mock.patch.object(module, name, value))

        try:
            phase2b_lib.validate_execution_control_lock(
                historical.directory, historical.manifest
            )
        except ValueError as exc:
            assert "inode identity mismatch" in str(exc)
        else:
            raise AssertionError(
                "ordinary v1 validation accepted a cross-node inode mismatch"
            )

        phase2b_lib.validate_historical_recovery_control_lock(
            historical.directory, historical.manifest
        )
        with phase2b_lib.historical_recovery_execution_lock(historical):
            pass

        wrong_scope = json.loads(json.dumps(historical.manifest))
        wrong_scope["execution_id"] += "-wrong"
        try:
            phase2b_lib.validate_historical_recovery_control_lock(
                historical.directory, wrong_scope
            )
        except ValueError as exc:
            assert "limited to the closed execution" in str(exc)
        else:
            raise AssertionError("historical recovery accepted another execution")

        try:
            load_execution_companion(
                historical.directory,
                repo_root=repo_root,
                allow_test_mode=True,
                require_readiness=False,
                _allow_historical_closed_recovery=True,
            )
        except ValueError as exc:
            assert "full formal validation mode" in str(exc)
        else:
            raise AssertionError("historical loader accepted a weakened mode")

        live_lock.chmod(0o640)
        try:
            phase2b_lib.validate_historical_recovery_control_lock(
                historical.directory, historical.manifest
            )
        except ValueError as exc:
            assert "mode mismatch" in str(exc)
        else:
            raise AssertionError("historical recovery accepted a mode tamper")
        finally:
            live_lock.chmod(0o600)

        live_lock.write_bytes(b"x")
        try:
            phase2b_lib.validate_historical_recovery_control_lock(
                historical.directory, historical.manifest
            )
        except ValueError as exc:
            assert "size mismatch" in str(exc)
        else:
            raise AssertionError("historical recovery accepted lock content")
        finally:
            live_lock.write_bytes(b"")
            live_lock.chmod(0o600)

        extra_link = scratch / "historical-recovery-extra-link"
        os.link(live_lock, extra_link)
        try:
            phase2b_lib.validate_historical_recovery_control_lock(
                historical.directory, historical.manifest
            )
        except ValueError as exc:
            assert "link-count mismatch" in str(exc)
        else:
            raise AssertionError("historical recovery accepted a hard link")
        finally:
            extra_link.unlink()

        replacement = scratch / "historical-recovery-held-replacement"
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        try:
            with phase2b_lib.historical_recovery_execution_lock(historical):
                os.replace(replacement, live_lock)
        except ValueError as exc:
            assert "changed while held" in str(exc)
        else:
            raise AssertionError(
                "historical recovery missed a same-node lock replacement"
            )
        phase2b_lib.validate_historical_recovery_control_lock(
            historical.directory, historical.manifest
        )

        scheduler_calls: list[tuple[str, ...]] = []

        def scheduler_read_subprocess(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            scheduler_calls.append(tuple(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(
            phase2b_seal.subprocess, "run", side_effect=scheduler_read_subprocess
        ):
            read_runner = phase2b_seal._historical_scheduler_read_runner(
                historical,
                {
                    "scheduler_read_commands": {
                        "sacct": "/usr/bin/sacct",
                        "squeue": "/usr/bin/squeue",
                    }
                },
            )
            read_runner(
                [
                    "sacct", "-n", "-P", "-j", job_id,
                    "--format=JobID,JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS,MaxVMSize",
                ],
                cwd=historical.directory,
            )
            assert scheduler_calls == [
                (
                    "/usr/bin/sacct", "-n", "-P", "-j", job_id,
                    "--format=JobID,JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS,MaxVMSize",
                )
            ]
            try:
                read_runner(
                    ["sbatch", "--parsable", "forbidden.sbatch"],
                    cwd=historical.directory,
                )
            except ValueError as exc:
                assert "non-allowlisted" in str(exc)
            else:
                raise AssertionError("historical recovery allowlisted sbatch")
            assert len(scheduler_calls) == 1

        def forbidden_normal_lock(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("historical recovery used the normal execution lock")

        fake = FakeSlurm(historical, attempt_id, mode="all-failed-accounting")
        with mock.patch.object(
            phase2b_manage, "execution_lock", side_effect=forbidden_normal_lock
        ), mock.patch.object(
            phase2b_lib, "execution_lock", side_effect=forbidden_normal_lock
        ):
            accounting = freeze_terminal_accounting(
                historical,
                attempt_id=attempt_id,
                intent_sha256=intent["intent_sha256"],
                actor="fixture-reviewer",
                runner=fake,
                historical_incident_recovery=True,
            )
            assert accounting["schema_version"] == (
                phase2b_lib.ACCOUNTING_SCHEMA_VERSION_V2
            )
            assert accounting["accepted_terminal"] is True
            assert accounting["all_tasks_completed"] is False
            assert set(accounting["task_states"].values()) == {"FAILED"}
            terminal_events = [
                event
                for event in read_attempt_events(historical, attempt_id)
                if event["event_type"] == "terminal-accounting-frozen"
            ]
            assert len(terminal_events) == 1
            calls_after_first_freeze = tuple(fake.calls)

            def forbidden_scheduler(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("idempotent recovery queried the scheduler")

            repeated = freeze_terminal_accounting(
                historical,
                attempt_id=attempt_id,
                intent_sha256=intent["intent_sha256"],
                actor="fixture-reviewer",
                runner=forbidden_scheduler,
                historical_incident_recovery=True,
            )
            assert repeated == accounting
            assert tuple(fake.calls) == calls_after_first_freeze
            assert len(
                [
                    event
                    for event in read_attempt_events(historical, attempt_id)
                    if event["event_type"] == "terminal-accounting-frozen"
                ]
            ) == 1

        assert all(Path(command[0]).name != "sbatch" for command in fake.calls)
        attempt_dir = historical.directory / "attempts" / attempt_id
        assert not (attempt_dir / "tasks").exists()
        assert not tuple(historical.directory.rglob("*.root"))


def test_exact_historical_event_lineage() -> None:
    job_id = "9001"
    job_name = "g4sm-fixed-history"
    intent = {"scheduler": {"job_name": job_name}}
    actor = phase2b_seal.FORMAL_RECOVERY_ACTOR
    waiver = {
        "field": "Account",
        "intent_value": "PAS2524",
        "scheduler_value": "pas2524",
        "rule": "exact-ASCII-lowercase-equivalence-only",
    }
    scontrol_stdout = "fixed scheduler identity\n"

    def event(
        event_type: str,
        payload: dict[str, object],
        *,
        event_actor: str = "fixture-reviewer",
        event_sha256: str = "f" * 64,
    ) -> dict[str, object]:
        return {
            "event_type": event_type,
            "payload": payload,
            "actor": event_actor,
            "event_sha256": event_sha256,
        }

    events = (
        event(
            "submission-invoked",
            {
                "command_sha256": phase2b_seal.FORMAL_SUBMISSION_COMMAND_SHA256,
                "job_name": job_name,
            },
        ),
        event("submitted-held", {"job_id": job_id}),
        event(
            "release-ambiguous",
            {"job_id": job_id, "reason": "Slurm held job Account mismatch"},
            event_sha256="8fd96fa45f98" + "0" * 52,
        ),
        event(
            "release-scheduler-observation",
            {
                "job_id": job_id,
                "match_count": 1,
                "manual_review": "OSC canonicalized the Slurm account to lowercase",
                "account_case_waiver": waiver,
                "submission_command_sha256": (
                    phase2b_seal.FORMAL_SUBMISSION_COMMAND_SHA256
                ),
                "recovery_script_sha256": (
                    phase2b_seal.FORMAL_RECOVERY_SCRIPT_SHA256
                ),
                "squeue_psv": (
                    f"{job_id}|{job_name}|pas2524|PENDING|JobHeldUser\n"
                ),
                "sacct_psv": f"{job_id}|{job_name}|PENDING|0:0\n",
                "scontrol_stdout": scontrol_stdout,
                "scontrol_stdout_sha256": hashlib.sha256(
                    scontrol_stdout.encode()
                ).hexdigest(),
                "no_sbatch": True,
            },
            event_actor=actor,
        ),
        event(
            "job-verified",
            {
                "job_id": job_id,
                "job_name": job_name,
                "state": "PENDING",
                "reason": "JobHeldUser",
                "reconciled": True,
                "account_case_waiver": waiver,
                "scontrol_stdout_sha256": "a" * 64,
            },
            event_actor=actor,
        ),
        event(
            "job-released",
            {
                "job_id": job_id,
                "reconciled": True,
                "manual_account_case_recovery": True,
                "release_command": ["/usr/bin/scontrol", "release", job_id],
                "return_code": 0,
            },
            event_actor=actor,
        ),
    )
    with mock.patch.object(phase2b_seal, "FORMAL_JOB_ID", job_id):
        phase2b_seal._validate_formal_event_lineage(
            intent, SimpleNamespace(status="released-active", events=events)
        )
        phase2b_seal._validate_formal_event_lineage(
            intent,
            SimpleNamespace(
                status="terminal-accounting-frozen",
                events=(
                    *events,
                    event("terminal-accounting-frozen", {}),
                ),
            ),
        )
        for index, replacement in (
            (3, {**events[3], "actor": "wrong-actor"}),
            (
                3,
                {
                    **events[3],
                    "payload": {**events[3]["payload"], "no_sbatch": False},
                },
            ),
            (
                3,
                {
                    **events[3],
                    "payload": {**events[3]["payload"], "unexpected": True},
                },
            ),
            (
                4,
                {
                    **events[4],
                    "payload": {**events[4]["payload"], "job_name": "wrong"},
                },
            ),
            (
                5,
                {
                    **events[5],
                    "payload": {**events[5]["payload"], "return_code": 1},
                },
            ),
        ):
            tampered = list(events)
            tampered[index] = replacement
            try:
                phase2b_seal._validate_formal_event_lineage(
                    intent,
                    SimpleNamespace(status="released-active", events=tuple(tampered)),
                )
            except ValueError:
                pass
            else:
                raise AssertionError("invalid historical event lineage was accepted")
        for wrong_shape in (
            (*events[:3], *events[4:]),
            (*events[:4], events[3], *events[4:]),
            (*events, events[5]),
        ):
            try:
                phase2b_seal._validate_formal_event_lineage(
                    intent,
                    SimpleNamespace(status="released-active", events=wrong_shape),
                )
            except ValueError:
                pass
            else:
                raise AssertionError("wrong historical event shape was accepted")


def test_exact_historical_event_lineage_integration(
    repo_root: Path, scratch: Path
) -> None:
    execution = make_execution(repo_root, scratch, "exact-event-lineage")
    attempt_id = "20260718T175107Z-lineage-fixture"
    intent = _prepare(execution, attempt_id)
    job_id = "9001"
    job_name = intent["scheduler"]["job_name"]
    actor = phase2b_seal.FORMAL_RECOVERY_ACTOR
    waiver = {
        "field": "Account",
        "intent_value": "PAS2524",
        "scheduler_value": "pas2524",
        "rule": "exact-ASCII-lowercase-equivalence-only",
    }
    append_attempt_event(
        execution,
        attempt_id,
        "submission-invoked",
        {
            "command_sha256": phase2b_seal.FORMAL_SUBMISSION_COMMAND_SHA256,
            "job_name": job_name,
        },
        actor="fixture-reviewer",
    )
    append_attempt_event(
        execution, attempt_id, "submitted-held", {"job_id": job_id},
        actor="fixture-reviewer",
    )
    append_attempt_event(
        execution,
        attempt_id,
        "release-ambiguous",
        {"job_id": job_id, "reason": "Slurm held job Account mismatch"},
        actor="fixture-reviewer",
    )
    scontrol_stdout = "fixed scheduler identity\n"
    append_attempt_event(
        execution,
        attempt_id,
        "release-scheduler-observation",
        {
            "job_id": job_id,
            "match_count": 1,
            "manual_review": "OSC canonicalized the Slurm account to lowercase",
            "account_case_waiver": waiver,
            "submission_command_sha256": (
                phase2b_seal.FORMAL_SUBMISSION_COMMAND_SHA256
            ),
            "recovery_script_sha256": phase2b_seal.FORMAL_RECOVERY_SCRIPT_SHA256,
            "squeue_psv": f"{job_id}|{job_name}|pas2524|PENDING|JobHeldUser\n",
            "sacct_psv": f"{job_id}|{job_name}|PENDING|0:0\n",
            "scontrol_stdout": scontrol_stdout,
            "scontrol_stdout_sha256": hashlib.sha256(
                scontrol_stdout.encode()
            ).hexdigest(),
            "no_sbatch": True,
        },
        actor=actor,
    )
    append_attempt_event(
        execution,
        attempt_id,
        "job-verified",
        {
            "job_id": job_id,
            "job_name": job_name,
            "state": "PENDING",
            "reason": "JobHeldUser",
            "reconciled": True,
            "account_case_waiver": waiver,
            "scontrol_stdout_sha256": "a" * 64,
        },
        actor=actor,
    )
    append_attempt_event(
        execution,
        attempt_id,
        "job-released",
        {
            "job_id": job_id,
            "reconciled": True,
            "manual_account_case_recovery": True,
            "release_command": ["/usr/bin/scontrol", "release", job_id],
            "return_code": 0,
        },
        actor=actor,
    )
    state = attempt_state(execution, attempt_id)
    assert state.status == "released-active"
    attempt_dir = execution.directory / "attempts" / attempt_id
    for index in range(1, 33):
        (attempt_dir / f"slurm-{job_id}_{index}.out").write_text(
            EXPECTED_FAILURE_LINE + "\n", encoding="utf-8"
        )
    readiness = {
        "path": "/fixture/readiness.json",
        "sha256": "a" * 64,
        "schema_version": "fixture-readiness-v1",
        "status": "accepted-fixture",
        "implementation_commit": "f" * 40,
        "readiness_commit": "e" * 40,
    }
    fake = FakeSlurm(execution, attempt_id, mode="all-failed-accounting")
    with ExitStack() as authority:
        for name, value in (
            ("FORMAL_EXECUTION_ID", execution.execution_id),
            ("FORMAL_EXECUTION_HASH", execution.execution_hash),
            ("FORMAL_ATTEMPT_ID", attempt_id),
            ("FORMAL_INTENT_SHA256", intent["intent_sha256"]),
            ("FORMAL_JOB_ID", job_id),
            (
                "FORMAL_RELEASE_AMBIGUOUS_EVENT_PREFIX",
                state.events[2]["event_sha256"][:12],
            ),
        ):
            authority.enter_context(mock.patch.object(phase2b_seal, name, value))
        authority.enter_context(
            mock.patch.object(
                phase2b_seal,
                "_historical_readiness_identity",
                return_value=readiness,
            )
        )
        evidence = collect_incident_evidence(
            execution,
            repo_root=repo_root,
            attempt_id=attempt_id,
            intent_sha256=intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=fake,
            historical_readiness=readiness,
        )
    assert evidence["attempt"]["job_id"] == job_id
    assert evidence["scheduler"]["states"] == {"FAILED": 32}
    assert len(fake.calls) == 2
    assert not (attempt_dir / "accounting").exists()


def test_materialization(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "materialize")
    require_pristine_readiness_workspace(execution)
    short_actor_intent = prepare_attempt_intent(
        execution, attempt_id="20260718T115900Z-check", mode="initial",
        actor="edison", write=False, readiness_lock_sha256="a" * 64,
    )
    assert short_actor_intent["created_by"] == "edison"
    orphan = execution.directory / "attempts" / "orphan-without-intent"
    orphan.mkdir()
    try:
        require_pristine_readiness_workspace(execution)
    except ValueError as exc:
        assert "empty attempts" in str(exc)
    else:
        raise AssertionError("orphan attempt evidence was accepted for readiness")
    orphan.rmdir()
    finalized_orphan = execution.directory / "finalized" / "orphan"
    finalized_orphan.mkdir()
    try:
        require_pristine_readiness_workspace(execution)
    except ValueError as exc:
        assert "empty finalized" in str(exc)
    else:
        raise AssertionError("orphan finalized evidence was accepted for readiness")
    finalized_orphan.rmdir()
    before = sha256_file(execution.directory / "managed_execution.json")
    try:
        materialize_execution_companion(
            repo_root=repo_root, managed_child_dir=execution.managed_child.directory,
            out_dir=execution.directory, pilot_dir=Path(execution.manifest["sealed_pilot"]["directory"]),
            test_mode=True,
        )
    except ValueError as exc:
        assert "overwrite" in str(exc)
    else:
        raise AssertionError("execution companion overwrite was accepted")
    assert sha256_file(execution.directory / "managed_execution.json") == before

    manifest = execution.directory / "managed_execution.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original + b" ")
    try:
        load_execution_companion(execution.directory, allow_test_mode=True, require_readiness=False)
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("tampered execution manifest was accepted")
    manifest.write_bytes(original)

    fault = scratch / "fault"
    fixture = scratch / "fault-input"
    fixture.mkdir()
    _, child = make_fixture(repo_root, fixture)
    pilot = fixture / "sealed-pilot"
    _pilot_analysis_fixture(pilot)
    try:
        materialize_execution_companion(
            repo_root=repo_root, managed_child_dir=child, out_dir=fault,
            pilot_dir=pilot, test_mode=True, fixture_sources=_fixture_sources(fixture),
            fault_after="sources",
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected execution fault succeeded")
    assert not fault.exists()


def test_readiness_protected_artifact_set(scratch: Path) -> None:
    root = scratch / "readiness-current"
    execution_root = scratch / "readiness-execution"
    frozen = execution_root / "sources" / "control"
    root.mkdir()
    frozen.mkdir(parents=True)
    artifacts: dict[str, dict[str, str]] = {}
    for relative in READINESS_CRITICAL_ARTIFACTS:
        current_path = root / relative
        frozen_path = frozen / relative
        current_path.parent.mkdir(parents=True, exist_ok=True)
        frozen_path.parent.mkdir(parents=True, exist_ok=True)
        content = f"protected:{relative}\n"
        current_path.write_text(content, encoding="utf-8")
        frozen_path.write_text(content, encoding="utf-8")
        artifacts[relative] = {"sha256": sha256_file(current_path)}
    execution = SimpleNamespace(
        directory=execution_root,
        manifest={"sources": {"control_plane": {"path": "sources/control"}}},
    )
    verify_readiness_protected_artifacts(
        execution, root, {"artifacts": artifacts}
    )

    incomplete = dict(artifacts)
    incomplete.pop(READINESS_CRITICAL_ARTIFACTS[-1])
    try:
        verify_readiness_protected_artifacts(
            execution, root, {"artifacts": incomplete}
        )
    except ValueError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("partial readiness protected set was accepted")

    victim = READINESS_CRITICAL_ARTIFACTS[0]
    (root / victim).write_text("tampered-current\n", encoding="utf-8")
    try:
        verify_readiness_protected_artifacts(
            execution, root, {"artifacts": artifacts}
        )
    except ValueError as exc:
        assert "changed" in str(exc)
    else:
        raise AssertionError("tampered R-checkout artifact was accepted")
    (root / victim).write_text(f"protected:{victim}\n", encoding="utf-8")
    (frozen / victim).write_text("tampered-frozen\n", encoding="utf-8")
    try:
        verify_readiness_protected_artifacts(
            execution, root, {"artifacts": artifacts}
        )
    except ValueError as exc:
        assert "commit C" in str(exc)
    else:
        raise AssertionError("tampered implementation-C artifact was accepted")


def _rewrite_intent(directory: Path, value: dict[str, object]) -> None:
    unhashed = dict(value)
    unhashed.pop("intent_sha256", None)
    value["intent_sha256"] = hashlib.sha256(
        json.dumps(
            unhashed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    intent_path = directory / "intent.json"
    checksum_path = directory / "SHA256SUMS"
    intent_path.chmod(0o644)
    checksum_path.chmod(0o644)
    intent_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    checksum_path.write_text(
        "".join(
            f"{sha256_file(directory / name)}  {name}\n"
            for name in ("intent.json", "job-wrapper.sh", "scan_args.txt", "tasks.tsv")
        ),
        encoding="utf-8",
    )


def test_intent_contract_tamper(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "intent-contract")
    intent = _prepare(execution, "20260718T120700Z-initial")
    directory = execution.directory / "intents" / intent["attempt_id"]

    scheduler_tamper = json.loads(json.dumps(intent))
    scheduler_tamper["scheduler"]["job_wrapper"]["path"] = "../../arbitrary.sh"
    _rewrite_intent(directory, scheduler_tamper)
    try:
        load_attempt_intent(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "scheduler contract" in str(exc)
    else:
        raise AssertionError("self-rehashed scheduler override was accepted")

    runtime_tamper = json.loads(json.dumps(intent))
    runtime_tamper["runtime"]["executable_sha256"] = "0" * 64
    _rewrite_intent(directory, runtime_tamper)
    try:
        load_attempt_intent(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "runtime" in str(exc)
    else:
        raise AssertionError("self-rehashed runtime override was accepted")

    wrapper = directory / "job-wrapper.sh"
    wrapper.chmod(0o644)
    wrapper.write_text("#!/usr/bin/env bash\nexec /tmp/arbitrary\n", encoding="utf-8")
    wrapper_tamper = json.loads(json.dumps(intent))
    wrapper_sha = sha256_file(wrapper)
    wrapper_tamper["artifacts"]["job_wrapper"]["sha256"] = wrapper_sha
    wrapper_tamper["scheduler"]["job_wrapper"]["sha256"] = wrapper_sha
    _rewrite_intent(directory, wrapper_tamper)
    try:
        load_attempt_intent(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "wrapper content" in str(exc)
    else:
        raise AssertionError("self-rehashed arbitrary wrapper was accepted")


def _prepare(execution: object, attempt_id: str) -> dict[str, object]:
    checked = prepare_attempt_intent(
        execution, attempt_id=attempt_id, mode="initial", actor="fixture-reviewer",
        write=False, readiness_lock_sha256="a" * 64,
    )
    assert not (execution.directory / "intents" / attempt_id).exists()
    written = prepare_attempt_intent(
        execution, attempt_id=attempt_id, mode="initial", actor="fixture-reviewer",
        write=True, readiness_lock_sha256="a" * 64,
    )
    assert written["intent_sha256"] == checked["intent_sha256"] or (
        # created_at is evidence, so separate check/write calls need not hash equal.
        written["selected"] == checked["selected"]
    )
    assert written["selected"]["task_count"] == 32
    assert (execution.directory / "intents" / attempt_id / "job-wrapper.sh").is_file()
    assert "json.load" not in (
        execution.directory / "intents" / attempt_id / "job-wrapper.sh"
    ).read_text(encoding="utf-8")
    return written


def test_success(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "success")
    intent = _prepare(execution, "20260718T120000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    job_id = submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
    )
    assert job_id == "9001"
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1
    sbatch_command = next(
        command for command in fake.calls if Path(command[0]).name == "sbatch"
    )
    assert sbatch_command[-3:] == (
        str(execution.directory), intent["attempt_id"], intent["intent_sha256"]
    )
    accounting = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
    )
    assert accounting["accepted_terminal"] is True
    assert accounting["all_tasks_completed"] is True
    assert accounting["array_index_identity_field"] == "JobID"
    assert accounting["array_parent_row_present"] is False
    assert accounting["task_job_ids"]["32"] == "9001_32"
    assert accounting["task_job_ids_raw"]["32"] == "9001"
    assert accounting["task_job_ids_raw"]["1"] != "9001"
    assert _successful_array_indexes(
        execution, intent["attempt_id"], accounting, "9001"
    ) == set(range(1, 33))
    assert attempt_state(execution, intent["attempt_id"]).status == "terminal-accounting-frozen"
    frozen_squeue = (
        execution.directory / "attempts" / intent["attempt_id"]
        / "accounting" / "squeue.psv"
    )
    assert frozen_squeue.read_text(encoding="utf-8") == ""

    first_task = execution.tasks[0]
    marker_dir = (
        execution.directory / "attempts" / intent["attempt_id"]
        / "tasks" / first_task.logical_task_id
    )
    marker_dir.mkdir(parents=True)
    artifact_records = {}
    for artifact_name in (
        "run_config", "points", "macro", "simulation_log", "root", "summary",
        "efficiency_map",
    ):
        artifact_path = marker_dir / f"fixture-{artifact_name}"
        artifact_path.write_text(f"{artifact_name}\n")
        artifact_records[artifact_name] = {
            "path": artifact_path.relative_to(execution.directory).as_posix(),
            "size_bytes": artifact_path.stat().st_size,
            "sha256": sha256_file(artifact_path),
        }
    (marker_dir / "task_result.json").write_text(
        json.dumps(
            {
                "schema_version": "steel-module-managed-production-task-result-v1",
                "execution_id": execution.execution_id,
                "execution_hash": execution.execution_hash,
                "intent_sha256": intent["intent_sha256"],
                "attempt_id": intent["attempt_id"],
                "logical_task_id": first_task.logical_task_id,
                "configuration_hash": first_task.configuration_hash,
                "seed1": first_task.seed1, "seed2": first_task.seed2,
                "events": first_task.events,
                "campaign_id": execution.campaign_id,
                "plan_hash": execution.plan_hash,
                "program_id": execution.manifest["program"]["program_id"],
                "program_hash": execution.manifest["program"]["program_hash"],
                "child_id": "BC-S1",
                "child_plan_hash": execution.manifest["managed_child"]["child_plan_hash"],
                "binding_hash": execution.manifest["managed_child"]["binding_hash"],
                "phase2a_lock_sha256": execution.manifest["phase2a_lock"]["sha256"],
                "readiness_lock_sha256": intent["readiness_lock_sha256"],
                "task_index": first_task.task_index,
                "tile_thickness_mm": first_task.tile_thickness_mm,
                "sipm_layout": first_task.sipm_layout,
                "absorber_transverse_mm": first_task.absorber_transverse_mm,
                "seed_block": first_task.seed_block,
                "simulation_source": execution.manifest["sources"]["simulation"],
                "control_plane_source": execution.manifest["sources"]["control_plane"],
                "runtime": execution.manifest["runtime"],
                "artifacts": artifact_records,
            }, indent=2,
        ) + "\n"
    )
    assert len(selected_task_ids(execution, "resume")) == 31
    assert len(selected_task_ids(execution, "retry-failed")) == 31
    assert first_task.logical_task_id not in selected_task_ids(execution, "resume")

    event = execution.directory / "attempts" / intent["attempt_id"] / "events"
    first = sorted(event.iterdir())[0]
    first.chmod(0o644)
    original = first.read_bytes()
    first.write_bytes(original.replace(b"submission-invoked", b"submission-invokex"))
    try:
        read_attempt_events(execution, intent["attempt_id"])
    except ValueError:
        pass
    else:
        raise AssertionError("tampered event chain was accepted")


def test_account_case_canonicalization(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "lowercase-account")
    intent = _prepare(execution, "20260718T120050Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="lowercase-account")
    submit_intent(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    verified = next(
        event
        for event in read_attempt_events(execution, intent["attempt_id"])
        if event["event_type"] == "job-verified"
    )
    assert verified["payload"]["expected_account"] == "PAS2524"
    assert verified["payload"]["observed_account"] == "pas2524"
    assert verified["payload"]["account_comparison"] == (
        "osc-ascii-lowercase-canonicalization"
    )

    rejected = make_execution(repo_root, scratch, "wrong-account")
    rejected_intent = _prepare(rejected, "20260718T120051Z-initial")
    rejected_fake = FakeSlurm(
        rejected, rejected_intent["attempt_id"], mode="wrong-account"
    )
    try:
        submit_intent(
            rejected,
            attempt_id=rejected_intent["attempt_id"],
            intent_sha256=rejected_intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=rejected_fake,
        )
    except ValueError as exc:
        assert "quarantined" in str(exc)
    else:
        raise AssertionError("a different OSC account was accepted")
    assert attempt_state(rejected, rejected_intent["attempt_id"]).status == (
        "release-ambiguous"
    )


def test_historical_execution_is_explicitly_closed() -> None:
    historical = SimpleNamespace(
        execution_id="sm-v1-production-bc-s1-execution-193d261c3059",
        execution_hash=(
            "d07a32a6afea7d2345b4f4ca45996fd88dde93a7cf8f9d875e0ba0e47e52cf7b"
        ),
    )
    try:
        require_production_execution_open(historical)
    except ValueError as exc:
        assert "historical Phase-2B execution is closed" in str(exc)
    else:
        raise AssertionError("the historical execution remained production-open")
    require_manager_command_allowed(historical, "status")
    for command in (
        "prepare-intent", "submit-intent", "cancel-intent", "reconcile",
        "freeze-accounting",
    ):
        try:
            require_manager_command_allowed(historical, command)
        except ValueError as exc:
            assert "historical Phase-2B execution is closed" in str(exc)
        else:
            raise AssertionError(f"historical command remained enabled: {command}")
    try:
        prepare_attempt_intent(
            historical,
            attempt_id="20260718T190000Z-retry",
            mode="retry-failed",
            actor="fixture-reviewer",
            write=False,
            readiness_lock_sha256="a" * 64,
        )
    except ValueError as exc:
        assert "historical Phase-2B execution is closed" in str(exc)
    else:
        raise AssertionError("direct historical intent preparation remained enabled")
    try:
        submit_intent(
            historical,
            attempt_id="20260718T175107Z-initial",
            intent_sha256="a" * 64,
            actor="fixture-reviewer",
        )
    except ValueError as exc:
        assert "historical Phase-2B execution is closed" in str(exc)
    else:
        raise AssertionError("direct historical submission remained enabled")
    successor = SimpleNamespace(
        execution_id="sm-v1-production-bc-s1-execution-v2-fixture",
        execution_hash="b" * 64,
    )
    require_production_execution_open(successor)
    require_manager_command_allowed(successor, "prepare-intent")


def test_accounting_publish_crash_recovery(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "accounting-recovery")
    intent = _prepare(execution, "20260718T120500Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    event_path = next(
        (execution.directory / "attempts" / intent["attempt_id"] / "events").glob(
            "*-terminal-accounting-frozen-*.json"
        )
    )
    event_path.unlink()
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"

    def forbidden_runner(*args: object, **kwargs: object) -> object:
        raise AssertionError("accounting recovery unexpectedly queried Slurm")

    recovered = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=forbidden_runner,
    )
    assert recovered["accepted_terminal"] is True
    assert attempt_state(execution, intent["attempt_id"]).status == "terminal-accounting-frozen"
    last = read_attempt_events(execution, intent["attempt_id"])[-1]
    assert last["payload"]["recovered_after_publish"] is True

    tampered = make_execution(repo_root, scratch, "accounting-recovery-tampered")
    tampered_intent = _prepare(tampered, "20260718T120501Z-initial")
    tampered_fake = FakeSlurm(tampered, tampered_intent["attempt_id"])
    submit_intent(
        tampered,
        attempt_id=tampered_intent["attempt_id"],
        intent_sha256=tampered_intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=tampered_fake,
    )
    freeze_terminal_accounting(
        tampered,
        attempt_id=tampered_intent["attempt_id"],
        intent_sha256=tampered_intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=tampered_fake,
    )
    tampered_event = next(
        (
            tampered.directory
            / "attempts"
            / tampered_intent["attempt_id"]
            / "events"
        ).glob("*-terminal-accounting-frozen-*.json")
    )
    tampered_event.unlink()
    accounting_dir = (
        tampered.directory / "attempts" / tampered_intent["attempt_id"] / "accounting"
    )
    sacct = accounting_dir / "sacct.psv"
    sacct.chmod(0o644)
    sacct.write_text(
        sacct.read_text(encoding="utf-8").replace(
            "9001_1|9101|COMPLETED|0:0|",
            "9001_1|9101|FAILED|1:0|",
            1,
        ),
        encoding="utf-8",
    )
    checksum = accounting_dir / "SHA256SUMS"
    checksum.chmod(0o644)
    checksum.write_text(
        "".join(
            f"{sha256_file(accounting_dir / name)}  {name}\n"
            for name in ("frozen.json", "sacct.psv", "squeue.psv")
        ),
        encoding="utf-8",
    )
    try:
        freeze_terminal_accounting(
            tampered,
            attempt_id=tampered_intent["attempt_id"],
            intent_sha256=tampered_intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=forbidden_runner,
        )
    except ValueError as exc:
        assert "disagree" in str(exc)
    else:
        raise AssertionError("self-rehashed sacct tamper was accepted before terminal event")
    assert attempt_state(tampered, tampered_intent["attempt_id"]).status == (
        "released-active"
    )


def test_terminal_accounting_v1_compatibility(
    repo_root: Path, scratch: Path
) -> None:
    execution = make_execution(repo_root, scratch, "terminal-accounting-v1")
    intent = _prepare(execution, "20260718T120501Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    accounting_dir = (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    )
    accounting_dir.mkdir()
    rows = [f"{fake.job_id}|COMPLETED|0:0|10|||\n"]
    rows.extend(
        f"{fake.job_id}_{index}|COMPLETED|0:0|10|100K|200K|\n"
        for index in range(1, 33)
    )
    sacct_psv = "".join(rows)
    task_states = {str(index): "COMPLETED" for index in range(1, 33)}
    task_exit_codes = {str(index): "0:0" for index in range(1, 33)}
    payload = {
        "schema_version": ACCOUNTING_SCHEMA_VERSION_V1,
        "created_at_utc": "2026-07-18T12:05:01+00:00",
        "created_by": "fixture-reviewer",
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
        "attempt_id": intent["attempt_id"],
        "intent_sha256": intent["intent_sha256"],
        "job_id": fake.job_id,
        "selected_task_count": 32,
        "terminal_task_count": 32,
        "accepted_terminal": True,
        "all_tasks_completed": True,
        "task_states": task_states,
        "task_exit_codes": task_exit_codes,
    }
    (accounting_dir / "frozen.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (accounting_dir / "sacct.psv").write_text(sacct_psv, encoding="utf-8")
    (accounting_dir / "squeue.psv").write_text("", encoding="utf-8")
    manifest = accounting_dir / "SHA256SUMS"
    manifest.write_text(
        "".join(
            f"{sha256_file(accounting_dir / name)}  {name}\n"
            for name in ("frozen.json", "sacct.psv", "squeue.psv")
        ),
        encoding="utf-8",
    )
    validate_terminal_accounting_snapshot(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        job_id=fake.job_id,
        payload=payload,
        sacct_psv=sacct_psv,
        squeue_psv="",
    )
    append_attempt_event(
        execution,
        intent["attempt_id"],
        "terminal-accounting-frozen",
        {
            "job_id": fake.job_id,
            "accounting_manifest_sha256": sha256_file(manifest),
            "all_tasks_completed": True,
        },
        actor="fixture-reviewer",
    )
    loaded = load_frozen_accounting(execution, intent["attempt_id"])
    assert loaded["schema_version"] == ACCOUNTING_SCHEMA_VERSION_V1
    assert _successful_array_indexes(
        execution, intent["attempt_id"], loaded, fake.job_id
    ) == set(range(1, 33))


def test_empty_raw_job_id_rejected_after_publish_crash(
    repo_root: Path, scratch: Path
) -> None:
    execution = make_execution(repo_root, scratch, "empty-raw-id-recovery")
    intent = _prepare(execution, "20260718T120503Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    freeze_terminal_accounting(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    event = next(
        (
            execution.directory / "attempts" / intent["attempt_id"] / "events"
        ).glob("*-terminal-accounting-frozen-*.json")
    )
    event.unlink()
    accounting = (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    )
    sacct = accounting / "sacct.psv"
    sacct.chmod(0o644)
    sacct.write_text(
        sacct.read_text(encoding="utf-8").replace(
            "9001_1|9101|COMPLETED", "9001_1||COMPLETED", 1
        ),
        encoding="utf-8",
    )
    frozen = accounting / "frozen.json"
    frozen.chmod(0o644)
    payload = json.loads(frozen.read_text(encoding="utf-8"))
    payload["task_job_ids_raw"]["1"] = ""
    frozen.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    manifest = accounting / "SHA256SUMS"
    manifest.chmod(0o644)
    manifest.write_text(
        "".join(
            f"{sha256_file(accounting / name)}  {name}\n"
            for name in ("frozen.json", "sacct.psv", "squeue.psv")
        ),
        encoding="utf-8",
    )

    def forbidden_runner(*args: object, **kwargs: object) -> object:
        raise AssertionError("self-rehashed accounting unexpectedly queried Slurm")

    try:
        freeze_terminal_accounting(
            execution,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=forbidden_runner,
        )
    except ValueError as exc:
        assert "job-ID maps disagree" in str(exc)
    else:
        raise AssertionError("empty JobIDRaw provenance was accepted after a crash")


def test_invalid_accounting_actor_zero_write(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "invalid-accounting-actor")
    intent = _prepare(execution, "20260718T120502Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    calls_before = len(fake.calls)
    try:
        freeze_terminal_accounting(
            execution,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor="invalid actor",
            runner=fake,
        )
    except ValueError as exc:
        assert "actor identity" in str(exc)
    else:
        raise AssertionError("invalid accounting actor was accepted")
    assert len(fake.calls) == calls_before
    assert not (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    ).exists()


def test_duplicate_accounting_rejected(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "duplicate-accounting")
    intent = _prepare(execution, "20260718T120600Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="duplicate-accounting")
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    try:
        freeze_terminal_accounting(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError as exc:
        assert "exact array-task" in str(exc)
    else:
        raise AssertionError("duplicate sacct array-task row was accepted")
    assert not (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    ).exists()


def test_missing_accounting_rejected(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "missing-accounting")
    intent = _prepare(execution, "20260718T120650Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="missing-accounting")
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    try:
        freeze_terminal_accounting(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError as exc:
        assert "exact array-task" in str(exc)
    else:
        raise AssertionError("incomplete logical sacct array-task set was accepted")
    assert not (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    ).exists()


def test_all_failed_accounting_enables_full_retry(
    repo_root: Path, scratch: Path
) -> None:
    execution = make_execution(repo_root, scratch, "all-failed-accounting")
    intent = _prepare(execution, "20260718T120655Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="all-failed-accounting")
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    accounting = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    assert accounting["terminal_task_count"] == 32
    assert accounting["all_tasks_completed"] is False
    assert set(accounting["task_states"].values()) == {"FAILED"}
    assert set(accounting["task_exit_codes"].values()) == {"1:0"}
    assert _successful_array_indexes(
        execution, intent["attempt_id"], accounting, "9001"
    ) == set()
    assert len(selected_task_ids(execution, "retry-failed")) == 32


def test_pre_simulation_incident_sealing(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "pre-simulation-incident")
    intent = _prepare(execution, "20260718T120650Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="all-failed-accounting")
    submit_intent(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    attempt_dir = execution.directory / "attempts" / intent["attempt_id"]
    for index in range(1, 33):
        (attempt_dir / f"slurm-{fake.job_id}_{index}.out").write_text(
            EXPECTED_FAILURE_LINE + "\n", encoding="utf-8"
        )
    readiness = {
        "path": "/fixture/readiness.json",
        "sha256": "a" * 64,
        "schema_version": "fixture-readiness-v1",
        "status": "accepted-fixture",
        "implementation_commit": "f" * 40,
        "readiness_commit": "e" * 40,
    }
    foreign_log = attempt_dir / "slurm-9999_1.out"
    foreign_log.write_text("foreign scheduler output\n", encoding="utf-8")
    try:
        collect_incident_evidence(
            execution,
            repo_root=repo_root,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=fake,
            historical_readiness=readiness,
        )
    except ValueError as exc:
        assert "foreign Slurm log" in str(exc)
    else:
        raise AssertionError("a foreign-job Slurm log was accepted")
    assert not (attempt_dir / "accounting").exists()
    calls_before_bad_local_evidence = len(fake.calls)
    events_before_bad_local_evidence = read_attempt_events(
        execution, intent["attempt_id"]
    )
    try:
        publish_incident_bundle(
            execution,
            repo_root=repo_root,
            out_parent=scratch / "bad-local-evidence-incident-bundles",
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=fake,
            historical_readiness=readiness,
        )
    except ValueError as exc:
        assert "foreign Slurm log" in str(exc)
    else:
        raise AssertionError("bad local evidence wrote terminal incident state")
    assert len(fake.calls) == calls_before_bad_local_evidence
    assert read_attempt_events(
        execution, intent["attempt_id"]
    ) == events_before_bad_local_evidence
    assert not (attempt_dir / "accounting").exists()
    foreign_log.unlink()
    calls_before_authority_rejection = len(fake.calls)
    wrong_readiness = dict(readiness, sha256="b" * 64)
    try:
        publish_incident_bundle(
            execution,
            repo_root=repo_root,
            out_parent=scratch / "rejected-incident-bundles",
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor="fixture-reviewer",
            runner=fake,
            historical_readiness=wrong_readiness,
        )
    except ValueError as exc:
        assert "intent/readiness digest mismatch" in str(exc)
    else:
        raise AssertionError("mismatched incident authority was allowed to publish")
    assert len(fake.calls) == calls_before_authority_rejection
    assert not (attempt_dir / "accounting").exists()
    preview = collect_incident_evidence(
        execution,
        repo_root=repo_root,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
        historical_readiness=readiness,
    )
    assert preview["classification"] == "pre-simulation-control-plane-failure"
    assert preview["accepted_incident_evidence"] is False
    assert preview["simulation_disposition"]["events_consumed"] == 0
    assert preview["simulation_disposition"]["production_seeds_consumed"] == 0
    assert len(preview["failure_logs"]) == 32
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
    assert not (attempt_dir / "accounting").exists()

    output_parent = scratch / "incident-bundles"
    target = publish_incident_bundle(
        execution,
        repo_root=repo_root,
        out_parent=output_parent,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
        historical_readiness=readiness,
    )
    assert attempt_state(execution, intent["attempt_id"]).status == (
        "terminal-accounting-frozen"
    )
    incident = json.loads((target / "incident.json").read_text(encoding="utf-8"))
    assert incident["attempt"]["job_id"] == fake.job_id
    assert incident["attempt"]["terminal_event_sha256"]
    assert incident["attempt"]["accounting_manifest_sha256"]
    assert incident["scheduler"]["array_parent_row_present"] is False
    assert incident["scheduler"]["task_job_ids_raw"]["32"] == fake.job_id
    assert (target / "SHA256SUMS").is_file()
    assert _existing_incident(
        output_parent,
        execution=execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        job_id="different-job",
    ) is None

    calls_before = len(fake.calls)
    assert publish_incident_bundle(
        execution,
        repo_root=repo_root,
        out_parent=output_parent,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="different-fixture-reviewer",
        runner=fake,
        historical_readiness=readiness,
    ) == target
    assert len(fake.calls) == calls_before
    assert len(selected_task_ids(execution, "resume")) == 32

    advanced_control = dict(incident["recovery_control_plane"])
    advanced_control["git_commit"] = "1" * 40
    advanced_control["git_tree"] = "2" * 40
    with mock.patch(
        "seal_steel_module_production_phase2b_incident."
        "_recovery_control_plane_identity",
        return_value=advanced_control,
    ):
        assert publish_incident_bundle(
            execution,
            repo_root=repo_root,
            out_parent=output_parent,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor="future-clean-checkout-reviewer",
            runner=fake,
            historical_readiness=readiness,
        ) == target
    assert len(fake.calls) == calls_before

    tampered_parent = scratch / "tampered-incident-bundles"
    tampered_parent.mkdir()
    tampered = tampered_parent / target.name
    shutil.copytree(target, tampered)
    event_chain = tampered / "event_chain.json"
    event_chain.chmod(0o644)
    event_chain.write_text("[]\n", encoding="utf-8")
    checksum = tampered / "SHA256SUMS"
    checksum.chmod(0o644)
    names = sorted(
        path.name
        for path in tampered.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    checksum.write_text(
        "".join(f"{sha256_file(tampered / name)}  {name}\n" for name in names),
        encoding="utf-8",
    )
    try:
        validate_incident_bundle(tampered, execution=execution)
    except ValueError as exc:
        assert "event chain" in str(exc)
    else:
        raise AssertionError("a self-rehashed cross-file incident tamper was accepted")

    accounting_tamper_parent = scratch / "accounting-tamper-incident-bundles"
    accounting_tamper_parent.mkdir()
    accounting_tamper = accounting_tamper_parent / target.name
    shutil.copytree(target, accounting_tamper)
    bundled_accounting = accounting_tamper / "accounting.json"
    bundled_accounting.chmod(0o644)
    accounting_value = json.loads(
        bundled_accounting.read_text(encoding="utf-8")
    )
    accounting_value["execution_hash"] = "c" * 64
    bundled_accounting.write_text(
        json.dumps(accounting_value, indent=2) + "\n", encoding="utf-8"
    )
    accounting_checksum = accounting_tamper / "SHA256SUMS"
    accounting_checksum.chmod(0o644)
    accounting_names = sorted(
        path.name
        for path in accounting_tamper.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    accounting_checksum.write_text(
        "".join(
            f"{sha256_file(accounting_tamper / name)}  {name}\n"
            for name in accounting_names
        ),
        encoding="utf-8",
    )
    try:
        validate_incident_bundle(accounting_tamper)
    except ValueError as exc:
        assert "accounting binding" in str(exc)
    else:
        raise AssertionError("a self-rehashed accounting identity tamper was accepted")


def test_concurrent_incident_publication(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "concurrent-incident")
    intent = _prepare(execution, "20260718T120659Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="all-failed-accounting")
    submit_intent(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    attempt_dir = execution.directory / "attempts" / intent["attempt_id"]
    for index in range(1, 33):
        (attempt_dir / f"slurm-{fake.job_id}_{index}.out").write_text(
            EXPECTED_FAILURE_LINE + "\n", encoding="utf-8"
        )
    freeze_terminal_accounting(
        execution,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer",
        runner=fake,
    )
    calls_before = len(fake.calls)
    readiness = {
        "path": "/fixture/readiness.json",
        "sha256": "a" * 64,
        "schema_version": "fixture-readiness-v1",
        "status": "accepted-fixture",
        "implementation_commit": "f" * 40,
        "readiness_commit": "e" * 40,
    }
    output_parent = scratch / "concurrent-incident-bundles"

    def publish(actor: str) -> tuple[str, str]:
        try:
            path = publish_incident_bundle(
                execution,
                repo_root=repo_root,
                out_parent=output_parent,
                attempt_id=intent["attempt_id"],
                intent_sha256=intent["intent_sha256"],
                actor=actor,
                runner=fake,
                historical_readiness=readiness,
            )
        except ValueError as exc:
            return "rejected", str(exc)
        return "published", str(path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                publish,
                ("fixture-reviewer-one", "fixture-reviewer-two"),
            )
        )
    bundles = sorted(
        path
        for path in output_parent.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    assert len(bundles) == 1
    assert any(kind == "published" for kind, _ in outcomes)
    assert all(
        kind == "published"
        or "publication is already active" in message
        or "refusing to overwrite" in message
        for kind, message in outcomes
    )
    assert not any(path.name.startswith(".") for path in output_parent.iterdir())
    assert len(fake.calls) == calls_before
    validate_incident_bundle(bundles[0], execution=execution)
    assert publish_incident_bundle(
        execution,
        repo_root=repo_root,
        out_parent=output_parent,
        attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer-three",
        runner=fake,
        historical_readiness=readiness,
    ) == bundles[0]
    assert len(fake.calls) == calls_before


def test_cancelled_accounting_enables_retry(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "cancelled-accounting")
    intent = _prepare(execution, "20260718T120700Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="cancelled-accounting")
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    accounting = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    assert accounting["all_tasks_completed"] is False
    assert accounting["task_states"]["1"] == "CANCELLED by 1234"
    assert attempt_state(execution, intent["attempt_id"]).status == "terminal-accounting-frozen"
    assert len(selected_task_ids(execution, "retry-failed")) == 32


def test_single_nonterminal_intent_gate(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "single-nonterminal-intent")
    first = _prepare(execution, "20260718T120800Z-initial")
    try:
        prepare_attempt_intent(
            execution, attempt_id="20260718T120900Z-resume", mode="resume",
            actor="fixture-reviewer", write=False,
            readiness_lock_sha256="b" * 64,
        )
    except ValueError as exc:
        assert "prior intent" in str(exc)
    else:
        raise AssertionError("a second intent was prepared while the first was non-terminal")

    append_attempt_event(
        execution, first["attempt_id"], "intent-cancelled",
        {"reason": "fixture creates a second intent before restoring the first"},
        actor="fixture-reviewer",
    )
    second = prepare_attempt_intent(
        execution, attempt_id="20260718T120900Z-resume", mode="resume",
        actor="fixture-reviewer", write=True, readiness_lock_sha256="b" * 64,
    )
    cancellation = next(
        (execution.directory / "attempts" / first["attempt_id"] / "events").glob(
            "*-intent-cancelled-*.json"
        )
    )
    cancellation.unlink()
    fake = FakeSlurm(execution, second["attempt_id"])
    try:
        submit_intent(
            execution, attempt_id=first["attempt_id"],
            intent_sha256=first["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("submission ignored a competing prepared intent")
    assert not any(Path(command[0]).name == "sbatch" for command in fake.calls)


def test_accounting_event_binding(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "accounting-event-binding")
    intent = _prepare(execution, "20260718T120950Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    accounting = (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    )
    frozen = accounting / "frozen.json"
    value = json.loads(frozen.read_text())
    value["all_tasks_completed"] = False
    frozen.chmod(0o644)
    frozen.write_text(json.dumps(value, indent=2) + "\n")
    files = ("frozen.json", "sacct.psv", "squeue.psv")
    (accounting / "SHA256SUMS").chmod(0o644)
    (accounting / "SHA256SUMS").write_text(
        "".join(f"{sha256_file(accounting / name)}  {name}\n" for name in files)
    )
    try:
        load_frozen_accounting(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "event binding" in str(exc)
    else:
        raise AssertionError("self-rehashed accounting escaped its terminal-event binding")


def test_ambiguity(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "ambiguity")
    intent = _prepare(execution, "20260718T121000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="timeout")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
        )
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("submission timeout was accepted")
    assert attempt_state(execution, intent["attempt_id"]).unresolved
    fake.mode = "zero"
    assert reconcile_attempt(
        execution, attempt_id=intent["attempt_id"], intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer", runner=fake, now_utc="2026-07-18T12:00:00+00:00",
    ) == "zero-match-observation-recorded"
    assert reconcile_attempt(
        execution, attempt_id=intent["attempt_id"], intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer", runner=fake, now_utc="2026-07-18T12:11:00+00:00",
        confirm_no_job=True, rationale="Two independent OSC observations found no job.",
    ) == "no-job-confirmed"
    assert attempt_state(execution, intent["attempt_id"]).status == "cancelled"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1


def test_submission_failure_shapes(repo_root: Path, scratch: Path) -> None:
    for suffix, mode in (("nonnumeric", "non-numeric"), ("nonzero", "non-zero")):
        execution = make_execution(repo_root, scratch, suffix)
        intent = _prepare(execution, f"20260718T1215{len(suffix):02d}Z-initial")
        fake = FakeSlurm(execution, intent["attempt_id"], mode=mode)
        try:
            submit_intent(
                execution, attempt_id=intent["attempt_id"],
                intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
                runner=fake,
            )
        except ValueError as exc:
            assert "ambiguous" in str(exc)
        else:
            raise AssertionError(f"{mode} sbatch result was accepted")
        assert attempt_state(execution, intent["attempt_id"]).unresolved
        assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1


def test_submission_adoption_and_multiple_match(repo_root: Path, scratch: Path) -> None:
    adopted = make_execution(repo_root, scratch, "adopted")
    intent = _prepare(adopted, "20260718T121700Z-initial")
    fake = FakeSlurm(adopted, intent["attempt_id"], mode="accepted-timeout")
    try:
        submit_intent(
            adopted, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted-but-lost submission receipt was not quarantined")
    assert reconcile_attempt(
        adopted, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    ) == "reconciled-same-job:9001:PENDING"
    assert attempt_state(adopted, intent["attempt_id"]).status == "released-active"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1

    multiple = make_execution(repo_root, scratch, "multiple")
    multi_intent = _prepare(multiple, "20260718T121800Z-initial")
    multi_fake = FakeSlurm(multiple, multi_intent["attempt_id"], mode="multi")
    try:
        submit_intent(
            multiple, attempt_id=multi_intent["attempt_id"],
            intent_sha256=multi_intent["intent_sha256"], actor="fixture-reviewer",
            runner=multi_fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("multiple-match fixture did not enter quarantine")
    try:
        reconcile_attempt(
            multiple, attempt_id=multi_intent["attempt_id"],
            intent_sha256=multi_intent["intent_sha256"], actor="fixture-reviewer",
            runner=multi_fake,
        )
    except ValueError as exc:
        assert "multiple" in str(exc)
    else:
        raise AssertionError("multiple scheduler matches were adopted")
    assert attempt_state(multiple, multi_intent["attempt_id"]).unresolved
    assert sum(1 for command in multi_fake.calls if Path(command[0]).name == "sbatch") == 1


def test_release_reconcile(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "release")
    intent = _prepare(execution, "20260718T122000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="release-timeout")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
        )
    except ValueError as exc:
        assert "release" in str(exc)
    else:
        raise AssertionError("release timeout was accepted")
    before = sum(1 for command in fake.calls if Path(command[0]).name == "sbatch")
    result = reconcile_attempt(
        execution, attempt_id=intent["attempt_id"], intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer", runner=fake,
    )
    assert result == "reconciled-same-job:9001:RUNNING"
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == before


def test_release_zero_match_stays_quarantined(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "release-zero")
    intent = _prepare(execution, "20260718T122500Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="release-timeout")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("release timeout was not quarantined")
    fake.mode = "zero"
    assert reconcile_attempt(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    ) == "release-zero-match-observation-recorded"
    try:
        reconcile_attempt(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake, confirm_no_job=True, rationale="not sufficient",
        )
    except ValueError as exc:
        assert "known submitted job" in str(exc)
    else:
        raise AssertionError("known released job was cleared as never submitted")
    assert attempt_state(execution, intent["attempt_id"]).unresolved


def _seed_known_held_state(
    execution: object, intent: dict[str, object], *, verified: bool
) -> None:
    append_attempt_event(
        execution, intent["attempt_id"], "submission-invoked",
        {"command_sha256": "a" * 64, "job_name": intent["scheduler"]["job_name"]},
        actor="fixture-reviewer",
    )
    append_attempt_event(
        execution, intent["attempt_id"], "submitted-held",
        {"job_id": "9001"}, actor="fixture-reviewer",
    )
    if verified:
        append_attempt_event(
            execution, intent["attempt_id"], "job-verified",
            {"job_id": "9001", "job_name": intent["scheduler"]["job_name"],
             "state": "PENDING"}, actor="fixture-reviewer",
        )


def test_known_held_crash_recovery(repo_root: Path, scratch: Path) -> None:
    for suffix, verified in (("submitted-crash", False), ("verified-crash", True)):
        execution = make_execution(repo_root, scratch, suffix)
        intent = _prepare(
            execution,
            "20260718T124000Z-initial" if not verified
            else "20260718T124100Z-initial",
        )
        _seed_known_held_state(execution, intent, verified=verified)
        fake = FakeSlurm(execution, intent["attempt_id"])
        result = reconcile_attempt(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
        assert result.startswith("reconciled-same-job:9001:")
        assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
        assert not any(Path(command[0]).name == "sbatch" for command in fake.calls)
        assert sum(
            1 for command in fake.calls
            if Path(command[0]).name == "scontrol" and command[1] == "release"
        ) == 1

    released = make_execution(repo_root, scratch, "verified-after-release-crash")
    released_intent = _prepare(released, "20260718T124200Z-initial")
    _seed_known_held_state(released, released_intent, verified=True)
    released_fake = FakeSlurm(
        released, released_intent["attempt_id"], mode="released-reconcile"
    )
    result = reconcile_attempt(
        released, attempt_id=released_intent["attempt_id"],
        intent_sha256=released_intent["intent_sha256"],
        actor="fixture-reviewer", runner=released_fake,
    )
    assert result == "reconciled-same-job:9001:RUNNING"
    assert attempt_state(released, released_intent["attempt_id"]).status == "released-active"
    assert not any(Path(command[0]).name == "sbatch" for command in released_fake.calls)
    assert not any(
        Path(command[0]).name == "scontrol" and command[1] == "release"
        for command in released_fake.calls
    )

    terminal = make_execution(repo_root, scratch, "verified-terminal-sacct")
    terminal_intent = _prepare(terminal, "20260718T124300Z-initial")
    _seed_known_held_state(terminal, terminal_intent, verified=True)
    terminal_fake = FakeSlurm(
        terminal, terminal_intent["attempt_id"],
        mode="scontrol-missing-terminal",
    )
    result = reconcile_attempt(
        terminal, attempt_id=terminal_intent["attempt_id"],
        intent_sha256=terminal_intent["intent_sha256"],
        actor="fixture-reviewer", runner=terminal_fake,
    )
    assert result == "reconciled-terminal-sacct:9001:COMPLETED"
    assert attempt_state(terminal, terminal_intent["attempt_id"]).status == "released-active"
    assert not any(Path(command[0]).name == "sbatch" for command in terminal_fake.calls)
    frozen = freeze_terminal_accounting(
        terminal, attempt_id=terminal_intent["attempt_id"],
        intent_sha256=terminal_intent["intent_sha256"],
        actor="fixture-reviewer", runner=terminal_fake,
    )
    assert frozen["all_tasks_completed"] is True


def test_event_concurrency_and_path_escape(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "event-concurrency")
    intent = _prepare(execution, "20260718T123500Z-initial")
    staging = (
        execution.directory / "attempts" / intent["attempt_id"] / "event-staging"
    )
    staging.mkdir(parents=True)
    (staging / ".unpublished-event-crash-residual").write_text(
        "partial", encoding="utf-8"
    )

    def cancel() -> str:
        try:
            append_attempt_event(
                execution, intent["attempt_id"], "intent-cancelled",
                {"reason": "concurrent-fixture"}, actor="fixture-reviewer",
            )
        except ValueError:
            return "rejected"
        return "written"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: cancel(), range(2)))
    assert outcomes == ["rejected", "written"]
    assert len(read_attempt_events(execution, intent["attempt_id"])) == 1
    assert attempt_state(execution, intent["attempt_id"]).status == "cancelled"
    assert not staging.exists()

    escaped = make_execution(repo_root, scratch, "event-symlink")
    escaped_intent = _prepare(escaped, "20260718T123600Z-initial")
    outside = scratch / "outside-events"
    outside.mkdir()
    attempt_link = escaped.directory / "attempts" / escaped_intent["attempt_id"]
    attempt_link.symlink_to(outside, target_is_directory=True)
    try:
        append_attempt_event(
            escaped, escaped_intent["attempt_id"], "intent-cancelled",
            {"reason": "symlink-fixture"}, actor="fixture-reviewer",
        )
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("symlinked attempt directory was accepted")
    assert not list(outside.iterdir())


def test_array_tamper(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "array")
    intent = _prepare(execution, "20260718T123000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="bad-array")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
        )
    except ValueError as exc:
        assert "quarantined" in str(exc)
    else:
        raise AssertionError("wrong Slurm array shape was accepted")
    assert attempt_state(execution, intent["attempt_id"]).status == "release-ambiguous"

    held = make_execution(repo_root, scratch, "held-reason")
    held_intent = _prepare(held, "20260718T123100Z-initial")
    held_fake = FakeSlurm(held, held_intent["attempt_id"], mode="not-user-held")
    try:
        submit_intent(
            held, attempt_id=held_intent["attempt_id"],
            intent_sha256=held_intent["intent_sha256"],
            actor="fixture-reviewer", runner=held_fake,
        )
    except ValueError as exc:
        assert "quarantined" in str(exc)
    else:
        raise AssertionError("non-user-held Slurm job was released")
    assert attempt_state(held, held_intent["attempt_id"]).status == "release-ambiguous"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    help_result = subprocess.run(
        [sys.executable, "hpc/osc/manage_steel_module_production_attempt.py", "--help"],
        cwd=repo_root, text=True, stdout=subprocess.PIPE, check=True,
    )
    assert "sbatch-command" not in help_result.stdout
    assert "execution-dir" not in help_result.stdout
    incident_help = subprocess.run(
        [
            sys.executable,
            "hpc/osc/seal_steel_module_production_phase2b_incident.py",
            "--help",
        ],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    for forbidden in ("execution-dir", "attempt-id", "job-id", "scheduler-command"):
        assert forbidden not in incident_help.stdout
    with tempfile.TemporaryDirectory(prefix="steel-module-phase2b-control-") as temporary:
        scratch = Path(temporary)
        sentinel = scratch / "real-sbatch-was-invoked"
        sentinel_bin = scratch / "sentinel-bin"
        sentinel_bin.mkdir()
        sbatch = sentinel_bin / "sbatch"
        sbatch.write_text(
            "#!/usr/bin/env bash\nprintf invoked > " + str(sentinel) + "\nexit 99\n",
            encoding="utf-8",
        )
        sbatch.chmod(0o755)
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(sentinel_bin) + os.pathsep + original_path
        test_portable_control_lock_v2(repo_root, scratch)
        test_inode_bound_control_lock_v1(repo_root, scratch)
        test_exact_historical_v1_recovery_lock(repo_root, scratch)
        test_exact_historical_event_lineage()
        test_exact_historical_event_lineage_integration(repo_root, scratch)
        test_materialization(repo_root, scratch)
        test_readiness_protected_artifact_set(scratch)
        test_intent_contract_tamper(repo_root, scratch)
        test_success(repo_root, scratch)
        test_account_case_canonicalization(repo_root, scratch)
        test_historical_execution_is_explicitly_closed()
        test_accounting_publish_crash_recovery(repo_root, scratch)
        test_terminal_accounting_v1_compatibility(repo_root, scratch)
        test_empty_raw_job_id_rejected_after_publish_crash(repo_root, scratch)
        test_invalid_accounting_actor_zero_write(repo_root, scratch)
        test_duplicate_accounting_rejected(repo_root, scratch)
        test_missing_accounting_rejected(repo_root, scratch)
        test_all_failed_accounting_enables_full_retry(repo_root, scratch)
        test_pre_simulation_incident_sealing(repo_root, scratch)
        test_concurrent_incident_publication(repo_root, scratch)
        test_cancelled_accounting_enables_retry(repo_root, scratch)
        test_single_nonterminal_intent_gate(repo_root, scratch)
        test_accounting_event_binding(repo_root, scratch)
        test_ambiguity(repo_root, scratch)
        test_submission_failure_shapes(repo_root, scratch)
        test_submission_adoption_and_multiple_match(repo_root, scratch)
        test_release_reconcile(repo_root, scratch)
        test_release_zero_match_stays_quarantined(repo_root, scratch)
        test_known_held_crash_recovery(repo_root, scratch)
        test_array_tamper(repo_root, scratch)
        test_event_concurrency_and_path_escape(repo_root, scratch)
        assert not sentinel.exists(), "a readiness test invoked the real sbatch command"
        os.environ["PATH"] = original_path
    print("steel-module Phase-2B control plane: PASS")
    print("scheduler: in-process fake only; real Slurm calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
