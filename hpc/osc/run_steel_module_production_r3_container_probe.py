#!/usr/bin/env python3
"""Run the R3 production-like Apptainer isolation probe on a compute node."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_r3_probe_lib import run_r3_container_probe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    control_root = Path(__file__).resolve().parents[2]
    try:
        workspace = run_r3_container_probe(
            execution_dir=args.execution_dir,
            control_root=control_root,
            workspace=args.workspace,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot run steel-module R3 container probe: {exc}", file=sys.stderr)
        return 1
    print("steel-module R3 container probe: PASS")
    print(f"raw workspace: {workspace}")
    print("Apptainer invoked: true")
    print("Geant4 invoked: false")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
