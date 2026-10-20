"""Run history in SQLite.

One database per user (default %USERPROFILE%/.responseboard/runs.db, or the
RESPONSEBOARD_DB environment variable). Each run keeps its whole status.json in
raw_json; the other columns are copies used for searching. When the project is in git,
run_git keeps what that run changed (see gitsnap.py), and run_talk its conversation
(see talk.py), when an adapter passed one.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import gitsnap
from . import status as st
from . import talk as tk

HISTORY_KEEP = 10
LEFT_KEEP = 20  # status.json "left_behind": the newest items nobody has picked up

SCHEMA_V1 = """
CREATE TABLE runs (
  id            INTEGER PRIMARY KEY,
  run_key       TEXT NOT NULL UNIQUE,
  recorded_at   TEXT NOT NULL,
  finished_at   TEXT NOT NULL,
  project       TEXT NOT NULL,
  project_path  TEXT NOT NULL,
  tool          TEXT NOT NULL,
  model         TEXT,
  written_by    TEXT,
  status        TEXT NOT NULL,
  title         TEXT NOT NULL,
  asks_count    INTEGER NOT NULL DEFAULT 0,
  checks_failed INTEGER NOT NULL DEFAULT 0,
  raw_json      TEXT NOT NULL
);
CREATE INDEX runs_project_time ON runs (project_path, finished_at);
CREATE INDEX runs_time ON runs (finished_at);
CREATE INDEX runs_status ON runs (status);
"""

# v2: sections are their own axis (one project has many), and git changes per run.
SCHEMA_V2 = """
ALTER TABLE runs ADD COLUMN section_id TEXT;
ALTER TABLE runs ADD COLUMN section_title TEXT;
CREATE INDEX runs_section ON runs (project_path, section_id);
CREATE TABLE run_git (
  run_id       INTEGER PRIMARY KEY REFERENCES runs (id),
  head         TEXT,
  branch       TEXT,
  tree         TEXT NOT NULL,
  base         TEXT,
  base_kind    TEXT,
  files_json   TEXT NOT NULL,
  commits_json TEXT NOT NULL,
  patch        TEXT NOT NULL,
  truncated    INTEGER NOT NULL DEFAULT 0
);
"""

# v3: the conversation of each run.
SCHEMA_V3 = """
CREATE TABLE run_talk (
  run_id    INTEGER PRIMARY KEY REFERENCES runs (id),
  items     INTEGER NOT NULL,
  talk_json TEXT NOT NULL,
  truncated INTEGER NOT NULL DEFAULT 0
);
"""
VERSION = 3


def _section_of(raw: str) -> tuple[str | None, str | None]:
    try:
        sec = json.loads(raw).get("section")
    except (ValueError, AttributeError):
        return None, None
    return (st.section_id(sec), sec.get("title")) if isinstance(sec, dict) else (None, None)


def db_path() -> Path:
    env = os.environ.get("RESPONSEBOARD_DB")
    return Path(env) if env else Path.home() / ".responseboard" / "runs.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    if con.execute("PRAGMA user_version").fetchone()[0] < VERSION:
        migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> None:
    # One writer at a time, and the version is read again inside the lock, so two sessions
    # opening an old database together do not both run the same ALTER TABLE.
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        for v, script in ((1, SCHEMA_V1), (2, SCHEMA_V2), (3, SCHEMA_V3)):
            if version < v:
                for stmt in filter(str.strip, script.split(";")):
                    con.execute(stmt)
        if version < 2:
            for rid, raw in con.execute("SELECT id, raw_json FROM runs").fetchall():
                con.execute("UPDATE runs SET section_id = ?, section_title = ? WHERE id = ?", (*_section_of(raw), rid))
        con.execute(f"PRAGMA user_version={VERSION}")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.isolation_level = ""


def run_key(d: dict) -> str:
    # Same project + same run content = same key, so a repeated Stop does not add a row.
    body = {"project": d["project"].get("path") or d["project"]["name"], "tool": d["source"]["tool"], "run": d["run"]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def insert(con: sqlite3.Connection, d: dict, raw: str | None = None) -> bool:
    run, src, sec = d["run"], d["source"], d.get("section") or None
    cur = con.execute(
        "INSERT OR IGNORE INTO runs (run_key, recorded_at, finished_at, project, project_path, tool, model,"
        " written_by, status, title, asks_count, checks_failed, raw_json, section_id, section_title)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_key(d), st.now_iso(), run["finished_at"], d["project"]["name"],
         d["project"].get("path") or d["project"]["name"], src["tool"], src.get("model"), src.get("written_by"),
         run["status"], run["title"], len(run.get("asks") or []),
         sum(1 for c in run.get("checks") or [] if c.get("result") == "fail"),
         raw if raw is not None else json.dumps(d, ensure_ascii=False),
         st.section_id(sec), sec.get("title") if sec else None),
    )
    con.commit()
    return cur.rowcount == 1


def _same_run(con: sqlite3.Connection, project_path: str, rid: str | None) -> sqlite3.Row | None:
    if not rid:
        return None
    return con.execute("SELECT id, run_key, recorded_at FROM runs WHERE project_path = ? AND json_extract(raw_json, '$.run.id') = ?"
                       " ORDER BY id DESC LIMIT 1", (project_path, rid)).fetchone()


def upsert(con: sqlite3.Connection, d: dict) -> bool:
    """Add the run, or replace the stored one with the same run.id (a fix of the same run).

    Returns True when something was written. The same content twice writes nothing.
    """
    old = _same_run(con, d["project"].get("path") or d["project"]["name"], d["run"].get("id"))
    if old is None:
        return insert(con, d)
    key = run_key(d)
    if old["run_key"] == key:
        return False
    run, src, sec = d["run"], d["source"], d.get("section") or None
    con.execute(
        "UPDATE runs SET run_key=?, recorded_at=?, finished_at=?, project=?, tool=?, model=?, written_by=?, status=?, title=?,"
        " asks_count=?, checks_failed=?, raw_json=?, section_id=?, section_title=? WHERE id=?",
        (key, st.now_iso(), run["finished_at"], d["project"]["name"], src["tool"], src.get("model"), src.get("written_by"),
         run["status"], run["title"], len(run.get("asks") or []),
         sum(1 for c in run.get("checks") or [] if c.get("result") == "fail"),
         json.dumps(d, ensure_ascii=False), st.section_id(sec), sec.get("title") if sec else None, old["id"]))
    con.commit()
    return True


def _epoch(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


def new_id() -> str:
    # microseconds: two runs recorded within one second must not share an id (the same id is the same run)
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def stamp(d: dict, turn_started: float | None = None, run_id: str | None = None) -> None:
    """rb, not the LLM, decides when a run finished and which run it is.

    An LLM does not know the time; its finished_at was off by hours in real use. With the turn start
    known (the Claude hook), a time outside this turn is replaced by now. run_id is the id the hook keeps
    for this turn: the LLM's own id is not used then, because it is often copied from the last run.
    """
    run, now = d["run"], time.time()
    fin = _epoch(run.get("finished_at"))
    if fin is None or (turn_started is not None and not turn_started - 2 <= fin <= now + 60):
        run["finished_at"] = st.now_iso()
    if run_id:
        run["id"] = run_id
    elif not run.get("id"):
        run["id"] = new_id()


def query(con: sqlite3.Connection, project_path: str | None = None, status: str | None = None,
          tool: str | None = None, limit: int = 20, exclude_key: str | None = None) -> list[sqlite3.Row]:
    where, args = [], []
    for col, val in (("project_path", project_path), ("status", status), ("tool", tool)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if exclude_key:
        where.append("run_key <> ?")
        args.append(exclude_key)
    sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
    rows = con.execute(sql + " ORDER BY finished_at DESC, id DESC LIMIT ?", (*args, limit)).fetchall()
    return rows[::-1]  # oldest first


def record_file(root: Path, default_writer: str = "llm", talk: list | None = None,
                turn_started: float | None = None, run_id: str | None = None) -> tuple[list[str], bool]:
    """Check root/.responseboard/status.json, add it to the database and fill its history.
    talk (see talk.py) is saved with the run when it is new or changed. turn_started (epoch) is when
    the user's turn began and run_id the id of this turn's run, if the caller keeps them; see stamp().

    Returns (errors, inserted). Nothing is written when errors is not empty.
    """
    path = st.status_path(root)
    try:
        d = st.read_json(path)
    except FileNotFoundError:
        return [f"{path} does not exist"], False
    except ValueError as e:
        return [f"{path} is not valid JSON: {e}"], False
    errs = st.validate(d)
    if errs:
        return errs, False
    d.pop("history", None)
    d.pop("git", None)
    d["project"]["path"] = st.norm_path(d["project"].get("path") or root)
    d["source"].setdefault("written_by", default_writer)
    con = connect()
    try:
        stamp(d, turn_started, run_id)
        inserted = upsert(con, d)
        key = run_key(d)
        if inserted:
            add_git(con, root, key)
            if talk:
                add_talk(con, key, talk)
        g = git_summary(con, key)
        if g:
            d["git"] = {"run": d["run"]["id"], **g}
        d["history"] = [
            {"finished_at": r["finished_at"], "status": r["status"], "title": r["title"], "tool": r["tool"]}
            for r in query(con, project_path=d["project"]["path"], limit=HISTORY_KEEP, exclude_key=key)
        ]
        left = [x for x in leftovers(con, d["project"]["path"]) if x["state"] == "left"]
        d["left_behind"] = [{k: x[k] for k in ("title", "reason", "finished_at", "section_id") if x.get(k)}
                            for x in left[-LEFT_KEEP:]]
    finally:
        con.close()
    st.write_json(path, d)
    return [], inserted


def add_git(con: sqlite3.Connection, root: Path, key: str) -> bool:
    """Save what the run with this key changed in git. False when the project is not in git."""
    snap = gitsnap.snapshot(root)
    if not snap:
        return False
    rid, path = con.execute("SELECT id, project_path FROM runs WHERE run_key = ?", (key,)).fetchone()
    prev = con.execute("SELECT g.tree, g.head FROM run_git g JOIN runs r ON r.id = g.run_id"
                       " WHERE r.project_path = ? AND g.run_id < ? ORDER BY g.run_id DESC LIMIT 1", (path, rid)).fetchone()
    g = gitsnap.diff(snap, dict(prev) if prev else None)
    con.execute("INSERT OR REPLACE INTO run_git (run_id, head, branch, tree, base, base_kind, files_json, commits_json,"
                " patch, truncated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, g["head"], g["branch"], g["tree"], g["base"], g["base_kind"],
                 json.dumps(g["files"], ensure_ascii=False), json.dumps(g["commits"], ensure_ascii=False),
                 g["patch"], int(g["truncated"])))
    con.commit()
    return True


def git_summary(con: sqlite3.Connection, key: str) -> dict | None:
    """How many files and lines the run with this key changed in git, or None without git."""
    r = con.execute("SELECT g.files_json FROM run_git g JOIN runs r ON r.id = g.run_id WHERE r.run_key = ?", (key,)).fetchone()
    if not r:
        return None
    files = json.loads(r[0])
    return {"files": len(files), "add": sum(f.get("add") or 0 for f in files), "del": sum(f.get("del") or 0 for f in files)}


def add_talk(con: sqlite3.Connection, key: str, items: list) -> int:
    """Save the conversation of the run with this key. Returns how many items were kept."""
    kept, truncated = tk.clean(items)
    if not kept:
        return 0
    rid = con.execute("SELECT id FROM runs WHERE run_key = ?", (key,)).fetchone()[0]
    con.execute("INSERT OR REPLACE INTO run_talk (run_id, items, talk_json, truncated) VALUES (?,?,?,?)",
                (rid, len(kept), json.dumps(kept, ensure_ascii=False), int(truncated)))
    con.commit()
    return len(kept)


def _dest(x: dict) -> str:
    return "task" if x.get("task") else "drop" if x.get("drop") else "left"


SKIPPED_NEXT = "前の回の「次にやる事」。この回では扱われなかった"


def skipped_next(prev: dict, cur: dict) -> dict | None:
    """prev's run.next as a left-behind item, when cur neither did it, kept it as next, nor said where it went."""
    nxt = (prev.get("run") or {}).get("next") or {}
    title = str(nxt.get("title") or "")[:60]
    if not title:
        return None
    run = cur.get("run") or {}
    if run.get("from_next"):  # the run says it took up the previous next
        return None
    for other in (run.get("title"), (run.get("next") or {}).get("title")):
        if other and (title in other or other in title):
            return None
    if any(isinstance(x, dict) and x.get("title") == title for x in run.get("not_done") or []):
        return None
    tid = nxt.get("task")
    if tid:
        col = lambda d: next((t.get("column") for t in d.get("tasks") or [] if isinstance(t, dict) and t.get("id") == tid), None)
        if (run.get("next") or {}).get("task") == tid or col(prev) != col(cur):
            return None
    return {"title": title, "reason": SKIPPED_NEXT, "auto": True}


