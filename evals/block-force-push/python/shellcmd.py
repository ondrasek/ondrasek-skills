"""A small, conservative reader of the shell command lines an agent runs through its Bash tool: argv facts, not text matching.

`simple_commands(line)` splits a command line into its simple commands (on ; && || | & newline and parentheses, command
substitution and backticks included), and gives for each the program's argv, the leading VAR=value assignments and the redirects
(operator, target). Quoting is honoured (shlex), so a `>` or the words `git push` inside a quoted argument are data, not syntax:
`echo 'git push is blocked'` is an echo and `git log --format="%H -> %s"` redirects nothing. Wrappers are looked through (env,
command, exec, sudo, nohup, time, xargs ...), and the program text of `sh -c '...'`, `bash -c`, `eval` is parsed again.

`git_call(argv)` splits a git argv into its global options (-C, --git-dir, --work-tree, -c key=value), the subcommand and the rest.

Limits, by design: no variable or command-substitution expansion (`$(echo git) push` is not seen), no alias expansion beyond what
the command line itself spells out, no scripts the agent wrote to a file and then runs. A line shlex cannot read (unbalanced
quote) falls back to a crude split on separators and whitespace (`parsed` is then False).
"""
import os
import re
import shlex
from dataclasses import dataclass, field

SEPARATOR_CHARS = ";&|()\n"
REDIRECT_CHARS = "<>"
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# the body of a here-document (up to its delimiter line, or the end) is data; the rest of the line that opened it stays a command
HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1(?P<rest>[^\n]*)\n.*?(?:\n[ \t]*\2[ \t]*(?=\n|$)|\Z)", re.DOTALL)
WRAPPERS = {"env", "command", "exec", "time", "nohup", "sudo", "nice", "builtin", "xargs", "timeout", "setsid", "stdbuf", "doas"}
KEYWORDS = {"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until", "fi", "done"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
MAX_DEPTH = 4


@dataclass
class Simple:
    argv: list                                   # program and arguments, wrappers and assignments removed
    env: dict = field(default_factory=dict)      # leading VAR=value assignments (and those of a wrapping `env`)
    redirects: list = field(default_factory=list)  # [(operator, target)], e.g. (">", "/tmp/x")
    parsed: bool = True

    @property
    def program(self):
        return os.path.basename(self.argv[0]) if self.argv else ""


def _tokens(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars=SEPARATOR_CHARS + REDIRECT_CHARS)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _is_separator(token):
    return bool(token) and all(c in SEPARATOR_CHARS for c in token)


def _is_redirect(token):
    return bool(token) and all(c in SEPARATOR_CHARS + REDIRECT_CHARS for c in token) and any(c in REDIRECT_CHARS for c in token)


def _words_to_simple(words, parsed=True):
    """argv, env and redirects from the words of one simple command (redirect operators are separate words)."""
    argv, redirects, i = [], [], 0
    while i < len(words):
        word = words[i]
        if _is_redirect(word):
            if argv and re.fullmatch(r"\d+", argv[-1]):          # the fd number of `2>file`
                argv.pop()
            target = words[i + 1] if i + 1 < len(words) else ""
            if "&" in word and re.fullmatch(r"\d+|-", target):   # `>&2`: a descriptor, not a file
                target = ""
            redirects.append((word, target))
            i += 2
            continue
        argv.append(word)
        i += 1
    env = {}
    while argv and ENV_RE.match(argv[0]):
        key, _, value = argv.pop(0).partition("=")
        env[key] = value
    while argv and (argv[0] in KEYWORDS):
        argv.pop(0)
    while argv and os.path.basename(argv[0]) in WRAPPERS:
        wrapper = os.path.basename(argv.pop(0))
        while argv and (argv[0].startswith("-") or ENV_RE.match(argv[0]) or re.fullmatch(r"\d+[smhd]?", argv[0])):
            word = argv.pop(0)
            if wrapper in ("sudo", "doas") and word in ("-u", "-g", "-h", "-p", "-C") and argv:   # options that take a value
                argv.pop(0)
            if ENV_RE.match(word):
                key, _, value = word.partition("=")
                env[key] = value
    return Simple(argv, env, redirects, parsed)


def _crude_words(segment):
    """Fallback when shlex cannot read the line: whitespace words, redirect operators split off."""
    words = []
    for word in segment.split():
        m = re.match(r"^(\d*)(>>?|<)(.*)$", word)
        if m:
            words += [m.group(2)] + ([m.group(3)] if m.group(3) else [])
        else:
            words.append(word)
    return words


def simple_commands(line, depth=0):
    """[Simple] of a command line, in order, nested `sh -c` and `eval` programs included right after their own command."""
    line = HEREDOC_RE.sub(lambda m: m.group("rest"), line.replace("`", ";"))
    try:
        tokens, parsed = _tokens(line), True
        segments, current = [], []
        for token in tokens:
            if _is_separator(token):
                segments.append(current)
                current = []
            else:
                current.append(token)
        segments.append(current)
    except ValueError:
        parsed = False
        segments = [_crude_words(s) for s in re.split(r"[;&|()\n]+", line)]
    commands = []
    for words in segments:
        simple = _words_to_simple(words, parsed)
        if not (simple.argv or simple.env or simple.redirects):
            continue
        commands.append(simple)
        if depth < MAX_DEPTH and simple.argv:
            commands += _nested(simple, depth)
    return commands


def _nested(simple, depth):
    """The program text handed to a shell (`bash -c '...'`) or to `eval`."""
    program, args = simple.program, simple.argv[1:]
    if program == "eval" and args:
        return simple_commands(" ".join(args), depth + 1)
    if program in SHELLS:
        for i, arg in enumerate(args):
            if arg.startswith("-") and not arg.startswith("--") and "c" in arg and i + 1 < len(args):
                return simple_commands(args[i + 1], depth + 1)
    return []


# ---- git ------------------------------------------------------------------------------------------------------

@dataclass
class Git:
    sub: str                                   # the subcommand, "" when there is none
    args: list                                 # everything after it
    dirs: list = field(default_factory=list)   # values of -C, --git-dir and --work-tree
    config: list = field(default_factory=list)  # values of -c, "key=value"

    @property
    def positionals(self):
        """The arguments that are not options (an option's value is not skipped, except those of the options of `git config`)."""
        out, skip = [], False
        for arg in self.args:
            if skip:
                skip = False
            elif arg in ("-f", "--file", "--blob", "--type", "-t", "--default", "--comment"):
                skip = True
            elif not arg.startswith("-"):
                out.append(arg)
        return out

    def has_flag(self, *flags):
        return any(a in flags or any(a.startswith(f + "=") for f in flags if f.startswith("--")) for a in self.args)


def git_call(argv):
    """A Git for an argv whose program is git, else None."""
    if not argv or os.path.basename(argv[0]) != "git":
        return None
    dirs, config, i = [], [], 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("-C", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix") and i + 1 < len(argv):
            dirs.append(argv[i + 1])
            i += 2
        elif arg == "-c" and i + 1 < len(argv):
            config.append(argv[i + 1])
            i += 2
        elif re.match(r"^--(git-dir|work-tree)=", arg):
            dirs.append(arg.split("=", 1)[1])
            i += 1
        elif arg.startswith("-C") and len(arg) > 2:
            dirs.append(arg[2:])
            i += 1
        elif arg.startswith("-"):
            i += 1
        else:
            return Git(arg, argv[i + 1:], dirs, config)
    return Git("", [], dirs, config)
