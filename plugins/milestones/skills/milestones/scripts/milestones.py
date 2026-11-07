#!/usr/bin/env python3
"""Milestone tracker for a Claude Code session (the `milestones` plugin of ondrasek-skills).

The data is `.cache/milestones.json` in the project (per checkout, gitignored), written by
the agent session through this script and read by the statusline (`statusline` next to this
file) and the /milestones skill. Schema: ../schema/milestones.schema.json; `validate()` below
checks the same rules without a library, because the statusline runs this script on every
refresh: stdlib only, no import beyond it, and `statusline` never raises and never fails.

    milestones.py statusline [--parts P,…]   one segment; reads Claude Code's statusline JSON on stdin
    milestones.py show [--json]              the table, the bar line, durations
    milestones.py set ID --state S [--name …] [--left …] [--needs …]
    milestones.py add --name … [--state S] [--left …] [--needs …]
    milestones.py remove ID | title TEXT | init [--force]

Environment: MILESTONES_FILE (another file), MILESTONES_NOW (ISO time; tests),
MILESTONES_AUTHORS (the directory of author profiles, see `author_forms`),
MILESTONES_STATUSLINE_PARTS (the statusline parts when --parts is absent), NO_COLOR
(https://no-color.org: set and non-empty, no colour) and MILESTONES_STATUSLINE_COLOR=0 (no colour).
The older names WORKER_STATUSLINE_PARTS and WORKER_STATUSLINE_COLOR still work.
The statusline is coloured with ANSI SGR codes (Claude Code renders them); `show` colours its
table only when stdout is a terminal.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

STATES = ("done", "doing", "todo", "blocked")
ICONS = {"done": "✅", "doing": "🟡", "todo": "⬜", "blocked": "⛔"}
ITEM_KEYS = {"id", "name", "state", "left", "needs", "started", "finished"}
TOP_KEYS = {"title", "updated", "items"}
BAR_CELLS = 6
NEXT_WIDTH = 28
# Statusline parts, in print order. milestones = title, bar, count, next, needs marker;
# timing = the current milestone's elapsed time; ctx, cost, time, limits come from the
# statusline JSON Claude Code sends on stdin (https://code.claude.com/docs/en/statusline#available-data).
# model = the model's display name, the session's effort level and the configured advisor.
PARTS = ("milestones", "timing", "ctx", "cost", "time", "limits", "model")


def statusline_env(name: str) -> str | None:
    """$MILESTONES_STATUSLINE_<NAME>, else the older $WORKER_STATUSLINE_<NAME>; None when neither is set."""
    for prefix in ("MILESTONES_STATUSLINE_", "WORKER_STATUSLINE_"):
        if prefix + name in os.environ:
            return os.environ[prefix + name]
    return None


def cwd_project() -> Path:
    """The project a command run is for: $CLAUDE_PROJECT_DIR, else the nearest directory at or
    above the working directory that holds `.git` (a checkout or a worktree), else the working
    directory. The script lives in the plugin, so it never falls back to its own location."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    try:
        here = Path.cwd().resolve()
    except OSError:
        return Path(".")
    for d in (here, *here.parents):
        if (d / ".git").exists():
            return d
    return here


def data_file(project: Path | None = None) -> Path:
    """MILESTONES_FILE, else `.cache/milestones.json` of PROJECT (the statusline's project),
    else of the command's project (cwd_project)."""
    return Path(os.environ.get("MILESTONES_FILE")
                or (project or cwd_project()) / ".cache" / "milestones.json")


def session_project(session: dict | None) -> Path:
    """The project a statusline run is for: the statusline JSON's workspace.project_dir, else
    $CLAUDE_PROJECT_DIR, else the working directory. A user-level statusline thus shows each project's
    own tracker, and none where a project has no `.cache/milestones.json`."""
    return Path(_text(session, "workspace", "project_dir")
                or os.environ.get("CLAUDE_PROJECT_DIR") or str(Path.cwd()))


def now() -> datetime:
    fixed = os.environ.get("MILESTONES_NOW")
    if fixed:
        return parse_time(fixed)
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


# ---------------------------------------------------------------- validation

