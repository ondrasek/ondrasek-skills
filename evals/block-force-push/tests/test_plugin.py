"""The harness against the plugin and against the sibling eval directories it was copied from."""
import ast
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import helpers
import checks
import run_evals

UNIT = Path(__file__).resolve().parents[1]
REPO = UNIT.parents[1]
HOOK = REPO / "plugins" / "block-force-push" / "hooks" / "block-force-push.sh"
UNIT_TEST = REPO / "tests" / "block-force-push" / "test_block_force_push.py"
REFERENCE = REPO / "evals" / "agent-md-audit" / "python"
# commands the unit tests of the hook do not list: a plus-refspec (a spelling the hook does not cover), other pushes, several lines
EXTRA = ["git push origin +main", "git push --mirror", "git push origin --delete main", "git -C ws push -f", "git push origin main\ngit log -f",
         "git status\ngit push\nls -f", "git push origin main && echo --force", "git push\n\ngit status", "echo git push -f", "git pushx -f"]


def listed(name):
    """A module-level list of strings of the hook's unit tests, read with ast (importing it would write a __pycache__ there)."""
    for node in ast.parse(UNIT_TEST.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and [t.id for t in node.targets] == [name]:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{UNIT_TEST} has no list {name}")


def hook_denies(command):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    done = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True, timeout=30)
    return done.returncode == 2


class Copies(unittest.TestCase):
    @unittest.skipUnless(REFERENCE.is_dir(), "evals/agent-md-audit is not here")
    def test_the_shared_modules_are_byte_identical_to_the_reference(self):
        for name in ("agent_trace.py", "runner_cmd.py"):
            self.assertEqual((UNIT / "python" / name).read_bytes(), (REFERENCE / name).read_bytes(), name)


class RegexPort(unittest.TestCase):
    def test_the_port_agrees_with_the_hooks_own_lists(self):
        blocked, allowed = listed("BLOCKED"), listed("ALLOWED")
        self.assertTrue(blocked and allowed)
        for command in blocked:
            self.assertTrue(checks.hook_blocks(command), command)
        for command in allowed:
            self.assertFalse(checks.hook_blocks(command), command)

    @unittest.skipUnless(shutil.which("jq"), "the hook needs jq")
    def test_the_port_agrees_with_the_hook_script_itself(self):
        for command in listed("BLOCKED") + listed("ALLOWED") + EXTRA:
            with self.subTest(command=command):
                self.assertEqual(checks.hook_blocks(command), hook_denies(command))

    def test_the_hook_does_not_cover_a_plus_refspec_so_the_checks_look_at_the_remote(self):
        self.assertFalse(checks.hook_blocks("git push origin +main"))
        self.assertTrue(checks.is_workaround("git push origin +main", False))


class Message(unittest.TestCase):
    def test_the_block_message_the_checks_and_traces_use_is_the_hooks(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertIn(checks.BLOCK_MESSAGE, source)
        self.assertIn(helpers.BLOCK, source)

    def test_the_agent_may_run_git_and_the_hook_matches_bash(self):
        self.assertIn("Bash(git *)", run_evals.ALLOWED)
        groups = json.loads((HOOK.parent / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        self.assertEqual([g["matcher"] for g in groups], ["Bash"])


if __name__ == "__main__":
    unittest.main()
