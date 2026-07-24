#!/usr/bin/env python3
"""Validate the formal Phase-2C preflight twin and its authority boundary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_lib import (
    load_preflight_v6,
    validate_preflight_v6_closure,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        twin = load_preflight_v6(repo_root=repo_root)
        closed = validate_preflight_v6_closure(twin, required=False)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate Phase-2C preflight-v6: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2C preflight-v6 validation: PASS")
    print(f"twin_id: {twin.twin_id}")
    print(f"twin_hash: {twin.twin_hash}")
    print(f"production_equivalence_hash: {twin.production_equivalence_hash}")
    print(f"twin_closed: {str(closed is not None).lower()}")
    print("production_authority: false")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
