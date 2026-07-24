#!/usr/bin/env python3
"""Immutable evidence for the fixed execution-v4 pre-workspace R3 failure.

The functions in this module are deliberately scheduler-free.  They consume
externally captured Slurm text, prove that the failed probe never crossed the
portable exclusive-control-lock boundary, and optionally publish one
content-addressed, read-only evidence bundle.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from steel_module_campaign_lib import canonical_json, load_json, sha256_bytes, sha256_file
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    recursive_file_records,
    verify_recursive_checksums,
    write_recursive_checksums,
)
from steel_module_production_phase2b_lib import (
    ROOT_STATIC_MANIFEST,
    fsync_tree,
    verify_execution_static_checksums,
)
from steel_module_production_r3_probe_lib import (
    _held_scheduler_identity,
    _parse_accounting_rows,
    canonical_r3_evidence_root,
    expected_r3_job_name,
)
from steel_module_production_successor_lib import (
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    load_successor_execution,
)


FAILURE_SCHEMA_VERSION = (
    "steel-module-production-r3-pre-workspace-failure-evidence-v1"
)
SNAPSHOT_SCHEMA_VERSION = "steel-module-production-r3-execution-snapshot-v3"
FORMAL_EXECUTION_NAME = "steel-module-production-bc-s1-execution-v4"
FORMAL_EXECUTION_ID = "sm-v1-production-bc-s1-execution-v4-dd37f569f5e8"
FORMAL_EXECUTION_HASH = (
    "c729dfab23ebc6af2da4e1b52b746f78bbb3dd786f1ff57bbe5c0e4b274e625f"
)
FORMAL_JOB_ID = "50558158"
FORMAL_JOB_NAME = "g4sm-r3-c729dfab23eb"
FORMAL_ACCOUNT = "PAS2524"
FORMAL_CLASSIFICATION = "portable-exclusive-control-lock-open-failure"
FORMAL_FAILURE_STAGE = "portable-exclusive-control-lock-before-workspace"
EXPECTED_LOG = (
    "Cannot run steel-module R3 container probe: [Errno 9] Bad file descriptor\n"
)
FORMAL_INPUT_SHA256 = {
    "held_scontrol": (
        "fa5c8bfe8417dbf1842fb57424a77dd70cd5a5a01bd684c5b1ac4af1ecc6e3eb"
    ),
    "sacct": "8df470b4fff19dbcca1d4c259e53ba7dd2377ba3cfc6b73f67aaf639df616ca4",
    "squeue": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "slurm_output": (
        "0c0d9783c71ba16ba4472d34a0bf2464d244258a401a04a5a4a6f471e5c27c4d"
    ),
}

JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MUTABLE_ROOTS = ("intents", "attempts", "finalized")
FAILURE_BUNDLE_FILES = {
    "execution_snapshot.json",
    "failure.json",
    "held_scontrol.txt",
    "sacct.psv",
    "squeue.psv",
    f"slurm-{FORMAL_JOB_ID}.out",
}


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def _failure_hash(value: Mapping[str, Any]) -> str:
    unhashed = dict(value)
    unhashed.pop("failure_id", None)
    unhashed.pop("failure_hash", None)
    return _canonical_hash(unhashed)


def _require_regular_input(path: Path, label: str) -> Path:
    requested = path.expanduser()
    if requested.is_symlink() or not requested.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    resolved = requested.resolve()
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} resolved to an unsafe input")
    return resolved


def _require_empty_mutable_roots(execution: Path) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for name in MUTABLE_ROOTS:
        path = execution / name
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ValueError(f"execution {name}/ must be an existing empty directory")
        result[f"{name}_empty"] = True
    return result


def _execution_snapshot(execution: Path) -> dict[str, Any]:
    """Snapshot every regular file while separately proving mutable roots empty."""

    mutable = _require_empty_mutable_roots(execution)
    records = recursive_file_records(execution, exclude=())
    payload: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "execution_directory": str(execution),
        "regular_file_snapshot_scope": "recursive-regular-files-only",
        "regular_file_records": records,
        "regular_file_snapshot_hash": _canonical_hash(records),
        "regular_file_snapshot_record_count": len(records),
        "mutable_roots": mutable,
    }
    payload["snapshot_hash"] = _canonical_hash(payload)
    return payload


def _validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "execution_directory",
        "regular_file_snapshot_scope",
        "regular_file_records",
        "regular_file_snapshot_hash",
        "regular_file_snapshot_record_count",
        "mutable_roots",
        "snapshot_hash",
    }
    records = snapshot.get("regular_file_records")
    mutable = snapshot.get("mutable_roots")
    unhashed = dict(snapshot)
    recorded_hash = unhashed.pop("snapshot_hash", None)
    if (
        set(snapshot) != expected_keys
        or snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(snapshot.get("execution_directory"), str)
        or snapshot.get("regular_file_snapshot_scope")
        != "recursive-regular-files-only"
        or not isinstance(records, dict)
        or not records
        or list(records) != sorted(records)
        or any(
            not isinstance(name, str)
            or not name
            or name.startswith("/")
            or ".." in Path(name).parts
            or not isinstance(digest, str)
            or not SHA256_RE.fullmatch(digest)
            for name, digest in records.items()
        )
        or snapshot.get("regular_file_snapshot_hash") != _canonical_hash(records)
        or snapshot.get("regular_file_snapshot_record_count") != len(records)
        or mutable
        != {
            "intents_empty": True,
            "attempts_empty": True,
            "finalized_empty": True,
        }
        or recorded_hash != _canonical_hash(unhashed)
    ):
        raise ValueError("R3 pre-workspace execution snapshot is invalid")
    return snapshot


def _load_execution(
    execution_dir: Path, *, repo_root: Path | None, test_mode: bool
) -> Any:
    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("R3 pre-workspace execution path must not be a symlink")
    execution = requested.resolve()
    if execution.name != FORMAL_EXECUTION_NAME or execution.parent.name != "campaigns":
        raise ValueError("R3 pre-workspace evidence requires canonical execution-v4")
    if test_mode:
        verify_execution_static_checksums(execution)
        manifest = load_json(execution / "managed_execution.json")
        if (
            manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION
            or manifest.get("execution_id") != FORMAL_EXECUTION_ID
            or manifest.get("execution_hash") != FORMAL_EXECUTION_HASH
            or manifest.get("test_mode") is not True
            or manifest.get("accepted_statistical_evidence") is not False
        ):
            raise ValueError("test execution-v4 identity mismatch")
        _require_empty_mutable_roots(execution)
        return SimpleNamespace(
            directory=execution,
            execution_id=FORMAL_EXECUTION_ID,
            execution_hash=FORMAL_EXECUTION_HASH,
            manifest=manifest,
        )
    if repo_root is None:
        raise ValueError("formal R3 pre-workspace validation requires repo_root")
    loaded = load_successor_execution(
        execution,
        repo_root=repo_root.expanduser().resolve(),
        require_readiness=False,
        verify_runtime=True,
        verify_phase2a_control_plane=True,
        verify_live_predecessor=True,
    )
    if (
        loaded.directory != execution
        or loaded.execution_id != FORMAL_EXECUTION_ID
        or loaded.execution_hash != FORMAL_EXECUTION_HASH
        or loaded.manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION
        or loaded.manifest.get("test_mode") is not False
        or loaded.manifest.get("accepted_statistical_evidence") is not True
    ):
        raise ValueError("formal execution-v4 identity mismatch")
    _require_empty_mutable_roots(execution)
    return loaded


def _parse_failed_accounting(text: str) -> list[dict[str, str]]:
    rows = _parse_accounting_rows(text)
    expected = (
        (FORMAL_JOB_ID, FORMAL_JOB_NAME, "FAILED", "1:0"),
        (f"{FORMAL_JOB_ID}.batch", "batch", "FAILED", "1:0"),
        (f"{FORMAL_JOB_ID}.extern", "extern", "COMPLETED", "0:0"),
    )
    if len(rows) != len(expected):
        raise ValueError("pre-workspace accounting must contain parent, batch, extern")
    for row, (job_id, job_name, state, exit_code) in zip(rows, expected):
        if (
            row["job_id"] != job_id
            or row["job_name"] != job_name
            or row["account"].casefold() != FORMAL_ACCOUNT.casefold()
            or row["state"] != state
            or row["exit_code"] != exit_code
            or not row["elapsed_raw"].isdigit()
        ):
            raise ValueError("pre-workspace terminal accounting identity mismatch")
    return rows


def _formal_source_paths(execution: Path) -> dict[str, Path]:
    root = canonical_r3_evidence_root(execution)
    return {
        "held_scontrol": root / f"held-scontrol-{FORMAL_JOB_ID}.txt",
        "sacct": root / f"sacct-{FORMAL_JOB_ID}.psv",
        "squeue": root / f"squeue-{FORMAL_JOB_ID}.psv",
        "slurm_output": root / f"slurm-{FORMAL_JOB_ID}.out",
    }


def collect_r3_preworkspace_failure_evidence(
    *,
    execution_dir: Path,
    held_scontrol_input: Path,
    sacct_input: Path,
    squeue_input: Path,
    slurm_output_input: Path,
    repo_root: Path | None = None,
    test_mode: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate external evidence without writing or contacting the scheduler."""

    execution = _load_execution(
        execution_dir, repo_root=repo_root, test_mode=test_mode
    )
    before = _execution_snapshot(execution.directory)
    r3_root = canonical_r3_evidence_root(execution.directory)
    if r3_root.is_symlink() or not r3_root.is_dir():
        raise ValueError("canonical R3 evidence root is missing or unsafe")
    raw_root = r3_root / "raw"
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise ValueError("canonical R3 raw root is missing or unsafe")
    raw_workspace = raw_root / FORMAL_JOB_NAME
    if raw_workspace.exists() or raw_workspace.is_symlink():
        raise ValueError("pre-workspace failure unexpectedly created a raw workspace")

    inputs = {
        "held_scontrol": _require_regular_input(
            held_scontrol_input, "held scontrol input"
        ),
        "sacct": _require_regular_input(sacct_input, "sacct input"),
        "squeue": _require_regular_input(squeue_input, "squeue input"),
        "slurm_output": _require_regular_input(
            slurm_output_input, "Slurm output input"
        ),
    }
    if len(set(inputs.values())) != len(inputs):
        raise ValueError("pre-workspace scheduler inputs must be distinct")
    input_hashes = {name: sha256_file(path) for name, path in inputs.items()}
    if not test_mode:
        formal_paths = _formal_source_paths(execution.directory)
        if any(inputs[name] != formal_paths[name] for name in inputs):
            raise ValueError("formal pre-workspace inputs are outside canonical paths")
        if input_hashes != FORMAL_INPUT_SHA256:
            raise ValueError("formal pre-workspace input SHA-256 identity mismatch")

    held = _held_scheduler_identity(
        inputs["held_scontrol"].read_text(encoding="utf-8"),
        job_id=FORMAL_JOB_ID,
        job_name=FORMAL_JOB_NAME,
        formal_execution_dir=execution.directory,
    )
    rows = _parse_failed_accounting(inputs["sacct"].read_text(encoding="utf-8"))
    if inputs["squeue"].read_text(encoding="utf-8").strip():
        raise ValueError("pre-workspace job remains active in supplied squeue")
    if inputs["slurm_output"].read_text(encoding="utf-8") != EXPECTED_LOG:
        raise ValueError("Slurm log is not the exact pre-workspace lock failure")
    if expected_r3_job_name(execution.execution_hash) != FORMAL_JOB_NAME:
        raise ValueError("execution-v4/job-name derivation mismatch")
    after = _execution_snapshot(execution.directory)
    if before != after:
        raise ValueError("execution-v4 changed while collecting failure evidence")

    payload: dict[str, Any] = {
        "schema_version": FAILURE_SCHEMA_VERSION,
        "test_mode": test_mode,
        "accepted_compute_preflight_evidence": False,
        "classification": FORMAL_CLASSIFICATION,
        "failure_stage": FORMAL_FAILURE_STAGE,
        "future_successor_required": True,
        "execution": {
            "directory": str(execution.directory),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "managed_execution_sha256": sha256_file(
                execution.directory / "managed_execution.json"
            ),
            "static_manifest_sha256": sha256_file(
                execution.directory / ROOT_STATIC_MANIFEST
            ),
            "snapshot_hash": before["snapshot_hash"],
            "regular_file_snapshot_hash": before["regular_file_snapshot_hash"],
            "regular_file_snapshot_record_count": before[
                "regular_file_snapshot_record_count"
            ],
        },
        "scheduler": {
            "job_id": FORMAL_JOB_ID,
            "job_name": FORMAL_JOB_NAME,
            "account": held["account"],
            "held_identity": held,
            "terminal_rows": rows,
            "squeue_terminal_empty": True,
        },
        "runtime_boundary": {
            "portable_control_lock_operation": "exclusive",
            "python_exception": "OSError: [Errno 9] Bad file descriptor",
            "workspace_created": False,
            "raw_workspace_created": False,
            "apptainer_invoked": False,
            "geant4_invoked": False,
            "simulation_started": False,
        },
        "mutable_execution_roots": before["mutable_roots"],
        "raw_workspace": {"path": str(raw_workspace), "exists": False},
        "consumption": {"events_consumed": 0, "production_seeds_consumed": 0},
        "inputs": {
            "held_scontrol_sha256": input_hashes["held_scontrol"],
            "sacct_sha256": input_hashes["sacct"],
            "squeue_sha256": input_hashes["squeue"],
            "slurm_output_sha256": input_hashes["slurm_output"],
        },
        "scheduler_contact_performed_by_tool": False,
    }
    payload["failure_hash"] = _failure_hash(payload)
    payload["failure_id"] = (
        f"sm-v1-r3-pre-workspace-failure-{FORMAL_JOB_ID}-"
        f"{payload['failure_hash'][:12]}"
    )
    return payload, before


