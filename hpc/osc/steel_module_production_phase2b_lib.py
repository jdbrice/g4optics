#!/usr/bin/env python3
"""Fail-closed managed execution primitives for steel-module Phase-2B.

This module deliberately does not contain a generic command runner.  Formal
Slurm commands are assembled from checksum-bound state and are invoked only by
``manage_steel_module_production_attempt.py`` after the tracked readiness lock
has been validated.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    load_json,
    parse_campaign_tasks,
    require_dict,
    require_sha1,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)
from steel_module_managed_production_lib import (
    INITIAL_CHILD_ID,
    ManagedProductionChild,
    load_managed_production_child,
)


EXECUTION_SCHEMA_VERSION_V1 = "steel-module-production-managed-execution-v1"
EXECUTION_SCHEMA_VERSION_V2 = "steel-module-production-managed-execution-v2"
EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1 = (
    "steel-module-production-managed-successor-execution-v1"
)
EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2 = (
    "steel-module-production-managed-successor-execution-v2"
)
EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3 = (
    "steel-module-production-managed-successor-execution-v3"
)
EXECUTION_SCHEMA_VERSION_SUCCESSOR_V4 = (
    "steel-module-production-managed-successor-execution-v4"
)
EXECUTION_SCHEMA_VERSION_PHASE2C_V6 = (
    "steel-module-production-managed-execution-v6"
)
SUCCESSOR_OBJECT_KIND = "managed-production-recovery-successor"
# Keep the public legacy name stable.  The canonical Phase-2B materializer
# remains v1 unless a successor explicitly selects the portable lock protocol.
EXECUTION_SCHEMA_VERSION = EXECUTION_SCHEMA_VERSION_V1
CONTROL_LOCK_PROTOCOL_V1 = "inode-bound-v1"
CONTROL_LOCK_PROTOCOL_V2 = "portable-flock-v2"
CONTROL_LOCK_PROTOCOL_V3_EMPTY = "portable-flock-v3-empty"
CONTROL_LOCK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
HISTORICAL_CLOSED_EXECUTION_ID = (
    "sm-v1-production-bc-s1-execution-193d261c3059"
)
HISTORICAL_CLOSED_EXECUTION_HASH = (
    "d07a32a6afea7d2345b4f4ca45996fd88dde93a7cf8f9d875e0ba0e47e52cf7b"
)
HISTORICAL_CLOSED_ATTEMPT_ID = "20260718T175107Z-initial"
HISTORICAL_CLOSED_INTENT_SHA256 = (
    "1232d0e69302634580b5e6d1ab4d1c725a7d6ede08e4181c717ba71adb494b9d"
)
HISTORICAL_CLOSED_JOB_ID = "50532143"
HISTORICAL_CLOSED_LOCK_DEVICE = 214
HISTORICAL_CLOSED_LOCK_INODE = 958967
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
INTENT_SCHEMA_VERSION = "steel-module-production-attempt-intent-v1"
EVENT_SCHEMA_VERSION = "steel-module-production-attempt-event-v1"
ACCOUNTING_SCHEMA_VERSION_V1 = "steel-module-production-terminal-accounting-v1"
ACCOUNTING_SCHEMA_VERSION_V2 = "steel-module-production-terminal-accounting-v2"
ACCOUNTING_SCHEMA_VERSION = ACCOUNTING_SCHEMA_VERSION_V2
TASK_RESULT_SCHEMA_VERSION = "steel-module-managed-production-task-result-v1"
READINESS_LOCK_SCHEMA_VERSION = "steel-module-production-phase2b-readiness-lock-v1"
PHASE2A_LOCK_SCHEMA_VERSION = "steel-module-production-bc-s1-phase2a-lock-v1"
PHASE2A_LOCK_RELATIVE = Path(
    "hpc/osc/configurations/steel-module-production-bc-s1-phase2a-v1.lock.json"
)
READINESS_LOCK_RELATIVE = Path(
    "hpc/osc/configurations/steel-module-production-phase2b-v1.lock.json"
)
READINESS_CRITICAL_ARTIFACTS = (
    "hpc/osc/steel_module_campaign_lib.py",
    "hpc/osc/steel_module_managed_production_lib.py",
    "hpc/osc/steel_module_production_program_lib.py",
    "hpc/osc/record_realistic_neutron_task_result.py",
    "hpc/osc/record_steel_module_task_result.py",
    "hpc/osc/audit_realistic_neutron_campaign.py",
    "hpc/osc/realistic_neutron_event_audit.py",
    "hpc/osc/finalize_steel_module_campaign.py",
    "hpc/osc/analyze_steel_module_campaign.py",
    "hpc/osc/analyze_steel_module_campaign_v2.py",
    "hpc/osc/plot_steel_module_analysis_v2.py",
    "hpc/osc/steel_module_production_phase2b_lib.py",
    "hpc/osc/materialize_steel_module_production_execution.py",
    "hpc/osc/manage_steel_module_production_attempt.py",
    "hpc/osc/seal_steel_module_production_phase2b_incident.py",
    "hpc/osc/record_steel_module_production_task_result.py",
    "hpc/osc/run_steel_module_production_phase2b_task.py",
    "hpc/osc/submit_steel_module_production_phase2b.sbatch",
    "hpc/osc/generate_steel_module_production_phase2b_readiness.py",
    "hpc/osc/check_steel_module_production_phase2b_control.py",
    "hpc/osc/validate_steel_module_production_phase2b_readiness.py",
    "hpc/osc/finalize_steel_module_managed_child.py",
    "hpc/osc/build_steel_module_production_checkpoint.py",
    "hpc/osc/steel_module_production_checkpoint_lib.py",
    "hpc/osc/check_steel_module_production_finalization.py",
    "hpc/osc/analyze_steel_module_production_checkpoint.py",
    "hpc/osc/render_steel_module_production_review.py",
    "hpc/osc/record_steel_module_progression.py",
    "hpc/osc/check_steel_module_production_analysis.py",
    "hpc/osc/check_steel_module_campaign_infrastructure.py",
    "hpc/osc/README.md",
    "docs/decisions/steel-module-production-phase2b-v1.md",
    "docs/decisions/steel-module-production-phase2b-recovery-v1.md",
    "docs/decisions/steel-module-production-program-freeze-v1.md",
)
FORMAL_CHILD_NAME = "steel-module-production-bc-s1"
FORMAL_EXECUTION_NAME = "steel-module-production-bc-s1-execution"
FORMAL_PILOT_NAME = "steel-module-convergence-pilot-cfd7d974"
FORMAL_ACCOUNT = "PAS2524"
STATIC_FILES = {
    "README.md",
    "managed_execution.json",
    "scan_args.txt",
    "source_archives.json",
    "tasks.tsv",
}
ROOT_STATIC_MANIFEST = "STATIC_SHA256SUMS"
BATCH_SCRIPT_RELATIVE = (
    "sources/control/hpc/osc/submit_steel_module_production_phase2b.sbatch"
)
ATTEMPT_ID_RE = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
)
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")
EVENT_NAME_RE = re.compile(
    r"^(?P<sequence>[0-9]{6})-(?P<kind>[a-z0-9-]+)-(?P<digest>[0-9a-f]{12})\.json$"
)
TERMINAL_FAILURE_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}
ACTIVE_STATES = {
    "COMPLETING",
    "CONFIGURING",
    "PENDING",
    "REQUEUED",
    "RESIZING",
    "RUNNING",
    "SUSPENDED",
}


def base_slurm_state(value: Any) -> str:
    """Return the stable Slurm state token used by accounting decisions.

    ``sacct`` commonly renders cancellation as ``CANCELLED by <uid>`` and may
    append ``+`` to truncated states.  Both forms must map to the same terminal
    state without weakening the handling of any other token.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Slurm state must be a non-empty string")
    state = value.strip().split("+", 1)[0]
    if state == "CANCELLED" or state.startswith("CANCELLED by "):
        return "CANCELLED"
    return state
ATTEMPT_EVENT_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"intent-cancelled", "submission-invoked"},
    "submission-invoked": {
        "submitted-held", "submission-ambiguous", "scheduler-observation",
    },
    "submission-ambiguous": {
        "submitted-held", "submission-not-found-confirmed",
        "submission-permanently-ambiguous", "scheduler-observation",
    },
    "scheduler-observation": {
        "submitted-held", "submission-not-found-confirmed",
        "submission-permanently-ambiguous", "scheduler-observation",
    },
    "submitted-held": {
        "job-verified", "release-ambiguous",
        "submission-permanently-ambiguous",
    },
    "job-verified": {
        "job-released", "release-invoked", "release-ambiguous",
        "submission-permanently-ambiguous",
    },
    "release-invoked": {
        "job-verified", "job-released", "release-outcome-ambiguous",
        "release-invoked-scheduler-observation",
        "submission-permanently-ambiguous",
    },
    "release-outcome-ambiguous": {
        "job-verified", "job-released",
        "release-invoked-scheduler-observation",
        "submission-permanently-ambiguous",
    },
    "release-invoked-scheduler-observation": {
        "job-verified", "job-released",
        "release-invoked-scheduler-observation",
        "submission-permanently-ambiguous",
    },
    "release-ambiguous": {
        "job-verified", "job-released", "release-scheduler-observation",
        "submission-permanently-ambiguous",
    },
    "release-scheduler-observation": {
        "job-verified", "job-released", "release-scheduler-observation",
        "submission-permanently-ambiguous",
    },
    "job-released": {"terminal-accounting-frozen"},
    "intent-cancelled": set(),
    "submission-not-found-confirmed": set(),
    "submission-permanently-ambiguous": set(),
    "terminal-accounting-frozen": set(),
}


@dataclass(frozen=True)
class ManagedExecution:
    directory: Path
    managed_child: ManagedProductionChild
    manifest: dict[str, Any]
    tasks: tuple[CampaignTask, ...]
    scan_args: tuple[str, ...]

    @property
    def execution_id(self) -> str:
        return require_string(self.manifest, "execution_id")

    @property
    def execution_hash(self) -> str:
        return require_sha256(self.manifest, "execution_hash")

    @property
    def campaign_id(self) -> str:
        return self.managed_child.plan.campaign_id

    @property
    def plan_hash(self) -> str:
        return self.managed_child.plan.plan_hash

    @property
    def git_commit(self) -> str:
        return self.managed_child.plan.git_commit

    @property
    def environment(self) -> dict[str, Any]:
        return self.managed_child.plan.environment


@dataclass(frozen=True)
class AttemptState:
    attempt_id: str
    status: str
    intent: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    job_id: str | None
    scheduler_contacted: bool
    unresolved: bool
    terminal: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str, label: str = "identifier") -> str:
    if not ATTEMPT_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe {label}: {value!r}")
    try:
        datetime.strptime(value[:16], "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise ValueError(f"unsafe {label} timestamp: {value!r}") from exc
    return value


def _safe_actor(value: str) -> str:
    if not ACTOR_RE.fullmatch(value):
        raise ValueError(f"unsafe actor identity: {value!r}")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path) -> None:
    _fsync_directory(path)


def write_exclusive_bytes(
    path: Path,
    content: bytes,
    *,
    mode: int = 0o444,
    temporary_dir: Path | None = None,
    temporary_prefix: str | None = None,
) -> None:
    """Publish a fully fsynced immutable file with atomic no-replace semantics."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=temporary_prefix or f".{path.name}.tmp-",
        dir=temporary_dir or path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode, follow_symlinks=False)
        # hard-link publication is atomic and fails rather than replacing an
        # existing file.  Both paths are necessarily on the same filesystem.
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
            _fsync_directory(path.parent)


def write_exclusive_json(
    path: Path,
    value: object,
    *,
    temporary_dir: Path | None = None,
    temporary_prefix: str | None = None,
) -> None:
    write_exclusive_bytes(
        path,
        json.dumps(value, indent=2).encode() + b"\n",
        temporary_dir=temporary_dir,
        temporary_prefix=temporary_prefix,
    )


def _portable_lock_content(token: str) -> bytes:
    if not isinstance(token, str) or not CONTROL_LOCK_TOKEN_RE.fullmatch(token):
        raise ValueError("portable control-lock content token is invalid")
    return f"steel-module-managed-execution-control-lock-v2:{token}\n".encode()


def _safe_lock_lstat(lock_path: Path) -> os.stat_result:
    try:
        value = lock_path.lstat()
    except OSError as exc:
        raise ValueError("managed execution control lock is missing or unsafe") from exc
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise ValueError("managed execution control lock is not a regular file")
    return value


def _same_node_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare two observations made on the same host.

    Parallel filesystems may expose different ``st_dev``/``st_ino`` values on
    login and compute nodes.  Those values are still useful for detecting a
    path swap between lstat/fstat calls made by one process on one node.
    """

    return os.path.samestat(left, right)


