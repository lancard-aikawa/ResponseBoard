# ResponseBoard

LLM の作業結果を、長い最終応答を読まずに一目で分かるようにするボード。説明は README.md。

## 毎回の終わりに `.responseboard/status.json` を書く

返事を終える前に、毎回 `.responseboard/status.json` を書き直す（形は `schema/status.schema.json`、
例は `samples/status.sample.json`）。書いていない・古い・形が違うと、Stop フック（`hooks/rb_hook.py`）が
一度だけ止めて理由を返すので、直してから終える。

- `run.title` は 40 字まで。したことを 1 行で
- `run.summary` は 120 字まで。長い説明は `run.notes` に入れる
- `run.asks` には、ユーザーの判断が要ることだけを入れる。あれば `run.status` は `needs_you`、なければ空にする
- `run.not_done` に、今回しなかったこと・できなかったことと理由（`title` 60 字・`reason` 120 字）。ボードの「過去」に、したことと並べて出る
- `run.next` に次に取りかかること（`title`・`detail`・`steps`・`task`）。会話を読み返さずに再開できるように書く
- `section` と `tasks` は前回の `status.json` を引き継いで、進んだ所だけ直す。`tasks[].detail` に何をするかと終わりの条件
- セクションはプロジェクトに何個もある。履歴はセクションごとにまとまるので、別の範囲に移ったら `section` を新しくする。
  `section.id` を省くと題の「:」の前（「第 4 段: …」→「第 4 段」）が id になる。`tasks[].section` はこの id を書く
- `history` はフックが履歴のデータベース（`%USERPROFILE%\.responseboard\runs.db`）から埋めるので書かない
- `run.finished_at` は `+09:00` 付きの ISO 8601

## つくりの決まり

- 本体 `src/responseboard/` は LLM に依らず、標準ライブラリだけで書く。Claude 固有のことは `hooks/` に閉じ込める
- LLM には SQLite を直接書かせない。書く口は `status.json` だけにして、記録は rb（`store.record_file`）が行う
- 本体とフックのテストは、`RESPONSEBOARD_DB` で一時ファイルの DB を使う。本物の `runs.db` に書かない

## 確かめ方

- 本体・フック・配信: `python -m unittest tests.test_hook tests.test_rb tests.test_server tests.test_statusline tests.test_install tests.test_git -v`
- ボードの見本: `python scripts/make_sample.py`（一時的な git リポジトリで 7 回分を本当に記録し、`samples/board.sample.json` とボードに埋め込む）
- 画面: `scripts\rb.cmd serve` を動かし、Playwright で http://127.0.0.1:28000/ を開く（スクリーンショットは `.playwright-mcp/` に保存する）
- ボード: `npm test`（jsdom で全タブを描く）
- 見本とスキーマ: `uv run --no-project --with jsonschema python -c "..."`（README.md）

## 気を付けること

- DOM の `append()` に配列を渡すと `[object HTMLDivElement]` という文字になる。配列は `...` で展開して渡す
  （2026-10-19、「あなたの番」タブで起きた）。`replaceChildren()` も同じで、`false` は「false」という文字になる。
  ボードでは `put()` を通す（2026-10-20、差分の欄で起きた。`npm test` が余計な文字を調べる）
- フックや rb が他のプロジェクトで動くときに見せるパスは、ResponseBoard のフォルダの絶対パスにする（相対パスだと見つからない）
- 「今回の作業中に書かれたか」を時刻で比べるときは 2 秒の余裕を持たせる。Windows ではファイルの時刻と time.time() がずれ、
  テストがときどき落ちた（2026-10-19）
