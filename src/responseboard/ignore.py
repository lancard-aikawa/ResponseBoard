"""Folders where ResponseBoard stays quiet: a deny list.

The list is the file "ignore" next to the history database (%USERPROFILE%\\.responseboard\\ignore).
One folder per line; that folder and everything under it is skipped. * and ? work as in
fnmatch, ~ is the home folder, and # starts a comment. Case is ignored on Windows.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from . import store

HEADER = """# ResponseBoard: folders where the hooks do nothing (a deny list).
# One folder per line. The folder and everything under it is skipped.
# * and ? work as wildcards, ~ is your home folder, # starts a comment.
# Add the current folder with:  rb ignore      Remove it with:  rb ignore --remove
#
# C:/Users/me/Downloads
# ~/scratch/*
"""


def path() -> Path:
    return store.db_path().parent / "ignore"


def norm(p: str | Path) -> str:
    s = os.path.normcase(os.path.expanduser(str(p))).replace("\\", "/")
    return s.rstrip("/") or "/"


def patterns() -> list[str]:
    try:
        text = path().read_text(encoding="utf-8")
    except OSError:
        return []
    return [s for s in (line.split("#", 1)[0].strip() for line in text.splitlines()) if s]


def match(root: str | Path) -> str | None:
    """The deny-list line that covers root, or None."""
    r = norm(Path(root).resolve())
    for pat in patterns():
        p = norm(pat)
        if r == p or r.startswith(p + "/") or fnmatch.fnmatchcase(r, p) or fnmatch.fnmatchcase(r, p + "/*"):
            return pat
    return None


def ensure_file() -> Path:
    f = path()
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(HEADER, encoding="utf-8", newline="\n")
    return f


def add(root: str | Path) -> bool:
    """Adds root to the list; False if it was already covered."""
    if match(root):
        return False
    f = ensure_file()
    text = f.read_text(encoding="utf-8")
    line = str(Path(root).resolve()).replace("\\", "/")
    f.write_text(text + ("" if text.endswith("\n") else "\n") + line + "\n", encoding="utf-8", newline="\n")
    return True


def remove(root: str | Path) -> bool:
    """Removes the lines that name root exactly; False if none did."""
    f = path()
    if not f.exists():
        return False
    r = norm(Path(root).resolve())
    lines = f.read_text(encoding="utf-8").splitlines()
    keep = [line for line in lines if not (line.split("#", 1)[0].strip() and norm(line.split("#", 1)[0].strip()) == r)]
    if len(keep) == len(lines):
        return False
    f.write_text("\n".join(keep) + "\n", encoding="utf-8", newline="\n")
    return True
