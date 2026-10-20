"""Run history in SQLite.

One database per user (default %USERPROFILE%/.responseboard/runs.db, or the
RESPONSEBOARD_DB environment variable). Each run keeps its whole status.json in
raw_json; the other columns are copies used for searching. When the project is in git,
run_git keeps what that run changed (see gitsnap.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from . import gitsnap
from . import status as st

HISTORY_KEEP = 10

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
    if con.execute("PRAGMA user_version").fetchone()[0] < 2:
        migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> None:
    # One writer at a time, and the version is read again inside the lock, so two sessions
    # opening an old database together do not both run the same ALTER TABLE.
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        for v, script in ((1, SCHEMA_V1), (2, SCHEMA_V2)):
            if version < v:
                for stmt in filter(str.strip, script.split(";")):
                    con.execute(stmt)
        if version < 2:
            for rid, raw in con.execute("SELECT id, raw_json FROM runs").fetchall():
                con.execute("UPDATE runs SET section_id = ?, section_title = ? WHERE id = ?", (*_section_of(raw), rid))
        con.execute("PRAGMA user_version=2")
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


def record_file(root: Path, default_writer: str = "llm") -> tuple[list[str], bool]:
    """Check root/.responseboard/status.json, add it to the database and fill its history.

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
    d["project"]["path"] = st.norm_path(d["project"].get("path") or root)
    d["source"].setdefault("written_by", default_writer)
    con = connect()
    try:
        inserted = insert(con, d)
        key = run_key(d)
        if inserted:
            add_git(con, root, key)
        d["history"] = [
            {"finished_at": r["finished_at"], "status": r["status"], "title": r["title"], "tool": r["tool"]}
            for r in query(con, project_path=d["project"]["path"], limit=HISTORY_KEEP, exclude_key=key)
        ]
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


def _light(raw: str) -> dict:
    """The parts of a stored status.json that the history axis needs for cross-filtering."""
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    run = d.get("run") or {}
    return {"tasks": {t["id"]: t.get("column") for t in d.get("tasks") or [] if isinstance(t, dict) and t.get("id")},
            "next_task": (run.get("next") or {}).get("task"), "summary": run.get("summary")}


def overview(con: sqlite3.Connection, project_path: str, limit: int = 500) -> list[dict]:
    """Runs of one project, newest first, with section, task columns and git totals for the board."""
    rows = con.execute(
        "SELECT r.id, r.finished_at, r.status, r.title, r.tool, r.written_by, r.asks_count, r.checks_failed,"
        " r.section_id, r.section_title, r.raw_json, g.files_json FROM runs r LEFT JOIN run_git g ON g.run_id = r.id"
        " WHERE r.project_path = ? ORDER BY r.finished_at DESC, r.id DESC LIMIT ?", (project_path, limit)).fetchall()
    out = []
    for r in rows:
        item = {k: r[k] for k in r.keys() if k not in ("raw_json", "files_json")}
        item.update(_light(r["raw_json"]))
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


def get_patch(con: sqlite3.Connection, project_path: str, rid: int) -> str | None:
    r = con.execute("SELECT g.patch FROM run_git g JOIN runs r ON r.id = g.run_id WHERE r.id = ? AND r.project_path = ?",
                    (rid, project_path)).fetchone()
    return r[0] if r else None
