#!/usr/bin/env python3
"""Audit scan, stack_layers, and stack_transfers as one causal event record."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

from realistic_neutron_event_audit import audit_event_arrays
from steel_module_stack_campaign_lib import SENSOR_COUNT, SENSORS_PER_LAYER, STACK_LAYERS


LAYER_FIELDS = (
    "event_id", "layer", "generated_optical_photons", "scintillation_photons",
    "cerenkov_photons", "sensor_0_all_origin_detected_photons",
    "sensor_1_all_origin_detected_photons", "sensor_0_local_origin_detected_photons",
    "sensor_1_local_origin_detected_photons", "steel_edep_mev",
    "primary_neutron_elastic_count", "primary_neutron_inelastic_count",
    "primary_neutron_capture_count", "charged_tile_entry_count",
    "charged_tile_entry_ke_mev", "electron_tile_entry_count",
    "electron_tile_entry_ke_mev", "proton_tile_entry_count",
    "proton_tile_entry_ke_mev", "other_charged_tile_entry_count",
    "other_charged_tile_entry_ke_mev", "primary_neutron_tile_entry_valid",
    "primary_neutron_tile_entry_x_mm", "primary_neutron_tile_entry_y_mm",
    "primary_neutron_tile_entry_z_mm", "tile_edep_mev", "electron_tile_edep_mev",
    "proton_tile_edep_mev", "other_charged_tile_edep_mev", "neutral_tile_edep_mev",
)
TRANSFER_FIELDS = (
    "event_id", "origin_layer", "destination_layer", "local_sensor",
    "global_copy", "detected_photons",
)
LAYER_COUNT_FIELDS = (
    "generated_optical_photons", "scintillation_photons", "cerenkov_photons",
    "sensor_0_all_origin_detected_photons", "sensor_1_all_origin_detected_photons",
    "sensor_0_local_origin_detected_photons", "sensor_1_local_origin_detected_photons",
    "primary_neutron_elastic_count", "primary_neutron_inelastic_count",
    "primary_neutron_capture_count", "charged_tile_entry_count",
    "electron_tile_entry_count", "proton_tile_entry_count",
    "other_charged_tile_entry_count", "primary_neutron_tile_entry_valid",
)
LAYER_ENERGY_FIELDS = (
    "steel_edep_mev", "charged_tile_entry_ke_mev", "electron_tile_entry_ke_mev",
    "proton_tile_entry_ke_mev", "other_charged_tile_entry_ke_mev", "tile_edep_mev",
    "electron_tile_edep_mev", "proton_tile_edep_mev",
    "other_charged_tile_edep_mev", "neutral_tile_edep_mev",
)
AGGREGATE_FIELDS = (
    "generated_optical_photons", "scintillation_photons", "cerenkov_photons",
    "steel_edep_mev", "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count", "primary_neutron_capture_count",
    "charged_tile_entry_count", "charged_tile_entry_ke_mev",
    "electron_tile_entry_count", "electron_tile_entry_ke_mev",
    "proton_tile_entry_count", "proton_tile_entry_ke_mev",
    "other_charged_tile_entry_count", "other_charged_tile_entry_ke_mev",
    "tile_edep_mev", "electron_tile_edep_mev", "proton_tile_edep_mev",
    "other_charged_tile_edep_mev", "neutral_tile_edep_mev",
)


def _integer(value: Any, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{label} must be a finite integer")
    return int(number)


def _nonnegative(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return number


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


def _require_fields(arrays: Mapping[str, Sequence[Any]], fields: Sequence[str], tree: str) -> int:
    missing = [field for field in fields if field not in arrays]
    if missing:
        raise ValueError(f"{tree} is missing fields: {', '.join(missing)}")
    lengths = {len(arrays[field]) for field in fields}
    if len(lengths) != 1:
        raise ValueError(f"{tree} columns have inconsistent lengths")
    return lengths.pop()


def audit_stack_arrays(
    scan: Mapping[str, Sequence[Any]],
    layers: Mapping[str, Sequence[Any]],
    transfers: Mapping[str, Sequence[Any]],
    *,
    expected_events: int,
    require_zero_unknown_origin: bool = True,
    context: str = "",
) -> dict[str, Any]:
    # In stack mode the four legacy sensor fields are only a compatibility
    # projection of global copies 0..3, not the aggregate of all 20 sensors.
    scan_without_compatibility_sensors = {
        key: value
        for key, value in scan.items()
        if not key.startswith("sipm_sensor_")
    }
    scan_report = audit_event_arrays(
        scan_without_compatibility_sensors,
        expected_events=expected_events,
        context=context,
    )
    layer_count = _require_fields(layers, LAYER_FIELDS, "stack_layers")
    transfer_count = _require_fields(transfers, TRANSFER_FIELDS, "stack_transfers")
    if layer_count != expected_events * STACK_LAYERS:
        raise ValueError(f"{context}: stack_layers row count mismatch")

    layer_rows: dict[tuple[int, int], dict[str, int | float]] = {}
    per_layer_totals: list[dict[str, float]] = [defaultdict(float) for _ in range(STACK_LAYERS)]
    for index in range(layer_count):
        event = _integer(layers["event_id"][index], "stack_layers.event_id")
        layer = _integer(layers["layer"][index], "stack_layers.layer")
        if not 0 <= event < expected_events or not 0 <= layer < STACK_LAYERS:
            raise ValueError(f"{context}: invalid stack layer identity ({event}, {layer})")
        key = (event, layer)
        if key in layer_rows:
            raise ValueError(f"{context}: duplicate stack layer row {key}")
        row: dict[str, int | float] = {}
        for field in LAYER_COUNT_FIELDS:
            value = _integer(layers[field][index], f"{key}.{field}")
            if value < 0 or (field == "primary_neutron_tile_entry_valid" and value not in (0, 1)):
                raise ValueError(f"{context}: invalid {field} in {key}")
            row[field] = value
            per_layer_totals[layer][field] += value
        for field in LAYER_ENERGY_FIELDS:
            value = _nonnegative(layers[field][index], f"{key}.{field}")
            row[field] = value
            per_layer_totals[layer][field] += value
        valid = int(row["primary_neutron_tile_entry_valid"])
        for coordinate in (
            "primary_neutron_tile_entry_x_mm", "primary_neutron_tile_entry_y_mm",
            "primary_neutron_tile_entry_z_mm",
        ):
            value = float(layers[coordinate][index])
            if valid and not math.isfinite(value):
                raise ValueError(f"{context}: valid tile entry has non-finite coordinate")
            if not valid and not math.isnan(value):
                raise ValueError(f"{context}: invalid tile entry must use NaN coordinates")
            row[coordinate] = value
        if row["generated_optical_photons"] != row["scintillation_photons"] + row["cerenkov_photons"]:
            raise ValueError(f"{context}: layer generated != scintillation + Cerenkov in {key}")
        for sensor in range(SENSORS_PER_LAYER):
            if row[f"sensor_{sensor}_local_origin_detected_photons"] > row[f"sensor_{sensor}_all_origin_detected_photons"]:
                raise ValueError(f"{context}: local-origin count exceeds all-origin in {key}")
        layer_rows[key] = row
    if set(layer_rows) != {(event, layer) for event in range(expected_events) for layer in range(STACK_LAYERS)}:
        raise ValueError(f"{context}: stack layer event/layer coverage mismatch")

    transfer_totals: dict[tuple[int, int], int] = defaultdict(int)
    seen_transfer_keys: set[tuple[int, int, int]] = set()
    unknown_origin = 0
    for index in range(transfer_count):
        event = _integer(transfers["event_id"][index], "transfer.event_id")
        origin = _integer(transfers["origin_layer"][index], "transfer.origin_layer")
        destination = _integer(transfers["destination_layer"][index], "transfer.destination_layer")
        local = _integer(transfers["local_sensor"][index], "transfer.local_sensor")
        copy = _integer(transfers["global_copy"][index], "transfer.global_copy")
        count = _integer(transfers["detected_photons"][index], "transfer.detected_photons")
        if not 0 <= event < expected_events or not -1 <= origin < STACK_LAYERS:
            raise ValueError(f"{context}: invalid transfer event/origin")
        if not 0 <= destination < STACK_LAYERS or not 0 <= local < SENSORS_PER_LAYER:
            raise ValueError(f"{context}: invalid transfer destination")
        if copy != SENSORS_PER_LAYER * destination + local or not 0 <= copy < SENSOR_COUNT:
            raise ValueError(f"{context}: transfer copy mapping mismatch")
        if count <= 0:
            raise ValueError(f"{context}: sparse transfer count must be positive")
        unique = (event, origin, copy)
        if unique in seen_transfer_keys:
            raise ValueError(f"{context}: duplicate sparse transfer row")
        seen_transfer_keys.add(unique)
        transfer_totals[(event, copy)] += count
        if origin == destination:
            transfer_totals[(event, SENSOR_COUNT + copy)] += count
        if origin == -1:
            unknown_origin += count

    total_detected = 0
    for event in range(expected_events):
        scan_generated = _integer(scan["generated_optical_photons"][event], "scan.generated")
        scan_detected = _integer(scan["sipm_detected_photons"][event], "scan.detected")
        total_detected += scan_detected
        for field in AGGREGATE_FIELDS:
            layer_sum = sum(float(layer_rows[(event, layer)][field]) for layer in range(STACK_LAYERS))
            scan_value = float(scan[field][event])
            if not _close(layer_sum, scan_value):
                raise ValueError(f"{context}: {field} layer/global mismatch in event {event}")
        generated_sum = sum(int(layer_rows[(event, layer)]["generated_optical_photons"]) for layer in range(STACK_LAYERS))
        if generated_sum != scan_generated:
            raise ValueError(f"{context}: generated layer/global mismatch")
        event_transfer_total = 0
        for layer in range(STACK_LAYERS):
            for local in range(SENSORS_PER_LAYER):
                copy = SENSORS_PER_LAYER * layer + local
                all_origin = transfer_totals[(event, copy)]
                local_origin = transfer_totals[(event, SENSOR_COUNT + copy)]
                row = layer_rows[(event, layer)]
                if all_origin != row[f"sensor_{local}_all_origin_detected_photons"]:
                    raise ValueError(f"{context}: all-origin transfer/layer mismatch")
                if local_origin != row[f"sensor_{local}_local_origin_detected_photons"]:
                    raise ValueError(f"{context}: local-origin transfer/layer mismatch")
                event_transfer_total += all_origin
                if copy < 4 and _integer(scan[f"sipm_sensor_{copy}_detected_photons"][event], "scan sensor") != all_origin:
                    raise ValueError(f"{context}: compatibility sensor projection mismatch")
        if event_transfer_total != scan_detected:
            raise ValueError(f"{context}: transfer/global SiPM mismatch in event {event}")
    if require_zero_unknown_origin and unknown_origin:
        raise ValueError(f"{context}: detected photons with unknown origin: {unknown_origin}")

    generated_total = sum(int(row["generated_optical_photons"]) for row in layer_rows.values())
    local_total = sum(
        int(row[f"sensor_{sensor}_local_origin_detected_photons"])
        for row in layer_rows.values() for sensor in range(SENSORS_PER_LAYER)
    )
    all_total = sum(
        int(row[f"sensor_{sensor}_all_origin_detected_photons"])
        for row in layer_rows.values() for sensor in range(SENSORS_PER_LAYER)
    )
    cross_total = all_total - local_total - unknown_origin
    return {
        "events": expected_events,
        "layer_rows": layer_count,
        "transfer_rows": transfer_count,
        "generated_optical_photons": generated_total,
        "local_origin_detected_photons": local_total,
        "all_origin_detected_photons": all_total,
        "cross_layer_detected_photons": cross_total,
        "unknown_origin_detected_photons": unknown_origin,
        "generated_zero_events": sum(
            sum(int(layer_rows[(event, layer)]["generated_optical_photons"]) for layer in range(STACK_LAYERS)) == 0
            for event in range(expected_events)
        ),
        "local_collection_valid": generated_total > 0,
        "local_collection": local_total / generated_total if generated_total else None,
        "all_origin_collection": all_total / generated_total if generated_total else None,
        "cross_in_fraction": cross_total / all_total if all_total else None,
        "scan_audit": scan_report,
        "per_layer_totals": [dict(row) for row in per_layer_totals],
    }


def audit_root_file(path: str, expected_events: int, *, context: str = "") -> dict[str, Any]:
    try:
        import uproot
    except ImportError as exc:  # pragma: no cover - production dependency
        raise ValueError("stack event audit requires uproot") from exc
    with uproot.open(path) as root:
        for tree in ("scan", "stack_layers", "stack_transfers"):
            if tree not in root:
                raise ValueError(f"{context}: ROOT file lacks {tree}")
        return audit_stack_arrays(
            root["scan"].arrays(library="np"),
            root["stack_layers"].arrays(library="np"),
            root["stack_transfers"].arrays(library="np"),
            expected_events=expected_events,
            context=context,
        )


__all__ = ["LAYER_FIELDS", "TRANSFER_FIELDS", "audit_root_file", "audit_stack_arrays"]
