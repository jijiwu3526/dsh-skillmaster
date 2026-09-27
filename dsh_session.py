#!/usr/bin/env python3
"""Create and archive DeepSeek Harness Web sessions through its native RPC."""

from __future__ import annotations

import argparse
import datetime as dt
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile


class DshError(RuntimeError):
    pass


class DshHttpError(DshError):
    def __init__(self, code: int, detail: str):
        self.code = code
        super().__init__(f"DSH HTTP {code}: {detail}")


def local_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise DshError("只接受本机 http://localhost 或 127.0.0.1 的 DSH Web 地址；远程实例请先建立本机 SSH 隧道。")
    if parsed.username or parsed.password or not parsed.port:
        raise DshError("DSH Web 地址必须含端口，不能包含用户名或密码。")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


class DshWeb:
    def __init__(self, url: str, *, timeout: float = 30.0, cookie_file: Path | None = None):
        self.base = local_url(url)
        self.timeout = timeout
        self.cookie_file = cookie_file
        jar = http.cookiejar.MozillaCookieJar(str(cookie_file) if cookie_file else None)
        if cookie_file and cookie_file.exists() and cookie_file.stat().st_size > 0:
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except (http.cookiejar.LoadError, OSError):
                # A truncated, empty, or half-written cache is not a hard error:
                # drop it and fall back to authenticating from the token URL.
                jar.clear()
        self.jar = jar
        self.token_url = url if urllib.parse.urlsplit(url).query else None
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
            urllib.request.ProxyHandler({}),
        )
        # On recent DSH releases the startup URL exchanges a token for an
        # HttpOnly session cookie. Never log or persist that URL or cookie.
        if self.token_url and not len(jar):
            with self._open(url, _retried=True):
                pass
            self._save_cookies(jar)

    def _save_cookies(self, jar: http.cookiejar.MozillaCookieJar) -> None:
        if not (self.cookie_file and len(jar)):
            return
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.cookie_file.exists():
            fd = os.open(self.cookie_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        os.chmod(self.cookie_file, 0o600)
        jar.save(ignore_discard=True, ignore_expires=True)

    def _reauthenticate(self) -> bool:
        """Trade a fresh token URL for a new cookie; True when one was obtained."""
        if not self.token_url:
            return False
        self.jar.clear()
        try:
            with self._open(self.token_url, _retried=True):
                pass
        except DshError:
            return False
        self._save_cookies(self.jar)
        return len(self.jar) > 0

    def _open(self, url: str, *, data: bytes | None = None, method: str = "GET",
              _retried: bool = False):
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            return self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            # Response bodies can contain operational details; keep a bounded
            # excerpt but never echo the token-bearing request URL.
            detail = exc.read(500).decode("utf-8", "replace")
            if exc.code == 401:
                # A cached cookie may simply have expired while a still-valid
                # token URL was supplied. Re-exchange once before giving up, so
                # the advice in the error message is actually actionable.
                if not _retried and self._reauthenticate():
                    return self._open(url, data=data, method=method, _retried=True)
                raise DshHttpError(401, "未认证或 Cookie 已过期。已尝试自动重新认证但失败——"
                                         "请确认 dsh-local-bridge 插件已安装且 DSH 正在运行，"
                                         "或手动传入 DSH 启动时打印的含 ?token= 的完整 URL。") from exc
            raise DshHttpError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise DshError(f"无法连接 DSH Web：{exc.reason}") from exc

    def rpc(self, method: str, payload: dict, *, args_key: str | None = None) -> object:
        request_id = str(uuid.uuid4())
        wire_payload = {"args": {} if args_key is None else {args_key: payload}} if "/" in method else payload
        body = json.dumps({
            "type": "client-request", "rpcId": request_id,
            "method": method, "payload": wire_payload,
        }, ensure_ascii=False).encode("utf-8")
        with self._open(f"{self.base}/api/{method}", data=body, method="POST") as response:
            try:
                message = json.load(response)
            except (ValueError, UnicodeDecodeError) as exc:
                raise DshError(f"{method} 返回的不是 JSON") from exc
        if not isinstance(message, dict) or message.get("type") != "server-response" or message.get("rpcId") != request_id:
            raise DshError(f"{method} 返回了不匹配的 RPC 信封")
        result = message.get("result")
        if not isinstance(result, dict):
            raise DshError(f"{method} 缺少业务结果")
        if result.get("ok") is not True:
            error = result.get("error") or {}
            failure = error.get("failure", error) if isinstance(error, dict) else {}
            raise DshError(f"{method} 失败：{failure.get('code', 'unknown')} — {failure.get('message', error)}")
        return result.get("value")

    def export(self, session_id: str, destination: Path) -> None:
        query = urllib.parse.urlencode({"sessionId": session_id, "includeDescendants": "true"})
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        try:
            with self._open(f"{self.base}/api/session.export?{query}") as source, temporary.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
            if not zipfile.is_zipfile(temporary):
                raise DshError("导出内容不是有效的 ZIP；请检查 DSH 版本或认证状态")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def entries(value: object, *keys: str) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, list):
                return [item for item in found if isinstance(item, dict)]
    raise DshError(f"DSH 返回的列表结构无法识别：{keys}")


