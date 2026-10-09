"""精确检查目标笔记的轮播：**真实图数** + 每张图的尺寸/内容指纹。

## 为什么需要

1. 之前抓到的 6 张里混进了**别的笔记**（Devi Lapin 解散公告）→ 推荐流污染
2. 缺 #35–#43，需要确认那一段到底在不在这个笔记里

做法：打开笔记 → 读轮播指示器（`1/6` 这类）→ **逐张切换**并记录当前主图，
这样能精确知道每张是什么，不再靠一次性的整页扫描。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, r"D:\AI\gd-calendar")

PORT = 9222
OUT = Path(r"D:\AI\gd-calendar\data\xhs_note53_exact")

# 读轮播状态：指示器文本 + 当前主图
_READ_STATE = r"""
(function(){
  var root = document.querySelector('#noteContainer')
          || document.querySelector('[role=dialog]')
          || document.body;
  // 指示器（"1/6"）
  var ind = '';
  var m = (root.innerText || '').match(/\b\d{1,2}\s*\/\s*\d{1,2}\b/);
  if(m) ind = m[0];
  // 当前可视的主图：取容器内最靠上、最大的图
  var best = null;
  [].slice.call(root.querySelectorAll('img')).forEach(function(img){
    var src = img.getAttribute('src') || '';
    if(src.indexOf('xhscdn') < 0) return;
    if(/avatar/i.test(src)) return;
    var r = img.getBoundingClientRect();
    if(r.width < 300 || r.height < 300) return;
    var area = r.width * r.height;
    if(!best || area > best.area){
      best = {area: area, src: src, w: Math.round(r.width), h: Math.round(r.height),
              top: Math.round(r.top)};
    }
  });
  return JSON.stringify({indicator: ind, main: best,
                         url: location.href, title: document.title});
})()
"""

# 点下一张（XHS 轮播右箭头；多种选择器）
_NEXT = r"""
(function(){
  var sels = ['.arrow-right','.swiper-button-next','[class*=arrow-right]',
              '[class*=swiper-button-next]','button[aria-label*=next]'];
  for(var i=0;i<sels.length;i++){
    var list = document.querySelectorAll(sels[i]);
    for(var j=0;j<list.length;j++){
      var e = list[j], r = e.getBoundingClientRect();
      if(r.width > 0 && r.height > 0){ e.click(); return 'clicked:' + sels[i]; }
    }
  }
  return 'no-next';
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
    notes = json.loads(
        Path(r"D:\AI\gd-calendar\data\xhs_notes.json").read_text(encoding="utf-8")
    )["items"]
    target = next(
        (n for n in notes if "53团" in (n.get("title") or "") and n.get("xsec_token")),
        None,
    )
    if target is None:
        print("  ✗ 找不到目标笔记")
        return

    from app.collectors.xhs import XhsNote, note_url

    url = note_url(XhsNote(
        note_id=target["note_id"], title=target["title"],
        xsec_token=target["xsec_token"],
    ))
    open_tab(url)
    time.sleep(12)

    from xhs_publish import Session  # type: ignore

    s = Session(port=PORT, prefer="xiaohongshu.com")
    seen: list[dict] = []
    try:
        for step in range(10):
            st = json.loads(s.ev(_READ_STATE) or "{}")
            main = st.get("main") or {}
            key = str(main.get("src") or "").split("?")[0]
            print(f"  [{step}] 指示器={st.get('indicator')!r} "
                  f"主图={main.get('w')}x{main.get('h')}")
            if key and not any(x["key"] == key for x in seen):
                seen.append({"key": key, "src": main.get("src"),
                             "w": main.get("w"), "h": main.get("h")})
            r = s.ev(_NEXT)
            if r == "no-next":
                print(f"    （找不到下一张：{r}）")
                break
            time.sleep(2.5)

        print()
        print(f"  轮播里出现过的**不同**主图：{len(seen)} 张")
        for i, x in enumerate(seen, 1):
            print(f"    [{i}] {x['w']}x{x['h']}  {str(x['key'])[-58:]}")

        # 下载
        OUT.mkdir(parents=True, exist_ok=True)
        for old in OUT.glob("*"):
            old.unlink()
        for i, x in enumerate(seen, 1):
            u = x["src"]
            if u.startswith("//"):
                u = "https:" + u
            try:
                req = urllib.request.Request(u, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.xiaohongshu.com/",
                })
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                (OUT / f"n{i:02d}_{x['w']}x{x['h']}.webp").write_bytes(data)
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ [{i}] {str(exc)[:50]}")
        print(f"  已保存到 {OUT}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
