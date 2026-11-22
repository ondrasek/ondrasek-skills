"""The command-line reader the process checks rest on: simple commands by argv, never by text that merely mentions a command."""
import unittest

import helpers  # noqa: F401  (puts python/ on the path: first)
import shell_argv


def names(line):
    return [c.name for c in shell_argv.commands(line)]


class Commands(unittest.TestCase):
    def test_operators_and_newlines_separate_commands(self):
        self.assertEqual(names("a;b && c || d | e & f\ng"), list("abcdefg"))
        self.assertEqual(names("true;rm /x"), ["true", "rm"])
        self.assertEqual(names("(cd /x; ls)"), ["cd", "ls"])
        self.assertIn("rm", names("echo $(rm y)"))

    def test_the_command_word_is_its_basename_and_the_arguments_follow(self):
        command, = shell_argv.commands("/usr/bin/rm -rf /x")
        self.assertEqual((command.name, command.args, command.path), ("rm", ["-rf", "/x"], "/usr/bin/rm"))

    def test_quoted_text_is_one_argument(self):
        command, = shell_argv.commands('echo "rm -rf /x; mkdir y"')
        self.assertEqual((command.name, command.args), ("echo", ["rm -rf /x; mkdir y"]))

    def test_assignments_and_wrappers_are_skipped(self):
        for line in ("X=1 rm a", "env -i X=1 rm a", "/usr/bin/env rm a", "sudo -n rm a", "nohup rm a", "time rm a", "command rm a"):
            self.assertEqual(names(line), ["rm"], line)

    def test_xargs_marks_its_command_and_skips_its_options(self):
        found = shell_argv.commands("find . | xargs -I {} rm -rf {}")
        self.assertEqual([(c.name, c.via_xargs) for c in found], [("find", False), ("rm", True)])

    def test_shell_strings_are_read_again(self):
        self.assertEqual(names("bash -c 'cd a; rm -r b'"), ["cd", "rm"])
        self.assertEqual(names("sh -c \"bash -c 'rm x'\""), ["rm"])
        self.assertEqual(names("eval rm x"), ["rm"])

    def test_a_script_run_by_an_interpreter_is_that_script(self):
        command, = shell_argv.commands("bash /p/report.sh new")
        self.assertEqual((command.name, command.args, command.path), ("report.sh", ["new"], "/p/report.sh"))
        self.assertEqual(names("python3 -c 'print(1)'"), ["python3"])

    def test_redirections_are_not_commands_or_arguments_of_the_operator(self):
        self.assertEqual(names("cat x > y 2>&1"), ["cat"])
        self.assertEqual(names("cat < x | wc -l"), ["cat", "wc"])

    def test_an_unbalanced_quote_still_shows_its_command_words(self):
        self.assertFalse(shell_argv.parsed('echo "oops'))
        self.assertEqual(names('rm -rf "/r/tmp/sitrep.x'), ["rm"])
        self.assertTrue(shell_argv.parsed("rm x"))

    def test_nothing_is_not_a_command(self):
        for line in ("", "   ", ";", "X=1"):
            self.assertEqual(shell_argv.commands(line), [], repr(line))


if __name__ == "__main__":
    unittest.main()
