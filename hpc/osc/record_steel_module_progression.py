#!/usr/bin/env python3
"""Validate and append one human steel-module progression decision."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from plot_steel_module_analysis_v2 import (
    sha256_file,
    verify_checksum_manifest,
    write_recursive_checksums,
)


ANALYSIS_SCHEMA = "steel-module-production-checkpoint-analysis-v1"
REVIEW_SCHEMA = "steel-module-production-checkpoint-review-v1"
DECISION_SCHEMA = "steel-module-production-progression-decision-v1"
STATE_ID = "BC-ONLY-S1"
TAIL_DISPOSITIONS = (
    "no-material-worsening",
    "material-worsening",
    "unable-to-determine",
)
DECISIONS = ("stop-success", "continue", "pause-review")
REQUIRED_ANALYSIS = (
    "primary_contrast.json",
    "numeric_eligibility.json",
    "distribution_diagnostics.csv",
    "pilot_distribution_diagnostics.csv",
    "block_loo.csv",
    "precision_trajectory.csv",
    "analysis_config.json",
)
REQUIRED_REVIEW = (
    "index.html",
    "review_report.pdf",
    "review_data.json",
    "review_provenance.json",
    "figures/precision_trajectory.png",
    "figures/precision_trajectory.pdf",
    "figures/block_loo_shifts.png",
    "figures/block_loo_shifts.pdf",
    "figures/tail_diagnostics.png",
    "figures/tail_diagnostics.pdf",
    "figures/endpoint_ratio.png",
    "figures/endpoint_ratio.pdf",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--review-dir", required=True, type=Path)
    parser.add_argument("--tail-disposition", required=True, choices=TAIL_DISPOSITIONS)
    parser.add_argument("--decision", required=True, choices=DECISIONS)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--rationale-file", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--record", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"invalid {label} SHA-256 identity")
    return value


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def semantic_hash(value: Mapping[str, object], hash_field: str) -> str:
    unhashed = dict(value)
    unhashed.pop(hash_field, None)
    return hashlib.sha256(canonical_json(unhashed)).hexdigest()


def validate_reviewer(value: str) -> str:
    if not 1 <= len(value) <= 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("reviewer must be 1-256 characters without controls")
    return value


def read_rationale(path: Path) -> str:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError("rationale file must not be a symlink")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise ValueError(f"missing rationale file: {resolved}")
    value = resolved.read_text(encoding="utf-8")
    if not 1 <= len(value) <= 16_384:
        raise ValueError("rationale must contain 1-16,384 UTF-8 characters")
    if "\x00" in value:
        raise ValueError("rationale contains a NUL character")
    return value


def decisions_root(analysis_dir: Path) -> Path:
    checkpoints = analysis_dir.parent
    if checkpoints.name != "steel-module-production-checkpoints":
        raise ValueError("analysis is not under the canonical production-checkpoints root")
    if not analysis_dir.name.endswith("-analysis"):
        raise ValueError("analysis directory name lacks the -analysis suffix")
    return checkpoints.parent / "steel-module-production-decisions" / "records"


def final_allowed(
    numeric: Sequence[str], tail_disposition: str
) -> list[str]:
    if tail_disposition != "no-material-worsening":
        return ["pause-review"]
    ordered = [decision for decision in DECISIONS if decision in set(numeric)]
    if "pause-review" not in ordered:
        raise ValueError("valid numeric eligibility must always permit pause-review")
    return ordered


def authorization(decision: str) -> dict[str, object]:
    if decision == "stop-success":
        return {
            "directly_authorized_children": ["FIXED"],
            "scheduling_eligible_children": [],
            "scheduling_record_required": False,
        }
    if decision == "continue":
        return {
            "directly_authorized_children": ["FIXED"],
            "scheduling_eligible_children": ["BC-S2"],
            "scheduling_record_required": True,
        }
    return {
        "directly_authorized_children": [],
        "scheduling_eligible_children": [],
        "scheduling_record_required": False,
    }


def validate_chain(records_root: Path) -> tuple[int, str | None]:
    if not records_root.exists():
        return 0, None
    if records_root.is_symlink() or not records_root.is_dir():
        raise ValueError("progression records root is unsafe")
    directories = sorted(path for path in records_root.iterdir() if path.is_dir())
    unexpected = [
        path.name
        for path in records_root.iterdir()
        if path.name != ".progression.lock" and not path.is_dir()
    ]
    if unexpected:
        raise ValueError(f"unexpected progression-root files: {unexpected}")
    previous: str | None = None
    for sequence, directory in enumerate(directories, 1):
        if directory.is_symlink():
            raise ValueError(f"progression record must not be a symlink: {directory}")
        prefix = f"{sequence:04d}-"
        if not directory.name.startswith(prefix):
            raise ValueError("progression record sequences are not contiguous")
        verify_checksum_manifest(
            directory, required=("decision.json", "rationale.txt")
        )
        decision = load_json(directory / "decision.json")
        if decision.get("schema_version") != DECISION_SCHEMA:
            raise ValueError(f"unsupported progression decision schema: {directory}")
        if decision.get("sequence") != sequence:
            raise ValueError(f"progression decision sequence mismatch: {directory}")
        recorded_hash = decision.get("decision_hash")
        if not isinstance(recorded_hash, str) or semantic_hash(decision, "decision_hash") != recorded_hash:
            raise ValueError(f"progression decision semantic hash mismatch: {directory}")
        if not directory.name.endswith(recorded_hash[:12]):
            raise ValueError(f"progression directory name does not match decision hash: {directory}")
        if decision.get("previous_decision_hash") != previous:
            raise ValueError("progression decision chain is forked or reordered")
        if sha256_file(directory / "rationale.txt") != decision.get("rationale_sha256"):
            raise ValueError("progression rationale checksum mismatch")
        previous = recorded_hash
    return len(directories), previous


def build_decision(
    *,
    analysis_dir: Path,
    review_dir: Path,
    config: Mapping[str, object],
    primary: Mapping[str, object],
    numeric: Mapping[str, object],
    review_data: Mapping[str, object],
    tail_disposition: str,
    selected_decision: str,
    reviewer: str,
    rationale: str,
    sequence: int,
    previous_hash: str | None,
) -> dict[str, object]:
    numeric_choices = numeric.get("numeric_eligible_decisions")
    if not isinstance(numeric_choices, list) or any(
        choice not in DECISIONS for choice in numeric_choices
    ):
        raise ValueError("analysis numeric eligibility is invalid")
    allowed = final_allowed([str(value) for value in numeric_choices], tail_disposition)
    if selected_decision not in allowed:
        raise ValueError(
            f"decision {selected_decision!r} is not allowed; choices: {allowed}"
        )
    maximum = review_data.get("maximum_loo")
    current_tails = review_data.get("current_sipm_tail_diagnostics")
    pilot_tails = review_data.get("pilot_sipm_tail_diagnostics")
    program = config.get("program")
    source_finalization = config.get("source_finalization")
    if (
        not isinstance(maximum, dict)
        or not isinstance(current_tails, list)
        or not isinstance(pilot_tails, list)
        or not isinstance(program, dict)
        or not isinstance(source_finalization, dict)
    ):
        raise ValueError("review data lacks required tail diagnostics")
    decision: dict[str, object] = {
        "schema_version": DECISION_SCHEMA,
        "object_role": "append-only-human-authorization-evidence",
        "accepted_statistical_evidence": False,
        "sequence": sequence,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "state_id": STATE_ID,
        "checkpoint_hash": config.get("checkpoint_hash"),
        "checkpoint_identity": {
            "checkpoint_hash": config.get("checkpoint_hash"),
            "source_checkpoint_sha256s_sha256": config.get(
                "source_checkpoint_sha256s_sha256"
            ),
            "task_set_hash": config.get("task_set_hash"),
            "checkpoint_task_index_sha256": config.get(
                "checkpoint_task_index_sha256"
            ),
            "root_set_hash": config.get("root_set_hash"),
        },
        "program_identity": dict(program),
        "task_set_hash": config.get("task_set_hash"),
        "checkpoint_task_index_sha256": config.get(
            "checkpoint_task_index_sha256"
        ),
        "root_set_hash": config.get("root_set_hash"),
        "task_count": config.get("task_count"),
        "event_count": config.get("event_count"),
        "source_finalization": dict(source_finalization),
        "source_event_audit_sha256": config.get("source_event_audit_sha256"),
        "source_analysis_directory": str(analysis_dir),
        "source_analysis_sha256s_sha256": sha256_file(analysis_dir / "SHA256SUMS"),
        "source_review_directory": str(review_dir),
        "source_review_sha256s_sha256": sha256_file(review_dir / "SHA256SUMS"),
        "simulation_commit": config.get("simulation_commit"),
        "analysis_commit": config.get("analysis_commit"),
        "analyzer_sha256": config.get("analyzer_sha256"),
        "primary_metrics": {
            "ratio": primary.get("ratio"),
            "ci95_low": primary.get("ci95_low"),
            "ci95_high": primary.get("ci95_high"),
            "relative_half_width": primary.get("relative_half_width"),
            "interval_narrowing": primary.get("interval_narrowing"),
            "max_leave_one_block_out_relative_shift": primary.get(
                "max_leave_one_block_out_relative_shift"
            ),
        },
        "tail_diagnostics": {
            "maximum_loo": maximum,
            "current_sipm_endpoints": current_tails,
            "sealed_pilot_sipm_endpoints": pilot_tails,
        },
        "numeric_eligible_decisions": numeric_choices,
        "human_tail_disposition": tail_disposition,
        "final_allowed_decisions": allowed,
        "selected_decision": selected_decision,
        "derived_authorization": authorization(selected_decision),
        "automatic_submission": False,
        "reviewer": reviewer,
        "rationale_sha256": hashlib.sha256(rationale.encode("utf-8")).hexdigest(),
        "previous_decision_hash": previous_hash,
    }
    decision["decision_hash"] = semantic_hash(decision, "decision_hash")
    return decision


def validate_inputs(
    analysis_dir: Path,
    review_dir: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    analysis_files = verify_checksum_manifest(
        analysis_dir, required=REQUIRED_ANALYSIS
    )
    review_files = verify_checksum_manifest(review_dir, required=REQUIRED_REVIEW)
    expected_review = analysis_dir.with_name(
        f"{analysis_dir.name[:-len('-analysis')]}-analysis-review"
    )
    if review_dir != expected_review:
        raise ValueError("review directory is not the exact sibling of the analysis")
    config = load_json(analysis_dir / "analysis_config.json")
    primary = load_json(analysis_dir / "primary_contrast.json")
    numeric = load_json(analysis_dir / "numeric_eligibility.json")
    loo_rows = read_csv(analysis_dir / "block_loo.csv")
    current_distribution = read_csv(
        analysis_dir / "distribution_diagnostics.csv"
    )
    pilot_distribution = read_csv(
        analysis_dir / "pilot_distribution_diagnostics.csv"
    )
    trajectory_rows = read_csv(analysis_dir / "precision_trajectory.csv")
    review_data = load_json(review_dir / "review_data.json")
    provenance = load_json(review_dir / "review_provenance.json")
    if config.get("schema_version") != ANALYSIS_SCHEMA:
        raise ValueError("unsupported analysis schema")
    if config.get("state_id") != STATE_ID or config.get("accepted_statistical_evidence") is not True:
        raise ValueError("progression requires accepted BC-ONLY-S1 analysis")
    if config.get("task_count") != 32 or config.get("event_count") != 8_000:
        raise ValueError("analysis identity does not describe exact BC-ONLY-S1 totals")
    checkpoint_hash = require_sha256(config.get("checkpoint_hash"), "checkpoint")
    checkpoint_manifest_hash = require_sha256(
        config.get("source_checkpoint_sha256s_sha256"), "checkpoint manifest"
    )
    task_set_hash = require_sha256(config.get("task_set_hash"), "task set")
    checkpoint_task_index_sha256 = require_sha256(
        config.get("checkpoint_task_index_sha256"), "checkpoint task index"
    )
    root_set_hash = require_sha256(config.get("root_set_hash"), "ROOT set")
    event_audit_hash = require_sha256(
        config.get("source_event_audit_sha256"), "event audit"
    )
    program = config.get("program")
    if not isinstance(program, dict) or not isinstance(program.get("program_id"), str):
        raise ValueError("analysis lacks the parent program identity")
    require_sha256(program.get("program_hash"), "program")
    source_finalization = config.get("source_finalization")
    if not isinstance(source_finalization, dict):
        raise ValueError("analysis lacks source-finalization identity")
    for key in (
        "checksum_manifest_sha256",
        "task_index_sha256",
        "event_audit_sha256",
        "seed_audit_sha256",
        "validation_report_sha256",
        "finalization_hash",
    ):
        require_sha256(source_finalization.get(key), f"source finalization {key}")
    if source_finalization.get("event_audit_sha256") != event_audit_hash:
        raise ValueError("source finalization and checkpoint event-audit identities differ")
    checkpoint_files = config.get("source_checkpoint_files")
    if (
        not isinstance(checkpoint_files, dict)
        or checkpoint_files.get("event_audit.json") != event_audit_hash
        or checkpoint_files.get("task_index.tsv") != checkpoint_task_index_sha256
    ):
        raise ValueError("analysis checkpoint file identities are inconsistent")
    snapshot = config.get("input_identity_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("file_count", 0) <= 0:
        raise ValueError("analysis lacks a complete input-identity snapshot")
    require_sha256(snapshot.get("sha256"), "input snapshot")
    if review_data.get("schema_version") != REVIEW_SCHEMA:
        raise ValueError("unsupported production review schema")
    if (
        provenance.get("schema_version") != f"{REVIEW_SCHEMA}-provenance-v1"
        or provenance.get("source_analysis_files") != dict(sorted(analysis_files.items()))
        or provenance.get("network_resources") is not False
        or provenance.get("write_controls") is not False
        or provenance.get("automatic_submission") is not False
        or not isinstance(provenance.get("python_version"), str)
        or not isinstance(provenance.get("matplotlib_version"), str)
        or not isinstance(provenance.get("backend"), str)
    ):
        raise ValueError("review provenance is incomplete or unsafe")
    require_sha256(provenance.get("renderer_sha256"), "review renderer")
    if set(review_files) != {
        "index.html",
        "review_report.pdf",
        "review_data.json",
        "review_provenance.json",
        "figures/precision_trajectory.png",
        "figures/precision_trajectory.pdf",
        "figures/block_loo_shifts.png",
        "figures/block_loo_shifts.pdf",
        "figures/tail_diagnostics.png",
        "figures/tail_diagnostics.pdf",
        "figures/endpoint_ratio.png",
        "figures/endpoint_ratio.pdf",
    }:
        raise ValueError("review artifact set is not the complete offline contract")
    if any((review_dir / name).stat().st_size == 0 for name in review_files):
        raise ValueError("review artifact set contains an empty file")
    analysis_manifest_hash = sha256_file(analysis_dir / "SHA256SUMS")
    if (
        review_data.get("source_analysis_sha256s_sha256") != analysis_manifest_hash
        or provenance.get("source_analysis_sha256s_sha256") != analysis_manifest_hash
    ):
        raise ValueError("review does not bind the selected analysis")
    if review_data.get("primary_contrast") != primary:
        raise ValueError("review primary contrast differs from core analysis")
    if review_data.get("numeric_eligibility") != numeric:
        raise ValueError("review numeric eligibility differs from core analysis")
    expected_checkpoint_identity = {
        "checkpoint_hash": checkpoint_hash,
        "task_set_hash": task_set_hash,
        "checkpoint_task_index_sha256": checkpoint_task_index_sha256,
        "root_set_hash": root_set_hash,
        "source_checkpoint_sha256s_sha256": checkpoint_manifest_hash,
    }
    if (
        review_data.get("checkpoint_identity") != expected_checkpoint_identity
        or review_data.get("program_identity") != program
        or review_data.get("source_finalization") != source_finalization
        or review_data.get("source_event_audit_sha256") != event_audit_hash
        or review_data.get("task_count") != 32
        or review_data.get("event_count") != 8_000
    ):
        raise ValueError("review identity differs from the core analysis")
    if (
        review_data.get("accepted_statistical_evidence") is not False
        or review_data.get("source_accepted_statistical_evidence") is not True
        or review_data.get("tail_disposition_required") is not True
        or review_data.get("tail_disposition_choices")
        != list(TAIL_DISPOSITIONS)
        or review_data.get("decision_boundary")
        != {
            "analysis_does_not_select_decision": True,
            "review_does_not_unlock_child": True,
            "review_does_not_submit_slurm": True,
        }
    ):
        raise ValueError("review decision boundary is incomplete")

    if len(loo_rows) != 32:
        raise ValueError("core analysis must contain exactly 32 LOO rows")
    omission_keys = {
        (int(row["omitted_tile_thickness_mm"]), int(row["omitted_seed_block"]))
        for row in loo_rows
    }
    if omission_keys != {
        (thickness, block) for thickness in (4, 24) for block in range(16)
    }:
        raise ValueError("LOO rows do not cover the exact BC-ONLY-S1 blocks")
    point_ratio = float(primary.get("ratio", math.nan))
    ci_low = float(primary.get("ci95_low", math.nan))
    ci_high = float(primary.get("ci95_high", math.nan))
    if point_ratio <= 0 or not all(
        math.isfinite(value) for value in (point_ratio, ci_low, ci_high)
    ):
        raise ValueError("primary ratio or interval is invalid")
    recalculated_h = (ci_high - ci_low) / (2.0 * abs(point_ratio))
    if not math.isclose(
        float(primary.get("relative_half_width", math.nan)),
        recalculated_h,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError("primary relative half-width does not reconcile")
    for row in loo_rows:
        full_ratio = float(row["full_ratio"])
        omitted_ratio = float(row["leave_one_block_out_ratio"])
        reported_shift = float(row["absolute_relative_shift"])
        if not all(
            math.isfinite(value)
            for value in (full_ratio, omitted_ratio, reported_shift)
        ) or not math.isclose(
            full_ratio, point_ratio, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("LOO full ratio does not match the primary contrast")
        expected_shift = abs(omitted_ratio / full_ratio - 1.0)
        if not math.isclose(
            reported_shift, expected_shift, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError("LOO shift does not match its ratio values")
        if row.get("within_10pct") != str(expected_shift <= 0.10):
            raise ValueError("LOO 10-percent flag is inconsistent")
        if row.get("within_20pct") != str(expected_shift <= 0.20):
            raise ValueError("LOO 20-percent flag is inconsistent")
        omitted_thickness = int(row["omitted_tile_thickness_mm"])
        expected_reference = 3750 if omitted_thickness == 4 else 4000
        expected_compared = 3750 if omitted_thickness == 24 else 4000
        if (
            int(row["reference_events_remaining"]) != expected_reference
            or int(row["compared_events_remaining"]) != expected_compared
        ):
            raise ValueError("LOO remaining-event counts are inconsistent")
    maximum = max(loo_rows, key=lambda row: float(row["absolute_relative_shift"]))
    maximum_shift = float(maximum["absolute_relative_shift"])
    if (
        not math.isfinite(maximum_shift)
        or not math.isclose(
            float(primary.get("max_leave_one_block_out_relative_shift", math.nan)),
            maximum_shift,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        or primary.get("leave_one_block_out_evaluations") != 32
        or review_data.get("maximum_loo") != dict(maximum)
    ):
        raise ValueError("primary/review maximum LOO does not reconcile with block_loo.csv")

    def sipm_rows(
        rows: Sequence[Mapping[str, str]], expected_state: str
    ) -> list[dict[str, str]]:
        selected = [dict(row) for row in rows if row.get("metric") == "sipm"]
        if (
            len(selected) != 2
            or {row.get("tile_thickness_mm") for row in selected} != {"4", "24"}
            or any(row.get("state_id") != expected_state for row in selected)
        ):
            raise ValueError(f"{expected_state} SiPM tail rows have the wrong shape")
        for row in selected:
            for field in (
                "zero_fraction",
                "top_1pct_sum_fraction",
                "top_5pct_sum_fraction",
                "max",
            ):
                if not math.isfinite(float(row[field])):
                    raise ValueError(f"non-finite {expected_state} tail diagnostic")
            if not row.get("maximum_logical_task_id"):
                raise ValueError(f"{expected_state} maximum-event provenance is absent")
        return selected

    current_tails = sipm_rows(current_distribution, STATE_ID)
    pilot_tails = sipm_rows(pilot_distribution, "sealed-pilot")
    if (
        review_data.get("current_sipm_tail_diagnostics") != current_tails
        or review_data.get("sipm_tail_diagnostics") != current_tails
        or review_data.get("pilot_sipm_tail_diagnostics") != pilot_tails
    ):
        raise ValueError("review tail diagnostics do not reconcile with core CSV files")

    h = recalculated_h
    pilot = config.get("pilot_baseline")
    if not isinstance(pilot, dict):
        raise ValueError("analysis lacks the sealed-pilot baseline")
    pilot_h = float(pilot.get("relative_half_width", math.nan))
    narrowing = h < pilot_h
    if (
        not math.isfinite(pilot_h)
        or not math.isclose(
            float(primary.get("pilot_relative_half_width", math.nan)),
            pilot_h,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        or primary.get("interval_narrowing") is not narrowing
    ):
        raise ValueError("interval narrowing does not reconcile with the sealed pilot")
    trajectory = {row.get("sample_id"): row for row in trajectory_rows}
    if set(trajectory) != {"sealed-pilot", STATE_ID}:
        raise ValueError("precision trajectory has the wrong samples")
    if (
        not math.isclose(
            float(trajectory["sealed-pilot"]["relative_half_width"]),
            pilot_h,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        or not math.isclose(
            float(trajectory[STATE_ID]["relative_half_width"]),
            h,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        or not math.isclose(
            float(trajectory[STATE_ID]["ratio"]),
            point_ratio,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    ):
        raise ValueError("precision trajectory does not reconcile with core metrics")
    if h <= 0.10 and maximum_shift <= 0.10:
        expected_choices = ["stop-success", "pause-review"]
        expected_branch = "precision-success"
    elif h > 0.10 and maximum_shift <= 0.20 and narrowing:
        expected_choices = ["continue", "pause-review"]
        expected_branch = "continue-eligible"
    else:
        expected_choices = ["pause-review"]
        expected_branch = "pause-only"
    if (
        not math.isfinite(h)
        or numeric.get("numeric_eligible_decisions") != expected_choices
        or numeric.get("branch") != expected_branch
    ):
        raise ValueError("numeric eligibility does not reconcile with core metrics")
    return config, primary, numeric, review_data


def _open_lock(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    if not os.path.isfile(path):
        os.close(descriptor)
        raise ValueError("progression lock is not a regular file")
    return descriptor


def main() -> int:
    args = parse_args()
    analysis_dir = args.analysis_dir.expanduser().resolve()
    review_dir = args.review_dir.expanduser().resolve()
    temp_dir: Path | None = None
    descriptor: int | None = None
    try:
        reviewer = validate_reviewer(args.reviewer)
        rationale = read_rationale(args.rationale_file)
        config, primary, numeric, review_data = validate_inputs(
            analysis_dir, review_dir
        )
        records_root = decisions_root(analysis_dir)
        sequence, previous = validate_chain(records_root)
        decision = build_decision(
            analysis_dir=analysis_dir,
            review_dir=review_dir,
            config=config,
            primary=primary,
            numeric=numeric,
            review_data=review_data,
            tail_disposition=args.tail_disposition,
            selected_decision=args.decision,
            reviewer=reviewer,
            rationale=rationale,
            sequence=sequence + 1,
            previous_hash=previous,
        )
        if args.check_only:
            print("steel-module progression decision check: PASS")
            print(f"state: {STATE_ID}")
            print(
                "allowed decisions: "
                + ", ".join(decision["final_allowed_decisions"])
            )
            print(f"selected decision: {args.decision}")
            print("No decision record or authorization state was written.")
            return 0
        records_root.mkdir(parents=True, exist_ok=True)
        descriptor = _open_lock(records_root / ".progression.lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        config, primary, numeric, review_data = validate_inputs(
            analysis_dir, review_dir
        )
        sequence, previous = validate_chain(records_root)
        decision = build_decision(
            analysis_dir=analysis_dir,
            review_dir=review_dir,
            config=config,
            primary=primary,
            numeric=numeric,
            review_data=review_data,
            tail_disposition=args.tail_disposition,
            selected_decision=args.decision,
            reviewer=reviewer,
            rationale=rationale,
            sequence=sequence + 1,
            previous_hash=previous,
        )
        digest = str(decision["decision_hash"])
        output_dir = records_root / f"{sequence + 1:04d}-{digest[:12]}"
        if output_dir.exists():
            raise ValueError(f"refusing to overwrite decision record: {output_dir}")
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=records_root)
        )
        with (temp_dir / "decision.json").open("w", encoding="utf-8") as stream:
            json.dump(decision, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        with (temp_dir / "rationale.txt").open("w", encoding="utf-8") as stream:
            stream.write(rationale)
            stream.flush()
            os.fsync(stream.fileno())
        write_recursive_checksums(temp_dir)
        verify_checksum_manifest(temp_dir, required=("decision.json", "rationale.txt"))
        os.replace(temp_dir, output_dir)
        temp_dir = None
        directory_descriptor = os.open(records_root, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        validate_chain(records_root)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot record steel-module progression decision: {exc}", file=sys.stderr)
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_dir is not None and temp_dir.exists():
            shutil.rmtree(temp_dir)
    print(f"Recorded immutable progression decision: {output_dir}")
    print("No child was modified or submitted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
