#!/usr/bin/env python3
"""Freeze externally collected terminal accounting for one R3 probe job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_r3_probe_lib import write_terminal_accounting_input


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--sacct-input", required=True, type=Path)
    parser.add_argument("--squeue-input", required=True, type=Path)
    parser.add_argument("--held-scontrol-input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        output = write_terminal_accounting_input(
            output_dir=args.output_dir,
            job_id=args.job_id,
            job_name=args.job_name,
            sacct_text=args.sacct_input.read_text(encoding="utf-8"),
            squeue_text=args.squeue_input.read_text(encoding="utf-8"),
            held_scontrol_text=args.held_scontrol_input.read_text(
                encoding="utf-8"
            ),
            formal_execution_dir=args.execution_dir,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot freeze steel-module R3 accounting: {exc}", file=sys.stderr)
        return 1
    print("steel-module R3 terminal accounting input: PASS")
    print(f"output: {output}")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
