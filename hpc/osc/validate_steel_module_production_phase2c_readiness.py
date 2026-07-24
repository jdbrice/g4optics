#!/usr/bin/env python3
"""Validate the lock-only R6 authority and pristine execution-v6."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_lib import load_execution_v6
from steel_module_production_phase2c_readiness import verify_phase2c_readiness


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--check-only", action="store_true", required=True)
    return parser.parse_args()


def main() -> int:
    parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        execution = load_execution_v6(
            repo_root=repo_root,
            require_readiness=False,
            require_pristine=True,
        )
        path, readiness = verify_phase2c_readiness(
            execution, repo_root=repo_root
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate Phase-2C R6 readiness: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2C R6 readiness lock: PASS")
    print(f"path: {path}")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(
        "production_equivalence_hash: "
        f"{readiness['production_equivalence_hash']}"
    )
    print("managed_submission_ready: true")
    print("production intents/jobs: 0")
    print(
        "preflight job: "
        f"{readiness['preflight_slurm_job_ids'][0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
