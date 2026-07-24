#!/usr/bin/env python3
"""Generate an ordinary direct steel-module-stack-v1 campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shlex
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from steel_module_stack_campaign_lib import (
    CAMPAIGN_SCHEMA_VERSION,
    STACK_LAYERS,
    STUDY_PRESET,
    TILE_THICKNESSES_MM,
    CampaignTask,
    canonical_json,
    environment_identity,
    sha256_bytes,
    sha256_file,
    task_configuration_hash,
    verify_checksum_manifest,
)


MAX_GEANT4_SEED = 2_147_483_646
MAX_CUMULATIVE_EVENTS = 120_000
MAX_PRODUCTION_TASKS = 1_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--stage", required=True, choices=("geometry-smoke", "benchmark", "pilot", "production")
    )
    parser.add_argument("--campaign-seed", required=True, type=int)
    parser.add_argument("--events", type=int)
    parser.add_argument("--blocks", type=int)
    parser.add_argument("--sizing-plan", type=Path)
    parser.add_argument("--benchmark-report", type=Path)
    parser.add_argument("--excluded-seeds", type=Path, action="append", default=[])
    parser.add_argument("--description")
    parser.add_argument("--environment-mode", choices=("local-dev", "osc-production"), default="local-dev")
    parser.add_argument("--geant4-version")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--g4-data-manifest", type=Path)
    parser.add_argument("--build-artifact", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    return parser.parse_args()


def git_value(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout.strip()


def artifact_record(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"missing provenance artifact: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def seed_values(path: Path) -> set[int]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"missing excluded-seed registry: {resolved}")
    values: set[int] = set()
    text = resolved.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        def visit(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    visit(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif key in {"seed1", "seed2", "seeds", "excluded_seeds"}:
                try:
                    values.add(int(value))
                except (TypeError, ValueError):
                    pass
        visit(payload)
    else:
        delimiter = "\t" if "\t" in text.partition("\n")[0] else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
        if reader.fieldnames and {"seed1", "seed2"}.issubset(reader.fieldnames):
            for row in reader:
                values.update((int(row["seed1"]), int(row["seed2"])))
        else:
            for token in text.replace(",", " ").split():
                if token.isdigit():
                    values.add(int(token))
    return {value for value in values if 0 < value <= MAX_GEANT4_SEED}


def derive_seed(campaign_seed: int, logical_id: str, slot: int, used: set[int]) -> int:
    nonce = 0
    while True:
        material = f"steel-module-stack-v1|{campaign_seed}|{logical_id}|{slot}|{nonce}".encode()
        candidate = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % MAX_GEANT4_SEED + 1
        if candidate not in used:
            used.add(candidate)
            return candidate
        nonce += 1


def read_sizing_plan(path: Path) -> tuple[int, dict[int, tuple[int, int]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"missing sizing plan: {resolved}")
    try:
        verify_checksum_manifest(resolved.parent, required=(resolved.name,))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid stack sizing checksums: {exc}") from exc
    with resolved.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or value.get("schema_version") != "steel-module-stack-sizing-v1":
        raise SystemExit("unsupported stack sizing plan")
    block_events = value.get("events_per_task")
    rows = value.get("thicknesses")
    if not isinstance(block_events, int) or block_events not in (250, 100, 50, 25, 10):
        raise SystemExit("sizing plan has invalid events_per_task")
    if not isinstance(rows, list):
        raise SystemExit("sizing plan lacks thickness rows")
    shape: dict[int, tuple[int, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit("invalid sizing thickness row")
        thickness = row.get("tile_thickness_mm")
        start = row.get("first_additional_seed_block")
        blocks = row.get("additional_blocks")
        if thickness not in TILE_THICKNESSES_MM or not isinstance(start, int) or not isinstance(blocks, int) or start < 4 or blocks < 0:
            raise SystemExit("invalid sizing task shape")
        shape[thickness] = (start, blocks)
    if set(shape) != set(TILE_THICKNESSES_MM):
        raise SystemExit("sizing plan must contain all six thicknesses")
    if value.get("automatic_acceptance") is not False:
        raise SystemExit("sizing plan must remain a human review aid")
    if value.get("production_plan_permitted_for_human_review") is not True:
        raise SystemExit("sizing plan is not eligible for a one-shot production campaign")
    if (
        not isinstance(value.get("pilot_campaign_id"), str)
        or not value["pilot_campaign_id"]
        or not isinstance(value.get("pilot_plan_hash"), str)
        or len(value["pilot_plan_hash"]) != 64
        or not isinstance(value.get("pilot_git_commit"), str)
        or len(value["pilot_git_commit"]) != 40
        or not isinstance(value.get("pilot_finalized_sha256"), str)
        or len(value["pilot_finalized_sha256"]) != 64
    ):
        raise SystemExit("sizing plan lacks a complete finalized-pilot identity")
    scheduler = value.get("scheduler_contract")
    if (
        not isinstance(scheduler, dict)
        or scheduler.get("nodes") != 1
        or scheduler.get("ntasks") != 1
        or scheduler.get("cpus_per_task") != 1
        or scheduler.get("time_seconds") != 3600
        or not isinstance(scheduler.get("memory_gib"), int)
        or scheduler["memory_gib"] < 2
    ):
        raise SystemExit("sizing plan has an invalid scheduler contract")
    return block_events, shape, value


def read_benchmark_report(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"missing stack benchmark report: {resolved}")
    try:
        recorded = verify_checksum_manifest(resolved.parent, required=(resolved.name,))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid stack benchmark checksums: {exc}") from exc
    if resolved.name not in recorded:
        raise SystemExit("stack benchmark report is not checksum-bound")
    with resolved.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "steel-module-stack-benchmark-v1"
        or value.get("accepted_statistical_evidence") is not True
        or value.get("pilot_permitted_for_human_review") is not True
        or value.get("selected_events_per_task") not in (250, 100, 50, 25, 10)
        or not isinstance(value.get("production_memory_gib"), int)
        or value["production_memory_gib"] < 2
    ):
        raise SystemExit("stack benchmark report does not authorize a pilot shape")
    return value


def stage_shape(args: argparse.Namespace) -> tuple[int, list[tuple[int, int]]]:
    if args.stage == "geometry-smoke":
        if args.events not in (None, 1) or args.blocks not in (None, 1) or args.sizing_plan or args.benchmark_report:
            raise SystemExit("geometry-smoke is fixed at six one-event tasks")
        return 1, [(thickness, 0) for thickness in TILE_THICKNESSES_MM]
    if args.stage == "benchmark":
        if args.events not in (None, 25) or args.blocks not in (None, 1) or args.sizing_plan or args.benchmark_report:
            raise SystemExit("benchmark is fixed at 4/24 mm x 25 events")
        return 25, [(4, 0), (24, 0)]
    if args.stage == "pilot":
        if args.events not in (250, 100, 50, 25, 10):
            raise SystemExit("pilot --events must be one benchmark-selected block size")
        if args.blocks not in (None, 4) or args.sizing_plan or args.benchmark_report is None:
            raise SystemExit("pilot is fixed at four seed blocks per thickness")
        benchmark = read_benchmark_report(args.benchmark_report)
        if benchmark["selected_events_per_task"] != args.events:
            raise SystemExit("pilot --events disagrees with the benchmark-selected block size")
        return args.events, [(thickness, block) for thickness in TILE_THICKNESSES_MM for block in range(4)]
    if args.sizing_plan is None or args.events is not None or args.blocks is not None or args.benchmark_report:
        raise SystemExit("production requires --sizing-plan and forbids --events/--blocks")
    events, shape, _ = read_sizing_plan(args.sizing_plan)
    rows = [
        (thickness, block)
        for thickness in TILE_THICKNESSES_MM
        for block in range(shape[thickness][0], shape[thickness][0] + shape[thickness][1])
    ]
    if not rows:
        raise SystemExit("sizing plan requests no additional production tasks")
    if len(rows) > MAX_PRODUCTION_TASKS:
        raise SystemExit("production plan exceeds the 1,000-task hard cap")
    pilot_events = 6 * 4 * events
    if pilot_events + len(rows) * events > MAX_CUMULATIVE_EVENTS:
        raise SystemExit("pilot plus production exceeds the 120,000-event hard cap")
    return events, rows


def build_tasks(
    *, stage: str, events: int, rows: Iterable[tuple[int, int]], campaign_seed: int,
    geant4_version: str, excluded: set[int]
) -> list[CampaignTask]:
    tasks: list[CampaignTask] = []
    used = set(excluded)
    for index, (thickness, block) in enumerate(rows, start=1):
        logical_id = f"stack-{stage}-t{thickness:02d}-b{block:03d}"
        seed1 = derive_seed(campaign_seed, logical_id, 1, used)
        seed2 = derive_seed(campaign_seed, logical_id, 2, used)
        tasks.append(
            CampaignTask(
                task_index=index,
                logical_task_id=logical_id,
                stage=stage,
                tile_thickness_mm=thickness,
                seed_block=block,
                events=events,
                seed1=seed1,
                seed2=seed2,
                configuration_hash=task_configuration_hash(
                    stage=stage, tile_thickness_mm=thickness, seed_block=block,
                    events=events, seed1=seed1, seed2=seed2,
                    geant4_version=geant4_version,
                ),
            )
        )
    return tasks


def scan_args(task: CampaignTask, campaign_id: str) -> list[str]:
    return [
        "full", "custom", "--study-preset", STUDY_PRESET,
        "--tile-thickness-mm", str(task.tile_thickness_mm),
        "--x-min", "0", "--x-max", "0", "--y-min", "0", "--y-max", "0",
        "--step", "1", "--grid-unit", "mm", "--events", str(task.events),
        "--seed1", str(task.seed1), "--seed2", str(task.seed2),
        "--campaign-id", campaign_id, "--campaign-stage", task.stage,
        "--logical-task-id", task.logical_task_id,
        "--configuration-hash", task.configuration_hash,
        "--seed-block", str(task.seed_block), "--no-root-plots",
    ]


def write_tasks(path: Path, tasks: list[CampaignTask]) -> None:
    fields = list(CampaignTask.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(asdict(task) for task in tasks)


def main() -> int:
    args = parse_args()
    if args.campaign_seed < 0:
        raise SystemExit("--campaign-seed must be non-negative")
    if args.environment_mode == "osc-production" and args.allow_dirty:
        raise SystemExit("--allow-dirty is forbidden for osc-production")
    root = Path(__file__).resolve().parents[2]
    dirty_text = git_value(root, "status", "--porcelain", "--untracked-files=all")
    dirty = bool(dirty_text)
    if dirty and not args.allow_dirty:
        raise SystemExit("refusing to freeze a stack campaign from a dirty checkout")
    geant4_version = args.geant4_version or ("11.4.2" if args.environment_mode == "osc-production" else "11.3.2")
    if args.environment_mode == "osc-production" and geant4_version != "11.4.2":
        raise SystemExit("OSC stack campaigns require Geant4 11.4.2")
    if args.environment_mode == "osc-production" and any(
        value is None for value in (args.image, args.g4_data_manifest, args.build_artifact)
    ):
        raise SystemExit("OSC stack campaigns require image, data manifest, and build artifact")
    environment: dict[str, Any] = {
        "mode": args.environment_mode,
        "geant4_version": geant4_version,
        "accepted_statistical_evidence": args.environment_mode == "osc-production",
        "image": artifact_record(args.image),
        "g4_data_manifest": artifact_record(args.g4_data_manifest),
        "build_artifact": artifact_record(args.build_artifact),
    }
    if args.environment_mode == "osc-production" and args.build_artifact and not (
        args.build_artifact.expanduser().resolve().stat().st_mode & 0o111
    ):
        raise SystemExit("build artifact is not executable")
    environment["identity_hash"] = environment_identity(environment)

    events, rows = stage_shape(args)
    benchmark_report = read_benchmark_report(args.benchmark_report) if args.stage == "pilot" else None
    sizing_value = read_sizing_plan(args.sizing_plan)[2] if args.stage == "production" else None
    memory_gib = (
        int(benchmark_report["production_memory_gib"])
        if benchmark_report is not None
        else int(sizing_value["scheduler_contract"]["memory_gib"])
        if sizing_value is not None
        else 2
    )
    excluded: set[int] = set()
    excluded_records: list[dict[str, str]] = []
    for path in args.excluded_seeds:
        resolved = path.expanduser().resolve()
        values = seed_values(resolved)
        excluded.update(values)
        excluded_records.append({"path": str(resolved), "sha256": sha256_file(resolved), "seed_count": str(len(values))})
    if args.environment_mode == "osc-production" and args.stage in {"pilot", "production"} and not excluded_records:
        raise SystemExit("formal pilot/production requires at least one --excluded-seeds registry")
    tasks = build_tasks(
        stage=args.stage, events=events, rows=rows, campaign_seed=args.campaign_seed,
        geant4_version=geant4_version, excluded=excluded,
    )
    plan_hash = sha256_bytes(canonical_json([asdict(task) for task in tasks]))
    campaign_id = f"sms-v1-{args.stage}-{plan_hash[:12]}"
    out = args.out_dir.expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty campaign: {out}")
    out.mkdir(parents=True, exist_ok=True)
    tasks_path, scan_path, readme_path = out / "tasks.tsv", out / "scan_args.txt", out / "README.md"
    write_tasks(tasks_path, tasks)
    scan_path.write_text(
        "\n".join([
            f"# schema_version={CAMPAIGN_SCHEMA_VERSION}",
            f"# campaign_id={campaign_id}", f"# task_count={len(tasks)}",
            *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
        ]) + "\n", encoding="utf-8"
    )
    readme_path.write_text(
        f"# {campaign_id}\n\n"
        f"{args.description or f'{STUDY_PRESET} {args.stage} campaign'}\n\n"
        f"- Tasks: `{len(tasks)}`; events: `{sum(task.events for task in tasks)}`.\n"
        f"- Ten `[40 mm steel][tile]` modules; six accepted thicknesses.\n"
        f"- Ordinary direct Slurm array; no preflight or managed execution.\n"
        f"- Plan hash: `{plan_hash}`.\n\n"
        "Validate with `python3 hpc/osc/submit_steel_module_stack_campaign.py --campaign-dir PATH --check-only`.\n",
        encoding="utf-8",
    )
    artifacts: dict[str, Any] = {
        "tasks_tsv": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
        "scan_args": {"path": "scan_args.txt", "sha256": sha256_file(scan_path)},
        "readme": {"path": "README.md", "sha256": sha256_file(readme_path)},
    }
    if args.sizing_plan:
        sizing_copy = out / "sizing_plan.json"
        sizing_copy.write_bytes(args.sizing_plan.expanduser().resolve().read_bytes())
        artifacts["sizing_plan"] = {"path": sizing_copy.name, "sha256": sha256_file(sizing_copy)}
    if args.benchmark_report:
        benchmark_copy = out / "benchmark_report.json"
        benchmark_copy.write_bytes(args.benchmark_report.expanduser().resolve().read_bytes())
        artifacts["benchmark_report"] = {"path": benchmark_copy.name, "sha256": sha256_file(benchmark_copy)}
    if excluded_records:
        registry = out / "excluded_seed_registry.json"
        registry.write_text(
            json.dumps(
                {
                    "schema_version": "steel-module-stack-excluded-seeds-v1",
                    "sources": excluded_records,
                    "excluded_seed_count": len(excluded),
                    "excluded_seeds": sorted(excluded),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        artifacts["excluded_seed_registry"] = {"path": registry.name, "sha256": sha256_file(registry)}
    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "study_preset": STUDY_PRESET,
        "stage": args.stage,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_hash": plan_hash,
        "campaign_seed": args.campaign_seed,
        "events_per_task": events,
        "task_count": len(tasks),
        "total_events": sum(task.events for task in tasks),
        "configuration_axes": {"tile_thickness_mm": sorted({task.tile_thickness_mm for task in tasks}), "layer": list(range(STACK_LAYERS)), "local_sensor": [0, 1]},
        "geometry_contract": {"layers": 10, "module_order": ["steel", "tile"], "steel_full_size_mm": [500, 500, 40], "tile_transverse_mm": [100, 100], "tile_gap_mm": 0, "sipm_layout": "edge-two", "sensor_count": 20},
        "source_contract": {"particle": "neutron", "kinetic_energy_mev": 1000, "profile": "point", "direction": [0, 0, -1], "source_z_formula_mm": "5*(40+t)+1.5"},
        "causal_contract": {"origin": "first_tile_layer_with_WLS_inheritance", "primary_collection": "same_origin_to_same_layer_ratio_of_sums", "unknown_origin_accepted": False},
        "hard_caps": {"pilot_plus_production_events": MAX_CUMULATIVE_EVENTS, "production_tasks": MAX_PRODUCTION_TASKS},
        "scheduler_contract": {"nodes": 1, "ntasks": 1, "cpus_per_task": 1, "time_seconds": 3600, "memory_gib": memory_gib},
        "git": {"commit": git_value(root, "rev-parse", "HEAD"), "branch": git_value(root, "branch", "--show-current"), "dirty": dirty, "dirty_paths": dirty_text.splitlines() if dirty else []},
        "environment": environment,
        "artifacts": artifacts,
    }
    (out / "campaign.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Campaign: {campaign_id}")
    print(f"Stage: {args.stage}; tasks: {len(tasks)}; events: {manifest['total_events']}")
    print(f"Output: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