def validate(data: object) -> list[str]:
    """The rules of milestones.schema.json, plus unique ids; returns the violations."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["the file is a JSON object"]
    for k in set(data) - TOP_KEYS:
        errs.append(f"unknown key {k!r}")
    if not isinstance(data.get("title"), str) or not data.get("title"):
        errs.append("title is a non-empty string")
    if "updated" in data and not isinstance(data["updated"], str):
        errs.append("updated is a string")
    items = data.get("items")
    if not isinstance(items, list):
        return errs + ["items is a list"]
    seen: set[int] = set()
    for n, it in enumerate(items):
        where = f"items[{n}]"
        if not isinstance(it, dict):
            errs.append(f"{where} is an object")
            continue
        for k in set(it) - ITEM_KEYS:
            errs.append(f"{where}: unknown key {k!r}")
        i = it.get("id")
        if not isinstance(i, int) or isinstance(i, bool) or i < 1:
            errs.append(f"{where}: id is an integer ≥ 1")
        elif i in seen:
            errs.append(f"{where}: id {i} is used twice")
        else:
            seen.add(i)
        if not isinstance(it.get("name"), str) or not it.get("name"):
            errs.append(f"{where}: name is a non-empty string")
        if it.get("state") not in STATES:
            errs.append(f"{where}: state is one of {', '.join(STATES)}")
        for k in ("left", "needs", "started", "finished"):
            if k in it and not isinstance(it[k], str):
                errs.append(f"{where}: {k} is a string")
        for k in ("started", "finished"):
            if isinstance(it.get(k), str):
                try:
                    parse_time(it[k])
                except ValueError:
                    errs.append(f"{where}: {k} is an ISO 8601 time")
    return errs


DEFAULT_TITLE = "Milestones"


def load_or_new(path: Path) -> dict:
    """The tracker, or an empty one (no items, DEFAULT_TITLE) when the project has none yet:
    `add` and `title` start a tracker from nothing (user-level use in any project)."""
    if not path.exists():
        return {"title": DEFAULT_TITLE, "items": []}
    return load(path)


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    errs = validate(data)
    if errs:
        raise ValueError("; ".join(errs))
    return data


def save(path: Path, data: dict) -> None:
    """Validate, then write atomically: a temporary file in the same directory, os.replace."""
    data["updated"] = iso(now())
    errs = validate(data)
    if errs:
        raise ValueError("; ".join(errs))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".milestones-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- colour and width

SGR = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36",
       "bold-red": "1;31", "bold-yellow": "1;33"}
RESET = "\x1b[0m"
ESC_RE = re.compile(r"\x1b\[[0-9;]*m")
STATE_COLOR = {"done": "green", "doing": "yellow", "todo": "dim", "blocked": "bold-red"}


def color_wanted() -> bool:
    """NO_COLOR set and non-empty (https://no-color.org) or MILESTONES_STATUSLINE_COLOR=0: none."""
    if os.environ.get("NO_COLOR"):
        return False
    return (statusline_env("COLOR") or "").strip() != "0"


def paint(text: str, style: str, on: bool) -> str:
    if not on or not text or style not in SGR:
        return text
    return f"\x1b[{SGR[style]}m{text}{RESET}"


def pct_style(pct: float) -> str:
    """Usage thresholds: green below 50 %, yellow below 80 %, red from 80 %."""
    return "green" if pct < 50 else "yellow" if pct < 80 else "red"


def char_width(ch: str) -> int:
    if unicodedata.combining(ch) or ch in "‍︎️":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def visible_width(text: str) -> int:
    """Terminal columns of `text`: escape codes count 0, wide characters (emoji) 2."""
    return sum(char_width(c) for c in ESC_RE.sub("", text))


# ---------------------------------------------------------------- rendering

def progress(items: list[dict]) -> float:
    if not items:
        return 0.0
    score = sum(1.0 if i["state"] == "done" else 0.5 if i["state"] == "doing" else 0.0
                for i in items)
    return score / len(items)


def bar(frac: float, cells: int = BAR_CELLS) -> str:
    """done counts full, doing half; resolution half a cell (█ full, ▌ half, ░ empty)."""
    halves = int(max(0.0, min(1.0, frac)) * cells * 2 + 0.5)  # half rounds up
    full, half = divmod(halves, 2)
    return "█" * full + "▌" * half + "░" * (cells - full - half)


def truncate(text: str, width: int) -> str:
    """At most `width` visible columns, the last one "…" when cut; escape codes count 0 and
    are kept, and a cut painted text ends with a reset."""
    if visible_width(text) <= width:
        return text
    out: list[str] = []
    used, pos, painted = 0, 0, False
    room = max(0, width - 1)
    while pos < len(text):
        m = ESC_RE.match(text, pos)
        if m:
            out.append(m.group())
            painted = True
            pos = m.end()
            continue
        w = char_width(text[pos])
        if used + w > room:
            break
        out.append(text[pos])
        used += w
        pos += 1
    cut = "".join(out).rstrip() + "…"
    return cut + RESET if painted else cut


def duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "<1m"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m" if m else f"{h}h"
    d, h = divmod(h, 24)
    return f"{d}d{h}h" if h else f"{d}d"


def item_seconds(it: dict, at: datetime) -> float | None:
    """done: finished - started; doing: now - started; else none."""
    try:
        if not it.get("started"):
            return None
        start = parse_time(it["started"])
        if it["state"] == "done" and it.get("finished"):
            return (parse_time(it["finished"]) - start).total_seconds()
        if it["state"] == "doing":
            return (at - start).total_seconds()
    except ValueError:
        return None
    return None


def overall_seconds(items: list[dict], at: datetime) -> float | None:
    """From the first start to now; to the last finish once every item is done."""
    starts = [parse_time(i["started"]) for i in items if i.get("started")]
    if not starts:
        return None
    end = at
    if items and all(i["state"] == "done" for i in items):
        ends = [parse_time(i["finished"]) for i in items if i.get("finished")]
        if ends:
            end = max(ends)
    return (end - min(starts)).total_seconds()


def author_forms(project: Path | None = None) -> set[str]:
    """Every way `needs` can name a human author: each `authors/<email>.md` profile's email, its
    **Name** and its **Preferred form of address** (lower case; `MILESTONES_AUTHORS` names the
    directory, else `authors/` of PROJECT or of the command's project; no such directory: no extra forms)."""
    d = Path(os.environ.get("MILESTONES_AUTHORS") or (project or cwd_project()) / "authors")
    forms: set[str] = set()
    try:
        profiles = sorted(d.glob("*.md"))
    except OSError:
        return forms
    for p in profiles:
        forms.add(p.stem.lower())
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            for field in ("**Name**:", "**Preferred form of address**:"):
                if line.startswith(field):
                    forms.add(line[len(field):].split(" — ")[0].strip().lower())
    return forms


def is_you(needs: str, forms: set[str] | None = None) -> bool:
    """`needs` names a human author: "you", "author", or one of an author's forms."""
    n = needs.strip().lower()
    return bool(n) and (n in ("you", "author") or n in (author_forms() if forms is None else forms))


def milestone_parts(data: dict, at: datetime, want: set[str], on: bool = False,
                    project: Path | None = None) -> list[str]:
    """Plain text is built and truncated first; colour wraps the finished pieces."""
    items = data["items"]
    out: list[str] = []
    if not items:          # an empty tracker shows no milestone segment
        return out
    if "milestones" in want:
        done = sum(1 for i in items if i["state"] == "done")
        bar_style = "green" if items and done == len(items) else "yellow"
        out.append(f"{paint(data['title'], 'bold', on)} {paint(bar(progress(items)), bar_style, on)}"
                   f" {done}/{len(items)}")
    if "timing" in want:
        cur = next((i for i in items if i["state"] == "doing"), None)
        secs = item_seconds(cur, at) if cur else None
        if cur is not None and secs is not None:
            out.append(paint(f"▸ #{cur['id']} {duration(secs)}", "yellow", on))
    if "milestones" in want:
        nxt = next((i for i in items if i["state"] != "done"), None)
        if nxt is not None:
            out.append("next: " + truncate(nxt["name"], NEXT_WIDTH))
        elif items:
            out.append(paint("all done", "green", on))
        forms = author_forms(project)
        you = sum(1 for i in items if i["state"] != "done" and is_you(i.get("needs", ""), forms))
        if you:
            out.append(paint("⚑ you" + (f" {you}" if you > 1 else ""), "bold-yellow", on))
        blocked = sum(1 for i in items if i["state"] == "blocked")
        if blocked:
            out.append(paint(f"⛔{blocked}", "bold-red", on))
    return out


def _num(obj: object, *path: str) -> float | None:
    for p in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(p)
    return float(obj) if isinstance(obj, (int, float)) and not isinstance(obj, bool) else None


def usage_parts(session: dict, want: set[str], on: bool = False) -> list[str]:
    """Session usage from Claude Code's statusline JSON (documented fields only:
    https://code.claude.com/docs/en/statusline#available-data). A field that is absent or
    null prints nothing — rate_limits is present only for Pro/Max/Ultra subscribers, after
    the session's first API response."""
    out: list[str] = []
    if "ctx" in want:
        pct = _num(session, "context_window", "used_percentage")
        if pct is not None:
            out.append("ctx " + paint(f"{pct:.0f}%", pct_style(pct), on))
    if "cost" in want:
        usd = _num(session, "cost", "total_cost_usd")
        if usd is not None:
            out.append(f"${usd:.2f}")
    if "time" in want:
        ms = _num(session, "cost", "total_duration_ms")
        if ms is not None:
            out.append("⏱ " + duration(ms / 1000))
    if "limits" in want:
        limits = []
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            pct = _num(session, "rate_limits", key, "used_percentage")
            if pct is not None:
                limits.append(f"{label} " + paint(f"{pct:.0f}%", pct_style(pct), on))
        if limits:
            out.append(" ".join(limits))
    return out


def _text(obj: object, *path: str) -> str | None:
    for p in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(p)
    return obj.strip() if isinstance(obj, str) and obj.strip() else None


MODEL_ID_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d))?(?:-\d{8})?$")


