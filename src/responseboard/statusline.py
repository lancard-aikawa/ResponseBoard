"""One line for a terminal status bar, built from .responseboard/status.json.

  ▲ あなたの番 2件 │ 手元のボードを作った │ ■■■□ 3/4 │ +120 −8 │ 5分前

If a turn started after status.json was last written (.responseboard/turn.json),
the line says the work is in progress and shows the previous result dimmed.
Reads files only (no database), so it stays fast enough to run on every refresh.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from . import status as st

RESET, DIM, GREEN, RED = "\033[0m", "\033[2m", "\033[32m", "\033[31m"
COLOR = {"done": "\033[32m", "needs_you": "\033[33m", "failed": "\033[31m", "running": "\033[34m"}
LABEL = {"done": "● 完了", "needs_you": "▲ あなたの番", "failed": "✕ 失敗", "running": "◐ 実行中"}
SEP = " │ "
TITLE_MAX = 30


def ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return "たった今"
    if s < 3600:
        return f"{s // 60}分前"
    if s < 86400:
        return f"{s // 3600}時間前"
    return f"{s // 86400}日前"


def elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    return "" if s < 60 else f" {s // 60}分" if s < 3600 else f" {s // 3600}時間{s % 3600 // 60}分"


def clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def line(root: Path, color: bool = True, now: float | None = None) -> str:
    now = time.time() if now is None else now
    c = (lambda code, s: f"{code}{s}{RESET}") if color else (lambda code, s: s)
    path = st.status_path(root)
    try:
        d = st.read_json(path)
        run = d["run"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return c(DIM, "ResponseBoard: 記録なし")

    try:
        finished = datetime.fromisoformat(run["finished_at"]).timestamp()
    except (KeyError, ValueError):
        finished = path.stat().st_mtime
    state = run.get("status", "done")
    asks = len(run.get("asks") or [])
    head = LABEL.get(state, state) + (f" {asks}件" if asks else "")
    parts = [c(COLOR.get(state, ""), head), clip(run.get("title", ""), TITLE_MAX)]

    steps = (d.get("section") or {}).get("steps") or []
    if steps:
        done = sum(1 for s in steps if s.get("state") == "done")
        parts.append("■" * done + "□" * (len(steps) - done) + f" {done}/{len(steps)}")
    g = d.get("git") or {}
    if g.get("files") and g.get("run") == run.get("id"):  # rb wrote it for this very run
        parts.append(c(GREEN, f"+{g.get('add', 0)}") + " " + c(RED, f"−{g.get('del', 0)}"))
    failed = sum(1 for x in run.get("checks") or [] if x.get("result") == "fail")
    if failed:
        parts.append(c(COLOR["failed"], f"✕ 確認 {failed}"))
    parts.append(ago(now - finished))
    text = SEP.join(parts)

    try:
        started = float(st.read_json(root / ".responseboard" / "turn.json").get("started_at") or 0)
    except (FileNotFoundError, ValueError, AttributeError):
        started = 0
    if started > path.stat().st_mtime:
        prev = SEP.join([head, clip(run.get("title", ""), TITLE_MAX), ago(now - finished)])
        return c(COLOR["running"], f"◐ 作業中{elapsed(now - started)}") + SEP + c(DIM, "前回: " + prev)
    return text
