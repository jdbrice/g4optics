#!/usr/bin/env python3
"""Isolated Phase-2C preflight control plane and evidence primitives.

This module is intentionally independent from the production-attempt manager.
It can authorize and run only the one fixed, non-array, no-Geant4 Phase-2C
preflight.  Its append-only journal lives below the external Phase-2C evidence
root; neither the sacrificial twin nor the future clean execution-v6 receives
an intent or attempt record.

The public functions accept an injectable command runner so focused tests can
prove every scheduler branch while placing forbidden-command sentinels in
front of real ``sbatch`` and ``scontrol`` binaries.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from steel_module_campaign_lib import (
    canonical_json,
    load_json,
    require_dict,
    sha256_file,
)
from steel_module_production_checkpoint_lib import publish_directory_no_replace
from steel_module_production_container_contract import NO_MOUNT_CLASSES


PREFLIGHT_TWIN_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-twin-v1"
)
PREFLIGHT_EVIDENCE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-evidence-v1"
)
PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-failure-evidence-v1"
)
AUTHORIZATION_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-authorization-v1"
)
EVENT_SCHEMA_VERSION = "steel-module-production-phase2c-preflight-event-v1"
ACCOUNTING_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-accounting-v1"
)
RAW_RESULT_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-raw-result-v1"
)
CLOSURE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-closure-v1"
)
FAILURE_CLOSURE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-failure-closure-v1"
)
FAILURE_MARKER_NAME = ".phase2c-preflight-failed.json"
ZERO_MATCH_REVIEW_INTERVAL_SECONDS = 600

FORMAL_ACCOUNT = "PAS2524"
FORMAL_TIME_LIMIT = "00:10:00"
FORMAL_CPUS = 1
FORMAL_MEMORY = "1G"
FORMAL_LAUNCHER_RELATIVE = (
    "sources/control/hpc/osc/run_steel_module_production_phase2c_preflight.sbatch"
)
MOUNTINFO_CONTRACT_VERSION = "linux-proc-mountinfo-v1"
CONTAINER_REPORT_KEYS = (
    "mountinfo_simulation_root_read_only",
    "mountinfo_control_root_read_only",
    "mountinfo_execution_root_read_only",
    "mountinfo_data_root_read_only",
    "mountinfo_prebuilt_root_read_only",
    "simulation_root_create_rejected",
    "control_root_create_rejected",
    "execution_root_create_rejected",
    "data_root_create_rejected",
    "prebuilt_root_create_rejected",
    "mountinfo_external_probe_root_read_write",
    "external_probe_root_writable",
    "challenge_roundtrip_matches",
)
RAW_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "created_at_utc",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "probe_passed",
        "apptainer_invoked",
        "geant4_invoked",
        "events_consumed",
        "production_seeds_consumed",
        "twin_execution_closed",
        "twin_id",
        "twin_hash",
        "production_equivalence_hash",
        "slurm_job_id",
        "slurm_job_name",
        "scheduler_account",
        "compute_node",
        "runtime_identity",
        "container_contract",
        "return_code",
        "challenge_sha256",
        "roundtrip_sha256",
    }
)
EVIDENCE_PAYLOAD_KEYS = frozenset(
    {
        "schema_version",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "evidence_id",
        "evidence_hash",
        "created_at_utc",
        "twin",
        "v5_evidence_hash",
        "production_equivalence_hash",
        "authorization_hash",
        "authorization_manifest_sha256",
        "scheduler",
        "runtime_boundary",
        "compute_preflight",
        "consumption",
        "twin_execution_closed",
        "source_artifacts",
    }
)
CONTROL_DIRECTORY = "preflight-control"
CONTROL_LOCK_NAME = ".control.lock"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")
EVENT_FILENAME_RE = re.compile(
    r"^(?P<sequence>[0-9]{6})-(?P<kind>[a-z0-9-]+)-"
    r"(?P<digest>[0-9a-f]{12})\.json$"
)
TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
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


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class PreflightContext:
    """The checksum-validated sacrificial twin and external journal root."""

    twin: Any
    directory: Path
    manifest: dict[str, Any]
    evidence_root: Path
    twin_id: str
    twin_hash: str
    production_equivalence_hash: str

    @property
    def job_name(self) -> str:
        return "g4sm-p2c-" + self.twin_hash[:12]

    @property
    def control_root(self) -> Path:
        return self.evidence_root / CONTROL_DIRECTORY


@dataclass(frozen=True)
class PreflightState:
    authorization_hash: str
    status: str
    events: tuple[dict[str, Any], ...]
    job_id: str | None
    scheduler_contacted: bool
    ambiguous: bool


EVENT_TRANSITIONS: dict[str, set[str]] = {
    "prepared": {"submission-invoked"},
    "submission-invoked": {"submitted-held", "submission-ambiguous"},
    "submission-ambiguous": {
        "submitted-held",
        "scheduler-observation",
        "permanently-ambiguous",
    },
    "scheduler-observation": {
        "submitted-held",
        "scheduler-observation",
        "permanently-ambiguous",
    },
    "submitted-held": {"held-verified", "verification-failed"},
    "held-verified": {"job-released", "release-ambiguous"},
    "release-ambiguous": {
        "held-verified",
        "job-released",
        "release-observation",
        "permanently-ambiguous",
    },
    "release-observation": {
        "held-verified",
        "job-released",
        "release-observation",
        "permanently-ambiguous",
    },
    "job-released": {"terminal-accounting-frozen"},
    "terminal-accounting-frozen": set(),
    "verification-failed": {"terminal-accounting-frozen"},
    "permanently-ambiguous": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _semantic_hash(value: Mapping[str, Any], field: str) -> str:
    copy = dict(value)
    copy[field] = None
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def _require_regular_directory(path: Path, label: str) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise ValueError(f"missing {label}: {resolved}")
    return resolved


def _require_safe_child(root: Path, path: Path, label: str) -> Path:
    resolved_root = root.resolve()
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = requested.resolve()
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ValueError(f"{label} escapes its canonical root")
    return resolved


def _write_exclusive_bytes(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite immutable file: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    path.chmod(mode)


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    _write_exclusive_bytes(
        path,
        json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ValueError("immutable publication contains a symlink")
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        elif path.is_dir():
            _fsync_directory(path)
        else:
            raise ValueError("immutable publication contains a special file")
    _fsync_directory(root)


def _publish_directory(temporary: Path, target: Path) -> Path:
    if target.exists() or target.is_symlink():
        raise ValueError(f"immutable target already exists: {target}")
    _fsync_tree(temporary)
    publish_directory_no_replace(temporary, target)
    _fsync_directory(target.parent)
    return target


def _recursive_records(root: Path, *, exclude: set[str] | None = None) -> dict[str, str]:
    omitted = set() if exclude is None else set(exclude)
    records: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in omitted:
            continue
        if path.is_symlink():
            raise ValueError(f"unsafe symlink in immutable tree: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"unsafe special file in immutable tree: {relative}")
        records[relative] = sha256_file(path)
    return records


def _write_checksum_manifest(root: Path, *, exclude: set[str] | None = None) -> None:
    records = _recursive_records(root, exclude=exclude)
    text = "".join(f"{digest}  {name}\n" for name, digest in sorted(records.items()))
    _write_exclusive_bytes(root / "SHA256SUMS", text.encode("utf-8"))


def verify_checksum_manifest(root: Path) -> dict[str, str]:
    directory = _require_regular_directory(root, "checksum bundle")
    manifest = directory / "SHA256SUMS"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("missing safe checksum manifest")
    recorded: dict[str, str] = {}
    for number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        pieces = raw.split("  ", 1)
        if (
            len(pieces) != 2
            or not _is_sha256(pieces[0])
            or not pieces[1]
            or pieces[1].startswith("/")
            or ".." in Path(pieces[1]).parts
            or pieces[1] in recorded
        ):
            raise ValueError(f"invalid checksum row {number}")
        recorded[pieces[1]] = pieces[0]
    observed = _recursive_records(directory, exclude={"SHA256SUMS"})
    if recorded != observed:
        raise ValueError("checksum manifest does not exactly cover the bundle")
    return recorded


def _core_module() -> Any:
    # Lazy import avoids a circular dependency while the Phase-2C materializer
    # imports this module's evidence validator.
    import steel_module_production_phase2c_lib as core

    return core


def load_formal_context(repo_root: Path) -> PreflightContext:
    core = _core_module()
    twin = core.load_preflight_v6(repo_root=repo_root)
    directory = _require_regular_directory(Path(twin.directory), "Phase-2C preflight twin")
    manifest = dict(twin.manifest)
    if manifest.get("schema_version") != PREFLIGHT_TWIN_SCHEMA_VERSION:
        raise ValueError("formal Phase-2C preflight twin schema mismatch")
    twin_id = manifest.get("twin_id")
    twin_hash = manifest.get("twin_hash")
    equivalence = manifest.get("production_equivalence_hash")
    if not isinstance(twin_id, str) or not twin_id.startswith(
        "sm-v1-production-bc-s1-preflight-v6-"
    ):
        raise ValueError("formal Phase-2C preflight twin ID is invalid")
    if not _is_sha256(twin_hash) or not _is_sha256(equivalence):
        raise ValueError("formal Phase-2C preflight twin hash is invalid")
    evidence_root = Path(core.canonical_phase2c_evidence_root(repo_root)).expanduser()
    if evidence_root.is_symlink():
        raise ValueError("canonical Phase-2C evidence root must not be a symlink")
    return PreflightContext(
        twin=twin,
        directory=directory,
        manifest=manifest,
        evidence_root=evidence_root.resolve(strict=False),
        twin_id=twin_id,
        twin_hash=twin_hash,
        production_equivalence_hash=equivalence,
    )


def _control_lock_path(context: PreflightContext) -> Path:
    return context.control_root / CONTROL_LOCK_NAME


def _initialize_control_root(context: PreflightContext) -> None:
    root = context.evidence_root
    if root.exists() or root.is_symlink():
        _require_regular_directory(root, "Phase-2C evidence root")
    else:
        parent = _require_regular_directory(root.parent, "evidence parent")
        if parent / root.name != root:
            raise ValueError("canonical Phase-2C evidence root path is not normalized")
        root.mkdir(mode=0o700)
        _fsync_directory(parent)
    control = context.control_root
    if control.exists() or control.is_symlink():
        _require_regular_directory(control, "Phase-2C preflight control root")
    else:
        control.mkdir(mode=0o700)
        _fsync_directory(root)
    for name in (
        "authorizations", "attempts", "raw", "accounting", "evidence",
        "failures", "slurm",
    ):
        path = control / name
        if path.exists() or path.is_symlink():
            _require_regular_directory(path, f"Phase-2C {name} root")
        else:
            path.mkdir(mode=0o700)
            _fsync_directory(control)
    lock = _control_lock_path(context)
    if not lock.exists() and not lock.is_symlink():
        descriptor = os.open(
            lock,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
        _fsync_directory(control)
    _validate_control_lock(lock)


def _validate_control_lock(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2C preflight control lock is unsafe")
    status = path.stat()
    if (
        stat.S_IMODE(status.st_mode) != 0o600
        or status.st_size != 0
        or status.st_nlink != 1
        or sha256_file(path) != EMPTY_SHA256
    ):
        raise ValueError("Phase-2C preflight control lock contract mismatch")


@contextmanager
def control_lock(context: PreflightContext) -> Iterator[None]:
    _initialize_control_root(context)
    path = _control_lock_path(context)
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        current = path.stat()
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Phase-2C preflight control lock path changed while opening")
        _validate_control_lock(path)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _validate_control_lock(path)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _static_manifest_sha(context: PreflightContext) -> str:
    path = context.directory / "STATIC_SHA256SUMS"
    if path.is_symlink() or not path.is_file():
        raise ValueError("preflight twin static checksum manifest is missing")
    return sha256_file(path)


def _launcher(context: PreflightContext) -> Path:
    path = context.directory / FORMAL_LAUNCHER_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("frozen Phase-2C preflight launcher is missing")
    if not os.access(path, os.X_OK):
        raise ValueError("frozen Phase-2C preflight launcher is not executable")
    return path


def authorization_payload(context: PreflightContext, *, actor: str) -> dict[str, Any]:
    if not ACTOR_RE.fullmatch(actor):
        raise ValueError("Phase-2C preflight actor identity is invalid")
    launcher = _launcher(context)
    value: dict[str, Any] = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": None,
        "authorization_hash": None,
        "created_at_utc": None,
        "created_by": actor,
        "twin": {
            "directory": str(context.directory),
            "twin_id": context.twin_id,
            "twin_hash": context.twin_hash,
            "static_manifest_sha256": _static_manifest_sha(context),
        },
        "production_equivalence_hash": context.production_equivalence_hash,
        "scheduler": {
            "account": FORMAL_ACCOUNT,
            "account_read_policy": "exact-or-ascii-lowercase",
            "array": False,
            "initially_held": True,
            "no_requeue": True,
            "export_mode": "NONE",
            "job_name": context.job_name,
            "output_pattern": str(context.control_root / "slurm" / "slurm-%j.out"),
            "resources": {
                "nodes": 1,
                "ntasks": 1,
                "cpus_per_task": FORMAL_CPUS,
                "memory": FORMAL_MEMORY,
                "time_limit": FORMAL_TIME_LIMIT,
            },
            "launcher": {
                "path": FORMAL_LAUNCHER_RELATIVE,
                "sha256": sha256_file(launcher),
            },
        },
        "authority": {
            "purpose": "held-non-array-no-geant4-preflight-only",
            "production_intent_authorized": False,
            "production_submission_authorized": False,
            "separate_release_required": True,
            "automatic_retry_allowed": False,
        },
    }
    # Timestamps are provenance rather than scheduler authority.  Excluding
    # them keeps check-only and write-authorization hashes identical.
    semantic = dict(value)
    semantic.pop("created_at_utc")
    semantic["authorization_hash"] = None
    semantic["authorization_id"] = None
    digest = hashlib.sha256(canonical_json(semantic)).hexdigest()
    value["authorization_hash"] = digest
    value["authorization_id"] = "sm-v1-phase2c-preflight-auth-" + digest[:12]
    return value


def _authorization_directory(context: PreflightContext, authorization_hash: str) -> Path:
    if not _is_sha256(authorization_hash):
        raise ValueError("Phase-2C preflight authorization hash is invalid")
    return context.control_root / "authorizations" / (
        "sm-v1-phase2c-preflight-auth-" + authorization_hash[:12]
    )


def write_authorization(context: PreflightContext, *, actor: str) -> dict[str, Any]:
    payload = authorization_payload(context, actor=actor)
    authorization_hash = payload["authorization_hash"]
    target = _authorization_directory(context, authorization_hash)
    with control_lock(context):
        existing = list_authorizations(context)
        if existing:
            raise ValueError(
                "this sacrificial twin already has its one preflight authorization"
            )
        if target.exists() or target.is_symlink():
            raise ValueError("Phase-2C preflight authorization already exists")
        temporary = Path(
            tempfile.mkdtemp(prefix=".authorization-", dir=target.parent)
        )
        try:
            value = dict(payload)
            value["created_at_utc"] = utc_now()
            _write_exclusive_json(temporary / "authorization.json", value)
            _write_checksum_manifest(temporary)
            _publish_directory(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return load_authorization(context, authorization_hash)


def list_authorizations(context: PreflightContext) -> tuple[str, ...]:
    root = context.control_root / "authorizations"
    if not root.exists():
        return ()
    _require_regular_directory(root, "Phase-2C authorization root")
    values: list[str] = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_dir():
            raise ValueError("unsafe Phase-2C authorization entry")
        payload = load_json(path / "authorization.json")
        digest = payload.get("authorization_hash")
        if not _is_sha256(digest) or path.name != (
            "sm-v1-phase2c-preflight-auth-" + digest[:12]
        ):
            raise ValueError("Phase-2C authorization directory identity mismatch")
        recorded_twin = payload.get("twin")
        if not isinstance(recorded_twin, dict):
            raise ValueError("Phase-2C authorization twin identity is invalid")
        if recorded_twin.get("twin_hash") == context.twin_hash:
            load_authorization(context, digest)
            values.append(digest)
    return tuple(values)


def load_authorization(
    context: PreflightContext, authorization_hash: str
) -> dict[str, Any]:
    root = _authorization_directory(context, authorization_hash)
    records = verify_checksum_manifest(root)
    if set(records) != {"authorization.json"}:
        raise ValueError("Phase-2C authorization file set mismatch")
    payload = load_json(root / "authorization.json")
    expected = authorization_payload(context, actor=str(payload.get("created_by", "")))
    expected["created_at_utc"] = payload.get("created_at_utc")
    if (
        payload != expected
        or payload.get("authorization_hash") != authorization_hash
        or not isinstance(payload.get("created_at_utc"), str)
    ):
        raise ValueError("Phase-2C authorization semantic validation failed")
    return payload


def submission_command(
    context: PreflightContext, authorization: Mapping[str, Any]
) -> list[str]:
    scheduler = authorization["scheduler"]
    launcher = context.directory / scheduler["launcher"]["path"]
    return [
        "sbatch",
        "--hold",
        "--parsable",
        "--no-requeue",
        "--export=NONE",
        "--time=" + FORMAL_TIME_LIMIT,
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=1",
        "--mem=" + FORMAL_MEMORY,
        "--account",
        FORMAL_ACCOUNT,
        "--job-name",
        scheduler["job_name"],
        "--output",
        scheduler["output_pattern"],
        str(launcher),
    ]


def _event_root(context: PreflightContext, authorization_hash: str) -> Path:
    if not _is_sha256(authorization_hash):
        raise ValueError("Phase-2C preflight authorization hash is invalid")
    return context.control_root / "attempts" / authorization_hash[:12] / "events"


def read_events(
    context: PreflightContext, authorization_hash: str
) -> tuple[dict[str, Any], ...]:
    load_authorization(context, authorization_hash)
    root = _event_root(context, authorization_hash)
    if not root.exists():
        return ()
    _require_regular_directory(root, "Phase-2C preflight journal")
    output: list[dict[str, Any]] = []
    previous: str | None = None
    previous_type = "prepared"
    for sequence, path in enumerate(sorted(root.iterdir()), 1):
        if path.is_symlink() or not path.is_file():
            raise ValueError("unsafe Phase-2C preflight journal entry")
        match = EVENT_FILENAME_RE.fullmatch(path.name)
        if match is None or int(match.group("sequence")) != sequence:
            raise ValueError("Phase-2C preflight journal sequence is not contiguous")
        value = load_json(path)
        event_type = value.get("event_type")
        if (
            value.get("schema_version") != EVENT_SCHEMA_VERSION
            or value.get("sequence") != sequence
            or value.get("authorization_hash") != authorization_hash
            or value.get("previous_event_sha256") != previous
            or event_type not in EVENT_TRANSITIONS.get(previous_type, set())
            or value.get("event_sha256") != _semantic_hash(value, "event_sha256")
            or match.group("kind") != event_type
            or match.group("digest") != str(value.get("event_sha256", ""))[:12]
        ):
            raise ValueError("Phase-2C preflight journal validation failed")
        output.append(value)
        previous = value["event_sha256"]
        previous_type = event_type
    return tuple(output)


def append_event(
    context: PreflightContext,
    authorization_hash: str,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    actor: str,
    lock_held: bool = False,
) -> dict[str, Any]:
    if not ACTOR_RE.fullmatch(actor):
        raise ValueError("Phase-2C preflight actor identity is invalid")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", event_type):
        raise ValueError("unsafe Phase-2C preflight event type")

    def append_locked() -> dict[str, Any]:
        events = read_events(context, authorization_hash)
        previous_type = events[-1]["event_type"] if events else "prepared"
        if event_type not in EVENT_TRANSITIONS.get(previous_type, set()):
            raise ValueError(
                f"invalid Phase-2C preflight transition: {previous_type} -> {event_type}"
            )
        root = _event_root(context, authorization_hash)
        attempt_root = root.parent
        attempts_root = attempt_root.parent
        attempt_created = not attempt_root.exists()
        attempt_root.mkdir(mode=0o700, exist_ok=True)
        if attempt_root.is_symlink() or not attempt_root.is_dir():
            raise ValueError("unsafe Phase-2C preflight attempt root")
        if attempt_created:
            _fsync_directory(attempts_root)
        events_created = not root.exists()
        root.mkdir(mode=0o700, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("unsafe Phase-2C preflight journal root")
        if events_created:
            _fsync_directory(attempt_root)
        sequence = len(events) + 1
        value: dict[str, Any] = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "sequence": sequence,
            "event_type": event_type,
            "created_at_utc": utc_now(),
            "actor": actor,
            "authorization_hash": authorization_hash,
            "previous_event_sha256": events[-1]["event_sha256"] if events else None,
            "payload": dict(payload),
            "event_sha256": None,
        }
        value["event_sha256"] = _semantic_hash(value, "event_sha256")
        name = f"{sequence:06d}-{event_type}-{value['event_sha256'][:12]}.json"
        _write_exclusive_json(root / name, value)
        _fsync_directory(root)
        return value

    if lock_held:
        return append_locked()
    with control_lock(context):
        return append_locked()


def preflight_state(context: PreflightContext, authorization_hash: str) -> PreflightState:
    events = read_events(context, authorization_hash)
    previous = events[-1]["event_type"] if events else "prepared"
    job_id: str | None = None
    for event in events:
        candidate = event["payload"].get("job_id")
        if candidate is not None:
            if not isinstance(candidate, str) or not JOB_ID_RE.fullmatch(candidate):
                raise ValueError("Phase-2C preflight journal contains an invalid job ID")
            if job_id is not None and candidate != job_id:
                raise ValueError("Phase-2C preflight journal changes the job ID")
            job_id = candidate
    status_map = {
        "prepared": "prepared",
        "submission-invoked": "submission-ambiguous",
        "submission-ambiguous": "submission-ambiguous",
        "scheduler-observation": "submission-ambiguous",
        "submitted-held": "submitted-held",
        "held-verified": "verified-held",
        "verification-failed": "closed-failed",
        "job-released": "released-active",
        "release-ambiguous": "release-ambiguous",
        "release-observation": "release-ambiguous",
        "permanently-ambiguous": "closed-ambiguous",
        "terminal-accounting-frozen": "terminal-accounting-frozen",
    }
    return PreflightState(
        authorization_hash=authorization_hash,
        status=status_map[previous],
        events=events,
        job_id=job_id,
        scheduler_contacted=bool(events),
        ambiguous=previous in {
            "submission-invoked",
            "submission-ambiguous",
            "scheduler-observation",
            "release-ambiguous",
            "release-observation",
            "permanently-ambiguous",
        },
    )


def _run(
    command: Sequence[str], *, cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            "LANG": "C",
        },
    )


def _run_with(
    runner: CommandRunner,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return runner(list(command), cwd=cwd, timeout=timeout)


def _scontrol_snapshot(
    command: Sequence[str], result: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    """Preserve one byte-for-byte scheduler identity observation."""

    stdout = result.stdout
    stderr = result.stderr
    return {
        "command": list(command),
        "return_code": result.returncode,
        "stdout": stdout,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr": stderr,
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }


def _validate_scontrol_snapshot(
    snapshot: Any, *, job_id: str
) -> str:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "command",
        "return_code",
        "stdout",
        "stdout_sha256",
        "stderr",
        "stderr_sha256",
    }:
        raise ValueError("Phase-2C scontrol snapshot key set mismatch")
    command = ["scontrol", "show", "job", job_id, "-o"]
    stdout = snapshot.get("stdout")
    stderr = snapshot.get("stderr")
    if (
        snapshot.get("command") != command
        or snapshot.get("return_code") != 0
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or snapshot.get("stdout_sha256")
        != hashlib.sha256(stdout.encode("utf-8")).hexdigest()
        or snapshot.get("stderr_sha256")
        != hashlib.sha256(stderr.encode("utf-8")).hexdigest()
    ):
        raise ValueError("Phase-2C scontrol snapshot validation failed")
    return stdout


def submit_authorization(
    context: PreflightContext,
    *,
    authorization_hash: str,
    actor: str,
    runner: CommandRunner = _run,
) -> str:
    authorization = load_authorization(context, authorization_hash)
    command = submission_command(context, authorization)
    with control_lock(context):
        state = preflight_state(context, authorization_hash)
        if state.status != "prepared":
            raise ValueError("only one never-submitted preflight authorization may submit")
        other = [
            value
            for value in list_authorizations(context)
            if value != authorization_hash
            and preflight_state(context, value).scheduler_contacted
        ]
        if other:
            raise ValueError("another Phase-2C preflight has contacted the scheduler")
        append_event(
            context,
            authorization_hash,
            "submission-invoked",
            {
                "command_sha256": hashlib.sha256(
                    "\0".join(command).encode("utf-8")
                ).hexdigest(),
                "job_name": context.job_name,
            },
            actor=actor,
            lock_held=True,
        )
    try:
        result = _run_with(runner, command, cwd=context.directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        append_event(
            context,
            authorization_hash,
            "submission-ambiguous",
            {"reason": type(exc).__name__},
            actor=actor,
        )
        raise ValueError("preflight submission outcome is ambiguous; do not retry") from exc
    raw = result.stdout.strip().split(";", 1)[0]
    if result.returncode != 0 or not JOB_ID_RE.fullmatch(raw):
        append_event(
            context,
            authorization_hash,
            "submission-ambiguous",
            {
                "return_code": result.returncode,
                "stdout": result.stdout[-1000:],
                "stderr": result.stderr[-1000:],
            },
            actor=actor,
        )
        raise ValueError("preflight submission outcome is ambiguous; do not retry")
    append_event(
        context,
        authorization_hash,
        "submitted-held",
        {"job_id": raw},
        actor=actor,
    )
    return raw


def _parse_scontrol(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        row: dict[str, str] = {}
        for token in shlex.split(raw):
            if "=" in token:
                key, value = token.split("=", 1)
                row[key] = value
        if row:
            rows.append(row)
    return rows


def _account_comparison(value: str) -> str:
    if value == FORMAL_ACCOUNT:
        return "exact"
    if value.isascii() and value == FORMAL_ACCOUNT.lower():
        return "osc-ascii-lowercase-canonicalization"
    raise ValueError("Slurm held preflight Account mismatch")


def validate_held_row(
    context: PreflightContext,
    authorization: Mapping[str, Any],
    *,
    job_id: str,
    text: str,
    require_held: bool = True,
) -> dict[str, str]:
    rows = [row for row in _parse_scontrol(text) if row.get("JobId") == job_id]
    if len(rows) != 1:
        raise ValueError("scontrol did not return one exact non-array preflight job")
    row = rows[0]
    scheduler = authorization["scheduler"]
    expected = {
        "JobName": scheduler["job_name"],
        "Command": str(context.directory / scheduler["launcher"]["path"]),
        "WorkDir": str(context.directory),
        "Requeue": "0",
        "TimeLimit": FORMAL_TIME_LIMIT,
        "NumCPUs": "1",
        "NumTasks": "1",
        "CPUs/Task": "1",
        "MinMemoryNode": FORMAL_MEMORY,
    }
    for key, value in expected.items():
        if row.get(key) != value:
            raise ValueError(f"Slurm held preflight {key} mismatch")
    if row.get("ArrayJobId") not in {None, "", "N/A"} or row.get(
        "ArrayTaskId"
    ) not in {None, "", "N/A"}:
        raise ValueError("Phase-2C preflight scheduler job must be non-array")
    comparison = _account_comparison(row.get("Account", ""))
    expected_output = str(context.control_root / "slurm" / f"slurm-{job_id}.out")
    if row.get("StdOut") != expected_output or row.get("StdErr") != expected_output:
        raise ValueError("Slurm held preflight output path mismatch")
    if require_held and (
        row.get("JobState") != "PENDING" or row.get("Reason") != "JobHeldUser"
    ):
        raise ValueError("Phase-2C preflight job is not held by the submitting user")
    row = dict(row)
    row["ExpectedAccount"] = FORMAL_ACCOUNT
    row["ObservedAccount"] = row["Account"]
    row["AccountComparison"] = comparison
    return row


def _validate_offline_held_snapshot(
    snapshot: Any,
    *,
    authorization: Mapping[str, Any],
    twin: Mapping[str, Any],
    job_id: str,
) -> dict[str, str]:
    """Validate a copied raw scontrol observation without scheduler access."""

    stdout = _validate_scontrol_snapshot(snapshot, job_id=job_id)
    rows = [row for row in _parse_scontrol(stdout) if row.get("JobId") == job_id]
    if len(rows) != 1:
        raise ValueError("offline scontrol snapshot lacks one exact preflight job")
    row = rows[0]
    scheduler = require_dict(authorization, "scheduler")
    launcher = require_dict(scheduler, "launcher")
    twin_directory = str(twin.get("directory", ""))
    output_pattern = scheduler.get("output_pattern")
    if (
        not twin_directory
        or not isinstance(output_pattern, str)
        or output_pattern.count("%j") != 1
    ):
        raise ValueError("offline scontrol snapshot authority is incomplete")
    expected = {
        "JobName": scheduler.get("job_name"),
        "Command": str(Path(twin_directory) / str(launcher.get("path", ""))),
        "WorkDir": twin_directory,
        "Requeue": "0",
        "TimeLimit": FORMAL_TIME_LIMIT,
        "NumCPUs": "1",
        "NumTasks": "1",
        "CPUs/Task": "1",
        "MinMemoryNode": FORMAL_MEMORY,
        "StdOut": output_pattern.replace("%j", job_id),
        "StdErr": output_pattern.replace("%j", job_id),
        "JobState": "PENDING",
        "Reason": "JobHeldUser",
    }
    if any(row.get(key) != value for key, value in expected.items()):
        raise ValueError("offline held scontrol snapshot identity mismatch")
    if row.get("ArrayJobId") not in {None, "", "N/A"} or row.get(
        "ArrayTaskId"
    ) not in {None, "", "N/A"}:
        raise ValueError("offline scontrol snapshot unexpectedly contains an array")
    observed_account = row.get("Account", "")
    comparison = _account_comparison(observed_account)
    value = dict(row)
    value["ExpectedAccount"] = FORMAL_ACCOUNT
    value["ObservedAccount"] = observed_account
    value["AccountComparison"] = comparison
    return value


def query_and_verify_held(
    context: PreflightContext,
    *,
    authorization_hash: str,
    actor: str,
    runner: CommandRunner = _run,
) -> dict[str, str]:
    authorization = load_authorization(context, authorization_hash)
    state = preflight_state(context, authorization_hash)
    if state.status != "submitted-held" or state.job_id is None:
        raise ValueError("held verification requires one submitted-held preflight")
    command = ["scontrol", "show", "job", state.job_id, "-o"]
    result = _run_with(
        runner,
        command,
        cwd=context.directory,
    )
    snapshot = _scontrol_snapshot(command, result)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "scontrol show job failed")
    try:
        row = validate_held_row(
            context,
            authorization,
            job_id=state.job_id,
            text=result.stdout,
        )
    except ValueError as exc:
        append_event(
            context,
            authorization_hash,
            "verification-failed",
            {
                "job_id": state.job_id,
                "reason": str(exc),
                "held_scontrol_snapshot": snapshot,
            },
            actor=actor,
        )
        raise
    append_event(
        context,
        authorization_hash,
        "held-verified",
        {
            "job_id": state.job_id,
            "job_name": row["JobName"],
            "expected_account": FORMAL_ACCOUNT,
            "observed_account": row["ObservedAccount"],
            "account_comparison": row["AccountComparison"],
            "state": "PENDING",
            "reason": "JobHeldUser",
            "held_scontrol_snapshot": snapshot,
        },
        actor=actor,
    )
    return row


def release_command(state: PreflightState) -> list[str]:
    if state.job_id is None:
        raise ValueError("Phase-2C preflight has no scheduler job to release")
    return ["scontrol", "release", state.job_id]


def preview_release_preflight(
    context: PreflightContext,
    *,
    authorization_hash: str,
    runner: CommandRunner = _run,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Re-read the held identity for release without changing the journal."""

    authorization = load_authorization(context, authorization_hash)
    state = preflight_state(context, authorization_hash)
    if state.status != "verified-held" or state.job_id is None:
        raise ValueError("only a separately verified held preflight may be released")
    command = ["scontrol", "show", "job", state.job_id, "-o"]
    shown = _run_with(runner, command, cwd=context.directory)
    if shown.returncode:
        raise ValueError(shown.stderr.strip() or "scontrol show job failed")
    row = validate_held_row(
        context,
        authorization,
        job_id=state.job_id,
        text=shown.stdout,
        require_held=True,
    )
    return row, _scontrol_snapshot(command, shown)


