#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书发帖助手（创作者中心）—— 可见浏览器 + 你自己的登录态
=========================================================
只做两件事：**让你扫码登录**，然后**用你自己的会话驱动发布页**。
不注入 Cookie、不伪造签名、不逆向接口、不改 `navigator.webdriver` 之类的指纹
—— 走的是平台自己的 UI，和真人操作同一条路。

浏览器用**持久化 profile**，所以登录一次之后（甚至重启浏览器）都不用再扫。

用法（全部通过 CDP 连到同一个浏览器，端口 9222）：

  python scripts/xhs_publish.py launch     # 起可见的 Edge（后台跑着，别关）
  python scripts/xhs_publish.py status      # 看当前落点 + 是否已登录
  python scripts/xhs_publish.py login       # 打开登录页，等你扫码（轮询到登录为止）
  python scripts/xhs_publish.py dump        # 把发布页的可交互元素列出来（调选择器用）
  python scripts/xhs_publish.py post --dry-run   # 填标题/正文/传图，**不点发布**
  python scripts/xhs_publish.py post --publish   # 真的点发布
  python scripts/xhs_publish.py close       # 关掉这个浏览器

profile 与 cookie 落在仓库**外面**，避免误提交：
  %LOCALAPPDATA%\\gd-calendar\\xhs-profile\\   （浏览器 profile）
  %LOCALAPPDATA%\\gd-calendar\\xhs-cookies.json（导出给你看的 cookie 清单）
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_fetch import WS, find_browser  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

PORT = 9222
BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "gd-calendar"
PROFILE = BASE / "xhs-profile"
COOKIES = BASE / "xhs-cookies.json"
SHOT_DIR = Path(__file__).resolve().parent.parent / "promo" / "xhs-run"

CREATOR_HOME = "https://creator.xiaohongshu.com/"
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
LOGIN_HINT = "/login"


# --------------------------------------------------------------------------- #
# 连到一个已经在跑的浏览器
# --------------------------------------------------------------------------- #

def list_targets(port: int = PORT) -> list[dict]:
    raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3).read()
    return json.loads(raw)