def with_skipped(raws: list[dict]) -> list[list[dict]]:
    """not_done of each run (oldest first), with the skipped next of the run before added."""
    out, prev = [], None
    for d in raws:
        nd = [x for x in (d.get("run") or {}).get("not_done") or [] if isinstance(x, dict) and x.get("title")]
        auto = skipped_next(prev, d) if prev and "run" in prev else None
        out.append(nd + ([auto] if auto else []))
        prev = d
    return out


def leftovers(con: sqlite3.Connection, project_path: str) -> list[dict]:
    """Every not_done item of a project, oldest first, with where it went (state task / drop / left).

    The same title written again in a later run is the same item, and the later word wins: this is how
    a left-behind item gets picked up (written again with task or drop).
    """
    rows = con.execute("SELECT id, finished_at, title, section_id, raw_json FROM runs WHERE project_path = ?"
                       " ORDER BY finished_at, id", (project_path,)).fetchall()
    by_title: dict[str, dict] = {}
    raws = []
    for r in rows:
        try:
            raws.append(json.loads(r["raw_json"]))
        except ValueError:
            raws.append({})
    for r, nd in zip(rows, with_skipped(raws)):
        for x in nd:
            t = str(x["title"])
            first = by_title.pop(t, {}).get("first_run", r["id"])
            by_title[t] = {"title": t, "reason": x.get("reason"), "state": _dest(x), "task": x.get("task"),
                           "run_id": r["id"], "first_run": first, "finished_at": r["finished_at"],
                           "run_title": r["title"], "section_id": r["section_id"]}
    return list(by_title.values())


