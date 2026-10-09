"""验证：带 `xsec_token` 的完整笔记 URL 能否打开正文。

## 背景（重要：推翻之前的结论）

之前我判定「小红书笔记正文不可自动读取（防抓取）」，依据是
直接导航 `/explore/<id>` 得到 404。但实测发现搜索页里的真实链接是：

    /search_result/<id>?xsec_token=AB-...&xsec_source=pc_search

**note_id 是对的，缺的是 `xsec_token`** —— XHS 现在要求笔记 URL 带这个
一次性令牌，不带就 404。所以之前的结论是**错的**（不是防抓取，是参数缺失）。

本脚本验证：带 token 打开 → 正文能否读到。
"""

from __future__ import annotations

import json
import re
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

# 取「笔记详情」弹层/页面的正文。XHS 详情页正文在 #detail-desc 或
# 带 desc 的元素里；命中不了就退回整页 innerText。
READ_DETAIL = r"""
(function(){
  var t = document.body.innerText || '';
  var desc = document.querySelector('#detail-desc')
          || document.querySelector('[class*=desc]')
          || document.querySelector('[class*=note-content]');
  return JSON.stringify({
    url: location.href,
    title: document.title,
    len: t.length,
    descLen: desc ? desc.innerText.length : 0,
    desc: desc ? desc.innerText.slice(0, 1200) : '',
    head: t.slice(0, 400)
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
        # 抓带 token 的笔记链接
        hrefs = json.loads(s.ev("""
        (function(){
          var out=[];
          [].slice.call(document.querySelectorAll('a[href]')).forEach(function(a){
            var h=a.getAttribute('href')||'';
            if(/^\/search_result\/[0-9a-f]{16,}/.test(h)) out.push(h);
          });
          var m={},u=[]; out.forEach(function(x){if(!m[x]){m[x]=1;u.push(x);}});
          return JSON.stringify(u.slice(0,4));
        })()
        """) or "[]")
        print(f"  带 token 的笔记链接 {len(hrefs)} 个")
        if not hrefs:
            print("  ✗ 没抓到带 token 的链接")
            return
        for h in hrefs:
            print(f"    {h[:110]}")

        target = "https://www.xiaohongshu.com" + hrefs[0]
        print()
        print(f"  导航到: {target[:110]}")
        s.nav(target, wait=12)
        time.sleep(3)

        d = json.loads(s.ev(READ_DETAIL) or "{}")
        print(f"    URL: {str(d.get('url'))[:100]}")
        print(f"    标题: {str(d.get('title'))[:56]}")
        print(f"    整页长度={d.get('len')}  正文元素长度={d.get('descLen')}")
        if d.get("desc"):
            print(f"    正文: {str(d['desc'])[:560].replace(chr(10), ' | ')}")
        else:
            print(f"    开头: {str(d.get('head'))[:280].replace(chr(10), ' | ')}")

        # 结论判定
        print()
        if d.get("descLen", 0) > 50:
            print("  ✅ 带 token 可以读到正文 —— 之前的「防抓取」结论是错的")
        elif d.get("len", 0) > 1500 and "404" not in str(d.get("url")):
            print("  ⚠️ 页面打开了但正文元素没命中，需要调选择器")
        else:
            print("  ❌ 仍打不开")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