def wait_port(port: int = PORT, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            list_targets(port)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def new_page(port: int = PORT, url: str = PUBLISH_URL) -> dict:
    """自己开一个标签页（PUT /json/new），而不是占用别人正在看的页面。

    ⚠️ 这里**不能**对 URL 做 quote：Chrome 的 `/json/new?` 是「问号后面整串就是 URL」，
    编码过的 `https%3A%2F%2F…` 会被当成相对路径，结果是 about:blank（实测踩过）。
    """
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


class Session:
    """一个页面 target 上的 CDP 会话。

    ⚠️ 只认 `creator.xiaohongshu.com`（创作者中心）。
    浏览器里可能同时开着用户自己在看的 `www.xiaohongshu.com` 标签页
    （实测出现过 13 个 explore / 搜索页），如果按「取第一个页面」的写法，
    自动化就会跑去填用户正在看的那个页面 —— 所以这里**只挑创作者中心，
    挑不到就自己新开一个**，绝不劫持其它标签页。
    """

    def __init__(self, port: int = PORT, prefer: str = "creator.xiaohongshu.com",
                 allow_any: bool = False) -> None:
        self.port = port
        self.created = False
        pages = [t for t in list_targets(port)
                 if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        page = None
        if prefer:
            page = next((t for t in pages if prefer in (t.get("url") or "")), None)
        if page is None and allow_any:
            page = pages[0]
        if page is None:
            print(f"（没有创作者中心标签页，自己开一个；当前共 {len(pages)} 个页面）")
            page = new_page(port)
            self.created = True
        self.ws = WS(page["webSocketDebuggerUrl"], timeout=60.0)
        self._id = 0
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        if self.created:
            # 新标签页兜底导航一次（/json/new 的 URL 偶尔不生效）
            time.sleep(0.6)
            if "creator.xiaohongshu.com" not in str(self.ev("location.href") or ""):
                self.cmd("Page.navigate", {"url": PUBLISH_URL})
                time.sleep(3)

    def cmd(self, method: str, params: dict | None = None, timeout: float = 60.0) -> dict:
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

    # ---- 基础动作 ----
    def ev(self, expr: str, await_promise: bool = False):
        res = self.cmd("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True,
                        "awaitPromise": await_promise})
        return res.get("result", {}).get("value")

    def url(self) -> str:
        return str(self.ev("location.href") or "")

    def title(self) -> str:
        return str(self.ev("document.title") or "")

    def nav(self, url: str, wait: float = 4.0) -> None:
        self.cmd("Page.navigate", {"url": url})
        deadline = time.time() + 40
        while time.time() < deadline:
            if str(self.ev("document.readyState") or "") == "complete":
                break
            time.sleep(0.3)
        time.sleep(wait)

    def _point(self, selector: str, index: int = 0, text: str = "", exact: bool = False):
        """返回元素的视口坐标（先滚到中间）。

        ⚠️ 按文本找元素时**必须优先取面积最小的那个**：小红书页面里
        「上传图文」既是一个 <div> 标签页，也是整页容器的 innerText 的一部分；
        按文档顺序取第一个会点到整页外层容器（实测点不中标签页）。
        """
        cmp = "t === want" if exact else "t.includes(want)"
        expr = f"""
        (() => {{
          const want = {json.dumps(text)};
          // ⚠️ 不要用 offsetParent 判可见：抽屉/弹窗是 position:fixed，
          //    它们的 offsetParent 是 null，会被整体漏掉。用 rect 判。
          const area = r => r.width * r.height;
          const rectOf = e => e.getBoundingClientRect();
          const boxed = [...document.querySelectorAll({json.dumps(selector)})]
            .map(e => ({{ e, r: rectOf(e) }}))
            .filter(o => o.r.width > 0 && o.r.height > 0);
          // ⚠️ 顺序很重要：**先按文本筛，再优先视口内**。
          //    反过来写会踩坑：池子里只要有一个（不匹配文本的）元素在视口内，
          //    inView 就非空，真正匹配的那个（差 1px 出视口）会被整体丢掉。
          let pool = boxed;
          if (want) {{
            pool = pool.filter(o => {{
              const t = String(o.e.innerText || o.e.value || '').trim();
              return {cmp};
            }});
          }}
          const inView = pool.filter(o =>
            o.r.left >= 0 && o.r.top >= 0 &&
            o.r.right <= window.innerWidth + 1 && o.r.bottom <= window.innerHeight + 1);
          if (inView.length) pool = inView;
          pool = pool.sort((a, b) => area(a.r) - area(b.r));   // 最小的最像叶子节点
          const hit = pool[{index}];
          if (!hit) return null;
          hit.e.scrollIntoView({{block: 'center', inline: 'center'}});
          const r = rectOf(hit.e);
          if (!r.width || !r.height) return null;
          return {{x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
                   tag: hit.e.tagName, cls: String(hit.e.className || '').slice(0, 70),
                   w: Math.round(r.width), h: Math.round(r.height),
                   txt: String(hit.e.innerText || '').trim().slice(0, 40)}};
        }})()
        """
        return self.ev(expr)

    def find_all(self, selector: str, text: str = "", limit: int = 8) -> list[dict]:
        """列出候选元素（面积从小到大），调选择器时看这个。"""
        cmp = f"String(e.innerText||e.value||'').trim().includes({json.dumps(text)})" \
            if text else "true"
        expr = f"""
        (() => {{
          return [...document.querySelectorAll({json.dumps(selector)})]
            .filter(e => {{ const r = e.getBoundingClientRect();
                            return r.width > 0 && r.height > 0; }})
            .filter(e => ({cmp}))
            .map(e => {{ const r = e.getBoundingClientRect();
              return {{tag: e.tagName, cls: String(e.className||'').slice(0,60),
                       w: Math.round(r.width), h: Math.round(r.height),
                       txt: String(e.innerText||e.value||'').trim().slice(0,40)}}; }})
            .sort((a,b) => (a.w*a.h) - (b.w*b.h))
            .slice(0, {limit});
        }})()
        """
        return self.ev(expr) or []

    def click(self, selector: str, index: int = 0, text: str = "",
              exact: bool = False, real: bool = True) -> dict | None:
        """点一下（默认走真实鼠标事件，SPA 才认）。"""
        pt = self._point(selector, index=index, text=text, exact=exact)
        if not pt:
            return None
        if real:
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                self.cmd("Input.dispatchMouseEvent",
                         {"type": typ, "x": pt["x"], "y": pt["y"],
                          "button": "left", "clickCount": 1})
        else:
            self.ev(f"""(() => {{ const els=[...document.querySelectorAll({json.dumps(selector)})];
                       const e=els[{index}]; if(e) e.click(); }})()""")
        return pt

    def click_text(self, text: str, selector: str = "button,div,span,a,li",
                   exact: bool = False) -> dict | None:
        return self.click(selector, text=text, exact=exact)

    def key(self, key: str, code: str, vk: int) -> None:
        for typ in ("rawKeyDown", "char", "keyUp"):
            self.cmd("Input.dispatchKeyEvent",
                     {"type": typ, "key": key, "code": code,
                      "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})

    def insert(self, text: str) -> None:
        self.cmd("Input.insertText", {"text": text})

    def type_lines(self, text: str, enter_key: str = "Enter") -> None:
        """逐行输入：contenteditable 里 insertText 不会自己换段。"""
        lines = text.replace("\r\n", "\n").split("\n")
        for i, line in enumerate(lines):
            if line:
                self.insert(line)
                time.sleep(0.05)
            if i != len(lines) - 1:
                self.key(enter_key, enter_key, 13)
                time.sleep(0.08)

    def set_files(self, paths: list[str], selector: str = "input[type=file]") -> int:
        """把本地图片塞进 file input（DOM.setFileInputFiles，不用点系统选择框）。"""
        res = self.cmd("Runtime.evaluate",
                       {"expression": f"document.querySelector({json.dumps(selector)})",
                        "returnByValue": False})
        obj = res.get("result", {}).get("objectId")
        if not obj:
            return 0
        self.cmd("DOM.enable")
        self.cmd("DOM.setFileInputFiles", {"files": paths, "objectId": obj})
        return len(paths)

    def set_value(self, selector: str, text: str) -> bool:
        """给 React 受控输入框赋值。

        ⚠️ `Input.insertText` 打进去的字 React **不认**（value tracker 没更新，
        下一次 re-render 就把值清空了 —— 实测标题框填完是空的）。
        必须走原生 value setter + 派发 input 事件，React 才会同步 state。
        """
        ok = self.ev(f"""
        (() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return false;
          const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          setter.call(el, {json.dumps(text)});
          el.dispatchEvent(new Event('input', {{ bubbles: true }}));
          el.dispatchEvent(new Event('change', {{ bubbles: true }}));
          return el.value === {json.dumps(text)};
        }})()
        """)
        return bool(ok)

    def shadow_button(self, host: str, class_contains: str) -> dict | None:
        """取 **closed shadow root** 里某个按钮的视口坐标。

        小红书把「暂存离开 / 发布」放在了 `<xhs-publish-btn>` 的封闭 shadow root 里
        （`el.shadowRoot` 是 null，`document.querySelectorAll` 也看不见），所以
        JS 点不到、按文本也搜不到。CDP 的 DOM 域能穿透（`pierce: true`）：
        describeNode 找到按钮的 backendNodeId → getBoxModel 拿四角坐标 → 换回视口坐标。
        """
        res = self.cmd("Runtime.evaluate",
                       {"expression": f"document.querySelector({json.dumps(host)})",
                        "returnByValue": False})
        obj = res.get("result", {}).get("objectId")
        if not obj:
            return None
        node = self.cmd("DOM.describeNode",
                        {"objectId": obj, "depth": -1, "pierce": True}).get("node", {})

        found: list[dict] = []

        def walk(n: dict) -> None:
            for sr in n.get("shadowRoots") or []:
                walk(sr)
            if n.get("nodeName") == "BUTTON" or n.get("localName") == "button":
                attrs = n.get("attributes") or []
                cls = ""
                for i in range(0, len(attrs) - 1, 2):
                    if attrs[i] == "class":
                        cls = attrs[i + 1]
                if class_contains in cls:
                    found.append({"backendNodeId": n.get("backendNodeId"),
                                  "cls": cls, "disabled": "disabled" in " ".join(attrs)})
            for c in n.get("children") or []:
                walk(c)

        walk(node)
        if not found:
            return None
        target = found[-1]                      # 红色主按钮在后面
        model = self.cmd("DOM.getBoxModel",
                         {"backendNodeId": target["backendNodeId"]}).get("model", {})
        quad = model.get("content") or model.get("border") or []
        if len(quad) < 8:
            return None
        # getBoxModel 给的是**页面坐标**，点击要视口坐标
        sx, sy = float(self.ev("window.scrollX") or 0), float(self.ev("window.scrollY") or 0)
        xs, ys = quad[0::2], quad[1::2]
        return {"x": round(sum(xs) / len(xs) - sx),
                "y": round(sum(ys) / len(ys) - sy),
                "cls": target["cls"], "w": round(max(xs) - min(xs))}

    def click_at(self, x: float, y: float, delay: float = 0.05) -> None:
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            p = {"type": typ, "x": x, "y": y, "button": "left", "clickCount": 1,
                 "buttons": 1 if typ == "mousePressed" else 0}
            self.cmd("Input.dispatchMouseEvent", p)
            if typ == "mousePressed":
                time.sleep(delay)

    def dismiss_modal(self) -> bool:
        """关掉「图片可以编辑啦」这类引导弹窗，否则会挡住发布按钮。"""
        hit = js_click_text(self, "我知道了", selector="button,div,span")
        if not hit:
            hit = js_click_text(self, "知道了", selector="button,div,span")
        return bool(hit)

    def shot(self, name: str) -> str:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = str(SHOT_DIR / name)
        metrics = self.cmd("Page.getLayoutMetrics")
        css = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
        w = min(int(css.get("width", 1440)), 1600)
        h = min(int(css.get("height", 1400)), 4000)
        res = self.cmd("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": 0, "y": 0, "width": w, "height": h, "scale": 1},
            "captureBeyondViewport": True,
        }, timeout=60)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))
        print(f"  截图 → {path}")
        return path

    def cookies(self) -> list[dict]:
        res = self.cmd("Network.getCookies",
                       {"urls": ["https://creator.xiaohongshu.com/",
                                 "https://www.xiaohongshu.com/",
                                 "https://edith.xiaohongshu.com/"]})
        return res.get("cookies", []) or []

    def logged_in(self) -> tuple[bool, list[str]]:
        names = sorted({c["name"] for c in self.cookies()})
        # 创作者中心的登录态：web_session；有的账号还有 access-token-creator…
        key = any(n in ("web_session", "access-token-creator.xiaohongshu.com")
                  for n in names)
        return key, names

    def dump(self) -> dict:
        expr = """
        (() => {
          const vis = e => { const r = e.getBoundingClientRect();
                             return r.width > 0 && r.height > 0; };
          const pick = (sel, f) => [...document.querySelectorAll(sel)].filter(vis).map(f);
          return JSON.stringify({
            url: location.href,
            title: document.title,
            inputs: pick('input', e => ({type: e.type, ph: e.placeholder || '',
              name: e.name, cls: String(e.className||'').slice(0,60),
              accept: e.accept || '', multi: e.multiple})),
            textareas: pick('textarea', e => ({ph: e.placeholder || '',
              cls: String(e.className||'').slice(0,60)})),
            editables: pick('[contenteditable]', e => ({cls: String(e.className||'').slice(0,80),
              ce: e.getAttribute('contenteditable'),
              txt: (e.innerText||'').trim().slice(0,60)})),
            buttons: pick('button,[role=button],[class*=btn]', e =>
              ({txt: (e.innerText||'').trim().slice(0,30),
                cls: String(e.className||'').slice(0,70)})).filter(b => b.txt),
            tabs: pick('[class*=tab],[class*=Tab]', e => (e.innerText||'').trim().slice(0, 24))
                  .filter(Boolean)
          }, null, 1);
        })()
        """
        return json.loads(self.ev(expr) or "{}")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #

