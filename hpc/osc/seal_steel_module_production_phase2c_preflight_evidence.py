#!/usr/bin/env python3
"""Seal successful Phase-2C accounting and raw output as immutable evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_preflight_lib import (
    load_formal_context,
    preview_phase2c_preflight_evidence,
    seal_phase2c_preflight_evidence,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--authorization-sha256", required=True)
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
            evidence = preview_phase2c_preflight_evidence(
                context,
                authorization_hash=args.authorization_sha256,
            )
            print("steel-module Phase-2C preflight evidence preview: PASS")
            print(f"evidence_id: {evidence['evidence_id']}")
            print(f"evidence_hash: {evidence['evidence_hash']}")
            print(f"job_id: {evidence['scheduler']['job_id']}")
            print("Apptainer invoked: true")
            print("Geant4 invoked: false")
            print("No file was written and no scheduler command was invoked.")
            return 0
        output = seal_phase2c_preflight_evidence(
            context, authorization_hash=args.authorization_sha256
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Cannot seal steel-module Phase-2C preflight evidence: {exc}",
            file=sys.stderr,
        )
        return 1
    print("steel-module Phase-2C preflight evidence sealing: PASS")
    print(f"output: {output}")
    print("Apptainer invoked: true")
    print("Geant4 invoked: false")
    print("events consumed: 0")
    print("production seeds consumed: 0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
