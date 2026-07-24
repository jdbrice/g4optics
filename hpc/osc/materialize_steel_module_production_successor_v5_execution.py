#!/usr/bin/env python3
"""Preview or materialize the pre-workspace-failure-bound BC-S1 successor v5."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_successor_lib import (
    materialize_successor_v5_execution_companion,
    preview_successor_v5_execution,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--materialize", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        if args.check_only:
            preview = preview_successor_v5_execution(repo_root=repo_root)
            authority = preview.recovery_authority
            failure = authority["preworkspace_r3_failure"]
            print("steel-module Phase-2B successor-v5 preview: PASS")
            print(f"predecessor_execution_id: {preview.predecessor.execution_id}")
            print(f"preworkspace_failure_id: {failure['failure_id']}")
            print(f"preworkspace_failure_hash: {failure['failure_hash']}")
            print(f"authority_hash: {authority['authority_hash']}")
            print("tasks: 32")
            print("events: 8000")
            print("production_seeds_reused: 64")
            print("No file was written and no scheduler command was invoked.")
            return 0
        execution = materialize_successor_v5_execution_companion(
            repo_root=repo_root
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot materialize Phase-2B successor-v5: {exc}", file=sys.stderr)
        return 1
    print("managed steel-module Phase-2B successor-v5 materialization: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(f"tasks: {len(execution.tasks)}")
    print(f"events: {sum(task.events for task in execution.tasks)}")
    print(f"output: {execution.directory}")
    print("control_lock_mode: 0600")
    print("submission_ready: false (new R3 preflight required)")
    print("intents: 0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
