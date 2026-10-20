"""Build the board's sample data by really running a small project through rb, then embed it.

  python scripts/make_sample.py

Makes a throwaway git repo and history database in a temp folder, plays 7 runs of a made-up
project (a weather widget) through store.record_file, and exports what rb serve would return:
samples/board.sample.json, also embedded in board/responseboard.html (<script id="sample">).
Dates are fixed, so running it again gives the same file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RB_ROOT / "src"))

from responseboard import status as st  # noqa: E402
from responseboard import store  # noqa: E402

SHOWN_PATH = "C:/Example/WeatherWidget"
S1, S2, S3 = "第 1 段: 画面の形", "第 2 段: 天気をつなぐ", "第 3 段: 仕上げ"


def steps(*pairs):
    return [{"title": t, "state": s} for t, s in pairs]


def tasks(cols: dict) -> list:
    base = [("t1", "画面の骨組み", "第 1 段"), ("t2", "今日の天気カード", "第 1 段"), ("t3", "週の予報の列", "第 1 段"),
            ("t4", "天気 API から取る", "第 2 段"), ("t5", "取れないときの表示", "第 2 段"),
            ("t6", "暗い画面に合わせる", "第 3 段"), ("t7", "公開する", "第 3 段")]
    detail = {"t4": "Open-Meteo から 7 日分を取る。終わりの条件: 東京の 7 日分が画面に出る。",
              "t7": "GitHub Pages に置く。終わりの条件: 公開の URL で開ける。"}
    return [{"id": i, "title": t, "column": cols.get(i, "todo"), "section": s, **({"detail": detail[i]} if i in detail else {})}
            for i, t, s in base]


# (files to write, status.json "run" + "section" + task columns, commit message or None)
RUNS = [
    ({"index.html": "<main id=app></main>\n<script src=app.js></script>\n",
      "app.js": "const app = document.getElementById('app');\napp.textContent = '天気';\n"},
     dict(finished_at="2026-10-18T10:05:00+09:00", status="done", title="画面の骨組みを作った",
          summary="index.html と app.js を置いた。", changes=[("index.html", "added"), ("app.js", "added")],
          checks=[("ブラウザで開く", "pass", "")], not_done=[("見た目の調整", "形が決まってから")],
          next=("今日の天気カード", "t2"),
          section=(S1, [("骨組み", "done"), ("今日", "todo"), ("週", "todo")]), cols={"t1": "done"}), None),
    ({"app.js": "const app = document.getElementById('app');\nfunction today(t) {\n  return `<div class=card>${t.temp}°</div>`;\n}\napp.innerHTML = today({ temp: 21 });\n",
      "style.css": ".card { border: 1px solid #ccc; padding: 8px; }\n"},
     dict(finished_at="2026-10-18T10:40:00+09:00", status="done", title="今日の天気カードを足した",
          summary="気温を 1 枚のカードで出す。値はまだ仮。", changes=[("app.js", "modified"), ("style.css", "added")],
          checks=[("カードが出る", "pass", "")], not_done=[("本物の天気", "第 2 段でつなぐ")],
          next=("週の予報の列", "t3"),
          section=(S1, [("骨組み", "done"), ("今日", "done"), ("週", "todo")]), cols={"t1": "done", "t2": "done", "t3": "doing"}), "今日のカード"),
    ({"app.js": "const app = document.getElementById('app');\nfunction today(t) {\n  return `<div class=card>${t.temp}°</div>`;\n}\nfunction week(days) {\n  return days.map(today).join('');\n}\napp.innerHTML = today({ temp: 21 }) + week([{ temp: 19 }, { temp: 23 }]);\n"},
     dict(finished_at="2026-10-18T11:20:00+09:00", status="needs_you", title="週の予報を横に並べた",
          summary="7 日分の列を足した。並べ方を選んでほしい。", changes=[("app.js", "modified")],
          checks=[("列が出る", "pass", ""), ("狭い画面", "skip", "まだ見ていない")],
          asks=[("週の予報は横に流す？縦に積む？", ["横に流す", "縦に積む"], 0)],
          next=("天気 API から取る", "t4"),
          section=(S1, [("骨組み", "done"), ("今日", "done"), ("週", "done")]), cols={"t1": "done", "t2": "done", "t3": "review"}), None),
    ({"api.js": "export async function forecast(lat, lon) {\n  const u = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&daily=temperature_2m_max`;\n  return (await fetch(u)).json();\n}\n"},
     dict(finished_at="2026-10-18T14:00:00+09:00", status="failed", title="天気 API をつなぐ途中で止まった",
          summary="fetch が CORS で失敗。file:// で開いていたのが原因らしい。", changes=[("api.js", "added")],
          checks=[("東京の 7 日分", "fail", "CORS エラー")], not_done=[("画面への反映", "取れていないため")],
          next=("手元のサーバで開き直す", "t4"),
          section=(S2, [("API", "doing"), ("失敗の表示", "todo")]), cols={"t1": "done", "t2": "done", "t3": "done", "t4": "doing"}), None),
    ({"app.js": "import { forecast } from './api.js';\nconst app = document.getElementById('app');\nfunction today(t) {\n  return `<div class=card>${t.temp}°</div>`;\n}\nconst d = await forecast(35.68, 139.69);\napp.innerHTML = d.daily.temperature_2m_max.map(temp => today({ temp })).join('');\n",
      "index.html": "<main id=app></main>\n<script type=module src=app.js></script>\n"},
     dict(finished_at="2026-10-18T14:30:00+09:00", status="done", title="天気 API から 7 日分を出した",
          summary="手元のサーバで開けば取れた。module に変えた。", changes=[("app.js", "modified"), ("index.html", "modified")],
          checks=[("東京の 7 日分", "pass", "")], not_done=[("取れないときの表示", "次の実行で")],
          next=("取れないときの表示", "t5"),
          section=(S2, [("API", "done"), ("失敗の表示", "todo")]), cols={"t1": "done", "t2": "done", "t3": "done", "t4": "done", "t5": "doing"}), "API をつないだ"),
    ({"app.js": "import { forecast } from './api.js';\nconst app = document.getElementById('app');\nfunction today(t) {\n  return `<div class=card>${t.temp}°</div>`;\n}\ntry {\n  const d = await forecast(35.68, 139.69);\n  app.innerHTML = d.daily.temperature_2m_max.map(temp => today({ temp })).join('');\n} catch {\n  app.textContent = '天気を取れませんでした';\n}\n"},
     dict(finished_at="2026-10-18T15:10:00+09:00", status="done", title="取れないときの表示を足した",
          summary="通信が切れたら一文を出す。", changes=[("app.js", "modified")],
          checks=[("通信を切って開く", "pass", "")],
          next=("暗い画面に合わせる", "t6"),
          section=(S2, [("API", "done"), ("失敗の表示", "done")]), cols={"t1": "done", "t2": "done", "t3": "done", "t4": "done", "t5": "done"}), None),
    ({"style.css": ".card { border: 1px solid #ccc; padding: 8px; }\n@media (prefers-color-scheme: dark) {\n  body { background: #111; color: #eee; }\n  .card { border-color: #444; }\n}\n"},
     dict(finished_at="2026-10-18T16:00:00+09:00", status="needs_you", title="暗い画面に合わせた",
          summary="OS が暗い設定のときの色を足した。公開先を決めてほしい。", changes=[("style.css", "modified")],
          checks=[("暗い設定で開く", "pass", ""), ("明るい設定で開く", "pass", "")],
          not_done=[("公開", "公開先が決まっていない"), ("アイコン", "今回の範囲の外")],
          asks=[("どこに公開しますか？", ["GitHub Pages", "Cloudflare Pages", "まだ公開しない"], 0)],
          next=("公開する", "t7"),
          section=(S3, [("暗い画面", "done"), ("公開", "todo")]), cols={"t1": "done", "t2": "done", "t3": "done", "t4": "done", "t5": "done", "t6": "review"}), None),
]


def status_for(r: dict) -> dict:
    run = {"id": r["finished_at"][:16], "finished_at": r["finished_at"], "status": r["status"], "title": r["title"],
           "summary": r["summary"],
           "asks": [{"id": f"q{i + 1}", "question": q, "options": o, "recommended": rec} for i, (q, o, rec) in enumerate(r.get("asks", []))],
           "checks": [{"name": n, "result": res, **({"detail": d} if d else {})} for n, res, d in r["checks"]],
           "changes": [{"path": p, "kind": k} for p, k in r["changes"]],
           "not_done": [{"title": t, "reason": why} for t, why in r.get("not_done", [])],
           "next": {"title": r["next"][0], "task": r["next"][1]}}
    sec_title, sec_steps = r["section"]
    return {"schema": st.SCHEMA_ID, "project": {"name": "お天気ウィジェット"},
            "source": {"tool": "claude-code", "model": "claude-opus-5-5", "written_by": "llm"},
            "run": run, "section": {"title": sec_title, "steps": steps(*sec_steps)}, "tasks": tasks(r["cols"])}


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    env_keep = os.environ.get("RESPONSEBOARD_DB")
    os.environ["RESPONSEBOARD_DB"] = str(tmp / "runs.db")
    proj = tmp / "WeatherWidget"
    proj.mkdir()
    genv = dict(os.environ, GIT_AUTHOR_NAME="sample", GIT_AUTHOR_EMAIL="sample@example.com",
                GIT_COMMITTER_NAME="sample", GIT_COMMITTER_EMAIL="sample@example.com")
    git = lambda *a, when="2026-10-18T09:00:00+09:00": subprocess.run(  # noqa: E731
        ["git", *a], cwd=proj, check=True, capture_output=True, env={**genv, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
    try:
        git("init", "-q", "-b", "main")
        (proj / ".gitignore").write_text(".responseboard/\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "start")
        for files, r, commit in RUNS:
            for name, text in files.items():
                (proj / name).write_text(text, encoding="utf-8", newline="\n")
            st.write_json(st.status_path(proj), status_for(r))
            errs, _ = store.record_file(proj)
            if errs:
                raise SystemExit(errs)
            if commit:
                git("add", "-A")
                git("commit", "-q", "-m", commit, when=r["finished_at"])
        path = st.norm_path(proj)
        con = store.connect()
        try:
            ov = store.overview(con, path)
            bundle = {"status": st.read_json(st.status_path(proj)), "overview": ov,
                      "runs": {str(o["id"]): store.get_run(con, path, o["id"]) for o in ov},
                      "patches": {str(o["id"]): store.get_patch(con, path, o["id"]) for o in ov if "git" in o}}
        finally:
            con.close()
    finally:
        if env_keep is None:
            os.environ.pop("RESPONSEBOARD_DB", None)
        else:
            os.environ["RESPONSEBOARD_DB"] = env_keep
        shutil.rmtree(tmp, ignore_errors=True)
    text = json.dumps(bundle, ensure_ascii=False, indent=1).replace(path, SHOWN_PATH)
    (RB_ROOT / "samples" / "board.sample.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    board = RB_ROOT / "board" / "responseboard.html"
    html = board.read_text(encoding="utf-8")
    # "</" cannot appear inside a <script> element; JSON allows "<\/" for it.
    embedded = text.replace("</", "<\\/")
    html, n = re.subn(r'(<script type="application/json" id="sample">).*?(</script>)',
                      lambda m: m.group(1) + embedded + m.group(2), html, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("sample script tag not found in board/responseboard.html")
    board.write_text(html, encoding="utf-8", newline="\n")
    print(f"{len(bundle['overview'])} runs, {len(bundle['patches'])} with git -> samples/board.sample.json and the board")
    return 0


if __name__ == "__main__":
    sys.exit(main())
