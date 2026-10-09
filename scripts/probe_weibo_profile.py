"""探测：能否读微博账号主页正文（决定能不能解析排期/阵容）。

背景：`app/collectors/weibo.py` 的注释说「个人主页强制跳登录」——
但我们现在有**持久 profile 的登录态**，所以重新实测这个结论是否还成立。

可读 → 就能做「按账号采集」，拿到图鉴作者的完整排期贴。
不可读 → 只能继续走搜索页，靠关键词命中。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

# Garry_Liu_（广州地偶图鉴作者）与地王广场相关账号
TARGETS = [
    ("Garry_Liu_（图鉴作者）", "https://m.weibo.cn/u/5861861144"),
    ("神启Apotheosis", "https://m.weibo.cn/u/9185021276"),
]

READ = r"""
(function(){
  var t = document.body.innerText || '';
  return JSON.stringify({
    url: location.href,
    title: document.title,
    len: t.length,
    hasLogin: /登录|扫码|手机号/.test(t),
    text: t.slice(0, 900)
  });
})()
"""


def main() -> None:
    br = Browser(headless=True, width=430, height=900, mobile=True)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        for label, url in TARGETS:
            br.render(url, wait=10)
            time.sleep(3)
            d = json.loads(br.cmd("Runtime.evaluate", {
                "expression": READ, "returnByValue": True
            })["result"]["value"])
            print(f"  【{label}】")
            print(f"    URL: {str(d['url'])[:96]}")
            print(f"    长度: {d['len']}  含登录字样: {d['hasLogin']}")
            print(f"    正文: {str(d['text'])[:420].replace(chr(10), ' | ')}")
            print()
    finally:
        br.close()


main()