def release_preflight(
    context: PreflightContext,
    *,
    authorization_hash: str,
    actor: str,
    runner: CommandRunner = _run,
) -> str:
    state = preflight_state(context, authorization_hash)
    if state.status != "verified-held" or state.job_id is None:
        raise ValueError("only a separately verified held preflight may be released")
    try:
        _row, pre_release_snapshot = preview_release_preflight(
            context,
            authorization_hash=authorization_hash,
            runner=runner,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        append_event(
            context,
            authorization_hash,
            "release-ambiguous",
            {
                "job_id": state.job_id,
                "reason": str(exc) or type(exc).__name__,
            },
            actor=actor,
        )
        raise ValueError(
            "preflight held identity changed before release; do not submit again"
        ) from exc
    command = release_command(state)
    try:
        result = _run_with(runner, command, cwd=context.directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        append_event(
            context,
            authorization_hash,
            "release-ambiguous",
            {
                "job_id": state.job_id,
                "reason": type(exc).__name__,
                "pre_release_scontrol_snapshot": pre_release_snapshot,
            },
            actor=actor,
        )
        raise ValueError("preflight release outcome is ambiguous; do not submit again") from exc
    if result.returncode:
        append_event(
            context,
            authorization_hash,
            "release-ambiguous",
            {
                "job_id": state.job_id,
                "return_code": result.returncode,
                "stderr": result.stderr[-1000:],
                "pre_release_scontrol_snapshot": pre_release_snapshot,
            },
            actor=actor,
        )
        raise ValueError("preflight release outcome is ambiguous; do not submit again")
    append_event(
        context,
        authorization_hash,
        "job-released",
        {
            "job_id": state.job_id,
            "pre_release_scontrol_snapshot": pre_release_snapshot,
        },
        actor=actor,
    )
    return state.job_id


def _query_matching_jobs(
    context: PreflightContext,
    *,
    runner: CommandRunner,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    commands = {
        "squeue": [
            "squeue",
            "-h",
            "-o",
            "%A|%j|%a|%T|%r",
            "--name",
            context.job_name,
        ],
        "sacct": [
            "sacct",
            "-n",
            "-P",
            "-X",
            "--name",
            context.job_name,
            "--format=JobID,JobIDRaw,JobName,State,ExitCode",
        ],
    }
    raw: dict[str, str] = {}
    matches: dict[str, dict[str, str]] = {}
    for name, command in commands.items():
        result = _run_with(runner, command, cwd=context.directory)
        if result.returncode:
            raise ValueError(f"{name} query failed during Phase-2C reconciliation")
        raw[name] = result.stdout
        for line in result.stdout.splitlines():
            values = line.split("|")
            if name == "squeue":
                if len(values) < 2:
                    continue
                job_id, job_name = values[:2]
            else:
                if len(values) < 3:
                    continue
                job_id, _raw_id, job_name = values[:3]
            parent = job_id.split(".", 1)[0].split("_", 1)[0]
            if job_name == context.job_name and JOB_ID_RE.fullmatch(parent):
                match = matches.setdefault(
                    parent, {"job_id": parent, "job_name": job_name}
                )
                if name == "sacct" and len(values) >= 5 and job_id == parent:
                    match["observed_state"] = _base_state(values[3])
                    match["exit_code"] = values[4]
    return matches, raw


def reconcile_preflight(
    context: PreflightContext,
    *,
    authorization_hash: str,
    actor: str,
    runner: CommandRunner = _run,
) -> str:
    authorization = load_authorization(context, authorization_hash)
    state = preflight_state(context, authorization_hash)
    if state.status not in {"submission-ambiguous", "release-ambiguous"}:
        raise ValueError("Phase-2C preflight is not in an ambiguous scheduler state")
    matches, raw = _query_matching_jobs(context, runner=runner)
    if len(matches) > 1:
        append_event(
            context,
            authorization_hash,
            "permanently-ambiguous",
            {
                "job_ids": sorted(matches, key=int),
                "squeue_psv": raw["squeue"],
                "sacct_psv": raw["sacct"],
            },
            actor=actor,
        )
        raise ValueError("multiple exact preflight jobs; twin is permanently quarantined")
    if not matches:
        kind = "scheduler-observation" if state.status == "submission-ambiguous" else "release-observation"
        append_event(
            context,
            authorization_hash,
            kind,
            {
                "match_count": 0,
                "squeue_psv": raw["squeue"],
                "sacct_psv": raw["sacct"],
            },
            actor=actor,
        )
        return "no exact scheduler match; state remains quarantined"
    job_id = next(iter(matches))
    if state.job_id is not None and state.job_id != job_id:
        append_event(
            context,
            authorization_hash,
            "permanently-ambiguous",
            {"job_ids": [state.job_id, job_id]},
            actor=actor,
        )
        raise ValueError("reconciliation changed the Phase-2C preflight job identity")
    shown = _run_with(
        runner, ["scontrol", "show", "job", job_id, "-o"], cwd=context.directory
    )
    if shown.returncode:
        observed = matches[job_id].get("observed_state")
        if state.status == "release-ambiguous" and observed in TERMINAL_STATES:
            append_event(
                context,
                authorization_hash,
                "job-released",
                {
                    "job_id": job_id,
                    "reconciled_from_terminal_accounting": True,
                    "observed_state": observed,
                    "exit_code": matches[job_id].get("exit_code"),
                },
                actor=actor,
            )
            return f"terminal preflight job reconciled after release: {job_id}"
        kind = "scheduler-observation" if state.status == "submission-ambiguous" else "release-observation"
        append_event(
            context,
            authorization_hash,
            kind,
            {"job_id": job_id, "scontrol_return_code": shown.returncode},
            actor=actor,
        )
        return "one exact job found but live identity is not yet observable"
    rows = [row for row in _parse_scontrol(shown.stdout) if row.get("JobId") == job_id]
    if len(rows) != 1:
        raise ValueError("reconciliation scontrol identity is not unique")
    row = rows[0]
    if row.get("JobState") == "PENDING" and row.get("Reason") == "JobHeldUser":
        row = validate_held_row(
            context, authorization, job_id=job_id, text=shown.stdout
        )
        if state.status == "submission-ambiguous":
            append_event(
                context,
                authorization_hash,
                "submitted-held",
                {"job_id": job_id, "reconciled": True},
                actor=actor,
            )
        else:
            append_event(
                context,
                authorization_hash,
                "held-verified",
                {
                    "job_id": job_id,
                    "job_name": row["JobName"],
                    "expected_account": FORMAL_ACCOUNT,
                    "observed_account": row["ObservedAccount"],
                    "account_comparison": row["AccountComparison"],
                    "state": "PENDING",
                    "reason": "JobHeldUser",
                    "held_scontrol_snapshot": _scontrol_snapshot(
                        ["scontrol", "show", "job", job_id, "-o"], shown
                    ),
                },
                actor=actor,
            )
        return f"held preflight job reconciled: {job_id}"
    if state.status == "release-ambiguous" and row.get("JobState") in (
        ACTIVE_STATES | TERMINAL_STATES
    ) and row.get("Reason") != "JobHeldUser":
        _account_comparison(row.get("Account", ""))
        if row.get("JobName") != context.job_name:
            raise ValueError("reconciled released preflight job name mismatch")
        append_event(
            context,
            authorization_hash,
            "job-released",
            {"job_id": job_id, "reconciled": True, "observed_state": row.get("JobState")},
            actor=actor,
        )
        return f"released preflight job reconciled: {job_id}"
    raise ValueError("exact preflight job is not in a safely reconcilable state")


def _base_state(value: str) -> str:
    state = value.strip().split("+", 1)[0]
    if state == "CANCELLED" or state.startswith("CANCELLED by "):
        return "CANCELLED"
    return state


def _parse_accounting_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        values = raw.split("|")
        if len(values) != 8:
            raise ValueError("invalid Phase-2C sacct row")
        rows.append(
            dict(
                zip(
                    (
                        "job_id",
                        "job_id_raw",
                        "job_name",
                        "state",
                        "exit_code",
                        "elapsed_raw",
                        "max_rss",
                        "max_vmsize",
                    ),
                    values,
                )
            )
        )
    return rows


def preview_terminal_accounting(
    context: PreflightContext,
    *,
    authorization_hash: str,
    actor: str,
    runner: CommandRunner = _run,
) -> dict[str, Any]:
    if not ACTOR_RE.fullmatch(actor):
        raise ValueError("Phase-2C preflight actor identity is invalid")
    state = preflight_state(context, authorization_hash)
    if state.status not in {"released-active", "closed-failed"} or state.job_id is None:
        raise ValueError(
            "accounting requires one released or identity-rejected terminal preflight"
        )
    sacct = _run_with(
        runner,
        [
            "sacct",
            "-n",
            "-P",
            "-j",
            state.job_id,
            "--format=JobID,JobIDRaw,JobName,State,ExitCode,ElapsedRaw,MaxRSS,MaxVMSize",
        ],
        cwd=context.directory,
    )
    squeue = _run_with(
        runner,
        ["squeue", "-h", "-j", state.job_id, "-o", "%A|%j|%T|%r"],
        cwd=context.directory,
    )
    if sacct.returncode or squeue.returncode:
        raise ValueError("cannot collect complete Phase-2C preflight accounting")
    if squeue.stdout.strip():
        raise ValueError("Phase-2C preflight job is still active")
    rows = _parse_accounting_rows(sacct.stdout)
    parents = [row for row in rows if row["job_id"] == state.job_id]
    if len(parents) != 1:
        raise ValueError("sacct lacks one exact Phase-2C preflight parent row")
    if any("_" in row["job_id"] for row in rows):
        raise ValueError("Phase-2C preflight accounting unexpectedly contains array rows")
    batch_rows = [row for row in rows if row["job_id"] == state.job_id + ".batch"]
    if len(batch_rows) != 1:
        raise ValueError("sacct lacks one exact Phase-2C preflight batch row")
    parent = parents[0]
    batch = batch_rows[0]
    terminal_state = _base_state(parent["state"])
    batch_state = _base_state(batch["state"])
    if terminal_state not in TERMINAL_STATES:
        raise ValueError("Phase-2C preflight accounting is not terminal")
    if parent["job_name"] != context.job_name:
        raise ValueError("Phase-2C preflight accounting job name mismatch")
    payload = {
        "schema_version": ACCOUNTING_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "created_by": actor,
        "twin_id": context.twin_id,
        "twin_hash": context.twin_hash,
        "production_equivalence_hash": context.production_equivalence_hash,
        "authorization_hash": authorization_hash,
        "job_id": state.job_id,
        "job_name": context.job_name,
        "terminal_state": terminal_state,
        "exit_code": parent["exit_code"],
        "accepted_terminal_success": (
            terminal_state == "COMPLETED"
            and parent["exit_code"] == "0:0"
            and batch_state == "COMPLETED"
            and batch["exit_code"] == "0:0"
        ),
        "batch_state": batch_state,
        "batch_exit_code": batch["exit_code"],
        "accounting_row_count": len(rows),
        "parent_job_id_raw": parent["job_id_raw"],
        "sacct_field_order": [
            "JobID",
            "JobIDRaw",
            "JobName",
            "State",
            "ExitCode",
            "ElapsedRaw",
            "MaxRSS",
            "MaxVMSize",
        ],
    }
    return {"payload": payload, "sacct_psv": sacct.stdout, "squeue_psv": squeue.stdout}


def _accounting_directory(context: PreflightContext, authorization_hash: str) -> Path:
    return context.control_root / "accounting" / authorization_hash[:12]


def freeze_terminal_accounting(
    context: PreflightContext,
    *,
    authorization_hash: str,
    actor: str,
    runner: CommandRunner = _run,
) -> dict[str, Any]:
    snapshot = preview_terminal_accounting(
        context,
        authorization_hash=authorization_hash,
        actor=actor,
        runner=runner,
    )
    target = _accounting_directory(context, authorization_hash)
    with control_lock(context):
        if target.exists() or target.is_symlink():
            raise ValueError("Phase-2C preflight accounting is already frozen")
        state = preflight_state(context, authorization_hash)
        if state.status not in {"released-active", "closed-failed"}:
            raise ValueError("Phase-2C preflight state changed before accounting freeze")
        temporary = Path(tempfile.mkdtemp(prefix=".accounting-", dir=target.parent))
        try:
            _write_exclusive_json(temporary / "accounting.json", snapshot["payload"])
            _write_exclusive_bytes(
                temporary / "sacct.psv", snapshot["sacct_psv"].encode("utf-8")
            )
            _write_exclusive_bytes(
                temporary / "squeue.psv", snapshot["squeue_psv"].encode("utf-8")
            )
            _write_checksum_manifest(temporary)
            _publish_directory(temporary, target)
            append_event(
                context,
                authorization_hash,
                "terminal-accounting-frozen",
                {
                    "job_id": snapshot["payload"]["job_id"],
                    "accounting_manifest_sha256": sha256_file(target / "SHA256SUMS"),
                    "accepted_terminal_success": snapshot["payload"][
                        "accepted_terminal_success"
                    ],
                },
                actor=actor,
                lock_held=True,
            )
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return snapshot["payload"]


def validate_frozen_accounting(
    context: PreflightContext, authorization_hash: str
) -> dict[str, Any]:
    root = _accounting_directory(context, authorization_hash)
    records = verify_checksum_manifest(root)
    if set(records) != {"accounting.json", "sacct.psv", "squeue.psv"}:
        raise ValueError("Phase-2C accounting file set mismatch")
    payload = load_json(root / "accounting.json")
    state = preflight_state(context, authorization_hash)
    rows = _parse_accounting_rows((root / "sacct.psv").read_text(encoding="utf-8"))
    parent_rows = [row for row in rows if row["job_id"] == state.job_id]
    batch_rows = [row for row in rows if row["job_id"] == f"{state.job_id}.batch"]
    terminal_event = state.events[-1] if state.events else {}
    terminal_payload = terminal_event.get("payload", {})
    if (
        state.status != "terminal-accounting-frozen"
        or len(parent_rows) != 1
        or len(batch_rows) != 1
        or any("_" in row["job_id"] for row in rows)
        or payload.get("schema_version") != ACCOUNTING_SCHEMA_VERSION
        or payload.get("twin_id") != context.twin_id
        or payload.get("twin_hash") != context.twin_hash
        or payload.get("production_equivalence_hash")
        != context.production_equivalence_hash
        or payload.get("authorization_hash") != authorization_hash
        or payload.get("job_id") != state.job_id
        or payload.get("job_name") != context.job_name
        or payload.get("terminal_state") not in TERMINAL_STATES
        or payload.get("batch_state") not in TERMINAL_STATES
        or not isinstance(payload.get("batch_exit_code"), str)
        or not isinstance(payload.get("accounting_row_count"), int)
        or payload.get("accounting_row_count") < 2
        or payload.get("accounting_row_count") != len(rows)
        or payload.get("terminal_state") != _base_state(parent_rows[0]["state"])
        or payload.get("exit_code") != parent_rows[0]["exit_code"]
        or payload.get("batch_state") != _base_state(batch_rows[0]["state"])
        or payload.get("batch_exit_code") != batch_rows[0]["exit_code"]
        or parent_rows[0]["job_name"] != context.job_name
        or not isinstance(payload.get("accepted_terminal_success"), bool)
        or payload.get("accepted_terminal_success") is not (
            payload.get("terminal_state") == "COMPLETED"
            and payload.get("exit_code") == "0:0"
            and payload.get("batch_state") == "COMPLETED"
            and payload.get("batch_exit_code") == "0:0"
        )
        or (root / "squeue.psv").read_text(encoding="utf-8").strip()
        or terminal_payload.get("job_id") != state.job_id
        or terminal_payload.get("accounting_manifest_sha256")
        != sha256_file(root / "SHA256SUMS")
        or terminal_payload.get("accepted_terminal_success")
        is not payload.get("accepted_terminal_success")
    ):
        raise ValueError("Phase-2C accounting semantic validation failed")
    return payload


def _raw_directory(context: PreflightContext) -> Path:
    return context.control_root / "raw" / context.job_name


def _read_container_report(path: Path) -> dict[str, bool]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2C container report is missing or unsafe")
    values: dict[str, bool] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        pieces = raw.split("\t")
        if (
            len(pieces) != 2
            or pieces[0] in values
            or pieces[1] not in {"true", "false"}
        ):
            raise ValueError("Phase-2C container report is invalid")
        values[pieces[0]] = pieces[1] == "true"
    if tuple(values) != CONTAINER_REPORT_KEYS:
        raise ValueError("Phase-2C container report key order is not exact")
    return values


def _container_contract_is_exact(
    contract: Any, *, report: Mapping[str, bool]
) -> bool:
    return (
        isinstance(contract, dict)
        and set(contract)
        == {
            "cleanenv",
            "containall",
            "no_home",
            "no_mount",
            "fixed_read_only_mounts",
            "external_read_write_mounts",
            "mountinfo_contract_version",
            "command_sha256",
            "report",
        }
        and contract.get("cleanenv") is True
        and contract.get("containall") is True
        and contract.get("no_home") is True
        and contract.get("no_mount") == NO_MOUNT_CLASSES
        and contract.get("fixed_read_only_mounts") == 5
        and contract.get("external_read_write_mounts") == 1
        and contract.get("mountinfo_contract_version")
        == MOUNTINFO_CONTRACT_VERSION
        and _is_sha256(contract.get("command_sha256"))
        and contract.get("report") == dict(report)
        and set(report) == set(CONTAINER_REPORT_KEYS)
        and all(value is True for value in report.values())
    )


def validate_raw_preflight_result(
    context: PreflightContext, *, job_id: str
) -> dict[str, Any]:
    root = _raw_directory(context)
    records = verify_checksum_manifest(root)
    if set(records) != {
        "challenge.bin",
        "container-report.tsv",
        "probe_result.json",
        "roundtrip.bin",
        "stdout.txt",
        "stderr.txt",
    }:
        raise ValueError("Phase-2C raw preflight file set mismatch")
    payload = load_json(root / "probe_result.json")
    runtime_identity = payload.get("runtime_identity", {})
    container_contract = payload.get("container_contract", {})
    report = (
        container_contract.get("report", {})
        if isinstance(container_contract, dict)
        else {}
    )
    report_values = _read_container_report(root / "container-report.tsv")
    marker_path = context.directory / ".phase2c-preflight-closed.json"
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("Phase-2C preflight closure marker is missing or unsafe")
    marker_status = marker_path.stat()
    marker = load_json(marker_path)
    expected_marker = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "created_at_utc": marker.get("created_at_utc"),
        "twin_id": context.twin_id,
        "twin_hash": context.twin_hash,
        "production_equivalence_hash": context.production_equivalence_hash,
        "slurm_job_id": job_id,
        "slurm_job_name": context.job_name,
        "raw_result_sha256": sha256_file(root / "probe_result.json"),
        "execution_closed": True,
        "future_execution_role": "clean-execution-v6",
        "events_consumed": 0,
        "production_seeds_consumed": 0,
    }
    required_true = {
        "probe_passed",
        "apptainer_invoked",
        "twin_execution_closed",
    }
    if (
        set(payload) != RAW_RESULT_KEYS
        or payload.get("schema_version") != RAW_RESULT_SCHEMA_VERSION
        or payload.get("twin_id") != context.twin_id
        or payload.get("twin_hash") != context.twin_hash
        or payload.get("production_equivalence_hash")
        != context.production_equivalence_hash
        or payload.get("slurm_job_id") != job_id
        or payload.get("slurm_job_name") != context.job_name
        or any(payload.get(key) is not True for key in required_true)
        or payload.get("geant4_invoked") is not False
        or payload.get("events_consumed") != 0
        or payload.get("production_seeds_consumed") != 0
        or not isinstance(payload.get("test_mode"), bool)
        or not isinstance(payload.get("created_at_utc"), str)
        or payload.get("accepted_compute_preflight_evidence")
        is not (payload.get("test_mode") is False)
        or payload.get("return_code") != 0
        or not isinstance(runtime_identity, dict)
        or not isinstance(runtime_identity.get("apptainer_path"), str)
        or not runtime_identity.get("apptainer_path")
        or not _is_sha256(runtime_identity.get("apptainer_sha256"))
        or not isinstance(runtime_identity.get("apptainer_version"), str)
        or not runtime_identity.get("apptainer_version")
        or not isinstance(report, dict)
        or not _container_contract_is_exact(
            container_contract, report=report_values
        )
        or report != report_values
        or payload.get("challenge_sha256")
        != sha256_file(root / "challenge.bin")
        or payload.get("roundtrip_sha256")
        != sha256_file(root / "roundtrip.bin")
        or payload.get("challenge_sha256") != payload.get("roundtrip_sha256")
        or marker != expected_marker
        or not isinstance(marker.get("created_at_utc"), str)
        or stat.S_IMODE(marker_status.st_mode) != 0o400
        or marker_status.st_nlink != 1
    ):
        raise ValueError("Phase-2C raw preflight semantic validation failed")
    return payload


def _copy_file(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"unsafe evidence input: {source}")
    _write_exclusive_bytes(target, source.read_bytes())


def _build_phase2c_preflight_evidence_payload(
    context: PreflightContext,
    *,
    authorization_hash: str,
    allow_test_mode: bool = False,
) -> dict[str, Any]:
    failure_marker = context.directory / FAILURE_MARKER_NAME
    if failure_marker.exists() or failure_marker.is_symlink():
        raise ValueError("failed Phase-2C twin cannot produce success evidence")
    authorization = load_authorization(context, authorization_hash)
    accounting = validate_frozen_accounting(context, authorization_hash)
    if accounting.get("accepted_terminal_success") is not True:
        raise ValueError("successful Phase-2C evidence requires COMPLETED / 0:0")
    job_id = str(accounting["job_id"])
    raw = validate_raw_preflight_result(context, job_id=job_id)
    test_mode = bool(raw.get("test_mode", False))
    if test_mode and not allow_test_mode:
        raise ValueError("test-mode Phase-2C preflight cannot become formal evidence")
    journal_events = read_events(context, authorization_hash)
    held_events = [
        event for event in journal_events
        if event.get("event_type") == "held-verified"
    ]
    if not held_events or journal_events[-1].get("event_type") != (
        "terminal-accounting-frozen"
    ):
        raise ValueError("successful Phase-2C evidence lacks its complete journal")
    journal_root = _event_root(context, authorization_hash)
    journal_records = _recursive_records(journal_root)
    journal_manifest_text = "".join(
        f"{digest}  {name}\n"
        for name, digest in sorted(journal_records.items())
    )
    closure_path = context.directory / ".phase2c-preflight-closed.json"
    if closure_path.is_symlink() or not closure_path.is_file():
        raise ValueError("Phase-2C twin closure marker is missing")
    twin = {
        "directory": str(context.directory),
        "twin_id": context.twin_id,
        "twin_hash": context.twin_hash,
        "static_manifest_sha256": _static_manifest_sha(context),
    }
    held_payload = require_dict(held_events[-1], "payload")
    held_snapshot_row = _validate_offline_held_snapshot(
        held_payload.get("held_scontrol_snapshot"),
        authorization=authorization,
        twin=twin,
        job_id=job_id,
    )
    pre_release_payloads = [
        require_dict(event, "payload")
        for event in journal_events
        if event.get("event_type") in {"job-released", "release-ambiguous"}
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("pre_release_scontrol_snapshot") is not None
    ]
    if not pre_release_payloads:
        raise ValueError("successful Phase-2C evidence lacks pre-release identity")
    pre_release_snapshot_row = _validate_offline_held_snapshot(
        pre_release_payloads[-1].get("pre_release_scontrol_snapshot"),
        authorization=authorization,
        twin=twin,
        job_id=job_id,
    )
    predecessor = context.manifest.get("predecessor_v5", {})
    accepted_r3 = (
        predecessor.get("accepted_r3_evidence", {})
        if isinstance(predecessor, dict)
        else {}
    )
    v5_evidence_hash = accepted_r3.get(
        "evidence_hash", context.manifest.get("v5_evidence_hash")
    )
    if not _is_sha256(v5_evidence_hash):
        raise ValueError("preflight twin lacks the accepted v5 evidence hash")
    scheduler = {
        "job_id": job_id,
        "job_name": context.job_name,
        "account": FORMAL_ACCOUNT,
        "observed_account": raw.get("scheduler_account", FORMAL_ACCOUNT.lower()),
        "terminal_state": accounting["terminal_state"],
        "exit_code": accounting["exit_code"],
        "non_array": True,
        "initially_held": True,
    }
    observed_account = str(scheduler["observed_account"])
    account_comparison = _account_comparison(observed_account)
    if (
        set(held_payload)
        != {
            "job_id",
            "job_name",
            "expected_account",
            "observed_account",
            "account_comparison",
            "state",
            "reason",
            "held_scontrol_snapshot",
        }
        or held_payload.get("job_id") != job_id
        or held_payload.get("job_name") != context.job_name
        or held_payload.get("expected_account") != FORMAL_ACCOUNT
        or held_payload.get("observed_account") != observed_account
        or held_payload.get("account_comparison") != account_comparison
        or held_payload.get("state") != "PENDING"
        or held_payload.get("reason") != "JobHeldUser"
        or held_snapshot_row.get("ObservedAccount") != observed_account
        or held_snapshot_row.get("AccountComparison") != account_comparison
        or pre_release_snapshot_row.get("ObservedAccount") != observed_account
        or pre_release_snapshot_row.get("AccountComparison") != account_comparison
    ):
        raise ValueError("successful Phase-2C evidence scheduler lineage mismatch")
    identity = {
        "schema_version": PREFLIGHT_EVIDENCE_SCHEMA_VERSION,
        "test_mode": test_mode,
        "accepted_compute_preflight_evidence": not test_mode,
        "evidence_id": None,
        "evidence_hash": None,
        "created_at_utc": raw.get("created_at_utc"),
        "twin": twin,
        "v5_evidence_hash": v5_evidence_hash,
        "production_equivalence_hash": context.production_equivalence_hash,
        "authorization_hash": authorization_hash,
        "authorization_manifest_sha256": sha256_file(
            _authorization_directory(context, authorization_hash) / "SHA256SUMS"
        ),
        "scheduler": scheduler,
        "runtime_boundary": {
            "apptainer_invoked": True,
            "geant4_invoked": False,
            "runtime_identity": raw.get("runtime_identity"),
            "container_contract": raw.get("container_contract"),
        },
        "compute_preflight": {
            "job_id": job_id,
            "apptainer_path": raw.get("runtime_identity", {}).get(
                "apptainer_path"
            ),
            "apptainer_sha256": raw.get("runtime_identity", {}).get(
                "apptainer_sha256"
            ),
            "apptainer_version": raw.get("runtime_identity", {}).get(
                "apptainer_version"
            ),
            "held_identity_event_sha256": held_events[-1]["event_sha256"],
            "accounting_manifest_sha256": sha256_file(
                _accounting_directory(context, authorization_hash) / "SHA256SUMS"
            ),
        },
        "consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "twin_execution_closed": True,
        "source_artifacts": {
            "raw_manifest_sha256": sha256_file(
                _raw_directory(context) / "SHA256SUMS"
            ),
            "accounting_manifest_sha256": sha256_file(
                _accounting_directory(context, authorization_hash) / "SHA256SUMS"
            ),
            "journal_manifest_sha256": hashlib.sha256(
                journal_manifest_text.encode("utf-8")
            ).hexdigest(),
            "twin_closure_sha256": sha256_file(closure_path),
        },
    }
    digest_value = dict(identity)
    digest_value["evidence_hash"] = None
    digest_value["evidence_id"] = None
    evidence_hash = hashlib.sha256(canonical_json(digest_value)).hexdigest()
    identity["evidence_hash"] = evidence_hash
    identity["evidence_id"] = "sm-v1-phase2c-preflight-" + evidence_hash[:12]
    # This is the same complete semantic payload that will be written by the
    # sealer.  Every source bundle, journal snapshot, closure marker, runtime
    # contract, and cross-object hash above has already been validated.  The
    # preview path can therefore exercise the full would-be evidence without
    # creating even a temporary publication.
    return identity


def preview_phase2c_preflight_evidence(
    context: PreflightContext,
    *,
    authorization_hash: str,
    allow_test_mode: bool = False,
) -> dict[str, Any]:
    return _build_phase2c_preflight_evidence_payload(
        context,
        authorization_hash=authorization_hash,
        allow_test_mode=allow_test_mode,
    )


def seal_phase2c_preflight_evidence(
    context: PreflightContext,
    *,
    authorization_hash: str,
    allow_test_mode: bool = False,
) -> Path:
    identity = _build_phase2c_preflight_evidence_payload(
        context,
        authorization_hash=authorization_hash,
        allow_test_mode=allow_test_mode,
    )
    journal_root = _event_root(context, authorization_hash)
    closure_path = context.directory / ".phase2c-preflight-closed.json"
    target = context.control_root / "evidence" / identity["evidence_id"]
    with control_lock(context):
        if target.exists() or target.is_symlink():
            raise ValueError("Phase-2C preflight evidence target already exists")
        temporary = Path(tempfile.mkdtemp(prefix=".evidence-", dir=target.parent))
        try:
            _write_exclusive_json(temporary / "evidence.json", identity)
            for directory_name, source in (
                ("authorization", _authorization_directory(context, authorization_hash)),
                ("accounting", _accounting_directory(context, authorization_hash)),
                ("raw", _raw_directory(context)),
            ):
                destination = temporary / directory_name
                destination.mkdir(mode=0o700)
                for relative in verify_checksum_manifest(source):
                    destination_file = destination / relative
                    destination_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    _copy_file(source / relative, destination_file)
                _copy_file(source / "SHA256SUMS", destination / "SHA256SUMS")
            journal_destination = temporary / "journal"
            journal_destination.mkdir(mode=0o700)
            for event_path in sorted(journal_root.iterdir()):
                _copy_file(event_path, journal_destination / event_path.name)
            _write_checksum_manifest(journal_destination)
            _copy_file(closure_path, temporary / "twin_closure.json")
            _write_checksum_manifest(temporary)
            _publish_directory(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    validate_phase2c_preflight_evidence(
        target, expected_twin=context.twin, allow_test_mode=allow_test_mode
    )
    return target


def _expected_twin_values(expected_twin: Any) -> tuple[str, str, str, Path]:
    manifest = dict(expected_twin.manifest)
    twin_id = manifest.get("twin_id")
    twin_hash = manifest.get("twin_hash")
    equivalence = manifest.get("production_equivalence_hash")
    directory = Path(expected_twin.directory).resolve()
    if not isinstance(twin_id, str) or not _is_sha256(twin_hash) or not _is_sha256(equivalence):
        raise ValueError("expected Phase-2C twin identity is invalid")
    return twin_id, twin_hash, equivalence, directory


def _validate_evidence_journal(
    root: Path, *, authorization_hash: str, job_id: str
) -> tuple[dict[str, Any], ...]:
    records = verify_checksum_manifest(root)
    if not records or any(not name.endswith(".json") for name in records):
        raise ValueError("Phase-2C evidence journal file set is invalid")
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    previous_type = "prepared"
    observed_job_id: str | None = None
    for sequence, path in enumerate(sorted(root.glob("*.json")), 1):
        match = EVENT_FILENAME_RE.fullmatch(path.name)
        value = load_json(path)
        event_type = value.get("event_type")
        candidate = require_dict(value, "payload").get("job_id")
        if candidate is not None:
            if candidate != job_id:
                raise ValueError("Phase-2C evidence journal changes the job ID")
            observed_job_id = candidate
        if (
            match is None
            or int(match.group("sequence")) != sequence
            or value.get("schema_version") != EVENT_SCHEMA_VERSION
            or value.get("sequence") != sequence
            or value.get("authorization_hash") != authorization_hash
            or value.get("previous_event_sha256") != previous_hash
            or event_type not in EVENT_TRANSITIONS.get(previous_type, set())
            or value.get("event_sha256") != _semantic_hash(value, "event_sha256")
            or match.group("kind") != event_type
            or match.group("digest") != str(value.get("event_sha256", ""))[:12]
            or not isinstance(value.get("created_at_utc"), str)
            or not ACTOR_RE.fullmatch(str(value.get("actor", "")))
        ):
            raise ValueError("Phase-2C evidence journal chain is invalid")
        events.append(value)
        previous_hash = value["event_sha256"]
        previous_type = event_type
    if (
        len(events) != len(records)
        or observed_job_id != job_id
        or events[-1].get("event_type") != "terminal-accounting-frozen"
    ):
        raise ValueError("Phase-2C evidence journal is incomplete")
    return tuple(events)


def validate_phase2c_preflight_evidence(
    directory: Path,
    *,
    expected_twin: Any | None = None,
    allow_test_mode: bool = False,
) -> dict[str, Any]:
    root = _require_regular_directory(directory, "Phase-2C preflight evidence")
    records = verify_checksum_manifest(root)
    required = {
        "evidence.json",
        "twin_closure.json",
        "authorization/authorization.json",
        "authorization/SHA256SUMS",
        "accounting/accounting.json",
        "accounting/sacct.psv",
        "accounting/squeue.psv",
        "accounting/SHA256SUMS",
        "raw/probe_result.json",
        "raw/challenge.bin",
        "raw/container-report.tsv",
        "raw/roundtrip.bin",
        "raw/stdout.txt",
        "raw/stderr.txt",
        "raw/SHA256SUMS",
        "journal/SHA256SUMS",
    }
    journal_files = {
        name for name in records
        if name.startswith("journal/") and name != "journal/SHA256SUMS"
    }
    if (
        set(records) != required | journal_files
        or not journal_files
        or any(not name.endswith(".json") for name in journal_files)
    ):
        raise ValueError("Phase-2C preflight evidence file set mismatch")
    for name in ("authorization", "accounting", "raw", "journal"):
        verify_checksum_manifest(root / name)
    payload = load_json(root / "evidence.json")
    evidence_hash = payload.get("evidence_hash")
    check = dict(payload)
    check["evidence_hash"] = None
    check["evidence_id"] = None
    test_mode = payload.get("test_mode")
    runtime = payload.get("runtime_boundary", {})
    consumption = payload.get("consumption", {})
    compute = payload.get("compute_preflight", {})
    twin = payload.get("twin", {})
    scheduler = payload.get("scheduler", {})
    source_artifacts = payload.get("source_artifacts", {})
    job_id = str(scheduler.get("job_id", ""))
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("Phase-2C evidence scheduler job ID is invalid")
    journal_events = _validate_evidence_journal(
        root / "journal",
        authorization_hash=str(payload.get("authorization_hash", "")),
        job_id=job_id,
    )
    held_events = [
        event for event in journal_events
        if event.get("event_type") == "held-verified"
    ]
    if not held_events:
        raise ValueError("Phase-2C evidence journal lacks held verification")
    authorization = load_json(root / "authorization" / "authorization.json")
    accounting = load_json(root / "accounting" / "accounting.json")
    raw = load_json(root / "raw" / "probe_result.json")
    closure = load_json(root / "twin_closure.json")
    sacct_rows = _parse_accounting_rows(
        (root / "accounting" / "sacct.psv").read_text(encoding="utf-8")
    )
    parent_rows = [row for row in sacct_rows if row["job_id"] == job_id]
    batch_rows = [row for row in sacct_rows if row["job_id"] == job_id + ".batch"]
    if len(parent_rows) != 1 or len(batch_rows) != 1:
        raise ValueError("Phase-2C evidence accounting rows are incomplete")
    terminal_payload = require_dict(journal_events[-1], "payload")
    closure_expected = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "created_at_utc": closure.get("created_at_utc"),
        "twin_id": twin.get("twin_id"),
        "twin_hash": twin.get("twin_hash"),
        "production_equivalence_hash": payload.get(
            "production_equivalence_hash"
        ),
        "slurm_job_id": job_id,
        "slurm_job_name": scheduler.get("job_name"),
        "raw_result_sha256": sha256_file(
            root / "raw" / "probe_result.json"
        ),
        "execution_closed": True,
        "future_execution_role": "clean-execution-v6",
        "events_consumed": 0,
        "production_seeds_consumed": 0,
    }
    authorization_hash = payload.get("authorization_hash")
    authorization_twin = authorization.get("twin", {})
    authorization_scheduler = authorization.get("scheduler", {})
    authorization_authority = authorization.get("authority", {})
    authorization_semantic = dict(authorization)
    authorization_semantic.pop("created_at_utc", None)
    authorization_semantic["authorization_hash"] = None
    authorization_semantic["authorization_id"] = None
    authorization_expected_hash = hashlib.sha256(
        canonical_json(authorization_semantic)
    ).hexdigest()
    held_event = held_events[-1]
    held_payload = require_dict(held_event, "payload")
    held_snapshot_row = _validate_offline_held_snapshot(
        held_payload.get("held_scontrol_snapshot"),
        authorization=authorization,
        twin=twin,
        job_id=job_id,
    )
    pre_release_payloads = [
        require_dict(event, "payload")
        for event in journal_events
        if event.get("event_type") in {"job-released", "release-ambiguous"}
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("pre_release_scontrol_snapshot") is not None
    ]
    if not pre_release_payloads:
        raise ValueError("Phase-2C evidence journal lacks its pre-release snapshot")
    pre_release_snapshot_row = _validate_offline_held_snapshot(
        pre_release_payloads[-1].get("pre_release_scontrol_snapshot"),
        authorization=authorization,
        twin=twin,
        job_id=job_id,
    )
    accounting_manifest_sha = sha256_file(root / "accounting" / "SHA256SUMS")
    raw_manifest_sha = sha256_file(root / "raw" / "SHA256SUMS")
    journal_manifest_sha = sha256_file(root / "journal" / "SHA256SUMS")
    closure_sha = sha256_file(root / "twin_closure.json")
    parent = parent_rows[0]
    batch = batch_rows[0]
    parent_state = _base_state(parent["state"])
    batch_state = _base_state(batch["state"])
    accepted_terminal_success = (
        parent_state == "COMPLETED"
        and parent["exit_code"] == "0:0"
        and batch_state == "COMPLETED"
        and batch["exit_code"] == "0:0"
    )
    raw_runtime = raw.get("runtime_identity", {})
    raw_container = raw.get("container_contract", {})
    raw_report = (
        raw_container.get("report", {})
        if isinstance(raw_container, dict)
        else {}
    )
    report_values = _read_container_report(
        root / "raw" / "container-report.tsv"
    )
    expected_authorization_scheduler = {
        "account": FORMAL_ACCOUNT,
        "account_read_policy": "exact-or-ascii-lowercase",
        "array": False,
        "initially_held": True,
        "no_requeue": True,
        "export_mode": "NONE",
        "job_name": scheduler.get("job_name"),
        "output_pattern": authorization_scheduler.get("output_pattern"),
        "resources": {
            "nodes": 1,
            "ntasks": 1,
            "cpus_per_task": FORMAL_CPUS,
            "memory": FORMAL_MEMORY,
            "time_limit": FORMAL_TIME_LIMIT,
        },
        "launcher": authorization_scheduler.get("launcher"),
    }
    if (
        set(payload) != EVIDENCE_PAYLOAD_KEYS
        or set(twin)
        != {"directory", "twin_id", "twin_hash", "static_manifest_sha256"}
        or set(scheduler)
        != {
            "job_id",
            "job_name",
            "account",
            "observed_account",
            "terminal_state",
            "exit_code",
            "non_array",
            "initially_held",
        }
        or set(runtime)
        != {
            "apptainer_invoked",
            "geant4_invoked",
            "runtime_identity",
            "container_contract",
        }
        or set(compute)
        != {
            "job_id",
            "apptainer_path",
            "apptainer_sha256",
            "apptainer_version",
            "held_identity_event_sha256",
            "accounting_manifest_sha256",
        }
        or set(source_artifacts)
        != {
            "raw_manifest_sha256",
            "accounting_manifest_sha256",
            "journal_manifest_sha256",
            "twin_closure_sha256",
        }
        or payload.get("schema_version") != PREFLIGHT_EVIDENCE_SCHEMA_VERSION
        or not _is_sha256(evidence_hash)
        or hashlib.sha256(canonical_json(check)).hexdigest() != evidence_hash
        or payload.get("evidence_id") != "sm-v1-phase2c-preflight-" + evidence_hash[:12]
        or root.name != payload.get("evidence_id")
        or not isinstance(test_mode, bool)
        or payload.get("accepted_compute_preflight_evidence") is not (not test_mode)
        or (test_mode and not allow_test_mode)
        or not _is_sha256(payload.get("v5_evidence_hash"))
        or not _is_sha256(payload.get("production_equivalence_hash"))
        or not _is_sha256(authorization_hash)
        or authorization.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION
        or authorization.get("authorization_hash") != authorization_hash
        or authorization_expected_hash != authorization_hash
        or authorization.get("authorization_id")
        != "sm-v1-phase2c-preflight-auth-" + str(authorization_hash)[:12]
        or not isinstance(authorization.get("created_at_utc"), str)
        or not ACTOR_RE.fullmatch(str(authorization.get("created_by", "")))
        or authorization_twin != twin
        or authorization.get("production_equivalence_hash")
        != payload.get("production_equivalence_hash")
        or authorization_scheduler != expected_authorization_scheduler
        or not isinstance(authorization_scheduler.get("output_pattern"), str)
        or not authorization_scheduler.get("output_pattern")
        or not isinstance(authorization_scheduler.get("launcher"), dict)
        or authorization_scheduler.get("launcher", {}).get("path")
        != FORMAL_LAUNCHER_RELATIVE
        or not _is_sha256(
            authorization_scheduler.get("launcher", {}).get("sha256")
        )
        or authorization_authority
        != {
            "purpose": "held-non-array-no-geant4-preflight-only",
            "production_intent_authorized": False,
            "production_submission_authorized": False,
            "separate_release_required": True,
            "automatic_retry_allowed": False,
        }
        or runtime.get("apptainer_invoked") is not True
        or runtime.get("geant4_invoked") is not False
        or runtime.get("runtime_identity") != raw_runtime
        or runtime.get("container_contract") != raw_container
        or consumption
        != {"events_consumed": 0, "production_seeds_consumed": 0}
        or compute.get("job_id") != scheduler.get("job_id")
        or not isinstance(compute.get("apptainer_path"), str)
        or not compute.get("apptainer_path")
        or not _is_sha256(compute.get("apptainer_sha256"))
        or not isinstance(compute.get("apptainer_version"), str)
        or not compute.get("apptainer_version")
        or not _is_sha256(compute.get("held_identity_event_sha256"))
        or not _is_sha256(compute.get("accounting_manifest_sha256"))
        or payload.get("authorization_manifest_sha256")
        != sha256_file(root / "authorization" / "SHA256SUMS")
        or source_artifacts.get("raw_manifest_sha256")
        != sha256_file(root / "raw" / "SHA256SUMS")
        or source_artifacts.get("accounting_manifest_sha256")
        != accounting_manifest_sha
        or source_artifacts.get("raw_manifest_sha256") != raw_manifest_sha
        or source_artifacts.get("journal_manifest_sha256")
        != journal_manifest_sha
        or source_artifacts.get("twin_closure_sha256") != closure_sha
        or compute.get("accounting_manifest_sha256")
        != source_artifacts.get("accounting_manifest_sha256")
        or compute.get("held_identity_event_sha256")
        != held_event.get("event_sha256")
        or set(held_payload)
        != {
            "job_id",
            "job_name",
            "expected_account",
            "observed_account",
            "account_comparison",
            "state",
            "reason",
            "held_scontrol_snapshot",
        }
        or held_payload.get("job_id") != job_id
        or held_payload.get("job_name") != scheduler.get("job_name")
        or held_payload.get("expected_account") != FORMAL_ACCOUNT
        or held_payload.get("observed_account") != scheduler.get("observed_account")
        or held_payload.get("account_comparison")
        != _account_comparison(str(scheduler.get("observed_account", "")))
        or held_payload.get("state") != "PENDING"
        or held_payload.get("reason") != "JobHeldUser"
        or held_snapshot_row.get("JobName") != scheduler.get("job_name")
        or held_snapshot_row.get("ObservedAccount")
        != scheduler.get("observed_account")
        or held_snapshot_row.get("AccountComparison")
        != _account_comparison(str(scheduler.get("observed_account", "")))
        or pre_release_snapshot_row.get("JobName") != scheduler.get("job_name")
        or pre_release_snapshot_row.get("ObservedAccount")
        != scheduler.get("observed_account")
        or pre_release_snapshot_row.get("AccountComparison")
        != _account_comparison(str(scheduler.get("observed_account", "")))
        or payload.get("twin_execution_closed") is not True
        or scheduler.get("terminal_state") != "COMPLETED"
        or scheduler.get("exit_code") != "0:0"
        or scheduler.get("non_array") is not True
        or scheduler.get("initially_held") is not True
        or scheduler.get("account") != FORMAL_ACCOUNT
        or scheduler.get("observed_account") != raw.get("scheduler_account")
        or not isinstance(twin.get("twin_id"), str)
        or not _is_sha256(twin.get("twin_hash"))
        or not _is_sha256(twin.get("static_manifest_sha256"))
        or accounting.get("schema_version") != ACCOUNTING_SCHEMA_VERSION
        or accounting.get("twin_id") != twin.get("twin_id")
        or accounting.get("twin_hash") != twin.get("twin_hash")
        or accounting.get("production_equivalence_hash")
        != payload.get("production_equivalence_hash")
        or accounting.get("authorization_hash") != authorization_hash
        or accounting.get("job_id") != job_id
        or accounting.get("job_name") != scheduler.get("job_name")
        or accounting.get("terminal_state") != parent_state
        or accounting.get("exit_code") != parent.get("exit_code")
        or accounting.get("batch_state") != batch_state
        or accounting.get("batch_exit_code") != batch.get("exit_code")
        or accounting.get("accepted_terminal_success")
        is not accepted_terminal_success
        or accepted_terminal_success is not True
        or accounting.get("accounting_row_count") != len(sacct_rows)
        or accounting.get("parent_job_id_raw") != parent.get("job_id_raw")
        or accounting.get("sacct_field_order")
        != [
            "JobID",
            "JobIDRaw",
            "JobName",
            "State",
            "ExitCode",
            "ElapsedRaw",
            "MaxRSS",
            "MaxVMSize",
        ]
        or any("_" in row["job_id"] for row in sacct_rows)
        or parent.get("job_name") != scheduler.get("job_name")
        or (root / "accounting" / "squeue.psv")
        .read_text(encoding="utf-8")
        .strip()
        or terminal_payload.get("job_id") != job_id
        or terminal_payload.get("accounting_manifest_sha256")
        != accounting_manifest_sha
        or terminal_payload.get("accepted_terminal_success") is not True
        or raw.get("schema_version") != RAW_RESULT_SCHEMA_VERSION
        or raw.get("test_mode") is not test_mode
        or raw.get("accepted_compute_preflight_evidence") is not (not test_mode)
        or raw.get("probe_passed") is not True
        or raw.get("apptainer_invoked") is not True
        or raw.get("geant4_invoked") is not False
        or raw.get("events_consumed") != 0
        or raw.get("production_seeds_consumed") != 0
        or raw.get("twin_execution_closed") is not True
        or raw.get("twin_id") != twin.get("twin_id")
        or raw.get("twin_hash") != twin.get("twin_hash")
        or raw.get("production_equivalence_hash")
        != payload.get("production_equivalence_hash")
        or raw.get("slurm_job_id") != job_id
        or raw.get("slurm_job_name") != scheduler.get("job_name")
        or raw.get("return_code") != 0
        or not isinstance(raw_runtime, dict)
        or compute.get("apptainer_path") != raw_runtime.get("apptainer_path")
        or compute.get("apptainer_sha256")
        != raw_runtime.get("apptainer_sha256")
        or compute.get("apptainer_version")
        != raw_runtime.get("apptainer_version")
        or not isinstance(raw_report, dict)
        or not _container_contract_is_exact(
            raw_container, report=report_values
        )
        or report_values != raw_report
        or raw.get("challenge_sha256")
        != sha256_file(root / "raw" / "challenge.bin")
        or raw.get("roundtrip_sha256")
        != sha256_file(root / "raw" / "roundtrip.bin")
        or raw.get("challenge_sha256") != raw.get("roundtrip_sha256")
        or closure != closure_expected
        or not isinstance(closure.get("created_at_utc"), str)
    ):
        raise ValueError("Phase-2C preflight evidence semantic validation failed")
    if expected_twin is not None:
        twin_id, twin_hash, equivalence, twin_dir = _expected_twin_values(expected_twin)
        expected = {
            "directory": str(twin_dir),
            "twin_id": twin_id,
            "twin_hash": twin_hash,
            "static_manifest_sha256": sha256_file(twin_dir / "STATIC_SHA256SUMS"),
        }
        if twin != expected or payload.get("production_equivalence_hash") != equivalence:
            raise ValueError("Phase-2C evidence does not bind the expected twin")
    return payload


def _failure_zero_match_observations(
    events: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    observations = [
        event
        for event in events
        if event.get("event_type") in {
            "scheduler-observation", "release-observation"
        }
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("match_count") == 0
    ]
    if len(observations) < 2:
        raise ValueError(
            "retryable zero-match failure needs two scheduler observations"
        )
    first, second = observations[-2:]
    try:
        first_time = datetime.fromisoformat(str(first["created_at_utc"]))
        second_time = datetime.fromisoformat(str(second["created_at_utc"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("zero-match observation timestamps are invalid") from exc
    if (
        first_time.tzinfo is None
        or second_time.tzinfo is None
        or (second_time - first_time).total_seconds()
        < ZERO_MATCH_REVIEW_INTERVAL_SECONDS
    ):
        raise ValueError(
            "zero-match observations must be separated by at least 10 minutes"
        )
    return first, second


def _failure_journal_records(
    root: Path, *, authorization_hash: str
) -> tuple[dict[str, Any], ...]:
    records = verify_checksum_manifest(root)
    if not records or any(not name.endswith(".json") for name in records):
        raise ValueError("Phase-2C failure journal file set is invalid")
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    previous_type = "prepared"
    for sequence, path in enumerate(sorted(root.glob("*.json")), 1):
        value = load_json(path)
        match = EVENT_FILENAME_RE.fullmatch(path.name)
        event_type = value.get("event_type")
        if (
            match is None
            or int(match.group("sequence")) != sequence
            or value.get("schema_version") != EVENT_SCHEMA_VERSION
            or value.get("sequence") != sequence
            or value.get("authorization_hash") != authorization_hash
            or value.get("previous_event_sha256") != previous_hash
            or event_type not in EVENT_TRANSITIONS.get(previous_type, set())
            or value.get("event_sha256") != _semantic_hash(value, "event_sha256")
            or match.group("kind") != event_type
            or match.group("digest") != str(value.get("event_sha256", ""))[:12]
        ):
            raise ValueError("Phase-2C failure journal chain is invalid")
        events.append(value)
        previous_hash = value["event_sha256"]
        previous_type = str(event_type)
    if len(events) != len(records):
        raise ValueError("Phase-2C failure journal coverage changed")
    return tuple(events)


def _failure_payload(
    context: PreflightContext,
    *,
    authorization_hash: str,
    reviewer: str,
    rationale: str,
    allow_test_mode: bool,
) -> tuple[dict[str, Any], dict[str, Path]]:
    if not ACTOR_RE.fullmatch(reviewer):
        raise ValueError("Phase-2C failure reviewer identity is invalid")
    if not rationale.strip() or len(rationale) > 2000:
        raise ValueError("Phase-2C failure rationale is missing or too long")
    authorization = load_authorization(context, authorization_hash)
    events = read_events(context, authorization_hash)
    if not events:
        raise ValueError("Phase-2C failure has no scheduler journal")
    state = preflight_state(context, authorization_hash)
    accounting_root = _accounting_directory(context, authorization_hash)
    accounting: dict[str, Any] | None = None
    if accounting_root.exists() or accounting_root.is_symlink():
        accounting = validate_frozen_accounting(context, authorization_hash)
    scheduler_job_ids: list[str]
    if accounting is not None:
        if accounting.get("accepted_terminal_success") is not False:
            raise ValueError("successful accounting cannot be sealed as failure")
        failure_class = "terminal-job-failure"
        retry_eligible = True
        scheduler_job_ids = [str(accounting["job_id"])]
    elif state.status in {"submission-ambiguous", "release-ambiguous"}:
        _failure_zero_match_observations(events)
        failure_class = "two-snapshot-zero-match-ambiguity"
        retry_eligible = True
        scheduler_job_ids = [state.job_id] if state.job_id is not None else []
    elif state.status == "closed-ambiguous":
        failure_class = "multiple-match-permanent-quarantine"
        retry_eligible = False
        final_payload = require_dict(events[-1], "payload")
        values = final_payload.get("job_ids", [])
        if not isinstance(values, list) or any(
            not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id)
            for job_id in values
        ):
            raise ValueError("permanent ambiguity job identities are invalid")
        scheduler_job_ids = sorted(set(values), key=int)
    else:
        raise ValueError(
            "Phase-2C failure is not terminal or safely reviewed ambiguous"
        )
    test_mode = context.manifest.get("test_mode") is True
    if test_mode and not allow_test_mode:
        raise ValueError("test-mode failure cannot become formal evidence")
    journal_root = _event_root(context, authorization_hash)
    journal_records = _recursive_records(journal_root)
    journal_text = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(journal_records.items())
    )
    raw_root = _raw_directory(context)
    raw_status = "absent"
    raw_payload: dict[str, Any] | None = None
    raw_records: dict[str, str] = {}
    if raw_root.exists() or raw_root.is_symlink():
        _require_regular_directory(raw_root, "Phase-2C failure raw workspace")
        raw_records = _recursive_records(raw_root, exclude={"SHA256SUMS"})
        if (raw_root / "SHA256SUMS").is_file():
            verify_checksum_manifest(raw_root)
            raw_status = "complete"
        else:
            raw_status = "partial"
        result_path = raw_root / "probe_result.json"
        if result_path.is_file() and not result_path.is_symlink():
            raw_payload = load_json(result_path)
            if (
                raw_payload.get("twin_id") != context.twin_id
                or raw_payload.get("twin_hash") != context.twin_hash
                or raw_payload.get("production_equivalence_hash")
                != context.production_equivalence_hash
                or raw_payload.get("geant4_invoked") is not False
                or raw_payload.get("events_consumed") != 0
                or raw_payload.get("production_seeds_consumed") != 0
            ):
                raise ValueError("Phase-2C failure raw result crossed its boundary")
    source_paths = {
        "authorization": _authorization_directory(
            context, authorization_hash
        ),
        "journal": journal_root,
    }
    if accounting is not None:
        source_paths["accounting"] = accounting_root
    if raw_status != "absent":
        source_paths["raw"] = raw_root
    twin = {
        "directory": str(context.directory),
        "twin_id": context.twin_id,
        "twin_hash": context.twin_hash,
        "static_manifest_sha256": _static_manifest_sha(context),
    }
    identity: dict[str, Any] = {
        "schema_version": PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION,
        "test_mode": test_mode,
        "accepted_compute_preflight_evidence": False,
        "failure_evidence_id": None,
        "failure_evidence_hash": None,
        "created_at_utc": events[-1].get("created_at_utc"),
        "twin": twin,
        "production_equivalence_hash": context.production_equivalence_hash,
        "authorization_hash": authorization_hash,
        "authorization_manifest_sha256": sha256_file(
            source_paths["authorization"] / "SHA256SUMS"
        ),
        "failure_class": failure_class,
        "review": {"reviewed_by": reviewer, "rationale": rationale.strip()},
        "journal_terminal_state": state.status,
        "scheduler_job_ids": scheduler_job_ids,
        "retry_eligible": retry_eligible,
        "terminal_accounting": (
            {
                "present": True,
                "terminal_state": accounting["terminal_state"],
                "exit_code": accounting["exit_code"],
                "batch_state": accounting["batch_state"],
                "batch_exit_code": accounting["batch_exit_code"],
                "accepted_terminal_success": False,
                "manifest_sha256": sha256_file(
                    accounting_root / "SHA256SUMS"
                ),
            }
            if accounting is not None
            else {"present": False}
        ),
        "runtime_boundary": {
            "apptainer_invoked": (
                raw_payload.get("apptainer_invoked")
                if raw_payload is not None
                else None
            ),
            "geant4_invoked": False,
            "raw_status": raw_status,
        },
        "consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        },
        "twin_execution_closed": True,
        "source_artifacts": {
            "journal_manifest_sha256": hashlib.sha256(
                journal_text.encode("utf-8")
            ).hexdigest(),
            "accounting_manifest_sha256": (
                sha256_file(accounting_root / "SHA256SUMS")
                if accounting is not None
                else None
            ),
            "raw_records_sha256": (
                hashlib.sha256(canonical_json(raw_records)).hexdigest()
                if raw_records
                else None
            ),
        },
    }
    digest_value = dict(identity)
    digest_value["failure_evidence_id"] = None
    digest_value["failure_evidence_hash"] = None
    digest = hashlib.sha256(canonical_json(digest_value)).hexdigest()
    identity["failure_evidence_hash"] = digest
    identity["failure_evidence_id"] = (
        "sm-v1-phase2c-preflight-failure-" + digest[:12]
    )
    return identity, source_paths


def preview_phase2c_preflight_failure_evidence(
    context: PreflightContext,
    *,
    authorization_hash: str,
    reviewer: str,
    rationale: str,
    allow_test_mode: bool = False,
) -> dict[str, Any]:
    payload, _sources = _failure_payload(
        context,
        authorization_hash=authorization_hash,
        reviewer=reviewer,
        rationale=rationale,
        allow_test_mode=allow_test_mode,
    )
    return payload


def seal_phase2c_preflight_failure_evidence(
    context: PreflightContext,
    *,
    authorization_hash: str,
    reviewer: str,
    rationale: str,
    allow_test_mode: bool = False,
) -> Path:
    payload, sources = _failure_payload(
        context,
        authorization_hash=authorization_hash,
        reviewer=reviewer,
        rationale=rationale,
        allow_test_mode=allow_test_mode,
    )
    target = context.control_root / "failures" / payload["failure_evidence_id"]
    marker_path = context.directory / FAILURE_MARKER_NAME
    with control_lock(context):
        success_root = context.control_root / "evidence"
        for candidate in success_root.iterdir():
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError("unsafe Phase-2C success evidence entry")
            evidence_json = candidate / "evidence.json"
            if evidence_json.is_file() and load_json(evidence_json).get(
                "twin", {}
            ).get("twin_hash") == context.twin_hash:
                raise ValueError(
                    "successful Phase-2C evidence already exists for this twin"
                )
        if marker_path.exists() or marker_path.is_symlink():
            raise ValueError("Phase-2C twin already has a failure disposition")
        if target.exists() or target.is_symlink():
            validate_phase2c_preflight_failure_evidence(
                target, expected_twin=context.twin, allow_test_mode=allow_test_mode
            )
        else:
            temporary = Path(
                tempfile.mkdtemp(prefix=".failure-", dir=target.parent)
            )
            try:
                _write_exclusive_json(
                    temporary / "failure_evidence.json", payload
                )
                for name, source in sources.items():
                    destination = temporary / name
                    destination.mkdir(mode=0o700)
                    for source_path in sorted(source.rglob("*")):
                        if source_path.name == "SHA256SUMS":
                            continue
                        relative = source_path.relative_to(source)
                        if source_path.is_symlink():
                            raise ValueError("unsafe Phase-2C failure source symlink")
                        if source_path.is_dir():
                            (destination / relative).mkdir(
                                mode=0o700, parents=True, exist_ok=True
                            )
                        elif source_path.is_file():
                            (destination / relative).parent.mkdir(
                                mode=0o700, parents=True, exist_ok=True
                            )
                            _copy_file(source_path, destination / relative)
                        else:
                            raise ValueError("unsafe Phase-2C failure source entry")
                    _write_checksum_manifest(destination)
                _write_checksum_manifest(temporary)
                _publish_directory(temporary, target)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        validate_phase2c_preflight_failure_evidence(
            target, expected_twin=context.twin, allow_test_mode=allow_test_mode
        )
        marker = {
            "schema_version": FAILURE_CLOSURE_SCHEMA_VERSION,
            "created_at_utc": utc_now(),
            "twin_id": context.twin_id,
            "twin_hash": context.twin_hash,
            "production_equivalence_hash": context.production_equivalence_hash,
            "failure_evidence_id": payload["failure_evidence_id"],
            "failure_evidence_hash": payload["failure_evidence_hash"],
            "failure_evidence_directory": str(target.resolve()),
            "execution_closed": True,
            "retry_eligible": payload["retry_eligible"],
            "scheduler_job_ids": payload["scheduler_job_ids"],
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        }
        _write_exclusive_json(marker_path, marker)
        marker_path.chmod(0o400)
        _fsync_directory(context.directory)
    return target


def validate_phase2c_preflight_failure_evidence(
    directory: Path,
    *,
    expected_twin: Any | None = None,
    allow_test_mode: bool = False,
) -> dict[str, Any]:
    root = _require_regular_directory(
        directory, "Phase-2C preflight failure evidence"
    )
    outer = verify_checksum_manifest(root)
    required = {
        "failure_evidence.json",
        "authorization/authorization.json",
        "authorization/SHA256SUMS",
        "journal/SHA256SUMS",
    }
    journal_files = {
        name for name in outer
        if name.startswith("journal/") and name != "journal/SHA256SUMS"
    }
    optional_roots = {
        name.split("/", 1)[0]
        for name in outer
        if "/" in name and name.split("/", 1)[0] in {"accounting", "raw"}
    }
    allowed = set(required) | journal_files
    for name in optional_roots:
        allowed.update(
            entry for entry in outer if entry.startswith(name + "/")
        )
    if set(outer) != allowed or not journal_files:
        raise ValueError("Phase-2C failure evidence file set mismatch")
    for name in ("authorization", "journal", *sorted(optional_roots)):
        verify_checksum_manifest(root / name)
    payload = load_json(root / "failure_evidence.json")
    expected_keys = {
        "schema_version", "test_mode", "accepted_compute_preflight_evidence",
        "failure_evidence_id", "failure_evidence_hash", "created_at_utc",
        "twin", "production_equivalence_hash", "authorization_hash",
        "authorization_manifest_sha256", "failure_class", "review",
        "journal_terminal_state", "scheduler_job_ids", "retry_eligible",
        "terminal_accounting", "runtime_boundary", "consumption",
        "twin_execution_closed", "source_artifacts",
    }
    if set(payload) != expected_keys:
        raise ValueError("Phase-2C failure evidence field set mismatch")
    digest = payload.get("failure_evidence_hash")
    check = dict(payload)
    check["failure_evidence_id"] = None
    check["failure_evidence_hash"] = None
    test_mode = payload.get("test_mode")
    authorization_hash = payload.get("authorization_hash")
    if (
        payload.get("schema_version")
        != PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION
        or not _is_sha256(digest)
        or hashlib.sha256(canonical_json(check)).hexdigest() != digest
        or payload.get("failure_evidence_id")
        != "sm-v1-phase2c-preflight-failure-" + str(digest)[:12]
        or root.name != payload.get("failure_evidence_id")
        or not isinstance(test_mode, bool)
        or (test_mode and not allow_test_mode)
        or payload.get("accepted_compute_preflight_evidence") is not False
        or not _is_sha256(authorization_hash)
        or payload.get("authorization_manifest_sha256")
        != sha256_file(root / "authorization" / "SHA256SUMS")
        or payload.get("consumption")
        != {"events_consumed": 0, "production_seeds_consumed": 0}
        or payload.get("twin_execution_closed") is not True
    ):
        raise ValueError("Phase-2C failure evidence identity is invalid")
    authorization = load_json(root / "authorization" / "authorization.json")
    twin = require_dict(payload, "twin")
    authorization_semantic = dict(authorization)
    authorization_semantic.pop("created_at_utc", None)
    authorization_semantic["authorization_hash"] = None
    authorization_semantic["authorization_id"] = None
    authorization_scheduler = require_dict(authorization, "scheduler")
    authorization_launcher = require_dict(authorization_scheduler, "launcher")
    authorization_resources = require_dict(authorization_scheduler, "resources")
    if (
        authorization.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION
        or authorization.get("authorization_hash") != authorization_hash
        or hashlib.sha256(canonical_json(authorization_semantic)).hexdigest()
        != authorization_hash
        or authorization.get("authorization_id")
        != "sm-v1-phase2c-preflight-auth-" + str(authorization_hash)[:12]
        or authorization.get("twin") != twin
        or authorization.get("production_equivalence_hash")
        != payload.get("production_equivalence_hash")
        or authorization_scheduler.get("account") != FORMAL_ACCOUNT
        or authorization_scheduler.get("account_read_policy")
        != "exact-or-ascii-lowercase"
        or authorization_scheduler.get("array") is not False
        or authorization_scheduler.get("initially_held") is not True
        or authorization_scheduler.get("no_requeue") is not True
        or authorization_scheduler.get("export_mode") != "NONE"
        or authorization_scheduler.get("job_name")
        != "g4sm-p2c-" + str(twin.get("twin_hash", ""))[:12]
        or not str(authorization_scheduler.get("output_pattern", "")).endswith(
            "/preflight-control/slurm/slurm-%j.out"
        )
        or authorization_resources
        != {
            "nodes": 1,
            "ntasks": 1,
            "cpus_per_task": FORMAL_CPUS,
            "memory": FORMAL_MEMORY,
            "time_limit": FORMAL_TIME_LIMIT,
        }
        or authorization_launcher.get("path") != FORMAL_LAUNCHER_RELATIVE
        or not _is_sha256(authorization_launcher.get("sha256"))
        or require_dict(authorization, "authority")
        != {
            "purpose": "held-non-array-no-geant4-preflight-only",
            "production_intent_authorized": False,
            "production_submission_authorized": False,
            "separate_release_required": True,
            "automatic_retry_allowed": False,
        }
    ):
        raise ValueError("Phase-2C failure authorization binding changed")
    events = _failure_journal_records(
        root / "journal", authorization_hash=str(authorization_hash)
    )
    terminal_status_map = {
        "submission-ambiguous": "submission-ambiguous",
        "scheduler-observation": "submission-ambiguous",
        "release-ambiguous": "release-ambiguous",
        "release-observation": "release-ambiguous",
        "permanently-ambiguous": "closed-ambiguous",
        "terminal-accounting-frozen": "terminal-accounting-frozen",
    }
    if payload.get("journal_terminal_state") != terminal_status_map.get(
        events[-1].get("event_type")
    ):
        raise ValueError("Phase-2C failure journal terminal state changed")
    accounting = require_dict(payload, "terminal_accounting")
    failure_class = payload.get("failure_class")
    scheduler_job_ids = payload.get("scheduler_job_ids")
    if (
        not isinstance(scheduler_job_ids, list)
        or any(
            not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id)
            for job_id in scheduler_job_ids
        )
        or not isinstance(payload.get("retry_eligible"), bool)
    ):
        raise ValueError("Phase-2C failure scheduler identities are invalid")
    if failure_class == "terminal-job-failure":
        if "accounting" not in optional_roots or accounting.get("present") is not True:
            raise ValueError("terminal failure lacks accounting")
        accounting_payload = load_json(root / "accounting" / "accounting.json")
        rows = _parse_accounting_rows(
            (root / "accounting" / "sacct.psv").read_text(encoding="utf-8")
        )
        job_id = str(accounting_payload.get("job_id", ""))
        parents = [row for row in rows if row["job_id"] == job_id]
        batches = [row for row in rows if row["job_id"] == job_id + ".batch"]
        terminal_payload = require_dict(events[-1], "payload")
        if (
            len(parents) != 1
            or len(batches) != 1
            or any("_" in row["job_id"] for row in rows)
            or (root / "accounting" / "squeue.psv")
            .read_text(encoding="utf-8")
            .strip()
            or accounting_payload.get("accepted_terminal_success") is not False
            or accounting_payload.get("terminal_state")
            != _base_state(parents[0]["state"])
            or accounting_payload.get("exit_code") != parents[0]["exit_code"]
            or accounting_payload.get("batch_state")
            != _base_state(batches[0]["state"])
            or accounting_payload.get("batch_exit_code")
            != batches[0]["exit_code"]
            or accounting_payload.get("accounting_row_count") != len(rows)
            or accounting.get("manifest_sha256")
            != sha256_file(root / "accounting" / "SHA256SUMS")
            or scheduler_job_ids != [accounting_payload.get("job_id")]
            or payload.get("retry_eligible") is not True
            or events[-1].get("event_type") != "terminal-accounting-frozen"
            or terminal_payload.get("job_id") != job_id
            or terminal_payload.get("accounting_manifest_sha256")
            != sha256_file(root / "accounting" / "SHA256SUMS")
            or terminal_payload.get("accepted_terminal_success") is not False
        ):
            raise ValueError("terminal failure accounting changed")
    elif failure_class == "two-snapshot-zero-match-ambiguity":
        if accounting != {"present": False} or "accounting" in optional_roots:
            raise ValueError("zero-match failure unexpectedly has accounting")
        _failure_zero_match_observations(events)
        if payload.get("retry_eligible") is not True:
            raise ValueError("reviewed zero-match failure is not retryable")
    elif failure_class == "multiple-match-permanent-quarantine":
        if payload.get("retry_eligible") is not False:
            raise ValueError("multiple-match failure cannot be retryable")
    else:
        raise ValueError("unsupported Phase-2C failure class")
    review = require_dict(payload, "review")
    if (
        not ACTOR_RE.fullmatch(str(review.get("reviewed_by", "")))
        or not isinstance(review.get("rationale"), str)
        or not review["rationale"].strip()
    ):
        raise ValueError("Phase-2C failure review is invalid")
    runtime = require_dict(payload, "runtime_boundary")
    if (
        runtime.get("geant4_invoked") is not False
        or runtime.get("raw_status") not in {"absent", "partial", "complete"}
        or runtime.get("apptainer_invoked") not in {None, True, False}
    ):
        raise ValueError("Phase-2C failure runtime boundary is invalid")
    artifacts = require_dict(payload, "source_artifacts")
    if artifacts.get("journal_manifest_sha256") != sha256_file(
        root / "journal" / "SHA256SUMS"
    ):
        raise ValueError("Phase-2C failure journal manifest changed")
    if "accounting" in optional_roots and artifacts.get(
        "accounting_manifest_sha256"
    ) != sha256_file(root / "accounting" / "SHA256SUMS"):
        raise ValueError("Phase-2C failure accounting manifest changed")
    if "accounting" not in optional_roots and artifacts.get(
        "accounting_manifest_sha256"
    ) is not None:
        raise ValueError("Phase-2C failure has phantom accounting identity")
    if "raw" in optional_roots:
        raw_records = _recursive_records(root / "raw", exclude={"SHA256SUMS"})
        if artifacts.get("raw_records_sha256") != hashlib.sha256(
            canonical_json(raw_records)
        ).hexdigest():
            raise ValueError("Phase-2C failure raw records changed")
        raw_result = root / "raw" / "probe_result.json"
        if raw_result.is_file():
            raw = load_json(raw_result)
            if (
                raw.get("twin_id") != twin.get("twin_id")
                or raw.get("twin_hash") != twin.get("twin_hash")
                or raw.get("geant4_invoked") is not False
                or raw.get("events_consumed") != 0
                or raw.get("production_seeds_consumed") != 0
            ):
                raise ValueError("Phase-2C failure raw boundary changed")
    elif artifacts.get("raw_records_sha256") is not None:
        raise ValueError("Phase-2C failure has phantom raw identity")
    if expected_twin is not None:
        twin_id, twin_hash, equivalence, twin_dir = _expected_twin_values(
            expected_twin
        )
        expected = {
            "directory": str(twin_dir),
            "twin_id": twin_id,
            "twin_hash": twin_hash,
            "static_manifest_sha256": sha256_file(
                twin_dir / "STATIC_SHA256SUMS"
            ),
        }
        if twin != expected or payload.get("production_equivalence_hash") != equivalence:
            raise ValueError("Phase-2C failure evidence binds another twin")
    return payload


__all__ = [
    "ACCOUNTING_SCHEMA_VERSION",
    "AUTHORIZATION_SCHEMA_VERSION",
    "CLOSURE_SCHEMA_VERSION",
    "CONTAINER_REPORT_KEYS",
    "EVENT_SCHEMA_VERSION",
    "FORMAL_ACCOUNT",
    "FORMAL_CPUS",
    "FORMAL_LAUNCHER_RELATIVE",
    "FORMAL_MEMORY",
    "FORMAL_TIME_LIMIT",
    "MOUNTINFO_CONTRACT_VERSION",
    "PREFLIGHT_EVIDENCE_SCHEMA_VERSION",
    "PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION",
    "PREFLIGHT_TWIN_SCHEMA_VERSION",
    "PreflightContext",
    "PreflightState",
    "RAW_RESULT_SCHEMA_VERSION",
    "append_event",
    "authorization_payload",
    "freeze_terminal_accounting",
    "list_authorizations",
    "load_authorization",
    "load_formal_context",
    "preflight_state",
    "preview_phase2c_preflight_evidence",
    "preview_phase2c_preflight_failure_evidence",
    "preview_release_preflight",
    "preview_terminal_accounting",
    "query_and_verify_held",
    "read_events",
    "reconcile_preflight",
    "release_command",
    "release_preflight",
    "seal_phase2c_preflight_evidence",
    "seal_phase2c_preflight_failure_evidence",
    "submission_command",
    "submit_authorization",
    "validate_frozen_accounting",
    "validate_held_row",
    "validate_phase2c_preflight_evidence",
    "validate_phase2c_preflight_failure_evidence",
    "validate_raw_preflight_result",
    "verify_checksum_manifest",
    "write_authorization",
]
