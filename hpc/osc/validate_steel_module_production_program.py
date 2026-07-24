#!/usr/bin/env python3
"""Validate an immutable Phase-1 steel-module production program."""

from __future__ import annotations

import argparse
from pathlib import Path

from steel_module_production_program_lib import CHILD_ORDER, load_production_program


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program-dir", required=True, type=Path)
    parser.add_argument(
        "--verify-runtime-artifacts",
        action="store_true",
        help="Also resolve and checksum the recorded image, data, executable, and build provenance.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        bundle = load_production_program(
            args.program_dir,
            verify_runtime_artifacts=args.verify_runtime_artifacts,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Invalid steel-module production program: {exc}") from exc

    print("steel-module production program: PASS")
    print(f"program: {bundle.manifest['program_id']}")
    print(f"tasks: {len(bundle.tasks)}")
    print(f"events: {sum(task.events for task in bundle.tasks)}")
    print(
        "children: "
        + " ".join(
            f"{child}={len(bundle.child_tasks[child])}" for child in CHILD_ORDER
        )
    )
    print(f"excluded pilot seeds: {len(bundle.excluded_seeds)}")
    print(f"submittable: {str(bundle.manifest['submittable']).lower()}")
    print(
        "accepted statistical evidence: "
        f"{bundle.manifest['accepted_statistical_evidence']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
