"""浏览器验证新增的 UI：日期选择 + 艺人搜索（截图 + 交互断言）。"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

BASE = "http://127.0.0.1:8000"
OUT = r"D:\AI\gd-calendar\data\shots"

READ = r"""
(function(){
  var d = document.getElementById('date-from');
  var t = document.getElementById('date-to');
  var a = document.getElementById('artist-q');
  return JSON.stringify({
    dateFrom: !!d, dateTo: !!t, dateClear: !!document.getElementById('date-clear'),
    fromVal: d ? d.value : null, toVal: t ? t.value : null,
    artistInput: !!a, artistList: !!document.getElementById('artist-list'),
    cards: document.querySelectorAll('[data-occ-id]').length,
    url: location.search
  });
})()
"""


def main() -> None:
    br = Browser(headless=True, width=1500, height=1150)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        br.render(f"{BASE}/?city=&from=2026-10-01&to=2026-10-31", wait=9)
        time.sleep(3)

        d = json.loads(br.cmd("Runtime.evaluate", {
            "expression": READ, "returnByValue": True
        })["result"]["value"])
        print(f"  日期控件: from={d['dateFrom']} to={d['dateTo']} 清空={d['dateClear']}")
        print(f"    回填值: {d['fromVal']} ~ {d['toVal']}")
        print(f"  艺人控件: input={d['artistInput']} 下拉={d['artistList']}")
        print(f"  卡片={d['cards']}  URL={d['url'][:60]}")

        # 交互 1：改日期
        br.cmd("Runtime.evaluate", {"expression": r"""
        (function(){
          var t = document.getElementById('date-to');
          var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
          setter.call(t, '2026-11-30');
          t.dispatchEvent(new Event('change', {bubbles: true}));
          return t.value;
        })()
        """, "returnByValue": True})
        time.sleep(3)
        d2 = json.loads(br.cmd("Runtime.evaluate", {
            "expression": READ, "returnByValue": True
        })["result"]["value"])
        print()
        print(f"  改结束日为 2026-11-30 → URL={d2['url'][:70]}  卡片={d2['cards']}")

        # 交互 2：艺人搜索
        br.cmd("Runtime.evaluate", {"expression": r"""
        (function(){
          var a = document.getElementById('artist-q');
          var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
          setter.call(a, '恋音');
          a.dispatchEvent(new Event('input', {bubbles: true}));
          return a.value;
        })()
        """, "returnByValue": True})
        time.sleep(2.5)
        d3 = json.loads(br.cmd("Runtime.evaluate", {"expression": r"""
        (function(){
          var l = document.getElementById('artist-list');
          var opts = l ? [].slice.call(l.querySelectorAll('[data-artist]'))
                          .map(function(li){return li.dataset.artist}) : [];
          return JSON.stringify({
            hidden: l ? l.hidden : null, opts: opts,
            cards: document.querySelectorAll('[data-occ-id]').length,
            url: location.search });
        })()
        """, "returnByValue": True})["result"]["value"])
        print()
        print(f"  搜「恋音」→ 下拉 hidden={d3['hidden']} 选项={d3['opts']}")
        print(f"    URL={d3['url'][:80]}  卡片={d3['cards']}")

        # 截图
        import pathlib
        pathlib.Path(OUT).mkdir(parents=True, exist_ok=True)
        p = br.cmd("Page.captureScreenshot", {"format": "png"})
        import base64
        (pathlib.Path(OUT) / "search-ui.png").write_bytes(
            base64.b64decode(p["data"])
        )
        print()
        print(f"  截图: {OUT}\\search-ui.png")
    finally:
        br.close()


main()
