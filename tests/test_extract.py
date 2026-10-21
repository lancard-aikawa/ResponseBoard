"""rb extract: a run drafted from a final answer by a local LLM, here a fake OpenAI-compatible server."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from responseboard import extract  # noqa: E402
from responseboard import status as st  # noqa: E402

ANSWER = "ログイン画面を直しました。テスト 12 件は通りました。メールの送信はまだ作っていません。\n次はどちらにしますか？"


class FakeLLM(BaseHTTPRequestHandler):
    reply = "{}"
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        FakeLLM.seen.append((self.path, body))
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": FakeLLM.reply}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.env = dict(os.environ, RESPONSEBOARD_DB=str(self.dir / "runs.db"), PYTHONPATH=str(ROOT / "src"))
        FakeLLM.seen = []

    def tearDown(self):
        shutil.rmtree(self.dir)

    def rb(self, *args, stdin=b""):
        return subprocess.run([sys.executable, "-m", "responseboard", "--dir", str(self.dir), *args],
                              input=stdin, capture_output=True, env=self.env)

    def test_messy_model_output_becomes_a_valid_run(self):
        raw = {"status": "maybe", "title": "ログイン画面を直した" + "あ" * 50, "summary": "テスト 13 件が通った",
               "asks": [{"question": "次はどちらにしますか？", "options": ["パスワードの再設定", {"label": "x"}, "二段階認証"], "recommended": 1},
                        {"question": "選択肢なし"}, "壊れた"],
               "checks": [{"name": "テスト 12 ิ件", "result": "ok"}], "not_done": [{"title": "メールの送信", "reason": "まだ"}, {}],
               "next": {"title": ""}}
        run = extract.to_run(raw, ANSWER)
        d = {"schema": st.SCHEMA_ID, "project": {"name": "p"}, "source": {"tool": "t", "written_by": "extractor"}, "run": run}
        self.assertEqual(st.validate(d), [])
        self.assertEqual(run["status"], "needs_you")  # there is a question, whatever status said
        self.assertEqual(len(run["title"]), st.TITLE_MAX)
        self.assertEqual(run["asks"][0]["options"], ["パスワードの再設定", "二段階認証"])
        self.assertEqual(run["asks"][0]["recommended"], 1)
        self.assertEqual(run["asks"][1]["options"], ["はい", "いいえ"])
        self.assertEqual(run["checks"], [{"name": "テスト 12 件", "result": "skip"}])  # stray Thai sign dropped
        self.assertEqual(run["not_done"], [{"title": "メールの送信", "reason": "まだ"}])
        self.assertNotIn("next", run)
        self.assertEqual(extract.unseen_numbers(run, ANSWER), ["13"])

    def test_parse_takes_the_object_out_of_fences(self):
        self.assertEqual(extract.parse('```json\n{"title": "x"}\n```'), {"title": "x"})
        for bad in ("no json", "[1, 2]", "{broken"):
            with self.assertRaises(extract.ExtractError):
                extract.parse(bad)

    def test_rb_extract_records_with_the_answer_as_talk(self):
        st.write_json(st.status_path(self.dir), {"schema": st.SCHEMA_ID, "project": {"name": "demo"}, "source": {"tool": "x"},
                                                 "run": {"status": "done", "title": "前の回"},
                                                 "tasks": [{"id": "t1", "title": "メール", "column": "todo"}]})
        FakeLLM.reply = json.dumps({"status": "done", "title": "ログイン画面を直した", "summary": "テスト 12 件が通った。",
                                    "checks": [{"name": "テスト 12 件", "result": "pass"}],
                                    "not_done": [{"title": "メールの送信", "reason": "まだ作っていない"}]}, ensure_ascii=False)
        p = self.rb("extract", "--tool", "aider", "--url", self.url, "--model", "fake", stdin=ANSWER.encode("utf-8"))
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        path, body = FakeLLM.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "fake")
        self.assertIn(ANSWER, body["messages"][1]["content"])
        d = st.read_json(st.status_path(self.dir))
        self.assertEqual(d["source"], {"tool": "aider", "written_by": "extractor"})
        self.assertEqual(d["project"]["name"], "demo")
        self.assertEqual(d["tasks"][0]["id"], "t1")  # the kanban carries over
        self.assertIn("fake が抜き出した", d["run"]["notes"])
        con = sqlite3.connect(self.dir / "runs.db")
        try:
            tool, writer, talk = con.execute("SELECT r.tool, r.written_by, t.talk_json FROM runs r JOIN run_talk t ON t.run_id = r.id").fetchone()
        finally:
            con.close()
        self.assertEqual((tool, writer), ("aider", "extractor"))
        self.assertEqual(json.loads(talk), [{"role": "assistant", "text": ANSWER}])

    def test_a_changed_number_is_reported(self):
        FakeLLM.reply = json.dumps({"status": "done", "title": "ログイン画面を直した", "summary": "テスト 13 件が通った。"}, ensure_ascii=False)
        p = self.rb("extract", "--url", self.url, "--no-record", stdin=ANSWER.encode("utf-8"))
        self.assertEqual(p.returncode, 0)
        self.assertIn("numbers not in the answer: 13", p.stderr.decode("utf-8"))
        self.assertIn("元の文に無い数字がある（13）", st.read_json(st.status_path(self.dir))["run"]["notes"])
        self.assertFalse((self.dir / "runs.db").exists())

    def test_no_llm_is_a_clear_error(self):
        p = self.rb("extract", "--url", "http://127.0.0.1:9/v1", stdin=ANSWER.encode("utf-8"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("cannot reach the local LLM", p.stderr.decode("utf-8"))
        self.assertFalse(st.status_path(self.dir).exists())


if __name__ == "__main__":
    unittest.main()
