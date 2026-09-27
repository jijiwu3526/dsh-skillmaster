#!/usr/bin/env python3
"""Targeted reproduction of suspected defects in dsh_session.py."""
import http.server, io, json, sys, tempfile, threading, traceback
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "extracted" / "dsh-conversation-studio"))
import dsh_session as app

HITS = {"exchange": 0}

class Dsh(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_POST(self):
        if self.headers.get("Cookie") == "dsh-auth=fresh":
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if msg["method"] == "workspace/create":
                body = json.dumps({"type": "server-response", "rpcId": msg["rpcId"],
                    "result": {"ok": True, "value": {"workspace": {"workspaceId": "ws-1",
                        "path": msg["payload"]["args"]["request"]["path"], "title": "t",
                        "sessionIds": [], "createdAt": "", "updatedAt": ""}, "created": True}}}).encode()
            elif msg["method"] == "session/create":
                body = json.dumps({"type": "server-response", "rpcId": msg["rpcId"],
                    "result": {"ok": True, "value": {"sessionId": "session-x"}}}).encode()
            else:
                body = json.dumps({"type": "server-response", "rpcId": msg["rpcId"],
                    "result": {"ok": True, "value": {"presets": [], "authorable": True}}}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        self.send_error(401)

    def do_GET(self):
        if self.path.startswith("/?token="):
            HITS["exchange"] += 1
            self.send_response(303)
            self.send_header("Set-Cookie", "dsh-auth=fresh; HttpOnly; Path=/")
            self.send_header("Location", "/")
            self.end_headers(); return
        if self.path.startswith("/api/"):
            self.send_error(401); return
        self.send_response(200); self.end_headers()

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Dsh)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/?token=fresh"

def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        try:
            rc = app.main(argv)
        except BaseException as e:
            return "EXC", f"{type(e).__name__}: {e}", traceback.format_exc()
    return rc, out.getvalue(), err.getvalue()

print("=" * 72)
print("BUG A: stale/expired cookie blocks re-auth even with a FRESH token URL")
print("=" * 72)
with tempfile.TemporaryDirectory() as tmp:
    cf = Path(tmp) / "c.txt"
    # Simulate a cookie file left behind by a previous DSH run (now-expired cookie)
    cf.write_text(
        "# Netscape HTTP Cookie File\n"
        "127.0.0.1\tFALSE\t/\tFALSE\t0\tdsh-auth\tEXPIRED-OLD\n")
    HITS["exchange"] = 0
    rc, out, err = run(["--url", URL, "--cookie-file", str(cf), "presets"])
    print(f"exit={rc}  token-exchange hits={HITS['exchange']} (expected 1 if re-auth worked)")
    print(f"stderr: {err.strip()}")
    print(f"cookie file STILL holds the dead cookie: {'EXPIRED-OLD' in cf.read_text()}")

print()
print("=" * 72)
print("BUG A2: self-heal works when the cookie file is simply ABSENT")
print("=" * 72)
with tempfile.TemporaryDirectory() as tmp:
    cf = Path(tmp) / "c.txt"
    HITS["exchange"] = 0
    rc, out, err = run(["--url", URL, "--cookie-file", str(cf), "presets"])
    print(f"exit={rc}  token-exchange hits={HITS['exchange']} -> confirms the gap is the stale-cookie guard")

print()
print("=" * 72)
print("BUG B: corrupted / truncated cookie file")
print("=" * 72)
for label, content in [("empty file", ""), ("truncated", "# Netscape HTTP Cookie File\n127.0.0.1\tFALSE")]:
    with tempfile.TemporaryDirectory() as tmp:
        cf = Path(tmp) / "c.txt"
        cf.write_text(content)
        HITS["exchange"] = 0
        rc, out, err = run(["--url", URL, "--cookie-file", str(cf), "presets"])
        kind = "clean error" if rc in (0, 1) and not err.startswith(("Traceback", "  ")) else "UNHANDLED TRACEBACK"
        print(f"  {label:12s} exit={rc!s:5s} kind={kind}")
        print(f"               stderr: {err.strip().splitlines()[0] if err.strip() else '(none)'}")

print()
print("=" * 72)
print("BUG C: --register-workspace silently ignored when --workspace-id is given")
print("=" * 72)
with tempfile.TemporaryDirectory() as tmp:
    rc, out, err = run(["--url", URL, "--cookie-file", f"{tmp}/c.txt", "new",
                        "--workspace-id", "ws-1", "--register-workspace",
                        "--records-dir", f"{tmp}/r"])
    print(f"exit={rc}  stderr={err.strip()!r}")
    print(f"-> contradictory flag combo accepted with no warning: {rc == 0 and not err.strip()}")

srv.shutdown()
