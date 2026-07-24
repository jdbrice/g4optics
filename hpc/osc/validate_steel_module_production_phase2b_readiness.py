#!/usr/bin/env python3
"""Validate the accepted formal Phase-2B readiness lock without scheduler contact."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from steel_module_production_phase2b_lib import (
    FORMAL_EXECUTION_NAME,
    load_execution_companion,
    load_phase2a_lock,
    require_pristine_readiness_workspace,
)


def git_output(repo_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git validation failed")
    return result.stdout.strip()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        if git_output(repo_root, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("formal readiness validation requires a clean checkout")
        _, phase2a = load_phase2a_lock(repo_root)
        execution_dir = (
            Path(phase2a["canonical_directory"]).resolve().parent
            / FORMAL_EXECUTION_NAME
        )
        execution = load_execution_companion(
            execution_dir,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_runtime=True,
        )
        require_pristine_readiness_workspace(execution)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate Phase-2B readiness: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2B readiness lock: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print("formal_intents: 0")
    print("real Slurm submission performed: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
