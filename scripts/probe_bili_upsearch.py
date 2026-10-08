"""探测：能否用知识库里的团体名，在 B站搜到它们的官方账号（用于动态通道）。

背景：B站会员购广东只有 ~17 条/城，扩量空间有限；但地偶团体的**官方 B站 账号**
会发演出预告（含阵容），这才是 B站作为主力源的价值所在。

合规：只用公开搜索页，不逆向 wbi 签名、不绕登录。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

GROUPS = ["DigitalDuel", "恋时青空", "月匙Moon-Key", "Yours", "娜娜捏口俱乐部"]

SEARCH_URL = "https://search.bilibili.com/upuser?keyword={kw}"

READ_USERS = r"""
(function(){
  var out = [];
  [].slice.call(document.querySelectorAll('a[href*="space.bilibili.com"]')).forEach(function(a){
    var m = /space\.bilibili\.com\/(\d+)/.exec(a.getAttribute('href') || '');
    if(!m) return;
    var txt = (a.innerText || '').trim();
    if(txt) out.push({mid: m[1], text: txt.slice(0, 60)});
  });
  var seen = {}, uniq = [];
  out.forEach(function(x){ if(!seen[x.mid]){ seen[x.mid]=1; uniq.push(x); } });
  return JSON.stringify({url: location.href, users: uniq.slice(0, 6),
                         body: document.body.innerText.replace(/\n+/g,' | ').slice(0,220)});
})()
"""


def main() -> None:
    br = Browser(headless=True, width=1500, height=1400)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        for name in GROUPS[:3]:
            url = SEARCH_URL.format(kw=name)
            br.render(url, wait=9)
            time.sleep(2.5)
            raw = br.cmd("Runtime.evaluate", {"expression": READ_USERS,
                                              "returnByValue": True})["result"]["value"]
            d = json.loads(raw)
            print(f"  【{name}】")
            print(f"    URL: {d['url'][:100]}")
            if d["users"]:
                for u in d["users"]:
                    print(f"    mid={u['mid']}  {u['text']}")
            else:
                print(f"    未提取到账号；正文: {d['body'][:150]}")
            print()
    finally:
        br.close()


main()
