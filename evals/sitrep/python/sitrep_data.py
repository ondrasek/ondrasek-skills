"""Read and validate the `data.js` of a sitrep report, in pure Python: no JavaScript is ever executed.

`parse` reads the object literal of `window.SITREP = {...};` the way a browser would for the subset of JavaScript that data
files use: comments, bare keys, single-quoted, double-quoted and backtick strings (escapes, no `${}`), trailing commas, numbers,
true/false/null. It is a recursive-descent reader, not a regex, so `//` inside a string is text. It does not read expressions:
string concatenation, variables, function calls, spreads, computed keys. Such a file is reported as unreadable, although a
browser would run it; that is the limit of this harness, stated in the README.

`validate` follows plugins/sitrep/skills/sitrep/schema.md. It is stricter in one place: every item needs a unique string `id`,
because `dependsOn`, stable regeneration and the feedback export are keyed by it (schema.md calls only the titles essential).
"""
import re

ASSIGN_RE = re.compile(r"\bwindow\s*\.\s*SITREP\s*=")
TYPES = ("decision", "question", "risk", "concern", "blocker", "other")
STATUSES = ("open", "blocked", "parked")
_DIRECTIVE = re.compile(r"(['\"])use strict\1\s*;?")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_HEX = {"x": re.compile(r"[0-9a-fA-F]{2}"), "u": re.compile(r"[0-9a-fA-F]{4}"), "{": re.compile(r"\{([0-9a-fA-F]+)\}")}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_CONSTANTS = {"true": True, "false": False, "null": None}


class DataError(ValueError):
    pass


class _Reader:
    def __init__(self, text, pos):
        self.text, self.pos = text, pos

    def fail(self, message):
        raise DataError(f"{message} at line {self.text.count(chr(10), 0, self.pos) + 1}")

    def skip(self):
        """Whitespace and comments."""
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text.startswith("//", self.pos):
                end = self.text.find("\n", self.pos)
                self.pos = len(self.text) if end < 0 else end
            elif self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos + 2)
                if end < 0:
                    self.fail("unterminated comment")
                self.pos = end + 2
            else:
                return

    def peek(self):
        self.skip()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expect(self, char, what):
        if self.peek() != char:
            self.fail(f"expected {what}" if self.pos < len(self.text) else f"the data ends where {what} is expected")
        self.pos += 1

    def value(self):
        char = self.peek()
        if not char:
            self.fail("the data ends where a value is expected")
        if char == "{":
            return self.collection("}", self.member)
        if char == "[":
            return self.collection("]", self.value)
        if char in "\"'`":
            return self.string()
        if char in "-+." or char.isdigit():
            match = _NUMBER.match(self.text, self.pos)
            if not match:
                self.fail("malformed number")
            self.pos = match.end()
            return float(match.group()) if re.search(r"[.eE]", match.group()) else int(match.group())
        match = _IDENT.match(self.text, self.pos)
        if match and match.group() in _CONSTANTS:
            self.pos = match.end()
            return _CONSTANTS[match.group()]
        self.fail("unsupported JavaScript (only literals are read, not expressions)")

    def collection(self, close, member):
        """An object (close '}') or array (close ']'): members separated by commas, a trailing comma allowed."""
        self.pos += 1
        found = []
        while self.peek() != close:
            found.append(member())
            following = self.peek()
            if following and following in "+*(.%":
                self.fail("unsupported JavaScript (an expression, not a literal)")
            if following != close:
                self.expect(",", f"',' or '{close}'")
        self.pos += 1
        return dict(found) if close == "}" else found

    def member(self):
        self.skip()
        match = _IDENT.match(self.text, self.pos)
        if match:
            self.pos, key = match.end(), match.group()
        elif self.text[self.pos:self.pos + 1] in ("\"", "'"):
            key = self.string()
        else:
            self.fail("expected a property name")
        self.expect(":", "':'")
        return key, self.value()

    def string(self):
        quote = self.text[self.pos]
        self.pos += 1
        out = []
        while self.pos < len(self.text):
            char = self.text[self.pos]
            self.pos += 1
            if char == quote:
                return "".join(out)
            if char == "\n" and quote != "`":
                self.fail("a line break inside a string")
            if char == "$" and quote == "`" and self.text.startswith("{", self.pos):
                self.fail("unsupported JavaScript (template placeholder)")
            out.append(self.escape() if char == "\\" else char)
        self.fail("unterminated string")

    def escape(self):
        char = self.text[self.pos] if self.pos < len(self.text) else ""
        self.pos += 1
        if char in _ESCAPES:
            return _ESCAPES[char]
        if char == "\n":
            return ""
        if char in ("x", "u"):
            kind = "{" if char == "u" and self.text.startswith("{", self.pos) else char
            match = _HEX[kind].match(self.text, self.pos)
            if not match:
                self.fail("malformed escape")
            self.pos = match.end()
            return chr(int(match.group(1) if kind == "{" else match.group(), 16))
        return char


