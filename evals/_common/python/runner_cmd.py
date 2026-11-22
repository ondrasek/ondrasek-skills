"""Run an external command (the agent or the judge) that is given as one string.

The prompt reaches the command as one argument: in place of the token `{prompt}` when the command holds it, else as the last
argument. (`--allowedTools` takes a list, so a command that uses it must put `{prompt}` before that flag.)
"""
import os
import shlex
import subprocess


def split(command):
    """A command string as an argument list; Windows paths keep their backslashes."""
    if os.name == "nt":
        return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in shlex.split(command, posix=False)]
    return shlex.split(command)


def build(command, prompt, extra=()):
    """argv for `command` with the prompt in place; `extra` arguments are added after the command's own."""
    tokens = split(command)
    if not tokens:
        raise ValueError("the command is empty")
    if "{prompt}" in tokens:
        argv = [prompt if token == "{prompt}" else token for token in tokens]
        return argv + list(extra)
    return tokens + list(extra) + [prompt]


def run(argv, timeout=300, env=None, cwd=None):
    """(exit code, stdout, stderr). A missing program raises FileNotFoundError; a timeout returns exit code 124."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                              env={**os.environ, **(env or {})}, cwd=cwd, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as expired:
        out = expired.stdout.decode("utf-8", "replace") if isinstance(expired.stdout, bytes) else (expired.stdout or "")
        return 124, out, f"timed out after {timeout} s"
    return done.returncode, done.stdout, done.stderr
