"""Reads one turn of a Claude Code transcript (.jsonl) into a talk (see src/responseboard/talk.py).

Kept: what the user wrote, what Claude wrote, one line per tool call, and the path of each
file change. Left out: thinking, tool output, the text of file changes (the git diff has it),
system reminders, and sub-agents.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TOOL_LINE = 200
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


def _epoch(ts: str | None) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]


def _rel(path: str, root: Path | None) -> str:
    p = str(path).replace("\\", "/")
    if root:
        r = str(root).replace("\\", "/").rstrip("/") + "/"
        if p.lower().startswith(r.lower()):
            return p[len(r):]
    return p


def _tool_line(name: str, args: dict) -> str:
    for key in ("description", "command", "pattern", "url", "query", "prompt", "file_path", "path", "skill"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            text = " ".join(v.split())
            return f"{name}: {text[:TOOL_LINE]}" + (" …" if len(text) > TOOL_LINE else "")
    return name


def turn_talk(transcript: str | Path, since: float, root: Path | None = None, until: float | None = None) -> list[dict]:
    """The talk of the turn that started at `since` and, if given, ended at `until`
    (epoch seconds; 2 s of slack for clock skew)."""
    try:
        lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [it for t, it in read_items(lines, root) if t >= since - 2 and (until is None or t <= until + 2)]


def read_items(lines: list[str], root: Path | None = None) -> list[tuple[float, dict]]:
    """Every talk item of a transcript with its time (epoch seconds), oldest first."""
    items: list[tuple[float, dict]] = []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        kind = d.get("type")
        if kind not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
            continue
        at = d.get("timestamp")
        t = _epoch(at)
        if t is None:
            continue
        content = (d.get("message") or {}).get("content")
        if kind == "user":
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue
            text = REMINDER.sub("", "\n".join(_texts(content))).strip()
            if text:
                items.append((t, {"role": "user", "text": text, "at": at}))
            continue
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                items.append((t, {"role": "assistant", "text": b["text"], "at": at}))
            elif b.get("type") == "tool_use":
                name, args = b.get("name", "?"), b.get("input") or {}
                if name in EDIT_TOOLS:
                    target = args.get("file_path") or args.get("notebook_path") or "?"
                    items.append((t, {"role": "edit", "text": _rel(target, root), "at": at}))
                else:
                    items.append((t, {"role": "tool", "text": _tool_line(name, args), "at": at}))
    return items
