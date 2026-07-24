#!/usr/bin/env python3
"""Validate, submit, resume, or retry one steel-module OSC campaign."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import submit_realistic_neutron_campaign as implementation
from generate_steel_module_direct_bc_s1_campaign import (
    validate_direct_bc_s1_bundle,
)
from generate_steel_module_direct_bc_s2_campaign import (
    validate_direct_bc_s2_bundle,
)
from generate_steel_module_direct_bc_s3_campaign import (
    validate_direct_bc_s3_bundle,
)
from generate_steel_module_direct_bc_s4_campaign import (
    validate_direct_bc_s4_bundle,
)
from generate_steel_module_direct_edge_two_campaign import (
    validate_direct_edge_two_bundle,
)
from generate_steel_module_direct_fixed_campaign import (
    validate_direct_fixed_bundle,
)
from record_steel_module_task_result import (
    integer_field,
    read_single_csv_row,
    validate_run_config,
)
from steel_module_campaign_lib import (
    RESULT_SCHEMA_VERSION,
    CampaignBundle,
    CampaignTask,
    load_campaign,
    load_json,
    read_attempt_rows,
    resolve_campaign_path,
    task_by_logical_id,
)


# The submission journal, immutable source archive, retry selection, Slurm
# accounting, and checksum logic are shared with the already validated neutron
# workflow. Replace only the campaign/task contract and result validator.
implementation.__doc__ = __doc__
implementation.CampaignBundle = CampaignBundle
implementation.CampaignTask = CampaignTask
implementation.RESULT_SCHEMA_VERSION = RESULT_SCHEMA_VERSION
implementation.load_campaign = load_campaign
implementation.load_json = load_json
implementation.read_attempt_rows = read_attempt_rows
implementation.resolve_campaign_path = resolve_campaign_path
implementation.task_by_logical_id = task_by_logical_id
implementation.integer_field = integer_field
implementation.read_single_csv_row = read_single_csv_row
implementation.validate_run_config = validate_run_config


def requested_campaign_dir(argv: list[str]) -> Path | None:
    """Return the requested campaign directory without accepting other options."""

    values: list[str] = []
    for index, value in enumerate(argv):
        if value == "--campaign-dir":
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise ValueError("--campaign-dir requires one path")
            values.append(argv[index + 1])
        if value.startswith("--campaign-dir="):
            values.append(value.split("=", 1)[1])
    if len(values) > 1:
        raise ValueError("--campaign-dir may be provided only once")
    if not values:
        return None
    if not values[0]:
        raise ValueError("--campaign-dir requires a non-empty path")
    return Path(values[0]).expanduser().resolve()


def reject_managed_production_route(argv: list[str]) -> None:
    """Admit only an exact reviewed direct-production child shape."""

    campaign_dir = requested_campaign_dir(argv)
    if campaign_dir is None:
        return
    if any(
        (campaign_dir / name).exists()
        for name in ("managed_child.json", "program_binding.json", "SHA256SUMS")
    ):
        raise ValueError(
            "managed/checksum-bound steel-module objects require their dedicated "
            "validator; the generic submitter is fail-closed"
        )
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot inspect steel-module campaign stage: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("steel-module campaign.json must contain an object")
    if "direct_production" in manifest:
        bundle = load_campaign(campaign_dir, verify_external_artifacts=False)
        marker = manifest.get("direct_production")
        if not isinstance(marker, dict):
            raise ValueError("direct production marker must be an object")
        child_id = marker.get("child_id")
        if child_id == "BC-S1":
            validate_direct_bc_s1_bundle(bundle)
        elif child_id == "BC-S2":
            validate_direct_bc_s2_bundle(bundle)
        elif child_id == "BC-S3":
            validate_direct_bc_s3_bundle(bundle)
        elif child_id == "BC-S4":
            validate_direct_bc_s4_bundle(bundle)
        elif child_id == "FIXED":
            validate_direct_fixed_bundle(bundle)
        elif child_id == "EDGE-TWO":
            validate_direct_edge_two_bundle(bundle)
        else:
            raise ValueError(f"unsupported direct production child: {child_id!r}")
        return
    tasks_path = campaign_dir / "tasks.tsv"
    task_stages: set[str] = set()
    logical_task_ids: list[str] = []
    if tasks_path.is_file():
        try:
            with tasks_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream, delimiter="\t")
                fields = set(reader.fieldnames or ())
                if not {"stage", "logical_task_id"}.issubset(fields):
                    raise ValueError("tasks.tsv lacks stage/logical-task identity")
                rows = list(reader)
                task_stages = {row.get("stage", "") for row in rows}
                logical_task_ids = [row.get("logical_task_id", "") for row in rows]
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ValueError(f"cannot inspect steel-module task stages: {exc}") from exc
    manifest_stage = manifest.get("stage")
    if not isinstance(manifest_stage, str) or not manifest_stage:
        raise ValueError("campaign manifest lacks a valid stage")
    allowed_stages = {"geometry-smoke", "benchmark", "convergence-pilot"}
    managed_marker = (
        "production_management" in manifest or "program_management" in manifest
    )
    if (
        manifest_stage not in allowed_stages
        or "production" in task_stages
        or managed_marker
        or any(value.startswith("production-") for value in logical_task_ids)
    ):
        raise ValueError(
            "steel-module production campaigns other than the exact reviewed "
            "direct-child shapes remain blocked; the generic submitter is fail-closed"
        )
    if task_stages and task_stages != {manifest_stage}:
        raise ValueError("campaign stage and task stages disagree")


def main() -> int:
    try:
        reject_managed_production_route(sys.argv[1:])
    except ValueError as exc:
        print(f"Cannot submit campaign: {exc}", file=sys.stderr)
        return 1
    return implementation.main()


if __name__ == "__main__":
    raise SystemExit(main())
