"""The conversation of a run: reading a Claude Code transcript, saving it through the Stop hook, and /api/talk."""
import contextlib
import io
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

    def test_the_tools_own_messages_are_not_the_user(self):
        from claude_transcript import user_item
        notice = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
                  "<summary>Background command \"Run the tests\" completed (exit code 0)</summary>\n</task-notification>")
        self.assertEqual(user_item(notice, "t"), {"role": "notice", "text": 'Background command "Run the tests" completed (exit code 0)', "at": "t"})
        self.assertEqual(user_item("<task-notification><status>stopped</status></task-notification>"),
                         {"role": "notice", "text": "background task stopped"})
        self.assertEqual(user_item("<ide_opened_file>The user opened a.py</ide_opened_file>ここを直して"), {"role": "user", "text": "ここを直して"})
        self.assertIsNone(user_item("<ide_selection>x</ide_selection><system-reminder>r</system-reminder>"))
        self.assertEqual(user_item("<command-message>loop</command-message>\n<command-name>/loop</command-name>\n<command-args>5m</command-args>"),
                         {"role": "user", "text": "/loop 5m"})
        self.assertEqual(user_item("<local-command-stdout>Set model to opus</local-command-stdout>"), {"role": "notice", "text": "Set model to opus"})
        self.assertIsNone(user_item("<local-command-stdout></local-command-stdout>"))
        self.assertIn("notice", talk.ROLES)

    def test_retag_saved_talk(self):
        import claude_backfill
        old = [{"role": "user", "text": "<task-notification><summary>done</summary></task-notification>", "at": "t"},
               {"role": "assistant", "text": "<task-notification> in prose stays"}, {"role": "user", "text": "ふつうの頼み"}]
        self.assertEqual(claude_backfill.retag(old), [{"role": "notice", "text": "done", "at": "t"},
                                                      old[1], {"role": "user", "text": "ふつうの頼み"}])

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


class BackfillTest(unittest.TestCase):
    """hooks/claude_backfill.py: runs recorded without a talk get one from the transcripts, matched by time."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.old_db = os.environ.get("RESPONSEBOARD_DB")
        os.environ["RESPONSEBOARD_DB"] = str(self.dir / "runs.db")

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("RESPONSEBOARD_DB", None)
        else:
            os.environ["RESPONSEBOARD_DB"] = self.old_db
        shutil.rmtree(self.dir)

    def test_fills_only_runs_without_talk_by_their_window(self):
        import claude_backfill
        from responseboard import store
        proj, now = "C:/x/proj", time.time()
        folder = self.dir / "projects" / "C--x-proj"
        folder.mkdir(parents=True)
        say = lambda t, role, text: {"type": role, "timestamp": iso(now + t), "message": {"role": role, "content":
            text if role == "user" else [{"type": "text", "text": text}]}}
        rows = [say(-10000, "user", "古い話"), say(-900, "user", "一つ目の頼み"), say(-890, "assistant", "一つ目の答え"),
                say(-500, "user", "二つ目の頼み"), say(-490, "assistant", "二つ目の答え"), say(-300, "user", "三つ目の頼み")]
        (folder / "s.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        con = store.connect()
        try:
            for i, (title, at) in enumerate([("一つ目", -800), ("二つ目", -400), ("三つ目", -200)]):
                d = json.loads(json.dumps(SAMPLE))
                d["project"] = {"name": "proj", "path": proj}
                d["run"].update(title=title, finished_at=datetime.fromtimestamp(now + at).astimezone().isoformat())
                store.insert(con, d)
                con.execute("UPDATE runs SET recorded_at = ? WHERE run_key = ?",
                            (datetime.fromtimestamp(now + at).astimezone().isoformat(timespec="seconds"), store.run_key(d)))
                if i == 2:
                    store.add_talk(con, store.run_key(d), [{"role": "user", "text": "もう入っている"}])
            con.commit()
            found = claude_backfill.plan(con, proj, claude_backfill.all_items(folder, Path(proj)), 6)
        finally:
            con.close()
        self.assertEqual([x["title"] for x in found], ["一つ目", "二つ目"])
        # the first run takes only its last turn, not the old talk before it
        self.assertEqual([it["text"] for it in found[0]["talk"]], ["一つ目の頼み", "一つ目の答え"])
        self.assertEqual([it["text"] for it in found[1]["talk"]], ["二つ目の頼み", "二つ目の答え"])

        with contextlib.redirect_stdout(io.StringIO()):
            claude_backfill.main(["--project", proj, "--transcripts", str(self.dir / "projects")])
        con = store.connect()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM run_talk").fetchone()[0], 1, "without --apply nothing is saved")
        finally:
            con.close()
        with contextlib.redirect_stdout(io.StringIO()):
            claude_backfill.main(["--project", proj, "--apply", "--transcripts", str(self.dir / "projects")])
        con = store.connect()
        try:
            talks = {r[0]: json.loads(r[1]) for r in con.execute("SELECT r.title, t.talk_json FROM run_talk t JOIN runs r ON r.id = t.run_id")}
        finally:
            con.close()
        self.assertEqual(sorted(talks), ["一つ目", "三つ目", "二つ目"])
        self.assertEqual(talks["三つ目"], [{"role": "user", "text": "もう入っている"}])  # left alone


if __name__ == "__main__":
    unittest.main()