def _directives(reader):
    """Skip the directive prologue a browser accepts before the assignment: comments, whitespace and "use strict"; statements."""
    while True:
        reader.skip()
        match = _DIRECTIVE.match(reader.text, reader.pos)
        if not match:
            return
        reader.pos = match.end()


def parse(text):
    """(object, error): the value assigned to window.SITREP, or (None, why it could not be read).

    Only comments, whitespace and a "use strict" directive may come before the assignment: any other statement (an `import`, a
    call, a variable) is not read, and a browser may reject it as a whole (an `import` is a syntax error in a classic script).
    """
    if not ASSIGN_RE.search(text):
        return None, "no `window.SITREP =` assignment"
    reader = _Reader(text, 0)
    try:
        _directives(reader)
        match = ASSIGN_RE.match(text, reader.pos)
        if not match:
            reader.fail("unsupported JavaScript before the `window.SITREP =` assignment (only comments and \"use strict\" may precede it)")
        reader.pos = match.end()
        value = reader.value()
        if reader.peek() == ";":
            reader.pos += 1
        if reader.peek():
            reader.fail("unexpected text after the object (only one assignment is read)")
    except DataError as error:
        return None, str(error)
    except RecursionError:
        return None, "nested too deeply"
    return value, ""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _check_item(item, where, ids):
    problems = []
    if not isinstance(item, dict):
        return [f"{where} is not an object"]
    if not _text(item.get("id")):
        problems.append(f"{where}: id must be a non-empty string")
    elif item["id"] in ids:
        problems.append(f"{where}: duplicate id {item['id']!r}")
    else:
        ids.add(item["id"])
    if not _text(item.get("title")):
        problems.append(f"{where}: title must be a non-empty string")
    for key, allowed in (("type", TYPES), ("status", STATUSES)):
        if key in item and item[key] not in allowed:
            problems.append(f"{where}: {key} {item[key]!r} is not one of {', '.join(allowed)}")
    for key in ("impact", "urgency"):
        if key in item and not (_is_int(item[key]) and 1 <= item[key] <= 3):
            problems.append(f"{where}: {key} {item[key]!r} is not an integer from 1 to 3")
    for key in ("dependsOn", "options"):
        if key in item and not (isinstance(item[key], list) and all(isinstance(v, str) for v in item[key])):
            problems.append(f"{where}: {key} must be a list of strings")
    for key in ("detail", "why", "next", "owner"):
        if key in item and not isinstance(item[key], str):
            problems.append(f"{where}: {key} must be a string")
    return problems


def validate(data):
    """The list of ways `data` departs from schema.md (empty when it follows it)."""
    if not isinstance(data, dict):
        return ["the value is not an object"]
    problems = []
    if not _text(data.get("title")):
        problems.append("title must be a non-empty string")
    for key in ("summary", "generated"):
        if key in data and not isinstance(data[key], str):
            problems.append(f"{key} must be a string")
    items = data.get("items")
    if not isinstance(items, list):
        problems.append("items must be a list")
    else:
        ids = set()
        for number, item in enumerate(items, start=1):
            problems += _check_item(item, f"item {number}", ids)
    if "next" in data:
        nxt = data["next"]
        if not (isinstance(nxt, dict) and _text(nxt.get("title")) and isinstance(nxt.get("why", ""), str)):
            problems.append("next must be an object with a title and an optional why")
    if "milestones" in data:
        ms = data["milestones"]
        counts = isinstance(ms, dict) and all(_is_int(ms.get(k)) and ms[k] >= 0 for k in ("done", "total"))
        if not (counts and ms["done"] <= ms["total"]):
            problems.append("milestones must be an object with integer done <= total")
        elif not all(isinstance(ms.get(k, ""), str) for k in ("title", "current")) or not all(_is_int(ms.get(k, 0)) for k in ("needsYou", "blocked")):
            problems.append("milestones: title and current must be strings, needsYou and blocked integers")
    return problems
