"""Small fixture checks; no container, compilation, data download, or job submission."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / "build_runtime.py"
SPEC = importlib.util.spec_from_file_location("layout_build_runtime", MODULE)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class RuntimeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="layout-runtime-build-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_requires_actual_compute_node_one_cpu_and_four_gib(self):
        env = {"SLURM_JOB_ID": "123", "SLURMD_NODENAME": "p0123",
               "SLURM_CPUS_PER_TASK": "1", "SLURM_MEM_PER_NODE": "4096"}
        self.assertEqual(runtime.compute_identity(env, "p0123.ten.osc.edu")["node"], "p0123")
        for changed, host in (({}, "pitzer-login01"), ({"SLURMD_NODENAME": "other"}, "p0123"),
                              ({"SLURM_JOB_ID": ""}, "p0123"), ({"SLURM_CPUS_PER_TASK": "2"}, "p0123"),
                              ({"SLURM_MEM_PER_NODE": "2048"}, "p0123")):
            with self.assertRaises(ValueError):
                runtime.compute_identity({**env, **changed}, host)

    def dataset_fixture(self):
        root = self.root / "data"
        root.mkdir()
        rows = []
        for index, variable in enumerate(sorted(runtime.DATA_VARIABLES)):
            name = "dataset-" + str(index)
            directory = root / name
            directory.mkdir()
            (directory / "table.dat").write_bytes((variable + "\n").encode())
            rows.append(f"{name} {variable} /g4data/{name}")
        return root, "\n".join(rows)

    def test_twelve_real_directories_are_hashed_without_touching_data(self):
        root, text = self.dataset_fixture()
        directories = runtime.dataset_directories(text, root)
        manifest = self.root / "dataset-SHA256SUMS"
        self.assertEqual(runtime.hash_datasets(root, directories, manifest), 12)
        rows = manifest.read_text().splitlines()
        self.assertEqual(len(rows), 12)
        for row in rows:
            digest, name = row.split(maxsplit=1)
            self.assertEqual(digest, hashlib.sha256((root / name).read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):
            runtime.hash_datasets(root, directories, manifest)

    def test_missing_duplicate_escaping_or_symlinked_dataset_is_rejected(self):
        root, text = self.dataset_fixture()
        for corrupted in ("\n".join(text.splitlines()[:-1]), text + "\n" + text.splitlines()[0],
                          text.replace("/g4data/dataset-0", "/g4data/../outside")):
            with self.assertRaises(ValueError):
                runtime.dataset_directories(corrupted, root)
        outside = self.root / "unfrozen"
        outside.mkdir()
        (outside / "secret-table").write_text("outside")
        (root / "dataset-0/link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            runtime.hash_datasets(root, runtime.dataset_directories(text, root), self.root / "manifest")

    def test_reference_normalization_and_content_file_set_mismatch(self):
        root, text = self.dataset_fixture()
        actual, reference = self.root / "actual", self.root / "reference"
        runtime.hash_datasets(root, runtime.dataset_directories(text, root), actual)
        rows = actual.read_text().splitlines()
        reference.write_text("\n".join(checksum + "  /g4data/" + name
                                      for checksum, name in (line.split(maxsplit=1) for line in rows)) + "\n")
        runtime.verify_data_manifest(actual, reference)
        reference.write_text("\n".join(rows[:-1]) + "\n")
        with self.assertRaisesRegex(ValueError, "file set mismatch"):
            runtime.verify_data_manifest(actual, reference)
        changed = rows.copy()
        changed[0] = "0" * 64 + "  " + changed[0].split(maxsplit=1)[1]
        reference.write_text("\n".join(changed) + "\n")
        with self.assertRaisesRegex(ValueError, "content differs"):
            runtime.verify_data_manifest(actual, reference)
        with self.assertRaises(FileNotFoundError):
            runtime.verify_data_manifest(actual, self.root / "missing-reference")

    def test_source_requires_exact_committed_head_and_clean_application(self):
        repo = self.root / "source"
        repo.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=Fixture",
                                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                                           "-c", "core.hooksPath=/dev/null", *args], stderr=subprocess.DEVNULL).decode().strip()
        git("init", "-q")
        for name in ("test/OpNovice2/CMakeLists.txt", "test/OpNovice2/OpNovice2.cc",
                     "test/OpNovice2/src/Detector.cc", "tools/layout_study/build_runtime.py"):
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        git("add", ".")
        git("commit", "-qm", "fixture")
        commit = git("rev-parse", "HEAD")
        self.assertEqual(len(runtime.source_snapshot(repo, commit)), 4)
        with self.assertRaises(ValueError):
            runtime.source_snapshot(repo, "0" * 40)
        (repo / "test/OpNovice2/src/Detector.cc").write_text("changed\n")
        with self.assertRaisesRegex(ValueError, "clean"):
            runtime.source_snapshot(repo, commit)
        # Even an assume-unchanged flag must not disguise bytes from a
        # different commit when producing the runtime's source identity.
        git("update-index", "--assume-unchanged", "test/OpNovice2/src/Detector.cc")
        with self.assertRaisesRegex(ValueError, "source bytes differ"):
            runtime.source_snapshot(repo, commit)

    def test_bind_permissions_and_elf_validation(self):
        command = runtime.container_command(self.root / "source", self.root / "output",
                                            self.root / "geant4.sif", self.root / "data", "true")
        self.assertIn(str(self.root / "source") + ":/layout-source:ro", command)
        self.assertIn(str(self.root / "data") + ":/g4data:ro", command)
        self.assertIn(str(self.root / "output") + ":/layout-build", command)
        self.assertIn("--cleanenv", command)
        binary = self.root / "binary"
        header = bytearray(20)
        header[:6] = b"\x7fELF\x02\x01"
        header[18:20] = b">\0"
        binary.write_bytes(header)
        binary.chmod(0o755)
        self.assertEqual(runtime.elf_identity(binary)["architecture"], "x86_64")
        header[18:20] = b"\xb7\0"
        binary.write_bytes(header)
        with self.assertRaisesRegex(ValueError, "x86_64"):
            runtime.elf_identity(binary)
        with self.assertRaises(ValueError):
            runtime.safe_path(self.root / "bad:bind")


if __name__ == "__main__":
    unittest.main()
