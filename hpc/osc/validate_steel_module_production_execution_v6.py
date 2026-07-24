#!/usr/bin/env python3
"""Validate formal clean execution-v6 before the lock-only R6 commit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_lib import (
    load_execution_v6,
    phase2c_recovery_lineage,
    validate_phase2c_recovery_lineage,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        execution = load_execution_v6(
            repo_root=repo_root,
            require_readiness=False,
            require_pristine=True,
        )
        lineage = phase2c_recovery_lineage(execution)
        validate_phase2c_recovery_lineage(execution, lineage)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate Phase-2C execution-v6: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2C execution-v6 validation: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(f"production_equivalence_hash: {execution.manifest['production_equivalence_hash']}")
    print(f"phase2c_authority_hash: {execution.manifest['phase2c_authority']['authority_hash']}")
    print("tasks: 32")
    print("events: 8000")
    print("intents: 0")
    print("production_jobs: 0")
    print("submission_ready: false (tracked R6 required)")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
