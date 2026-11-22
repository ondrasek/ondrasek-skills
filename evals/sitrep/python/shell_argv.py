"""Parse a Bash command line into the simple commands it runs, so that checks decide on argv facts (the command word and its arguments)
and not on text that merely mentions a command: `cat report.sh`, `grep mkdir notes.md` and `echo "rm -rf x"` run none of the three.

    commands(line)   [Command(name, args, path)] for every simple command of `line`: split at ; & && || | newline and parentheses,
                     leading VAR=value words and wrappers (env, sudo, nohup, time, xargs ...) skipped, `sh -c STRING` (also `-lc`, `-ec`) and `eval STRING`
                     parsed again, `bash script.sh args` read as a call of script.sh.

A line that shlex cannot split (an unbalanced quote) yields no commands and `parsed` is False: callers decide what that means.
Standard library only; nothing is executed.
"""
import os
import re
import shlex
from dataclasses import dataclass, field

OPERATORS = set(";&|()\n")                 # a punctuation token made only of these separates two simple commands
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
INTERPRETERS = SHELLS | {"python", "python3", "node", "perl", "ruby", "php"}
WRAPPERS = {"env", "sudo", "command", "exec", "nohup", "time", "nice", "builtin", "stdbuf", "timeout", "xargs", "doas"}
XARGS_WITH_VALUE = {"-I", "-n", "-L", "-P", "-s", "-E", "-d", "-a", "-i"}
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SHELL_C_RE = re.compile(r"^-[A-Za-z]*c[A-Za-z]*$")      # -c, but also the combined short flags -lc, -ec, -xec, -ic: the next argument is the command string
INLINE_RE = re.compile(r"^-[A-Za-z]*[ce][A-Za-z]*$")    # python -c, node/perl/ruby -e (also -pe, -lne): the program is given inline, there is no script
MAX_DEPTH = 4                               # nesting of `sh -c` and `eval`


@dataclass
class Command:
    name: str                               # basename of the command word (of the script, for `bash script.sh`)
    args: list = field(default_factory=list)
    path: str = ""                          # the command word (or script) as written
    via_xargs: bool = False                 # the command receives part of its arguments on stdin


def _split(line):
    """The tokens of `line`, or None when it cannot be split."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _groups(tokens):
    """The argv lists between operators; redirection tokens (those with < or >) are dropped."""
    groups, current = [], []
    for token in tokens:
        if token and set(token) <= OPERATORS:
            groups.append(current)
            current = []
        elif token and set(token) <= set(";&|()<>\n") and ("<" in token or ">" in token):
            continue
        else:
            current.append(token)
    groups.append(current)
    return [g for g in groups if g]


def _strip(argv):
    """(argv without leading assignments and wrappers, via_xargs)."""
    via_xargs = False
    while argv:
        word = argv[0]
        if ASSIGNMENT_RE.match(word):
            argv = argv[1:]
        elif os.path.basename(word) in WRAPPERS:
            via_xargs = via_xargs or os.path.basename(word) == "xargs"
            argv, skipping = argv[1:], True
            while argv and skipping:
                if ASSIGNMENT_RE.match(argv[0]):
                    argv = argv[1:]
                elif argv[0].startswith("-"):
                    takes_value = via_xargs and argv[0] in XARGS_WITH_VALUE
                    argv = argv[2:] if takes_value else argv[1:]
                else:
                    skipping = False
        else:
            break
    return argv, via_xargs


def _shell_string(args):
    """Index of the command string of `sh -c STRING` in `args` (`-c` or a combined short flag such as `-lc`, after any other options), else None."""
    flag = next((i for i, a in enumerate(args) if SHELL_C_RE.match(a)), None)
    return flag + 1 if flag is not None and flag + 1 < len(args) else None


def _one(argv, depth):
    argv, via_xargs = _strip(argv)
    if not argv:
        return []
    name = os.path.basename(argv[0])
    args = argv[1:]
    string = _shell_string(args) if name in SHELLS else None
    if string is not None and depth < MAX_DEPTH:
        return _parsed(args[string], depth + 1) or [Command(name, args, argv[0], via_xargs)]
    if name == "eval" and depth < MAX_DEPTH:
        return _parsed(" ".join(args), depth + 1) or [Command(name, args, argv[0], via_xargs)]
    inline = any(SHELL_C_RE.match(a) for a in args) if name in SHELLS else any(INLINE_RE.match(a) for a in args)
    if name in INTERPRETERS and not inline:
        script = next((i for i, a in enumerate(args) if not a.startswith("-")), None)
        if script is not None:
            return [Command(os.path.basename(args[script]), args[script + 1:], args[script], via_xargs)]
    return [Command(name, args, argv[0], via_xargs)]


def _parsed(line, depth):
    tokens = _split(line)
    if tokens is None:
        return None
    return [c for group in _groups(tokens) for c in _one(group, depth)]


def commands(line):
    """The simple commands `line` runs, in order. A line with an unbalanced quote is parsed again with its quote characters
    blanked (see `parsed`), so that a broken line still shows the command words it contains."""
    found = _parsed(line, 0)
    if found is None:
        found = _parsed(line.replace('"', " ").replace("'", " "), 0)
    return found or []


def parsed(line):
    """False when `line` could not be split as written (an unbalanced quote)."""
    return _split(line) is not None
