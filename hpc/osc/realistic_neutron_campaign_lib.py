#!/usr/bin/env python3
"""Shared validation helpers for realistic-neutron campaign tooling."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


CAMPAIGN_SCHEMA_VERSION = "realistic-neutron-campaign-v1"
RUN_CONFIG_SCHEMA_VERSION = "opnovice2-run-config-v3"
EVENT_SCHEMA_VERSION = "opnovice2-scan-event-v2"
RESULT_SCHEMA_VERSION = "realistic-neutron-task-result-v1"
STUDY_PRESET = "realistic-neutron-v1"
ATTEMPT_MANIFEST_NAME = "submission-attempts.tsv"
FINALIZED_REQUIRED_FILES = (
    "task_index.tsv",
    "configuration_summary.csv",
    "thickness_ratios.csv",
    "validation_report.json",
    "analysis_config.json",
)

ATTEMPT_FIELDS = (
    "attempt_id",
    "submitted_utc",
    "mode",
    "status",
    "campaign_id",
    "plan_hash",
    "git_commit",
    "image_sha256",
    "g4_data_manifest_sha256",
    "executable_sha256",
    "slurm_job_id",
    "array_spec",
    "task_count",
    "attempt_tasks_tsv",
    "scan_args_file",
    "frozen_source",
)


@dataclass(frozen=True)
class CampaignTask:
    task_index: int
    logical_task_id: str
    stage: str
    tile_thickness_mm: int
    absorber_transverse_mm: int
    x_mm: int
    y_mm: int
    seed_block: int
    events: int
    seed1: int
    seed2: int
    configuration_hash: str


@dataclass(frozen=True)
class CampaignBundle:
    directory: Path
    manifest: dict[str, Any]
    tasks: tuple[CampaignTask, ...]
    scan_args: tuple[str, ...]

    @property
    def campaign_id(self) -> str:
        return require_string(self.manifest, "campaign_id")

    @property
    def plan_hash(self) -> str:
        return require_sha256(self.manifest, "plan_hash")

    @property
    def git_commit(self) -> str:
        git = require_dict(self.manifest, "git")
        return require_sha1(git, "commit")

    @property
    def environment(self) -> dict[str, Any]:
        return require_dict(self.manifest, "environment")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksum_manifest(
    directory: Path,
    *,
    required: Iterable[str] = (),
    manifest_name: str = "SHA256SUMS",
) -> set[str]:
    """Verify a flat checksum manifest and return its recorded filenames."""

    checksum_path = directory / manifest_name
    recorded: set[str] = set()
    for raw in checksum_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        parts = raw.split("  ", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid checksum row in {checksum_path}: {raw!r}")
        digest, name = parts
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or Path(name).name != name
            or name in recorded
        ):
            raise ValueError(f"invalid checksum entry in {checksum_path}: {raw!r}")
        path = directory / name
        if not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"checksum mismatch: {path}")
        recorded.add(name)
    missing = set(required) - recorded
    if missing:
        raise ValueError(
            f"checksums omit required files in {directory}: {sorted(missing)}"
        )
    return recorded


def verify_finalized_checksums(finalized_dir: Path) -> set[str]:
    return verify_checksum_manifest(
        finalized_dir,
        required=FINALIZED_REQUIRED_FILES,
    )


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def require_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def require_string(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def require_sha256(parent: dict[str, Any], key: str) -> str:
    value = require_string(parent, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{key} must be a lowercase SHA-256 digest")
    return value


def require_sha1(parent: dict[str, Any], key: str) -> str:
    value = require_string(parent, key)
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{key} must be a lowercase 40-character Git commit")
    return value


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def parse_campaign_tasks(path: Path) -> tuple[CampaignTask, ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    tasks: list[CampaignTask] = []
    for row in rows:
        try:
            task = CampaignTask(
                task_index=int(row["task_index"]),
                logical_task_id=row["logical_task_id"],
                stage=row["stage"],
                tile_thickness_mm=int(row["tile_thickness_mm"]),
                absorber_transverse_mm=int(row["absorber_transverse_mm"]),
                x_mm=int(row["x_mm"]),
                y_mm=int(row["y_mm"]),
                seed_block=int(row["seed_block"]),
                events=int(row["events"]),
                seed1=int(row["seed1"]),
                seed2=int(row["seed2"]),
                configuration_hash=row["configuration_hash"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid campaign task row: {row}") from exc
        tasks.append(task)

    if not tasks:
        raise ValueError("tasks.tsv contains no tasks")
    if [task.task_index for task in tasks] != list(range(1, len(tasks) + 1)):
        raise ValueError("task_index must be contiguous and one-based")
    logical_ids = [task.logical_task_id for task in tasks]
    if any(not value for value in logical_ids) or len(set(logical_ids)) != len(logical_ids):
        raise ValueError("logical_task_id values must be non-empty and unique")
    hashes = [task.configuration_hash for task in tasks]
    if any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in hashes
    ):
        raise ValueError("configuration_hash values must be lowercase SHA-256 digests")
    seeds = [seed for task in tasks for seed in (task.seed1, task.seed2)]
    if len(set(seeds)) != len(seeds):
        raise ValueError("campaign task seeds must be globally unique")
    return tuple(tasks)


def read_scan_args(path: Path) -> tuple[str, ...]:
    lines = tuple(
        line
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    )
    if not lines:
        raise ValueError("scan_args.txt contains no executable task lines")
    return lines


def resolve_recorded_artifact(record: object, label: str) -> tuple[Path, str]:
    if not isinstance(record, dict):
        raise ValueError(f"missing {label} artifact record")
    raw_path = record.get("path")
    digest = record.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"{label} artifact path is invalid")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"{label} artifact SHA-256 is invalid")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"missing {label} artifact: {path}")
    actual = sha256_file(path)
    if actual != digest:
        raise ValueError(
            f"{label} artifact checksum mismatch: expected {digest}, got {actual}"
        )
    return path, digest


def environment_identity(environment: dict[str, Any]) -> str:
    payload: dict[str, object] = {
        "mode": environment.get("mode"),
        "geant4_version": environment.get("geant4_version"),
        "image_sha256": None,
        "g4_data_manifest_sha256": None,
        "build_artifact_sha256": None,
    }
    for field, payload_key in (
        ("image", "image_sha256"),
        ("g4_data_manifest", "g4_data_manifest_sha256"),
        ("build_artifact", "build_artifact_sha256"),
    ):
        record = environment.get(field)
        if isinstance(record, dict):
            payload[payload_key] = record.get("sha256")
    return sha256_bytes(canonical_json(payload))


def load_campaign(campaign_dir: Path, *, verify_external_artifacts: bool = True) -> CampaignBundle:
    directory = campaign_dir.expanduser().resolve()
    manifest_path = directory / "campaign.json"
    tasks_path = directory / "tasks.tsv"
    scan_args_path = directory / "scan_args.txt"
    readme_path = directory / "README.md"
    for path in (manifest_path, tasks_path, scan_args_path, readme_path):
        if not path.is_file():
            raise ValueError(f"missing campaign file: {path}")

    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported campaign schema: {manifest.get('schema_version')!r}"
        )
    if manifest.get("study_preset") != STUDY_PRESET:
        raise ValueError(f"campaign study_preset must be {STUDY_PRESET}")
    campaign_id = require_string(manifest, "campaign_id")
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in campaign_id):
        raise ValueError("campaign_id contains unsafe characters")
    require_sha256(manifest, "plan_hash")
    git = require_dict(manifest, "git")
    require_sha1(git, "commit")
    if not isinstance(git.get("dirty"), bool):
        raise ValueError("git.dirty must be boolean")

    tasks = parse_campaign_tasks(tasks_path)
    scan_args = read_scan_args(scan_args_path)
    if len(tasks) != len(scan_args) or manifest.get("task_count") != len(tasks):
        raise ValueError("campaign task counts disagree")
    calculated_plan_hash = sha256_bytes(
        canonical_json([asdict(task) for task in tasks])
    )
    if calculated_plan_hash != manifest["plan_hash"]:
        raise ValueError(
            "tasks.tsv does not match campaign plan_hash: "
            f"expected {manifest['plan_hash']}, got {calculated_plan_hash}"
        )

    artifacts = require_dict(manifest, "artifacts")
    for key, path in (
        ("tasks_tsv", tasks_path),
        ("scan_args", scan_args_path),
        ("readme", readme_path),
    ):
        record = require_dict(artifacts, key)
        expected = require_sha256(record, "sha256")
        if sha256_file(path) != expected:
            raise ValueError(f"campaign artifact checksum mismatch: {path}")

    environment = require_dict(manifest, "environment")
    expected_identity = require_sha256(environment, "identity_hash")
    actual_identity = environment_identity(environment)
    if actual_identity != expected_identity:
        raise ValueError(
            "campaign environment identity mismatch: "
            f"expected {expected_identity}, got {actual_identity}"
        )
    if verify_external_artifacts:
        for field, label in (
            ("image", "Apptainer image"),
            ("g4_data_manifest", "Geant4 data manifest"),
            ("build_artifact", "prebuilt executable"),
        ):
            record = environment.get(field)
            if record is not None:
                resolve_recorded_artifact(record, label)

    return CampaignBundle(directory, manifest, tasks, scan_args)


def task_by_logical_id(bundle: CampaignBundle) -> dict[str, CampaignTask]:
    return {task.logical_task_id: task for task in bundle.tasks}


def campaign_relative(path: Path, campaign_dir: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(campaign_dir.resolve()))
    except ValueError as exc:
        raise ValueError(f"artifact is outside campaign directory: {resolved}") from exc


def resolve_campaign_path(campaign_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"campaign artifact path must be relative: {value}")
    resolved = (campaign_dir / path).resolve()
    try:
        resolved.relative_to(campaign_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"campaign-relative path escapes campaign directory: {value}") from exc
    return resolved


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_attempt_rows(campaign_dir: Path) -> list[dict[str, str]]:
    path = campaign_dir / ATTEMPT_MANIFEST_NAME
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != ATTEMPT_FIELDS:
            raise ValueError(f"unexpected attempt manifest header: {path}")
        return list(reader)


def write_tsv(path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
