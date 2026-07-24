#!/usr/bin/env python3
"""Validate an externally captured held R3 job before any release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_r3_probe_lib import validate_held_r3_job_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--held-scontrol-input", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        identity = validate_held_r3_job_snapshot(
            execution_dir=args.execution_dir,
            control_root=repo_root,
            held_scontrol_input=args.held_scontrol_input,
            job_id=args.job_id,
            job_name=args.job_name,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate held steel-module R3 job: {exc}", file=sys.stderr)
        return 1
    print("steel-module held R3 job identity: PASS")
    print(f"job_id: {identity['job_id']}")
    print(f"job_name: {identity['job_name']}")
    print(f"account: {identity['account']}")
    print(f"execution_id: {identity['execution_id']}")
    print(f"execution_hash: {identity['execution_hash']}")
    print("state: PENDING / JobHeldUser")
    print("non_array: true")
    print("requeue: 0")
    print("scheduler contact performed by validator: false")
    print("release performed by validator: false")
    print("geant4 invoked: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
