"""rb: record runs for ResponseBoard without any particular LLM.

  rb check                      check .responseboard/status.json
  rb record [--talk FILE]       check it and add it to the history database (FILE: the conversation, see talk.py)
  rb done TITLE [--summary S]   write a "done" run and record it
  rb fail TITLE [--summary S]   write a "failed" run and record it
  rb running TITLE              write a "running" run (not recorded until it ends)
  rb ask TITLE --ask "Q|*A|B"   write a "needs you" run; * marks the recommended option
  rb history [--all] [--status S] [--tool T] [--limit N] [--json]
  rb import-jsonl FILE          add runs from an old history.jsonl
  rb serve [--port N] [--open]  show the board in a local browser (http://127.0.0.1:28000/)
  rb statusline [--no-color]    one line for a terminal status bar
  rb ignore [--remove | --list] add this folder to the deny list (the hooks then do nothing here)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import status as st
from . import store

MARK = {"done": "●", "needs_you": "▲", "failed": "✕", "running": "◐"}


def out(text: str = "") -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


def err(text: str) -> None:
    sys.stderr.buffer.write((text + "\n").encode("utf-8"))
    sys.stderr.flush()


def parse_ask(i: int, spec: str) -> dict:
    parts = [p.strip() for p in spec.split("|")]
    question, opts = parts[0], parts[1:]
    rec = next((n for n, o in enumerate(opts) if o.startswith("*")), None)
    ask = {"id": f"q{i + 1}", "question": question, "options": [o.lstrip("*").strip() for o in opts]}
    if rec is not None:
        ask["recommended"] = rec
    return ask


def build(root: Path, state: str, title: str, args) -> dict:
    path = st.status_path(root)
    try:
        prev = st.read_json(path)
    except (FileNotFoundError, ValueError):
        prev = {}
    run = {"id": store.new_id(), "finished_at": st.now_iso(), "status": state, "title": title}
    if args.summary:
        run["summary"] = args.summary
    run["asks"] = [parse_ask(i, s) for i, s in enumerate(getattr(args, "ask", None) or [])]
    d = {
        "schema": st.SCHEMA_ID,
        "project": {"name": args.project or (prev.get("project") or {}).get("name") or root.name,
                    "path": st.norm_path(root)},
        "source": {"tool": args.tool, "written_by": "manual"},
        "run": run,
    }
    for keep in ("section", "tasks"):  # the kanban carries over between runs
        if keep in prev:
            d[keep] = prev[keep]
    return d


def cmd_write(args, state: str) -> int:
    root = Path(args.dir)
    d = build(root, state, args.title, args)
    errs = st.validate(d)
    if errs:
        for e in errs:
            err(f"error: {e}")
        return 1
    st.write_json(st.status_path(root), d)
    if state == "running":
        out(f"{MARK[state]} {args.title}")
        return 0
    return cmd_record(args)


def cmd_check(args) -> int:
    path = st.status_path(Path(args.dir))
    try:
        errs = st.validate(st.read_json(path))
    except FileNotFoundError:
        errs = [f"{path} does not exist"]
    except ValueError as e:
        errs = [f"{path} is not valid JSON: {e}"]
    for e in errs:
        err(f"error: {e}")
    if not errs:
        out("ok")
    return 1 if errs else 0


def cmd_record(args) -> int:
    talk = None
    if getattr(args, "talk", None):
        try:
            talk = json.loads(Path(args.talk).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            err(f"error: {args.talk}: {e}")
            return 1
    errs, inserted = store.record_file(Path(args.dir), default_writer="manual", talk=talk)
    for e in errs:
        err(f"error: {e}")
    if errs:
        return 1
    d = st.read_json(st.status_path(Path(args.dir)))
    out(f"{MARK[d['run']['status']]} {d['run']['title']}  ({'recorded' if inserted else 'already recorded'})")
    return 0


def cmd_history(args) -> int:
    con = store.connect()
    try:
        rows = store.query(con, project_path=None if args.all else st.norm_path(args.dir),
                           status=args.status, tool=args.tool, limit=args.limit)
    finally:
        con.close()
    if args.json:
        out(json.dumps([{k: r[k] for k in r.keys() if k != "raw_json"} for r in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        out("no runs recorded")
    for r in rows:
        when = r["finished_at"][:16].replace("T", " ")
        where = f"  [{r['project']}]" if args.all else ""
        out(f"{MARK.get(r['status'], '?')} {when}  {r['tool']:<12} {r['title']}{where}")
    return 0


LEFT_MARK = {"left": "!", "task": ">", "drop": "-"}


def cmd_left(args) -> int:
    con = store.connect()
    try:
        items = store.leftovers(con, st.norm_path(args.dir))
    finally:
        con.close()
    if not args.all:
        items = [x for x in items if x["state"] == "left"]
    if args.json:
        out(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        out("nothing left behind")
    for x in items:
        when = x["finished_at"][:16].replace("T", " ")
        where = f" -> {x['task']}" if x["state"] == "task" else " (dropped)" if x["state"] == "drop" else ""
        why = f" - {x['reason']}" if x.get("reason") else ""
        out(f"{LEFT_MARK[x['state']]} {when}  {x['title']}{where}{why}")
    return 0


def cmd_import(args) -> int:
    root = Path(args.dir)
    try:
        name = st.read_json(st.status_path(root))["project"]["name"]
    except (FileNotFoundError, ValueError, KeyError):
        name = root.name
    con = store.connect()
    added = 0
    try:
        with Path(args.file).open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)
                d = {"schema": st.SCHEMA_ID, "project": {"name": name, "path": st.norm_path(root)},
                     "source": {"tool": e.get("tool") or "unknown", "written_by": "hook"},
                     "run": {"finished_at": e["finished_at"], "status": e["status"], "title": e["title"]}}
                added += store.insert(con, d, raw=line.strip())
    finally:
        con.close()
    out(f"imported {added} run(s)")
    return 0


def cmd_ignore(args) -> int:
    from . import ignore
    if args.list:
        out(f"# {ignore.path()}")
        for pat in ignore.patterns():
            out(pat)
        return 0
    if args.remove:
        removed = ignore.remove(args.dir)
        hit = ignore.match(args.dir)
        if hit:
            out(f"still skipped by: {hit} (edit {ignore.path()})")
            return 1
        out(f"removed: {args.dir}" if removed else f"not in the list: {args.dir}")
        return 0
    hit = ignore.match(args.dir)
    if ignore.add(args.dir):
        out(f"added: {args.dir} ({ignore.path()})")
    else:
        out(f"already skipped by: {hit}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rb", description="Record LLM or manual runs for ResponseBoard.")
    p.add_argument("--dir", default=".", help="project folder (default: current folder)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check .responseboard/status.json")
    rec = sub.add_parser("record", help="check status.json and add it to the history database")
    rec.add_argument("--talk", metavar="FILE", help="JSON list of {role, text, at} to save with the run")
    for name, help_ in (("done", "finished, nothing for the user to do"), ("fail", "stopped on an error"),
                        ("running", "still working"), ("ask", "needs an answer from the user")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("title")
        sp.add_argument("--summary")
        sp.add_argument("--tool", default="manual", help="who did the work (default: manual)")
        sp.add_argument("--project", help="project name (default: kept from status.json or the folder name)")
        if name == "ask":
            sp.add_argument("--ask", action="append", required=True, metavar='"QUESTION|*OPTION|OPTION"')
    h = sub.add_parser("history", help="list recorded runs")
    h.add_argument("--all", action="store_true", help="all projects, not only this folder")
    h.add_argument("--status", choices=sorted(st.RUN_STATUS))
    h.add_argument("--tool")
    h.add_argument("--limit", type=int, default=20)
    h.add_argument("--json", action="store_true")
    lf = sub.add_parser("left", help="list not_done items that have neither a task nor drop")
    lf.add_argument("--all", action="store_true", help="also the ones moved to a task or dropped")
    lf.add_argument("--json", action="store_true")
    im = sub.add_parser("import-jsonl", help="add runs from an old history.jsonl")
    im.add_argument("file")
    sv = sub.add_parser("serve", help="show the board in a local browser")
    sv.add_argument("--port", type=int, default=28000)
    sv.add_argument("--open", action="store_true", help="open the browser")
    sl = sub.add_parser("statusline", help="one line for a terminal status bar")
    sl.add_argument("--no-color", action="store_true")
    ig = sub.add_parser("ignore", help="add this folder to the deny list")
    ig.add_argument("--remove", action="store_true", help="take this folder off the list")
    ig.add_argument("--list", action="store_true", help="show the list")
    args = p.parse_args(argv)
    args.dir = str(Path(args.dir).resolve())
    state = {"done": "done", "fail": "failed", "running": "running", "ask": "needs_you"}.get(args.cmd)
    if state:
        return cmd_write(args, state)
    if args.cmd == "statusline":
        from .statusline import line
        out(line(Path(args.dir), color=not args.no_color))
        return 0
    if args.cmd == "serve":
        from .server import serve
        serve(args.port, args.open)
        return 0
    return {"check": cmd_check, "record": cmd_record, "history": cmd_history, "left": cmd_left, "import-jsonl": cmd_import,
            "ignore": cmd_ignore}[args.cmd](args)
