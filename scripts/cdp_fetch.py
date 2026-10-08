#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 Chrome DevTools Protocol (CDP) 的无头浏览器抓取器
=====================================================
只依赖标准库（urllib + socket），不需要 Playwright/Selenium。

设计目的：验证「全自动 + 截图分析」路线在目标站点的真实可行性。
  1) render(url)      → 渲染后取 DOM 文本（对 SPA 站点，比 httpx 强得多）
  2) screenshot(url)  → 输出 PNG（供视觉模型/OCR 分析）
  3) 结果同时落盘，便于对每个站点做可用性结论

用法：
  python cdp_fetch.py --url <URL> [--shot out.png] [--wait 6] [--text out.txt]
  python cdp_fetch.py --batch targets.json

合规说明：仅访问公开页面，不注入登录 Cookie、不破解验证码、不做指纹伪装；
         单 URL 串行 + 间隔，避免给站点造成压力。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlparse

# 控制台 UTF-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def find_browser() -> str:
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    raise RuntimeError("找不到 Edge/Chrome 可执行文件")


# --------------------------------------------------------------------------- #
# 极简 WebSocket 客户端（RFC6455 客户端帧）
# --------------------------------------------------------------------------- #

class WS:
    """只实现 CDP 所需的最小 WebSocket 客户端。"""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        u = urlparse(url)
        host, port = u.hostname, u.port or 80
        path = u.path + (f"?{u.query}" if u.query else "")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        # 读握手响应
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError(f"WebSocket 握手失败: {buf[:120]!r}")
        self._buf = buf.split(b"\r\n\r\n", 1)[1]

    def _recv_exact(self, n: int) -> bytes:
        out = b""
        if self._buf:
            out, self._buf = self._buf[:n], self._buf[n:]
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError("连接关闭")
            out += chunk
        return out

    def send(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])  # FIN + text
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv(self) -> str:
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            data = self._recv_exact(length) if length else b""
            if opcode == 0x9:      # ping → pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode == 0x8:      # close
                raise ConnectionError("服务端关闭")
            if opcode in (0x1, 0x2, 0x0):
                return data.decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# CDP 会话
# --------------------------------------------------------------------------- #

class Browser:
    def __init__(self, ua: str = DEFAULT_UA, width: int = 1440, height: int = 2200,
                 headless: bool = True, extra_args: list[str] | None = None) -> None:
        self.exe = find_browser()
        self.profile = tempfile.mkdtemp(prefix="cdp-profile-")
        self.port = self._free_port()
        args = [
            self.exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            f"--window-size={width},{height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--hide-scrollbars",
            f"--user-agent={ua}",
        ]
        if headless:
            args.append("--headless=new")
        args += extra_args or []
        args.append("about:blank")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws: WS | None = None
        self._id = 0
        self._wait_ready()

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def _wait_ready(self, timeout: float = 25.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=2
                ).read()
                tabs = json.loads(raw)
                page = next((t for t in tabs if t.get("type") == "page"), None)
                if page and page.get("webSocketDebuggerUrl"):
                    self.ws = WS(page["webSocketDebuggerUrl"])
                    return
            except Exception:  # noqa: BLE001
                time.sleep(0.4)
        raise RuntimeError("浏览器 CDP 未就绪")

    def cmd(self, method: str, params: dict | None = None, timeout: float = 40.0) -> dict:
        assert self.ws
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"{method} 超时")

    def render(self, url: str, wait: float = 5.0, scroll: bool = True) -> dict:
        """导航到 URL，等待渲染，返回 DOM 文本 / 标题 / 状态。"""
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        self.cmd("Network.enable")
        self.cmd("Page.navigate", {"url": url})

        deadline = time.time() + 30
        while time.time() < deadline:
            ready = self.cmd(
                "Runtime.evaluate",
                {"expression": "document.readyState", "returnByValue": True},
            )
            if ready.get("result", {}).get("value") == "complete":
                break
            time.sleep(0.3)

        # 触发懒加载：滚到底再回顶
        if scroll:
            for expr in ("window.scrollTo(0, document.body.scrollHeight)",
                         "window.scrollTo(0, document.body.scrollHeight*2)",
                         "window.scrollTo(0, 0)"):
                try:
                    self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
                    time.sleep(0.6)
                except Exception:  # noqa: BLE001
                    pass

        time.sleep(wait)

        info = self.cmd(
            "Runtime.evaluate",
            {
                "expression": (
                    "JSON.stringify({"
                    "title: document.title,"
                    "url: location.href,"
                    "text: document.body ? document.body.innerText : '',"
                    "html_len: document.documentElement.outerHTML.length,"
                    "imgs: document.images.length"
                    "})"
                ),
                "returnByValue": True,
            },
        )
        data = json.loads(info["result"]["value"])
        return data

    def screenshot(self, path: str, full_page: bool = True) -> None:
        params: dict = {"format": "png"}
        if full_page:
            metrics = self.cmd("Page.getLayoutMetrics")
            css = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            w = min(int(css.get("width", 1440)), 1600)
            h = min(int(css.get("height", 2200)), 6000)
            params["clip"] = {"x": 0, "y": 0, "width": w, "height": h, "scale": 1}
            params["captureBeyondViewport"] = True
        res = self.cmd("Page.captureScreenshot", params, timeout=60)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self.profile, ignore_errors=True)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def summarize(text: str, limit: int = 420) -> str:
    one = re.sub(r"\s+", " ", text or "").strip()
    return one[:limit]


