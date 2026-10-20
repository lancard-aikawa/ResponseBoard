# ResponseBoard

LLM の作業結果を、長い最終応答を読まずに一目で分かるようにするボード。
Claude 以外の LLM でも、LLM を使わない作業（人やスクリプト）でも同じ形で記録する。

## つくり

```
LLM / 人 / スクリプト ─→ .responseboard/status.json ─→ rb（形のチェック・記録） ─→ runs.db（SQLite）
                                                                    └→ ボード
```

| 場所 | 中身 |
|---|---|
| `src/responseboard/` | 本体（標準ライブラリだけ）。`status.py` 形のチェック、`store.py` SQLite、`gitsnap.py` git の差分、`cli.py` rb コマンド |
| `scripts/rb.cmd` | rb コマンドの起動役 |
| `hooks/rb_hook.py` | Claude Code のつなぎ役（UserPromptSubmit / Stop フック）。中身は本体を呼ぶだけ |
| `schema/status.schema.json` | 1 回の実行結果の形（`responseboard/status@1`） |
| `samples/status.sample.json` | 見本のデータ |
| `board/responseboard.html` | ボード（見本データを中に埋め込んである） |
| `scripts/make_sample.py` | 見本データを作る（一時的な git リポジトリで 7 回分を本当に記録する） |

## rb コマンド

```powershell
C:\Repos\claude\ResponseBoard\scripts\rb.cmd done "ビルド成功" --summary "テスト 12 件通過"
rb fail "デプロイ失敗"
rb ask "公開の確認" --ask "公開しますか？|*はい|いいえ"    # * はおすすめの選択肢
rb running "変換中"                                        # 実行中。終わるまで記録しない
rb record                                                  # LLM が書いた status.json を記録する
rb check                                                   # 形だけ確かめる
rb history                      # このフォルダの履歴
rb history --all --status failed --limit 50                # 全プロジェクトの失敗
rb history --json
rb import-jsonl <古い history.jsonl>
rb serve [--open]                                          # 手元のボード http://127.0.0.1:28000/
rb statusline [--no-color]                                 # ステータスライン用の 1 行
```

どれも今いるフォルダ（`--dir` で変えられる）の `.responseboard/status.json` を扱う。
`done` / `fail` / `ask` は、前の `status.json` の `section` と `tasks`（カンバン）を引き継ぐ。

## 他のプロジェクトに入れる（Claude Code）

```powershell
python C:\Repos\claude\ResponseBoard\hooks\install_claude.py <プロジェクトのフォルダ> [--name 表示名]
```

本体はコピーせず、このフォルダを絶対パスで呼ぶ。何度実行しても同じ結果になる（済んでいる所は飛ばす）。

1. `.claude/settings.local.json` に UserPromptSubmit / Stop フックと statusLine を足す（この PC 用なので local。
   すでに別の statusLine があればそのまま残す）
2. `CLAUDE.md` に `<!-- responseboard:start -->` 〜 `end` の節を足す（無ければ作る）
3. `.gitignore` があれば `.responseboard/` を足す
4. 最初の 1 件を記録して、ボードのプロジェクト一覧に出す

外すときは、1 のフックと statusLine、2 の節、`.responseboard/` を消す。

## 履歴のデータベース

- 置き場所: `%USERPROFILE%\.responseboard\runs.db`（全プロジェクト共通）。`RESPONSEBOARD_DB` で変えられる
- 表 `runs` に 1 回 1 行。`status.json` の中身は丸ごと `raw_json` に入れ、検索に使う項目（日時・プロジェクト・
  道具・状態・題・要回答の数・失敗した確認の数）だけを別の列にする。形が変わっても古い行を読める
- 同じ内容をもう一度記録しても行は増えない（`run_key` は内容のハッシュ）
- WAL モードと 5 秒の待ち時間で、複数のセッションが同時に書いても壊れない
- `status.json` の `history` は、記録のたびにこの表から同じプロジェクトの直近 10 件で埋める
- 各行に `section_id`（セクションの id）を持つ。履歴はセクションごとにまとめて見る
- 古い形（user_version 1）のデータベースは、開いたときに 2 へ作り直す（同時に開いても 1 回だけ）

## git の差分

プロジェクトが git の中にあれば、rb は記録のたびに作業中のファイルの写し（git の tree）を取り、
**前の実行の写しとの差分**を表 `run_git` に残す。コミットしていない変更も、新しいファイルも、その 1 回で変わった分だけが出る。

- 写しは別の index ファイルで作る。ユーザーの index・ブランチ・ref は変えない（`.git/objects` に物が増えるだけで、git add と同じ）
- 差分の文字は記録のときに保存するので、あとで git gc が写しを消しても見られる。長い差分は 50 万字で切る
- 前の写しが無いときは最後のコミットから、コミットも無いときは最初からの差分になる。間のコミットは一覧にする
- `.responseboard/` は含めない。git の呼び出しは全部で 6 秒まで。git が無い・遅いときは差分なしで記録する

## 手元のボード（rb serve）

`rb serve` で `http://127.0.0.1:28000/` にボードを出す（ポート 28000 は LPortMan に登録済み）。
127.0.0.1 だけで受け付け、読むのは履歴のデータベースに載っているプロジェクトの `status.json` だけ。

