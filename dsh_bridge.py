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
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BRIDGE_PATH = "/local-bridge/auth"
# Honour a custom DSH_HOME the same way the plugin does. Getting this wrong is
# silent: the client looks in ~/.dsh, the plugin writes elsewhere, and the only
# symptom is a mysterious "未找到共享密钥".
DEFAULT_SECRET = Path(
    os.environ.get("DSH_HOME") or Path.home() / ".dsh",
) / "local-bridge.secret"


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
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError is not an OSError, so a secret file that is not
        # valid UTF-8 used to crash before this function could return. A
        # corrupt secret is indistinguishable from an absent one, and
        # degrading to the next fallback rung is the right answer for both.
        return None
    return value or None


def _normalise_origin(origin: str) -> str:
    """Accept `host:port` and `https://host` as well as a full origin.

    A schemeless value would otherwise reach urllib as a relative path and
    fail with "unknown url type", which reads like a server problem when it is
    really a misconfigured argument.
    """
    text = origin.strip().rstrip("/")
    if not text:
        return "http://127.0.0.1:3080"
    if "://" in text:
        return text
    return f"http://{text}"


def fetch_via_bridge(origin: str, secret: str, timeout: float = 10.0) -> dict:
    """Ask the plugin for a freshly minted authenticated URL.

    Every failure mode here has to become a `BridgeError`. A bridge that is
    down, restarting, half-written, or speaking garbage is an ordinary
    condition for this client — the whole point of the fallback chain is to
    degrade quietly — and a raw traceback helps nobody.
    """
    origin = _normalise_origin(origin)
    url = f"{origin}{BRIDGE_PATH}"
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
    except TimeoutError as exc:
        # A DSH that is starting, or wedged, is the single most likely real
        # failure — far likelier than a complete non-JSON body.
        raise BridgeError(f"桥接超时（{timeout}s）：{url} 是否还在启动？") from exc
    except http.client.HTTPException as exc:
        # Truncated body (IncompleteRead), non-HTTP garbage (BadStatusLine) —
        # neither is an OSError or a ValueError, so they need naming.
        raise BridgeError(f"桥接响应中断（DSH 正在重启？）：{exc}") from exc
    except json.JSONDecodeError as exc:
        # A 200 carrying something that is not JSON: an HTML error page from a
        # proxy, or a body truncated between write and read.
        raise BridgeError(f"桥接返回的不是 JSON（DSH 正在重启？）：{exc}") from exc
    except UnicodeDecodeError as exc:
        # A body that is not valid UTF-8 — a gzipped page, or a multibyte
        # character cut in half by a restart mid-write. This is a ValueError
        # but NOT a JSONDecodeError, so it needs its own clause; catching only
        # JSONDecodeError here regressed exactly this case.
        raise BridgeError(f"桥接返回的不是 UTF-8 文本（DSH 正在重启？）：{exc}") from exc
    except OSError as exc:
        # Connection reset before the status line, and anything else the
        # socket layer can throw that is not already a URLError.
        raise BridgeError(f"无法连接桥接 {url}：{exc}（DSH 是否在运行？）") from exc
    except (RecursionError, ValueError) as exc:
        # The backstop that makes the docstring true. Deeply nested JSON
        # exhausts the stack (RecursionError is a RuntimeError, so no earlier
        # clause sees it), and a syntactically valid integer literal longer
        # than CPython's digit limit is a plain ValueError, not a
        # JSONDecodeError. Both are "this response was not usable".
        raise BridgeError(f"桥接返回无法解析（DSH 正在重启？）：{exc}") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise BridgeError(f"桥接返回异常：{payload}")
    url_value = payload.get("url")
    if not isinstance(url_value, str) or not url_value:
        # `ok: true` without a usable url is version skew or a partial write.
        # Reporting it as a bridge failure keeps the fallback chain working
        # instead of handing the caller a None it will try to open.
        raise BridgeError("桥接返回 ok 但没有可用的 url")
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
