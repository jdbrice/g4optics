#!/usr/bin/env python3
"""Cheap deterministic checks for the realistic-neutron infrastructure."""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from realistic_neutron_campaign_lib import (
    ATTEMPT_FIELDS,
    environment_identity,
    sha256_file,
)
from realistic_neutron_event_audit import (
    EVENT_SCHEMA_FIELDS,
    SIPM_SENSOR_FIELDS,
    audit_event_arrays,
)
from analyze_realistic_neutron_campaign import production_statistics_recommendation
from run_realistic_neutron_electron_regression import (
    FLOAT_FIELDS as FLOAT_FIELDS_FOR_REGRESSION,
    INTEGER_FIELDS as INTEGER_FIELDS_FOR_REGRESSION,
    compare_summaries,
)


EXPECTED_SCAN_COLUMNS = [
    "event_id",
    "shoot_x_mm",
    "shoot_y_mm",
    "shoot_z_mm",
    "hit_valid",
    "hit_x_mm",
    "hit_y_mm",
    "hit_z_mm",
    "scint_centroid_valid",
    "scint_centroid_x_mm",
    "scint_centroid_y_mm",
    "scint_centroid_z_mm",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "collection_efficiency",
    "primary_kinetic_energy_mev",
    "collection_efficiency_valid",
    "cerenkov_photons",
    "steel_edep_mev",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "primary_neutron_elastic_flag",
    "primary_neutron_inelastic_flag",
    "primary_neutron_capture_flag",
    "primary_neutron_any_interaction_flag",
    "charged_tile_entry_count",
    "charged_tile_entry_ke_mev",
    "electron_tile_entry_count",
    "electron_tile_entry_ke_mev",
    "proton_tile_entry_count",
    "proton_tile_entry_ke_mev",
    "other_charged_tile_entry_count",
    "other_charged_tile_entry_ke_mev",
    "primary_neutron_tile_entry_valid",
    "primary_neutron_tile_entry_x_mm",
    "primary_neutron_tile_entry_y_mm",
    "primary_neutron_tile_entry_z_mm",
    "tile_edep_mev",
    "electron_tile_edep_mev",
    "proton_tile_edep_mev",
    "other_charged_tile_edep_mev",
    "neutral_tile_edep_mev",
    "sipm_sensor_0_detected_photons",
    "sipm_sensor_1_detected_photons",
    "sipm_sensor_2_detected_photons",
    "sipm_sensor_3_detected_photons",
]


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if expect_success and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {shlex.join(args)}\n{result.stdout}"
        )
    if not expect_success and result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {shlex.join(args)}")
    return result


