#!/usr/bin/env python3
"""Preview or materialize clean Phase-2C BC-S1 execution-v6."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steel_module_production_phase2c_lib import (
    _execution_v6_authority_hash,
    _phase2c_evidence_binding,
    _validate_phase2c_evidence,
    canonical_phase2c_evidence_root,
    load_preflight_v6,
    materialize_execution_v6,
    validate_preflight_v6_closure,
)


def _accepted_evidence(repo_root: Path, twin: object) -> tuple[Path, dict]:
    parent = canonical_phase2c_evidence_root(repo_root) / "preflight-control/evidence"
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("canonical Phase-2C preflight evidence root is missing")
    candidates = [path for path in parent.iterdir() if path.is_dir() and not path.is_symlink()]
    accepted: list[tuple[Path, dict]] = []
    for candidate in candidates:
        try:
            accepted.append((candidate.resolve(), _validate_phase2c_evidence(candidate, twin=twin)))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
    if len(accepted) != 1:
        raise ValueError(f"expected one accepted Phase-2C preflight evidence bundle, found {len(accepted)}")
    return accepted[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--materialize", action="store_true")
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        twin = load_preflight_v6(repo_root=repo_root)
        validate_preflight_v6_closure(twin, required=True)
        evidence_dir, evidence = _accepted_evidence(repo_root, twin)
        if args.check_only:
            binding = _phase2c_evidence_binding(evidence_dir, evidence)
            execution_hash = _execution_v6_authority_hash(twin, binding)
            print("steel-module Phase-2C execution-v6 preview: PASS")
            print(f"execution_id: sm-v1-production-bc-s1-execution-v6-{execution_hash[:12]}")
            print(f"execution_hash: {execution_hash}")
            print(f"twin_id: {twin.twin_id}")
            print(f"preflight_evidence_id: {evidence['evidence_id']}")
            print(f"production_equivalence_hash: {twin.production_equivalence_hash}")
            print("No file was written and no scheduler command was invoked.")
            return 0
        execution = materialize_execution_v6(
            repo_root=repo_root,
            evidence_dir=evidence_dir,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot materialize Phase-2C execution-v6: {exc}", file=sys.stderr)
        return 1
    print("managed steel-module Phase-2C execution-v6 materialization: PASS")
    print(f"execution_id: {execution.execution_id}")
    print(f"execution_hash: {execution.execution_hash}")
    print(f"production_equivalence_hash: {execution.manifest['production_equivalence_hash']}")
    print(f"output: {execution.directory}")
    print("tasks: 32")
    print("events: 8000")
    print("intents: 0")
    print("production_jobs: 0")
    print("submission_ready: false (tracked R6 required)")
    print("No scheduler command was invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
