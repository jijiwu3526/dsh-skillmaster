"""Tests for the bridge client — the piece with zero coverage until now.

`dsh_bridge.py` is what makes the whole plugin story work: it turns a running
DSH into something a CLI can authenticate against without a human in the loop.
Its three-layer fallback is the part most likely to rot silently, so each rung
gets a test, and so does every way the plugin can refuse us.
"""
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import http.server
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).parent.parent))
import dsh_bridge


class FakeBridge(BaseHTTPRequestHandler):
    """A stand-in for the plugin's loopback route.

    `mode` picks the behaviour under test so each scenario is a real HTTP
    round trip rather than a mocked function call — the fallback chain is
    exactly the kind of code that only breaks at the socket layer.
    """

    protocol_version = "HTTP/1.0"
    mode = "ok"
    want_secret = "s3cret"
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).seen.append({
            "path": self.path,
            "secret": self.headers.get("X-DSH-Bridge-Secret"),
        })
        if type(self).mode == "loopback-403":
            return self._json(403, {"ok": False, "error": "loopback-only"})
        if type(self).mode == "bad-secret":
            return self._json(403, {"ok": False, "error": "bad-secret"})
        if type(self).mode == "not-installed":
            return self._json(404, {"ok": False, "error": "not found"})
        if type(self).mode == "mint-fails":
            return self._json(500, {"ok": False, "error": "no connection"})
        if type(self).mode == "garbage":
            body = io.BytesIO(b"not json at all")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body.getvalue())))
            self.end_headers()
            self.wfile.write(body.getvalue())
            return
        if type(self).mode == "ok-false":
            return self._json(200, {"ok": False, "error": "nope"})
        if self.headers.get("X-DSH-Bridge-Secret") != type(self).want_secret:
            return self._json(403, {"ok": False, "error": "bad-secret"})
        return self._json(200, {
            "ok": True,
            "url": "http://127.0.0.1:3080/?token=fresh-token",
            "origin": "http://127.0.0.1:3080",
            "secretPath": "/fake/local-bridge.secret",
        })

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BridgeServerMixin:
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridge)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeBridge.mode = "ok"
        FakeBridge.seen = []
        self._env = {k: os.environ.pop(k, None) for k in
                     ("DSH_WEB_URL", "DSH_WEB_BRIDGE_ORIGIN")}
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write_secret(self, tmp: Path, value: str = "s3cret") -> Path:
        path = tmp / "local-bridge.secret"
        path.write_text(value, encoding="utf-8")
        return path


