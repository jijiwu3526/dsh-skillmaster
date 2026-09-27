import http.server
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import dsh_session as app


class FakeDsh(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    requests = []
    dialect = "remote"
    token_hits = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/?token=test"):
            type(self).token_hits += 1
            self.send_response(303)
            self.send_header("Set-Cookie", "dsh-auth=test-cookie; HttpOnly; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path.startswith("/api/session.export?"):
            if self.headers.get("Cookie") != "dsh-auth=test-cookie":
                self.send_error(401)
                return
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as archive:
                archive.writestr("session.jsonl", "{}\n")
            body = data.getvalue()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.headers.get("Cookie") != "dsh-auth=test-cookie":
            self.send_error(401)
            return
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.path, request))
        if (self.dialect == "legacy" and "/" in request["method"]) or (self.dialect == "remote" and "." in request["method"]):
            self.send_error(404)
            return
        method = request["method"].replace(".", "/")
        if method in {"agentPresets/list", "agentPreset/list"}:
            value = {"presets": [{"id": "work-helper", "isDefault": True}]}
        elif method == "session/create":
            value = {"sessionId": "session-test-123"}
        else:
            self.send_error(404)
            return
        body = json.dumps({"type": "server-response", "rpcId": request["rpcId"],
                           "result": {"ok": True, "value": value}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeDsh)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/?token=test"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        FakeDsh.requests = []
        FakeDsh.token_hits = 0

    def test_remote_session_create_and_cookie_export(self):
        FakeDsh.dialect = "remote"
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO) as output:
            cookie_file = f"{tmp}/auth/cookies.txt"
            rc = app.main(["--url", self.url, "--cookie-file", cookie_file, "new", "--workspace-path", tmp,
                           "--preset", "work-helper", "--records-dir", tmp])
            self.assertEqual(rc, 0)
            record = json.loads(output.getvalue())
            self.assertEqual(record["session_id"], "session-test-123")
            create = next(body for path, body in FakeDsh.requests if path == "/api/session/create")
            self.assertEqual(create["payload"]["args"]["request"]["agentPreset"], "work-helper")
            self.assertEqual(create["payload"]["args"]["request"]["cwd"], str(Path(tmp).resolve()))
            self.assertNotIn("token", Path(record["receipt"]).read_text())
            self.assertEqual(Path(cookie_file).stat().st_mode & 0o777, 0o600)
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(app.main(["--url", self.url, "--cookie-file", cookie_file, "presets"]), 0)
            self.assertEqual(FakeDsh.token_hits, 1)
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = app.main(["--url", self.url.split("?")[0],
                               "--cookie-file", cookie_file, "export", "session-test-123", "--output", f"{tmp}/log.zip"])
            self.assertEqual(rc, 0)
            with zipfile.ZipFile(f"{tmp}/log.zip") as archive:
                self.assertEqual(archive.read("session.jsonl"), b"{}\n")

    def test_legacy_route_fallback(self):
        FakeDsh.dialect = "legacy"
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO):
            rc = app.main(["--url", self.url, "--cookie-file", f"{tmp}/auth", "new", "--workspace-id", "ws-1",
                           "--preset", "work-helper", "--records-dir", tmp])
        self.assertEqual(rc, 0)
        create = next(body for path, body in FakeDsh.requests if path == "/api/session.create")
        self.assertEqual(create["payload"]["workspaceId"], "ws-1")

    def test_reject_nonlocal_url(self):
        with self.assertRaises(app.DshError):
            app.DshWeb("http://example.com:3080/?token=secret")

    def test_bad_preset_does_not_create(self):
        FakeDsh.dialect = "remote"
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stderr", new_callable=io.StringIO):
            rc = app.main(["--url", self.url, "--cookie-file", f"{tmp}/auth", "new", "--workspace-id", "ws-1",
                           "--preset", "missing", "--records-dir", tmp])
        self.assertEqual(rc, 1)
        self.assertFalse(any(path.endswith("session/create") for path, _ in FakeDsh.requests))

    def test_stale_cookie_reauthenticates_from_token_url(self):
        """A cached-but-dead cookie must not block a fresh ?token= URL."""
        FakeDsh.dialect = "remote"
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO):
            cookie_file = Path(tmp) / "cookies.txt"
            cookie_file.write_text(
                "# Netscape HTTP Cookie File\n"
                "127.0.0.1\tFALSE\t/\tFALSE\t0\tdsh-auth\tdead\n")
            rc = app.main(["--url", self.url, "--cookie-file", str(cookie_file), "presets"])
            self.assertEqual(rc, 0)
            self.assertEqual(FakeDsh.token_hits, 1, "a dead cookie must trigger one re-exchange")
            self.assertIn("dsh-auth", cookie_file.read_text())

    def test_corrupt_cookie_file_is_tolerated(self):
        """An empty or truncated cache must not abort the run."""
        FakeDsh.dialect = "remote"
        for content in ("", "# Netscape HTTP Cookie File\n127.0.0.1\tFALSE"):
            with tempfile.TemporaryDirectory() as tmp, \
                    patch("sys.stdout", new_callable=io.StringIO), \
                    patch("sys.stderr", new_callable=io.StringIO):
                cookie_file = Path(tmp) / "cookies.txt"
                cookie_file.write_text(content)
                rc = app.main(["--url", self.url, "--cookie-file", str(cookie_file), "presets"])
                self.assertEqual(rc, 0, f"corrupt cache {content!r} should be recovered from")

    def test_workspace_id_rejects_register_workspace(self):
        """Contradictory scope flags must fail loudly instead of silently dropping one."""
        FakeDsh.dialect = "remote"
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stderr", new_callable=io.StringIO) as err:
            rc = app.main(["--url", self.url, "--cookie-file", f"{tmp}/auth", "new", "--workspace-id", "ws-1",
                           "--register-workspace", "--records-dir", tmp])
        self.assertEqual(rc, 1)
        self.assertIn("--register-workspace", err.getvalue())
        self.assertFalse(any(path.endswith("session/create") for path, _ in FakeDsh.requests))


if __name__ == "__main__":
    unittest.main()
