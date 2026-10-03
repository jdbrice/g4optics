#!/usr/bin/env python3
"""Audit the layout study ROOT records; this is an engineering gate."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import uproot

SENSORS = {"back-four": 4, "back-two": 2, "back-center": 1, "edge-two": 2}
TILE_THICKNESSES_MM = (4, 8, 12, 16, 20, 24)
REQUIRED_COLUMNS = {
    "scan": {"event_id", "primary_kinetic_energy_mev", "shoot_x_mm", "shoot_y_mm",
             "shoot_z_mm", "generated_optical_photons", "scintillation_photons",
             "cerenkov_photons", "sipm_detected_photons", "steel_edep_mev",
             "tile_edep_mev", "collection_efficiency", "collection_efficiency_valid"},
    "layout_layers": {"event_id", "layer", "generated_optical_photons",
                      "scintillation_photons", "cerenkov_photons",
                      "all_origin_detected_photons", "local_origin_detected_photons",
                      "steel_edep_mev", "tile_edep_mev"},
    "layout_sensors": {"event_id", "layer", "local_sensor", "global_copy",
                       "all_origin_detected_photons", "local_origin_detected_photons"},
    "layout_transfers": {"event_id", "origin_layer", "destination_layer", "local_sensor",
                         "global_copy", "detected_photons"},
}
INTEGER_COLUMNS = {"event_id", "layer", "origin_layer", "destination_layer",
                   "local_sensor", "global_copy", "collection_efficiency_valid"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def indexed(table, names, expected=None):
    rows = {}
    for i in range(len(table[names[0]])):
        key = tuple(int(table[n][i]) for n in names)
        require(key not in rows, f"duplicate {names}: {key}")
        rows[key] = {n: values[i].item() for n, values in table.items()}
    if expected is not None:
        require(set(rows) == set(expected), f"missing or unexpected {names}")
    return rows


def counts_valid(table):
    for name, values in table.items():
        if name in INTEGER_COLUMNS:
            require(np.issubdtype(values.dtype, np.integer), f"noninteger {name}")
        if name.endswith("photons"):
            require(np.issubdtype(values.dtype, np.integer), f"noninteger {name}")
            require(np.all(values >= 0), f"negative {name}")
        if name.endswith("edep_mev"):
            require(np.all(np.isfinite(values)) and np.all(values >= 0),
                    f"invalid energy {name}")


def audit_tables(tables, layout, events, tile_thickness_mm=4):
    require(events > 0, "a zero-event initialization has no event audit")
    require(layout in SENSORS, "unknown layout")
    require(tile_thickness_mm in TILE_THICKNESSES_MM, "unsupported tile thickness")
    source_z_mm = (10 * (40 + tile_thickness_mm) + 4.5) / 2 + 1.5
    n = SENSORS[layout]
    for name, required in REQUIRED_COLUMNS.items():
        require(name in tables, f"missing tree {name}")
        table = tables[name]
        require(required <= set(table), f"missing columns in {name}: {sorted(required-set(table))}")
        require(len({len(values) for values in table.values()}) == 1,
                f"inconsistent column lengths in {name}")
        counts_valid(table)
    scan = indexed(tables["scan"], ("event_id",), [(e,) for e in range(events)])
    layers = indexed(tables["layout_layers"], ("event_id", "layer"),
                     [(e, l) for e in range(events) for l in range(10)])
    sensors = indexed(tables["layout_sensors"], ("event_id", "layer", "local_sensor"),
                      [(e, l, s) for e in range(events) for l in range(10) for s in range(n)])
    transfers = indexed(tables["layout_transfers"],
                        ("event_id", "origin_layer", "global_copy"))
    all_counts, local_counts = {}, {}
    cross_layer = 0
    for (e, origin, copy), row in transfers.items():
        require(0 <= e < events and 0 <= origin < 10, "unknown origin or event")
        l, s = divmod(copy, 4)
        require(0 <= l < 10 and s < n, "inactive sensor in transfers")
        require(row["destination_layer"] == l and row["local_sensor"] == s,
                "transfer identity mismatch")
        d = row["detected_photons"]
        require(d > 0, "sparse transfer must be positive")
        key = (e, l, s)
        all_counts[key] = all_counts.get(key, 0) + d
        if origin == l:
            local_counts[key] = local_counts.get(key, 0) + d
        else:
            cross_layer += d
    total_g = total_d = total_scint = total_cerenkov = 0
    total_steel_edep = total_tile_edep = 0.0
    for e in range(events):
        event = scan[(e,)]
        require(np.isclose(event["primary_kinetic_energy_mev"], 1000, rtol=0, atol=1e-9),
                "wrong primary energy")
        for name, value in (("shoot_x_mm", 0), ("shoot_y_mm", 0), ("shoot_z_mm", source_z_mm)):
            require(np.isclose(event[name], value, rtol=0, atol=1e-9), f"wrong source {name}")
        layer_g = layer_d = layer_scint = layer_cerenkov = 0
        steel_edep = tile_edep = 0.0
        for l in range(10):
            row = layers[(e, l)]
            require(row["generated_optical_photons"] ==
                    row["scintillation_photons"] + row["cerenkov_photons"],
                    "layer generation mismatch")
            all_sum = local_sum = 0
            for s in range(n):
                key = (e, l, s)
                sensor = sensors[key]
                require(sensor["global_copy"] == 4*l+s, "sensor identity mismatch")
                require(sensor["all_origin_detected_photons"] == all_counts.get(key, 0),
                        "sensor/transfer all-origin mismatch")
                require(sensor["local_origin_detected_photons"] == local_counts.get(key, 0),
                        "sensor/transfer local-origin mismatch")
                all_sum += sensor["all_origin_detected_photons"]
                local_sum += sensor["local_origin_detected_photons"]
            require(row["all_origin_detected_photons"] == all_sum and
                    row["local_origin_detected_photons"] == local_sum,
                    "layer/sensor mismatch")
            layer_g += row["generated_optical_photons"]
            layer_scint += row["scintillation_photons"]
            layer_cerenkov += row["cerenkov_photons"]
            layer_d += all_sum
            steel_edep += row["steel_edep_mev"]
            tile_edep += row["tile_edep_mev"]
        require(layer_g == event["generated_optical_photons"], "layer/scan G mismatch")
        require(layer_scint == event["scintillation_photons"], "layer/scan scintillation mismatch")
        require(layer_cerenkov == event["cerenkov_photons"], "layer/scan Cerenkov mismatch")
        require(layer_d == event["sipm_detected_photons"], "sensor/scan D mismatch")
        require(np.isclose(steel_edep, event["steel_edep_mev"], rtol=1e-10, atol=1e-9),
                "steel energy mismatch")
        require(np.isclose(tile_edep, event["tile_edep_mev"], rtol=1e-10, atol=1e-9),
                "tile energy mismatch")
        valid = layer_g > 0
        require(event["collection_efficiency_valid"] == int(valid), "ratio validity mismatch")
        if valid:
            require(np.isclose(event["collection_efficiency"], layer_d/layer_g,
                               rtol=1e-10, atol=1e-12), "ratio mismatch")
        else:
            require(np.isnan(event["collection_efficiency"]), "zero denominator must be NaN")
        total_g += layer_g
        total_d += layer_d
        total_scint += layer_scint
        total_cerenkov += layer_cerenkov
        total_steel_edep += steel_edep
        total_tile_edep += tile_edep
    require(total_g > 0 and total_d > 0, "sample did not exercise optical response")
    return {"events": events, "layout": layout, "sensors_per_layer": n,
            "tile_thickness_mm": tile_thickness_mm,
            "generated_photons": total_g, "detected_photons": total_d,
            "scintillation_photons": total_scint, "cerenkov_photons": total_cerenkov,
            "steel_edep_sum_mev": total_steel_edep, "tile_edep_sum_mev": total_tile_edep,
            "cross_layer_detected_photons": cross_layer,
            "unknown_origin_detected_photons": 0,
            "scope": "engineering sample; legacy photon denominator; no precision claim"}


def audit_log(path):
    log = Path(path).read_text(errors="replace")
    # The unchanged baseline requests ntuple merging. Serial Geant4 explicitly
    # ignores it; allow only this warning, not arbitrary Analysis_W001 failures.
    def known_serial_warning(match):
        block = match.group()
        lines = [" ".join(line.split()) for line in block.splitlines() if line.strip()]
        known = lines[1:-1] == [
            "*** G4Exception : Analysis_W001",
            "issued by : G4RootNtupleFileManager::SetNtupleMergingMode",
            "Merging ntuples is not applicable in sequential application.",
            "Setting was ignored.",
            "*** This is just a warning message. ***",
        ]
        return "" if known else block

    checked = re.sub(r"[^\n]*G4Exception-START.*?[^\n]*G4Exception-END[^\n]*",
                     known_serial_warning, log, flags=re.S)
    patterns = (r"G4Exception", r"Overlap is detected", r"COMMAND NOT FOUND",
                r"Illegal parameter", r"Segmentation fault", r"AddressSanitizer",
                r"Aborting", r"Illegal application state", r"command refused")
    for pattern in patterns:
        require(not re.search(pattern, checked, re.I), f"simulation log failure: {pattern}")
    for match in re.finditer(r"No\s*RINDEX\s*:\s*(\S+)", log, re.I):
        require(float(match[1]) == 0, "simulation log failure: NoRINDEX")
    without_zero_counts = re.sub(r"No\s*RINDEX\s*:\s*\S+", "", log, flags=re.I)
    require(not re.search(r"No\s*RINDEX", without_zero_counts, re.I),
            "simulation log failure: unparsed NoRINDEX")
    return log


def audit_file(root_path, layout, events, log_path, exit_code, tile_thickness_mm=4):
    require(exit_code == 0, f"process exited {exit_code}")
    log = audit_log(log_path)
    require(re.search(r"Primary particle was:\s*neutron\b", log), "log primary must be neutron")
    logged_events = re.findall(r"^Number of events:\s*(\d+)\s*$", log, re.M)
    require(logged_events == [str(events)], "log event count mismatch or missing")
    with uproot.open(root_path) as root:
        for name in ("stack_layers", "stack_transfers"):
            require(root[name].num_entries == 0, f"legacy tree {name} must remain empty")
        tables = {name: root[name].arrays(library="np") for name in
                  ("scan", "layout_layers", "layout_sensors", "layout_transfers")}
    report = audit_tables(tables, layout, events, tile_thickness_mm=tile_thickness_mm)
    summary_path = Path(root_path).with_name(Path(root_path).stem + "_summary.csv")
    with summary_path.open() as handle:
        summaries = list(csv.DictReader(handle))
    require(len(summaries) == 1, "summary must contain one row")
    summary = summaries[0]
    for field, expected in (("events", events), ("committed_events", events),
                            ("generated_optical_photons", report["generated_photons"]),
                            ("sipm_detected_photons", report["detected_photons"]),
                            ("scintillation_photons", report["scintillation_photons"]),
                            ("cerenkov_photons", report["cerenkov_photons"]),
                            ("collection_efficiency_valid", 1)):
        require(int(summary[field]) == expected, f"summary {field} mismatch")
    for field, expected in (
            ("steel_edep_sum_mev", report["steel_edep_sum_mev"]),
            ("tile_edep_sum_mev", report["tile_edep_sum_mev"]),
            ("collection_efficiency", report["detected_photons"] / report["generated_photons"]),
            ("net_sipm_photons_per_event", report["detected_photons"] / events),
            ("production_scint_photons_per_event", report["scintillation_photons"] / events)):
        require(np.isclose(float(summary[field]), expected, rtol=1e-10, atol=1e-9),
                f"summary {field} mismatch")
    report.update(status="passed", root_sha256=hashlib.sha256(Path(root_path).read_bytes()).hexdigest(),
                  log_sha256=hashlib.sha256(Path(log_path).read_bytes()).hexdigest(),
                  summary_sha256=hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                  exit_code=exit_code)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--layout", choices=SENSORS, required=True)
    parser.add_argument("--events", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--tile-thickness-mm", type=int, choices=TILE_THICKNESSES_MM, default=4)
    args = parser.parse_args()
    print(json.dumps(audit_file(args.root, args.layout, args.events, args.log, args.exit_code,
                               tile_thickness_mm=args.tile_thickness_mm), indent=2))


if __name__ == "__main__":
    main()
