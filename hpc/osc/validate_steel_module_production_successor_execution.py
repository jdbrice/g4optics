#!/usr/bin/env python3
"""Validate the immutable, permanently closed BC-S1 successor-v2 history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2b_lib import load_phase2a_lock
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME_V2,
    load_successor_execution,
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
            / FORMAL_SUCCESSOR_EXECUTION_NAME_V2
        )
        execution = load_successor_execution(
            execution_dir,
            repo_root=repo_root,
            require_readiness=False,
            verify_phase2a_control_plane=True,
            verify_live_predecessor=True,
            allow_closed_v2=True,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate Phase-2B R2 successor: {exc}", file=sys.stderr)
        return 1
    authority = execution.manifest["recovery_authority"]
    print("steel-module Phase-2B successor-v2 closed-history validation: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(f"authority_hash: {authority['authority_hash']}")
    print(f"incident_hash: {authority['incident']['incident_hash']}")
    print("tasks: 32")
    print("events: 8000")
    print("production_seeds_reused: 64")
    print("historical_execution_closed: true")
    print("submission_ready: false (permanent)")
    print("intents: 0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