def model_name(value: str) -> str:
    """A full model id as a name (claude-opus-5-5 → Opus 5.5, claude-haiku-4-5-20251001 →
    Haiku 4.5); an alias capitalised (opus → Opus); anything else as written."""
    m = MODEL_ID_RE.match(value)
    if m:
        family, major, minor = m.groups()
        return f"{family.capitalize()} {major}" + (f".{minor}" if minor else "")
    if re.fullmatch(r"[a-z]+", value):
        return value.capitalize()
    return value


def settings_files(session: dict | None) -> list[Path]:
    """The settings files Claude Code reads advisorModel from, lowest precedence first
    (https://code.claude.com/docs/en/settings#settings-precedence): user
    ($CLAUDE_CONFIG_DIR or ~/.claude), shared project, project local. The project is the
    statusline JSON's workspace.project_dir, else $CLAUDE_PROJECT_DIR, else this checkout."""
    user = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    pdir = session_project(session) / ".claude"
    return [Path(user) / "settings.json", pdir / "settings.json", pdir / "settings.local.json"]


def advisor_model(session: dict | None) -> str | None:
    """advisorModel of the highest-precedence settings file that sets it; none when
    CLAUDE_CODE_DISABLE_ADVISOR_TOOL is set (https://code.claude.com/docs/en/advisor). The
    statusline JSON carries no advisor; a --advisor flag and managed settings stay unseen."""
    if os.environ.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "").strip() not in ("", "0", "false"):
        return None
    found: str | None = None
    for path in settings_files(session):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and "advisorModel" in data:
            value = data["advisorModel"]
            found = value.strip() if isinstance(value, str) and value.strip() else None
    return found