- 右上の選択でプロジェクトを切り替える（全プロジェクト共通の runs.db から一覧を作る）
- 3 秒ごとに変化を確かめ、`status.json` か履歴が変わったら描き直す。選びかけの答えと、開いた欄は残す
- 同じ HTML を Artifact や `file://` で開いたときは、見本データ（`samples/board.sample.json`）を出す

| API | 中身 |
|---|---|
| `/api/overview?path=` | 履歴（新しい順）。セクション・各タスクの列・git の行数つき |
| `/api/run?path=&id=` | その回の `status.json`（記録した時のまま）と git の変更 |
| `/api/diff?path=&id=` | その回の差分（文字） |

## ステータスライン

`rb statusline` は `status.json` から 1 行を作る（データベースは読まないので速い）。

```
▲ あなたの番 2件 │ 手元のボードを作った │ ■■■□ 3/4 │ 5分前
◐ 作業中 3分 │ 前回: ● 完了 │ 前の作業 │ 10分前        ← 質問を送ったあと、status.json が書かれるまで
```

Claude Code では `.claude/settings.json` の `statusLine` から `hooks/rb_statusline.py` を呼ぶ（5 秒ごとに更新）。
他の道具のステータスバーやプロンプトからは `rb statusline` を呼ぶ。

## ボードの形

```
最新の信号（いつも一番新しい実行）
履歴 ── セクションごとの流れ。丸 1 つが 1 回の実行。押すとその時点を見る
  第 1 段 ●─●─▲
  第 2 段 ✕─●─●
  第 3 段 ▲
過去（したこと・しなかったこと） │ 現在（取り組み範囲・プロジェクト全体） │ 未来（次にやること・質問）
```

- **履歴は別の軸**。丸を選ぶと、過去・現在・未来の 3 枚がその時点の中身に変わる（現在＝その時点の段取りとカンバン、
  未来＝その時点の予定と「実際にこのあとしたこと」）
- **絞り込みは双方向**（クロスフィルター）。セクションを押すと、そのセクションの実行だけが明るく残り、カンバンもそのセクションに絞る。
  カンバンのカードの「履歴で見る」を押すと、そのタスクを動かした実行（列が変わった・次にやることに指した）だけが明るく残る
- 3 枚とも、ふだんは 1 行の要約と札だけ。中身（変えたファイル・確認・動いたタスク・しなかったこと・差分・説明・段取り・カンバン・手順）は折り畳む。
  開いた欄は描き直しても開いたまま。質問だけは、答えが要るので最新のときは開いて出す
- 差分は「± 差分」を開いたときに読み、ファイルごとにさらに畳む

### 絵文字と記号の使い方

1 つの記号に 1 つの意味だけを持たせ、必ず言葉と並べる。

| 記号 | 意味 | 使う所 |
|---|---|---|
| ● ▲ ✕ ◐ | 完了・あなたの番・失敗・実行中 | 信号、履歴の丸、札。形が違うので色が見分けにくくても読める |
| 🕘 📍 🧭 | 過去・現在・未来 | 3 枚の見出し。大きな区切りだけに使い、目印にする |
| ✅ ⊘ ± 📝 ⇄ | したこと・しなかったこと・差分・説明・動いたタスク | 折り畳みの見出し |
| ● ◐ ○ ✕ | 段取りの 完了・作業中・未着手・止まっている | 段取りの欄 |
| +12 −3 | 足した行・消した行（緑・赤） | 差分 |

飾りの絵文字は置かない。押せるものは枠か塗りのある形（丸・枠つきの見出し・ボタン）にし、押せない札は枠なしの平らな形にする。

`run.next`（2026-10-19）: 再開したときに会話を読み返さずに始められるように、次に取りかかることを書く。
`title`（40 字）・`detail`（400 字。何をするか・なぜ次か・終わりの条件）・`steps`（最初の手順、80 字 × 8 つまで）・`task`（tasks の id）。
`tasks[].detail`（400 字）は、項目ごとの「何をするか・終わりの条件」。カンバンと一緒に次の実行へ引き継ぐ。
⛔ 前は次の項目の中身を書く欄が無く（題名 40 字だけ）、長い説明を `run.notes` に入れても毎回上書きされて「くわしく」に畳まれ、再開時に判断しにくかった

`run.asks` には、ユーザーの判断が要ることだけを入れる。長い説明は `run.notes` に入れ、ボードでは「くわしく」に寄せる。
`source.written_by` に、LLM が自分で書いたか（`llm`）、フックか（`hook`）、最終応答から抜き出したか（`extractor`）、
rb で手で書いたか（`manual`）を残す。

## 確かめ方

- 本体・フック・配信: `python -m unittest tests.test_hook tests.test_rb tests.test_server tests.test_statusline tests.test_install tests.test_git -v`
- ボード: `npm install` のあと `npm test`（jsdom で見本を描き、時点の切り替え・差分・絞り込み・答えのコピーと、
  `[object ...]`・`false`・`undefined` の文字が出ないことを見る）。
  DOM の `append()` に配列を渡すと `[object HTMLDivElement]` という文字になるので、配列は `...` で展開して渡す
- 見本とスキーマ: `uv run --no-project --with jsonschema python -c "..."`（Draft 2020-12）