def cmd_launch(args) -> int:
    if wait_port(PORT, timeout=1.5):
        print(f"端口 {PORT} 已有浏览器在跑（复用），不重复启动。")
        return 0
    exe = find_browser()
    PROFILE.mkdir(parents=True, exist_ok=True)
    cargs = [
        exe,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", "--disable-background-networking", "--disable-sync",
        "--window-size=1440,1000", "--window-position=60,40",
        CREATOR_HOME,
    ]
    proc = subprocess.Popen(cargs, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"浏览器已启动（pid={proc.pid}，可见窗口）")
    print(f"  profile: {PROFILE}")
    print(f"  CDP    : http://127.0.0.1:{PORT}")
    if not wait_port(PORT, timeout=30):
        print("!! CDP 没就绪，检查是否被安全软件拦了", file=sys.stderr)
        return 1
    print("CDP 已就绪。下一步：python scripts/xhs_publish.py login")
    return 0


def cmd_status(args) -> int:
    pages = [t for t in list_targets(PORT) if t.get("type") == "page"]
    print(f"浏览器里共 {len(pages)} 个标签页：")
    for t in pages[:15]:
        mark = "  ← 自动化会连这个" if "creator.xiaohongshu.com" in (t.get("url") or "") else ""
        print(f"  - {(t.get('url') or '')[:78]}{mark}")
    s = Session()
    ok, names = s.logged_in()
    print(f"URL    : {s.url()}")
    print(f"标题   : {s.title()}")
    print(f"已登录 : {ok}")
    print(f"cookie : {', '.join(names) if names else '(无)'}")
    s.close()
    return 0 if ok else 2


