"""Reading, checking and writing .responseboard/status.json."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

SCHEMA_ID = "responseboard/status@1"
RUN_STATUS = {"done", "needs_you", "failed", "running"}
STEP_STATE = {"done", "doing", "todo", "blocked"}
COLUMNS = {"todo", "doing", "review", "done"}
WRITERS = {"llm", "hook", "extractor", "manual"}
TITLE_MAX = 40
SUMMARY_MAX = 120
DETAIL_MAX = 400   # run.next.detail and tasks[].detail
ASK_OPTION_MAX = 40  # run.asks[].options[]
NOT_DONE_MAX = 60  # run.not_done[].title


def status_path(root: Path) -> Path:
    return Path(root) / ".responseboard" / "status.json"


def read_json(path: Path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm_path(p) -> str:
    return str(Path(p).resolve()).replace("\\", "/")


def section_id(sec: dict | None) -> str | None:
    """A section's key: its id, or the part of the title before ":" ("第 4 段: ..." -> "第 4 段")."""
    if not sec:
        return None
    if sec.get("id"):
        return str(sec["id"])
    title = str(sec.get("title") or "").replace("：", ":")
    return title.split(":", 1)[0].strip() or None


def validate(d) -> list[str]:
    errs: list[str] = []
    if not isinstance(d, dict):
        return ["top level must be an object"]
    if d.get("schema") != SCHEMA_ID:
        errs.append(f'"schema" must be "{SCHEMA_ID}"')
    if not (d.get("project") or {}).get("name"):
        errs.append("project.name is required")
    src = d.get("source") or {}
    if not src.get("tool"):
        errs.append("source.tool is required")
    if src.get("written_by") not in (None, *WRITERS):
        errs.append(f"source.written_by must be one of {sorted(WRITERS)}")
    run = d.get("run")
    if not isinstance(run, dict):
        return errs + ["run is required"]
    if run.get("status") not in RUN_STATUS:
        errs.append(f"run.status must be one of {sorted(RUN_STATUS)}")
    title = run.get("title") or ""
    if not title:
        errs.append("run.title is required")
    elif len(title) > TITLE_MAX:
        errs.append(f"run.title is {len(title)} chars; keep it to {TITLE_MAX}")
    if len(run.get("summary") or "") > SUMMARY_MAX:
        errs.append(f"run.summary is over {SUMMARY_MAX} chars; move the rest to run.notes")
    # rb fills finished_at when it is left out, so only a written one is checked
    if run.get("finished_at") is not None:
        try:
            if datetime.fromisoformat(str(run["finished_at"])).tzinfo is None:
                errs.append("run.finished_at needs a UTC offset such as +09:00")
        except ValueError:
            errs.append("run.finished_at must be an ISO 8601 date-time with offset")
    asks = run.get("asks") or []
    if run.get("status") == "needs_you" and not asks:
        errs.append('run.status is "needs_you" but run.asks is empty')
    if asks and run.get("status") != "needs_you":
        errs.append('run.asks is not empty, so run.status must be "needs_you"')
    for i, a in enumerate(asks):
        opts = a.get("options") or []
        if not a.get("id") or not a.get("question") or not 2 <= len(opts) <= 4:
            errs.append(f"run.asks[{i}] needs id, question and 2-4 options")
        # the board prints each option as text, so an object shows up as [object Object]
        for j, o in enumerate(opts if isinstance(opts, list) else []):
            if not isinstance(o, str) or not o.strip() or len(o) > ASK_OPTION_MAX:
                errs.append(f"run.asks[{i}].options[{j}] must be a string of up to {ASK_OPTION_MAX} chars")
    nd = run.get("not_done") or []
    if not isinstance(nd, list):
        errs.append("run.not_done must be a list")
        nd = []
    task_ids = {t.get("id") for t in d.get("tasks") or [] if isinstance(t, dict)}
    for i, x in enumerate(nd):
        if not isinstance(x, dict) or not x.get("title") or len(str(x["title"])) > NOT_DONE_MAX:
            errs.append(f"run.not_done[{i}] needs a title of up to {NOT_DONE_MAX} chars")
            continue
        # where the rest went: a task that holds it, or dropped on purpose; neither = left behind
        if x.get("task") is not None and x.get("drop"):
            errs.append(f"run.not_done[{i}] has both task and drop; pick one")
        if x.get("task") is not None and x["task"] not in task_ids:
            errs.append(f'run.not_done[{i}].task "{x["task"]}" is not an id in tasks')
        if x.get("drop") is not None and not isinstance(x["drop"], bool):
            errs.append(f"run.not_done[{i}].drop must be true or false")
    if run.get("from_next") is not None and not isinstance(run["from_next"], bool):
        errs.append("run.from_next must be true or false")
    nxt = run.get("next")
    if nxt is not None:
        if not isinstance(nxt, dict) or not nxt.get("title"):
            errs.append("run.next needs a title")
        else:
            if len(nxt["title"]) > TITLE_MAX:
                errs.append(f"run.next.title is {len(nxt['title'])} chars; keep it to {TITLE_MAX}")
            if len(nxt.get("detail") or "") > DETAIL_MAX:
                errs.append(f"run.next.detail is over {DETAIL_MAX} chars")
            steps = nxt.get("steps") or []
            if not isinstance(steps, list) or len(steps) > 8 or not all(isinstance(s, str) for s in steps):
                errs.append("run.next.steps must be a list of up to 8 strings")
    sec = d.get("section")
    if sec is not None:
        steps = sec.get("steps") or []
        if sec.get("id") is not None and not isinstance(sec["id"], str):
            errs.append("section.id must be a string")
        if not sec.get("title") or not steps:
            errs.append("section needs title and at least one step")
        for i, s in enumerate(steps):
            if s.get("state") not in STEP_STATE:
                errs.append(f"section.steps[{i}].state must be one of {sorted(STEP_STATE)}")
    for i, t in enumerate(d.get("tasks") or []):
        if t.get("column") not in COLUMNS:
            errs.append(f"tasks[{i}].column must be one of {sorted(COLUMNS)}")
        if t.get("detail") is not None and (not isinstance(t["detail"], str) or len(t["detail"]) > DETAIL_MAX):
            errs.append(f"tasks[{i}].detail must be a string of up to {DETAIL_MAX} chars")
    return errs
