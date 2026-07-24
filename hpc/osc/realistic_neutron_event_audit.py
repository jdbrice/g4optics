#!/usr/bin/env python3
"""Validate the complete D-012 realistic-neutron per-event causal schema."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping, Sequence


EVENT_SCHEMA_FIELDS = (
    "event_id",
    "shoot_x_mm",
    "shoot_y_mm",
    "shoot_z_mm",
    "hit_valid",
    "hit_x_mm",
    "hit_y_mm",
    "hit_z_mm",
    "scint_centroid_valid",
    "scint_centroid_x_mm",
    "scint_centroid_y_mm",
    "scint_centroid_z_mm",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "collection_efficiency",
    "primary_kinetic_energy_mev",
    "collection_efficiency_valid",
    "cerenkov_photons",
    "steel_edep_mev",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "primary_neutron_elastic_flag",
    "primary_neutron_inelastic_flag",
    "primary_neutron_capture_flag",
    "primary_neutron_any_interaction_flag",
    "charged_tile_entry_count",
    "charged_tile_entry_ke_mev",
    "electron_tile_entry_count",
    "electron_tile_entry_ke_mev",
    "proton_tile_entry_count",
    "proton_tile_entry_ke_mev",
    "other_charged_tile_entry_count",
    "other_charged_tile_entry_ke_mev",
    "primary_neutron_tile_entry_valid",
    "primary_neutron_tile_entry_x_mm",
    "primary_neutron_tile_entry_y_mm",
    "primary_neutron_tile_entry_z_mm",
    "tile_edep_mev",
    "electron_tile_edep_mev",
    "proton_tile_edep_mev",
    "other_charged_tile_edep_mev",
    "neutral_tile_edep_mev",
)

SIPM_SENSOR_FIELDS = (
    "sipm_sensor_0_detected_photons",
    "sipm_sensor_1_detected_photons",
    "sipm_sensor_2_detected_photons",
    "sipm_sensor_3_detected_photons",
)

COUNT_FIELDS = (
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "cerenkov_photons",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "charged_tile_entry_count",
    "electron_tile_entry_count",
    "proton_tile_entry_count",
    "other_charged_tile_entry_count",
)

FLAG_FIELDS = (
    "hit_valid",
    "scint_centroid_valid",
    "collection_efficiency_valid",
    "primary_neutron_elastic_flag",
    "primary_neutron_inelastic_flag",
    "primary_neutron_capture_flag",
    "primary_neutron_any_interaction_flag",
    "primary_neutron_tile_entry_valid",
)

NONNEGATIVE_ENERGY_FIELDS = (
    "steel_edep_mev",
    "charged_tile_entry_ke_mev",
    "electron_tile_entry_ke_mev",
    "proton_tile_entry_ke_mev",
    "other_charged_tile_entry_ke_mev",
    "tile_edep_mev",
    "electron_tile_edep_mev",
    "proton_tile_edep_mev",
    "other_charged_tile_edep_mev",
    "neutral_tile_edep_mev",
)

OPTIONAL_COORDINATES = (
    ("hit_valid", ("hit_x_mm", "hit_y_mm", "hit_z_mm")),
    (
        "scint_centroid_valid",
        (
            "scint_centroid_x_mm",
            "scint_centroid_y_mm",
            "scint_centroid_z_mm",
        ),
    ),
    (
        "primary_neutron_tile_entry_valid",
        (
            "primary_neutron_tile_entry_x_mm",
            "primary_neutron_tile_entry_y_mm",
            "primary_neutron_tile_entry_z_mm",
        ),
    ),
)


def _fail(context: str, event_id: int | None, message: str) -> None:
    prefix = f"{context}: " if context else ""
    if event_id is not None:
        prefix += f"event {event_id}: "
    raise ValueError(prefix + message)


def _integer(value: Any, field: str, context: str, event_id: int | None) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        _fail(context, event_id, f"{field} is not numeric: {value!r}")
        raise AssertionError from exc
    if not math.isfinite(number) or not number.is_integer():
        _fail(context, event_id, f"{field} must be a finite integer: {value!r}")
    return int(number)


def _finite(value: Any, field: str, context: str, event_id: int | None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        _fail(context, event_id, f"{field} is not numeric: {value!r}")
        raise AssertionError from exc
    if not math.isfinite(number):
        _fail(context, event_id, f"{field} must be finite: {value!r}")
    return number


def _nonnegative(
    value: Any, field: str, context: str, event_id: int | None
) -> float:
    number = _finite(value, field, context, event_id)
    if number < 0:
        _fail(context, event_id, f"{field} must be non-negative: {number}")
    return number


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


def audit_event_arrays(
    arrays: Mapping[str, Sequence[Any]],
    *,
    expected_events: int | None = None,
    context: str = "",
) -> dict[str, int | float]:
    """Audit one task's complete scan arrays and return additive diagnostics."""

    missing = [field for field in EVENT_SCHEMA_FIELDS if field not in arrays]
    if missing:
        _fail(context, None, f"ROOT scan tree is missing fields: {', '.join(missing)}")

    present_sensor_fields = [field for field in SIPM_SENSOR_FIELDS if field in arrays]
    if present_sensor_fields and len(present_sensor_fields) != len(SIPM_SENSOR_FIELDS):
        _fail(
            context,
            None,
            "ROOT scan tree must contain either all four per-SiPM fields or none",
        )
    audited_fields = EVENT_SCHEMA_FIELDS + (
        SIPM_SENSOR_FIELDS if present_sensor_fields else ()
    )
    lengths = {len(arrays[field]) for field in audited_fields}
    if len(lengths) != 1:
        _fail(context, None, "ROOT scan columns have inconsistent lengths")
    event_count = lengths.pop()
    if event_count <= 0:
        _fail(context, None, "ROOT scan tree is empty")
    if expected_events is not None and event_count != expected_events:
        _fail(
            context,
            None,
            f"event count mismatch: {event_count} != {expected_events}",
        )

    totals = {
        "events": event_count,
        "committed_events": event_count,
        "shoot_position_events": event_count,
        "primary_energy_events": event_count,
        "generated_optical_photons": 0,
        "scintillation_photons": 0,
        "sipm_detected_photons": 0,
        "cerenkov_photons": 0,
        "steel_edep_sum_mev": 0.0,
        "steel_edep_nonzero_events": 0,
        "primary_neutron_interaction_events": 0,
        "primary_neutron_elastic_count": 0,
        "primary_neutron_inelastic_count": 0,
        "primary_neutron_capture_count": 0,
        "primary_neutron_elastic_events": 0,
        "primary_neutron_inelastic_events": 0,
        "primary_neutron_capture_events": 0,
        "charged_tile_entry_events": 0,
        "charged_tile_entry_count": 0,
        "charged_tile_entry_ke_sum_mev": 0.0,
        "electron_tile_entry_count": 0,
        "electron_tile_entry_ke_sum_mev": 0.0,
        "proton_tile_entry_count": 0,
        "proton_tile_entry_ke_sum_mev": 0.0,
        "other_charged_tile_entry_count": 0,
        "other_charged_tile_entry_ke_sum_mev": 0.0,
        "primary_neutron_tile_entry_events": 0,
        "hit_position_events": 0,
        "scint_centroid_events": 0,
        "tile_edep_sum_mev": 0.0,
        "electron_tile_edep_sum_mev": 0.0,
        "proton_tile_edep_sum_mev": 0.0,
        "other_charged_tile_edep_sum_mev": 0.0,
        "neutral_tile_edep_sum_mev": 0.0,
        "tile_edep_nonzero_events": 0,
        "generated_optical_zero_events": 0,
        "scintillation_zero_events": 0,
        "sipm_detected_zero_events": 0,
    }
    if present_sensor_fields:
        for field in SIPM_SENSOR_FIELDS:
            totals[field] = 0
    observed_ids: list[int] = []

    for index in range(event_count):
        event_id = _integer(arrays["event_id"][index], "event_id", context, None)
        observed_ids.append(event_id)

        values: dict[str, int | float] = {"event_id": event_id}
        for field in COUNT_FIELDS:
            value = _integer(arrays[field][index], field, context, event_id)
            if value < 0:
                _fail(context, event_id, f"{field} must be non-negative: {value}")
            values[field] = value
        for field in present_sensor_fields:
            value = _integer(arrays[field][index], field, context, event_id)
            if value < 0:
                _fail(context, event_id, f"{field} must be non-negative: {value}")
            values[field] = value
        for field in FLAG_FIELDS:
            value = _integer(arrays[field][index], field, context, event_id)
            if value not in (0, 1):
                _fail(context, event_id, f"{field} must be 0 or 1: {value}")
            values[field] = value
        for field in NONNEGATIVE_ENERGY_FIELDS:
            values[field] = _nonnegative(arrays[field][index], field, context, event_id)
        for field in (
            "shoot_x_mm",
            "shoot_y_mm",
            "shoot_z_mm",
            "primary_kinetic_energy_mev",
        ):
            values[field] = _finite(arrays[field][index], field, context, event_id)
        if float(values["primary_kinetic_energy_mev"]) <= 0:
            _fail(context, event_id, "primary_kinetic_energy_mev must be positive")

        for flag_field, coordinate_fields in OPTIONAL_COORDINATES:
            valid = int(values[flag_field]) == 1
            for field in coordinate_fields:
                try:
                    coordinate = float(arrays[field][index])
                except (TypeError, ValueError):
                    _fail(context, event_id, f"{field} is not numeric")
                    raise AssertionError
                if valid and not math.isfinite(coordinate):
                    _fail(context, event_id, f"valid {field} must be finite")
                if not valid and not math.isnan(coordinate):
                    _fail(context, event_id, f"invalid {field} must be NaN")
                values[field] = coordinate

        generated = int(values["generated_optical_photons"])
        scintillation = int(values["scintillation_photons"])
        cerenkov = int(values["cerenkov_photons"])
        sipm = int(values["sipm_detected_photons"])
        if generated != scintillation + cerenkov:
            _fail(
                context,
                event_id,
                "generated optical count must equal scintillation + Cerenkov "
                "for the WLS-disabled realistic-neutron preset",
            )
        if sipm > generated:
            _fail(context, event_id, "SiPM photon count exceeds generated optical light")
        if present_sensor_fields:
            per_sensor_sum = sum(int(values[field]) for field in SIPM_SENSOR_FIELDS)
            if sipm != per_sensor_sum:
                _fail(
                    context,
                    event_id,
                    "aggregate SiPM photon count must equal the four sensor counts",
                )

        collection_valid = int(values["collection_efficiency_valid"])
        try:
            collection = float(arrays["collection_efficiency"][index])
        except (TypeError, ValueError):
            _fail(context, event_id, "collection_efficiency is not numeric")
            raise AssertionError
        if collection_valid != int(generated > 0):
            _fail(
                context,
                event_id,
                "collection_efficiency_valid must equal generated_optical_photons > 0",
            )
        if generated > 0:
            expected_collection = sipm / generated
            if not math.isfinite(collection) or not _close(collection, expected_collection):
                _fail(
                    context,
                    event_id,
                    "collection_efficiency disagrees with SiPM/generated ratio",
                )
        elif not math.isnan(collection):
            _fail(context, event_id, "undefined collection_efficiency must be NaN")

        elastic = int(values["primary_neutron_elastic_count"])
        inelastic = int(values["primary_neutron_inelastic_count"])
        capture = int(values["primary_neutron_capture_count"])
        interaction_counts = {
            "primary_neutron_elastic_flag": elastic,
            "primary_neutron_inelastic_flag": inelastic,
            "primary_neutron_capture_flag": capture,
        }
        for flag_field, count in interaction_counts.items():
            if int(values[flag_field]) != int(count > 0):
                _fail(context, event_id, f"{flag_field} must equal count > 0")
        any_interaction = elastic > 0 or inelastic > 0 or capture > 0
        if int(values["primary_neutron_any_interaction_flag"]) != int(any_interaction):
            _fail(
                context,
                event_id,
                "primary_neutron_any_interaction_flag must equal any count > 0",
            )

        charged_count = int(values["charged_tile_entry_count"])
        charged_categories = sum(
            int(values[field])
            for field in (
                "electron_tile_entry_count",
                "proton_tile_entry_count",
                "other_charged_tile_entry_count",
            )
        )
        if charged_count != charged_categories:
            _fail(context, event_id, "charged tile-entry category counts do not sum")
        charged_ke = float(values["charged_tile_entry_ke_mev"])
        category_ke = sum(
            float(values[field])
            for field in (
                "electron_tile_entry_ke_mev",
                "proton_tile_entry_ke_mev",
                "other_charged_tile_entry_ke_mev",
            )
        )
        if not _close(charged_ke, category_ke):
            _fail(context, event_id, "charged tile-entry kinetic energies do not sum")

        tile_edep = float(values["tile_edep_mev"])
        category_edep = sum(
            float(values[field])
            for field in (
                "electron_tile_edep_mev",
                "proton_tile_edep_mev",
                "other_charged_tile_edep_mev",
                "neutral_tile_edep_mev",
            )
        )
        if not _close(tile_edep, category_edep):
            _fail(context, event_id, "tile energy-deposition categories do not sum")

        hit_valid = int(values["hit_valid"])
        tile_entry_valid = int(values["primary_neutron_tile_entry_valid"])
        if hit_valid != tile_entry_valid:
            _fail(
                context,
                event_id,
                "primary-only hit_valid disagrees with neutron tile-entry validity",
            )
        if hit_valid:
            for hit_field, entry_field in zip(
                ("hit_x_mm", "hit_y_mm", "hit_z_mm"),
                (
                    "primary_neutron_tile_entry_x_mm",
                    "primary_neutron_tile_entry_y_mm",
                    "primary_neutron_tile_entry_z_mm",
                ),
            ):
                if not _close(float(values[hit_field]), float(values[entry_field])):
                    _fail(context, event_id, "primary hit and neutron-entry positions disagree")
        if int(values["scint_centroid_valid"]) != int(scintillation > 0):
            _fail(
                context,
                event_id,
                "scintillation centroid validity must equal scintillation count > 0",
            )

        totals["generated_optical_photons"] += generated
        totals["scintillation_photons"] += scintillation
        totals["sipm_detected_photons"] += sipm
        for field in present_sensor_fields:
            totals[field] += int(values[field])
        totals["cerenkov_photons"] += cerenkov
        steel_edep = float(values["steel_edep_mev"])
        totals["steel_edep_sum_mev"] += steel_edep
        totals["steel_edep_nonzero_events"] += int(steel_edep > 0)
        totals["primary_neutron_interaction_events"] += int(any_interaction)
        totals["primary_neutron_elastic_count"] += elastic
        totals["primary_neutron_inelastic_count"] += inelastic
        totals["primary_neutron_capture_count"] += capture
        totals["primary_neutron_elastic_events"] += int(elastic > 0)
        totals["primary_neutron_inelastic_events"] += int(inelastic > 0)
        totals["primary_neutron_capture_events"] += int(capture > 0)
        totals["charged_tile_entry_events"] += int(charged_count > 0)
        totals["charged_tile_entry_count"] += charged_count
        totals["charged_tile_entry_ke_sum_mev"] += charged_ke
        for count_field, energy_field, summary_count, summary_energy in (
            (
                "electron_tile_entry_count",
                "electron_tile_entry_ke_mev",
                "electron_tile_entry_count",
                "electron_tile_entry_ke_sum_mev",
            ),
            (
                "proton_tile_entry_count",
                "proton_tile_entry_ke_mev",
                "proton_tile_entry_count",
                "proton_tile_entry_ke_sum_mev",
            ),
            (
                "other_charged_tile_entry_count",
                "other_charged_tile_entry_ke_mev",
                "other_charged_tile_entry_count",
                "other_charged_tile_entry_ke_sum_mev",
            ),
        ):
            totals[summary_count] += int(values[count_field])
            totals[summary_energy] += float(values[energy_field])
        totals["primary_neutron_tile_entry_events"] += tile_entry_valid
        totals["hit_position_events"] += hit_valid
        totals["scint_centroid_events"] += int(values["scint_centroid_valid"])
        totals["tile_edep_sum_mev"] += tile_edep
        for event_field, summary_field in (
            ("electron_tile_edep_mev", "electron_tile_edep_sum_mev"),
            ("proton_tile_edep_mev", "proton_tile_edep_sum_mev"),
            ("other_charged_tile_edep_mev", "other_charged_tile_edep_sum_mev"),
            ("neutral_tile_edep_mev", "neutral_tile_edep_sum_mev"),
        ):
            totals[summary_field] += float(values[event_field])
        totals["tile_edep_nonzero_events"] += int(tile_edep > 0)
        totals["generated_optical_zero_events"] += int(generated == 0)
        totals["scintillation_zero_events"] += int(scintillation == 0)
        totals["sipm_detected_zero_events"] += int(sipm == 0)

    if sorted(observed_ids) != list(range(event_count)):
        _fail(context, None, "event_id values must be unique and contiguous from zero")
    return totals


def read_and_audit_root(
    path: Path,
    *,
    expected_events: int | None = None,
    context: str = "",
) -> tuple[Mapping[str, Sequence[Any]], dict[str, int | float]]:
    """Read a ROOT scan tree with uproot and apply the complete causal audit."""

    try:
        import uproot  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("ROOT event auditing requires uproot and numpy") from exc
    try:
        with uproot.open(path) as root_file:
            tree = root_file["scan"]
            available = set(tree.keys())
            sensor_fields = [
                field for field in SIPM_SENSOR_FIELDS if field in available
            ]
            arrays = tree.arrays(
                [*EVENT_SCHEMA_FIELDS, *sensor_fields], library="np", how=dict
            )
    except Exception as exc:
        raise ValueError(f"cannot read complete scan tree from {path}: {exc}") from exc
    report = audit_event_arrays(
        arrays,
        expected_events=expected_events,
        context=context or str(path),
    )
    return arrays, report
