"""Set up ResponseBoard for Claude Code, for every folder (user) or for one project.

  python hooks/install_claude.py --scope user                 every folder, minus the deny list
  python hooks/install_claude.py --scope project <folder>     one project [--name NAME]
  add --uninstall to take it out again (the history and .responseboard/ folders are kept)

Safe to run again: each step is skipped when already done.

user (~/.claude/settings.json, or $CLAUDE_CONFIG_DIR/settings.json):
  1. hooks SessionStart (gives Claude the rules), UserPromptSubmit, Stop, and statusLine,
     merged into the existing settings. An existing statusLine is left alone.
  2. the deny list %USERPROFILE%/.responseboard/ignore (created with comments if missing)
project:
  1. .claude/settings.local.json  hooks (UserPromptSubmit, Stop) and statusLine. local = this machine only.
  2. CLAUDE.md                    a ResponseBoard section between markers (file created if missing)
  3. .gitignore                   ".responseboard/" (only if the project has a .gitignore)
  4. .responseboard/status.json   a first "done" run, recorded, so the project appears on the board
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

RB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RB_ROOT / "src"))

from claude_rules import END, START, posix, rules  # noqa: E402
from responseboard import cli  # noqa: E402
from responseboard import ignore  # noqa: E402
from responseboard import status as st  # noqa: E402

HOOK = posix(RB_ROOT / "hooks" / "rb_hook.py")
LINE = posix(RB_ROOT / "hooks" / "rb_statusline.py")
EVENTS = {
    "project": (("UserPromptSubmit", "prompt", "ResponseBoard: 開始時刻を記録"),
                ("Stop", "stop", "ResponseBoard: status.json を確認")),
    "user": (("SessionStart", "session --global", "ResponseBoard: 決まりを渡す"),
             ("UserPromptSubmit", "prompt --global", "ResponseBoard: 開始時刻を記録"),
             ("Stop", "stop --global", "ResponseBoard: status.json を確認")),
}


def user_settings() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "settings.json"


def install_settings(path: Path, scope: str) -> str:
    d = st.read_json(path) if path.exists() else {}
    hooks = d.setdefault("hooks", {})
    added = []
    for event, arg, msg in EVENTS[scope]:
        cmd = f'python "{HOOK}" {arg}'
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == cmd for g in groups for h in g.get("hooks", [])):
            continue
        groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10, "statusMessage": msg}]})
        added.append(event)
    if "statusLine" not in d:
        d["statusLine"] = {"type": "command", "command": f'python "{LINE}"', "refreshInterval": 5}
        added.append("statusLine")
    if added:
        st.write_json(path, d)
    msg = ", ".join(added) if added else "already set up"
    if LINE not in d["statusLine"].get("command", ""):
        msg += "; statusLine skipped: there is one already"
    return f"{path}: {msg}"


def uninstall_settings(path: Path) -> str:
    if not path.exists():
        return f"{path}: none"
    d = st.read_json(path)
    removed = []
    hooks = d.get("hooks", {})
    for event in list(hooks):
        groups = []
        for g in hooks[event]:
            keep = [h for h in g.get("hooks", []) if HOOK not in h.get("command", "")]
            if len(keep) != len(g.get("hooks", [])):
                removed.append(event)
            if keep:
                groups.append({**g, "hooks": keep})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if "hooks" in d and not hooks:
        del d["hooks"]
    if LINE in d.get("statusLine", {}).get("command", ""):
        del d["statusLine"]
        removed.append("statusLine")
    if removed:
        st.write_json(path, d)
    return f"{path}: " + (f"removed {', '.join(removed)}" if removed else "nothing of ResponseBoard")


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


def uninstall_rules(proj: Path) -> str:
    path = proj / "CLAUDE.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if START not in text:
        return f"{path}: no section"
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1] if END in rest else ""
    new = (head.rstrip("\n") + "\n\n" + tail.lstrip("\n")).strip("\n")
    if not new:
        path.unlink()
        return f"{path}: removed (it had only the ResponseBoard section)"
    path.write_text(new + "\n", encoding="utf-8", newline="\n")
    return f"{path}: section removed"


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


def deny_list() -> str:
    existed = ignore.path().exists()
    f = ignore.ensure_file()
    return f"{f}: " + ("kept" if existed else "created (add folders to skip, one per line)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set up ResponseBoard for Claude Code.")
    p.add_argument("project", nargs="?", help="the project folder (scope project)")
    p.add_argument("--scope", choices=("user", "project"), default="project",
                   help="user = every folder except the deny list; project = only this folder (default)")
    p.add_argument("--name", help="project name on the board (default: folder name)")
    p.add_argument("--uninstall", action="store_true", help="take ResponseBoard out again")
    a = p.parse_args(argv)
    if a.scope == "user":
        if a.project:
            p.error("--scope user takes no folder")
        lines = ([uninstall_settings(user_settings())] if a.uninstall
                 else [install_settings(user_settings(), "user"), deny_list()])
    else:
        if not a.project:
            p.error("--scope project needs the project folder")
        proj = Path(a.project).resolve()
        if not proj.is_dir():
            print(f"error: {proj} is not a folder", file=sys.stderr)
            return 1
        if proj == RB_ROOT:
            print("error: ResponseBoard itself uses .claude/settings.json; nothing to install", file=sys.stderr)
            return 1
        settings = proj / ".claude" / "settings.local.json"
        lines = ([uninstall_settings(settings), uninstall_rules(proj)] if a.uninstall
                 else [install_settings(settings, "project"), install_rules(proj), install_gitignore(proj),
                       seed(proj, a.name)])
    for line in lines:
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
