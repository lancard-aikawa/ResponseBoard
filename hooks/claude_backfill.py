"""Fills in the conversation of runs recorded without one, from Claude Code transcripts.

  python hooks/claude_backfill.py [--project PATH] [--apply] [--max-hours 6] [--transcripts DIR]

Runs recorded before the Stop hook kept conversations (or by "rb done") have no talk. A run's
turn is found by time: what was said after the run before it was recorded, up to when this run
was recorded. recorded_at is rb's own clock, unlike finished_at, which older runs had guessed.
The window is at most --max-hours long. A project's first run has no run before it, so it takes only
the last turn (from the last thing the user said).

Without --apply it only lists what it would add. Runs that already have a talk are left alone.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RB_ROOT / "src"))

from claude_transcript import read_items  # noqa: E402
from responseboard import store  # noqa: E402


def transcripts_dir(project_path: str, base: Path | None = None) -> Path | None:
    """Claude Code keeps a project's transcripts in ~/.claude/projects/<path with every non-alphanumeric as ->."""
    base = base or Path.home() / ".claude" / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", project_path).lower()
    try:
        return next((d for d in base.iterdir() if d.is_dir() and d.name.lower() == slug), None)
    except OSError:
        return None


def all_items(folder: Path, root: Path) -> list[tuple[float, dict]]:
    items = []
    for f in sorted(folder.glob("*.jsonl")):  # sub-agent transcripts live in subfolders and are left out
        try:
            items += read_items(f.read_text(encoding="utf-8").splitlines(), root)
        except OSError:
            continue
    return sorted(items, key=lambda x: x[0])


def plan(con, project_path: str, items: list[tuple[float, dict]], max_hours: float) -> list[dict]:
    """The runs without a talk, each with the items said in its window."""
    rows = con.execute("SELECT r.id, r.run_key, r.recorded_at, r.title, t.run_id IS NOT NULL AS has_talk FROM runs r"
                       " LEFT JOIN run_talk t ON t.run_id = r.id WHERE r.project_path = ? ORDER BY r.recorded_at, r.id",
                       (project_path,)).fetchall()
    out, prev = [], None
    for r in rows:
        end = store._epoch(r["recorded_at"])
        if end is None:
            continue
        if not r["has_talk"]:
            start = max(prev or 0, end - max_hours * 3600)
            talk = [(t, it) for t, it in items if start < t <= end + 2]
            if prev is None:
                # the project's first run: the hook came in during a session, so only its last turn is this run
                last = max((t for t, it in talk if it["role"] == "user"), default=None)
                talk = [(t, it) for t, it in talk if last is not None and t >= last]
            talk = [it for _, it in talk]
            if any(it["role"] in ("user", "assistant") for it in talk):
                out.append({"id": r["id"], "key": r["run_key"], "recorded_at": r["recorded_at"], "title": r["title"], "talk": talk})
        prev = end
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", help="project path as recorded (default: every project)")
    ap.add_argument("--apply", action="store_true", help="save the talks (default: only list them)")
    ap.add_argument("--max-hours", type=float, default=6.0)
    ap.add_argument("--transcripts", type=Path, help="folder that holds the per-project transcript folders")
    a = ap.parse_args(argv)
    con = store.connect()
    try:
        projects = [a.project.replace("\\", "/")] if a.project else [r[0] for r in con.execute("SELECT DISTINCT project_path FROM runs")]
        added = 0
        for p in projects:
            folder = transcripts_dir(p, a.transcripts)
            if not folder:
                print(f"{p}: no transcripts")
                continue
            found = plan(con, p, all_items(folder, Path(p)), a.max_hours)
            print(f"{p}: {len(found)} run(s) to fill")
            for x in found:
                says = sum(1 for it in x["talk"] if it["role"] in ("user", "assistant"))
                print(f"  #{x['id']} {x['recorded_at']} {x['title']}  ({len(x['talk'])} items, {says} said)")
                if a.apply:
                    added += store.add_talk(con, x["key"], x["talk"]) > 0
        if a.apply:
            print(f"saved {added} talk(s)")
        else:
            print("nothing saved; add --apply to save")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
