"""The conversation of one run, in a shape that does not depend on any LLM.

A talk is a list of items, oldest first:
  {"role": "user" | "assistant" | "tool" | "edit" | "notice", "text": str, "at": ISO 8601 (optional)}
user and assistant are what was said. notice is something the tool itself put in the conversation
(a background task finished, a command's output): it reads like the user but was not written by them. tool is one line per tool call (no output).
edit is one line per file change, only the path: the change itself is in the git diff.
Adapters (hooks/claude_transcript.py for Claude Code) build it; store.add_talk saves it.
"""
from __future__ import annotations

ROLES = ("user", "assistant", "tool", "edit", "notice")
ITEM_MAX = 20000
TOTAL_MAX = 200000


def clean(items) -> tuple[list[dict], bool]:
    """Keeps well-formed items, shortens long ones. Returns (items, truncated)."""
    out, total, truncated = [], 0, False
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or it.get("role") not in ROLES or not isinstance(it.get("text"), str):
            continue
        text = it["text"].strip()
        if not text:
            continue
        if len(text) > ITEM_MAX:
            text, truncated = text[:ITEM_MAX] + " …", True
        if total + len(text) > TOTAL_MAX:
            truncated = True
            break
        total += len(text)
        item = {"role": it["role"], "text": text}
        if isinstance(it.get("at"), str):
            item["at"] = it["at"]
        out.append(item)
    return out, truncated