def cmd_login(args) -> int:
    s = Session()
    ok, _ = s.logged_in()
    if ok:
        print("已经是登录状态，不用扫码。")
        _dump_cookies(s)
        return 0
    s.nav(PUBLISH_URL, wait=3)
    if LOGIN_HINT not in s.url():
        # 有些情况是弹窗登录
        s.click_text("登录", exact=False)
        time.sleep(1)
    print("=" * 66)
    print("请在弹出的浏览器窗口里扫码登录小红书（用手机 App 扫）")
    print("登录成功后本脚本会自动继续 —— 我会一直轮询，最长等 "
          f"{args.timeout} 秒。")
    print("=" * 66)
    deadline = time.time() + args.timeout
    last = ""
    while time.time() < deadline:
        ok, names = s.logged_in()
        cur = s.url()
        if ok:
            print(f"\n登录成功 ✔  当前：{cur}")
            _dump_cookies(s)
            return 0
        msg = f"  等待扫码… {int(deadline - time.time())}s  当前：{cur[:70]}"
        if msg != last:
            print(msg, flush=True)
            last = msg
        time.sleep(4)
    print("\n超时仍未登录。可以再跑一次 login 继续等。", file=sys.stderr)
    return 1


def _dump_cookies(s: Session) -> None:
    ck = s.cookies()
    BASE.mkdir(parents=True, exist_ok=True)
    with open(COOKIES, "w", encoding="utf-8") as fh:
        json.dump(ck, fh, ensure_ascii=False, indent=1)
    names = ", ".join(sorted({c["name"] for c in ck}))
    print(f"cookie 已导出 {len(ck)} 条 → {COOKIES}")
    print(f"  字段名（值不打印）：{names}")


