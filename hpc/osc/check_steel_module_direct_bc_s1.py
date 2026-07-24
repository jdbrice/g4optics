#!/usr/bin/env python3
"""Focused, scheduler-free checks for the direct BC-S1 campaign route."""

from __future__ import annotations

import json
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from generate_steel_module_direct_bc_s1_campaign import (
    validate_direct_bc_s1_bundle,
    validate_direct_bc_s1_tasks,
    write_direct_campaign_atomic,
)
from steel_module_campaign_lib import (
    CampaignTask,
    environment_identity,
    load_campaign,
    sha256_file,
    task_configuration_hash,
)
from submit_steel_module_campaign import reject_managed_production_route
from steel_module_production_program_lib import (
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
        for block in range(16):
            logical_id = (
                f"production-back-center-t{thickness:02d}-a500-b{block:03d}"
            )
            seed1 = 10_000 + 2 * index
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


def fixture_environment(scratch: Path) -> dict[str, object]:
    image = scratch / "geant4.sif"
    data_manifest = scratch / "g4-data-manifest.sha256"
    executable = scratch / "OpNovice2"
    image.write_bytes(b"fixture image\n")
    data_manifest.write_text("fixture data manifest\n", encoding="utf-8")
    executable.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    environment: dict[str, object] = {
        "mode": "osc-production",
        "geant4_version": "11.4.2",
        "accepted_statistical_evidence": True,
        "image": {"path": str(image), "sha256": sha256_file(image)},
        "g4_data_manifest": {
            "path": str(data_manifest),
            "sha256": sha256_file(data_manifest),
        },
        "build_artifact": {
            "path": str(executable),
            "sha256": sha256_file(executable),
        },
    }
    environment["identity_hash"] = environment_identity(environment)
    return environment


def fixture_git_checkout(root: Path) -> dict[str, object]:
    root.mkdir()
    run(["git", "init", "-q"], cwd=root)
    run(["git", "config", "user.name", "Steel Module Fixture"], cwd=root)
    run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root)
    tracked = root / "README.md"
    tracked.write_text("fixture checkout\n", encoding="utf-8")
    batch = root / "hpc" / "osc" / "submit_scan.sbatch"
    batch.parent.mkdir(parents=True)
    batch.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    batch.chmod(batch.stat().st_mode | stat.S_IXUSR)
    run(["git", "add", "."], cwd=root)
    run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
        cwd=root,
    )
    commit = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return {
        "commit": commit,
        "branch": "fixture",
        "dirty": False,
        "dirty_paths": [],
    }


def fixture_source(tasks: tuple[CampaignTask, ...]) -> dict[str, object]:
    digest = "1" * 64
    seeds = [seed for task in tasks for seed in (task.seed1, task.seed2)]
    return {
        "managed_child_directory": "/fixture/steel-module-production-bc-s1",
        "managed_child_campaign_id": "sm-v1-production-bc-s1-fixture",
        "managed_child_plan_hash": "2" * 64,
        "managed_child_binding_sha256": "3" * 64,
        "managed_child_binding_hash": "4" * 64,
        "child_id": "BC-S1",
        "child_plan_hash": "5" * 64,
        "source_task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "source_seed_set_hash": seed_set_hash(seeds),
        "program_id": "sm-v1-production-program-fixture",
        "program_hash": "6" * 64,
        "authorization_graph_hash": "7" * 64,
        "source_tasks_tsv_sha256": "8" * 64,
        "source_scan_args_sha256": digest,
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-module-direct-bc-s1-") as raw:
        scratch = Path(raw)
        fake_checkout = scratch / "checkout"
        git = fixture_git_checkout(fake_checkout)
        tasks = fixture_tasks()
        source = fixture_source(tasks)
        environment = fixture_environment(scratch)
        campaign_dir = scratch / "campaign"
        bundle = write_direct_campaign_atomic(
            campaign_dir,
            tasks=tasks,
            environment=environment,
            source=source,
            git=git,
            created_at_utc="2026-07-20T00:00:00+00:00",
        )
        validate_direct_bc_s1_bundle(bundle)
        assert load_campaign(campaign_dir).campaign_id == bundle.campaign_id
        generated_readme = (campaign_dir / "README.md").read_text(encoding="utf-8")
        assert str(campaign_dir) in generated_readme
        assert ".tmp-" not in generated_readme
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
        assert "Tasks: 32 total, 0 submitted, 0 complete" in checked.stdout
        assert "Campaign validation passed; no Slurm job was submitted." in checked.stdout
        assert not scheduler_marker.exists()
        assert not (campaign_dir / "attempts").exists()
        assert not any(
            "preflight" in path.name.lower() or "readiness" in path.name.lower()
            for path in campaign_dir.rglob("*")
        )

        fake_squeue = scratch / "fake-squeue"
        fake_squeue.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
        fake_squeue.chmod(fake_squeue.stat().st_mode | stat.S_IXUSR)
        submitted_command = scratch / "submitted-command.txt"
        fake_sbatch = scratch / "fake-sbatch"
        fake_sbatch.write_text(
            "#!/usr/bin/env sh\n"
            f"printf '%s\\n' \"$@\" > {shlex.quote(str(submitted_command))}\n"
            "printf '123456\\n'\n",
            encoding="utf-8",
        )
        fake_sbatch.chmod(fake_sbatch.stat().st_mode | stat.S_IXUSR)
        data_root = scratch / "g4-data"
        data_root.mkdir()
        submitted = run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(campaign_dir),
                "--project-root",
                str(fake_checkout),
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
        assert "Submitted Slurm array 123456 with 32 logical tasks." in submitted.stdout
        command = submitted_command.read_text(encoding="utf-8").splitlines()
        assert command[command.index("--array") + 1] == "1-32"
        assert command[command.index("-A") + 1] == "PAS2524"
        assert "--hold" not in command
        assert not any("preflight" in value or "readiness" in value for value in command)
        attempts = list((campaign_dir / "attempts").glob("*/attempt.json"))
        assert len(attempts) == 1
        attempt = json.loads(attempts[0].read_text(encoding="utf-8"))
        assert attempt["status"] == "submitted"
        assert attempt["task_count"] == 32
        assert attempt["slurm_job_id"] == "123456"

        bad_tasks = list(tasks)
        bad_tasks[0] = replace(bad_tasks[0], events=249)
        try:
            validate_direct_bc_s1_tasks(bad_tasks, geant4_version="11.4.2")
        except ValueError:
            pass
        else:
            raise AssertionError("wrong direct BC-S1 event shape was accepted")

        tampered = scratch / "tampered"
        shutil.copytree(campaign_dir, tampered)
        manifest_path = tampered / "campaign.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["direct_production"]["route"] = "held-preflight"
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
        assert "ordinary no-preflight route" in rejected.stdout

        try:
            write_direct_campaign_atomic(
                campaign_dir,
                tasks=tasks,
                environment=environment,
                source=source,
                git=git,
            )
        except (OSError, ValueError):
            pass
        else:
            raise AssertionError("existing direct campaign target was overwritten")

    print(
        "steel-module direct BC-S1: PASS "
        "(32 tasks, 8000 events, ordinary array, no preflight)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
