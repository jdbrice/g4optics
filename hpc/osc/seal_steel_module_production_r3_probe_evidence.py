#!/usr/bin/env python3
"""Seal passed R3 raw output plus terminal accounting as immutable evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_r3_probe_lib import seal_r3_probe_evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--raw-workspace", required=True, type=Path)
    parser.add_argument("--terminal-accounting-dir", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--execution-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output = seal_r3_probe_evidence(
            raw_workspace=args.raw_workspace,
            terminal_accounting_dir=args.terminal_accounting_dir,
            output_root=args.output_root,
            execution_dir=args.execution_dir,
            control_root=Path(__file__).resolve().parents[2],
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot seal steel-module R3 probe evidence: {exc}", file=sys.stderr)
        return 1
    print("steel-module R3 container-probe evidence: PASS")
    print(f"output: {output}")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
