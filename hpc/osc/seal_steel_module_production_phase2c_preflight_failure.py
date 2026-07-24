#!/usr/bin/env python3
"""Preview or seal a reviewed Phase-2C preflight failure disposition."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_preflight_lib import (
    load_formal_context,
    preview_phase2c_preflight_failure_evidence,
    seal_phase2c_preflight_failure_evidence,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--authorization-sha256", required=True)
    parser.add_argument("--reviewer", default=getpass.getuser())
    parser.add_argument("--rationale", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--seal", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        context = load_formal_context(repo_root)
        if args.check_only:
            value = preview_phase2c_preflight_failure_evidence(
                context,
                authorization_hash=args.authorization_sha256,
                reviewer=args.reviewer,
                rationale=args.rationale,
            )
            print("steel-module Phase-2C preflight failure preview: PASS")
            print(f"failure_evidence_id: {value['failure_evidence_id']}")
            print(f"failure_evidence_hash: {value['failure_evidence_hash']}")
            print(f"failure_class: {value['failure_class']}")
            print(f"retry_eligible: {str(value['retry_eligible']).lower()}")
            print("events/seeds consumed: 0/0")
            print("No file was written and no scheduler command was invoked.")
            return 0
        output = seal_phase2c_preflight_failure_evidence(
            context,
            authorization_hash=args.authorization_sha256,
            reviewer=args.reviewer,
            rationale=args.rationale,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot seal Phase-2C preflight failure: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2C preflight failure sealing: PASS")
    print(f"output: {output}")
    print("twin closed: true")
    print("Geant4 invoked: false")
    print("events/seeds consumed: 0/0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
