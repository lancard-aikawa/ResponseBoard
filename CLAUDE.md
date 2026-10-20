# ResponseBoard

LLM の作業結果を、長い最終応答を読まずに一目で分かるようにするボード。説明は README.md。

## 毎回の終わりに `.responseboard/status.json` を書く

返事を終える前に、毎回 `.responseboard/status.json` を書き直す（形は `schema/status.schema.json`、
例は `samples/status.sample.json`）。書いていない・古い・形が違うと、Stop フック（`hooks/rb_hook.py`）が
一度だけ止めて理由を返すので、直してから終える。

- `run.title` は 40 字まで。したことを 1 行で
- `run.summary` は 120 字まで。長い説明は `run.notes` に入れる
- `run.asks` には、ユーザーの判断が要ることだけを入れる。あれば `run.status` は `needs_you`、なければ空にする
- `run.not_done` に、今回やらずに残したこと（続きに回すもの・できなかったもの・やらないと決めたもの）と理由（`title` 60 字・`reason` 120 字）。
  ボードの「過去」に「残したこと」として、行き先ごとに分けて出る（「しなかった」と書くと、もうやらないと決まったように見えるので使わない）。
  それぞれに行き先を付ける: 続きをやるなら `tasks` に未着手のタスクを置いて `task` にその id、やらないと決めたなら `drop: true`。
  一部だけやったときは、タスクを分けて残りを未着手に置く。行き先の無いものはボードで「置き去り」と出る
- `left_behind` は rb が埋める置き去りの一覧。拾うときは、同じ `title` のまま今回の `not_done` に `task` か `drop` を付けて書く
- `run.next` に次に取りかかること（`title`・`detail`・`steps`・`task`）。会話を読み返さずに再開できるように書く
- `section` と `tasks` は前回の `status.json` を引き継いで、進んだ所だけ直す。`tasks[].detail` に何をするかと終わりの条件
- セクションはプロジェクトに何個もある。履歴はセクションごとにまとまるので、別の範囲に移ったら `section` を新しくする。
  `section.id` を省くと題の「:」の前（「第 4 段: …」→「第 4 段」）が id になる。`tasks[].section` はこの id を書く
- `history` と `left_behind` はフックが履歴のデータベース（`%USERPROFILE%\.responseboard\runs.db`）から埋めるので書かない
- `run.finished_at` は `+09:00` 付きの ISO 8601

## つくりの決まり

- 本体 `src/responseboard/` は LLM に依らず、標準ライブラリだけで書く。Claude 固有のことは `hooks/` に閉じ込める
- 入れ方は全体（`--scope user`）と個別（`--scope project`）。全体はどこでも動き、外す一覧（`ignore.py`）だけ除く。
  Claude に渡す決まりの文は `hooks/claude_rules.py` の 1 か所に置く
- LLM には SQLite を直接書かせない。書く口は `status.json` だけにして、記録は rb（`store.record_file`）が行う
- 本体とフックのテストは、`RESPONSEBOARD_DB` で一時ファイルの DB を使う。本物の `runs.db` に書かない

## 確かめ方

- 本体・フック・配信: `python -m unittest tests.test_hook tests.test_rb tests.test_server tests.test_statusline tests.test_install tests.test_git tests.test_scope tests.test_talk -v`
- ボードの見本: `python scripts/make_sample.py`（一時的な git リポジトリで 7 回分を本当に記録し、`samples/board.sample.json` とボードに埋め込む）
- 画面: `scripts\rb.cmd serve` を動かし、Playwright で http://127.0.0.1:28000/ を開く（スクリーンショットは `.playwright-mcp/` に保存する）
- ボード: `npm test`（jsdom で全タブを描く）
- 見本とスキーマ: `uv run --no-project --with jsonschema python -c "..."`（README.md）

## git

- 2026-10-20 に `git init` し、同じ日に https://github.com/lancard-aikawa/ResponseBoard へ公開した（public）。
  公開されるので、個人のパス・メールアドレス・本物の runs.db の中身を入れない。`.responseboard/` は追跡しない（毎回書き換わり、差分の写しからも外している）
- 改行は `.gitattributes` で LF にそろえる。`.cmd` / `.bat` / `.ps1` だけ CRLF

## 気を付けること

- DOM の `append()` に配列を渡すと `[object HTMLDivElement]` という文字になる。配列は `...` で展開して渡す
  （2026-10-19、「あなたの番」タブで起きた）。`replaceChildren()` も同じで、`false` は「false」という文字になる。
  ボードでは `put()` を通す（2026-10-20、差分の欄で起きた。`npm test` が余計な文字を調べる）
- `run.asks[].options` は文字列の配列。`{"label": …}` にすると選択肢が `[object Object]` と出る（2026-10-20）。
  `rb check` が調べるので、書いたら必ず通す
- フックや rb が他のプロジェクトで動くときに見せるパスは、ResponseBoard のフォルダの絶対パスにする（相対パスだと見つからない）
- bash の heredoc で Python を渡すと、文字列の中の `\n` が本物の改行になって割れることがある（2026-10-20、rb_hook.py で起きた）。
  スクラッチパッドにファイルを書いてから実行する
- スクロールする grid の中に `overflow: hidden` の子を並べると、行が縮んで中身が切れる（grid の子は最小の高さが 0 になる）。
  `grid-auto-rows: max-content` を付ける（2026-10-20、差分のダイアログで各ファイルが 120px で切れた。jsdom では見つからない）
- Playwright の窓はユーザーにも見えている。スマホの幅（390px）で確かめたら、終わる前に 1280px へ戻す
  （2026-10-20、戻し忘れて「スマホ表示みたいに狭くなった」と言われた）
- 「今回の作業中に書かれたか」を時刻で比べるときは 2 秒の余裕を持たせる。Windows ではファイルの時刻と time.time() がずれ、
  テストがときどき落ちた（2026-10-19）
