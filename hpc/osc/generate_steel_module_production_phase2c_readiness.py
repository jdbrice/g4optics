#!/usr/bin/env python3
"""Emit a Phase-2C R6 proposal or publish its reviewed lock-only payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_lib import canonical_phase2c_evidence_root
from steel_module_production_phase2c_readiness import (
    PROPOSAL_FILENAME,
    write_phase2c_readiness_lock,
    write_phase2c_readiness_proposal,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write-proposal", action="store_true")
    mode.add_argument("--write-lock", action="store_true")
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--reviewer")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        if args.write_proposal:
            if args.proposal is not None or args.reviewer is not None:
                raise ValueError("proposal generation has no formal overrides")
            evidence_root = canonical_phase2c_evidence_root(repo_root)
            if evidence_root.is_symlink() or not evidence_root.is_dir():
                raise ValueError("canonical Phase-2C evidence root is missing")
            target = write_phase2c_readiness_proposal(
                repo_root=repo_root,
                output=evidence_root / PROPOSAL_FILENAME,
            )
            print(f"Phase-2C R6 readiness proposal: {target}")
            print("Proposal is non-authoritative; no readiness lock was created.")
            print("production intents/jobs: 0")
            return 0
        if args.proposal is None or args.reviewer is None:
            raise ValueError("--write-lock requires --proposal and --reviewer")
        target = write_phase2c_readiness_lock(
            proposal_path=args.proposal,
            repo_root=repo_root,
            reviewer=args.reviewer,
        )
        print(f"Phase-2C R6 readiness lock written: {target}")
        print("Commit this file as the only tracked delta; no scheduler was contacted.")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot generate Phase-2C readiness: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
