"""A standard-library validator for the plugin's tracker schema (plugins/milestones/skills/milestones/schema/milestones.schema.json).

The harness grades the file the agent leaves behind, so it needs its own judge of "valid tracker": it must not import the script
that wrote the file. Only the keywords the schema uses are implemented; `validate` raises ValueError for any other keyword, so that
a schema that grows one makes the harness fail loudly instead of silently accepting more. tests/test_schema_check.py also compares
it with milestones.py's own `validate()` on a list of trackers.
"""
import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "plugins" / "milestones" / "skills" / "milestones" / "schema" / "milestones.schema.json"
IMPLEMENTED = {"type", "required", "properties", "additionalProperties", "items", "enum", "minLength", "minimum"}
ANNOTATIONS = {"$schema", "$id", "title", "description"}
TYPES = {"object": dict, "array": list, "string": str, "integer": int}


def unsupported(schema):
    """The keywords of `schema` and its subschemas that are neither implemented nor annotations."""
    found = set(schema) - IMPLEMENTED - ANNOTATIONS
    for sub in [*schema.get("properties", {}).values(), schema.get("items"), schema.get("additionalProperties")]:
        if isinstance(sub, dict):
            found |= unsupported(sub)
    return found


def _is(value, name):
    """JSON typing: a bool is not an integer."""
    return isinstance(value, TYPES[name]) and not (name == "integer" and isinstance(value, bool))


def _errors(value, schema, where):
    if "type" in schema and not _is(value, schema["type"]):
        return [f"{where}: not a {schema['type']}"]
    errs = []
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        errs.append(f"{where}: shorter than {schema['minLength']}")
    if "minimum" in schema and _is(value, "integer") and value < schema["minimum"]:
        errs.append(f"{where}: below {schema['minimum']}")
    if isinstance(value, dict):
        errs += [f"{where}: missing {key}" for key in schema.get("required", []) if key not in value]
        props, extra = schema.get("properties", {}), schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in props:
                errs += _errors(sub, props[key], f"{where}.{key}")
            elif extra is False:
                errs.append(f"{where}: unknown key {key!r}")
            elif isinstance(extra, dict):
                errs += _errors(sub, extra, f"{where}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for n, sub in enumerate(value):
            errs += _errors(sub, schema["items"], f"{where}[{n}]")
    return errs


def validate(value, schema):
    """The violations of `schema` by `value` (an empty list when valid); ValueError when the schema uses an unimplemented keyword."""
    bad = unsupported(schema)
    if bad:
        raise ValueError(f"schema_check does not implement: {', '.join(sorted(bad))}")
    return _errors(value, schema, "$")


SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def tracker_errors(data):
    """The violations of the plugin's schema by a parsed tracker, plus the rule JSON Schema cannot state: ids are unique."""
    items = data.get("items") if isinstance(data, dict) else None
    ids = [i["id"] for i in items if isinstance(i, dict) and _is(i.get("id"), "integer")] if isinstance(items, list) else []
    return validate(data, SCHEMA) + [f"$.items: id {i} is used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
