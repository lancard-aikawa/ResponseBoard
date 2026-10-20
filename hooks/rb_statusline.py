"""Claude Code statusLine adapter: prints the ResponseBoard line for the session's project.

Claude Code passes session JSON on stdin; workspace.project_dir (or cwd) tells which project.
Must never fail loudly: any error prints a short plain line instead.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8") or "{}"
        payload = json.loads(raw)
    except ValueError:
        payload = {}
    ws = payload.get("workspace") or {}
    root = Path(ws.get("project_dir") or os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    try:
        from responseboard.statusline import line
        text = line(root)
    except Exception as e:  # noqa: BLE001 - the status bar must always show something
        text = f"ResponseBoard: {type(e).__name__}"
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
