"""Runner receipt and real process-tree cleanup tests; no Geant4/container is run."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("layout_run_local", DIRECTORY / "run_local.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
sys.path.pop(0)
EMPTY_HASH = hashlib.sha256(b"").hexdigest()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="layout-runner-test-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        source = self.repo / "test/OpNovice2"
        source.mkdir(parents=True)
        for name in ("OpNovice2.cc", "CMakeLists.txt"):
            (source / name).write_text("test source")
        self.binary = self.repo / "binary"
        self.binary.write_text("not an executable; subprocess is mocked")
        self.output = self.repo / "output"
        self.argv = ["run_local.py", "--executable", str(self.binary), "--output-dir", str(self.output),
                     "--events", "10", "--layout", "back-two", "--layout", "edge-two"]

    def prepare(self, repo, output, *, events, seeds, layouts, thickness):
        output.mkdir()
        for layout in layouts:
            directory = output / layout
            directory.mkdir()
            (directory / "run.mac").write_text("test macro")
            (directory / "config.json").write_text(json.dumps({"layout": layout}))

    def output_command(self, command, **kwargs):
        if "geant4-config" in command:
            return "11.4.2\n"
        if "uname" in command:
            return "x86_64\n"
        return "numpy==2.0.2\nuproot==5.6.9\n"

    def geometry(self, config, log, exit_code):
        if exit_code:
            raise ValueError("process exited " + str(exit_code))
        return {"status": "passed", "log_sha256": EMPTY_HASH}

    def invoke(self, process=None, event_audit=None):
        result = process if process is not None else (0, False)
        with patch.object(runner, "__file__", str(self.repo / "tools/layout_study/run_local.py")), \
             patch.object(sys, "argv", self.argv), \
             patch.object(runner.prepare, "prepare", side_effect=self.prepare), \
             patch.object(runner.subprocess, "check_output", side_effect=self.output_command), \
             patch.object(runner, "run_process", side_effect=[result, result]) as run, \
             patch.object(runner.audit_geometry, "audit_geometry_file", side_effect=self.geometry), \
             patch.object(runner.audit, "audit_file", autospec=True) as events:
            if event_audit:
                events.side_effect = event_audit
            else:
                events.return_value = {"status": "passed", "log_sha256": EMPTY_HASH}
            code = runner.main()
            calls = events.call_args_list
            return code, run.call_count, calls

    def test_passed_layouts_pair_audits_and_keep_real_exit_code(self):
        code, count, calls = self.invoke()
        self.assertEqual((code, count), (0, 2))
        self.assertEqual(json.loads((self.output / "run-summary.json").read_text())["status"], "passed")
        for layout in ("back-two", "edge-two"):
            receipt = json.loads((self.output / layout / "receipt.json").read_text())
            self.assertEqual(receipt["returncode"], 0)
            self.assertFalse(receipt["timed_out"])
            self.assertEqual(receipt["log_sha256"], EMPTY_HASH)
        self.assertEqual(calls[0].kwargs["tile_thickness_mm"], 4)

    def test_failed_simulation_stops_before_next_layout(self):
        code, count, calls = self.invoke((134, False))
        self.assertEqual((code, count), (1, 1))
        self.assertEqual(calls, [])
        receipt = json.loads((self.output / "back-two/receipt.json").read_text())
        self.assertEqual(receipt["returncode"], 134)
        self.assertFalse((self.output / "edge-two/simulation.log").exists())

    def test_timeout_is_failure_with_receipt(self):
        code, count, _ = self.invoke((124, True))
        self.assertEqual((code, count), (1, 1))
        receipt = json.loads((self.output / "back-two/receipt.json").read_text())
        self.assertEqual(receipt["returncode"], 124)
        self.assertTrue(receipt["timed_out"])

    def test_audit_failure_is_not_hidden_by_zero_process_exit(self):
        code, count, _ = self.invoke(event_audit=ValueError("inconsistent count"))
        self.assertEqual((code, count), (1, 1))
        summary = json.loads((self.output / "run-summary.json").read_text())
        self.assertEqual(summary["status"], "failed")
        self.assertIn("inconsistent count", summary["cases"][0]["error"])


class ProcessGroupTests(unittest.TestCase):
    def test_interrupted_wait_cleans_up_the_real_isolated_process(self):
        # Inject Ctrl-C at the wait boundary while retaining a real child. A
        # new session must not leave the simulation running when Python stops.
        original_popen = subprocess.Popen
        children = []

        def start(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            children.append(process)
            real_wait = process.wait
            first = True

            def wait(*args, **kwargs):
                nonlocal first
                if first:
                    first = False
                    raise KeyboardInterrupt
                return real_wait(*args, **kwargs)

            process.wait = wait
            return process

        with tempfile.TemporaryDirectory(prefix="layout-process-interrupt-") as temporary:
            directory = Path(temporary)
            with (directory / "process.log").open("w") as log:
                with patch.object(runner.subprocess, "Popen", side_effect=start), self.assertRaises(KeyboardInterrupt):
                    runner.run_process([sys.executable, "-c", "import time; time.sleep(60)"],
                                       cwd=directory, env=os.environ.copy(), log=log, timeout=5)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode)

    def test_real_parent_and_sigterm_ignoring_child_stop_after_timeout(self):
        with tempfile.TemporaryDirectory(prefix="layout-process-tree-") as temporary:
            directory = Path(temporary)
            child_code = (
                "import os, signal, time\n"
                "from pathlib import Path\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "Path('child.pid').write_text(str(os.getpid()))\n"
                "while True:\n"
                "    with Path('child-writes.txt').open('a') as stream: stream.write('child\\n')\n"
                "    time.sleep(0.02)\n"
            )
            parent_code = (
                "import os, subprocess, sys, time\n"
                "from pathlib import Path\n"
                "Path('parent.pid').write_text(str(os.getpid()))\n"
                "subprocess.Popen([sys.executable, '-c', " + repr(child_code) + "])\n"
                "while True:\n"
                "    with Path('parent-writes.txt').open('a') as stream: stream.write('parent\\n')\n"
                "    time.sleep(0.02)\n"
            )
            try:
                with (directory / "process.log").open("w") as log:
                    result = runner.run_process(
                        [sys.executable, "-c", parent_code], cwd=directory, env=os.environ.copy(),
                        log=log, timeout=0.5, termination_grace_seconds=1)
                self.assertEqual(result, (124, True))
                files = [directory / "parent-writes.txt", directory / "child-writes.txt"]
                before = [path.read_bytes() for path in files]
                self.assertTrue(all(before), "both processes must have started writing")
                time.sleep(0.25)
                self.assertEqual([path.read_bytes() for path in files], before,
                                 "a timed-out descendant is still writing")
                parent_pid = int((directory / "parent.pid").read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(parent_pid, 0)
            finally:
                # Avoid leaking a child even if a regression makes this test fail.
                for name in ("parent.pid", "child.pid"):
                    path = directory / name
                    if path.exists():
                        try:
                            os.kill(int(path.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_nonzero_real_process_return_code_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="layout-process-exit-") as temporary:
            directory = Path(temporary)
            with (directory / "process.log").open("w") as log:
                self.assertEqual(runner.run_process(
                    [sys.executable, "-c", "raise SystemExit(7)"], cwd=directory,
                    env=os.environ.copy(), log=log, timeout=2), (7, False))


if __name__ == "__main__":
    unittest.main()
