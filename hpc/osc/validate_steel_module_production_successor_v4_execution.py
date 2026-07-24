#!/usr/bin/env python3
"""Validate formal BC-S1 successor-v4 and its no-submission boundary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2b_lib import load_phase2a_lock
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME,
    load_successor_execution,
    validate_successor_r2_boundary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--check-only", action="store_true", required=True)
    return parser.parse_args()


def main() -> int:
    parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        _, phase2a = load_phase2a_lock(repo_root)
        execution_dir = (
            Path(phase2a["canonical_directory"]).parent
            / FORMAL_SUCCESSOR_EXECUTION_NAME
        )
        execution = load_successor_execution(
            execution_dir,
            repo_root=repo_root,
            require_readiness=False,
            verify_live_predecessor=True,
        )
        frozen_execution = load_successor_execution(
            execution_dir,
            repo_root=execution_dir / "sources/control",
            require_readiness=False,
            verify_phase2a_control_plane=False,
            verify_live_predecessor=False,
        )
        if (
            frozen_execution.directory != execution.directory
            or frozen_execution.execution_id != execution.execution_id
            or frozen_execution.execution_hash != execution.execution_hash
            or frozen_execution.manifest != execution.manifest
            or frozen_execution.tasks != execution.tasks
            or frozen_execution.scan_args != execution.scan_args
        ):
            raise ValueError("live and frozen successor-v4 admission disagree")
        validate_successor_r2_boundary(execution, repo_root=repo_root)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate Phase-2B successor-v4: {exc}", file=sys.stderr)
        return 1
    authority = execution.manifest["recovery_authority"]
    print("steel-module Phase-2B successor-v4 validation: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(f"authority_hash: {authority['authority_hash']}")
    print(
        "rejected_r3_hash: "
        f"{authority['rejected_r3_preflight']['rejection_hash']}"
    )
    print("tasks: 32")
    print("events: 8000")
    print("production_seeds_reused: 64")
    print("frozen_worker_admission: true")
    print("submission_ready: false")
    print("intents: 0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
