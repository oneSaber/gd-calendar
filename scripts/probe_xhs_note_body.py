"""最后一次攻克小红书笔记正文：试多种打开方式。

搜索页只给标题，演出时间/地点/阵容都在**正文**里。前面的尝试：
  * 直接 nav() 到 /explore/<id>  → 404
  * 在搜索页 click() 卡片        → 弹层没打开

本次系统试完并打印诊断，成功就固化，失败就明确记为不可行（不再反复试）。
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.xhs import XhsBrowser, SEARCH_URL  # noqa: E402

# 「广州地王广场也有地偶看？！」—— 这条的正文是地王广场的关键证据
NOTE_ID = "6821ed8c000000002301df16"
NOTE_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"

READ = r"""
(function(){
  var t = document.body.innerText || '';
  var dialogs = document.querySelectorAll('[role=dialog],.note-detail-mask,#noteContainer');
  return JSON.stringify({
    url: location.href,
    title: document.title,
    len: t.length,
    dialogs: dialogs.length,
    head: t.slice(0, 500)
  });
})()
"""


def show(br: XhsBrowser, label: str) -> dict:
    try:
        d = json.loads(br.ev(READ))
    except Exception as exc:  # noqa: BLE001
        print(f"    [{label}] 读取失败: {exc}")
        return {}
    print(f"    [{label}] url={str(d.get('url'))[:80]}")
    print(f"        长度={d.get('len')} 弹层={d.get('dialogs')}")
    print(f"        开头={str(d.get('head'))[:200].replace(chr(10),' | ')}")
    return d


def main() -> None:
    br = XhsBrowser()
    try:
        print("  方式 A：登录态直接开笔记页")
        br.nav(NOTE_URL, wait=10.0)
        time.sleep(3)
        show(br, "A")

        print()
        print("  方式 B：先搜到该笔记，再点真实 DOM 链接（带坐标点击）")
        br.nav(SEARCH_URL.format(kw="地王广场 地偶"), wait=9.0)
        time.sleep(3)
        found = br.ev(
            "(function(){var a=document.querySelector('a[href*=\"%s\"]');"
            "return a ? 'found' : 'missing';})()" % NOTE_ID
        )
        print(f"    搜索页里找该笔记: {found}")
        if found == "found":
            # 用坐标点击（比 element.click() 更接近真实操作）
            box = br.ev(
                "(function(){var a=document.querySelector('a[href*=\"%s\"]');"
                "if(!a) return '';a.scrollIntoView({block:'center'});"
                "var r=a.getBoundingClientRect();"
                "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()" % NOTE_ID
            )
            print(f"    卡片坐标: {box}")
            try:
                pt = json.loads(box)
                br.click_at(pt["x"], pt["y"])
                time.sleep(5)
                show(br, "B")
            except Exception as exc:  # noqa: BLE001
                print(f"    坐标点击失败: {exc}")

        print()
        print("  方式 C：任意点开第一个卡片（不指定 id），看弹层是否出现")
        br.nav(SEARCH_URL.format(kw="广州地偶"), wait=9.0)
        time.sleep(3)
        br.ev("(function(){var a=document.querySelector('a[href*=\"/explore/\"]');"
              "if(a){a.scrollIntoView({block:'center'});a.click();}})()")
        time.sleep(5)
        show(br, "C")
    finally:
        br.close()


main()
