"""What a run changed in git: a snapshot of the working tree and the diff from the previous run.

The snapshot is a git tree object written with a temporary index (git add -A into a copy of the
real index, then git write-tree), so it includes uncommitted and untracked files but never touches
the user's index, branches or refs. Only the new objects are added to .git/objects, like git add.
The diff between two snapshots is the change made during that one run, even when nothing was
committed. The patch text is saved in the database at record time, so later git gc does not lose it.

Standard library only. Every git call shares one time budget; on any failure there is no snapshot.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

BUDGET = 6.0                 # seconds for one snapshot + diff (the Stop hook has 10)
PATCH_MAX = 512_000          # characters of patch text kept per run
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
EXCLUDE = ":(exclude,glob)**/.responseboard/**"


class _Git:
    def __init__(self, cwd: Path, budget: float = BUDGET):
        self.cwd, self.deadline = cwd, time.monotonic() + budget

    def __call__(self, *args: str, env: dict | None = None) -> str | None:
        left = self.deadline - time.monotonic()
        if left <= 0:
            return None
        try:
            p = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=self.cwd, env=env,
                               capture_output=True, timeout=left, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def snapshot(root: Path, budget: float = BUDGET) -> dict | None:
    """Tree of the working tree under git. None when root is not in a git work tree or git fails."""
    git = _Git(Path(root), budget)
    top = git("rev-parse", "--show-toplevel")
    if not top:
        return None
    top_p = Path(top.strip())
    git.cwd = top_p
    rel = os.path.relpath(Path(root).resolve(), top_p.resolve()).replace("\\", "/")
    head = (git("rev-parse", "--verify", "-q", "HEAD") or "").strip() or None
    branch = (git("rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    index = (git("rev-parse", "--git-path", "index") or "").strip()
    with tempfile.TemporaryDirectory() as t:
        tmp_index = Path(t) / "index"
        real = top_p / index if index and not Path(index).is_absolute() else Path(index)
        if index and real.is_file():
            shutil.copyfile(real, tmp_index)  # keeps the stat cache, so add -A does not rehash everything
        env = dict(os.environ, GIT_INDEX_FILE=str(tmp_index))
        if git("add", "-A", "--", ".", EXCLUDE, env=env) is None:
            return None
        tree = (git("write-tree", env=env) or "").strip()
    if not tree:
        return None
    return {"top": str(top_p).replace("\\", "/"), "rel": "" if rel == "." else rel,
            "head": head, "branch": branch, "tree": tree, "_git": git}


def diff(snap: dict, prev: dict | None) -> dict:
    """Diff from the previous run's snapshot (or HEAD, or nothing) to snap. Adds files, patch, commits."""
    git: _Git = snap.pop("_git")
    base, base_kind = None, None
    if prev and prev.get("tree") and git("cat-file", "-e", prev["tree"] + "^{tree}") is not None:
        base, base_kind = prev["tree"], "prev"
    elif snap["head"]:
        base, base_kind = snap["head"] + "^{tree}", "head"
    else:
        base, base_kind = EMPTY_TREE, "empty"
    spec = ["--", snap["rel"] or ".", EXCLUDE]
    files = []
    for line in (git("diff", "--numstat", "-M", "--no-color", base, snap["tree"], *spec) or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            add, dele, path = parts
            files.append({"path": path, "add": int(add) if add.isdigit() else None,
                          "del": int(dele) if dele.isdigit() else None})
    patch = git("diff", "-M", "--no-color", "--no-ext-diff", base, snap["tree"], *spec) or ""
    commits = []
    if prev and prev.get("head") and snap["head"] and prev["head"] != snap["head"]:
        log = git("log", "--format=%h%x09%s", f"{prev['head']}..{snap['head']}", "--", snap["rel"] or ".")
        commits = [dict(zip(("hash", "subject"), l.split("\t", 1))) for l in (log or "").splitlines() if "\t" in l]
    return {**snap, "base": base, "base_kind": base_kind, "files": files, "commits": commits,
            "patch": patch[:PATCH_MAX], "truncated": len(patch) > PATCH_MAX}
