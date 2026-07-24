#!/usr/bin/env python3
"""Focused zero-scheduler checks for R3 pre-workspace failure evidence."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import seal_steel_module_production_r3_preworkspace_failure as failure_cli
import steel_module_production_r3_preworkspace_failure_lib as failure_lib
from steel_module_campaign_lib import sha256_file
from steel_module_production_checkpoint_lib import (
    recursive_file_records,
    write_recursive_checksums,
)
from steel_module_production_phase2b_lib import ROOT_STATIC_MANIFEST
from steel_module_production_r3_preworkspace_failure_lib import (
    EXPECTED_LOG,
    FORMAL_EXECUTION_HASH,
    FORMAL_EXECUTION_ID,
    FORMAL_INPUT_SHA256,
    FORMAL_JOB_ID,
    FORMAL_JOB_NAME,
    collect_r3_preworkspace_failure_evidence,
    seal_r3_preworkspace_failure_evidence,
    validate_r3_preworkspace_failure_bundle,
)
from steel_module_production_successor_lib import SUCCESSOR_EXECUTION_SCHEMA_VERSION


STATIC_FILES = (
    "README.md",
    "managed_execution.json",
    "scan_args.txt",
    "source_archives.json",
    "tasks.tsv",
)


class Fixture:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.work = self.root / "work"
        self.execution = (
            self.work / "campaigns" / "steel-module-production-bc-s1-execution-v4"
        )
        self.r3 = self.work / "evidence" / "steel-module-production-r3"
        self.inputs = self.root / "inputs"
        self.held = self.inputs / "held.txt"
        self.sacct = self.inputs / "sacct.psv"
        self.squeue = self.inputs / "squeue.psv"
        self.log = self.r3 / f"slurm-{FORMAL_JOB_ID}.out"


def _write_static_manifest(execution: Path) -> None:
    text = "".join(
        f"{sha256_file(execution / name)}  {name}\n" for name in sorted(STATIC_FILES)
    )
    (execution / ROOT_STATIC_MANIFEST).write_text(text, encoding="utf-8")


def _make_fixture(root: Path) -> Fixture:
    fixture = Fixture(root)
    execution = fixture.execution
    launcher = execution / "sources/control/hpc/osc/run_steel_module_production_r3_probe.sbatch"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/bash\nexit 99\n", encoding="utf-8")
    launcher.chmod(0o555)
    for name in ("intents", "attempts", "finalized"):
        (execution / name).mkdir()
    (execution / ".control.lock").write_text("fixture-lock\n", encoding="utf-8")
    (execution / "README.md").write_text("fixture\n", encoding="utf-8")
    (execution / "scan_args.txt").write_text("--fixture\n", encoding="utf-8")
    (execution / "source_archives.json").write_text("{}\n", encoding="utf-8")
    (execution / "tasks.tsv").write_text(
        "logical_task_id\nfixture\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION,
        "execution_id": FORMAL_EXECUTION_ID,
        "execution_hash": FORMAL_EXECUTION_HASH,
        "test_mode": True,
        "accepted_statistical_evidence": False,
    }
    (execution / "managed_execution.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_static_manifest(execution)
    (fixture.r3 / "raw").mkdir(parents=True)
    fixture.inputs.mkdir()
    fixture.held.write_text(
        " ".join(
            (
                f"JobId={FORMAL_JOB_ID}",
                f"JobName={FORMAL_JOB_NAME}",
                "Account=pas2524",
                "JobState=PENDING",
                "Reason=JobHeldUser",
                "Requeue=0",
                f"Command={launcher}",
                f"WorkDir={execution}",
                f"StdOut={fixture.log}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fixture.sacct.write_text(
        "\n".join(
            (
                f"{FORMAL_JOB_ID}|{FORMAL_JOB_NAME}|pas2524|FAILED|1:0|3",
                f"{FORMAL_JOB_ID}.batch|batch|pas2524|FAILED|1:0|3",
                f"{FORMAL_JOB_ID}.extern|extern|pas2524|COMPLETED|0:0|3",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fixture.squeue.write_text("", encoding="utf-8")
    fixture.log.write_text(EXPECTED_LOG, encoding="utf-8")
    return fixture


def _kwargs(fixture: Fixture) -> dict[str, object]:
    return {
        "execution_dir": fixture.execution,
        "held_scontrol_input": fixture.held,
        "sacct_input": fixture.sacct,
        "squeue_input": fixture.squeue,
        "slurm_output_input": fixture.log,
        "test_mode": True,
    }


def _expect_failure(action: Callable[[], object], fragment: str) -> None:
    try:
        action()
    except (OSError, RuntimeError, ValueError) as exc:
        if fragment not in str(exc):
            raise AssertionError(f"expected {fragment!r}, got {exc!r}") from exc
    else:
        raise AssertionError(f"operation unexpectedly passed; expected {fragment!r}")


def _with_fixture(mutate: Callable[[Fixture], None], fragment: str) -> None:
    with tempfile.TemporaryDirectory(prefix="steel-r3-preworkspace-negative-") as raw:
        fixture = _make_fixture(Path(raw))
        mutate(fixture)
        _expect_failure(
            lambda: collect_r3_preworkspace_failure_evidence(**_kwargs(fixture)),
            fragment,
        )


def _make_tree_writable(root: Path) -> None:
    root.chmod(0o700)
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def _scheduler_sentinels(root: Path) -> tuple[dict[str, Path], str]:
    sentinel_bin = root / "scheduler-sentinel-bin"
    sentinel_bin.mkdir()
    markers: dict[str, Path] = {}
    for command in ("sbatch", "scontrol", "squeue", "sacct"):
        marker = root / f"forbidden-{command}-was-invoked"
        executable = sentinel_bin / command
        executable.write_text(
            "#!/usr/bin/env bash\n"
            f"printf invoked > {str(marker)!r}\n"
            "exit 97\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        markers[command] = marker
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(sentinel_bin) + os.pathsep + original_path
    return markers, original_path


def _assert_no_scheduler_contact(markers: dict[str, Path]) -> None:
    invoked = sorted(name for name, marker in markers.items() if marker.exists())
    assert not invoked, f"pre-workspace evidence contacted scheduler commands: {invoked}"


def _test_formal_loader_route(root: Path) -> None:
    fixture = _make_fixture(root)
    manifest_path = fixture.execution / "managed_execution.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["test_mode"] = False
    manifest["accepted_statistical_evidence"] = True
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_static_manifest(fixture.execution)
    live_repo = root / "live-repo"
    live_repo.mkdir()
    live_repo = live_repo.resolve()
    calls: list[dict[str, object]] = []
    original = failure_lib.load_successor_execution

    def fake_loader(execution_dir: Path, **kwargs: object) -> SimpleNamespace:
        calls.append({"execution_dir": execution_dir, **kwargs})
        return SimpleNamespace(
            directory=fixture.execution,
            execution_id=FORMAL_EXECUTION_ID,
            execution_hash=FORMAL_EXECUTION_HASH,
            manifest=manifest,
        )

    failure_lib.load_successor_execution = fake_loader
    original_hashes = failure_lib.FORMAL_INPUT_SHA256
    try:
        loaded = failure_lib._load_execution(
            fixture.execution, repo_root=live_repo, test_mode=False
        )
        canonical = {
            "held_scontrol": fixture.r3 / f"held-scontrol-{FORMAL_JOB_ID}.txt",
            "sacct": fixture.r3 / f"sacct-{FORMAL_JOB_ID}.psv",
            "squeue": fixture.r3 / f"squeue-{FORMAL_JOB_ID}.psv",
            "slurm_output": fixture.log,
        }
        shutil.copyfile(fixture.held, canonical["held_scontrol"])
        shutil.copyfile(fixture.sacct, canonical["sacct"])
        shutil.copyfile(fixture.squeue, canonical["squeue"])
        failure_lib.FORMAL_INPUT_SHA256 = {
            name: sha256_file(path) for name, path in canonical.items()
        }
        payload, _ = failure_lib.collect_r3_preworkspace_failure_evidence(
            execution_dir=fixture.execution,
            held_scontrol_input=canonical["held_scontrol"],
            sacct_input=canonical["sacct"],
            squeue_input=canonical["squeue"],
            slurm_output_input=canonical["slurm_output"],
            repo_root=live_repo,
            test_mode=False,
        )
        assert payload["test_mode"] is False
        assert payload["execution"]["execution_hash"] == FORMAL_EXECUTION_HASH
        _expect_failure(
            lambda: failure_lib.collect_r3_preworkspace_failure_evidence(
                execution_dir=fixture.execution,
                held_scontrol_input=fixture.held,
                sacct_input=canonical["sacct"],
                squeue_input=canonical["squeue"],
                slurm_output_input=canonical["slurm_output"],
                repo_root=live_repo,
                test_mode=False,
            ),
            "outside canonical paths",
        )
        canonical["held_scontrol"].write_text(
            canonical["held_scontrol"].read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        _expect_failure(
            lambda: failure_lib.collect_r3_preworkspace_failure_evidence(
                execution_dir=fixture.execution,
                held_scontrol_input=canonical["held_scontrol"],
                sacct_input=canonical["sacct"],
                squeue_input=canonical["squeue"],
                slurm_output_input=canonical["slurm_output"],
                repo_root=live_repo,
                test_mode=False,
            ),
            "input SHA-256 identity mismatch",
        )
    finally:
        failure_lib.FORMAL_INPUT_SHA256 = original_hashes
        failure_lib.load_successor_execution = original
    assert loaded.execution_hash == FORMAL_EXECUTION_HASH
    expected_call = {
        "execution_dir": fixture.execution,
        "repo_root": live_repo,
        "require_readiness": False,
        "verify_runtime": True,
        "verify_phase2a_control_plane": True,
        "verify_live_predecessor": True,
    }
    assert calls == [expected_call] * 4


def _test_publication_fault_cleanup(root: Path) -> None:
    fixture = _make_fixture(root)
    failures = fixture.r3 / "failures"
    original_writer = failure_lib.write_recursive_checksums

    def partial_writer(directory: Path) -> None:
        (directory / "partial-residue.txt").write_text(
            "injected\n", encoding="utf-8"
        )
        raise RuntimeError("injected pre-publication fault")

    failure_lib.write_recursive_checksums = partial_writer
    try:
        _expect_failure(
            lambda: seal_r3_preworkspace_failure_evidence(**_kwargs(fixture)),
            "injected pre-publication fault",
        )
    finally:
        failure_lib.write_recursive_checksums = original_writer
    assert failures.is_dir()
    assert not any(failures.iterdir()), "partial staging tree survived a write fault"

    original_publisher = failure_lib.publish_directory_no_replace

    def failed_publisher(_: Path, __: Path) -> None:
        raise RuntimeError("injected publication fault")

    failure_lib.publish_directory_no_replace = failed_publisher
    try:
        _expect_failure(
            lambda: seal_r3_preworkspace_failure_evidence(**_kwargs(fixture)),
            "injected publication fault",
        )
    finally:
        failure_lib.publish_directory_no_replace = original_publisher
    assert not any(failures.iterdir()), "staging tree survived publication failure"


def main() -> int:
    assert FORMAL_JOB_NAME == "g4sm-r3-" + FORMAL_EXECUTION_HASH[:12]
    assert FORMAL_INPUT_SHA256 == {
        "held_scontrol": (
            "fa5c8bfe8417dbf1842fb57424a77dd70cd5a5a01bd684c5b1ac4af1ecc6e3eb"
        ),
        "sacct": (
            "8df470b4fff19dbcca1d4c259e53ba7dd2377ba3cfc6b73f67aaf639df616ca4"
        ),
        "squeue": (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        ),
        "slurm_output": (
            "0c0d9783c71ba16ba4472d34a0bf2464d244258a401a04a5a4a6f471e5c27c4d"
        ),
    }

    with tempfile.TemporaryDirectory(prefix="steel-r3-preworkspace-") as raw:
        fixture = _make_fixture(Path(raw))
        scheduler_markers, original_path = _scheduler_sentinels(fixture.root)
        before = recursive_file_records(fixture.root, exclude=())
        payload, snapshot = collect_r3_preworkspace_failure_evidence(
            **_kwargs(fixture)
        )
        after = recursive_file_records(fixture.root, exclude=())
        assert before == after, "check-only evidence collection wrote a file"
        assert payload["accepted_compute_preflight_evidence"] is False
        assert payload["failure_stage"] == (
            "portable-exclusive-control-lock-before-workspace"
        )
        assert payload["future_successor_required"] is True
        assert payload["runtime_boundary"] == {
            "portable_control_lock_operation": "exclusive",
            "python_exception": "OSError: [Errno 9] Bad file descriptor",
            "workspace_created": False,
            "raw_workspace_created": False,
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "simulation_started": False,
        }
        assert payload["consumption"] == {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        }
        assert snapshot["mutable_roots"] == {
            "intents_empty": True,
            "attempts_empty": True,
            "finalized_empty": True,
        }

        # The public CLI check-only route also stays byte-for-byte read-only.
        original_parse = failure_cli.parse_args
        failure_cli.parse_args = lambda: Namespace(
            check_only=True,
            seal=False,
            execution_dir=fixture.execution,
            held_scontrol_input=fixture.held,
            sacct_input=fixture.sacct,
            squeue_input=fixture.squeue,
            slurm_output_input=fixture.log,
            test_mode=True,
        )
        try:
            with redirect_stdout(StringIO()) as output:
                assert failure_cli.main() == 0
            assert "preview: PASS" in output.getvalue()
        finally:
            failure_cli.parse_args = original_parse
        assert recursive_file_records(fixture.root, exclude=()) == before

        bundle = seal_r3_preworkspace_failure_evidence(**_kwargs(fixture))
        recorded = validate_r3_preworkspace_failure_bundle(
            bundle,
            execution_dir=fixture.execution,
            require_current_execution=True,
            allow_test_mode=True,
        )
        assert recorded == payload
        assert bundle.name == (
            f"pre-workspace-{FORMAL_JOB_ID}-{payload['failure_hash'][:12]}"
        )
        assert bundle.stat().st_mode & 0o7777 == 0o555
        assert all(path.stat().st_mode & 0o7777 == 0o444 for path in bundle.rglob("*"))
        bundle.parent.chmod(0o755)
        assert validate_r3_preworkspace_failure_bundle(
            bundle,
            execution_dir=fixture.execution,
            allow_test_mode=True,
        ) == payload
        bundle.chmod(0o755)
        _expect_failure(
            lambda: validate_r3_preworkspace_failure_bundle(
                bundle,
                execution_dir=fixture.execution,
                allow_test_mode=True,
            ),
            "root mode is not 0555",
        )
        bundle.chmod(0o555)
        failure_json = bundle / "failure.json"
        failure_json.chmod(0o644)
        _expect_failure(
            lambda: validate_r3_preworkspace_failure_bundle(
                bundle,
                execution_dir=fixture.execution,
                allow_test_mode=True,
            ),
            "file mode is not 0444",
        )
        failure_json.chmod(0o444)
        _expect_failure(
            lambda: seal_r3_preworkspace_failure_evidence(**_kwargs(fixture)),
            "refusing to overwrite",
        )

        # A copied/tampered bundle cannot pass even with freshly generated
        # checksums and a recomputed semantic content address.
        tampered = fixture.root / "tampered"
        shutil.copytree(bundle, tampered)
        _make_tree_writable(tampered)
        (tampered / "SHA256SUMS").unlink()
        value = json.loads((tampered / "failure.json").read_text(encoding="utf-8"))
        value["future_successor_required"] = False
        value["failure_hash"] = failure_lib._failure_hash(value)
        value["failure_id"] = (
            f"sm-v1-r3-pre-workspace-failure-{FORMAL_JOB_ID}-"
            f"{value['failure_hash'][:12]}"
        )
        (tampered / "failure.json").write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_recursive_checksums(tampered)
        _expect_failure(
            lambda: validate_r3_preworkspace_failure_bundle(
                tampered,
                allow_test_mode=True,
                require_canonical_location=False,
            ),
            "semantic validation failed",
        )
        _assert_no_scheduler_contact(scheduler_markers)
        os.environ["PATH"] = original_path

    def bad_log(fixture: Fixture) -> None:
        fixture.log.write_text(EXPECTED_LOG.rstrip("\n"), encoding="utf-8")

    _with_fixture(bad_log, "exact pre-workspace lock failure")

    def active_queue(fixture: Fixture) -> None:
        fixture.squeue.write_text(
            f"{FORMAL_JOB_ID}|{FORMAL_JOB_NAME}|RUNNING\n", encoding="utf-8"
        )

    _with_fixture(active_queue, "remains active")

    def wrong_exit(fixture: Fixture) -> None:
        text = fixture.sacct.read_text(encoding="utf-8")
        fixture.sacct.write_text(text.replace("FAILED|1:0", "COMPLETED|0:0", 1))

    _with_fixture(wrong_exit, "terminal accounting identity mismatch")

    def raw_created(fixture: Fixture) -> None:
        (fixture.r3 / "raw" / FORMAL_JOB_NAME).mkdir()

    _with_fixture(raw_created, "unexpectedly created a raw workspace")

    def attempt_created(fixture: Fixture) -> None:
        (fixture.execution / "attempts" / "unexpected").write_text(
            "x\n", encoding="utf-8"
        )

    _with_fixture(attempt_created, "attempts/ must be an existing empty directory")

    def held_mismatch(fixture: Fixture) -> None:
        text = fixture.held.read_text(encoding="utf-8")
        fixture.held.write_text(text.replace("Account=pas2524", "Account=wrong"))

    _with_fixture(held_mismatch, "scheduler identity is not accepted")

    def symlink_input(fixture: Fixture) -> None:
        target = fixture.inputs / "held-real.txt"
        fixture.held.rename(target)
        fixture.held.symlink_to(target.name)

    _with_fixture(symlink_input, "regular non-symlink file")

    def execution_tamper(fixture: Fixture) -> None:
        (fixture.execution / "README.md").write_text("changed\n", encoding="utf-8")

    _with_fixture(execution_tamper, "checksum mismatch")

    with tempfile.TemporaryDirectory(prefix="steel-r3-preworkspace-route-") as raw:
        _test_formal_loader_route(Path(raw))

    with tempfile.TemporaryDirectory(prefix="steel-r3-preworkspace-fault-") as raw:
        _test_publication_fault_cleanup(Path(raw))

    print("steel-module R3 pre-workspace failure evidence: PASS")
    print("scheduler contact: 0; Apptainer calls: 0; Geant4 calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