class ResolveTests(BridgeServerMixin, unittest.TestCase):
    """The three rungs, in order: plugin -> env -> plain origin."""

    def test_plugin_is_preferred_when_it_answers(self):
        with tempfile.TemporaryDirectory() as d:
            secret = self.write_secret(Path(d))
            result = dsh_bridge.resolve(self.origin, secret, timeout=5)
        self.assertEqual(result["source"], "bridge-plugin")
        self.assertEqual(result["url"], "http://127.0.0.1:3080/?token=fresh-token")
        # The minted origin comes from the plugin, not from what we guessed.
        self.assertEqual(result["origin"], "http://127.0.0.1:3080")

    def test_falls_back_to_env_when_plugin_refuses(self):
        os.environ["DSH_WEB_URL"] = "http://127.0.0.1:3080/?token=from-env"
        with tempfile.TemporaryDirectory() as d:
            secret = self.write_secret(Path(d))
            FakeBridge.mode = "bad-secret"
            result = dsh_bridge.resolve(self.origin, secret, timeout=5)
        self.assertEqual(result["source"], "env")
        self.assertEqual(result["url"], "http://127.0.0.1:3080/?token=from-env")
        # The reason we fell back must survive, or the user debugs blind.
        self.assertIn("403", result["note"])

    def test_falls_back_to_plain_origin_as_last_resort(self):
        os.environ.pop("DSH_WEB_URL", None)
        with tempfile.TemporaryDirectory() as d:
            secret = self.write_secret(Path(d))
            FakeBridge.mode = "not-installed"
            result = dsh_bridge.resolve(self.origin, secret, timeout=5)
        self.assertEqual(result["source"], "plain-origin")
        self.assertEqual(result["url"], self.origin)
        self.assertNotIn("token", result["url"])
        self.assertIn("Cookie", result["note"])

    def test_missing_secret_skips_the_plugin_entirely(self):
        os.environ.pop("DSH_WEB_URL", None)
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "does-not-exist"
            result = dsh_bridge.resolve(self.origin, missing, timeout=5)
        self.assertEqual(result["source"], "plain-origin")
        self.assertIn("未找到共享密钥", result["note"])
        # No request should have been made at all — we had nothing to send.
        self.assertEqual(FakeBridge.seen, [])

    def test_empty_secret_file_is_treated_as_absent(self):
        os.environ.pop("DSH_WEB_URL", None)
        with tempfile.TemporaryDirectory() as d:
            empty = self.write_secret(Path(d), value="")
            self.assertIsNone(dsh_bridge.read_secret(empty))
            result = dsh_bridge.resolve(self.origin, empty, timeout=5)
        self.assertEqual(result["source"], "plain-origin")
        self.assertEqual(FakeBridge.seen, [])

    def test_bridge_error_is_raised_from_fetch(self):
        for mode, marker in (("loopback-403", "loopback-only"),
                             ("bad-secret", "bad-secret"),
                             ("mint-fails", "no connection")):
            with self.subTest(mode=mode):
                FakeBridge.mode = mode
                with self.assertRaises(dsh_bridge.BridgeError) as caught:
                    dsh_bridge.fetch_via_bridge(self.origin, "s3cret", timeout=5)
                self.assertIn(marker, str(caught.exception))

    def test_connection_refused_is_a_bridge_error_not_a_crash(self):
        # Port 1 is reserved; nothing listens there.
        with self.assertRaises(dsh_bridge.BridgeError) as caught:
            dsh_bridge.fetch_via_bridge("http://127.0.0.1:1", "s3cret", timeout=2)
        message = str(caught.exception)
        self.assertIn("无法连接", message)
        self.assertIn("DSH", message)

    def test_non_json_and_ok_false_are_both_rejected(self):
        for mode in ("garbage", "ok-false"):
            with self.subTest(mode=mode):
                FakeBridge.mode = mode
                with self.assertRaises(dsh_bridge.BridgeError):
                    dsh_bridge.fetch_via_bridge(self.origin, "s3cret", timeout=5)

    def test_credential_never_travels_through_an_env_proxy(self):
        """A ProxyHandler-less opener would leak the header to any proxy."""
        opened = []
        real_build = dsh_bridge.urllib.request.build_opener

        def spy(*handlers):
            opened.extend(handlers)
            return real_build(*handlers)

        with patch.object(dsh_bridge.urllib.request, "build_opener", spy):
            dsh_bridge.fetch_via_bridge(self.origin, "s3cret", timeout=5)
        proxy_handlers = [h for h in opened if isinstance(h, dsh_bridge.urllib.request.ProxyHandler)]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})

    def test_secret_travels_in_the_header_not_the_query_string(self):
        dsh_bridge.fetch_via_bridge(self.origin, "s3cret", timeout=5)
        self.assertEqual(FakeBridge.seen[0]["secret"], "s3cret")
        self.assertNotIn("secret", FakeBridge.seen[0]["path"])

    def test_origin_env_override_is_honoured(self):
        os.environ["DSH_WEB_BRIDGE_ORIGIN"] = self.origin
        with tempfile.TemporaryDirectory() as d:
            secret = self.write_secret(Path(d))
            result = dsh_bridge.resolve(None, secret, timeout=5)
        self.assertEqual(result["source"], "bridge-plugin")
        self.assertEqual(FakeBridge.seen[0]["path"], "/local-bridge/auth")

    def test_bridge_path_matches_the_plugin(self):
        # The plugin registers this exact path. If it moves, this fails loudly
        # instead of producing a mysterious 404 at runtime.
        self.assertEqual(dsh_bridge.BRIDGE_PATH, "/local-bridge/auth")


