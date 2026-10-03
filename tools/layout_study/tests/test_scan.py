"""Formal first-scan invariants using frozen inputs and mocked process/audit results."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("layout_formal_scan", DIRECTORY / "scan.py")
scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scan)
sys.path.pop(0)
COMMIT = "1" * 40
REQUIRED_SOURCES = ("test/OpNovice2/OpNovice2.cc", "test/OpNovice2/src/DetectorConstruction.cc",
                    "tools/layout_study/scan.py", "tools/layout_study/audit.py",
                    "tools/layout_study/audit_geometry.py", "tools/layout_study/run_local.py")


class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="layout-formal-scan-test-")
        cls.base = Path(cls.temporary.name)
        cls.repo = DIRECTORY.parents[1]
        header = bytearray(64); header[:6] = b"\x7fELF\x02\x01"; header[18:20] = b">\0"
        binary, image, datasets = cls.base / "binary", cls.base / "runtime.sif", cls.base / "datasets"
        binary.write_bytes(header); image.write_text("mock SIF, never executed"); datasets.mkdir()
        data_manifest = cls.base / "data-SHA256SUMS"; data_manifest.write_text("frozen data list")
        preflight_path = cls.base / "data-preflight.json"
        scan.write_json(preflight_path, {"schema_version": "steel-layout-dataset-preflight-v1", "status": "verified",
                        "geant4_version": "11.4.2", "dataset_dir": str(datasets),
                        "dataset_manifest_sha256": scan.sha(data_manifest), "verified_file_count": 3,
                        "job_id": "800", "node": "n01"})
        cls.identity = {"source_commit": COMMIT, "geant4_version": "11.4.2", "architecture": "x86_64",
                        "run_manager": "Serial", "executable_sha256": scan.sha(binary), "sif_sha256": scan.sha(image),
                        "dataset_manifest_sha256": scan.sha(data_manifest)}
        cls.runtime = {"simulation_identity": cls.identity, "dataset_preflight_sha256": scan.sha(preflight_path),
                       "paths": {"source_dir": str(cls.repo), "executable": str(binary), "sif": str(image),
                                 "dataset_dir": str(datasets), "dataset_manifest": str(data_manifest),
                                 "dataset_preflight": str(preflight_path)}}
        runtime_path, excluded_path = cls.base / "runtime.json", cls.base / "excluded.json"
        scan.write_json(runtime_path, cls.runtime); scan.write_json(excluded_path, [100000, 100002, 20261002, 10401])
        cls.campaign = cls.base / "campaign"
        source_files = {name: scan.sha(cls.repo / name) for name in REQUIRED_SOURCES}
        with patch.object(scan, "source_snapshot", return_value=source_files):
            cls.manifest_hash = scan.prepare_campaign(cls.repo, cls.campaign, runtime_path, excluded_path, COMMIT, 100000)
        cls.manifest = scan.read_json(cls.campaign / "manifest.json")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def copy_campaign(self):
        temporary = tempfile.TemporaryDirectory(prefix="layout-formal-case-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "campaign"
        shutil.copytree(self.campaign, root)
        return root

    def test_prepare_exact_matrix_unique_seeds_and_unchanged_macro(self):
        scan.validate_manifest(self.manifest)
        tasks = self.manifest["tasks"]
        self.assertEqual(sum(t["events"] for t in tasks), 24000)
        self.assertEqual(len({s for task in tasks for s in task["seeds"]}), 480)
        self.assertEqual(tasks[0]["seeds"], [100001, 100003])
        self.assertEqual(tasks[-1]["task_id"], "t24-edge-two-b09")
        for task in tasks:
            scan.verify_inputs(self.campaign, self.manifest, task)
            registry = scan.read_json((self.campaign / task["config_path"]).parent / "registry.json")
            self.assertEqual(registry["stage"], "formal-inputs-not-accepted")
            self.assertFalse(registry["accepted_statistical_evidence"])
        self.assertFalse((self.campaign / "accepted").exists())
        # The independent analyzer consumes this same schema, without an adapter.
        spec = importlib.util.spec_from_file_location("formal_analysis_schema", DIRECTORY / "analysis.py")
        analysis = importlib.util.module_from_spec(spec); spec.loader.exec_module(analysis)
        self.assertEqual(len(analysis.validate_manifest(self.manifest)), 240)

    def test_manifest_collision_budget_and_identity_rejections(self):
        for mutation in (lambda m: m["tasks"][1].update(seeds=m["tasks"][0]["seeds"]),
                         lambda m: m["tasks"][0].update(seeds=[100000, 100001]),
                         lambda m: m["tasks"][0].update(events=101),
                         lambda m: m["tasks"].pop(),
                         lambda m: m["simulation_identity"].update(source_commit="2" * 40)):
            manifest = copy.deepcopy(self.manifest); mutation(manifest)
            with self.assertRaises(ValueError): scan.validate_manifest(manifest)
        with self.assertRaises(ValueError): scan.allocate_seeds(scan.inputs.MAX_SEED - 1, [1])

    def test_prepare_rejects_wrong_head_or_uncommitted_source(self):
        with patch.object(scan.subprocess, "check_output", return_value="2" * 40):
            with self.assertRaisesRegex(ValueError, "HEAD"):
                scan.source_snapshot(self.repo, COMMIT)
        with patch.object(scan.subprocess, "check_output", side_effect=[COMMIT, " M tools/layout_study/scan.py"]):
            with self.assertRaisesRegex(ValueError, "clean"):
                scan.source_snapshot(self.repo, COMMIT)

    def test_runtime_uses_bound_preflight_not_per_task_dataset_rehash(self):
        with patch.object(scan, "sha", wraps=scan.sha) as hashes:
            scan.verify_runtime(self.runtime, self.manifest)
        called = {str(call.args[0]) for call in hashes.call_args_list}
        self.assertIn(self.runtime["paths"]["dataset_manifest"], called)
        self.assertIn(self.runtime["paths"]["dataset_preflight"], called)
        self.assertFalse(any(str(self.runtime["paths"]["dataset_dir"]) + "/" in name for name in called))
        runtime = copy.deepcopy(self.runtime); runtime["dataset_preflight_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "preflight receipt hash"):
            scan.verify_runtime(runtime, self.manifest)

    def test_manifest_input_hash_and_path_escape_rejected(self):
        root = self.copy_campaign()
        with self.assertRaisesRegex(ValueError, "manifest hash"):
            scan.load_campaign(root / "manifest.json", "0" * 64)
        task = self.manifest["tasks"][0]
        with (root / task["macro_path"]).open("a") as stream: stream.write("/run/beamOn 1\n")
        with self.assertRaisesRegex(ValueError, "input hash"):
            scan.verify_inputs(root, self.manifest, task)
        with self.assertRaises(ValueError): scan.located(root, "../outside")

    def test_slurm_requires_one_cpu_and_actual_compute_node(self):
        env = {"SLURM_ARRAY_TASK_ID": "3", "SLURM_ARRAY_TASK_COUNT": "240", "SLURM_CPUS_PER_TASK": "1",
               "SLURM_CPUS_ON_NODE": "1", "SLURMD_NODENAME": "n01", "SLURM_JOB_ID": "903", "SLURM_ARRAY_JOB_ID": "900"}
        with patch.object(socket, "gethostname", return_value="n01.example"):
            self.assertEqual(scan.slurm_identity(3, env)["job_id"], "903")
            for key, value in (("SLURM_CPUS_PER_TASK", "2"), ("SLURM_CPUS_ON_NODE", "2"),
                               ("SLURMD_NODENAME", "login"), ("SLURM_ARRAY_TASK_COUNT", "24")):
                changed = dict(env); changed[key] = value
                with self.assertRaises(ValueError): scan.slurm_identity(3, changed)

    @staticmethod
    def geometry(config, log, code):
        return {"status": "passed", "log_sha256": scan.sha(log)}

    @staticmethod
    def event_audit(root, layout, events, log, code, tile_thickness_mm=4):
        return {"status": "passed", "log_sha256": scan.sha(log)}

    def test_worker_verified_is_not_accepted_and_failure_never_retries(self):
        for code, timeout in ((0, False), (134, False), (124, True)):
            with self.subTest(code=code):
                root = self.copy_campaign()
                def process(command, *, cwd, env, log, timeout):
                    self.assertEqual(command[:2], ["/usr/bin/time", "-v"])
                    self.assertEqual(timeout, 3300)
                    log.write("mock process\n")
                    for name in ("result.root", "result_summary.csv", "time.txt"):
                        (cwd / name).write_text("mock artifact")
                    return code, code == 124
                scheduler = {"job_id": "10000", "array_job_id": "900", "array_index": 0, "cpus": 1, "node": "n01"}
                with patch.object(scan, "slurm_identity", return_value=scheduler), \
                     patch.object(scan.subprocess, "check_output", return_value="11.4.2\nx86_64\n"), \
                     patch.object(scan, "run_process", side_effect=process) as run, \
                     patch.object(scan.audit_geometry, "audit_geometry_file", side_effect=self.geometry), \
                     patch.object(scan.audit, "audit_file", side_effect=self.event_audit):
                    result = scan.worker(root / "manifest.json", self.manifest_hash, 0)
                    self.assertEqual(result, 0 if code == 0 else 1)
                    self.assertEqual(run.call_count, 1)
                    with self.assertRaises(FileExistsError):
                        scan.worker(root / "manifest.json", self.manifest_hash, 0)
                    self.assertEqual(run.call_count, 1)
                receipt = scan.read_json(root / "results/t04-back-four-b00/process-receipt.json")
                self.assertEqual(receipt["exit_code"], code)
                self.assertEqual(receipt["status"], "process-verified" if code == 0 else "failed")
                self.assertFalse((root / "accepted").exists())

    def scheduler_file(self, root):
        path = root / "sacct.psv"
        path.write_text("JobID|JobIDRaw|State|ExitCode|AllocCPUS|NodeList|\n" + "".join(
            f"900_{i}|{10000+i}|COMPLETED|0:0|1|n01|\n" for i in range(240)))
        return path

    def test_scheduler_rejects_missing_failed_duplicate_and_extra_cpu_allocations(self):
        root = self.copy_campaign(); path = self.scheduler_file(root); original = path.read_text()
        self.assertEqual(len(scan.scheduler_rows(path, "900")), 240)
        for changed in (original.replace("COMPLETED", "TIMEOUT", 1), original.replace("|0:0|", "|1:0|", 1),
                        original.replace("|1|n01|", "|2|n01|", 1), "\n".join(original.splitlines()[:-1]),
                        original + original.splitlines()[1] + "\n"):
            path.write_text(changed)
            with self.assertRaises(ValueError): scan.scheduler_rows(path, "900")

    def test_collect_accepts_only_all_completed_identity_bound_outputs(self):
        root = self.copy_campaign(); sacct = self.scheduler_file(root)
        for index, task in enumerate(self.manifest["tasks"]):
            directory = root / "results" / task["task_id"]; directory.mkdir(parents=True)
            for name in ("result.root", "run.log", "result_summary.csv", "time.txt"):
                (directory / name).write_text("mock artifact " + task["task_id"])
            audit = {"status": "passed", "log_sha256": scan.sha(directory / "run.log")}
            scan.write_json(directory / "geometry-audit.json", audit); scan.write_json(directory / "event-audit.json", audit)
            scan.write_json(directory / "process-receipt.json", {
                "schema_version": scan.PROCESS_SCHEMA, "status": "process-verified", "task_id": task["task_id"],
                "task_sha256": scan.object_sha(task), "manifest_sha256": self.manifest_hash, "seeds": task["seeds"],
                "simulation_identity": self.identity, "exit_code": 0, "timed_out": False,
                "worker_scheduler": {"job_id": str(10000+index), "array_job_id": "900", "array_index": index, "cpus": 1, "node": "n01"},
                "files": scan.files_for(root, directory), "time_sha256": scan.sha(directory / "time.txt"),
                "dataset_preflight_sha256": self.runtime["dataset_preflight_sha256"], "observed_runtime": ["11.4.2", "x86_64"]})
        original = sacct.read_text(); sacct.write_text(original.replace("COMPLETED", "RUNNING", 1))
        with self.assertRaises(ValueError): scan.collect(root / "manifest.json", self.manifest_hash, sacct, "900")
        self.assertFalse((root / "accepted").exists()); sacct.write_text(original)
        with patch.object(scan.audit_geometry, "audit_geometry_file", side_effect=self.geometry), \
             patch.object(scan.audit, "audit_file", side_effect=self.event_audit):
            self.assertEqual(scan.collect(root / "manifest.json", self.manifest_hash, sacct, "900"), 240)
        receipt = scan.read_json(root / "accepted/t04-back-four-b00.json")
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["task_sha256"], scan.object_sha(self.manifest["tasks"][0]))
        self.assertEqual(receipt["scheduler"], {"state": "COMPLETED", "exit_code": "0:0", "cpus": 1, "job_id": "10000"})
        with self.assertRaises(ValueError): scan.collect(root / "manifest.json", self.manifest_hash, sacct, "900")


if __name__ == "__main__":
    unittest.main()
