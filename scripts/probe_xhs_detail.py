"""探测：小红书笔记详情页能否读取正文（决定采集深度）。

搜索页只给标题 + 作者；演出时间/地点/阵容在**正文**里。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.xhs import XhsBrowser  # noqa: E402

# 三条已知含场地/排期线索的笔记
NOTES = [
    ("广州地王广场也有地偶看？！", "https://www.xiaohongshu.com/explore/6821ed8c000000002301df16"),
    ("地王地偶重开，但是好热啊！", "https://www.xiaohongshu.com/explore/6aabe274000000002600b652"),
    ("广州地下偶像常去场馆统计", "https://www.xiaohongshu.com/explore/6a47ac6f000000001603da0d"),
]

READ_DETAIL = r"""
(function(){
  var t = document.body.innerText || '';
  return JSON.stringify({
    url: location.href,
    title: document.title,
    len: t.length,
    text: t.slice(0, 1500)
  });
})()
"""


def main() -> None:
    br = XhsBrowser()
    try:
        for label, url in NOTES:
            br.nav(url, wait=9.0)
            time.sleep(3)
            raw = br.ev(READ_DETAIL)
            d = json.loads(raw) if isinstance(raw, str) else {}
            print(f"  【{label}】")
            print(f"    URL: {str(d.get('url'))[:90]}")
            print(f"    长度: {d.get('len')}")
            print(f"    正文: {str(d.get('text'))[:520].replace(chr(10), ' | ')}")
            print()
    finally:
        br.close()


main()