def model_part(session: dict | None, on: bool = False) -> str | None:
    """`Opus 5.5 · effort high · advisor Fable`: model.display_name and effort.level from the
    statusline JSON (https://code.claude.com/docs/en/statusline#available-data; effort is
    absent when the model has no effort parameter), the advisor from the settings files."""
    name = _text(session, "model", "display_name") or _text(session, "model", "id")
    pieces = [name] if name else []
    effort = _text(session, "effort", "level")
    if effort:
        pieces.append(f"effort {effort}")
    advisor = advisor_model(session)
    if advisor:
        pieces.append(f"advisor {model_name(advisor)}")
    if not pieces:
        return None
    return paint(" · ".join(pieces), "cyan", on)


def segment(data: dict | None, session: dict | None, want: set[str], at: datetime,
            on: bool = False) -> str:
    parts: list[str] = []
    if data is not None:
        parts += milestone_parts(data, at, want, on, session_project(session))
    if session:
        parts += usage_parts(session, want, on)
    if "model" in want:
        m = model_part(session, on)
        if m:
            parts.append(m)
    return " · ".join(parts)


def parse_parts(text: str | None) -> set[str]:
    if text is None:
        return set(PARTS)
    return {p.strip() for p in text.split(",") if p.strip() in PARTS}


# ---------------------------------------------------------------- commands

