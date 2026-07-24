#!/usr/bin/env python3
"""Generate the inert R4 lock candidate from a sealed formal R3 bundle.

The output is not authority by itself.  It becomes usable only after human
review and a clean Git commit whose sole delta from the frozen v3 control
commit is the tracked recovery-readiness lock.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from steel_module_production_phase2b_lib import (
    load_phase2a_lock,
    write_exclusive_json,
)
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME,
    RECOVERY_READINESS_LOCK_RELATIVE,
    build_successor_recovery_readiness_candidate,
    load_successor_execution,
    validate_successor_r2_boundary,
)


FORMAL_PROPOSAL_NAME = (
    "steel-module-phase2b-recovery-readiness-candidate.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--preflight-evidence-dir", required=True, type=Path)
    parser.add_argument("--write-proposal", required=True, type=Path)
    return parser.parse_args()


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def collect(
    *, repo_root: Path, preflight_evidence_dir: Path
) -> dict[str, object]:
    root = repo_root.resolve()
    readiness = root / RECOVERY_READINESS_LOCK_RELATIVE
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("R4 recovery-readiness lock already exists")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("R4 proposal requires a clean v3 implementation checkout")
    _, phase2a = load_phase2a_lock(root)
    execution_dir = (
        Path(phase2a["canonical_directory"]).parent
        / FORMAL_SUCCESSOR_EXECUTION_NAME
    )
    execution = load_successor_execution(
        execution_dir,
        repo_root=root,
        require_readiness=False,
        verify_live_predecessor=True,
    )
    validate_successor_r2_boundary(execution, repo_root=root)
    control = execution.manifest["sources"]["control_plane"]
    if (
        _git(root, "rev-parse", "HEAD") != control["git_commit"]
        or _git(root, "rev-parse", "HEAD^{tree}") != control["git_tree"]
    ):
        raise ValueError("live checkout is not the successor's frozen v3 source")
    return build_successor_recovery_readiness_candidate(
        execution, preflight_evidence_dir=preflight_evidence_dir
    )


def validate_proposal_target(requested: Path, *, execution_root: Path) -> Path:
    """Allow the inert proposal only at the canonical WORK/evidence leaf.

    The proposal is deliberately outside both Git and the execution companion,
    but those exclusions alone are insufficient: writing it inside a sealed
    incident, failed-preflight, or accepted-preflight tree would mutate
    immutable authority before the R4 review.  The single canonical leaf keeps
    proposal generation non-authoritative and non-polluting.
    """

    execution = execution_root.expanduser().resolve()
    work_root = execution.parent.parent
    evidence_root = work_root / "evidence"
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ValueError("canonical WORK/evidence directory is missing or unsafe")
    target = requested.expanduser()
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError("R4 proposal path must not be a symlink")
    target = target.parent.resolve() / target.name
    expected = evidence_root.resolve() / FORMAL_PROPOSAL_NAME
    if target != expected:
        raise ValueError(
            "R4 proposal must use the canonical WORK/evidence top-level path"
        )
    return target


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        proposal = collect(
            repo_root=root,
            preflight_evidence_dir=args.preflight_evidence_dir,
        )
        execution_root = Path(
            proposal["successor_execution"]["directory"]  # type: ignore[index]
        ).resolve()
        target = validate_proposal_target(
            args.write_proposal, execution_root=execution_root
        )
        write_exclusive_json(target, proposal)
        print(f"Phase-2B recovery readiness candidate: {target}")
        print(f"readiness_hash: {proposal['readiness_hash']}")
        print(
            "Candidate is non-authoritative until reviewed and committed as the "
            "sole R4 lock-only delta."
        )
        print("No scheduler command was invoked.")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Cannot generate steel-module recovery readiness candidate: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
