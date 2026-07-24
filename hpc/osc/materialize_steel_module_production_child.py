#!/usr/bin/env python3
"""Materialize the formally locked BC-S1 plan without creating a campaign."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_managed_production_lib import materialize_formal_bc_s1


def parse_args() -> argparse.Namespace:
    return argparse.ArgumentParser(description=__doc__).parse_args()


def main() -> int:
    parse_args()
    try:
        managed = materialize_formal_bc_s1(
            repo_root=Path(__file__).resolve().parents[2]
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot materialize managed steel-module child: {exc}", file=sys.stderr)
        return 1

    program = managed.binding["program"]
    child = managed.binding["child"]
    print("managed steel-module BC-S1 materialization: PASS")
    print(f"reserved_campaign_id: {managed.plan.campaign_id}")
    print(f"program: {program['program_id']}")
    print(f"program_hash: {program['program_hash']}")
    print(f"child_plan_hash: {child['child_plan_hash']}")
    print(f"binding_hash: {managed.binding['binding_hash']}")
    print(f"tasks: {len(managed.plan.tasks)}")
    print(f"events: {sum(task.events for task in managed.plan.tasks)}")
    print(f"output: {managed.directory}")
    print("submittable: false (Phase-2A check-only)")
    print("No submission journal, source archive, or Slurm command was created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
