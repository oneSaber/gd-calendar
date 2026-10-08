#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
页面交互 + 响应拦截（CDP Input domain）
=====================================
验证：对需要「点一下才会加载数据」的页面，能否用「真实点击 + XHR 拦截」拿到数据。
这比拼接接口参数更稳（参数改了也不怕），也是 SPA 类站点的通用打法。

目标：B 站会员购首页切到「广州」，抓取它自己请求的列表接口 JSON。

用法：
  python cdp_interact.py
"""

from __future__ import annotations

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

TARGET = "https://show.bilibili.com/platform/home.html?msource=pc_web"
MATCH = "/api/ticket/project"


def click_by_text(br: Browser, text: str, tag: str = "*") -> bool:
    """在页面里找到文本等于 text 的可点元素并点击（返回是否点到）。

    用 elementFromPoint + 真实鼠标事件，而不是 el.click()，
    因为部分框架只响应真实事件。
    """
    expr = f"""
    (() => {{
      const want = {json.dumps(text)};
      const els = [...document.querySelectorAll({json.dumps(tag)})];
      const hit = els.find(e => (e.innerText || '').trim() === want
                             && e.offsetParent !== null);
      if (!hit) return null;
      const r = hit.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return null;
      return {{x: r.left + r.width/2, y: r.top + r.height/2, tag: hit.tagName,
               cls: (hit.className||'').toString().slice(0,60)}};
    }})()
    """
    res = br.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    pt = res.get("result", {}).get("value")
    if not pt:
        return False
    for typ in ("mousePressed", "mouseReleased"):
        br.cmd("Input.dispatchMouseEvent", {
            "type": typ, "x": pt["x"], "y": pt["y"],
            "button": "left", "clickCount": 1,
        })
    print(f"  点击「{text}」→ {pt['tag']}.{pt['cls']} @ ({pt['x']:.0f},{pt['y']:.0f})")
    return True


def main() -> int:
    br = Browser(headless=True, width=1440, height=1400)
    hits: list[dict] = []
    try:
        br.cmd("Page.enable")
        br.cmd("Runtime.enable")
        br.cmd("Network.enable")

        print("=" * 78)
        print(f"导航到 {TARGET}")
        br.cmd("Page.navigate", {"url": TARGET})
        time.sleep(8)

        print("\n--- 页面上的城市选项 ---")
        res = br.cmd("Runtime.evaluate", {
            "expression": "JSON.stringify([...document.querySelectorAll('*')]"
                          ".filter(e=>e.children.length===0&&(e.innerText||'').trim().length<=4)"
                          ".map(e=>(e.innerText||'').trim()).filter(t=>t&&/[\\u4e00-\\u9fa5]{2}/.test(t)).slice(0,40))",
            "returnByValue": True,
        })
        try:
            cities = json.loads(res["result"]["value"])
            print("  " + " / ".join(cities[:30]))
        except Exception:  # noqa: BLE001
            cities = []

        print("\n--- 尝试点击「广州」并拦截列表接口 ---")
        clicked = click_by_text(br, "广州")
        if not clicked:
            print("  未找到可点的「广州」，改用 JS 直接触发")
            br.cmd("Runtime.evaluate", {
                "expression": "(()=>{const e=[...document.querySelectorAll('*')]"
                              ".find(x=>(x.innerText||'').trim()==='广州');"
                              "if(e){e.click();return true}return false})()",
                "returnByValue": True,
            })

        # 收集 20 秒内的响应
        deadline = time.time() + 20
        seen: set[str] = set()
        while time.time() < deadline:
            try:
                msg = json.loads(br.ws.recv())  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                break
            if msg.get("method") != "Network.responseReceived":
                continue
            p = msg.get("params", {})
            rurl = p.get("response", {}).get("url", "")
            rid = p.get("requestId")
            if MATCH in rurl and rid not in seen:
                seen.add(rid)
                try:
                    body = br.cmd("Network.getResponseBody", {"requestId": rid})
                    txt = body.get("body", "")
                    hits.append({"url": rurl, "len": len(txt), "body": txt})
                    print(f"  ✓ 拦截 {rurl[:110]} ({len(txt)} 字节)")
                except Exception as exc:  # noqa: BLE001
                    print(f"  ✗ {rurl[:80]}: {exc}")

        print("\n--- 点击后页面上的广州条目 ---")
        res = br.cmd("Runtime.evaluate", {
            "expression": "JSON.stringify((document.body.innerText||'').split('\\n')"
                          ".filter(l=>l.includes('广州')).slice(0,12))",
            "returnByValue": True,
        })
        try:
            for line in json.loads(res["result"]["value"]):
                print("  " + line.strip()[:100])
        except Exception:  # noqa: BLE001
            pass

    finally:
        br.close()

    print(f"\n共拦截 {len(hits)} 个列表接口响应")
    for h in hits:
        try:
            j = json.loads(h["body"])
            res_ = (j.get("data") or {}).get("result") or j.get("result") or []
            print(f"  {h['url'][:80]}")
            print(f"    errno={j.get('errno')} 条目={len(res_)}")
            for it in res_[:5]:
                print(f"      - {it.get('project_name','?')[:44]} | "
                      f"{it.get('venue_name','?')[:20]} | {it.get('price_low')}-{it.get('price_high')}")
        except Exception:  # noqa: BLE001
            print(f"  {h['url'][:80]}  非 JSON")

    if hits:
        with open("bili_show_xhr.json", "w", encoding="utf-8") as fh:
            json.dump(hits, fh, ensure_ascii=False, indent=2)
        print("\n已写入 bili_show_xhr.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
