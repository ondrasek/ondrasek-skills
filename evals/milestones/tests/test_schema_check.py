import importlib.machinery
import importlib.util
import json
import sys
import unittest

import helpers
import schema_check

SCHEMA = json.loads(schema_check.SCHEMA_PATH.read_text(encoding="utf-8"))


def load_script():
    """milestones.py as a module, without letting Python write a __pycache__ under plugins/."""
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        loader = importlib.machinery.SourceFileLoader("milestones_under_test", str(helpers.SCRIPT))
        module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = keep


def example(**override):
    return {"title": "x", "items": [{"id": 1, "name": "a", "state": "todo"}], **override}


def item(**override):
    return example(items=[{"id": 1, "name": "a", "state": "todo", **override}])


INVALID = {
    "not an object": [],
    "missing title": {"items": []},
    "empty title": {"title": "", "items": []},
    "missing items": {"title": "x"},
    "items not a list": {"title": "x", "items": {}},
    "extra top-level key": example(owner="me"),
    "updated not a string": example(updated=3),
    "item not an object": example(items=["a"]),
    "state outside the enum": item(state="finished"),
    "id zero": item(id=0),
    "id a string": item(id="1"),
    "id a bool": item(id=True),
    "id a float": item(id=1.5),
    "empty name": item(name=""),
    "extra item key": item(owner="me"),
    "left not a string": item(left=3),
    "missing state": example(items=[{"id": 1, "name": "a"}]),
    "duplicate id": example(items=[{"id": 1, "name": "a", "state": "todo"}, {"id": 1, "name": "b", "state": "todo"}]),
}


class Keywords(unittest.TestCase):
    def test_every_keyword_of_the_plugin_schema_is_implemented(self):
        self.assertEqual(schema_check.unsupported(SCHEMA), set())

    def test_the_walk_is_structural_so_a_property_named_title_is_not_a_keyword(self):
        self.assertIn("title", SCHEMA["properties"])
        self.assertEqual(schema_check.unsupported({"properties": {"title": {"type": "string"}, "pattern": {"type": "string"}}}), set())

    def test_a_new_keyword_anywhere_in_the_schema_is_found(self):
        for schema in ({"pattern": "x"}, {"properties": {"a": {"format": "date"}}}, {"items": {"oneOf": []}}, {"additionalProperties": {"$ref": "#"}}):
            self.assertTrue(schema_check.unsupported(schema), schema)

    def test_validate_refuses_a_schema_with_an_unimplemented_keyword(self):
        with self.assertRaisesRegex(ValueError, "pattern"):
            schema_check.validate({"title": "x"}, {"type": "object", "properties": {"title": {"pattern": "^x"}}})


class Validate(unittest.TestCase):
    def test_every_fixture_tracker_is_valid(self):
        for name in ("tracker", "stale-task", "stale-idle"):
            self.assertEqual(schema_check.tracker_errors(helpers.tracker(name)), [], name)

    def test_a_valid_tracker_with_every_optional_field(self):
        full = item(left="x", needs="you", started="2026-01-01T00:00:00+00:00", finished="2026-01-02T00:00:00+00:00")
        self.assertEqual(schema_check.tracker_errors({**full, "updated": "2026-01-02T00:00:00+00:00"}), [])

    def test_every_invalid_tracker_is_rejected(self):
        for label, data in INVALID.items():
            with self.subTest(label):
                self.assertTrue(schema_check.tracker_errors(data))

    def test_an_error_names_the_place(self):
        errors = schema_check.tracker_errors(item(state="finished", owner="me"))
        self.assertTrue(any("$.items[0].state" in e and "finished" in e for e in errors), errors)
        self.assertTrue(any("owner" in e for e in errors), errors)

    def test_a_bool_is_not_an_integer_and_a_dict_additional_properties_is_validated(self):
        self.assertTrue(schema_check.validate(True, {"type": "integer"}))
        self.assertEqual(schema_check.validate(3, {"type": "integer", "minimum": 1}), [])
        self.assertTrue(schema_check.validate({"a": 1}, {"type": "object", "additionalProperties": {"type": "string"}}))

    def test_it_agrees_with_the_scripts_own_validator(self):
        script = load_script()
        every = [example(), example(updated="2026-01-01T00:00:00+00:00"), *[helpers.tracker(n) for n in ("tracker", "stale-task")], *INVALID.values()]
        for data in every:
            self.assertEqual(schema_check.tracker_errors(data) == [], script.validate(data) == [], data)


if __name__ == "__main__":
    unittest.main()
