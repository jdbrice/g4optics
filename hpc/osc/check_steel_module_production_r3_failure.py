#!/usr/bin/env python3
"""Synthetic checks for the failed-R3 evidence path (zero scheduler contact)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import steel_module_production_r3_failure_lib as failure_lib
import steel_module_production_successor_lib as successor_lib
import seal_steel_module_production_r3_failure as failure_cli
from steel_module_campaign_lib import sha256_file
from steel_module_production_phase2b_lib import (
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
    ROOT_STATIC_MANIFEST,
)
from steel_module_production_r3_failure_lib import (
    EXPECTED_LOG_LINES,
    FORMAL_EXECUTION_HASH,
    FORMAL_EXECUTION_ID,
    FORMAL_JOB_ID,
    FORMAL_JOB_NAME,
    collect_r3_failure_evidence,
    seal_r3_failure_evidence,
    validate_r3_failure_bundle,
)


STATIC_FILES = (
    "README.md",
    "managed_execution.json",
    "scan_args.txt",
    "source_archives.json",
    "tasks.tsv",
)


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.work = root / "work"
        self.execution = (
            self.work / "campaigns" / "steel-module-production-bc-s1-execution-v2"
        )
        self.r3 = self.work / "evidence" / "steel-module-production-r3"
        self.inputs = root / "inputs"
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
    fixture = Fixture(root.resolve())
    execution = fixture.execution
    (execution / "sources/control/hpc/osc").mkdir(parents=True)
    for name in ("intents", "attempts", "finalized"):
        (execution / name).mkdir()
    (execution / ".control.lock").write_bytes(b"")
    (execution / "README.md").write_text("fixture\n", encoding="utf-8")
    (execution / "scan_args.txt").write_text("--fixture\n", encoding="utf-8")
    (execution / "source_archives.json").write_text("{}\n", encoding="utf-8")
    (execution / "tasks.tsv").write_text("logical_task_id\nfixture\n", encoding="utf-8")
    (execution / "sources/control/hpc/osc/run_steel_module_production_r3_container_probe.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        "execution_id": FORMAL_EXECUTION_ID,
        "execution_hash": FORMAL_EXECUTION_HASH,
        "test_mode": True,
        "accepted_statistical_evidence": False,
    }
    (execution / "managed_execution.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
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
                "Command="
                + str(
                    execution
                    / "sources/control/hpc/osc/"
                    "run_steel_module_production_r3_container_probe.py"
                ),
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
                f"{FORMAL_JOB_ID}|{FORMAL_JOB_NAME}|pas2524|FAILED|1:0|1",
                f"{FORMAL_JOB_ID}.batch|batch|pas2524|FAILED|1:0|1",
                f"{FORMAL_JOB_ID}.extern|extern|pas2524|COMPLETED|0:0|1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fixture.squeue.write_text("", encoding="utf-8")
    fixture.log.write_text("\n".join(EXPECTED_LOG_LINES) + "\n", encoding="utf-8")
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
    with tempfile.TemporaryDirectory(prefix="steel-r3-failure-negative-") as raw:
        fixture = _make_fixture(Path(raw))
        mutate(fixture)
        _expect_failure(lambda: collect_r3_failure_evidence(**_kwargs(fixture)), fragment)


def _rewrite_bundle_checksums(bundle: Path) -> None:
    manifest = bundle / "SHA256SUMS"
    manifest.chmod(0o644)
    lines = []
    for path in sorted(bundle.iterdir(), key=lambda value: value.name):
        if path.name != "SHA256SUMS":
            lines.append(f"{sha256_file(path)}  {path.name}\n")
    manifest.write_text("".join(lines), encoding="utf-8")
    manifest.chmod(0o444)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"fixture mutation source missing: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    # The public sealer derives its formal root from its tracked file, never
    # from CWD or from the failed execution's frozen source archive.
    cli_calls: list[dict[str, object]] = []
    original_parse_args = failure_cli.parse_args
    original_collect = failure_cli.collect_r3_failure_evidence

    def cli_collect(**kwargs: object) -> tuple[dict[str, str], dict[str, object]]:
        cli_calls.append(kwargs)
        return {
            "failure_id": "fixture-failure",
            "failure_hash": "b" * 64,
        }, {}

    failure_cli.parse_args = lambda: Namespace(
        check_only=True,
        seal=False,
        execution_dir=Path("/fixture/execution"),
        held_scontrol_input=Path("/fixture/held"),
        sacct_input=Path("/fixture/sacct"),
        squeue_input=Path("/fixture/squeue"),
        slurm_output_input=Path("/fixture/log"),
        test_mode=False,
    )
    failure_cli.collect_r3_failure_evidence = cli_collect
    try:
        with redirect_stdout(StringIO()):
            assert failure_cli.main() == 0
    finally:
        failure_cli.parse_args = original_parse_args
        failure_cli.collect_r3_failure_evidence = original_collect
    assert len(cli_calls) == 1
    assert cli_calls[0]["repo_root"] == Path(failure_cli.__file__).resolve().parents[2]

    # Formal admission must use the canonical live checkout.  The old bug used
    # sources/control here: that lets the outer v2 relocation pass, then fails
    # when the historical v1 loader compares its frozen absolute lock path.
    # Keep this branch independent from the synthetic test-mode coverage below.
    with tempfile.TemporaryDirectory(prefix="steel-r3-failure-formal-route-") as raw:
        fixture = _make_fixture(Path(raw))
        manifest_path = fixture.execution / "managed_execution.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["test_mode"] = False
        manifest["accepted_statistical_evidence"] = True
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        _write_static_manifest(fixture.execution)
        live_root = fixture.root / "canonical-live-repo"
        live_root.mkdir()
        archive_root = fixture.execution / "sources/control"
        calls: list[dict[str, object]] = []
        original_loader = successor_lib.load_successor_execution

        def formal_loader(
            execution_dir: Path, **kwargs: object
        ) -> SimpleNamespace:
            calls.append({"execution_dir": execution_dir, **kwargs})
            if kwargs.get("repo_root") != live_root:
                raise ValueError("managed child formal lock path mismatch")
            return SimpleNamespace(
                directory=fixture.execution,
                execution_id=FORMAL_EXECUTION_ID,
                execution_hash=FORMAL_EXECUTION_HASH,
            )

        successor_lib.load_successor_execution = formal_loader
        try:
            execution, loaded_manifest = failure_lib._validate_execution(
                fixture.execution, repo_root=live_root, test_mode=False
            )
            assert execution == fixture.execution
            assert loaded_manifest == manifest
            assert calls == [
                {
                    "execution_dir": fixture.execution,
                    "repo_root": live_root,
                    "require_readiness": False,
                    "verify_runtime": True,
                    "verify_phase2a_control_plane": True,
                    "verify_live_predecessor": True,
                    "allow_closed_v2": True,
                }
            ]
            _expect_failure(
                lambda: failure_lib._validate_execution(
                    fixture.execution,
                    repo_root=archive_root,
                    test_mode=False,
                ),
                "formal lock path mismatch",
            )
            _expect_failure(
                lambda: failure_lib._validate_execution(
                    fixture.execution, repo_root=None, test_mode=False
                ),
                "canonical live repo root",
            )
        finally:
            successor_lib.load_successor_execution = original_loader

    # Successor admission has already loaded the predecessor in either live or
    # frozen context.  Its failure-bundle bridge must reuse that exact object
    # rather than recursively choosing another repo-root policy.
    predecessor = SimpleNamespace(
        directory=Path("/fixture/execution-v2"),
        execution_id="fixture-execution-id",
        execution_hash="a" * 64,
    )
    bridge_calls: list[dict[str, object]] = []
    original_bundle_validator = failure_lib.validate_r3_failure_bundle

    def bridge_validator(path: Path, **kwargs: object) -> dict[str, object]:
        bridge_calls.append({"path": path, **kwargs})
        return {"test_mode": True}

    failure_lib.validate_r3_failure_bundle = bridge_validator
    try:
        assert successor_lib._validated_r3_failure(
            Path("/fixture/failure"),
            predecessor=predecessor,
            test_mode=True,
            require_current_execution=True,
        ) == {"test_mode": True}
    finally:
        failure_lib.validate_r3_failure_bundle = original_bundle_validator
    assert len(bridge_calls) == 1
    assert bridge_calls[0]["preloaded_execution"] is predecessor
    assert bridge_calls[0]["require_current_execution"] is True

    # Positive preview and seal.  Preview must leave the complete tree unchanged.
    with tempfile.TemporaryDirectory(prefix="steel-r3-failure-positive-") as raw:
        fixture = _make_fixture(Path(raw))
        before = sorted(path.relative_to(fixture.root).as_posix() for path in fixture.root.rglob("*"))
        payload, snapshot = collect_r3_failure_evidence(**_kwargs(fixture))
        after = sorted(path.relative_to(fixture.root).as_posix() for path in fixture.root.rglob("*"))
        assert before == after
        assert payload["accepted_compute_preflight_evidence"] is False
        assert payload["runtime_boundary"]["apptainer_invoked"] is False
        assert payload["runtime_boundary"]["geant4_invoked"] is False
        assert payload["consumption"] == {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        }
        assert snapshot["snapshot_hash"] == payload["execution"]["snapshot_hash"]

        target = seal_r3_failure_evidence(**_kwargs(fixture))
        assert target.parent == fixture.r3 / "failures"
        assert target.name.startswith(f"r3-preflight-{FORMAL_JOB_ID}-")
        recorded = validate_r3_failure_bundle(
            target,
            execution_dir=fixture.execution,
            require_current_execution=True,
            allow_test_mode=True,
        )
        assert recorded == payload
        # A later successor generation may create its own accepted R3 raw
        # workspace.  That must not invalidate this immutable v2 failure; only
        # a workspace for the failed v2 job name would contradict it.
        later_raw = fixture.r3 / "raw" / "g4sm-r3-successor-v3"
        later_raw.mkdir()
        assert validate_r3_failure_bundle(
            target,
            execution_dir=fixture.execution,
            require_current_execution=True,
            allow_test_mode=True,
        ) == payload
        (fixture.r3 / "raw" / FORMAL_JOB_NAME).mkdir()
        _expect_failure(
            lambda: validate_r3_failure_bundle(
                target,
                execution_dir=fixture.execution,
                require_current_execution=True,
                allow_test_mode=True,
            ),
            "unexpectedly acquired a raw workspace",
        )
        (fixture.r3 / "raw" / FORMAL_JOB_NAME).rmdir()
        later_raw.rmdir()

        # A test bundle cannot be relabelled as formal by recomputing its
        # semantic hash and checksum manifest.  Formal validation re-requires
        # the four OSC-captured input digests.
        relabelled = fixture.root / "relabelled-formal"
        shutil.copytree(target, relabelled)
        relabelled.chmod(0o755)
        for path in relabelled.iterdir():
            path.chmod(0o644)
        relabelled_payload_path = relabelled / "failure.json"
        relabelled_payload = json.loads(
            relabelled_payload_path.read_text(encoding="utf-8")
        )
        relabelled_payload["test_mode"] = False
        relabelled_payload["failure_hash"] = failure_lib._failure_hash(
            relabelled_payload
        )
        relabelled_payload["failure_id"] = (
            f"sm-v1-r3-preflight-failure-{FORMAL_JOB_ID}-"
            f"{relabelled_payload['failure_hash'][:12]}"
        )
        relabelled_payload_path.write_text(
            json.dumps(relabelled_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _rewrite_bundle_checksums(relabelled)
        _expect_failure(
            lambda: validate_r3_failure_bundle(
                relabelled,
                require_canonical_location=False,
            ),
            "formal R3 input SHA-256",
        )
        _expect_failure(
            lambda: seal_r3_failure_evidence(**_kwargs(fixture)),
            "refusing to overwrite",
        )

        # Even recomputing SHA256SUMS cannot authorize a semantic alteration.
        failure_path = target / "failure.json"
        failure_path.chmod(0o644)
        altered = json.loads(failure_path.read_text(encoding="utf-8"))
        altered["consumption"]["events_consumed"] = 1
        failure_path.write_text(json.dumps(altered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        failure_path.chmod(0o444)
        _rewrite_bundle_checksums(target)
        _expect_failure(
            lambda: validate_r3_failure_bundle(target, allow_test_mode=True),
            "semantic validation",
        )

    # Scheduler identity, terminal accounting, exact traceback, and no-active-job gates.
    _with_fixture(
        lambda f: _replace(f.held, "Account=pas2524", "Account=WRONG"),
        "scheduler identity",
    )
    _with_fixture(
        lambda f: _replace(f.held, "Requeue=0", "Requeue=1"),
        "scheduler identity",
    )
    _with_fixture(
        lambda f: _replace(f.held, "WorkDir=", "ArrayJobId=50544247 WorkDir="),
        "scheduler identity",
    )
    _with_fixture(
        lambda f: _replace(f.sacct, "|FAILED|1:0|1", "|COMPLETED|0:0|1"),
        "terminal accounting",
    )
    _with_fixture(
        lambda f: _replace(f.sacct, ".extern|extern|pas2524|COMPLETED|0:0", ".extern|extern|pas2524|FAILED|1:0"),
        "terminal accounting",
    )
    _with_fixture(
        lambda f: f.squeue.write_text("50544247|RUNNING\n", encoding="utf-8"),
        "remains active",
    )
    _with_fixture(
        lambda f: _replace(f.log, "ModuleNotFoundError", "RuntimeError"),
        "exact pre-Apptainer",
    )

    # No raw workspace, mutable execution artifact, or alternate identity is admissible.
    _with_fixture(
        lambda f: (f.r3 / "raw" / FORMAL_JOB_NAME).mkdir(),
        "raw root",
    )
    _with_fixture(
        lambda f: (f.execution / "intents" / "unexpected").write_text("x", encoding="utf-8"),
        "intents/",
    )
    _with_fixture(
        lambda f: _replace(f.execution / "managed_execution.json", FORMAL_EXECUTION_ID, "wrong-id"),
        "checksum mismatch",
    )
    with tempfile.TemporaryDirectory(prefix="steel-r3-failure-symlink-") as raw:
        fixture = _make_fixture(Path(raw))
        link = fixture.inputs / "held-link.txt"
        link.symlink_to(fixture.held)
        kwargs = _kwargs(fixture)
        kwargs["held_scontrol_input"] = link
        _expect_failure(
            lambda: collect_r3_failure_evidence(**kwargs),
            "regular non-symlink",
        )

    # A publication failure cleans its staging directory and creates no bundle.
    with tempfile.TemporaryDirectory(prefix="steel-r3-failure-fault-") as raw:
        fixture = _make_fixture(Path(raw))
        original = failure_lib.publish_directory_no_replace

        def fail_publish(_temporary: Path, _target: Path) -> None:
            raise RuntimeError("injected publication failure")

        failure_lib.publish_directory_no_replace = fail_publish
        try:
            _expect_failure(
                lambda: seal_r3_failure_evidence(**_kwargs(fixture)),
                "injected publication failure",
            )
        finally:
            failure_lib.publish_directory_no_replace = original
        failures = fixture.r3 / "failures"
        assert failures.is_dir()
        assert not list(failures.iterdir())

    # A sealed bundle ceases to validate against a mutated live execution.
    with tempfile.TemporaryDirectory(prefix="steel-r3-failure-current-") as raw:
        fixture = _make_fixture(Path(raw))
        target = seal_r3_failure_evidence(**_kwargs(fixture))
        (fixture.execution / "attempts" / "unexpected").write_text("x", encoding="utf-8")
        _expect_failure(
            lambda: validate_r3_failure_bundle(
                target,
                execution_dir=fixture.execution,
                require_current_execution=True,
                allow_test_mode=True,
            ),
            "attempts/",
        )

    print("steel-module failed R3 preflight evidence: PASS")
    print("fixed job: 50544247; failure before Apptainer; events/seeds: 0/0")
    print("scheduler: external frozen inputs only; real scheduler calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
