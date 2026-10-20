"""Checks the status bar line for each state (no colors, fixed clock)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from responseboard.statusline import line  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 19, 17, 0, tzinfo=JST)


class StatuslineTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / ".responseboard").mkdir()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, run, section=None, mtime=None):
        d = {"schema": "responseboard/status@1", "project": {"name": "p"}, "source": {"tool": "t"}, "run": run}
        if section:
            d["section"] = section
        f = self.dir / ".responseboard" / "status.json"
        f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        t = mtime or NOW.timestamp() - 300
        os.utime(f, (t, t))

    def run_at(self, minutes_ago, **kw):
        return {"finished_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(), **kw}

    def text(self):
        return line(self.dir, color=False, now=NOW.timestamp())

    def test_no_status(self):
        self.assertEqual(self.text(), "ResponseBoard: 記録なし")

    def test_needs_you_with_progress(self):
        self.write(self.run_at(5, status="needs_you", title="見本ボード",
                               asks=[{"id": "a", "question": "q", "options": ["x", "y"]}] * 2),
                   section={"title": "s", "steps": [{"title": "a", "state": "done"}] * 3 + [{"title": "b", "state": "todo"}]})
        self.assertEqual(self.text(), "▲ あなたの番 2件 │ 見本ボード │ ■■■□ 3/4 │ 5分前")

    def test_failed_checks_and_long_title(self):
        self.write(self.run_at(120, status="failed", title="あ" * 40,
                               checks=[{"name": "n", "result": "fail"}, {"name": "m", "result": "pass"}]))
        self.assertEqual(self.text(), "✕ 失敗 │ " + "あ" * 29 + "… │ ✕ 確認 1 │ 2時間前")

    def test_turn_in_progress_shows_previous_dimmed(self):
        self.write(self.run_at(10, status="done", title="前の作業"), mtime=NOW.timestamp() - 600)
        (self.dir / ".responseboard" / "turn.json").write_text(
            json.dumps({"started_at": NOW.timestamp() - 180}), encoding="utf-8")
        self.assertEqual(self.text(), "◐ 作業中 3分 │ 前回: ● 完了 │ 前の作業 │ 10分前")

    def test_claude_adapter_reads_project_dir_from_stdin(self):
        self.write(self.run_at(0, status="done", title="ok"), mtime=time.time())
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "rb_statusline.py")],
                           input=json.dumps({"workspace": {"project_dir": str(self.dir)}}).encode("utf-8"),
                           capture_output=True)
        out = p.stdout.decode("utf-8")
        self.assertEqual(p.returncode, 0)
        self.assertIn("● 完了", out)
        self.assertIn("\033[32m", out)  # colored for the terminal


if __name__ == "__main__":
    unittest.main()
