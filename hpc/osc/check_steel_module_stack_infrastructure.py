#!/usr/bin/env python3
"""Deterministic infrastructure checks for steel-module-stack-v1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import analyze_steel_module_stack_campaign as analyzer
import finalize_steel_module_stack_campaign as stack_finalizer
import submit_realistic_neutron_campaign as ordinary_submitter
from realistic_neutron_campaign_lib import verify_checksum_manifest
from realistic_neutron_event_audit import EVENT_SCHEMA_FIELDS, SIPM_SENSOR_FIELDS
from steel_module_stack_campaign_lib import (
    FINALIZED_REQUIRED_FILES,
    STACK_LAYERS,
    TILE_THICKNESSES_MM,
    CampaignBundle,
    CampaignTask,
    load_campaign,
)
from steel_module_stack_event_audit import LAYER_FIELDS, TRANSFER_FIELDS, audit_stack_arrays
from summarize_steel_module_stack_benchmark import select_block_size, select_memory_gib


def run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None, success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if success and result.returncode:
        raise AssertionError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}")
    if not success and not result.returncode:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}")
    return result


def read_tasks(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def synthetic_event() -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, list[int]]]:
    nan = float("nan")
    scan: dict[str, list[float]] = {field: [0.0] for field in EVENT_SCHEMA_FIELDS}
    scan.update({field: [0.0] for field in SIPM_SENSOR_FIELDS})
    scan.update(
        {
            "event_id": [0], "shoot_x_mm": [0.0], "shoot_y_mm": [0.0], "shoot_z_mm": [221.5],
            "hit_valid": [1], "hit_x_mm": [0.0], "hit_y_mm": [0.0], "hit_z_mm": [178.0],
            "scint_centroid_valid": [1], "scint_centroid_x_mm": [0.0], "scint_centroid_y_mm": [0.0], "scint_centroid_z_mm": [150.0],
            "generated_optical_photons": [30], "scintillation_photons": [30], "cerenkov_photons": [0],
            "sipm_detected_photons": [3], "collection_efficiency": [0.1], "collection_efficiency_valid": [1],
            "primary_kinetic_energy_mev": [1000.0], "steel_edep_mev": [1.0],
            "primary_neutron_elastic_count": [1], "primary_neutron_elastic_flag": [1],
            "primary_neutron_inelastic_count": [0], "primary_neutron_inelastic_flag": [0],
            "primary_neutron_capture_count": [0], "primary_neutron_capture_flag": [0],
            "primary_neutron_any_interaction_flag": [1],
            "charged_tile_entry_count": [1], "charged_tile_entry_ke_mev": [2.0],
            "electron_tile_entry_count": [1], "electron_tile_entry_ke_mev": [2.0],
            "proton_tile_entry_count": [0], "proton_tile_entry_ke_mev": [0.0],
            "other_charged_tile_entry_count": [0], "other_charged_tile_entry_ke_mev": [0.0],
            "primary_neutron_tile_entry_valid": [1], "primary_neutron_tile_entry_x_mm": [0.0],
            "primary_neutron_tile_entry_y_mm": [0.0], "primary_neutron_tile_entry_z_mm": [178.0],
            "tile_edep_mev": [3.0], "electron_tile_edep_mev": [3.0],
            "proton_tile_edep_mev": [0.0], "other_charged_tile_edep_mev": [0.0], "neutral_tile_edep_mev": [0.0],
            "sipm_sensor_0_detected_photons": [1], "sipm_sensor_1_detected_photons": [0],
            "sipm_sensor_2_detected_photons": [2], "sipm_sensor_3_detected_photons": [0],
        }
    )
    layers: dict[str, list[float]] = {field: [] for field in LAYER_FIELDS}
    for layer in range(STACK_LAYERS):
        row: dict[str, float] = {field: 0.0 for field in LAYER_FIELDS}
        row.update({"event_id": 0, "layer": layer})
        for field in ("primary_neutron_tile_entry_x_mm", "primary_neutron_tile_entry_y_mm", "primary_neutron_tile_entry_z_mm"):
            row[field] = nan
        if layer == 0:
            row.update(
                {
                    "generated_optical_photons": 10, "scintillation_photons": 10,
                    "sensor_0_all_origin_detected_photons": 1,
                    "sensor_0_local_origin_detected_photons": 1,
                    "steel_edep_mev": 1.0, "primary_neutron_elastic_count": 1,
                    "primary_neutron_tile_entry_valid": 1,
                    "primary_neutron_tile_entry_x_mm": 0.0, "primary_neutron_tile_entry_y_mm": 0.0,
                    "primary_neutron_tile_entry_z_mm": 178.0,
                }
            )
        elif layer == 1:
            row.update(
                {
                    "generated_optical_photons": 20, "scintillation_photons": 20,
                    "sensor_0_all_origin_detected_photons": 2,
                    "sensor_0_local_origin_detected_photons": 1,
                    "charged_tile_entry_count": 1, "charged_tile_entry_ke_mev": 2.0,
                    "electron_tile_entry_count": 1, "electron_tile_entry_ke_mev": 2.0,
                    "tile_edep_mev": 3.0, "electron_tile_edep_mev": 3.0,
                }
            )
        for field in LAYER_FIELDS:
            layers[field].append(row[field])
    transfers: dict[str, list[int]] = {field: [] for field in TRANSFER_FIELDS}
    for origin, destination, sensor, count in ((0, 0, 0, 1), (1, 1, 0, 1), (0, 1, 0, 1)):
        values = (0, origin, destination, sensor, 2 * destination + sensor, count)
        for field, value in zip(TRANSFER_FIELDS, values):
            transfers[field].append(value)
    return scan, layers, transfers


def geometry_checks(repo: Path, scratch: Path) -> None:
    for thickness in TILE_THICKNESSES_MM:
        length = 10 * (40 + thickness)
        source = length / 2 + 1.5
        assert source < 500
        for layer in range(10):
            steel = length / 2 - layer * (40 + thickness) - 20
            tile = length / 2 - layer * (40 + thickness) - 40 - thickness / 2
            assert math.isclose(steel - 20, tile + thickness / 2)
            if layer < 9:
                next_steel = length / 2 - (layer + 1) * (40 + thickness) - 20
                assert math.isclose(tile - thickness / 2, next_steel + 20)
        assert [2 * layer + sensor for layer in range(10) for sensor in range(2)] == list(range(20))
    source = (repo / "test/OpNovice2/src/DetectorConstruction.cc").read_text(encoding="utf-8")
    for token in ("TankToUpstreamSteelSurface_", "TankToDownstreamSteelSurface_", "2 * layer", "ValidateStackConfiguration"):
        assert token in source
    tracking = (repo / "test/OpNovice2/src/TrackingAction.cc").read_text(encoding="utf-8")
    assert "SetOpticalOriginLayer" in tracking and "GetTileLayer" in tracking
    track_information = (repo / "test/OpNovice2/src/TrackInformation.cc").read_text(encoding="utf-8")
    assert "fOpticalOriginLayer = aTrackInfo->fOpticalOriginLayer" in track_information
    assert "fOpticalOriginLayer = -1" in track_information
    apptainer_runner = (repo / "hpc/osc/run_scan_apptainer.sh").read_text(encoding="utf-8")
    assert "record_steel_module_stack_task_result.py" in apptainer_runner
    run(
        [sys.executable, "test/OpNovice2/visualize_steel_module_stack_geometry.py", "--prepare-only", "--output-dir", str(scratch / "visuals")],
        cwd=repo,
    )
    configs = sorted((scratch / "visuals").glob("t*/run_config.json"))
    assert len(configs) == 6
    for config_path in configs:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        thickness = int(config["tank"]["size"].split()[2])
        assert config["stack"]["layers"] == 10
        assert config["stack"]["stack_length_mm"] == 10 * (40 + thickness)
        assert config["simulation"]["beam_z"] == f"{5 * (40 + thickness) + 1.5:g} mm"
        assert config["sipm"]["sensor_count"] == 20
    base_runner = [
        str(repo / "test/OpNovice2/run_sipm_cavity_scan.sh"), "full", "custom",
        "--study-preset", "steel-module-stack-v1", "--tile-thickness-mm", "4",
        "--x-min", "0", "--x-max", "0", "--y-min", "0", "--y-max", "0",
        "--step", "1", "--grid-unit", "mm", "--events", "1", "--dry-run",
    ]
    run([*base_runner, "--primary-energy", "2 GeV"], cwd=repo / "test/OpNovice2", success=False)
    run([*base_runner, "--sipm-layout", "edge-two"], cwd=repo / "test/OpNovice2", success=False)


def audit_checks() -> None:
    scan, layers, transfers = synthetic_event()
    report = audit_stack_arrays(scan, layers, transfers, expected_events=1, context="fixture")
    assert report["generated_optical_photons"] == 30
    assert report["local_origin_detected_photons"] == 2
    assert report["all_origin_detected_photons"] == 3
    assert report["cross_layer_detected_photons"] == 1
    unknown = deepcopy(transfers)
    unknown["origin_layer"][2] = -1
    try:
        audit_stack_arrays(scan, layers, unknown, expected_events=1)
    except ValueError as exc:
        assert "unknown origin" in str(exc)
    else:
        raise AssertionError("unknown-origin transfer was accepted")
    bad_copy = deepcopy(transfers)
    bad_copy["global_copy"][2] = 3
    try:
        audit_stack_arrays(scan, layers, bad_copy, expected_events=1)
    except ValueError as exc:
        assert "copy mapping" in str(exc)
    else:
        raise AssertionError("tampered copy mapping was accepted")
    missing = {field: values[:-1] for field, values in layers.items()}
    try:
        audit_stack_arrays(scan, missing, transfers, expected_events=1)
    except ValueError as exc:
        assert "row count" in str(exc) or "coverage" in str(exc)
    else:
        raise AssertionError("missing stack layer row was accepted")


def finalizer_checks(scratch: Path) -> None:
    scan, layers, transfers = synthetic_event()
    report = audit_stack_arrays(scan, layers, transfers, expected_events=1)
    campaign_dir = scratch / "finalizer-campaign"
    campaign_dir.mkdir()
    tasks = tuple(
        CampaignTask(
            task_index=index,
            logical_task_id=f"finalizer-t{thickness:02d}",
            stage="geometry-smoke",
            tile_thickness_mm=thickness,
            seed_block=0,
            events=1,
            seed1=80000 + index * 2,
            seed2=80001 + index * 2,
            configuration_hash=f"{index:064x}",
        )
        for index, thickness in enumerate(TILE_THICKNESSES_MM, start=1)
    )
    bundle = CampaignBundle(
        directory=campaign_dir,
        manifest={
            "campaign_id": "fixture-stack-finalizer", "plan_hash": "1" * 64,
            "git": {"commit": "2" * 40},
            "environment": {"accepted_statistical_evidence": False},
            "artifacts": {},
        },
        tasks=tasks,
        scan_args=tuple("fixture" for _ in tasks),
    )
    selected = []
    for task in tasks:
        artifacts = {
            key: {"path": f"fixture/{task.logical_task_id}/{key}", "sha256": "3" * 64}
            for key in ("run_config", "macro", "simulation_log", "root", "summary")
        }
        selected.append(
            SimpleNamespace(
                task=task,
                attempt={"attempt_id": "fixture-attempt", "slurm_job_id": "12345"},
                array_index=task.task_index,
                marker={"artifacts": artifacts},
                event_report=deepcopy(report),
            )
        )
    output = campaign_dir / "finalized"
    stack_finalizer.finalize_outputs(bundle, output, selected, [], bootstrap_seed=20260715, bootstrap_resamples=10000)
    verify_checksum_manifest(output, required=FINALIZED_REQUIRED_FILES)
    validation = json.loads((output / "validation_report.json").read_text(encoding="utf-8"))
    assert validation["valid"] is True and validation["selected_tasks"] == 6
    try:
        stack_finalizer.finalize_outputs(bundle, output, selected, [], bootstrap_seed=20260715, bootstrap_resamples=10000)
    except ValueError as exc:
        assert "overwrite" in str(exc)
    else:
        raise AssertionError("stack finalizer overwrote an existing output")


def campaign_checks(repo: Path, scratch: Path) -> None:
    registry = scratch / "excluded.json"
    registry.write_text(json.dumps({"excluded_seeds": [11, 12, 13, 14]}) + "\n", encoding="utf-8")
    cases = (("geometry-smoke", 6, 1), ("benchmark", 2, 25))
    benchmark_bundle = None
    for stage, tasks, events in cases:
        out = scratch / stage
        run([sys.executable, "hpc/osc/generate_steel_module_stack_campaign.py", "--out-dir", str(out), "--stage", stage, "--campaign-seed", "20260722", "--allow-dirty"], cwd=repo)
        bundle = load_campaign(out)
        if stage == "benchmark":
            benchmark_bundle = bundle
        assert len(bundle.tasks) == tasks and all(task.events == events for task in bundle.tasks)
        assert ordinary_submitter.campaign_scheduler_args(bundle) == [
            "--time=01:00:00", "--nodes=1", "--ntasks=1",
            "--cpus-per-task=1", "--mem=2G",
        ]
        if stage == "geometry-smoke":
            tampered = scratch / "tampered-source"
            shutil.copytree(out, tampered)
            manifest_path = tampered / "campaign.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_contract"]["kinetic_energy_mev"] = 999
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            try:
                load_campaign(tampered)
            except ValueError as exc:
                assert "source contract" in str(exc)
            else:
                raise AssertionError("tampered stack source contract was accepted")
    assert benchmark_bundle is not None
    scan, layers, transfers = synthetic_event()
    event_report = audit_stack_arrays(scan, layers, transfers, expected_events=1)
    benchmark_candidates = []
    fixture_root = benchmark_bundle.directory / "fixture-artifacts"
    fixture_root.mkdir()
    for task in benchmark_bundle.tasks:
        root_path = fixture_root / f"{task.logical_task_id}.root"
        root_path.write_bytes((task.logical_task_id + "\n").encode())
        root_sha = hashlib.sha256(root_path.read_bytes()).hexdigest()
        artifacts = {
            key: {"path": str(root_path.relative_to(benchmark_bundle.directory)), "sha256": root_sha}
            for key in ("run_config", "macro", "simulation_log", "root", "summary")
        }
        scaled_report = deepcopy(event_report)
        scaled_report["events"] = task.events
        benchmark_candidates.append(
            SimpleNamespace(
                task=task,
                attempt={"attempt_id": "fixture-attempt", "slurm_job_id": "91000"},
                array_index=task.task_index,
                marker={"artifacts": artifacts},
                event_report=scaled_report,
            )
        )
    stack_finalizer.finalize_outputs(
        benchmark_bundle, benchmark_bundle.directory / "finalized", benchmark_candidates, [],
        bootstrap_seed=20260715, bootstrap_resamples=10000,
    )
    sacct = scratch / "benchmark-sacct.psv"
    sacct.write_text(
        "91000_1|91001|COMPLETED|0:0|100||\n"
        "91000_1.batch|91001.batch|COMPLETED|0:0|100|500M|0\n"
        "91000_2|91002|COMPLETED|0:0|200||\n"
        "91000_2.batch|91002.batch|COMPLETED|0:0|200|1G|0\n",
        encoding="utf-8",
    )
    run(
        [sys.executable, "hpc/osc/summarize_steel_module_stack_benchmark.py", "--campaign-dir", str(benchmark_bundle.directory), "--sacct-input", str(sacct)],
        cwd=repo,
    )
    frozen_benchmark = json.loads((benchmark_bundle.directory / "finalized/benchmark/benchmark_report.json").read_text(encoding="utf-8"))
    assert frozen_benchmark["selected_events_per_task"] == 100
    assert frozen_benchmark["production_memory_gib"] == 2
    pilot = scratch / "pilot"
    benchmark_evidence = scratch / "benchmark-evidence"
    benchmark_evidence.mkdir()
    benchmark_report = benchmark_evidence / "benchmark_report.json"
    benchmark_report.write_text(
        json.dumps(
            {
                "schema_version": "steel-module-stack-benchmark-v1",
                "accepted_statistical_evidence": True,
                "pilot_permitted_for_human_review": True,
                "selected_events_per_task": 25,
                "production_memory_gib": 3,
                "git_commit": benchmark_bundle.git_commit,
                "environment_identity": benchmark_bundle.environment["identity_hash"],
                "image_sha256": None,
                "g4_data_manifest_sha256": None,
                "executable_sha256": None,
            }
        ) + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(benchmark_report.read_bytes()).hexdigest()
    (benchmark_evidence / "SHA256SUMS").write_text(f"{digest}  benchmark_report.json\n", encoding="utf-8")
    run([sys.executable, "hpc/osc/generate_steel_module_stack_campaign.py", "--out-dir", str(pilot), "--stage", "pilot", "--campaign-seed", "20260723", "--events", "25", "--benchmark-report", str(benchmark_report), "--excluded-seeds", str(registry), "--allow-dirty"], cwd=repo)
    pilot_bundle = load_campaign(pilot)
    assert len(pilot_bundle.tasks) == 24
    assert ordinary_submitter.campaign_scheduler_args(pilot_bundle)[-1] == "--mem=3G"
    assert {(task.tile_thickness_mm, task.seed_block) for task in pilot_bundle.tasks} == {(thickness, block) for thickness in TILE_THICKNESSES_MM for block in range(4)}
    sizing = scratch / "sizing.json"
    sizing.write_text(
        json.dumps(
            {
                "schema_version": "steel-module-stack-sizing-v1", "automatic_acceptance": False,
                "production_plan_permitted_for_human_review": True, "events_per_task": 25,
                "pilot_campaign_id": pilot_bundle.campaign_id,
                "pilot_plan_hash": pilot_bundle.plan_hash,
                "pilot_git_commit": pilot_bundle.git_commit,
                "pilot_finalized_sha256": "4" * 64,
                "scheduler_contract": {"nodes": 1, "ntasks": 1, "cpus_per_task": 1, "time_seconds": 3600, "memory_gib": 3},
                "thicknesses": [
                    {"tile_thickness_mm": thickness, "first_additional_seed_block": 4, "additional_blocks": 2}
                    for thickness in TILE_THICKNESSES_MM
                ],
            }
        ) + "\n",
        encoding="utf-8",
    )
    sizing_digest = hashlib.sha256(sizing.read_bytes()).hexdigest()
    (scratch / "SHA256SUMS").write_text(f"{sizing_digest}  sizing.json\n", encoding="utf-8")
    production = scratch / "production"
    run([sys.executable, "hpc/osc/generate_steel_module_stack_campaign.py", "--out-dir", str(production), "--stage", "production", "--campaign-seed", "20260724", "--sizing-plan", str(sizing), "--excluded-seeds", str(pilot / "tasks.tsv"), "--allow-dirty"], cwd=repo)
    production_bundle = load_campaign(production)
    assert len(production_bundle.tasks) == 12
    assert all(task.seed_block in (4, 5) for task in production_bundle.tasks)
    pilot_seeds = {seed for task in pilot_bundle.tasks for seed in (task.seed1, task.seed2)}
    production_seeds = {seed for task in production_bundle.tasks for seed in (task.seed1, task.seed2)}
    assert pilot_seeds.isdisjoint(production_seeds)
    run([sys.executable, "hpc/osc/generate_steel_module_stack_campaign.py", "--out-dir", str(production), "--stage", "production", "--campaign-seed", "20260724", "--sizing-plan", str(sizing), "--excluded-seeds", str(pilot / "tasks.tsv"), "--allow-dirty"], cwd=repo, success=False)
    assert select_block_size(4.0) == 250
    assert select_block_size(10.0) == 100
    assert select_block_size(200.0) is None
    assert select_memory_gib(500 * 1024**2) == 2
    assert select_memory_gib(2 * 1024**3) == 3


def fake_analysis(scratch: Path) -> tuple[dict[str, object], object]:
    tasks: list[analyzer.TaskData] = []
    identities = []
    for thickness in TILE_THICKNESSES_MM:
        for block in range(4):
            events = 25
            generated = np.zeros((events, 10), dtype=float)
            local = np.zeros((events, 10, 2), dtype=float)
            all_origin = np.zeros((events, 10, 2), dtype=float)
            transfers = np.zeros((10, 10, 2), dtype=np.int64)
            for event in range(events):
                for layer in range(10):
                    if (event + block + layer) % 5 == 0:
                        value = float((thickness + layer + block + 4) * 10)
                        if event == 24 and block == 0 and layer == 0:
                            value *= 12
                        generated[event, layer] = value
                        for sensor in range(2):
                            count = max(1, int(value * (0.008 + 0.002 * sensor)))
                            local[event, layer, sensor] = count
                            all_origin[event, layer, sensor] += count
                            transfers[layer, layer, sensor] += count
                        if layer < 9:
                            all_origin[event, layer + 1, 0] += 1
                            transfers[layer, layer + 1, 0] += 1
            tasks.append(
                analyzer.TaskData(
                    logical_task_id=f"fixture-t{thickness:02d}-b{block:03d}", stage="pilot", thickness=thickness,
                    block=block, events=events, generated=generated, scintillation=generated.copy(),
                    cerenkov=np.zeros_like(generated), local=local, all_origin=all_origin,
                    steel_edep=generated * 0.02, tile_edep=generated * 0.01,
                    charged_entries=(generated > 0).astype(float), transfer_totals=transfers,
                    neutron_elastic=(generated > 0).astype(float),
                    neutron_inelastic=np.zeros_like(generated),
                    neutron_capture=np.zeros_like(generated),
                    primary_tile_entry=(generated > 0).astype(float),
                    root_path=f"fixture-{thickness}-{block}.root", root_sha256="0" * 64,
                )
            )
            identities.append(SimpleNamespace(seed1=100000 + thickness * 100 + block * 2, seed2=100001 + thickness * 100 + block * 2))
    pilot_directory = scratch / "synthetic-pilot"
    (pilot_directory / "finalized").mkdir(parents=True)
    (pilot_directory / "finalized" / "SHA256SUMS").write_text("fixture\n", encoding="utf-8")
    bundle = SimpleNamespace(
        tasks=identities,
        manifest={"events_per_task": 25, "scheduler_contract": {"nodes": 1, "ntasks": 1, "cpus_per_task": 1, "time_seconds": 3600, "memory_gib": 3}},
        environment={"accepted_statistical_evidence": True},
        campaign_id="fixture-stack-pilot", plan_hash="1" * 64, git_commit="2" * 40,
        directory=pilot_directory,
    )
    result = analyzer.analyze(bundle, tasks, None, [])
    return result, bundle


def analysis_checks(repo: Path, scratch: Path) -> None:
    features = np.arange(40, dtype=float).reshape(20, 2) + 1
    assert np.array_equal(analyzer.bootstrap_feature_sums(features, 4), analyzer.bootstrap_feature_sums(features, 4))
    result, bundle = fake_analysis(scratch)
    assert len(result["thickness_rows"]) == 6
    assert len(result["layer_rows"]) == 120
    assert len(result["profile_rows"]) == 60
    assert len(result["transfer_rows"]) == 1200
    assert len(result["loo_rows"]) == 48
    sizing = result["sizing_plan"]
    assert sizing is not None and len(sizing["thicknesses"]) == 6
    for row in sizing["thicknesses"]:
        assert int(row["target_events"]) % 25 == 0
        for metric in row["metrics"]:
            h = metric["relative_half_width"]
            target = metric["target_events"]
            assert h is not None and target is not None
            raw = 100 * (h / 0.10) ** 2
            expected = math.ceil(max(100, 1.25 * raw) / 25) * 25
            if h <= 0.10 and metric["maximum_loo_shift"] <= 0.10:
                expected = 100
            assert target == expected
    core = scratch / "analysis"
    core.mkdir()
    analyzer.write_csv(core / "thickness_summary.csv", result["thickness_rows"])
    analyzer.write_csv(core / "layer_sensor_estimates.csv", result["layer_rows"])
    analyzer.write_csv(core / "longitudinal_profiles.csv", result["profile_rows"])
    analyzer.write_csv(core / "transfer_matrix.csv", result["transfer_rows"])
    analyzer.write_csv(core / "distribution_diagnostics.csv", result["distribution_rows"])
    analyzer.write_csv(core / "loo_diagnostics.csv", result["loo_rows"])
    (core / "summary.md").write_text("# fixture\n", encoding="utf-8")
    (core / "analysis_config.json").write_text(json.dumps({"schema_version": "steel-module-stack-analysis-v1", "accepted_statistical_evidence": True, "campaign_id": bundle.campaign_id, "configuration_count": 6, "finalized_additive_totals_reconciliation": "passed", "unknown_origin_detection_audit": "passed_zero"}) + "\n", encoding="utf-8")
    analyzer.write_checksums(core)
    env = os.environ.copy()
    env["MPLCONFIGDIR"] = str(scratch / "mpl")
    run([sys.executable, "hpc/osc/plot_steel_module_stack_analysis.py", "--analysis-dir", str(core), "--output-dir", str(scratch / "figures")], cwd=repo, env=env)
    figures = scratch / "figures"
    assert len(list(figures.glob("*.png"))) == 6 and len(list(figures.glob("*.pdf"))) == 6
    assert all(path.stat().st_size > 0 for path in figures.glob("*.*"))
    with (core / "thickness_summary.csv").open("a", encoding="utf-8") as stream:
        stream.write("tamper\n")
    run([sys.executable, "hpc/osc/plot_steel_module_stack_analysis.py", "--analysis-dir", str(core), "--output-dir", str(scratch / "tampered-figures")], cwd=repo, env=env, success=False)


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-stack-check-") as directory:
        scratch = Path(directory)
        geometry_checks(repo, scratch)
        audit_checks()
        finalizer_checks(scratch)
        campaign_checks(repo, scratch)
        analysis_checks(repo, scratch)
    print("steel-module stack: PASS (10-layer geometry, causal transfer/finalizer audit, 6/2/24/production shapes, benchmark-bound resources, sizing, bootstrap, plots, tamper rejection)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
