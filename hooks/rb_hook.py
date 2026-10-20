"""ResponseBoard hooks for Claude Code (the Claude adapter; the core is src/responseboard).

  rb_hook.py prompt   (UserPromptSubmit) records when the turn started.
  rb_hook.py stop     (Stop) checks that .responseboard/status.json was written
                      during this turn and is valid. If not, it blocks the stop
                      once and tells Claude what to fix. On success the run is
                      added to the history database and status.json "history"
                      is filled from it.

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

from responseboard import status as st  # noqa: E402
from responseboard import store  # noqa: E402


def project_root(payload: dict) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())


def emit(obj: dict) -> None:
    # ASCII-escaped so the hook output survives any console code page.
    print(json.dumps(obj, ensure_ascii=True))


def block(reason: str) -> None:
    emit({"decision": "block", "reason": reason})


def cmd_prompt(payload: dict) -> None:
    bd = project_root(payload) / ".responseboard"
    bd.mkdir(exist_ok=True)
    st.write_json(bd / "turn.json", {"started_at": time.time(), "session_id": payload.get("session_id")})


def cmd_stop(payload: dict) -> None:
    if payload.get("stop_hook_active"):
        return  # already asked once this turn; never loop
    root = project_root(payload)
    turn = root / ".responseboard" / "turn.json"
    if not turn.exists():
        return  # no turn start recorded (hook added mid-session); nothing to compare
    started = float(st.read_json(turn).get("started_at") or 0)
    path = st.status_path(root)
    how = ("Before finishing, write .responseboard/status.json for this turn "
           f"(shape: {RB_ROOT / 'schema' / 'status.schema.json'}, example: {RB_ROOT / 'samples' / 'status.sample.json'}; "
           "rules in CLAUDE.md). ").replace("\\", "/")
    if not path.exists():
        return block(how + "The file does not exist yet.")
    # 2 s slack: on Windows the file time and time.time() can disagree by a few ms to a tick.
    if path.stat().st_mtime < started - 2:
        return block(how + "The file was not updated during this turn.")
    try:
        errs, _ = store.record_file(root, default_writer="llm")
    except sqlite3.Error as e:
        # The run itself is fine; only the history could not be saved. Do not block the user.
        return emit({"systemMessage": f"ResponseBoard: history database error ({e}); status.json was not recorded."})
    if errs:
        return block(how + "Fix: " + "; ".join(errs))


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8") or "{}"
    payload = json.loads(raw)
    {"prompt": cmd_prompt, "stop": cmd_stop}[sys.argv[1]](payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
