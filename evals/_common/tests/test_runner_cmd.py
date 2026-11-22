"""How a command string becomes an argument list, how the prompt reaches the command, and how the command is run."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

import runner_cmd  # noqa: E402

# a fake agent: prints its arguments one per line, then the value of FAKE_ENV and its working directory
FAKE_AGENT = "import os, sys\nprint('\\n'.join(sys.argv[1:]))\nprint(os.environ.get('FAKE_ENV', ''))\nprint(os.getcwd())\n"


class BuildTests(unittest.TestCase):
    def test_the_prompt_is_the_last_argument_by_default(self):
        self.assertEqual(runner_cmd.build("claude -p --verbose", "hello"), ["claude", "-p", "--verbose", "hello"])

    def test_a_placeholder_puts_the_prompt_in_the_middle(self):
        self.assertEqual(runner_cmd.build("claude -p {prompt} --allowedTools Skill", "hi"), ["claude", "-p", "hi", "--allowedTools", "Skill"])

    def test_extra_arguments_come_after_the_command_and_before_a_trailing_prompt(self):
        self.assertEqual(runner_cmd.build("claude -p", "hi", ["--plugin-dir", "/p"]), ["claude", "-p", "--plugin-dir", "/p", "hi"])
        self.assertEqual(runner_cmd.build("claude -p {prompt} --x", "hi", ["--plugin-dir", "/p"]), ["claude", "-p", "hi", "--x", "--plugin-dir", "/p"])

    def test_a_prompt_with_newlines_and_quotes_stays_one_argument(self):
        prompt = "line one\nline \"two\" and 'three'"
        self.assertEqual(runner_cmd.build("agent {prompt}", prompt)[-1], prompt)

    def test_an_empty_command_is_refused(self):
        with self.assertRaises(ValueError):
            runner_cmd.build("  ", "x")

    def test_split_keeps_a_quoted_argument_together(self):
        self.assertEqual(runner_cmd.split('agent --name "two words" plain'), ["agent", "--name", "two words", "plain"])


class RunTests(unittest.TestCase):
    def test_run_returns_code_output_and_error(self):
        code, out, err = runner_cmd.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(4)"])
        self.assertEqual((code, out.strip(), err.strip()), (4, "out", "err"))

    def test_a_built_command_reaches_a_fake_agent_with_the_prompt_env_and_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = Path(tmp) / "fake_agent.py"
            agent.write_text(FAKE_AGENT, encoding="utf-8")
            argv = runner_cmd.build(f'"{sys.executable}" "{agent}" -p', "two\nlines", ["--extra"])
            code, out, _ = runner_cmd.run(argv, env={"FAKE_ENV": "set"}, cwd=tmp)
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[:4], ["-p", "--extra", "two", "lines"])
        self.assertEqual(lines[4], "set")
        self.assertEqual(Path(lines[5]).resolve(), Path(tmp).resolve())

    def test_a_timeout_returns_124(self):
        code, _, err = runner_cmd.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertEqual(code, 124)
        self.assertIn("timed out", err)

    def test_a_missing_program_raises(self):
        with self.assertRaises(FileNotFoundError):
            runner_cmd.run(["definitely-not-a-program-xyz"])


if __name__ == "__main__":
    unittest.main()