def cmd_statusline(args: argparse.Namespace) -> int:
    """Never raises, never fails, prints nothing it cannot compute."""
    try:
        want = parse_parts(args.parts if args.parts is not None
                           else statusline_env("PARTS"))
        session = None
        try:
            if not sys.stdin.isatty():
                raw = sys.stdin.read()
                session = json.loads(raw) if raw.strip() else None
                if not isinstance(session, dict):
                    session = None
        except Exception:
            session = None
        try:
            data = load(data_file(session_project(session)))
        except Exception:
            data = None
        line = segment(data, session, want, now(), color_wanted())
        if line:
            sys.stdout.write(line + "\n")
    except BaseException:  # the statusline never breaks
        pass
    return 0


def render_table(data: dict, at: datetime, on: bool = False) -> str:
    """Cells are padded as plain text; colour (the milestone name by state, a `needs` that is
    the author's, the bar) wraps them afterwards, so the columns line up either way."""
    items = data["items"]
    rows = [("#", "", "Milestone", "Left", "Needs", "Duration")]
    for i in items:
        secs = item_seconds(i, at)
        rows.append((str(i["id"]), ICONS[i["state"]], i["name"], i.get("left", ""),
                     i.get("needs", ""), duration(secs) if secs is not None else ""))
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    lines = []
    for n, r in enumerate(rows):
        cells = [cell.ljust(widths[c]) for c, cell in enumerate(r)]
        if n > 0:
            state = items[n - 1]["state"]
            cells[2] = paint(r[2], STATE_COLOR[state], on) + cells[2][len(r[2]):]
            if state != "done" and is_you(r[4]):
                cells[4] = paint(r[4], "bold-yellow", on) + cells[4][len(r[4]):]
        lines.append("  ".join(cells).rstrip())
        if n == 0:
            lines.append("  ".join("-" * w for w in widths).rstrip())
    done = sum(1 for i in items if i["state"] == "done")
    total = overall_seconds(items, at)
    bar_style = "green" if items and done == len(items) else "yellow"
    tail = f"{data['title']}  {paint(bar(progress(items)), bar_style, on)}  {done}/{len(items)} done"
    tail += f"  ·  {progress(items) * 100:.0f}%"
    if total is not None:
        tail += f"  ·  elapsed {duration(total)} since the first start"
    return "\n".join([*lines, "", tail])


def cmd_show(args: argparse.Namespace) -> int:
    path = data_file()
    if not path.exists():
        print(f"no milestones yet: {path} (start one with `milestones.py init --empty --title '…'` or "
              f"`milestones.py add --name '…'`)", file=sys.stderr)
        return 1
    data = load(path)
    at = now()
    if args.json:
        out = dict(data)
        out["progress"] = round(progress(data["items"]), 4)
        total = overall_seconds(data["items"], at)
        out["elapsed_seconds"] = int(total) if total is not None else None
        out["items"] = [dict(i, duration_seconds=(int(s) if (s := item_seconds(i, at)) is not None
                                                  else None)) for i in data["items"]]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif not data["items"]:
        print(f"{data['title']}: no milestones yet "
              f"(add one: milestones.py add --name \"…\")")
    else:
        print(render_table(data, at, color_wanted() and sys.stdout.isatty()))
    return 0


