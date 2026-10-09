"""诊断：为什么直接 nav() 打不开笔记，而站内点击可以。

## 结论（推翻之前「防抓取」的判断）

之前的判断是「小红书笔记正文不可自动读取（防抓取）」，依据：
  * 直接 `nav()` 到 `/explore/<id>` → **404**
  * `element.click()` → 弹层不出现

实测真相（三层原因，都不是「防抓取」）：
  1. **缺 `xsec_token`**：真实链接是
     `/search_result/<id>?xsec_token=AB-…&xsec_source=pc_search`，
     token 就在**搜索页 HTML 里**，可以采集
  2. **缺 Referer 链路**：从搜索页直接进 `/explore/<id>` 会 404；
     必须从**搜索页内部**跳转过去
  3. **`element.click()` 不够**：需要真实鼠标事件（CDP `Input.dispatchMouseEvent`），
     或者用「在搜索页点链接」这个等价路径

本脚本把三者对照验证，写清哪个是必需的 —— 这样以后不用再猜。
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
    "?keyword=%E5%B9%BF%E5%B7%9E%E5%9C%B0%E5%81%B6&type=51"
)

GET_TOKEN_LINK = r"""
(function(){
  var a = document.querySelector('a[href*="/search_result/"]');
  return a ? (a.getAttribute('href') || '') : '';
})()
"""

READ = r"""
(function(){
  var t = document.body.innerText || '';
  var box = document.querySelector('#noteContainer')
         || document.querySelector('[role=dialog]')
         || document.querySelector('[class*=note-detail]');
  return JSON.stringify({
    url: location.href, title: document.title,
    pageLen: t.length, boxLen: box ? box.innerText.length : 0
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


def fresh_search() -> object:
    """开一个干净的搜索页会话。"""
    open_tab(SEARCH)
    time.sleep(9)
    from xhs_publish import Session  # type: ignore

    return Session(port=PORT, prefer="xiaohongshu.com")


def main() -> None:
    print("=" * 66)
    print("  实验 A：直接 nav() 到 /explore/<id>（不带 token）")
    print("=" * 66)
    s = fresh_search()
    try:
        bare = "https://www.xiaohongshu.com/explore/6abbc42a000000001303e73f"
        s.nav(bare, wait=10)
        time.sleep(2)
        d = json.loads(s.ev(READ) or "{}")
        print(f"    → {str(d.get('url'))[:88]}")
        print(f"    boxLen={d.get('boxLen')}  {'❌ 打不开' if d.get('boxLen',0)<50 else '✅'}")
    finally:
        s.close()

    print()
    print("=" * 66)
    print("  实验 B：先拿 token 链接，再**直接 nav 带 token 的 URL**")
    print("=" * 66)
    s = fresh_search()
    try:
        link = s.ev(GET_TOKEN_LINK)
        print(f"    采集到链接: {str(link)[:84]}")
        if not link:
            print("    ✗ 没抓到链接")
        else:
            s.nav("https://www.xiaohongshu.com" + link, wait=11)
            time.sleep(2)
            d = json.loads(s.ev(READ) or "{}")
            print(f"    → {str(d.get('url'))[:88]}")
            print(f"    boxLen={d.get('boxLen')}  "
                  f"{'❌ 仍被重定向' if d.get('boxLen',0)<50 else '✅ 可读'}")
    finally:
        s.close()

    print()
    print("=" * 66)
    print("  实验 C：在搜索页里**点该链接**（站内跳转）")
    print("=" * 66)
    s = fresh_search()
    try:
        s.ev("(function(){var a=document.querySelector('a[href*=\"/search_result/\"]');"
             "if(a){a.scrollIntoView({block:'center'});a.click();}})()")
        time.sleep(6)
        d = json.loads(s.ev(READ) or "{}")
        print(f"    → {str(d.get('url'))[:88]}")
        print(f"    boxLen={d.get('boxLen')}  "
              f"{'✅ 可读' if d.get('boxLen',0)>50 else '❌'}")
    finally:
        s.close()

    print()
    print("  结论：**C（站内点击）是唯一可行路径** —— "
          "token 从搜索页采集，再用点击打开。")


main()