def _read_descriptor_bytes(descriptor: int, size: int) -> bytes:
    if size < 0 or size > 4096:
        raise ValueError("managed execution control-lock size is unsafe")
    chunks: list[bytes] = []
    offset = 0
    while offset <= size:
        chunk = os.pread(descriptor, min(4096, size + 1 - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _validate_portable_lock_stat(
    value: os.stat_result, record: dict[str, Any]
) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("managed execution control lock is not a regular file")
    if value.st_nlink != record["link_count"]:
        raise ValueError("managed execution control-lock link-count mismatch")
    if value.st_nlink != 1:
        raise ValueError("managed execution portable control lock must have one link")
    if value.st_mode & 0o777 != record["mode"]:
        raise ValueError("managed execution control-lock mode mismatch")
    if value.st_size != record["size_bytes"]:
        raise ValueError("managed execution control-lock size mismatch")


def _validate_portable_lock_record(record: dict[str, Any]) -> bytes:
    if record.get("protocol") == CONTROL_LOCK_PROTOCOL_V3_EMPTY:
        expected_keys = {
            "path", "protocol", "mode", "size_bytes", "sha256",
            "link_count", "device_inode_authority", "creation_device",
            "creation_inode",
        }
        if set(record) != expected_keys:
            raise ValueError("empty portable control-lock record shape mismatch")
        if (
            record.get("path") != ".control.lock"
            or record.get("mode") != 0o600
            or record.get("size_bytes") != 0
            or record.get("sha256") != EMPTY_SHA256
            or record.get("link_count") != 1
            or record.get("device_inode_authority") is not False
            or not isinstance(record.get("creation_device"), int)
            or record["creation_device"] < 0
            or not isinstance(record.get("creation_inode"), int)
            or record["creation_inode"] < 0
        ):
            raise ValueError("empty portable control-lock identity mismatch")
        return b""
    expected_keys = {
        "path", "protocol", "content_token", "mode", "size_bytes", "sha256",
        "link_count", "creation_device", "creation_inode",
    }
    if set(record) != expected_keys:
        raise ValueError("portable control-lock record shape mismatch")
    if record.get("path") != ".control.lock":
        raise ValueError("portable control-lock path mismatch")
    if record.get("protocol") != CONTROL_LOCK_PROTOCOL_V2:
        raise ValueError("portable control-lock protocol mismatch")
    content = _portable_lock_content(record.get("content_token"))
    if (
        record.get("mode") not in {0o400, 0o600}
        or record.get("size_bytes") != len(content)
        or record.get("sha256") != sha256_bytes(content)
        or record.get("link_count") != 1
        or not isinstance(record.get("creation_device"), int)
        or record["creation_device"] < 0
        or not isinstance(record.get("creation_inode"), int)
        or record["creation_inode"] < 0
    ):
        raise ValueError("portable control-lock immutable identity mismatch")
    return content


@contextmanager
def _opened_portable_control_lock(
    lock_path: Path,
    record: dict[str, Any],
    *,
    operation: int,
) -> Iterator[None]:
    expected_content = _validate_portable_lock_record(record)
    before_path = _safe_lock_lstat(lock_path)
    _validate_portable_lock_stat(before_path, record)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("portable control lock requires O_NOFOLLOW support")
    lock_kind = operation & (fcntl.LOCK_SH | fcntl.LOCK_EX)
    if lock_kind not in {fcntl.LOCK_SH, fcntl.LOCK_EX}:
        raise ValueError("portable control lock operation is unsupported")
    # GPFS requires a write-capable descriptor for an exclusive flock.  Keep
    # validation read-only, but require the recorded 0600 authority and open
    # O_RDWR for every mutation lease.  In particular, a historical 0400 lock
    # fails closed here before open/flock; this function never repairs modes.
    if lock_kind == fcntl.LOCK_EX:
        if record["mode"] != 0o600:
            raise ValueError(
                "portable control lock requires recorded mode 0600 for "
                "exclusive acquisition"
            )
        access_mode = os.O_RDWR
    else:
        access_mode = os.O_RDONLY
    flags = access_mode | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock_path, flags)
    except OSError as exc:
        raise ValueError("managed execution control lock cannot be opened safely") from exc
    locked = False
    try:
        before_fd = os.fstat(descriptor)
        if not _same_node_file_identity(before_path, before_fd):
            raise ValueError("managed execution control lock changed while opening")
        _validate_portable_lock_stat(before_fd, record)
        content = _read_descriptor_bytes(descriptor, before_fd.st_size)
        if content != expected_content or sha256_bytes(content) != record["sha256"]:
            raise ValueError("managed execution control-lock content-token/hash mismatch")

        fcntl.flock(descriptor, operation)
        locked = True
        after_fd = os.fstat(descriptor)
        after_path = _safe_lock_lstat(lock_path)
        if (
            not _same_node_file_identity(before_fd, after_fd)
            or not _same_node_file_identity(after_fd, after_path)
        ):
            raise ValueError("managed execution control lock changed while acquiring flock")
        _validate_portable_lock_stat(after_fd, record)
        _validate_portable_lock_stat(after_path, record)
        content = _read_descriptor_bytes(descriptor, after_fd.st_size)
        if content != expected_content or sha256_bytes(content) != record["sha256"]:
            raise ValueError("managed execution control-lock content-token/hash mismatch")

        try:
            yield
        finally:
            # Recheck even when the protected operation raises.  A path swap
            # must not be hidden by an unrelated exception from the caller.
            final_fd = os.fstat(descriptor)
            final_path = _safe_lock_lstat(lock_path)
            if (
                not _same_node_file_identity(after_fd, final_fd)
                or not _same_node_file_identity(final_fd, final_path)
            ):
                raise ValueError("managed execution control lock changed while held")
            _validate_portable_lock_stat(final_fd, record)
            _validate_portable_lock_stat(final_path, record)
            content = _read_descriptor_bytes(descriptor, final_fd.st_size)
            if content != expected_content or sha256_bytes(content) != record["sha256"]:
                raise ValueError("managed execution control-lock content-token/hash mismatch")
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_inode_bound_v1_lock(lock_path: Path, record: dict[str, Any]) -> None:
    value = _safe_lock_lstat(lock_path)
    expected = {
        "path": ".control.lock", "device": value.st_dev,
        "inode": value.st_ino, "mode": value.st_mode & 0o777,
        "size_bytes": value.st_size, "sha256": sha256_file(lock_path),
    }
    if record != expected:
        raise ValueError("managed execution control-lock inode identity mismatch")


def _require_historical_closed_execution_identity(
    manifest: dict[str, Any]
) -> dict[str, Any]:
    if (
        manifest.get("schema_version") != EXECUTION_SCHEMA_VERSION_V1
        or manifest.get("test_mode") is not False
        or manifest.get("execution_id") != HISTORICAL_CLOSED_EXECUTION_ID
        or manifest.get("execution_hash") != HISTORICAL_CLOSED_EXECUTION_HASH
        or manifest.get("execution_hash")
        != _semantic_hash(manifest, "execution_hash")
    ):
        raise ValueError("historical recovery lock is limited to the closed execution")
    record = require_dict(require_dict(manifest, "artifacts"), "control_lock")
    if (
        set(record)
        != {"path", "device", "inode", "mode", "size_bytes", "sha256"}
        or record.get("path") != ".control.lock"
        or record.get("device") != HISTORICAL_CLOSED_LOCK_DEVICE
        or record.get("inode") != HISTORICAL_CLOSED_LOCK_INODE
        or record.get("mode") != 0o600
        or record.get("size_bytes") != 0
        or record.get("sha256") != EMPTY_SHA256
    ):
        raise ValueError("historical recovery control-lock record mismatch")
    return record


def _validate_historical_recovery_lock_stat(
    value: os.stat_result, record: dict[str, Any]
) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise ValueError("historical recovery control lock is not a regular file")
    if value.st_nlink != 1:
        raise ValueError("historical recovery control-lock link-count mismatch")
    if value.st_mode & 0o777 != record["mode"]:
        raise ValueError("historical recovery control-lock mode mismatch")
    if value.st_size != record["size_bytes"]:
        raise ValueError("historical recovery control-lock size mismatch")


@contextmanager
def _opened_historical_recovery_lock(
    lock_path: Path,
    record: dict[str, Any],
    *,
    operation: int,
) -> Iterator[None]:
    """Lock the exact closed v1 companion without a cross-node inode claim."""

    before_path = _safe_lock_lstat(lock_path)
    _validate_historical_recovery_lock_stat(before_path, record)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("historical recovery control lock requires O_NOFOLLOW")
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise ValueError("historical recovery control lock cannot be opened") from exc
    locked = False
    try:
        before_fd = os.fstat(descriptor)
        if not _same_node_file_identity(before_path, before_fd):
            raise ValueError("historical recovery control lock changed while opening")
        _validate_historical_recovery_lock_stat(before_fd, record)
        content = _read_descriptor_bytes(descriptor, before_fd.st_size)
        if content or sha256_bytes(content) != record["sha256"]:
            raise ValueError("historical recovery control-lock content mismatch")
        fcntl.flock(descriptor, operation)
        locked = True
        after_fd = os.fstat(descriptor)
        after_path = _safe_lock_lstat(lock_path)
        if (
            not _same_node_file_identity(before_fd, after_fd)
            or not _same_node_file_identity(after_fd, after_path)
        ):
            raise ValueError("historical recovery control lock changed while acquiring")
        _validate_historical_recovery_lock_stat(after_fd, record)
        _validate_historical_recovery_lock_stat(after_path, record)
        content = _read_descriptor_bytes(descriptor, after_fd.st_size)
        if content or sha256_bytes(content) != record["sha256"]:
            raise ValueError("historical recovery control-lock content mismatch")
        try:
            yield
        finally:
            final_fd = os.fstat(descriptor)
            final_path = _safe_lock_lstat(lock_path)
            if (
                not _same_node_file_identity(after_fd, final_fd)
                or not _same_node_file_identity(final_fd, final_path)
            ):
                raise ValueError("historical recovery control lock changed while held")
            _validate_historical_recovery_lock_stat(final_fd, record)
            _validate_historical_recovery_lock_stat(final_path, record)
            content = _read_descriptor_bytes(descriptor, final_fd.st_size)
            if content or sha256_bytes(content) != record["sha256"]:
                raise ValueError("historical recovery control-lock content mismatch")
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _opened_inode_bound_v1_lock(
    lock_path: Path, record: dict[str, Any]
) -> Iterator[None]:
    """Acquire a legacy lock without weakening its frozen inode semantics."""

    _validate_inode_bound_v1_lock(lock_path, record)
    before_path = _safe_lock_lstat(lock_path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("managed execution control lock requires O_NOFOLLOW support")
    try:
        descriptor = os.open(
            lock_path, os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as exc:
        raise ValueError("managed execution control lock cannot be opened safely") from exc
    locked = False
    try:
        before_fd = os.fstat(descriptor)
        if not _same_node_file_identity(before_path, before_fd):
            raise ValueError("managed execution legacy control lock changed while opening")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        after_fd = os.fstat(descriptor)
        after_path = _safe_lock_lstat(lock_path)
        if (
            not _same_node_file_identity(before_fd, after_fd)
            or not _same_node_file_identity(after_fd, after_path)
        ):
            raise ValueError(
                "managed execution legacy control lock changed while acquiring flock"
            )
        _validate_inode_bound_v1_lock(lock_path, record)
        try:
            yield
        finally:
            final_fd = os.fstat(descriptor)
            final_path = _safe_lock_lstat(lock_path)
            if (
                not _same_node_file_identity(after_fd, final_fd)
                or not _same_node_file_identity(final_fd, final_path)
            ):
                raise ValueError("managed execution legacy control lock changed while held")
            _validate_inode_bound_v1_lock(lock_path, record)
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _control_lock_record_from_manifest(
    directory: Path, manifest: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    schema = manifest.get("schema_version")
    record = require_dict(require_dict(manifest, "artifacts"), "control_lock")
    if schema == EXECUTION_SCHEMA_VERSION_V1:
        return CONTROL_LOCK_PROTOCOL_V1, record
    if schema in {
        EXECUTION_SCHEMA_VERSION_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
        EXECUTION_SCHEMA_VERSION_SUCCESSOR_V4,
    }:
        return CONTROL_LOCK_PROTOCOL_V2, record
    if schema == EXECUTION_SCHEMA_VERSION_PHASE2C_V6:
        if record.get("protocol") != CONTROL_LOCK_PROTOCOL_V3_EMPTY:
            raise ValueError("execution-v6 portable control-lock protocol mismatch")
        return CONTROL_LOCK_PROTOCOL_V3_EMPTY, record
    raise ValueError("unsupported managed-execution schema")


def validate_execution_control_lock(
    directory: Path, manifest: dict[str, Any]
) -> None:
    protocol, record = _control_lock_record_from_manifest(directory, manifest)
    lock_path = directory / ".control.lock"
    if protocol == CONTROL_LOCK_PROTOCOL_V1:
        _validate_inode_bound_v1_lock(lock_path, record)
        return
    with _opened_portable_control_lock(lock_path, record, operation=fcntl.LOCK_SH):
        pass


def validate_historical_recovery_control_lock(
    directory: Path, manifest: dict[str, Any]
) -> None:
    record = _require_historical_closed_execution_identity(manifest)
    with _opened_historical_recovery_lock(
        directory / ".control.lock", record, operation=fcntl.LOCK_SH
    ):
        pass


@contextmanager
def historical_recovery_execution_lock(
    execution: ManagedExecution,
) -> Iterator[None]:
    record = _require_historical_closed_execution_identity(execution.manifest)
    with _opened_historical_recovery_lock(
        execution.directory / ".control.lock", record, operation=fcntl.LOCK_EX
    ):
        yield


@contextmanager
def execution_lock(execution: ManagedExecution | Path | Any) -> Iterator[None]:
    if not isinstance(execution, (str, os.PathLike)) and hasattr(
        execution, "directory"
    ) and hasattr(execution, "manifest"):
        directory = execution.directory
        manifest = execution.manifest
    else:
        directory = Path(execution).expanduser().resolve()
        verify_execution_static_checksums(directory)
        manifest = load_json(directory / "managed_execution.json")
        if manifest.get("execution_hash") != _semantic_hash(manifest, "execution_hash"):
            raise ValueError("managed-execution semantic hash mismatch")
    protocol, record = _control_lock_record_from_manifest(directory, manifest)
    lock_path = directory / ".control.lock"
    if protocol == CONTROL_LOCK_PROTOCOL_V1:
        with _opened_inode_bound_v1_lock(lock_path, record):
            yield
        return
    with _opened_portable_control_lock(lock_path, record, operation=fcntl.LOCK_EX):
        yield


def _parse_checksum_manifest(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        parts = raw.split("  ", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid checksum row in {path}: {raw!r}")
        digest, name = parts
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not name
            or name.startswith("/")
            or ".." in PurePosixPath(name).parts
            or name in rows
        ):
            raise ValueError(f"invalid checksum identity in {path}: {raw!r}")
        rows[name] = digest
    return rows


def fsync_tree(directory: Path) -> None:
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for path in sorted(
        (value for value in directory.rglob("*") if value.is_dir()),
        key=lambda value: len(value.parts), reverse=True,
    ):
        _fsync_directory(path)
    _fsync_directory(directory)


def verify_checksum_manifest(directory: Path, name: str = "SHA256SUMS") -> dict[str, str]:
    manifest = directory / name
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError(f"missing safe checksum manifest: {manifest}")
    rows = _parse_checksum_manifest(manifest)
    for relative, expected in rows.items():
        path = directory / relative
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise ValueError(f"checksum mismatch: {path}")
    return rows


def _write_static_checksums(directory: Path) -> None:
    content = "".join(
        f"{sha256_file(directory / name)}  {name}\n" for name in sorted(STATIC_FILES)
    )
    (directory / ROOT_STATIC_MANIFEST).write_text(content, encoding="utf-8")


def verify_execution_static_checksums(directory: Path) -> None:
    rows = verify_checksum_manifest(directory, ROOT_STATIC_MANIFEST)
    if set(rows) != STATIC_FILES:
        raise ValueError("managed execution static checksum set mismatch")


def source_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode() + b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            digest.update(b"X\0" if path.stat().st_mode & 0o111 else b"N\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            raise ValueError(f"unsupported source entry: {path}")
    return digest.hexdigest()


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            continue
        if path.is_dir():
            path.chmod(0o555)
        else:
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
    root.chmod(0o555)


def _remove_tree(path: Path) -> None:
    for target in [path, *path.rglob("*")]:
        if not target.is_symlink():
            target.chmod(0o700 if target.is_dir() else 0o600)
    shutil.rmtree(path)


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _archive_git_source(repo_root: Path, commit: str, destination: Path) -> dict[str, Any]:
    destination.mkdir()
    process = subprocess.Popen(
        ["git", "archive", "--format=tar", commit], cwd=repo_root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
        for member in archive:
            pure = PurePosixPath(member.name)
            if not member.name or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"unsafe Git archive member: {member.name}")
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or ".." in link.parts:
                    raise ValueError(f"unsafe Git archive link: {member.name}")
            archive.extract(member, destination)
    process.stdout.close()
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.wait():
        raise ValueError(f"git archive failed: {stderr.strip()}")
    identity = {
        "git_commit": require_sha1({"value": commit}, "value"),
        "git_tree": _git(repo_root, "rev-parse", f"{commit}^{{tree}}"),
        "source_tree_sha256": source_tree_hash(destination),
    }
    _make_read_only(destination)
    return identity


def _copy_fixture_source(source: Path, destination: Path, commit: str) -> dict[str, Any]:
    shutil.copytree(source, destination, symlinks=True)
    identity = {
        "git_commit": commit,
        "git_tree": "0" * 40,
        "source_tree_sha256": source_tree_hash(destination),
    }
    _make_read_only(destination)
    return identity


def load_phase2a_lock(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    path = repo_root.expanduser().resolve() / PHASE2A_LOCK_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2A lock must be a regular file")
    value = load_json(path)
    if value.get("schema_version") != PHASE2A_LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported Phase-2A lock schema")
    if value.get("status") != "accepted-formal-phase-2a-check-only":
        raise ValueError("Phase-2A lock is not accepted")
    if value.get("child_id") != INITIAL_CHILD_ID or value.get("submittable") is not False:
        raise ValueError("Phase-2A lock authorization boundary is invalid")
    if value.get("campaign_json_created") is not False:
        raise ValueError("Phase-2A lock reports a forbidden campaign.json")
    return path, value


def canonical_execution_directory(managed_child: ManagedProductionChild) -> Path:
    if managed_child.directory.name != FORMAL_CHILD_NAME:
        raise ValueError("formal managed child has an unexpected directory name")
    return managed_child.directory.parent / FORMAL_EXECUTION_NAME


def _validate_phase2a_binding(
    managed: ManagedProductionChild, lock_path: Path, lock: dict[str, Any]
) -> None:
    if managed.directory != Path(require_string(lock, "canonical_directory")):
        raise ValueError("Phase-2A lock canonical directory mismatch")
    if managed.plan.campaign_id != lock.get("reserved_campaign_id"):
        raise ValueError("Phase-2A campaign ID mismatch")
    program = require_dict(lock, "program")
    binding_program = require_dict(managed.binding, "program")
    for key in ("program_id", "program_hash", "parent_plan_hash", "authorization_graph_hash"):
        if program.get(key) != binding_program.get(key):
            raise ValueError(f"Phase-2A program {key} mismatch")
    child = require_dict(lock, "child")
    binding_child = require_dict(managed.binding, "child")
    if child.get("child_plan_hash") != binding_child.get("child_plan_hash"):
        raise ValueError("Phase-2A child-plan hash mismatch")
    if child.get("binding_hash") != managed.binding.get("binding_hash"):
        raise ValueError("Phase-2A binding hash mismatch")
    artifacts = require_dict(lock, "artifacts")
    for key, filename in (
        ("managed_child_json", "managed_child.json"),
        ("program_binding_json", "program_binding.json"),
        ("root_checksum_manifest", "SHA256SUMS"),
    ):
        if require_dict(artifacts, key).get("sha256") != sha256_file(managed.directory / filename):
            raise ValueError(f"Phase-2A artifact mismatch: {filename}")


def _write_tasks(path: Path, tasks: Sequence[CampaignTask]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(CampaignTask.__dataclass_fields__), delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(asdict(task) for task in tasks)


def _read_scan_args(path: Path) -> tuple[str, ...]:
    return tuple(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _sealed_pilot_identity(pilot_dir: Path, *, allow_fixture: bool) -> dict[str, Any]:
    directory = pilot_dir.expanduser().resolve()
    finalized = directory / "finalized"
    analysis = finalized / "analysis-v2"
    required = (
        directory / "campaign.json",
        finalized / "SHA256SUMS",
        analysis / "SHA256SUMS",
        analysis / "analysis_config.json",
    )
    if not all(path.is_file() for path in required):
        raise ValueError(f"sealed pilot is incomplete: {directory}")
    verify_checksum_manifest(finalized)
    verify_checksum_manifest(analysis)
    campaign = load_json(directory / "campaign.json")
    config = load_json(analysis / "analysis_config.json")
    if not allow_fixture:
        if campaign.get("campaign_id") != "sm-v1-convergence-pilot-4e9ef8618948":
            raise ValueError("unexpected formal sealed-pilot campaign")
        if config.get("accepted_statistical_evidence") is not True:
            raise ValueError("formal sealed-pilot analysis-v2 is not accepted evidence")
    return {
        "directory": str(directory),
        "campaign_id": campaign.get("campaign_id"),
        "plan_hash": campaign.get("plan_hash"),
        "simulation_commit": require_dict(campaign, "git").get("commit"),
        "finalized_checksums_sha256": sha256_file(finalized / "SHA256SUMS"),
        "analysis_v2_checksums_sha256": sha256_file(analysis / "SHA256SUMS"),
        "analysis_v2_config_sha256": sha256_file(analysis / "analysis_config.json"),
        "analysis_v2_schema_version": config.get("schema_version"),
        "analysis_commit": config.get("analysis_commit"),
        "accepted_statistical_evidence": config.get("accepted_statistical_evidence") is True,
    }


def _semantic_hash(value: dict[str, Any], key: str) -> str:
    unhashed = dict(value)
    unhashed.pop(key, None)
    return sha256_bytes(canonical_json(unhashed))


def _execution_id(binding_hash: str, control_commit: str) -> str:
    suffix = sha256_bytes(canonical_json({"binding": binding_hash, "control": control_commit}))[:12]
    return f"sm-v1-production-bc-s1-execution-{suffix}"


def _write_execution_tree(
    directory: Path,
    *,
    managed: ManagedProductionChild,
    repo_root: Path,
    phase2a_path: Path,
    phase2a_lock: dict[str, Any],
    pilot_dir: Path,
    test_mode: bool,
    fixture_sources: tuple[Path, Path] | None,
    fault_after: str | None,
    control_lock_protocol: str,
) -> None:
    directory.mkdir()
    for name in ("intents", "attempts", "finalized"):
        (directory / name).mkdir()
    lock_path = directory / ".control.lock"
    lock_token: str | None = None
    if control_lock_protocol == CONTROL_LOCK_PROTOCOL_V1:
        lock_path.touch(mode=0o600, exist_ok=False)
    elif control_lock_protocol == CONTROL_LOCK_PROTOCOL_V2:
        lock_token = secrets.token_hex(32)
        write_exclusive_bytes(
            lock_path, _portable_lock_content(lock_token), mode=0o600
        )
    else:
        raise ValueError("unsupported managed execution control-lock protocol")
    tasks_path = directory / "tasks.tsv"
    args_path = directory / "scan_args.txt"
    _write_tasks(tasks_path, managed.plan.tasks)
    args_path.write_text("\n".join(managed.plan.scan_args) + "\n", encoding="utf-8")
    if fault_after == "tasks":
        raise RuntimeError("injected Phase-2B materialization failure after tasks")

    sources_root = directory / "sources"
    sources_root.mkdir()
    simulation_dir = sources_root / "simulation"
    control_dir = sources_root / "control"
    simulation_commit = require_sha1(require_dict(phase2a_lock, "execution_source"), "commit")
    if fixture_sources is None:
        control_commit = _git(repo_root, "rev-parse", "HEAD")
        if not test_mode and _git(repo_root, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("formal execution materialization requires a clean checkout")
        simulation_identity = _archive_git_source(repo_root, simulation_commit, simulation_dir)
        control_identity = _archive_git_source(repo_root, control_commit, control_dir)
    else:
        simulation_source, control_source = fixture_sources
        control_commit = "f" * 40
        simulation_identity = _copy_fixture_source(simulation_source, simulation_dir, simulation_commit)
        control_identity = _copy_fixture_source(control_source, control_dir, control_commit)
    for identity, expected in (
        (simulation_identity, simulation_commit), (control_identity, control_commit)
    ):
        if identity["git_commit"] != expected:
            raise ValueError("frozen source commit mismatch")
    source_manifest = {
        "schema_version": "steel-module-production-dual-source-v1",
        "simulation": {**simulation_identity, "path": "sources/simulation"},
        "control_plane": {**control_identity, "path": "sources/control"},
    }
    (directory / "source_archives.json").write_text(
        json.dumps(source_manifest, indent=2) + "\n", encoding="utf-8"
    )
    if fault_after == "sources":
        raise RuntimeError("injected Phase-2B materialization failure after sources")

    pilot = _sealed_pilot_identity(pilot_dir, allow_fixture=test_mode)
    program_pilot = require_dict(
        load_json(Path(managed.binding["program"]["program_directory"]) / "production_program.json"),
        "pilot_exclusion",
    )
    lock_stat = lock_path.lstat()
    if control_lock_protocol == CONTROL_LOCK_PROTOCOL_V1:
        control_lock_record: dict[str, Any] = {
            "path": ".control.lock",
            "device": lock_stat.st_dev,
            "inode": lock_stat.st_ino,
            "mode": lock_stat.st_mode & 0o777,
            "size_bytes": lock_stat.st_size,
            "sha256": sha256_file(lock_path),
        }
        execution_schema_version = EXECUTION_SCHEMA_VERSION_V1
    else:
        if lock_token is None:
            raise AssertionError("portable control-lock token was not created")
        control_lock_record = {
            "path": ".control.lock",
            "protocol": CONTROL_LOCK_PROTOCOL_V2,
            "content_token": lock_token,
            "mode": lock_stat.st_mode & 0o777,
            "size_bytes": lock_stat.st_size,
            "sha256": sha256_file(lock_path),
            "link_count": lock_stat.st_nlink,
            # Creation-node stat values are provenance diagnostics only.
            "creation_device": lock_stat.st_dev,
            "creation_inode": lock_stat.st_ino,
        }
        _validate_portable_lock_record(control_lock_record)
        execution_schema_version = EXECUTION_SCHEMA_VERSION_V2

    payload: dict[str, Any] = {
        "schema_version": execution_schema_version,
        "object_kind": "managed-production-execution-companion",
        "created_at_utc": utc_now(),
        "test_mode": test_mode,
        "accepted_statistical_evidence": managed.binding.get("accepted_statistical_evidence") is True and not test_mode,
        "execution_id": _execution_id(managed.binding["binding_hash"], control_commit),
        "execution_hash": None,
        "phase": "Phase-2B",
        "managed_child": {
            "directory": str(managed.directory),
            "campaign_id": managed.plan.campaign_id,
            "plan_hash": managed.plan.plan_hash,
            "binding_hash": managed.binding["binding_hash"],
            "child_id": INITIAL_CHILD_ID,
            "child_plan_hash": managed.binding["child"]["child_plan_hash"],
            "task_set_hash": managed.binding["child"]["task_set_hash"],
            "parent_task_plan_hash": managed.binding["child"]["parent_task_plan_hash"],
            "task_seed_mapping_hash": managed.binding["child"]["task_seed_mapping_hash"],
            "seed_set_hash": managed.binding["child"]["seed_set_hash"],
            "tasks_sha256": sha256_file(managed.directory / "tasks.tsv"),
            "scan_args_sha256": sha256_file(managed.directory / "scan_args.txt"),
        },
        "program": {
            key: managed.binding["program"][key]
            for key in ("program_id", "program_hash", "parent_plan_hash", "authorization_graph_hash")
        },
        "phase2a_lock": {
            "path": str(phase2a_path),
            "sha256": sha256_file(phase2a_path),
            "schema_version": PHASE2A_LOCK_SCHEMA_VERSION,
        },
        "readiness_gate": {
            "tracked_path": READINESS_LOCK_RELATIVE.as_posix(),
            "schema_version": READINESS_LOCK_SCHEMA_VERSION,
            "required_for_scheduler_contact": True,
            "automatic_submission": False,
        },
        "shape": {
            "task_count": 32, "event_count": 8000, "events_per_task": 250,
            "seed_count": 64, "tile_thickness_mm": [4, 24],
            "sipm_layout": "back-center", "absorber_transverse_mm": 500,
            "seed_block_range_inclusive": [0, 15],
        },
        "runtime": {
            "environment_identity": managed.plan.environment["identity_hash"],
            "image_sha256": managed.plan.environment["image"]["sha256"],
            "g4_data_manifest_sha256": managed.plan.environment["g4_data_manifest"]["sha256"],
            "executable_sha256": managed.plan.environment["build_artifact"]["sha256"],
        },
        "sources": source_manifest,
        "sealed_pilot": {
            **pilot,
            "pilot_exclusion_hash": program_pilot.get("pilot_exclusion_hash"),
            "seed_set_hash": program_pilot.get("seed_set_hash"),
            "seed_count": program_pilot.get("seed_count"),
        },
        "artifacts": {
            "tasks": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
            "scan_args": {"path": "scan_args.txt", "sha256": sha256_file(args_path)},
            "source_archives": {"path": "source_archives.json", "sha256": sha256_file(directory / "source_archives.json")},
            "control_lock": control_lock_record,
        },
        "submission_policy": {
            "account": FORMAL_ACCOUNT, "two_step_required": True,
            "initially_held": True, "no_requeue": True, "export_mode": "NONE",
            "formal_child": INITIAL_CHILD_ID, "formal_initial_mode": "initial",
        },
    }
    payload["execution_hash"] = _semantic_hash(payload, "execution_hash")
    (directory / "managed_execution.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "README.md").write_text(
        f"# {payload['execution_id']}\n\nManaged Phase-2B execution companion for BC-S1.\n"
        "It is not a campaign, contains no campaign.json, and cannot contact Slurm "
        "without the tracked readiness lock and an explicit submit-intent command.\n",
        encoding="utf-8",
    )
    _write_static_checksums(directory)
    if fault_after == "checksums":
        raise RuntimeError("injected Phase-2B materialization failure after checksums")


def materialize_execution_companion(
    *,
    repo_root: Path,
    managed_child_dir: Path | None = None,
    out_dir: Path | None = None,
    pilot_dir: Path | None = None,
    test_mode: bool = False,
    fixture_sources: tuple[Path, Path] | None = None,
    fault_after: str | None = None,
    control_lock_protocol: str = CONTROL_LOCK_PROTOCOL_V1,
) -> ManagedExecution:
    """Atomically create one execution companion; formal paths are not overridable."""

    root = repo_root.expanduser().resolve()
    phase2a_path, phase2a = load_phase2a_lock(root)
    formal_child = Path(require_string(phase2a, "canonical_directory"))
    child_path = (managed_child_dir or formal_child).expanduser().resolve()
    if not test_mode and child_path != formal_child:
        raise ValueError("formal managed-child path is fixed by Phase-2A")
    managed = load_managed_production_child(
        child_path, repo_root=root if not test_mode else None,
        verify_runtime_artifacts=not test_mode, verify_control_plane=not test_mode,
        allow_test_mode=test_mode,
    )
    if not test_mode:
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
    target = (out_dir or canonical_execution_directory(managed)).expanduser().resolve()
    expected = formal_child.parent / FORMAL_EXECUTION_NAME
    if not test_mode and target != expected:
        raise ValueError(f"formal execution path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite execution companion: {target}")
    if not target.parent.is_dir():
        raise ValueError(f"execution parent does not exist: {target.parent}")
    pilot = (pilot_dir or (formal_child.parent / FORMAL_PILOT_NAME)).expanduser().resolve()
    if not test_mode and pilot != formal_child.parent / FORMAL_PILOT_NAME:
        raise ValueError("formal sealed-pilot path is fixed")
    lock_dir = target.parent / f".{target.name}.materialize.lock"
    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        raise ValueError("execution materialization is already active") from exc
    temporary: Path | None = None
    try:
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
        temporary.rmdir()
        _write_execution_tree(
            temporary, managed=managed, repo_root=root, phase2a_path=phase2a_path,
            phase2a_lock=phase2a, pilot_dir=pilot, test_mode=test_mode,
            fixture_sources=fixture_sources, fault_after=fault_after,
            control_lock_protocol=control_lock_protocol,
        )
        loaded = load_execution_companion(
            temporary, repo_root=root if not test_mode else None,
            allow_test_mode=test_mode, require_readiness=False,
            verify_runtime=not test_mode, allow_staging=True,
        )
        fsync_tree(temporary)
        os.rename(temporary, target)
        temporary = None
        _fsync_directory(target.parent)
        return ManagedExecution(target, loaded.managed_child, loaded.manifest, loaded.tasks, loaded.scan_args)
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)
        lock_dir.rmdir()


def _verify_source_record(directory: Path, record: dict[str, Any]) -> None:
    relative = require_string(record, "path")
    if relative.startswith("/") or ".." in PurePosixPath(relative).parts:
        raise ValueError("unsafe frozen-source path")
    source = (directory / relative).resolve()
    if directory not in source.parents or not source.is_dir() or source.is_symlink():
        raise ValueError("frozen source escapes execution companion")
    if source_tree_hash(source) != require_sha256(record, "source_tree_sha256"):
        raise ValueError("frozen source tree hash mismatch")
    for path in [source, *source.rglob("*")]:
        if path.is_symlink():
            resolved = path.resolve()
            if resolved != source and source not in resolved.parents:
                raise ValueError("frozen source contains an escaping symlink")
        elif path.stat().st_mode & 0o222:
            raise ValueError("frozen source tree is writable")


def _verify_readiness_lock(execution: ManagedExecution, repo_root: Path) -> dict[str, Any]:
    root = repo_root.expanduser().resolve()
    path = root / READINESS_LOCK_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2B readiness lock must be a regular file")
    lock = load_json(path)
    if lock.get("schema_version") != READINESS_LOCK_SCHEMA_VERSION:
        raise ValueError("unsupported Phase-2B readiness-lock schema")
    if lock.get("status") != "accepted-formal-phase-2b-ready":
        raise ValueError("Phase-2B readiness lock is not accepted")
    if lock.get("managed_submission_ready") is not True:
        raise ValueError("managed submission is not ready")
    if lock.get("automatic_submission") is not False:
        raise ValueError("readiness lock permits forbidden automatic submission")
    if lock.get("real_slurm_submission_performed") is not False:
        raise ValueError("readiness lock unexpectedly records a real submission")
    if lock.get("formal_intent_count") != 0 or lock.get("slurm_job_ids") != []:
        raise ValueError("readiness lock was not frozen before formal intents")
    if require_dict(lock, "phase2a_lock") != require_dict(
        execution.manifest, "phase2a_lock"
    ):
        raise ValueError("readiness lock Phase-2A identity mismatch")
    expected = {
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
        "managed_execution_json_sha256": sha256_file(execution.directory / "managed_execution.json"),
        "static_checksum_manifest_sha256": sha256_file(execution.directory / ROOT_STATIC_MANIFEST),
    }
    bound = require_dict(lock, "execution_companion")
    for key, value in expected.items():
        if bound.get(key) != value:
            raise ValueError(f"readiness lock execution {key} mismatch")
    if bound.get("control_lock") != require_dict(
        require_dict(execution.manifest, "artifacts"), "control_lock"
    ):
        raise ValueError("readiness lock control-lock inode identity mismatch")
    control = require_dict(require_dict(execution.manifest, "sources"), "control_plane")
    locked_control = require_dict(lock, "control_plane")
    for key in ("git_commit", "git_tree", "source_tree_sha256"):
        if locked_control.get(key) != control.get(key):
            raise ValueError(f"readiness lock control-plane {key} mismatch")
    verify_readiness_protected_artifacts(execution, root, locked_control)
    git_status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if git_status.returncode or git_status.stdout:
        raise ValueError("formal readiness requires a clean control-plane checkout")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", control["git_commit"], "HEAD"],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if ancestor.returncode:
        raise ValueError("current checkout is not a descendant of implementation commit C")
    release_delta = subprocess.run(
        [
            "git", "diff", "--name-status", "--no-renames",
            f"{control['git_commit']}..HEAD",
        ],
        cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    expected_delta = f"A\t{READINESS_LOCK_RELATIVE.as_posix()}"
    if release_delta.returncode or release_delta.stdout.strip() != expected_delta:
        raise ValueError(
            "readiness commit R must differ from implementation C only by the "
            "new readiness lock"
        )
    checker = require_dict(lock, "checker_evidence")
    top_level = require_dict(checker, "top_level")
    shared = require_dict(checker, "shared_filesystem")
    environment = require_dict(checker, "analysis_environment")
    frozen_worker = require_dict(checker, "frozen_worker_source")
    if (
        top_level.get("exit_code") != 0
        or top_level.get("phase2b_markers_verified") is not True
        or top_level.get("real_scheduler_contact_performed") is not False
        or top_level.get("scheduler_commands_blocked") is not True
        or top_level.get("blocked_commands")
        != ["sbatch", "scontrol", "squeue", "sacct"]
        or shared.get("cross_process_flock") is not True
        or shared.get("atomic_directory_publish") is not True
        or shared.get("existing_target_rejected") is not True
        or shared.get("exclusive_create_no_replace") is not True
        or shared.get("temporary_probe_removed") is not True
        or frozen_worker.get("exit_code") != 0
        or frozen_worker.get("control_source_has_git_metadata") is not False
        or frozen_worker.get("phase2a_lock_relocated") is not True
        or frozen_worker.get("runtime_artifacts_verified") is not True
        or frozen_worker.get("geant4_run_performed") is not False
        or frozen_worker.get("real_scheduler_contact_performed") is not False
        or checker.get("synthetic_downstream_dry_run") is not True
        or checker.get("formal_execution_intent_count_after_checks") != 0
        or set(environment) != {"numpy", "uproot", "matplotlib"}
    ):
        raise ValueError("readiness checker evidence is incomplete")
    capabilities = require_dict(lock, "osc_capabilities")
    if capabilities.get("real_scheduler_contact_performed") is not False:
        raise ValueError("readiness evidence reports forbidden scheduler contact")
    commands = require_dict(capabilities, "commands")
    if set(commands) != {"apptainer", "sbatch", "scontrol", "squeue", "sacct"}:
        raise ValueError("readiness scheduler capability set is incomplete")
    for name, recorded_path in commands.items():
        if not isinstance(recorded_path, str) or shutil.which(name) != recorded_path:
            raise ValueError(f"readiness command path changed: {name}")
    return lock


def verify_readiness_protected_artifacts(
    execution: ManagedExecution, root: Path, locked_control: dict[str, Any]
) -> None:
    """Compare the fixed protected set across lock, R checkout, and C archive."""

    artifacts = require_dict(locked_control, "artifacts")
    if set(artifacts) != set(READINESS_CRITICAL_ARTIFACTS):
        raise ValueError("readiness protected-artifact set is incomplete")
    control = require_dict(require_dict(execution.manifest, "sources"), "control_plane")
    frozen_control_root = (
        execution.directory / require_string(control, "path")
    ).resolve()
    for relative in READINESS_CRITICAL_ARTIFACTS:
        raw_record = artifacts[relative]
        if (
            relative.startswith("/")
            or ".." in PurePosixPath(relative).parts
            or not isinstance(raw_record, dict)
        ):
            raise ValueError("readiness lock contains an unsafe protected artifact")
        artifact = root / relative
        frozen_artifact = frozen_control_root / relative
        if artifact.is_symlink() or not artifact.is_file():
            raise ValueError(f"readiness protected artifact is missing: {relative}")
        if frozen_artifact.is_symlink() or not frozen_artifact.is_file():
            raise ValueError(
                f"frozen implementation artifact is missing: {relative}"
            )
        expected_sha = require_sha256(raw_record, "sha256")
        if sha256_file(artifact) != expected_sha:
            raise ValueError(f"readiness protected artifact changed: {relative}")
        if sha256_file(frozen_artifact) != expected_sha:
            raise ValueError(
                f"readiness artifact differs from implementation commit C: {relative}"
            )


def require_pristine_readiness_workspace(execution: ManagedExecution) -> None:
    """Require an execution companion with no mutable evidence of any kind."""

    for name in ("intents", "attempts", "finalized"):
        directory = execution.directory / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"readiness mutable root is unsafe: {name}")
        entries = list(directory.iterdir())
        if entries:
            raise ValueError(
                f"readiness requires an empty {name}/ directory; found "
                + ", ".join(sorted(path.name for path in entries))
            )


def load_execution_companion(
    execution_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = True,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
    allow_staging: bool = False,
    _allow_historical_closed_recovery: bool = False,
) -> ManagedExecution:
    if _allow_historical_closed_recovery and (
        repo_root is None
        or allow_test_mode
        or require_readiness
        or not verify_runtime
        or not verify_phase2a_control_plane
        or allow_staging
    ):
        raise ValueError(
            "historical closed recovery requires the full formal validation mode"
        )
    requested = execution_dir.expanduser()
    if requested.is_symlink():
        raise ValueError("execution companion must not be a symlink")
    directory = requested.resolve()
    verify_execution_static_checksums(directory)
    if (directory / "campaign.json").exists():
        raise ValueError("managed execution must never contain campaign.json")
    expected_root = {
        ".control.lock", "README.md", ROOT_STATIC_MANIFEST, "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv", "sources",
        "intents", "attempts", "finalized",
    }
    actual_root = {path.name for path in directory.iterdir()}
    if actual_root != expected_root:
        raise ValueError(
            "managed execution root entry set mismatch: "
            f"unexpected={sorted(actual_root - expected_root)} "
            f"missing={sorted(expected_root - actual_root)}"
        )
    if any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError("managed execution root must not contain symlinks")
    manifest = load_json(directory / "managed_execution.json")
    if manifest.get("schema_version") not in {
        EXECUTION_SCHEMA_VERSION_V1, EXECUTION_SCHEMA_VERSION_V2,
    }:
        raise ValueError("unsupported managed-execution schema")
    if manifest.get("execution_hash") != _semantic_hash(manifest, "execution_hash"):
        raise ValueError("managed-execution semantic hash mismatch")
    test_mode = manifest.get("test_mode")
    accepted = manifest.get("accepted_statistical_evidence")
    if not isinstance(test_mode, bool) or not isinstance(accepted, bool) or accepted == test_mode:
        raise ValueError("managed-execution test/evidence flags are invalid")
    if test_mode and not allow_test_mode:
        raise ValueError("formal loader refuses test-mode execution evidence")
    child_record = require_dict(manifest, "managed_child")
    child_dir = Path(require_string(child_record, "directory"))
    sources = require_dict(manifest, "sources")
    frozen_control_mode = not test_mode and not verify_phase2a_control_plane
    if frozen_control_mode:
        if repo_root is None:
            raise ValueError("frozen control-source validation requires repo_root")
        control_record = require_dict(sources, "control_plane")
        expected_control_root = (
            directory / require_string(control_record, "path")
        ).resolve()
        if repo_root.expanduser().resolve() != expected_control_root:
            raise ValueError(
                "relocated formal lock is permitted only inside the frozen "
                "control-plane source"
            )
        _verify_source_record(directory, control_record)
    managed = load_managed_production_child(
        child_dir, repo_root=repo_root if not test_mode else None,
        verify_runtime_artifacts=verify_runtime and not test_mode,
        verify_control_plane=not test_mode and verify_phase2a_control_plane,
        allow_test_mode=test_mode,
        _allow_formal_lock_relocation=frozen_control_mode,
    )
    if managed.plan.campaign_id != child_record.get("campaign_id") or managed.plan.plan_hash != child_record.get("plan_hash"):
        raise ValueError("managed child identity mismatch")
    if managed.binding.get("binding_hash") != child_record.get("binding_hash"):
        raise ValueError("managed child binding mismatch")
    binding_child = require_dict(managed.binding, "child")
    for key in (
        "child_plan_hash", "task_set_hash", "parent_task_plan_hash",
        "task_seed_mapping_hash", "seed_set_hash",
    ):
        if child_record.get(key) != binding_child.get(key):
            raise ValueError(f"managed child {key} mismatch")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    scan_args = _read_scan_args(directory / "scan_args.txt")
    if tasks != managed.plan.tasks or scan_args != managed.plan.scan_args:
        raise ValueError("execution task/scan plan differs from Phase-2A")
    artifacts = require_dict(manifest, "artifacts")
    for key, filename in (
        ("tasks", "tasks.tsv"), ("scan_args", "scan_args.txt"),
        ("source_archives", "source_archives.json"),
    ):
        record = require_dict(artifacts, key)
        if record.get("path") != filename or record.get("sha256") != sha256_file(directory / filename):
            raise ValueError(f"managed execution artifact binding mismatch: {filename}")
    shape = require_dict(manifest, "shape")
    seeds = [seed for task in tasks for seed in (task.seed1, task.seed2)]
    if (
        len(tasks) != 32 or sum(task.events for task in tasks) != 8000
        or len(seeds) != 64 or len(set(seeds)) != 64
        or shape.get("task_count") != 32 or shape.get("event_count") != 8000
    ):
        raise ValueError("execution shape is not exact BC-S1")
    if load_json(directory / "source_archives.json") != sources:
        raise ValueError("source archive manifest mismatch")
    _verify_source_record(directory, require_dict(sources, "simulation"))
    _verify_source_record(directory, require_dict(sources, "control_plane"))
    if _allow_historical_closed_recovery:
        validate_historical_recovery_control_lock(directory, manifest)
    else:
        validate_execution_control_lock(directory, manifest)
    recorded_pilot = require_dict(manifest, "sealed_pilot")
    current_pilot = _sealed_pilot_identity(
        Path(require_string(recorded_pilot, "directory")), allow_fixture=test_mode
    )
    for key, expected_value in current_pilot.items():
        if recorded_pilot.get(key) != expected_value:
            raise ValueError(f"sealed-pilot {key} identity mismatch")
    program_manifest = load_json(
        Path(managed.binding["program"]["program_directory"]) / "production_program.json"
    )
    pilot_exclusion = require_dict(program_manifest, "pilot_exclusion")
    for key in ("pilot_exclusion_hash", "seed_set_hash", "seed_count"):
        if recorded_pilot.get(key) != pilot_exclusion.get(key):
            raise ValueError(f"sealed-pilot exclusion {key} mismatch")
    execution = ManagedExecution(directory, managed, manifest, tasks, scan_args)
    if not test_mode:
        if repo_root is None:
            raise ValueError("formal execution validation requires repo_root")
        phase2a_path, phase2a = load_phase2a_lock(repo_root)
        _validate_phase2a_binding(managed, phase2a_path, phase2a)
        recorded_phase2a = require_dict(manifest, "phase2a_lock")
        if (
            recorded_phase2a.get("schema_version") != PHASE2A_LOCK_SCHEMA_VERSION
            or recorded_phase2a.get("sha256") != sha256_file(phase2a_path)
            or (
                not frozen_control_mode
                and recorded_phase2a.get("path") != str(phase2a_path)
            )
        ):
            raise ValueError("managed execution Phase-2A lock identity mismatch")
        expected = canonical_execution_directory(managed)
        staging_prefix = f".{expected.name}.tmp-"
        if directory != expected and not (
            allow_staging and directory.parent == expected.parent and directory.name.startswith(staging_prefix)
        ):
            raise ValueError(f"formal execution path must be canonical: {expected}")
        if require_readiness:
            _verify_readiness_lock(execution, repo_root)
    elif require_readiness:
        raise ValueError("test-mode execution can never satisfy formal readiness")
    return execution


def load_historical_closed_execution_for_recovery(
    execution_dir: Path, *, repo_root: Path
) -> ManagedExecution:
    """Load only the exact closed v1 companion for read/seal recovery.

    This is deliberately separate from the ordinary loader interface.  It
    preserves every formal identity and runtime check, relaxing only the
    historical cross-node device/inode comparison for ``.control.lock``.
    """

    return load_execution_companion(
        execution_dir,
        repo_root=repo_root,
        allow_test_mode=False,
        require_readiness=False,
        verify_runtime=True,
        verify_phase2a_control_plane=True,
        allow_staging=False,
        _allow_historical_closed_recovery=True,
    )


def execution_task_by_id(execution: ManagedExecution) -> dict[str, CampaignTask]:
    return {task.logical_task_id: task for task in execution.tasks}


def _intent_dir(execution: ManagedExecution, attempt_id: str) -> Path:
    return execution.directory / "intents" / _safe_id(attempt_id, "attempt ID")


def _attempt_dir(execution: ManagedExecution, attempt_id: str) -> Path:
    return execution.directory / "attempts" / _safe_id(attempt_id, "attempt ID")


def _attempt_job_name(attempt_id: str) -> str:
    token = hashlib.sha256(attempt_id.encode()).hexdigest()[:10]
    return f"g4sm-{attempt_id[:28]}-{token}"


def _job_wrapper_text(execution: ManagedExecution, attempt_id: str) -> str:
    expected_root = shlex.quote(str(execution.directory))
    expected_attempt = shlex.quote(attempt_id)
    return (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "if [[ \"$#\" -ne 3 ]]; then echo 'Expected execution root, attempt ID, "
        "and intent SHA-256.' >&2; exit 2; fi\n"
        "execution_root=\"$1\"\nattempt_id=\"$2\"\nintent_sha256=\"$3\"\n"
        f"expected_execution_root={expected_root}\n"
        f"expected_attempt_id={expected_attempt}\n"
        "if [[ \"${execution_root}\" != \"${expected_execution_root}\" || "
        "\"${attempt_id}\" != \"${expected_attempt_id}\" || "
        "! \"${intent_sha256}\" =~ ^[0-9a-f]{64}$ ]]; then\n"
        "  echo 'Managed wrapper identity mismatch.' >&2; exit 2\nfi\n"
        "exec bash \"${execution_root}/sources/control/hpc/osc/"
        "submit_steel_module_production_phase2b.sbatch\" "
        "\"${execution_root}\" \"${attempt_id}\" \"${intent_sha256}\"\n"
    )


def load_attempt_intent(execution: ManagedExecution, attempt_id: str) -> dict[str, Any]:
    directory = _intent_dir(execution, attempt_id)
    rows = verify_checksum_manifest(directory)
    if set(rows) != {"intent.json", "job-wrapper.sh", "scan_args.txt", "tasks.tsv"}:
        raise ValueError("attempt intent checksum set mismatch")
    intent = load_json(directory / "intent.json")
    expected_keys = {
        "schema_version", "attempt_id", "created_at_utc", "created_by", "mode",
        "execution_id", "execution_hash", "campaign_id", "plan_hash",
        "program_id", "program_hash", "child_id", "child_plan_hash",
        "binding_hash", "phase2a_lock_sha256", "readiness_lock_sha256",
        "simulation_source", "control_plane_source", "runtime", "selected",
        "artifacts", "scheduler", "intent_sha256",
    }
    if set(intent) != expected_keys:
        raise ValueError("attempt intent field set mismatch")
    if intent.get("schema_version") != INTENT_SCHEMA_VERSION or intent.get("attempt_id") != attempt_id:
        raise ValueError("attempt intent identity mismatch")
    _safe_actor(require_string(intent, "created_by"))
    try:
        created_at = datetime.fromisoformat(require_string(intent, "created_at_utc"))
    except ValueError as exc:
        raise ValueError("attempt intent creation time is invalid") from exc
    allowed_modes = {"initial", "resume", "retry-failed"}
    if execution.manifest.get("object_kind") == SUCCESSOR_OBJECT_KIND:
        allowed_modes = {"predecessor-retry", "resume", "retry-failed"}
    if created_at.tzinfo is None or intent.get("mode") not in allowed_modes:
        raise ValueError("attempt intent creation/mode metadata is invalid")
    expected_identity = {
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
        "campaign_id": execution.campaign_id,
        "plan_hash": execution.plan_hash,
        "program_id": execution.manifest["program"]["program_id"],
        "program_hash": execution.manifest["program"]["program_hash"],
        "child_id": INITIAL_CHILD_ID,
        "child_plan_hash": execution.manifest["managed_child"]["child_plan_hash"],
        "binding_hash": execution.manifest["managed_child"]["binding_hash"],
        "phase2a_lock_sha256": execution.manifest["phase2a_lock"]["sha256"],
    }
    for key, value in expected_identity.items():
        if intent.get(key) != value:
            raise ValueError(f"attempt intent {key} mismatch")
    require_sha256(intent, "readiness_lock_sha256")
    sources = require_dict(execution.manifest, "sources")
    if intent.get("simulation_source") != require_dict(sources, "simulation"):
        raise ValueError("attempt intent simulation-source mismatch")
    if intent.get("control_plane_source") != require_dict(sources, "control_plane"):
        raise ValueError("attempt intent control-plane-source mismatch")
    if intent.get("runtime") != execution.manifest["runtime"]:
        raise ValueError("attempt intent runtime mismatch")
    if intent.get("intent_sha256") != _semantic_hash(intent, "intent_sha256"):
        raise ValueError("attempt intent semantic hash mismatch")
    tasks = parse_campaign_tasks(directory / "tasks.tsv")
    known = execution_task_by_id(execution)
    if any(known.get(task.logical_task_id) != task for task in tasks):
        raise ValueError("attempt contains a task outside the frozen child")
    selected_ids = [task.logical_task_id for task in tasks]
    intent_ids = _intent_directory_ids(execution)
    if attempt_id not in intent_ids:
        raise ValueError("attempt intent directory is not registered")
    prior_ids = tuple(value for value in intent_ids if value < attempt_id)
    expected_selected_ids = _selected_task_ids_from_prior(
        execution, require_string(intent, "mode"), prior_ids
    )
    if selected_ids != list(expected_selected_ids):
        raise ValueError("attempt task selection does not match its prior-attempt state")
    selected = require_dict(intent, "selected")
    expected_selected = {
        "task_count": len(tasks),
        "event_count": sum(task.events for task in tasks),
        "task_set_hash": _task_set_hash(tasks),
        "logical_task_ids": selected_ids,
    }
    if selected != expected_selected or not tasks:
        raise ValueError("attempt selected-task contract mismatch")
    scan_by_id = dict(
        zip(
            (task.logical_task_id for task in execution.tasks),
            execution.scan_args,
        )
    )
    expected_scan_lines = [scan_by_id[value] for value in selected_ids]
    if (directory / "scan_args.txt").read_text(encoding="utf-8").splitlines() != expected_scan_lines:
        raise ValueError("attempt scan arguments differ from the frozen execution")
    expected_wrapper = _job_wrapper_text(execution, attempt_id)
    if (directory / "job-wrapper.sh").read_text(encoding="utf-8") != expected_wrapper:
        raise ValueError("attempt job wrapper content mismatch")
    expected_artifacts = {
        "tasks": {"path": "tasks.tsv", "sha256": sha256_file(directory / "tasks.tsv")},
        "scan_args": {
            "path": "scan_args.txt", "sha256": sha256_file(directory / "scan_args.txt")
        },
        "job_wrapper": {
            "path": "job-wrapper.sh", "sha256": sha256_file(directory / "job-wrapper.sh")
        },
    }
    if require_dict(intent, "artifacts") != expected_artifacts:
        raise ValueError("attempt intent artifact contract mismatch")
    batch = execution.directory / BATCH_SCRIPT_RELATIVE
    if batch.is_symlink() or not batch.is_file():
        raise ValueError("attempt frozen batch script is missing or unsafe")
    wrapper_record = expected_artifacts["job_wrapper"]
    expected_scheduler = {
        "account": FORMAL_ACCOUNT,
        "job_name": _attempt_job_name(attempt_id),
        "array_spec": f"1-{len(tasks)}",
        "output_pattern": str(
            execution.directory / "attempts" / attempt_id / "slurm-%A_%a.out"
        ),
        "batch_script": {
            "path": BATCH_SCRIPT_RELATIVE, "sha256": sha256_file(batch)
        },
        "job_wrapper": wrapper_record,
        "no_requeue": True,
        "export_mode": "NONE",
        "initially_held": True,
        "resources": {
            "time_limit": "01:00:00", "nodes": 1, "ntasks": 1,
            "cpus_per_task": 1, "memory": "2G",
        },
    }
    if require_dict(intent, "scheduler") != expected_scheduler:
        raise ValueError("attempt scheduler contract mismatch")
    return intent


def read_attempt_events(execution: ManagedExecution, attempt_id: str) -> tuple[dict[str, Any], ...]:
    events_dir = _attempt_dir(execution, attempt_id) / "events"
    if not events_dir.exists():
        return ()
    if not events_dir.is_dir() or events_dir.is_symlink():
        raise ValueError("attempt events path is unsafe")
    output: list[dict[str, Any]] = []
    previous: str | None = None
    for expected_sequence, path in enumerate(sorted(events_dir.iterdir()), start=1):
        if not path.is_file() or path.is_symlink():
            raise ValueError("attempt event entry is unsafe")
        match = EVENT_NAME_RE.fullmatch(path.name)
        if not match or int(match.group("sequence")) != expected_sequence:
            raise ValueError("attempt event sequence is not contiguous")
        value = load_json(path)
        if value.get("schema_version") != EVENT_SCHEMA_VERSION or value.get("sequence") != expected_sequence:
            raise ValueError("attempt event schema/sequence mismatch")
        if value.get("attempt_id") != attempt_id or value.get("previous_event_sha256") != previous:
            raise ValueError("attempt event chain mismatch")
        recorded_hash = require_sha256(value, "event_sha256")
        if recorded_hash != _semantic_hash(value, "event_sha256"):
            raise ValueError("attempt event semantic hash mismatch")
        if match.group("digest") != recorded_hash[:12] or match.group("kind") != value.get("event_type"):
            raise ValueError("attempt event filename mismatch")
        intent = load_attempt_intent(execution, attempt_id)
        if value.get("intent_sha256") != intent.get("intent_sha256"):
            raise ValueError("attempt event intent mismatch")
        output.append(value)
        previous = recorded_hash
    return tuple(output)


def append_attempt_event(
    execution: ManagedExecution,
    attempt_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    actor: str,
    lock_held: bool = False,
    historical_incident_recovery: bool = False,
) -> dict[str, Any]:
    if historical_incident_recovery:
        if (
            execution.execution_id != HISTORICAL_CLOSED_EXECUTION_ID
            or execution.execution_hash != HISTORICAL_CLOSED_EXECUTION_HASH
            or attempt_id != HISTORICAL_CLOSED_ATTEMPT_ID
            or event_type != "terminal-accounting-frozen"
            or payload.get("job_id") != HISTORICAL_CLOSED_JOB_ID
            or set(payload)
            not in (
                {
                    "job_id",
                    "accounting_manifest_sha256",
                    "all_tasks_completed",
                },
                {
                    "job_id",
                    "accounting_manifest_sha256",
                    "all_tasks_completed",
                    "recovered_after_publish",
                },
            )
        ):
            raise ValueError("historical incident event authority mismatch")
        accounting = (
            execution.directory
            / "attempts"
            / attempt_id
            / "accounting"
        )
        rows = verify_checksum_manifest(accounting)
        frozen_accounting = load_json(accounting / "frozen.json")
        if (
            set(rows) != {"frozen.json", "sacct.psv", "squeue.psv"}
            or payload.get("accounting_manifest_sha256")
            != sha256_file(accounting / "SHA256SUMS")
            or frozen_accounting.get("job_id") != HISTORICAL_CLOSED_JOB_ID
            or payload.get("all_tasks_completed")
            is not frozen_accounting.get("all_tasks_completed")
            or (
                "recovered_after_publish" in payload
                and payload.get("recovered_after_publish") is not True
            )
        ):
            raise ValueError("historical incident accounting/event binding mismatch")
    else:
        require_production_execution_open(execution)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", event_type):
        raise ValueError("unsafe attempt event type")
    _safe_actor(actor)
    def append_locked() -> dict[str, Any]:
        intent = load_attempt_intent(execution, attempt_id)
        attempt_dir = _attempt_dir(execution, attempt_id)
        attempt_dir.mkdir(exist_ok=True)
        if attempt_dir.is_symlink() or not attempt_dir.is_dir():
            raise ValueError("attempt directory is unsafe")
        events_dir = attempt_dir / "events"
        events_dir.mkdir(exist_ok=True)
        if events_dir.is_symlink() or not events_dir.is_dir():
            raise ValueError("attempt events directory is unsafe")
        staging_dir = attempt_dir / "event-staging"
        staging_dir.mkdir(exist_ok=True)
        if staging_dir.is_symlink() or not staging_dir.is_dir():
            raise ValueError("attempt event-staging directory is unsafe")
        for residual in staging_dir.iterdir():
            if (
                not residual.name.startswith(".unpublished-event-")
                or residual.is_symlink()
                or not residual.is_file()
            ):
                raise ValueError("attempt event-staging contains an unsafe entry")
            residual.unlink()
        _fsync_directory(staging_dir)
        events = read_attempt_events(execution, attempt_id)
        previous_type = events[-1]["event_type"] if events else "prepared"
        if event_type not in ATTEMPT_EVENT_TRANSITIONS.get(previous_type, set()):
            staging_dir.rmdir()
            _fsync_directory(attempt_dir)
            raise ValueError(
                f"invalid attempt state transition: {previous_type} -> {event_type}"
            )
        sequence = len(events) + 1
        value: dict[str, Any] = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "sequence": sequence,
            "event_type": event_type,
            "created_at_utc": utc_now(),
            "actor": actor,
            "attempt_id": attempt_id,
            "intent_sha256": intent["intent_sha256"],
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
            "payload": payload,
            "event_sha256": None,
        }
        value["event_sha256"] = _semantic_hash(value, "event_sha256")
        filename = f"{sequence:06d}-{event_type}-{value['event_sha256'][:12]}.json"
        write_exclusive_json(
            events_dir / filename,
            value,
            temporary_dir=staging_dir,
            temporary_prefix=".unpublished-event-",
        )
        staging_dir.rmdir()
        _fsync_directory(attempt_dir)
        return value

    if lock_held:
        return append_locked()
    with execution_lock(execution):
        return append_locked()


def attempt_state(execution: ManagedExecution, attempt_id: str) -> AttemptState:
    intent = load_attempt_intent(execution, attempt_id)
    events = read_attempt_events(execution, attempt_id)
    if not events:
        return AttemptState(attempt_id, "prepared", intent, events, None, False, False, False)
    previous_type = "prepared"
    job_id: str | None = None
    for event in events:
        event_type = event["event_type"]
        if event_type not in ATTEMPT_EVENT_TRANSITIONS.get(previous_type, set()):
            raise ValueError(f"invalid attempt state transition: {previous_type} -> {event_type}")
        candidate = require_dict(event, "payload").get("job_id")
        if candidate is not None:
            if not isinstance(candidate, str) or not JOB_ID_RE.fullmatch(candidate):
                raise ValueError("invalid Slurm job ID in attempt event")
            if job_id is not None and candidate != job_id:
                raise ValueError("attempt event chain changes the Slurm job ID")
            job_id = candidate
        previous_type = event_type
    status_map = {
        "intent-cancelled": "cancelled",
        "submission-invoked": "submission-ambiguous",
        "submission-ambiguous": "submission-ambiguous",
        "scheduler-observation": "submission-ambiguous",
        "submission-not-found-confirmed": "cancelled",
        "submission-permanently-ambiguous": "permanently-quarantined",
        "submitted-held": "submitted-held",
        "job-verified": "verified-held",
        "release-invoked": "release-invoked",
        "release-outcome-ambiguous": "release-invoked",
        "release-invoked-scheduler-observation": "release-invoked",
        "release-ambiguous": "release-ambiguous",
        "release-scheduler-observation": "release-ambiguous",
        "job-released": "released-active",
        "terminal-accounting-frozen": "terminal-accounting-frozen",
    }
    status = status_map[previous_type]
    return AttemptState(
        attempt_id, status, intent, events, job_id, True,
        status in {
            "submission-ambiguous", "release-ambiguous",
            "release-invoked",
            "permanently-quarantined",
        },
        status in {"cancelled", "terminal-accounting-frozen"},
    )


def validate_worker_scheduler_identity(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent: dict[str, Any],
    array_index: int,
    environment: Mapping[str, str],
) -> AttemptState:
    """Bind one worker process to the exact verified Slurm array attempt.

    A checksum-valid intent is necessary but not sufficient authority to spend
    production seeds.  This check rejects direct/manual worker invocation and
    jobs from another submission before a task directory or Geant4 process can
    be created.  ``release-invoked`` is accepted to close the race between the
    exact release authorization, Slurm starting a task, and the manager
    recording the outcome.  For execution-v6, both pre-release
    ``verified-held`` and ordinary ``release-ambiguous`` remain rejected;
    legacy companions retain their historical verified-held admission.
    """

    state = attempt_state(execution, attempt_id)
    phase2c_v6 = (
        execution.manifest.get("schema_version")
        == EXECUTION_SCHEMA_VERSION_PHASE2C_V6
    )
    allowed_states = (
        {"release-invoked", "released-active"}
        if phase2c_v6
        else {"verified-held", "released-active"}
    )
    if state.status not in allowed_states:
        raise ValueError("managed worker attempt is not verified for execution")
    if state.job_id is None:
        raise ValueError("managed worker attempt has no verified Slurm job")
    selected = require_dict(intent, "selected")
    task_count = selected.get("task_count")
    if (
        not isinstance(task_count, int)
        or isinstance(task_count, bool)
        or array_index < 1
        or array_index > task_count
    ):
        raise ValueError("managed worker array index is outside the intent")
    expected = {
        "SLURM_ARRAY_JOB_ID": state.job_id,
        "SLURM_JOB_NAME": require_string(require_dict(intent, "scheduler"), "job_name"),
        "SLURM_ARRAY_TASK_ID": str(array_index),
        "SLURM_ARRAY_TASK_COUNT": str(task_count),
        "SLURM_ARRAY_TASK_MIN": "1",
        "SLURM_ARRAY_TASK_MAX": str(task_count),
        "SLURM_ARRAY_TASK_STEP": "1",
        "SLURM_SUBMIT_DIR": str(execution.directory),
    }
    if any(environment.get(name) != value for name, value in expected.items()):
        raise ValueError("managed worker Slurm environment identity mismatch")
    raw_job_id = environment.get("SLURM_JOB_ID", "")
    if not JOB_ID_RE.fullmatch(raw_job_id):
        raise ValueError("managed worker lacks a valid Slurm task job ID")
    return state


def _intent_directory_ids(execution: ManagedExecution) -> tuple[str, ...]:
    root = execution.directory / "intents"
    values = []
    for path in sorted(root.iterdir()):
        if path.name.startswith(".") and ".intent-" in path.name:
            prefix, _, suffix = path.name[1:].partition(".intent-")
            if (
                ATTEMPT_ID_RE.fullmatch(prefix)
                and suffix
                and re.fullmatch(r"[A-Za-z0-9_]+", suffix)
                and path.is_dir()
                and not path.is_symlink()
            ):
                continue
            raise ValueError("unsafe intent-staging entry")
        if not path.is_dir() or path.is_symlink():
            raise ValueError("unsafe entry in intents directory")
        _safe_id(path.name, "attempt ID")
        values.append(path.name)
    return tuple(values)


def list_attempt_ids(execution: ManagedExecution) -> tuple[str, ...]:
    values = _intent_directory_ids(execution)
    for attempt_id in values:
        load_attempt_intent(execution, attempt_id)
    return values


def _selected_task_ids_from_prior(
    execution: ManagedExecution, mode: str, prior_attempt_ids: Sequence[str]
) -> tuple[str, ...]:
    successor = execution.manifest.get("object_kind") == SUCCESSOR_OBJECT_KIND
    allowed_modes = (
        {"predecessor-retry", "resume", "retry-failed"}
        if successor
        else {"initial", "resume", "retry-failed"}
    )
    if mode not in allowed_modes:
        raise ValueError("unsupported attempt mode")
    attempts = [attempt_state(execution, value) for value in prior_attempt_ids]
    if any(
        state.status not in {"cancelled", "terminal-accounting-frozen"}
        for state in attempts
    ):
        raise ValueError(
            "cannot select tasks while a prior intent is prepared, active, or unresolved"
        )
    all_ids = tuple(task.logical_task_id for task in execution.tasks)
    phase2c_v6 = (
        execution.manifest.get("schema_version")
        == EXECUTION_SCHEMA_VERSION_PHASE2C_V6
    )
    if (
        phase2c_v6
        and mode in {"resume", "retry-failed"}
        and not any(
            state.status == "terminal-accounting-frozen" for state in attempts
        )
    ):
        raise ValueError(
            "execution-v6 resume/retry-failed requires terminal prior evidence"
        )
    if successor and not attempts and mode != "predecessor-retry":
        raise ValueError(
            "the first successor intent must use predecessor-retry"
        )
    if mode == "predecessor-retry":
        harmless_closed_attempts = all(
            state.status == "cancelled"
            and state.job_id is None
            and state.events
            and state.events[-1]["event_type"]
            in {"intent-cancelled", "submission-not-found-confirmed"}
            for state in attempts
        )
        if attempts and not harmless_closed_attempts:
            raise ValueError(
                "predecessor-retry prior intents are not harmless closed attempts"
            )
        authority = require_dict(execution.manifest, "recovery_authority")
        disposition = require_dict(authority, "zero_consumption")
        if (
            disposition.get("events_consumed") != 0
            or disposition.get("production_seeds_consumed") != 0
            or disposition.get("seed_reuse_authorized") is not True
            or len(all_ids) != 32
        ):
            raise ValueError("predecessor-retry lacks exact zero-consumption authority")
        return all_ids
    if mode == "initial":
        if attempts:
            raise ValueError("initial mode requires no prior intents")
        return all_ids
    completed: set[str] = set()
    failed: set[str] = set()
    for state in attempts:
        if state.status != "terminal-accounting-frozen":
            continue
        accounting = load_frozen_accounting(execution, state.attempt_id)
        selected = state.intent["selected"]["logical_task_ids"]
        task_states = require_dict(accounting, "task_states")
        task_exit_codes = require_dict(accounting, "task_exit_codes")
        for array_index, logical_id in enumerate(selected, start=1):
            key = str(array_index)
            scheduler_success = (
                base_slurm_state(task_states.get(key, "")) == "COMPLETED"
                and task_exit_codes.get(key) == "0:0"
            )
            marker_success = False
            if scheduler_success:
                try:
                    load_managed_task_result(execution, state.attempt_id, logical_id)
                except (OSError, ValueError, json.JSONDecodeError):
                    marker_success = False
                else:
                    marker_success = True
            if scheduler_success and marker_success:
                completed.add(logical_id)
            else:
                failed.add(logical_id)
    if mode == "resume":
        return tuple(value for value in all_ids if value not in completed)
    return tuple(value for value in all_ids if value in failed and value not in completed)


def selected_task_ids(execution: ManagedExecution, mode: str) -> tuple[str, ...]:
    return _selected_task_ids_from_prior(execution, mode, list_attempt_ids(execution))


def require_production_execution_open(execution: ManagedExecution) -> None:
    """Require an execution with current, execution-specific production authority.

    The failed historical v1 companion is permanently closed.  Its additive
    successor is open only while the separately tracked recovery-readiness
    lock validates against that exact successor and incident lineage.  The
    import is deliberately local: the successor library builds on this module,
    while this common mutation guard must also protect direct helper callers
    that bypass the formal CLI.
    """

    if (
        execution.execution_id == HISTORICAL_CLOSED_EXECUTION_ID
        and execution.execution_hash == HISTORICAL_CLOSED_EXECUTION_HASH
    ):
        raise ValueError(
            "historical Phase-2B execution is closed after its pre-simulation "
            "incident; use the additive recovery sealer, not a new intent"
        )
    manifest = getattr(execution, "manifest", {})
    if manifest.get("schema_version") == EXECUTION_SCHEMA_VERSION_PHASE2C_V6:
        try:
            from steel_module_production_phase2c_readiness import (
                verify_phase2c_readiness,
            )

            verify_phase2c_readiness(execution, repo_root=None)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "Phase-2C execution-v6 cannot create or mutate production "
                "attempts before valid R6 readiness: "
                f"{exc}"
            ) from exc
        return
    if (
        manifest.get("schema_version")
        in {
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2,
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3,
        }
        or manifest.get("object_kind") == SUCCESSOR_OBJECT_KIND
    ):
        try:
            from steel_module_production_successor_lib import (
                verify_successor_recovery_readiness,
            )

            verify_successor_recovery_readiness(execution, repo_root=None)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "Phase-2B recovery successor cannot create or mutate "
                "production attempts before valid additive recovery readiness: "
                f"{exc}"
            ) from exc


def _task_set_hash(tasks: Sequence[CampaignTask]) -> str:
    return sha256_bytes(canonical_json([asdict(task) for task in tasks]))


def prepare_attempt_intent(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    mode: str,
    actor: str,
    write: bool,
    readiness_lock_sha256: str | None,
) -> dict[str, Any]:
    require_production_execution_open(execution)
    if execution.manifest.get("object_kind") == SUCCESSOR_OBJECT_KIND:
        if (
            execution.manifest.get("schema_version")
            == EXECUTION_SCHEMA_VERSION_PHASE2C_V6
        ):
            from steel_module_production_phase2c_readiness import (
                verify_phase2c_readiness,
            )

            readiness_path, _ = verify_phase2c_readiness(
                execution, repo_root=None
            )
        else:
            from steel_module_production_successor_lib import (
                verify_successor_recovery_readiness,
            )

            readiness_path, _ = verify_successor_recovery_readiness(
                execution, repo_root=None
            )
        if readiness_lock_sha256 != sha256_file(readiness_path):
            raise ValueError("successor intent recovery-readiness digest is stale")
    _safe_id(attempt_id, "attempt ID")
    _safe_actor(actor)
    require_sha256({"readiness": readiness_lock_sha256}, "readiness")
    prior_ids = list_attempt_ids(execution)
    if prior_ids and attempt_id <= prior_ids[-1]:
        raise ValueError("attempt IDs must increase monotonically")
    selected_ids = _selected_task_ids_from_prior(execution, mode, prior_ids)
    if not selected_ids:
        raise ValueError("attempt mode selects no tasks")
    known = execution_task_by_id(execution)
    tasks = tuple(known[value] for value in selected_ids)
    scan_by_id = dict(zip((task.logical_task_id for task in execution.tasks), execution.scan_args))
    lines = tuple(scan_by_id[value] for value in selected_ids)
    target = _intent_dir(execution, attempt_id)
    if target.exists() or target.is_symlink():
        raise ValueError("refusing to overwrite attempt intent")
    job_name = _attempt_job_name(attempt_id)
    batch_relative = BATCH_SCRIPT_RELATIVE
    batch = execution.directory / batch_relative
    batch_sha = sha256_file(batch) if batch.is_file() else None
    if batch_sha is None and not execution.manifest.get("test_mode"):
        raise ValueError("frozen control source lacks the managed batch script")
    source_manifest = require_dict(execution.manifest, "sources")
    temp: Path | None = Path(
        tempfile.mkdtemp(
            prefix=f".{attempt_id}.intent-", dir=execution.directory / "intents"
        )
    )
    try:
        _write_tasks(temp / "tasks.tsv", tasks)
        (temp / "scan_args.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        wrapper = temp / "job-wrapper.sh"
        wrapper.write_text(_job_wrapper_text(execution, attempt_id), encoding="utf-8")
        wrapper.chmod(0o555)
        payload: dict[str, Any] = {
            "schema_version": INTENT_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "created_at_utc": utc_now(),
            "created_by": actor,
            "mode": mode,
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "campaign_id": execution.campaign_id,
            "plan_hash": execution.plan_hash,
            "program_id": execution.manifest["program"]["program_id"],
            "program_hash": execution.manifest["program"]["program_hash"],
            "child_id": INITIAL_CHILD_ID,
            "child_plan_hash": execution.manifest["managed_child"]["child_plan_hash"],
            "binding_hash": execution.manifest["managed_child"]["binding_hash"],
            "phase2a_lock_sha256": execution.manifest["phase2a_lock"]["sha256"],
            "readiness_lock_sha256": readiness_lock_sha256,
            "simulation_source": require_dict(source_manifest, "simulation"),
            "control_plane_source": require_dict(source_manifest, "control_plane"),
            "runtime": execution.manifest["runtime"],
            "selected": {
                "task_count": len(tasks), "event_count": sum(task.events for task in tasks),
                "task_set_hash": _task_set_hash(tasks),
                "logical_task_ids": list(selected_ids),
            },
            "artifacts": {
                "tasks": {"path": "tasks.tsv", "sha256": sha256_file(temp / "tasks.tsv")},
                "scan_args": {"path": "scan_args.txt", "sha256": sha256_file(temp / "scan_args.txt")},
                "job_wrapper": {"path": "job-wrapper.sh", "sha256": sha256_file(wrapper)},
            },
            "scheduler": {
                "account": FORMAL_ACCOUNT, "job_name": job_name,
                "array_spec": f"1-{len(tasks)}", "output_pattern": str(execution.directory / "attempts" / attempt_id / "slurm-%A_%a.out"),
                "batch_script": {"path": batch_relative, "sha256": batch_sha},
                "job_wrapper": {"path": "job-wrapper.sh", "sha256": sha256_file(wrapper)},
                "no_requeue": True, "export_mode": "NONE", "initially_held": True,
                "resources": {
                    "time_limit": "01:00:00", "nodes": 1, "ntasks": 1,
                    "cpus_per_task": 1, "memory": "2G",
                },
            },
            "intent_sha256": None,
        }
        payload["intent_sha256"] = _semantic_hash(payload, "intent_sha256")
        (temp / "intent.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        checksum = "".join(
                f"{sha256_file(temp / name)}  {name}\n"
                for name in ("intent.json", "job-wrapper.sh", "scan_args.txt", "tasks.tsv")
        )
        (temp / "SHA256SUMS").write_text(checksum, encoding="utf-8")
        if not write:
            return payload
        for path in temp.iterdir():
            path.chmod(0o555 if path.name == "job-wrapper.sh" else 0o444)
        fsync_tree(temp)
        with execution_lock(execution):
            if list_attempt_ids(execution) != prior_ids:
                raise ValueError("attempt lineage changed concurrently")
            if target.exists() or target.is_symlink():
                raise ValueError("attempt intent appeared concurrently")
            os.rename(temp, target)
            temp = None
            _fsync_directory(target.parent)
        return load_attempt_intent(execution, attempt_id)
    finally:
        if temp is not None and temp.exists():
            shutil.rmtree(temp)


def load_frozen_accounting(
    execution: ManagedExecution,
    attempt_id: str,
    *,
    require_event_binding: bool = True,
) -> dict[str, Any]:
    directory = _attempt_dir(execution, attempt_id) / "accounting"
    rows = verify_checksum_manifest(directory)
    if set(rows) != {"frozen.json", "sacct.psv", "squeue.psv"}:
        raise ValueError("terminal accounting checksum set mismatch")
    value = load_json(directory / "frozen.json")
    schema = value.get("schema_version")
    if schema not in {ACCOUNTING_SCHEMA_VERSION_V1, ACCOUNTING_SCHEMA_VERSION_V2}:
        raise ValueError("unsupported terminal-accounting schema")
    if schema == ACCOUNTING_SCHEMA_VERSION_V2:
        selected_task_count = value.get("selected_task_count")
        if not isinstance(selected_task_count, int) or selected_task_count < 1:
            raise ValueError("terminal-accounting v2 task count is invalid")
        expected_indices = {
            str(index)
            for index in range(1, selected_task_count + 1)
        }
        if (
            value.get("sacct_field_order")
            != [
                "JobID", "JobIDRaw", "State", "ExitCode", "ElapsedRaw",
                "MaxRSS", "MaxVMSize",
            ]
            or value.get("array_index_identity_field") != "JobID"
            or not isinstance(value.get("array_parent_row_present"), bool)
            or set(require_dict(value, "task_job_ids")) != expected_indices
            or set(require_dict(value, "task_job_ids_raw")) != expected_indices
        ):
            raise ValueError("terminal-accounting v2 provenance is incomplete")
    intent = load_attempt_intent(execution, attempt_id)
    if value.get("attempt_id") != attempt_id or value.get("intent_sha256") != intent.get("intent_sha256"):
        raise ValueError("terminal accounting identity mismatch")
    if require_event_binding:
        state = attempt_state(execution, attempt_id)
        if state.status != "terminal-accounting-frozen" or not state.events:
            raise ValueError("terminal accounting lacks its immutable terminal event")
        terminal_payload = require_dict(state.events[-1], "payload")
        expected_manifest_sha256 = sha256_file(directory / "SHA256SUMS")
        all_tasks_completed = value.get("all_tasks_completed")
        if not isinstance(all_tasks_completed, bool):
            raise ValueError("terminal accounting completion flag is invalid")
        if (
            terminal_payload.get("job_id") != value.get("job_id")
            or terminal_payload.get("accounting_manifest_sha256")
            != expected_manifest_sha256
            or terminal_payload.get("all_tasks_completed")
            != all_tasks_completed
        ):
            raise ValueError("terminal accounting event binding mismatch")
    return value


def load_managed_task_result(
    execution: ManagedExecution, attempt_id: str, logical_task_id: str
) -> dict[str, Any]:
    path = _attempt_dir(execution, attempt_id) / "tasks" / logical_task_id / "task_result.json"
    value = load_json(path)
    if value.get("schema_version") != TASK_RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported managed task-result schema")
    intent = load_attempt_intent(execution, attempt_id)
    task = execution_task_by_id(execution).get(logical_task_id)
    if task is None:
        raise ValueError("task result names an unknown logical task")
    expected = {
        "execution_id": execution.execution_id, "execution_hash": execution.execution_hash,
        "intent_sha256": intent["intent_sha256"], "attempt_id": attempt_id,
        "logical_task_id": logical_task_id, "configuration_hash": task.configuration_hash,
        "seed1": task.seed1, "seed2": task.seed2, "events": task.events,
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ValueError(f"managed task-result {key} mismatch")
    identity_expected = {
        "campaign_id": execution.campaign_id,
        "plan_hash": execution.plan_hash,
        "program_id": execution.manifest["program"]["program_id"],
        "program_hash": execution.manifest["program"]["program_hash"],
        "child_id": INITIAL_CHILD_ID,
        "child_plan_hash": execution.manifest["managed_child"]["child_plan_hash"],
        "binding_hash": execution.manifest["managed_child"]["binding_hash"],
        "phase2a_lock_sha256": execution.manifest["phase2a_lock"]["sha256"],
        "readiness_lock_sha256": intent["readiness_lock_sha256"],
        "task_index": task.task_index,
        "tile_thickness_mm": task.tile_thickness_mm,
        "sipm_layout": task.sipm_layout,
        "absorber_transverse_mm": task.absorber_transverse_mm,
        "seed_block": task.seed_block,
    }
    for key, expected_value in identity_expected.items():
        if value.get(key) != expected_value:
            raise ValueError(f"managed task-result {key} mismatch")
    if value.get("simulation_source") != execution.manifest["sources"]["simulation"]:
        raise ValueError("managed task-result simulation-source mismatch")
    if value.get("control_plane_source") != execution.manifest["sources"]["control_plane"]:
        raise ValueError("managed task-result control-plane-source mismatch")
    if value.get("runtime") != execution.manifest["runtime"]:
        raise ValueError("managed task-result runtime mismatch")
    artifacts = require_dict(value, "artifacts")
    expected_artifacts = {
        "run_config", "points", "macro", "simulation_log", "root", "summary",
        "efficiency_map",
    }
    if set(artifacts) != expected_artifacts:
        raise ValueError("managed task-result artifact set mismatch")
    for label, record_value in artifacts.items():
        record = require_dict(artifacts, label)
        relative = require_string(record, "path")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("managed task-result artifact path is unsafe")
        artifact = (execution.directory / relative).resolve()
        if execution.directory not in artifact.parents or not artifact.is_file() or artifact.is_symlink():
            raise ValueError("managed task-result artifact escapes execution root")
        if record.get("size_bytes") != artifact.stat().st_size:
            raise ValueError("managed task-result artifact size mismatch")
        if require_sha256(record, "sha256") != sha256_file(artifact):
            raise ValueError("managed task-result artifact checksum mismatch")
    return value


__all__ = [
    "ACCOUNTING_SCHEMA_VERSION", "ACCOUNTING_SCHEMA_VERSION_V1",
    "ACCOUNTING_SCHEMA_VERSION_V2", "AttemptState", "EVENT_SCHEMA_VERSION",
    "CONTROL_LOCK_PROTOCOL_V1", "CONTROL_LOCK_PROTOCOL_V2",
    "EXECUTION_SCHEMA_VERSION_SUCCESSOR_V4",
    "EXECUTION_SCHEMA_VERSION", "EXECUTION_SCHEMA_VERSION_V1",
    "EXECUTION_SCHEMA_VERSION_V2", "EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1",
    "EXECUTION_SCHEMA_VERSION_SUCCESSOR_V2",
    "EXECUTION_SCHEMA_VERSION_SUCCESSOR_V3",
    "SUCCESSOR_OBJECT_KIND", "FORMAL_ACCOUNT", "FORMAL_EXECUTION_NAME",
    "HISTORICAL_CLOSED_ATTEMPT_ID", "HISTORICAL_CLOSED_EXECUTION_HASH",
    "HISTORICAL_CLOSED_EXECUTION_ID", "HISTORICAL_CLOSED_INTENT_SHA256",
    "HISTORICAL_CLOSED_JOB_ID", "HISTORICAL_CLOSED_LOCK_DEVICE",
    "HISTORICAL_CLOSED_LOCK_INODE",
    "INTENT_SCHEMA_VERSION", "ManagedExecution", "READINESS_CRITICAL_ARTIFACTS",
    "READINESS_LOCK_RELATIVE", "READINESS_LOCK_SCHEMA_VERSION",
    "TASK_RESULT_SCHEMA_VERSION", "append_attempt_event", "attempt_state",
    "validate_worker_scheduler_identity",
    "canonical_execution_directory", "execution_lock", "execution_task_by_id",
    "fsync_directory", "fsync_tree", "historical_recovery_execution_lock",
    "list_attempt_ids", "load_attempt_intent", "load_execution_companion",
    "load_historical_closed_execution_for_recovery",
    "load_frozen_accounting", "load_managed_task_result", "load_phase2a_lock",
    "materialize_execution_companion", "prepare_attempt_intent",
    "require_production_execution_open", "read_attempt_events",
    "require_pristine_readiness_workspace", "selected_task_ids", "source_tree_hash",
    "utc_now", "verify_checksum_manifest", "verify_execution_static_checksums",
    "validate_execution_control_lock", "validate_historical_recovery_control_lock",
    "verify_readiness_protected_artifacts",
    "write_exclusive_bytes",
    "write_exclusive_json",
]
