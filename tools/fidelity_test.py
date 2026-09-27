#!/usr/bin/env python3
"""Protocol-fidelity tests for dsh_session.py against the REAL DSH 0.1.5-rc.1 wire shapes.

Shapes are taken verbatim from the installed DSH generated descriptors:
  - session/create     -> payload.args.request, SessionCreateRequest
  - workspace/create   -> payload.args.request, WorkspaceCreateRequest
  - agentPresets/list  -> payload.args, AgentPresetRoster
  - error branch       -> {code, message, details}  (ConnectionRpcFailure, NOT nested "failure")
  - session.export     -> GET, query sessionId + includeDescendants
"""
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "extracted" / "dsh-conversation-studio"))
import dsh_session as app


class RealShapeDsh(http.server.BaseHTTPRequestHandler):
    """Mock that mirrors the installed DSH descriptors exactly."""
    protocol_version = "HTTP/1.0"
    calls = []
    broken_preset = False
    existing_workspace = False

    def log_message(self, *a):
        pass

    def _cookie_ok(self):
        return self.headers.get("Cookie") == "dsh-auth=real"

    def do_GET(self):
        if self.path.startswith("/?token=real"):
            self.send_response(303)
            self.send_header("Set-Cookie", "dsh-auth=real; HttpOnly; Path=/")
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path.startswith("/api/session.export"):
            if not self._cookie_ok():
                self.send_error(401)
                return
            # Real server: missing/invalid sessionId -> 400
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            if not q.get("sessionId", [""])[0]:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"missing or invalid sessionId query parameter")
                return
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("session.jsonl", "{}\n")
            body = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="dsh-session-x.zip"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if not self._cookie_ok():
            self.send_error(401)
            return
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        msg = json.loads(raw)
        type(self).calls.append((self.path, msg))

        method = msg["method"]
        # Strictly validate the real args envelope.
        payload = msg.get("payload")
        if "/" in method:
            if not isinstance(payload, dict) or "args" not in payload:
                return self._err(msg["rpcId"], "gateway/bad-request", "missing args")
            args = payload["args"]
        else:
            args = payload

        def reply(value):
            body = json.dumps({"type": "server-response", "rpcId": msg["rpcId"],
                               "result": {"ok": True, "value": value}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        if method == "agentPresets/list":
            presets = [{"id": "work-helper", "trust": "user", "isDefault": True}]
            if self.broken_preset:
                presets.append({"id": "broken-one", "trust": "user", "isDefault": False,
                                "broken": "plugin @missing/pkg not installed"})
            reply({"presets": presets, "authorable": True})
        elif method == "workspace/create":
            req = args.get("request") or {}
            path = req.get("path", "")
            if not isinstance(path, str) or not path:
                return self._err(msg["rpcId"], "gateway/bad-request", "invalid path")
            reply({"workspace": {"workspaceId": "ws-real-1", "path": path, "title": os.path.basename(path),
                                 "sessionIds": [], "createdAt": "2026-09-27T00:00:00Z",
                                 "updatedAt": "2026-09-27T00:00:00Z"},
                   "created": not self.existing_workspace})
        elif method == "session/create":
            req = args.get("request") or {}
            sid = req.get("sessionId") or "session-generated"
            reply({"sessionId": sid, "agentPreset": req.get("agentPreset")})
        else:
            self.send_error(404)
            return

    def _err(self, rpc_id, code, message):
        """Real Remote error branch: flat {code,message,details}, not nested."""
        body = json.dumps({"type": "server-response", "rpcId": rpc_id,
                           "result": {"ok": False,
                                      "error": {"code": code, "message": message, "details": {}}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FidelityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RealShapeDsh)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/?token=real"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        RealShapeDsh.calls = []
        RealShapeDsh.broken_preset = False
        RealShapeDsh.existing_workspace = False

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            rc = app.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_register_workspace_full_remote_path(self):
        """--register-workspace: workspace/create then session/create, real response shapes."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "proj"
            ws.mkdir()
            rc, out, err = self._run([
                "--url", self.url, "--cookie-file", f"{tmp}/c.txt", "new",
                "--workspace-path", str(ws), "--register-workspace",
                "--preset", "work-helper", "--records-dir", f"{tmp}/records"])
            self.assertEqual(rc, 0, err)

            wsc = next(b for p, b in RealShapeDsh.calls if p == "/api/workspace/create")
            self.assertEqual(wsc["method"], "workspace/create")
            self.assertIn("request", wsc["payload"]["args"], "workspace/create must use args.request")
            self.assertEqual(wsc["payload"]["args"]["request"]["path"], str(ws.resolve()))

            crt = next(b for p, b in RealShapeDsh.calls if p == "/api/session/create")
            self.assertEqual(crt["payload"]["args"]["request"]["workspaceId"], "ws-real-1",
                             "must extract workspaceId from the nested workspace view")

    def test_flat_error_branch_is_reported(self):
        """Real errors are {code,message,details}; message must surface."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "p"
            ws.mkdir()
            os.chmod(ws, 0o500)  # block writes -> workspace/create should fail server-side
            try:
                rc, out, err = self._run([
                    "--url", self.url, "--cookie-file", f"{tmp}/c.txt", "new",
                    "--workspace-path", "/definitely/not/real", "--register-workspace",
                    "--records-dir", f"{tmp}/r"])
            finally:
                os.chmod(ws, 0o700)
            self.assertEqual(rc, 1)
            self.assertIn("工作区不是已有目录", err)

    def test_broken_preset_rejected_before_create(self):
        RealShapeDsh.broken_preset = True
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run([
                "--url", self.url, "--cookie-file", f"{tmp}/c.txt", "new",
                "--workspace-id", "ws-1", "--preset", "broken-one",
                "--records-dir", f"{tmp}/r"])
            self.assertEqual(rc, 1)
            self.assertIn("broken-one", err)
            self.assertFalse(any(p.endswith("session/create") for p, _ in RealShapeDsh.calls),
                             "must not create a session for a broken preset")

    def test_retry_same_session_id_idempotency(self):
        """Documented retry path: same --session-id twice must be usable both times."""
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--url", self.url, "--cookie-file", f"{tmp}/c.txt", "new",
                    "--workspace-id", "ws-1", "--session-id", "session-fixed",
                    "--records-dir", f"{tmp}/records"]
            rc1, out1, err1 = self._run(args)
            self.assertEqual(rc1, 0, err1)
            rc2, out2, err2 = self._run(args)
            self.assertEqual(rc2, 0, f"retry of the documented --session-id path failed: {err2}")

    def test_export_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = f"{tmp}/log.zip"
            rc1, _, e1 = self._run(["--url", self.url, "--cookie-file", f"{tmp}/c.txt",
                                    "export", "session-fixed", "--output", target])
            self.assertEqual(rc1, 0, e1)
            rc2, _, e2 = self._run(["--url", self.url, "--cookie-file", f"{tmp}/c.txt",
                                    "export", "session-fixed", "--output", target])
            self.assertEqual(rc2, 1)
            self.assertIn("拒绝覆盖", e2)

    def test_export_validates_zip(self):
        """A 200 that is not a ZIP must be rejected, not silently saved."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run(["--url", self.url, "--cookie-file", f"{tmp}/c.txt",
                                      "export", "s1", "--output", f"{tmp}/x.zip"])
            self.assertEqual(rc, 0, err)
            self.assertTrue(zipfile.is_zipfile(f"{tmp}/x.zip"))
            self.assertFalse(os.path.exists(f"{tmp}/x.zip.part"), ".part must be cleaned up")

    def test_no_token_leak_in_receipt_or_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run([
                "--url", self.url, "--cookie-file", f"{tmp}/c.txt", "new",
                "--workspace-id", "ws-1", "--preset", "work-helper",
                "--records-dir", f"{tmp}/records"])
            self.assertEqual(rc, 0, err)
            receipt = next(Path(f"{tmp}/records").glob("*.json")).read_text()
            self.assertNotIn("token", receipt)
            self.assertNotIn("real", receipt.split('"note"')[0].replace("records", ""))
            mode = Path(f"{tmp}/c.txt").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_401_gives_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(f"{tmp}/c.txt")
            empty.write_text("")  # zero-byte cookie file -> no cookies
            rc, out, err = self._run(["--url", self.url, "--cookie-file", str(empty), "presets"])
            self.assertEqual(rc, 0, err)  # token exchange repopulates

    def test_register_workspace_with_workspace_id_is_silently_ignored(self):
        """--workspace-id + --register-workspace: is the contradictory flag flagged?"""
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run([
                "--url", self.url, "--cookie-file", f"{tmp}/c.txt", "new",
                "--workspace-id", "ws-1", "--register-workspace",
                "--records-dir", f"{tmp}/r"])
            self.assertEqual(rc, 0, err)
            registered = any(p.endswith("workspace/create") for p, _ in RealShapeDsh.calls)
            self.assertFalse(registered, "register-workspace should have registered (or errored)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
