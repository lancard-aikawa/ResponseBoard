"""Runs the rb command (python -m responseboard) against a temp project and database."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class RbTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = self.dir / "demo"
        self.proj.mkdir()
        self.env = dict(os.environ, RESPONSEBOARD_DB=str(self.dir / "runs.db"),
                        PYTHONPATH=str(ROOT / "src"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def rb(self, *args, code=0, cwd=None):
        p = subprocess.run([sys.executable, "-m", "responseboard", *args], capture_output=True,
                           env=self.env, cwd=cwd or self.proj)
        self.assertEqual(p.returncode, code, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("utf-8"), p.stderr.decode("utf-8")

    def status(self):
        return json.loads((self.proj / ".responseboard" / "status.json").read_text(encoding="utf-8"))

    def test_done_writes_and_records(self):
        out, _ = self.rb("done", "ビルド成功", "--summary", "テスト 12 件通過")
        self.assertIn("recorded", out)
        d = self.status()
        self.assertEqual((d["run"]["status"], d["source"]["written_by"], d["project"]["name"]),
                         ("done", "manual", "demo"))
        out, _ = self.rb("history")
        self.assertIn("● ", out)
        self.assertIn("ビルド成功", out)

    def test_ask_parses_options_and_recommendation(self):
        self.rb("ask", "公開の確認", "--ask", "公開しますか？|*はい|いいえ")
        a = self.status()["run"]["asks"][0]
        self.assertEqual((a["question"], a["options"], a["recommended"]), ("公開しますか？", ["はい", "いいえ"], 0))
        self.assertEqual(self.status()["run"]["status"], "needs_you")

    def test_ask_with_one_option_fails(self):
        _, err = self.rb("ask", "x", "--ask", "Q|only", code=1)
        self.assertIn("2-4 options", err)

    def test_history_fills_and_kanban_carries_over(self):
        self.rb("done", "one")
        d = self.status()
        d["tasks"] = [{"id": "t1", "title": "task", "column": "doing"}]
        (self.proj / ".responseboard" / "status.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.rb("fail", "two")
        d = self.status()
        self.assertEqual([h["title"] for h in d["history"]], ["one"])
        self.assertEqual(d["tasks"][0]["column"], "doing")

    def test_next_and_task_detail_check_and_carry_over(self):
        self.rb("done", "one")
        p = self.proj / ".responseboard" / "status.json"
        d = self.status()
        d["run"]["next"] = {"title": "次の作業", "detail": "何をするか", "steps": ["a", "b"], "task": "t1"}
        d["tasks"] = [{"id": "t1", "title": "task", "column": "todo", "detail": "終わりの条件"}]
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.rb("check")
        self.rb("fail", "two")   # the kanban (with its details) carries over
        self.assertEqual(self.status()["tasks"][0]["detail"], "終わりの条件")
        d = self.status()
        d["run"]["next"] = {"detail": "no title", "steps": "not a list"}
        d["tasks"][0]["detail"] = "x" * 401
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        _, err = self.rb("check", code=1)
        self.assertIn("run.next needs a title", err)
        self.assertIn("tasks[0].detail", err)

    def test_record_with_talk(self):
        self.rb("running", "作業中")
        d = self.status()
        d["run"]["status"] = "done"
        (self.proj / ".responseboard" / "status.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        (self.dir / "talk.json").write_text(json.dumps([{"role": "user", "text": "直して"},
                                                         {"role": "assistant", "text": "直した"}]), encoding="utf-8")
        self.assertIn("recorded", self.rb("record", "--talk", str(self.dir / "talk.json"))[0])
        out, _ = self.rb("history", "--json")
        rid = json.loads(out)[0]["id"]
        os.environ["RESPONSEBOARD_DB"], old = self.env["RESPONSEBOARD_DB"], os.environ.get("RESPONSEBOARD_DB")
        try:
            sys.path.insert(0, str(ROOT / "src"))
            from responseboard import store
            con = store.connect()
            try:
                t = store.get_talk(con, str(self.proj.resolve()).replace("\\", "/"), rid)
            finally:
                con.close()
        finally:
            os.environ.pop("RESPONSEBOARD_DB") if old is None else os.environ.__setitem__("RESPONSEBOARD_DB", old)
        self.assertEqual([i["text"] for i in t["items"]], ["直して", "直した"])
        self.rb("record", "--talk", str(self.dir / "none.json"), code=1)

    def test_record_twice_adds_one_row(self):
        self.rb("done", "same")
        out, _ = self.rb("record")
        self.assertIn("already recorded", out)
        out, _ = self.rb("history", "--json")
        self.assertEqual(len(json.loads(out)), 1)

    def test_running_is_not_recorded(self):
        self.rb("running", "working")
        out, _ = self.rb("history")
        self.assertIn("no runs recorded", out)

    def test_check_reports_errors(self):
        _, err = self.rb("check", code=1)
        self.assertIn("does not exist", err)
        self.rb("done", "ok")
        self.rb("check")

    def test_left_lists_items_without_a_destination(self):
        self.rb("done", "first")
        d = self.status()
        d["run"]["not_done"] = [{"title": "見た目", "reason": "後で"}, {"title": "公開", "drop": True}]
        p = self.proj / ".responseboard" / "status.json"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.rb("record")
        out, _ = self.rb("left")
        self.assertIn("見た目 - 後で", out)
        self.assertNotIn("公開", out)
        out, _ = self.rb("left", "--all")
        self.assertIn("公開 (dropped)", out)
        self.assertEqual([x["title"] for x in self.status()["left_behind"]], ["見た目"])

    def test_check_rejects_ask_options_that_are_not_strings(self):
        self.rb("ask", "t", "--ask", "どれ？|A|B")
        d = self.status()
        d["run"]["asks"][0]["options"] = [{"label": "A"}, {"label": "B"}]
        p = self.proj / ".responseboard" / "status.json"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        _, err = self.rb("check", code=1)
        self.assertIn("run.asks[0].options[0] must be a string", err)

    def test_history_filters_and_all_projects(self):
        other = self.dir / "other"
        other.mkdir()
        self.rb("done", "alpha")
        self.rb("fail", "beta", cwd=other)
        out, _ = self.rb("history")
        self.assertNotIn("beta", out)
        out, _ = self.rb("history", "--all", "--status", "failed")
        self.assertIn("beta", out)
        self.assertIn("[other]", out)
        self.assertNotIn("alpha", out)

    def test_import_jsonl(self):
        f = self.dir / "history.jsonl"
        f.write_text(json.dumps({"finished_at": "2026-10-19T16:10:00+09:00", "status": "done",
                                 "title": "old run", "tool": "claude-code"}, ensure_ascii=False) + "\n",
                     encoding="utf-8")
        out, _ = self.rb("import-jsonl", str(f))
        self.assertIn("imported 1", out)
        out, _ = self.rb("import-jsonl", str(f))
        self.assertIn("imported 0", out)
        out, _ = self.rb("history")
        self.assertIn("old run", out)


class ScriptFilesTest(unittest.TestCase):
    def test_cmd_and_ps1_are_ascii_crlf(self):
        for p in [*ROOT.glob("scripts/*.cmd"), *ROOT.glob("scripts/*.ps1")]:
            data = p.read_bytes()
            data.decode("ascii")  # raises on non-ASCII
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""), f"{p.name} has bare LF")


if __name__ == "__main__":
    unittest.main()
