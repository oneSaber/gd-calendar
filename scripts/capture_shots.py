#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为推广文案批量截取界面演示图
============================
复用 `cdp_fetch.Browser`（纯标准库 CDP，无 Playwright 依赖），把本项目的
前端界面按「小红书 3:4 / 抖音 9:16 / 桌面 16:10」三种画幅截下来。

用法：
  python scripts/capture_shots.py                     # 默认 http://127.0.0.1:8000
  python scripts/capture_shots.py --base http://127.0.0.1:8010
  python scripts/capture_shots.py --out promo/screenshots
  python scripts/capture_shots.py --only 03           # 只重拍某几个镜头

前置：服务已启动（`启动.bat` 或 `python -m app.cli serve`），且库里已有真实数据。
画幅与用途：
  1440x1920（3:4）  → 小红书图文
  430x932 @DPR2（9:16）→ 抖音竖版
  1440x1080（16:10）→ 备用/技术类截图
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp_fetch import Browser  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# 页面操作小工具
# --------------------------------------------------------------------------- #

def js(br: Browser, expr: str):
    res = br.cmd("Runtime.evaluate",
                 {"expression": expr, "returnByValue": True, "awaitPromise": True})
    return res.get("result", {}).get("value")


def goto(br: Browser, url: str, wait: float = 2.5) -> None:
    """导航 + 等 DOM 完成 + 等前端把数据渲染出来（列表有卡片才算好）。"""
    br.cmd("Page.navigate", {"url": url})
    deadline = time.time() + 25
    while time.time() < deadline:
        val = str(js(br, "document.readyState") or "")
        if val == "complete":
            break
        time.sleep(0.3)
    # 等列表/详情渲染出内容：最多再等 8 秒
    deadline = time.time() + 8
    while time.time() < deadline:
        n = js(br, "(document.querySelectorAll('.card,.cell').length)")
        if n:
            break
        time.sleep(0.4)
    time.sleep(wait)


def select_first_card(br: Browser) -> bool:
    """点开第一张演出卡（JS click 走事件委托；失败再退回真实鼠标点击）。"""
    js(br, "(() => { const c = document.querySelector('[data-occ-id]');"
           "if (c) { c.scrollIntoView({block:'center'}); c.click(); } })()")
    time.sleep(1.2)
    if _detail_len(br) > 60:
        return True
    # 兜底：真实鼠标事件
    pt = js(br, """
    (() => { const e = document.querySelector('[data-occ-id]');
      if (!e) return null;
      e.scrollIntoView({block: 'center'});
      const r = e.getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()
    """)
    if pt:
        for typ in ("mousePressed", "mouseReleased"):
            br.cmd("Input.dispatchMouseEvent",
                   {"type": typ, "x": pt["x"], "y": pt["y"], "button": "left", "clickCount": 1})
        time.sleep(1.2)
    return _detail_len(br) > 60


def _detail_len(br: Browser) -> int:
    return int(js(br, "(document.getElementById('detail-panel')||{}).innerText"
                      " ? document.getElementById('detail-panel').innerText.length : 0") or 0)


def select_busy_day(br: Browser) -> str:
    """在月历里点一个有演出的日子（否则侧栏是「这一天没有收录的演出」空态）。"""
    day = js(br, """
    (() => {
      const c = [...document.querySelectorAll('[data-day]')]
        .find(e => (e.getAttribute('aria-label') || '').includes('共') && e.offsetParent);
      if (!c) return '';
      c.click();
      return c.dataset.day;
    })()
    """)
    time.sleep(1.2)
    return str(day or "")


def scroll_to(br: Browser, selector: str) -> None:
    js(br, f"(() => {{ const e = document.querySelector('{selector}');"
           f"if (e) e.scrollIntoView({{block:'start'}}); }})()")
    time.sleep(0.8)


def element_page_y(br: Browser, selector: str, pad: int = 24) -> int | None:
    """元素在**页面绝对坐标**里的 y（截图 clip 用的就是这套坐标）。"""
    y = js(br, f"""
    (() => {{ const e = document.querySelector('{selector}');
      if (!e) return null;
      return Math.max(0, Math.round(e.getBoundingClientRect().top + window.scrollY) - {pad}); }})()
    """)
    return int(y) if y is not None else None


def setup_viewport(br: Browser, width: int, height: int, mobile: bool = False,
                   scale: int = 1) -> None:
    br.cmd("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": scale, "mobile": mobile,
    })
    time.sleep(0.4)


def shoot(br: Browser, path: str, width: int, height: int, full: bool = False,
          y: int = 0) -> str:
    """截固定画幅的图；full=True 时按页面实际高度截（上限 8000）。

    y 是**页面绝对坐标**：顶栏是 sticky 的，页面滚动后截图会把顶栏糊进画面，
    所以截局部时先 scrollTo(0,0) 再用绝对 y。
    """
    if full:
        metrics = br.cmd("Page.getLayoutMetrics")
        css = metrics.get("contentSize") or metrics.get("cssContentSize") or {}
        height = min(int(css.get("height", height)), 8000)
        width = min(int(css.get("width", width)), 2400)
    res = br.cmd("Page.captureScreenshot", {
        "format": "png",
        "clip": {"x": 0, "y": y, "width": width, "height": height, "scale": 1},
        "captureBeyondViewport": True,
    }, timeout=90)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(res["data"]))
    print(f"  OK  {os.path.basename(path):34s} {width}x{height}  "
          f"{os.path.getsize(path)/1024:6.0f} KB")
    return path


