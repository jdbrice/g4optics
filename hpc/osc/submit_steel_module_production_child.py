#!/usr/bin/env python3
"""Validate the one managed BC-S1 plan; Phase-2A cannot submit it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_managed_production_lib import load_managed_production_child


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-child-dir", required=True, type=Path)
    parser.add_argument(
        "--g4-data-root",
        required=True,
        type=Path,
        help="Host directory containing the pinned Geant4 11.4.2 datasets.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Perform read-only formal validation. This is the only Phase-2A mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.check_only:
        print(
            "Cannot submit managed production child: Phase-2A is check-only; "
            "actual Slurm submission remains locked until the downstream closure "
            "is implemented and reviewed",
            file=sys.stderr,
        )
        return 2

    try:
        data_root = args.g4_data_root.expanduser().resolve()
        if not data_root.is_dir():
            raise ValueError(f"missing Geant4 data root: {data_root}")
        managed = load_managed_production_child(
            args.managed_child_dir,
            repo_root=Path(__file__).resolve().parents[2],
            verify_runtime_artifacts=True,
            verify_control_plane=True,
            require_current_control_plane=True,
        )
        if managed.child_id != "BC-S1":
            raise ValueError("Phase-2A authorizes only the exact BC-S1 child")
        if managed.binding.get("accepted_statistical_evidence") is not True:
            raise ValueError("managed child is not accepted statistical evidence")
        if managed.binding.get("test_mode") is not False:
            raise ValueError("formal managed child cannot be a test fixture")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot validate managed production child: {exc}", file=sys.stderr)
        return 1

    child = managed.binding["child"]
    program = managed.binding["program"]
    print("managed steel-module production child: PASS")
    print(f"reserved_campaign_id: {managed.plan.campaign_id}")
    print(f"program: {program['program_id']}")
    print(f"program_hash: {program['program_hash']}")
    print(f"child: {managed.child_id}")
    print(f"child_plan_hash: {child['child_plan_hash']}")
    print(f"binding_hash: {managed.binding['binding_hash']}")
    print(
        "control_plane_commit: "
        f"{managed.binding['control_plane_source']['git_commit']}"
    )
    print(f"tasks: {len(managed.plan.tasks)}")
    print(f"events: {sum(task.events for task in managed.plan.tasks)}")
    print("submittable: false (Phase-2A check-only)")
    print("No submission journal, source archive, or Slurm command was created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
