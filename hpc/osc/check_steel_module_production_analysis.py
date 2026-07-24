#!/usr/bin/env python3
"""Exercise Phase-2B analysis, review, and progression infrastructure."""

from __future__ import annotations

import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import analyze_steel_module_campaign as v1
import analyze_steel_module_production_checkpoint as analyzer
import check_steel_module_production_finalization as finalization_fixture
import finalize_steel_module_managed_child as managed_finalizer
import record_steel_module_progression as recorder
import render_steel_module_production_review as renderer_module
from build_steel_module_production_checkpoint import build_checkpoint
from plot_steel_module_analysis_v2 import (
    sha256_file,
    verify_checksum_manifest,
    write_recursive_checksums,
)
from realistic_neutron_event_audit import (
    EVENT_SCHEMA_FIELDS,
    SIPM_SENSOR_FIELDS,
    read_and_audit_root,
)
from steel_module_production_checkpoint_lib import (
    load_production_checkpoint,
    managed_finalization_directory,
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def run(command: list[str], *, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != expected:
        raise AssertionError(
            f"command returned {result.returncode}, expected {expected}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def synthetic_groups() -> tuple[
    dict[int, list[v1.Event]],
    dict[int, dict[int, list[v1.Event]]],
    dict[int, list[analyzer.LocatedEvent]],
    dict[tuple[int, int], str],
]:
    groups: dict[int, list[v1.Event]] = {}
    blocks: dict[int, dict[int, list[v1.Event]]] = {}
    located: dict[int, list[analyzer.LocatedEvent]] = {}
    task_ids: dict[tuple[int, int], str] = {}
    for thickness, scale in ((4, 1), (24, 3)):
        endpoint: list[v1.Event] = []
        endpoint_blocks: dict[int, list[v1.Event]] = {}
        endpoint_located: list[analyzer.LocatedEvent] = []
        for block in range(16):
            logical_id = f"fixture-t{thickness:02d}-b{block:02d}"
            task_ids[(thickness, block)] = logical_id
            values: list[v1.Event] = []
            for entry in range(250):
                positive = (entry + block * 7) % 5 == 0
                sipm = scale * (2 + entry % 7) if positive else 0
                if block == 15 and entry == 249:
                    sipm = scale * 300
                event = v1.Event(
                    generated=sipm * 20,
                    scintillation=sipm * 18,
                    sipm=sipm,
                    elastic=1 if positive else 0,
                    inelastic=0,
                    capture=0,
                    sensor0=sipm,
                    sensor1=0,
                    sensor2=0,
                    sensor3=0,
                )
                values.append(event)
                endpoint_located.append(
                    analyzer.LocatedEvent(event, logical_id, block, entry)
                )
            endpoint_blocks[block] = values
            endpoint.extend(values)
        groups[thickness] = endpoint
        blocks[thickness] = endpoint_blocks
        located[thickness] = endpoint_located
    return groups, blocks, located, task_ids


def phase2c_root_arrays(task: object, np: object) -> dict[str, object]:
    """Build one complete causal-audit ROOT payload for a BC-S1 block."""

    count = 250
    block = int(task.seed_block)
    scale = {4: 1, 24: 3}[int(task.tile_thickness_mm)]
    arrays = {
        field: np.zeros(count, dtype=np.float64)
        for field in (*EVENT_SCHEMA_FIELDS, *SIPM_SENSOR_FIELDS)
    }
    arrays["event_id"] = np.arange(count, dtype=np.int64)
    arrays["shoot_z_mm"].fill(40.0)
    arrays["primary_kinetic_energy_mev"].fill(1000.0)
    for field in (
        "hit_x_mm",
        "hit_y_mm",
        "hit_z_mm",
        "scint_centroid_x_mm",
        "scint_centroid_y_mm",
        "scint_centroid_z_mm",
        "primary_neutron_tile_entry_x_mm",
        "primary_neutron_tile_entry_y_mm",
        "primary_neutron_tile_entry_z_mm",
        "collection_efficiency",
    ):
        arrays[field].fill(np.nan)

    for entry in range(count):
        positive = (entry + block * 7) % 5 == 0
        sipm = scale * (2 + entry % 7) if positive else 0
        if block == 15 and entry == count - 1:
            sipm = scale * 300
        active = sipm > 0
        generated = sipm * 20
        scintillation = sipm * 18
        arrays["generated_optical_photons"][entry] = generated
        arrays["scintillation_photons"][entry] = scintillation
        arrays["cerenkov_photons"][entry] = generated - scintillation
        arrays["sipm_detected_photons"][entry] = sipm
        arrays["sipm_sensor_0_detected_photons"][entry] = sipm
        arrays["collection_efficiency_valid"][entry] = int(active)
        arrays["primary_neutron_elastic_count"][entry] = int(active)
        arrays["primary_neutron_elastic_flag"][entry] = int(active)
        arrays["primary_neutron_any_interaction_flag"][entry] = int(active)
        arrays["steel_edep_mev"][entry] = float(active)
        arrays["scint_centroid_valid"][entry] = int(active)
        if active:
            arrays["collection_efficiency"][entry] = sipm / generated
            arrays["scint_centroid_x_mm"][entry] = 0.0
            arrays["scint_centroid_y_mm"][entry] = 0.0
            arrays["scint_centroid_z_mm"][entry] = 0.0
    return arrays


def write_phase2c_root(
    path: Path, task: object, np: object, uproot: object
) -> dict[str, int | float]:
    arrays = phase2c_root_arrays(task, np)
    with uproot.recreate(path) as root_file:
        root_file.mktree(
            "scan", {field: values.dtype for field, values in arrays.items()}
        )
        root_file["scan"].extend(arrays)
    _, report = read_and_audit_root(
        path, expected_events=int(task.events), context=str(task.logical_task_id)
    )
    return report


def phase2c_seed_audit(
    tasks: tuple[object, ...], identity: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    program = {
        "program_id": "fixture-phase2c-program",
        "program_hash": "9" * 64,
    }
    audit = {
        "schema_version": "steel-module-managed-seed-audit-v1",
        "valid": True,
        "production_seed_count": 64,
        "production_unique_seed_count": 64,
        "production_seed_set_hash": identity["seed_set_hash"],
        "sealed_pilot_seed_count": 240,
        "sealed_pilot_unique_seed_count": 240,
        "sealed_pilot_seed_set_hash": "5" * 64,
        "overlap_count": 0,
        "overlap": [],
        "parent_registry_reconciled": True,
        "phase2a_plan_reconciled": True,
        "run_config_reconciled": True,
        "task_result_reconciled": True,
        "tasks": [
            {
                "logical_task_id": task.logical_task_id,
                "seed_block": task.seed_block,
                "seed1": task.seed1,
                "seed2": task.seed2,
            }
            for task in tasks
        ],
    }
    return program, audit


def phase2c_v6_finalizer_checkpoint_analyzer_e2e(root: Path) -> None:
    """Carry one test-only v6 child through finalization, checkpoint, and analysis."""

    if (
        importlib.util.find_spec("numpy") is None
        or importlib.util.find_spec("uproot") is None
    ):
        raise AssertionError("Phase-2C integrated evidence requires NumPy and uproot")
    import numpy as np  # type: ignore[import-not-found]
    import uproot  # type: ignore[import-not-found]

    execution = finalization_fixture.fixture_phase2c_successor(root)
    execution_dir = execution.directory
    execution_dir.mkdir(parents=True)
    tasks = finalization_fixture.fixture_tasks(execution_dir)
    identity = finalization_fixture.fixture_task_identity(execution_dir)
    execution.tasks = tasks
    execution.campaign_id = "fixture-phase2c-bc-s1"
    execution.plan_hash = "1" * 64
    execution.git_commit = "2" * 40
    execution.managed_child = SimpleNamespace(
        binding={
            "binding_hash": "3" * 64,
            "child": {
                "child_plan_hash": "4" * 64,
                "task_set_hash": identity["task_set_hash"],
                "parent_task_plan_hash": "5" * 64,
                "task_seed_mapping_hash": identity["task_seed_mapping_hash"],
                "seed_set_hash": identity["seed_set_hash"],
            },
        }
    )
    execution.manifest.update(
        {
            "accepted_statistical_evidence": False,
            "test_mode": True,
            "sealed_pilot": {
                "directory": "/fixture/sealed-pilot",
                "campaign_id": "fixture-sealed-pilot",
                "plan_hash": "6" * 64,
                "simulation_commit": "7" * 40,
                "finalized_checksums_sha256": "8" * 64,
                "analysis_v2_checksums_sha256": "9" * 64,
                "analysis_v2_config_sha256": "a" * 64,
                "analysis_commit": "b" * 40,
            },
        }
    )

    attempt_id = "fixture-predecessor-retry"
    job_id = "70000001"
    selected: list[managed_finalizer.ManagedCandidate] = []
    attempt_root = execution_dir / "attempts" / attempt_id
    for task in tasks:
        task_dir = attempt_root / "tasks" / task.logical_task_id
        task_dir.mkdir(parents=True)
        paths = {
            "run_config": task_dir / "run_config.json",
            "macro": task_dir / "run.mac",
            "simulation_log": task_dir / "simulation.log",
            "root": task_dir / "result.root",
            "summary": task_dir / "summary.csv",
            "efficiency_map": task_dir / "efficiency_map.csv",
        }
        write_json(paths["run_config"], {"fixture": True})
        paths["macro"].write_text("# fixture\n", encoding="utf-8")
        paths["simulation_log"].write_text("fixture complete\n", encoding="utf-8")
        report = write_phase2c_root(paths["root"], task, np, uproot)
        summary = {key: str(value) for key, value in report.items()}
        write_csv(paths["summary"], list(summary), [summary])
        paths["efficiency_map"].write_text("sensor,photons\n0,0\n", encoding="utf-8")
        artifacts = {
            name: {
                "path": str(path.relative_to(execution_dir)),
                "sha256": sha256_file(path),
            }
            for name, path in paths.items()
        }
        marker = {
            "schema_version": "steel-module-production-test-result-v1",
            "logical_task_id": task.logical_task_id,
            "artifacts": artifacts,
        }
        marker_path = task_dir / "task_result.json"
        write_json(marker_path, marker)
        selected.append(
            managed_finalizer.ManagedCandidate(
                task=task,
                attempt={"attempt_id": attempt_id},
                array_index=task.task_index,
                job_id=job_id,
                marker_path=marker_path,
                marker=marker,
                paths=paths,
                summary=summary,
                event_report=report,
            )
        )
    accounting = attempt_root / "accounting"
    accounting.mkdir()
    write_json(
        accounting / "frozen.json",
        {"attempt_id": attempt_id, "job_id": job_id},
    )
    finalized_container = execution_dir / "finalized"
    finalized_container.mkdir(mode=0o700)
    execution_dir.chmod(0o555)
    try:
        output = managed_finalization_directory(execution_dir, execution.manifest)
        with patch.object(
            managed_finalizer,
            "_program_and_seed_audit",
            return_value=phase2c_seed_audit(tasks, identity),
        ), patch.object(
            managed_finalizer,
            "load_frozen_accounting",
            return_value={"attempt_id": attempt_id, "job_id": job_id},
        ):
            managed_finalizer.finalize(
                execution,
                root,
                output,
                selected,
                [],
                [attempt_id],
                selection={},
                selection_file_sha256=None,
            )
        checkpoint_dir = build_checkpoint(
            output,
            root / "checkpoints",
            allow_test_mode=True,
            verify_root_files=True,
        )
        loaded = load_production_checkpoint(
            checkpoint_dir,
            allow_test_mode=True,
            require_readiness=False,
            verify_root_files=True,
        )
        assert loaded.manifest["test_mode"] is True
        assert loaded.manifest["accepted_statistical_evidence"] is False
        assert len(loaded.task_rows) == 32
        checkpoint = analyzer.load_json(checkpoint_dir / "checkpoint.json")
        source_children = analyzer.load_json(checkpoint_dir / "source_children.json")
        event_audit = analyzer.load_json(checkpoint_dir / "event_audit.json")
        task_rows = analyzer.read_table(checkpoint_dir / "task_index.tsv", delimiter="\t")
        analyzer.validate_checkpoint_shape(checkpoint, task_rows)
        groups, blocks, located = analyzer.load_checkpoint_events(
            checkpoint_dir,
            checkpoint,
            task_rows,
            event_audit,
            source_children,
        )
        assert {thickness: len(events) for thickness, events in groups.items()} == {
            4: 4000,
            24: 4000,
        }
        assert len({row["root"] for row in task_rows}) == 32
        located_by_thickness: dict[int, list[analyzer.LocatedEvent]] = {
            4: [],
            24: [],
        }
        task_ids: dict[tuple[int, int], str] = {}
        identities = {
            row["logical_task_id"]: (
                int(row["tile_thickness_mm"]),
                int(row["seed_block"]),
            )
            for row in task_rows
        }
        for item in located:
            thickness, block = identities[item.logical_task_id]
            located_by_thickness[thickness].append(item)
            task_ids[(thickness, block)] = item.logical_task_id
        result = analyzer.analyze_event_groups(
            groups,
            blocks,
            located_by_thickness,
            task_ids,
            pilot={
                "ratio": 2.5,
                "ci95_low": 0.5,
                "ci95_high": 4.5,
                "relative_half_width": 0.8,
                "events_per_endpoint": 1000,
            },
            np=np,
        )
        assert result.primary["ratio"] == 3.0
        assert result.primary["valid_resamples"] == 10_000
        assert len(result.loo_rows) == 32

        tampered_root = Path(task_rows[0]["root"])
        with tampered_root.open("ab") as stream:
            stream.write(b"phase2c-tamper")
        entered_analysis_core = False
        try:
            analyzer.load_checkpoint_events(
                checkpoint_dir,
                checkpoint,
                task_rows,
                event_audit,
                source_children,
            )
            entered_analysis_core = True
        except ValueError as exc:
            assert "checkpoint ROOT checksum mismatch" in str(exc)
        else:
            raise AssertionError("tampered ROOT reached the production analysis core")
        assert entered_analysis_core is False
    finally:
        execution_dir.chmod(0o755)


def core_analysis_check() -> None:
    assert analyzer.numeric_eligibility(
        relative_half_width=0.10, maximum_loo_shift=0.10, narrowing=False
    ) == (["stop-success", "pause-review"], "precision-success")
    assert analyzer.numeric_eligibility(
        relative_half_width=0.20, maximum_loo_shift=0.20, narrowing=True
    ) == (["continue", "pause-review"], "continue-eligible")
    assert analyzer.numeric_eligibility(
        relative_half_width=0.20, maximum_loo_shift=0.2000001, narrowing=True
    ) == (["pause-review"], "pause-only")
    assert recorder.final_allowed(
        ["continue", "pause-review"], "material-worsening"
    ) == ["pause-review"]
    positive = v1.Event(1, 1, 1, 0, 0, 0, 1, 0, 0, 0)
    zero = v1.Event(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    try:
        analyzer.ratio_summary(
            {4: [positive], 24: [zero]},
            {4: {"net": [1.0]}, 24: {"net": [0.0]}},
        )
    except ValueError as exc:
        assert "cannot support" in str(exc)
    else:
        raise AssertionError("zero numerator unexpectedly produced formal evidence")
    task_row = {
        "logical_task_id": "fixture-task",
        "tile_thickness_mm": "4",
        "sipm_layout": "back-center",
        "seed_block": "0",
        "events": "250",
        "root": "/fixture/task.root",
        "root_sha256": "a" * 64,
    }
    task_audit = {
        "logical_task_id": "fixture-task",
        "tile_thickness_mm": 4,
        "sipm_layout": "back-center",
        "seed_block": 0,
        "events": 250,
        "root": "/fixture/task.root",
        "root_sha256": "a" * 64,
    }
    assert analyzer.reconcile_task_audit(task_row, task_audit) == (
        "/fixture/task.root",
        "a" * 64,
    )
    task_audit["root_sha256"] = "b" * 64
    try:
        analyzer.reconcile_task_audit(task_row, task_audit)
    except ValueError as exc:
        assert "event audit disagree" in str(exc)
    else:
        raise AssertionError("task-index/event-audit mismatch was accepted")
    with tempfile.TemporaryDirectory(prefix="steel-module-snapshot-check-") as raw:
        scratch = Path(raw)
        evidence = scratch / "evidence.bin"
        evidence.write_bytes(b"before")
        first: dict[str, str] = {}
        analyzer._record_snapshot_file(first, "fixture", evidence)
        evidence.write_bytes(b"after")
        second: dict[str, str] = {}
        analyzer._record_snapshot_file(second, "fixture", evidence)
        assert first != second, "input snapshot did not detect evidence tampering"
        for acquire, name in (
            (analyzer.acquire_publish_lock, "analysis"),
            (renderer_module.acquire_publish_lock, "review"),
        ):
            output = scratch / name
            lock = acquire(output)
            try:
                try:
                    acquire(output)
                except ValueError as exc:
                    assert "publish lock already exists" in str(exc)
                else:
                    raise AssertionError("concurrent publish lock unexpectedly succeeded")
            finally:
                lock.rmdir()
    if importlib.util.find_spec("numpy") is None:
        print("steel-module production core bootstrap: SKIP (NumPy unavailable)")
        return
    import numpy as np  # type: ignore[import-not-found]

    groups, blocks, located, task_ids = synthetic_groups()
    result = analyzer.analyze_event_groups(
        groups,
        blocks,
        located,
        task_ids,
        pilot={
            "ratio": 2.5,
            "ci95_low": 0.5,
            "ci95_high": 4.5,
            "relative_half_width": 0.8,
            "events_per_endpoint": 1000,
        },
        np=np,
    )
    assert result.primary["valid_resamples"] == 10_000
    assert len(result.loo_rows) == 32
    assert len(result.endpoint_rows) == 2
    assert len(result.distribution_rows) == 6
    assert result.primary["ratio"] == 3.0


def create_analysis_fixture(root: Path) -> tuple[Path, Path]:
    checkpoints = root / "campaigns" / "steel-module-production-checkpoints"
    analysis = checkpoints / "bc-only-s1-fixture-analysis"
    analysis.mkdir(parents=True)
    primary = {
        "schema_version": "steel-module-production-checkpoint-analysis-v1-primary-v1",
        "state_id": "BC-ONLY-S1",
        "metric": "observed-net-sipm-response",
        "ratio": 2.0,
        "ci95_low": 1.0,
        "ci95_high": 3.0,
        "valid_resamples": 10000,
        "relative_half_width": 0.5,
        "ci_excludes_unity": False,
        "max_leave_one_block_out_relative_shift": 0.08,
        "leave_one_block_out_evaluations": 32,
        "pilot_relative_half_width": 0.8,
        "interval_narrowing": True,
    }
    numeric = {
        "schema_version": "steel-module-production-checkpoint-analysis-v1-numeric-eligibility-v1",
        "state_id": "BC-ONLY-S1",
        "valid_evidence": True,
        "branch": "continue-eligible",
        "numeric_eligible_decisions": ["continue", "pause-review"],
        "automatic_decision": False,
        "automatic_submission": False,
        "human_tail_disposition_required": True,
    }
    config = {
        "schema_version": "steel-module-production-checkpoint-analysis-v1",
        "state_id": "BC-ONLY-S1",
        "checkpoint_hash": "1" * 64,
        "source_checkpoint_sha256s_sha256": "6" * 64,
        "source_checkpoint_files": {
            "event_audit.json": "8" * 64,
            "task_index.tsv": "5" * 64,
        },
        "program": {
            "program_id": "fixture-program",
            "program_hash": "9" * 64,
        },
        "task_set_hash": "4" * 64,
        "checkpoint_task_index_sha256": "5" * 64,
        "root_set_hash": "7" * 64,
        "task_count": 32,
        "event_count": 8000,
        "source_event_audit_sha256": "8" * 64,
        "source_finalization": {
            "directory": "/fixture/execution/finalized",
            "checksum_manifest_sha256": "a" * 64,
            "task_index_sha256": "5" * 64,
            "event_audit_sha256": "8" * 64,
            "seed_audit_sha256": "b" * 64,
            "selection_record_sha256": "c" * 64,
            "validation_report_sha256": "d" * 64,
            "finalization_hash": "e" * 64,
        },
        "input_identity_snapshot": {
            "algorithm": "sha256(canonical-json(logical-name-to-sha256))",
            "file_count": 100,
            "sha256": "f" * 64,
        },
        "pilot_baseline": {
            "ratio": 2.0,
            "ci95_low": 0.4,
            "ci95_high": 3.6,
            "relative_half_width": 0.8,
            "events_per_endpoint": 1000,
            "campaign_id": "fixture-pilot",
            "reconciliation": "passed",
        },
        "accepted_statistical_evidence": True,
        "simulation_commit": "2" * 40,
        "analysis_commit": "3" * 40,
        "analyzer_sha256": "4" * 64,
    }
    write_json(analysis / "primary_contrast.json", primary)
    write_json(analysis / "numeric_eligibility.json", numeric)
    write_json(analysis / "analysis_config.json", config)
    (analysis / "summary.md").write_text("# fixture\n", encoding="utf-8")
    endpoints = [
        {
            "state_id": "BC-ONLY-S1",
            "tile_thickness_mm": thickness,
            "sipm_layout": "back-center",
            "absorber_transverse_mm": 500,
            "blocks": 16,
            "events": 4000,
            "generated_optical_photons_per_neutron": 100 * thickness,
            "scintillation_photons_per_neutron": 90 * thickness,
            "observed_net_sipm_photons_per_neutron": thickness,
            "net_ci95_low": thickness * 0.8,
            "net_ci95_high": thickness * 1.2,
            "net_valid_resamples": 10000,
            "sipm_zero_events": 3200,
            "sipm_zero_fraction": 0.8,
            "sipm_positive_events": 800,
        }
        for thickness in (4, 24)
    ]
    write_csv(analysis / "endpoint_estimates.csv", list(endpoints[0]), endpoints)
    distribution_rows = []
    for thickness in (4, 24):
        for metric in ("generated", "scintillation", "sipm"):
            row = {field: 0 for field in analyzer.DISTRIBUTION_FIELDS}
            row.update(
                {
                    "state_id": "BC-ONLY-S1",
                    "tile_thickness_mm": thickness,
                    "metric": metric,
                    "events": 4000,
                    "positive_events": 800,
                    "zero_events": 3200,
                    "zero_fraction": 0.8,
                    "sum": 1000,
                    "mean": 0.25,
                    "rms": 1,
                    "population_stddev": 1,
                    "standard_error": 0.01,
                    "max": 100,
                    "top_1pct_event_count": 40,
                    "top_1pct_sum_fraction": 0.4,
                    "top_1pct_fraction_valid": True,
                    "top_5pct_event_count": 200,
                    "top_5pct_sum_fraction": 0.7,
                    "top_5pct_fraction_valid": True,
                    "maximum_logical_task_id": f"fixture-{thickness}",
                    "maximum_seed_block": 15,
                    "maximum_root_entry": 249,
                }
            )
            distribution_rows.append(row)
    write_csv(
        analysis / "distribution_diagnostics.csv",
        list(analyzer.DISTRIBUTION_FIELDS),
        distribution_rows,
    )
    pilot_distribution_rows = []
    for row in distribution_rows:
        pilot_row = dict(row)
        pilot_row["state_id"] = "sealed-pilot"
        pilot_row["events"] = 1000
        pilot_row["positive_events"] = 180
        pilot_row["zero_events"] = 820
        pilot_row["zero_fraction"] = 0.82
        pilot_row["top_1pct_event_count"] = 10
        pilot_row["top_1pct_sum_fraction"] = 0.35
        pilot_row["top_5pct_event_count"] = 50
        pilot_row["top_5pct_sum_fraction"] = 0.65
        pilot_row["max"] = 90
        pilot_row["maximum_logical_task_id"] = (
            f"pilot-fixture-{pilot_row['tile_thickness_mm']}"
        )
        pilot_distribution_rows.append(pilot_row)
    write_csv(
        analysis / "pilot_distribution_diagnostics.csv",
        list(analyzer.DISTRIBUTION_FIELDS),
        pilot_distribution_rows,
    )
    loo_rows = []
    for thickness in (4, 24):
        for block in range(16):
            loo_rows.append(
                {
                    "state_id": "BC-ONLY-S1",
                    "omitted_tile_thickness_mm": thickness,
                    "omitted_seed_block": block,
                    "omitted_logical_task_id": f"fixture-{thickness}-{block}",
                    "reference_events_remaining": 3750 if thickness == 4 else 4000,
                    "compared_events_remaining": 3750 if thickness == 24 else 4000,
                    "full_ratio": 2.0,
                    "leave_one_block_out_ratio": (
                        2.16 if thickness == 24 and block == 15 else 2.0 + block / 1000
                    ),
                    "absolute_relative_shift": (
                        0.08 if thickness == 24 and block == 15 else block / 2000
                    ),
                    "within_10pct": True,
                    "within_20pct": True,
                }
            )
    write_csv(analysis / "block_loo.csv", list(analyzer.LOO_FIELDS), loo_rows)
    trajectory = [
        {
            "sample_id": "sealed-pilot",
            "sample_role": "baseline",
            "events_per_endpoint": 1000,
            "ratio": 2,
            "ci95_low": 0,
            "ci95_high": 4,
            "relative_half_width": 0.8,
            "projected_relative_half_width": 0.8,
            "source_in_production_estimate": False,
        },
        {
            "sample_id": "BC-ONLY-S1",
            "sample_role": "production",
            "events_per_endpoint": 4000,
            "ratio": 2,
            "ci95_low": 1,
            "ci95_high": 3,
            "relative_half_width": 0.5,
            "projected_relative_half_width": 0.5,
            "source_in_production_estimate": True,
        },
    ]
    write_csv(
        analysis / "precision_trajectory.csv",
        list(analyzer.TRAJECTORY_FIELDS),
        trajectory,
    )
    write_recursive_checksums(analysis)
    review = analysis.with_name(f"{analysis.name}-review")
    return analysis, review


def manual_review_fixture(analysis: Path, review: Path) -> None:
    review.mkdir()
    primary = json.loads((analysis / "primary_contrast.json").read_text(encoding="utf-8"))
    numeric = json.loads((analysis / "numeric_eligibility.json").read_text(encoding="utf-8"))
    config = json.loads((analysis / "analysis_config.json").read_text(encoding="utf-8"))
    payload = renderer_module.review_payload(
        analysis,
        config,
        primary,
        numeric,
        read_csv(analysis / "endpoint_estimates.csv"),
        read_csv(analysis / "distribution_diagnostics.csv"),
        read_csv(analysis / "pilot_distribution_diagnostics.csv"),
        read_csv(analysis / "block_loo.csv"),
        read_csv(analysis / "precision_trajectory.csv"),
    )
    write_json(review / "review_data.json", payload)
    analysis_files = verify_checksum_manifest(
        analysis, required=renderer_module.REQUIRED_ANALYSIS
    )
    write_json(
        review / "review_provenance.json",
        {
            "schema_version": (
                "steel-module-production-checkpoint-review-v1-provenance-v1"
            ),
            "created_at_utc": "2026-07-18T00:00:00+00:00",
            "source_analysis_sha256s_sha256": sha256_file(
                analysis / "SHA256SUMS"
            ),
            "source_analysis_files": dict(sorted(analysis_files.items())),
            "renderer_sha256": sha256_file(
                Path(renderer_module.__file__).resolve()
            ),
            "python_version": sys.version.split()[0],
            "matplotlib_version": "fixture-no-render",
            "backend": "fixture-no-render",
            "network_resources": False,
            "write_controls": False,
            "automatic_submission": False,
        },
    )
    (review / "index.html").write_text(
        renderer_module.html_document(payload), encoding="utf-8"
    )
    (review / "review_report.pdf").write_bytes(b"%PDF-1.4\n% fixture\n")
    figures = review / "figures"
    figures.mkdir()
    for name in renderer_module.FIGURE_NAMES:
        (figures / f"{name}.png").write_bytes(b"fixture-png\n")
        (figures / f"{name}.pdf").write_bytes(b"%PDF-1.4\n% fixture\n")
    write_recursive_checksums(review)


def review_and_recorder_check(repo_root: Path, scratch: Path) -> None:
    analysis, review = create_analysis_fixture(scratch)
    renderer = repo_root / "hpc/osc/render_steel_module_production_review.py"
    if importlib.util.find_spec("matplotlib") is None:
        print("steel-module production review rendering: SKIP (matplotlib unavailable)")
        manual_review_fixture(analysis, review)
    else:
        run([sys.executable, str(renderer), "--analysis-dir", str(analysis)])
        verify_checksum_manifest(
            review,
            required=(
                "index.html",
                "review_report.pdf",
                "figures/precision_trajectory.png",
                "figures/block_loo_shifts.pdf",
                "figures/tail_diagnostics.png",
                "figures/endpoint_ratio.pdf",
            ),
        )
    html_text = (review / "index.html").read_text(encoding="utf-8")
    assert "Maximum leave-one-block-out shift (LOO)" in html_text
    assert "Sealed pilot versus new production tail" in html_text
    assert "pilot-fixture-4" in html_text
    assert "http://" not in html_text and "https://" not in html_text
    assert "<form" not in html_text
    rationale = scratch / "rationale.txt"
    rationale.write_text("Fixture reviewer found no material tail worsening.\n", encoding="utf-8")
    recorder_cli = repo_root / "hpc/osc/record_steel_module_progression.py"
    base = [
        sys.executable,
        str(recorder_cli),
        "--analysis-dir",
        str(analysis),
        "--review-dir",
        str(review),
        "--tail-disposition",
        "no-material-worsening",
        "--decision",
        "continue",
        "--reviewer",
        "fixture-reviewer",
        "--rationale-file",
        str(rationale),
    ]
    decision_root = scratch / "campaigns" / "steel-module-production-decisions"
    recorder.validate_inputs(analysis, review)

    original_block_loo = (analysis / "block_loo.csv").read_bytes()
    original_primary = (analysis / "primary_contrast.json").read_bytes()
    original_review_data = (review / "review_data.json").read_text(encoding="utf-8")
    original_review_provenance = (review / "review_provenance.json").read_text(
        encoding="utf-8"
    )
    tampered_loo = read_csv(analysis / "block_loo.csv")
    tampered_loo[-1]["absolute_relative_shift"] = "0.09"
    write_csv(analysis / "block_loo.csv", list(analyzer.LOO_FIELDS), tampered_loo)
    write_recursive_checksums(analysis)
    changed_analysis_hash = sha256_file(analysis / "SHA256SUMS")
    changed_analysis_files = verify_checksum_manifest(
        analysis, required=renderer_module.REQUIRED_ANALYSIS
    )
    for name in ("review_data.json", "review_provenance.json"):
        payload = json.loads((review / name).read_text(encoding="utf-8"))
        payload["source_analysis_sha256s_sha256"] = changed_analysis_hash
        if name == "review_provenance.json":
            payload["source_analysis_files"] = dict(
                sorted(changed_analysis_files.items())
            )
        write_json(review / name, payload)
    write_recursive_checksums(review)
    mismatch = run([*base, "--check-only"], expected=1)
    assert "LOO shift does not match" in mismatch.stderr

    (analysis / "block_loo.csv").write_bytes(original_block_loo)
    write_recursive_checksums(analysis)
    (review / "review_data.json").write_text(original_review_data, encoding="utf-8")
    (review / "review_provenance.json").write_text(
        original_review_provenance, encoding="utf-8"
    )
    write_recursive_checksums(review)

    tampered_primary = json.loads(
        (analysis / "primary_contrast.json").read_text(encoding="utf-8")
    )
    tampered_primary["interval_narrowing"] = False
    write_json(analysis / "primary_contrast.json", tampered_primary)
    write_recursive_checksums(analysis)
    changed_analysis_hash = sha256_file(analysis / "SHA256SUMS")
    changed_analysis_files = verify_checksum_manifest(
        analysis, required=renderer_module.REQUIRED_ANALYSIS
    )
    changed_review = json.loads(original_review_data)
    changed_review["source_analysis_sha256s_sha256"] = changed_analysis_hash
    changed_review["primary_contrast"] = tampered_primary
    write_json(review / "review_data.json", changed_review)
    changed_provenance = json.loads(original_review_provenance)
    changed_provenance["source_analysis_sha256s_sha256"] = changed_analysis_hash
    changed_provenance["source_analysis_files"] = dict(
        sorted(changed_analysis_files.items())
    )
    write_json(review / "review_provenance.json", changed_provenance)
    write_recursive_checksums(review)
    mismatch = run([*base, "--check-only"], expected=1)
    assert "interval narrowing does not reconcile" in mismatch.stderr
    (analysis / "primary_contrast.json").write_bytes(original_primary)
    write_recursive_checksums(analysis)
    (review / "review_data.json").write_text(original_review_data, encoding="utf-8")
    (review / "review_provenance.json").write_text(
        original_review_provenance, encoding="utf-8"
    )
    write_recursive_checksums(review)

    review_payload = json.loads((review / "review_data.json").read_text(encoding="utf-8"))
    review_payload["pilot_sipm_tail_diagnostics"][0]["max"] = "999999"
    write_json(review / "review_data.json", review_payload)
    write_recursive_checksums(review)
    mismatch = run([*base, "--check-only"], expected=1)
    assert "tail diagnostics do not reconcile" in mismatch.stderr, mismatch.stderr
    (review / "review_data.json").write_text(original_review_data, encoding="utf-8")
    write_recursive_checksums(review)

    missing_figure = review / "figures/endpoint_ratio.pdf"
    figure_bytes = missing_figure.read_bytes()
    missing_figure.unlink()
    write_recursive_checksums(review)
    mismatch = run([*base, "--check-only"], expected=1)
    assert "required files" in mismatch.stderr or "required" in mismatch.stderr
    missing_figure.write_bytes(figure_bytes)
    write_recursive_checksums(review)

    unsafe_provenance = json.loads(
        (review / "review_provenance.json").read_text(encoding="utf-8")
    )
    unsafe_provenance["network_resources"] = True
    write_json(review / "review_provenance.json", unsafe_provenance)
    write_recursive_checksums(review)
    mismatch = run([*base, "--check-only"], expected=1)
    assert "provenance is incomplete or unsafe" in mismatch.stderr
    (review / "review_provenance.json").write_text(
        original_review_provenance, encoding="utf-8"
    )
    write_recursive_checksums(review)

    run([*base, "--check-only"])
    assert not decision_root.exists(), "check-only progression unexpectedly wrote output"
    run([*base, "--record"])
    records = decision_root / "records"
    assert recorder.validate_chain(records)[0] == 1
    decision = json.loads(next(records.glob("*/decision.json")).read_text(encoding="utf-8"))
    assert decision["program_identity"]["program_id"] == "fixture-program"
    assert decision["task_set_hash"] == "4" * 64
    assert decision["checkpoint_task_index_sha256"] == "5" * 64
    assert decision["task_count"] == 32 and decision["event_count"] == 8000
    assert len(decision["tail_diagnostics"]["sealed_pilot_sipm_endpoints"]) == 2
    adverse = list(base)
    adverse[adverse.index("no-material-worsening")] = "material-worsening"
    run(adverse + ["--check-only"], expected=1)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    core_analysis_check()
    for path in (
        repo_root / "hpc/osc/analyze_steel_module_production_checkpoint.py",
        repo_root / "hpc/osc/render_steel_module_production_review.py",
        repo_root / "hpc/osc/record_steel_module_progression.py",
    ):
        text = path.read_text(encoding="utf-8")
        assert "sbatch" not in text and "scontrol" not in text and "sacct" not in text
    with tempfile.TemporaryDirectory(prefix="steel-module-phase2b-analysis-") as raw:
        scratch = Path(raw)
        phase2c_v6_finalizer_checkpoint_analyzer_e2e(
            scratch / "phase2c-v6-e2e"
        )
        review_and_recorder_check(repo_root, scratch)
    print(
        "steel-module production analysis/review/progression: PASS "
        "(v6 finalizer/checkpoint/analyzer synthetic E2E included)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
