"""Codex: reading a rollout, the hooks run with --tool codex, and hooks/install_codex.py."""
import json
import os
import shutil
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

from codex_transcript import turn_talk  # noqa: E402
from responseboard import status as st  # noqa: E402

SAMPLE = json.loads((ROOT / "samples" / "status.sample.json").read_text(encoding="utf-8"))


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def item(t, payload):
    return {"timestamp": iso(t), "type": "response_item", "payload": payload}


def rollout(path, now, proj):
    say = lambda role, text: {"type": "message", "role": role, "content": [
        {"type": "input_text" if role == "user" else "output_text", "text": text}]}
    patch = f"*** Begin Patch\n*** Update File: {proj / 'src' / 'a.js'}\n@@\n-秘密の古い中身\n+x\n*** Add File: b.md\n+new\n*** End Patch"
    rows = [
        item(now - 600, say("user", "前の回の頼み")),
        {"timestamp": iso(now + 1), "type": "session_meta", "payload": {"cwd": str(proj)}},
        item(now + 1, say("user", "<environment_context>\n  <cwd>x</cwd>\n</environment_context>")),
        item(now + 1, say("user", "# AGENTS.md instructions for x\n\n本文")),
        item(now + 1, say("user", "# Context from my IDE setup:\n\n## Active file: a.js\n\n## My request for Codex:\nボタンを直して")),
        item(now + 2, {"type": "reasoning", "summary": [], "encrypted_content": "xx"}),
        item(now + 2, say("assistant", "直します。")),
        item(now + 3, {"type": "function_call", "name": "shell_command", "arguments": json.dumps({"command": "npm test"})}),
        item(now + 3, {"type": "function_call_output", "output": "長い出力"}),
        item(now + 4, {"type": "custom_tool_call", "name": "apply_patch", "input": patch}),
        # code mode (2026-10): tools are called from a script; its patch is a string with escapes
        item(now + 4, {"type": "custom_tool_call", "name": "exec", "input":
            'const r = await tools.exec_command({cmd:"npm run lint",workdir:"x"});\n'
            f'const patch = "*** Begin Patch\\n*** Add File: {json.dumps(str(proj / "src" / "c.js"))[1:-1]}\\n+x\\n*** End Patch";'}),
        item(now + 4, {"type": "function_call", "name": "wait", "arguments": json.dumps({"cell_id": "1"})}),
        item(now + 5, say("assistant", "直しました。")),
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n", encoding="utf-8")


class RolloutTest(unittest.TestCase):
    def test_turn_keeps_words_and_drops_context_and_patch_text(self):
        d = Path(tempfile.mkdtemp())
        try:
            now = time.time()
            rollout(d / "r.jsonl", now, d)
            items = turn_talk(d / "r.jsonl", now, d)
            self.assertEqual([(i["role"], i["text"]) for i in items], [
                ("user", "ボタンを直して"), ("assistant", "直します。"), ("tool", "shell_command: npm test"),
                ("edit", "src/a.js"), ("edit", "b.md"), ("tool", "exec_command: npm run lint"), ("edit", "src/c.js"),
                ("assistant", "直しました。")])
            self.assertNotIn("秘密", json.dumps(items, ensure_ascii=False))
            self.assertEqual(turn_talk(d / "missing.jsonl", now), [])
            self.assertEqual(turn_talk(None, now), [])
        finally:
            shutil.rmtree(d)


class CodexHookTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = self.dir / "proj"
        self.proj.mkdir()
        self.db = self.dir / "runs.db"
        # a Codex started from a Claude Code terminal still has CLAUDE_PROJECT_DIR: it must not be used
        self.env = dict(os.environ, RESPONSEBOARD_DB=str(self.db), CLAUDE_PROJECT_DIR=str(self.dir / "elsewhere"),
                        RESPONSEBOARD_IGNORE=str(self.dir / "ignore"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def hook(self, cmd, *flags, **payload):
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "rb_hook.py"), cmd, "--tool", "codex", *flags],
                           input=json.dumps({"cwd": str(self.proj), **payload}).encode("utf-8"), capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        out = p.stdout.decode("ascii").strip()
        return json.loads(out) if out else None

    def talk(self):
        con = __import__("sqlite3").connect(self.db)
        try:
            r = con.execute("SELECT t.talk_json, r.tool FROM run_talk t JOIN runs r ON r.id = t.run_id").fetchone()
        finally:
            con.close()
        return (json.loads(r[0]), r[1]) if r else (None, None)

    def write_status(self):
        d = json.loads(json.dumps(SAMPLE))
        d["source"] = {"tool": "codex", "written_by": "llm"}
        for k in ("history", "left_behind"):
            d.pop(k, None)
        d["run"].pop("finished_at", None)
        d["run"].pop("id", None)
        st.write_json(st.status_path(self.proj), d)

    def test_stop_blocks_until_status_is_written_then_records_with_fallback_talk(self):
        self.assertIsNone(self.hook("prompt", prompt="天気を出して", turn_id="t1"))
        self.assertEqual(st.read_json(self.proj / ".responseboard" / "turn.json")["prompt"], "天気を出して")
        out = self.hook("stop", turn_id="t1", stop_hook_active=False, last_assistant_message="出しました", transcript_path=None)
        self.assertEqual(out["decision"], "block")
        self.assertIn("does not exist", out["reason"])
        self.assertFalse((self.dir / "elsewhere").exists())
        self.write_status()
        # the Stop after the fix: not blocked again, and recorded
        self.assertIsNone(self.hook("stop", turn_id="t1", stop_hook_active=True, last_assistant_message="出しました"))
        talk, tool = self.talk()
        self.assertEqual(tool, "codex")
        self.assertEqual([(i["role"], i["text"]) for i in talk], [("user", "天気を出して"), ("assistant", "出しました")])

    def test_stop_reads_the_rollout_when_there_is_one(self):
        self.hook("prompt", prompt="ボタンを直して")
        rollout(self.dir / "r.jsonl", st.read_json(self.proj / ".responseboard" / "turn.json")["started_at"], self.proj)
        self.write_status()
        self.hook("stop", stop_hook_active=False, transcript_path=str(self.dir / "r.jsonl"), last_assistant_message="直しました。")
        talk, _ = self.talk()
        self.assertIn(("edit", "src/a.js"), [(i["role"], i["text"]) for i in talk])

    def test_session_gives_rules_for_codex_unless_agents_md_has_them(self):
        out = self.hook("session", source="startup")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("`source.tool` は `codex`", ctx)
        self.assertNotIn("ステータスライン", ctx)  # Codex has none to fill
        (self.proj / "AGENTS.md").write_text(ctx, encoding="utf-8")
        self.assertIsNone(self.hook("session", source="startup"))

    def test_global_hooks_stand_aside_for_project_hooks(self):
        (self.proj / ".codex").mkdir()
        (self.proj / ".codex" / "hooks.json").write_text('{"hooks": {"Stop": [{"hooks": [{"command": "python rb_hook.py stop"}]}]}}', encoding="utf-8")
        self.assertIsNone(self.hook("prompt", "--global", prompt="x"))
        self.assertFalse((self.proj / ".responseboard").exists())


class InstallCodexTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = self.dir / "Town"
        self.proj.mkdir()
        self.env = dict(os.environ, RESPONSEBOARD_DB=str(self.dir / "runs.db"), CODEX_HOME=str(self.dir / "codex"),
                        RESPONSEBOARD_IGNORE=str(self.dir / "ignore"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_install(self, *args):
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "install_codex.py"), *args], capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("utf-8")

    def test_project_install_and_uninstall(self):
        out = self.run_install(str(self.proj))
        self.assertIn("/hooks", out)
        hooks = json.loads((self.proj / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(hooks["hooks"]), ["Stop", "UserPromptSubmit"])
        self.assertTrue(hooks["hooks"]["Stop"][0]["hooks"][0]["command"].endswith("stop --tool codex"))
        self.assertNotIn("statusLine", hooks)
        self.assertIn("`source.tool` は `codex`", (self.proj / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertFalse((self.proj / "CLAUDE.md").exists())
        self.assertTrue(st.status_path(self.proj).exists())
        self.assertIn("already set up", self.run_install(str(self.proj)))
        self.run_install(str(self.proj), "--uninstall")
        self.assertNotIn("hooks", json.loads((self.proj / ".codex" / "hooks.json").read_text(encoding="utf-8")))
        self.assertFalse((self.proj / "AGENTS.md").exists())

    def test_user_install_keeps_other_hooks(self):
        f = self.dir / "codex" / "hooks.json"
        f.parent.mkdir()
        f.write_text('{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}', encoding="utf-8")
        self.run_install("--scope", "user")
        hooks = json.loads(f.read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(sorted(hooks), ["SessionStart", "Stop", "UserPromptSubmit"])
        self.assertEqual([h["command"] for g in hooks["Stop"] for h in g["hooks"]][0], "other")
        self.assertTrue(hooks["SessionStart"][0]["hooks"][0]["command"].endswith("session --global --tool codex"))
        self.run_install("--scope", "user", "--uninstall")
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")), {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}})


if __name__ == "__main__":
    unittest.main()
