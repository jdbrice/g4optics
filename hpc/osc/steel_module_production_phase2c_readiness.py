#!/usr/bin/env python3
"""Lock-only R6 readiness for the clean Phase-2C execution-v6.

The implementation commit may create only a non-authoritative proposal.  A
separately reviewed direct child commit may add the tracked lock; the formal
validator proves that the lock is the only tracked delta from C6.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from steel_module_campaign_lib import (
    canonical_json,
    load_json,
    require_dict,
    require_sha1,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_phase2b_lib import (
    PHASE2A_LOCK_RELATIVE,
    SUCCESSOR_OBJECT_KIND,
    fsync_directory,
    write_exclusive_json,
)
from steel_module_production_checkpoint_lib import publish_directory_no_replace


READINESS_SCHEMA_VERSION = "steel-module-production-phase2c-readiness-lock-v1"
READINESS_LOCK_RELATIVE = Path(
    "hpc/osc/configurations/steel-module-production-phase2c-v1.lock.json"
)
PROPOSAL_FILENAME = "steel-module-production-phase2c-readiness-proposal.json"
ACCEPTANCE_SCHEMA_VERSION = (
    "steel-module-production-phase2c-c6-osc-acceptance-v1"
)
ACCEPTANCE_DIRECTORY_PREFIX = "c6-acceptance-"

CRITICAL_ARTIFACTS = (
    "docs/decisions/steel-module-production-phase2c-v1.md",
    "hpc/osc/steel_module_production_phase2c_lib.py",
    "hpc/osc/steel_module_production_phase2c_preflight_lib.py",
    "hpc/osc/steel_module_production_phase2c_readiness.py",
    "hpc/osc/materialize_steel_module_production_preflight_v6.py",
    "hpc/osc/validate_steel_module_production_preflight_v6.py",
    "hpc/osc/manage_steel_module_production_phase2c_preflight.py",
    "hpc/osc/run_steel_module_production_phase2c_preflight.py",
    "hpc/osc/run_steel_module_production_phase2c_preflight.sbatch",
    "hpc/osc/seal_steel_module_production_phase2c_preflight_evidence.py",
    "hpc/osc/seal_steel_module_production_phase2c_preflight_failure.py",
    "hpc/osc/validate_steel_module_production_phase2c_preflight_evidence.py",
    "hpc/osc/materialize_steel_module_production_execution_v6.py",
    "hpc/osc/validate_steel_module_production_execution_v6.py",
    "hpc/osc/generate_steel_module_production_phase2c_readiness.py",
    "hpc/osc/generate_steel_module_production_phase2c_acceptance.py",
    "hpc/osc/validate_steel_module_production_phase2c_readiness.py",
    "hpc/osc/steel_module_production_phase2b_lib.py",
    "hpc/osc/steel_module_production_r3_probe_lib.py",
    "hpc/osc/manage_steel_module_production_attempt.py",
    "hpc/osc/run_steel_module_production_phase2b_task.py",
    "hpc/osc/submit_steel_module_production_phase2b.sbatch",
    "hpc/osc/record_steel_module_production_task_result.py",
    "hpc/osc/steel_module_production_container_contract.py",
    "hpc/osc/finalize_steel_module_managed_child.py",
    "hpc/osc/build_steel_module_production_checkpoint.py",
    "hpc/osc/steel_module_production_checkpoint_lib.py",
    "hpc/osc/analyze_steel_module_production_checkpoint.py",
    "hpc/osc/render_steel_module_production_review.py",
    "hpc/osc/record_steel_module_progression.py",
    "hpc/osc/check_steel_module_production_phase2c.py",
    "hpc/osc/check_steel_module_production_finalization.py",
    "hpc/osc/check_steel_module_production_analysis.py",
    "hpc/osc/check_steel_module_production_successor_v5.py",
    "hpc/osc/check_steel_module_campaign_infrastructure.py",
    "hpc/osc/README.md",
)

CHECKER_PATHS = {
    "focused": "hpc/osc/check_steel_module_production_phase2c.py",
    "top_level": "hpc/osc/check_steel_module_campaign_infrastructure.py",
}
FOCUSED_SCHEDULER_SENTINEL = (
    "scheduler: injected fakes + forbidden-command sentinels; real Slurm calls: 0"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semantic_hash(value: dict[str, Any], field: str) -> str:
    payload = copy.deepcopy(value)
    payload[field] = None
    return sha256_bytes(canonical_json(payload))


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _require_clean_checkout(repo_root: Path) -> None:
    if _git(repo_root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Phase-2C readiness requires a clean checkout")


def _critical_artifact_records(repo_root: Path) -> dict[str, str]:
    records: dict[str, str] = {}
    for relative in CRITICAL_ARTIFACTS:
        path = repo_root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing Phase-2C critical artifact: {relative}")
        records[relative] = sha256_file(path)
    return records


def _c6_identity(repo_root: Path) -> dict[str, Any]:
    commit = _git(repo_root, "rev-parse", "HEAD")
    tree = _git(repo_root, "rev-parse", "HEAD^{tree}")
    if len(commit) != 40 or len(tree) != 40:
        raise ValueError("Phase-2C readiness requires SHA-1 Git identities")
    parents = _git(repo_root, "rev-list", "--parents", "-n", "1", commit).split()
    if not parents or parents[0] != commit:
        raise ValueError("cannot derive C6 parent identity")
    return {
        "commit": commit,
        "tree": tree,
        "parent_count": len(parents) - 1,
        "branch": _git(repo_root, "branch", "--show-current"),
        "critical_artifacts": _critical_artifact_records(repo_root),
    }


def _acceptance_root(repo_root: Path) -> Path:
    from steel_module_production_phase2c_lib import canonical_phase2c_evidence_root

    root = canonical_phase2c_evidence_root(repo_root) / "c6-acceptance"
    if root.is_symlink():
        raise ValueError("Phase-2C C6 acceptance root is a symlink")
    return root


def _acceptance_manifest(root: Path) -> dict[str, str]:
    manifest = root / "SHA256SUMS"
    if root.is_symlink() or not root.is_dir() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Phase-2C C6 acceptance bundle is unsafe")
    rows = manifest.read_text(encoding="utf-8").splitlines()
    if len(rows) != 1:
        raise ValueError("Phase-2C C6 acceptance manifest must contain one row")
    pieces = rows[0].split("  ", 1)
    if len(pieces) != 2 or pieces[1] != "acceptance.json":
        raise ValueError("Phase-2C C6 acceptance manifest row is invalid")
    target = root / pieces[1]
    if (
        len(pieces[0]) != 64
        or any(character not in "0123456789abcdef" for character in pieces[0])
        or target.is_symlink()
        or not target.is_file()
        or sha256_file(target) != pieces[0]
        or {path.name for path in root.iterdir()} != {"acceptance.json", "SHA256SUMS"}
    ):
        raise ValueError("Phase-2C C6 acceptance checksum validation failed")
    return {pieces[1]: pieces[0]}


def _acceptance_candidates(
    repo_root: Path, *, allow_missing_root: bool
) -> tuple[Path, tuple[tuple[Path, dict[str, Any]], ...]]:
    """Load every immutable acceptance bundle without binding it to HEAD.

    Historical C6 bundles are allowed to remain in the canonical evidence
    root.  They must still be checksum-valid and semantically valid; only the
    later selection step decides whether a bundle belongs to the current C6.
    """

    parent = _acceptance_root(repo_root)
    if parent.is_symlink():
        raise ValueError("canonical Phase-2C C6 acceptance root is a symlink")
    if not parent.exists():
        if allow_missing_root:
            return parent, ()
        raise ValueError("canonical Phase-2C C6 acceptance root is missing")
    if not parent.is_dir():
        raise ValueError("canonical Phase-2C C6 acceptance root is not a directory")
    candidates_list: list[Path] = []
    for path in sorted(parent.iterdir()):
        if not path.name.startswith(ACCEPTANCE_DIRECTORY_PREFIX):
            continue
        if path.is_symlink() or not path.is_dir():
            raise ValueError("canonical Phase-2C C6 acceptance child is unsafe")
        candidates_list.append(path)
    candidates = tuple(candidates_list)
    return parent, tuple(
        (path, validate_phase2c_c6_acceptance(path)) for path in candidates
    )


def _current_c6_acceptances(
    repo_root: Path,
    *,
    expected_c6_commit: str,
    expected_c6_tree: str,
    allow_missing_root: bool,
) -> tuple[Path, tuple[tuple[Path, dict[str, Any]], ...]]:
    """Return only bundles bound to the requested current C6 identity."""

    parent, candidates = _acceptance_candidates(
        repo_root, allow_missing_root=allow_missing_root
    )
    matches = tuple(
        (path, payload)
        for path, payload in candidates
        if require_dict(payload, "c6").get("commit") == expected_c6_commit
        and require_dict(payload, "c6").get("tree") == expected_c6_tree
    )
    # Revalidate current-C6 matches against the live checkout.  This adds the
    # checker-byte binding that deliberately cannot apply to stale C6 history.
    for path, _payload in matches:
        validate_phase2c_c6_acceptance(
            path,
            repo_root=repo_root,
            expected_c6_commit=expected_c6_commit,
            expected_c6_tree=expected_c6_tree,
        )
    return parent, matches


def _checker_record(
    repo_root: Path, *, name: str, relative: str
) -> dict[str, Any]:
    checker = repo_root / relative
    if checker.is_symlink() or not checker.is_file():
        raise ValueError(f"missing Phase-2C {name} checker")
    command = [sys.executable, str(checker)]
    result = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=1800,
        env=dict(os.environ),
    )
    stdout = result.stdout
    stderr = result.stderr
    return {
        "name": name,
        "path": relative,
        "sha256": sha256_file(checker),
        "command": command,
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }


def _validate_checker_record(
    record: dict[str, Any], *, name: str, relative: str
) -> None:
    expected_keys = {
        "name", "path", "sha256", "command", "exit_code", "stdout",
        "stderr", "stdout_sha256", "stderr_sha256",
    }
    if set(record) != expected_keys:
        raise ValueError(f"Phase-2C {name} checker evidence field set mismatch")
    stdout = record.get("stdout")
    stderr = record.get("stderr")
    if (
        record.get("name") != name
        or record.get("path") != relative
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or record.get("exit_code") != 0
        or record.get("stdout_sha256")
        != hashlib.sha256(stdout.encode("utf-8")).hexdigest()
        or record.get("stderr_sha256")
        != hashlib.sha256(stderr.encode("utf-8")).hexdigest()
        or not isinstance(record.get("command"), list)
        or len(record["command"]) != 2
        or Path(str(record["command"][1])).name != Path(relative).name
    ):
        raise ValueError(f"Phase-2C {name} checker evidence is invalid")
    require_sha256(record, "sha256")
    expected_pass = (
        "steel-module production Phase-2C: PASS"
        if name == "focused"
        else "steel-module campaign infrastructure: PASS"
    )
    if expected_pass not in stdout:
        raise ValueError(f"Phase-2C {name} checker PASS marker is missing")
    if name == "focused" and FOCUSED_SCHEDULER_SENTINEL not in stdout:
        raise ValueError("Phase-2C focused checker scheduler sentinel is missing")


def validate_phase2c_c6_acceptance(
    directory: Path,
    *,
    repo_root: Path | None = None,
    expected_c6_commit: str | None = None,
    expected_c6_tree: str | None = None,
) -> dict[str, Any]:
    requested = directory.expanduser()
    if requested.is_symlink() or not requested.is_dir():
        raise ValueError("Phase-2C C6 acceptance directory is unsafe")
    root = requested.resolve()
    _acceptance_manifest(root)
    payload = load_json(root / "acceptance.json")
    expected_keys = {
        "schema_version", "acceptance_id", "acceptance_hash",
        "created_at_utc", "c6", "python", "checkers",
        "scheduler_contact", "geant4_invoked", "accepted_for_r6_proposal",
    }
    if set(payload) != expected_keys:
        raise ValueError("Phase-2C C6 acceptance field set mismatch")
    acceptance_hash = require_sha256(payload, "acceptance_hash")
    check = copy.deepcopy(payload)
    check["acceptance_hash"] = None
    check["acceptance_id"] = None
    if (
        payload.get("schema_version") != ACCEPTANCE_SCHEMA_VERSION
        or acceptance_hash != sha256_bytes(canonical_json(check))
        or payload.get("acceptance_id")
        != "sm-v1-phase2c-c6-acceptance-" + acceptance_hash[:12]
        or root.name != ACCEPTANCE_DIRECTORY_PREFIX + acceptance_hash[:12]
        or payload.get("scheduler_contact")
        != {
            "real_slurm_calls": 0,
            "forbidden_command_sentinels": True,
            "scheduler_contact_performed": False,
        }
        or payload.get("geant4_invoked") is not False
        or payload.get("accepted_for_r6_proposal") is not True
    ):
        raise ValueError("Phase-2C C6 acceptance semantic validation failed")
    require_string(payload, "created_at_utc")
    c6 = require_dict(payload, "c6")
    require_sha1(c6, "commit")
    require_sha1(c6, "tree")
    checkers = require_dict(payload, "checkers")
    if set(checkers) != set(CHECKER_PATHS):
        raise ValueError("Phase-2C C6 acceptance checker set mismatch")
    for name, relative in CHECKER_PATHS.items():
        _validate_checker_record(require_dict(checkers, name), name=name, relative=relative)
    python = require_dict(payload, "python")
    require_string(python, "executable")
    require_string(python, "version")
    if repo_root is not None:
        repo = repo_root.expanduser().resolve()
        if (expected_c6_commit is None) != (expected_c6_tree is None):
            raise ValueError("expected C6 commit/tree must be supplied together")
        expected_commit = expected_c6_commit or _git(repo, "rev-parse", "HEAD")
        expected_tree = expected_c6_tree or _git(repo, "rev-parse", "HEAD^{tree}")
        if (
            c6.get("commit") != expected_commit
            or c6.get("tree") != expected_tree
        ):
            raise ValueError("Phase-2C C6 acceptance does not bind this checkout")
        for name, relative in CHECKER_PATHS.items():
            if checkers[name].get("sha256") != sha256_file(repo / relative):
                raise ValueError(f"Phase-2C {name} checker bytes changed")
    return payload


def _acceptance_publish_lock_path(parent: Path, c6_commit: str) -> Path:
    return parent / f".current-c6-{c6_commit[:12]}.publish.lock"


def write_phase2c_c6_acceptance(*, repo_root: Path) -> Path:
    """Idempotently freeze exactly one acceptance for the current C6.

    The external evidence root can retain older C6 bundles.  A per-C6
    no-replace directory lock closes the otherwise dangerous race in which two
    checker runs could publish different timestamped bundles simultaneously.
    """

    root = repo_root.expanduser().resolve()
    _require_clean_checkout(root)
    if (root / READINESS_LOCK_RELATIVE).exists():
        raise ValueError("C6 acceptance must be frozen before R6")
    c6 = _c6_identity(root)
    parent, current = _current_c6_acceptances(
        root,
        expected_c6_commit=c6["commit"],
        expected_c6_tree=c6["tree"],
        allow_missing_root=True,
    )
    if len(current) > 1:
        raise ValueError("expected at most one current-C6 acceptance bundle")
    if current:
        return current[0][0]
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("canonical Phase-2C C6 acceptance root is unsafe")
    lock = _acceptance_publish_lock_path(parent, c6["commit"])
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("current-C6 acceptance publication is already active") from exc
    try:
        return _write_phase2c_c6_acceptance_unlocked(
            repo_root=root, expected_c6=c6
        )
    finally:
        lock.rmdir()
        fsync_directory(parent)


def _write_phase2c_c6_acceptance_unlocked(
    *, repo_root: Path, expected_c6: dict[str, Any]
) -> Path:
    root = repo_root.expanduser().resolve()
    _require_clean_checkout(root)
    if (root / READINESS_LOCK_RELATIVE).exists():
        raise ValueError("C6 acceptance must be frozen before R6")
    c6 = _c6_identity(root)
    if c6 != expected_c6:
        raise ValueError("C6 identity changed after acceptance lock acquisition")
    parent, current = _current_c6_acceptances(
        root,
        expected_c6_commit=c6["commit"],
        expected_c6_tree=c6["tree"],
        allow_missing_root=True,
    )
    if len(current) > 1:
        raise ValueError("expected at most one current-C6 acceptance bundle")
    if current:
        return current[0][0]
    records = {
        name: _checker_record(root, name=name, relative=relative)
        for name, relative in CHECKER_PATHS.items()
    }
    _require_clean_checkout(root)
    if _c6_identity(root) != expected_c6:
        raise ValueError("C6 identity changed while acceptance checkers ran")
    if records["focused"]["exit_code"] != 0 or records["top_level"]["exit_code"] != 0:
        raise ValueError("Phase-2C C6 checkers did not both pass")
    if FOCUSED_SCHEDULER_SENTINEL not in records["focused"]["stdout"]:
        raise ValueError("Phase-2C checker did not prove forbidden scheduler sentinels")
    payload: dict[str, Any] = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "acceptance_id": None,
        "acceptance_hash": None,
        "created_at_utc": _utc_now(),
        "c6": {"commit": c6["commit"], "tree": c6["tree"]},
        "python": {
            "executable": sys.executable,
            "version": sys.version,
        },
        "checkers": records,
        "scheduler_contact": {
            "real_slurm_calls": 0,
            "forbidden_command_sentinels": True,
            "scheduler_contact_performed": False,
        },
        "geant4_invoked": False,
        "accepted_for_r6_proposal": True,
    }
    check = copy.deepcopy(payload)
    check["acceptance_hash"] = None
    check["acceptance_id"] = None
    digest = sha256_bytes(canonical_json(check))
    payload["acceptance_hash"] = digest
    payload["acceptance_id"] = "sm-v1-phase2c-c6-acceptance-" + digest[:12]
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # A previous invocation may have published while the checkers were
    # running.  Prefer its already-validated immutable evidence instead of
    # creating a second acceptance for the same C6.
    _parent, current = _current_c6_acceptances(
        root,
        expected_c6_commit=c6["commit"],
        expected_c6_tree=c6["tree"],
        allow_missing_root=False,
    )
    if len(current) > 1:
        raise ValueError("expected at most one current-C6 acceptance bundle")
    if current:
        return current[0][0]
    _require_clean_checkout(root)
    if _c6_identity(root) != expected_c6:
        raise ValueError("C6 identity changed before acceptance publication")
    target = parent / (ACCEPTANCE_DIRECTORY_PREFIX + digest[:12])
    if target.exists() or target.is_symlink():
        existing = validate_phase2c_c6_acceptance(
            target,
            repo_root=root,
            expected_c6_commit=c6["commit"],
            expected_c6_tree=c6["tree"],
        )
        if require_dict(existing, "c6") == {
            "commit": c6["commit"],
            "tree": c6["tree"],
        }:
            return target
        raise ValueError("Phase-2C C6 acceptance target identity collision")
    temporary = Path(tempfile.mkdtemp(prefix=".c6-acceptance-", dir=parent))
    try:
        write_exclusive_json(temporary / "acceptance.json", payload)
        manifest = sha256_file(temporary / "acceptance.json") + "  acceptance.json\n"
        descriptor = os.open(
            temporary / "SHA256SUMS",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.write(descriptor, manifest.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        publish_directory_no_replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            import shutil

            shutil.rmtree(temporary)
    validate_phase2c_c6_acceptance(target, repo_root=root)
    return target


def _acceptance_binding(directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "directory": str(directory.expanduser().resolve()),
        "schema_version": payload.get("schema_version"),
        "acceptance_id": require_string(payload, "acceptance_id"),
        "acceptance_hash": require_sha256(payload, "acceptance_hash"),
        "acceptance_json_sha256": sha256_file(directory / "acceptance.json"),
        "checksum_manifest_sha256": sha256_file(directory / "SHA256SUMS"),
    }


def _load_unique_c6_acceptance(repo_root: Path) -> tuple[Path, dict[str, Any]]:
    root = repo_root.expanduser().resolve()
    current_commit = _git(root, "rev-parse", "HEAD")
    current_tree = _git(root, "rev-parse", "HEAD^{tree}")
    _parent, current = _current_c6_acceptances(
        root,
        expected_c6_commit=current_commit,
        expected_c6_tree=current_tree,
        allow_missing_root=False,
    )
    if len(current) != 1:
        raise ValueError(
            "expected exactly one acceptance bundle for the current Phase-2C C6"
        )
    return current[0]


def _empty_mutable_snapshot(
    execution: Any, *, require_currently_empty: bool
) -> dict[str, Any]:
    root = execution.directory
    records: dict[str, list[str]] = {
        "intents": [], "attempts": [], "finalized": []
    }
    for name in ("intents", "attempts", "finalized"):
        path = root / name
        if path.is_symlink() or not path.is_dir():
            raise ValueError(f"execution-v6 mutable root is unsafe: {name}")
        entries = sorted(child.name for child in path.iterdir())
        if require_currently_empty and entries:
            raise ValueError(f"execution-v6 mutable root is not empty: {name}")
    snapshot = {
        "schema_version": "steel-module-production-empty-mutable-snapshot-v1",
        "records": records,
        "snapshot_hash": None,
    }
    snapshot["snapshot_hash"] = _semantic_hash(snapshot, "snapshot_hash")
    return snapshot


def _execution_identity(
    execution: Any, *, require_pristine: bool
) -> dict[str, Any]:
    manifest = execution.manifest
    authority = require_dict(manifest, "phase2c_authority")
    preflight = require_dict(authority, "phase2c_preflight_evidence")
    compute = require_dict(preflight, "compute_preflight")
    for key in ("apptainer_path", "apptainer_sha256", "apptainer_version"):
        require_string(compute, key)
    job_id = require_string(preflight, "job_id")
    if not job_id.isdigit() or job_id.startswith("0"):
        raise ValueError("Phase-2C preflight job ID is invalid")
    equivalence = require_sha256(authority, "production_equivalence_hash")
    if manifest.get("production_equivalence_hash") != equivalence:
        raise ValueError("execution-v6 equivalence identity is inconsistent")
    return {
        "execution_id": require_string(manifest, "execution_id"),
        "execution_hash": require_sha256(manifest, "execution_hash"),
        "directory": str(execution.directory),
        "managed_execution_sha256": sha256_file(
            execution.directory / "managed_execution.json"
        ),
        "static_checksums_sha256": sha256_file(
            execution.directory / "STATIC_SHA256SUMS"
        ),
        "production_equivalence_hash": equivalence,
        "shape": copy.deepcopy(require_dict(manifest, "shape")),
        "runtime": copy.deepcopy(require_dict(manifest, "runtime")),
        "sources": copy.deepcopy(require_dict(manifest, "sources")),
        "phase2c_authority": copy.deepcopy(authority),
        "compute_preflight": copy.deepcopy(compute),
        "preflight_job_id": job_id,
        "empty_mutable_snapshot": _empty_mutable_snapshot(
            execution, require_currently_empty=require_pristine
        ),
    }


def build_phase2c_readiness_proposal(
    *, repo_root: Path, execution_dir: Path | None = None
) -> dict[str, Any]:
    """Build but do not publish a non-authoritative C6 readiness proposal."""

    root = repo_root.expanduser().resolve()
    lock = root / READINESS_LOCK_RELATIVE
    if lock.exists() or lock.is_symlink():
        raise ValueError("tracked R6 readiness lock already exists")
    _require_clean_checkout(root)
    from steel_module_production_phase2c_lib import load_execution_v6

    execution = load_execution_v6(
        execution_dir, repo_root=root, require_readiness=False
    )
    if execution.manifest.get("object_kind") != SUCCESSOR_OBJECT_KIND:
        raise ValueError("R6 proposal requires the production execution-v6")
    identity = _execution_identity(execution, require_pristine=True)
    acceptance_dir, acceptance = _load_unique_c6_acceptance(root)
    proposal: dict[str, Any] = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "status": "proposed-phase2c-r6-readiness",
        "created_at_utc": _utc_now(),
        "review": None,
        "c6": _c6_identity(root),
        "c6_osc_acceptance": _acceptance_binding(
            acceptance_dir, acceptance
        ),
        "execution_v6": identity,
        "production_equivalence_hash": identity["production_equivalence_hash"],
        "compute_preflight": identity["compute_preflight"],
        "managed_submission_ready": False,
        "automatic_submission": False,
        "real_production_submission": False,
        "production_intent_count": 0,
        "production_slurm_job_ids": [],
        "preflight_slurm_job_ids": [identity["preflight_job_id"]],
        "readiness_hash": None,
    }
    proposal["readiness_hash"] = _semantic_hash(proposal, "readiness_hash")
    validate_phase2c_readiness_payload(proposal, authoritative=False)
    return proposal


def validate_phase2c_readiness_payload(
    payload: dict[str, Any], *, authoritative: bool
) -> dict[str, Any]:
    expected_keys = {
        "schema_version", "status", "created_at_utc", "review", "c6",
        "c6_osc_acceptance",
        "execution_v6", "production_equivalence_hash", "compute_preflight",
        "managed_submission_ready", "automatic_submission",
        "real_production_submission", "production_intent_count",
        "production_slurm_job_ids", "preflight_slurm_job_ids",
        "readiness_hash",
    }
    if set(payload) != expected_keys:
        raise ValueError("Phase-2C readiness payload key set mismatch")
    expected_status = (
        "accepted-formal-phase2c-r6-ready"
        if authoritative
        else "proposed-phase2c-r6-readiness"
    )
    if (
        payload.get("schema_version") != READINESS_SCHEMA_VERSION
        or payload.get("status") != expected_status
        or payload.get("readiness_hash")
        != _semantic_hash(payload, "readiness_hash")
        or payload.get("managed_submission_ready") is not authoritative
        or payload.get("automatic_submission") is not False
        or payload.get("real_production_submission") is not False
        or payload.get("production_intent_count") != 0
        or payload.get("production_slurm_job_ids") != []
    ):
        raise ValueError("Phase-2C readiness semantic state is invalid")
    require_string(payload, "created_at_utc")
    equivalence = require_sha256(payload, "production_equivalence_hash")
    execution = require_dict(payload, "execution_v6")
    expected_execution_keys = {
        "execution_id", "execution_hash", "directory",
        "managed_execution_sha256", "static_checksums_sha256",
        "production_equivalence_hash", "shape", "runtime", "sources",
        "phase2c_authority", "compute_preflight", "preflight_job_id",
        "empty_mutable_snapshot",
    }
    if set(execution) != expected_execution_keys:
        raise ValueError("R6 execution identity field set mismatch")
    require_string(execution, "execution_id")
    require_sha256(execution, "execution_hash")
    require_string(execution, "directory")
    require_sha256(execution, "managed_execution_sha256")
    require_sha256(execution, "static_checksums_sha256")
    if execution.get("production_equivalence_hash") != equivalence:
        raise ValueError("R6 execution/equivalence identity mismatch")
    shape = require_dict(execution, "shape")
    if (
        shape.get("task_count") != 32
        or shape.get("event_count") != 8000
        or shape.get("events_per_task") != 250
        or shape.get("seed_count") != 64
        or shape.get("tile_thickness_mm") != [4, 24]
        or shape.get("sipm_layout") != "back-center"
        or shape.get("absorber_transverse_mm") != 500
        or shape.get("seed_block_range_inclusive") != [0, 15]
    ):
        raise ValueError("R6 execution shape is not exact BC-S1")
    authority = require_dict(execution, "phase2c_authority")
    if authority.get("production_equivalence_hash") != equivalence:
        raise ValueError("R6 Phase-2C authority/equivalence mismatch")
    zero = require_dict(authority, "zero_consumption")
    if zero != {
        "events_consumed": 0,
        "production_seeds_consumed": 0,
        "seed_reuse_authorized": True,
    }:
        raise ValueError("R6 zero-consumption authority is invalid")
    empty = require_dict(execution, "empty_mutable_snapshot")
    expected_empty = {
        "schema_version": "steel-module-production-empty-mutable-snapshot-v1",
        "records": {"intents": [], "attempts": [], "finalized": []},
        "snapshot_hash": empty.get("snapshot_hash"),
    }
    if (
        empty != expected_empty
        or empty.get("snapshot_hash") != _semantic_hash(empty, "snapshot_hash")
    ):
        raise ValueError("R6 initial empty-mutable snapshot is invalid")
    job_ids = payload.get("preflight_slurm_job_ids")
    if (
        not isinstance(job_ids, list)
        or len(job_ids) != 1
        or not isinstance(job_ids[0], str)
        or not job_ids[0].isdigit()
        or execution.get("preflight_job_id") != job_ids[0]
    ):
        raise ValueError("R6 preflight scheduler lineage is invalid")
    c6 = require_dict(payload, "c6")
    require_sha1(c6, "commit")
    require_sha1(c6, "tree")
    if not isinstance(c6.get("parent_count"), int) or c6["parent_count"] < 1:
        raise ValueError("R6 C6 parent identity is invalid")
    artifacts = require_dict(c6, "critical_artifacts")
    if set(artifacts) != set(CRITICAL_ARTIFACTS):
        raise ValueError("R6 critical-artifact set mismatch")
    for value in artifacts.values():
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("R6 critical-artifact digest is invalid")
    acceptance = require_dict(payload, "c6_osc_acceptance")
    expected_acceptance_keys = {
        "directory", "schema_version", "acceptance_id", "acceptance_hash",
        "acceptance_json_sha256", "checksum_manifest_sha256",
    }
    if set(acceptance) != expected_acceptance_keys:
        raise ValueError("R6 C6 acceptance binding field set mismatch")
    require_string(acceptance, "directory")
    if acceptance.get("schema_version") != ACCEPTANCE_SCHEMA_VERSION:
        raise ValueError("R6 C6 acceptance schema mismatch")
    require_string(acceptance, "acceptance_id")
    for key in (
        "acceptance_hash", "acceptance_json_sha256",
        "checksum_manifest_sha256",
    ):
        require_sha256(acceptance, key)
    compute = require_dict(payload, "compute_preflight")
    if compute != execution.get("compute_preflight"):
        raise ValueError("R6 compute-preflight identity mismatch")
    for key in ("job_id", "apptainer_path", "apptainer_version"):
        require_string(compute, key)
    require_sha256(compute, "apptainer_sha256")
    require_sha256(compute, "held_identity_event_sha256")
    require_sha256(compute, "accounting_manifest_sha256")
    if authoritative:
        review = require_dict(payload, "review")
        require_string(review, "reviewed_at_utc")
        require_string(review, "reviewed_by")
        require_sha256(review, "proposal_sha256")
    elif payload.get("review") is not None:
        raise ValueError("non-authoritative proposal cannot contain review authority")
    return payload


def write_phase2c_readiness_proposal(
    *, repo_root: Path, output: Path, execution_dir: Path | None = None
) -> Path:
    payload = build_phase2c_readiness_proposal(
        repo_root=repo_root, execution_dir=execution_dir
    )
    target = output.expanduser()
    if target.is_symlink() or not target.parent.is_dir():
        raise ValueError("Phase-2C readiness proposal target is unsafe")
    write_exclusive_json(target, payload)
    return target.resolve()


def build_phase2c_readiness_lock(
    *, proposal_path: Path, repo_root: Path, reviewer: str
) -> dict[str, Any]:
    root = repo_root.expanduser().resolve()
    proposal_file = proposal_path.expanduser()
    if proposal_file.is_symlink() or not proposal_file.is_file():
        raise ValueError("Phase-2C readiness proposal is missing or unsafe")
    proposal_bytes = proposal_file.read_bytes()
    try:
        proposal = json.loads(proposal_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Phase-2C readiness proposal is not valid JSON") from exc
    if not isinstance(proposal, dict):
        raise ValueError("Phase-2C readiness proposal must be a JSON object")
    validate_phase2c_readiness_payload(proposal, authoritative=False)
    _require_clean_checkout(root)
    if _git(root, "rev-parse", "HEAD") != require_string(
        require_dict(proposal, "c6"), "commit"
    ):
        raise ValueError("readiness lock must be created from the exact C6 commit")
    if not reviewer or any(character.isspace() for character in reviewer):
        raise ValueError("readiness reviewer must be a non-empty token")
    lock = copy.deepcopy(proposal)
    lock["status"] = "accepted-formal-phase2c-r6-ready"
    lock["managed_submission_ready"] = True
    lock["review"] = {
        "reviewed_at_utc": _utc_now(),
        "reviewed_by": reviewer,
        "proposal_sha256": sha256_bytes(proposal_bytes),
    }
    lock["readiness_hash"] = _semantic_hash(lock, "readiness_hash")
    validate_phase2c_readiness_payload(lock, authoritative=True)
    return lock


def write_phase2c_readiness_lock(
    *, proposal_path: Path, repo_root: Path, reviewer: str
) -> Path:
    root = repo_root.expanduser().resolve()
    target = root / READINESS_LOCK_RELATIVE
    if target.exists() or target.is_symlink():
        raise ValueError("refusing to overwrite R6 readiness lock")
    payload = build_phase2c_readiness_lock(
        proposal_path=proposal_path, repo_root=root, reviewer=reviewer
    )
    write_exclusive_json(target, payload)
    fsync_directory(target.parent)
    return target


def phase2c_readiness_path(execution: Any, *, repo_root: Path | None) -> Path:
    if repo_root is not None:
        return repo_root.expanduser().resolve() / READINESS_LOCK_RELATIVE
    phase2a = Path(
        require_string(require_dict(execution.manifest, "phase2a_lock"), "path")
    )
    suffix = PHASE2A_LOCK_RELATIVE.parts
    if tuple(phase2a.parts[-len(suffix):]) != suffix:
        raise ValueError("execution-v6 Phase-2A path cannot locate R6 checkout")
    return Path(*phase2a.parts[:-len(suffix)]) / READINESS_LOCK_RELATIVE


def _verify_lock_only_git_gate(repo_root: Path, payload: dict[str, Any]) -> None:
    _require_clean_checkout(repo_root)
    head = _git(repo_root, "rev-parse", "HEAD")
    parents = _git(repo_root, "rev-list", "--parents", "-n", "1", head).split()
    c6 = require_string(require_dict(payload, "c6"), "commit")
    if len(parents) != 2 or parents[1] != c6:
        raise ValueError("R6 must be a direct non-merge child of C6")
    lock_relative = READINESS_LOCK_RELATIVE.as_posix()
    c6_lock = _git(repo_root, "ls-tree", c6, "--", lock_relative)
    if c6_lock:
        raise ValueError("readiness lock already existed in C6")
    changed = tuple(
        line
        for line in _git(
            repo_root,
            "diff",
            "--name-status",
            "--no-renames",
            c6,
            head,
            "--",
        ).splitlines()
        if line
    )
    if changed != (f"A\t{lock_relative}",):
        raise ValueError("R6 tracked delta is not the single readiness lock")
    lock_record = _git(repo_root, "ls-tree", head, "--", lock_relative).splitlines()
    if (
        len(lock_record) != 1
        or not lock_record[0].startswith("100644 blob ")
        or not lock_record[0].endswith(f"\t{lock_relative}")
    ):
        raise ValueError("R6 readiness lock is not one ordinary non-executable blob")
    current = _critical_artifact_records(repo_root)
    if current != require_dict(require_dict(payload, "c6"), "critical_artifacts"):
        raise ValueError("R6 changed C6 production-critical artifacts")
    if _git(repo_root, "rev-parse", f"{c6}^{{tree}}") != require_string(
        require_dict(payload, "c6"), "tree"
    ):
        raise ValueError("R6 recorded C6 tree identity mismatch")


def verify_phase2c_readiness(
    execution: Any, *, repo_root: Path | None
) -> tuple[Path, dict[str, Any]]:
    """Validate the tracked R6 lock against one pristine execution-v6."""

    path = phase2c_readiness_path(execution, repo_root=repo_root)
    if path.is_symlink() or not path.is_file():
        raise ValueError("tracked R6 readiness lock is missing or unsafe")
    payload = load_json(path)
    validate_phase2c_readiness_payload(payload, authoritative=True)
    identity = _execution_identity(execution, require_pristine=False)
    if payload.get("execution_v6") != identity:
        raise ValueError("R6 readiness does not bind the current execution-v6")
    root = path.parents[len(READINESS_LOCK_RELATIVE.parts) - 1]
    c6 = require_dict(payload, "c6")
    acceptance_binding = require_dict(payload, "c6_osc_acceptance")
    acceptance_path = Path(require_string(acceptance_binding, "directory"))
    _acceptance_parent, current_acceptances = _current_c6_acceptances(
        root,
        expected_c6_commit=require_string(c6, "commit"),
        expected_c6_tree=require_string(c6, "tree"),
        allow_missing_root=False,
    )
    if len(current_acceptances) != 1:
        raise ValueError("R6 requires exactly one canonical current-C6 acceptance")
    canonical_path, canonical_acceptance = current_acceptances[0]
    canonical_parent = _acceptance_root(root).resolve()
    if canonical_path.parent.resolve() != canonical_parent:
        raise ValueError("R6 canonical C6 acceptance escaped its evidence root")
    acceptance = validate_phase2c_c6_acceptance(
        acceptance_path,
        repo_root=root,
        expected_c6_commit=require_string(c6, "commit"),
        expected_c6_tree=require_string(c6, "tree"),
    )
    if (
        canonical_path.resolve() != acceptance_path.resolve()
        or canonical_acceptance != acceptance
    ):
        raise ValueError("R6 C6 acceptance is not the unique canonical evidence")
    if acceptance_binding != _acceptance_binding(acceptance_path, acceptance):
        raise ValueError("R6 C6 acceptance evidence changed")
    _verify_lock_only_git_gate(root, payload)
    return path, payload
