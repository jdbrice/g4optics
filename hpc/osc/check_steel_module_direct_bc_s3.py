#!/usr/bin/env python3
"""Focused, scheduler-free checks for the direct BC-S3 campaign route."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import generate_steel_module_direct_bc_s3_campaign as generator
from check_steel_module_direct_bc_s1 import (
    fixture_environment,
    fixture_git_checkout,
)
from check_steel_module_direct_bc_s2 import (
    fixture_progression as bc_s2_fixture_progression,
    fixture_source as bc_s2_fixture_source,
    fixture_tasks as bc_s2_fixture_tasks,
)
from generate_steel_module_direct_bc_s3_campaign import (
    EXPECTED_PREDECESSOR_CAMPAIGN_ID,
    EXPECTED_PREDECESSOR_JOB_ID,
    EXPECTED_PREDECESSOR_SIMULATION_COMMIT,
    EXPECTED_PRIMARY,
    validate_direct_bc_s3_bundle,
    validate_direct_bc_s3_tasks,
    validate_progression_values,
    write_direct_bc_s3_campaign_atomic,
)
from generate_steel_module_direct_bc_s2_campaign import (
    EXPECTED_AUTHORIZATION_GRAPH_HASH,
    EXPECTED_PROGRAM_HASH,
    EXPECTED_PROGRAM_ID,
    write_direct_bc_s2_campaign_atomic,
)
from plot_steel_module_analysis_v2 import write_recursive_checksums
from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    load_campaign,
    sha256_bytes,
    task_configuration_hash,
)
from steel_module_production_program_lib import (
    ACCEPTED_PILOT_EXCLUSION_HASH,
    seed_pair_registry_hash,
    seed_set_hash,
)


def run(
    args: list[str], *, cwd: Path, expect_success: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
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


def fixture_tasks() -> tuple[CampaignTask, ...]:
    tasks: list[CampaignTask] = []
    index = 1
    for thickness in (4, 24):
        for block in range(40, 80):
            logical_id = (
                f"production-lback-center-t{thickness:02d}-a500-b{block:03d}"
            )
            seed1 = 90_000 + 2 * index
            seed2 = seed1 + 1
            configuration_hash = task_configuration_hash(
                stage="production",
                tile_thickness_mm=thickness,
                sipm_layout="back-center",
                absorber_transverse_mm=500,
                x_mm=0,
                y_mm=0,
                events=250,
                seed_block=block,
                seed1=seed1,
                seed2=seed2,
                geant4_version="11.4.2",
            )
            tasks.append(
                CampaignTask(
                    task_index=index,
                    logical_task_id=logical_id,
                    stage="production",
                    tile_thickness_mm=thickness,
                    sipm_layout="back-center",
                    absorber_transverse_mm=500,
                    x_mm=0,
                    y_mm=0,
                    seed_block=block,
                    events=250,
                    seed1=seed1,
                    seed2=seed2,
                    configuration_hash=configuration_hash,
                )
            )
            index += 1
    return tuple(tasks)


def fixture_source(tasks: tuple[CampaignTask, ...]) -> dict[str, object]:
    return {
        "production_program_directory": "/fixture/production-program",
        "program_id": EXPECTED_PROGRAM_ID,
        "program_hash": EXPECTED_PROGRAM_HASH,
        "authorization_graph_hash": EXPECTED_AUTHORIZATION_GRAPH_HASH,
        "production_program_json_sha256": "1" * 64,
        "production_program_sha256s_sha256": "2" * 64,
        "child_id": "BC-S3",
        "child_plan_hash": "3" * 64,
        "child_plan_json_sha256": "4" * 64,
        "child_task_set_sha256": "5" * 64,
        "child_scan_args_sha256": "6" * 64,
        "source_task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "source_seed_set_hash": seed_set_hash(
            seed for task in tasks for seed in (task.seed1, task.seed2)
        ),
        "accepted_pilot_exclusion_hash": ACCEPTED_PILOT_EXCLUSION_HASH,
    }


def fixture_progression() -> dict[str, object]:
    primary, eligibility, decomposition = fixture_analysis_records()
    metrics = validate_progression_values(
        primary=primary,
        eligibility=eligibility,
        decomposition_rows=decomposition,
    )
    progression: dict[str, object] = {
        "schema_version": "steel-module-direct-progression-v1",
        "predecessor_child": "BC-S2",
        "predecessor_campaign_id": EXPECTED_PREDECESSOR_CAMPAIGN_ID,
        "predecessor_plan_hash": "7" * 64,
        "predecessor_simulation_commit": EXPECTED_PREDECESSOR_SIMULATION_COMMIT,
        "predecessor_attempt_ids": ["fixture-bc-s2-attempt"],
        "predecessor_slurm_job_ids": [EXPECTED_PREDECESSOR_JOB_ID],
        "predecessor_finalized_sha256s_sha256": "8" * 64,
        "predecessor_analysis_sha256s_sha256": "9" * 64,
        "predecessor_analysis_config_sha256": "a" * 64,
        "metrics": metrics,
        "tail_disposition": "no-material-worsening",
        "decision": "continue",
        "next_child": "BC-S3",
        "decision_date": "2026-07-21",
        "decision_document": {
            "path": "docs/decisions/steel-module-direct-bc-s1-execution-v1.md",
            "sha256": "b" * 64,
        },
        "automatic_submission": False,
    }
    progression["progression_hash"] = sha256_bytes(canonical_json(progression))
    return progression


def fixture_analysis_records() -> tuple[
    dict[str, object], dict[str, object], list[dict[str, object]]
]:
    primary = {
        "schema_version": (
            "steel-module-direct-bc-s2-cumulative-analysis-v1-primary-v1"
        ),
        "state_id": "BC-ONLY-S2",
        "metric": "observed-net-sipm-response",
        "ratio_direction": "24-mm-over-4-mm",
        "reference_events": 10_000,
        "compared_events": 10_000,
        "valid_resamples": 10_000,
        "leave_one_block_out_evaluations": 80,
        "maximum_loo_tile_thickness_mm": 4,
        "maximum_loo_seed_block": 34,
        "interval_narrowing_vs_bc_s1": True,
        **EXPECTED_PRIMARY,
    }
    eligibility = {
        "schema_version": (
            "steel-module-direct-bc-s2-cumulative-analysis-v1-"
            "numeric-eligibility-v1"
        ),
        "state_id": "BC-ONLY-S2",
        "valid_evidence": True,
        "branch": "continue-eligible",
        "numeric_eligible_decisions": ["continue", "pause-review"],
        "next_child_if_continue": "BC-S3",
        "automatic_decision": False,
        "automatic_submission": False,
        "human_tail_disposition_required": True,
    }
    decomposition = [
        {
            "sample_id": "cumulative",
            "metric": "net",
            "ratio_24_over_4": EXPECTED_PRIMARY["ratio"],
        }
    ]
    return primary, eligibility, decomposition


def predecessor_binding_check(
    scratch: Path,
    checkout: Path,
    environment: dict[str, object],
    git: dict[str, object],
) -> None:
    predecessor_dir = scratch / "predecessor"
    predecessor = write_direct_bc_s2_campaign_atomic(
        predecessor_dir,
        tasks=bc_s2_fixture_tasks(),
        environment=environment,
        source=bc_s2_fixture_source(bc_s2_fixture_tasks()),
        progression=bc_s2_fixture_progression(),
        git=git,
        created_at_utc="2026-07-21T00:00:00+00:00",
    )
    finalized = predecessor_dir / "finalized"
    finalized.mkdir()
    (finalized / "task_index.tsv").write_text("fixture\n", encoding="utf-8")
    (finalized / "configuration_summary.csv").write_text(
        "fixture\n", encoding="utf-8"
    )
    (finalized / "event_audit.json").write_text("{}\n", encoding="utf-8")
    (finalized / "analysis_config.json").write_text("{}\n", encoding="utf-8")
    (finalized / "validation_report.json").write_text(
        json.dumps(
            {
                "accepted_statistical_evidence": True,
                "expected_tasks": 48,
                "selected_tasks": 48,
                "event_audit_integrated": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_recursive_checksums(finalized)

    analysis = finalized / "direct-cumulative-analysis"
    analysis.mkdir()
    primary, eligibility, decomposition = fixture_analysis_records()
    (analysis / "primary_contrast.json").write_text(
        json.dumps(primary, indent=2) + "\n", encoding="utf-8"
    )
    (analysis / "numeric_eligibility.json").write_text(
        json.dumps(eligibility, indent=2) + "\n", encoding="utf-8"
    )
    (analysis / "analysis_config.json").write_text(
        json.dumps(
            {
                "schema_version": generator.ANALYSIS_SCHEMA_VERSION,
                "route": "ordinary-slurm-array-no-preflight",
                "state_id": "BC-ONLY-S2",
                "included_children": ["BC-S1", "BC-S2"],
                "task_count": 80,
                "event_count": 20_000,
                "events_per_endpoint": 10_000,
                "accepted_statistical_evidence": True,
                "analysis_git": {"commit": "f" * 40},
                "sources": {
                    "BC-S2": {
                        "campaign_id": predecessor.campaign_id,
                        "plan_hash": predecessor.plan_hash,
                        "simulation_commit": predecessor.git_commit,
                        "task_count": 48,
                        "event_count": 12_000,
                        "attempt_ids": ["fixture-attempt"],
                        "slurm_job_ids": ["fixture-job"],
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with (analysis / "pathway_decomposition.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        stream.write("sample_id,metric,ratio_24_over_4\n")
        for row in decomposition:
            stream.write(
                f"{row['sample_id']},{row['metric']},{row['ratio_24_over_4']}\n"
            )
    for name in generator.ANALYSIS_REQUIRED_FILES:
        path = analysis / name
        if not path.exists():
            path.write_text(f"fixture {name}\n", encoding="utf-8")
    write_recursive_checksums(analysis)

    decision_path = checkout / generator.DECISION_DOCUMENT
    decision_path.parent.mkdir(parents=True)
    decision_path.write_text("accepted fixture decision\n", encoding="utf-8")

    original = (
        generator.EXPECTED_PREDECESSOR_CAMPAIGN_ID,
        generator.EXPECTED_PREDECESSOR_SIMULATION_COMMIT,
        generator.EXPECTED_PREDECESSOR_JOB_ID,
        generator.EXPECTED_ANALYSIS_COMMIT,
    )
    generator.EXPECTED_PREDECESSOR_CAMPAIGN_ID = predecessor.campaign_id
    generator.EXPECTED_PREDECESSOR_SIMULATION_COMMIT = predecessor.git_commit
    generator.EXPECTED_PREDECESSOR_JOB_ID = "fixture-job"
    generator.EXPECTED_ANALYSIS_COMMIT = "f" * 40
    try:
        loaded, progression = generator.load_bc_s2_progression(
            predecessor_dir, repo_root=checkout
        )
        assert loaded.campaign_id == predecessor.campaign_id
        assert progression["predecessor_attempt_ids"] == ["fixture-attempt"]
        assert progression["predecessor_slurm_job_ids"] == ["fixture-job"]
        assert progression["next_child"] == "BC-S3"

        config_path = analysis / "analysis_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["analysis_git"]["commit"] = "e" * 40
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        write_recursive_checksums(analysis)
        try:
            generator.load_bc_s2_progression(predecessor_dir, repo_root=checkout)
        except ValueError as exc:
            assert "provenance" in str(exc)
        else:
            raise AssertionError("changed cumulative analysis identity was accepted")
    finally:
        (
            generator.EXPECTED_PREDECESSOR_CAMPAIGN_ID,
            generator.EXPECTED_PREDECESSOR_SIMULATION_COMMIT,
            generator.EXPECTED_PREDECESSOR_JOB_ID,
            generator.EXPECTED_ANALYSIS_COMMIT,
        ) = original


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-direct-bc-s3-") as raw:
        scratch = Path(raw)
        checkout = scratch / "checkout"
        git = fixture_git_checkout(checkout)
        tasks = fixture_tasks()
        environment = fixture_environment(scratch)
        source = fixture_source(tasks)
        progression = fixture_progression()
        campaign_dir = scratch / "campaign"
        bundle = write_direct_bc_s3_campaign_atomic(
            campaign_dir,
            tasks=tasks,
            environment=environment,
            source=source,
            progression=progression,
            git=git,
            created_at_utc="2026-07-21T00:00:00+00:00",
        )
        validate_direct_bc_s3_bundle(bundle)
        assert load_campaign(campaign_dir).campaign_id == bundle.campaign_id

        scheduler_marker = scratch / "scheduler-contacted"
        forbidden = scratch / "forbidden-sbatch"
        forbidden.write_text(
            f"#!/usr/bin/env sh\ntouch {shlex.quote(str(scheduler_marker))}\nexit 99\n",
            encoding="utf-8",
        )
        forbidden.chmod(0o700)
        checked = run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(campaign_dir),
                "--project-root",
                str(checkout),
                "--sbatch-command",
                str(forbidden),
                "--check-only",
            ],
            cwd=repo_root,
        )
        assert "Tasks: 80 total, 0 submitted, 0 complete" in checked.stdout
        assert "Campaign validation passed; no Slurm job was submitted." in checked.stdout
        assert not scheduler_marker.exists()
        assert not (campaign_dir / "attempts").exists()

        fake_squeue = scratch / "fake-squeue"
        fake_squeue.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        fake_squeue.chmod(0o700)
        submitted_command = scratch / "submitted-command.txt"
        fake_sbatch = scratch / "fake-sbatch"
        fake_sbatch.write_text(
            "#!/usr/bin/env sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(submitted_command))}\n"
            "printf '777777\\n'\n",
            encoding="utf-8",
        )
        fake_sbatch.chmod(0o700)
        data_root = scratch / "g4-data"
        data_root.mkdir()
        submitted = run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(campaign_dir),
                "--project-root",
                str(checkout),
                "--account",
                "PAS2524",
                "--g4-data-root",
                str(data_root),
                "--frozen-root",
                str(scratch / "frozen"),
                "--sbatch-command",
                str(fake_sbatch),
                "--squeue-command",
                str(fake_squeue),
            ],
            cwd=repo_root,
        )
        assert "Submitted Slurm array 777777 with 80 logical tasks." in submitted.stdout
        command = submitted_command.read_text(encoding="utf-8").splitlines()
        assert command[command.index("--array") + 1] == "1-80"
        assert "--hold" not in command
        assert not any("preflight" in value or "readiness" in value for value in command)

        bad_tasks = list(tasks)
        bad_tasks[0] = replace(bad_tasks[0], seed_block=39)
        try:
            validate_direct_bc_s3_tasks(bad_tasks, geant4_version="11.4.2")
        except ValueError:
            pass
        else:
            raise AssertionError("wrong direct BC-S3 block range was accepted")

        bad_primary = {
            "schema_version": (
                "steel-module-direct-bc-s2-cumulative-analysis-v1-primary-v1"
            ),
            "state_id": "BC-ONLY-S2",
            "metric": "observed-net-sipm-response",
            "ratio_direction": "24-mm-over-4-mm",
            "reference_events": 10_000,
            "compared_events": 10_000,
            "valid_resamples": 10_000,
            "leave_one_block_out_evaluations": 80,
            "maximum_loo_tile_thickness_mm": 4,
            "maximum_loo_seed_block": 34,
            "interval_narrowing_vs_bc_s1": True,
            **EXPECTED_PRIMARY,
            "ratio": float(EXPECTED_PRIMARY["ratio"]) + 0.1,
        }
        try:
            validate_progression_values(
                primary=bad_primary,
                eligibility={
                    "schema_version": (
                        "steel-module-direct-bc-s2-cumulative-analysis-v1-"
                        "numeric-eligibility-v1"
                    ),
                    "state_id": "BC-ONLY-S2",
                    "valid_evidence": True,
                    "branch": "continue-eligible",
                    "numeric_eligible_decisions": ["continue", "pause-review"],
                    "next_child_if_continue": "BC-S3",
                    "automatic_decision": False,
                    "automatic_submission": False,
                    "human_tail_disposition_required": True,
                },
                decomposition_rows=[
                    {
                        "sample_id": "cumulative",
                        "metric": "net",
                        "ratio_24_over_4": bad_primary["ratio"],
                    }
                ],
            )
        except ValueError as exc:
            assert "reviewed evidence" in str(exc)
        else:
            raise AssertionError("changed BC-S2 primary result was accepted")

        tampered = scratch / "tampered"
        shutil.copytree(campaign_dir, tampered)
        manifest_path = tampered / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["direct_production"]["progression"]["tail_disposition"] = (
            "material-worsening"
        )
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        rejected = run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(tampered),
                "--check-only",
            ],
            cwd=repo_root,
            expect_success=False,
        )
        assert "progression decision is invalid" in rejected.stdout

        try:
            write_direct_bc_s3_campaign_atomic(
                campaign_dir,
                tasks=tasks,
                environment=environment,
                source=source,
                progression=progression,
                git=git,
            )
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError("existing direct BC-S3 campaign target was overwritten")

        predecessor_binding_check(scratch, checkout, environment, git)

    print(
        "steel-module direct BC-S3: PASS "
        "(80 tasks, 20000 events, reviewed continue, ordinary array, no preflight)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