def cmd_dump(args) -> int:
    s = Session()
    if args.nav:
        s.nav(args.nav, wait=args.wait)
    elif args.publish:
        s.nav(PUBLISH_URL, wait=args.wait)
    for text in (args.click or []):
        cands = s.find_all("div,span,button,li,p,a", text=text, limit=4)
        hit = s.click_text(text, selector="div,span,button,li,p,a")
        print(f"点击「{text}」→ {hit}")
        if args.verbose:
            print("  候选（面积小→大）：" +
                  json.dumps(cands, ensure_ascii=False))
        time.sleep(args.click_wait)
    info = s.dump()
    print(json.dumps(info, ensure_ascii=False, indent=1))
    if args.shot:
        s.shot(args.shot)
    s.close()
    return 0


# --------------------------------------------------------------------------- #
# 文案解析：从 promo/小红书文案.md 里取「主推」的标题与正文
# --------------------------------------------------------------------------- #

def parse_copy(md_path: Path) -> tuple[str, str]:
    text = md_path.read_text(encoding="utf-8")
    def block_after(heading: str) -> str:
        m = re.search(re.escape(heading) + r"(.{0,400}?)```(?:\w+)?\n(.*?)```", text, re.S)
        if not m:
            raise SystemExit(f"在 {md_path} 里找不到 {heading} 后面的代码块")
        return m.group(2).strip()
    title = block_after("### 标题（≤ 20 字，直接复制）")
    body = block_after("### 正文（直接复制")
    return title, body


