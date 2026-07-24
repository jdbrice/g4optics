#!/usr/bin/env python3
"""Focused scheduler-free checks for the one-array direct FIXED route."""

from __future__ import annotations

import copy
import shlex
import stat
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import generate_steel_module_direct_fixed_campaign as generator
from check_steel_module_direct_bc_s1 import fixture_environment, fixture_git_checkout
from generate_steel_module_direct_fixed_campaign import (
    EXPECTED_ANALYSIS_COMMIT,
    EXPECTED_AUTHORIZATION_GRAPH_HASH,
    EXPECTED_PREDECESSOR_CAMPAIGN_ID,
    EXPECTED_PREDECESSOR_JOB_ID,
    EXPECTED_PROGRAM_HASH,
    EXPECTED_PROGRAM_ID,
    OSC_ARRAY_CONCURRENCY_LIMIT,
    validate_direct_fixed_bundle,
    validate_direct_fixed_tasks,
    validate_decision_document_text,
    validate_stop_success_values,
    write_direct_fixed_campaign_atomic,
)
from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    load_campaign,
    sha256_bytes,
    task_configuration_hash,
)
from steel_module_production_program_lib import seed_pair_registry_hash, seed_set_hash
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
    for (layout, thickness), blocks in generator.EXPECTED_BLOCKS.items():
        for block in blocks:
            logical_id = (
                f"production-l{layout}-t{thickness:02d}-a500-b{block:03d}"
            )
            seed1 = 200_000 + 2 * index
            seed2 = seed1 + 1
            configuration_hash = task_configuration_hash(
                stage="production",
                tile_thickness_mm=thickness,
                sipm_layout=layout,
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
                    sipm_layout=layout,
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
        "child_id": "FIXED",
        "child_plan_hash": "3" * 64,
        "child_plan_json_sha256": "4" * 64,
        "child_task_set_sha256": "5" * 64,
        "child_scan_args_sha256": "6" * 64,
        "source_task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "source_seed_set_hash": seed_set_hash(
            seed for task in tasks for seed in (task.seed1, task.seed2)
        ),
        "accepted_pilot_exclusion_hash": generator.ACCEPTED_PILOT_EXCLUSION_HASH,
    }


def fixture_analysis_records() -> tuple[
    dict[str, object], dict[str, object], list[dict[str, object]]
]:
    generated = 5.67082
    collection = 0.245724
    ratio = generated * collection
    primary = {
        "schema_version": generator.PRIMARY_SCHEMA_VERSION,
        "state_id": "BC-ONLY-S4",
        "metric": "observed-net-sipm-response",
        "ratio_direction": "24-mm-over-4-mm",
        "reference_events": 40_250,
        "compared_events": 40_250,
        "valid_resamples": 10_000,
        "leave_one_block_out_evaluations": 322,
        "maximum_loo_tile_thickness_mm": 4,
        "maximum_loo_seed_block": 34,
        "maximum_loo_logical_task_id": "production-lback-center-t04-a500-b034",
        "interval_narrowing_vs_bc_s3": True,
        "hard_ceiling": True,
        "ratio": ratio,
        "ci95_low": 1.29104,
        "ci95_high": 1.50771,
        "relative_half_width": 0.0777,
        "max_leave_one_block_out_relative_shift": 0.0089,
        "ci_excludes_unity": True,
    }
    eligibility = {
        "schema_version": generator.ELIGIBILITY_SCHEMA_VERSION,
        "state_id": "BC-ONLY-S4",
        "valid_evidence": True,
        "branch": "stop-success-eligible",
        "numeric_eligible_decisions": ["stop-success", "pause-review"],
        "hard_ceiling": True,
        "continue_suppressed_by_hard_ceiling": True,
        "next_child_if_continue": None,
        "automatic_decision": False,
        "automatic_submission": False,
        "human_tail_disposition_required": True,
    }
    decomposition = [
        {
            "sample_id": "cumulative",
            "metric": metric,
            "ratio_24_over_4": value,
        }
        for metric, value in (
            ("generated", generated),
            ("scintillation", generated),
            ("collection", collection),
            ("net", ratio),
        )
    ]
    return primary, eligibility, decomposition


