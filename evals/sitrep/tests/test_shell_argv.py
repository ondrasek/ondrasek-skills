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

    def test_combined_short_flags_that_include_c_give_the_command_string(self):
        """`bash -lc 'mkdir ...'` runs the string like `-c` does (round 4): -lc, -ec, -xec, -ic, flags before the -c, zsh and sh."""
        for line in ("bash -lc 'mkdir -p /r/tmp/x'", "bash -ec 'mkdir -p /r/tmp/x'", "sh -xec 'mkdir -p /r/tmp/x'", "zsh -ic 'mkdir -p /r/tmp/x'",
                     "bash -c 'mkdir -p /r/tmp/x'", "bash -o pipefail -c 'mkdir -p /r/tmp/x'", "bash --login -c 'mkdir -p /r/tmp/x'",
                     "env X=1 bash -lc 'mkdir -p /r/tmp/x'"):
            command, = shell_argv.commands(line)
            self.assertEqual((command.name, command.args), ("mkdir", ["-p", "/r/tmp/x"]), line)
        self.assertEqual(names("bash -lc 'cd a; rm -r b'"), ["cd", "rm"])
        self.assertEqual(names("sh -c \"bash -lc 'rm x'\""), ["rm"])

    def test_a_shell_flag_cluster_without_c_is_not_a_command_string(self):
        command, = shell_argv.commands("bash -e /p/report.sh new")
        self.assertEqual((command.name, command.args), ("report.sh", ["new"]))
        command, = shell_argv.commands("bash -l /p/report.sh new")
        self.assertEqual(command.name, "report.sh")
        self.assertEqual(names("bash -lc"), ["bash"])          # no string to read: the shell itself

    def test_inline_programs_of_other_interpreters_are_not_scripts(self):
        for line in ("python3 -c 'print(1)'", "perl -pe 's/a/b/' f", "node -e 'x'", "perl -lne 'print' f"):
            self.assertEqual(len(shell_argv.commands(line)), 1, line)
            self.assertEqual(names(line)[0], line.split()[0], line)

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
