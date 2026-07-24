#!/usr/bin/env python3
"""Audit complete D-012 event invariants for a finalized neutron campaign."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from realistic_neutron_campaign_lib import (
    atomic_write_json,
    load_campaign,
    load_json,
    resolve_campaign_path,
    sha256_file,
    verify_finalized_checksums,
)
from realistic_neutron_event_audit import read_and_audit_root


SUMMARY_INTEGER_FIELDS = (
    "events",
    "committed_events",
    "shoot_position_events",
    "hit_position_events",
    "scint_centroid_events",
    "primary_energy_events",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "cerenkov_photons",
    "steel_edep_nonzero_events",
    "primary_neutron_interaction_events",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "primary_neutron_elastic_events",
    "primary_neutron_inelastic_events",
    "primary_neutron_capture_events",
    "charged_tile_entry_events",
    "charged_tile_entry_count",
    "electron_tile_entry_count",
    "proton_tile_entry_count",
    "other_charged_tile_entry_count",
    "primary_neutron_tile_entry_events",
    "tile_edep_nonzero_events",
    "generated_optical_zero_events",
    "scintillation_zero_events",
    "sipm_detected_zero_events",
)

SUMMARY_FLOAT_FIELDS = (
    "steel_edep_sum_mev",
    "charged_tile_entry_ke_sum_mev",
    "electron_tile_entry_ke_sum_mev",
    "proton_tile_entry_ke_sum_mev",
    "other_charged_tile_entry_ke_sum_mev",
    "tile_edep_sum_mev",
    "electron_tile_edep_sum_mev",
    "proton_tile_edep_sum_mev",
    "other_charged_tile_edep_sum_mev",
    "neutral_tile_edep_sum_mev",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--finalized-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def read_single_csv_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"expected one summary row in {path}, found {len(rows)}")
    return rows[0]


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256_file(path)}  {path.name}\n")


def main() -> int:
    args = parse_args()
    campaign_dir = args.campaign_dir.expanduser().resolve()
    finalized_dir = (
        args.finalized_dir.expanduser().resolve()
        if args.finalized_dir is not None
        else campaign_dir / "finalized"
    )
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir is not None
        else finalized_dir / "event-audit"
    )
    try:
        bundle = load_campaign(campaign_dir, verify_external_artifacts=True)
        verify_finalized_checksums(finalized_dir)
        validation = load_json(finalized_dir / "validation_report.json")
        if validation.get("valid") is not True:
            raise ValueError("finalized validation report is not valid")
        if (
            validation.get("campaign_id") != bundle.campaign_id
            or validation.get("plan_hash") != bundle.plan_hash
            or validation.get("git_commit") != bundle.git_commit
            or validation.get("environment_identity")
            != bundle.environment.get("identity_hash")
        ):
            raise ValueError("finalized identity does not match campaign")
        task_rows = read_tsv(finalized_dir / "task_index.tsv")
        if len(task_rows) != len(bundle.tasks):
            raise ValueError("finalized task index does not cover the campaign")
        if {row["logical_task_id"] for row in task_rows} != {
            task.logical_task_id for task in bundle.tasks
        }:
            raise ValueError("finalized task identities do not match the campaign")

        task_reports: list[dict[str, object]] = []
        for row in task_rows:
            logical_id = row["logical_task_id"]
            root_path = resolve_campaign_path(campaign_dir, row["root"])
            marker_path = (
                campaign_dir
                / "attempts"
                / row["attempt_id"]
                / "tasks"
                / logical_id
                / "task_result.json"
            )
            marker = load_json(marker_path)
            artifacts = marker.get("artifacts")
            root_record = artifacts.get("root") if isinstance(artifacts, dict) else None
            if not isinstance(root_record, dict):
                raise ValueError(f"task result has no ROOT identity: {logical_id}")
            digest = root_record.get("sha256")
            if (
                root_record.get("path") != row["root"]
                or not isinstance(digest, str)
                or sha256_file(root_path) != digest
            ):
                raise ValueError(f"ROOT identity mismatch for {logical_id}")

            expected_events = int(row["events"])
            _, event_report = read_and_audit_root(
                root_path,
                expected_events=expected_events,
                context=logical_id,
            )
            summary_path = resolve_campaign_path(campaign_dir, row["summary"])
            summary = read_single_csv_row(summary_path)
            for field in SUMMARY_INTEGER_FIELDS:
                if int(summary[field]) != int(event_report[field]):
                    raise ValueError(
                        f"{logical_id}: event audit disagrees with summary field {field}"
                    )
            for field in SUMMARY_FLOAT_FIELDS:
                if not math.isclose(
                    float(summary[field]),
                    float(event_report[field]),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    raise ValueError(
                        f"{logical_id}: event audit disagrees with summary field {field}"
                    )
            task_reports.append(
                {
                    "logical_task_id": logical_id,
                    "tile_thickness_mm": int(row["tile_thickness_mm"]),
                    "absorber_transverse_mm": int(row["absorber_transverse_mm"]),
                    "x_mm": int(row["x_mm"]),
                    "y_mm": int(row["y_mm"]),
                    "root": row["root"],
                    "root_sha256": digest,
                    "audit": event_report,
                }
            )

        if output_dir.exists():
            raise ValueError(f"refusing to overwrite event-audit directory: {output_dir}")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
        )
        try:
            report = {
                "schema_version": "realistic-neutron-event-audit-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "valid": True,
                "campaign_id": bundle.campaign_id,
                "plan_hash": bundle.plan_hash,
                "git_commit": bundle.git_commit,
                "environment_identity": bundle.environment.get("identity_hash"),
                "accepted_statistical_evidence": (
                    validation.get("accepted_statistical_evidence") is True
                ),
                "task_count": len(task_reports),
                "tasks": task_reports,
            }
            atomic_write_json(temp_dir / "event_audit.json", report)
            write_checksums(temp_dir)
            temp_dir.replace(output_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot audit realistic-neutron campaign: {exc}", file=sys.stderr)
        return 1

    print(f"Audited {len(task_reports)} tasks into {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
