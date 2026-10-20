"""The conversation of a run: reading a Claude Code transcript, saving it through the Stop hook, and /api/talk."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "hooks"))

from claude_transcript import turn_talk  # noqa: E402
from responseboard import talk  # noqa: E402

SAMPLE = json.loads((ROOT / "samples" / "status.sample.json").read_text(encoding="utf-8"))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def transcript(path, now, proj):
    old, new = iso(now - 600), iso(now + 1)
    rows = [
        {"type": "user", "timestamp": old, "message": {"role": "user", "content": "前の回の質問"}},
        {"type": "user", "timestamp": new, "message": {"role": "user", "content":
            "ボタンを直して<system-reminder>内部の注意</system-reminder>"}},
        {"type": "user", "timestamp": new, "isMeta": True, "message": {"role": "user", "content": "meta"}},
        {"type": "assistant", "timestamp": new, "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "考え中"},
            {"type": "text", "text": "直します。"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "npm test", "description": "Run the tests"}},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(proj / "src" / "a.js"),
                                                           "old_string": "秘密の古い中身", "new_string": "x"}},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "foo"}}]}},
        {"type": "user", "timestamp": new, "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "長い出力"}]}},
        {"type": "assistant", "timestamp": new, "isSidechain": True, "message": {"role": "assistant", "content": [
            {"type": "text", "text": "サブエージェント"}]}},
        {"type": "attachment", "timestamp": new},
        {"type": "assistant", "timestamp": new, "message": {"role": "assistant", "content": [
            {"type": "text", "text": "直しました。"}]}},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n", encoding="utf-8")


class TranscriptTest(unittest.TestCase):
    def test_turn_keeps_words_and_drops_edit_text(self):
        d = Path(tempfile.mkdtemp())
        try:
            now = time.time()
            transcript(d / "t.jsonl", now, d)
            items = turn_talk(d / "t.jsonl", now, d)
            self.assertEqual([(i["role"], i["text"]) for i in items], [
                ("user", "ボタンを直して"), ("assistant", "直します。"), ("tool", "Bash: Run the tests"),
                ("edit", "src/a.js"), ("tool", "Grep: foo"), ("assistant", "直しました。")])
            self.assertNotIn("秘密", json.dumps(items, ensure_ascii=False))
            self.assertEqual(turn_talk(d / "missing.jsonl", now), [])
        finally:
            shutil.rmtree(d)

    def test_clean_drops_bad_items_and_caps(self):
        items, cut = talk.clean([{"role": "user", "text": " hi "}, {"role": "x", "text": "no"}, {"role": "tool"},
                                 {"role": "assistant", "text": "a" * (talk.ITEM_MAX + 5)}])
        self.assertEqual([i["role"] for i in items], ["user", "assistant"])
        self.assertEqual(items[0]["text"], "hi")
        self.assertTrue(cut)
        self.assertEqual(talk.clean("nope"), ([], False))


class HookTalkTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = self.dir / "runs.db"
        self.env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.dir), RESPONSEBOARD_DB=str(self.db))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def hook(self, cmd, **payload):
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "rb_hook.py"), cmd],
                           input=json.dumps(payload).encode("utf-8"), capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("ascii").strip()

    def test_stop_saves_the_turn_and_api_serves_it(self):
        self.hook("prompt")
        now = time.time()
        transcript(self.dir / "t.jsonl", now, self.dir)
        d = json.loads(json.dumps(SAMPLE))
        d.pop("history", None)
        d["project"].pop("path", None)
        (self.dir / ".responseboard" / "status.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.hook("stop", transcript_path=str(self.dir / "t.jsonl")), "")
        con = sqlite3.connect(self.db)
        try:
            n, raw = con.execute("SELECT items, talk_json FROM run_talk").fetchone()
        finally:
            con.close()
        self.assertEqual(n, 6)
        self.assertEqual(json.loads(raw)[3], {"role": "edit", "text": "src/a.js", "at": json.loads(raw)[3]["at"]})

        os.environ["RESPONSEBOARD_DB"], old = str(self.db), os.environ.get("RESPONSEBOARD_DB")
        try:
            from responseboard import store
            con = store.connect()
            try:
                path = str(self.dir.resolve()).replace("\\", "/")
                ov = store.overview(con, path)
                self.assertEqual(ov[0]["talk"], 6)
                t = store.get_talk(con, path, ov[0]["id"])
                self.assertEqual((t["items"][0]["text"], t["truncated"]), ("ボタンを直して", False))
                self.assertIsNone(store.get_talk(con, "elsewhere", ov[0]["id"]))
            finally:
                con.close()
        finally:
            if old is None:
                os.environ.pop("RESPONSEBOARD_DB")
            else:
                os.environ["RESPONSEBOARD_DB"] = old


if __name__ == "__main__":
    unittest.main()
