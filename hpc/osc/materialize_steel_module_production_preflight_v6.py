#!/usr/bin/env python3
"""Preview or materialize the sacrificial Phase-2C BC-S1 preflight twin."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_lib import (
    canonical_preflight_v6_directory,
    materialize_preflight_v6,
    materialize_preflight_v6_retry,
    preview_preflight_v6,
    preview_preflight_v6_retry,
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
        initial_path = canonical_preflight_v6_directory(repo_root)
        initial_exists = initial_path.exists() or initial_path.is_symlink()
        if args.check_only:
            if initial_exists:
                previous, _failure, ordinal, twin_hash, target = (
                    preview_preflight_v6_retry(repo_root=repo_root)
                )
                print("steel-module Phase-2C retry preflight-v6 preview: PASS")
                print(
                    "twin_id: "
                    f"sm-v1-production-bc-s1-preflight-v6-retry-"
                    f"{ordinal:02d}-{twin_hash[:12]}"
                )
                print(f"twin_hash: {twin_hash}")
                print(
                    "production_equivalence_hash: "
                    f"{previous.production_equivalence_hash}"
                )
                print(f"retry_ordinal: {ordinal}")
                print(f"previous_twin_id: {previous.twin_id}")
                print(f"output: {target}")
                print("tasks: 32")
                print("events: 8000")
                print("production_authority: false")
                print("No file was retained and no scheduler command was invoked.")
                return 0
            preview = preview_preflight_v6(repo_root=repo_root)
            print("steel-module Phase-2C preflight-v6 preview: PASS")
            print(f"twin_id: {preview.twin_id}")
            print(f"twin_hash: {preview.twin_hash}")
            print(
                "production_equivalence_hash: "
                f"{preview.production_equivalence['production_equivalence_hash']}"
            )
            print(f"predecessor_execution_id: {preview.predecessor.execution_id}")
            print(f"v5_evidence_hash: {preview.v5_evidence['evidence_hash']}")
            print("tasks: 32")
            print("events: 8000")
            print("production_authority: false")
            print("No file was retained and no scheduler command was invoked.")
            return 0
        twin = (
            materialize_preflight_v6_retry(repo_root=repo_root)
            if initial_exists
            else materialize_preflight_v6(repo_root=repo_root)
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot materialize Phase-2C preflight-v6: {exc}", file=sys.stderr)
        return 1
    print("managed steel-module Phase-2C preflight-v6 materialization: PASS")
    print(f"twin_id: {twin.twin_id}")
    print(f"twin_hash: {twin.twin_hash}")
    print(f"production_equivalence_hash: {twin.production_equivalence_hash}")
    print(f"output: {twin.directory}")
    print("tasks: 32")
    print("events: 8000")
    print("production_authority: false")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
