"""Failure-injection tests using independently constructed ROOT TTrees."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
import uproot


SPEC = importlib.util.spec_from_file_location(
    "layout_audit", Path(__file__).resolve().parents[1] / "audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

SERIAL_WARNING = """-------- WWWW ------- G4Exception-START -------- WWWW -------
*** G4Exception : Analysis_W001
      issued by : G4RootNtupleFileManager::SetNtupleMergingMode
Merging ntuples is not applicable in sequential application.
Setting was ignored.
*** This is just a warning message. ***
-------- WWWW ------- G4Exception-END -------- WWWW -------
"""
GOOD_LOG = SERIAL_WARNING + "Primary particle was: neutron with energy 1 GeV.\nNumber of events: 2\n"


def columns(rows):
    return {key: np.array([row[key] for row in rows], dtype=(
        np.float64 if isinstance(value, float) else np.int32))
        for key, value in rows[0].items()}


def fixture(layout="back-two"):
    """Event 0: 12 generated, 3 local + 2 cross-layer collected; event 1: zero light."""
    count = audit.SENSORS[layout]
    scan = []
    layers = []
    sensors = []
    for event in range(2):
        scan.append({
            "event_id": event, "primary_kinetic_energy_mev": 1000.0,
            "shoot_x_mm": 0.0, "shoot_y_mm": 0.0, "shoot_z_mm": 223.75,
            "generated_optical_photons": 12 if event == 0 else 0,
            "scintillation_photons": 10 if event == 0 else 0,
            "cerenkov_photons": 2 if event == 0 else 0,
            "sipm_detected_photons": 5 if event == 0 else 0,
            "steel_edep_mev": 0.2 if event == 0 else 0.01,
            "tile_edep_mev": 0.1 if event == 0 else 0.0,
            "collection_efficiency_valid": 1 if event == 0 else 0,
            "collection_efficiency": 5.0 / 12 if event == 0 else float("nan"),
        })
        for layer in range(10):
            born = event == 0 and layer == 0
            detected = 3 if born else 2 if event == 0 and layer == 1 else 0
            layers.append({
                "event_id": event, "layer": layer,
                "generated_optical_photons": 12 if born else 0,
                "scintillation_photons": 10 if born else 0,
                "cerenkov_photons": 2 if born else 0,
                "all_origin_detected_photons": detected,
                "local_origin_detected_photons": 3 if born else 0,
                "steel_edep_mev": 0.2 if born else 0.01 if event == 1 and layer == 3 else 0.0,
                "tile_edep_mev": 0.1 if born else 0.0,
            })
            for sensor in range(count):
                sensors.append({
                    "event_id": event, "layer": layer, "local_sensor": sensor,
                    "global_copy": 4 * layer + sensor,
                    "all_origin_detected_photons": detected if sensor == 0 else 0,
                    "local_origin_detected_photons": 3 if born and sensor == 0 else 0,
                })
    transfers = [
        {"event_id": 0, "origin_layer": 0, "destination_layer": layer,
         "local_sensor": 0, "global_copy": 4 * layer, "detected_photons": count}
        for layer, count in ((0, 3), (1, 2))]
    return {"scan": columns(scan), "layout_layers": columns(layers),
            "layout_sensors": columns(sensors), "layout_transfers": columns(transfers)}


def write_fixture(directory, tables=None, log=GOOD_LOG, summary_changes=None):
    tables = fixture() if tables is None else tables
    root_path = directory / "result.root"
    with uproot.recreate(root_path) as root:
        for name, data in tables.items():
            root.mktree(name, {column: values.dtype for column, values in data.items()})
            root[name].extend(data)
        for name in ("stack_layers", "stack_transfers"):
            root.mktree(name, {"event_id": np.dtype("int32")})
    summary = {"events": 2, "committed_events": 2,
               "generated_optical_photons": 12, "sipm_detected_photons": 5,
               "scintillation_photons": 10, "cerenkov_photons": 2,
               "collection_efficiency_valid": 1, "collection_efficiency": 5.0 / 12,
               "steel_edep_sum_mev": 0.21, "tile_edep_sum_mev": 0.1,
               "net_sipm_photons_per_event": 2.5, "production_scint_photons_per_event": 5.0}
    summary.update(summary_changes or {})
    with (directory / "result_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary)
        writer.writeheader()
        writer.writerow(summary)
    log_path = directory / "run.log"
    log_path.write_text(log)
    return root_path, log_path


class AuditTablesTests(unittest.TestCase):
    def test_all_layouts_and_zero_denominator_event(self):
        for layout in audit.SENSORS:
            with self.subTest(layout=layout):
                report = audit.audit_tables(fixture(layout), layout, 2)
                self.assertEqual(report["generated_photons"], 12)
                self.assertEqual(report["detected_photons"], 5)
                self.assertEqual(report["cross_layer_detected_photons"], 2)
                self.assertEqual(report["unknown_origin_detected_photons"], 0)
                self.assertEqual(report["tile_thickness_mm"], 4)

    def test_six_thicknesses_use_corresponding_source_position(self):
        for thickness, source_z in ((4, 223.75), (8, 243.75), (12, 263.75),
                                    (16, 283.75), (20, 303.75), (24, 323.75)):
            with self.subTest(thickness=thickness):
                tables = fixture()
                tables["scan"]["shoot_z_mm"][:] = source_z
                report = audit.audit_tables(tables, "back-two", 2, tile_thickness_mm=thickness)
                self.assertEqual(report["tile_thickness_mm"], thickness)

    def test_24mm_rejects_old_or_shifted_source(self):
        for source_z in (223.75, 323.751):
            with self.subTest(source_z=source_z):
                tables = fixture()
                tables["scan"]["shoot_z_mm"][:] = source_z
                with self.assertRaisesRegex(ValueError, "wrong source shoot_z_mm"):
                    audit.audit_tables(tables, "back-two", 2, tile_thickness_mm=24)

    def test_unsupported_thickness(self):
        with self.assertRaisesRegex(ValueError, "unsupported tile thickness"):
            audit.audit_tables(fixture(), "back-two", 2, tile_thickness_mm=6)

    def test_missing_sensor_row_including_zero_response(self):
        tables = fixture()
        tables["layout_sensors"] = {name: values[:-1] for name, values in tables["layout_sensors"].items()}
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            audit.audit_tables(tables, "back-two", 2)

    def test_duplicate_sensor_row(self):
        tables = fixture()
        tables["layout_sensors"] = {name: np.concatenate((values, values[:1]))
                                    for name, values in tables["layout_sensors"].items()}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            audit.audit_tables(tables, "back-two", 2)

    def test_inactive_sensor_copy(self):
        tables = fixture("back-center")
        tables["layout_transfers"]["global_copy"][0] = 1
        tables["layout_transfers"]["local_sensor"][0] = 1
        with self.assertRaisesRegex(ValueError, "inactive sensor"):
            audit.audit_tables(tables, "back-center", 2)

    def test_wrong_sensor_copy(self):
        tables = fixture()
        tables["layout_sensors"]["global_copy"][2] = 2  # layer 1 must start at 4
        with self.assertRaisesRegex(ValueError, "sensor identity mismatch"):
            audit.audit_tables(tables, "back-two", 2)

    def test_unknown_origin(self):
        tables = fixture()
        tables["layout_transfers"]["origin_layer"][0] = -1
        with self.assertRaisesRegex(ValueError, "unknown origin"):
            audit.audit_tables(tables, "back-two", 2)

    def test_transfer_wrong_total_or_local_origin(self):
        for field, value, error in (("detected_photons", 4, "all-origin mismatch"),
                                    ("origin_layer", 2, "local-origin mismatch")):
            with self.subTest(field=field):
                tables = fixture()
                tables["layout_transfers"][field][0] = value
                with self.assertRaisesRegex(ValueError, error):
                    audit.audit_tables(tables, "back-two", 2)

    def test_source_values(self):
        for field, value in (("primary_kinetic_energy_mev", 1000.001),
                             ("shoot_x_mm", 0.001), ("shoot_y_mm", 0.001),
                             ("shoot_z_mm", 223.751)):
            with self.subTest(field=field):
                tables = fixture()
                tables["scan"][field][0] = value
                with self.assertRaisesRegex(ValueError, "wrong (primary|source)"):
                    audit.audit_tables(tables, "back-two", 2)

    def test_zero_denominator_requires_nan_and_invalid_flag(self):
        for field, value in (("collection_efficiency", 0), ("collection_efficiency_valid", 1)):
            with self.subTest(field=field):
                tables = fixture()
                tables["scan"][field][1] = value
                with self.assertRaises(ValueError):
                    audit.audit_tables(tables, "back-two", 2)

    def test_invalid_ids_and_counts(self):
        for name in ("event_id", "all_origin_detected_photons"):
            with self.subTest(name=name):
                tables = fixture()
                tables["layout_sensors"][name] = tables["layout_sensors"][name].astype(float)
                tables["layout_sensors"][name][0] = 0.5
                with self.assertRaisesRegex(ValueError, "noninteger"):
                    audit.audit_tables(tables, "back-two", 2)

    def test_generation_energy_and_event_totals(self):
        for tree, field, value in (("layout_layers", "generated_optical_photons", 13),
                                    ("scan", "scintillation_photons", 11),
                                    ("scan", "sipm_detected_photons", 6),
                                    ("layout_layers", "steel_edep_mev", 0.3),
                                    ("layout_layers", "tile_edep_mev", float("nan"))):
            with self.subTest(field=field):
                tables = fixture()
                tables[tree][field][0] = value
                with self.assertRaises(ValueError):
                    audit.audit_tables(tables, "back-two", 2)


class AuditFileTests(unittest.TestCase):
    def test_24mm_root_file_source_and_report(self):
        tables = fixture()
        tables["scan"]["shoot_z_mm"][:] = 323.75
        with tempfile.TemporaryDirectory() as temporary:
            root, log = write_fixture(Path(temporary), tables=tables)
            report = audit.audit_file(root, "back-two", 2, log, 0, tile_thickness_mm=24)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["tile_thickness_mm"], 24)
            with self.assertRaisesRegex(ValueError, "wrong source shoot_z_mm"):
                audit.audit_file(root, "back-two", 2, log, 0)

    def test_real_root_ttrees_summary_and_known_serial_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, log = write_fixture(Path(temporary))
            report = audit.audit_file(root, "back-two", 2, log, 0)
            self.assertEqual(report["status"], "passed")
            for key in ("root_sha256", "log_sha256", "summary_sha256"):
                self.assertEqual(len(report[key]), 64)

    def test_nonzero_exit_rejects_valid_saved_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, log = write_fixture(Path(temporary))
            with self.assertRaisesRegex(ValueError, "process exited 139"):
                audit.audit_file(root, "back-two", 2, log, 139)

    def test_norindex_forms(self):
        for text in ("No RINDEX: 1", "NoRINDEX: 0.01", "NoRINDEX: 1e-3", "NoRINDEX detected"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temporary:
                root, log = write_fixture(Path(temporary), log=GOOD_LOG + text)
                with self.assertRaisesRegex(ValueError, "NoRINDEX"):
                    audit.audit_file(root, "back-two", 2, log, 0)

    def test_other_exception_and_changed_warning_rejected(self):
        for log_text in (GOOD_LOG.replace("Analysis_W001", "GeomVol1002"),
                         GOOD_LOG.replace("Setting was ignored.", "Setting was ignored.\nUnexpected failure."),
                         GOOD_LOG + "Overlap is detected\n", GOOD_LOG + "Segmentation fault\n"):
            with self.subTest(log=log_text), tempfile.TemporaryDirectory() as temporary:
                root, log = write_fixture(Path(temporary), log=log_text)
                with self.assertRaisesRegex(ValueError, "simulation log failure"):
                    audit.audit_file(root, "back-two", 2, log, 0)

    def test_primary_and_event_count_in_log(self):
        for log_text in (GOOD_LOG.replace("neutron", "gamma"),
                         GOOD_LOG.replace("Number of events: 2", "Number of events: 1")):
            with self.subTest(log=log_text), tempfile.TemporaryDirectory() as temporary:
                root, log = write_fixture(Path(temporary), log=log_text)
                with self.assertRaisesRegex(ValueError, "log (primary|event)"):
                    audit.audit_file(root, "back-two", 2, log, 0)

    def test_summary_corruption(self):
        for field, value in (("sipm_detected_photons", 6), ("committed_events", 1),
                             ("scintillation_photons", 9), ("collection_efficiency", 0),
                             ("steel_edep_sum_mev", 0.2), ("net_sipm_photons_per_event", 5)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root, log = write_fixture(Path(temporary), summary_changes={field: value})
                with self.assertRaisesRegex(ValueError, "summary"):
                    audit.audit_file(root, "back-two", 2, log, 0)

    def test_legacy_tree_must_be_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, log = write_fixture(Path(temporary))
            with uproot.update(root) as output:
                output.mktree("stack_layers", {"event_id": np.dtype("int32")})
                output["stack_layers"].extend({"event_id": np.array([0], dtype=np.int32)})
            with self.assertRaisesRegex(ValueError, "legacy tree stack_layers"):
                audit.audit_file(root, "back-two", 2, log, 0)


if __name__ == "__main__":
    unittest.main()
