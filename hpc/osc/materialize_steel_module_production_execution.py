#!/usr/bin/env python3
"""Materialize the canonical Phase-2B BC-S1 execution companion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2b_lib import materialize_execution_companion


def parse_args() -> argparse.Namespace:
    # Formal paths and identities intentionally have no CLI override.
    return argparse.ArgumentParser(description=__doc__, allow_abbrev=False).parse_args()


def main() -> int:
    parse_args()
    try:
        execution = materialize_execution_companion(
            repo_root=Path(__file__).resolve().parents[2]
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot materialize Phase-2B managed execution: {exc}", file=sys.stderr)
        return 1
    print("managed steel-module Phase-2B execution materialization: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(f"tasks: {len(execution.tasks)}")
    print(f"events: {sum(task.events for task in execution.tasks)}")
    print(f"output: {execution.directory}")
    print("submission_ready: false until the tracked Phase-2B readiness lock exists")
    print("No intent was created and no scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