def run_one(br: Browser, url: str, wait: float, shot: str | None, text_out: str | None) -> dict:
    print("=" * 78)
    print(f"URL: {url}")
    try:
        data = br.render(url, wait=wait)
    except Exception as exc:  # noqa: BLE001
        print(f"  !! 渲染失败: {exc}")
        return {"url": url, "ok": False, "error": str(exc)}

    text = data.get("text", "")
    print(f"  标题 : {data.get('title')!r}")
    print(f"  最终URL: {data.get('url')}")
    print(f"  正文 : {len(text)} 字符 | DOM {data.get('html_len')} | 图片 {data.get('imgs')}")
    print(f"  摘要 : {summarize(text)}")

    if shot:
        try:
            br.screenshot(shot)
            size = os.path.getsize(shot)
            print(f"  截图 : {shot} ({size/1024:.0f} KB)")
        except Exception as exc:  # noqa: BLE001
            print(f"  !! 截图失败: {exc}")

    if text_out:
        with open(text_out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"  存文本: {text_out}")

    return {"url": url, "ok": True, "title": data.get("title"),
            "final_url": data.get("url"), "text_len": len(text),
            "text": text}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--batch", help="JSON 文件：[{url, name, wait?}]")
    ap.add_argument("--shot-dir", help="为每个 URL 输出截图到该目录")
    ap.add_argument("--text-dir", help="为每个 URL 输出正文到该目录")
    ap.add_argument("--wait", type=float, default=5.0)
    ap.add_argument("--out", help="结果 JSON")
    ap.add_argument("--show", action="store_true", help="显示浏览器窗口（人工登录用）")
    args = ap.parse_args()

    targets: list[dict] = [{"url": u} for u in args.url]
    if args.batch:
        with open(args.batch, encoding="utf-8") as fh:
            targets += json.load(fh)
    if not targets:
        print("需要 --url 或 --batch", file=sys.stderr)
        return 2

    if args.shot_dir:
        os.makedirs(args.shot_dir, exist_ok=True)
    if args.text_dir:
        os.makedirs(args.text_dir, exist_ok=True)

    br = Browser(headless=not args.show)
    print(f"浏览器已启动（端口 {br.port}，headless={not args.show}）")
    results = []
    try:
        for i, t in enumerate(targets):
            name = t.get("name") or f"t{i:02d}"
            shot = os.path.join(args.shot_dir, f"{name}.png") if args.shot_dir else None
            txt = os.path.join(args.text_dir, f"{name}.txt") if args.text_dir else None
            r = run_one(br, t["url"], t.get("wait", args.wait), shot, txt)
            r["name"] = name
            results.append(r)
            time.sleep(2)
    finally:
        br.close()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        print(f"\n结果写入 {args.out}")

    ok = sum(1 for r in results if r.get("ok"))
    print(f"\n完成：{ok}/{len(results)} 个页面渲染成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
