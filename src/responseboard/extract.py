"""Drafts a run from an LLM's final answer with a local LLM, for tools that do not write status.json.

The answer goes to any OpenAI-compatible chat endpoint (Ollama, llama.cpp llama-server, LM Studio)
with a prompt asking for the run fields as JSON. What comes back is cut to the limits of
status.py, so the draft always passes rb check. The answer itself is kept as the run's talk,
so the board can show what the draft was made from.

Small local models are good at pulling fields out of a text but may drop or change numbers
(measured 2026-09-25 on an RTX 3050 6GB), so numbers in title and summary that do not
appear in the answer are reported.

  RESPONSEBOARD_LLM_URL    default http://127.0.0.1:11434/v1 (Ollama)
  RESPONSEBOARD_LLM_MODEL  default gemma4:e2b. On Ollama it takes 45 s or more to load each time (about 3.5 s on
                           llama.cpp from an SSD). qwen3:4b-instruct loads fast but put stray Chinese characters
                           into the draft ("117 何件", 2026-10-21), and missed "not committed yet" in not_done.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from . import status as st

URL = "http://127.0.0.1:11434/v1"
MODEL = "gemma4:e2b"
TIMEOUT = 300  # the first call loads the model: 90 s or more from an HDD
ANSWER_MAX = 12000  # characters of the answer sent to the model

PROMPT = """あなたは作業の報告を読み、決まった形の JSON にまとめる係です。
次の「最終応答」は、AI が作業を終えたときにユーザーへ書いた文です。

- 文に書かれていることだけを使う。推測で足さない。数字・ファイル名・コマンドは文のとおりに写す
- title は「〜した」の形で、したこと。質問を title にしない
- ユーザーに判断や答えを求めているなら status は "needs_you" にして asks に入れる。
  options は選択肢の短い名前だけ（番号・説明は付けない）。「おすすめ」と書かれた選択肢の番号（0 から）を recommended に入れる。
  選択肢が書かれていなければ ["はい", "いいえ"]
- エラーで止まった・できなかったと書かれていれば "failed"。それ以外は "done"
- 「まだ〜していない」「〜は残した」と書かれたことは、必ず not_done に 1 つずつ入れる
- checks は、テストや確認を実際に行ったと書かれたものだけ（件数も文のとおり）
- 当てはまるものが無い欄は空の配列。次にやることが書かれていなければ next は null

出力は次の形の JSON 1 つだけ:
{"status": "done" | "needs_you" | "failed",
 "title": "したことを 1 行で（40 字まで）",
 "summary": "要点（120 字まで）",
 "asks": [{"question": "質問", "options": ["選択肢", "選択肢"], "recommended": 0}],
 "checks": [{"name": "確かめたこと", "result": "pass" | "fail" | "skip"}],
 "not_done": [{"title": "やらずに残したこと", "reason": "理由"}],
 "next": {"title": "次にやること", "detail": "くわしく"}}