class TransportFailureTests(BridgeServerMixin, unittest.TestCase):
    """A bridge that is down, restarting or half-written must never crash the CLI.

    Each of these was observed escaping `resolve()` as a raw traceback before
    the exception handling was widened. They are all ordinary conditions for
    this client: the fallback chain exists precisely so they degrade quietly.
    """

    def setUp(self):
        super().setUp()
        os.environ.pop("DSH_WEB_URL", None)

    def _secret(self, tmp):
        return self.write_secret(tmp)

    @staticmethod
    def _serve(handler_cls):
        """Start a throwaway HTTP server and always close its socket."""
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _resolve_against(self, port_or_url, secret, timeout=2.0):
        """`resolve()` must return a dict, never raise a non-BridgeError."""
        try:
            return dsh_bridge.resolve(port_or_url, secret, timeout=timeout)
        except dsh_bridge.BridgeError:
            return None

    def test_timeout_falls_back(self):
        """A DSH that is starting up accepts the socket then stalls."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        threading.Thread(
            target=lambda: (lambda c: (time.sleep(5), c.close()))(srv.accept()[0]),
            daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                result = self._resolve_against(f"http://127.0.0.1:{port}",
                                               self._secret(Path(d)), timeout=1.0)
        finally:
            srv.close()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "plain-origin")
        self.assertIn("超时", result["note"])

    def test_truncated_body_falls_back(self):
        """Content-Length promises more than the server sends."""
        class Truncating(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "500")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "url"')
                self.close_connection = True

        srv = self._serve(Truncating)
        try:
            with tempfile.TemporaryDirectory() as d:
                result = self._resolve_against(
                    f"http://127.0.0.1:{srv.server_address[1]}", self._secret(Path(d)))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "plain-origin")
        self.assertIn("中断", result["note"])

    def test_non_http_garbage_falls_back(self):
        """Another process on the port answering with something that is not HTTP."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def speak():
            conn, _ = srv.accept()
            conn.sendall(b"this is not http at all\r\n\r\n")
            time.sleep(0.2)
            conn.close()

        threading.Thread(target=speak, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                result = self._resolve_against(f"http://127.0.0.1:{port}",
                                               self._secret(Path(d)), timeout=3)
        finally:
            srv.close()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "plain-origin")

    def test_connection_reset_falls_back(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def reset():
            conn, _ = srv.accept()
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            b"\x01\x00\x00\x00\x00\x00\x00\x00")
            conn.close()

        threading.Thread(target=reset, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                result = self._resolve_against(f"http://127.0.0.1:{port}",
                                               self._secret(Path(d)), timeout=3)
        finally:
            srv.close()
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "plain-origin")

    def test_ok_true_without_a_url_is_rejected(self):
        """Version skew or a partial write; must not hand the caller None."""
        class NoUrl(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"ok": True, "origin": "http://127.0.0.1:3080"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = self._serve(NoUrl)
        try:
            with tempfile.TemporaryDirectory() as d:
                secret = self._secret(Path(d))
                origin = f"http://127.0.0.1:{srv.server_address[1]}"
                with self.assertRaises(dsh_bridge.BridgeError) as caught:
                    dsh_bridge.fetch_via_bridge(origin, "s3cret", timeout=3)
                self.assertIn("url", str(caught.exception))
                result = self._resolve_against(origin, secret)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(result["source"], "plain-origin")

    def test_empty_or_non_string_url_is_rejected(self):
        for payload in ({"ok": True, "url": None}, {"ok": True, "url": ""},
                        {"ok": True, "url": ["x"]}):
            with self.subTest(payload=payload):
                class Weird(http.server.BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.0"

                    def log_message(self, *a):
                        pass

                    def do_GET(self):
                        body = json.dumps(payload).encode()
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)

                srv = self._serve(Weird)
                try:
                    with self.assertRaises(dsh_bridge.BridgeError):
                        dsh_bridge.fetch_via_bridge(
                            f"http://127.0.0.1:{srv.server_address[1]}", "s3cret", timeout=3)
                finally:
                    srv.shutdown()
                    srv.server_close()


class OriginNormalisationTests(unittest.TestCase):
    def test_schemeless_origin_gets_a_scheme(self):
        self.assertEqual(dsh_bridge._normalise_origin("localhost:3080"),
                         "http://localhost:3080")
        self.assertEqual(dsh_bridge._normalise_origin("127.0.0.1:3080"),
                         "http://127.0.0.1:3080")

    def test_existing_scheme_is_kept(self):
        self.assertEqual(dsh_bridge._normalise_origin("https://dsh.example"),
                         "https://dsh.example")
        self.assertEqual(dsh_bridge._normalise_origin("http://127.0.0.1:3080/"),
                         "http://127.0.0.1:3080")

    def test_empty_origin_falls_back_to_the_default(self):
        self.assertEqual(dsh_bridge._normalise_origin(""), "http://127.0.0.1:3080")
        self.assertEqual(dsh_bridge._normalise_origin("   "), "http://127.0.0.1:3080")

    def test_secret_path_honours_a_custom_dsh_home(self):
        """The plugin prefers $DSH_HOME; the client must look in the same place."""
        with patch.dict(os.environ, {"DSH_HOME": "/tmp/custom-dsh"}):
            import importlib
            reloaded = importlib.reload(dsh_bridge)
            try:
                self.assertEqual(reloaded.DEFAULT_SECRET,
                                 Path("/tmp/custom-dsh/local-bridge.secret"))
            finally:
                importlib.reload(dsh_bridge)


class DefaultOriginTests(unittest.TestCase):
    def test_plain_loopback_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(dsh_bridge._default_origin(), "http://127.0.0.1:3080")

    def test_env_url_keeps_only_the_origin(self):
        with patch.dict(os.environ, {"DSH_WEB_URL": "http://127.0.0.1:9999/?token=abc"},
                        clear=True):
            self.assertEqual(dsh_bridge._default_origin(), "http://127.0.0.1:9999")

    def test_env_url_without_port_falls_back_to_default(self):
        with patch.dict(os.environ, {"DSH_WEB_URL": "https://dsh.example/?token=abc"},
                        clear=True):
            self.assertEqual(dsh_bridge._default_origin(), "http://127.0.0.1:3080")


class CliTests(unittest.TestCase):
    def test_quiet_prints_only_the_url(self):
        out = io.StringIO()
        with patch.object(dsh_bridge, "resolve", return_value={"url": "http://x/?token=t",
                                                               "origin": "http://x",
                                                               "source": "bridge-plugin"}), \
             patch("sys.stdout", out):
            self.assertEqual(dsh_bridge.main(["--quiet"]), 0)
        self.assertEqual(out.getvalue().strip(), "http://x/?token=t")

    def test_default_output_redacts_the_url(self):
        out = io.StringIO()
        with patch.object(dsh_bridge, "resolve", return_value={"url": "http://x/?token=SECRET",
                                                               "origin": "http://x",
                                                               "source": "bridge-plugin"}), \
             patch("sys.stdout", out):
            self.assertEqual(dsh_bridge.main([]), 0)
        text = out.getvalue()
        self.assertNotIn("SECRET", text)
        self.assertIn("bridge-plugin", text)

    def test_bridge_failure_exits_nonzero(self):
        err = io.StringIO()
        with patch.object(dsh_bridge, "resolve", side_effect=dsh_bridge.BridgeError("boom")), \
             patch("sys.stderr", err):
            self.assertEqual(dsh_bridge.main([]), 1)
        self.assertIn("boom", err.getvalue())


if __name__ == "__main__":
    unittest.main()
