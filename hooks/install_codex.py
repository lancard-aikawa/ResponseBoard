"""Set up ResponseBoard for Codex, for every folder (user) or for one project.

  python hooks/install_codex.py --scope user                 every folder, minus the deny list
  python hooks/install_codex.py --scope project <folder>     one project [--name NAME]
  add --uninstall to take it out again (the history and .responseboard/ folders are kept)

Codex hooks take the same payload and answers as Claude Code's, so this shares install_claude.py.
Codex runs a hook only after you trust it: open Codex and run /hooks once after installing
(and again after ResponseBoard moves, since trust follows the command). Project hooks also need
the project itself to be trusted.

user ($CODEX_HOME/hooks.json, default ~/.codex/hooks.json):
  1. hooks SessionStart (gives Codex the rules), UserPromptSubmit, Stop, merged into the file
  2. the deny list %USERPROFILE%/.responseboard/ignore (created with comments if missing)
project:
  1. .codex/hooks.json            hooks UserPromptSubmit, Stop
  2. AGENTS.md                    a ResponseBoard section between markers (file created if missing)
  3. .gitignore                   ".responseboard/" (only if the project has a .gitignore)
  4. .responseboard/status.json   a first "done" run, recorded, so the project appears on the board

Codex has no status line of its own to fill, so there is none here; the board shows the result.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from install_claude import (RB_ROOT, deny_list, install_gitignore, install_rules, install_settings, seed,
                            uninstall_rules, uninstall_settings)

TOOL = "codex"
TRUST = "next: open Codex and run /hooks to trust the ResponseBoard hooks (Codex skips untrusted hooks)"


def user_hooks() -> Path:
    base = os.environ.get("CODEX_HOME")
    return (Path(base) if base else Path.home() / ".codex") / "hooks.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Set up ResponseBoard for Codex.")
    p.add_argument("project", nargs="?", help="the project folder (scope project)")
    p.add_argument("--scope", choices=("user", "project"), default="project",
                   help="user = every folder except the deny list; project = only this folder (default)")
    p.add_argument("--name", help="project name on the board (default: folder name)")
    p.add_argument("--uninstall", action="store_true", help="take ResponseBoard out again")
    a = p.parse_args(argv)
    if a.scope == "user":
        if a.project:
            p.error("--scope user takes no folder")
        lines = ([uninstall_settings(user_hooks())] if a.uninstall
                 else [install_settings(user_hooks(), "user", TOOL), deny_list(), TRUST])
    else:
        if not a.project:
            p.error("--scope project needs the project folder")
        proj = Path(a.project).resolve()
        if not proj.is_dir():
            print(f"error: {proj} is not a folder", file=sys.stderr)
            return 1
        if proj == RB_ROOT:
            print("error: ResponseBoard itself is set up for Claude Code; nothing to install", file=sys.stderr)
            return 1
        hooks = proj / ".codex" / "hooks.json"
        lines = ([uninstall_settings(hooks), uninstall_rules(proj, TOOL)] if a.uninstall
                 else [install_settings(hooks, "project", TOOL), install_rules(proj, TOOL), install_gitignore(proj),
                       seed(proj, a.name), TRUST])
    for line in lines:
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
