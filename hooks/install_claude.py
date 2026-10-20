"""Set up ResponseBoard for Claude Code in another project.

  python hooks/install_claude.py <project folder> [--name NAME]

Safe to run again: each step is skipped when already done.
  1. .claude/settings.local.json  hooks (UserPromptSubmit, Stop) and statusLine pointing at this
                                  checkout by absolute path. local = this machine only, not shared.
                                  An existing statusLine is left alone.
  2. CLAUDE.md                    a ResponseBoard section between markers (file created if missing)
  3. .gitignore                   ".responseboard/" (only if the project has a .gitignore)
  4. .responseboard/status.json   a first "done" run, recorded, so the project appears on the board
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RB_ROOT / "src"))

from responseboard import cli  # noqa: E402
from responseboard import status as st  # noqa: E402

START, END = "<!-- responseboard:start -->", "<!-- responseboard:end -->"


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def hook_cmd(arg: str) -> str:
    return f'python "{posix(RB_ROOT / "hooks" / "rb_hook.py")}" {arg}'


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
- `run.not_done` に今回しなかったこと・できなかったことと理由（`title` 60 字・`reason` 120 字）
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
{END}"""


def install_settings(proj: Path) -> str:
    path = proj / ".claude" / "settings.local.json"
    d = st.read_json(path) if path.exists() else {}
    hooks = d.setdefault("hooks", {})
    added = []
    for event, arg, msg in (("UserPromptSubmit", "prompt", "ResponseBoard: 開始時刻を記録"),
                            ("Stop", "stop", "ResponseBoard: status.json を確認")):
        cmd = hook_cmd(arg)
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
            continue
        groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10, "statusMessage": msg}]})
        added.append(event)
    if "statusLine" not in d:
        d["statusLine"] = {"type": "command", "command": f'python "{posix(RB_ROOT / "hooks" / "rb_statusline.py")}"',
                           "refreshInterval": 5}
        added.append("statusLine")
    elif "rb_statusline.py" not in d["statusLine"].get("command", ""):
        added.append("statusLine skipped: the project already has its own")
    if added:
        st.write_json(path, d)
    return f"{path}: " + (", ".join(added) if added else "already set up")


def install_rules(proj: Path) -> str:
    path = proj / "CLAUDE.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if START in text:
        head, rest = text.split(START, 1)
        new = head + rules() + rest.split(END, 1)[1]
        what = "section updated" if new != text else "already set up"
    else:
        new = (text.rstrip("\n") + "\n\n" if text else "") + rules() + "\n"
        what = "section added" if text else "created"
    if new != text:
        path.write_text(new, encoding="utf-8", newline="\n")
    return f"{path}: {what}"


def install_gitignore(proj: Path) -> str:
    path = proj / ".gitignore"
    if not path.exists():
        return f"{path}: none, skipped"
    lines = path.read_text(encoding="utf-8").splitlines()
    if ".responseboard/" in lines:
        return f"{path}: already set up"
    path.write_text("\n".join([*lines, ".responseboard/"]) + "\n", encoding="utf-8", newline="\n")
    return f"{path}: added .responseboard/"


def seed(proj: Path, name: str | None) -> str:
    if st.status_path(proj).exists():
        return f"{st.status_path(proj)}: already exists"
    args = ["--dir", str(proj), "done", "ResponseBoard を入れた", "--tool", "manual"]
    if name:
        args += ["--project", name]
    cli.main(args)
    return f"{st.status_path(proj)}: first run recorded"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set up ResponseBoard for Claude Code in a project.")
    p.add_argument("project")
    p.add_argument("--name", help="project name on the board (default: folder name)")
    a = p.parse_args(argv)
    proj = Path(a.project).resolve()
    if not proj.is_dir():
        print(f"error: {proj} is not a folder", file=sys.stderr)
        return 1
    if proj == RB_ROOT:
        print("error: ResponseBoard itself uses .claude/settings.json; nothing to install", file=sys.stderr)
        return 1
    for line in (install_settings(proj), install_rules(proj), install_gitignore(proj), seed(proj, a.name)):
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
