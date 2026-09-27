#!/usr/bin/env python3
"""Read the fresh authenticated Web URL from the `dsh-local-bridge` plugin.

The plugin runs inside the DSH process, where `authenticatedUrl()` can mint a
valid token URL on demand. This helper talks to its loopback route so a CLI
never has to be handed a token by a human.

Falls back cleanly:
  1. the bridge plugin, if installed and running;
  2. `DSH_WEB_URL`, if the caller already exported one;
  3. a plain loopback origin, which works while a cached cookie is still valid.

Nothing here ever writes the token to disk.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BRIDGE_PATH = "/local-bridge/auth"
DEFAULT_SECRET = Path.home() / ".dsh" / "local-bridge.secret"


class BridgeError(RuntimeError):
    pass


def _default_origin() -> str:
    env = os.environ.get("DSH_WEB_URL", "")
    if env:
        # Keep only the origin; any token stays out of the base URL.
        parts = urllib.parse.urlsplit(env)
        if parts.hostname and parts.port:
            return f"http://{parts.hostname}:{parts.port}"
    return "http://127.0.0.1:3080"


def read_secret(path: Path = DEFAULT_SECRET) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def fetch_via_bridge(origin: str, secret: str, timeout: float = 10.0) -> dict:
    """Ask the plugin for a freshly minted authenticated URL."""
    url = f"{origin.rstrip('/')}{BRIDGE_PATH}"
    request = urllib.request.Request(
        url, headers={"X-DSH-Bridge-Secret": secret, "Accept": "application/json"})
    # Never route a credential through an environment proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(200).decode("utf-8", "replace")
        raise BridgeError(f"桥接返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"无法连接桥接 {url}：{exc.reason}（DSH 是否在运行？）") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise BridgeError(f"桥接返回异常：{payload}")
    return payload


def resolve(origin: str | None = None, secret_file: Path = DEFAULT_SECRET,
            timeout: float = 10.0) -> dict:
    """Best available authenticated URL, with a note on how it was obtained."""
    target = origin or os.environ.get("DSH_WEB_BRIDGE_ORIGIN") or _default_origin()

    secret = read_secret(secret_file)
    if secret:
        try:
            result = fetch_via_bridge(target, secret, timeout)
            return {"url": result["url"], "origin": result.get("origin", target),
                    "source": "bridge-plugin"}
        except BridgeError as exc:
            bridge_error = str(exc)
    else:
        bridge_error = "未找到共享密钥（插件未安装或 DSH 未重启？）"

    env = os.environ.get("DSH_WEB_URL")
    if env:
        return {"url": env, "origin": target, "source": "env", "note": bridge_error}

    return {"url": target, "origin": target, "source": "plain-origin",
            "note": bridge_error + "；当前仅回落到无 token 地址，需要已缓存的 Cookie 有效。"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="取得 DSH Web 的带 token URL（优先走本机桥接插件）")
    ap.add_argument("--origin", help="DSH 根地址，默认 http://127.0.0.1:3080")
    ap.add_argument("--secret-file", type=Path, default=DEFAULT_SECRET)
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--quiet", action="store_true", help="只输出 URL 本身")
    args = ap.parse_args(argv)
    try:
        result = resolve(args.origin, args.secret_file, args.timeout)
    except BridgeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    if args.quiet:
        print(result["url"])
    else:
        redacted = {k: v for k, v in result.items() if k != "url"}
        redacted["url"] = "<redacted; use --quiet to print>"
        print(json.dumps(redacted, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
