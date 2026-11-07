"""The entry point: the root Makefile has the standard targets."""
import json
import shutil
import subprocess
import sys
import unittest

from helpers import UNIT
import demo


def make_list(directory=UNIT):
    done = subprocess.run(["make", "-s", "-C", str(directory), "list"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


class PortabilityTests(unittest.TestCase):
    def test_the_windows_command_is_the_demo_that_make_run_calls(self):
        text = (UNIT / "python" / "Makefile").read_text()
        self.assertIn("python3 demo.py\n", text)
        self.assertTrue((UNIT / "python" / "demo.py").is_file())


@unittest.skipUnless(shutil.which("make") and shutil.which("awk"), "needs make and awk")
class MakefileTests(unittest.TestCase):
    def test_the_standard_targets_are_there(self):
        names = make_list()
        for target in ("run", "test", "measure", "grade-fixtures", "eval", "compare", "list"):
            self.assertIn(target, names)

    def test_the_python_makefile_has_list_and_its_targets(self):
        names = make_list(UNIT / "python")
        for target in ("run", "grade-fixtures", "eval", "compare", "test", "measure", "list"):
            self.assertIn(target, names)

    def test_make_run_prints_the_four_sections(self):
        done = subprocess.run(["make", "-s", "-C", str(UNIT), "run"], capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "\n".join(demo.render(s) for s in demo.SECTIONS))

    def test_make_measure_prints_the_cost_of_the_checks(self):
        done = subprocess.run(["make", "-s", "-C", str(UNIT), "measure"], capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("per trace:", done.stdout)

    def test_make_eval_passes_its_arguments_to_the_runner(self):
        done = subprocess.run(["make", "-n", "-C", str(UNIT), "eval", "ARGS=--only explicit-name --trials 1"], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("run_evals.py --only explicit-name --trials 1", done.stdout)

    def test_a_real_run_with_a_missing_agent_stops_with_a_message(self):
        done = subprocess.run([sys.executable, str(UNIT / "python" / "run_evals.py"), "--agent-cmd", "definitely-not-a-program-xyz", "--only", "explicit-name",
                               "--out", str(UNIT / ".cache-test-artifacts")], capture_output=True, text=True, timeout=60)
        self.addCleanup(shutil.rmtree, UNIT / ".cache-test-artifacts", True)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot start the agent command", done.stderr)


if __name__ == "__main__":
    unittest.main()
