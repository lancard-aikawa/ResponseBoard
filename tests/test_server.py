"""Starts rb serve's handler on a free port with a temp database and calls the API."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.old_db = os.environ.get("RESPONSEBOARD_DB")
        os.environ["RESPONSEBOARD_DB"] = str(self.dir / "runs.db")
        from responseboard import cli, server
        self.proj = self.dir / "demo"
        self.proj.mkdir()
        cli.main(["--dir", str(self.proj), "fail", "first"])
        cli.main(["--dir", str(self.proj), "ask", "second", "--ask", "Q?|*A|B"])
        self.path = str(self.proj.resolve()).replace("\\", "/")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.old_db is None:
            os.environ.pop("RESPONSEBOARD_DB", None)
        else:
            os.environ["RESPONSEBOARD_DB"] = self.old_db
        shutil.rmtree(self.dir)

    def get(self, url):
        with urllib.request.urlopen(self.base + url) as r:
            return r.status, r.read().decode("utf-8")

    def get_json(self, url):
        return json.loads(self.get(url)[1])

    def test_page(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("<title>ResponseBoard</title>", body)

    def test_projects_runs_status(self):
        projects = self.get_json("/api/projects")
        self.assertEqual([(p["project"], p["runs"], p["failed"], p["status"]) for p in projects],
                         [("demo", 2, 1, "needs_you")])
        q = "path=" + quote(self.path)
        self.assertEqual([r["title"] for r in self.get_json("/api/runs?" + q)], ["second", "first"])
        self.assertEqual([r["title"] for r in self.get_json("/api/runs?status=failed&" + q)], ["first"])
        self.assertEqual(self.get_json("/api/status?" + q)["run"]["title"], "second")
        rev = self.get_json("/api/rev?" + q)["rev"]
        from responseboard import cli
        cli.main(["--dir", str(self.proj), "done", "third"])
        self.assertNotEqual(self.get_json("/api/rev?" + q)["rev"], rev)

    def test_overview_run_diff(self):
        q = "path=" + quote(self.path)
        ov = self.get_json("/api/overview?" + q)
        self.assertEqual([r["title"] for r in ov], ["second", "first"])
        self.assertNotIn("raw_json", ov[0])
        run = self.get_json(f"/api/run?id={ov[1]['id']}&" + q)
        self.assertEqual((run["status"]["run"]["title"], run["git"]), ("first", None))
        for url, code in ((f"/api/diff?id={ov[0]['id']}&", 404), ("/api/run?id=x&", 400), ("/api/run?id=99999&", 404)):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(url + q)
            self.assertEqual(cm.exception.code, code, url)

    def test_unknown_project_is_refused(self):
        for url in ("/api/status?path=" + quote("C:/Windows"), "/api/runs?path=x", "/nope"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(url)
            self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
