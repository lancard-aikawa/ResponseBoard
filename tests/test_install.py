"""Runs hooks/install_claude.py against a temp project and checks it is safe to repeat."""
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


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.proj = self.dir / "Town"
        self.proj.mkdir()
        self.env = dict(os.environ, RESPONSEBOARD_DB=str(self.dir / "runs.db"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def install(self):
        p = subprocess.run([sys.executable, str(INSTALL), str(self.proj)], capture_output=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        return p.stdout.decode("utf-8")

    def settings(self):
        return json.loads((self.proj / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    def test_fresh_project(self):
        (self.proj / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        self.install()
        s = self.settings()
        self.assertIn("rb_hook.py\" stop", s["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertIn("rb_statusline.py", s["statusLine"]["command"])
        md = (self.proj / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("status.schema.json", md)
        self.assertIn(".responseboard/", (self.proj / ".gitignore").read_text(encoding="utf-8"))
        st = json.loads((self.proj / ".responseboard" / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((st["project"]["name"], st["run"]["status"]), ("Town", "done"))

    def test_repeat_changes_nothing(self):
        self.install()
        files = {p: p.read_bytes() for p in self.proj.rglob("*") if p.is_file()}
        out = self.install()
        self.assertIn("already set up", out)
        self.assertEqual({p: p.read_bytes() for p in self.proj.rglob("*") if p.is_file()}, files)

    def test_keeps_existing_settings_and_text(self):
        (self.proj / ".claude").mkdir()
        (self.proj / ".claude" / "settings.local.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(npm *)"]},
            "statusLine": {"type": "command", "command": "my-line"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-stop"}]}]},
        }), encoding="utf-8")
        (self.proj / "CLAUDE.md").write_text("# Town\n\nown rules\n", encoding="utf-8")
        out = self.install()
        s = self.settings()
        self.assertEqual(s["permissions"], {"allow": ["Bash(npm *)"]})
        self.assertEqual(s["statusLine"]["command"], "my-line")
        self.assertIn("statusLine skipped", out)
        self.assertEqual([g["hooks"][0]["command"] for g in s["hooks"]["Stop"]][0], "my-stop")
        self.assertEqual(len(s["hooks"]["Stop"]), 2)
        md = (self.proj / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# Town\n\nown rules\n\n<!-- responseboard:start -->"))


if __name__ == "__main__":
    unittest.main()
