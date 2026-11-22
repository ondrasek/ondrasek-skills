"""Every eval harness imports the modules of evals/_common/python and keeps no copy of its own."""
import subprocess
import sys
import unittest
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "python"
EVALS = COMMON.parents[1]
SHARED = ("agent_trace", "runner_cmd")
HARNESSES = sorted(p.parent for p in EVALS.glob("*/python") if p.parent.name != "_common")
# run in a fresh interpreter: import the named modules the way a harness does, then say where the shared modules came from
PROBE = ("import importlib, sys; sys.path.insert(0, sys.argv[1]); [importlib.import_module(n) for n in sys.argv[2:]]; "
         "print('\\n'.join(sys.modules[n].__file__ for n in ('agent_trace', 'runner_cmd') if n in sys.modules))")


def entry_points(unit):
    """(directory, modules to import in order) of every way a harness starts: each script with a __main__ block on its own, and the
    tests' helpers.py followed by the shared modules (a test imports them right after helpers)."""
    scripts = [p for p in sorted((unit / "python").glob("*.py")) if '__name__ == "__main__"' in p.read_text(encoding="utf-8")]
    return [(p.parent, [p.stem]) for p in scripts] + [(unit / "tests", ["helpers", *SHARED])]


class Layout(unittest.TestCase):
    def test_there_are_harnesses_to_look_at(self):
        self.assertIn("oaat", [u.name for u in HARNESSES])
        self.assertGreaterEqual(len(HARNESSES), 6)

    def test_no_harness_keeps_a_copy_of_a_shared_module(self):
        copies = [str(p.relative_to(EVALS)) for u in HARNESSES for n in SHARED for d in ("python", "tests") for p in (u / d).glob(f"{n}.py")]
        self.assertEqual(copies, [], "delete the copy: a harness imports evals/_common/python")

    def test_every_entry_point_gets_the_shared_modules_from_here(self):
        for unit in HARNESSES:
            for directory, modules in entry_points(unit):
                with self.subTest(harness=unit.name, module=modules[0]):
                    done = subprocess.run([sys.executable, "-B", "-c", PROBE, str(directory), *modules], capture_output=True, text=True,
                                          timeout=60, cwd=str(EVALS.parent))
                    self.assertEqual(done.returncode, 0, done.stderr)
                    for found in filter(None, done.stdout.splitlines()):
                        self.assertEqual(Path(found).resolve().parent, COMMON, f"{modules[0]} imported {found}")

    def test_every_harness_that_reads_a_trace_reaches_the_shared_reader(self):
        for unit in HARNESSES:
            done = subprocess.run([sys.executable, "-B", "-c", PROBE, str(unit / "python"), "run_evals"], capture_output=True, text=True, timeout=60)
            self.assertEqual(sorted(Path(f).name for f in filter(None, done.stdout.splitlines())), ["agent_trace.py", "runner_cmd.py"], unit.name + done.stderr)


if __name__ == "__main__":
    unittest.main()
