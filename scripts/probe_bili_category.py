"""实测：B站会员购的分类筛选（only同人展 / 展览 / 演出）能否扩大覆盖。

背景：默认「全部」列表广州只有 total=17 条，「下一页」点不到是因为确实只有一页。
所以扩量要靠**分类维度**（页面上有 全部/展览/演出/赛事/本地生活/only同人展）。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

SHOW_HOME = "https://show.bilibili.com/platform/home.html?msource=pc_web"
XHR_MATCH = "/api/ticket/project"
CITY = "广州"


def collect(br, hits, seconds: float) -> None:
    """在 seconds 秒内收集拦截到的 listV2 响应（带 page 信息）。"""
    end = time.time() + seconds
    while time.time() < end:
        time.sleep(0.3)


def main() -> None:
    br = Browser(headless=True, width=1500, height=1700)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        br.render(SHOW_HOME, wait=10)
        time.sleep(4)

        def click_text(text: str) -> str:
            return br.cmd("Runtime.evaluate", {"expression": f"""
            (function(){{
              var nodes = [].slice.call(document.querySelectorAll('a,li,span,div,button'));
              var n = nodes.find(function(e){{
                return (e.innerText || '').trim() === {json.dumps(text)};
              }});
              if(!n) return 'not-found';
              n.click();
              return 'clicked';
            }})()
            """, "returnByValue": True}).get("result", {}).get("value", "?")

        print("  点广州:", click_text("广州"))
        time.sleep(6)

        for cat in ("全部", "only同人展", "展览", "演出"):
            r = click_text(cat)
            time.sleep(6)
            txt = br.cmd("Runtime.evaluate", {"expression":
                "document.body.innerText.replace(/\\n+/g,' | ').slice(0, 700)",
                "returnByValue": True})["result"]["value"]
            # 数出正文里形如「广州·xxx」的条目
            n_items = txt.count("广州·") + txt.count("广州 ·")
            print(f"  分类「{cat}」 {r:9} 正文条目数≈{n_items}")
            print(f"      {txt[:230]}")
    finally:
        br.close()


main()
