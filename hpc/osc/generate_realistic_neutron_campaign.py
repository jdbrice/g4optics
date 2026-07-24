#!/usr/bin/env python3
"""Generate immutable realistic-neutron-v1 campaign manifests for OSC arrays."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from realistic_neutron_campaign_lib import environment_identity


SCHEMA_VERSION = "realistic-neutron-campaign-v1"
STUDY_PRESET = "realistic-neutron-v1"
MAX_GEANT4_SEED = 2_147_483_646
TILE_THICKNESSES_MM = (4, 16)
ABSORBER_SIZES_MM = (200, 300, 500)
LINE_X_MM = (-20, 5, 10, 15, 20, 25, 30, 35, 40)


@dataclass(frozen=True)
class Task:
    task_index: int
    logical_task_id: str
    stage: str
    tile_thickness_mm: int
    absorber_transverse_mm: int
    x_mm: int
    y_mm: int
    seed_block: int
    events: int
    seed1: int
    seed2: int
    configuration_hash: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--stage",
        required=True,
        choices=(
            "geometry-smoke",
            "benchmark",
            "convergence-pilot",
            "center-production",
            "line-scan",
        ),
    )
    parser.add_argument("--campaign-seed", required=True, type=int)
    parser.add_argument("--events", type=int)
    parser.add_argument("--blocks", type=int)
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
        help="Development-only escape hatch; never valid for osc-production.",
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def resolve_stage_shape(args: argparse.Namespace) -> tuple[int, int]:
    defaults = {
        "geometry-smoke": (1, 1),
        "benchmark": (100, 1),
        "convergence-pilot": (250, 4),
    }
    if args.stage in defaults:
        default_events, default_blocks = defaults[args.stage]
        events = args.events if args.events is not None else default_events
        blocks = args.blocks if args.blocks is not None else default_blocks
    else:
        if args.events is None:
            raise SystemExit(f"--stage {args.stage} requires --events after N is frozen")
        if args.blocks is None:
            raise SystemExit(f"--stage {args.stage} requires --blocks (at least 4)")
        events = args.events
        blocks = args.blocks

    if events <= 0:
        raise SystemExit("--events must be positive")
    if blocks <= 0:
        raise SystemExit("--blocks must be positive")
    if args.stage in ("geometry-smoke", "benchmark") and blocks != 1:
        raise SystemExit(f"--stage {args.stage} has exactly one seed block")
    if args.stage == "convergence-pilot" and blocks < 4:
        raise SystemExit("--stage convergence-pilot requires at least four seed blocks")
    if args.stage in ("center-production", "line-scan") and blocks < 4:
        raise SystemExit(f"--stage {args.stage} requires at least four seed blocks")
    return events, blocks


def task_coordinates(stage: str) -> Iterable[tuple[int, int, int]]:
    if stage in ("geometry-smoke", "convergence-pilot"):
        for tile in TILE_THICKNESSES_MM:
            for absorber in ABSORBER_SIZES_MM:
                yield tile, absorber, 0
    elif stage in ("benchmark", "center-production"):
        for tile in TILE_THICKNESSES_MM:
            yield tile, 500, 0
    elif stage == "line-scan":
        for tile in TILE_THICKNESSES_MM:
            for x_mm in LINE_X_MM:
                yield tile, 500, x_mm
    else:  # pragma: no cover - argparse restricts this
        raise ValueError(stage)


def signed_label(value: int) -> str:
    if value < 0:
        return f"m{abs(value):03d}"
    return f"p{value:03d}"


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
    *, stage: str, events: int, blocks: int, campaign_seed: int, geant4_version: str
) -> list[Task]:
    tasks: list[Task] = []
    used_seeds: set[int] = set()
    task_index = 1
    for tile, absorber, x_mm in task_coordinates(stage):
        for block in range(blocks):
            logical_task_id = (
                f"{stage}-t{tile:02d}-a{absorber:03d}-"
                f"x{signed_label(x_mm)}-yp000-b{block:02d}"
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
            resolved = {
                "schema_version": SCHEMA_VERSION,
                "study_preset": STUDY_PRESET,
                "stage": stage,
                "tile_thickness_mm": tile,
                "absorber_transverse_mm": absorber,
                "absorber_thickness_mm": 40,
                "x_mm": x_mm,
                "y_mm": 0,
                "events": events,
                "seed_block": block,
                "seed1": seed1,
                "seed2": seed2,
                "neutron_momentum_gev_c": 1,
                "gps_kinetic_energy_mev": 432.58,
                "beam_divergence_mrad": 55,
                "geant4_version": geant4_version,
            }
            configuration_hash = sha256_bytes(canonical_json(resolved))
            tasks.append(
                Task(
                    task_index=task_index,
                    logical_task_id=logical_task_id,
                    stage=stage,
                    tile_thickness_mm=tile,
                    absorber_transverse_mm=absorber,
                    x_mm=x_mm,
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


def task_plan_hash(tasks: list[Task]) -> str:
    return sha256_bytes(canonical_json([asdict(task) for task in tasks]))


def scan_args(task: Task, campaign_id: str) -> list[str]:
    x = str(task.x_mm)
    y = str(task.y_mm)
    return [
        "full",
        "custom",
        "--study-preset",
        STUDY_PRESET,
        "--tile-thickness-mm",
        str(task.tile_thickness_mm),
        "--absorber-transverse-mm",
        str(task.absorber_transverse_mm),
        "--x-min",
        x,
        "--x-max",
        x,
        "--y-min",
        y,
        "--y-max",
        y,
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


def artifact_record(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"Missing provenance artifact: {resolved}")
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def write_tasks(path: Path, tasks: list[Task]) -> None:
    fieldnames = list(Task.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter="\t")
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
        geant4_version = "11.4.2" if args.environment_mode == "osc-production" else "11.3.2"
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
                "osc-production campaigns require provenance artifacts: " + ", ".join(missing)
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
    environment = {
        "mode": args.environment_mode,
        "geant4_version": geant4_version,
        "accepted_statistical_evidence": args.environment_mode == "osc-production",
        "image": image_record,
        "g4_data_manifest": data_manifest_record,
        "build_artifact": build_artifact_record,
    }
    environment["identity_hash"] = environment_identity(environment)

    events, blocks = resolve_stage_shape(args)
    tasks = build_tasks(
        stage=args.stage,
        events=events,
        blocks=blocks,
        campaign_seed=args.campaign_seed,
        geant4_version=geant4_version,
    )
    plan_hash = task_plan_hash(tasks)
    campaign_id = f"rn-v1-{args.stage}-{plan_hash[:12]}"

    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"Refusing to overwrite non-empty campaign directory: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks_path = out_dir / "tasks.tsv"
    scan_args_path = out_dir / "scan_args.txt"
    readme_path = out_dir / "README.md"
    campaign_path = out_dir / "campaign.json"

    write_tasks(tasks_path, tasks)
    scan_lines = [
        f"# schema_version={SCHEMA_VERSION}",
        f"# campaign_id={campaign_id}",
        f"# task_count={len(tasks)}",
        *[shlex.join(scan_args(task, campaign_id)) for task in tasks],
    ]
    scan_args_path.write_text("\n".join(scan_lines) + "\n", encoding="utf-8")

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

Validate from the repository root after reviewing `campaign.json` and `tasks.tsv`:

```bash
python3 hpc/osc/submit_realistic_neutron_campaign.py \\
  --campaign-dir {out_dir} --check-only
```

An `osc-production` campaign is submitted only through the same wrapper with
`--account`, `--g4-data-root`, and `--frozen-root`. Recovery uses `--resume` or
`--retry-failed`; do not call `sbatch` directly. After every expected task has
one unambiguous valid result, run:

```bash
python3 hpc/osc/finalize_realistic_neutron_campaign.py \\
  --campaign-dir {out_dir}

python3 hpc/osc/analyze_realistic_neutron_campaign.py \\
  --campaign-dir {out_dir} \\
  --finalized-dir {out_dir / "finalized"}
```

Retries retain the same logical task row, configuration hash, and seed pair.
Pilot, benchmark, centered production, and line-scan campaigns are separate evidence sets.
"""
    readme_path.write_text(readme, encoding="utf-8")

    manifest = {
        "schema_version": SCHEMA_VERSION,
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
        "git": {
            "commit": run_git(repo_root, "rev-parse", "HEAD"),
            "branch": run_git(repo_root, "branch", "--show-current"),
            "dirty": dirty,
            "dirty_paths": dirty_output.splitlines() if dirty else [],
        },
        "environment": environment,
        "artifacts": {
            "tasks_tsv": {"path": "tasks.tsv", "sha256": sha256_file(tasks_path)},
            "scan_args": {"path": "scan_args.txt", "sha256": sha256_file(scan_args_path)},
            "readme": {"path": "README.md", "sha256": sha256_file(readme_path)},
        },
    }
    campaign_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Campaign: {campaign_id}")
    print(f"Tasks: {len(tasks)}")
    print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