def _validate_failure_payload(payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "classification",
        "failure_stage",
        "future_successor_required",
        "execution",
        "scheduler",
        "runtime_boundary",
        "mutable_execution_roots",
        "raw_workspace",
        "consumption",
        "inputs",
        "scheduler_contact_performed_by_tool",
        "failure_hash",
        "failure_id",
    }
    execution = payload.get("execution")
    scheduler = payload.get("scheduler")
    runtime = payload.get("runtime_boundary")
    mutable = payload.get("mutable_execution_roots")
    raw = payload.get("raw_workspace")
    consumption = payload.get("consumption")
    inputs = payload.get("inputs")
    if set(payload) != expected_keys or not all(
        isinstance(value, dict)
        for value in (execution, scheduler, runtime, mutable, raw, consumption, inputs)
    ):
        raise ValueError("R3 pre-workspace failure payload field set mismatch")
    if (
        set(execution)
        != {
            "directory",
            "execution_id",
            "execution_hash",
            "managed_execution_sha256",
            "static_manifest_sha256",
            "snapshot_hash",
            "regular_file_snapshot_hash",
            "regular_file_snapshot_record_count",
        }
        or set(scheduler)
        != {
            "job_id",
            "job_name",
            "account",
            "held_identity",
            "terminal_rows",
            "squeue_terminal_empty",
        }
        or set(runtime)
        != {
            "portable_control_lock_operation",
            "python_exception",
            "workspace_created",
            "raw_workspace_created",
            "apptainer_invoked",
            "geant4_invoked",
            "simulation_started",
        }
        or set(mutable) != {"intents_empty", "attempts_empty", "finalized_empty"}
        or set(raw) != {"path", "exists"}
        or set(consumption) != {"events_consumed", "production_seeds_consumed"}
        or set(inputs)
        != {
            "held_scontrol_sha256",
            "sacct_sha256",
            "squeue_sha256",
            "slurm_output_sha256",
        }
    ):
        raise ValueError("R3 pre-workspace failure nested field set mismatch")
    failure_hash = _failure_hash(payload)
    expected_runtime = {
        "portable_control_lock_operation": "exclusive",
        "python_exception": "OSError: [Errno 9] Bad file descriptor",
        "workspace_created": False,
        "raw_workspace_created": False,
        "apptainer_invoked": False,
        "geant4_invoked": False,
        "simulation_started": False,
    }
    if (
        payload.get("schema_version") != FAILURE_SCHEMA_VERSION
        or not isinstance(payload.get("test_mode"), bool)
        or payload.get("accepted_compute_preflight_evidence") is not False
        or payload.get("classification") != FORMAL_CLASSIFICATION
        or payload.get("failure_stage") != FORMAL_FAILURE_STAGE
        or payload.get("future_successor_required") is not True
        or payload.get("scheduler_contact_performed_by_tool") is not False
        or payload.get("failure_hash") != failure_hash
        or payload.get("failure_id")
        != f"sm-v1-r3-pre-workspace-failure-{FORMAL_JOB_ID}-{failure_hash[:12]}"
        or execution.get("directory") != snapshot.get("execution_directory")
        or execution.get("execution_id") != FORMAL_EXECUTION_ID
        or execution.get("execution_hash") != FORMAL_EXECUTION_HASH
        or execution.get("snapshot_hash") != snapshot.get("snapshot_hash")
        or execution.get("regular_file_snapshot_hash")
        != snapshot.get("regular_file_snapshot_hash")
        or execution.get("regular_file_snapshot_record_count")
        != snapshot.get("regular_file_snapshot_record_count")
        or scheduler.get("job_id") != FORMAL_JOB_ID
        or scheduler.get("job_name") != FORMAL_JOB_NAME
        or scheduler.get("squeue_terminal_empty") is not True
        or runtime != expected_runtime
        or mutable != snapshot.get("mutable_roots")
        or raw.get("path")
        != str(
            canonical_r3_evidence_root(Path(str(execution.get("directory", ""))))
            / "raw"
            / FORMAL_JOB_NAME
        )
        or raw.get("exists") is not False
        or consumption
        != {"events_consumed": 0, "production_seeds_consumed": 0}
    ):
        raise ValueError("R3 pre-workspace failure payload semantic validation failed")


