#!/usr/bin/env python3
"""Cheap deterministic checks for the direct BC-S1 analysis adapter."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import analyze_steel_module_campaign as v1
import analyze_steel_module_direct_bc_s1 as direct
from check_steel_module_direct_bc_s1 import fixture_tasks
from plot_steel_module_analysis_v2 import write_recursive_checksums
from steel_module_campaign_lib import CampaignBundle


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def pilot_binding_check(root: Path) -> None:
    pilot = root / "pilot"
    finalized = pilot / "finalized"
    analysis = finalized / "analysis-v2"
    analysis.mkdir(parents=True)
    for name in (
        "task_index.tsv",
        "configuration_summary.csv",
        "event_audit.json",
        "validation_report.json",
        "analysis_config.json",
    ):
        (finalized / name).write_text(f"fixture {name}\n", encoding="utf-8")
    with (finalized / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for name in (
            "analysis_config.json",
            "configuration_summary.csv",
            "event_audit.json",
            "task_index.tsv",
            "validation_report.json",
        ):
            stream.write(f"{direct.sha256_file(finalized / name)}  {name}\n")

    write_json(
        analysis / "analysis_config.json",
        {
            "schema_version": direct.PILOT_ANALYSIS_SCHEMA_VERSION,
            "campaign_id": "fixture-pilot",
            "plan_hash": "1" * 64,
            "simulation_git_commit": "2" * 40,
            "accepted_statistical_evidence": True,
            "analysis_git": {"commit": "3" * 40},
            "v1_reconciliation": {"status": "passed"},
        },
    )
    (analysis / "primary_contrasts.csv").write_text(
        "primary_contrast_id,ratio\nfixture,1.0\n", encoding="utf-8"
    )
    write_recursive_checksums(analysis)
    bundle = CampaignBundle(
        pilot,
        {
            "campaign_id": "fixture-pilot",
            "plan_hash": "1" * 64,
            "git": {"commit": "2" * 40},
        },
        (),
        (),
    )
    record = direct.pilot_baseline_record(bundle, analysis)
    assert record["campaign_id"] == "fixture-pilot"
    assert record["analysis_commit"] == "3" * 40
    assert len(str(record["analysis_v2_sha256s_sha256"])) == 64

    config_path = analysis / "analysis_config.json"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("passed", "failed"),
        encoding="utf-8",
    )
    try:
        direct.pilot_baseline_record(bundle, analysis)
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("tampered pilot analysis unexpectedly passed")


def task_index_check(root: Path) -> None:
    tasks = fixture_tasks()
    bundle = CampaignBundle(root, {}, tasks, ())
    rows: list[dict[str, str]] = []
    audited: dict[str, v1.AuditedRoot] = {}
    for task in tasks:
        row = {field: str(value) for field, value in asdict(task).items()}
        row["root"] = f"attempts/fixture/{task.logical_task_id}.root"
        rows.append(row)
        audited[task.logical_task_id] = v1.AuditedRoot(
            path=row["root"],
            sha256="4" * 64,
            tile_thickness_mm=task.tile_thickness_mm,
            sipm_layout=task.sipm_layout,
            absorber_transverse_mm=task.absorber_transverse_mm,
        )
    direct.validate_direct_task_index(bundle, rows, audited)
    tampered = [dict(row) for row in rows]
    tampered[0]["seed1"] = str(int(tampered[0]["seed1"]) + 1)
    try:
        direct.validate_direct_task_index(bundle, tampered, audited)
    except ValueError as exc:
        assert "seed1" in str(exc)
    else:
        raise AssertionError("tampered direct task index unexpectedly passed")


def pathway_check() -> None:
    def event(generated: int, sipm: int) -> v1.Event:
        return v1.Event(
            generated=generated,
            scintillation=generated,
            sipm=sipm,
            elastic=1,
            inelastic=0,
            capture=0,
            sensor0=sipm,
            sensor1=0,
            sensor2=0,
            sensor3=0,
        )

    pathway = direct.pathway_decomposition(
        {4: [event(100, 10)], 24: [event(600, 15)]}
    )
    assert pathway["generated_optical_production_ratio_24_over_4"] == 6.0
    assert pathway["scintillation_production_ratio_24_over_4"] == 6.0
    assert pathway["collection_ratio_24_over_4"] == 0.25
    assert pathway["observed_net_ratio_24_over_4"] == 1.5
    assert abs(float(pathway["factorization_relative_residual"])) < 1e-15


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="steel-module-direct-analysis-") as raw:
        scratch = Path(raw)
        pilot_binding_check(scratch)
        task_index_check(scratch)
        pathway_check()
    print(
        "steel-module direct BC-S1 analysis: PASS "
        "(pilot binding, task-index mapping, pathway decomposition, tamper rejection)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
