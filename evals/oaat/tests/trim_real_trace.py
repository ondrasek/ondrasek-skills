"""Trim a REAL trace (artifacts/<id>.jsonl, written by `make eval`) to the events and fields the checks read.

    python3 tests/trim_real_trace.py artifacts/explicit-name.jsonl tests/fixtures/traces/real/explicit-name.jsonl

A real stream carries your working directory, memory paths, session id, the full lists of your tools and skills, and the
skill text as a tool result. This script keeps only what `python/agent_trace.py` reads, so the file is safe to commit:

    system/init   type, subtype, model, claude_code_version, session_id (replaced by REAL-<id>), skills and tools
                  (cut to the entries that the checks look for: the skill under test and the Skill tool)
    assistant     the `tool_use` and `text` blocks of the main conversation (thinking blocks are dropped)
    result        type, subtype, is_error, result, num_turns, total_cost_usd

The reply text and the numbers stay as the model produced them. Read the output before you commit it.
"""
import json
import sys
from pathlib import Path

KEEP_RESULT = ("type", "subtype", "is_error", "result", "num_turns", "total_cost_usd")


def trim(events, name, skill="oaat"):
    session = f"REAL-{name}"
    out = []
    for event in events:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            out.append({
                "type": "system", "subtype": "init", "session_id": session,
                "model": event.get("model"), "claude_code_version": event.get("claude_code_version"),
                "skills": [s for s in event.get("skills", []) if s == skill or s.endswith(":" + skill)],
                "tools": [t for t in event.get("tools", []) if t == "Skill"],
            })
        elif kind == "assistant" and event.get("parent_tool_use_id") is None:
            blocks = []
            for block in event["message"]["content"]:
                if block.get("type") == "text":
                    blocks.append({"type": "text", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    blocks.append({"type": "tool_use", "id": block["id"], "name": block["name"], "input": block["input"]})
            if blocks:
                out.append({"type": "assistant", "session_id": session, "parent_tool_use_id": None,
                            "message": {"role": "assistant", "content": blocks}})
        elif kind == "result":
            out.append({**{k: event[k] for k in KEEP_RESULT if k in event}, "session_id": session})
    return out


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    source, target = Path(argv[1]), Path(argv[2])
    events = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    trimmed = trim(events, target.stem)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in trimmed), encoding="utf-8")
    print(f"{len(events)} events in, {len(trimmed)} events out: {target}")


if __name__ == "__main__":
    main(sys.argv)
