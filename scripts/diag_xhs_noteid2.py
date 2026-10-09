"""诊断：note_id 是否提取正确（用持久 profile，先开小红书标签页再接管）。

⚠️ 三个踩过的坑：
  1. 持久 profile 启动后停在 `creator.xiaohongshu.com/new/home`，
     那页持续发请求 → `Page.navigate` 超时。**必须另开标签页**。
     → `Session(prefer="xiaohongshu")` 可以只接管小红书那页。
  2. `cdp_fetch.Browser` 用临时 profile（无登录态），查不了登录后页面。
  3. `WS.send()` 收的是**字符串**，传 dict 会 `AttributeError`。

结论用途：若真实 href 的 id 与我采集的不一致，则 404 是**ID 提取错**
而非防抓取 —— 那会改写「小红书正文不可读」的结论。
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


def open_tab(url: str) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=12):
        pass


def main() -> None:
    open_tab(SEARCH)
    print("  已新开小红书搜索标签页，等待加载…")
    time.sleep(10)

    from xhs_publish import Session  # type: ignore

    try:
        s = Session(port=PORT, prefer="xiaohongshu.com")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 接管失败：{exc}")
        return

    try:
        print(f"  当前: {s.url()[:92]}")
        print(f"  标题: {s.title()[:50]}")

        hrefs = json.loads(s.ev("""
        (function(){
          var out=[];
          [].slice.call(document.querySelectorAll('a[href]')).forEach(function(a){
            var h=a.getAttribute('href')||'';
            if(/explore|search_result|note/.test(h)) out.push(h);
          });
          var m={},u=[]; out.forEach(function(x){if(!m[x]){m[x]=1;u.push(x);}});
          return JSON.stringify(u.slice(0,12));
        })()
        """) or "[]")
        print()
        print("  搜索页真实 href（前 12 个）:")
        for h in hrefs:
            print(f"    {h[:104]}")

        # 与我采集到的 note_id 对照
        try:
            notes = json.loads(
                open(r"D:\AI\gd-calendar\data\xhs_notes.json", encoding="utf-8")
            )["items"]
            mine = {n["note_id"] for n in notes[:40] if n.get("note_id")}
        except Exception:  # noqa: BLE001
            mine = set()
        on_page = {
            h.rsplit("/", 1)[-1].split("?")[0] for h in hrefs
        }
        print()
        print(f"  我采集的 id 样本（40 条里）: {list(mine)[:3]}")
        print(f"  页面 href 里的 id        : {list(on_page)[:3]}")
        print(f"  有交集: {len(mine & on_page)} 个")

        if hrefs:
            tgt = hrefs[0]
            full = (tgt if tgt.startswith("http")
                    else "https://www.xiaohongshu.com" + tgt)
            print()
            print(f"  用真实 href 导航: {full[:100]}")
            s.nav(full, wait=12)
            time.sleep(3)
            d = json.loads(s.ev(
                "JSON.stringify({url:location.href,title:document.title,"
                "len:document.body.innerText.length,"
                "head:document.body.innerText.slice(0,320)})"
            ) or "{}")
            print(f"    → {str(d.get('url'))[:100]}")
            print(f"    长度={d.get('len')} 标题={str(d.get('title'))[:46]}")
            print(f"    开头: {str(d.get('head'))[:280].replace(chr(10),' | ')}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
