#!/usr/bin/env python3
"""Immutable Phase-2C preflight-twin and production execution-v6 objects.

This module deliberately contains no scheduler operation.  It turns the
accepted, permanently closed execution-v5 evidence into a sacrificial
preflight twin and, only after a separately sealed successful preflight, into
an equivalent and pristine execution-v6.  R6 remains a tracked lock-only
authority checked lazily by the readiness module.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    recursive_file_records,
)
from steel_module_production_phase2b_lib import (
    ROOT_STATIC_MANIFEST,
    PHASE2A_LOCK_RELATIVE,
    _archive_git_source,
    _copy_fixture_source,
    _git,
    _make_read_only,
    _read_scan_args,
    _remove_tree,
    _sealed_pilot_identity,
    _validate_phase2a_binding,
    fsync_tree,
    load_phase2a_lock,
    source_tree_hash,
    utc_now,
    verify_checksum_manifest,
)
from steel_module_production_program_lib import (
    ordered_task_hash,
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)
from steel_module_managed_production_lib import load_managed_production_child
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME_V5,
    SUCCESSOR_OBJECT_KIND,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5,
    load_successor_execution,
    successor_r3_preflight_binding,
)


PREFLIGHT_TWIN_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-twin-v1"
)
EXECUTION_V6_SCHEMA_VERSION = "steel-module-production-managed-execution-v6"
PREFLIGHT_EVIDENCE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-evidence-v1"
)
PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-failure-evidence-v1"
)
READINESS_V6_SCHEMA_VERSION = (
    "steel-module-production-phase2c-readiness-lock-v1"
)
PRODUCTION_EQUIVALENCE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-production-equivalence-v1"
)
SOURCE_CHECKSUM_SCHEMA_VERSION = (
    "steel-module-production-phase2c-source-checksums-v1"
)
PREFLIGHT_OBJECT_KIND = "steel-module-production-preflight-twin"
EXECUTION_V6_OBJECT_KIND = SUCCESSOR_OBJECT_KIND
FORMAL_PREFLIGHT_V6_NAME = "steel-module-production-bc-s1-preflight-v6"
FORMAL_EXECUTION_V6_NAME = "steel-module-production-bc-s1-execution-v6"
FORMAL_PHASE2C_EVIDENCE_NAME = "steel-module-production-phase2c"
PHASE2C_READINESS_LOCK_RELATIVE = Path(
    "hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json"
)

FORMAL_V5_EXECUTION_ID = (
    "sm-v1-production-bc-s1-execution-v5-704c313624aa"
)
FORMAL_V5_EXECUTION_HASH = (
    "dd774195e93a5c5e479815fd265c2de629fa71f22a30cae0a13284e0e828b73e"
)
FORMAL_V5_EVIDENCE_ID = "sm-v1-r3-container-probe-732cc54bbeb7"
FORMAL_V5_EVIDENCE_HASH = (
    "732cc54bbeb7f42a2c537d7c343db6ca090b169b6ec700c21ec2d4b98af73c24"
)
FORMAL_V5_PREFLIGHT_JOB_ID = "50561809"

# Every entry is scheduler evidence, never a production attempt.  The
# Phase-2C preflight job is appended dynamically by the v6 materializer.
FORMAL_HISTORICAL_NONPRODUCTION_JOB_IDS = (
    "50532143",
    "50544247",
    "50547698",
    "50548308",
    "50558158",
    FORMAL_V5_PREFLIGHT_JOB_ID,
)

PORTABLE_LOCK_PROTOCOL_V6 = "portable-flock-v3-empty"
EMPTY_SHA256 = sha256_bytes(b"")
PORTABLE_LOCK_EQUIVALENCE = {
    "protocol": PORTABLE_LOCK_PROTOCOL_V6,
    "mode": 0o600,
    "size_bytes": 0,
    "sha256": EMPTY_SHA256,
    "link_count": 1,
    "device_inode_authority": False,
}

PRODUCTION_SCHEDULER_POLICY = {
    "account": "PAS2524",
    "account_readback_casefold": True,
    "array_spec": "1-32",
    "task_count": 32,
    "events": 8000,
    "cpus_per_task": 1,
    "memory_mib": 2048,
    "time_limit_seconds": 3600,
    "initially_held": True,
    "no_requeue": True,
    "export_mode": "NONE",
    "first_intent_mode": "predecessor-retry",
}

CRITICAL_CONTROL_BLOBS = (
    "hpc/osc/run_steel_module_production_phase2b_task.py",
    "hpc/osc/submit_steel_module_production_phase2b.sbatch",
    "hpc/osc/record_steel_module_production_task_result.py",
    "hpc/osc/steel_module_production_container_contract.py",
    "hpc/osc/steel_module_production_phase2c_lib.py",
    "hpc/osc/steel_module_production_phase2c_readiness.py",
    "hpc/osc/validate_steel_module_production_phase2c_readiness.py",
    "hpc/osc/steel_module_production_phase2c_preflight_lib.py",
    "hpc/osc/manage_steel_module_production_phase2c_preflight.py",
    "hpc/osc/run_steel_module_production_phase2c_preflight.py",
    "hpc/osc/run_steel_module_production_phase2c_preflight.sbatch",
    "hpc/osc/seal_steel_module_production_phase2c_preflight_evidence.py",
    "hpc/osc/seal_steel_module_production_phase2c_preflight_failure.py",
    "hpc/osc/validate_steel_module_production_phase2c_preflight_evidence.py",
)

PREFLIGHT_CLOSURE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-closure-v1"
)
PREFLIGHT_FAILURE_CLOSURE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-preflight-failure-closure-v1"
)
PREFLIGHT_CLOSURE_MARKER = ".phase2c-preflight-closed.json"
PREFLIGHT_FAILURE_MARKER = ".phase2c-preflight-failed.json"
FORMAL_HISTORICAL_EXCLUDED_ATTEMPT_IDS = (
    "20260718T175107Z-initial",
)

TWIN_STATIC_FILES = {
    "README.md",
    "preflight_twin.json",
    "production_equivalence.json",
    "scan_args.txt",
    "source_archives.json",
    "source_recursive_checksums.json",
    "tasks.tsv",
}
EXECUTION_V6_STATIC_FILES = {
    "README.md",
    "managed_execution.json",
    "preflight_evidence.json",
    "production_equivalence.json",
    "scan_args.txt",
    "source_archives.json",
    "source_recursive_checksums.json",
    "tasks.tsv",
}
TWIN_ROOT_ENTRIES = TWIN_STATIC_FILES | {
    ROOT_STATIC_MANIFEST,
    ".control.lock",
    "sources",
}
EXECUTION_V6_ROOT_ENTRIES = EXECUTION_V6_STATIC_FILES | {
    ROOT_STATIC_MANIFEST,
    ".control.lock",
    "sources",
    "intents",
    "attempts",
    "finalized",
}


V5EvidenceValidator = Callable[[Any, Path], dict[str, Any]]
V5ExecutionLoader = Callable[[Path], Any]


@dataclass(frozen=True)
class Phase2CPreflightTwin:
    directory: Path
    manifest: dict[str, Any]
    predecessor: Any | None
    tasks: tuple[CampaignTask, ...]
    scan_args: tuple[str, ...]
    previous_twin: "Phase2CPreflightTwin | None" = None
    failure_evidence: dict[str, Any] | None = None

    @property
    def twin_id(self) -> str:
        return require_string(self.manifest, "twin_id")

    @property
    def twin_hash(self) -> str:
        return require_sha256(self.manifest, "twin_hash")

    @property
    def production_equivalence_hash(self) -> str:
        return require_sha256(self.manifest, "production_equivalence_hash")


@dataclass(frozen=True)
class Phase2CExecutionV6:
    directory: Path
    manifest: dict[str, Any]
    managed_child: Any
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
        return require_string(require_dict(self.manifest, "managed_child"), "campaign_id")

    @property
    def plan_hash(self) -> str:
        return require_sha256(require_dict(self.manifest, "managed_child"), "plan_hash")

    @property
    def git_commit(self) -> str:
        return require_sha1(require_dict(self.manifest, "sources")["simulation"], "git_commit")

    @property
    def environment(self) -> dict[str, Any]:
        if self.managed_child is None:
            raise ValueError("execution-v6 managed-child environment is unavailable")
        return self.managed_child.plan.environment


@dataclass(frozen=True)
class PreflightV6Preview:
    repo_root: Path
    predecessor: Any
    v5_evidence: dict[str, Any]
    production_equivalence: dict[str, Any]
    source_archives: dict[str, Any]
    source_checksums: dict[str, Any]
    control_source: Path | None
    test_mode: bool

    @property
    def twin_hash(self) -> str:
        return _twin_authority_hash(
            self.predecessor, self.v5_evidence, self.production_equivalence
        )

    @property
    def twin_id(self) -> str:
        return f"sm-v1-production-bc-s1-preflight-v6-{self.twin_hash[:12]}"


def _require_exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ValueError(
            f"{label} field set mismatch: "
            f"unexpected={sorted(set(value) - keys)}, "
            f"missing={sorted(keys - set(value))}"
        )


def _semantic_hash(value: dict[str, Any], key: str) -> str:
    payload = dict(value)
    payload.pop(key, None)
    return sha256_bytes(canonical_json(payload))


def _safe_directory(path: Path, label: str, *, must_exist: bool = True) -> Path:
    requested = path.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    resolved = requested.resolve()
    if must_exist and not resolved.is_dir():
        raise ValueError(f"{label} is missing")
    return resolved


def _managed_work_root_from_repo(repo_root: Path) -> Path:
    _, lock = load_phase2a_lock(repo_root)
    managed = Path(require_string(lock, "canonical_directory"))
    if managed.name != "steel-module-production-bc-s1":
        raise ValueError("Phase-2A canonical child name changed")
    return managed.parent.parent


def canonical_preflight_v6_directory(repo_root: Path) -> Path:
    work = _managed_work_root_from_repo(repo_root.expanduser().resolve())
    return work / "campaigns" / FORMAL_PREFLIGHT_V6_NAME


def _retry_preflight_name(ordinal: int, twin_hash: str) -> str:
    if ordinal < 1 or not re.fullmatch(r"[0-9a-f]{64}", twin_hash):
        raise ValueError("invalid Phase-2C retry twin identity")
    return f"{FORMAL_PREFLIGHT_V6_NAME}-retry-{ordinal:02d}-{twin_hash[:12]}"


def _formal_preflight_candidates(repo_root: Path) -> tuple[Path, ...]:
    initial = canonical_preflight_v6_directory(repo_root)
    parent = initial.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("canonical Phase-2C campaign root is unsafe")
    candidates: list[Path] = []
    if initial.exists() or initial.is_symlink():
        candidates.append(initial)
    pattern = re.compile(
        rf"^{re.escape(FORMAL_PREFLIGHT_V6_NAME)}-retry-"
        r"(?P<ordinal>[0-9]{2})-(?P<digest>[0-9a-f]{12})$"
    )
    retry_rows: list[tuple[int, Path]] = []
    for path in parent.iterdir():
        match = pattern.fullmatch(path.name)
        if match is not None:
            retry_rows.append((int(match.group("ordinal")), path))
    retry_rows.sort(key=lambda row: row[0])
    if [ordinal for ordinal, _path in retry_rows] != list(
        range(1, len(retry_rows) + 1)
    ):
        raise ValueError("Phase-2C retry twin ordinals are not contiguous")
    candidates.extend(path for _ordinal, path in retry_rows)
    return tuple(candidates)


def current_formal_preflight_v6_directory(repo_root: Path) -> Path:
    candidates = _formal_preflight_candidates(repo_root.expanduser().resolve())
    if not candidates:
        return canonical_preflight_v6_directory(repo_root)
    # The full lineage is validated by load_preflight_v6.  Naming provides no
    # authority; it only selects the greatest contiguous candidate for loading.
    return candidates[-1]


def canonical_execution_v6_directory(repo_root: Path) -> Path:
    work = _managed_work_root_from_repo(repo_root.expanduser().resolve())
    return work / "campaigns" / FORMAL_EXECUTION_V6_NAME


def canonical_phase2c_evidence_root(repo_root_or_execution: Path) -> Path:
    """Return the fixed external Phase-2C evidence root.

    A repository checkout is recognized by its tracked Phase-2A lock.  An
    execution/twin path is otherwise recognized only under ``WORK/campaigns``.
    """

    value = repo_root_or_execution.expanduser().resolve()
    if (value / PHASE2A_LOCK_RELATIVE).is_file():
        return _managed_work_root_from_repo(value) / "evidence" / FORMAL_PHASE2C_EVIDENCE_NAME
    if value.parent.name != "campaigns":
        raise ValueError("cannot derive Phase-2C evidence root from this path")
    return value.parent.parent / "evidence" / FORMAL_PHASE2C_EVIDENCE_NAME


def _formal_v5_evidence_directory(predecessor: Any) -> Path:
    from steel_module_production_r3_probe_lib import canonical_r3_evidence_root

    return (
        canonical_r3_evidence_root(predecessor.directory)
        / "evidence"
        / FORMAL_V5_EVIDENCE_ID
    )


def _default_v5_evidence_validator(
    predecessor: Any, evidence_dir: Path
) -> dict[str, Any]:
    return successor_r3_preflight_binding(
        predecessor,
        evidence_dir,
        allow_test_mode=predecessor.manifest.get("test_mode") is True,
        require_current_snapshot=predecessor.manifest.get("test_mode") is False,
    )


def _validate_v5_identity(
    predecessor: Any,
    evidence: dict[str, Any],
    *,
    test_mode: bool,
) -> None:
    if predecessor.manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION_V5:
        raise ValueError("Phase-2C predecessor is not execution-v5")
    if predecessor.manifest.get("test_mode") is not test_mode:
        raise ValueError("Phase-2C predecessor evidence mode mismatch")
    if evidence.get("evidence_hash") is None:
        raise ValueError("execution-v5 evidence has no complete evidence hash")
    if not test_mode:
        if (
            predecessor.execution_id != FORMAL_V5_EXECUTION_ID
            or predecessor.execution_hash != FORMAL_V5_EXECUTION_HASH
            or evidence.get("evidence_id") != FORMAL_V5_EVIDENCE_ID
            or evidence.get("evidence_hash") != FORMAL_V5_EVIDENCE_HASH
            or evidence.get("job_id") != FORMAL_V5_PREFLIGHT_JOB_ID
        ):
            raise ValueError("formal execution-v5 evidence identity changed")


def _seed_values(tasks: tuple[CampaignTask, ...]) -> tuple[int, ...]:
    return tuple(seed for task in tasks for seed in (task.seed1, task.seed2))


def _task_seed_mapping(tasks: tuple[CampaignTask, ...]) -> list[dict[str, Any]]:
    return [
        {
            "task_index": task.task_index,
            "logical_task_id": task.logical_task_id,
            "tile_thickness_mm": task.tile_thickness_mm,
            "seed_block": task.seed_block,
            "events": task.events,
            "seed1": task.seed1,
            "seed2": task.seed2,
        }
        for task in tasks
    ]


def _exact_bc_s1_shape(tasks: tuple[CampaignTask, ...]) -> dict[str, Any]:
    seeds = _seed_values(tasks)
    expected_pairs = {
        (thickness, block) for thickness in (4, 24) for block in range(16)
    }
    actual_pairs = {(task.tile_thickness_mm, task.seed_block) for task in tasks}
    if (
        len(tasks) != 32
        or sum(task.events for task in tasks) != 8000
        or any(task.events != 250 for task in tasks)
        or actual_pairs != expected_pairs
        or any(task.sipm_layout != "back-center" for task in tasks)
        or any(task.absorber_transverse_mm != 500 for task in tasks)
        or len(seeds) != 64
        or len(set(seeds)) != 64
    ):
        raise ValueError("Phase-2C task plan is not exact BC-S1")
    return {
        "task_count": 32,
        "event_count": 8000,
        "events_per_task": 250,
        "seed_count": 64,
        "tile_thickness_mm": [4, 24],
        "sipm_layout": "back-center",
        "absorber_transverse_mm": 500,
        "seed_block_range_inclusive": [0, 15],
    }


def _source_checksum_payload(sources_root: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SOURCE_CHECKSUM_SCHEMA_VERSION,
        "sources": {},
    }
    output = require_dict(payload, "sources")
    for name in ("simulation", "control_plane"):
        leaf = "control" if name == "control_plane" else name
        records = recursive_file_records(sources_root / leaf, exclude=())
        output[name] = {
            "record_count": len(records),
            "records": records,
            "records_sha256": sha256_bytes(canonical_json(records)),
        }
    payload["manifest_hash"] = _semantic_hash(payload, "manifest_hash")
    return payload


def _validate_source_checksum_payload(
    directory: Path, payload: dict[str, Any]
) -> None:
    _require_exact_keys(
        payload,
        {"schema_version", "sources", "manifest_hash"},
        "Phase-2C source checksum manifest",
    )
    if (
        payload.get("schema_version") != SOURCE_CHECKSUM_SCHEMA_VERSION
        or payload.get("manifest_hash") != _semantic_hash(payload, "manifest_hash")
    ):
        raise ValueError("Phase-2C source checksum identity mismatch")
    recorded_sources = require_dict(payload, "sources")
    _require_exact_keys(recorded_sources, {"simulation", "control_plane"}, "source checksums")
    current = _source_checksum_payload(directory / "sources")
    if payload != current:
        raise ValueError("Phase-2C frozen source bytes changed")


def _validate_frozen_source_tree(
    directory: Path,
    record: dict[str, Any],
    *,
    expected_relative: str,
    label: str,
) -> None:
    """Validate one canonical, immutable Phase-2C source tree.

    The recursive checksum manifest binds regular-file bytes, while the source
    archive record separately binds Git's executable-bit-sensitive tree hash.
    Requiring canonical read-only modes closes the remaining gap where a
    non-executable file could otherwise become writable without changing
    either digest.
    """

    if require_string(record, "path") != expected_relative:
        raise ValueError(f"Phase-2C {label} source path is not canonical")
    source = directory / expected_relative
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Phase-2C {label} source root is missing or unsafe")
    for path in (source, *sorted(source.rglob("*"))):
        if path.is_symlink():
            raise ValueError(f"Phase-2C {label} source contains a symlink")
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            if mode != 0o555:
                raise ValueError(
                    f"Phase-2C {label} source directory mode changed"
                )
        elif stat.S_ISREG(info.st_mode):
            canonical_mode = 0o555 if mode & 0o111 else 0o444
            if mode != canonical_mode:
                raise ValueError(f"Phase-2C {label} source file mode changed")
        else:
            raise ValueError(f"Phase-2C {label} source entry is not regular")
    if source_tree_hash(source) != require_sha256(record, "source_tree_sha256"):
        raise ValueError(f"Phase-2C {label} source tree hash changed")


def _validate_frozen_sources(
    directory: Path, source_archives: dict[str, Any]
) -> None:
    sources = directory / "sources"
    if (
        sources.is_symlink()
        or not sources.is_dir()
        or stat.S_IMODE(sources.lstat().st_mode) != 0o555
    ):
        raise ValueError("Phase-2C frozen sources root is missing or unsafe")
    entries = {path.name for path in sources.iterdir()}
    if entries != {"simulation", "control"} or any(
        path.is_symlink() for path in sources.iterdir()
    ):
        raise ValueError("Phase-2C frozen source entry set is unsafe")
    if source_archives.get("schema_version") != (
        "steel-module-production-dual-source-v1"
    ):
        raise ValueError("Phase-2C source archive schema changed")
    _validate_frozen_source_tree(
        directory,
        require_dict(source_archives, "simulation"),
        expected_relative="sources/simulation",
        label="simulation",
    )
    _validate_frozen_source_tree(
        directory,
        require_dict(source_archives, "control_plane"),
        expected_relative="sources/control",
        label="control-plane",
    )


def _critical_blob_records(control_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for relative in CRITICAL_CONTROL_BLOBS:
        path = control_root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing production-critical control blob: {relative}")
        result[relative] = {
            "sha256": sha256_file(path),
            "mode": path.stat().st_mode & 0o777,
        }
    return result


def _task_identity(tasks: tuple[CampaignTask, ...]) -> dict[str, Any]:
    mapping = _task_seed_mapping(tasks)
    return {
        "task_set_hash": task_set_hash(tasks),
        "ordered_task_hash": ordered_task_hash(tasks),
        "seed_set_hash": seed_set_hash(_seed_values(tasks)),
        "seed_pair_registry_hash": seed_pair_registry_hash(tasks),
        "task_seed_mapping": mapping,
        "task_seed_mapping_hash": sha256_bytes(canonical_json(mapping)),
    }


def _production_equivalence(
    *,
    tasks: tuple[CampaignTask, ...],
    tasks_sha256: str,
    scan_args_sha256: str,
    source_archives: dict[str, Any],
    source_checksums: dict[str, Any],
    runtime: dict[str, Any],
    control_root: Path,
) -> dict[str, Any]:
    source_binding: dict[str, Any] = {}
    checksum_sources = require_dict(source_checksums, "sources")
    for name in ("simulation", "control_plane"):
        archive = require_dict(source_archives, name)
        checksums = require_dict(checksum_sources, name)
        source_binding[name] = {
            "git_commit": archive.get("git_commit"),
            "git_tree": archive.get("git_tree"),
            "source_tree_sha256": archive.get("source_tree_sha256"),
            "recursive_record_count": checksums.get("record_count"),
            "recursive_records_sha256": checksums.get("records_sha256"),
        }
    payload: dict[str, Any] = {
        "schema_version": PRODUCTION_EQUIVALENCE_SCHEMA_VERSION,
        "shape": _exact_bc_s1_shape(tasks),
        "tasks": {
            **_task_identity(tasks),
            "tasks_tsv_sha256": tasks_sha256,
            "scan_args_sha256": scan_args_sha256,
        },
        "sources": source_binding,
        "source_recursive_manifest_hash": require_sha256(
            source_checksums, "manifest_hash"
        ),
        "runtime": runtime,
        "critical_control_blobs": _critical_blob_records(control_root),
        "portable_lock": PORTABLE_LOCK_EQUIVALENCE,
        "scheduler": PRODUCTION_SCHEDULER_POLICY,
        "production_equivalence_hash": None,
    }
    payload["production_equivalence_hash"] = _semantic_hash(
        payload, "production_equivalence_hash"
    )
    return payload


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_static_manifest(directory: Path, expected: set[str]) -> None:
    text = "".join(
        f"{sha256_file(directory / name)}  {name}\n" for name in sorted(expected)
    )
    (directory / ROOT_STATIC_MANIFEST).write_text(text, encoding="utf-8")


def _verify_static_manifest(directory: Path, expected: set[str]) -> None:
    rows = verify_checksum_manifest(directory, ROOT_STATIC_MANIFEST)
    if set(rows) != expected:
        raise ValueError("Phase-2C static checksum set mismatch")


def _validate_artifacts(directory: Path, manifest: dict[str, Any]) -> None:
    artifacts = require_dict(manifest, "artifacts")
    names = {
        "tasks": "tasks.tsv",
        "scan_args": "scan_args.txt",
        "source_archives": "source_archives.json",
        "source_recursive_checksums": "source_recursive_checksums.json",
        "production_equivalence": "production_equivalence.json",
    }
    for key, name in names.items():
        record = require_dict(artifacts, key)
        if record != {"path": name, "sha256": sha256_file(directory / name)}:
            raise ValueError(f"Phase-2C artifact binding mismatch: {name}")
    if require_dict(artifacts, "control_lock") != require_dict(
        manifest, "portable_lock"
    ):
        raise ValueError("Phase-2C control-lock artifact binding mismatch")


def _create_empty_portable_lock(directory: Path) -> dict[str, Any]:
    path = directory / ".control.lock"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = path.lstat()
    return {
        "path": ".control.lock",
        **PORTABLE_LOCK_EQUIVALENCE,
        "creation_device": info.st_dev,
        "creation_inode": info.st_ino,
    }


def _validate_empty_portable_lock(directory: Path, record: dict[str, Any]) -> None:
    _require_exact_keys(
        record,
        {
            "path", "protocol", "mode", "size_bytes", "sha256",
            "link_count", "device_inode_authority", "creation_device",
            "creation_inode",
        },
        "Phase-2C portable lock",
    )
    if {key: record.get(key) for key in PORTABLE_LOCK_EQUIVALENCE} != PORTABLE_LOCK_EQUIVALENCE:
        raise ValueError("Phase-2C portable lock contract changed")
    path = directory / ".control.lock"
    if path.is_symlink():
        raise ValueError("Phase-2C portable lock is a symlink")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o777 != 0o600
        or info.st_size != 0
        or info.st_nlink != 1
        or sha256_file(path) != EMPTY_SHA256
    ):
        raise ValueError("Phase-2C portable lock filesystem contract changed")
    # Device and inode are recorded diagnostics, intentionally not authority.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def validate_preflight_v6_closure(
    twin: Phase2CPreflightTwin, *, required: bool
) -> dict[str, Any] | None:
    path = twin.directory / PREFLIGHT_CLOSURE_MARKER
    if not path.exists() and not path.is_symlink():
        if required:
            raise ValueError("Phase-2C preflight twin is not closed")
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2C preflight closure marker is unsafe")
    info = path.lstat()
    if info.st_mode & 0o777 != 0o400 or info.st_nlink != 1:
        raise ValueError("Phase-2C preflight closure marker mode/link changed")
    marker = load_json(path)
    _require_exact_keys(
        marker,
        {
            "schema_version", "created_at_utc", "twin_id", "twin_hash",
            "production_equivalence_hash", "slurm_job_id", "slurm_job_name",
            "raw_result_sha256", "execution_closed", "future_execution_role",
            "events_consumed", "production_seeds_consumed",
        },
        "Phase-2C preflight closure marker",
    )
    if (
        marker.get("schema_version") != PREFLIGHT_CLOSURE_SCHEMA_VERSION
        or marker.get("twin_id") != twin.twin_id
        or marker.get("twin_hash") != twin.twin_hash
        or marker.get("production_equivalence_hash")
        != twin.production_equivalence_hash
        or marker.get("execution_closed") is not True
        or marker.get("future_execution_role") != "clean-execution-v6"
        or marker.get("events_consumed") != 0
        or marker.get("production_seeds_consumed") != 0
    ):
        raise ValueError("Phase-2C preflight closure semantics changed")
    require_string(marker, "created_at_utc")
    require_string(marker, "slurm_job_id")
    require_string(marker, "slurm_job_name")
    require_sha256(marker, "raw_result_sha256")
    return marker


def validate_preflight_v6_failure(
    twin: Phase2CPreflightTwin, *, required: bool
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    path = twin.directory / PREFLIGHT_FAILURE_MARKER
    if not path.exists() and not path.is_symlink():
        if required:
            raise ValueError("Phase-2C preflight twin has no sealed failure")
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("Phase-2C preflight failure marker is unsafe")
    info = path.lstat()
    if info.st_mode & 0o777 != 0o400 or info.st_nlink != 1:
        raise ValueError("Phase-2C preflight failure marker mode/link changed")
    marker = load_json(path)
    _require_exact_keys(
        marker,
        {
            "schema_version", "created_at_utc", "twin_id", "twin_hash",
            "production_equivalence_hash", "failure_evidence_id",
            "failure_evidence_hash", "failure_evidence_directory",
            "execution_closed", "retry_eligible", "scheduler_job_ids",
            "events_consumed", "production_seeds_consumed",
        },
        "Phase-2C preflight failure marker",
    )
    if (
        marker.get("schema_version")
        != PREFLIGHT_FAILURE_CLOSURE_SCHEMA_VERSION
        or marker.get("twin_id") != twin.twin_id
        or marker.get("twin_hash") != twin.twin_hash
        or marker.get("production_equivalence_hash")
        != twin.production_equivalence_hash
        or marker.get("execution_closed") is not True
        or not isinstance(marker.get("retry_eligible"), bool)
        or marker.get("events_consumed") != 0
        or marker.get("production_seeds_consumed") != 0
        or not isinstance(marker.get("scheduler_job_ids"), list)
        or any(
            not isinstance(job_id, str) or not job_id.isdigit()
            for job_id in marker.get("scheduler_job_ids", [])
        )
    ):
        raise ValueError("Phase-2C preflight failure marker changed")
    require_string(marker, "created_at_utc")
    require_string(marker, "failure_evidence_id")
    require_sha256(marker, "failure_evidence_hash")
    evidence_dir = Path(require_string(marker, "failure_evidence_directory"))
    from steel_module_production_phase2c_preflight_lib import (
        validate_phase2c_preflight_failure_evidence,
    )

    evidence = validate_phase2c_preflight_failure_evidence(
        evidence_dir,
        expected_twin=twin,
        allow_test_mode=twin.manifest.get("test_mode") is True,
    )
    if (
        evidence.get("failure_evidence_id") != marker.get("failure_evidence_id")
        or evidence.get("failure_evidence_hash")
        != marker.get("failure_evidence_hash")
        or evidence.get("retry_eligible") is not marker.get("retry_eligible")
        or evidence.get("scheduler_job_ids") != marker.get("scheduler_job_ids")
    ):
        raise ValueError("Phase-2C preflight failure evidence binding changed")
    return marker, evidence


def _harden_static_tree(directory: Path, static_files: set[str]) -> None:
    for name in static_files | {ROOT_STATIC_MANIFEST}:
        (directory / name).chmod(0o444)
    for source in (directory / "sources").rglob("*"):
        if source.is_symlink():
            raise ValueError("Phase-2C frozen sources contain a symlink")
    _make_read_only(directory / "sources")


def _copy_frozen_source(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ValueError("frozen source directory is missing or unsafe")
    recursive_file_records(source, exclude=())
    shutil.copytree(source, destination, symlinks=False)
    _make_read_only(destination)


def _archive_preview_sources(
    *,
    repo_root: Path,
    predecessor: Any,
    test_mode: bool,
    fixture_control_source: Path | None,
    destination: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = destination / "sources"
    sources.mkdir()
    simulation = sources / "simulation"
    control = sources / "control"
    predecessor_sources = require_dict(predecessor.manifest, "sources")
    recorded_simulation = require_dict(predecessor_sources, "simulation")
    predecessor_simulation = (
        predecessor.directory / require_string(recorded_simulation, "path")
    )
    _copy_frozen_source(predecessor_simulation, simulation)
    simulation_identity = {
        "git_commit": recorded_simulation.get("git_commit"),
        "git_tree": recorded_simulation.get("git_tree"),
        "source_tree_sha256": source_tree_hash(simulation),
        "path": "sources/simulation",
    }
    if simulation_identity["source_tree_sha256"] != recorded_simulation.get(
        "source_tree_sha256"
    ):
        raise ValueError("execution-v5 simulation source copy changed")
    if fixture_control_source is None:
        commit = _git(repo_root, "rev-parse", "HEAD")
        if not test_mode and _git(
            repo_root, "status", "--porcelain", "--untracked-files=no"
        ):
            raise ValueError("formal Phase-2C materialization requires a clean tracked checkout")
        control_identity = _archive_git_source(repo_root, commit, control)
    else:
        if not test_mode:
            raise ValueError("formal Phase-2C cannot override the control source")
        control_identity = _copy_fixture_source(
            fixture_control_source, control, "f" * 40
        )
    source_archives = {
        "schema_version": "steel-module-production-dual-source-v1",
        "simulation": simulation_identity,
        "control_plane": {**control_identity, "path": "sources/control"},
    }
    source_checksums = _source_checksum_payload(sources)
    return source_archives, source_checksums


def _twin_authority_hash(
    predecessor: Any,
    v5_evidence: dict[str, Any],
    equivalence: dict[str, Any],
) -> str:
    payload = {
        "schema_version": PREFLIGHT_TWIN_SCHEMA_VERSION,
        "predecessor_execution_id": predecessor.execution_id,
        "predecessor_execution_hash": predecessor.execution_hash,
        "v5_evidence_id": v5_evidence.get("evidence_id"),
        "v5_evidence_hash": v5_evidence.get("evidence_hash"),
        "production_equivalence_hash": equivalence.get(
            "production_equivalence_hash"
        ),
    }
    return sha256_bytes(canonical_json(payload))


def _retry_twin_authority_hash(
    *,
    previous_twin: Phase2CPreflightTwin,
    failure_evidence: dict[str, Any],
    ordinal: int,
) -> str:
    payload = {
        "schema_version": PREFLIGHT_TWIN_SCHEMA_VERSION,
        "retry_ordinal": ordinal,
        "previous_twin_id": previous_twin.twin_id,
        "previous_twin_hash": previous_twin.twin_hash,
        "failure_evidence_id": require_string(
            failure_evidence, "failure_evidence_id"
        ),
        "failure_evidence_hash": require_sha256(
            failure_evidence, "failure_evidence_hash"
        ),
        "production_equivalence_hash": (
            previous_twin.production_equivalence_hash
        ),
    }
    return sha256_bytes(canonical_json(payload))


def _preflight_twin_binding(twin: Phase2CPreflightTwin) -> dict[str, Any]:
    return {
        "directory": str(twin.directory),
        "twin_id": twin.twin_id,
        "twin_hash": twin.twin_hash,
        "static_manifest_sha256": sha256_file(
            twin.directory / ROOT_STATIC_MANIFEST
        ),
    }


def _failure_evidence_binding(
    directory: Path, evidence: dict[str, Any]
) -> dict[str, Any]:
    return {
        "directory": str(directory.expanduser().resolve()),
        "schema_version": evidence.get("schema_version"),
        "failure_evidence_id": require_string(
            evidence, "failure_evidence_id"
        ),
        "failure_evidence_hash": require_sha256(
            evidence, "failure_evidence_hash"
        ),
        "evidence_json_sha256": sha256_file(
            directory / "failure_evidence.json"
        ),
        "checksum_manifest_sha256": sha256_file(
            directory / "SHA256SUMS"
        ),
        "retry_eligible": evidence.get("retry_eligible"),
        "scheduler_job_ids": evidence.get("scheduler_job_ids"),
    }


def _load_formal_or_test_v5(
    *,
    repo_root: Path,
    v5_dir: Path | None,
    test_mode: bool,
    verify_live_predecessor: bool = True,
    verify_runtime: bool = True,
    verify_phase2a_control_plane: bool = True,
) -> Any:
    directory = v5_dir
    if directory is None:
        work = _managed_work_root_from_repo(repo_root)
        directory = work / "campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME_V5
    if not test_mode:
        expected = (
            _managed_work_root_from_repo(repo_root)
            / "campaigns"
            / FORMAL_SUCCESSOR_EXECUTION_NAME_V5
        )
        if directory.expanduser().resolve() != expected:
            raise ValueError(f"formal execution-v5 path is fixed: {expected}")
    effective_repo_root = repo_root
    if not test_mode and not verify_phase2a_control_plane:
        predecessor_manifest = load_json(
            directory.expanduser().resolve() / "managed_execution.json"
        )
        predecessor_sources = require_dict(predecessor_manifest, "sources")
        predecessor_control = require_dict(
            predecessor_sources, "control_plane"
        )
        effective_repo_root = (
            directory.expanduser().resolve()
            / require_string(predecessor_control, "path")
        )
    return load_successor_execution(
        directory,
        repo_root=effective_repo_root if not test_mode else None,
        allow_test_mode=test_mode,
        require_readiness=False,
        verify_runtime=not test_mode and verify_runtime,
        verify_phase2a_control_plane=(
            not test_mode and verify_phase2a_control_plane
        ),
        verify_live_predecessor=not test_mode and verify_live_predecessor,
    )


def preview_preflight_v6(
    *,
    repo_root: Path,
    v5_dir: Path | None = None,
    v5_evidence_dir: Path | None = None,
    test_mode: bool = False,
    fixture_control_source: Path | None = None,
    v5_evidence_validator: V5EvidenceValidator | None = None,
    v5_execution_loader: V5ExecutionLoader | None = None,
) -> PreflightV6Preview:
    root = _safe_directory(repo_root, "repository root")
    if v5_execution_loader is not None:
        if not test_mode or v5_dir is None:
            raise ValueError("custom execution-v5 loader is test-mode only")
        predecessor = v5_execution_loader(v5_dir.expanduser().resolve())
    else:
        predecessor = _load_formal_or_test_v5(
            repo_root=root, v5_dir=v5_dir, test_mode=test_mode
        )
    evidence_path = (
        v5_evidence_dir
        if v5_evidence_dir is not None
        else _formal_v5_evidence_directory(predecessor)
    )
    validator = v5_evidence_validator or _default_v5_evidence_validator
    evidence = validator(predecessor, evidence_path.expanduser().resolve())
    _validate_v5_identity(predecessor, evidence, test_mode=test_mode)
    _exact_bc_s1_shape(predecessor.tasks)
    with tempfile.TemporaryDirectory(prefix="phase2c-preview-") as scratch:
        staging = Path(scratch)
        archives, checksums = _archive_preview_sources(
            repo_root=root,
            predecessor=predecessor,
            test_mode=test_mode,
            fixture_control_source=fixture_control_source,
            destination=staging,
        )
        equivalence = _production_equivalence(
            tasks=predecessor.tasks,
            tasks_sha256=sha256_file(predecessor.directory / "tasks.tsv"),
            scan_args_sha256=sha256_file(predecessor.directory / "scan_args.txt"),
            source_archives=archives,
            source_checksums=checksums,
            runtime=require_dict(predecessor.manifest, "runtime"),
            control_root=staging / "sources/control",
        )
    return PreflightV6Preview(
        root,
        predecessor,
        evidence,
        equivalence,
        archives,
        checksums,
        fixture_control_source,
        test_mode,
    )


def _v5_binding(predecessor: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "directory": str(predecessor.directory),
        "schema_version": predecessor.manifest.get("schema_version"),
        "execution_id": predecessor.execution_id,
        "execution_hash": predecessor.execution_hash,
        "managed_execution_sha256": sha256_file(
            predecessor.directory / "managed_execution.json"
        ),
        "static_manifest_sha256": sha256_file(
            predecessor.directory / ROOT_STATIC_MANIFEST
        ),
        "recovery_authority_hash": require_sha256(
            require_dict(predecessor.manifest, "recovery_authority"),
            "authority_hash",
        ),
        "recovery_authority": predecessor.manifest["recovery_authority"],
        "accepted_r3_evidence": evidence,
    }


def _write_preflight_tree(directory: Path, preview: PreflightV6Preview) -> None:
    directory.mkdir()
    lock = _create_empty_portable_lock(directory)
    shutil.copyfile(preview.predecessor.directory / "tasks.tsv", directory / "tasks.tsv")
    shutil.copyfile(preview.predecessor.directory / "scan_args.txt", directory / "scan_args.txt")
    archives, checksums = _archive_preview_sources(
        repo_root=preview.repo_root,
        predecessor=preview.predecessor,
        test_mode=preview.test_mode,
        fixture_control_source=preview.control_source,
        destination=directory,
    )
    equivalence = _production_equivalence(
        tasks=preview.predecessor.tasks,
        tasks_sha256=sha256_file(directory / "tasks.tsv"),
        scan_args_sha256=sha256_file(directory / "scan_args.txt"),
        source_archives=archives,
        source_checksums=checksums,
        runtime=require_dict(preview.predecessor.manifest, "runtime"),
        control_root=directory / "sources/control",
    )
    if equivalence != preview.production_equivalence:
        raise ValueError("Phase-2C preview/materialization equivalence drift")
    _write_json(directory / "source_archives.json", archives)
    _write_json(directory / "source_recursive_checksums.json", checksums)
    _write_json(directory / "production_equivalence.json", equivalence)
    twin_hash = _twin_authority_hash(
        preview.predecessor, preview.v5_evidence, equivalence
    )
    twin_id = f"sm-v1-production-bc-s1-preflight-v6-{twin_hash[:12]}"
    manifest = {
        "schema_version": PREFLIGHT_TWIN_SCHEMA_VERSION,
        "object_kind": PREFLIGHT_OBJECT_KIND,
        "created_at_utc": utc_now(),
        "test_mode": preview.test_mode,
        "accepted_predecessor_evidence": not preview.test_mode,
        "twin_id": twin_id,
        "twin_hash": twin_hash,
        "role": "sacrificial-no-geant4-preflight-only",
        "production_authority": False,
        "production_equivalence_hash": equivalence[
            "production_equivalence_hash"
        ],
        "managed_child": preview.predecessor.manifest["managed_child"],
        "program": preview.predecessor.manifest["program"],
        "phase2a_lock": preview.predecessor.manifest["phase2a_lock"],
        "shape": _exact_bc_s1_shape(preview.predecessor.tasks),
        "runtime": preview.predecessor.manifest["runtime"],
        "sealed_pilot": preview.predecessor.manifest["sealed_pilot"],
        "sources": archives,
        "predecessor_v5": _v5_binding(
            preview.predecessor, preview.v5_evidence
        ),
        "portable_lock": lock,
        "artifacts": {
            "tasks": {"path": "tasks.tsv", "sha256": sha256_file(directory / "tasks.tsv")},
            "scan_args": {"path": "scan_args.txt", "sha256": sha256_file(directory / "scan_args.txt")},
            "source_archives": {"path": "source_archives.json", "sha256": sha256_file(directory / "source_archives.json")},
            "source_recursive_checksums": {"path": "source_recursive_checksums.json", "sha256": sha256_file(directory / "source_recursive_checksums.json")},
            "production_equivalence": {"path": "production_equivalence.json", "sha256": sha256_file(directory / "production_equivalence.json")},
            "control_lock": lock,
        },
        "preflight_policy": {
            "scheduler_shape": "single-non-array",
            "initially_held": True,
            "no_requeue": True,
            "export_mode": "NONE",
            "account": "PAS2524",
            "account_readback_casefold": True,
            "cpus": 1,
            "memory_mib": 1024,
            "time_limit_seconds": 600,
            "geant4_permitted": False,
            "external_journal_required": True,
        },
    }
    _write_json(directory / "preflight_twin.json", manifest)
    (directory / "README.md").write_text(
        f"# {twin_id}\n\n"
        "Sacrificial Phase-2C no-Geant4 preflight twin. This object has no "
        "production authority and is never a BC-S1 worker target.\n",
        encoding="utf-8",
    )
    _write_static_manifest(directory, TWIN_STATIC_FILES)
    _harden_static_tree(directory, TWIN_STATIC_FILES)


def materialize_preflight_v6(
    *,
    repo_root: Path,
    v5_dir: Path | None = None,
    v5_evidence_dir: Path | None = None,
    out_dir: Path | None = None,
    test_mode: bool = False,
    fixture_control_source: Path | None = None,
    v5_evidence_validator: V5EvidenceValidator | None = None,
    v5_execution_loader: V5ExecutionLoader | None = None,
    fault_after: str | None = None,
) -> Phase2CPreflightTwin:
    preview = preview_preflight_v6(
        repo_root=repo_root,
        v5_dir=v5_dir,
        v5_evidence_dir=v5_evidence_dir,
        test_mode=test_mode,
        fixture_control_source=fixture_control_source,
        v5_evidence_validator=v5_evidence_validator,
        v5_execution_loader=v5_execution_loader,
    )
    expected = canonical_preflight_v6_directory(preview.repo_root)
    target = _safe_directory(
        out_dir or expected, "preflight-v6 target", must_exist=False
    )
    if not test_mode and target != expected:
        raise ValueError(f"formal preflight-v6 path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite preflight-v6: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("preflight-v6 parent is missing or unsafe")
    readiness = preview.repo_root / PHASE2C_READINESS_LOCK_RELATIVE
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("preflight-v6 cannot be materialized after R6")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_preflight_tree(temporary, preview)
        if fault_after is not None:
            raise RuntimeError(f"injected Phase-2C preflight failure: {fault_after}")
        loaded = load_preflight_v6(
            temporary,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            evidence_dir=v5_evidence_dir,
            _allow_staging=True,
            _v5_evidence_validator=v5_evidence_validator,
            _test_predecessor=preview.predecessor if test_mode else None,
        )
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        return load_preflight_v6(
            target,
            repo_root=preview.repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            evidence_dir=v5_evidence_dir,
            _v5_evidence_validator=v5_evidence_validator,
            _test_predecessor=preview.predecessor if test_mode else None,
        )
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def preview_preflight_v6_retry(
    *,
    repo_root: Path,
    previous_dir: Path | None = None,
    test_mode: bool = False,
    _test_predecessor: Any | None = None,
) -> tuple[Phase2CPreflightTwin, dict[str, Any], int, str, Path]:
    root = _safe_directory(repo_root, "repository root")
    if not test_mode and previous_dir is not None:
        raise ValueError("formal Phase-2C retry cannot override its predecessor")
    previous = load_preflight_v6(
        previous_dir,
        repo_root=root if not test_mode else None,
        allow_test_mode=test_mode,
        _test_predecessor=_test_predecessor,
    )
    failure = validate_preflight_v6_failure(previous, required=True)
    assert failure is not None
    marker, evidence = failure
    if evidence.get("retry_eligible") is not True:
        raise ValueError("sealed Phase-2C failure is not retry-eligible")
    previous_ordinal = (
        require_dict(previous.manifest, "retry_lineage").get("retry_ordinal")
        if previous.manifest.get("retry_lineage") is not None
        else 0
    )
    if not isinstance(previous_ordinal, int) or previous_ordinal < 0:
        raise ValueError("Phase-2C previous retry ordinal is invalid")
    ordinal = previous_ordinal + 1
    if ordinal > 99:
        raise ValueError("Phase-2C retry ordinal limit exceeded")
    twin_hash = _retry_twin_authority_hash(
        previous_twin=previous,
        failure_evidence=evidence,
        ordinal=ordinal,
    )
    target = (
        canonical_preflight_v6_directory(root).parent
        / _retry_preflight_name(ordinal, twin_hash)
    )
    if test_mode and previous_dir is not None:
        target = previous.directory.parent / _retry_preflight_name(
            ordinal, twin_hash
        )
    if marker.get("failure_evidence_directory") != str(
        Path(require_string(marker, "failure_evidence_directory")).resolve()
    ):
        raise ValueError("Phase-2C failure evidence path is not canonical")
    return previous, evidence, ordinal, twin_hash, target


def _write_retry_preflight_tree(
    directory: Path,
    *,
    previous: Phase2CPreflightTwin,
    failure_evidence: dict[str, Any],
    ordinal: int,
    twin_hash: str,
) -> None:
    directory.mkdir()
    for name in (
        "tasks.tsv",
        "scan_args.txt",
        "source_archives.json",
        "source_recursive_checksums.json",
        "production_equivalence.json",
    ):
        shutil.copyfile(previous.directory / name, directory / name)
    (directory / "sources").mkdir()
    _copy_frozen_source(
        previous.directory / "sources/simulation",
        directory / "sources/simulation",
    )
    _copy_frozen_source(
        previous.directory / "sources/control",
        directory / "sources/control",
    )
    lock = _create_empty_portable_lock(directory)
    failure_result = validate_preflight_v6_failure(previous, required=True)
    assert failure_result is not None
    marker, current_failure = failure_result
    if current_failure != failure_evidence:
        raise ValueError("Phase-2C retry failure evidence changed during publish")
    evidence_directory = Path(
        require_string(marker, "failure_evidence_directory")
    )
    expected_hash = _retry_twin_authority_hash(
        previous_twin=previous,
        failure_evidence=failure_evidence,
        ordinal=ordinal,
    )
    if twin_hash != expected_hash:
        raise ValueError("Phase-2C retry preview identity changed")
    twin_id = (
        f"sm-v1-production-bc-s1-preflight-v6-retry-{ordinal:02d}-"
        f"{twin_hash[:12]}"
    )
    manifest = copy.deepcopy(previous.manifest)
    manifest.update(
        {
            "created_at_utc": utc_now(),
            "twin_id": twin_id,
            "twin_hash": twin_hash,
            "retry_lineage": {
                "retry_ordinal": ordinal,
                "previous_twin": _preflight_twin_binding(previous),
                "failure_evidence": _failure_evidence_binding(
                    evidence_directory, failure_evidence
                ),
            },
            "portable_lock": lock,
        }
    )
    manifest["artifacts"] = {
        "tasks": {
            "path": "tasks.tsv",
            "sha256": sha256_file(directory / "tasks.tsv"),
        },
        "scan_args": {
            "path": "scan_args.txt",
            "sha256": sha256_file(directory / "scan_args.txt"),
        },
        "source_archives": {
            "path": "source_archives.json",
            "sha256": sha256_file(directory / "source_archives.json"),
        },
        "source_recursive_checksums": {
            "path": "source_recursive_checksums.json",
            "sha256": sha256_file(
                directory / "source_recursive_checksums.json"
            ),
        },
        "production_equivalence": {
            "path": "production_equivalence.json",
            "sha256": sha256_file(directory / "production_equivalence.json"),
        },
        "control_lock": lock,
    }
    _write_json(directory / "preflight_twin.json", manifest)
    (directory / "README.md").write_text(
        f"# {twin_id}\n\n"
        "Content-addressed Phase-2C retry twin derived exclusively from its "
        "checksum-valid failed predecessor. It has no production authority.\n",
        encoding="utf-8",
    )
    _write_static_manifest(directory, TWIN_STATIC_FILES)
    _harden_static_tree(directory, TWIN_STATIC_FILES)


def materialize_preflight_v6_retry(
    *,
    repo_root: Path,
    previous_dir: Path | None = None,
    out_dir: Path | None = None,
    test_mode: bool = False,
    _test_predecessor: Any | None = None,
    fault_after: str | None = None,
) -> Phase2CPreflightTwin:
    previous, evidence, ordinal, twin_hash, expected = (
        preview_preflight_v6_retry(
            repo_root=repo_root,
            previous_dir=previous_dir,
            test_mode=test_mode,
            _test_predecessor=_test_predecessor,
        )
    )
    if not test_mode and out_dir is not None:
        raise ValueError("formal Phase-2C retry target cannot be overridden")
    target = _safe_directory(
        out_dir or expected, "Phase-2C retry target", must_exist=False
    )
    if not test_mode and target != expected:
        raise ValueError("formal Phase-2C retry target is not canonical")
    if target.exists() or target.is_symlink():
        raise ValueError("refusing to overwrite Phase-2C retry twin")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("Phase-2C retry parent is missing or unsafe")
    if (repo_root / PHASE2C_READINESS_LOCK_RELATIVE).exists():
        raise ValueError("Phase-2C retry cannot be materialized after R6")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_retry_preflight_tree(
            temporary,
            previous=previous,
            failure_evidence=evidence,
            ordinal=ordinal,
            twin_hash=twin_hash,
        )
        if fault_after is not None:
            raise RuntimeError(f"injected Phase-2C retry failure: {fault_after}")
        loaded = load_preflight_v6(
            temporary,
            repo_root=repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            _allow_staging=True,
            _test_predecessor=_test_predecessor or previous.predecessor,
            _test_previous_twin=previous if test_mode else None,
        )
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        return load_preflight_v6(
            target,
            repo_root=repo_root if not test_mode else None,
            allow_test_mode=test_mode,
            _test_predecessor=_test_predecessor or previous.predecessor,
            _test_previous_twin=previous if test_mode else None,
        )
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def load_preflight_v6(
    directory: Path | None = None,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    evidence_dir: Path | None = None,
    _allow_staging: bool = False,
    _v5_evidence_validator: V5EvidenceValidator | None = None,
    _verify_live_predecessor: bool = True,
    _verify_runtime: bool = True,
    _verify_phase2a_control_plane: bool = True,
    _test_predecessor: Any | None = None,
    _test_previous_twin: Phase2CPreflightTwin | None = None,
) -> Phase2CPreflightTwin:
    if directory is None:
        if repo_root is None:
            raise ValueError("formal preflight-v6 loader requires repo_root")
        directory = current_formal_preflight_v6_directory(repo_root)
    root = _safe_directory(directory, "preflight-v6")
    _verify_static_manifest(root, TWIN_STATIC_FILES)
    actual = {path.name for path in root.iterdir()}
    allowed_roots = {
        frozenset(TWIN_ROOT_ENTRIES),
        frozenset(TWIN_ROOT_ENTRIES | {PREFLIGHT_CLOSURE_MARKER}),
        frozenset(TWIN_ROOT_ENTRIES | {PREFLIGHT_FAILURE_MARKER}),
        frozenset(
            TWIN_ROOT_ENTRIES
            | {PREFLIGHT_CLOSURE_MARKER, PREFLIGHT_FAILURE_MARKER}
        ),
    }
    if frozenset(actual) not in allowed_roots or any(path.is_symlink() for path in root.iterdir()):
        raise ValueError("preflight-v6 root entry set is unsafe")
    manifest = load_json(root / "preflight_twin.json")
    if (
        manifest.get("schema_version") != PREFLIGHT_TWIN_SCHEMA_VERSION
        or manifest.get("object_kind") != PREFLIGHT_OBJECT_KIND
        or manifest.get("role") != "sacrificial-no-geant4-preflight-only"
        or manifest.get("production_authority") is not False
    ):
        raise ValueError("unsupported or authority-bearing preflight-v6")
    test_mode = manifest.get("test_mode")
    if not isinstance(test_mode, bool) or (test_mode and not allow_test_mode):
        raise ValueError("preflight-v6 evidence mode is not admissible")
    if manifest.get("accepted_predecessor_evidence") is not (not test_mode):
        raise ValueError("preflight-v6 predecessor acceptance flag mismatch")
    tasks = parse_campaign_tasks(root / "tasks.tsv")
    scan_args = _read_scan_args(root / "scan_args.txt")
    if require_dict(manifest, "shape") != _exact_bc_s1_shape(tasks):
        raise ValueError("preflight-v6 shape mismatch")
    archives = load_json(root / "source_archives.json")
    checksums = load_json(root / "source_recursive_checksums.json")
    equivalence = load_json(root / "production_equivalence.json")
    if manifest.get("sources") != archives:
        raise ValueError("preflight-v6 source archive binding mismatch")
    _validate_frozen_sources(root, archives)
    _validate_source_checksum_payload(root, checksums)
    expected_equivalence = _production_equivalence(
        tasks=tasks,
        tasks_sha256=sha256_file(root / "tasks.tsv"),
        scan_args_sha256=sha256_file(root / "scan_args.txt"),
        source_archives=archives,
        source_checksums=checksums,
        runtime=require_dict(manifest, "runtime"),
        control_root=root / "sources/control",
    )
    if equivalence != expected_equivalence:
        raise ValueError("preflight-v6 production equivalence mismatch")
    if manifest.get("production_equivalence_hash") != equivalence.get(
        "production_equivalence_hash"
    ):
        raise ValueError("preflight-v6 equivalence hash binding mismatch")
    _validate_empty_portable_lock(root, require_dict(manifest, "portable_lock"))
    _validate_artifacts(root, manifest)
    predecessor: Any | None = None
    predecessor_record = require_dict(manifest, "predecessor_v5")
    if not test_mode:
        if repo_root is None:
            raise ValueError("formal preflight-v6 validation requires repo_root")
        live_repo = repo_root.expanduser().resolve()
        candidates = _formal_preflight_candidates(live_repo)
        if root not in candidates and not (
            _allow_staging
            and root.parent == canonical_preflight_v6_directory(live_repo).parent
            and root.name.startswith(".steel-module-production-bc-s1-preflight-v6")
        ):
            raise ValueError("formal preflight-v6 path is not in the fixed lineage")
        predecessor = _load_formal_or_test_v5(
            repo_root=live_repo,
            v5_dir=Path(require_string(predecessor_record, "directory")),
            test_mode=False,
            verify_live_predecessor=_verify_live_predecessor,
            verify_runtime=_verify_runtime,
            verify_phase2a_control_plane=_verify_phase2a_control_plane,
        )
        recorded_evidence = require_dict(predecessor_record, "accepted_r3_evidence")
        chosen_evidence = evidence_dir or Path(require_string(recorded_evidence, "directory"))
        validator = _v5_evidence_validator or _default_v5_evidence_validator
        current_evidence = validator(predecessor, chosen_evidence)
        _validate_v5_identity(predecessor, current_evidence, test_mode=False)
        if predecessor_record != _v5_binding(predecessor, current_evidence):
            raise ValueError("preflight-v6 execution-v5 lineage changed")
        if tasks != predecessor.tasks or scan_args != predecessor.scan_args:
            raise ValueError("preflight-v6 task/scan bytes differ from execution-v5")
        phase2a_path, phase2a = load_phase2a_lock(live_repo)
        _validate_phase2a_binding(predecessor.managed_child, phase2a_path, phase2a)
    elif _test_predecessor is not None:
        if (
            _test_predecessor.execution_id != predecessor_record.get("execution_id")
            or _test_predecessor.execution_hash
            != predecessor_record.get("execution_hash")
        ):
            raise ValueError("test preflight-v6 predecessor identity mismatch")
        predecessor = _test_predecessor
    retry_lineage = manifest.get("retry_lineage")
    previous_twin: Phase2CPreflightTwin | None = None
    failure_evidence: dict[str, Any] | None = None
    if retry_lineage is None:
        twin_hash = _twin_authority_hash(
            type("RecordedV5", (), {
                "execution_id": predecessor_record.get("execution_id"),
                "execution_hash": predecessor_record.get("execution_hash"),
            })(),
            require_dict(predecessor_record, "accepted_r3_evidence"),
            equivalence,
        )
        expected_id = f"sm-v1-production-bc-s1-preflight-v6-{twin_hash[:12]}"
        if not test_mode and root.name != FORMAL_PREFLIGHT_V6_NAME and not _allow_staging:
            raise ValueError("initial Phase-2C preflight twin path changed")
    else:
        retry = require_dict(manifest, "retry_lineage")
        _require_exact_keys(
            retry,
            {"retry_ordinal", "previous_twin", "failure_evidence"},
            "Phase-2C retry lineage",
        )
        ordinal = retry.get("retry_ordinal")
        if not isinstance(ordinal, int) or ordinal < 1 or ordinal > 99:
            raise ValueError("Phase-2C retry ordinal is invalid")
        previous_record = require_dict(retry, "previous_twin")
        failure_record = require_dict(retry, "failure_evidence")
        if test_mode and _test_previous_twin is not None:
            previous_twin = _test_previous_twin
        else:
            previous_twin = load_preflight_v6(
                Path(require_string(previous_record, "directory")),
                repo_root=repo_root if not test_mode else None,
                allow_test_mode=test_mode,
                evidence_dir=evidence_dir,
                _v5_evidence_validator=_v5_evidence_validator,
                _verify_live_predecessor=_verify_live_predecessor,
                _verify_runtime=_verify_runtime,
                _verify_phase2a_control_plane=_verify_phase2a_control_plane,
                _test_predecessor=_test_predecessor,
            )
        if previous_record != _preflight_twin_binding(previous_twin):
            raise ValueError("Phase-2C retry previous-twin binding changed")
        previous_failure = validate_preflight_v6_failure(
            previous_twin, required=True
        )
        assert previous_failure is not None
        _marker, failure_evidence = previous_failure
        failure_directory = Path(require_string(failure_record, "directory"))
        if failure_record != _failure_evidence_binding(
            failure_directory, failure_evidence
        ):
            raise ValueError("Phase-2C retry failure-evidence binding changed")
        if failure_evidence.get("retry_eligible") is not True:
            raise ValueError("Phase-2C retry predecessor is not retry-eligible")
        previous_ordinal = (
            require_dict(previous_twin.manifest, "retry_lineage").get(
                "retry_ordinal"
            )
            if previous_twin.manifest.get("retry_lineage") is not None
            else 0
        )
        if ordinal != previous_ordinal + 1:
            raise ValueError("Phase-2C retry ordinal does not extend its predecessor")
        if (
            tasks != previous_twin.tasks
            or scan_args != previous_twin.scan_args
            or equivalence.get("production_equivalence_hash")
            != previous_twin.production_equivalence_hash
        ):
            raise ValueError("Phase-2C retry changed production inputs")
        twin_hash = _retry_twin_authority_hash(
            previous_twin=previous_twin,
            failure_evidence=failure_evidence,
            ordinal=ordinal,
        )
        expected_id = (
            f"sm-v1-production-bc-s1-preflight-v6-retry-{ordinal:02d}-"
            f"{twin_hash[:12]}"
        )
        if not test_mode and root.name != _retry_preflight_name(ordinal, twin_hash) and not _allow_staging:
            raise ValueError("Phase-2C retry twin path changed")
    if manifest.get("twin_hash") != twin_hash or manifest.get("twin_id") != expected_id:
        raise ValueError("preflight-v6 content identity mismatch")
    result = Phase2CPreflightTwin(
        root,
        manifest,
        predecessor,
        tasks,
        scan_args,
        previous_twin,
        failure_evidence,
    )
    validate_preflight_v6_closure(result, required=False)
    validate_preflight_v6_failure(result, required=False)
    return result


def _validate_phase2c_evidence(
    evidence_dir: Path,
    *,
    twin: Phase2CPreflightTwin,
) -> dict[str, Any]:
    from steel_module_production_phase2c_preflight_lib import (
        validate_phase2c_preflight_evidence,
    )

    evidence = validate_phase2c_preflight_evidence(
        evidence_dir,
        expected_twin=twin,
        allow_test_mode=twin.manifest.get("test_mode") is True,
    )
    if (
        evidence.get("schema_version") != PREFLIGHT_EVIDENCE_SCHEMA_VERSION
        or evidence.get("test_mode") is not twin.manifest.get("test_mode")
        or evidence.get("accepted_compute_preflight_evidence")
        is not (twin.manifest.get("test_mode") is False)
        or evidence.get("production_equivalence_hash")
        != twin.production_equivalence_hash
        or evidence.get("v5_evidence_hash")
        != require_sha256(
            require_dict(require_dict(twin.manifest, "predecessor_v5"), "accepted_r3_evidence"),
            "evidence_hash",
        )
        or evidence.get("twin_execution_closed") is not True
    ):
        raise ValueError("Phase-2C preflight evidence is not admissible")
    runtime = require_dict(evidence, "runtime_boundary")
    consumption = require_dict(evidence, "consumption")
    if (
        runtime.get("apptainer_invoked") is not True
        or runtime.get("geant4_invoked") is not False
        or consumption.get("events_consumed") != 0
        or consumption.get("production_seeds_consumed") != 0
    ):
        raise ValueError("Phase-2C preflight crossed the no-Geant4 boundary")
    return evidence


def _phase2c_evidence_binding(directory: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "directory": str(directory.expanduser().resolve()),
        "schema_version": evidence.get("schema_version"),
        "evidence_id": require_string(evidence, "evidence_id"),
        "evidence_hash": require_sha256(evidence, "evidence_hash"),
        "evidence_json_sha256": sha256_file(directory / "evidence.json"),
        "checksum_manifest_sha256": sha256_file(directory / "SHA256SUMS"),
        "job_id": require_string(require_dict(evidence, "scheduler"), "job_id"),
    }


def _phase2c_evidence_authority_record(
    directory: Path, evidence: dict[str, Any]
) -> dict[str, Any]:
    record = json.loads(json.dumps(evidence))
    record["bundle_identity"] = _phase2c_evidence_binding(directory, evidence)
    record["job_id"] = require_string(require_dict(evidence, "scheduler"), "job_id")
    return record


def _execution_v6_authority_hash(
    twin: Phase2CPreflightTwin,
    evidence_binding: dict[str, Any],
) -> str:
    return sha256_bytes(
        canonical_json(
            {
                "schema_version": EXECUTION_V6_SCHEMA_VERSION,
                "twin_hash": twin.twin_hash,
                "phase2c_preflight_evidence_hash": evidence_binding[
                    "evidence_hash"
                ],
                "production_equivalence_hash": twin.production_equivalence_hash,
            }
        )
    )


def _copy_twin_static_inputs(twin: Phase2CPreflightTwin, directory: Path) -> None:
    for name in (
        "tasks.tsv",
        "scan_args.txt",
        "source_archives.json",
        "source_recursive_checksums.json",
        "production_equivalence.json",
    ):
        shutil.copyfile(twin.directory / name, directory / name)
    (directory / "sources").mkdir()
    _copy_frozen_source(
        twin.directory / "sources/simulation", directory / "sources/simulation"
    )
    _copy_frozen_source(
        twin.directory / "sources/control", directory / "sources/control"
    )


def _preflight_retry_history(
    twin: Phase2CPreflightTwin,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if twin.previous_twin is not None:
        history.extend(_preflight_retry_history(twin.previous_twin))
        if twin.failure_evidence is None:
            raise ValueError("Phase-2C retry twin lost its failure evidence")
        failure_dir = Path(
            require_string(
                require_dict(twin.manifest, "retry_lineage")[
                    "failure_evidence"
                ],
                "directory",
            )
        )
        history.append(
            {
                "failed_twin": _preflight_twin_binding(twin.previous_twin),
                "failure_evidence": _failure_evidence_binding(
                    failure_dir, twin.failure_evidence
                ),
            }
        )
    return history


def _write_execution_v6_tree(
    directory: Path,
    *,
    twin: Phase2CPreflightTwin,
    evidence_dir: Path,
    evidence: dict[str, Any],
) -> None:
    directory.mkdir()
    for name in ("intents", "attempts", "finalized"):
        (directory / name).mkdir()
    lock = _create_empty_portable_lock(directory)
    _copy_twin_static_inputs(twin, directory)
    binding = _phase2c_evidence_binding(evidence_dir, evidence)
    _write_json(directory / "preflight_evidence.json", binding)
    execution_hash = _execution_v6_authority_hash(twin, binding)
    execution_id = f"sm-v1-production-bc-s1-execution-v6-{execution_hash[:12]}"
    job_id = binding["job_id"]
    retry_history = _preflight_retry_history(twin)
    failed_job_ids = [
        job
        for row in retry_history
        for job in row["failure_evidence"].get("scheduler_job_ids", [])
    ]
    excluded = [
        *FORMAL_HISTORICAL_NONPRODUCTION_JOB_IDS,
        *failed_job_ids,
        job_id,
    ]
    if len(excluded) != len(set(excluded)):
        raise ValueError("Phase-2C preflight reused a historical scheduler job")
    v5_record = require_dict(twin.manifest, "predecessor_v5")
    raw_lineage = {
        "schema_version": "steel-module-production-phase2c-lineage-v1",
        "phase2a_lock": twin.manifest["phase2a_lock"],
        "managed_child": twin.manifest["managed_child"],
        "program": twin.manifest["program"],
        "execution_v5": v5_record,
        "preflight_twin": {
            "directory": str(twin.directory),
            "twin_id": twin.twin_id,
            "twin_hash": twin.twin_hash,
            "static_manifest_sha256": sha256_file(
                twin.directory / ROOT_STATIC_MANIFEST
            ),
        },
        "preflight_retry_history": retry_history,
        "phase2c_preflight_evidence": binding,
        "production_equivalence_hash": twin.production_equivalence_hash,
        "excluded_nonproduction_job_ids": excluded,
    }
    authorization_hash = require_sha256(evidence, "authorization_hash")
    evidence_attempt = f"phase2c-preflight-{authorization_hash[:12]}"
    excluded_attempts = [*FORMAL_HISTORICAL_EXCLUDED_ATTEMPT_IDS, evidence_attempt]
    for row in retry_history:
        failure_dir = Path(row["failure_evidence"]["directory"])
        failure_value = load_json(failure_dir / "failure_evidence.json")
        failed_authorization = failure_value.get("authorization_hash")
        if isinstance(failed_authorization, str):
            excluded_attempts.insert(
                -1, f"phase2c-preflight-{failed_authorization[:12]}"
            )
    if len(excluded_attempts) != len(set(excluded_attempts)):
        raise ValueError("Phase-2C preflight reused a historical attempt identity")
    phase2c_authority: dict[str, Any] = {
        "accepted_v5_evidence": v5_record["accepted_r3_evidence"],
        "preflight_twin": raw_lineage["preflight_twin"],
        "preflight_retry_history": retry_history,
        "phase2c_preflight_evidence": _phase2c_evidence_authority_record(
            evidence_dir, evidence
        ),
        "production_equivalence_hash": twin.production_equivalence_hash,
        "zero_consumption": {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
            "seed_reuse_authorized": True,
        },
        "excluded_attempt_ids": excluded_attempts,
        "excluded_job_ids": excluded,
        "authority_hash": None,
    }
    phase2c_authority["authority_hash"] = _semantic_hash(
        phase2c_authority, "authority_hash"
    )
    recovery_authority = json.loads(json.dumps(v5_record["recovery_authority"]))
    if require_dict(recovery_authority, "zero_consumption") != {
        "events_consumed": 0,
        "production_seeds_consumed": 0,
        "seed_reuse_authorized": True,
    }:
        raise ValueError("execution-v5 recovery zero-consumption contract changed")
    manifest = {
        "schema_version": EXECUTION_V6_SCHEMA_VERSION,
        "object_kind": EXECUTION_V6_OBJECT_KIND,
        "execution_generation": "production-ready-execution-v6",
        "created_at_utc": utc_now(),
        "test_mode": twin.manifest["test_mode"],
        "accepted_statistical_evidence": twin.manifest["test_mode"] is False,
        "execution_id": execution_id,
        "execution_hash": execution_hash,
        "phase": "Phase-2C-C6",
        "managed_child": twin.manifest["managed_child"],
        "program": twin.manifest["program"],
        "phase2a_lock": twin.manifest["phase2a_lock"],
        "shape": twin.manifest["shape"],
        "runtime": twin.manifest["runtime"],
        "sealed_pilot": twin.manifest["sealed_pilot"],
        "sources": twin.manifest["sources"],
        "production_equivalence_hash": twin.production_equivalence_hash,
        "portable_lock": lock,
        "artifacts": {
            "tasks": {"path": "tasks.tsv", "sha256": sha256_file(directory / "tasks.tsv")},
            "scan_args": {"path": "scan_args.txt", "sha256": sha256_file(directory / "scan_args.txt")},
            "source_archives": {"path": "source_archives.json", "sha256": sha256_file(directory / "source_archives.json")},
            "source_recursive_checksums": {"path": "source_recursive_checksums.json", "sha256": sha256_file(directory / "source_recursive_checksums.json")},
            "production_equivalence": {"path": "production_equivalence.json", "sha256": sha256_file(directory / "production_equivalence.json")},
            "control_lock": lock,
        },
        "recovery_authority": recovery_authority,
        "phase2c_authority": phase2c_authority,
        "lineage": raw_lineage,
        "submission_policy": {
            **PRODUCTION_SCHEDULER_POLICY,
            "formal_child": "BC-S1",
            "first_intent_must_select_all_tasks": True,
            "subset_submission_forbidden": True,
        },
        "readiness_gate": {
            "tracked_path": PHASE2C_READINESS_LOCK_RELATIVE.as_posix(),
            "schema_version": READINESS_V6_SCHEMA_VERSION,
            "required_for_any_production_action": True,
            "automatic_submission": False,
            "c6_submission_ready": False,
        },
    }
    _write_json(directory / "managed_execution.json", manifest)
    (directory / "README.md").write_text(
        f"# {execution_id}\n\n"
        "Clean Phase-2C BC-S1 execution-v6. Its frozen production inputs are "
        "byte-equivalent to the successful sacrificial preflight twin. It "
        "remains non-submittable until the tracked lock-only R6 commit.\n",
        encoding="utf-8",
    )
    _write_static_manifest(directory, EXECUTION_V6_STATIC_FILES)
    _harden_static_tree(directory, EXECUTION_V6_STATIC_FILES)


def materialize_execution_v6(
    *,
    repo_root: Path,
    preflight_dir: Path | None = None,
    evidence_dir: Path,
    out_dir: Path | None = None,
    test_mode: bool = False,
    fault_after: str | None = None,
) -> Phase2CExecutionV6:
    root = _safe_directory(repo_root, "repository root")
    twin = load_preflight_v6(
        preflight_dir,
        repo_root=root if not test_mode else None,
        allow_test_mode=test_mode,
    )
    if twin.manifest.get("test_mode") is not test_mode:
        raise ValueError("execution-v6/twin evidence mode mismatch")
    validate_preflight_v6_closure(twin, required=True)
    evidence_path = _safe_directory(evidence_dir, "Phase-2C preflight evidence")
    evidence = _validate_phase2c_evidence(evidence_path, twin=twin)
    expected = canonical_execution_v6_directory(root)
    target = _safe_directory(
        out_dir or expected, "execution-v6 target", must_exist=False
    )
    if not test_mode and target != expected:
        raise ValueError(f"formal execution-v6 path is fixed: {expected}")
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite execution-v6: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError("execution-v6 parent is missing or unsafe")
    readiness = root / PHASE2C_READINESS_LOCK_RELATIVE
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("execution-v6 materialization requires R6 to be absent")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    )
    temporary.rmdir()
    try:
        _write_execution_v6_tree(
            temporary, twin=twin, evidence_dir=evidence_path, evidence=evidence
        )
        if fault_after is not None:
            raise RuntimeError(f"injected execution-v6 failure: {fault_after}")
        loaded = load_execution_v6(
            temporary,
            repo_root=root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            require_pristine=True,
            _allow_staging=True,
        )
        fsync_tree(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        return load_execution_v6(
            target,
            repo_root=root if not test_mode else None,
            allow_test_mode=test_mode,
            require_readiness=False,
            require_pristine=True,
        )
    finally:
        if temporary is not None and temporary.exists():
            _remove_tree(temporary)


def load_execution_v6(
    directory: Path | None = None,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = False,
    require_pristine: bool = False,
    verify_runtime: bool = True,
    verify_live_predecessor: bool = True,
    verify_phase2a_control_plane: bool = True,
    _allow_staging: bool = False,
) -> Phase2CExecutionV6:
    if directory is None:
        if repo_root is None:
            raise ValueError("formal execution-v6 loader requires repo_root")
        directory = canonical_execution_v6_directory(repo_root)
    root = _safe_directory(directory, "execution-v6")
    _verify_static_manifest(root, EXECUTION_V6_STATIC_FILES)
    actual = {path.name for path in root.iterdir()}
    if actual != EXECUTION_V6_ROOT_ENTRIES or any(path.is_symlink() for path in root.iterdir()):
        raise ValueError("execution-v6 root entry set is unsafe")
    for name in ("intents", "attempts", "finalized"):
        mutable = root / name
        if mutable.is_symlink() or not mutable.is_dir():
            raise ValueError(f"execution-v6 mutable root is unsafe: {name}")
        if require_pristine and any(mutable.iterdir()):
            raise ValueError(f"clean execution-v6 requires pristine {name}/")
    manifest = load_json(root / "managed_execution.json")
    if (
        manifest.get("schema_version") != EXECUTION_V6_SCHEMA_VERSION
        or manifest.get("object_kind") != EXECUTION_V6_OBJECT_KIND
        or manifest.get("execution_generation") != "production-ready-execution-v6"
    ):
        raise ValueError("unsupported execution-v6 manifest")
    test_mode = manifest.get("test_mode")
    if not isinstance(test_mode, bool) or (test_mode and not allow_test_mode):
        raise ValueError("execution-v6 evidence mode is not admissible")
    if manifest.get("accepted_statistical_evidence") is not (not test_mode):
        raise ValueError("execution-v6 evidence acceptance flag mismatch")
    tasks = parse_campaign_tasks(root / "tasks.tsv")
    scan_args = _read_scan_args(root / "scan_args.txt")
    if require_dict(manifest, "shape") != _exact_bc_s1_shape(tasks):
        raise ValueError("execution-v6 task shape mismatch")
    archives = load_json(root / "source_archives.json")
    checksums = load_json(root / "source_recursive_checksums.json")
    equivalence = load_json(root / "production_equivalence.json")
    if manifest.get("sources") != archives:
        raise ValueError("execution-v6 source archive binding mismatch")
    _validate_frozen_sources(root, archives)
    _validate_source_checksum_payload(root, checksums)
    expected_equivalence = _production_equivalence(
        tasks=tasks,
        tasks_sha256=sha256_file(root / "tasks.tsv"),
        scan_args_sha256=sha256_file(root / "scan_args.txt"),
        source_archives=archives,
        source_checksums=checksums,
        runtime=require_dict(manifest, "runtime"),
        control_root=root / "sources/control",
    )
    if equivalence != expected_equivalence or manifest.get(
        "production_equivalence_hash"
    ) != equivalence.get("production_equivalence_hash"):
        raise ValueError("execution-v6 production equivalence changed")
    _validate_empty_portable_lock(root, require_dict(manifest, "portable_lock"))
    _validate_artifacts(root, manifest)
    evidence_binding = load_json(root / "preflight_evidence.json")
    lineage = require_dict(manifest, "lineage")
    if require_dict(lineage, "phase2c_preflight_evidence") != evidence_binding:
        raise ValueError("execution-v6 preflight evidence binding mismatch")
    execution_hash = _execution_v6_authority_hash(
        type("RecordedTwin", (), {
            "twin_hash": require_sha256(require_dict(lineage, "preflight_twin"), "twin_hash"),
            "production_equivalence_hash": require_sha256(lineage, "production_equivalence_hash"),
        })(),
        evidence_binding,
    )
    if (
        manifest.get("execution_hash") != execution_hash
        or manifest.get("execution_id")
        != f"sm-v1-production-bc-s1-execution-v6-{execution_hash[:12]}"
    ):
        raise ValueError("execution-v6 content identity mismatch")
    policy = require_dict(manifest, "submission_policy")
    for key, value in PRODUCTION_SCHEDULER_POLICY.items():
        if policy.get(key) != value:
            raise ValueError(f"execution-v6 scheduler policy changed: {key}")
    if (
        policy.get("formal_child") != "BC-S1"
        or policy.get("first_intent_must_select_all_tasks") is not True
        or policy.get("subset_submission_forbidden") is not True
    ):
        raise ValueError("execution-v6 predecessor-retry admission changed")
    excluded = lineage.get("excluded_nonproduction_job_ids")
    retry_history = lineage.get("preflight_retry_history")
    if not isinstance(retry_history, list):
        raise ValueError("execution-v6 retry history is invalid")
    retry_job_ids: list[str] = []
    for row in retry_history:
        if not isinstance(row, dict) or set(row) != {
            "failed_twin", "failure_evidence"
        }:
            raise ValueError("execution-v6 retry history row is invalid")
        failed_twin = require_dict(row, "failed_twin")
        failure = require_dict(row, "failure_evidence")
        for key in ("twin_hash", "static_manifest_sha256"):
            require_sha256(failed_twin, key)
        require_string(failed_twin, "directory")
        require_string(failed_twin, "twin_id")
        require_sha256(failure, "failure_evidence_hash")
        require_sha256(failure, "evidence_json_sha256")
        require_sha256(failure, "checksum_manifest_sha256")
        jobs = failure.get("scheduler_job_ids")
        if (
            failure.get("schema_version")
            != PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION
            or failure.get("retry_eligible") is not True
            or not isinstance(jobs, list)
            or any(not isinstance(job, str) or not job.isdigit() for job in jobs)
        ):
            raise ValueError("execution-v6 retry failure binding is invalid")
        retry_job_ids.extend(jobs)
    final_job_id = require_string(
        require_dict(require_dict(authority := require_dict(manifest, "phase2c_authority"), "phase2c_preflight_evidence"), "scheduler"),
        "job_id",
    )
    expected_excluded = [
        *FORMAL_HISTORICAL_NONPRODUCTION_JOB_IDS,
        *retry_job_ids,
        final_job_id,
    ]
    if (
        not isinstance(excluded, list)
        or excluded != expected_excluded
        or len(excluded) != len(set(excluded))
    ):
        raise ValueError("execution-v6 nonproduction job exclusions changed")
    if (
        authority.get("authority_hash") != _semantic_hash(authority, "authority_hash")
        or authority.get("accepted_v5_evidence")
        != require_dict(require_dict(lineage, "execution_v5"), "accepted_r3_evidence")
        or require_dict(
            require_dict(authority, "phase2c_preflight_evidence"),
            "bundle_identity",
        ) != evidence_binding
        or authority.get("preflight_twin") != lineage.get("preflight_twin")
        or authority.get("preflight_retry_history") != retry_history
        or authority.get("production_equivalence_hash")
        != manifest.get("production_equivalence_hash")
        or authority.get("excluded_job_ids") != excluded
        or require_dict(authority, "zero_consumption") != {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
            "seed_reuse_authorized": True,
        }
        or require_dict(require_dict(manifest, "recovery_authority"), "zero_consumption")
        != require_dict(authority, "zero_consumption")
        or manifest.get("recovery_authority")
        != require_dict(lineage, "execution_v5").get("recovery_authority")
    ):
        raise ValueError("execution-v6 Phase-2C authority changed")
    managed_child: Any = None
    if not test_mode:
        if repo_root is None:
            raise ValueError("formal execution-v6 validation requires repo_root")
        live_repo = repo_root.expanduser().resolve()
        expected = canonical_execution_v6_directory(live_repo)
        if root != expected and not (
            _allow_staging
            and root.parent == expected.parent
            and root.name.startswith(f".{expected.name}.tmp-")
        ):
            raise ValueError(f"formal execution-v6 path is fixed: {expected}")
        twin_record = require_dict(lineage, "preflight_twin")
        twin = load_preflight_v6(
            Path(require_string(twin_record, "directory")),
            repo_root=live_repo,
            _verify_live_predecessor=verify_live_predecessor,
            _verify_runtime=verify_runtime,
            _verify_phase2a_control_plane=verify_phase2a_control_plane,
        )
        if (
            twin_record.get("twin_id") != twin.twin_id
            or twin_record.get("twin_hash") != twin.twin_hash
            or twin_record.get("static_manifest_sha256")
            != sha256_file(twin.directory / ROOT_STATIC_MANIFEST)
            or twin.production_equivalence_hash
            != manifest.get("production_equivalence_hash")
        ):
            raise ValueError("execution-v6 twin lineage changed")
        if retry_history != _preflight_retry_history(twin):
            raise ValueError("execution-v6 retry history changed")
        evidence_path = Path(require_string(evidence_binding, "directory"))
        current_evidence = _validate_phase2c_evidence(evidence_path, twin=twin)
        if evidence_binding != _phase2c_evidence_binding(evidence_path, current_evidence):
            raise ValueError("execution-v6 Phase-2C evidence changed")
        if authority.get("phase2c_preflight_evidence") != (
            _phase2c_evidence_authority_record(evidence_path, current_evidence)
        ):
            raise ValueError("execution-v6 compute-preflight authority changed")
        if tasks != twin.tasks or scan_args != twin.scan_args:
            raise ValueError("execution-v6 differs from its preflight twin")
        managed_child = twin.predecessor.managed_child if twin.predecessor else None
        phase2a_path, phase2a = load_phase2a_lock(live_repo)
        if managed_child is None:
            raise ValueError("formal execution-v6 lost managed-child lineage")
        _validate_phase2a_binding(managed_child, phase2a_path, phase2a)
    else:
        managed_child = load_managed_production_child(
            Path(require_string(require_dict(manifest, "managed_child"), "directory")),
            verify_runtime_artifacts=False,
            verify_control_plane=False,
            allow_test_mode=True,
        )
    if require_readiness:
        if test_mode:
            raise ValueError("test-mode execution-v6 cannot satisfy formal R6")
        from steel_module_production_phase2c_readiness import (
            verify_phase2c_readiness,
        )

        verify_phase2c_readiness(
            Phase2CExecutionV6(root, manifest, managed_child, tasks, scan_args),
            repo_root=repo_root,
        )
    return Phase2CExecutionV6(root, manifest, managed_child, tasks, scan_args)


def load_execution_v6_for_frozen_worker(
    execution_dir: Path,
    *,
    control_root: Path,
) -> Phase2CExecutionV6:
    execution = load_execution_v6(
        execution_dir,
        repo_root=control_root,
        allow_test_mode=False,
        require_readiness=False,
        require_pristine=False,
        verify_runtime=True,
        verify_live_predecessor=False,
        verify_phase2a_control_plane=False,
    )
    expected_control = execution.directory / "sources/control"
    if control_root.expanduser().resolve() != expected_control.resolve():
        raise ValueError("execution-v6 frozen worker control root mismatch")
    # Frozen source bytes and all critical admissions were already re-derived
    # above. R6 must still be checked from the live repository locator recorded
    # by the readiness module; the worker must never infer authority locally.
    from steel_module_production_phase2c_readiness import (
        verify_phase2c_readiness,
    )

    verify_phase2c_readiness(execution, repo_root=None)
    return execution


def phase2c_recovery_lineage(execution: Phase2CExecutionV6) -> dict[str, Any]:
    """Return the downstream-safe v4 recovery lineage for execution-v6."""

    manifest = execution.manifest
    if manifest.get("schema_version") != EXECUTION_V6_SCHEMA_VERSION:
        raise ValueError("Phase-2C lineage requires execution-v6")
    authority = require_dict(manifest, "phase2c_authority")
    if authority.get("authority_hash") != _semantic_hash(authority, "authority_hash"):
        raise ValueError("Phase-2C authority semantic hash mismatch")
    attempts = authority.get("excluded_attempt_ids")
    jobs = authority.get("excluded_job_ids")
    if (
        not isinstance(attempts, list)
        or not attempts
        or len(attempts) != len(set(attempts))
        or not isinstance(jobs, list)
        or not jobs
        or len(jobs) != len(set(jobs))
    ):
        raise ValueError("Phase-2C lineage exclusions are invalid")
    payload: dict[str, Any] = {
        "schema_version": "steel-module-managed-recovery-lineage-v4",
        "successor_execution": {
            "directory": str(execution.directory),
            "schema_version": manifest.get("schema_version"),
            "object_kind": manifest.get("object_kind"),
            "execution_generation": manifest.get("execution_generation"),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
        },
        "phase2c_authority": json.loads(json.dumps(authority)),
        "production_equivalence_hash": require_sha256(
            manifest, "production_equivalence_hash"
        ),
        "excluded_attempt_ids": list(attempts),
        "excluded_job_ids": list(jobs),
        "result_scope": {
            "source_execution_directory": str(execution.directory),
            "selected_results": "execution-v6-production-successes-only",
            "historical_execution_outputs_included": False,
            "historical_execution_accounting_included": False,
            "phase2c_preflight_outputs_included": False,
            "phase2c_preflight_accounting_included": False,
        },
    }
    payload["lineage_hash"] = sha256_bytes(canonical_json(payload))
    return payload


def validate_phase2c_recovery_lineage(
    execution_record: Phase2CExecutionV6 | dict[str, Any],
    lineage: dict[str, Any],
) -> dict[str, Any]:
    """Validate one persisted v4 lineage against an execution record."""

    if isinstance(execution_record, Phase2CExecutionV6):
        expected_record = {
            "directory": str(execution_record.directory),
            "schema_version": execution_record.manifest.get("schema_version"),
            "object_kind": execution_record.manifest.get("object_kind"),
            "execution_generation": execution_record.manifest.get(
                "execution_generation"
            ),
            "execution_id": execution_record.execution_id,
            "execution_hash": execution_record.execution_hash,
            "phase2c_authority_hash": require_sha256(
                require_dict(execution_record.manifest, "phase2c_authority"),
                "authority_hash",
            ),
            "production_equivalence_hash": require_sha256(
                execution_record.manifest, "production_equivalence_hash"
            ),
        }
    else:
        expected_record = execution_record
    if lineage.get("schema_version") != "steel-module-managed-recovery-lineage-v4":
        raise ValueError("unsupported Phase-2C recovery-lineage schema")
    expected_hash = require_sha256(lineage, "lineage_hash")
    unhashed = dict(lineage)
    unhashed.pop("lineage_hash")
    if sha256_bytes(canonical_json(unhashed)) != expected_hash:
        raise ValueError("Phase-2C recovery-lineage semantic hash mismatch")
    successor = require_dict(lineage, "successor_execution")
    for key in (
        "directory", "schema_version", "object_kind", "execution_generation",
        "execution_id", "execution_hash",
    ):
        if successor.get(key) != expected_record.get(key):
            raise ValueError(f"Phase-2C lineage execution {key} mismatch")
    authority = require_dict(lineage, "phase2c_authority")
    if (
        authority.get("authority_hash") != _semantic_hash(authority, "authority_hash")
        or authority.get("authority_hash")
        != expected_record.get("phase2c_authority_hash")
        or lineage.get("production_equivalence_hash")
        != expected_record.get("production_equivalence_hash")
        or lineage.get("excluded_attempt_ids")
        != authority.get("excluded_attempt_ids")
        or lineage.get("excluded_job_ids") != authority.get("excluded_job_ids")
    ):
        raise ValueError("Phase-2C recovery-lineage authority mismatch")
    scope = require_dict(lineage, "result_scope")
    expected_scope = {
        "source_execution_directory": expected_record.get("directory"),
        "selected_results": "execution-v6-production-successes-only",
        "historical_execution_outputs_included": False,
        "historical_execution_accounting_included": False,
        "phase2c_preflight_outputs_included": False,
        "phase2c_preflight_accounting_included": False,
    }
    if scope != expected_scope:
        raise ValueError("Phase-2C recovery-lineage result scope is unsafe")
    return json.loads(json.dumps(lineage))


__all__ = [
    "CRITICAL_CONTROL_BLOBS",
    "EXECUTION_V6_SCHEMA_VERSION",
    "FORMAL_EXECUTION_V6_NAME",
    "FORMAL_HISTORICAL_NONPRODUCTION_JOB_IDS",
    "FORMAL_PREFLIGHT_V6_NAME",
    "PHASE2C_READINESS_LOCK_RELATIVE",
    "PREFLIGHT_EVIDENCE_SCHEMA_VERSION",
    "PREFLIGHT_FAILURE_EVIDENCE_SCHEMA_VERSION",
    "PREFLIGHT_TWIN_SCHEMA_VERSION",
    "PRODUCTION_EQUIVALENCE_SCHEMA_VERSION",
    "Phase2CExecutionV6",
    "Phase2CPreflightTwin",
    "canonical_execution_v6_directory",
    "canonical_phase2c_evidence_root",
    "canonical_preflight_v6_directory",
    "current_formal_preflight_v6_directory",
    "load_execution_v6",
    "load_execution_v6_for_frozen_worker",
    "load_preflight_v6",
    "materialize_execution_v6",
    "materialize_preflight_v6",
    "materialize_preflight_v6_retry",
    "phase2c_recovery_lineage",
    "validate_phase2c_recovery_lineage",
    "validate_preflight_v6_closure",
    "validate_preflight_v6_failure",
    "preview_preflight_v6",
    "preview_preflight_v6_retry",
]
