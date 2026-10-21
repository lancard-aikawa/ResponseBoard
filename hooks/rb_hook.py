"""ResponseBoard hooks for Claude Code and Codex (the adapters; the core is src/responseboard).

  rb_hook.py session  (SessionStart) gives Claude the rules, unless CLAUDE.md already has them.
  rb_hook.py prompt   (UserPromptSubmit) records when the turn started.
  rb_hook.py stop     (Stop) checks that .responseboard/status.json was written
                      during this turn and is valid. If not, it blocks the stop
                      once and tells Claude what to fix; the Stop after the fix
                      records it, or only warns if it is still wrong. On success the run is
                      added to the history database and status.json "history"
                      is filled from it.

All of them do nothing in a folder on the deny list (src/responseboard/ignore.py).
"--global" marks the hooks installed for every folder: they also stand aside when the
project has its own ResponseBoard hooks, so nothing runs twice.
"--tool codex" runs them for Codex, whose hooks take the same payload and answers: the rules
go to AGENTS.md instead of CLAUDE.md, and the talk comes from a Codex rollout (codex_transcript.py),
or, when there is none, from the prompt and the last message.

Standard library only. Reads the hook payload as JSON on stdin.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

RB_ROOT = Path(__file__).resolve().parent.parent  # the ResponseBoard checkout, wherever the project is
sys.path.insert(0, str(RB_ROOT / "src"))

import claude_transcript  # noqa: E402
import codex_transcript  # noqa: E402
from claude_rules import START, rules  # noqa: E402
from responseboard import ignore  # noqa: E402
from responseboard import status as st  # noqa: E402
from responseboard import store  # noqa: E402


# per tool: the file that holds the rules, the reader of its transcript, the files where a project sets its own hooks
TOOLS = {
    "claude-code": {"rules": "CLAUDE.md", "talk": claude_transcript.turn_talk,
                    "own": (".claude/settings.json", ".claude/settings.local.json")},
    "codex": {"rules": "AGENTS.md", "talk": codex_transcript.turn_talk,
              "own": (".codex/hooks.json", ".codex/config.toml")},
}
TOOL = "claude-code"


def project_root(payload: dict) -> Path:
    # CLAUDE_PROJECT_DIR can be left over in the environment of a Codex started from a Claude Code terminal
    claude = os.environ.get("CLAUDE_PROJECT_DIR") if TOOL == "claude-code" else None
    return Path(claude or payload.get("cwd") or os.getcwd())


def emit(obj: dict) -> None:
    # ASCII-escaped so the hook output survives any console code page.
    print(json.dumps(obj, ensure_ascii=True))


def block(reason: str) -> None:
    emit({"decision": "block", "reason": reason})


def skipped(root: Path, is_global: bool) -> bool:
    if ignore.match(root):
        return True
    if not is_global:
        return False
    for name in TOOLS[TOOL]["own"]:
        f = root / name
        try:
            if "rb_hook.py" in f.read_text(encoding="utf-8"):
                return True  # the project's own hooks do the work
        except OSError:
            pass
    return False


def cmd_session(payload: dict) -> None:
    try:
        if START in (project_root(payload) / TOOLS[TOOL]["rules"]).read_text(encoding="utf-8"):
            return
    except OSError:
        pass
    emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": rules(TOOL)}})


def cmd_prompt(payload: dict) -> None:
    bd = project_root(payload) / ".responseboard"
    if not bd.exists():
        bd.mkdir()
        # git leaves the folder alone without touching the project's own .gitignore
        (bd / ".gitignore").write_text("*\n", encoding="utf-8", newline="\n")
    turn = {"started_at": time.time(), "session_id": payload.get("session_id")}
    if TOOL == "codex" and isinstance(payload.get("prompt"), str):
        turn["prompt"] = payload["prompt"]  # the talk to fall back on when there is no transcript
    st.write_json(bd / "turn.json", turn)


def cmd_stop(payload: dict) -> None:
    # Already asked once this turn: never ask again (no loop), but still record the fixed file.
    # Returning early here lost every run that had been sent back once (found 2026-10-21).
    again = bool(payload.get("stop_hook_active"))

    def refuse(reason: str) -> None:
        if again:
            emit({"systemMessage": "ResponseBoard: status.json was not recorded. " + reason})
        else:
            block(how + reason)

    root = project_root(payload)
    turn = root / ".responseboard" / "turn.json"
    if not turn.exists():
        return  # no turn start recorded (hook added mid-session); nothing to compare
    tj = st.read_json(turn)
    started = float(tj.get("started_at") or 0)
    path = st.status_path(root)
    how = ("Before finishing, write .responseboard/status.json for this turn "
           f"(shape: {RB_ROOT / 'schema' / 'status.schema.json'}, example: {RB_ROOT / 'samples' / 'status.sample.json'}; "
           "rules in CLAUDE.md or given at session start). ").replace("\\", "/")
    if not path.exists():
        return refuse("The file does not exist yet.")
    # 2 s slack: on Windows the file time and time.time() can disagree by a few ms to a tick.
    if path.stat().st_mtime < started - 2:
        return refuse("The file was not updated during this turn.")
    # one id per turn: a second Stop in the same turn (after a fix) replaces that run instead of adding one
    rid = tj.get("run_id") or store.new_id()
    try:
        talk = TOOLS[TOOL]["talk"](payload["transcript_path"], started, root) if payload.get("transcript_path") else None
        if not talk and TOOL == "codex":
            talk = [{"role": r, "text": t} for r, t in (("user", tj.get("prompt")), ("assistant", payload.get("last_assistant_message")))
                    if isinstance(t, str) and t.strip()] or None
        errs, _ = store.record_file(root, default_writer="llm", talk=talk, turn_started=started, run_id=rid)
        if not errs and tj.get("run_id") != rid:
            st.write_json(turn, {**tj, "run_id": rid})
    except sqlite3.Error as e:
        # The run itself is fine; only the history could not be saved. Do not block the user.
        return emit({"systemMessage": f"ResponseBoard: history database error ({e}); status.json was not recorded."})
    if errs:
        return refuse("Fix: " + "; ".join(errs))


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8") or "{}"
    payload = json.loads(raw)
    global TOOL
    if "--tool" in sys.argv[2:]:
        TOOL = sys.argv[sys.argv.index("--tool") + 1]
    if skipped(project_root(payload), "--global" in sys.argv[2:]):
        return 0
    {"session": cmd_session, "prompt": cmd_prompt, "stop": cmd_stop}[sys.argv[1]](payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
