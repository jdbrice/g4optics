#!/usr/bin/env python3
"""Retired formal entrypoint for the permanently closed successor-v2."""

from __future__ import annotations

import argparse
import sys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--materialize", action="store_true")
    return parser.parse_args()


def main() -> int:
    parse_args()
    print(
        "Cannot materialize Phase-2B successor-v2: this generation is "
        "permanently closed after failed R3 job 50544247; seal that failure "
        "and use materialize_steel_module_production_successor_v3_execution.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
