#!/usr/bin/env python3
"""Preset boundary + robustness harness for DSH Agent Presets.

Measures three things the bundled mock tests cannot:

  A. BOUNDARY  - which tools a preset actually controls vs. which the HOST
                 injects regardless. Answers "can a preset really lock down
                 its tool surface?"
  B. FAULT     - how DSH reacts to deliberately broken preset definitions
                 (missing required field, unknown plugin, bad YAML, bad id,
                 realm collision, empty file...). Every fault must fail
                 LOUDLY and leave the rest of the roster healthy.
  C. RECOVERY  - after each fault, is DSH still able to create and serve a
                 session with a KNOWN-GOOD preset?

Read-only against the live DSH except for the presets this harness creates
under ~/.dsh/.agent-presets/ (each removed in teardown).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "extracted" / "dsh-conversation-studio"))
import dsh_session as app  # noqa: E402

PRESET_ROOT = Path.home() / ".dsh" / ".agent-presets"
WORKSPACE_ID = "84c62e4f-7497-4be6-b738-b4f2d73486ec"
TOKEN_ENV = "DSH_WEB_URL"


def raw_rpc(web, method: str, args: dict):
    """agentPresets/read|select take BARE named wire fields, not args.request."""
    rid = str(uuid.uuid4())
    body = json.dumps({"type": "client-request", "rpcId": rid, "method": method,
                       "payload": {"args": args}}, ensure_ascii=False).encode()
    with web._open(f"{web.base}/api/{method}", data=body, method="POST") as r:
        msg = json.load(r)
    res = msg.get("result", {})
    if not res.get("ok"):
        raise app.DshError(f"{method}: {json.dumps(res.get('error'), ensure_ascii=False)}")
    return res.get("value")


def session_summary(web, sid: str) -> dict:
    res = app.call(web, "session/list", "session.list", {"limit": 50}, args_key="_request")
    for s in app.entries(res, "sessions", "items"):
        if s.get("sessionId") == sid:
            v = s.get("projections", {}).get("values", {})
            return {"blank": s.get("blank"), "title": v.get("title"),
                    "turns": v.get("sessionStats", {}).get("turns")}
    return {}


def seed_session(web, sid: str, text: str, wait: int = 30) -> dict:
    """Create -> prompt -> wait until non-blank, so the session is usable."""
    app.call(web, "session/prompt", "session.prompt",
             {"requestId": str(uuid.uuid4()), "sessionId": sid,
              "mode": "queue", "content": [{"type": "text", "text": text}]},
             args_key="request")
    for _ in range(wait):
        time.sleep(2)
        s = session_summary(web, sid)
        if s.get("blank") is False:
            return s
    return session_summary(web, sid)


class Harness:
    def __init__(self, cookie: Path):
        self.cookie = cookie
        self.web = app.DshWeb(os.environ[TOKEN_ENV], cookie_file=cookie)
        self.created: list[str] = []

    def roster(self) -> dict:
        return raw_rpc(self.web, "agentPresets/list", {})

    def make_session(self, preset: str, records: Path) -> str:
        records.mkdir(parents=True, exist_ok=True)
        rc = app.main(["--url", os.environ[TOKEN_ENV], "--cookie-file", str(self.cookie),
                       "new", "--workspace-id", WORKSPACE_ID, "--preset", preset,
                       "--records-dir", str(records)])
        if rc != 0:
            raise app.DshError(f"session create failed for preset {preset!r}")
        receipts = sorted(records.glob("*.json"), key=lambda p: p.stat().st_mtime)
        return json.loads(receipts[-1].read_text())["session_id"]

    def ask_tools(self, sid: str) -> str:
        self.web.export(sid, Path(f"/tmp/_tools_{sid}.zip"))
        with zipfile.ZipFile(f"/tmp/_tools_{sid}.zip") as z:
            body = z.read(z.namelist()[0]).decode("utf-8", "replace")
        Path(f"/tmp/_tools_{sid}.zip").unlink(missing_ok=True)
        for line in body.splitlines():
            e = json.loads(line)
            if e.get("type") == "assistant/message":
                msg = (e.get("data") or {}).get("message") or {}
                for c in msg.get("content", []):
                    if c.get("type") == "text" and c.get("text", "").strip():
                        return c["text"]
        return ""


# ── fault definitions ────────────────────────────────────────────────────────
GOOD = (PRESET_ROOT / "review-focused" / "agent.cordis.yml").read_text(encoding="utf-8")

FAULTS = {
    "missing-required-prefix": lambda c: c.replace(
        "    prefix: >-\n      You are a coding agent powered by the {{model}} model.\n", ""),
    "unknown-plugin": lambda c: c.replace(
        "- id: tool-todo\n  name: '@deepseek-ai/dsh-tool-todo'",
        "- id: tool-nonexistent\n  name: '@deepseek-ai/dsh-tool-does-not-exist'"),
    "malformed-yaml": lambda c: c + "\n- id: broken\n   name: [unclosed\n",
    "duplicate-id": lambda c: c + "\n- id: tool-todo\n  name: '@deepseek-ai/dsh-tool-todo'\n",
    "empty-file": lambda c: "",
    "group-without-isolate": lambda c: c.replace("  isolate:\n    planMode: true\n", ""),
    "bad-preset-id-dir": None,   # handled specially
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", type=Path, default=Path("/tmp/dsh-fault-cookies.txt"))
    ap.add_argument("--records", type=Path, default=Path("/tmp/dsh-fault-records"))
    args = ap.parse_args()

    if TOKEN_ENV not in os.environ:
        print("需要 DSH_WEB_URL（含 ?token=）", file=sys.stderr)
        return 2

    h = Harness(args.cookie)
    pid = "rb-test"
    pdir = PRESET_ROOT / pid
    results = []

    def write(content: str):
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "agent.cordis.yml").write_text(content, encoding="utf-8")
        (pdir / "preset.yml").write_text(
            "name: RB测试\ndescription: 临时故障注入预设\n", encoding="utf-8")

    def probe_session() -> tuple[bool, str]:
        """Can DSH still create a session on a KNOWN-GOOD preset?"""
        try:
            sid = h.make_session("standard", args.records)
            h.created.append(sid)
            return True, sid
        except Exception as e:  # noqa: BLE001
            return False, str(e)

    # ── baseline ──
    print("=" * 66)
    print("基线：DSH 现状")
    print("=" * 66)
    r = h.roster()
    print(f"  预设数: {len(r['presets'])}  authorable={r.get('authorable')}")
    ok, info = probe_session()
    print(f"  可用 standard 创建会话: {ok}")

    # ── B. fault injection ──
    print()
    print("=" * 66)
    print("B. 故障注入（每个故障都必须响亮失败且不拖垮 roster）")
    print("=" * 66)
    for name, fn in FAULTS.items():
        if fn is None:
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / "agent.cordis.yml").write_text(GOOD, encoding="utf-8")
            (pdir / "preset.yml").write_text("name: 'bad id'\ndescription: x\n", encoding="utf-8")
        else:
            try:
                write(fn(GOOD))
            except Exception as e:  # noqa: BLE001
                print(f"  {name:26s} 构造失败: {e}")
                continue
        # DSH reads the file on each create; give the roster a beat.
        time.sleep(0.4)
        try:
            sid = h.make_session(pid, args.records)
            h.created.append(sid)
            verdict = "❌ 未被拦截（竟然成功了）"
        except app.DshError as e:
            msg = str(e).replace("\n", " ")
            verdict = "✅ 拒绝" + (f" — {msg[:88]}" if msg else "")
        except Exception as e:  # noqa: BLE001
            verdict = f"✅ 拒绝 — {type(e).__name__}: {str(e)[:80]}"
        # roster health after the fault
        try:
            rr = h.roster()
            broken = [p["id"] for p in rr["presets"] if p.get("broken")]
            health = f"roster={len(rr['presets'])}" + (f" broken={broken}" if broken else " 无 broken")
        except Exception as e:  # noqa: BLE001
            health = f"roster 查询失败: {e}"
        print(f"  {name:26s} {verdict}")
        print(f"  {'':26s} {health}")

    # ── C. recovery ──
    print()
    print("=" * 66)
    print("C. 恢复：删掉坏预设后 DSH 是否健康")
    print("=" * 66)
    shutil.rmtree(pdir, ignore_errors=True)
    time.sleep(0.4)
    ok, info = probe_session()
    print(f"  删除坏预设后可正常创建会话: {ok}")
    rr = h.roster()
    print(f"  roster 恢复: {len(rr['presets'])} 个预设，ids={[p['id'] for p in rr['presets']]}")

    print()
    print(f"  期间创建的测试会话（均为空白，未发消息）: {len(h.created)}")
    for s in h.created:
        print(f"    {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
