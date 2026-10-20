"""The ResponseBoard rules for Claude, shared by the installer (CLAUDE.md) and the SessionStart hook."""
from __future__ import annotations

from pathlib import Path

RB_ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- responseboard:start -->", "<!-- responseboard:end -->"


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def rules() -> str:
    schema, sample = posix(RB_ROOT / "schema" / "status.schema.json"), posix(RB_ROOT / "samples" / "status.sample.json")
    return f"""{START}
## 毎回の終わりに `.responseboard/status.json` を書く（ResponseBoard）

返事を終える前に、毎回 `.responseboard/status.json` を書き直す。形は `{schema}`、例は `{sample}`。
書いていない・古い・形が違うと Stop フックが一度だけ止めて理由を返すので、直してから終える。
結果はステータスラインと、手元のボード（`{posix(RB_ROOT / "scripts" / "rb.cmd")} serve --open`）に出る。

- `run.title` は 40 字まで。したことを 1 行で
- `run.summary` は 120 字まで。長い説明は `run.notes` に入れる
- `run.asks` には、ユーザーの判断が要ることだけを入れる（選択肢 2〜4 個）。あれば `run.status` は `needs_you`、なければ空にする
- `run.checks` に確かめたこと（pass / fail / skip）、`run.changes` に変えたファイルを入れる
- `run.not_done` に今回やらずに残したこと（続きに回すもの・できなかったもの・やらないと決めたもの）と理由（`title` 60 字・`reason` 120 字）。
  それぞれに行き先を付ける: 続きをやるなら `tasks` に未着手のタスクを置いて `task` にその id、やらないと決めたなら `drop: true`。
  一部だけやったときは、タスクを分けて残りを未着手に置く。行き先の無いものはボードで「置き去り」と出る
- `left_behind` は rb が埋める置き去りの一覧。拾うときは、同じ `title` のまま今回の `not_done` に `task` か `drop` を付けて書く
- `run.next` に次に取りかかること（`title` 40 字・`detail` 400 字・`steps` 最初の手順 8 つまで・`task` は tasks の id）。
  会話を読み返さずに再開できるように、何をするか・なぜ次か・終わりの条件を書く。ボードでは信号のすぐ下に出る
- `section` は今取り組んでいる範囲の段取り、`tasks` はプロジェクト全体のカンバン（todo / doing / review / done）。
  前回の `status.json` を引き継いで、進んだ所だけ直す。各項目の `detail`（400 字）に、何をするかと終わりの条件を書く
- セクションはプロジェクトに何個もあり、履歴はセクションごとにまとまる。別の範囲に移ったら `section` を新しくする。
  `section.id` を省くと題の「:」の前が id になる。`tasks[].section` にはこの id を書く
- git の差分は rb が記録するので書かない
- `source.tool` は `claude-code`、`source.written_by` は `llm`
- `history` はフックが履歴のデータベースから埋めるので書かない
- `run.finished_at` は `+09:00` 付きの ISO 8601
- ユーザーが「このフォルダでは要らない」と言ったときだけ、`{posix(RB_ROOT / "scripts" / "rb.cmd")} --dir <フォルダ> ignore` で
  対象から外す（外す一覧は `%USERPROFILE%/.responseboard/ignore`）。自分の判断では外さない
{END}"""
