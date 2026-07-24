#!/usr/bin/env python3
"""Preview or seal the fixed execution-v4 R3 pre-workspace failure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_r3_preworkspace_failure_lib import (
    collect_r3_preworkspace_failure_evidence,
    seal_r3_preworkspace_failure_evidence,
    validate_r3_preworkspace_failure_bundle,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--seal", action="store_true")
    parser.add_argument("--execution-dir", type=Path, required=True)
    parser.add_argument("--held-scontrol-input", type=Path, required=True)
    parser.add_argument("--sacct-input", type=Path, required=True)
    parser.add_argument("--squeue-input", type=Path, required=True)
    parser.add_argument("--slurm-output-input", type=Path, required=True)
    parser.add_argument("--test-mode", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    kwargs = {
        "execution_dir": args.execution_dir,
        "held_scontrol_input": args.held_scontrol_input,
        "sacct_input": args.sacct_input,
        "squeue_input": args.squeue_input,
        "slurm_output_input": args.slurm_output_input,
        "repo_root": repo_root,
        "test_mode": args.test_mode,
    }
    try:
        if args.check_only:
            payload, _ = collect_r3_preworkspace_failure_evidence(**kwargs)
            print("steel-module R3 pre-workspace failure preview: PASS")
            print(f"failure_id: {payload['failure_id']}")
            print(f"failure_hash: {payload['failure_hash']}")
            print("job_id: 50558158")
            print(
                "failure_stage: portable-exclusive-control-lock-before-workspace"
            )
            print("accepted_compute_preflight_evidence: false")
            print("future_successor_required: true")
            print("workspace_created: false")
            print("apptainer_invoked: false")
            print("geant4_invoked: false")
            print("events_consumed: 0")
            print("production_seeds_consumed: 0")
            print("No file was written and no scheduler command was invoked.")
            return 0
        target = seal_r3_preworkspace_failure_evidence(**kwargs)
        payload = validate_r3_preworkspace_failure_bundle(
            target,
            execution_dir=args.execution_dir,
            require_current_execution=True,
            allow_test_mode=args.test_mode,
            repo_root=repo_root,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Cannot seal steel-module R3 pre-workspace failure: {exc}",
            file=sys.stderr,
        )
        return 1
    print("steel-module R3 pre-workspace failure sealing: PASS")
    print(f"failure_id: {payload['failure_id']}")
    print(f"failure_hash: {payload['failure_hash']}")
    print(f"output: {target}")
    print("accepted_compute_preflight_evidence: false")
    print("future_successor_required: true")
    print("workspace_created: false")
    print("apptainer_invoked: false")
    print("geant4_invoked: false")
    print("events_consumed: 0")
    print("production_seeds_consumed: 0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
