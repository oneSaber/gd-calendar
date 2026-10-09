"""诊断：我提取的 note_id 到底对不对（404 是不是因为 ID 错）。

前面的结论是「点击打不开、导航 404」，但如果**ID 本身就提取错了**，
那 404 是必然的 —— 这比「防抓取」更可能是真因。
先在搜索页里把卡片链接的**原始 href** 打出来核对。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.xhs import XhsBrowser, SEARCH_URL  # noqa: E402

# 把我采集到的某个 note_id 与搜索页里的真实 href 对照
KNOWN_ID = "6abbc42a000000001303e73f"   # 广州国庆偶活速览

DUMP_HREFS = r"""
(function(){
  var out = [];
  [].slice.call(document.querySelectorAll('a[href]')).forEach(function(a){
    var h = a.getAttribute('href') || '';
    if(/explore|search_result|note/.test(h)) out.push(h);
  });
  var seen = {}, uniq = [];
  out.forEach(function(x){ if(!seen[x]){ seen[x]=1; uniq.push(x); } });
  return JSON.stringify(uniq.slice(0, 14));
})()
"""

READ_ANY = r"""
(function(){
  var t = document.body.innerText || '';
  return JSON.stringify({url: location.href, title: document.title,
                         len: t.length, head: t.slice(0,260)});
})()
"""


def main() -> None:
    br = XhsBrowser()
    try:
        # 1) 搜索页里真实的 href 长什么样
        br.nav(SEARCH_URL.format(kw="广州地偶图鉴"), wait=9.0)
        time.sleep(3)
        hrefs = json.loads(br.ev(DUMP_HREFS))
        print("  搜索页真实 href（前 14 个）:")
        for h in hrefs:
            print(f"    {h[:96]}")

        print()
        print(f"  我采集到的 note_id 是否出现在页面里: "
              f"{'是' if any(KNOWN_ID in h for h in hrefs) else '否'}")

        # 2) 直接用真实 href 打开（而不是拼 /explore/<id>）
        if hrefs:
            target = hrefs[0]
            full = target if target.startswith("http") else (
                "https://www.xiaohongshu.com" + target
            )
            print()
            print(f"  用真实 href 打开: {full[:96]}")
            br.nav(full, wait=10.0)
            time.sleep(3)
            d = json.loads(br.ev(READ_ANY))
            print(f"    → {d['url'][:96]}")
            print(f"    长度={d['len']} 标题={d['title'][:50]}")
            print(f"    开头: {d['head'][:220].replace(chr(10), ' | ')}")
    finally:
        br.close()


main()
