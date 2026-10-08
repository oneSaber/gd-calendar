#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
页面 XHR 响应拦截器（CDP Network domain）
========================================
思路：不逆向任何签名，而是让**页面自己去请求**，我们只把它的 JSON 响应抄下来。
这比解析 DOM 稳定得多，也不涉及伪造签名。

实现要点（踩过的坑都写在注释里）：
  * CDP 的 Network.responseReceived / Network.getResponseBody 走的是**独立 socket**，
    且必须带 sessionId，所以每条命令都要路由到事件所属的 session。
  * 想在导航开始前挂上监听，必须用 Target.setAutoAttach + waitForDebuggerOnStart。

用法：
  python cdp_xhr.py --url <URL> --match "/api/container" --out out.json [--wait 12]
"""

from __future__ import annotations

import argparse
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from cdp_fetch import Browser  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def capture_xhr(url: str, match: str, wait: float = 12.0,
                scroll: bool = True, ua: str | None = None) -> list[dict]:
    """渲染 url，抓取所有 URL 含 match 的响应体（JSON/文本）。"""
    br = Browser(headless=True, **({"ua": ua} if ua else {}))
    hits: list[dict] = []
    try:
        br.cmd("Page.enable")
        br.cmd("Runtime.enable")
        br.cmd("Network.enable")

        # 扁平模式：所有事件都在主 session 上，命令直接发给它。
        # （Browser 里我们已经连到了 page 的 webSocketDebuggerUrl，等价于附加到该 target）
        br.cmd("Page.navigate", {"url": url})

        # 轮询式收集：不断读 socket，把命中 match 的响应体取出来
        deadline = time.time() + wait
        scrolled = False
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                msg = json.loads(br.ws.recv())  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001 超时/关闭
                break

            method = msg.get("method")
            if method == "Network.responseReceived":
                p = msg.get("params", {})
                rurl = p.get("response", {}).get("url", "")
                rid = p.get("requestId")
                if match in rurl and rid not in seen:
                    seen.add(rid)
                    try:
                        body = br.cmd("Network.getResponseBody", {"requestId": rid})
                        text = body.get("body", "")
                        hits.append({
                            "url": rurl,
                            "mime": p.get("response", {}).get("mimeType"),
                            "status": p.get("response", {}).get("status"),
                            "len": len(text),
                            "body": text,
                        })
                        print(f"  ✓ 命中 {rurl[:96]}  ({len(text)} 字节)")
                    except Exception as exc:  # noqa: BLE001
                        print(f"  ✗ 取 body 失败 {rurl[:70]}: {exc}")

            if not scrolled and time.time() > deadline - wait * 0.6:
                scrolled = True
                for expr in ("window.scrollTo(0, document.body.scrollHeight)",
                             "window.scrollTo(0, document.body.scrollHeight*2)"):
                    try:
                        br.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
                        time.sleep(0.8)
                    except Exception:  # noqa: BLE001
                        pass
    finally:
        br.close()
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--match", required=True, help="URL 子串匹配")
    ap.add_argument("--out", help="命中结果写入 JSON")
    ap.add_argument("--wait", type=float, default=12.0)
    args = ap.parse_args()

    print("=" * 78)
    print(f"URL   : {args.url}")
    print(f"匹配  : {args.match}")
    print("=" * 78)
    hits = capture_xhr(args.url, args.match, wait=args.wait)
    print(f"\n共命中 {len(hits)} 个响应")

    # 尝试把每个响应解析成 JSON，打印顶层结构
    for h in hits:
        try:
            j = json.loads(h["body"])
            keys = list(j.keys())[:12] if isinstance(j, dict) else f"list[{len(j)}]"
            print(f"  - {h['url'][:70]}\n      JSON 顶层: {keys}")
        except Exception:  # noqa: BLE001
            print(f"  - {h['url'][:70]}\n      非 JSON  ({h['len']} 字节)")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(hits, fh, ensure_ascii=False, indent=2)
        print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
