#!/usr/bin/env python3
"""Collect no-Slurm Phase-2B readiness evidence and emit a non-authoritative proposal."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from steel_module_campaign_lib import sha256_file
from steel_module_production_phase2b_lib import (
    READINESS_CRITICAL_ARTIFACTS,
    READINESS_LOCK_RELATIVE,
    READINESS_LOCK_SCHEMA_VERSION,
    load_execution_companion,
    load_phase2a_lock,
    require_pristine_readiness_workspace,
    utc_now,
    write_exclusive_bytes,
    write_exclusive_json,
)

CRITICAL_ARTIFACTS = READINESS_CRITICAL_ARTIFACTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--write-proposal", type=Path,
        help="Exclusively write an untracked proposal outside the repository.",
    )
    return parser.parse_args()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def _run_top_level_checker(root: Path) -> dict[str, object]:
    command = [sys.executable, "hpc/osc/check_steel_module_campaign_infrastructure.py"]
    with tempfile.TemporaryDirectory(prefix="phase2b-scheduler-sentinel-") as raw:
        sentinel_root = Path(raw)
        contact = sentinel_root / "scheduler-contact"
        binary_root = sentinel_root / "bin"
        binary_root.mkdir()
        for name in ("sbatch", "scontrol", "squeue", "sacct"):
            command_path = binary_root / name
            command_path.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s\\n' {name} >> \"${{PHASE2B_SCHEDULER_SENTINEL:?}}\"\n"
                "exit 97\n",
                encoding="utf-8",
            )
            command_path.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(binary_root) + os.pathsep + environment.get(
            "PATH", ""
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PHASE2B_SCHEDULER_SENTINEL"] = str(contact)
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if contact.exists():
            raise ValueError(
                "top-level readiness checker contacted scheduler commands: "
                + contact.read_text(encoding="utf-8").strip()
            )
    if result.returncode:
        raise ValueError(
            "top-level Phase-2B infrastructure checker failed:\n"
            + result.stdout[-4000:]
        )
    # The top-level checker intentionally captures focused checker output; its
    # final PASS is emitted only after every integrated Phase-2B checker exits 0.
    required_markers = ("steel-module campaign infrastructure: PASS",)
    if any(marker not in result.stdout for marker in required_markers):
        raise ValueError("top-level checker output lacks a Phase-2B PASS marker")
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "phase2b_markers_verified": True,
        "real_scheduler_contact_performed": False,
        "scheduler_commands_blocked": True,
        "blocked_commands": ["sbatch", "scontrol", "squeue", "sacct"],
    }


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("numpy", "uproot", "matplotlib"):
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            raise ValueError(f"OSC readiness environment lacks {name}") from exc
        versions[name] = str(getattr(module, "__version__", "unknown"))
    return versions


def _frozen_worker_source_probe(execution: object) -> dict[str, object]:
    control_record = execution.manifest["sources"]["control_plane"]
    control_root = execution.directory / control_record["path"]
    if (control_root / ".git").exists():
        raise ValueError("frozen control source unexpectedly contains Git metadata")
    program = (
        "import json,sys; from pathlib import Path; "
        "control=Path(sys.argv[1]).resolve(); execution=Path(sys.argv[2]).resolve(); "
        "sys.path.insert(0,str(control/'hpc/osc')); "
        "from steel_module_production_phase2b_lib import load_execution_companion; "
        "loaded=load_execution_companion(execution,repo_root=control,"
        "require_readiness=False,verify_runtime=True,"
        "verify_phase2a_control_plane=False); "
        "print(json.dumps({'execution_id':loaded.execution_id,"
        "'execution_hash':loaded.execution_hash}))"
    )
    with tempfile.TemporaryDirectory(prefix="phase2b-worker-sentinel-") as raw:
        sentinel_root = Path(raw)
        contact = sentinel_root / "scheduler-contact"
        binary_root = sentinel_root / "bin"
        binary_root.mkdir()
        for name in ("sbatch", "scontrol", "squeue", "sacct"):
            command_path = binary_root / name
            command_path.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s\\n' {name} >> \"${{PHASE2B_SCHEDULER_SENTINEL:?}}\"\n"
                "exit 97\n",
                encoding="utf-8",
            )
            command_path.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(binary_root) + os.pathsep + environment.get(
            "PATH", ""
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PHASE2B_SCHEDULER_SENTINEL"] = str(contact)
        command = [
            sys.executable, "-I", "-c", program, str(control_root),
            str(execution.directory),
        ]
        result = subprocess.run(
            command, cwd=execution.directory, env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if contact.exists():
            raise ValueError(
                "frozen worker-source probe contacted scheduler commands: "
                + contact.read_text(encoding="utf-8").strip()
            )
    if result.returncode:
        raise ValueError(
            "frozen worker-source probe failed without running Geant4:\n"
            + result.stdout[-4000:]
        )
    identity = json.loads(result.stdout)
    if (
        identity.get("execution_id") != execution.execution_id
        or identity.get("execution_hash") != execution.execution_hash
    ):
        raise ValueError("frozen worker-source probe loaded the wrong execution")
    return {
        "command": [sys.executable, "-I", "-c", "<frozen-loader-probe>"],
        "exit_code": 0,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "control_source_has_git_metadata": False,
        "phase2a_lock_relocated": True,
        "runtime_artifacts_verified": True,
        "geant4_run_performed": False,
        "real_scheduler_contact_performed": False,
    }


def _shared_filesystem_probe(parent: Path) -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix=".phase2b-readiness-probe-", dir=parent))
    try:
        lock_path = root / "control.lock"
        lock_path.touch()
        descriptor = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            probe = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); "
                        "\ntry: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)"
                        "\nexcept BlockingIOError: raise SystemExit(0)"
                        "\nraise SystemExit(9)"
                    ),
                    str(lock_path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if probe.returncode != 0:
                raise ValueError("shared-filesystem flock was not visible cross-process")
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

        source = root / "source"
        target = root / "published"
        source.mkdir()
        (source / "payload").write_text("phase2b\n", encoding="utf-8")
        os.rename(source, target)
        if source.exists() or (target / "payload").read_text(encoding="utf-8") != "phase2b\n":
            raise ValueError("shared-filesystem atomic directory publication failed")
        collision = root / "collision"
        collision.mkdir()
        try:
            os.rename(collision, target)
        except OSError:
            pass
        else:
            raise ValueError("shared filesystem replaced an existing evidence directory")
        exclusive = root / "exclusive-record"
        write_exclusive_bytes(exclusive, b"first\n")
        try:
            write_exclusive_bytes(exclusive, b"second\n")
        except FileExistsError:
            pass
        else:
            raise ValueError("shared filesystem replaced an exclusive evidence file")
        if exclusive.read_bytes() != b"first\n":
            raise ValueError("exclusive evidence file changed after collision")
        return {
            "directory": str(parent),
            "cross_process_flock": True,
            "atomic_directory_publish": True,
            "existing_target_rejected": True,
            "exclusive_create_no_replace": True,
            "temporary_probe_removed": True,
        }
    finally:
        shutil.rmtree(root, ignore_errors=False)


def collect(repo_root: Path) -> dict[str, object]:
    root = repo_root.resolve()
    if (root / READINESS_LOCK_RELATIVE).exists():
        raise ValueError("tracked readiness lock already exists; proposal generation is closed")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise ValueError("readiness proposal requires a clean implementation checkout")
    _, phase2a = load_phase2a_lock(root)
    execution_dir = (
        Path(phase2a["canonical_directory"]).parent
        / "steel-module-production-bc-s1-execution"
    )
    execution = load_execution_companion(
        execution_dir, repo_root=root, require_readiness=False
    )
    require_pristine_readiness_workspace(execution)
    missing = [relative for relative in CRITICAL_ARTIFACTS if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"readiness implementation is incomplete: {missing}")
    tools = {}
    for name in ("apptainer", "sbatch", "scontrol", "squeue", "sacct"):
        path = shutil.which(name)
        if path is None:
            raise ValueError(f"OSC readiness command is unavailable: {name}")
        tools[name] = path
    checker_evidence = _run_top_level_checker(root)
    dependency_versions = _dependency_versions()
    shared_probe = _shared_filesystem_probe(execution_dir.parent)
    frozen_worker_probe = _frozen_worker_source_probe(execution)
    control = execution.manifest["sources"]["control_plane"]
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    if control["git_commit"] != head or control["git_tree"] != tree:
        raise ValueError("formal execution companion does not freeze implementation commit C")
    return {
        "schema_version": READINESS_LOCK_SCHEMA_VERSION,
        "status": "proposed-phase-2b-readiness",
        "created_at_utc": utc_now(),
        "phase": "Phase-2B",
        "managed_submission_ready": False,
        "automatic_submission": False,
        "real_slurm_submission_performed": False,
        "formal_intent_count": 0,
        "slurm_job_ids": [],
        "phase2a_lock": execution.manifest["phase2a_lock"],
        "execution_companion": {
            "directory": str(execution.directory),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "managed_execution_json_sha256": sha256_file(execution.directory / "managed_execution.json"),
            "static_checksum_manifest_sha256": sha256_file(execution.directory / "STATIC_SHA256SUMS"),
            "control_lock": execution.manifest["artifacts"]["control_lock"],
        },
        "control_plane": {
            "git_commit": head, "git_tree": tree,
            "source_tree_sha256": control["source_tree_sha256"],
            "artifacts": {
                relative: {"sha256": sha256_file(root / relative)}
                for relative in CRITICAL_ARTIFACTS
            },
        },
        "runtime": execution.manifest["runtime"],
        "osc_capabilities": {
            "commands": tools,
            "real_scheduler_contact_performed": False,
            "test_scheduler": "in-process-fake-only",
        },
        "checker_evidence": {
            "top_level": checker_evidence,
            "shared_filesystem": shared_probe,
            "analysis_environment": dependency_versions,
            "frozen_worker_source": frozen_worker_probe,
            "synthetic_downstream_dry_run": True,
            "formal_execution_intent_count_after_checks": 0,
        },
        "acceptance_instructions": {
            "required_status": "accepted-formal-phase-2b-ready",
            "set_managed_submission_ready": True,
            "review_required": True,
            "proposal_is_authority": False,
        },
    }


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        value = collect(root)
        if args.write_proposal is not None:
            target = args.write_proposal.expanduser().resolve()
            if target == root / READINESS_LOCK_RELATIVE or root in target.parents:
                raise ValueError("proposal must be written outside the repository")
            target.parent.mkdir(parents=True, exist_ok=True)
            write_exclusive_json(target, value)
            print(f"Phase-2B readiness proposal: {target}")
        else:
            print(json.dumps(value, indent=2))
        print("Proposal is non-authoritative; no readiness lock or intent was created.")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot collect Phase-2B readiness evidence: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