def _light(raw: str) -> dict:
    """The parts of a stored status.json that the history axis needs for cross-filtering."""
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    run = d.get("run") or {}
    return {"tasks": {t["id"]: t.get("column") for t in d.get("tasks") or [] if isinstance(t, dict) and t.get("id")},
            "next_task": (run.get("next") or {}).get("task"), "summary": run.get("summary"),
            "not_done": [{k: x[k] for k in ("title", "reason", "task", "drop") if x.get(k)}
                         for x in run.get("not_done") or [] if isinstance(x, dict) and x.get("title")]}


def overview(con: sqlite3.Connection, project_path: str, limit: int = 500) -> list[dict]:
    """Runs of one project, newest first, with section, task columns and git totals for the board."""
    rows = con.execute(
        "SELECT r.id, r.finished_at, r.status, r.title, r.tool, r.written_by, r.asks_count, r.checks_failed,"
        " r.section_id, r.section_title, r.raw_json, g.files_json, t.items AS talk FROM runs r"
        " LEFT JOIN run_git g ON g.run_id = r.id LEFT JOIN run_talk t ON t.run_id = r.id"
        " WHERE r.project_path = ? ORDER BY r.finished_at DESC, r.id DESC LIMIT ?", (project_path, limit)).fetchall()
    out = []
    raws = []
    for r in rows:
        try:
            raws.append(json.loads(r["raw_json"]))
        except ValueError:
            raws.append({})
    left = with_skipped(raws[::-1])[::-1]  # the skipped next is found oldest first
    for r, nd in zip(rows, left):
        item = {k: r[k] for k in r.keys() if k not in ("raw_json", "files_json", "talk")}
        if r["talk"]:
            item["talk"] = r["talk"]
        item.update(_light(r["raw_json"]))
        item["not_done"] = [{k: x[k] for k in ("title", "reason", "task", "drop", "auto") if x.get(k)} for x in nd]
        if r["files_json"] is not None:
            files = json.loads(r["files_json"])
            item["git"] = {"files": len(files), "add": sum(f["add"] or 0 for f in files),
                           "del": sum(f["del"] or 0 for f in files)}
        out.append(item)
    return out


