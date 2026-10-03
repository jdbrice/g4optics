"""Small independent statistical/provenance tests; no Geant4 execution."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import numpy as np
import uproot

SPEC = importlib.util.spec_from_file_location(
    "layout_first_scan_analysis", Path(__file__).resolve().parents[1] / "analysis.py")
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def manifest_fixture():
    identity = {"source_commit": "a" * 40, "geant4_version": "11.4.2",
                "architecture": "x86_64", "run_manager": "Serial",
                "executable_sha256": "b" * 64, "sif_sha256": "c" * 64,
                "dataset_manifest_sha256": "d" * 64}
    tasks = []
    for thickness in analysis.THICKNESSES:
        for layout in analysis.LAYOUTS:
            for block in range(10):
                index = len(tasks)
                tasks.append({"task_id": f"t{thickness}-{layout}-{block}",
                              "tile_thickness_mm": thickness, "layout": layout,
                              "block_id": block, "events": 100,
                              "seeds": [100001 + 2*index, 100002 + 2*index]})
    return {"schema_version": "steel-layout-first-scan-analysis-input-v1",
            "purpose": "independent-first-formal-layout-scan", "approved_source_commit": "a" * 40,
            "simulation_identity": identity, "analysis_seed": 2026100209,
            "bootstrap_replicates": 10000, "excluded_simulation_seed_values": [71004, 72004],
            "tasks": tasks}


def root_fixture(path, layout, *, all_local=False):
    """100 events: D=(1,9,0,...), G=(1,99,0,...); second response is cross-layer."""
    n = analysis.SENSORS[layout]
    d, g = np.zeros(100, dtype=np.int32), np.zeros(100, dtype=np.int32)
    d[:2], g[:2] = [1, 9], [1, 99]
    scan = {"event_id": np.arange(100, dtype=np.int32),
            "sipm_detected_photons": d, "generated_optical_photons": g,
            "scintillation_photons": g, "cerenkov_photons": np.zeros(100, dtype=np.int32),
            "steel_edep_mev": np.zeros(100), "tile_edep_mev": np.zeros(100)}
    layers = {"event_id": np.repeat(np.arange(100, dtype=np.int32), 10),
              "layer": np.tile(np.arange(10, dtype=np.int32), 100)}
    for name in ("all_origin_detected_photons", "local_origin_detected_photons", "generated_optical_photons"):
        layers[name] = np.zeros(1000, dtype=np.int32)
    layers["all_origin_detected_photons"][[0, 11]] = [1, 9]
    layers["local_origin_detected_photons"][0] = 1
    layers["generated_optical_photons"][[0, 10]] = [1, 99]
    layers["steel_edep_mev"] = np.zeros(1000)
    layers["tile_edep_mev"] = np.zeros(1000)
    sensors = {"event_id": np.repeat(np.arange(100, dtype=np.int32), 10*n),
               "layer": np.tile(np.repeat(np.arange(10, dtype=np.int32), n), 100),
               "local_sensor": np.tile(np.arange(n, dtype=np.int32), 1000),
               "all_origin_detected_photons": np.zeros(1000*n, dtype=np.int32),
               "local_origin_detected_photons": np.zeros(1000*n, dtype=np.int32)}
    sensors["all_origin_detected_photons"][[0, 11*n+n-1]] = [1, 9]
    sensors["local_origin_detected_photons"][0] = 1
    if all_local:
        layers["local_origin_detected_photons"][11] = 9
        sensors["local_origin_detected_photons"][11*n+n-1] = 9
    # Deliberately reverse ROOT row order; analysis must use event/volume IDs.
    with uproot.recreate(path) as root:
        for name, data in (("scan", scan), ("layout_layers", layers), ("layout_sensors", sensors)):
            data = {column: values[::-1] for column, values in data.items()}
            root.mktree(name, {column: values.dtype for column, values in data.items()})
            root[name].extend(data)


class ManifestTests(unittest.TestCase):
    def test_complete_fixed_allocation_and_dynamic_pr_commit(self):
        manifest = manifest_fixture()
        self.assertEqual(len(analysis.validate_manifest(manifest)), 240)
        manifest["approved_source_commit"] = "f" * 40
        manifest["simulation_identity"]["source_commit"] = "f" * 40
        self.assertEqual(len(analysis.validate_manifest(manifest)), 240)

    def test_missing_duplicate_seed_block_and_unapproved_source_rejected(self):
        for failure in ("missing", "seed", "block", "source", "excluded"):
            with self.subTest(failure=failure):
                manifest = manifest_fixture()
                if failure == "missing":
                    manifest["tasks"].pop()
                elif failure == "seed":
                    manifest["tasks"][1]["seeds"][0] = manifest["tasks"][0]["seeds"][0]
                elif failure == "block":
                    manifest["tasks"][1]["block_id"] = 0
                elif failure == "source":
                    manifest["simulation_identity"]["source_commit"] = "e" * 40
                else:
                    manifest["excluded_simulation_seed_values"] = [100001]
                with self.assertRaises(ValueError):
                    analysis.validate_manifest(manifest)

    def test_task_digest_is_order_independent_and_binds_seeds(self):
        task = manifest_fixture()["tasks"][0]
        self.assertEqual(analysis.task_sha256(task), analysis.task_sha256(dict(reversed(list(task.items())))))
        changed = copy.deepcopy(task)
        changed["seeds"][0] += 1
        self.assertNotEqual(analysis.task_sha256(task), analysis.task_sha256(changed))

    def test_path_containment_with_tmp_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "present").write_text("ok")
            self.assertEqual(analysis.local_path(base, "present"), (base / "present").resolve())
            with self.assertRaises(ValueError):
                analysis.local_path(base, "../outside")


class StatisticsTests(unittest.TestCase):
    def test_whole_event_shared_weights_and_reproducibility(self):
        values = np.arange(10, dtype=float)
        matrix = np.column_stack([values, 2*values])
        first = analysis.bootstrap_means(matrix, np.random.default_rng(2026100209))
        second = analysis.bootstrap_means(matrix, np.random.default_rng(2026100209))
        self.assertEqual(first.shape, (10000, 2))
        np.testing.assert_array_equal(first, second)
        np.testing.assert_array_equal(first[:, 1], 2*first[:, 0])
        self.assertAlmostEqual(first[:, 0].mean(), 4.5, delta=0.03)

    def test_invalid_ratio_replicate_is_not_silently_removed(self):
        samples = analysis.ratio(np.array([1., 1.]), np.array([0., 1.]))
        result = analysis.interval(1.0, samples)
        self.assertIsNone(result["percentile_interval"])
        self.assertEqual(result["invalid_bootstrap_replicates"], 1)

    def test_bonferroni_interval_has_registered_tail(self):
        samples = np.arange(10000, dtype=float)
        result = analysis.interval(5000., samples, analysis.PRIMARY_TAIL)
        self.assertAlmostEqual(result["nominal_individual_coverage"], 1-0.05/18)
        np.testing.assert_allclose(result["percentile_interval"], np.quantile(samples, [0.05/36, 1-0.05/36]))

    def test_zero_cross_layer_events_remain_exactly_zero_in_all_replicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all-local.root"
            root_fixture(path, "back-two", all_local=True)
            block, labels = analysis.event_matrix(path, "back-two")
            index = labels.index("cross_D")
            np.testing.assert_array_equal(block[:, index], np.zeros(100))
            bootstrap = analysis.bootstrap_means(block[:, [0, index]], np.random.default_rng(2026100209))
            np.testing.assert_array_equal(bootstrap[:, 1], np.zeros(10000))
            matrix = np.tile(block, (10, 1))
            summaries = analysis.summarize(matrix, labels,
                                          np.tile(matrix.mean(axis=0), (20, 1)), "back-two")
            for name in ("cross_layer_D_per_neutron", "cross_layer_collection_share"):
                self.assertEqual(summaries[name]["estimate"], 0.0)
                self.assertEqual(summaries[name]["percentile_interval"], [0.0, 0.0])

    def test_nonzero_cross_counts_use_direct_whole_event_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cross-layer.root"
            root_fixture(path, "back-two")
            block, labels = analysis.event_matrix(path, "back-two")
            index = labels.index("cross_D")
            expected = np.zeros(100)
            expected[1] = 9
            np.testing.assert_array_equal(block[:, index], expected)
            bootstrap = analysis.bootstrap_means(block[:, [0, index]], np.random.default_rng(2026100209))
            weights = np.random.default_rng(2026100209).multinomial(100, np.full(100, .01), size=100)
            np.testing.assert_array_equal(bootstrap[:100, 1], weights @ expected / 100)
            np.testing.assert_array_equal(bootstrap[:100, 0], (weights[:, 0] + 9*weights[:, 1]) / 100)

    def test_negative_primary_contrast_is_not_clamped(self):
        result = analysis.interval(-2.0, np.array([-3.0, -2.0, -1.0]))
        self.assertEqual(result["estimate"], -2.0)
        self.assertLess(result["percentile_interval"][1], 0)

    def test_sensor_shapes_ratio_of_totals_zeros_and_cross_layer(self):
        for layout, n in analysis.SENSORS.items():
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "synthetic.root"
                root_fixture(path, layout)
                block, labels = analysis.event_matrix(path, layout)
                self.assertEqual(block.shape, (100, 8 + 50 + 20*n))
                self.assertEqual(len(set(labels)), len(labels))
                np.testing.assert_array_equal(block[:2, :2], [[1, 1], [9, 99]])
                matrix = np.tile(block, (10, 1))
                # Constant replicate means isolate aggregation/ratio formulas;
                # the separate bootstrap test checks actual resampling.
                replicates = np.tile(matrix.mean(axis=0), (20, 1))
                result = analysis.summarize(matrix, labels, replicates, layout)
                self.assertAlmostEqual(result["legacy_D_over_G"]["estimate"], 0.1)
                self.assertNotAlmostEqual(result["legacy_D_over_G"]["estimate"], (1 + 9/99)/2)
                self.assertAlmostEqual(result["D"]["estimate"], 0.1)
                self.assertEqual(result["response_distribution_descriptive"]["zero_events"], 980)
                self.assertAlmostEqual(result["cross_layer_D_per_neutron"]["estimate"], 0.09)
                self.assertAlmostEqual(result["cross_layer_collection_share"]["estimate"], 0.9)
                self.assertEqual(len(result["sensor_share_of_D"]), 10*n)
                self.assertAlmostEqual(sum(value["estimate"] for value in result["sensor_share_of_D"].values()), 1)
                self.assertAlmostEqual(result["nominal_total_collection_face_area_mm2"], 10*n*2.4*2.4)


if __name__ == "__main__":
    unittest.main()
