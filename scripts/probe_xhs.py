"""探测小红书搜索是否可无登录访问（决定它能不能当数据源）。

背景：项目早期记录「小红书登录墙，已放弃」，但维护者希望加入。
必须实测确认，而不是沿用旧结论。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar\scripts")
from cdp_fetch import Browser  # noqa: E402

READ = r"""
(function(){
  var txt = document.body.innerText || '';
  return JSON.stringify({
    url: location.href,
    title: document.title,
    len: txt.length,
    text: txt.replace(/\n+/g, ' | ').slice(0, 400),
    hasLogin: /登录|扫码|手机号|验证/.test(txt),
    notes: document.querySelectorAll('section.note-item, a[href*="/explore/"]').length
  });
})()
"""


def probe(br: Browser, label: str, url: str, wait: float = 9.0) -> dict:
    br.render(url, wait)
    time.sleep(3)
    d = json.loads(br.cmd("Runtime.evaluate", {"expression": READ,
                                               "returnByValue": True})["result"]["value"])
    print(f"  【{label}】")
    print(f"    URL: {d['url'][:100]}")
    print(f"    title={d['title'][:50]}  文本长度={d['len']}  笔记元素={d['notes']}")
    print(f"    含登录字样={d['hasLogin']}")
    print(f"    内容: {d['text'][:220]}")
    print()
    return d


def main() -> None:
    br = Browser(headless=True, width=1440, height=1600)
    try:
        br.cmd("Network.enable")
        br.cmd("Runtime.enable")
        # 1) 首页
        probe(br, "首页", "https://www.xiaohongshu.com/explore")
        # 2) 关键词搜索
        probe(br, "搜索 地偶", "https://www.xiaohongshu.com/search_result?keyword=%E5%9C%B0%E5%81%B6")
        # 3) 搜索 ACG 乐队
        probe(br, "搜索 地王广场",
              "https://www.xiaohongshu.com/search_result?keyword=%E5%9C%B0%E7%8E%8B%E5%B9%BF%E5%9C%BA")
    finally:
        br.close()


main()