# --------------------------------------------------------------------------- #
# 镜头表
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="promo/screenshots")
    ap.add_argument("--only", default="", help="只跑文件名含该子串的镜头，逗号分隔")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    out = args.out
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    made: list[str] = []

    def want(name: str) -> bool:
        # 按**完整镜头号**匹配（"03" 不应命中 "03b"）
        return (not only) or name in only

    br = Browser(headless=True, width=1440, height=1920)
    detail_ok: list[str] = []
    try:
        br.cmd("Page.enable")
        br.cmd("Runtime.enable")

        # 1. 桌面 · 本月列表（3:4）
        if want("01"):
            setup_viewport(br, 1440, 1920)
            goto(br, f"{base}/?range=month")
            made.append(shoot(br, f"{out}/01-list-desktop-3x4.png", 1440, 1920))

        # 2. 桌面 · 月历视图 + 点开有演出的一天（3:4）
        if want("02"):
            setup_viewport(br, 1440, 1920)
            goto(br, f"{base}/?view=calendar&range=month")
            day = select_busy_day(br)
            print(f"      点开的日子：{day or '(没找到有演出的日子)'}")
            made.append(shoot(br, f"{out}/02-month-calendar-3x4.png", 1440, 1920))

        # 3. 桌面 · 只看地偶 + 详情（3:4）
        if want("03"):
            setup_viewport(br, 1440, 1920)
            goto(br, f"{base}/?is_idol=true&range=month")
            detail_ok.append(f"03={select_first_card(br)}")
            made.append(shoot(br, f"{out}/03-idol-detail-3x4.png", 1440, 1920))

        # 3b. 桌面 · 带海报的 ACG 场次详情（3:4）
        if want("03b"):
            setup_viewport(br, 1440, 1920)
            goto(br, f"{base}/?q=BanG+Dream&range=month")
            detail_ok.append(f"03b={select_first_card(br)}")
            time.sleep(1.2)   # 等海报代理取图
            made.append(shoot(br, f"{out}/03b-poster-detail-3x4.png", 1440, 1920))

        # 4. 桌面 · 全页长图（列表）
        if want("04"):
            setup_viewport(br, 1440, 1200)
            goto(br, f"{base}/?range=month")
            made.append(shoot(br, f"{out}/04-list-desktop-full.png", 1440, 1200, full=True))

        # 5. 桌面 · 全页长图（月历）
        if want("05"):
            setup_viewport(br, 1440, 1200)
            goto(br, f"{base}/?view=calendar&range=month")
            made.append(shoot(br, f"{out}/05-month-full.png", 1440, 1200, full=True))

        # 6. 移动端 · 列表（9:16）
        if want("06"):
            setup_viewport(br, 430, 932, mobile=True, scale=2)
            goto(br, f"{base}/?range=month")
            made.append(shoot(br, f"{out}/06-mobile-list-9x16.png", 430, 932))

        # 7. 移动端 · 只看地偶 + 详情（9:16）
        if want("07"):
            setup_viewport(br, 430, 932, mobile=True, scale=2)
            goto(br, f"{base}/?is_idol=true&range=month")
            detail_ok.append(f"07={select_first_card(br)}")
            js(br, "window.scrollTo(0, 0)")
            time.sleep(0.5)
            y = element_page_y(br, "#detail-panel", pad=24) or 0
            made.append(shoot(br, f"{out}/07-mobile-detail-9x16.png", 430, 932, y=y))

        # 8. 移动端 · 月历（9:16）
        if want("08"):
            setup_viewport(br, 430, 932, mobile=True, scale=2)
            goto(br, f"{base}/?view=calendar&range=month")
            select_busy_day(br)
            js(br, "window.scrollTo(0, 0)")
            time.sleep(0.5)
            made.append(shoot(br, f"{out}/08-mobile-month-9x16.png", 430, 932))

        # 9. 接口文档（技术向）
        if want("09"):
            setup_viewport(br, 1440, 1080)
            goto(br, f"{base}/docs")
            made.append(shoot(br, f"{out}/09-api-docs.png", 1440, 1080))

        # 10. 「订阅这个日历」——点击后 toast 给出 .ics 订阅链接
        if want("10"):
            setup_viewport(br, 1440, 1080)
            goto(br, f"{base}/?range=month")
            # 无头浏览器没有剪贴板权限，真实浏览器里这一步是成功的
            # （localhost 属安全上下文 + 真实点击手势）；这里让 toast 走成功分支。
            js(br, "(() => { try { Object.defineProperty(navigator, 'clipboard',"
                   " { value: { writeText: () => Promise.resolve() }, configurable: true }); }"
                   " catch (e) {} const b = document.getElementById('ics-btn'); if (b) b.click(); })()")
            time.sleep(0.7)
            made.append(shoot(br, f"{out}/10-ics-subscribe-toast.png", 1440, 1080))

        # 11. 「数据来源与免责声明」弹窗（合规口径）
        if want("11"):
            setup_viewport(br, 1440, 1080)
            goto(br, f"{base}/?range=month")
            js(br, "(() => { const b = document.getElementById('about-btn'); if (b) b.click(); })()")
            time.sleep(0.8)
            made.append(shoot(br, f"{out}/11-about-sources.png", 1440, 1080))

    finally:
        br.close()

    print(f"\n共 {len(made)} 张 → {out}")
    if detail_ok:
        print("详情面板是否打开：" + ", ".join(detail_ok))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
