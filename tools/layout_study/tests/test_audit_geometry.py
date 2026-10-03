"""Corruption tests for placement evidence, independent of a Geant4 installation."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


DIRECTORY = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


prepare = module("prepare")
audit = module("audit_geometry")


def v(values):
    return "(" + ",".join(f"{value:g}" for value in values) + ")"


def log_fixture(config):
    layout, n = config["layout"], config["sensors_per_layer"]
    lines = [f"Stack layout study: layout={layout}, layers=10, internal_gaps=9, "
             f"readout_gap=0.5 mm, core_length={config['stack_length_mm']:g} mm, "
             f"required_source_z={config['source']['position_mm'][2]:g} mm, "
             f"sensor_stride=4, sensors_per_layer={n}"]
    for row in config["layer_geometry"]:
        layer = row["layer"]
        for kind, size in (("tile", config["tile_size_mm"]), ("steel", config["steel_size_mm"])):
            lines.append(f"Stack {kind} placement: layer={layer}, "
                         f"world={v([0, 0, row[kind + '_center_z_mm']])} mm, full_size={v(size)} mm")
        for sensor in row["sensors"]:
            local_id = sensor["local_sensor"]
            local = config["sipm_local_centers_mm"][local_id] + [0]
            lines.append(f"SiPM placement: layout={layout}, layer={layer}, local_sensor={local_id}, "
                         f"copy={sensor['global_copy']}, face={config['sipm_face']}, "
                         f"local={v(local)} mm, world={v(sensor['center_mm'])} mm")
    lines.append(f"Stack layout geometry: PASS, {20+10*n} placed boxes, no positive-volume overlap, "
                 "all contained in World; 9 internal gaps of 0.5 mm, final tile followed by World.")
    return "\n".join(lines) + "\n"


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="layout-geometry-audit-test-")
        cls.output = Path(cls.temporary.name) / "inputs"
        for thickness in (4, 8, 12, 16, 20, 24):
            prepare.prepare(DIRECTORY.parents[1], cls.output / str(thickness),
                            events=0, seeds=(123, 456), thickness=thickness)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def config(self, layout="back-four", thickness=4):
        return json.loads((self.output / str(thickness) / layout / "config.json").read_text())

    def test_six_thicknesses_four_layouts_and_zero_events_need_no_root(self):
        # Independent expected extents for all six prescribed tile thicknesses.
        lengths = {4: 444.5, 8: 484.5, 12: 524.5, 16: 564.5, 20: 604.5, 24: 644.5}
        for thickness, length in lengths.items():
            for layout in ("back-four", "back-two", "back-center", "edge-two"):
                with self.subTest(thickness=thickness, layout=layout):
                    config = self.config(layout, thickness)
                    directory = self.output / str(thickness) / layout
                    log_path = directory / "simulation.log"
                    log_path.write_text(log_fixture(config))
                    report = audit.audit_geometry_file(directory / "config.json", log_path, 0)
                    self.assertEqual(report["status"], "passed")
                    self.assertEqual(report["tile_thickness_mm"], thickness)
                    self.assertEqual(report["core_length_mm"], length)
                    self.assertEqual(report["required_source_z_mm"], length / 2 + 1.5)
                    self.assertEqual(report["actual_internal_gaps_mm"], [0.5] * 9)
                    self.assertFalse((log_path.parent / "result.root").exists())

    def test_disallowed_or_malformed_thickness_rejected(self):
        for thickness in (0, 5, 28, 4.0001, "4", None, True, float("nan"), float("inf")):
            with self.subTest(thickness=thickness):
                original = self.config()
                corrupted = copy.deepcopy(original)
                corrupted["tile_size_mm"][2] = thickness
                with self.assertRaisesRegex(ValueError, "tile thickness"):
                    audit.audit_geometry_text(log_fixture(original), corrupted)
        for size in ([], [100, 100], [100, 100, 4, 0], "100,100,4", None):
            corrupted = self.config()
            corrupted["tile_size_mm"] = size
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "three dimensions"):
                audit.audit_geometry_text("", corrupted)

    def test_thickness_label_consistency_and_historical_four_mm_config(self):
        config = self.config()
        config["tile_thickness_mm"] = 8
        with self.assertRaisesRegex(ValueError, "thickness label"):
            audit.audit_geometry_text(log_fixture(config), config)
        del config["tile_thickness_mm"]
        report = audit.audit_geometry_text(log_fixture(config), config)
        self.assertEqual(report["tile_thickness_mm"], 4)

    def test_each_thickness_rejects_extra_final_gap_and_wrong_internal_gap(self):
        for thickness in (4, 8, 12, 16, 20, 24):
            for layout in ("back-four", "back-two", "back-center", "edge-two"):
                with self.subTest(thickness=thickness, layout=layout):
                    original = self.config(layout, thickness)
                    # A matching corrupted config/log must not mask moving the
                    # last tile by another gap or shortening an internal gap.
                    for layer, field, delta in ((9, "tile_center_z_mm", -0.5),
                                                 (4, "steel_center_z_mm", 0.25)):
                        corrupted = copy.deepcopy(original)
                        corrupted["layer_geometry"][layer][field] += delta
                        with self.assertRaises(ValueError):
                            audit.audit_geometry_text(log_fixture(corrupted), corrupted)
                    for field, value in (("tile_to_next_steel_gap_mm", 0.0),
                                         ("tile_to_next_steel_gap_mm", 1.0),
                                         ("stack_length_mm", original["stack_length_mm"] + 0.5)):
                        corrupted = copy.deepcopy(original)
                        corrupted[field] = value
                        with self.assertRaises(ValueError):
                            audit.audit_geometry_text(log_fixture(corrupted), corrupted)

    def test_side_sensor_local_v_remains_zero_for_every_thickness(self):
        for thickness in (4, 8, 12, 16, 20, 24):
            config = self.config("edge-two", thickness)
            config["sipm_local_centers_mm"][0][1] = thickness / 4
            with self.subTest(thickness=thickness), self.assertRaises(ValueError):
                audit.audit_geometry_text(log_fixture(config), config)

    def test_duplicate_and_missing_placement_rows_are_rejected(self):
        config = self.config()
        lines = log_fixture(config).splitlines()
        for prefix in ("Stack tile placement:", "Stack steel placement:", "SiPM placement:"):
            line = next(line for line in lines if line.startswith(prefix))
            for corrupted in ("\n".join(lines + [line]), "\n".join(x for x in lines if x != line)):
                with self.assertRaises(ValueError):
                    audit.audit_geometry_text(corrupted, config)

    def test_missing_duplicate_or_failed_aabb_and_summary_rejected(self):
        config = self.config()
        original = log_fixture(config)
        for prefix in ("Stack layout study:", "Stack layout geometry:"):
            target = next(line for line in original.splitlines() if line.startswith(prefix))
            for corrupted in (original.replace(target, ""), original + target + "\n"):
                with self.assertRaises(ValueError):
                    audit.audit_geometry_text(corrupted, config)
        with self.assertRaises(ValueError):
            audit.audit_geometry_text(original.replace("geometry: PASS", "geometry: FAIL"), config)

    def test_wrong_copy_coordinate_gap_source_or_final_extent_rejected(self):
        config = self.config()
        original = log_fixture(config)
        changes = (("copy=39", "copy=38"), ("world=(-25,-25,178)", "world=(-25,-25,178.1)"),
                   ("internal_gaps=9", "internal_gaps=10"), ("core_length=444.5", "core_length=445"),
                   ("required_source_z=223.75", "required_source_z=224"),
                   ("world=(0,0,-220.25)", "world=(0,0,-220.75)"))
        for before, after in changes:
            self.assertIn(before, original)
            with self.assertRaises(ValueError):
                audit.audit_geometry_text(original.replace(before, after), config)

    def test_wrong_config_cannot_mask_shifted_log(self):
        config = copy.deepcopy(self.config())
        config["layer_geometry"][9]["tile_center_z_mm"] -= 0.5
        with self.assertRaises(ValueError):
            audit.audit_geometry_text(log_fixture(config), config)

    def test_nonzero_exit_and_exception_rejected(self):
        config = self.config()
        log_path = Path(self.temporary.name) / "failure.log"
        log_path.write_text(log_fixture(config))
        with self.assertRaisesRegex(ValueError, "process exited"):
            audit.audit_geometry_file(self.output / "4/back-four/config.json", log_path, 134)
        log_path.write_text(log_fixture(config) + "*** G4Exception : unexpected_failure\n")
        with self.assertRaisesRegex(ValueError, "simulation log failure"):
            audit.audit_geometry_file(self.output / "4/back-four/config.json", log_path, 0)


if __name__ == "__main__":
    unittest.main()
