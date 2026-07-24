#!/usr/bin/env python3
"""Generate immutable steel-module-scan-v1 campaign manifests for OSC arrays."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shlex
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from steel_module_campaign_lib import (
    ABSORBER_SIZES_MM,
    CAMPAIGN_SCHEMA_VERSION,
    SIPM_LAYOUTS,
    STUDY_PRESET,
    TILE_THICKNESSES_MM,
    CampaignTask,
    canonical_json,
    environment_identity,
    sha256_bytes,
    sha256_file,
    task_configuration_hash,
)


MAX_GEANT4_SEED = 2_147_483_646
BENCHMARK_CONFIGURATIONS = (
    (4, "back-center", 500),
    (24, "back-center", 500),
    (24, "edge-center", 500),
    (24, "back-four", 500),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--stage",
        required=True,
        choices=("geometry-smoke", "benchmark", "convergence-pilot", "production"),
    )
    parser.add_argument("--campaign-seed", required=True, type=int)
    parser.add_argument("--events", type=int)
    parser.add_argument("--blocks", type=int)
    parser.add_argument(
        "--configurations-tsv",
        type=Path,
        help=(
            "Required only for convergence-pilot. TSV columns: "
            "tile_thickness_mm, sipm_layout, absorber_transverse_mm."
        ),
    )
    parser.add_argument("--description")
    parser.add_argument(
        "--environment-mode",
        choices=("local-dev", "osc-production"),
        default="local-dev",
    )
    parser.add_argument("--geant4-version")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--g4-data-manifest", type=Path)
    parser.add_argument("--build-artifact", type=Path)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development-only escape hatch; forbidden for osc-production.",
    )
    return parser.parse_args()


def run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def artifact_record(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"Missing provenance artifact: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def resolve_stage_shape(args: argparse.Namespace) -> tuple[int, int]:
    if args.stage == "geometry-smoke":
        events = 1 if args.events is None else args.events
        blocks = 1 if args.blocks is None else args.blocks
        if events != 1 or blocks != 1:
            raise SystemExit("geometry-smoke is fixed at one event and one seed block")
    elif args.stage == "benchmark":
        if args.events is None:
            raise SystemExit(
                "benchmark requires --events after the geometry smoke is reviewed"
            )
        events = args.events
        blocks = 1 if args.blocks is None else args.blocks
        if blocks != 1:
            raise SystemExit("benchmark has exactly one seed block")
    else:
        if args.events is None or args.blocks is None:
            raise SystemExit(
                f"{args.stage} requires explicit --events and --blocks after its prior gate"
            )
        events = args.events
        blocks = args.blocks
        if blocks < 4:
            raise SystemExit(f"{args.stage} requires at least four independent seed blocks")

    if events <= 0:
        raise SystemExit("--events must be positive")
    return events, blocks


def read_pilot_configurations(path: Path) -> tuple[tuple[int, str, int], ...]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"Missing --configurations-tsv: {resolved}")
    with resolved.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    expected_fields = [
        "tile_thickness_mm",
        "sipm_layout",
        "absorber_transverse_mm",
    ]
    if list(reader.fieldnames or []) != expected_fields:
        raise SystemExit(
            "--configurations-tsv must have exactly this header: "
            + "\t".join(expected_fields)
        )
    configurations: list[tuple[int, str, int]] = []
    for number, row in enumerate(rows, start=2):
        try:
            thickness = int(row["tile_thickness_mm"])
            layout = row["sipm_layout"]
            absorber = int(row["absorber_transverse_mm"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"Invalid convergence configuration on TSV line {number}: {row}"
            ) from exc
        if thickness not in TILE_THICKNESSES_MM:
            raise SystemExit(
                f"Invalid tile_thickness_mm on TSV line {number}: {thickness}"
            )
        if layout not in SIPM_LAYOUTS:
            raise SystemExit(f"Invalid sipm_layout on TSV line {number}: {layout}")
        if absorber not in ABSORBER_SIZES_MM:
            raise SystemExit(
                f"Invalid absorber_transverse_mm on TSV line {number}: {absorber}"
            )
        configurations.append((thickness, layout, absorber))
    if not configurations:
        raise SystemExit("--configurations-tsv contains no configurations")
    if len(set(configurations)) != len(configurations):
        raise SystemExit("--configurations-tsv contains duplicate configurations")
    return tuple(configurations)


def stage_configurations(
    stage: str,
    *,
    pilot_configurations: tuple[tuple[int, str, int], ...] = (),
) -> Iterable[tuple[int, str, int]]:
    if stage == "geometry-smoke":
        for layout in SIPM_LAYOUTS:
            for thickness in TILE_THICKNESSES_MM:
                yield thickness, layout, 500
    elif stage == "benchmark":
        yield from BENCHMARK_CONFIGURATIONS
    elif stage == "convergence-pilot":
        yield from pilot_configurations
    elif stage == "production":
        for layout in SIPM_LAYOUTS:
            for thickness in TILE_THICKNESSES_MM:
                yield thickness, layout, 500
    else:  # pragma: no cover - argparse restricts this
        raise ValueError(stage)


def derive_unique_seed(
    *, campaign_seed: int, logical_task_id: str, slot: int, used: set[int]
) -> int:
    nonce = 0
    while True:
        material = f"{campaign_seed}|{logical_task_id}|{slot}|{nonce}".encode()
        candidate = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        candidate = candidate % MAX_GEANT4_SEED + 1
        if candidate not in used:
            used.add(candidate)
            return candidate
        nonce += 1


def build_tasks(
    *,
    stage: str,
    events: int,
    blocks: int,
    campaign_seed: int,
    geant4_version: str,
    pilot_configurations: tuple[tuple[int, str, int], ...] = (),
) -> list[CampaignTask]:
    tasks: list[CampaignTask] = []
    used_seeds: set[int] = set()
    task_index = 1
    for thickness, layout, absorber in stage_configurations(
        stage, pilot_configurations=pilot_configurations
    ):
        for block in range(blocks):
            logical_task_id = (
                f"{stage}-l{layout}-t{thickness:02d}-a{absorber:03d}-b{block:02d}"
            )
            seed1 = derive_unique_seed(
                campaign_seed=campaign_seed,
                logical_task_id=logical_task_id,
                slot=1,
                used=used_seeds,
            )
            seed2 = derive_unique_seed(
                campaign_seed=campaign_seed,
                logical_task_id=logical_task_id,
                slot=2,
                used=used_seeds,
            )
            configuration_hash = task_configuration_hash(
                stage=stage,
                tile_thickness_mm=thickness,
                sipm_layout=layout,
                absorber_transverse_mm=absorber,
                x_mm=0,
                y_mm=0,
                events=events,
                seed_block=block,
                seed1=seed1,
                seed2=seed2,
                geant4_version=geant4_version,
            )
            tasks.append(
                CampaignTask(
                    task_index=task_index,
                    logical_task_id=logical_task_id,
                    stage=stage,
                    tile_thickness_mm=thickness,
                    sipm_layout=layout,
                    absorber_transverse_mm=absorber,
                    x_mm=0,
                    y_mm=0,
                    seed_block=block,
                    events=events,
                    seed1=seed1,
                    seed2=seed2,
                    configuration_hash=configuration_hash,
                )
            )
            task_index += 1
    return tasks


def scan_args(task: CampaignTask, campaign_id: str) -> list[str]:
    return [
        "full",
        "custom",
        "--study-preset",
        STUDY_PRESET,
        "--tile-thickness-mm",
        str(task.tile_thickness_mm),
        "--sipm-layout",
        task.sipm_layout,
        "--absorber-transverse-mm",
        str(task.absorber_transverse_mm),
        "--x-min",
        "0",
        "--x-max",
        "0",
        "--y-min",
        "0",
        "--y-max",
        "0",
        "--step",
        "5",
        "--grid-unit",
        "mm",
        "--events",
        str(task.events),
        "--seed1",
        str(task.seed1),
        "--seed2",
        str(task.seed2),
        "--campaign-id",
        campaign_id,
        "--campaign-stage",
        task.stage,
        "--logical-task-id",
        task.logical_task_id,
        "--configuration-hash",
        task.configuration_hash,
        "--seed-block",
        str(task.seed_block),
        "--no-root-plots",
    ]


def write_tasks(path: Path, tasks: list[CampaignTask]) -> None:
    fields = list(CampaignTask.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for task in tasks:
            writer.writerow(asdict(task))


def main() -> int:
    args = parse_args()
    if args.campaign_seed < 0:
        raise SystemExit("--campaign-seed must be non-negative")
    if args.environment_mode == "osc-production" and args.allow_dirty:
        raise SystemExit("--allow-dirty is forbidden for osc-production campaigns")

    repo_root = Path(__file__).resolve().parents[2]
    dirty_output = run_git(repo_root, "status", "--porcelain", "--untracked-files=all")
    dirty = bool(dirty_output)
    if dirty and not args.allow_dirty:
        raise SystemExit(
            "Refusing to freeze a campaign from a dirty checkout. Commit the intended "
            "implementation first; --allow-dirty is for local development only."
        )

    geant4_version = args.geant4_version
    if geant4_version is None:
        geant4_version = (
            "11.4.2" if args.environment_mode == "osc-production" else "11.3.2"
        )
    if args.environment_mode == "osc-production" and geant4_version != "11.4.2":
        raise SystemExit("Accepted OSC production campaigns require Geant4 11.4.2")
    if args.environment_mode == "osc-production":
        missing = [
            name
            for name, value in (
                ("--image", args.image),
                ("--g4-data-manifest", args.g4_data_manifest),
                ("--build-artifact", args.build_artifact),
            )
            if value is None
        ]
        if missing:
            raise SystemExit(
                "osc-production campaigns require provenance artifacts: "
                + ", ".join(missing)
            )

    image_record = artifact_record(args.image)
    data_manifest_record = artifact_record(args.g4_data_manifest)
    build_artifact_record = artifact_record(args.build_artifact)
    if args.environment_mode == "osc-production":
        assert args.build_artifact is not None
        if (args.build_artifact.expanduser().resolve().stat().st_mode & 0o111) == 0:
            raise SystemExit(
                "--build-artifact must be an executable frozen OpNovice2 binary"
            )
    environment: dict[str, object] = {
        "mode": args.environment_mode,
        "geant4_version": geant4_version,
        "accepted_statistical_evidence": args.environment_mode == "osc-production",
        "image": image_record,
        "g4_data_manifest": data_manifest_record,
        "build_artifact": build_artifact_record,
    }
    environment["identity_hash"] = environment_identity(environment)

    events, blocks = resolve_stage_shape(args)
    if args.stage == "convergence-pilot":
        if args.configurations_tsv is None:
            raise SystemExit(
                "convergence-pilot requires --configurations-tsv from the "
                "post-benchmark engineering review"
            )
        pilot_configurations = read_pilot_configurations(args.configurations_tsv)
    else:
        if args.configurations_tsv is not None:
            raise SystemExit(
                "--configurations-tsv is accepted only for convergence-pilot"
            )
        pilot_configurations = ()
    tasks = build_tasks(
        stage=args.stage,
        events=events,
        blocks=blocks,
        campaign_seed=args.campaign_seed,
        geant4_version=geant4_version,
        pilot_configurations=pilot_configurations,
    )
    plan_hash = sha256_bytes(canonical_json([asdict(task) for task in tasks]))
    campaign_id = f"sm-v1-{args.stage}-{plan_hash[:12]}"

    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty campaign directory: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks_path = out_dir / "tasks.tsv"
    scan_args_path = out_dir / "scan_args.txt"
    readme_path = out_dir / "README.md"
    campaign_path = out_dir / "campaign.json"
    write_tasks(tasks_path, tasks)
    configuration_selection_path: Path | None = None
    if pilot_configurations:
        configuration_selection_path = out_dir / "configuration_selection.tsv"
        with configuration_selection_path.open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(
                [
                    "tile_thickness_mm",
                    "sipm_layout",
                    "absorber_transverse_mm",
                ]
            )
            writer.writerows(pilot_configurations)
    scan_args_path.write_text(
        "\n".join(
            [
                f"# schema_version={CAMPAIGN_SCHEMA_VERSION}",
                f"# campaign_id={campaign_id}",
                f"# task_count={len(tasks)}",
                *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    description = args.description or f"{STUDY_PRESET} {args.stage} campaign"
    readme = f"""# {campaign_id}