def seal_r3_preworkspace_failure_evidence(
    *,
    execution_dir: Path,
    held_scontrol_input: Path,
    sacct_input: Path,
    squeue_input: Path,
    slurm_output_input: Path,
    repo_root: Path | None = None,
    test_mode: bool = False,
) -> Path:
    payload, snapshot = collect_r3_preworkspace_failure_evidence(
        execution_dir=execution_dir,
        held_scontrol_input=held_scontrol_input,
        sacct_input=sacct_input,
        squeue_input=squeue_input,
        slurm_output_input=slurm_output_input,
        repo_root=repo_root,
        test_mode=test_mode,
    )
    execution = Path(payload["execution"]["directory"])
    failures = canonical_r3_evidence_root(execution) / "failures"
    if failures.is_symlink():
        raise ValueError("R3 failure output root must not be a symlink")
    failures.mkdir(mode=0o700, exist_ok=True)
    if not failures.is_dir():
        raise ValueError("R3 failure output root is not a directory")
    target = failures / (
        f"pre-workspace-{FORMAL_JOB_ID}-{payload['failure_hash'][:12]}"
    )
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite R3 failure bundle: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=failures))
    try:
        sources = {
            "held_scontrol.txt": held_scontrol_input,
            "sacct.psv": sacct_input,
            "squeue.psv": squeue_input,
            f"slurm-{FORMAL_JOB_ID}.out": slurm_output_input,
        }
        for name, source in sources.items():
            shutil.copyfile(_require_regular_input(Path(source), name), temporary / name)
        (temporary / "execution_snapshot.json").write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "failure.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_recursive_checksums(temporary)
        validate_r3_preworkspace_failure_bundle(
            temporary,
            execution_dir=execution,
            require_current_execution=True,
            allow_test_mode=test_mode,
            require_canonical_location=False,
            repo_root=repo_root,
        )
        for path in temporary.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        fsync_tree(temporary)
        temporary.chmod(0o555)
        publish_directory_no_replace(temporary, target)
        temporary = None
        validate_r3_preworkspace_failure_bundle(
            target,
            execution_dir=execution,
            require_current_execution=True,
            allow_test_mode=test_mode,
            repo_root=repo_root,
        )
        return target
    finally:
        if temporary is not None and temporary.exists():
            for path in temporary.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)
                elif path.is_dir():
                    path.chmod(0o700)
            temporary.chmod(0o700)
            shutil.rmtree(temporary)


