#!/usr/bin/env python3
"""Focused, scheduler-free checks for the direct BC-S2 campaign route."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from check_steel_module_direct_bc_s1 import (
    fixture_environment,
    fixture_git_checkout,
)
from generate_steel_module_direct_bc_s2_campaign import (
    EXPECTED_AUTHORIZATION_GRAPH_HASH,
    EXPECTED_PREDECESSOR_ATTEMPT_ID,
    EXPECTED_PREDECESSOR_CAMPAIGN_ID,
    EXPECTED_PREDECESSOR_JOB_ID,
    EXPECTED_PREDECESSOR_SIMULATION_COMMIT,
    EXPECTED_PROGRAM_HASH,
    EXPECTED_PROGRAM_ID,
    validate_direct_bc_s2_bundle,
    validate_direct_bc_s2_tasks,
    validate_progression_values,
    write_direct_bc_s2_campaign_atomic,
)
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
from submit_steel_module_campaign import reject_managed_production_route


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
        for block in range(16, 40):
            logical_id = (
                f"production-lback-center-t{thickness:02d}-a500-b{block:03d}"
            )
            seed1 = 50_000 + 2 * index
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
        "child_id": "BC-S2",
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
    primary = {
        "schema_version": "steel-module-direct-bc-s1-analysis-v1-primary-v1",
        "source_route": "ordinary-slurm-array-no-preflight",
        "ratio_direction": "24-mm-over-4-mm",
        "reference_events": 4000,
        "compared_events": 4000,
        "leave_one_block_out_evaluations": 32,
        "ratio": 1.64567,
        "ci95_low": 1.27719,
        "ci95_high": 2.12547,
        "relative_half_width": 0.2577,
        "max_leave_one_block_out_relative_shift": 0.0503,
        "interval_narrowing": True,
    }
    eligibility = {
        "schema_version": (
            "steel-module-direct-bc-s1-analysis-v1-numeric-eligibility-v1"
        ),
        "source_route": "ordinary-slurm-array-no-preflight",
        "valid_evidence": True,
        "branch": "continue-eligible",
        "numeric_eligible_decisions": ["continue", "pause-review"],
        "automatic_decision": False,
        "automatic_submission": False,
        "human_tail_disposition_required": True,
    }
    pathway = {"observed_net_ratio_24_over_4": 1.64567}
    metrics = validate_progression_values(
        primary=primary, eligibility=eligibility, pathway=pathway
    )
    progression: dict[str, object] = {
        "schema_version": "steel-module-direct-progression-v1",
        "predecessor_child": "BC-S1",
        "predecessor_campaign_id": EXPECTED_PREDECESSOR_CAMPAIGN_ID,
        "predecessor_plan_hash": "7" * 64,
        "predecessor_simulation_commit": EXPECTED_PREDECESSOR_SIMULATION_COMMIT,
        "predecessor_attempt_ids": [EXPECTED_PREDECESSOR_ATTEMPT_ID],
        "predecessor_slurm_job_ids": [EXPECTED_PREDECESSOR_JOB_ID],
        "predecessor_finalized_sha256s_sha256": "8" * 64,
        "predecessor_analysis_sha256s_sha256": "9" * 64,
        "predecessor_analysis_config_sha256": "a" * 64,
        "metrics": metrics,
        "tail_disposition": "no-material-worsening",
        "decision": "continue",
        "next_child": "BC-S2",
        "decision_date": "2026-07-21",
        "decision_document": {
            "path": "docs/decisions/steel-module-direct-bc-s1-execution-v1.md",
            "sha256": "b" * 64,
        },
        "automatic_submission": False,
    }
    progression["progression_hash"] = sha256_bytes(canonical_json(progression))
    return progression


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-direct-bc-s2-") as raw:
        scratch = Path(raw)
        checkout = scratch / "checkout"
        git = fixture_git_checkout(checkout)
        tasks = fixture_tasks()
        environment = fixture_environment(scratch)
        source = fixture_source(tasks)
        progression = fixture_progression()
        campaign_dir = scratch / "campaign"
        bundle = write_direct_bc_s2_campaign_atomic(
            campaign_dir,
            tasks=tasks,
            environment=environment,
            source=source,
            progression=progression,
            git=git,
            created_at_utc="2026-07-21T00:00:00+00:00",
        )
        validate_direct_bc_s2_bundle(bundle)
        assert load_campaign(campaign_dir).campaign_id == bundle.campaign_id
        reject_managed_production_route(
            ["--campaign-dir", str(campaign_dir), "--check-only"]
        )

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
        assert "Tasks: 48 total, 0 submitted, 0 complete" in checked.stdout
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
            "printf '654321\\n'\n",
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
        assert "Submitted Slurm array 654321 with 48 logical tasks." in submitted.stdout
        command = submitted_command.read_text(encoding="utf-8").splitlines()
        assert command[command.index("--array") + 1] == "1-48"
        assert "--hold" not in command
        assert not any("preflight" in value or "readiness" in value for value in command)

        bad_tasks = list(tasks)
        bad_tasks[0] = replace(bad_tasks[0], seed_block=15)
        try:
            validate_direct_bc_s2_tasks(bad_tasks, geant4_version="11.4.2")
        except ValueError:
            pass
        else:
            raise AssertionError("wrong direct BC-S2 block range was accepted")

        tampered = scratch / "tampered"
        shutil.copytree(campaign_dir, tampered)
        manifest_path = tampered / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["direct_production"]["progression"]["decision"] = "pause-review"
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
            write_direct_bc_s2_campaign_atomic(
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
            raise AssertionError("existing direct BC-S2 campaign was overwritten")

    print(
        "steel-module direct BC-S2: PASS "
        "(48 tasks, 12000 events, reviewed continue, ordinary array, no preflight)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
