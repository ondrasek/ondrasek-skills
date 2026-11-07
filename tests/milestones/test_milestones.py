"""Tests for the milestones plugin: scripts/milestones.py, the statusline wrapper next to it and the schema."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL_DIR = ROOT / "plugins" / "milestones" / "skills" / "milestones"
SCRIPT = SKILL_DIR / "scripts" / "milestones.py"
WRAPPER = SKILL_DIR / "scripts" / "statusline"
SCHEMA = SKILL_DIR / "schema" / "milestones.schema.json"
sys.path.insert(0, str(SCRIPT.parent))
import milestones as ms  # noqa: E402

T0 = "2026-10-31T12:00:00+00:00"
T1 = "2026-10-31T13:12:00+00:00"
SESSION = {
    "model": {"id": "claude-x", "display_name": "X"},
    "workspace": {"current_dir": "/w", "project_dir": "/w"},
    "context_window": {"used_percentage": 42},
    "cost": {"total_cost_usd": 1.234, "total_duration_ms": 2_700_000},
    "rate_limits": {"five_hour": {"used_percentage": 23.5},
                    "seven_day": {"used_percentage": 41.2}},
}


def example(**over) -> dict:
    data = {
        "title": "To F2/F3",
        "items": [
            {"id": 1, "name": "one", "state": "done", "started": T0, "finished": T0},
            {"id": 2, "name": "two", "state": "doing", "started": T0},
            {"id": 3, "name": "three", "state": "todo"},
            {"id": 4, "name": "four", "state": "blocked", "needs": "Ada"},
        ],
    }
    data.update(over)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milestones-test-")).resolve()
        self.file = self.tmp / ".cache" / "milestones.json"
        # The developer's shell never leaks in: colour, the settings directory, the advisor
        # switch. Colour is off by default here; ColorTest turns it on.
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("MILESTONES_", "WORKER_STATUSLINE"))
                    and k not in ("NO_COLOR", "CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR",
                                  "CLAUDE_CODE_DISABLE_ADVISOR_TOOL", "CLAUDE_CODE_EFFORT_LEVEL")}
        self.env.update(MILESTONES_FILE=str(self.file), MILESTONES_NOW=T1, HOME=str(self.tmp),
                        NO_COLOR="1", MILESTONES_AUTHORS=str(self.tmp / "authors"))
        (self.tmp / "authors").mkdir()
        (self.tmp / "authors" / "ada@example.org.md").write_text(
            "# Author Profile\n\n**Name**: Ada Byron — the ASCII form\n"
            "**Preferred form of address**: Ada — in all generated content\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, data) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(data if isinstance(data, str) else json.dumps(data))

    def run_ms(self, *args, stdin: str = "", env: dict | None = None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin,
                              capture_output=True, text=True, env=env or self.env, timeout=30)

    def read(self) -> dict:
        return json.loads(self.file.read_text())


class StatuslineTest(Base):
    def test_absent_empty_invalid_print_nothing(self):
        r = self.run_ms("statusline")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        for bad in ("", "{", "[]", json.dumps({"title": "x"}),
                    json.dumps(example(items=[{"id": 1, "name": "a", "state": "nope"}]))):
            self.write(bad)
            r = self.run_ms("statusline")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), bad)

    def test_invalid_stdin_is_ignored(self):
        self.write(example())
        r = self.run_ms("statusline", stdin="not json")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("To F2/F3 "))

    def test_segment(self):
        self.write(example())
        r = self.run_ms("statusline", "--parts", "milestones,timing")
        self.assertEqual(r.stdout,
                         "To F2/F3 ██▌░░░ 1/4 · ▸ #2 1h12m · next: two · ⚑ you · ⛔1\n")

    def test_bar_math(self):
        self.assertEqual(ms.bar(0), "░░░░░░")
        self.assertEqual(ms.bar(1), "██████")
        self.assertEqual(ms.bar(0.5), "███░░░")
        self.assertEqual(ms.bar(1.5 / 8), "█░░░░░")  # 2.25 halves → 2
        self.assertEqual(ms.bar(1.5 / 4), "██▌░░░")  # 4.5 halves → 5 (half up)
        self.assertEqual(ms.bar(0.25), "█▌░░░░")
        items = [{"state": s} for s in ("done", "doing", "todo", "blocked")]
        self.assertAlmostEqual(ms.progress(items), 1.5 / 4)
        self.assertEqual(ms.progress([]), 0.0)

    def test_needs_marker(self):
        d = example()
        for who in ("ada", "Ada Byron", "ada@example.org", "you"):   # every form of an author
            d["items"][2]["needs"] = who
            self.write(d)
            self.assertIn("⚑ you 2", self.run_ms("statusline", "--parts", "milestones").stdout, who)
        d["items"][2]["needs"] = "the VM"
        d["items"][3]["needs"] = "someone"
        self.write(d)
        self.assertNotIn("⚑", self.run_ms("statusline", "--parts", "milestones").stdout)
        d["items"][3]["state"] = "done"
        d["items"][3]["needs"] = "Ada"   # a done item needs nobody any more
        self.write(d)
        self.assertNotIn("⚑", self.run_ms("statusline", "--parts", "milestones").stdout)

    def test_truncation(self):
        d = example()
        d["items"][1]["name"] = "a very long milestone name that runs on and on"
        self.write(d)
        out = self.run_ms("statusline", "--parts", "milestones").stdout
        nxt = out.split("next: ")[1].split(" · ")[0]
        self.assertLessEqual(len(nxt), ms.NEXT_WIDTH)
        self.assertTrue(nxt.endswith("…"))

    def test_all_done(self):
        d = example()
        for i in d["items"]:
            i["state"] = "done"
            i.pop("needs", None)
        self.write(d)
        self.assertEqual(self.run_ms("statusline", "--parts", "milestones,timing").stdout,
                         "To F2/F3 ██████ 4/4 · all done\n")

    def test_usage_parts(self):
        self.write(example())
        r = self.run_ms("statusline", "--parts", "ctx,cost,time,limits",
                        stdin=json.dumps(SESSION))
        self.assertEqual(r.stdout, "ctx 42% · $1.23 · ⏱ 45m · 5h 24% 7d 41%\n")

    def test_usage_without_milestones_file(self):
        r = self.run_ms("statusline", "--parts", "ctx,cost", stdin=json.dumps(SESSION))
        self.assertEqual(r.stdout, "ctx 42% · $1.23\n")

    def test_absent_usage_fields_print_nothing(self):
        session = {"model": {"id": "x"}, "context_window": {"used_percentage": None},
                   "rate_limits": {"five_hour": {"used_percentage": 10}}}
        r = self.run_ms("statusline", "--parts", "ctx,cost,time,limits",
                        stdin=json.dumps(session))
        self.assertEqual(r.stdout, "5h 10%\n")

    def test_parts_from_environment(self):
        self.write(example())
        env = dict(self.env, MILESTONES_STATUSLINE_PARTS="cost")
        self.assertEqual(self.run_ms("statusline", stdin=json.dumps(SESSION), env=env).stdout,
                         "$1.23\n")

    def test_all_parts_by_default(self):
        self.write(example())
        out = self.run_ms("statusline", stdin=json.dumps(SESSION)).stdout
        for piece in ("To F2/F3", "▸ #2", "ctx 42%", "$1.23", "⏱ 45m", "5h 24%"):
            self.assertIn(piece, out)

    def test_duration_format(self):
        self.assertEqual(ms.duration(30), "<1m")
        self.assertEqual(ms.duration(12 * 60), "12m")
        self.assertEqual(ms.duration(3600), "1h")
        self.assertEqual(ms.duration(72 * 60), "1h12m")
        self.assertEqual(ms.duration(27 * 3600), "1d3h")


ESC = "\x1b["


def strip(text: str) -> str:
    return ms.ESC_RE.sub("", text)


class ModelTest(Base):
    """The model part: display name and effort from stdin, the advisor from settings files."""

    def setUp(self):
        super().setUp()
        self.project = self.tmp / "proj"
        self.session = dict(SESSION, model={"id": "claude-zeta-9-1", "display_name": "Zeta 9.1"},
                            effort={"level": "high"},
                            workspace={"current_dir": str(self.project),
                                       "project_dir": str(self.project)})

    def settings(self, where: Path, body) -> None:
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(body if isinstance(body, str) else json.dumps(body))

    def model_line(self, session=None, env=None) -> str:
        return self.run_ms("statusline", "--parts", "model",
                           stdin=json.dumps(session or self.session), env=env).stdout

    def test_name_and_effort_from_stdin(self):
        self.assertEqual(self.model_line(), "Zeta 9.1 · effort high\n")

    def test_effort_absent_prints_no_effort(self):
        session = dict(self.session)
        del session["effort"]
        self.assertEqual(self.model_line(session), "Zeta 9.1\n")

    def test_id_when_no_display_name(self):
        session = dict(self.session, model={"id": "claude-zeta-9-1"})
        self.assertEqual(self.model_line(session), "claude-zeta-9-1 · effort high\n")

    def test_advisor_precedence(self):
        user = self.tmp / ".claude" / "settings.json"
        shared = self.project / ".claude" / "settings.json"
        local = self.project / ".claude" / "settings.local.json"
        self.settings(user, {"advisorModel": "sonnet"})
        self.assertEqual(self.model_line(), "Zeta 9.1 · effort high · advisor Sonnet\n")
        self.settings(shared, {"advisorModel": "claude-zeta-9-1"})
        self.assertEqual(self.model_line(), "Zeta 9.1 · effort high · advisor Zeta 9.1\n")
        self.settings(local, {"advisorModel": "claude-omega-4-5-20990101"})
        self.assertEqual(self.model_line(), "Zeta 9.1 · effort high · advisor Omega 4.5\n")
        self.settings(local, {"advisorModel": ""})   # an empty value: no advisor
        self.assertEqual(self.model_line(), "Zeta 9.1 · effort high\n")

    def test_advisor_kill_switch(self):
        self.settings(self.tmp / ".claude" / "settings.json", {"advisorModel": "opus"})
        env = dict(self.env, CLAUDE_CODE_DISABLE_ADVISOR_TOOL="1")
        self.assertEqual(self.model_line(env=env), "Zeta 9.1 · effort high\n")

    def test_claude_config_dir_replaces_home(self):
        self.settings(self.tmp / ".claude" / "settings.json", {"advisorModel": "opus"})
        cfg = self.tmp / "cfg"
        self.settings(cfg / "settings.json", {"advisorModel": "fable"})
        env = dict(self.env, CLAUDE_CONFIG_DIR=str(cfg))
        self.assertEqual(self.model_line(env=env), "Zeta 9.1 · effort high · advisor Fable\n")

    def test_malformed_settings_are_skipped(self):
        self.settings(self.tmp / ".claude" / "settings.json", {"advisorModel": "opus"})
        self.settings(self.project / ".claude" / "settings.json", "{not json")
        self.settings(self.project / ".claude" / "settings.local.json", "[1, 2]")
        r = self.run_ms("statusline", "--parts", "model", stdin=json.dumps(self.session))
        self.assertEqual((r.returncode, r.stdout, r.stderr),
                         (0, "Zeta 9.1 · effort high · advisor Opus\n", ""))

    def test_part_selection(self):
        self.write(example())
        out = self.run_ms("statusline", stdin=json.dumps(self.session)).stdout
        self.assertTrue(out.rstrip("\n").endswith(" · Zeta 9.1 · effort high"), out)
        out = self.run_ms("statusline", "--parts", "milestones,ctx",
                          stdin=json.dumps(self.session)).stdout
        self.assertNotIn("Zeta", out)

    def test_nothing_known_prints_nothing(self):
        r = self.run_ms("statusline", "--parts", "model", stdin="")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_model_name(self):
        self.assertEqual(ms.model_name("claude-zeta-9-1"), "Zeta 9.1")
        self.assertEqual(ms.model_name("claude-omega-4-5-20990101"), "Omega 4.5")
        self.assertEqual(ms.model_name("claude-omega-7"), "Omega 7")
        self.assertEqual(ms.model_name("sonnet"), "Sonnet")
        self.assertEqual(ms.model_name("some/Custom-Model"), "some/Custom-Model")


class ColorTest(Base):
    def setUp(self):
        super().setUp()
        del self.env["NO_COLOR"]

    def test_colored_segment(self):
        self.write(example())
        session = dict(SESSION, context_window={"used_percentage": 85},
                       rate_limits={"five_hour": {"used_percentage": 10},
                                    "seven_day": {"used_percentage": 60}})
        out = self.run_ms("statusline", "--parts", "milestones,timing,ctx,limits,model",
                          stdin=json.dumps(session)).stdout
        self.assertIn(ESC + "1;33m⚑ you" + ms.RESET, out)
        self.assertIn(ESC + "1;31m⛔1" + ms.RESET, out)
        self.assertIn(ESC + "33m██▌░░░" + ms.RESET, out)        # the bar
        self.assertIn("ctx " + ESC + "31m85%" + ms.RESET, out)  # ≥ 80: red
        self.assertIn("5h " + ESC + "32m10%" + ms.RESET, out)   # < 50: green
        self.assertIn("7d " + ESC + "33m60%" + ms.RESET, out)   # < 80: yellow
        self.assertIn(ESC + "36mX" + ms.RESET, out)             # the model part
        plain = self.run_ms("statusline", "--parts", "milestones,timing,ctx,limits,model",
                            stdin=json.dumps(session), env=dict(self.env, NO_COLOR="1")).stdout
        self.assertNotIn("\x1b", plain)
        self.assertEqual(strip(out), plain)

    def test_color_switches(self):
        self.write(example())
        for extra, colored in (({}, True), ({"NO_COLOR": ""}, True), ({"NO_COLOR": "1"}, False),
                               ({"MILESTONES_STATUSLINE_COLOR": "0"}, False),
                               ({"MILESTONES_STATUSLINE_COLOR": "1"}, True)):
            out = self.run_ms("statusline", "--parts", "milestones",
                              env=dict(self.env, **extra)).stdout
            self.assertEqual("\x1b" in out, colored, extra)

    def test_show_colors_only_on_a_terminal(self):
        self.write(example())
        r = self.run_ms("show")   # captured stdout: no terminal
        self.assertNotIn("\x1b", r.stdout)
        self.assertNotIn("\x1b", self.run_ms("show", "--json").stdout)

    def test_colored_table_keeps_its_columns(self):
        at = ms.parse_time(T1)
        with mock.patch.dict(os.environ, MILESTONES_AUTHORS=self.env["MILESTONES_AUTHORS"]):
            plain = ms.render_table(example(), at, on=False)
            colored = ms.render_table(example(), at, on=True)
        self.assertIn(ESC + "1;31mfour" + ms.RESET, colored)   # blocked
        self.assertIn(ESC + "1;33mAda" + ms.RESET, colored)    # needs the author
        self.assertEqual(strip(colored), plain)


class ProjectTest(Base):
    """A user-level statusline shows each project's own tracker (workspace.project_dir), and
    the commands write the tracker of the project they run in."""

    def env_without_file(self) -> dict:
        env = dict(self.env)
        env.pop("MILESTONES_FILE", None)
        env.pop("CLAUDE_PROJECT_DIR", None)
        return env

    def test_statusline_reads_the_session_project(self):
        proj = self.tmp / "other"
        (proj / ".cache").mkdir(parents=True)
        (proj / ".cache" / "milestones.json").write_text(json.dumps(example(title="Other")),
                                                       encoding="utf-8")
        session = json.dumps({"workspace": {"project_dir": str(proj)}})
        out = self.run_ms("statusline", "--parts", "milestones", stdin=session,
                          env=self.env_without_file()).stdout
        self.assertTrue(out.startswith("Other "), out)

    def test_a_project_without_a_tracker_shows_no_milestones(self):
        proj = self.tmp / "bare"
        proj.mkdir()
        session = json.dumps({"workspace": {"project_dir": str(proj)},
                              "model": {"display_name": "Model X"}})
        out = self.run_ms("statusline", "--parts", "milestones,model", stdin=session,
                          env=self.env_without_file()).stdout
        self.assertEqual(out, "Model X\n")

    def test_commands_write_the_tracker_of_the_working_directory(self):
        proj = self.tmp / "repo"
        (proj / ".git").mkdir(parents=True)
        (proj / "sub").mkdir()
        r = subprocess.run([sys.executable, str(SCRIPT), "init"], cwd=proj / "sub",
                           env=self.env_without_file(), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((proj / ".cache" / "milestones.json").is_file())


class EmptyTrackerTest(Base):
    """A project starts a tracker from nothing: `init --empty`, or `add` / `title` on a missing
    file; an empty tracker shows no milestone segment and a one-line `show`."""

    def test_init_empty_writes_a_tracker_without_items(self):
        r = self.run_ms("init", "--empty", "--title", "Thesis")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read()["items"], [])
        self.assertEqual(self.read()["title"], "Thesis")
        self.assertEqual(self.run_ms("statusline", "--parts", "milestones,timing").stdout, "")
        self.assertIn("Thesis: no milestones yet", self.run_ms("show").stdout)

    def test_add_and_title_create_a_missing_tracker(self):
        self.assertFalse(self.file.exists())
        r = self.run_ms("add", "--name", "first step")
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"), r.stderr)
        self.assertEqual(self.read()["title"], ms.DEFAULT_TITLE)
        self.file.unlink()
        self.assertEqual(self.run_ms("title", "Book").returncode, 0)
        self.assertEqual(self.read(), {**self.read(), "title": "Book", "items": []})

    def test_init_without_flags_keeps_the_example(self):
        self.assertEqual(self.run_ms("init").returncode, 0)
        self.assertEqual(len(self.read()["items"]), len(ms.EXAMPLE["items"]))


class WidthTest(unittest.TestCase):
    def test_visible_width_ignores_escapes(self):
        self.assertEqual(ms.visible_width(ms.paint("abc", "red", True)), 3)
        self.assertEqual(ms.visible_width("⛔1"), 3)        # an emoji takes two columns
        self.assertEqual(ms.visible_width("██▌░░░"), 6)

    def test_truncate_counts_visible_columns(self):
        painted = ms.paint("abcdefghij", "cyan", True)
        self.assertEqual(ms.truncate(painted, 10), painted)  # fits: escapes count 0
        cut = ms.truncate(painted, 5)
        self.assertEqual(strip(cut), "abcd…")
        self.assertTrue(cut.startswith(ESC + "36m") and cut.endswith(ms.RESET))
        self.assertEqual(ms.truncate("⛔⛔⛔x", 5), "⛔⛔…")
        self.assertEqual(ms.truncate("plain text here", 6), "plain…")


class ShowTest(Base):
    def test_show_table_and_bar(self):
        self.write(example())
        r = self.run_ms("show")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0].split(), ["#", "Milestone", "Left", "Needs", "Duration"])
        self.assertIn("✅", r.stdout)
        self.assertIn("⛔", r.stdout)
        row2 = next(line for line in lines if line.startswith("2 "))
        self.assertIn("🟡", row2)
        self.assertTrue(row2.endswith("1h12m"))
        row1 = next(line for line in lines if line.startswith("1 "))
        self.assertTrue(row1.endswith("<1m"))
        self.assertIn("Ada", next(line for line in lines if line.startswith("4 ")))
        self.assertEqual(lines[-1],
                         "To F2/F3  ██▌░░░  1/4 done  ·  38%  ·  elapsed 1h12m since the first start")

    def test_show_json(self):
        self.write(example())
        out = json.loads(self.run_ms("show", "--json").stdout)
        self.assertEqual(out["progress"], 0.375)
        self.assertEqual(out["elapsed_seconds"], 72 * 60)
        self.assertEqual([i["duration_seconds"] for i in out["items"]], [0, 72 * 60, None, None])

    def test_show_absent(self):
        r = self.run_ms("show")
        self.assertEqual(r.returncode, 1)
        self.assertIn("init", r.stderr)


class WriteTest(Base):
    def test_init_writes_a_valid_example(self):
        r = self.run_ms("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.read()
        self.assertEqual(ms.validate(data), [])
        self.assertEqual(data["updated"], T1)
        self.assertEqual(self.run_ms("init").returncode, 1)  # an existing file stays
        self.assertEqual(self.run_ms("init", "--force").returncode, 0)

    def test_set_records_started_and_finished(self):
        self.write(example())
        env = dict(self.env, MILESTONES_NOW="2026-10-31T14:00:00+00:00")
        self.assertEqual(self.run_ms("set", "3", "--state", "doing", env=env).returncode, 0)
        it = self.read()["items"][2]
        self.assertEqual(it["started"], "2026-10-31T14:00:00+00:00")
        self.assertNotIn("finished", it)
        env2 = dict(self.env, MILESTONES_NOW="2026-10-31T15:30:00+00:00")
        self.run_ms("set", "3", "--state", "done", "--left", "", env=env2)
        it = self.read()["items"][2]
        self.assertEqual((it["started"], it["finished"]),
                         ("2026-10-31T14:00:00+00:00", "2026-10-31T15:30:00+00:00"))
        # back to doing: the first start stays, finished goes
        self.run_ms("set", "3", "--state", "doing", env=env2)
        it = self.read()["items"][2]
        self.assertEqual(it["started"], "2026-10-31T14:00:00+00:00")
        self.assertNotIn("finished", it)

    def test_set_text_fields(self):
        self.write(example())
        self.run_ms("set", "2", "--left", "the tests", "--needs", "Ada", "--name", "TWO")
        it = self.read()["items"][1]
        self.assertEqual((it["name"], it["left"], it["needs"]), ("TWO", "the tests", "Ada"))
        self.run_ms("set", "2", "--needs", "")
        self.assertNotIn("needs", self.read()["items"][1])

    def test_add_and_remove(self):
        self.write(example())
        r = self.run_ms("add", "--name", "five", "--left", "all", "--state", "doing")
        self.assertEqual(r.stdout.strip(), "5")
        it = self.read()["items"][-1]
        self.assertEqual((it["id"], it["state"], it["left"], it["started"]), (5, "doing", "all", T1))
        self.assertEqual(self.run_ms("remove", "2").returncode, 0)
        self.assertEqual([i["id"] for i in self.read()["items"]], [1, 3, 4, 5])
        r = self.run_ms("remove", "9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("#9", r.stderr)

    def test_title(self):
        self.write(example())
        self.run_ms("title", "To F4")
        self.assertEqual(self.read()["title"], "To F4")

    def test_invalid_write_leaves_the_file(self):
        self.write(example())
        before = self.file.read_text()
        r = self.run_ms("title", "")
        self.assertEqual(r.returncode, 2)
        self.assertIn("title", r.stderr)
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ["milestones.json"])

    def test_write_is_atomic(self):
        """save() writes a temporary file and os.replace()s it: a failure mid-write keeps the old file."""
        self.write(example())
        before = self.file.read_text()
        real = os.replace

        def boom(a, b):
            raise OSError("disk full")
        os.environ["MILESTONES_FILE"] = str(self.file)
        try:
            ms.os.replace = boom
            with self.assertRaises(OSError):
                ms.save(self.file, example(title="new"))
        finally:
            ms.os.replace = real
            del os.environ["MILESTONES_FILE"]
        self.assertEqual(self.file.read_text(), before)
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ["milestones.json"])

    def test_validator_agrees_with_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = json.loads(SCHEMA.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        v = jsonschema.Draft202012Validator(schema)
        cases = [
            example(),
            example(updated=T1),
            {"title": "x"},
            {"title": "", "items": []},
            {"title": "x", "items": [], "extra": 1},
            example(items=[{"id": 1, "name": "a", "state": "nope"}]),
            example(items=[{"id": 0, "name": "a", "state": "todo"}]),
            example(items=[{"id": 1, "name": "", "state": "todo"}]),
            example(items=[{"id": 1, "name": "a", "state": "todo", "x": 1}]),
            example(items=[{"id": 1, "name": "a", "state": "todo", "left": 3}]),
            example(items=[{"id": "1", "name": "a", "state": "todo"}]),
        ]
        for c in cases:
            self.assertEqual(v.is_valid(c), ms.validate(c) == [], c)
        dup = example(items=[{"id": 1, "name": "a", "state": "todo"},
                             {"id": 1, "name": "b", "state": "todo"}])
        self.assertTrue(ms.validate(dup))  # unique ids: the script's own rule


class WrapperTest(Base):
    def run_wrapper(self, env_extra: dict, stdin: str = json.dumps(SESSION)):
        env = dict(self.env, **env_extra)
        return subprocess.run(["sh", str(WRAPPER)], input=stdin, capture_output=True,
                              text=True, env=env, timeout=30)

    def fake_base(self, body: str) -> Path:
        p = self.tmp / "base.sh"
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return p

    def test_base_first_then_segment(self):
        self.write(example())
        # the base reads the same stdin: it prints the model name from it
        base = self.fake_base("python3 -c 'import json,sys; print(\"BASE\", json.load(sys.stdin)[\"model\"][\"id\"])'\n")
        r = self.run_wrapper({"MILESTONES_STATUSLINE_BASE": str(base)})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout,
                         "BASE claude-x │ To F2/F3 ██▌░░░ 1/4 · ▸ #2 1h12m · next: two · ⚑ you · ⛔1\n")

    def test_home_statusline_is_not_used(self):
        self.write(example())
        (self.tmp / ".claude").mkdir()
        p = self.tmp / ".claude" / "statusline.sh"
        p.write_text("#!/bin/sh\ncat >/dev/null\necho line1\n")
        p.chmod(0o755)
        r = self.run_wrapper({})
        self.assertNotIn("line1", r.stdout)
        self.assertTrue(r.stdout.startswith("To F2/F3 "), r.stdout)

    def test_no_base_prints_usage_too(self):
        self.write(example())
        r = self.run_wrapper({})  # HOME has no statusline.sh
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("To F2/F3 "))
        self.assertIn("ctx 42% · $1.23 · ⏱ 45m · 5h 24% 7d 41%", r.stdout)

    def test_failing_base(self):
        self.write(example())
        base = self.fake_base("echo half; exit 3\n")
        r = self.run_wrapper({"MILESTONES_STATUSLINE_BASE": str(base)})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("To F2/F3 "), r.stdout)
        self.assertNotIn("half", r.stdout)
        r = self.run_wrapper({"MILESTONES_STATUSLINE_BASE": str(self.tmp / "missing.sh")})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("To F2/F3 "), r.stdout)

    def test_parts_override_and_nothing_to_show(self):
        r = self.run_wrapper({"MILESTONES_STATUSLINE_BASE": "", "MILESTONES_STATUSLINE_PARTS": "milestones"})
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        base = self.fake_base("echo B\n")
        r = self.run_wrapper({"MILESTONES_STATUSLINE_BASE": str(base),
                              "MILESTONES_STATUSLINE_PARTS": "cost"})
        self.assertEqual(r.stdout, "B │ $1.23\n")

    def test_recursion_guard(self):
        self.write(example())
        r = self.run_wrapper({"MILESTONES_STATUSLINE_BASE": f"sh {WRAPPER}"})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("To F2/F3 "), r.stdout)

    def test_the_wrapper_is_executable(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK))

    def test_the_older_worker_names_still_work(self):
        self.write(example())
        base = self.fake_base("echo B\n")
        r = self.run_wrapper({"WORKER_STATUSLINE_BASE": str(base), "WORKER_STATUSLINE_PARTS": "cost"})
        self.assertEqual(r.stdout, "B │ $1.23\n")

    def test_the_readme_command_picks_the_newest_installed_version(self):
        """The statusLine command of the README runs the wrapper of the highest plugin version."""
        self.write(example())
        for version in ("0.9.0", "0.10.0"):
            target = self.tmp / ".claude" / "plugins" / "cache" / "ondrasek-skills" / "milestones" / version
            shutil.copytree(SKILL_DIR, target / "skills" / "milestones")
        marker = self.tmp / ".claude" / "plugins" / "cache" / "ondrasek-skills" / "milestones" / "0.10.0" / "skills" / "milestones" / "scripts" / "statusline"
        marker.write_text("#!/bin/sh\necho NEWEST\n")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        command = next(json.loads(l.split('"command": ', 1)[1].rstrip().rstrip(","))
                       for l in readme.splitlines() if '"command": ' in l)
        r = subprocess.run(["sh", "-c", command], input=json.dumps(SESSION), capture_output=True,
                           text=True, env=dict(self.env, HOME=str(self.tmp)), timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, "NEWEST\n"), r.stderr)


if __name__ == "__main__":
    unittest.main()