例:
最終応答「ログイン画面を直しました。テスト 12 件は通りました。メールの送信はまだ作っていません。
次はどちらにしますか？ 1. **パスワードの再設定**（おすすめ） 2. **二段階認証**」
→ {"status": "needs_you", "title": "ログイン画面を直した", "summary": "ログイン画面を直し、テスト 12 件が通った。",
 "asks": [{"question": "次はどちらにしますか？", "options": ["パスワードの再設定", "二段階認証"], "recommended": 0}],
 "checks": [{"name": "テスト 12 件", "result": "pass"}],
 "not_done": [{"title": "メールの送信", "reason": "まだ作っていない"}], "next": null}"""


class ExtractError(Exception):
    pass


def complete(answer: str, url: str, model: str, timeout: float = TIMEOUT) -> str:
    """Sends the answer to the model and returns its reply text."""
    body = {"model": model, "temperature": 0, "stream": False, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": PROMPT},
                         {"role": "user", "content": "最終応答:\n\n" + answer[:ANSWER_MAX]}]}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return d["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError) as e:
        raise ExtractError(f"cannot reach the local LLM at {url} ({e}). Start Ollama or llama-server, "
                           "or set RESPONSEBOARD_LLM_URL") from e
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ExtractError(f"unexpected reply from {url}: {e}") from e


def parse(text: str) -> dict:
    """The JSON object in the reply; models sometimes wrap it in ``` fences or add words around it."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ExtractError("the model did not answer with JSON")
    try:
        d = json.loads(text[a:b + 1])
    except ValueError as e:
        raise ExtractError(f"the model's JSON is broken: {e}") from e
    if not isinstance(d, dict):
        raise ExtractError("the model's JSON is not an object")
    return d


# Scripts a Japanese or English answer is written in. A small model now and then lets a stray letter of
# another script slip in (a Thai vowel sign in "117 件", 2026-10-21); such a letter is dropped unless the answer has it.
COMMON = ((0x0000, 0x024F), (0x2000, 0x2BFF), (0x3000, 0x30FF), (0x3400, 0x9FFF), (0xFF00, 0xFFEF))


def text(v, n: int, answer: str = "") -> str:
    s = " ".join(str(v).split()) if isinstance(v, (str, int, float)) else ""
    s = "".join(ch for ch in s if any(a <= ord(ch) <= b for a, b in COMMON) or ch in answer)
    return s if len(s) <= n else s[: n - 1] + "…"


def _list(v) -> list:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def to_run(raw: dict, answer: str = "") -> dict:
    """The model's fields as a valid run (see status.validate), whatever it wrote."""
    def _s(v, n):
        return text(v, n, answer)

    asks = []
    for a in _list(raw.get("asks"))[:4]:
        opts = [_s(o, st.ASK_OPTION_MAX) for o in a.get("options") or [] if _s(o, st.ASK_OPTION_MAX)][:4]
        q = _s(a.get("question"), 200)
        if q:
            ask = {"id": f"q{len(asks) + 1}", "question": q, "options": opts if len(opts) >= 2 else ["はい", "いいえ"]}
            rec = a.get("recommended")
            if isinstance(rec, int) and not isinstance(rec, bool) and 0 <= rec < len(ask["options"]):
                ask["recommended"] = rec
            asks.append(ask)
    status = raw.get("status") if raw.get("status") in ("done", "failed") else "done"
    run = {"status": "needs_you" if asks else status,
           "title": _s(raw.get("title"), st.TITLE_MAX) or "（題を抜き出せなかった）",
           "summary": _s(raw.get("summary"), st.SUMMARY_MAX), "asks": asks,
           "checks": [{"name": _s(c.get("name"), 120), "result": c.get("result") if c.get("result") in ("pass", "fail", "skip") else "skip"}
                      for c in _list(raw.get("checks")) if _s(c.get("name"), 120)],
           "not_done": [{"title": _s(x.get("title"), st.NOT_DONE_MAX), "reason": _s(x.get("reason"), 120)}
                        for x in _list(raw.get("not_done")) if _s(x.get("title"), st.NOT_DONE_MAX)]}
    nx = raw.get("next")
    if isinstance(nx, dict) and _s(nx.get("title"), st.TITLE_MAX):
        run["next"] = {"title": _s(nx["title"], st.TITLE_MAX), "detail": _s(nx.get("detail"), st.DETAIL_MAX)}
    return {k: v for k, v in run.items() if v != ""}


def unseen_numbers(run: dict, answer: str) -> list[str]:
    """Numbers in the title and summary that the answer does not have (a small model may change them)."""
    have = set(re.findall(r"\d+(?:\.\d+)?", answer.replace(",", "")))
    said = re.findall(r"\d+(?:\.\d+)?", f"{run.get('title', '')} {run.get('summary', '')}".replace(",", ""))
    return sorted(set(n for n in said if n not in have))


def draft(answer: str, prev: dict, project: dict, tool: str, url: str = URL, model: str = MODEL,
          timeout: float = TIMEOUT) -> tuple[dict, list[str]]:
    """A status.json made from the answer, carrying section and tasks over from prev. Returns (status, warnings)."""
    run = to_run(parse(complete(answer, url, model, timeout)), answer)
    warnings = []
    odd = unseen_numbers(run, answer)
    if odd:
        warnings.append(f"numbers not in the answer: {', '.join(odd)}")
    run["notes"] = f"最終応答から {model} が抜き出した。元の文は会話にある。" + (
        f"\n\n元の文に無い数字がある（{', '.join(odd)}）。確かめること。" if odd else "")
    d = {"schema": st.SCHEMA_ID, "project": project, "source": {"tool": tool, "written_by": "extractor"}, "run": run}
    for keep in ("section", "tasks"):
        if keep in prev:
            d[keep] = prev[keep]
    return d, warnings


def settings() -> tuple[str, str]:
    return os.environ.get("RESPONSEBOARD_LLM_URL") or URL, os.environ.get("RESPONSEBOARD_LLM_MODEL") or MODEL