def get_run(con: sqlite3.Connection, project_path: str, rid: int) -> dict | None:
    """One stored run: its whole status.json as recorded and its git change (without the patch text)."""
    r = con.execute("SELECT raw_json FROM runs WHERE id = ? AND project_path = ?", (rid, project_path)).fetchone()
    if not r:
        return None
    g = con.execute("SELECT head, branch, base_kind, files_json, commits_json, truncated FROM run_git WHERE run_id = ?",
                    (rid,)).fetchone()
    git = None
    if g:
        git = {"head": g["head"], "branch": g["branch"], "base_kind": g["base_kind"],
               "files": json.loads(g["files_json"]), "commits": json.loads(g["commits_json"]),
               "truncated": bool(g["truncated"])}
    return {"id": rid, "status": json.loads(r["raw_json"]), "git": git}


def get_talk(con: sqlite3.Connection, project_path: str, rid: int) -> dict | None:
    r = con.execute("SELECT t.talk_json, t.truncated FROM run_talk t JOIN runs r ON r.id = t.run_id"
                    " WHERE r.id = ? AND r.project_path = ?", (rid, project_path)).fetchone()
    return {"items": json.loads(r[0]), "truncated": bool(r[1])} if r else None


def get_patch(con: sqlite3.Connection, project_path: str, rid: int) -> str | None:
    r = con.execute("SELECT g.patch FROM run_git g JOIN runs r ON r.id = g.run_id WHERE r.id = ? AND r.project_path = ?",
                    (rid, project_path)).fetchone()
    return r[0] if r else None
