#!/usr/bin/env python3
"""Preview or seal the fixed execution-v3 R3 probe rejection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_r3_probe_rejection_lib import (
    collect_r3_probe_rejection_evidence,
    seal_r3_probe_rejection_evidence,
    validate_r3_probe_rejection_bundle,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--seal", action="store_true")
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument("--raw-workspace", required=True, type=Path)
    parser.add_argument("--held-scontrol-input", required=True, type=Path)
    parser.add_argument("--sacct-input", required=True, type=Path)
    parser.add_argument("--squeue-input", required=True, type=Path)
    parser.add_argument("--slurm-output-input", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    kwargs = {
        "execution_dir": args.execution_dir,
        "raw_workspace": args.raw_workspace,
        "held_scontrol_input": args.held_scontrol_input,
        "sacct_input": args.sacct_input,
        "squeue_input": args.squeue_input,
        "slurm_output_input": args.slurm_output_input,
        "repo_root": repo_root,
    }
    try:
        if args.check_only:
            payload, _ = collect_r3_probe_rejection_evidence(**kwargs)
            print("steel-module R3 probe rejection preview: PASS")
            print(f"rejection_id: {payload['rejection_id']}")
            print(f"rejection_hash: {payload['rejection_hash']}")
            print(f"job_id: {payload['scheduler']['job_id']}")
            print(
                "false_report_keys: "
                + ",".join(payload["probe"]["false_report_keys"])
            )
            print("accepted_compute_preflight_evidence: false")
            print("events_consumed: 0")
            print("production_seeds_consumed: 0")
            print("No file was written and no scheduler command was invoked.")
            return 0
        target = seal_r3_probe_rejection_evidence(**kwargs)
        payload = validate_r3_probe_rejection_bundle(
            target,
            execution_dir=args.execution_dir,
            require_current_execution=True,
            repo_root=repo_root,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot seal steel-module R3 probe rejection: {exc}", file=sys.stderr)
        return 1
    print("steel-module R3 probe rejection sealing: PASS")
    print(f"rejection_id: {payload['rejection_id']}")
    print(f"rejection_hash: {payload['rejection_hash']}")
    print(f"output: {target}")
    print("accepted_compute_preflight_evidence: false")
    print("apptainer_invoked: true")
    print("geant4_invoked: false")
    print("events_consumed: 0")
    print("production_seeds_consumed: 0")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
