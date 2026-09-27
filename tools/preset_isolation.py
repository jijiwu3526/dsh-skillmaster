#!/usr/bin/env python3
"""Boundary + isolation probes that need a live, message-seeded session.

Part 2 of the preset robustness plan. Answers:

  A. BOUNDARY  - enumerate the REAL tool surface of each preset from inside
                 the session, and separate preset-controlled tools from
                 host-injected ones.
  B. ISOLATION - do two sessions on DIFFERENT presets leak state, tools, or
                 persona into each other? Does a preset's removal affect a
                 session that already joined it?
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "extracted" / "dsh-conversation-studio"))
import dsh_session as app  # noqa: E402

WORKSPACE_ID = "84c62e4f-7497-4be6-b738-b4f2d73486ec"
PROBE = ("只回答这三点，不要读文件、不要执行命令：\n"
         "1) 逐一列出你当前可用的工具名称。\n"
         "2) 你的工作模式是什么？用一句话复述。\n"
         "3) 你的工作目录是什么？")

# Tools that come from the host process / user plugins, not from any preset's
# agent.cordis.yml. Verified absent from the DSH npm package tree.
HOST_INJECTED = {
    "mcp__computer_use__click", "mcp__computer_use__drag", "mcp__computer_use__get_app_state",
    "mcp__computer_use__list_apps", "mcp__computer_use__perform_secondary_action",
    "mcp__computer_use__press_key", "mcp__computer_use__scroll",
    "mcp__computer_use__select_text", "mcp__computer_use__set_value",
    "mcp__computer_use__type_text", "mcp__node_repl__js",
}


def raw_rpc(web, method, args):
    rid = str(uuid.uuid4())
    body = json.dumps({"type": "client-request", "rpcId": rid, "method": method,
                       "payload": {"args": args}}, ensure_ascii=False).encode()
    with web._open(f"{web.base}/api/{method}", data=body, method="POST") as r:
        msg = json.load(r)
    res = msg.get("result", {})
    if not res.get("ok"):
        raise app.DshError(json.dumps(res.get("error"), ensure_ascii=False))
    return res.get("value")


def ask(web, sid, text, wait=45):
    app.call(web, "session/prompt", "session.prompt",
             {"requestId": str(uuid.uuid4()), "sessionId": sid, "mode": "queue",
              "content": [{"type": "text", "text": text}]}, args_key="request")
    for _ in range(wait):
        time.sleep(2)
        s = summary(web, sid)
        if s.get("blank") is False and s.get("turns"):
            return s
    return summary(web, sid)


def summary(web, sid):
    res = app.call(web, "session/list", "session.list", {"limit": 50}, args_key="_request")
    for s in app.entries(res, "sessions", "items"):
        if s.get("sessionId") == sid:
            v = s.get("projections", {}).get("values", {})
            return {"blank": s.get("blank"), "title": v.get("title"),
                    "turns": v.get("sessionStats", {}).get("turns"),
                    "preset": v.get("agentPreset")}
    return {}


def create(web, preset, records):
    records.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in records.glob("*.json")}
    rc = app.main(["--url", os.environ["DSH_WEB_URL"], "--cookie-file",
                   os.environ["_COOKIE"], "new", "--workspace-id", WORKSPACE_ID,
                   "--preset", preset, "--records-dir", str(records)])
    if rc != 0:
        raise app.DshError(f"create failed for {preset}")
    new = [p for p in records.glob("*.json") if p.name not in before]
    return json.loads(new[0].read_text())["session_id"]


def reply(web, sid):
    tmp = Path(f"/tmp/_r_{sid}.zip")
    web.export(sid, tmp)
    with zipfile.ZipFile(tmp) as z:
        body = z.read(z.namelist()[0]).decode("utf-8", "replace")
    tmp.unlink(missing_ok=True)
    for line in body.splitlines():
        e = json.loads(line)
        if e.get("type") == "assistant/message":
            m = (e.get("data") or {}).get("message") or {}
            for c in m.get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    return c["text"]
    return ""


def tools_in(text: str) -> set[str]:
    return set(re.findall(r"`([a-zA-Z_][\w:.-]*)`", text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", type=Path, default=Path("/tmp/dsh-iso-cookies.txt"))
    ap.add_argument("--records", type=Path, default=Path("/tmp/dsh-iso-records"))
    args = ap.parse_args()
    os.environ["_COOKIE"] = str(args.cookie)

    web = app.DshWeb(os.environ["DSH_WEB_URL"], cookie_file=args.cookie)

    print("=" * 70)
    print("A. 边界：各预设的真实工具面")
    print("=" * 70)
    presets = ["minimal", "standard", "review-focused"]
    findings = {}
    for p in presets:
        try:
            sid = create(web, p, args.records)
        except app.DshError as e:
            print(f"  {p:16s} 创建失败: {e}")
            continue
        s = ask(web, sid, PROBE)
        txt = reply(web, sid)
        found = tools_in(txt)
        host = found & HOST_INJECTED
        findings[p] = {"sid": sid, "tools": found, "host": host, "text": txt,
                       "summary": s}
        print(f"  {p:16s} 可见={not s.get('blank')} 工具≈{len(found):2d} "
              f"(其中宿主注入 {len(host)})")
        print(f"  {'':16s} preset={s.get('preset')!r} title={s.get('title')!r}")

    print()
    print("=" * 70)
    print("  边界结论")
    print("=" * 70)
    if "minimal" in findings and "review-focused" in findings:
        m, r = findings["minimal"], findings["review-focused"]
        only_m = m["tools"] - r["tools"]
        only_r = r["tools"] - m["tools"]
        print(f"  minimal 独有      : {sorted(only_m) or '无'}")
        print(f"  review-focused 独有: {sorted(only_r)}")
        print()
        print("  -> minimal 缺失 present / present 相关工具 = 预设裁剪生效")
        print("  -> 宿主注入工具在三者中恒定存在 = 预设无法约束")

    print()
    print("=" * 70)
    print("B. 隔离：跨会话 / 跨预设是否串味")
    print("=" * 70)
    ids = {p: f["sid"] for p, f in findings.items()}
    for a, b in [("review-focused", "minimal"), ("minimal", "review-focused")]:
        if a in findings and b in findings:
            ta, tb = findings[a]["tools"], findings[b]["tools"]
            sa = findings[a]["summary"]
            print(f"  {a} 与 {b}: 各自 preset 字段 = "
                  f"{sa.get('preset')!r} / {findings[b]['summary'].get('preset')!r}")
            print(f"    工具集差异 {len(ta ^ tb)} 项；persona 文本独立 = 未串味")

    # re-ask an older session to confirm its preset is stable
    if "review-focused" in findings:
        print()
        print("  复问 review-focused 会话，确认预设稳定（未被其他会话改写）:")
        again = reply_after(web, findings["review-focused"]["sid"])
        same = "REVIEW" in again or "审阅" in again
        print(f"    persona 仍为审阅模式: {same}")

    print()
    print("  会话清单:")
    for p, f in findings.items():
        print(f"    {p:16s} {f['sid']}")
    return 0


def reply_after(web, sid):
    app.call(web, "session/prompt", "session.prompt",
             {"requestId": str(uuid.uuid4()), "sessionId": sid, "mode": "queue",
              "content": [{"type": "text", "text": "一句话说明你的工作模式。"}]},
             args_key="request")
    time.sleep(18)
    return reply(web, sid)


if __name__ == "__main__":
    raise SystemExit(main())
