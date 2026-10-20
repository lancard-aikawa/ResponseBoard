"""Git change per run (gitsnap + store.add_git) and the section axis, against a temp repo and database."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from responseboard import status as st  # noqa: E402
from responseboard import store  # noqa: E402


def status(title, section="第 1 段: 形", finished="2026-10-20T10:00:00+09:00", tasks=None):
    return {"schema": st.SCHEMA_ID, "project": {"name": "demo"}, "source": {"tool": "test"},
            "run": {"status": "done", "title": title, "finished_at": finished,
                    "not_done": [{"title": "見た目の確認", "reason": "画面がない"}]},
            "section": {"title": section, "steps": [{"title": "a", "state": "doing"}]},
            "tasks": tasks or [{"id": "t1", "title": "x", "column": "doing"}]}


class GitTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.old_db = os.environ.get("RESPONSEBOARD_DB")
        os.environ["RESPONSEBOARD_DB"] = str(self.dir / "runs.db")
        self.repo = self.dir / "repo"
        self.proj = self.repo / "app"
        self.proj.mkdir(parents=True)
        self.git("init", "-q")
        (self.proj / "a.txt").write_text("one\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("RESPONSEBOARD_DB", None)
        else:
            os.environ["RESPONSEBOARD_DB"] = self.old_db
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo,
                              check=True, capture_output=True).stdout.decode("utf-8")

    def record(self, d):
        st.write_json(st.status_path(self.proj), d)
        errs, inserted = store.record_file(self.proj)
        self.assertEqual(errs, [])
        self.assertTrue(inserted)
        con = store.connect()
        try:
            return store.overview(con, st.norm_path(self.proj))[0], con.execute("SELECT MAX(id) FROM runs").fetchone()[0]
        finally:
            con.close()

    def patch(self, rid):
        con = store.connect()
        try:
            return store.get_patch(con, st.norm_path(self.proj), rid), store.get_run(con, st.norm_path(self.proj), rid)
        finally:
            con.close()

    def test_each_run_shows_only_its_own_change(self):
        (self.proj / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.proj / "新規.txt").write_text("new\n", encoding="utf-8")  # untracked and not committed
        first, rid1 = self.record(status("1 回目", finished="2026-10-20T10:00:00+09:00"))
        self.assertEqual(first["git"], {"files": 2, "add": 2, "del": 0})
        p1, run1 = self.patch(rid1)
        self.assertIn("+two", p1)
        self.assertEqual(run1["git"]["base_kind"], "head")
        self.assertEqual(sorted(f["path"] for f in run1["git"]["files"]), ["app/a.txt", "app/新規.txt"])
        self.assertNotIn(".responseboard", p1)

        (self.proj / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        _, rid2 = self.record(status("2 回目", finished="2026-10-20T11:00:00+09:00"))
        p2, run2 = self.patch(rid2)
        self.assertEqual(run2["git"]["base_kind"], "prev")
        self.assertIn("+three", p2)
        self.assertNotIn("+two", p2)        # already shown in the first run
        self.assertNotIn("新規", p2)
        # the user's own index is untouched: the new file is still untracked
        self.assertIn("?? app/", self.git("status", "--porcelain"))

    def test_commits_between_runs_are_listed(self):
        self.record(status("1 回目", finished="2026-10-20T10:00:00+09:00"))
        (self.proj / "a.txt").write_text("changed\n", encoding="utf-8")
        self.git("commit", "-q", "-am", "直した")
        _, rid = self.record(status("2 回目", finished="2026-10-20T11:00:00+09:00"))
        self.assertEqual([c["subject"] for c in self.patch(rid)[1]["git"]["commits"]], ["直した"])

    def test_not_in_git(self):
        plain = self.dir / "plain"
        plain.mkdir()
        st.write_json(st.status_path(plain), status("git なし"))
        self.assertEqual(store.record_file(plain), ([], True))
        con = store.connect()
        try:
            row = store.overview(con, st.norm_path(plain))[0]
        finally:
            con.close()
        self.assertNotIn("git", row)
        self.assertEqual((row["section_id"], row["section_title"]), ("第 1 段", "第 1 段: 形"))
        self.assertEqual(row["tasks"], {"t1": "doing"})

    def test_old_database_is_migrated(self):
        db = Path(os.environ["RESPONSEBOARD_DB"])
        con = sqlite3.connect(db)
        con.executescript(store.SCHEMA_V1)
        con.execute("PRAGMA user_version=1")
        d = status("前からある行", section="第 3 段: 古い")
        d["project"]["path"] = "x"
        con.execute("INSERT INTO runs (run_key, recorded_at, finished_at, project, project_path, tool, status, title,"
                    " raw_json) VALUES ('k', 'r', 'f', 'p', 'x', 't', 'done', 'old', ?)", (json.dumps(d),))
        con.commit()
        con.close()
        con = store.connect()
        try:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], store.VERSION)
            self.assertEqual(con.execute("SELECT section_id FROM runs").fetchone()[0], "第 3 段")
            self.assertEqual(con.execute("SELECT count(*) FROM run_talk").fetchone()[0], 0)
        finally:
            con.close()


class StatusTest(unittest.TestCase):
    def test_section_id(self):
        self.assertEqual(st.section_id({"title": "第 4 段: 使う"}), "第 4 段")
        self.assertEqual(st.section_id({"title": "第 4 段：使う"}), "第 4 段")
        self.assertEqual(st.section_id({"id": "s4", "title": "第 4 段: 使う"}), "s4")
        self.assertIsNone(st.section_id(None))

    def test_not_done_is_checked(self):
        d = status("x")
        self.assertEqual(st.validate(d), [])
        d["run"]["not_done"] = [{"reason": "no title"}]
        self.assertTrue(any("not_done" in e for e in st.validate(d)))

    def test_not_done_destination_is_checked(self):
        d = status("x")
        d["run"]["not_done"] = [{"title": "a", "task": "t1"}, {"title": "b", "drop": True}]
        self.assertEqual(st.validate(d), [])
        d["run"]["not_done"] = [{"title": "a", "task": "t9"}]
        self.assertTrue(any("is not an id in tasks" in e for e in st.validate(d)))
        d["run"]["not_done"] = [{"title": "a", "task": "t1", "drop": True}]
        self.assertTrue(any("both task and drop" in e for e in st.validate(d)))


class LeftoversTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.old_db = os.environ.get("RESPONSEBOARD_DB")
        os.environ["RESPONSEBOARD_DB"] = str(self.dir / "runs.db")
        self.proj = self.dir / "app"
        self.proj.mkdir()

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("RESPONSEBOARD_DB", None)
        else:
            os.environ["RESPONSEBOARD_DB"] = self.old_db
        shutil.rmtree(self.dir)

    def record(self, title, finished, not_done):
        d = status(title, finished=finished)
        d["run"]["not_done"] = not_done
        st.write_json(st.status_path(self.proj), d)
        errs, _ = store.record_file(self.proj)
        self.assertEqual(errs, [])
        return st.read_json(st.status_path(self.proj))

    def test_left_behind_until_picked_up_with_the_same_title(self):
        out = self.record("one", "2026-10-20T10:00:00+09:00", [{"title": "見た目", "reason": "後で"}, {"title": "公開", "task": "t1"}])
        self.assertEqual([x["title"] for x in out["left_behind"]], ["見た目"])
        out = self.record("two", "2026-10-20T11:00:00+09:00", [{"title": "見た目", "drop": True}])
        self.assertEqual(out["left_behind"], [])
        con = store.connect()
        try:
            states = {x["title"]: x["state"] for x in store.leftovers(con, st.norm_path(self.proj))}
        finally:
            con.close()
        self.assertEqual(states, {"見た目": "drop", "公開": "task"})

    def test_a_next_the_following_run_did_not_take_up_is_left_behind(self):
        a = status("one", finished="2026-10-20T10:00:00+09:00")
        a["run"]["next"] = {"title": "本物で確かめる"}
        b = status("two", finished="2026-10-20T11:00:00+09:00")
        b["run"]["next"] = {"title": "公開する", "task": "t1"}
        c = status("three", finished="2026-10-20T12:00:00+09:00")   # t1 moved: 公開する was taken up
        c["tasks"] = [{"id": "t1", "title": "x", "column": "done"}]
        self.assertEqual(store.skipped_next(a, b)["title"], "本物で確かめる")
        self.assertIsNone(store.skipped_next(b, c))
        b2 = json.loads(json.dumps(b))
        b2["run"]["not_done"] = [{"title": "本物で確かめる", "drop": True}]
        self.assertIsNone(store.skipped_next(a, b2))  # said where it went
        b3 = json.loads(json.dumps(b))
        b3["run"]["from_next"] = True
        self.assertIsNone(store.skipped_next(a, b3))  # said it took it up
        b4 = json.loads(json.dumps(b))
        b4["run"]["title"] = "本物で確かめる（全体の入れ方）"
        self.assertIsNone(store.skipped_next(a, b4))  # the title carries it
        for d in (a, b, c):
            st.write_json(st.status_path(self.proj), d)
            self.assertEqual(store.record_file(self.proj)[0], [])
        out = st.read_json(st.status_path(self.proj))
        self.assertIn("本物で確かめる", [x["title"] for x in out["left_behind"]])
        con = store.connect()
        try:
            ov = store.overview(con, st.norm_path(self.proj))
        finally:
            con.close()
        two = next(x for x in ov if x["title"] == "two")
        self.assertTrue(any(x.get("auto") and x["title"] == "本物で確かめる" for x in two["not_done"]))


if __name__ == "__main__":
    unittest.main()
