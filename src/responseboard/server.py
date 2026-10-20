"""rb serve: the board in a local browser, reading runs.db and each project's status.json.

Listens on 127.0.0.1 only. A status.json is read only for projects that are in
the history database, so the server cannot be used to read other files.

  GET /                      the board page
  GET /api/projects          one row per project with its latest run
  GET /api/status?path=P     that project's status.json (latest run from the database if the file is gone)
  GET /api/runs?path=P&status=S&limit=N
  GET /api/overview?path=P   runs newest first, with section, task columns and git totals (the history axis)
  GET /api/run?path=P&id=N   one run's status.json as recorded, and its git change
  GET /api/diff?path=P&id=N  that run's git patch (text)
  GET /api/talk?path=P&id=N  that run's conversation {items, truncated}
  GET /api/rev?path=P        a short string that changes when the project's data changes
"""
from __future__ import annotations

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import status as st
from . import store

DEFAULT_PORT = 28000  # reserved in LPortMan
BOARD = Path(__file__).resolve().parents[2] / "board" / "responseboard.html"

PROJECTS_SQL = """
SELECT r.project, r.project_path, r.finished_at, r.status, r.title, r.tool, c.runs, c.failed
FROM runs r
JOIN (SELECT project_path, MAX(id) AS last_id, COUNT(*) AS runs,
             SUM(status = 'failed') AS failed FROM runs GROUP BY project_path) c
  ON r.id = c.last_id
ORDER BY r.finished_at DESC
"""


def page_html() -> bytes:
    # The page is wrapped in a skeleton by the Artifact publisher; add one here for a plain browser.
    body = BOARD.read_text(encoding="utf-8")
    return ("<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head><body>"
            + body + "</body></html>").encode("utf-8")


def known_paths(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT DISTINCT project_path FROM runs")}


def project_status(con, path: str) -> dict | None:
    f = Path(path) / ".responseboard" / "status.json"
    try:
        return st.read_json(f)
    except (FileNotFoundError, ValueError):
        row = con.execute("SELECT raw_json FROM runs WHERE project_path = ? ORDER BY id DESC LIMIT 1",
                          (path,)).fetchone()
        return json.loads(row[0]) if row else None


def project_rev(con, path: str) -> str:
    f = Path(path) / ".responseboard" / "status.json"
    mtime = f.stat().st_mtime_ns if f.exists() else 0
    last = con.execute("SELECT MAX(id) FROM runs WHERE project_path = ?", (path,)).fetchone()[0] or 0
    return f"{mtime}-{last}"


class Handler(BaseHTTPRequestHandler):
    server_version = "ResponseBoard"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code: int = 200) -> None:
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            return self.send(200, page_html(), "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self.send(204, b"", "image/x-icon")
        if not url.path.startswith("/api/"):
            return self.json({"error": "not found"}, 404)
        con = store.connect()
        try:
            if url.path == "/api/projects":
                return self.json([dict(r) for r in con.execute(PROJECTS_SQL)])
            path = q.get("path", "")
            if path not in known_paths(con):
                return self.json({"error": "unknown project"}, 404)
            if url.path == "/api/status":
                d = project_status(con, path)
                return self.json(d) if d else self.json({"error": "no status"}, 404)
            if url.path == "/api/runs":
                rows = store.query(con, project_path=path, status=q.get("status") or None,
                                   limit=min(int(q.get("limit") or 50), 500))
                return self.json([{k: r[k] for k in r.keys() if k != "raw_json"} for r in rows][::-1])
            if url.path == "/api/overview":
                return self.json(store.overview(con, path, limit=min(int(q.get("limit") or 500), 2000)))
            if url.path in ("/api/run", "/api/diff", "/api/talk"):
                try:
                    rid = int(q.get("id", ""))
                except ValueError:
                    return self.json({"error": "id must be a number"}, 400)
                if url.path == "/api/run":
                    r = store.get_run(con, path, rid)
                    return self.json(r) if r else self.json({"error": "no such run"}, 404)
                if url.path == "/api/talk":
                    t = store.get_talk(con, path, rid)
                    return self.json(t) if t else self.json({"error": "no conversation for this run"}, 404)
                patch = store.get_patch(con, path, rid)
                if patch is None:
                    return self.json({"error": "no git change for this run"}, 404)
                return self.send(200, patch.encode("utf-8"), "text/plain; charset=utf-8")
            if url.path == "/api/rev":
                return self.json({"rev": project_rev(con, path)})
            return self.json({"error": "not found"}, 404)
        finally:
            con.close()


def serve(port: int = DEFAULT_PORT, open_browser: bool = False) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"ResponseBoard: {url}  (db: {store.db_path()})  Ctrl+C to stop", flush=True)
    if open_browser and not os.environ.get("RB_NO_BROWSER"):
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