{description}

- Study preset: `{STUDY_PRESET}`
- Stage: `{args.stage}`
- Tasks: `{len(tasks)}`
- Events per task: `{events}`
- Seed blocks per configuration: `{blocks}`
- Environment class: `{args.environment_mode}` (Geant4 `{geant4_version}`)
- Plan hash: `{plan_hash}`

Validate from the repository root before contacting Slurm:

```bash
python3 hpc/osc/submit_steel_module_campaign.py \\
  --campaign-dir {out_dir} --check-only
```

Submit an accepted `osc-production` campaign only through the same wrapper;
do not call `sbatch` directly. After all tasks complete, run the steel-module
finalizer and event audit. Benchmark, convergence-pilot, and production are
separate campaigns and must pass their preceding gates.
"""
    readme_path.write_text(readme, encoding="utf-8")

    manifest = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "study_preset": STUDY_PRESET,
        "stage": args.stage,
        "description": description,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_hash": plan_hash,
        "campaign_seed": args.campaign_seed,
        "events_per_task": events,
        "seed_blocks_per_configuration": blocks,
        "task_count": len(tasks),
        "configuration_axes": {
            "tile_thickness_mm": sorted(
                {task.tile_thickness_mm for task in tasks}
            ),
            "sipm_layout": [
                layout
                for layout in SIPM_LAYOUTS
                if any(task.sipm_layout == layout for task in tasks)
            ],
            "absorber_transverse_mm": sorted(
                {task.absorber_transverse_mm for task in tasks}
            ),
            "x_mm": [0],
            "y_mm": [0],
        },
        "accepted_domain": {
            "tile_thickness_mm": list(TILE_THICKNESSES_MM),
            "sipm_layout": list(SIPM_LAYOUTS),
            "absorber_transverse_mm": list(ABSORBER_SIZES_MM),
        },
        "source_contract": {
            "particle": "neutron",
            "authoritative_quantity": "kinetic_energy",
            "kinetic_energy_mev": 1000,
            "profile": "point",
            "direction": [0, 0, -1],
            "angular_model": "pencil",
        },
        "git": {
            "commit": run_git(repo_root, "rev-parse", "HEAD"),
            "branch": run_git(repo_root, "branch", "--show-current"),
            "dirty": dirty,
            "dirty_paths": dirty_output.splitlines() if dirty else [],
        },
        "environment": environment,
        "artifacts": {
            "tasks_tsv": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
            "scan_args": {
                "path": "scan_args.txt",
                "sha256": sha256_file(scan_args_path),
            },
            "readme": {"path": "README.md", "sha256": sha256_file(readme_path)},
            **(
                {
                    "configuration_selection": {
                        "path": "configuration_selection.tsv",
                        "sha256": sha256_file(configuration_selection_path),
                    }
                }
                if configuration_selection_path is not None
                else {}
            ),
        },
    }
    campaign_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Campaign: {campaign_id}")
    print(f"Stage: {args.stage}")
    print(f"Tasks: {len(tasks)}")
    print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
