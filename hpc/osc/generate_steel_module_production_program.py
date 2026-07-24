#!/usr/bin/env python3
"""Generate the non-submittable Phase-1 steel-module production program."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steel_module_campaign_lib import (
    canonical_json,
    environment_identity,
    require_dict,
    require_sha256,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_program_lib import (
    ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT,
    ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE,
    ACCEPTED_EXECUTABLE_SHA256,
    build_program_plan,
    load_sealed_pilot,
    read_allocation,
    write_program_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--program-seed", required=True, type=int)
    parser.add_argument("--sealed-pilot-dir", required=True, type=Path)
    parser.add_argument(
        "--executable-build-source-commit",
        required=True,
        help="Full Git commit from which the frozen OpNovice2 executable was built.",
    )
    parser.add_argument(
        "--environment-mode",
        choices=("local-dev", "osc-production"),
        default="local-dev",
    )
    parser.add_argument("--image", type=Path)
    parser.add_argument("--g4-data-manifest", type=Path)
    parser.add_argument("--build-artifact", type=Path)
    parser.add_argument("--build-provenance", type=Path)
    parser.add_argument("--description")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development-only escape hatch; forbidden for osc-production.",
    )
    return parser.parse_args()


def run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def require_full_commit(value: str, label: str) -> str:
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a full lowercase 40-character Git commit")
    return value


def artifact_record(path: Path, label: str) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"missing {label}: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def _historical_record(record: dict[str, Any], label: str) -> dict[str, Any]:
    return {
        "path": f"historical-pilot-hash-only:{label}",
        "sha256": require_sha256(record, "sha256"),
    }


def build_runtime(
    *,
    args: argparse.Namespace,
    repo_root: Path,
    pilot_manifest: dict[str, Any],
    build_source_commit: str,
) -> dict[str, Any]:
    pilot_environment = require_dict(pilot_manifest, "environment")
    geant4_version = pilot_environment.get("geant4_version")
    if geant4_version != "11.4.2":
        raise ValueError("accepted production runtime requires Geant4 11.4.2")
    build_source_tree = run_git(
        repo_root, "rev-parse", f"{build_source_commit}^{{tree}}"
    )
    require_full_commit(build_source_tree, "executable build source tree")
    if build_source_commit != ACCEPTED_EXECUTABLE_BUILD_SOURCE_COMMIT:
        raise ValueError("executable build-source commit is not the accepted commit")
    if build_source_tree != ACCEPTED_EXECUTABLE_BUILD_SOURCE_TREE:
        raise ValueError("executable build-source tree is not the accepted tree")

    if args.environment_mode == "osc-production":
        missing = [
            name
            for name, value in (
                ("--image", args.image),
                ("--g4-data-manifest", args.g4_data_manifest),
                ("--build-artifact", args.build_artifact),
                ("--build-provenance", args.build_provenance),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "osc-production requires runtime provenance: " + ", ".join(missing)
            )
        assert args.image is not None
        assert args.g4_data_manifest is not None
        assert args.build_artifact is not None
        assert args.build_provenance is not None
        image = artifact_record(args.image, "container image")
        data = artifact_record(args.g4_data_manifest, "Geant4 data manifest")
        executable = artifact_record(args.build_artifact, "frozen executable")
        build_provenance = artifact_record(
            args.build_provenance, "build provenance"
        )
        if (args.build_artifact.expanduser().resolve().stat().st_mode & stat.S_IXUSR) == 0:
            raise ValueError("--build-artifact must be executable")
        expected_records = {
            "image": image,
            "g4_data_manifest": data,
            "build_artifact": executable,
        }
        for field, actual in expected_records.items():
            expected = require_sha256(require_dict(pilot_environment, field), "sha256")
            if actual["sha256"] != expected:
                raise ValueError(
                    f"production {field} SHA-256 differs from the sealed pilot runtime"
                )
        artifacts_verified = True
    else:
        image = _historical_record(
            require_dict(pilot_environment, "image"), "container-image"
        )
        data = _historical_record(
            require_dict(pilot_environment, "g4_data_manifest"), "g4-data-manifest"
        )
        executable = _historical_record(
            require_dict(pilot_environment, "build_artifact"), "frozen-executable"
        )
        provenance_payload = {
            "mode": "local-dev-unverified",
            "build_source_commit": build_source_commit,
            "build_source_tree": build_source_tree,
            "executable_sha256": executable["sha256"],
        }
        build_provenance = {
            "path": "not-recorded-local-dev",
            "sha256": sha256_bytes(canonical_json(provenance_payload)),
        }
        artifacts_verified = False

    environment_payload: dict[str, Any] = {
        # The physics runtime identity remains the accepted OSC environment;
        # local-dev changes only whether those historical hashes were verified
        # at generation time.
        "mode": "osc-production",
        "geant4_version": geant4_version,
        "image": image,
        "g4_data_manifest": data,
        "build_artifact": executable,
    }
    runtime_environment_identity = environment_identity(environment_payload)
    if runtime_environment_identity != require_sha256(
        pilot_environment, "identity_hash"
    ):
        raise ValueError("production runtime environment identity differs from pilot")
    if executable["sha256"] != ACCEPTED_EXECUTABLE_SHA256:
        raise ValueError("production executable is not the accepted frozen binary")
    return {
        "mode": args.environment_mode,
        "geant4_version": geant4_version,
        "environment_identity": runtime_environment_identity,
        "artifacts_verified": artifacts_verified,
        "image": image,
        "g4_data_manifest": data,
        "executable": {
            **executable,
            "build_source_commit": build_source_commit,
            "build_source_tree": build_source_tree,
            "build_provenance": build_provenance,
        },
    }


def build_program_source(
    *,
    repo_root: Path,
    dirty: bool,
    dirty_output: str,
    allocation_source: Path,
) -> dict[str, Any]:
    commit = run_git(repo_root, "rev-parse", "HEAD")
    tree = run_git(repo_root, "rev-parse", "HEAD^{tree}")
    require_full_commit(commit, "program source commit")
    require_full_commit(tree, "program source tree")
    source_files = (
        "hpc/osc/generate_steel_module_production_program.py",
        "hpc/osc/steel_module_production_program_lib.py",
        "hpc/osc/steel_module_campaign_lib.py",
        "docs/decisions/steel-module-scan-v1.md",
        "docs/decisions/steel-module-analysis-v2-review-v1.md",
    )
    artifacts: dict[str, Any] = {}
    for relative in source_files:
        path = repo_root / relative
        artifacts[relative] = {"sha256": sha256_file(path)}
    artifacts[str(allocation_source.relative_to(repo_root))] = {
        "sha256": sha256_file(allocation_source)
    }
    return {
        "git_commit": commit,
        "git_tree": tree,
        "branch": run_git(repo_root, "branch", "--show-current"),
        "dirty": dirty,
        "dirty_paths": dirty_output.splitlines() if dirty else [],
        "artifacts": artifacts,
    }


def main() -> int:
    args = parse_args()
    try:
        if args.program_seed < 0:
            raise ValueError("--program-seed must be non-negative")
        build_source_commit = require_full_commit(
            args.executable_build_source_commit,
            "--executable-build-source-commit",
        )
        if args.environment_mode == "osc-production" and args.allow_dirty:
            raise ValueError("--allow-dirty is forbidden for osc-production")

        repo_root = Path(__file__).resolve().parents[2]
        dirty_output = run_git(
            repo_root, "status", "--porcelain", "--untracked-files=all"
        )
        dirty = bool(dirty_output)
        if dirty and not args.allow_dirty:
            raise ValueError(
                "refusing to freeze a program from a dirty checkout; commit the "
                "intended implementation first"
            )

        allocation_source = (
            repo_root
            / "hpc/osc/configurations/steel-module-production-allocation-v1.tsv"
        )
        allocation_rows = read_allocation(allocation_source)
        pilot_exclusion, excluded_seeds = load_sealed_pilot(
            args.sealed_pilot_dir
        )
        pilot_manifest = json.loads(
            (args.sealed_pilot_dir.expanduser().resolve() / "campaign.json").read_text(
                encoding="utf-8"
            )
        )
        runtime = build_runtime(
            args=args,
            repo_root=repo_root,
            pilot_manifest=pilot_manifest,
            build_source_commit=build_source_commit,
        )
        program_source = build_program_source(
            repo_root=repo_root,
            dirty=dirty,
            dirty_output=dirty_output,
            allocation_source=allocation_source,
        )
        plan = build_program_plan(
            allocation_rows=allocation_rows,
            program_seed=args.program_seed,
            excluded_seeds=excluded_seeds,
            geant4_version=str(runtime["geant4_version"]),
        )
        description = args.description or (
            "Steel-module SMS-016..019 staged production program"
        )
        bundle = write_program_atomic(
            args.out_dir,
            plan=plan,
            allocation_source=allocation_source,
            allocation_rows=allocation_rows,
            program_seed=args.program_seed,
            pilot_exclusion=pilot_exclusion,
            excluded_seeds=excluded_seeds,
            program_source=program_source,
            runtime=runtime,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            description=description,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Cannot generate steel-module production program: {exc}") from exc

    print(f"Program: {bundle.manifest['program_id']}")
    print(f"Tasks: {bundle.manifest['task_count']}")
    print(f"Events: {bundle.manifest['total_events']}")
    print("Children: FIXED=592 BC-S1=32 BC-S2=48 BC-S3=80 BC-S4=162")
    print(f"Accepted statistical evidence: {bundle.manifest['accepted_statistical_evidence']}")
    print(f"Output: {bundle.directory}")
    print("Submittable: no (Phase-1 plan only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