def fixture_authorization() -> dict[str, object]:
    primary, eligibility, decomposition = fixture_analysis_records()
    metrics = validate_stop_success_values(
        primary=primary,
        eligibility=eligibility,
        decomposition_rows=decomposition,
    )
    authorization: dict[str, object] = {
        "schema_version": "steel-module-direct-fixed-authorization-v1",
        "predecessor_child": "BC-S4",
        "predecessor_campaign_id": EXPECTED_PREDECESSOR_CAMPAIGN_ID,
        "predecessor_plan_hash": "7" * 64,
        "predecessor_simulation_commit": "8" * 40,
        "predecessor_attempt_ids": ["fixture-bc-s4-attempt"],
        "predecessor_slurm_job_ids": [EXPECTED_PREDECESSOR_JOB_ID],
        "predecessor_finalized_sha256s_sha256": "9" * 64,
        "predecessor_analysis_sha256s_sha256": "a" * 64,
        "predecessor_analysis_config_sha256": "b" * 64,
        "metrics": metrics,
        "tail_disposition": "no-material-worsening",
        "decision": "stop-success",
        "authorized_child": "FIXED",
        "next_bc_child": None,
        "decision_date": "2026-07-21",
        "decision_document": {
            "path": generator.DECISION_DOCUMENT.as_posix(),
            "sha256": "c" * 64,
        },
        "submission_policy": {
            "route": generator.DIRECT_ROUTE,
            "array_spec": "1-592",
            "task_count": 592,
            "event_count": 148_000,
            "osc_array_concurrency_limit": OSC_ARRAY_CONCURRENCY_LIMIT,
            "preflight_required": False,
            "planned_partitioning": "none",
            "failed_elements_may_retry": True,
        },
        "automatic_submission": False,
    }
    authorization["authorization_hash"] = sha256_bytes(canonical_json(authorization))
    return authorization


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    decision_text = (repo_root / generator.DECISION_DOCUMENT).read_text(
        encoding="utf-8"
    )
    validate_decision_document_text(decision_text)
    try:
        validate_decision_document_text(
            decision_text.replace("`1-592` Slurm array", "`1-591` Slurm array")
        )
    except ValueError as exc:
        assert "accepted FIXED policy" in str(exc)
    else:
        raise AssertionError("tampered FIXED array policy was accepted")

    tasks = fixture_tasks()
    assert len(tasks) == 592
    validate_direct_fixed_tasks(tasks, geant4_version="11.4.2")
    assert sum(task.events for task in tasks) == 148_000
    assert len({task.configuration_hash for task in tasks}) == 592
    assert len({(task.sipm_layout, task.tile_thickness_mm) for task in tasks}) == 16
    assert EXPECTED_ANALYSIS_COMMIT == "9a74465aea6ee8bb482081694b28314918ee9cee"

    with tempfile.TemporaryDirectory(prefix="steel-module-direct-fixed-") as raw:
        scratch = Path(raw)
        fake_checkout = scratch / "checkout"
        git = fixture_git_checkout(fake_checkout)
        environment = fixture_environment(scratch)
        source = fixture_source(tasks)
        authorization = fixture_authorization()
        campaign_dir = scratch / "campaign"
        bundle = write_direct_fixed_campaign_atomic(
            campaign_dir,
            tasks=tasks,
            environment=environment,
            source=source,
            authorization=authorization,
            git=git,
            created_at_utc="2026-07-21T00:00:00+00:00",
        )
        validate_direct_fixed_bundle(bundle)
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
        forbidden.chmod(forbidden.stat().st_mode | stat.S_IXUSR)
        checked = run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(campaign_dir),
                "--project-root",
                str(fake_checkout),
                "--sbatch-command",
                str(forbidden),
                "--check-only",
            ],
            cwd=repo_root,
        )
        assert "Tasks: 592 total, 0 submitted, 0 complete" in checked.stdout
        assert "Campaign validation passed; no Slurm job was submitted." in checked.stdout
        assert not scheduler_marker.exists()
        assert not (campaign_dir / "attempts").exists()

        overwrite = write_direct_fixed_campaign_atomic
        try:
            overwrite(
                campaign_dir,
                tasks=tasks,
                environment=environment,
                source=source,
                authorization=authorization,
                git=git,
            )
        except ValueError as exc:
            assert "refusing to overwrite" in str(exc)
        else:
            raise AssertionError("existing direct FIXED campaign was overwritten")

        bad_tasks = list(tasks)
        bad_tasks.pop()
        try:
            validate_direct_fixed_tasks(bad_tasks, geant4_version="11.4.2")
        except ValueError as exc:
            assert "592 tasks" in str(exc)
        else:
            raise AssertionError("591-task FIXED plan was accepted")

        overlapping = list(tasks)
        overlapping[1] = replace(overlapping[1], seed1=overlapping[0].seed1)
        try:
            validate_direct_fixed_tasks(overlapping, geant4_version="11.4.2")
        except ValueError as exc:
            assert "unique production seeds" in str(exc)
        else:
            raise AssertionError("duplicate FIXED production seed was accepted")

        tampered_manifest = copy.deepcopy(bundle.manifest)
        tampered_manifest["direct_production"]["osc_array_concurrency_limit"] = 591
        try:
            validate_direct_fixed_bundle(replace(bundle, manifest=tampered_manifest))
        except ValueError as exc:
            assert "marker identity" in str(exc)
        else:
            raise AssertionError("undersized array concurrency limit was accepted")

        bad_authorization = fixture_authorization()
        bad_authorization["decision"] = "continue"
        bad_authorization["authorization_hash"] = sha256_bytes(
            canonical_json(
                {
                    key: value
                    for key, value in bad_authorization.items()
                    if key != "authorization_hash"
                }
            )
        )
        try:
            write_direct_fixed_campaign_atomic(
                scratch / "bad-authorization",
                tasks=tasks,
                environment=environment,
                source=source,
                authorization=bad_authorization,
                git=git,
            )
        except ValueError as exc:
            assert "authorization" in str(exc)
        else:
            raise AssertionError("non-stop FIXED authorization was accepted")

    print(
        "steel-module direct FIXED: PASS "
        "(592 tasks, 148000 events, 16 configurations, one ordinary array, "
        "stop-success binding, check-only no scheduler contact)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
