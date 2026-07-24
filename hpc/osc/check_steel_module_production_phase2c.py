#!/usr/bin/env python3
"""No-scheduler focused checks for Phase-2C twin, preflight, and execution-v6.

The fixture intentionally reuses the complete synthetic execution-v5 lineage
from the existing successor checker.  Every scheduler interaction below is
handled by an injected fake runner while executable sentinels shadow the real
OSC command names on ``PATH``.  Synthetic evidence is never accepted compute
or statistical evidence.
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence
from unittest import mock

import manage_steel_module_production_attempt as production_manager
import run_steel_module_production_phase2b_task as production_worker
import steel_module_production_phase2b_lib as phase2b_lib
import steel_module_production_phase2c_lib as phase2c_lib
import steel_module_production_phase2c_preflight_lib as preflight_lib
import steel_module_production_phase2c_readiness as readiness_lib
from check_steel_module_production_successor import _scheduler_sentinels
from check_steel_module_production_successor_v5 import _make_v5_fixture
from steel_module_campaign_lib import sha256_file
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME_V5,
    materialize_successor_v5_execution_companion,
)


ACTOR = "phase2c-fixture-reviewer"
JOB_ID = "70000626"


def _assert_rejected(callable_value: Any, message: str) -> str:
    try:
        callable_value()
    except (OSError, RuntimeError, ValueError) as exc:
        return str(exc)
    raise AssertionError(message)


def _write_c6_acceptance_fixture(
    parent: Path, payload: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    value = json.loads(json.dumps(payload))
    value["acceptance_id"] = None
    value["acceptance_hash"] = None
    digest = hashlib.sha256(readiness_lib.canonical_json(value)).hexdigest()
    value["acceptance_hash"] = digest
    value["acceptance_id"] = (
        "sm-v1-phase2c-c6-acceptance-" + digest[:12]
    )
    directory = parent / (
        readiness_lib.ACCEPTANCE_DIRECTORY_PREFIX + digest[:12]
    )
    directory.mkdir(parents=True)
    acceptance_json = directory / "acceptance.json"
    acceptance_json.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "SHA256SUMS").write_text(
        f"{sha256_file(acceptance_json)}  acceptance.json\n",
        encoding="utf-8",
    )
    return directory, value


def _copy_phase2c_control_source(repo_root: Path, target: Path) -> Path:
    target.mkdir(parents=True)
    for relative in phase2c_lib.CRITICAL_CONTROL_BLOBS:
        source = repo_root / relative
        assert source.is_file() and not source.is_symlink(), relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


def _fixture_v5_evidence(_predecessor: Any, _path: Path) -> dict[str, Any]:
    return {
        "schema_version": "steel-module-r3-test-evidence-v1",
        "test_mode": True,
        "accepted_compute_preflight_evidence": False,
        "evidence_id": "fixture-v5-success-evidence",
        "evidence_hash": "5" * 64,
        "job_id": "70000555",
        "apptainer_invoked": True,
        "geant4_invoked": False,
        "events_consumed": 0,
        "production_seeds_consumed": 0,
    }


def _materialize_twin(repo_root: Path, scratch: Path) -> tuple[Any, Any, Path]:
    scratch.mkdir(parents=True)
    lineage = scratch / "v5-lineage"
    lineage.mkdir()
    _v4, _failure, _failure_dir, inputs = _make_v5_fixture(repo_root, lineage)
    v5_parent = scratch / "work/campaigns"
    v5_parent.mkdir(parents=True)
    v5 = materialize_successor_v5_execution_companion(
        **inputs,
        out_dir=v5_parent / FORMAL_SUCCESSOR_EXECUTION_NAME_V5,
    )
    control = _copy_phase2c_control_source(
        repo_root, scratch / "phase2c-control-source"
    )
    target = v5_parent / phase2c_lib.FORMAL_PREFLIGHT_V6_NAME
    twin = phase2c_lib.materialize_preflight_v6(
        repo_root=repo_root,
        v5_dir=v5.directory,
        v5_evidence_dir=scratch / "fixture-v5-evidence",
        out_dir=target,
        test_mode=True,
        fixture_control_source=control,
        v5_evidence_validator=_fixture_v5_evidence,
        v5_execution_loader=lambda _path: v5,
    )
    assert twin.manifest["accepted_predecessor_evidence"] is False
    assert twin.manifest["production_authority"] is False
    assert twin.tasks == v5.tasks
    assert twin.scan_args == v5.scan_args
    assert twin.production_equivalence_hash == (
        twin.manifest["production_equivalence_hash"]
    )
    return v5, twin, control


def _context(twin: Any, evidence_root: Path) -> preflight_lib.PreflightContext:
    evidence_root.parent.mkdir(parents=True, exist_ok=True)
    evidence_root = evidence_root.resolve(strict=False)
    return preflight_lib.PreflightContext(
        twin=twin,
        directory=twin.directory,
        manifest=twin.manifest,
        evidence_root=evidence_root,
        twin_id=twin.twin_id,
        twin_hash=twin.twin_hash,
        production_equivalence_hash=twin.production_equivalence_hash,
    )


def _held_scontrol_row(
    context: preflight_lib.PreflightContext, job_id: str
) -> str:
    launcher = context.directory / preflight_lib.FORMAL_LAUNCHER_RELATIVE
    output = context.control_root / "slurm" / f"slurm-{job_id}.out"
    return " ".join(
        (
            f"JobId={job_id}",
            f"JobName={context.job_name}",
            "Account=pas2524",
            "JobState=PENDING",
            "Reason=JobHeldUser",
            "Requeue=0",
            "TimeLimit=00:10:00",
            "NumCPUs=1",
            "NumTasks=1",
            "CPUs/Task=1",
            "MinMemoryNode=1G",
            f"Command={launcher}",
            f"WorkDir={context.directory}",
            f"StdOut={output}",
            f"StdErr={output}",
        )
    ) + "\n"


class FakePreflightSlurm:
    def __init__(
        self,
        context: preflight_lib.PreflightContext,
        *,
        terminal_success: bool = True,
        job_id: str = JOB_ID,
    ) -> None:
        self.context = context
        self.terminal_success = terminal_success
        self.job_id = job_id
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        values = tuple(str(value) for value in command)
        self.commands.append(values)
        assert cwd == self.context.directory
        if values[0] == "sbatch":
            assert "--hold" in values
            assert "--parsable" in values
            assert "--no-requeue" in values
            assert "--export=NONE" in values
            assert "--array" not in values
            return subprocess.CompletedProcess(values, 0, self.job_id + "\n", "")
        if values[:4] == ("scontrol", "show", "job", self.job_id):
            return subprocess.CompletedProcess(
                values, 0, _held_scontrol_row(self.context, self.job_id), ""
            )
        if values == ("scontrol", "release", self.job_id):
            return subprocess.CompletedProcess(values, 0, "", "")
        if values[0] == "sacct":
            state = "COMPLETED" if self.terminal_success else "FAILED"
            exit_code = "0:0" if self.terminal_success else "2:0"
            rows = (
                f"{self.job_id}|{self.job_id}|{self.context.job_name}|{state}|{exit_code}|4|1K|2K\n"
                f"{self.job_id}.batch|{self.job_id}.batch|batch|{state}|{exit_code}|4|1K|2K\n"
                f"{self.job_id}.extern|{self.job_id}.extern|extern|COMPLETED|0:0|4|1K|2K\n"
            )
            return subprocess.CompletedProcess(values, 0, rows, "")
        if values[0] == "squeue":
            return subprocess.CompletedProcess(values, 0, "", "")
        raise AssertionError(f"unexpected fake scheduler command: {values}")


class TimeoutSlurm:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del command, cwd, timeout
        self.calls += 1
        raise subprocess.TimeoutExpired("sbatch", 120)


class InvalidSubmitSlurm:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, timeout
        self.calls += 1
        values = tuple(str(value) for value in command)
        assert values[0] == "sbatch"
        return subprocess.CompletedProcess(values, 0, "not-a-job\n", "")


class ReleaseTimeoutSlurm(FakePreflightSlurm):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        values = tuple(str(value) for value in command)
        if values == ("scontrol", "release", self.job_id):
            self.commands.append(values)
            raise subprocess.TimeoutExpired("scontrol release", timeout)
        return super().__call__(command, cwd=cwd, timeout=timeout)


class ReconcileSlurm:
    def __init__(
        self, context: preflight_lib.PreflightContext, job_ids: Sequence[str]
    ) -> None:
        self.context = context
        self.job_ids = tuple(job_ids)
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        values = tuple(str(value) for value in command)
        self.commands.append(values)
        assert cwd == self.context.directory
        if values[0] == "squeue":
            output = "".join(
                f"{job_id}|{self.context.job_name}|N/A|PENDING|JobHeldUser\n"
                for job_id in self.job_ids
            )
            return subprocess.CompletedProcess(values, 0, output, "")
        if values[0] == "sacct":
            output = "".join(
                f"{job_id}|{job_id}|{self.context.job_name}|PENDING|0:0\n"
                for job_id in self.job_ids
            )
            return subprocess.CompletedProcess(values, 0, output, "")
        if (
            len(self.job_ids) == 1
            and values[:4] == ("scontrol", "show", "job", self.job_ids[0])
        ):
            return subprocess.CompletedProcess(
                values,
                0,
                _held_scontrol_row(self.context, self.job_ids[0]),
                "",
            )
        raise AssertionError(f"unexpected reconciliation command: {values}")


class FakeProductionSlurm:
    """Exact held-array fake used to prove explicit v6 release admission."""

    def __init__(self, execution: Any, intent: dict[str, Any]) -> None:
        self.execution = execution
        self.intent = intent
        self.job_id = "70000627"
        self.extra_job_ids: tuple[str, ...] = ()
        self.held = True
        self.release_callback: Any = None
        self.release_timeout = False
        self.commands: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        values = tuple(str(value) for value in command)
        self.commands.append(values)
        assert cwd == self.execution.directory
        if values[0] == "sbatch":
            assert "--hold" in values and "--array" in values
            return subprocess.CompletedProcess(values, 0, self.job_id + "\n", "")
        if values[:4] == ("scontrol", "show", "job", self.job_id):
            scheduler = self.intent["scheduler"]
            wrapper = (
                self.execution.directory
                / "intents"
                / self.intent["attempt_id"]
                / scheduler["job_wrapper"]["path"]
            )
            row = " ".join(
                (
                    f"JobId={self.job_id}",
                    f"ArrayJobId={self.job_id}",
                    "ArrayTaskId=1-32",
                    f"JobName={scheduler['job_name']}",
                    "Account=pas2524",
                    f"JobState={'PENDING' if self.held else 'RUNNING'}",
                    f"Reason={'JobHeldUser' if self.held else 'None'}",
                    "Requeue=0",
                    "TimeLimit=01:00:00",
                    "NumCPUs=1",
                    "NumTasks=1",
                    "CPUs/Task=1",
                    "MinMemoryNode=2G",
                    f"Command={wrapper}",
                    f"WorkDir={self.execution.directory}",
                    f"StdOut={scheduler['output_pattern']}",
                )
            ) + "\n"
            return subprocess.CompletedProcess(values, 0, row, "")
        if values[0] == "squeue":
            row = "".join(
                f"{job_id}|{self.intent['scheduler']['job_name']}|"
                f"1-32|{'PENDING' if self.held else 'RUNNING'}|"
                f"{'JobHeldUser' if self.held else 'None'}\n"
                for job_id in (self.job_id, *self.extra_job_ids)
            )
            return subprocess.CompletedProcess(values, 0, row, "")
        if values[0] == "sacct":
            row = "".join(
                f"{job_id}|{job_id}|"
                f"{self.intent['scheduler']['job_name']}|"
                f"{'PENDING' if self.held else 'RUNNING'}|0:0\n"
                for job_id in (self.job_id, *self.extra_job_ids)
            )
            return subprocess.CompletedProcess(values, 0, row, "")
        if values == ("scontrol", "release", self.job_id):
            self.held = False
            if self.release_callback is not None:
                self.release_callback()
            if self.release_timeout:
                raise subprocess.TimeoutExpired("scontrol release", 120)
            return subprocess.CompletedProcess(values, 0, "", "")
        raise AssertionError(f"unexpected fake production command: {values}")


def _write_raw_preflight(
    context: preflight_lib.PreflightContext, *, job_id: str
) -> None:
    raw = preflight_lib._raw_directory(context)
    raw.mkdir()
    challenge = b"phase2c-deterministic-container-roundtrip\n"
    (raw / "challenge.bin").write_bytes(challenge)
    (raw / "roundtrip.bin").write_bytes(challenge)
    report = {key: True for key in preflight_lib.CONTAINER_REPORT_KEYS}
    (raw / "container-report.tsv").write_text(
        "".join(f"{key}\ttrue\n" for key in report), encoding="utf-8"
    )
    (raw / "stdout.txt").write_text("fixture preflight pass\n", encoding="utf-8")
    (raw / "stderr.txt").write_text("", encoding="utf-8")
    digest = hashlib.sha256(challenge).hexdigest()
    created = "2026-07-20T00:00:00+00:00"
    payload = {
        "schema_version": preflight_lib.RAW_RESULT_SCHEMA_VERSION,
        "created_at_utc": created,
        "test_mode": True,
        "accepted_compute_preflight_evidence": False,
        "probe_passed": True,
        "apptainer_invoked": True,
        "geant4_invoked": False,
        "events_consumed": 0,
        "production_seeds_consumed": 0,
        "twin_execution_closed": True,
        "twin_id": context.twin_id,
        "twin_hash": context.twin_hash,
        "production_equivalence_hash": context.production_equivalence_hash,
        "slurm_job_id": job_id,
        "slurm_job_name": context.job_name,
        "scheduler_account": "pas2524",
        "compute_node": {"hostname": "fixture", "slurmd_nodename": "fixture001"},
        "runtime_identity": {
            "apptainer_path": "/fixture/bin/apptainer",
            "apptainer_sha256": "a" * 64,
            "apptainer_version": "apptainer version 1.fixture",
        },
        "container_contract": {
            "cleanenv": True,
            "containall": True,
            "no_home": True,
            "no_mount": "hostfs,cwd,bind-paths",
            "fixed_read_only_mounts": 5,
            "external_read_write_mounts": 1,
            "mountinfo_contract_version": (
                preflight_lib.MOUNTINFO_CONTRACT_VERSION
            ),
            "command_sha256": "b" * 64,
            "report": report,
        },
        "return_code": 0,
        "challenge_sha256": digest,
        "roundtrip_sha256": digest,
    }
    (raw / "probe_result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    closure = {
        "schema_version": preflight_lib.CLOSURE_SCHEMA_VERSION,
        "created_at_utc": created,
        "twin_id": context.twin_id,
        "twin_hash": context.twin_hash,
        "production_equivalence_hash": context.production_equivalence_hash,
        "slurm_job_id": job_id,
        "slurm_job_name": context.job_name,
        "raw_result_sha256": sha256_file(raw / "probe_result.json"),
        "execution_closed": True,
        "future_execution_role": "clean-execution-v6",
        "events_consumed": 0,
        "production_seeds_consumed": 0,
    }
    marker = context.directory / phase2c_lib.PREFLIGHT_CLOSURE_MARKER
    marker.write_text(
        json.dumps(closure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    marker.chmod(0o400)
    preflight_lib._write_checksum_manifest(raw)
    for path in raw.iterdir():
        if path.is_file():
            path.chmod(0o444)


def test_retry_launcher_admission(repo_root: Path, scratch: Path) -> None:
    """Execute the frozen shell admission path for a retry twin name."""

    twin_hash = "a" * 64
    home = scratch / "fixture-home"
    execution = (
        home
        / "g4optics-rn/campaigns"
        / (
            "steel-module-production-bc-s1-preflight-v6-retry-01-"
            + twin_hash[:12]
        )
    )
    control = execution / "sources/control"
    runner = control / "hpc/osc/run_steel_module_production_phase2c_preflight.py"
    runner.parent.mkdir(parents=True)
    (home / "projects/g4optics").mkdir(parents=True)
    (execution / "preflight_twin.json").write_text(
        json.dumps({"twin_hash": twin_hash}) + "\n", encoding="utf-8"
    )
    runner.write_text(
        """#!/usr/bin/env python3
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--repo-root", required=True)
parser.add_argument("--execution-dir", required=True)
args = parser.parse_args()
Path(args.execution_dir, "launcher-admitted.txt").write_text(
    str(Path(args.repo_root).resolve()) + "\\n", encoding="utf-8"
)
""",
        encoding="utf-8",
    )
    fake_bin = scratch / "fake-runtime-bin"
    fake_bin.mkdir()
    fake_apptainer = fake_bin / "apptainer"
    fake_apptainer.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_apptainer.chmod(0o755)
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": str(fake_bin) + os.pathsep + environment.get("PATH", ""),
            "SLURM_JOB_ID": "70000628",
            "SLURM_JOB_NAME": "g4sm-p2c-" + twin_hash[:12],
            "SLURMD_NODENAME": "fixture001",
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(repo_root / "hpc/osc/run_steel_module_production_phase2c_preflight.sbatch"),
        ],
        cwd=execution,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    marker = execution / "launcher-admitted.txt"
    assert marker.read_text(encoding="utf-8").strip() == str(
        (home / "projects/g4optics").resolve()
    )


def _successful_preflight(
    twin: Any, scratch: Path
) -> tuple[preflight_lib.PreflightContext, Path, FakePreflightSlurm]:
    context = _context(twin, scratch / "work/evidence/steel-module-production-phase2c")
    authorization = preflight_lib.write_authorization(context, actor=ACTOR)
    authorization_hash = str(authorization["authorization_hash"])
    fake = FakePreflightSlurm(context)
    assert preflight_lib.submit_authorization(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    ) == JOB_ID
    row = preflight_lib.query_and_verify_held(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    )
    assert row["AccountComparison"] == "osc-ascii-lowercase-canonicalization"
    assert preflight_lib.preflight_state(context, authorization_hash).status == (
        "verified-held"
    )
    assert preflight_lib.release_preflight(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    ) == JOB_ID
    accounting = preflight_lib.freeze_terminal_accounting(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    )
    assert accounting["accepted_terminal_success"] is True
    _write_raw_preflight(context, job_id=JOB_ID)
    evidence = preflight_lib.seal_phase2c_preflight_evidence(
        context,
        authorization_hash=authorization_hash,
        allow_test_mode=True,
    )
    value = preflight_lib.validate_phase2c_preflight_evidence(
        evidence, expected_twin=twin, allow_test_mode=True
    )
    assert value["accepted_compute_preflight_evidence"] is False
    assert value["runtime_boundary"]["apptainer_invoked"] is True
    assert value["runtime_boundary"]["geant4_invoked"] is False
    assert value["consumption"] == {
        "events_consumed": 0,
        "production_seeds_consumed": 0,
    }
    return context, evidence, fake


def test_timeout_is_quarantined(twin: Any, scratch: Path) -> None:
    context = _context(twin, scratch / "work/evidence/steel-module-production-phase2c")
    authorization = preflight_lib.write_authorization(context, actor=ACTOR)
    digest = str(authorization["authorization_hash"])
    timeout = TimeoutSlurm()
    _assert_rejected(
        lambda: preflight_lib.submit_authorization(
            context,
            authorization_hash=digest,
            actor=ACTOR,
            runner=timeout,
        ),
        "timed-out preflight submission was accepted",
    )
    assert timeout.calls == 1
    assert preflight_lib.preflight_state(context, digest).status == (
        "submission-ambiguous"
    )
    _assert_rejected(
        lambda: preflight_lib.submit_authorization(
            context,
            authorization_hash=digest,
            actor=ACTOR,
            runner=timeout,
        ),
        "ambiguous preflight submission was retried",
    )
    assert timeout.calls == 1
    zero = ReconcileSlurm(context, ())
    outcome = preflight_lib.reconcile_preflight(
        context,
        authorization_hash=digest,
        actor=ACTOR,
        runner=zero,
    )
    assert "no exact scheduler match" in outcome
    assert preflight_lib.preflight_state(context, digest).status == (
        "submission-ambiguous"
    )
    first_observation = preflight_lib.read_events(context, digest)[-1]
    second_time = (
        datetime.fromisoformat(first_observation["created_at_utc"])
        + timedelta(seconds=601)
    ).isoformat()
    with mock.patch.object(preflight_lib, "utc_now", return_value=second_time):
        preflight_lib.reconcile_preflight(
            context,
            authorization_hash=digest,
            actor=ACTOR,
            runner=zero,
        )
    zero_preview = preflight_lib.preview_phase2c_preflight_failure_evidence(
        context,
        authorization_hash=digest,
        reviewer=ACTOR,
        rationale="two read-only scheduler snapshots found no exact job",
        allow_test_mode=True,
    )
    assert zero_preview["failure_class"] == (
        "two-snapshot-zero-match-ambiguity"
    )
    assert zero_preview["retry_eligible"] is True
    assert all(command[0] != "sbatch" for command in zero.commands)

    invalid_context = _context(
        twin,
        scratch / "invalid-submit/work/evidence/steel-module-production-phase2c",
    )
    invalid_authorization = preflight_lib.write_authorization(
        invalid_context, actor=ACTOR
    )
    invalid_hash = str(invalid_authorization["authorization_hash"])
    invalid = InvalidSubmitSlurm()
    _assert_rejected(
        lambda: preflight_lib.submit_authorization(
            invalid_context,
            authorization_hash=invalid_hash,
            actor=ACTOR,
            runner=invalid,
        ),
        "non-numeric sbatch response was accepted",
    )
    assert invalid.calls == 1
    assert preflight_lib.preflight_state(
        invalid_context, invalid_hash
    ).status == "submission-ambiguous"

    adopted_context = _context(
        twin,
        scratch / "adopt-one/work/evidence/steel-module-production-phase2c",
    )
    adopted = preflight_lib.write_authorization(adopted_context, actor=ACTOR)
    adopted_hash = str(adopted["authorization_hash"])
    adopted_timeout = TimeoutSlurm()
    _assert_rejected(
        lambda: preflight_lib.submit_authorization(
            adopted_context,
            authorization_hash=adopted_hash,
            actor=ACTOR,
            runner=adopted_timeout,
        ),
        "one-match fixture did not enter submission ambiguity",
    )
    one = ReconcileSlurm(adopted_context, (JOB_ID,))
    assert preflight_lib.reconcile_preflight(
        adopted_context,
        authorization_hash=adopted_hash,
        actor=ACTOR,
        runner=one,
    ) == f"held preflight job reconciled: {JOB_ID}"
    assert preflight_lib.preflight_state(adopted_context, adopted_hash).status == (
        "submitted-held"
    )
    preflight_lib.query_and_verify_held(
        adopted_context,
        authorization_hash=adopted_hash,
        actor=ACTOR,
        runner=FakePreflightSlurm(adopted_context),
    )
    assert preflight_lib.preflight_state(adopted_context, adopted_hash).status == (
        "verified-held"
    )
    assert adopted_timeout.calls == 1
    assert all(command[0] != "sbatch" for command in one.commands)

    multiple_context = _context(
        twin,
        scratch / "multiple/work/evidence/steel-module-production-phase2c",
    )
    multiple = preflight_lib.write_authorization(multiple_context, actor=ACTOR)
    multiple_hash = str(multiple["authorization_hash"])
    multiple_timeout = TimeoutSlurm()
    _assert_rejected(
        lambda: preflight_lib.submit_authorization(
            multiple_context,
            authorization_hash=multiple_hash,
            actor=ACTOR,
            runner=multiple_timeout,
        ),
        "multi-match fixture did not enter submission ambiguity",
    )
    many = ReconcileSlurm(multiple_context, ("70000627", "70000628"))
    _assert_rejected(
        lambda: preflight_lib.reconcile_preflight(
            multiple_context,
            authorization_hash=multiple_hash,
            actor=ACTOR,
            runner=many,
        ),
        "multiple scheduler matches were not failed closed",
    )
    assert preflight_lib.preflight_state(
        multiple_context, multiple_hash
    ).status == "closed-ambiguous"
    assert multiple_timeout.calls == 1
    assert all(command[0] != "sbatch" for command in many.commands)


def test_failed_twin_retry_lifecycle(
    repo_root: Path, twin: Any, scratch: Path
) -> None:
    failed_twin_dir = scratch / "campaigns" / "failed-preflight-v6"
    failed_twin_dir.parent.mkdir(parents=True)
    shutil.copytree(twin.directory, failed_twin_dir)
    failed_twin = phase2c_lib.load_preflight_v6(
        failed_twin_dir,
        allow_test_mode=True,
        _test_predecessor=twin.predecessor,
    )
    context = _context(
        failed_twin,
        scratch / "evidence" / "steel-module-production-phase2c",
    )
    authorization = preflight_lib.write_authorization(context, actor=ACTOR)
    authorization_hash = str(authorization["authorization_hash"])
    fake = FakePreflightSlurm(context, terminal_success=False)
    preflight_lib.submit_authorization(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    )
    preflight_lib.query_and_verify_held(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    )
    preflight_lib.release_preflight(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    )
    accounting = preflight_lib.freeze_terminal_accounting(
        context,
        authorization_hash=authorization_hash,
        actor=ACTOR,
        runner=fake,
    )
    assert accounting["accepted_terminal_success"] is False
    preview = preflight_lib.preview_phase2c_preflight_failure_evidence(
        context,
        authorization_hash=authorization_hash,
        reviewer=ACTOR,
        rationale="synthetic terminal preflight failure",
        allow_test_mode=True,
    )
    failure_dir = preflight_lib.seal_phase2c_preflight_failure_evidence(
        context,
        authorization_hash=authorization_hash,
        reviewer=ACTOR,
        rationale="synthetic terminal preflight failure",
        allow_test_mode=True,
    )
    failure = preflight_lib.validate_phase2c_preflight_failure_evidence(
        failure_dir,
        expected_twin=failed_twin,
        allow_test_mode=True,
    )
    assert failure["failure_evidence_id"] == preview["failure_evidence_id"]
    assert failure["failure_evidence_hash"] == preview["failure_evidence_hash"]
    assert failure["retry_eligible"] is True
    marker, bound_failure = phase2c_lib.validate_preflight_v6_failure(
        failed_twin, required=True
    )
    assert marker["events_consumed"] == 0
    assert bound_failure == failure
    _assert_rejected(
        lambda: phase2c_lib.materialize_execution_v6(
            repo_root=repo_root,
            preflight_dir=failed_twin.directory,
            evidence_dir=failure_dir,
            out_dir=scratch / "campaigns" / "forbidden-execution-v6",
            test_mode=True,
        ),
        "failed Phase-2C twin materialized execution-v6",
    )
    retry = phase2c_lib.materialize_preflight_v6_retry(
        repo_root=repo_root,
        previous_dir=failed_twin.directory,
        out_dir=scratch / "campaigns" / "retry-preflight-v6",
        test_mode=True,
        _test_predecessor=twin.predecessor,
    )
    assert retry.previous_twin is not None
    assert retry.previous_twin.twin_hash == failed_twin.twin_hash
    assert retry.production_equivalence_hash == failed_twin.production_equivalence_hash
    assert retry.twin_hash != failed_twin.twin_hash
    assert retry.manifest["retry_lineage"]["retry_ordinal"] == 1
    retry_context = _context(
        retry,
        context.evidence_root,
    )
    retry_authorization = preflight_lib.write_authorization(
        retry_context, actor=ACTOR
    )
    assert retry_authorization["authorization_hash"] != authorization_hash
    assert preflight_lib.preflight_state(
        retry_context, retry_authorization["authorization_hash"]
    ).scheduler_contacted is False
    retry_job_id = "70000630"
    retry_fake = FakePreflightSlurm(retry_context, job_id=retry_job_id)
    retry_hash = str(retry_authorization["authorization_hash"])
    assert preflight_lib.submit_authorization(
        retry_context,
        authorization_hash=retry_hash,
        actor=ACTOR,
        runner=retry_fake,
    ) == retry_job_id
    preflight_lib.query_and_verify_held(
        retry_context,
        authorization_hash=retry_hash,
        actor=ACTOR,
        runner=retry_fake,
    )
    preflight_lib.release_preflight(
        retry_context,
        authorization_hash=retry_hash,
        actor=ACTOR,
        runner=retry_fake,
    )
    preflight_lib.freeze_terminal_accounting(
        retry_context,
        authorization_hash=retry_hash,
        actor=ACTOR,
        runner=retry_fake,
    )
    _write_raw_preflight(retry_context, job_id=retry_job_id)
    retry_success = preflight_lib.seal_phase2c_preflight_evidence(
        retry_context,
        authorization_hash=retry_hash,
        allow_test_mode=True,
    )
    retry_execution = phase2c_lib.materialize_execution_v6(
        repo_root=repo_root,
        preflight_dir=retry.directory,
        evidence_dir=retry_success,
        out_dir=scratch / "campaigns" / "retry-derived-execution-v6",
        test_mode=True,
    )
    retry_history = retry_execution.manifest["lineage"][
        "preflight_retry_history"
    ]
    assert len(retry_history) == 1
    assert retry_history[0]["failed_twin"]["twin_hash"] == failed_twin.twin_hash
    excluded_jobs = retry_execution.manifest["lineage"][
        "excluded_nonproduction_job_ids"
    ]
    assert JOB_ID in excluded_jobs and retry_job_id in excluded_jobs
    assert len(excluded_jobs) == len(set(excluded_jobs))


def test_release_ambiguity_recovery(twin: Any, scratch: Path) -> None:
    twin_directory = scratch / "campaigns" / "release-recovery-twin"
    twin_directory.parent.mkdir(parents=True)
    shutil.copytree(twin.directory, twin_directory)
    recovery_twin = phase2c_lib.load_preflight_v6(
        twin_directory,
        allow_test_mode=True,
        _test_predecessor=twin.predecessor,
    )
    context = _context(
        recovery_twin,
        scratch / "work/evidence/steel-module-production-phase2c",
    )
    authorization = preflight_lib.write_authorization(context, actor=ACTOR)
    digest = str(authorization["authorization_hash"])
    ambiguous = ReleaseTimeoutSlurm(context)
    preflight_lib.submit_authorization(
        context, authorization_hash=digest, actor=ACTOR, runner=ambiguous
    )
    preflight_lib.query_and_verify_held(
        context, authorization_hash=digest, actor=ACTOR, runner=ambiguous
    )
    _assert_rejected(
        lambda: preflight_lib.release_preflight(
            context,
            authorization_hash=digest,
            actor=ACTOR,
            runner=ambiguous,
        ),
        "release timeout was treated as success",
    )
    assert preflight_lib.preflight_state(
        context, digest
    ).status == "release-ambiguous"
    reconcile = ReconcileSlurm(context, (JOB_ID,))
    assert preflight_lib.reconcile_preflight(
        context,
        authorization_hash=digest,
        actor=ACTOR,
        runner=reconcile,
    ) == f"held preflight job reconciled: {JOB_ID}"
    assert preflight_lib.preflight_state(
        context, digest
    ).status == "verified-held"
    recovered = FakePreflightSlurm(context)
    preflight_lib.release_preflight(
        context,
        authorization_hash=digest,
        actor=ACTOR,
        runner=recovered,
    )
    preflight_lib.freeze_terminal_accounting(
        context,
        authorization_hash=digest,
        actor=ACTOR,
        runner=recovered,
    )
    _write_raw_preflight(context, job_id=JOB_ID)
    evidence = preflight_lib.seal_phase2c_preflight_evidence(
        context, authorization_hash=digest, allow_test_mode=True
    )
    preflight_lib.validate_phase2c_preflight_evidence(
        evidence, expected_twin=recovery_twin, allow_test_mode=True
    )


def test_readiness_lock_gate(
    repo_root: Path, execution: Any, scratch: Path
) -> None:
    scratch.mkdir()

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=scratch,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Phase2C Fixture")
    git("config", "user.email", "phase2c-fixture@example.invalid")
    (scratch / "base.txt").write_text("base\n", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-q", "-m", "fixture base")
    base_commit = git("rev-parse", "HEAD")
    base_tree = git("rev-parse", "HEAD^{tree}")
    for relative in readiness_lib.CRITICAL_ARTIFACTS:
        source = repo_root / relative
        assert source.is_file() and not source.is_symlink(), relative
        destination = scratch / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    git("add", *readiness_lib.CRITICAL_ARTIFACTS)
    git("commit", "-q", "-m", "C6 fixture")
    c6 = git("rev-parse", "HEAD")
    c6_identity = {
        "commit": c6,
        "tree": git("rev-parse", "HEAD^{tree}"),
        "parent_count": 1,
        "branch": git("branch", "--show-current"),
        "critical_artifacts": readiness_lib._critical_artifact_records(scratch),
    }
    checker_outputs = {
        "focused": (
            "steel-module production Phase-2C: PASS\n"
            + readiness_lib.FOCUSED_SCHEDULER_SENTINEL
            + "\n"
        ),
        "top_level": "steel-module campaign infrastructure: PASS (fixture)\n",
    }
    checker_records: dict[str, dict[str, Any]] = {}
    for name, relative in readiness_lib.CHECKER_PATHS.items():
        stdout = checker_outputs[name]
        checker_records[name] = {
            "name": name,
            "path": relative,
            "sha256": sha256_file(scratch / relative),
            "command": ["python3", str(scratch / relative)],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }
    acceptance = {
        "schema_version": readiness_lib.ACCEPTANCE_SCHEMA_VERSION,
        "acceptance_id": None,
        "acceptance_hash": None,
        "created_at_utc": "2026-07-20T00:00:00+00:00",
        "c6": {"commit": c6, "tree": c6_identity["tree"]},
        "python": {"executable": "python3", "version": "fixture"},
        "checkers": checker_records,
        "scheduler_contact": {
            "real_slurm_calls": 0,
            "forbidden_command_sentinels": True,
            "scheduler_contact_performed": False,
        },
        "geant4_invoked": False,
        "accepted_for_r6_proposal": True,
    }
    acceptance_root = scratch.parent / (scratch.name + "-external-evidence")
    stale = json.loads(json.dumps(acceptance))
    stale["created_at_utc"] = "2026-07-19T23:59:00+00:00"
    stale["c6"] = {"commit": base_commit, "tree": base_tree}
    stale_dir, _stale_payload = _write_c6_acceptance_fixture(
        acceptance_root, stale
    )
    readiness_lib.validate_phase2c_c6_acceptance(stale_dir)

    def checker_record(
        _root: Path, *, name: str, relative: str
    ) -> dict[str, Any]:
        assert relative == readiness_lib.CHECKER_PATHS[name]
        return json.loads(json.dumps(checker_records[name]))

    with (
        mock.patch.object(
            readiness_lib, "_acceptance_root", return_value=acceptance_root
        ),
        mock.patch.object(
            readiness_lib, "_checker_record", side_effect=checker_record
        ) as checker_mock,
    ):
        _assert_rejected(
            lambda: readiness_lib._load_unique_c6_acceptance(scratch),
            "a stale prior-C6 acceptance was selected for the current C6",
        )
        publish_lock = readiness_lib._acceptance_publish_lock_path(
            acceptance_root, c6
        )
        publish_lock.mkdir()
        _assert_rejected(
            lambda: readiness_lib.write_phase2c_c6_acceptance(repo_root=scratch),
            "concurrent current-C6 acceptance publication was not rejected",
        )
        publish_lock.rmdir()
        acceptance_dir = readiness_lib.write_phase2c_c6_acceptance(
            repo_root=scratch
        )
        assert checker_mock.call_count == len(readiness_lib.CHECKER_PATHS)
        repeated = readiness_lib.write_phase2c_c6_acceptance(
            repo_root=scratch
        )
        assert repeated == acceptance_dir
        assert checker_mock.call_count == len(readiness_lib.CHECKER_PATHS)
        stale_lock_identity = json.loads(json.dumps(c6_identity))
        stale_lock_identity["commit"] = base_commit
        stale_lock_identity["tree"] = base_tree
        _assert_rejected(
            lambda: readiness_lib._write_phase2c_c6_acceptance_unlocked(
                repo_root=scratch, expected_c6=stale_lock_identity
            ),
            "C6 acceptance writer ignored identity drift after lock acquisition",
        )
        selected_dir, acceptance = readiness_lib._load_unique_c6_acceptance(
            scratch
        )
        assert selected_dir == acceptance_dir
        assert acceptance["c6"] == {
            "commit": c6,
            "tree": c6_identity["tree"],
        }
        direct_symlink = scratch.parent / (scratch.name + "-acceptance-link")
        direct_symlink.symlink_to(acceptance_dir, target_is_directory=True)
        _assert_rejected(
            lambda: readiness_lib.validate_phase2c_c6_acceptance(
                direct_symlink
            ),
            "C6 acceptance validator followed a bundle symlink",
        )
        canonical_symlink = acceptance_root / "c6-acceptance-symlink-fixture"
        canonical_symlink.symlink_to(acceptance_dir, target_is_directory=True)
        _assert_rejected(
            lambda: readiness_lib._load_unique_c6_acceptance(scratch),
            "canonical C6 acceptance selector followed a child symlink",
        )
        canonical_symlink.unlink()

        duplicate = json.loads(json.dumps(acceptance))
        duplicate["created_at_utc"] = "2026-07-20T00:00:01+00:00"
        duplicate_dir, _duplicate_payload = _write_c6_acceptance_fixture(
            acceptance_root, duplicate
        )
        _assert_rejected(
            lambda: readiness_lib._load_unique_c6_acceptance(scratch),
            "multiple current-C6 acceptance bundles were not rejected",
        )
        shutil.rmtree(duplicate_dir)
        selected_dir, acceptance = readiness_lib._load_unique_c6_acceptance(
            scratch
        )
        assert selected_dir == acceptance_dir
    identity = readiness_lib._execution_identity(
        execution, require_pristine=True
    )
    proposal = {
        "schema_version": readiness_lib.READINESS_SCHEMA_VERSION,
        "status": "proposed-phase2c-r6-readiness",
        "created_at_utc": "2026-07-20T00:00:00+00:00",
        "review": None,
        "c6": c6_identity,
        "c6_osc_acceptance": readiness_lib._acceptance_binding(
            acceptance_dir, acceptance
        ),
        "execution_v6": identity,
        "production_equivalence_hash": identity[
            "production_equivalence_hash"
        ],
        "compute_preflight": identity["compute_preflight"],
        "managed_submission_ready": False,
        "automatic_submission": False,
        "real_production_submission": False,
        "production_intent_count": 0,
        "production_slurm_job_ids": [],
        "preflight_slurm_job_ids": [identity["preflight_job_id"]],
        "readiness_hash": None,
    }
    proposal["readiness_hash"] = readiness_lib._semantic_hash(
        proposal, "readiness_hash"
    )
    readiness_lib.validate_phase2c_readiness_payload(
        proposal, authoritative=False
    )
    accepted = json.loads(json.dumps(proposal))
    accepted["status"] = "accepted-formal-phase2c-r6-ready"
    accepted["managed_submission_ready"] = True
    accepted["review"] = {
        "reviewed_at_utc": "2026-07-20T00:01:00+00:00",
        "reviewed_by": ACTOR,
        "proposal_sha256": "f" * 64,
    }
    accepted["readiness_hash"] = readiness_lib._semantic_hash(
        accepted, "readiness_hash"
    )
    readiness_lib.validate_phase2c_readiness_payload(
        accepted, authoritative=True
    )
    lock = scratch / readiness_lib.READINESS_LOCK_RELATIVE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps(accepted, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    git("add", readiness_lib.READINESS_LOCK_RELATIVE.as_posix())
    git("commit", "-q", "-m", "R6 fixture lock only")
    r6_commit = git("rev-parse", "HEAD")
    readiness_lib._verify_lock_only_git_gate(scratch, accepted)

    def verify_readiness() -> tuple[Path, dict[str, Any]]:
        with mock.patch.object(
            readiness_lib, "_acceptance_root", return_value=acceptance_root
        ):
            return readiness_lib.verify_phase2c_readiness(
                execution, repo_root=scratch
            )

    verified_path, verified_payload = verify_readiness()
    assert verified_path == lock.resolve()
    assert verified_payload == accepted

    duplicate = json.loads(json.dumps(acceptance))
    duplicate["created_at_utc"] = "2026-07-20T00:02:00+00:00"
    duplicate_dir, _duplicate_payload = _write_c6_acceptance_fixture(
        acceptance_root, duplicate
    )
    _assert_rejected(
        verify_readiness,
        "R6 verifier accepted multiple canonical current-C6 bundles",
    )
    shutil.rmtree(duplicate_dir)
    verify_readiness()

    outside_parent = scratch.parent / (scratch.name + "-outside-acceptance")
    outside_parent.mkdir()
    outside_root = outside_parent / acceptance_dir.name
    shutil.copytree(acceptance_dir, outside_root)
    outside_accepted = json.loads(json.dumps(accepted))
    outside_accepted["c6_osc_acceptance"] = readiness_lib._acceptance_binding(
        outside_root,
        readiness_lib.validate_phase2c_c6_acceptance(outside_root),
    )
    outside_accepted["readiness_hash"] = readiness_lib._semantic_hash(
        outside_accepted, "readiness_hash"
    )
    git("switch", "-q", "--detach", c6)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(
        json.dumps(outside_accepted, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    git("add", readiness_lib.READINESS_LOCK_RELATIVE.as_posix())
    git("commit", "-q", "-m", "R6 noncanonical acceptance fixture")
    _assert_rejected(
        verify_readiness,
        "R6 verifier accepted a noncanonical C6 acceptance bundle",
    )

    git("switch", "-q", "--detach", c6)
    rename_source = scratch / "rename-source.json"
    rename_source.write_text("rename attack fixture\n", encoding="utf-8")
    git("add", rename_source.name)
    git("commit", "-q", "-m", "C6 rename fixture")
    rename_c6 = git("rev-parse", "HEAD")
    rename_accepted = json.loads(json.dumps(accepted))
    rename_accepted["c6"]["commit"] = rename_c6
    rename_accepted["c6"]["tree"] = git("rev-parse", "HEAD^{tree}")
    lock.parent.mkdir(parents=True, exist_ok=True)
    git("mv", rename_source.name, readiness_lib.READINESS_LOCK_RELATIVE.as_posix())
    git("commit", "-q", "-m", "R6 rename attack fixture")
    _assert_rejected(
        lambda: readiness_lib._verify_lock_only_git_gate(
            scratch, rename_accepted
        ),
        "R6 lock-only gate accepted a rename as an add-only lock",
    )

    git("switch", "-q", "--detach", r6_commit)

    protected = scratch / readiness_lib.CRITICAL_ARTIFACTS[0]
    protected.write_bytes(protected.read_bytes() + b"tamper\n")
    _assert_rejected(
        lambda: readiness_lib._verify_lock_only_git_gate(scratch, accepted),
        "R6 gate accepted a modified production-critical blob",
    )


def test_twin_and_execution_v6(repo_root: Path, scratch: Path) -> None:
    v5, twin, control = _materialize_twin(repo_root, scratch)

    materialize_arguments = {
        "repo_root": repo_root,
        "v5_dir": v5.directory,
        "v5_evidence_dir": scratch / "fixture-v5-evidence",
        "test_mode": True,
        "fixture_control_source": control,
        "v5_evidence_validator": _fixture_v5_evidence,
        "v5_execution_loader": lambda _path: v5,
    }
    _assert_rejected(
        lambda: phase2c_lib.materialize_preflight_v6(
            **materialize_arguments, out_dir=twin.directory
        ),
        "preflight-v6 materializer overwrote an existing target",
    )
    fault_target = scratch / "work/campaigns/preflight-v6-fault"
    _assert_rejected(
        lambda: phase2c_lib.materialize_preflight_v6(
            **materialize_arguments,
            out_dir=fault_target,
            fault_after="pre-publish",
        ),
        "preflight-v6 fault injection unexpectedly published",
    )
    assert not fault_target.exists()
    assert not tuple(fault_target.parent.glob(f".{fault_target.name}.tmp-*"))

    link_target = scratch / "work/campaigns/preflight-v6-symlink"
    link_destination = scratch / "preflight-v6-symlink-destination"
    link_destination.mkdir()
    link_target.symlink_to(link_destination, target_is_directory=True)
    _assert_rejected(
        lambda: phase2c_lib.materialize_preflight_v6(
            **materialize_arguments, out_dir=link_target
        ),
        "preflight-v6 accepted a symlink target",
    )

    concurrent_target = scratch / "work/campaigns/preflight-v6-concurrent"
    def concurrent_materialize() -> Any:
        return phase2c_lib.materialize_preflight_v6(
            **materialize_arguments, out_dir=concurrent_target
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(concurrent_materialize) for _ in range(2)]
        outcomes: list[str] = []
        for future in futures:
            try:
                future.result()
            except (OSError, RuntimeError, ValueError):
                outcomes.append("rejected")
            else:
                outcomes.append("published")
    assert sorted(outcomes) == ["published", "rejected"]
    assert not tuple(
        concurrent_target.parent.glob(f".{concurrent_target.name}.tmp-*")
    )

    lock = twin.directory / ".control.lock"
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert lock.stat().st_size == 0
    assert lock.stat().st_nlink == 1
    descriptor = os.open(lock, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)

    # Device/inode are creation diagnostics, not cross-node authority.
    lock.unlink()
    lock.touch(mode=0o600)
    lock.chmod(0o600)
    reloaded = phase2c_lib.load_preflight_v6(
        twin.directory, allow_test_mode=True
    )
    assert reloaded.twin_hash == twin.twin_hash
    lock.write_bytes(b"not-empty")
    lock.chmod(0o600)
    _assert_rejected(
        lambda: phase2c_lib.load_preflight_v6(
            twin.directory, allow_test_mode=True
        ),
        "preflight twin accepted a non-empty portable lock",
    )
    lock.write_bytes(b"")
    lock.chmod(0o600)

    equivalence_tamper = scratch / "preflight-equivalence-tamper"
    shutil.copytree(twin.directory, equivalence_tamper)
    equivalence_file = equivalence_tamper / "production_equivalence.json"
    equivalence_file.chmod(0o644)
    equivalence_file.write_text("{}\n", encoding="utf-8")
    _assert_rejected(
        lambda: phase2c_lib.load_preflight_v6(
            equivalence_tamper, allow_test_mode=True
        ),
        "preflight twin accepted production-equivalence tamper",
    )

    mode_tamper = scratch / "preflight-source-mode-tamper"
    shutil.copytree(twin.directory, mode_tamper)
    executable_source = next(
        path
        for path in (mode_tamper / "sources").rglob("*")
        if path.is_file() and path.stat().st_mode & 0o111
    )
    executable_source.chmod(0o444)
    _assert_rejected(
        lambda: phase2c_lib.load_preflight_v6(
            mode_tamper, allow_test_mode=True
        ),
        "preflight twin accepted executable-mode tamper",
    )

    test_timeout_is_quarantined(twin, scratch / "timeout")
    test_failed_twin_retry_lifecycle(
        repo_root, twin, scratch / "failed-twin-retry"
    )
    test_release_ambiguity_recovery(
        twin, scratch / "release-ambiguity-recovery"
    )
    _context_value, evidence, fake = _successful_preflight(
        twin, scratch / "success"
    )
    assert [command[0] for command in fake.commands].count("sbatch") == 1
    assert [command[0] for command in fake.commands].count("scontrol") == 3

    # The sealed evidence remains semantically auditable after an attacker
    # recomputes the ordinary checksum manifests: both the copied closure and
    # the append-only event hash chain are independently bound by evidence.json.
    closure_tamper = scratch / "closure-tamper" / evidence.name
    closure_tamper.parent.mkdir()
    shutil.copytree(evidence, closure_tamper)
    closure_file = closure_tamper / "twin_closure.json"
    closure_file.chmod(0o644)
    closure_value = json.loads(closure_file.read_text(encoding="utf-8"))
    closure_value["events_consumed"] = 1
    closure_file.write_text(
        json.dumps(closure_value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (closure_tamper / "SHA256SUMS").unlink()
    preflight_lib._write_checksum_manifest(closure_tamper)
    _assert_rejected(
        lambda: preflight_lib.validate_phase2c_preflight_evidence(
            closure_tamper, expected_twin=twin, allow_test_mode=True
        ),
        "Phase-2C evidence accepted a rechecksummed twin-closure tamper",
    )

    journal_tamper = scratch / "journal-tamper" / evidence.name
    journal_tamper.parent.mkdir()
    shutil.copytree(evidence, journal_tamper)
    event_file = sorted((journal_tamper / "journal").glob("*.json"))[0]
    event_file.chmod(0o644)
    event_value = json.loads(event_file.read_text(encoding="utf-8"))
    event_value["actor"] = "fixture-tamper"
    event_file.write_text(
        json.dumps(event_value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (journal_tamper / "journal" / "SHA256SUMS").unlink()
    preflight_lib._write_checksum_manifest(journal_tamper / "journal")
    (journal_tamper / "SHA256SUMS").unlink()
    preflight_lib._write_checksum_manifest(journal_tamper)
    _assert_rejected(
        lambda: preflight_lib.validate_phase2c_preflight_evidence(
            journal_tamper, expected_twin=twin, allow_test_mode=True
        ),
        "Phase-2C evidence accepted a rechecksummed journal tamper",
    )

    execution_fault = scratch / "work/campaigns/execution-v6-fault"
    _assert_rejected(
        lambda: phase2c_lib.materialize_execution_v6(
            repo_root=repo_root,
            preflight_dir=twin.directory,
            evidence_dir=evidence,
            out_dir=execution_fault,
            test_mode=True,
            fault_after="pre-publish",
        ),
        "execution-v6 fault injection unexpectedly published",
    )
    assert not execution_fault.exists()
    assert not tuple(execution_fault.parent.glob(f".{execution_fault.name}.tmp-*"))

    execution_target = scratch / "work/campaigns" / phase2c_lib.FORMAL_EXECUTION_V6_NAME
    execution = phase2c_lib.materialize_execution_v6(
        repo_root=repo_root,
        preflight_dir=twin.directory,
        evidence_dir=evidence,
        out_dir=execution_target,
        test_mode=True,
    )
    _assert_rejected(
        lambda: phase2c_lib.materialize_execution_v6(
            repo_root=repo_root,
            preflight_dir=twin.directory,
            evidence_dir=evidence,
            out_dir=execution_target,
            test_mode=True,
        ),
        "execution-v6 materializer overwrote an existing target",
    )
    assert execution.manifest["accepted_statistical_evidence"] is False
    assert execution.manifest["production_equivalence_hash"] == (
        twin.production_equivalence_hash
    )
    for name in ("intents", "attempts", "finalized"):
        assert not any((execution.directory / name).iterdir())
    assert phase2c_lib.load_execution_v6(
        execution.directory,
        allow_test_mode=True,
        require_pristine=True,
    ).execution_hash == execution.execution_hash
    frozen_control = execution.directory / "sources/control"
    with (
        mock.patch.object(
            phase2c_lib, "load_execution_v6", return_value=execution
        ) as frozen_loader,
        mock.patch.object(
            readiness_lib,
            "verify_phase2c_readiness",
            return_value=(Path("fixture-r6"), {"fixture": True}),
        ),
    ):
        assert phase2c_lib.load_execution_v6_for_frozen_worker(
            execution.directory, control_root=frozen_control
        ) is execution
    frozen_kwargs = frozen_loader.call_args.kwargs
    assert frozen_kwargs["verify_live_predecessor"] is False
    assert frozen_kwargs["verify_phase2a_control_plane"] is False
    test_readiness_lock_gate(repo_root, execution, scratch / "r6-git-gate")

    # R6 is absent in C6: production admission fails before any mutable write.
    before = {
        name: tuple((execution.directory / name).iterdir())
        for name in ("intents", "attempts", "finalized")
    }
    reason = _assert_rejected(
        lambda: production_manager.require_manager_command_allowed(
            execution, "prepare-intent"
        ),
        "execution-v6 admitted production before R6",
    )
    assert "readiness" in reason.lower() or "r6" in reason.lower()
    assert before == {
        name: tuple((execution.directory / name).iterdir())
        for name in ("intents", "attempts", "finalized")
    }

    worker_args = SimpleNamespace(
        execution_dir=execution.directory,
        attempt_id="fixture-worker-pre-r6",
        intent_sha256="d" * 64,
        array_index=1,
    )
    with (
        mock.patch.object(
            production_worker,
            "_load_execution_for_frozen_worker",
            return_value=execution,
        ),
        mock.patch.object(
            production_worker,
            "load_attempt_intent",
            return_value={
                "intent_sha256": "d" * 64,
                "readiness_lock_sha256": "e" * 64,
            },
        ),
        mock.patch(
            "steel_module_production_phase2c_readiness.verify_phase2c_readiness",
            side_effect=ValueError("fixture R6 missing"),
        ),
        mock.patch.object(
            production_worker.shutil,
            "which",
            side_effect=AssertionError("Apptainer lookup occurred before R6"),
        ),
    ):
        _assert_rejected(
            lambda: production_worker.run_task(worker_args),
            "production worker crossed the missing-R6 gate",
        )
    assert before == {
        name: tuple((execution.directory / name).iterdir())
        for name in ("intents", "attempts", "finalized")
    }

    # A synthetic accepted-R6 hook exercises the actual first-intent builder.
    # It never turns test-mode evidence into accepted evidence and never calls
    # a scheduler; the formal readiness validator is tested independently.
    fake_r6 = scratch / "fixture-r6.lock.json"
    fake_r6.write_text('{"fixture": true}\n', encoding="utf-8")
    fake_r6_sha = sha256_file(fake_r6)

    def clone_execution(name: str) -> Any:
        target = execution.directory.parent / name
        shutil.copytree(execution.directory, target)
        return replace(execution, directory=target)

    cancel_execution = clone_execution("execution-v6-cancel-replacement")
    no_job_execution = clone_execution("execution-v6-no-job-replacement")
    reconcile_quarantine_execution = clone_execution(
        "execution-v6-reconcile-quarantine"
    )
    release_quarantine_execution = clone_execution(
        "execution-v6-release-quarantine"
    )
    release_timeout_execution = clone_execution(
        "execution-v6-release-timeout"
    )
    with mock.patch(
        "steel_module_production_phase2c_readiness.verify_phase2c_readiness",
        return_value=(fake_r6, {"fixture": True}),
    ):
        _assert_rejected(
            lambda: phase2b_lib.prepare_attempt_intent(
                execution,
                attempt_id="20260720T000000Z-invalid-initial",
                mode="initial",
                actor=ACTOR,
                write=False,
                readiness_lock_sha256=fake_r6_sha,
            ),
            "execution-v6 accepted initial instead of predecessor-retry",
        )

        cancelled = phase2b_lib.prepare_attempt_intent(
            cancel_execution,
            attempt_id="20260720T000001Z-cancelled-predecessor-retry",
            mode="predecessor-retry",
            actor=ACTOR,
            write=True,
            readiness_lock_sha256=fake_r6_sha,
        )
        phase2b_lib.append_attempt_event(
            cancel_execution,
            cancelled["attempt_id"],
            "intent-cancelled",
            {"reason": "fixture cancellation before scheduler contact"},
            actor=ACTOR,
        )
        cancelled_replacement = phase2b_lib.prepare_attempt_intent(
            cancel_execution,
            attempt_id="20260720T000002Z-replacement-predecessor-retry",
            mode="predecessor-retry",
            actor=ACTOR,
            write=True,
            readiness_lock_sha256=fake_r6_sha,
        )
        assert cancelled_replacement["selected"]["task_count"] == 32

        no_job = phase2b_lib.prepare_attempt_intent(
            no_job_execution,
            attempt_id="20260720T000001Z-no-job-predecessor-retry",
            mode="predecessor-retry",
            actor=ACTOR,
            write=True,
            readiness_lock_sha256=fake_r6_sha,
        )
        phase2b_lib.append_attempt_event(
            no_job_execution,
            no_job["attempt_id"],
            "submission-invoked",
            {"command_sha256": "a" * 64, "job_name": no_job["scheduler"]["job_name"]},
            actor=ACTOR,
        )

        def zero_match_runner(
            command: Sequence[str], *, cwd: Path, timeout: int = 120
        ) -> subprocess.CompletedProcess[str]:
            del timeout
            assert cwd == no_job_execution.directory
            values = tuple(str(value) for value in command)
            assert values[0] in {"squeue", "sacct"}
            return subprocess.CompletedProcess(values, 0, "", "")

        assert production_manager.reconcile_attempt(
            no_job_execution,
            attempt_id=no_job["attempt_id"],
            intent_sha256=no_job["intent_sha256"],
            actor=ACTOR,
            runner=zero_match_runner,
            now_utc="2026-07-20T00:00:00+00:00",
        ) == "zero-match-observation-recorded"
        assert production_manager.reconcile_attempt(
            no_job_execution,
            attempt_id=no_job["attempt_id"],
            intent_sha256=no_job["intent_sha256"],
            actor=ACTOR,
            runner=zero_match_runner,
            now_utc="2026-07-20T00:10:01+00:00",
            confirm_no_job=True,
            rationale="two retained scheduler snapshots prove no accepted job",
        ) == "no-job-confirmed"
        no_job_replacement = phase2b_lib.prepare_attempt_intent(
            no_job_execution,
            attempt_id="20260720T000002Z-replacement-predecessor-retry",
            mode="predecessor-retry",
            actor=ACTOR,
            write=True,
            readiness_lock_sha256=fake_r6_sha,
        )
        assert no_job_replacement["selected"]["event_count"] == 8000

        def held_attempt(target_execution: Any, attempt_id: str) -> tuple[Any, Any]:
            held_intent = phase2b_lib.prepare_attempt_intent(
                target_execution,
                attempt_id=attempt_id,
                mode="predecessor-retry",
                actor=ACTOR,
                write=True,
                readiness_lock_sha256=fake_r6_sha,
            )
            fake = FakeProductionSlurm(target_execution, held_intent)
            assert production_manager.submit_intent(
                target_execution,
                attempt_id=held_intent["attempt_id"],
                intent_sha256=held_intent["intent_sha256"],
                actor=ACTOR,
                runner=fake,
                release_after_verification=False,
            ) == fake.job_id
            fake.extra_job_ids = ("70000628",)
            return held_intent, fake

        def worker_environment(
            target_execution: Any, held_intent: dict[str, Any], job_id: str
        ) -> dict[str, str]:
            return {
                "SLURM_ARRAY_JOB_ID": job_id,
                "SLURM_JOB_ID": "70000999",
                "SLURM_JOB_NAME": held_intent["scheduler"]["job_name"],
                "SLURM_ARRAY_TASK_ID": "1",
                "SLURM_ARRAY_TASK_COUNT": "32",
                "SLURM_ARRAY_TASK_MIN": "1",
                "SLURM_ARRAY_TASK_MAX": "32",
                "SLURM_ARRAY_TASK_STEP": "1",
                "SLURM_SUBMIT_DIR": str(target_execution.directory),
            }

        quarantined_intent, quarantine_slurm = held_attempt(
            reconcile_quarantine_execution,
            "20260720T000001Z-reconcile-quarantine",
        )
        _assert_rejected(
            lambda: production_manager.reconcile_attempt(
                reconcile_quarantine_execution,
                attempt_id=quarantined_intent["attempt_id"],
                intent_sha256=quarantined_intent["intent_sha256"],
                actor=ACTOR,
                runner=quarantine_slurm,
            ),
            "verified-held production attempt was not permanently quarantined",
        )
        assert phase2b_lib.attempt_state(
            reconcile_quarantine_execution, quarantined_intent["attempt_id"]
        ).status == "permanently-quarantined"
        assert not any(
            command[0:2] == ("scontrol", "release")
            for command in quarantine_slurm.commands
        )

        release_intent, release_quarantine_slurm = held_attempt(
            release_quarantine_execution,
            "20260720T000001Z-release-quarantine",
        )
        _assert_rejected(
            lambda: production_manager.release_verified_intent(
                release_quarantine_execution,
                attempt_id=release_intent["attempt_id"],
                intent_sha256=release_intent["intent_sha256"],
                actor=ACTOR,
                runner=release_quarantine_slurm,
            ),
            "explicit production release ignored multiple exact scheduler matches",
        )
        assert phase2b_lib.attempt_state(
            release_quarantine_execution, release_intent["attempt_id"]
        ).status == "permanently-quarantined"
        assert not any(
            command[0:2] == ("scontrol", "release")
            for command in release_quarantine_slurm.commands
        )

        timeout_intent, timeout_slurm = held_attempt(
            release_timeout_execution,
            "20260720T000001Z-release-timeout",
        )
        timeout_slurm.extra_job_ids = ()
        callback_states: list[str] = []

        def admit_worker_during_release_timeout() -> None:
            state = phase2b_lib.validate_worker_scheduler_identity(
                release_timeout_execution,
                attempt_id=timeout_intent["attempt_id"],
                intent=timeout_intent,
                array_index=1,
                environment=worker_environment(
                    release_timeout_execution, timeout_intent, timeout_slurm.job_id
                ),
            )
            callback_states.append(state.status)

        timeout_slurm.release_callback = admit_worker_during_release_timeout
        timeout_slurm.release_timeout = True
        _assert_rejected(
            lambda: production_manager.release_verified_intent(
                release_timeout_execution,
                attempt_id=timeout_intent["attempt_id"],
                intent_sha256=timeout_intent["intent_sha256"],
                actor=ACTOR,
                runner=timeout_slurm,
            ),
            "release timeout after scheduler start was not quarantined",
        )
        assert callback_states == ["release-invoked"]
        assert phase2b_lib.attempt_state(
            release_timeout_execution, timeout_intent["attempt_id"]
        ).status == "release-invoked"
        assert phase2b_lib.validate_worker_scheduler_identity(
            release_timeout_execution,
            attempt_id=timeout_intent["attempt_id"],
            intent=timeout_intent,
            array_index=1,
            environment=worker_environment(
                release_timeout_execution, timeout_intent, timeout_slurm.job_id
            ),
        ).status == "release-invoked"

        def timeout_zero_match_runner(
            command: Sequence[str], *, cwd: Path, timeout: int = 120
        ) -> subprocess.CompletedProcess[str]:
            del timeout
            assert cwd == release_timeout_execution.directory
            values = tuple(str(value) for value in command)
            assert values[0] in {"squeue", "sacct"}
            return subprocess.CompletedProcess(values, 0, "", "")

        assert production_manager.reconcile_attempt(
            release_timeout_execution,
            attempt_id=timeout_intent["attempt_id"],
            intent_sha256=timeout_intent["intent_sha256"],
            actor=ACTOR,
            runner=timeout_zero_match_runner,
        ) == "release-zero-match-observation-recorded"
        assert phase2b_lib.attempt_state(
            release_timeout_execution, timeout_intent["attempt_id"]
        ).status == "release-invoked"
        assert phase2b_lib.validate_worker_scheduler_identity(
            release_timeout_execution,
            attempt_id=timeout_intent["attempt_id"],
            intent=timeout_intent,
            array_index=1,
            environment=worker_environment(
                release_timeout_execution, timeout_intent, timeout_slurm.job_id
            ),
        ).status == "release-invoked"
        assert production_manager.reconcile_attempt(
            release_timeout_execution,
            attempt_id=timeout_intent["attempt_id"],
            intent_sha256=timeout_intent["intent_sha256"],
            actor=ACTOR,
            runner=timeout_slurm,
        ) == f"reconciled-same-job:{timeout_slurm.job_id}:RUNNING"

        intent = phase2b_lib.prepare_attempt_intent(
            execution,
            attempt_id="20260720T000001Z-predecessor-retry",
            mode="predecessor-retry",
            actor=ACTOR,
            write=True,
            readiness_lock_sha256=fake_r6_sha,
        )
        production_manager._validate_phase2c_intent_shape(execution, intent)
        assert intent["selected"]["task_count"] == 32
        assert intent["selected"]["event_count"] == 8000
        assert len(
            {
                seed
                for task in execution.tasks
                for seed in (task.seed1, task.seed2)
            }
        ) == 64
        production_slurm = FakeProductionSlurm(execution, intent)
        _assert_rejected(
            lambda: production_manager.submit_intent(
                execution,
                attempt_id=intent["attempt_id"],
                intent_sha256=intent["intent_sha256"],
                actor=ACTOR,
                runner=production_slurm,
            ),
            "execution-v6 helper auto-released the production array",
        )
        assert not production_slurm.commands
        assert production_manager.submit_intent(
            execution,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor=ACTOR,
            runner=production_slurm,
            release_after_verification=False,
        ) == production_slurm.job_id
        assert phase2b_lib.attempt_state(
            execution, intent["attempt_id"]
        ).status == "verified-held"
        assert production_manager.reconcile_attempt(
            execution,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor=ACTOR,
            runner=production_slurm,
        ) == f"reconciled-held:{production_slurm.job_id}"
        assert not any(
            command == ("scontrol", "release", production_slurm.job_id)
            for command in production_slurm.commands
        )
        _assert_rejected(
            lambda: phase2b_lib.validate_worker_scheduler_identity(
                execution,
                attempt_id=intent["attempt_id"],
                intent=intent,
                array_index=1,
                environment=worker_environment(
                    execution, intent, production_slurm.job_id
                ),
            ),
            "execution-v6 worker admitted a held job before release-invoked",
        )
        release_callback_states: list[str] = []

        def admit_worker_during_successful_release() -> None:
            state = phase2b_lib.validate_worker_scheduler_identity(
                execution,
                attempt_id=intent["attempt_id"],
                intent=intent,
                array_index=1,
                environment=worker_environment(
                    execution, intent, production_slurm.job_id
                ),
            )
            release_callback_states.append(state.status)

        production_slurm.release_callback = admit_worker_during_successful_release
        assert production_manager.release_verified_intent(
            execution,
            attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"],
            actor=ACTOR,
            runner=production_slurm,
        ) == production_slurm.job_id
        assert release_callback_states == ["release-invoked"]
        assert sum(
            command == ("scontrol", "release", production_slurm.job_id)
            for command in production_slurm.commands
        ) == 1
        _assert_rejected(
            lambda: phase2b_lib.prepare_attempt_intent(
                execution,
                attempt_id="20260720T000002Z-resume-without-terminal",
                mode="resume",
                actor=ACTOR,
                write=False,
                readiness_lock_sha256=fake_r6_sha,
            ),
            "execution-v6 accepted resume without terminal evidence",
        )

    task_ids = [task.logical_task_id for task in execution.tasks]
    valid_intent = {
        "mode": "predecessor-retry",
        "selected": {
            "logical_task_ids": task_ids,
            "task_count": 32,
            "event_count": 8000,
        },
    }
    production_manager._validate_phase2c_intent_shape(execution, valid_intent)
    invalid = json.loads(json.dumps(valid_intent))
    invalid["selected"]["logical_task_ids"] = task_ids[:1]
    invalid["selected"]["task_count"] = 1
    invalid["selected"]["event_count"] = 250
    _assert_rejected(
        lambda: production_manager._validate_phase2c_intent_shape(execution, invalid),
        "execution-v6 accepted a one-task production smoke",
    )

    # A mutable record is admissible to post-intent readers but not pristine gates.
    phase2c_lib.load_execution_v6(
        execution.directory, allow_test_mode=True, require_pristine=False
    )
    _assert_rejected(
        lambda: phase2c_lib.load_execution_v6(
            execution.directory, allow_test_mode=True, require_pristine=True
        ),
        "execution-v6 pristine gate ignored a mutable intent",
    )

    # Frozen-source tamper is rejected even if the mutable roots are otherwise allowed.
    critical = (
        execution.directory
        / "sources/control/hpc/osc/run_steel_module_production_phase2b_task.py"
    )
    critical.chmod(0o644)
    critical.write_text("tampered\n", encoding="utf-8")
    _assert_rejected(
        lambda: phase2c_lib.load_execution_v6(
            execution.directory, allow_test_mode=True, require_pristine=False
        ),
        "execution-v6 accepted a production-critical source tamper",
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-phase2c-check-") as raw:
        scratch = Path(raw)
        _sentinel_bin, markers, original_path = _scheduler_sentinels(scratch)
        try:
            test_retry_launcher_admission(repo_root, scratch / "retry-launcher")
            test_twin_and_execution_v6(repo_root, scratch / "phase2c")
        finally:
            os.environ["PATH"] = original_path
        invoked = sorted(name for name, marker in markers.items() if marker.exists())
        assert not invoked, f"Phase-2C checker touched real scheduler names: {invoked}"
    print("steel-module production Phase-2C: PASS")
    print("scheduler: injected fakes + forbidden-command sentinels; real Slurm calls: 0")
    print("evidence: Apptainer modeled, Geant4 false, events/seeds consumed: 0/0")
    print("production: execution-v6 pristine and blocked before tracked R6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
