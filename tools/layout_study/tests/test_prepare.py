"""Input tests that do not build or launch Geant4."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


MODULE_PATH = Path(__file__).resolve().parents[1] / "prepare.py"
SPEC = importlib.util.spec_from_file_location("layout_prepare", MODULE_PATH)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)
REPO = MODULE_PATH.parents[2]


def commands(text: str) -> list[str]:
    return [line.partition("#")[0].strip() for line in text.splitlines()
            if prepare.command_name(line)]


class PrepareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="layout-prepare-test-")
        cls.output = Path(cls.temporary.name) / "inputs"
        cls.registry = prepare.prepare(REPO, cls.output, events=2, seeds=(20261002, 20261003))
        cls.template = (cls.output / "baseline_template.mac").read_text()
        cls.reference_path = MODULE_PATH.parent / "templates/macro_reference.json"
        cls.reference = json.loads(cls.reference_path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_preserves_all_baseline_physics_and_material_commands(self):
        # Only the explicitly approved geometry/execution commands may differ.
        changed = {
            "/analysis/setFileName", "/opnovice2/sipm/layout", "/opnovice2/sipm/face",
            "/gps/pos/centre", "/random/setSeeds", "/run/beamOn",
            "/opnovice2/stack/layoutStudy", "/geometry/test/run",
        }
        baseline = [line for line in commands(self.template) if line.split()[0] not in changed]
        self.assertIn("/gps/particle neutron", baseline)
        self.assertIn("/gps/energy 1000 MeV", baseline)
        self.assertIn("/opnovice2/surfacePreset polishedfrontpainted", baseline)
        self.assertTrue(any(line.startswith("/opnovice2/surfaceProperty REFLECTIVITY ")
                            for line in baseline))
        for layout in prepare.LAYOUTS:
            macro = (self.output / layout / "run.mac").read_text()
            self.assertEqual(baseline, [line for line in commands(macro)
                                       if line.split()[0] not in changed])

    def test_geometry_check_cannot_change_recorded_physics_seed_start(self):
        for layout in prepare.LAYOUTS:
            active = commands((self.output / layout / "run.mac").read_text())
            self.assertEqual(active[-3:], ["/geometry/test/run",
                                          "/random/setSeeds 20261002 20261003",
                                          "/run/beamOn 2"])
            self.assertEqual(sum(line.startswith("/run/beamOn ") for line in active), 1)
            self.assertLess(active.index("/opnovice2/stack/layoutStudy true"),
                            active.index("/run/initialize"))

    def test_zero_events_initializes_and_checks_without_beam_on(self):
        for layout in prepare.LAYOUTS:
            active = commands(prepare.study_macro(self.template, layout, 0, (12, 34)))
            self.assertIn("/run/initialize", active)
            self.assertIn("/geometry/test/run", active)
            self.assertFalse(any(line.startswith("/run/beamOn") for line in active))

    def test_geometry_registry_matches_four_mm_contract(self):
        expected_counts = {"back-four": 40, "back-two": 20, "back-center": 10, "edge-two": 20}
        for layout, count in expected_counts.items():
            config = json.loads((self.output / layout / "config.json").read_text())
            self.assertEqual(config["sensor_count"], count)
            self.assertEqual(config["source"]["position_mm"], [0, 0, 223.75])
            self.assertEqual(config["stack_length_mm"], 444.5)
            layers = config["layer_geometry"]
            self.assertEqual(layers[0]["steel_center_z_mm"] + 20, 222.25)
            self.assertEqual(layers[-1]["tile_center_z_mm"] - 2, -222.25)
            copies = [sensor["global_copy"] for layer in layers for sensor in layer["sensors"]]
            self.assertEqual(copies, [4 * layer + sensor for layer in range(10)
                                      for sensor in range(count // 10)])
            for layer, following in zip(layers, layers[1:]):
                self.assertEqual((layer["tile_center_z_mm"] - 2)
                                 - (following["steel_center_z_mm"] + 20), 0.5)
            if layout == "back-two":
                self.assertEqual(config["sipm_local_centers_mm"], [[-25, -25], [25, 25]])

    def test_all_twenty_four_macro_bytes_match_frozen_reference(self):
        self.assertEqual(prepare.sha256(self.reference_path.read_bytes()),
                         "5f30028138b39b5474b5701924993bbb55e0da3811ca1fd6248612fd452c7d4a")
        self.assertEqual(self.reference["baseline_template_sha256"], prepare.BASELINE_TEMPLATE_SHA256)
        cases = self.reference["cases"]
        self.assertEqual(len(cases), 24)
        self.assertEqual({(case["tile_thickness_mm"], case["layout"]) for case in cases},
                         {(t, layout) for t in (4, 8, 12, 16, 20, 24) for layout in prepare.LAYOUTS})
        for case in cases:
            macro = prepare.study_macro(self.template, case["layout"], self.reference["events"],
                                        tuple(self.reference["seeds"]), case["tile_thickness_mm"])
            self.assertEqual(prepare.sha256(macro.encode()), case["macro_sha256"])

    def test_source_zip_without_git_bash_or_historical_runner_generates_all_layouts(self):
        # Package only the generator and its data. Neither .git nor the old
        # runner/source files are copied, and child processes cannot find Git
        # or Bash through PATH. Test the actual CLI as a ZIP recipient would.
        root = Path(self.temporary.name) / "zip-copy"
        archive_path = Path(self.temporary.name) / "source.zip"
        files = [MODULE_PATH] + [p for p in (MODULE_PATH.parent / "templates").iterdir() if p.is_file()]
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in files:
                archive.write(path, path.relative_to(REPO))
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(root)
        self.assertFalse((root / ".git").exists())
        self.assertFalse((root / "test/OpNovice2").exists())
        env = os.environ.copy()
        env["PATH"] = str(root / "no-external-programs")
        for thickness in (4, 8, 12, 16, 20, 24):
            output = root / f"inputs-t{thickness:02d}"
            result = subprocess.run(
                [sys.executable, str(root / "tools/layout_study/prepare.py"),
                 "--output-dir", str(output), "--tile-thickness-mm", str(thickness),
                 "--events", "2", "--seed1", "20261002", "--seed2", "20261003"],
                cwd=root, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["layouts"], 4)
            for case in self.reference["cases"]:
                if case["tile_thickness_mm"] == thickness:
                    macro = output / case["layout"] / "run.mac"
                    self.assertEqual(prepare.sha256(macro.read_bytes()), case["macro_sha256"])

    def test_modified_template_and_provenance_are_rejected_before_output(self):
        root = Path(self.temporary.name) / "corrupt-package"
        templates = root / "tools/layout_study/templates"
        shutil.copytree(MODULE_PATH.parent / "templates", templates)
        template = templates / "baseline_template.mac"
        provenance_path = templates / "baseline_template.provenance.json"
        original = template.read_bytes()
        template.write_bytes(original + b"/gps/energy 1 MeV\n")
        forged = json.loads(provenance_path.read_text())
        forged["template_sha256"] = prepare.sha256(template.read_bytes())
        provenance_path.write_text(json.dumps(forged))
        destination = root / "inputs"
        with self.assertRaisesRegex(ValueError, "template checksum"):
            prepare.prepare(root, destination, events=1, seeds=(12, 34))
        self.assertFalse(destination.exists())
        template.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "provenance checksum"):
            prepare.prepare(root, destination, events=1, seeds=(12, 34))
        self.assertFalse(destination.exists())

    def test_all_thickness_macros_change_only_size_and_source_position(self):
        allowed = {"/opnovice2/tank/size", "/gps/pos/centre"}
        for layout in prepare.LAYOUTS:
            original = commands((self.output / layout / "run.mac").read_text())
            for thickness in (4, 8, 12, 16, 20, 24):
                rendered = commands(prepare.study_macro(self.template, layout, 2,
                                                        (20261002, 20261003), thickness))
                self.assertEqual([line for line in original if line.split()[0] not in allowed],
                                 [line for line in rendered if line.split()[0] not in allowed])
                self.assertIn(f"/opnovice2/tank/size 100 100 {thickness} mm", rendered)
                source_z = 10 * (40 + thickness) / 2 + 3.75
                self.assertIn(f"/gps/pos/centre 0 0 {source_z} mm", rendered)

    def test_twenty_four_mm_registry_keeps_nine_fixed_gaps(self):
        destination = Path(self.temporary.name) / "t24"
        registry = prepare.prepare(REPO, destination, events=5, seeds=(120, 340), thickness=24)
        self.assertEqual(registry["stage"], "engineering-validation")
        self.assertFalse(registry["accepted_statistical_evidence"])
        self.assertEqual(registry["tile_thickness_mm"], 24)
        for layout in prepare.LAYOUTS:
            config = json.loads((destination / layout / "config.json").read_text())
            self.assertEqual(config["tile_size_mm"], [100, 100, 24])
            self.assertEqual(config["tile_thickness_mm"], 24)
            self.assertEqual(config["stack_length_mm"], 644.5)
            self.assertEqual(config["source"]["position_mm"], [0, 0, 323.75])
            layers = config["layer_geometry"]
            self.assertEqual(layers[0]["steel_center_z_mm"] + 20, 322.25)
            self.assertEqual(layers[-1]["tile_center_z_mm"] - 12, -322.25)
            for layer, following in zip(layers, layers[1:]):
                self.assertEqual(layer["tile_center_z_mm"] - 12
                                 - (following["steel_center_z_mm"] + 20), 0.5)
            for layer in layers:
                for sensor in layer["sensors"]:
                    center = sensor["center_mm"]
                    if layout == "edge-two":
                        self.assertEqual(center[0], 50.25)
                        self.assertEqual(center[2], layer["tile_center_z_mm"])
                    else:
                        self.assertEqual(center[2], layer["tile_center_z_mm"] - 12.25)

    def test_frozen_inputs_are_checksum_bound(self):
        self.assertEqual(self.registry["baseline"]["git_commit"], prepare.BASELINE_COMMIT)
        packaged_provenance = MODULE_PATH.parent / "templates/baseline_template.provenance.json"
        self.assertEqual(prepare.sha256(packaged_provenance.read_bytes()), prepare.BASELINE_PROVENANCE_SHA256)
        self.assertEqual(self.registry["baseline"], json.loads(packaged_provenance.read_text()))
        self.assertEqual(self.registry["baseline"]["template_sha256"],
                         hashlib.sha256(self.template.encode()).hexdigest())
        for row in (self.output / "SHA256SUMS").read_text().splitlines():
            digest, relative = row.split(maxsplit=1)
            self.assertEqual(digest, hashlib.sha256((self.output / relative).read_bytes()).hexdigest())

    def test_back_four_sensor_order_matches_detector_source(self):
        # Compare with the detector's actual local-ID order, independently of
        # the Python registry definition; swapping IDs leaves geometry intact
        # but would silently associate per-sensor analysis with the wrong tile quadrant.
        source = (REPO / "test/OpNovice2/src/DetectorConstruction.cc").read_text()
        method = source.split("DetectorConstruction::GetSiPMLocalPositions() const", 1)[1]
        block = method.split('if (fSiPMLayout == "back-four")', 1)[1].split("};", 1)[0]
        positions = re.findall(r"G4ThreeVector\((-?offset),\s*(-?offset),\s*0\.\)", block)
        self.assertEqual(len(positions), 4)
        actual = [[-25 if value.startswith("-") else 25 for value in pair] for pair in positions]
        config = json.loads((self.output / "back-four/config.json").read_text())
        self.assertEqual(config["sipm_local_centers_mm"], actual)

    def test_scope_seed_and_overwrite_rejections(self):
        destination = Path(self.temporary.name) / "not-created"
        for options in ({"thickness": 6}, {"thickness": 0}, {"events": -1}, {"seeds": (0, 3)},
                        {"seeds": (3, 3)}, {"layouts": ("back-four", "back-four")}):
            arguments = {"events": 1, "seeds": (12, 34)} | options
            with self.assertRaises(ValueError):
                prepare.prepare(REPO, destination, **arguments)
            self.assertFalse(destination.exists())
        with self.assertRaisesRegex(ValueError, "overwrite"):
            prepare.prepare(REPO, self.output, events=1, seeds=(12, 34))


if __name__ == "__main__":
    unittest.main()
