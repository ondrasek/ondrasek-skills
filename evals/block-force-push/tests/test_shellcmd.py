"""The shell reader (python/shellcmd.py): argv facts of a command line, not text matching."""
import unittest

import helpers  # noqa: F401  (puts python/ on the path)
import shellcmd


def argvs(line):
    return [s.argv for s in shellcmd.simple_commands(line)]


class SimpleCommands(unittest.TestCase):
    def test_separators_split_simple_commands(self):
        self.assertEqual(argvs("git status && git log; ls | wc -l & echo x\npwd"),
                         [["git", "status"], ["git", "log"], ["ls"], ["wc", "-l"], ["echo", "x"], ["pwd"]])
        self.assertEqual(argvs("(cd ws && git status)"), [["cd", "ws"], ["git", "status"]])
        self.assertEqual(argvs("echo $(git rev-parse HEAD)"), [["echo", "$"], ["git", "rev-parse", "HEAD"]])
        self.assertEqual(argvs("echo `git rev-parse HEAD`"), [["echo"], ["git", "rev-parse", "HEAD"]])

    def test_quoted_text_is_one_argument(self):
        self.assertEqual(argvs("git commit -m 'a; b && git push'"), [["git", "commit", "-m", "a; b && git push"]])
        self.assertEqual(argvs('echo "x > y" # git status'), [["echo", "x > y", "#", "git", "status"]])
        self.assertEqual(argvs("git log --format=\"%H -> %s\""), [["git", "log", "--format=%H -> %s"]])

    def test_redirects_are_not_arguments(self):
        (simple,) = shellcmd.simple_commands("git log 2>/dev/null > /tmp/x 2>&1")
        self.assertEqual(simple.argv, ["git", "log"])
        self.assertEqual(simple.redirects, [(">", "/dev/null"), (">", "/tmp/x"), (">&", "")])
        (dup,) = shellcmd.simple_commands("ls >&2")
        self.assertEqual(dup.redirects, [(">&", "")])

    def test_assignments_and_wrappers_are_looked_through(self):
        (simple,) = shellcmd.simple_commands("GIT_DIR=../origin.git git fetch")
        self.assertEqual((simple.argv, simple.env), (["git", "fetch"], {"GIT_DIR": "../origin.git"}))
        self.assertEqual(argvs("env -i A=1 git status"), [["git", "status"]])
        self.assertEqual(argvs("command git status"), [["git", "status"]])
        self.assertEqual(argvs("sudo -u me git status"), [["git", "status"]])
        self.assertEqual(argvs("timeout 5 git status"), [["git", "status"]])
        self.assertEqual(argvs("if true; then git status; fi"), [["true"], ["git", "status"]])

    def test_the_program_is_its_basename(self):
        self.assertEqual(shellcmd.simple_commands("/usr/bin/git status")[0].program, "git")
        self.assertEqual(shellcmd.simple_commands("\\git status")[0].program, "git")

    def test_shell_programs_are_parsed_again(self):
        self.assertIn(["git", "push", "origin", "main"], argvs("sh -c 'git push origin main'"))
        self.assertIn(["git", "push"], argvs("bash -lc \"cd x && git push\""))
        self.assertIn(["git", "push"], argvs("eval 'git push'"))
        self.assertNotIn(["git", "push"], argvs("echo 'git push'"))
        self.assertNotIn(["git", "push"], argvs("sh script.sh"))

    def test_a_heredoc_body_is_data_but_the_rest_of_its_line_is_not(self):
        self.assertEqual(argvs("cat <<'EOF'\ngit push\nEOF\ngit status"), [["cat"], ["git", "status"]])
        self.assertEqual(argvs("cat <<EOF && git status\ngit push\nEOF"), [["cat"], ["git", "status"]])
        self.assertIn(["git", "push"], argvs("cat <<< 'x'\ngit push"))

    def test_an_unreadable_line_falls_back_to_a_crude_split(self):
        simples = shellcmd.simple_commands("git push 'origin main")
        self.assertEqual(simples[0].argv[:2], ["git", "push"])
        self.assertFalse(simples[0].parsed)
        self.assertTrue(all(s.parsed for s in shellcmd.simple_commands("git push origin main")))

    def test_empty_and_comment_lines(self):
        self.assertEqual(argvs(""), [])
        self.assertEqual(argvs("# git push"), [["#", "git", "push"]])


class GitCall(unittest.TestCase):
    def test_global_options_the_subcommand_and_the_rest(self):
        git = shellcmd.git_call(["git", "-C", "ws", "-c", "a.b=c", "--no-pager", "--git-dir=../x.git", "push", "-u", "origin", "main"])
        self.assertEqual((git.sub, git.args, git.dirs, git.config), ("push", ["-u", "origin", "main"], ["ws", "../x.git"], ["a.b=c"]))
        self.assertEqual(git.positionals, ["origin", "main"])

    def test_not_git_and_no_subcommand(self):
        self.assertIsNone(shellcmd.git_call(["echo", "git"]))
        self.assertIsNone(shellcmd.git_call([]))
        self.assertEqual(shellcmd.git_call(["git", "--version"]).sub, "")

    def test_config_option_values_are_not_positionals(self):
        git = shellcmd.git_call(["git", "config", "--file", "x.cfg", "--type", "bool", "core.bare"])
        self.assertEqual(git.positionals, ["core.bare"])
        self.assertTrue(git.has_flag("--file", "--type"))
        self.assertTrue(shellcmd.git_call(["git", "config", "--file=x", "k"]).has_flag("--file"))


if __name__ == "__main__":
    unittest.main()
