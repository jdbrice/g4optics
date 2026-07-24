#!/usr/bin/env python3
"""Focused scheduler-free checks for the direct edge-two follow-up route."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from generate_steel_module_direct_edge_two_campaign import (
    EXPECTED_EVENT_COUNT,
    EXPECTED_EXCLUDED_SEEDS,
    EXPECTED_TASK_COUNT,
    build_edge_two_tasks,
    validate_direct_edge_two_bundle,
    write_direct_edge_two_campaign_atomic,
)
from steel_module_campaign_lib import environment_identity, load_campaign, sha256_file
from steel_module_production_program_lib import seed_set_hash
from submit_steel_module_campaign import reject_managed_production_route


def git_commit(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def fixture_environment(scratch: Path) -> dict[str, object]:
    image = scratch / "geant4.sif"
    data = scratch / "g4-data-manifest.sha256"
    executable = scratch / "OpNovice2"
    image.write_bytes(b"fixture image\n")
    data.write_text("fixture data manifest\n", encoding="utf-8")
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    environment: dict[str, object] = {
        "mode": "osc-production",
        "geant4_version": "11.4.2",
        "accepted_statistical_evidence": True,
        "image": {"path": str(image), "sha256": sha256_file(image)},
        "g4_data_manifest": {"path": str(data), "sha256": sha256_file(data)},
        "build_artifact": {
            "path": str(executable),
            "sha256": sha256_file(executable),
        },
    }
    environment["identity_hash"] = environment_identity(environment)
    return environment


def fixture_source(repo_root: Path, excluded: set[int]) -> dict[str, object]:
    decision = repo_root / "docs/decisions/steel-module-scan-v1.md"
    return {
        "production_program_directory": "/fixture/production-program",
        "program_id": "sm-v1-production-program-fixture",
        "program_hash": "1" * 64,
        "authorization_graph_hash": "2" * 64,
        "production_program_json_sha256": "3" * 64,
        "production_program_sha256s_sha256": "4" * 64,
        "prior_production_task_count": 914,
        "prior_production_seed_count": 1_828,
        "pilot_excluded_seed_count": 240,
        "excluded_seed_count": EXPECTED_EXCLUDED_SEEDS,
        "excluded_seed_set_hash": seed_set_hash(excluded),
        "decision_document": {
            "path": "docs/decisions/steel-module-scan-v1.md",
            "sha256": sha256_file(decision),
        },
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    excluded = set(range(1, EXPECTED_EXCLUDED_SEEDS + 1))
    tasks = build_edge_two_tasks(excluded)
    assert len(tasks) == EXPECTED_TASK_COUNT
    assert sum(task.events for task in tasks) == EXPECTED_EVENT_COUNT
    assert {task.tile_thickness_mm for task in tasks} == {4, 8, 12, 16, 20, 24}
    assert {task.sipm_layout for task in tasks} == {"edge-two"}
    new_seeds = {seed for task in tasks for seed in (task.seed1, task.seed2)}
    assert not (new_seeds & excluded)

    try:
        build_edge_two_tasks(range(EXPECTED_EXCLUDED_SEEDS - 1))
    except ValueError as exc:
        assert "excluded pilot+production seeds" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("incomplete seed exclusion unexpectedly succeeded")

    with tempfile.TemporaryDirectory(prefix="steel-module-edge-two-check-") as raw:
        scratch = Path(raw)
        campaign = scratch / "campaign"
        environment = fixture_environment(scratch)
        source = fixture_source(repo_root, excluded)
        git = {
            "commit": git_commit(repo_root),
            "branch": "fixture",
            "dirty": False,
            "dirty_paths": [],
        }
        bundle = write_direct_edge_two_campaign_atomic(
            campaign,
            tasks=tasks,
            environment=environment,
            source=source,
            git=git,
            created_at_utc="2026-07-21T00:00:00+00:00",
        )
        validate_direct_edge_two_bundle(bundle)
        reject_managed_production_route(["--campaign-dir", str(campaign)])
        assert bundle.manifest["direct_production"]["child_id"] == "EDGE-TWO"
        assert bundle.manifest["direct_production"]["array_spec"] == "1-240"
        assert len(bundle.scan_args) == 240
        assert all("--sipm-layout edge-two" in line for line in bundle.scan_args)
        assert all("--events 250" in line for line in bundle.scan_args)

        try:
            write_direct_edge_two_campaign_atomic(
                campaign,
                tasks=tasks,
                environment=environment,
                source=source,
                git=git,
            )
        except ValueError as exc:
            assert "refusing to overwrite" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("existing campaign target was overwritten")

        tampered = scratch / "tampered"
        shutil.copytree(campaign, tampered)
        manifest_path = tampered / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["direct_production"]["task_count"] = 239
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        tampered_bundle = load_campaign(tampered, verify_external_artifacts=True)
        try:
            validate_direct_edge_two_bundle(tampered_bundle)
        except ValueError as exc:
            assert "marker mismatch" in str(exc) or "identity hash" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("tampered direct marker unexpectedly validated")

    print(
        "steel-module direct edge-two: PASS "
        "(6 thicknesses, 240 tasks, 60000 events, disjoint new seeds, "
        "ordinary array, no preflight)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