def field(value: object, *keys: str) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
        for key in ("session", "workspace", "summary", "item"):
            if isinstance(value.get(key), dict):
                nested = field(value[key], *keys)
                if nested:
                    return nested
    return None


def workspace_payload(web: DshWeb, path: Path, *, register: bool) -> dict:
    expanded = path.expanduser()
    if not expanded.is_dir():
        raise DshError(f"工作区不是已有目录：{expanded}")
    target = str(expanded.resolve(strict=True))
    if not register:
        return {"cwd": target}
    created = call(web, "workspace/create", "workspace.create", {"path": target}, args_key="request")
    found = field(created, "workspaceId", "id")
    if not found:
        raise DshError("工作区已请求登记，但返回值没有 ID；请在 Web 中检查后再试。")
    return {"workspaceId": found}


def preset_list(web: DshWeb) -> list[dict]:
    return entries(call(web, "agentPresets/list", "agentPreset.list"), "presets", "items")


def call(web: DshWeb, remote: str, legacy: str, payload: dict | None = None,
         *, args_key: str | None = None) -> object:
    """Only a missing Remote route can trigger the old protocol fallback."""
    try:
        return web.rpc(remote, payload or {}, args_key=args_key)
    except DshHttpError as exc:
        if exc.code != 404:
            raise
    return web.rpc(legacy, payload or {})


def session_state(web: DshWeb, session_id: str) -> dict | None:
    """Locate one session in the list and return its visible state.

    DSH marks a session `blank: true` until it holds at least one turn, and
    the Web sidebar hides blank sessions. That is why a freshly created
    session looks like it does not exist.
    """
    found = session_list(web)
    for row in found:
        if field(row, "sessionId", "id") == session_id:
            return row
    return None


def session_list(web: DshWeb) -> list[dict]:
    return entries(call(web, "session/list", "session.list", {"limit": 200},
                        args_key="_request"), "sessions", "items")


def send_message(web: DshWeb, session_id: str, text: str, *,
                 mode: str = "queue", wait: int = 0) -> dict:
    """Deliver one user message so a blank session becomes visible.

    A session created by `new` carries no turn, so DSH keeps it out of the
    sidebar. Sending one message is the supported way to make it appear.
    """
    if not text.strip():
        raise DshError("消息正文不能为空")
    request = {
        "requestId": str(uuid.uuid4()),
        "sessionId": session_id,
        "mode": mode,
        "content": [{"type": "text", "text": text}],
    }
    result = call(web, "session/prompt", "session.prompt", request, args_key="request")
    receipt = {
        "session_id": session_id,
        "accepted": result.get("accepted") is True if isinstance(result, dict) else False,
        "mode": mode,
    }
    if wait:
        deadline = time.monotonic() + max(wait, 0)
        state = None
        while time.monotonic() < deadline:
            time.sleep(2)
            state = session_state(web, session_id)
            if state is not None and state.get("blank") is False:
                break
        receipt["blank"] = state.get("blank") if state else None
        receipt["title"] = field(state or {}, "title") or None
        receipt["visible_in_sidebar"] = state is not None and state.get("blank") is False
    return receipt