def validate_r3_preworkspace_failure_bundle(
    bundle_dir: Path,
    *,
    execution_dir: Path | None = None,
    require_current_execution: bool = False,
    allow_test_mode: bool = False,
    require_canonical_location: bool = True,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Validate one sealed bundle without scheduler, container, or simulation use."""

    requested = bundle_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink() or not requested.is_dir():
        raise ValueError("R3 pre-workspace failure bundle must be a regular directory")
    root = requested.resolve()
    records = verify_recursive_checksums(root, required=FAILURE_BUNDLE_FILES)
    if set(records) != FAILURE_BUNDLE_FILES:
        raise ValueError("R3 pre-workspace failure bundle file set mismatch")
    payload = load_json(root / "failure.json")
    snapshot = _validate_snapshot(load_json(root / "execution_snapshot.json"))
    _validate_failure_payload(payload, snapshot)
    test_mode = payload["test_mode"]
    if test_mode and not allow_test_mode:
        raise ValueError("test pre-workspace failure is not formal evidence")
    execution = Path(payload["execution"]["directory"])
    if execution_dir is not None and execution != execution_dir.expanduser().resolve():
        raise ValueError("pre-workspace failure execution directory mismatch")
    expected = canonical_r3_evidence_root(execution) / "failures" / (
        f"pre-workspace-{FORMAL_JOB_ID}-{payload['failure_hash'][:12]}"
    )
    if require_canonical_location and root != expected:
        raise ValueError("pre-workspace failure bundle is outside its content address")
    if require_canonical_location:
        if stat.S_IMODE(root.stat().st_mode) != 0o555:
            raise ValueError("canonical pre-workspace failure root mode is not 0555")
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                raise ValueError("canonical pre-workspace failure entry is unsafe")
            if stat.S_IMODE(path.stat().st_mode) != 0o444:
                raise ValueError(
                    "canonical pre-workspace failure file mode is not 0444"
                )

    held_text = (root / "held_scontrol.txt").read_text(encoding="utf-8")
    sacct_text = (root / "sacct.psv").read_text(encoding="utf-8")
    squeue_text = (root / "squeue.psv").read_text(encoding="utf-8")
    log_text = (root / f"slurm-{FORMAL_JOB_ID}.out").read_text(encoding="utf-8")
    held = _held_scheduler_identity(
        held_text,
        job_id=FORMAL_JOB_ID,
        job_name=FORMAL_JOB_NAME,
        formal_execution_dir=execution,
    )
    rows = _parse_failed_accounting(sacct_text)
    if squeue_text.strip() or log_text != EXPECTED_LOG:
        raise ValueError("sealed pre-workspace scheduler/log evidence changed")
    actual_inputs = {
        "held_scontrol_sha256": sha256_file(root / "held_scontrol.txt"),
        "sacct_sha256": sha256_file(root / "sacct.psv"),
        "squeue_sha256": sha256_file(root / "squeue.psv"),
        "slurm_output_sha256": sha256_file(root / f"slurm-{FORMAL_JOB_ID}.out"),
    }
    if (
        payload["scheduler"].get("held_identity") != held
        or payload["scheduler"].get("terminal_rows") != rows
        or payload["scheduler"].get("account") != held["account"]
        or payload["inputs"] != actual_inputs
    ):
        raise ValueError("sealed pre-workspace source identity mismatch")
    if not test_mode and {
        "held_scontrol": actual_inputs["held_scontrol_sha256"],
        "sacct": actual_inputs["sacct_sha256"],
        "squeue": actual_inputs["squeue_sha256"],
        "slurm_output": actual_inputs["slurm_output_sha256"],
    } != FORMAL_INPUT_SHA256:
        raise ValueError("sealed formal pre-workspace input SHA-256 mismatch")

    snapshot_files = snapshot["regular_file_records"]
    if (
        snapshot_files.get("managed_execution.json")
        != payload["execution"].get("managed_execution_sha256")
        or snapshot_files.get(ROOT_STATIC_MANIFEST)
        != payload["execution"].get("static_manifest_sha256")
    ):
        raise ValueError("sealed pre-workspace execution snapshot identity mismatch")
    if require_current_execution:
        current = _load_execution(
            execution, repo_root=repo_root, test_mode=test_mode
        )
        if _execution_snapshot(current.directory) != snapshot:
            raise ValueError("execution-v4 changed since failure capture")
        raw_workspace = (
            canonical_r3_evidence_root(current.directory) / "raw" / FORMAL_JOB_NAME
        )
        if raw_workspace.exists() or raw_workspace.is_symlink():
            raise ValueError("failed pre-workspace probe acquired a raw workspace")
    return payload


__all__ = [
    "EXPECTED_LOG",
    "FAILURE_SCHEMA_VERSION",
    "FORMAL_EXECUTION_HASH",
    "FORMAL_EXECUTION_ID",
    "FORMAL_INPUT_SHA256",
    "FORMAL_JOB_ID",
    "FORMAL_JOB_NAME",
    "collect_r3_preworkspace_failure_evidence",
    "seal_r3_preworkspace_failure_evidence",
    "validate_r3_preworkspace_failure_bundle",
]