def read_tasks(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def generate_campaign(
    *,
    repo_root: Path,
    out_dir: Path,
    stage: str,
    extra: list[str] | None = None,
) -> dict[str, object]:
    args = [
        sys.executable,
        "hpc/osc/generate_realistic_neutron_campaign.py",
        "--out-dir",
        str(out_dir),
        "--stage",
        stage,
        "--campaign-seed",
        "20260714",
        "--allow-dirty",
    ]
    if extra:
        args.extend(extra)
    run(args, cwd=repo_root)
    return json.loads((out_dir / "campaign.json").read_text(encoding="utf-8"))


def validate_event_schema(opnovice_dir: Path) -> None:
    histo_source = (opnovice_dir / "src/HistoManager.cc").read_text(encoding="utf-8")
    start = histo_source.index('CreateNtuple("scan"')
    end = histo_source.index("FinishNtuple();", start)
    scan_booking = histo_source[start:end]
    columns = re.findall(r'CreateNtuple[IDS]Column\("([^"]+)"\)', scan_booking)
    assert columns == EXPECTED_SCAN_COLUMNS, (columns, EXPECTED_SCAN_COLUMNS)
    legacy_count = len(EVENT_SCHEMA_FIELDS)
    assert columns[:legacy_count] == list(EVENT_SCHEMA_FIELDS)
    assert columns[legacy_count:] == list(SIPM_SENSOR_FIELDS)

    event_source = (opnovice_dir / "src/EventAction.cc").read_text(encoding="utf-8")
    filled_ids = [
        int(value)
        for value in re.findall(r"FillNtuple[IDS]Column\(\s*(\d+)", event_source)
    ]
    assert sorted(filled_ids) == list(range(len(EXPECTED_SCAN_COLUMNS))), filled_ids


def validate_event_causal_audit() -> None:
    nan = float("nan")
    row = {
        "event_id": 0,
        "shoot_x_mm": 0.0,
        "shoot_y_mm": 0.0,
        "shoot_z_mm": 43.5,
        "hit_valid": 0,
        "hit_x_mm": nan,
        "hit_y_mm": nan,
        "hit_z_mm": nan,
        "scint_centroid_valid": 1,
        "scint_centroid_x_mm": 0.1,
        "scint_centroid_y_mm": 0.2,
        "scint_centroid_z_mm": 0.3,
        "generated_optical_photons": 10,
        "scintillation_photons": 10,
        "sipm_detected_photons": 2,
        "collection_efficiency": 0.2,
        "primary_kinetic_energy_mev": 432.58,
        "collection_efficiency_valid": 1,
        "cerenkov_photons": 0,
        "steel_edep_mev": 1.5,
        "primary_neutron_elastic_count": 1,
        "primary_neutron_inelastic_count": 0,
        "primary_neutron_capture_count": 0,
        "primary_neutron_elastic_flag": 1,
        "primary_neutron_inelastic_flag": 0,
        "primary_neutron_capture_flag": 0,
        "primary_neutron_any_interaction_flag": 1,
        "charged_tile_entry_count": 3,
        "charged_tile_entry_ke_mev": 6.0,
        "electron_tile_entry_count": 1,
        "electron_tile_entry_ke_mev": 1.0,
        "proton_tile_entry_count": 1,
        "proton_tile_entry_ke_mev": 2.0,
        "other_charged_tile_entry_count": 1,
        "other_charged_tile_entry_ke_mev": 3.0,
        "primary_neutron_tile_entry_valid": 0,
        "primary_neutron_tile_entry_x_mm": nan,
        "primary_neutron_tile_entry_y_mm": nan,
        "primary_neutron_tile_entry_z_mm": nan,
        "tile_edep_mev": 1.0,
        "electron_tile_edep_mev": 0.2,
        "proton_tile_edep_mev": 0.3,
        "other_charged_tile_edep_mev": 0.4,
        "neutral_tile_edep_mev": 0.1,
    }
    legacy_arrays = {field: [row[field]] for field in EVENT_SCHEMA_FIELDS}
    report = audit_event_arrays(
        legacy_arrays, expected_events=1, context="legacy-fixture"
    )
    assert report["events"] == 1
    assert report["primary_neutron_interaction_events"] == 1

    arrays = {
        **legacy_arrays,
        "sipm_sensor_0_detected_photons": [1],
        "sipm_sensor_1_detected_photons": [0],
        "sipm_sensor_2_detected_photons": [1],
        "sipm_sensor_3_detected_photons": [0],
    }
    report = audit_event_arrays(arrays, expected_events=1, context="sensor-fixture")
    assert report["sipm_sensor_0_detected_photons"] == 1
    assert report["sipm_sensor_2_detected_photons"] == 1

    invalid = {field: list(values) for field, values in arrays.items()}
    invalid["primary_neutron_any_interaction_flag"][0] = 0
    try:
        audit_event_arrays(invalid, expected_events=1, context="invalid-fixture")
    except ValueError as exc:
        assert "any_interaction_flag" in str(exc)
    else:
        raise AssertionError("causal audit accepted an invalid any-interaction flag")

    invalid_sensor_sum = {field: list(values) for field, values in arrays.items()}
    invalid_sensor_sum["sipm_sensor_3_detected_photons"][0] = 1
    try:
        audit_event_arrays(
            invalid_sensor_sum,
            expected_events=1,
            context="invalid-sensor-fixture",
        )
    except ValueError as exc:
        assert "four sensor counts" in str(exc)
    else:
        raise AssertionError("causal audit accepted an invalid per-SiPM sum")

    recommendation = production_statistics_recommendation(
        [
            {
                "absorber_transverse_mm": 500,
                "x_mm": 0,
                "y_mm": 0,
                "events_4mm": 1000,
                "events_16mm": 1000,
                "production_ratio_16_over_4": 1.2,
                "production_ci95_low": 1.1,
                "production_ci95_high": 1.3,
                "collection_ratio_16_over_4": 0.8,
                "collection_ci95_low": 0.74,
                "collection_ci95_high": 0.86,
                "net_ratio_16_over_4": 0.96,
                "net_ci95_low": 0.88,
                "net_ci95_high": 1.04,
            }
        ],
        stage="convergence-pilot",
        block_events=250,
    )
    assert recommendation["status"] == "recommendation_requires_review"
    assert int(recommendation["recommended_total_events_per_thickness"]) % 250 == 0
    assert int(recommendation["recommended_blocks_per_thickness"]) >= 4

    baseline = {field: "1" for field in INTEGER_FIELDS_FOR_REGRESSION}
    baseline.update({field: "1.0" for field in FLOAT_FIELDS_FOR_REGRESSION})
    candidate = dict(baseline)
    comparisons = compare_summaries(
        baseline, candidate, rel_tol=1e-12, abs_tol=1e-12
    )
    assert all(item["passed"] is True for item in comparisons)
    candidate["generated_optical_photons"] = "2"
    comparisons = compare_summaries(
        baseline, candidate, rel_tol=1e-12, abs_tol=1e-12
    )
    assert any(
        item["field"] == "generated_optical_photons"
        and item["passed"] is False
        for item in comparisons
    )


def validate_runner_campaign(repo_root: Path, temp_root: Path) -> None:
    opnovice_dir = repo_root / "test/OpNovice2"
    campaign_dir = temp_root / "geometry-smoke"
    manifest = generate_campaign(
        repo_root=repo_root, out_dir=campaign_dir, stage="geometry-smoke"
    )
    assert manifest["task_count"] == 6
    assert manifest["environment"]["identity_hash"] == environment_identity(
        manifest["environment"]
    )

    repeat_dir = temp_root / "geometry-smoke-repeat"
    repeat_manifest = generate_campaign(
        repo_root=repo_root, out_dir=repeat_dir, stage="geometry-smoke"
    )
    assert repeat_manifest["campaign_id"] == manifest["campaign_id"]
    assert (repeat_dir / "tasks.tsv").read_bytes() == (campaign_dir / "tasks.tsv").read_bytes()

    plan_lines = [
        line
        for line in (campaign_dir / "scan_args.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(plan_lines) == 6

    runner_root = temp_root / "runner"
    env = os.environ.copy()
    env.update(
        {
            "SCAN_RUNS_DIR": str(runner_root / "runs"),
            "LATEST_RUN_LINK": str(runner_root / "latest"),
            "LATEST_POINTS_CSV": str(runner_root / "points.csv"),
            "LATEST_RUN_CONFIG": str(runner_root / "run_config.json"),
            "LATEST_EFFICIENCY_MAP": str(runner_root / "efficiency_map.csv"),
        }
    )
    for line in plan_lines:
        args = ["./run_sipm_cavity_scan.sh", *shlex.split(line), "--dry-run"]
        run(args, cwd=opnovice_dir, env=env)

    configs = sorted((runner_root / "runs").glob("*/run_config.json"))
    assert len(configs) == 6
    expected_geometries = {
        (4, 200),
        (4, 300),
        (4, 500),
        (16, 200),
        (16, 300),
        (16, 500),
    }
    actual_geometries: set[tuple[int, int]] = set()
    seen_seeds: set[int] = set()
    for config_path in configs:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["schema_version"] == "opnovice2-run-config-v3"
        assert config["event_schema_version"] == "opnovice2-scan-event-v2"
        assert config["study_preset"] == "realistic-neutron-v1"
        assert config["simulation"]["primary_particle"] == "neutron"
        assert config["simulation"]["derived_gps_kinetic_energy_mev"] == 432.58
        assert config["beam"]["divergence_mrad"] == 55
        tile = int(config["tank"]["size"].split()[2])
        absorber = int(config["absorber"]["full_size_mm"][0])
        expected_source_z = 43.5 if tile == 4 else 49.5
        assert config["simulation"]["beam_z"] == f"{expected_source_z:g} mm"
        assert config["grid"]["point_count"] == 1
        assert config["campaign"]["configuration_hash"]
        seed1 = config["random"]["seed1"]
        seed2 = config["random"]["seed2"]
        assert seed1 != seed2
        assert seed1 not in seen_seeds and seed2 not in seen_seeds
        seen_seeds.update((seed1, seed2))
        actual_geometries.add((tile, absorber))

        macro_path = next(config_path.parent.glob("macros/*.mac"))
        macro = macro_path.read_text(encoding="utf-8")
        assert "/opnovice2/absorber/enabled true" in macro
        assert f"/opnovice2/absorber/size {absorber} {absorber} 40 mm" in macro
        assert "/gps/particle neutron" in macro
        assert "/gps/energy 432.58 MeV" in macro
        assert "/gps/ang/sigma_x 55 mrad" in macro
        assert f"/random/setSeeds {seed1} {seed2}" in macro
    assert actual_geometries == expected_geometries
    assert len(seen_seeds) == 12

    locked = run(
        [
            "./run_sipm_cavity_scan.sh",
            *shlex.split(plan_lines[0]),
            "--beam-z",
            "43.5",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=env,
        expect_success=False,
    )
    assert "locks these options: --beam-z" in locked.stdout

    multi_point = run(
        [
            "./run_sipm_cavity_scan.sh",
            *shlex.split(plan_lines[0]),
            "--x-max",
            "5",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=env,
        expect_success=False,
    )
    assert "requires exactly one scan point per invocation" in multi_point.stdout

    legacy_root = temp_root / "legacy-runner"
    legacy_env = env.copy()
    legacy_env.update(
        {
            "SCAN_RUNS_DIR": str(legacy_root / "runs"),
            "LATEST_RUN_LINK": str(legacy_root / "latest"),
            "LATEST_POINTS_CSV": str(legacy_root / "points.csv"),
            "LATEST_RUN_CONFIG": str(legacy_root / "run_config.json"),
            "LATEST_EFFICIENCY_MAP": str(legacy_root / "efficiency_map.csv"),
        }
    )
    run(
        [
            "./run_sipm_cavity_scan.sh",
            "full",
            "custom",
            "--events",
            "1",
            "--source-mode",
            "gps",
            "--tank-size",
            "50 50 4 mm",
            "--x-min",
            "0",
            "--x-max",
            "0",
            "--y-min",
            "0",
            "--y-max",
            "0",
            "--step",
            "1",
            "--grid-unit",
            "mm",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=legacy_env,
    )
    legacy_config_path = next((legacy_root / "runs").glob("*/run_config.json"))
    legacy_config = json.loads(legacy_config_path.read_text(encoding="utf-8"))
    assert legacy_config["study_preset"] is None
    assert legacy_config["absorber"]["enabled"] is False
    legacy_macro_path = next(legacy_config_path.parent.glob("macros/*.mac"))
    legacy_macro = legacy_macro_path.read_text(encoding="utf-8")
    assert "/opnovice2/absorber/enabled" not in legacy_macro
    assert "/gps/particle e-" in legacy_macro


def validate_visualization_tool(repo_root: Path, temp_root: Path) -> None:
    output_dir = temp_root / "geometry-visual"
    run(
        [
            sys.executable,
            "test/OpNovice2/visualize_realistic_neutron_geometry.py",
            "--tile-thickness-mm",
            "16",
            "--absorber-transverse-mm",
            "500",
            "--output-dir",
            str(output_dir),
            "--prepare-only",
        ],
        cwd=repo_root,
    )
    macro = (output_dir / "realistic_neutron_geometry_visual.mac").read_text(
        encoding="utf-8"
    )
    assert "/opnovice2/tank/size 50 50 16 mm" in macro
    assert "/opnovice2/absorber/size 500 500 40 mm" in macro
    assert "/opnovice2/sipm/face -Z" in macro
    assert "/gps/direction 0 0 -1" in macro
    assert "/vis/geometry/set/forceWireframe SteelAbsorber 0 true" in macro
    assert "/run/beamOn 0" in macro


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def regression_fixture_executable(*, generated: int) -> str:
    fields = (*INTEGER_FIELDS_FOR_REGRESSION, *FLOAT_FIELDS_FOR_REGRESSION)
    values = [
        str(generated) if field == "generated_optical_photons" else "1"
        for field in INTEGER_FIELDS_FOR_REGRESSION
    ] + ["1.0" for _ in FLOAT_FIELDS_FOR_REGRESSION]
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'base="${1%.mac}"\n'
        f"printf '%s\\n' '{','.join(fields)}' > \"${{base}}_summary.csv\"\n"
        f"printf '%s\\n' '{','.join(values)}' >> \"${{base}}_summary.csv\"\n"
        "printf '%s\\n' fixture-root > \"${base}.root\"\n"
    )


def validate_electron_regression_orchestration(
    repo_root: Path, temp_root: Path
) -> None:
    baseline = temp_root / "regression-baseline"
    candidate = temp_root / "regression-candidate"
    write_executable(baseline, regression_fixture_executable(generated=1))
    write_executable(candidate, regression_fixture_executable(generated=1))
    output_dir = temp_root / "electron-regression-pass"
    run(
        [
            sys.executable,
            "hpc/osc/run_realistic_neutron_electron_regression.py",
            "--baseline-executable",
            str(baseline),
            "--candidate-executable",
            str(candidate),
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
    )
    report = json.loads(
        (output_dir / "regression_report.json").read_text(encoding="utf-8")
    )
    assert report["valid"] is True
    assert (output_dir / "baseline.root").is_file()
    assert (output_dir / "candidate.root").is_file()
    baseline_macro = (output_dir / "baseline.mac").read_text(encoding="utf-8")
    candidate_macro = (output_dir / "candidate.mac").read_text(encoding="utf-8")
    assert "/opnovice2/sipm/layout" not in baseline_macro
    assert "/opnovice2/sipm/layout single" in candidate_macro

    changed = temp_root / "regression-candidate-changed"
    write_executable(changed, regression_fixture_executable(generated=2))
    failed_dir = temp_root / "electron-regression-fail"
    run(
        [
            sys.executable,
            "hpc/osc/run_realistic_neutron_electron_regression.py",
            "--baseline-executable",
            str(baseline),
            "--candidate-executable",
            str(changed),
            "--output-dir",
            str(failed_dir),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    failed_report = json.loads(
        (failed_dir / "regression_report.json").read_text(encoding="utf-8")
    )
    assert failed_report["valid"] is False
    assert failed_report["failed_fields"] == ["generated_optical_photons"]

    no_root = temp_root / "regression-no-root"
    write_executable(no_root, "#!/usr/bin/env bash\nset -euo pipefail\n")
    missing_root_dir = temp_root / "electron-regression-no-root"
    run(
        [
            sys.executable,
            "hpc/osc/run_realistic_neutron_electron_regression.py",
            "--baseline-executable",
            str(no_root),
            "--candidate-executable",
            str(candidate),
            "--output-dir",
            str(missing_root_dir),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    preserved_failures = list(
        temp_root.glob("electron-regression-no-root.failed-*")
    )
    assert len(preserved_failures) == 1
    assert (preserved_failures[0] / "baseline.log").is_file()
    assert not missing_root_dir.exists()


def promote_campaign_to_production(
    campaign_dir: Path, artifact_dir: Path, *, git_commit: str
) -> dict[str, object]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    image = artifact_dir / "geant4-11.4.2.sif"
    data_manifest = artifact_dir / "g4-data-manifest.json"
    executable = artifact_dir / "OpNovice2"
    image.write_bytes(b"fixture apptainer image\n")
    data_manifest.write_text('{"fixture": true}\n', encoding="utf-8")
    write_executable(executable, "#!/usr/bin/env sh\nexit 0\n")

    manifest_path = campaign_dir / "campaign.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["git"] = {
        "commit": git_commit,
        "branch": "fixture",
        "dirty": False,
        "dirty_paths": [],
    }
    environment = {
        "mode": "osc-production",
        "geant4_version": "11.4.2",
        "accepted_statistical_evidence": True,
        "image": {"path": str(image.resolve()), "sha256": sha256_file(image)},
        "g4_data_manifest": {
            "path": str(data_manifest.resolve()),
            "sha256": sha256_file(data_manifest),
        },
        "build_artifact": {
            "path": str(executable.resolve()),
            "sha256": sha256_file(executable),
        },
    }
    environment["identity_hash"] = environment_identity(environment)
    manifest["environment"] = environment
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_fake_summary(path: Path, task: dict[str, str]) -> None:
    tile = int(task["tile_thickness_mm"])
    events = int(task["events"])
    if tile == 4:
        generated_per_event, scintillation_per_event, sipm_per_event = 100, 40, 10
    else:
        generated_per_event, scintillation_per_event, sipm_per_event = 200, 120, 15
    generated = generated_per_event * events
    scintillation = scintillation_per_event * events
    sipm = sipm_per_event * events
    row = {
        "events": events,
        "committed_events": events,
        "generated_optical_photons": generated,
        "scintillation_photons": scintillation,
        "sipm_detected_photons": sipm,
        "cerenkov_photons": 0,
        "steel_edep_sum_mev": float(events),
        "tile_edep_sum_mev": 2.0 * events,
        "primary_neutron_interaction_events": events,
        "primary_neutron_elastic_count": events,
        "primary_neutron_inelastic_count": 0,
        "primary_neutron_capture_count": 0,
        "charged_tile_entry_events": events,
        "charged_tile_entry_count": events,
        "generated_optical_zero_events": 0,
        "scintillation_zero_events": 0,
        "sipm_detected_zero_events": 0,
        "generated_optical_mean": generated_per_event,
        "generated_optical_rms": 1.0,
        "generated_optical_zero_fraction": 0.0,
        "scintillation_mean": scintillation_per_event,
        "scintillation_rms": 1.0,
        "scintillation_zero_fraction": 0.0,
        "sipm_detected_mean": sipm_per_event,
        "sipm_detected_rms": 1.0,
        "sipm_detected_zero_fraction": 0.0,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def prepare_fake_results(
    repo_root: Path,
    campaign_dir: Path,
    manifest: dict[str, object],
) -> None:
    opnovice_dir = repo_root / "test/OpNovice2"
    attempt_id = "fixture-attempt"
    attempt_dir = campaign_dir / "attempts" / attempt_id
    attempt_dir.mkdir(parents=True)
    tasks = read_tasks(campaign_dir / "tasks.tsv")
    plan_lines = [
        line
        for line in (campaign_dir / "scan_args.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    environment = manifest["environment"]
    assert isinstance(environment, dict)
    git = manifest["git"]
    assert isinstance(git, dict)

    mapped_rows: list[dict[str, object]] = []
    for array_index, (task, line) in enumerate(zip(tasks, plan_lines), start=1):
        logical_id = task["logical_task_id"]
        task_root = attempt_dir / "tasks" / logical_id
        runner_root = task_root
        env = os.environ.copy()
        env.update(
            {
                "SCAN_RUNS_DIR": str(runner_root / "runs"),
                "LATEST_RUN_LINK": str(runner_root / "latest"),
                "LATEST_POINTS_CSV": str(runner_root / "points.csv"),
                "LATEST_RUN_CONFIG": str(runner_root / "run_config.json"),
                "LATEST_EFFICIENCY_MAP": str(runner_root / "efficiency_map.csv"),
                "SCAN_GIT_COMMIT": str(git["commit"]),
                "SCAN_GIT_BRANCH": "detached-fixture",
                "SCAN_GIT_DIRTY": "false",
                "G4RUN_MANAGER_TYPE": "Serial",
                "SCAN_RUN_ID_SUFFIX": f"{attempt_id}_{logical_id}",
                "RN_ATTEMPT_ID": attempt_id,
                "RN_LOGICAL_TASK_INDEX": task["task_index"],
                "RN_LOGICAL_TASK_ID": logical_id,
                "RN_PLAN_HASH": str(manifest["plan_hash"]),
                "RN_GIT_COMMIT": str(git["commit"]),
                "RN_ENVIRONMENT_MODE": "osc-production",
                "RN_ENVIRONMENT_IDENTITY": str(environment["identity_hash"]),
                "RN_IMAGE_SHA256": str(environment["image"]["sha256"]),
                "RN_G4_DATA_MANIFEST_SHA256": str(
                    environment["g4_data_manifest"]["sha256"]
                ),
                "RN_EXECUTABLE_SHA256": str(
                    environment["build_artifact"]["sha256"]
                ),
                "SLURM_ARRAY_JOB_ID": "12345",
                "SLURM_ARRAY_TASK_ID": str(array_index),
                "SLURM_JOB_ID": str(12345 + array_index),
            }
        )
        run(
            ["./run_sipm_cavity_scan.sh", *shlex.split(line), "--dry-run"],
            cwd=opnovice_dir,
            env=env,
        )
        config_path = next((runner_root / "runs").glob("*/run_config.json"))
        run_dir = config_path.parent
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["execution"]["formal_campaign_task"] is True
        config["dry_run"] = False
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        with (run_dir / "points.csv").open(encoding="utf-8", newline="") as stream:
            point = next(csv.DictReader(stream))
        root_path = Path(point["root"])
        log_path = Path(point["log"])
        root_path.write_bytes(b"fixture ROOT payload\n")
        log_path.write_text("fixture simulation completed\n", encoding="utf-8")
        write_fake_summary(
            root_path.with_name(f"{root_path.stem}_summary.csv"), task
        )
        (run_dir / "efficiency_map.csv").write_text(
            "x,y,generated,detected\n0,0,1,1\n", encoding="utf-8"
        )
        run(
            [
                sys.executable,
                "hpc/osc/record_realistic_neutron_task_result.py",
                "--campaign-dir",
                str(campaign_dir),
                "--attempt-id",
                attempt_id,
                "--logical-task-id",
                logical_id,
                "--task-root",
                str(task_root),
            ],
            cwd=repo_root,
            env=env,
        )
        mapped_rows.append({"array_index": array_index, **task})

    attempt_tasks_path = attempt_dir / "tasks.tsv"
    fields = ["array_index", *tasks[0].keys()]
    with attempt_tasks_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(mapped_rows)
    attempt_scan_args = attempt_dir / "scan_args.txt"
    attempt_scan_args.write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
    (attempt_dir / "attempt.json").write_text(
        json.dumps(
            {
                "schema_version": "realistic-neutron-submission-attempt-v1",
                "attempt_id": attempt_id,
                "campaign_id": manifest["campaign_id"],
                "plan_hash": manifest["plan_hash"],
                "mode": "submit",
                "status": "submitted",
                "task_count": len(tasks),
                "logical_task_ids": [task["logical_task_id"] for task in tasks],
                "frozen_source": "/fixture/frozen/source",
                "submitted_utc": "2026-07-14T00:00:00+00:00",
                "slurm_job_id": "12345",
                "submission_command": ["sbatch", "fixture"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    attempt_row = {
        "attempt_id": attempt_id,
        "submitted_utc": "2026-07-14T00:00:00+00:00",
        "mode": "submit",
        "status": "submitted",
        "campaign_id": manifest["campaign_id"],
        "plan_hash": manifest["plan_hash"],
        "git_commit": git["commit"],
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
        "slurm_job_id": "12345",
        "array_spec": f"1-{len(tasks)}",
        "task_count": len(tasks),
        "attempt_tasks_tsv": str(attempt_tasks_path.relative_to(campaign_dir)),
        "scan_args_file": str(attempt_scan_args.relative_to(campaign_dir)),
        "frozen_source": "/fixture/frozen/source",
    }
    with (campaign_dir / "submission-attempts.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=ATTEMPT_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerow(attempt_row)


def validate_result_finalizer(repo_root: Path, temp_root: Path) -> None:
    campaign_dir = temp_root / "finalizer-campaign"
    generate_campaign(
        repo_root=repo_root,
        out_dir=campaign_dir,
        stage="geometry-smoke",
        extra=["--geant4-version", "11.4.2"],
    )
    git_commit = run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    manifest = promote_campaign_to_production(
        campaign_dir, temp_root / "finalizer-artifacts", git_commit=git_commit
    )
    prepare_fake_results(repo_root, campaign_dir, manifest)
    output_dir = campaign_dir / "finalized"
    run(
        [
            sys.executable,
            "hpc/osc/finalize_realistic_neutron_campaign.py",
            "--campaign-dir",
            str(campaign_dir),
            "--output-dir",
            str(output_dir),
            "--bootstrap-resamples",
            "100",
        ],
        cwd=repo_root,
    )
    assert (output_dir / "task_index.tsv").is_file()
    assert (output_dir / "validation_report.json").is_file()
    with (output_dir / "thickness_ratios.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        ratios = list(csv.DictReader(stream))
    assert len(ratios) == 3
    assert all(
        math.isclose(float(row["production_ratio_16_over_4"]), 3.0)
        for row in ratios
    )
    assert all(
        math.isclose(float(row["collection_ratio_16_over_4"]), 0.75)
        for row in ratios
    )
    assert all(
        math.isclose(float(row["net_ratio_16_over_4"]), 1.5)
        for row in ratios
    )
    report = json.loads(
        (output_dir / "validation_report.json").read_text(encoding="utf-8")
    )
    assert report["valid"] is True and report["selected_tasks"] == 6

    fixture_dir = temp_root / "event-fixtures"
    fixture_dir.mkdir()
    for task in read_tasks(campaign_dir / "tasks.tsv"):
        tile = int(task["tile_thickness_mm"])
        generated, scintillation, sipm = (
            (100, 40, 10) if tile == 4 else (200, 120, 15)
        )
        fixture_path = fixture_dir / f"{task['logical_task_id']}.csv"
        with fixture_path.open("w", encoding="utf-8", newline="") as stream:
            row = {
                "generated_optical_photons": generated,
                "scintillation_photons": scintillation,
                "sipm_detected_photons": sipm,
                "primary_neutron_elastic_count": 1,
                "primary_neutron_inelastic_count": 0,
                "primary_neutron_capture_count": 0,
            }
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    analysis_dir = output_dir / "analysis"
    root_analysis = run(
        [
            sys.executable,
            "hpc/osc/analyze_realistic_neutron_campaign.py",
            "--campaign-dir",
            str(campaign_dir),
            "--finalized-dir",
            str(output_dir),
            "--output-dir",
            str(output_dir / "root-analysis-fixture"),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "NameError" not in root_analysis.stdout
    missing_fixture_opt_in = run(
        [
            sys.executable,
            "hpc/osc/analyze_realistic_neutron_campaign.py",
            "--campaign-dir",
            str(campaign_dir),
            "--finalized-dir",
            str(output_dir),
            "--output-dir",
            str(output_dir / "unmarked-fixture-analysis"),
            "--event-fixture-dir",
            str(fixture_dir),
        ],
        cwd=repo_root,
        expect_success=False,
    )
    assert "requires --testing-allow-event-fixtures" in missing_fixture_opt_in.stdout
    run(
        [
            sys.executable,
            "hpc/osc/analyze_realistic_neutron_campaign.py",
            "--campaign-dir",
            str(campaign_dir),
            "--finalized-dir",
            str(output_dir),
            "--output-dir",
            str(analysis_dir),
            "--event-fixture-dir",
            str(fixture_dir),
            "--testing-allow-event-fixtures",
        ],
        cwd=repo_root,
    )
    analyzed = read_csv_rows(analysis_dir / "thickness_ratios.csv")
    assert len(analyzed) == 3
    assert all(
        math.isclose(float(row["production_ci95_low"]), 3.0)
        and math.isclose(float(row["production_ci95_high"]), 3.0)
        for row in analyzed
    )
    convergence = read_csv_rows(analysis_dir / "absorber_convergence.csv")
    assert len(convergence) == 2
    assert all(row["passes_absorber_convergence"] == "True" for row in convergence)
    production_statistics = json.loads(
        (analysis_dir / "production_statistics.json").read_text(encoding="utf-8")
    )
    assert production_statistics["status"] == "not_applicable_outside_convergence_pilot"
    completed_config = json.loads(
        (analysis_dir / "analysis_config.json").read_text(encoding="utf-8")
    )
    assert completed_config["accepted_statistical_evidence"] is False


def validate_benchmark_summary(repo_root: Path, temp_root: Path) -> None:
    campaign_dir = temp_root / "benchmark-campaign"
    generate_campaign(
        repo_root=repo_root,
        out_dir=campaign_dir,
        stage="benchmark",
        extra=["--geant4-version", "11.4.2"],
    )
    git_commit = run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    manifest = promote_campaign_to_production(
        campaign_dir, temp_root / "benchmark-artifacts", git_commit=git_commit
    )
    prepare_fake_results(repo_root, campaign_dir, manifest)
    finalized_dir = campaign_dir / "finalized"
    run(
        [
            sys.executable,
            "hpc/osc/finalize_realistic_neutron_campaign.py",
            "--campaign-dir",
            str(campaign_dir),
            "--output-dir",
            str(finalized_dir),
            "--bootstrap-resamples",
            "100",
        ],
        cwd=repo_root,
    )
    sacct = temp_root / "benchmark-sacct.psv"
    sacct.write_text(
        "12345|12345|COMPLETED|0:0|200||\n"
        "12345_1|12346|COMPLETED|0:0|100||\n"
        "12345_1.batch|12346.batch|COMPLETED|0:0|100|256M|512M\n"
        "12345_2|12347|COMPLETED|0:0|200||\n"
        "12345_2.batch|12347.batch|COMPLETED|0:0|200|384M|768M\n",
        encoding="utf-8",
    )
    output_dir = finalized_dir / "benchmark"
    run(
        [
            sys.executable,
            "hpc/osc/summarize_realistic_neutron_benchmark.py",
            "--campaign-dir",
            str(campaign_dir),
            "--finalized-dir",
            str(finalized_dir),
            "--output-dir",
            str(output_dir),
            "--sacct-input",
            str(sacct),
        ],
        cwd=repo_root,
    )
    rows = read_csv_rows(output_dir / "benchmark_report.csv")
    assert len(rows) == 2
    assert {int(row["max_rss_bytes"]) for row in rows} == {
        256 * 1024 * 1024,
        384 * 1024 * 1024,
    }
    report = json.loads(
        (output_dir / "benchmark_report.json").read_text(encoding="utf-8")
    )
    recommendation = report["convergence_pilot_block_recommendation"]
    assert recommendation["recommended_events_per_task"] == 250
    assert recommendation["recommended_blocks_per_configuration"] == 4
    assert recommendation["recommended_block_within_target"] is True
    assert recommendation["target_feasible_at_one_event"] is True

    legacy_sacct = temp_root / "benchmark-sacct-jobidraw-only.psv"
    legacy_sacct.write_text(
        "12345|COMPLETED|0:0|200||\n"
        "12346|COMPLETED|0:0|100||\n"
        "12346.batch|COMPLETED|0:0|100|256M|512M\n"
        "12347|COMPLETED|0:0|200||\n"
        "12347.batch|COMPLETED|0:0|200|384M|768M\n",
        encoding="utf-8",
    )
    legacy_output_dir = finalized_dir / "benchmark-jobidraw-only"
    run(
        [
            sys.executable,
            "hpc/osc/summarize_realistic_neutron_benchmark.py",
            "--campaign-dir",
            str(campaign_dir),
            "--finalized-dir",
            str(finalized_dir),
            "--output-dir",
            str(legacy_output_dir),
            "--sacct-input",
            str(legacy_sacct),
        ],
        cwd=repo_root,
    )
    legacy_rows = read_csv_rows(legacy_output_dir / "benchmark_report.csv")
    assert len(legacy_rows) == 2
    assert {int(row["max_rss_bytes"]) for row in legacy_rows} == {
        256 * 1024 * 1024,
        384 * 1024 * 1024,
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def initialize_fixture_repo(path: Path) -> str:
    path.mkdir()
    (path / "hpc/osc").mkdir(parents=True)
    (path / "hpc/osc/submit_scan.sbatch").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    run(["git", "init", "-q"], cwd=path)
    run(["git", "config", "user.name", "Infrastructure Test"], cwd=path)
    run(["git", "config", "user.email", "infra@example.invalid"], cwd=path)
    run(["git", "add", "."], cwd=path)
    run(["git", "commit", "-q", "-m", "fixture"], cwd=path)
    return run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()


def validate_submission_workflow(repo_root: Path, temp_root: Path) -> None:
    campaign_dir = temp_root / "submission-campaign"
    generate_campaign(
        repo_root=repo_root,
        out_dir=campaign_dir,
        stage="geometry-smoke",
        extra=["--geant4-version", "11.4.2"],
    )
    fixture_repo = temp_root / "fixture-repo"
    fixture_commit = initialize_fixture_repo(fixture_repo)
    promote_campaign_to_production(
        campaign_dir, temp_root / "submission-artifacts", git_commit=fixture_commit
    )
    fake_bin = temp_root / "fake-bin"
    fake_bin.mkdir()
    fake_sbatch = fake_bin / "sbatch"
    fake_squeue = fake_bin / "squeue"
    fake_sacct = fake_bin / "sacct"
    write_executable(fake_sbatch, "#!/usr/bin/env sh\necho 9001\n")
    write_executable(fake_squeue, "#!/usr/bin/env sh\nexit 0\n")
    write_executable(
        fake_sacct,
        "#!/usr/bin/env sh\n"
        "i=1\n"
        "while [ \"$i\" -le 6 ]; do echo \"9001_${i}|FAILED|1:0\"; i=$((i+1)); done\n",
    )
    data_root = temp_root / "g4-data"
    data_root.mkdir()
    frozen_root = temp_root / "frozen"
    base = [
        sys.executable,
        "hpc/osc/submit_realistic_neutron_campaign.py",
        "--campaign-dir",
        str(campaign_dir),
        "--project-root",
        str(fixture_repo),
        "--account",
        "TEST",
        "--g4-data-root",
        str(data_root),
        "--frozen-root",
        str(frozen_root),
        "--sbatch-command",
        str(fake_sbatch),
        "--squeue-command",
        str(fake_squeue),
        "--sacct-command",
        str(fake_sacct),
    ]
    run(base, cwd=repo_root)
    manifest_path = campaign_dir / "submission-attempts.tsv"
    with manifest_path.open(encoding="utf-8", newline="") as stream:
        attempts = list(csv.DictReader(stream, delimiter="\t"))
    assert len(attempts) == 1 and attempts[0]["task_count"] == "6"
    resumed = run([*base, "--resume"], cwd=repo_root)
    assert "No tasks require resume submission" in resumed.stdout
    run([*base, "--retry-failed"], cwd=repo_root)
    with manifest_path.open(encoding="utf-8", newline="") as stream:
        attempts = list(csv.DictReader(stream, delimiter="\t"))
    assert len(attempts) == 2
    assert attempts[-1]["mode"] == "retry-failed"
    assert attempts[-1]["task_count"] == "6"

    orphan_dir = campaign_dir / "attempts" / "orphan-attempt"
    orphan_dir.mkdir()
    (orphan_dir / "attempt.json").write_text(
        json.dumps(
            {
                "attempt_id": "orphan-attempt",
                "status": "prepared",
                "slurm_job_id": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    orphan_check = run([*base, "--resume"], cwd=repo_root, expect_success=False)
    assert "requires manual Slurm audit" in orphan_check.stdout


def validate_sbatch_task_mapping(repo_root: Path, temp_root: Path) -> None:
    project = temp_root / "sbatch-project"
    (project / "hpc/osc").mkdir(parents=True)
    fake_runner = project / "hpc/osc/run_scan_apptainer.sh"
    write_executable(
        fake_runner,
        "#!/usr/bin/env bash\n"
        "printf 'mapped=%s:%s\\n' \"${RN_LOGICAL_TASK_INDEX}\" \"${RN_LOGICAL_TASK_ID}\"\n"
        "printf 'args=%s\\n' \"$*\"\n",
    )
    campaign = temp_root / "sbatch-campaign"
    campaign.mkdir()
    task_map = campaign / "tasks.tsv"
    task_map.write_text(
        "array_index\ttask_index\tlogical_task_id\n"
        "1\t4\ttask-four\n"
        "2\t9\ttask-nine\n",
        encoding="utf-8",
    )
    scan_args = campaign / "scan_args.txt"
    scan_args.write_text(
        "# fixture\nfull custom --events 4\nfull custom --events 9\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "G4_PROJECT_ROOT": str(project),
            "RN_CAMPAIGN_HOST_DIR": str(campaign),
            "RN_ATTEMPT_ID": "attempt-fixture",
            "RN_ATTEMPT_TASKS_FILE": str(task_map),
            "SCAN_ARGS_FILE": str(scan_args),
            "SLURM_ARRAY_JOB_ID": "7000",
            "SLURM_ARRAY_TASK_ID": "2",
        }
    )
    result = run(
        ["bash", str(repo_root / "hpc/osc/submit_scan.sbatch")],
        cwd=project,
        env=env,
    )
    assert "mapped=9:task-nine" in result.stdout
    assert "args=full custom --events 9" in result.stdout


def validate_stage_shapes(repo_root: Path, temp_root: Path) -> None:
    cases = (
        ("benchmark", [], 2),
        ("convergence-pilot", [], 24),
        ("center-production", ["--events", "10", "--blocks", "4"], 8),
        ("line-scan", ["--events", "10", "--blocks", "4"], 72),
    )
    all_seeds: set[int] = set()
    for stage, extra, expected_count in cases:
        out_dir = temp_root / stage
        manifest = generate_campaign(
            repo_root=repo_root, out_dir=out_dir, stage=stage, extra=extra
        )
        tasks = read_tasks(out_dir / "tasks.tsv")
        assert manifest["task_count"] == expected_count
        assert len(tasks) == expected_count
        stage_seeds = {
            int(task[key]) for task in tasks for key in ("seed1", "seed2")
        }
        assert len(stage_seeds) == expected_count * 2
        all_seeds.update(stage_seeds)
        if stage == "line-scan":
            xs = {int(task["x_mm"]) for task in tasks}
            assert xs == {-20, 5, 10, 15, 20, 25, 30, 35, 40}
            assert 0 not in xs


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    opnovice_dir = repo_root / "test/OpNovice2"
    run(["bash", "-n", "run_sipm_cavity_scan.sh"], cwd=opnovice_dir)
    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "hpc/osc/realistic_neutron_campaign_lib.py",
            "hpc/osc/generate_realistic_neutron_campaign.py",
            "hpc/osc/submit_realistic_neutron_campaign.py",
            "hpc/osc/record_realistic_neutron_task_result.py",
            "hpc/osc/finalize_realistic_neutron_campaign.py",
            "hpc/osc/analyze_realistic_neutron_campaign.py",
            "hpc/osc/realistic_neutron_event_audit.py",
            "hpc/osc/audit_realistic_neutron_campaign.py",
            "hpc/osc/summarize_realistic_neutron_benchmark.py",
            "hpc/osc/run_realistic_neutron_electron_regression.py",
            "test/OpNovice2/visualize_realistic_neutron_geometry.py",
        ],
        cwd=repo_root,
    )
    run(["bash", "-n", "hpc/osc/run_scan_apptainer.sh"], cwd=repo_root)
    run(
        ["bash", "-n", "hpc/osc/configure_geant4_data_env.sh"],
        cwd=repo_root,
    )
    run(
        [
            "bash",
            "-n",
            "hpc/osc/run_realistic_neutron_electron_regression_apptainer.sh",
        ],
        cwd=repo_root,
    )
    run(["bash", "-n", "hpc/osc/submit_scan.sbatch"], cwd=repo_root)
    validate_event_schema(opnovice_dir)
    validate_event_causal_audit()
    with tempfile.TemporaryDirectory(prefix="g4optics-realistic-neutron-") as temp:
        temp_root = Path(temp)
        validate_runner_campaign(repo_root, temp_root)
        validate_visualization_tool(repo_root, temp_root)
        validate_electron_regression_orchestration(repo_root, temp_root)
        validate_stage_shapes(repo_root, temp_root)
        validate_result_finalizer(repo_root, temp_root)
        validate_benchmark_summary(repo_root, temp_root)
        validate_submission_workflow(repo_root, temp_root)
        validate_sbatch_task_mapping(repo_root, temp_root)
    print("realistic-neutron infrastructure checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
