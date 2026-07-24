#!/usr/bin/env python3
"""Analyze finalized steel-module stack pilot and optional one-shot production."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import uproot

from realistic_neutron_campaign_lib import canonical_json, resolve_campaign_path, sha256_file, verify_checksum_manifest
from steel_module_stack_campaign_lib import (
    FINALIZED_REQUIRED_FILES,
    SENSORS_PER_LAYER,
    STACK_LAYERS,
    TILE_THICKNESSES_MM,
    load_campaign,
)
from steel_module_stack_event_audit import audit_root_file


BOOTSTRAP_SEED = 20260715
BOOTSTRAP_RESAMPLES = 10_000
CI_LEVEL = 0.95
PRECISION_TARGET = 0.10
SAFETY_FACTOR = 1.25
MAX_EVENTS = 120_000
MAX_TASKS = 1_000
BLOCK_CHOICES = (250, 100, 50, 25, 10)


@dataclass
class TaskData:
    logical_task_id: str
    stage: str
    thickness: int
    block: int
    events: int
    generated: np.ndarray
    scintillation: np.ndarray
    cerenkov: np.ndarray
    local: np.ndarray
    all_origin: np.ndarray
    steel_edep: np.ndarray
    tile_edep: np.ndarray
    charged_entries: np.ndarray
    neutron_elastic: np.ndarray
    neutron_inelastic: np.ndarray
    neutron_capture: np.ndarray
    primary_tile_entry: np.ndarray
    transfer_totals: np.ndarray
    root_path: str
    root_sha256: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-campaign-dir", required=True, type=Path)
    parser.add_argument("--production-campaign-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def load_task(row: dict[str, str], campaign_dir: Path) -> TaskData:
    root_path = resolve_campaign_path(campaign_dir, row["root"])
    if sha256_file(root_path) != row["root_sha256"]:
        raise ValueError(f"ROOT checksum mismatch: {row['logical_task_id']}")
    events = int(row["events"])
    audit_root_file(str(root_path), events, context=row["logical_task_id"])
    with uproot.open(root_path) as root:
        layers = root["stack_layers"].arrays(library="np")
        transfers = root["stack_transfers"].arrays(library="np")
    order = np.lexsort((layers["layer"], layers["event_id"]))
    if not np.array_equal(order, np.arange(len(order))):
        layers = {key: values[order] for key, values in layers.items()}
    shape = (events, STACK_LAYERS)
    generated = layers["generated_optical_photons"].reshape(shape).astype(np.float64)
    scintillation = layers["scintillation_photons"].reshape(shape).astype(np.float64)
    cerenkov = layers["cerenkov_photons"].reshape(shape).astype(np.float64)
    local = np.stack(
        [
            layers[f"sensor_{sensor}_local_origin_detected_photons"].reshape(shape)
            for sensor in range(SENSORS_PER_LAYER)
        ],
        axis=2,
    ).astype(np.float64)
    all_origin = np.stack(
        [
            layers[f"sensor_{sensor}_all_origin_detected_photons"].reshape(shape)
            for sensor in range(SENSORS_PER_LAYER)
        ],
        axis=2,
    ).astype(np.float64)
    transfer_totals = np.zeros((STACK_LAYERS, STACK_LAYERS, SENSORS_PER_LAYER), dtype=np.int64)
    for event, origin, destination, sensor, count in zip(
        transfers["event_id"], transfers["origin_layer"], transfers["destination_layer"],
        transfers["local_sensor"], transfers["detected_photons"],
    ):
        if int(origin) >= 0:
            transfer_totals[int(origin), int(destination), int(sensor)] += int(count)
    return TaskData(
        logical_task_id=row["logical_task_id"],
        stage=row["stage"],
        thickness=int(row["tile_thickness_mm"]),
        block=int(row["seed_block"]),
        events=events,
        generated=generated,
        scintillation=scintillation,
        cerenkov=cerenkov,
        local=local,
        all_origin=all_origin,
        steel_edep=layers["steel_edep_mev"].reshape(shape).astype(np.float64),
        tile_edep=layers["tile_edep_mev"].reshape(shape).astype(np.float64),
        charged_entries=layers["charged_tile_entry_count"].reshape(shape).astype(np.float64),
        neutron_elastic=layers["primary_neutron_elastic_count"].reshape(shape).astype(np.float64),
        neutron_inelastic=layers["primary_neutron_inelastic_count"].reshape(shape).astype(np.float64),
        neutron_capture=layers["primary_neutron_capture_count"].reshape(shape).astype(np.float64),
        primary_tile_entry=layers["primary_neutron_tile_entry_valid"].reshape(shape).astype(np.float64),
        transfer_totals=transfer_totals,
        root_path=str(root_path),
        root_sha256=row["root_sha256"],
    )


def load_finalized(directory: Path, expected_stage: str) -> tuple[Any, list[TaskData]]:
    bundle = load_campaign(directory, verify_external_artifacts=False)
    if bundle.manifest.get("stage") != expected_stage:
        raise ValueError(f"expected {expected_stage} campaign")
    finalized = bundle.directory / "finalized"
    verify_checksum_manifest(finalized, required=FINALIZED_REQUIRED_FILES)
    validation = json.loads((finalized / "validation_report.json").read_text(encoding="utf-8"))
    if validation.get("valid") is not True or validation.get("selected_tasks") != len(bundle.tasks):
        raise ValueError("stack finalization is not valid and complete")
    rows = read_tsv(finalized / "task_index.tsv")
    if len(rows) != len(bundle.tasks):
        raise ValueError("stack finalized task-index count mismatch")
    tasks = [load_task(row, bundle.directory) for row in rows]
    with (finalized / "configuration_summary.csv").open(encoding="utf-8", newline="") as stream:
        configuration_rows = list(csv.DictReader(stream))
    if len(configuration_rows) != len({task.thickness for task in tasks}):
        raise ValueError("stack finalized configuration-summary count mismatch")
    by_thickness = {int(row["tile_thickness_mm"]): row for row in configuration_rows}
    for thickness in {task.thickness for task in tasks}:
        selected = [task for task in tasks if task.thickness == thickness]
        row = by_thickness.get(thickness)
        if row is None:
            raise ValueError(f"stack finalized summary lacks {thickness} mm")
        expected = {
            "events": sum(task.events for task in selected),
            "generated_optical_photons": int(sum(task.generated.sum() for task in selected)),
            "local_origin_detected_photons": int(sum(task.local.sum() for task in selected)),
            "all_origin_detected_photons": int(sum(task.all_origin.sum() for task in selected)),
        }
        for field, value in expected.items():
            if int(row[field]) != value:
                raise ValueError(f"stack finalized {field} does not reconcile for {thickness} mm")
    return bundle, tasks


def combine(tasks: list[TaskData], field: str, thickness: int) -> np.ndarray:
    arrays = [getattr(task, field) for task in tasks if task.thickness == thickness]
    if not arrays:
        raise ValueError(f"no tasks for {thickness} mm")
    return np.concatenate(arrays, axis=0)


def percentile_interval(values: np.ndarray) -> tuple[float, float, int]:
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return math.nan, math.nan, 0
    low, high = np.quantile(valid, (0.025, 0.975), method="linear")
    return float(low), float(high), int(valid.size)


def bootstrap_feature_sums(features: np.ndarray, thickness: int) -> np.ndarray:
    events = features.shape[0]
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence([BOOTSTRAP_SEED, thickness])))
    output = np.empty((BOOTSTRAP_RESAMPLES, features.shape[1]), dtype=np.float64)
    probabilities = np.full(events, 1.0 / events, dtype=np.float64)
    chunk_size = max(8, min(128, 2_000_000 // max(events, 1)))
    for start in range(0, BOOTSTRAP_RESAMPLES, chunk_size):
        stop = min(start + chunk_size, BOOTSTRAP_RESAMPLES)
        weights = rng.multinomial(events, probabilities, size=stop - start).astype(np.float64)
        output[start:stop] = weights @ features
    return output


def ratio(samples: np.ndarray, numerator: int, denominator: int) -> np.ndarray:
    result = np.full(samples.shape[0], np.nan, dtype=np.float64)
    np.divide(samples[:, numerator], samples[:, denominator], out=result, where=samples[:, denominator] > 0)
    return result


def distribution_row(
    stage: str,
    thickness: int,
    metric: str,
    values: np.ndarray,
    identities: list[tuple[str, int]],
) -> dict[str, object]:
    values = np.asarray(values, dtype=np.float64)
    if len(values) != len(identities):
        raise ValueError("distribution event identities do not align")
    total = float(values.sum())
    sorted_values = np.sort(values)
    positive = values[values > 0]
    def top_share(fraction: float) -> float:
        count = max(1, math.ceil(fraction * len(values)))
        return float(sorted_values[-count:].sum() / total) if total else math.nan
    quantiles = np.quantile(values, (0.5, 0.75, 0.9, 0.95, 0.99), method="linear")
    max_index = int(np.argmax(values))
    return {
        "stage": stage, "tile_thickness_mm": thickness, "metric": metric,
        "events": len(values), "positive_events": int(np.count_nonzero(values)),
        "zero_fraction": float(np.mean(values == 0)), "sum": total,
        "mean": float(np.mean(values)), "rms": float(np.sqrt(np.mean(values * values))),
        "population_standard_deviation": float(np.std(values)),
        "standard_error": float(np.std(values) / math.sqrt(len(values))),
        "positive_only_valid": positive.size > 0,
        "positive_only_sum": float(positive.sum()),
        "positive_only_mean": float(positive.mean()) if positive.size else math.nan,
        "positive_only_rms": float(np.sqrt(np.mean(positive * positive))) if positive.size else math.nan,
        "positive_only_population_standard_deviation": float(np.std(positive)) if positive.size else math.nan,
        "p50": float(quantiles[0]), "p75": float(quantiles[1]),
        "p90": float(quantiles[2]), "p95": float(quantiles[3]),
        "p99": float(quantiles[4]), "max": float(values[max_index]),
        "max_logical_task_id": identities[max_index][0],
        "max_event_entry": identities[max_index][1],
        "tail_share_valid": total > 0,
        "top_1pct_share": top_share(0.01), "top_5pct_share": top_share(0.05),
    }


def relative_shift(value: float, reference: float) -> float:
    return abs(value - reference) / abs(reference) if reference else math.inf


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"refusing empty CSV: {path.name}")
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_checksums(directory: Path) -> None:
    with (directory / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(item for item in directory.iterdir() if item.is_file() and item.name != "SHA256SUMS"):
            stream.write(f"{sha256_file(path)}  {path.name}\n")


def analyze(pilot_bundle: Any, pilot_tasks: list[TaskData], production_bundle: Any | None, production_tasks: list[TaskData]) -> dict[str, Any]:
    all_tasks = pilot_tasks + production_tasks
    if len({(task.thickness, task.block) for task in all_tasks}) != len(all_tasks):
        raise ValueError("pilot/production task block overlap")
    all_seeds = [seed for bundle in ([pilot_bundle] + ([production_bundle] if production_bundle else [])) for task in bundle.tasks for seed in (task.seed1, task.seed2)]
    if len(all_seeds) != len(set(all_seeds)):
        raise ValueError("pilot/production seed overlap")
    if production_bundle:
        if pilot_bundle.git_commit != production_bundle.git_commit:
            raise ValueError("pilot/production simulation commit mismatch")
        for key in ("image", "g4_data_manifest", "build_artifact"):
            pilot_record = pilot_bundle.environment.get(key)
            production_record = production_bundle.environment.get(key)
            if not isinstance(pilot_record, dict) or not isinstance(production_record, dict) or pilot_record.get("sha256") != production_record.get("sha256"):
                raise ValueError(f"pilot/production {key} identity mismatch")
        for key in ("geometry_contract", "source_contract", "causal_contract"):
            if canonical_json(pilot_bundle.manifest.get(key)) != canonical_json(production_bundle.manifest.get(key)):
                raise ValueError(f"pilot/production {key} mismatch")
        sizing_record = production_bundle.manifest.get("artifacts", {}).get("sizing_plan")
        if not isinstance(sizing_record, dict):
            raise ValueError("production campaign lacks sizing-plan binding")
        sizing = json.loads(resolve_campaign_path(production_bundle.directory, sizing_record["path"]).read_text(encoding="utf-8"))
        if (
            sizing.get("pilot_campaign_id") != pilot_bundle.campaign_id
            or sizing.get("pilot_plan_hash") != pilot_bundle.plan_hash
            or sizing.get("pilot_git_commit") != pilot_bundle.git_commit
            or sizing.get("pilot_finalized_sha256") != sha256_file(pilot_bundle.directory / "finalized" / "SHA256SUMS")
        ):
            raise ValueError("production sizing plan is not bound to this finalized pilot")
        registry_record = production_bundle.manifest.get("artifacts", {}).get("excluded_seed_registry")
        if not isinstance(registry_record, dict):
            raise ValueError("production campaign lacks excluded pilot seeds")
        registry = json.loads(resolve_campaign_path(production_bundle.directory, registry_record["path"]).read_text(encoding="utf-8"))
        pilot_seeds = {seed for task in pilot_bundle.tasks for seed in (task.seed1, task.seed2)}
        if not pilot_seeds.issubset(set(registry.get("excluded_seeds", []))):
            raise ValueError("production excluded-seed registry does not contain all pilot seeds")
    stage_label = "pilot+production" if production_tasks else "pilot"
    thickness_rows: list[dict[str, object]] = []
    layer_rows: list[dict[str, object]] = []
    profile_rows: list[dict[str, object]] = []
    transfer_rows: list[dict[str, object]] = []
    distribution_rows: list[dict[str, object]] = []
    loo_rows: list[dict[str, object]] = []
    sizing_rows: list[dict[str, object]] = []

    for thickness in TILE_THICKNESSES_MM:
        thickness_tasks = [task for task in all_tasks if task.thickness == thickness]
        generated = combine(all_tasks, "generated", thickness)
        scintillation = combine(all_tasks, "scintillation", thickness)
        cerenkov = combine(all_tasks, "cerenkov", thickness)
        local = combine(all_tasks, "local", thickness)
        all_origin = combine(all_tasks, "all_origin", thickness)
        steel = combine(all_tasks, "steel_edep", thickness)
        tile = combine(all_tasks, "tile_edep", thickness)
        charged = combine(all_tasks, "charged_entries", thickness)
        neutron_elastic = combine(all_tasks, "neutron_elastic", thickness)
        neutron_inelastic = combine(all_tasks, "neutron_inelastic", thickness)
        neutron_capture = combine(all_tasks, "neutron_capture", thickness)
        primary_tile_entry = combine(all_tasks, "primary_tile_entry", thickness)
        events = generated.shape[0]
        ones = np.ones((events, 1), dtype=np.float64)
        feature_parts = [ones, generated.sum(axis=1, keepdims=True), local.sum(axis=(1, 2), keepdims=True).reshape(events, 1), all_origin.sum(axis=(1, 2), keepdims=True).reshape(events, 1), generated]
        feature_parts.extend(local[:, layer, sensor : sensor + 1] for layer in range(STACK_LAYERS) for sensor in range(SENSORS_PER_LAYER))
        feature_parts.extend(all_origin[:, layer, sensor : sensor + 1] for layer in range(STACK_LAYERS) for sensor in range(SENSORS_PER_LAYER))
        features = np.concatenate(feature_parts, axis=1)
        samples = bootstrap_feature_sums(features, thickness)
        # indices: 0 events, 1 global generated, 2 global local, 3 global all,
        # 4..13 layer generated, 14..33 local, 34..53 all-origin.
        point_generated = float(generated.sum())
        point_local = float(local.sum())
        point_all = float(all_origin.sum())
        primary_metrics = {
            "generated_photons_per_neutron": (point_generated / events, ratio(samples, 1, 0)),
            "local_collection": (point_local / point_generated if point_generated else math.nan, ratio(samples, 2, 1)),
            "local_net_photons_per_neutron": (point_local / events, ratio(samples, 2, 0)),
            "all_origin_collection": (point_all / point_generated if point_generated else math.nan, ratio(samples, 3, 1)),
            "all_origin_net_photons_per_neutron": (point_all / events, ratio(samples, 3, 0)),
        }
        intervals: dict[str, tuple[float, float, int]] = {name: percentile_interval(values) for name, (_, values) in primary_metrics.items()}
        local_ci = intervals["local_collection"]
        net_ci = intervals["local_net_photons_per_neutron"]
        local_point = primary_metrics["local_collection"][0]
        net_point = primary_metrics["local_net_photons_per_neutron"][0]

        task_totals = []
        for task in thickness_tasks:
            task_totals.append((task, float(task.generated.sum()), float(task.local.sum()), task.events))
        loo_max_collection = 0.0
        loo_max_net = 0.0
        for task, task_gen, task_local, task_events in task_totals:
            remaining_gen = point_generated - task_gen
            remaining_local = point_local - task_local
            remaining_events = events - task_events
            collection = remaining_local / remaining_gen if remaining_gen else math.nan
            net = remaining_local / remaining_events if remaining_events else math.nan
            collection_shift = relative_shift(collection, local_point)
            net_shift = relative_shift(net, net_point)
            loo_max_collection = max(loo_max_collection, collection_shift)
            loo_max_net = max(loo_max_net, net_shift)
            loo_rows.extend(
                [
                    {"stage": stage_label, "tile_thickness_mm": thickness, "omitted_logical_task_id": task.logical_task_id, "omitted_seed_block": task.block, "metric": "local_collection", "full_estimate": local_point, "leave_one_out_estimate": collection, "relative_shift": collection_shift, "within_10pct": collection_shift <= 0.10, "within_20pct": collection_shift <= 0.20},
                    {"stage": stage_label, "tile_thickness_mm": thickness, "omitted_logical_task_id": task.logical_task_id, "omitted_seed_block": task.block, "metric": "local_net_photons_per_neutron", "full_estimate": net_point, "leave_one_out_estimate": net, "relative_shift": net_shift, "within_10pct": net_shift <= 0.10, "within_20pct": net_shift <= 0.20},
                ]
            )
        thickness_rows.append(
            {
                "stage": stage_label, "tile_thickness_mm": thickness, "tasks": len(thickness_tasks), "events": events,
                "generated_photons_per_neutron": primary_metrics["generated_photons_per_neutron"][0],
                "generated_ci95_low": intervals["generated_photons_per_neutron"][0], "generated_ci95_high": intervals["generated_photons_per_neutron"][1],
                "local_collection": local_point, "local_collection_ci95_low": local_ci[0], "local_collection_ci95_high": local_ci[1], "local_collection_valid_resamples": local_ci[2],
                "local_net_photons_per_neutron": net_point, "local_net_ci95_low": net_ci[0], "local_net_ci95_high": net_ci[1], "local_net_valid_resamples": net_ci[2],
                "all_origin_collection": primary_metrics["all_origin_collection"][0],
                "all_origin_collection_ci95_low": intervals["all_origin_collection"][0], "all_origin_collection_ci95_high": intervals["all_origin_collection"][1],
                "all_origin_net_photons_per_neutron": point_all / events,
                "all_origin_net_ci95_low": intervals["all_origin_net_photons_per_neutron"][0], "all_origin_net_ci95_high": intervals["all_origin_net_photons_per_neutron"][1],
                "cross_layer_fraction": (point_all - point_local) / point_all if point_all else math.nan,
                "unknown_origin_detected_photons": 0,
                "unknown_origin_fraction_valid": point_all > 0,
                "unknown_origin_fraction": 0.0 if point_all > 0 else math.nan,
                "max_local_collection_loo_shift": loo_max_collection, "max_local_net_loo_shift": loo_max_net,
            }
        )
        distribution_rows.extend(
            [
                distribution_row(stage_label, thickness, "generated_optical_photons", generated.sum(axis=1), [(task.logical_task_id, entry) for task in thickness_tasks for entry in range(task.events)]),
                distribution_row(stage_label, thickness, "local_origin_detected_photons", local.sum(axis=(1, 2)), [(task.logical_task_id, entry) for task in thickness_tasks for entry in range(task.events)]),
                distribution_row(stage_label, thickness, "all_origin_detected_photons", all_origin.sum(axis=(1, 2)), [(task.logical_task_id, entry) for task in thickness_tasks for entry in range(task.events)]),
            ]
        )
        matrix = sum((task.transfer_totals for task in thickness_tasks), np.zeros((STACK_LAYERS, STACK_LAYERS, SENSORS_PER_LAYER), dtype=np.int64))
        for layer in range(STACK_LAYERS):
            gen_point = float(generated[:, layer].sum())
            gen_samples = ratio(samples, 4 + layer, 0)
            gen_ci = percentile_interval(gen_samples)
            incoming_total = int(matrix[:, layer, :].sum())
            incoming_cross = incoming_total - int(matrix[layer, layer, :].sum())
            outgoing_total = int(matrix[layer, :, :].sum())
            outgoing_cross = outgoing_total - int(matrix[layer, layer, :].sum())
            profile_rows.append(
                {
                    "stage": stage_label, "tile_thickness_mm": thickness, "layer": layer, "events": events,
                    "generated_photons_per_neutron": gen_point / events,
                    "generated_ci95_low": gen_ci[0], "generated_ci95_high": gen_ci[1],
                    "scintillation_photons_per_neutron": float(scintillation[:, layer].sum()) / events,
                    "cerenkov_photons_per_neutron": float(cerenkov[:, layer].sum()) / events,
                    "steel_edep_mev_per_neutron": float(steel[:, layer].sum()) / events,
                    "tile_edep_mev_per_neutron": float(tile[:, layer].sum()) / events,
                    "charged_entries_per_neutron": float(charged[:, layer].sum()) / events,
                    "primary_neutron_elastic_per_neutron": float(neutron_elastic[:, layer].sum()) / events,
                    "primary_neutron_inelastic_per_neutron": float(neutron_inelastic[:, layer].sum()) / events,
                    "primary_neutron_capture_per_neutron": float(neutron_capture[:, layer].sum()) / events,
                    "primary_neutron_tile_entry_fraction": float(primary_tile_entry[:, layer].sum()) / events,
                    "incoming_detected_photons": incoming_total,
                    "cross_in_detected_photons": incoming_cross,
                    "cross_in_fraction": incoming_cross / incoming_total if incoming_total else math.nan,
                    "outgoing_detected_photons": outgoing_total,
                    "cross_out_detected_photons": outgoing_cross,
                    "cross_out_fraction": outgoing_cross / outgoing_total if outgoing_total else math.nan,
                    "unknown_origin_detected_photons": 0,
                    "unknown_origin_fraction_valid": incoming_total > 0,
                    "unknown_origin_fraction": 0.0 if incoming_total > 0 else math.nan,
                }
            )
            for sensor in range(SENSORS_PER_LAYER):
                feature_offset = layer * SENSORS_PER_LAYER + sensor
                local_index = 14 + feature_offset
                all_index = 34 + feature_offset
                local_samples = ratio(samples, local_index, 4 + layer)
                local_net_samples = ratio(samples, local_index, 0)
                all_net_samples = ratio(samples, all_index, 0)
                local_collection_ci = percentile_interval(local_samples)
                local_net_ci = percentile_interval(local_net_samples)
                all_net_ci = percentile_interval(all_net_samples)
                local_count = float(local[:, layer, sensor].sum())
                all_count = float(all_origin[:, layer, sensor].sum())
                layer_rows.append(
                    {
                        "stage": stage_label, "tile_thickness_mm": thickness, "layer": layer, "local_sensor": sensor, "global_copy": 2 * layer + sensor, "events": events,
                        "generated_layer_photons": gen_point, "local_detected_photons": local_count, "all_origin_detected_photons": all_count,
                        "local_collection_valid": gen_point > 0,
                        "local_collection": local_count / gen_point if gen_point else math.nan,
                        "local_collection_ci95_low": local_collection_ci[0], "local_collection_ci95_high": local_collection_ci[1], "local_collection_valid_resamples": local_collection_ci[2],
                        "local_net_photons_per_neutron": local_count / events,
                        "local_net_ci95_low": local_net_ci[0], "local_net_ci95_high": local_net_ci[1],
                        "all_origin_net_photons_per_neutron": all_count / events,
                        "all_origin_net_ci95_low": all_net_ci[0], "all_origin_net_ci95_high": all_net_ci[1],
                        "cross_in_fraction": (all_count - local_count) / all_count if all_count else math.nan,
                        "low_statistics": local_collection_ci[2] < 9900 or local_count < 100,
                    }
                )
        for origin in range(STACK_LAYERS):
            origin_generated = float(generated[:, origin].sum())
            origin_detected = int(matrix[origin].sum())
            for destination in range(STACK_LAYERS):
                for sensor in range(SENSORS_PER_LAYER):
                    count = int(matrix[origin, destination, sensor])
                    transfer_rows.append(
                        {
                            "stage": stage_label, "tile_thickness_mm": thickness,
                            "origin_layer": origin, "destination_layer": destination,
                            "local_sensor": sensor, "global_copy": 2 * destination + sensor,
                            "detected_photons": count,
                            "per_origin_generated_photon": count / origin_generated if origin_generated else math.nan,
                            "fraction_of_origin_detected": count / origin_detected if origin_detected else math.nan,
                            "same_layer": origin == destination,
                        }
                    )
        if not production_tasks:
            def sizing(metric: str, point: float, ci: tuple[float, float, int], loo: float) -> dict[str, object]:
                half_width = (ci[1] - ci[0]) / (2 * abs(point)) if point and all(math.isfinite(x) for x in ci[:2]) else math.inf
                raw = events * (half_width / PRECISION_TARGET) ** 2 if math.isfinite(half_width) else math.inf
                target = math.ceil(max(events, SAFETY_FACTOR * raw) / pilot_bundle.manifest["events_per_task"]) * pilot_bundle.manifest["events_per_task"] if math.isfinite(raw) else math.inf
                if half_width <= PRECISION_TARGET and loo <= 0.10:
                    target = events
                return {
                    "metric": metric,
                    "point_estimate": point if math.isfinite(point) else None,
                    "ci95_low": ci[0] if math.isfinite(ci[0]) else None,
                    "ci95_high": ci[1] if math.isfinite(ci[1]) else None,
                    "valid_resamples": ci[2],
                    "relative_half_width": half_width if math.isfinite(half_width) else None,
                    "maximum_loo_shift": loo if math.isfinite(loo) else None,
                    "projected_events_before_safety_factor": raw if math.isfinite(raw) else None,
                    "target_events": int(target) if math.isfinite(target) else None,
                }
            candidates = [sizing("local_collection", local_point, local_ci, loo_max_collection), sizing("local_net_photons_per_neutron", net_point, net_ci, loo_max_net)]
            controlling = max(
                candidates,
                key=lambda row: math.inf if row["target_events"] is None else float(row["target_events"]),
            )
            target_events = controlling["target_events"]
            block_events = pilot_bundle.manifest["events_per_task"]
            additional_blocks = (
                int((int(target_events) - events) / block_events)
                if target_events is not None
                else -1
            )
            sizing_rows.append({"tile_thickness_mm": thickness, "pilot_events": events, "events_per_task": block_events, "metrics": candidates, "controlling_metric": controlling["metric"], "target_events": target_events, "first_additional_seed_block": 4, "additional_blocks": additional_blocks, "pilot_gate_valid": all(row["valid_resamples"] >= 9900 for row in candidates) and max(loo_max_collection, loo_max_net) <= 0.20 and target_events is not None})

    sizing_plan: dict[str, Any] | None = None
    if not production_tasks:
        total_additional_tasks = sum(max(0, int(row["additional_blocks"])) for row in sizing_rows)
        pilot_events = sum(task.events for task in pilot_tasks)
        projected_total_events = pilot_events + total_additional_tasks * pilot_bundle.manifest["events_per_task"]
        gate_valid = all(row["pilot_gate_valid"] and int(row["additional_blocks"]) >= 0 for row in sizing_rows)
        within_caps = projected_total_events <= MAX_EVENTS and total_additional_tasks <= MAX_TASKS
        sizing_plan = {
            "schema_version": "steel-module-stack-sizing-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "pilot_campaign_id": pilot_bundle.campaign_id,
            "pilot_plan_hash": pilot_bundle.plan_hash,
            "pilot_git_commit": pilot_bundle.git_commit,
            "pilot_finalized_sha256": sha256_file(pilot_bundle.directory / "finalized" / "SHA256SUMS"),
            "automatic_acceptance": False, "bootstrap_seed": BOOTSTRAP_SEED, "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "precision_target": PRECISION_TARGET, "safety_factor": SAFETY_FACTOR,
            "events_per_task": pilot_bundle.manifest["events_per_task"],
            "scheduler_contract": pilot_bundle.manifest["scheduler_contract"],
            "pilot_events": pilot_events, "additional_tasks": total_additional_tasks,
            "projected_cumulative_events": projected_total_events,
            "hard_caps": {"cumulative_events": MAX_EVENTS, "production_tasks": MAX_TASKS},
            "pilot_gate_valid": gate_valid, "within_hard_caps": within_caps,
            "production_plan_permitted_for_human_review": gate_valid and within_caps,
            "thicknesses": sizing_rows,
            "formula": "ceil_to_block(max(N_pilot,1.25*N_pilot*(h/0.10)^2))",
            "no_second_automatic_production_round": True,
        }
    return {
        "stage_label": stage_label, "tasks": all_tasks,
        "thickness_rows": thickness_rows, "layer_rows": layer_rows,
        "profile_rows": profile_rows, "transfer_rows": transfer_rows,
        "distribution_rows": distribution_rows, "loo_rows": loo_rows,
        "sizing_plan": sizing_plan,
    }


def main() -> int:
    args = parse_args()
    try:
        pilot_bundle, pilot_tasks = load_finalized(args.pilot_campaign_dir, "pilot")
        if args.production_campaign_dir:
            production_bundle, production_tasks = load_finalized(args.production_campaign_dir, "production")
        else:
            production_bundle, production_tasks = None, []
        output = (
            args.output_dir.expanduser().resolve()
            if args.output_dir
            else (production_bundle.directory if production_bundle else pilot_bundle.directory) / "finalized" / "stack-analysis"
        )
        if output.exists():
            raise ValueError(f"refusing to overwrite analysis: {output}")
        repo = Path(__file__).resolve().parents[2]
        analysis_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.strip()
        analysis_branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=repo, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.strip()
        dirty_paths = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.splitlines()
        accepted_input = pilot_bundle.environment.get("accepted_statistical_evidence") is True and (production_bundle is None or production_bundle.environment.get("accepted_statistical_evidence") is True)
        if accepted_input and dirty_paths:
            raise ValueError("accepted stack analysis requires a clean analysis checkout")
        result = analyze(pilot_bundle, pilot_tasks, production_bundle, production_tasks)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        try:
            write_csv(temp / "thickness_summary.csv", result["thickness_rows"])
            write_csv(temp / "layer_sensor_estimates.csv", result["layer_rows"])
            write_csv(temp / "longitudinal_profiles.csv", result["profile_rows"])
            write_csv(temp / "transfer_matrix.csv", result["transfer_rows"])
            write_csv(temp / "distribution_diagnostics.csv", result["distribution_rows"])
            write_csv(temp / "loo_diagnostics.csv", result["loo_rows"])
            if result["sizing_plan"] is not None:
                (temp / "production_sizing.json").write_text(json.dumps(result["sizing_plan"], indent=2, allow_nan=False) + "\n", encoding="utf-8")
            config = {
                "schema_version": "steel-module-stack-analysis-v1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "accepted_statistical_evidence": accepted_input and not dirty_paths,
                "analysis": {
                    "git_commit": analysis_commit,
                    "git_branch": analysis_branch,
                    "dirty": bool(dirty_paths),
                    "dirty_paths": dirty_paths,
                    "analyzer_sha256": sha256_file(Path(__file__).resolve()),
                    "python_version": sys.version.split()[0],
                    "numpy_version": np.__version__,
                    "uproot_version": uproot.__version__,
                },
                "pilot": {"campaign_id": pilot_bundle.campaign_id, "plan_hash": pilot_bundle.plan_hash, "git_commit": pilot_bundle.git_commit},
                "pilot_finalized_sha256": sha256_file(pilot_bundle.directory / "finalized" / "SHA256SUMS"),
                "production": None if production_bundle is None else {"campaign_id": production_bundle.campaign_id, "plan_hash": production_bundle.plan_hash, "git_commit": production_bundle.git_commit},
                "production_finalized_sha256": None if production_bundle is None else sha256_file(production_bundle.directory / "finalized" / "SHA256SUMS"),
                "event_count": sum(task.events for task in result["tasks"]), "task_count": len(result["tasks"]),
                "configuration_count": len(result["thickness_rows"]),
                "finalized_additive_totals_reconciliation": "passed",
                "unknown_origin_detection_audit": "passed_zero",
                "bootstrap": {"algorithm": "event_bootstrap_multinomial_weights", "bit_generator": "PCG64", "seed": BOOTSTRAP_SEED, "resamples": BOOTSTRAP_RESAMPLES, "interval": "percentile_95", "quantile_method": "linear"},
                "estimators": {"local_collection": "sum(same-origin same-layer detections)/sum(generated in tile layers)", "local_net": "sum(same-origin same-layer detections)/incident neutrons", "all_origin_net": "sum(all-origin detections)/incident neutrons"},
                "output_map": {"thickness": "thickness_summary.csv", "layer_sensor": "layer_sensor_estimates.csv", "longitudinal": "longitudinal_profiles.csv", "transfers": "transfer_matrix.csv", "distribution": "distribution_diagnostics.csv", "loo": "loo_diagnostics.csv", "sizing": "production_sizing.json" if result["sizing_plan"] is not None else None},
            }
            (temp / "analysis_config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            lines = [
                "# Steel Module Ten-layer Stack Analysis", "",
                f"- Evidence: `{result['stage_label']}`; {config['task_count']} tasks / {config['event_count']} events.",
                "- Primary collection is same-origin tile to same-layer two-SiPM response, using ratio of sums.",
                "- All-origin response and cross-layer transfer are secondary diagnostics.", "",
                "## Full-stack estimates", "",
                "| tile (mm) | local collection | local net / neutron | all-origin net / neutron | cross-layer fraction | max LOO |", "|---:|---:|---:|---:|---:|---:|",
            ]
            for row in result["thickness_rows"]:
                lines.append(f"| {row['tile_thickness_mm']} | {row['local_collection']:.6g} [{row['local_collection_ci95_low']:.6g}, {row['local_collection_ci95_high']:.6g}] | {row['local_net_photons_per_neutron']:.6g} [{row['local_net_ci95_low']:.6g}, {row['local_net_ci95_high']:.6g}] | {row['all_origin_net_photons_per_neutron']:.6g} | {row['cross_layer_fraction']:.3%} | {max(row['max_local_collection_loo_shift'], row['max_local_net_loo_shift']):.2%} |")
            lines.extend(["", "Every layer/sensor estimate, longitudinal profile, tail diagnostic, and transfer cell is retained in the CSV outputs. A zero generated-light denominator is marked invalid/NaN; it is never reinterpreted as zero collection.", "", "No detector choice, extra production round, or scheduler action is automatic."])
            (temp / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
            write_checksums(temp)
            os.replace(temp, output)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f"Cannot analyze steel-module stack: {exc}", file=os.sys.stderr)
        return 1
    print(f"Analyzed steel-module stack into {output}")
    print(f"Tasks: {config['task_count']}; events: {config['event_count']}; configurations: 6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
