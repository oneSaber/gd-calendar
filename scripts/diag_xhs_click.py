"""验证：用搜索页里抓到的**带 token 链接**，以「站内点击」方式打开笔记。

直接 `nav()` 到带 token 的 URL 会被重定向到 `/explore`（实测），
所以换成**在搜索页里点该链接**（等价于站内跳转）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")

PORT = 9222
SEARCH = (
    "https://www.xiaohongshu.com/search_result"
    "?keyword=%E5%B9%BF%E5%B7%9E%E5%9C%B0%E5%81%B6%E5%9B%BE%E9%89%B4&type=51"
)

# 用法：在搜索页里找到含 token 的链接 → 用真实 DOM 点击 → 读弹层
CLICK_AND_READ = r"""
(function(){
  var a = document.querySelector('a[href*="/search_result/"]');
  if(!a) return JSON.stringify({step:'no-link'});
  var href = a.getAttribute('href') || '';
  a.scrollIntoView({block:'center'});
  a.click();
  return JSON.stringify({step:'clicked', href: href.slice(0, 90)});
})()
"""

READ_AFTER = r"""
(function(){
  var t = document.body.innerText || '';
  var box = document.querySelector('[role=dialog]')
         || document.querySelector('#noteContainer')
         || document.querySelector('.note-detail-mask')
         || document.querySelector('[class*=note-detail]');
  return JSON.stringify({
    url: location.href,
    title: document.title,
    pageLen: t.length,
    boxLen: box ? box.innerText.length : 0,
    boxText: box ? box.innerText.slice(0, 1500) : '',
    head: t.slice(0, 300)
  });
})()
"""


def open_tab(url: str) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=12):
        pass


def main() -> None:
    open_tab(SEARCH)
    time.sleep(10)

    from xhs_publish import Session  # type: ignore

    s = Session(port=PORT, prefer="xiaohongshu.com")
    try:
        print(f"  起始页: {s.url()[:88]}")
        r = json.loads(s.ev(CLICK_AND_READ) or "{}")
        print(f"  点击结果: {r}")
        time.sleep(6)

        d = json.loads(s.ev(READ_AFTER) or "{}")
        print(f"    URL: {str(d.get('url'))[:100]}")
        print(f"    标题: {str(d.get('title'))[:52]}")
        print(f"    整页={d.get('pageLen')}  弹层/详情={d.get('boxLen')}")
        if d.get("boxText"):
            print(f"    正文: {str(d['boxText'])[:600].replace(chr(10), ' | ')}")
        else:
            print(f"    开头: {str(d.get('head'))[:280].replace(chr(10), ' | ')}")

        print()
        if d.get("boxLen", 0) > 80:
            print("  ✅ 站内点击可读正文")
        elif "/search_result/" in str(d.get("url")):
            print("  ⚠️ 停在搜索页 —— 说明点击被拦（可能需要真实手势）")
        else:
            print("  ❌ 未读到正文")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