def resolve_url(explicit: str | None = None, timeout: float = 10.0) -> tuple[str, str]:
    """Find an authenticated Web URL, preferring the in-process bridge plugin.

    Order: explicit argument → dsh-local-bridge plugin → DSH_WEB_URL → plain
    loopback origin. The plugin path means a CLI never needs a token pasted
    in by a human, so nothing sensitive ends up in shell history or a log.
    """
    if explicit:
        return explicit, "explicit"
    try:
        from dsh_bridge import resolve as _bridge_resolve
    except ImportError:
        _bridge_resolve = None
    if _bridge_resolve is not None:
        try:
            found = _bridge_resolve(timeout=timeout)
            return found["url"], f"bridge:{found.get('source', '?')}"
        except Exception:  # noqa: BLE001 — fall through to env/plain origin
            pass
    env = os.environ.get("DSH_WEB_URL")
    if env:
        return env, "env"
    return "http://127.0.0.1:3080", "plain-origin"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DSH Preset 会话创建与完整日志归档")
    parser.add_argument("--url", default=None,
                        help="DSH Web 启动时打印的完整 URL。省略时优先向本机 dsh-local-bridge "
                             "插件索取一个新鲜的带 token URL（需插件已安装且 DSH 在运行），"
                             "其次读 DSH_WEB_URL，最后回落到无 token 的本机地址")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--cookie-file", type=Path,
                        default=Path.home() / ".config/dsh-conversation-studio/cookies.txt",
                        help="本机认证 Cookie 缓存路径（0600，不要放进分享的 ZIP）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("presets", help="查看 DSH 已加载的 Agent Preset")
    new = commands.add_parser("new", help="直接创建一个 Web 会话")
    new.add_argument("--preset", help="预设 ID；省略则使用 DSH 当前默认预设")
    scope = new.add_mutually_exclusive_group(required=True)
    scope.add_argument("--workspace-id")
    scope.add_argument("--workspace-path", type=Path)
    new.add_argument("--register-workspace", action="store_true", help="先把已有目录登记到 DSH 工作区，再创建分组会话")
    new.add_argument("--session-id", help="预分配 ID；同 ID 重试可避免网络故障后重复创建")
    new.add_argument("--records-dir", type=Path, default=Path("records"))
    archive = commands.add_parser("export", help="导出原始会话日志、子会话与附件 ZIP")
    archive.add_argument("session_id")
    archive.add_argument("--output", type=Path)
    send = commands.add_parser(
        "send", help="向会话发送一条消息（新建会话后必须发一条才会在侧栏显示）")
    send.add_argument("session_id")
    send.add_argument("--text", required=True, help="消息正文")
    send.add_argument("--mode", choices=["queue", "steer"], default="queue")
    send.add_argument("--wait", type=int, default=0,
                      help="等待会话脱离 blank 状态的秒数；0 表示只投递不等待")
    args = parser.parse_args(argv)
    try:
        url, origin = resolve_url(args.url)
        web = DshWeb(url, timeout=args.timeout, cookie_file=args.cookie_file)
        if args.command == "presets":
            print(json.dumps(preset_list(web), ensure_ascii=False, indent=2))
        elif args.command == "new":
            known = preset_list(web)
            selected = next((item for item in known if field(item, "id", "presetId") == args.preset), None)
            if args.workspace_id and args.register_workspace:
                raise DshError("--register-workspace 只对 --workspace-path 有意义；"
                               "--workspace-id 指向已有工作区，不会再登记新的。请二选一。")
            if args.preset and selected is None:
                raise DshError(f"预设 {args.preset!r} 不在 DSH 的预设清单中；先在 DSH 中创建并刷新。")
            if selected and (selected.get("broken") or selected.get("healthy") is False or selected.get("status") in {"broken", "invalid"}):
                raise DshError(f"预设 {args.preset!r} 当前不可用；先查看 DSH 的配置诊断。")
            location = ({"workspaceId": args.workspace_id} if args.workspace_id else
                        workspace_payload(web, args.workspace_path, register=args.register_workspace))
            payload = {**location, "sessionId": args.session_id or f"session-{uuid.uuid4()}"}
            if args.preset:
                payload["agentPreset"] = args.preset
            result = call(web, "session/create", "session.create", payload, args_key="request")
            sid = field(result, "sessionId", "id")
            if not sid:
                raise DshError("DSH 接受了创建请求，但没有返回会话 ID；请检查 Web，避免重复创建。")
            record = {
                "session_id": sid, "location": location, "requested_preset": args.preset,
                "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "note": "空白会话可能暂不出现在侧栏；在 Web 里打开并发送第一条消息。",
            }
            args.records_dir.mkdir(parents=True, exist_ok=True)
            receipt = args.records_dir / f"{sid}.json"
            if receipt.exists():
                raise DshError(f"会话 {sid} 已创建，但记录文件 {receipt} 已存在；为防覆盖请手工核对。")
            receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({**record, "receipt": str(receipt)}, ensure_ascii=False, indent=2))
        else:
            sid = args.session_id
            if not sid or any(c in sid for c in "/\\\0"):
                raise DshError("会话 ID 不合法")
            if args.command == "send":
                receipt = send_message(web, sid, args.text, mode=args.mode, wait=args.wait)
                print(json.dumps(receipt, ensure_ascii=False, indent=2))
                return 0 if receipt.get("ok") else 1
            target = args.output or Path("records") / f"dsh-session-{sid}.zip"
            if target.exists():
                raise DshError(f"目标已存在，拒绝覆盖：{target}")
            web.export(sid, target)
            print(json.dumps({"session_id": sid, "archive": str(target)}, ensure_ascii=False))
    except (DshError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
