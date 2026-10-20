"""Installing for every folder (--scope user), the deny list, and uninstalling, with a temp home and database."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "hooks" / "install_claude.py"
HOOK = ROOT / "hooks" / "rb_hook.py"


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.conf = self.dir / "claude"
        self.proj = self.dir / "Town"
        self.proj.mkdir()
        self.env = dict(os.environ, RESPONSEBOARD_DB=str(self.dir / "rb" / "runs.db"),
                        CLAUDE_CONFIG_DIR=str(self.conf), PYTHONPATH=str(ROOT / "src"))
        self.env.pop("CLAUDE_PROJECT_DIR", None)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_py(self, *args, stdin=b"", code=0):
        p = subprocess.run([sys.executable, *map(str, args)], input=stdin, capture_output=True, env=self.env)
        self.assertEqual(p.returncode, code, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("utf-8")

    def hook(self, *args, folder=None):
        out = self.run_py(HOOK, *args, stdin=json.dumps({"cwd": str(folder or self.proj)}).encode("utf-8")).strip()
        return json.loads(out) if out else None

    def rb(self, *args, code=0):
        return self.run_py("-m", "responseboard", "--dir", self.proj, *args, code=code)

    def settings(self):
        return json.loads((self.conf / "settings.json").read_text(encoding="utf-8"))

    def test_user_install_merges_repeats_and_uninstalls(self):
        self.conf.mkdir()
        mine = {"model": "opus", "statusLine": {"type": "command", "command": "my-line"},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-stop"}]}]}}
        (self.conf / "settings.json").write_text(json.dumps(mine), encoding="utf-8")
        out = self.run_py(INSTALL, "--scope", "user")
        s = self.settings()
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["statusLine"]["command"], "my-line")
        self.assertIn("statusLine skipped", out)
        self.assertEqual([g["hooks"][0]["command"] for g in s["hooks"]["Stop"]][0], "my-stop")
        for event, arg in (("SessionStart", "session --global"), ("UserPromptSubmit", "prompt --global"),
                           ("Stop", "stop --global")):
            self.assertTrue(any(h["command"].endswith(arg) for g in s["hooks"][event] for h in g["hooks"]), event)
        self.assertTrue((self.dir / "rb" / "ignore").exists())
        before = (self.conf / "settings.json").read_bytes()
        self.assertIn("already set up", self.run_py(INSTALL, "--scope", "user"))
        self.assertEqual((self.conf / "settings.json").read_bytes(), before)
        self.run_py(INSTALL, "--scope", "user", "--uninstall")
        self.assertEqual(self.settings(), mine)

    def test_user_install_adds_statusline_when_none(self):
        self.run_py(INSTALL, "--scope", "user")
        self.assertIn("rb_statusline.py", self.settings()["statusLine"]["command"])
        self.run_py(INSTALL, "--scope", "user", "--uninstall")
        self.assertEqual(self.settings(), {})

    def test_session_gives_rules_unless_claude_md_has_them(self):
        ctx = self.hook("session", "--global")["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        self.assertIn("status.schema.json", ctx["additionalContext"])
        self.assertIn("ignore", ctx["additionalContext"])
        (self.proj / "CLAUDE.md").write_text("<!-- responseboard:start -->\nx\n<!-- responseboard:end -->\n",
                                             encoding="utf-8")
        self.assertIsNone(self.hook("session", "--global"))

    def test_prompt_hides_the_folder_from_git(self):
        self.hook("prompt", "--global")
        self.assertEqual((self.proj / ".responseboard" / ".gitignore").read_text(encoding="utf-8"), "*\n")
        self.assertEqual(self.hook("stop", "--global")["decision"], "block")

    def test_deny_list_silences_every_hook(self):
        out = self.rb("ignore")
        self.assertIn("added", out)
        self.assertIn("already skipped", self.rb("ignore"))
        sub = self.proj / "sub"
        sub.mkdir()
        for args in (("session", "--global"), ("prompt", "--global"), ("prompt",), ("stop", "--global")):
            self.assertIsNone(self.hook(*args, folder=sub), args)
        self.assertFalse((sub / ".responseboard").exists())
        self.assertFalse((self.proj / ".responseboard").exists())
        line = self.run_py(ROOT / "hooks" / "rb_statusline.py", stdin=json.dumps({"cwd": str(sub)}).encode("utf-8"))
        self.assertEqual(line, "")
        self.assertIn("removed", self.rb("ignore", "--remove"))
        self.assertIsNotNone(self.hook("session", "--global", folder=sub))

    def test_deny_list_wildcards_and_comments(self):
        (self.dir / "rb").mkdir()
        (self.dir / "rb" / "ignore").write_text(f"# note\n{str(self.dir).upper()}/T*  # temp\n", encoding="utf-8")
        self.assertIsNone(self.hook("session", "--global"))
        self.assertIn("still skipped", self.rb("ignore", "--remove", code=1))
        self.assertIn("T*", self.rb("ignore", "--list"))

    def test_global_stands_aside_for_project_hooks(self):
        self.run_py(INSTALL, "--scope", "project", self.proj)
        self.assertIsNone(self.hook("session", "--global"))
        (self.proj / ".responseboard" / "turn.json").unlink(missing_ok=True)
        self.assertIsNone(self.hook("prompt", "--global"))
        self.assertFalse((self.proj / ".responseboard" / "turn.json").exists())

    def test_project_uninstall(self):
        (self.proj / "CLAUDE.md").write_text("# Town\n\nown rules\n", encoding="utf-8")
        self.run_py(INSTALL, "--scope", "project", self.proj)
        self.run_py(INSTALL, "--scope", "project", self.proj, "--uninstall")
        self.assertEqual((self.proj / "CLAUDE.md").read_text(encoding="utf-8"), "# Town\n\nown rules\n")
        s = json.loads((self.proj / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
        self.assertEqual(s, {})

    def test_scope_arguments(self):
        self.run_py(INSTALL, "--scope", "user", self.proj, code=2)
        self.run_py(INSTALL, "--scope", "project", code=2)


if __name__ == "__main__":
    unittest.main()
