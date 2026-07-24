#!/usr/bin/env python3
"""Cheap deterministic checks for the steel-module campaign workflow."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

import analyze_steel_module_campaign as analyzer
import finalize_steel_module_campaign as finalizer
from audit_realistic_neutron_campaign import (
    SUMMARY_FLOAT_FIELDS,
    SUMMARY_INTEGER_FIELDS,
)
from realistic_neutron_event_audit import SIPM_SENSOR_FIELDS
from steel_module_campaign_lib import (
    ATTEMPT_FIELDS,
    CampaignTask,
    environment_identity,
    load_campaign,
    sha256_file,
    verify_finalized_checksums,
)


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


def generate(
    repo_root: Path,
    output: Path,
    *,
    stage: str,
    extra: tuple[str, ...] = (),
) -> dict[str, object]:
    run(
        [
            sys.executable,
            "hpc/osc/generate_steel_module_campaign.py",
            "--out-dir",
            str(output),
            "--stage",
            stage,
            "--campaign-seed",
            "20260715",
            "--allow-dirty",
            *extra,
        ],
        cwd=repo_root,
    )
    return json.loads((output / "campaign.json").read_text(encoding="utf-8"))


def empty_summary_report() -> dict[str, int | float]:
    report: dict[str, int | float] = {
        field: 0 for field in SUMMARY_INTEGER_FIELDS
    }
    report.update({field: 0.0 for field in SUMMARY_FLOAT_FIELDS})
    return report


def default_event_report(task: dict[str, str]) -> dict[str, int | float]:
    report = empty_summary_report()
    report.update(
        {
            "events": 1,
            "committed_events": 1,
            "shoot_position_events": 1,
            "primary_energy_events": 1,
            "generated_optical_photons": 10,
            "scintillation_photons": 10,
            "sipm_detected_photons": 2,
            "primary_neutron_interaction_events": 1,
            "primary_neutron_elastic_count": 1,
            "primary_neutron_elastic_events": 1,
            "charged_tile_entry_events": 1,
            "charged_tile_entry_count": 1,
            "steel_edep_sum_mev": 1.0,
            "tile_edep_sum_mev": 0.5,
        }
    )
    sensor_values = [2, 0, 0, 0]
    if task["sipm_layout"] == "back-four":
        sensor_values = [1, 1, 0, 0]
    for field, value in zip(SIPM_SENSOR_FIELDS, sensor_values):
        report[field] = value
    return report


def make_production_fixture(
    repo_root: Path,
    campaign_dir: Path,
    scratch: Path,
    *,
    report_factory: Callable[
        [dict[str, str]], dict[str, int | float]
    ]
    | None = None,
) -> tuple[
    dict[str, object],
    dict[Path, dict[str, int | float]],
    dict[str, dict[str, int]],
]:
    image = scratch / "geant4.sif"
    data_manifest = scratch / "g4-data-manifest.json"
    executable = scratch / "OpNovice2"
    image.write_bytes(b"fixture image\n")
    data_manifest.write_text('{"fixture": true}\n', encoding="utf-8")
    executable.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    manifest_path = campaign_dir / "campaign.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    manifest["git"] = {
        "commit": commit,
        "branch": "fixture",
        "dirty": False,
        "dirty_paths": [],
    }
    environment: dict[str, object] = {
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

    attempt_id = "fixture-attempt"
    attempt_dir = campaign_dir / "attempts" / attempt_id
    attempt_dir.mkdir(parents=True)
    tasks = read_tasks(campaign_dir / "tasks.tsv")
    plan_lines = [
        line
        for line in (campaign_dir / "scan_args.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    ]
    root_reports: dict[Path, dict[str, int | float]] = {}
    event_rows: dict[str, dict[str, int]] = {}
    mapped_rows: list[dict[str, object]] = []

    for array_index, (task, line) in enumerate(zip(tasks, plan_lines), start=1):
        logical_id = task["logical_task_id"]
        task_root = attempt_dir / "tasks" / logical_id
        env = os.environ.copy()
        env.update(
            {
                "SCAN_RUNS_DIR": str(task_root / "runs"),
                "LATEST_RUN_LINK": str(task_root / "latest"),
                "LATEST_POINTS_CSV": str(task_root / "points.csv"),
                "LATEST_RUN_CONFIG": str(task_root / "run_config.json"),
                "LATEST_EFFICIENCY_MAP": str(task_root / "efficiency_map.csv"),
                "SCAN_GIT_COMMIT": commit,
                "SCAN_GIT_BRANCH": "detached-fixture",
                "SCAN_GIT_DIRTY": "false",
                "G4RUN_MANAGER_TYPE": "Serial",
                "SCAN_RUN_ID_SUFFIX": f"{attempt_id}_{logical_id}",
                "RN_ATTEMPT_ID": attempt_id,
                "RN_LOGICAL_TASK_INDEX": task["task_index"],
                "RN_LOGICAL_TASK_ID": logical_id,
                "RN_PLAN_HASH": str(manifest["plan_hash"]),
                "RN_GIT_COMMIT": commit,
                "RN_ENVIRONMENT_MODE": "osc-production",
                "RN_ENVIRONMENT_IDENTITY": str(environment["identity_hash"]),
                "RN_IMAGE_SHA256": str(environment["image"]["sha256"]),
                "RN_G4_DATA_MANIFEST_SHA256": str(
                    environment["g4_data_manifest"]["sha256"]
                ),
                "RN_EXECUTABLE_SHA256": str(
                    environment["build_artifact"]["sha256"]
                ),
                "RN_TASK_RESULT_RECORDER": "record_steel_module_task_result.py",
                "SLURM_ARRAY_JOB_ID": "12345",
                "SLURM_ARRAY_TASK_ID": str(array_index),
                "SLURM_JOB_ID": str(12345 + array_index),
            }
        )
        run(
            ["./run_sipm_cavity_scan.sh", *shlex.split(line), "--dry-run"],
            cwd=repo_root / "test/OpNovice2",
            env=env,
        )
        config_path = next((task_root / "runs").glob("*/run_config.json"))
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
        sensor_count = 4 if task["sipm_layout"] == "back-four" else 1
        log_path.write_text(
            "Checking overlaps for volume SteelAbsorber:0 (G4Box) ... OK!\n"
            + "".join(
                f"Checking overlaps for volume SiPM:{index} (G4Box) ... OK!\n"
                for index in range(sensor_count)
            ),
            encoding="utf-8",
        )

        if report_factory is None:
            report = default_event_report(task)
        else:
            report = report_factory(task)
        assert int(report["events"]) == int(task["events"])
        event_rows[logical_id] = {
            field: int(report[field]) for field in analyzer.EVENT_FIELDS
        }
        summary_path = root_path.with_name(f"{root_path.stem}_summary.csv")
        summary_fields = [*SUMMARY_INTEGER_FIELDS, *SUMMARY_FLOAT_FIELDS]
        with summary_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=summary_fields)
            writer.writeheader()
            writer.writerow({field: report[field] for field in summary_fields})
        (run_dir / "efficiency_map.csv").write_text(
            "tag,x,y,events\nfixture,0,0,1\n", encoding="utf-8"
        )
        root_reports[root_path.resolve()] = report

        run(
            [
                sys.executable,
                "hpc/osc/record_steel_module_task_result.py",
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

    task_map = attempt_dir / "tasks.tsv"
    with task_map.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["array_index", *CampaignTask.__dataclass_fields__],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(mapped_rows)
    attempt_scan_args = attempt_dir / "scan_args.txt"
    attempt_scan_args.write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
    (attempt_dir / "attempt.json").write_text(
        json.dumps(
            {
                "schema_version": "steel-module-submission-attempt-v1",
                "attempt_id": attempt_id,
                "campaign_id": manifest["campaign_id"],
                "plan_hash": manifest["plan_hash"],
                "mode": "submit",
                "status": "submitted",
                "task_count": len(tasks),
                "logical_task_ids": [task["logical_task_id"] for task in tasks],
                "frozen_source": "/fixture/frozen/source",
                "submitted_utc": "2026-07-15T00:00:00+00:00",
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
        "submitted_utc": "2026-07-15T00:00:00+00:00",
        "mode": "submit",
        "status": "submitted",
        "campaign_id": manifest["campaign_id"],
        "plan_hash": manifest["plan_hash"],
        "git_commit": commit,
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
        "slurm_job_id": "12345",
        "array_spec": f"1-{len(tasks)}",
        "task_count": len(tasks),
        "attempt_tasks_tsv": str(task_map.relative_to(campaign_dir)),
        "scan_args_file": str(attempt_scan_args.relative_to(campaign_dir)),
        "frozen_source": "/fixture/frozen/source",
    }
    with (campaign_dir / "submission-attempts.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=ATTEMPT_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerow(attempt_row)
    return manifest, root_reports, event_rows


def analysis_event_report(task: dict[str, str]) -> dict[str, int | float]:
    """One deterministic event; four blocks form a known zero/ratio pattern."""
    configuration = (
        int(task["tile_thickness_mm"]),
        task["sipm_layout"],
        int(task["absorber_transverse_mm"]),
    )
    scales = {
        (4, "back-center", 200): (1, 1, 4),
        (4, "back-center", 300): (1, 1, 4),
        (4, "back-center", 500): (1, 1, 4),
        (24, "back-center", 500): (2, 2, 8),
        (24, "edge-center", 500): (3, 3, 12),
        (24, "back-four", 500): (4, 4, 16),
    }
    production_scale, generated_scale, detected = scales[configuration]
    block = int(task["seed_block"])
    generated = (0, 10, 20, 30)[block] * generated_scale
    scintillation = (0, 0, 10, 20)[block] * production_scale
    sipm = detected if block == 3 else 0
    interacted = int(block >= 2)

    report = empty_summary_report()
    report.update(
        {
            "events": 1,
            "committed_events": 1,
            "shoot_position_events": 1,
            "primary_energy_events": 1,
            "generated_optical_photons": generated,
            "scintillation_photons": scintillation,
            "sipm_detected_photons": sipm,
            "cerenkov_photons": generated - scintillation,
            "steel_edep_nonzero_events": interacted,
            "primary_neutron_interaction_events": interacted,
            "primary_neutron_elastic_count": interacted,
            "primary_neutron_elastic_events": interacted,
            "charged_tile_entry_events": interacted,
            "charged_tile_entry_count": interacted,
            "tile_edep_nonzero_events": interacted,
            "generated_optical_zero_events": int(generated == 0),
            "scintillation_zero_events": int(scintillation == 0),
            "sipm_detected_zero_events": int(sipm == 0),
            "steel_edep_sum_mev": float(interacted),
            "tile_edep_sum_mev": 0.5 * interacted,
        }
    )
    sensor_values = [sipm, 0, 0, 0]
    if task["sipm_layout"] == "back-four" and sipm:
        assert sipm % 4 == 0
        sensor_values = [sipm // 4] * 4
    for field, value in zip(SIPM_SENSOR_FIELDS, sensor_values):
        report[field] = value
    return report


def write_event_fixtures(
    directory: Path, event_rows: dict[str, dict[str, int]]
) -> None:
    directory.mkdir(parents=True)
    for logical_id, row in event_rows.items():
        with (directory / f"{logical_id}.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=analyzer.EVENT_FIELDS)
            writer.writeheader()
            writer.writerow(row)


def assert_checksum_manifest(directory: Path) -> None:
    lines = (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    expected_files = {
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    }
    recorded_files: set[str] = set()
    for line in lines:
        digest, name = line.split("  ", maxsplit=1)
        assert "/" not in name and name not in recorded_files
        recorded_files.add(name)
        assert sha256_file(directory / name) == digest
    assert recorded_files == expected_files


def finalize_fixture(
    campaign_dir: Path,
    root_reports: dict[Path, dict[str, int | float]],
    output: Path,
) -> None:
    def fake_root_audit(
        path: Path, *, expected_events: int | None = None, context: str = ""
    ) -> tuple[dict[str, list[int]], dict[str, int | float]]:
        report = root_reports[path.resolve()]
        assert expected_events == report["events"]
        return {}, dict(report)

    finalizer.read_and_audit_root = fake_root_audit
    bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
    attempts = finalizer.load_submitted_attempts(bundle)
    candidates, invalid = finalizer.collect_candidates(bundle, attempts)
    assert not invalid
    selected = finalizer.select_candidates(bundle, candidates, {})
    finalizer.finalize_outputs(
        bundle,
        output,
        selected,
        invalid,
        bootstrap_seed=20260715,
        bootstrap_resamples=100,
    )
    verify_finalized_checksums(output)


def main() -> int:
    missing_dependencies = [
        name for name in ("numpy", "uproot") if importlib.util.find_spec(name) is None
    ]
    if missing_dependencies:
        print(
            "Cannot check steel-module campaign infrastructure: activate the "
            "analysis environment with NumPy and uproot (missing: "
            + ", ".join(missing_dependencies)
            + ")",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parents[2]
    ten_percent = analyzer.projection_requirement(
        point=2.0,
        low=1.8,
        high=2.2,
        current_events=1000,
        target=0.10,
        block_events=250,
        safety_factor=1.25,
    )
    five_percent = analyzer.projection_requirement(
        point=2.0,
        low=1.8,
        high=2.2,
        current_events=1000,
        target=0.05,
        block_events=250,
        safety_factor=1.25,
    )
    assert ten_percent is not None and five_percent is not None
    assert ten_percent["rounded_events_per_configuration"] == 1250
    assert five_percent["rounded_events_per_configuration"] == 5000
    run(
        [sys.executable, "hpc/osc/check_steel_module_scan_preset.py"],
        cwd=repo_root,
    )
    with tempfile.TemporaryDirectory(prefix="steel-module-campaign-check-") as raw:
        scratch = Path(raw)
        geometry = scratch / "geometry-smoke"
        generate(repo_root, geometry, stage="geometry-smoke")
        tasks = read_tasks(geometry / "tasks.tsv")
        assert len(tasks) == 18
        assert {int(task["tile_thickness_mm"]) for task in tasks} == {
            4,
            8,
            12,
            16,
            20,
            24,
        }
        assert {task["sipm_layout"] for task in tasks} == {
            "back-center",
            "edge-center",
            "back-four",
        }
        run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(geometry),
                "--check-only",
            ],
            cwd=repo_root,
        )

        missing_events = scratch / "bad-benchmark"
        run(
            [
                sys.executable,
                "hpc/osc/generate_steel_module_campaign.py",
                "--out-dir",
                str(missing_events),
                "--stage",
                "benchmark",
                "--campaign-seed",
                "1",
                "--allow-dirty",
            ],
            cwd=repo_root,
            expect_success=False,
        )

        missing_pilot_selection = scratch / "bad-pilot"
        run(
            [
                sys.executable,
                "hpc/osc/generate_steel_module_campaign.py",
                "--out-dir",
                str(missing_pilot_selection),
                "--stage",
                "convergence-pilot",
                "--campaign-seed",
                "1",
                "--events",
                "1",
                "--blocks",
                "4",
                "--allow-dirty",
            ],
            cwd=repo_root,
            expect_success=False,
        )

        checked_pilot_selection = (
            repo_root
            / "hpc/osc/configurations/steel-module-convergence-pilot-v1.tsv"
        )
        frozen_pilot = scratch / "frozen-pilot"
        generate(
            repo_root,
            frozen_pilot,
            stage="convergence-pilot",
            extra=(
                "--events",
                "250",
                "--blocks",
                "4",
                "--configurations-tsv",
                str(checked_pilot_selection),
            ),
        )
        frozen_pilot_tasks = read_tasks(frozen_pilot / "tasks.tsv")
        frozen_configurations = {
            (
                int(task["tile_thickness_mm"]),
                task["sipm_layout"],
                int(task["absorber_transverse_mm"]),
            )
            for task in frozen_pilot_tasks
        }
        expected_frozen_configurations = {
            (thickness, layout, 500)
            for layout in ("back-center", "edge-center", "back-four")
            for thickness in (4, 8, 12, 16, 20, 24)
        } | {
            (thickness, layout, absorber)
            for layout in ("back-center", "edge-center", "back-four")
            for thickness in (4, 24)
            for absorber in (200, 300)
        }
        assert frozen_configurations == expected_frozen_configurations
        assert len(frozen_configurations) == 30
        assert len(frozen_pilot_tasks) == 120
        assert sum(int(task["events"]) for task in frozen_pilot_tasks) == 30_000
        for configuration in frozen_configurations:
            rows = [
                task
                for task in frozen_pilot_tasks
                if (
                    int(task["tile_thickness_mm"]),
                    task["sipm_layout"],
                    int(task["absorber_transverse_mm"]),
                )
                == configuration
            ]
            assert {int(task["seed_block"]) for task in rows} == {0, 1, 2, 3}
            assert {int(task["events"]) for task in rows} == {250}
        load_campaign(frozen_pilot, verify_external_artifacts=False)

        pilot_selection = scratch / "pilot-selection.tsv"
        pilot_selection.write_text(
            "tile_thickness_mm\tsipm_layout\tabsorber_transverse_mm\n"
            "4\tback-center\t300\n"
            "24\tback-four\t500\n",
            encoding="utf-8",
        )
        pilot = scratch / "pilot"
        generate(
            repo_root,
            pilot,
            stage="convergence-pilot",
            extra=(
                "--events",
                "1",
                "--blocks",
                "4",
                "--configurations-tsv",
                str(pilot_selection),
            ),
        )
        pilot_tasks = read_tasks(pilot / "tasks.tsv")
        assert len(pilot_tasks) == 8
        assert {
            (
                int(task["tile_thickness_mm"]),
                task["sipm_layout"],
                int(task["absorber_transverse_mm"]),
            )
            for task in pilot_tasks
        } == {(4, "back-center", 300), (24, "back-four", 500)}
        load_campaign(pilot, verify_external_artifacts=False)

        benchmark = scratch / "benchmark"
        generate(
            repo_root,
            benchmark,
            stage="benchmark",
            extra=("--events", "1"),
        )
        benchmark_tasks = read_tasks(benchmark / "tasks.tsv")
        assert len(benchmark_tasks) == 4
        assert {
            (int(task["tile_thickness_mm"]), task["sipm_layout"])
            for task in benchmark_tasks
        } == {
            (4, "back-center"),
            (24, "back-center"),
            (24, "edge-center"),
            (24, "back-four"),
        }

        _, root_reports, _ = make_production_fixture(repo_root, benchmark, scratch)
        output = benchmark / "finalized"
        finalize_fixture(benchmark, root_reports, output)
        validation = json.loads(
            (output / "validation_report.json").read_text(encoding="utf-8")
        )
        event_audit = json.loads(
            (output / "event_audit.json").read_text(encoding="utf-8")
        )
        assert validation["valid"] is True
        assert validation["event_audit_integrated"] is True
        assert validation["selected_tasks"] == 4
        assert event_audit["valid"] is True
        assert event_audit["task_count"] == 4
        with (output / "configuration_summary.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            configurations = list(csv.DictReader(stream))
        assert len(configurations) == 4
        back_four = next(
            row for row in configurations if row["sipm_layout"] == "back-four"
        )
        assert int(back_four["sipm_sensor_0_detected_photons"]) == 1
        assert int(back_four["sipm_sensor_1_detected_photons"]) == 1

        analysis_selection = scratch / "analysis-selection.tsv"
        analysis_selection.write_text(
            "tile_thickness_mm\tsipm_layout\tabsorber_transverse_mm\n"
            "4\tback-center\t200\n"
            "4\tback-center\t300\n"
            "4\tback-center\t500\n"
            "24\tback-center\t500\n"
            "24\tedge-center\t500\n"
            "24\tback-four\t500\n",
            encoding="utf-8",
        )
        analysis_campaign = scratch / "analysis-campaign"
        generate(
            repo_root,
            analysis_campaign,
            stage="convergence-pilot",
            extra=(
                "--events",
                "1",
                "--blocks",
                "4",
                "--configurations-tsv",
                str(analysis_selection),
            ),
        )
        analysis_tasks = read_tasks(analysis_campaign / "tasks.tsv")
        assert len(analysis_tasks) == 24
        _, analysis_root_reports, analysis_event_rows = make_production_fixture(
            repo_root,
            analysis_campaign,
            scratch,
            report_factory=analysis_event_report,
        )
        analysis_finalized = analysis_campaign / "finalized"
        finalize_fixture(
            analysis_campaign, analysis_root_reports, analysis_finalized
        )
        event_fixture_dir = scratch / "analysis-event-fixtures"
        write_event_fixtures(event_fixture_dir, analysis_event_rows)
        analysis_command = [
            sys.executable,
            "hpc/osc/analyze_steel_module_campaign.py",
            "--campaign-dir",
            str(analysis_campaign),
            "--event-fixture-dir",
            str(event_fixture_dir),
            "--testing-allow-event-fixtures",
            "--production-block-events",
            "250",
        ]
        run(analysis_command, cwd=repo_root)
        analysis_output = analysis_finalized / "analysis"
        assert_checksum_manifest(analysis_output)

        with (analysis_output / "configuration_intervals.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            interval_rows = list(csv.DictReader(stream))
        assert len(interval_rows) == 6
        reference_interval = next(
            row
            for row in interval_rows
            if int(row["tile_thickness_mm"]) == 4
            and row["sipm_layout"] == "back-center"
            and int(row["absorber_transverse_mm"]) == 500
        )
        expected_fractions = {
            "interaction": (
                0.5,
                0.15003898915214947,
                0.8499610108478506,
            ),
            "generated_optical_zero": (
                0.25,
                0.04558726080970055,
                0.6993581574175981,
            ),
            "scintillation_zero": (
                0.5,
                0.15003898915214947,
                0.8499610108478506,
            ),
            "sipm_detected_zero": (
                0.75,
                0.30064184258240184,
                0.9544127391902995,
            ),
        }
        for name, (fraction, low, high) in expected_fractions.items():
            assert math.isclose(
                float(reference_interval[f"{name}_fraction"]), fraction
            )
            assert math.isclose(
                float(reference_interval[f"{name}_wilson95_low"]),
                low,
                abs_tol=1e-12,
            )
            assert math.isclose(
                float(reference_interval[f"{name}_wilson95_high"]),
                high,
                abs_tol=1e-12,
            )

        with (analysis_output / "per_sensor_intervals.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            sensor_rows = list(csv.DictReader(stream))
        assert len(sensor_rows) == 24
        back_four_sensors = sorted(
            (
                int(row["sensor_index"]),
                float(row["sipm_photons_per_neutron"]),
            )
            for row in sensor_rows
            if int(row["tile_thickness_mm"]) == 24
            and row["sipm_layout"] == "back-four"
            and int(row["absorber_transverse_mm"]) == 500
        )
        assert back_four_sensors == [(0, 1.0), (1, 1.0), (2, 1.0), (3, 1.0)]
        single_sensors = sorted(
            (
                int(row["sensor_index"]),
                float(row["sipm_photons_per_neutron"]),
            )
            for row in sensor_rows
            if int(row["tile_thickness_mm"]) == 24
            and row["sipm_layout"] == "back-center"
            and int(row["absorber_transverse_mm"]) == 500
        )
        assert single_sensors == [(0, 2.0), (1, 0.0), (2, 0.0), (3, 0.0)]

        with (analysis_output / "thickness_ratios.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            thickness_rows = list(csv.DictReader(stream))
        thickness_ratio = next(
            row
            for row in thickness_rows
            if row["sipm_layout"] == "back-center"
            and int(row["absorber_transverse_mm"]) == 500
            and int(row["compared_tile_thickness_mm"]) == 24
        )
        assert math.isclose(float(thickness_ratio["production_ratio"]), 2.0)
        assert math.isclose(float(thickness_ratio["collection_ratio"]), 1.0)
        assert math.isclose(float(thickness_ratio["net_ratio"]), 2.0)

        with (analysis_output / "layout_ratios.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            layout_rows = list(csv.DictReader(stream))
        edge_ratio = next(
            row
            for row in layout_rows
            if row["compared_sipm_layout"] == "edge-center"
        )
        assert math.isclose(float(edge_ratio["production_ratio"]), 1.5)
        assert math.isclose(float(edge_ratio["collection_ratio"]), 1.0)
        assert math.isclose(float(edge_ratio["net_ratio"]), 1.5)
        four_ratio = next(
            row
            for row in layout_rows
            if row["compared_sipm_layout"] == "back-four"
        )
        assert math.isclose(float(four_ratio["production_ratio"]), 2.0)
        assert math.isclose(float(four_ratio["collection_ratio"]), 1.0)
        assert math.isclose(float(four_ratio["net_ratio"]), 2.0)

        with (analysis_output / "absorber_convergence.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            absorber_rows = list(csv.DictReader(stream))
        assert {int(row["candidate_absorber_transverse_mm"]) for row in absorber_rows} == {
            200,
            300,
        }
        for row in absorber_rows:
            for metric in ("production", "collection", "net"):
                assert math.isclose(float(row[f"{metric}_ratio"]), 1.0)
                assert row[f"{metric}_point_within_0p95_1p05"] == "True"

        production = json.loads(
            (analysis_output / "production_statistics.json").read_text(
                encoding="utf-8"
            )
        )
        assert production["automatic_acceptance"] is False
        assert production["precision_targets"] == [0.05, 0.1]
        assert production["safety_factor"] == 1.25
        assert production["status"].startswith("projection_requires_review")
        assert production["events_per_execution_block"] == 250
        assert len(production["targets"]) == 2
        for target in production["targets"]:
            assert target["status"] == "projection_requires_review"
            assert target["recommended_events_per_task"] == 250
            assert target["recommended_blocks_per_configuration"] >= 4
            assert (
                target["recommended_total_events_per_configuration"]
                == target["recommended_blocks_per_configuration"] * 250
            )

        completed_analysis_config = json.loads(
            (analysis_output / "analysis_config.json").read_text(encoding="utf-8")
        )
        assert completed_analysis_config["event_source"] == "fixture_csv"
        assert completed_analysis_config["accepted_statistical_evidence"] is False
        assert completed_analysis_config["analyzer"]["sha256"] == sha256_file(
            repo_root / "hpc/osc/analyze_steel_module_campaign.py"
        )
        assert completed_analysis_config["bootstrap"]["generator"] in {
            "numpy.random.Generator(numpy.random.PCG64)",
            "random.Random(test-fixture-only)",
        }
        assert (
            completed_analysis_config["precision_projection"]["automatic_acceptance"]
            is False
        )

        overwrite = run(
            analysis_command,
            cwd=repo_root,
            expect_success=False,
        )
        assert "refusing to overwrite analysis directory" in overwrite.stdout

    # Keep the immutable production-program and heavier analysis-v2 contracts
    # in dedicated checkers while making both part of the canonical gate.
    run(
        [
            sys.executable,
            "hpc/osc/check_steel_module_production_program.py",
        ],
        cwd=repo_root,
    )
    run(
        [
            sys.executable,
            "hpc/osc/check_steel_module_managed_production.py",
        ],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s1.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s2.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s3.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s4.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_fixed.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_edge_two.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_final.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_four_layout_analysis.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_stack_infrastructure.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_analysis.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s2_analysis.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s3_analysis.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_direct_bc_s4_analysis.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_analysis_v2.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_phase2b_control.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_successor.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_successor_v4.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_r3_failure.py"],
        cwd=repo_root,
    )
    run(
        [
            sys.executable,
            "hpc/osc/check_steel_module_production_r3_preworkspace_failure.py",
        ],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_r3_probe.py"],
        cwd=repo_root,
    )
    run(
        [
            sys.executable,
            "hpc/osc/check_steel_module_production_r3_probe_rejection.py",
        ],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_successor_v5.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_phase2c.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_finalization.py"],
        cwd=repo_root,
    )
    run(
        [sys.executable, "hpc/osc/check_steel_module_production_analysis.py"],
        cwd=repo_root,
    )

    print(
        "steel-module campaign infrastructure: PASS "
        "(18-task smoke, 120-task frozen pilot, 914-task production program, "
        "managed BC-S1 Phase-2A/2B, direct BC-S1 through BC-S4 cumulative "
        "analysis and ordinary arrays plus one-array FIXED production and "
        "final 18-configuration curves plus the 240-task edge-two follow-up "
        "and four-layout aggregate/per-sensor/per-area analysis, plus the "
        "ten-layer stack geometry/causal-audit/direct-campaign/analysis route, "
        "OSC-shaped incident fixture, "
        "closed R2/v3/v4 plus failed/rejected/pre-workspace-R3 evidence, "
        "incident-bound v5 successor, Phase-2C twin and production-ready v6, "
        "GPFS-compatible portable-lock gate, "
        "copy-safe R3 no-Geant4 evidence, "
        "v1-v6 successor-lineage whole-child finalizer and checkpoint, "
        "v1/v2/production analyzers, offline review and progression recorder)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
