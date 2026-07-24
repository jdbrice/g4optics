#!/usr/bin/env python3
"""Validate one canonical Phase-2C preflight evidence bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from steel_module_production_phase2c_preflight_lib import (
    load_formal_context,
    validate_phase2c_preflight_evidence,
)


EVIDENCE_ID_RE = re.compile(r"^sm-v1-phase2c-preflight-[0-9a-f]{12}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--check-only", action="store_true", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        if EVIDENCE_ID_RE.fullmatch(args.evidence_id) is None:
            raise ValueError("Phase-2C preflight evidence ID is invalid")
        context = load_formal_context(repo_root)
        directory = context.control_root / "evidence" / args.evidence_id
        payload = validate_phase2c_preflight_evidence(
            directory, expected_twin=context.twin
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Cannot validate steel-module Phase-2C preflight evidence: {exc}",
            file=sys.stderr,
        )
        return 1
    print("steel-module Phase-2C preflight evidence: PASS")
    print(f"evidence_id: {payload['evidence_id']}")
    print(f"evidence_hash: {payload['evidence_hash']}")
    print(f"job_id: {payload['scheduler']['job_id']}")
    print("Apptainer invoked: true")
    print("Geant4 invoked: false")
    print("twin closed: true")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
