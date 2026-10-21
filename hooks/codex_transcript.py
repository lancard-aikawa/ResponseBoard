"""Reads one turn of a Codex rollout (.jsonl) into a talk (see src/responseboard/talk.py).

Codex says the transcript format is not stable and transcript_path may be null, so everything
here is best effort: unknown lines are skipped, and the Stop hook falls back to the prompt and
the last message when nothing is found (rb_hook.py).

Kept: what the user wrote, what Codex wrote, one line per tool call, and the path of each file
apply_patch touched. Left out: reasoning, tool output, the patch text (the git diff has it),
and the context blocks Codex adds on the user side (<environment_context> and the like).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from claude_transcript import _rel, _tool_line

# the files a patch touches. Given to apply_patch as is, a path runs to the end of its line.
# Written inside a code-mode script as a string, it ends at an escape such as \n or at the closing quote
# (an escaped \\ goes on).
PATCH_FILE = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", re.M)
PATCH_IN_SCRIPT = re.compile(r'\*\*\* (?:(?:Add|Update|Delete) File|Move to): ((?:[^\n\\"]|\\\\)+)')
EXEC_CMD = re.compile(r'exec_command\(\{\s*cmd:\s*"((?:[^"\\]|\\.)*)"')  # code mode: tools.exec_command({cmd:"..."})
QUIET = {"wait"}  # polling a running script says nothing new
CONTEXT = re.compile(r"^\s*(<([\w -]+)>.*</\2>\s*)+$", re.S)  # a message that is only context blocks
INSTRUCTIONS = "# AGENTS.md instructions"
IDE_HEAD, IDE_ASK = "# Context from my IDE setup:", "## My request for Codex:"  # what the VS Code extension adds


def _epoch(ts: str | None) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _texts(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text"))


def _unescape(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def read_items(lines: list[str], root: Path | None = None) -> list[tuple[float, dict]]:
    """Every talk item of a rollout with its time (epoch seconds), oldest first."""
    items: list[tuple[float, dict]] = []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "response_item":
            continue
        at = d.get("timestamp")
        t = _epoch(at)
        p = d.get("payload") or {}
        if t is None or not isinstance(p, dict):
            continue
        kind = p.get("type")
        if kind == "message" and p.get("role") in ("user", "assistant"):
            text = _texts(p.get("content")).strip()
            if p["role"] == "user" and text.startswith(IDE_HEAD) and IDE_ASK in text:
                text = text.split(IDE_ASK, 1)[1].strip()
            if not text or (p["role"] == "user" and (CONTEXT.match(text) or text.startswith(INSTRUCTIONS))):
                continue
            items.append((t, {"role": p["role"], "text": text, "at": at}))
        elif kind == "custom_tool_call" and p.get("name") in ("apply_patch", "exec"):
            script = p.get("input") if isinstance(p.get("input"), str) else ""
            for m in EXEC_CMD.finditer(script):
                items.append((t, {"role": "tool", "text": _tool_line("exec_command", {"command": _unescape(m.group(1))}), "at": at}))
            paths = ([m.group(1) for m in PATCH_FILE.finditer(script)] if p["name"] == "apply_patch"
                     else [_unescape(m.group(1)) for m in PATCH_IN_SCRIPT.finditer(script)])
            for path in dict.fromkeys(x.strip() for x in paths):
                items.append((t, {"role": "edit", "text": _rel(path, root), "at": at}))
        elif kind in ("function_call", "custom_tool_call", "local_shell_call") and p.get("name") not in QUIET:
            args = _args(p.get("arguments") if "arguments" in p else p.get("action") or {})
            if isinstance(args.get("command"), list):
                args["command"] = " ".join(map(str, args["command"]))
            items.append((t, {"role": "tool", "text": _tool_line(p.get("name") or "shell", args), "at": at}))
    return items


def turn_talk(transcript: str | Path, since: float, root: Path | None = None) -> list[dict]:
    """The talk of the turn that started at `since` (epoch seconds; 2 s of slack for clock skew)."""
    try:
        lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    except (OSError, TypeError):
        return []
    return [it for t, it in read_items(lines, root) if t >= since - 2]