def wait_until(s: Session, expr: str, timeout: float = 90.0, label: str = "",
               interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if s.ev(expr):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    if label:
        print(f"  !! 等待超时：{label}")
    return False


def js_click_text(s: Session, text: str, selector: str = ".creator-tab,button,div,span") -> dict | None:
    """用 DOM `click()` 点（**不是**合成鼠标事件）。

    实测：小红书发布页的「上传图文」标签在页面里挂了不止一份，真实鼠标事件会落到
    一个装饰性副本上（点了没反应），只有对真实节点派发 click 才会切换。
    这是浏览器内部的正常点击，不是什么反检测手段。
    """
    expr = f"""
    (() => {{
      const want = {json.dumps(text)};
      const cands = [...document.querySelectorAll({json.dumps(selector)})]
        .filter(e => String(e.innerText || '').trim().includes(want));
      // 取面积最小的那个（最靠近叶子节点）
      cands.sort((a, b) => {{
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        return (ra.width * ra.height) - (rb.width * rb.height);
      }});
      const hit = cands[0];
      if (!hit) return null;
      hit.click();
      const r = hit.getBoundingClientRect();
      return {{tag: hit.tagName, cls: String(hit.className || '').slice(0, 60),
               txt: String(hit.innerText || '').trim().slice(0, 30),
               x: Math.round(r.left), y: Math.round(r.top)}};
    }})()
    """
    return s.ev(expr)


def cmd_post(args) -> int:
    root = Path(__file__).resolve().parent.parent
    md = root / "promo" / "小红书文案.md"
    title, body = (args.title, args.body)
    if not title or not body:
        t, b = parse_copy(md)
        title = title or t
        body = body or b
    images = args.images or [
        str(root / "promo" / "screenshots" / n) for n in [
            "03b-poster-detail-3x4.png",
            "01-list-desktop-3x4.png",
            "02-month-calendar-3x4.png",
            "03-idol-detail-3x4.png",
        ]
    ]
    missing = [p for p in images if not Path(p).exists()]
    if missing:
        print("!! 找不到图片：" + ", ".join(missing), file=sys.stderr)
        return 1

    print(f"标题（{len(title)} 字）：{title}")
    print(f"正文（{len(body)} 字，{body.count(chr(10)) + 1} 行）")
    print(f"图片 {len(images)} 张：" + ", ".join(Path(p).name for p in images))

    s = Session()
    ok, _ = s.logged_in()
    if not ok:
        print("!! 未登录，先跑：python scripts/xhs_publish.py login", file=sys.stderr)
        return 2

    s.nav(PUBLISH_URL, wait=5)

    # 1) 切到「上传图文」
    hit = js_click_text(s, "上传图文", selector=".creator-tab")
    print(f"点「上传图文」→ {hit}")
    if not wait_until(s, "!!document.querySelector('input[type=file][accept*=\"jpg\"]')",
                      timeout=15, label="图片上传输入框出现"):
        print("!! 没切到图文模式，下面是当前 DOM：", file=sys.stderr)
        print(json.dumps(s.dump(), ensure_ascii=False, indent=1))
        s.shot("err-no-image-input.png")
        s.close()
        return 3
    s.shot("01-image-tab.png")

    # 2) 传图
    sel = 'input[type=file][accept*="jpg"]'
    n = s.set_files(images, selector=sel)
    if not n:
        print("!! file input 没吃到文件", file=sys.stderr)
        s.close()
        return 4
    print(f"已投喂 {n} 张图，等编辑器（图片处理完才会出现标题/正文）…")

    # 3) 等编辑器：出现标题输入框 + 正文编辑区
    ok_title = wait_until(
        s, "!!document.querySelector('input[placeholder*=\"标题\"], "
           "input[placeholder*=\"填写标题\"]')", timeout=args.upload_wait,
        label="标题输入框出现")
    # 上传完会弹「图片可以编辑啦」引导框，先关掉（它会挡住底部按钮）
    if s.dismiss_modal():
        print("已关掉引导弹窗")
        time.sleep(1)
    s.shot("02-editor.png")
    if not ok_title:
        print("!! 编辑器没出来。当前 DOM：", file=sys.stderr)
        print(json.dumps(s.dump(), ensure_ascii=False, indent=1))
        s.close()
        return 5

    # 4) 标题（React 受控输入：必须用原生 setter，insertText 会被 re-render 清掉）
    tsel = "input[placeholder*='标题'], input[placeholder*='填写标题']"
    s.click(tsel)
    if s.set_value(tsel, title) and s.ev(
            f"document.querySelector({json.dumps(tsel)}).value === {json.dumps(title)}"):
        print(f"标题已填（{len(title)} 字）")
    else:
        print("!! 标题没写进去，试试逐字键盘输入", file=sys.stderr)
        s.click(tsel)
        for ch in title:
            s.cmd("Input.dispatchKeyEvent",
                  {"type": "keyDown", "text": ch, "unmodifiedText": ch})
            s.cmd("Input.dispatchKeyEvent", {"type": "keyUp"})
        print("  标题现值：" + str(s.ev(
            f"document.querySelector({json.dumps(tsel)}).value")))

    # 5) 正文
    ed = s.click("[contenteditable='true'], [contenteditable=''], .ql-editor")
    if ed:
        s.type_lines(body)
        got = int(s.ev("(() => { const e=document.querySelector('.tiptap,[contenteditable=true]');"
                       "return e ? e.innerText.replace(/\\s/g,'').length : 0; })()") or 0)
        want = len(re.sub(r"\s", "", body))
        print(f"正文已输入（{ed['tag']}.{ed['cls'][:30]}）：编辑区 {got} 字 / 期望 {want} 字"
              + ("" if abs(got - want) <= 6 else "  ← ⚠️ 对不上，检查是不是被截断/吞字"))
    else:
        print("!! 没找到正文编辑区", file=sys.stderr)
        print(json.dumps(s.dump(), ensure_ascii=False, indent=1))

    time.sleep(1.5)
    s.key("Escape", "Escape", 27)      # 关掉 #话题 联想框，别挡发布按钮
    time.sleep(0.8)
    s.shot("03-filled.png")

    # 6) 发布按钮在封闭 shadow root 里，只能靠 CDP 穿透拿坐标
    btn = s.shadow_button("xhs-publish-btn", "ce-btn")
    print(f"发布/暂存按钮：{btn}")
    state = s.ev("""(() => { const h=document.querySelector('xhs-publish-btn');
      if (!h) return null;
      const g = k => h.getAttribute(k);
      return JSON.stringify({submit: g('submit-text'), save: g('save-text'),
        submit_disabled: g('submit-disabled'), submit_loading: g('submit-loading'),
        save_disabled: g('save-disabled'), save_loading: g('save-loading')}); })()""")
    print(f"宿主元素状态：{state}")

    if not args.publish and not args.draft:
        print("\n--dry-run：标题/正文/图片都填好了，**没有点发布**。")
        print("  看 promo/xhs-run/03-filled.png 确认排版，再加 --publish 真发。")
        s.close()
        return 0

    # 草稿箱计数（存草稿后应该 +1）
    draft_before = s.ev("(() => { const m=document.body.innerText.match(/草稿箱\\s*\\(?(\\d+)\\)?/);"
                        "return m ? m[1] : null; })()")

    if args.draft:
        if '"save_disabled": "true"' in str(state):
            print("!! 「暂存离开」是 disabled，存不了草稿", file=sys.stderr)
            s.close()
            return 8
        white = s.shadow_button("xhs-publish-btn", "ce-btn white")
        if not white:
            print("!! 没定位到「暂存离开」按钮", file=sys.stderr)
            s.close()
            return 9
        print(f"点击「暂存离开」@ ({white['x']}, {white['y']})")
        s.click_at(white["x"], white["y"])
        time.sleep(8)
        s.shot("04-draft-saved.png")
        draft_after = s.ev("(() => { const m=document.body.innerText.match(/草稿箱\\s*\\(?(\\d+)\\)?/);"
                           "return m ? m[1] : null; })()")
        toast = s.ev("(() => { const t=document.querySelector('[class*=toast],[class*=Toast],"
                     "[class*=message],[class*=Message]');"
                     "return t ? t.innerText.trim().slice(0, 60) : null; })()")
        print(f"草稿箱计数：{draft_before} → {draft_after}"
              + ("  ✔" if draft_after and draft_after != draft_before else "  （没变，看截图）"))
        print(f"页面提示：{toast}")
        print(f"当前落点：{s.url()}")
        s.close()
        return 0

    if '"submit_disabled": "true"' in str(state):
        print("!! 发布按钮是 disabled 状态，先看页面提示（通常是字数/图片/标题不合规）",
              file=sys.stderr)
        s.close()
        return 6

    # 红色主按钮 = 最后一个 ce-btn（.ce-btn.bg-red）
    red = s.shadow_button("xhs-publish-btn", "bg-red")
    if not red:
        print("!! 没定位到红色发布按钮", file=sys.stderr)
        s.close()
        return 7
    print(f"点击发布按钮 @ ({red['x']}, {red['y']})")
    s.click_at(red["x"], red["y"])
    time.sleep(10)
    s.shot("04-after-publish.png")
    print(f"发布后落点：{s.url()}")
    print("页面提示片段：" + str(s.ev(
        "document.body.innerText.replace(/\\s+/g,' ').slice(0, 200)")))
    s.close()
    return 0


def cmd_drafts(args) -> int:
    """看一眼草稿箱里有什么（存完草稿后核对用）。"""
    s = Session()
    s.nav(PUBLISH_URL, wait=4)
    hit = js_click_text(s, "草稿箱", selector="div,span,button,a")
    print(f"点「草稿箱」→ {hit}")
    time.sleep(3.5)
    cands = s.find_all("div,span,button,a,li", text="图文笔记", limit=5)
    print("「图文笔记」候选：" + json.dumps(cands, ensure_ascii=False))
    got = s.click("div,span,button,a,li", text="图文笔记")
    print(f"点「图文笔记」→ {got}")
    time.sleep(3.5)
    info = s.ev("""
    (() => {
      const t = document.body.innerText.replace(/\\n{2,}/g, '\\n');
      const m = t.match(/草稿箱[\\s\\S]{0,700}/);
      return m ? m[0] : t.slice(0, 700);
    })()
    """)
    print(str(info)[:900])
    s.shot("05-draft-box.png")
    s.close()
    return 0


def cmd_close(args) -> int:
    try:
        s = Session()
        s.cmd("Browser.close")
    except Exception as exc:  # noqa: BLE001
        print(f"关闭时出错（可能已经关了）：{exc}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="小红书发帖助手（自有账号 + 自己的浏览器）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("launch", help="起一个可见的 Edge（持久 profile，端口 9222）")
    sub.add_parser("status", help="看当前页面与登录态")

    p = sub.add_parser("login", help="打开登录页等你扫码")
    p.add_argument("--timeout", type=int, default=300)

    p = sub.add_parser("dump", help="打印发布页可交互元素")
    p.add_argument("--nav", default="", help="先导航到这个地址")
    p.add_argument("--publish", action="store_true", help="导航到发布页")
    p.add_argument("--click", action="append", default=[], help="按文本点一下（可重复）")
    p.add_argument("--click-wait", type=float, default=2.5)
    p.add_argument("--wait", type=float, default=5.0)
    p.add_argument("--verbose", action="store_true", help="打印候选元素")
    p.add_argument("--shot", default="", help="顺便截图到这个文件名")

    p = sub.add_parser("post", help="填标题/正文/传图（默认不点发布）")
    p.add_argument("--title", default="")
    p.add_argument("--body", default="")
    p.add_argument("--images", nargs="*", default=None)
    p.add_argument("--upload-wait", type=float, default=12.0)
    p.add_argument("--publish", action="store_true", help="真的点发布")
    p.add_argument("--draft", action="store_true", help="只点「暂存离开」存草稿")
    p.add_argument("--dry-run", action="store_true", help="（默认行为）只填不发")

    sub.add_parser("drafts", help="看草稿箱内容（核对存草稿结果）")
    sub.add_parser("close", help="关掉这个浏览器")

    args = ap.parse_args()
    fn = {
        "launch": cmd_launch, "status": cmd_status, "login": cmd_login,
        "dump": cmd_dump, "post": cmd_post, "drafts": cmd_drafts,
        "close": cmd_close,
    }[args.cmd]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
