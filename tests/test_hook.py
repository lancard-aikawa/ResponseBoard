"""Runs hooks/rb_hook.py the way Claude Code does (JSON on stdin) against a temp project and database."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "rb_hook.py"
SAMPLE = json.loads((ROOT / "samples" / "status.sample.json").read_text(encoding="utf-8"))


class HookTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.bd = self.dir / ".responseboard"
        self.db = self.dir / "runs.db"

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_hook(self, cmd, **payload):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.dir), RESPONSEBOARD_DB=str(self.db))
        p = subprocess.run([sys.executable, str(HOOK), cmd], input=json.dumps(payload).encode("utf-8"),
                           capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        out = p.stdout.decode("ascii").strip()  # hook output must stay ASCII
        return json.loads(out) if out else None

    def write_status(self, data):
        (self.bd / "status.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def sample(self):
        d = json.loads(json.dumps(SAMPLE))
        d.pop("history", None)
        d["project"].pop("path", None)
        return d

    def rows(self):
        con = sqlite3.connect(self.db)
        try:
            return con.execute("SELECT title, status, written_by FROM runs ORDER BY id").fetchall()
        finally:
            con.close()

    def test_missing_status_blocks(self):
        self.run_hook("prompt")
        out = self.run_hook("stop", stop_hook_active=False)
        self.assertEqual(out["decision"], "block")
        self.assertIn("does not exist", out["reason"])

    def test_stale_status_blocks(self):
        self.bd.mkdir()
        self.write_status(self.sample())
        old = time.time() - 60
        os.utime(self.bd / "status.json", (old, old))
        self.run_hook("prompt")
        self.assertIn("not updated", self.run_hook("stop")["reason"])

    def test_invalid_status_blocks_with_reasons(self):
        self.run_hook("prompt")
        bad = self.sample()
        bad["run"]["status"] = "done"  # asks are present, so this is wrong
        self.write_status(bad)
        self.assertIn('must be "needs_you"', self.run_hook("stop")["reason"])
        self.assertFalse(self.db.exists() and self.rows())

    def test_stop_hook_active_never_blocks(self):
        self.run_hook("prompt")
        out = self.run_hook("stop", stop_hook_active=True)
        self.assertNotEqual(out.get("decision"), "block")
        self.assertIn("not recorded", out["systemMessage"])

    def test_the_fix_after_a_block_is_recorded(self):
        # the Stop after the fix comes with stop_hook_active; it used to return before recording (2026-10-21)
        self.run_hook("prompt")
        self.assertEqual(self.run_hook("stop", stop_hook_active=False)["decision"], "block")
        self.bd.mkdir(exist_ok=True)
        self.write_status(self.sample())
        self.assertIsNone(self.run_hook("stop", stop_hook_active=True))
        self.assertEqual(len(self.rows()), 1)

    def test_rb_stamps_time_and_id(self):
        # an LLM does not know the time: a guessed finished_at outside the turn is replaced, a missing one filled
        self.run_hook("prompt")
        d = self.sample()
        d["run"]["finished_at"] = "2020-01-01T10:00:00+09:00"
        self.write_status(d)
        before = time.time()
        self.assertIsNone(self.run_hook("stop"))
        saved = json.loads((self.bd / "status.json").read_text(encoding="utf-8"))
        from datetime import datetime
        self.assertGreater(datetime.fromisoformat(saved["run"]["finished_at"]).timestamp(), before - 5)
        first_id = saved["run"]["id"]
        self.assertNotEqual(first_id, self.sample()["run"]["id"])  # the LLM's id is not used under the hook
        # next turn: the LLM copies last run's id and leaves the time out
        self.run_hook("prompt")
        d = saved
        d["run"].pop("finished_at")
        d["run"]["title"] = "next turn"
        self.write_status(d)
        self.assertIsNone(self.run_hook("stop"))
        saved = json.loads((self.bd / "status.json").read_text(encoding="utf-8"))
        self.assertNotEqual(saved["run"]["id"], first_id)
        self.assertIn("finished_at", saved["run"])
        self.assertEqual([r[0] for r in self.rows()], [self.sample()["run"]["title"], "next turn"])

    def test_a_fix_in_the_same_turn_replaces_the_run(self):
        self.run_hook("prompt")
        self.write_status(self.sample())
        self.assertIsNone(self.run_hook("stop"))
        d = self.sample()
        d["run"]["title"] = "fixed in the same turn"
        self.write_status(d)
        self.assertIsNone(self.run_hook("stop"))
        self.assertEqual([r[0] for r in self.rows()], ["fixed in the same turn"])

    def test_no_turn_record_never_blocks(self):
        self.assertIsNone(self.run_hook("stop"))

    def test_valid_status_is_recorded_and_fills_history(self):
        for i, title in enumerate(["first", "second", "third"]):
            self.run_hook("prompt")
            d = self.sample()
            d["run"]["title"] = title
            d["run"]["finished_at"] = f"2026-10-19T16:0{i}:00+09:00"
            self.write_status(d)
            self.assertIsNone(self.run_hook("stop"))
        saved = json.loads((self.bd / "status.json").read_text(encoding="utf-8"))
        self.assertEqual([h["title"] for h in saved["history"]], ["first", "second"])
        self.assertEqual(saved["project"]["path"], str(self.dir.resolve()).replace("\\", "/"))
        # A second Stop in the same turn must not add a row.
        self.assertIsNone(self.run_hook("stop"))
        self.assertEqual(self.rows(), [("first", "needs_you", "llm"), ("second", "needs_you", "llm"),
                                       ("third", "needs_you", "llm")])


if __name__ == "__main__":
    unittest.main()
