#!/usr/bin/env python3
"""Build the exact content-addressed BC-ONLY-S1 production checkpoint."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any

from steel_module_campaign_lib import canonical_json, require_dict, sha256_bytes, sha256_file
from steel_module_production_checkpoint_lib import (
    CHECKPOINT_SCHEMA_VERSION,
    FORMAL_CHECKPOINT_STATE,
    load_execution_for_downstream,
    load_managed_finalization,
    managed_finalization_directory,
    recovery_lineage_from_execution,
    validate_recovery_lineage,
    write_checkpoint_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument(
        "--state",
        choices=(FORMAL_CHECKPOINT_STATE,),
        default=FORMAL_CHECKPOINT_STATE,
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the complete finalized child without writing a checkpoint.",
    )
    return parser.parse_args()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _task_index_text(rows: tuple[dict[str, str], ...]) -> str:
    if not rows:
        raise ValueError("cannot write an empty checkpoint task index")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _audit_task_identity(
    event_audit: dict[str, Any], rows: tuple[dict[str, str], ...]
) -> None:
    tasks = event_audit.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 32:
        raise ValueError("managed event audit must contain exactly 32 tasks")
    by_id = {row["logical_task_id"]: row for row in rows}
    if len(by_id) != 32:
        raise ValueError("checkpoint task index contains duplicate identities")
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("event-audit task entry must be an object")
        logical_id = task.get("logical_task_id")
        row = by_id.get(logical_id)
        if row is None:
            raise ValueError(f"event audit contains an unknown task: {logical_id!r}")
        expected = {
            "tile_thickness_mm": int(row["tile_thickness_mm"]),
            "sipm_layout": "back-center",
            "seed_block": int(row["seed_block"]),
            "events": 250,
            "root": row["root"],
            "root_sha256": row["root_sha256"],
        }
        for key, value in expected.items():
            if task.get(key) != value:
                raise ValueError(f"event audit and task index disagree on {key}")


def build_checkpoint(
    finalized_dir: Path,
    output_root: Path,
    *,
    allow_test_mode: bool,
    verify_root_files: bool,
) -> Path:
    finalized = finalized_dir.expanduser().resolve()
    validation, rows = load_managed_finalization(
        finalized,
        allow_test_mode=allow_test_mode,
        verify_root_files=verify_root_files,
    )
    if not verify_root_files and validation.get("test_mode") is not True:
        raise ValueError("accepted evidence may not skip ROOT-file verification")
    event_audit = json.loads((finalized / "event_audit.json").read_text(encoding="utf-8"))
    seed_audit = json.loads((finalized / "seed_audit.json").read_text(encoding="utf-8"))
    if not isinstance(event_audit, dict) or not isinstance(seed_audit, dict):
        raise ValueError("managed audits must be JSON objects")
    _audit_task_identity(event_audit, rows)

    task_text = _task_index_text(rows)
    configuration_summary_text = (finalized / "configuration_summary.csv").read_text(
        encoding="utf-8"
    )
    source_finalization: dict[str, Any] = {
        "directory": str(finalized),
        "checksum_manifest_sha256": sha256_file(finalized / "SHA256SUMS"),
        "task_index_sha256": sha256_file(finalized / "task_index.tsv"),
        "event_audit_sha256": sha256_file(finalized / "event_audit.json"),
        "seed_audit_sha256": sha256_file(finalized / "seed_audit.json"),
        "selection_record_sha256": sha256_file(finalized / "selection_record.json"),
        "validation_report_sha256": sha256_file(finalized / "validation_report.json"),
        "finalization_hash": validation.get("finalization_hash"),
    }
    program = require_dict(validation, "program")
    execution = require_dict(validation, "execution")
    recovery_lineage = validate_recovery_lineage(
        execution, validation.get("recovery_lineage")
    )
    readiness = require_dict(validation, "readiness")
    pilot_baseline = require_dict(validation, "pilot_baseline")
    source_children: dict[str, Any] = {
        "schema_version": "steel-module-production-checkpoint-sources-v1",
        "execution_directory": execution.get("directory"),
        "children": [
            {
                "child_id": "BC-S1",
                "execution_directory": execution.get("directory"),
                "finalization": source_finalization,
            }
        ],
        "pilot_baseline": pilot_baseline,
        "recovery_lineage": recovery_lineage,
    }
    root_records = [
        {
            "logical_task_id": row["logical_task_id"],
            "root": row["root"],
            "root_sha256": row["root_sha256"],
        }
        for row in rows
    ]
    manifest: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        # The checkpoint identity must be reproducible from frozen evidence.
        # Use the source finalization time rather than the wall clock here.
        "created_at_utc": validation.get("created_at_utc"),
        "state_id": FORMAL_CHECKPOINT_STATE,
        "evidence_mode": "back-center-only",
        "child_ids": ["BC-S1"],
        "included_children": ["BC-S1"],
        "task_count": 32,
        "event_count": 8_000,
        "events_per_task": 250,
        "root_tree": "scan",
        "accepted_statistical_evidence": validation[
            "accepted_statistical_evidence"
        ],
        "test_mode": validation["test_mode"],
        "program": program,
        "execution": execution,
        "source_execution_directory": execution.get("directory"),
        "simulation_commit": execution.get("simulation_commit"),
        "readiness": readiness,
        "pilot_baseline": pilot_baseline,
        "recovery_lineage": recovery_lineage,
        "source_finalization": source_finalization,
        "source_children": source_children,
        "configuration_shape": {
            "tile_thickness_mm": [4, 24],
            "sipm_layout": ["back-center"],
            "absorber_transverse_mm": [500],
            "seed_blocks": {"start_inclusive": 0, "stop_exclusive": 16},
            "events_per_configuration": 4_000,
        },
        "task_set_hash": execution.get("task_set_hash"),
        "task_seed_mapping_hash": execution.get("task_seed_mapping_hash"),
        "production_seed_set_hash": execution.get("seed_set_hash"),
        "task_index_sha256": sha256_bytes(task_text.encode("utf-8")),
        "event_audit_sha256": sha256_bytes(_json_bytes(event_audit)),
        "seed_audit_sha256": sha256_bytes(_json_bytes(seed_audit)),
        "source_finalization_sha256": sha256_bytes(
            _json_bytes(source_finalization)
        ),
        "source_children_sha256": sha256_bytes(_json_bytes(source_children)),
        "root_set_hash": sha256_bytes(canonical_json(root_records)),
    }
    return write_checkpoint_atomic(
        output_root,
        manifest_payload=manifest,
        task_index_text=task_text,
        configuration_summary_text=configuration_summary_text,
        event_audit=event_audit,
        seed_audit=seed_audit,
        source_children=source_children,
        source_finalization=source_finalization,
    )


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    execution = args.execution_dir.expanduser()
    if execution.is_symlink():
        print("Cannot build steel-module production checkpoint: execution directory is a symlink", file=sys.stderr)
        return 1
    execution = execution.resolve()
    output_root = execution.parent / "steel-module-production-checkpoints"
    try:
        loaded_execution = load_execution_for_downstream(
            execution,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_runtime=True,
            verify_live_predecessor=True,
        )
        if loaded_execution.directory != execution:
            raise ValueError("formal execution directory identity changed")
        finalized = managed_finalization_directory(
            execution, loaded_execution.manifest
        )
        validation, rows = load_managed_finalization(
            finalized, allow_test_mode=False, verify_root_files=True
        )
        recorded_execution = require_dict(validation, "execution")
        for key, actual in (
            ("directory", str(loaded_execution.directory)),
            ("execution_id", loaded_execution.execution_id),
            ("execution_hash", loaded_execution.execution_hash),
        ):
            if recorded_execution.get(key) != actual:
                raise ValueError(
                    f"finalization/source execution {key} mismatch"
                )
        if validation.get("recovery_lineage") != recovery_lineage_from_execution(
            loaded_execution
        ):
            raise ValueError("finalization/source recovery lineage mismatch")
        if args.check_only:
            event_audit = json.loads(
                (finalized / "event_audit.json").read_text(encoding="utf-8")
            )
            if not isinstance(event_audit, dict):
                raise ValueError("managed event audit must be an object")
            _audit_task_identity(event_audit, rows)
            print("steel-module production checkpoint check: PASS")
            print(f"state: {args.state}")
            print("tasks: 32")
            print("events: 8000")
            print("checkpoint written: false")
            return 0
        output = build_checkpoint(
            finalized,
            output_root,
            allow_test_mode=False,
            verify_root_files=True,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot build steel-module production checkpoint: {exc}", file=sys.stderr)
        return 1
    print("steel-module production checkpoint: PASS")
    print(f"state: {FORMAL_CHECKPOINT_STATE}")
    print("tasks: 32")
    print("events: 8000")
    print(f"output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