def apply_state(item: dict, state: str, at: datetime) -> None:
    """started when the item first goes to doing; finished when it goes to done."""
    item["state"] = state
    if state == "doing":
        item.setdefault("started", iso(at))
        item.pop("finished", None)
    elif state == "done":
        item["finished"] = iso(at)
    else:
        item.pop("finished", None)


def apply_text(item: dict, key: str, value: str | None) -> None:
    if value is None:
        return
    if value == "":
        item.pop(key, None)
    else:
        item[key] = value


def find(data: dict, ident: int) -> dict:
    for i in data["items"]:
        if i["id"] == ident:
            return i
    raise SystemExit(f"no milestone #{ident}")


def cmd_set(args: argparse.Namespace) -> int:
    path = data_file()
    data = load(path)
    item = find(data, args.id)
    if args.state:
        apply_state(item, args.state, now())
    if args.name is not None:
        item["name"] = args.name
    apply_text(item, "left", args.left)
    apply_text(item, "needs", args.needs)
    save(path, data)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    path = data_file()
    data = load_or_new(path)
    ident = max((i["id"] for i in data["items"]), default=0) + 1
    item: dict = {"id": ident, "name": args.name, "state": "todo"}
    apply_state(item, args.state, now())
    apply_text(item, "left", args.left)
    apply_text(item, "needs", args.needs)
    data["items"].append(item)
    save(path, data)
    print(ident)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    path = data_file()
    data = load(path)
    item = find(data, args.id)
    data["items"].remove(item)
    save(path, data)
    return 0


def cmd_title(args: argparse.Namespace) -> int:
    path = data_file()
    data = load_or_new(path)
    data["title"] = args.text
    save(path, data)
    return 0


EXAMPLE = {
    "title": "To release 1.0",
    "items": [
        {"id": 1, "name": "API design agreed", "state": "done"},
        {"id": 2, "name": "Storage layer", "state": "doing", "left": "migrations"},
        {"id": 3, "name": "Docs site", "state": "todo"},
        {"id": 4, "name": "Pick the license", "state": "blocked",
         "left": "legal review", "needs": "you"},
    ],
}


def cmd_init(args: argparse.Namespace) -> int:
    path = data_file()
    if path.exists() and not args.force:
        print(f"{path} exists (--force replaces it)", file=sys.stderr)
        return 1
    if args.empty:
        save(path, {"title": args.title or DEFAULT_TITLE, "items": []})
        print(path)
        return 0
    data = json.loads(json.dumps(EXAMPLE))
    if args.title:
        data["title"] = args.title
    at = now()
    for item in data["items"]:
        state = item["state"]
        if state in ("done", "doing"):
            item["started"] = iso(at)
        if state == "done":
            item["finished"] = iso(at)
    save(path, data)
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="milestones.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("statusline", help="one statusline segment (stdin: Claude Code's statusline JSON)")
    p.add_argument("--parts", help=f"comma list of {','.join(PARTS)} (default: all, or $MILESTONES_STATUSLINE_PARTS)")
    p.set_defaults(fn=cmd_statusline)
    p = sub.add_parser("show", help="the table and the bar line")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("set", help="change one milestone")
    p.add_argument("id", type=int)
    p.add_argument("--state", choices=STATES)
    p.add_argument("--name")
    p.add_argument("--left", help="what is left ('' clears it)")
    p.add_argument("--needs", help="who must act ('' clears it)")
    p.set_defaults(fn=cmd_set)
    p = sub.add_parser("add", help="append a milestone; prints its id")
    p.add_argument("--name", required=True)
    p.add_argument("--state", choices=STATES, default="todo")
    p.add_argument("--left")
    p.add_argument("--needs")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("remove", help="delete a milestone")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("title", help="set the title")
    p.add_argument("text")
    p.set_defaults(fn=cmd_title)
    p = sub.add_parser("init", help="write an example file (--empty: a tracker with no items)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--empty", action="store_true", help="no items, only the title")
    p.add_argument("--title", help=f"the title (default with --empty: {DEFAULT_TITLE})")
    p.set_defaults(fn=cmd_init)
    args = ap.parse_args(argv)
    if args.cmd == "statusline":
        return args.fn(args)
    try:
        return args.fn(args)
    except (ValueError, OSError) as e:
        print(f"milestones: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
