#!/usr/bin/env python3
"""Run C6 checkers and freeze their no-scheduler OSC acceptance evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from steel_module_production_phase2c_readiness import (
    write_phase2c_c6_acceptance,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--write", action="store_true", required=True)
    return parser.parse_args()


def main() -> int:
    parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        target = write_phase2c_c6_acceptance(repo_root=repo_root)
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Cannot freeze Phase-2C C6 acceptance: {exc}", file=sys.stderr)
        return 1
    print("steel-module Phase-2C C6 OSC acceptance: PASS")
    print(f"output: {target}")
    payload = json.loads((target / "acceptance.json").read_text(encoding="utf-8"))
    print(f"acceptance_id: {payload['acceptance_id']}")
    print(f"acceptance_hash: {payload['acceptance_hash']}")
    print("top/focused checker exits: 0/0")
    print("real Slurm calls: 0")
    print("Geant4 invoked: false")
    print("No readiness lock or production intent was created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
