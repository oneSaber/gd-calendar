"""直接从 Swiper 的 slide 里取全部图片（不用点箭头）。

## 关键发现

`scripts/xhs_carousel_dump.py` 的输出显示：弹层里是 **Swiper**，
`.swiper-slide` **全部同时在 DOM 里**（6 张 + 2 张 duplicate）。

所以根本不需要模拟点「下一张」—— 一次性把所有 slide 的图取出来即可。
之前 `no-next` 失败是因为箭头是 `div.arrow-controller.right`（不在我猜的
选择器列表里），但既然 slide 都在，点不点箭头都无关紧要。

⚠️ 同时要**排除 duplicate**：Swiper 的循环模式会克隆首/尾 slide，
不排除会重复计数（实测 8 个 slide = 6 张真图 + 2 张克隆）。
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
OUT = Path(r"D:\AI\gd-calendar\data\xhs_note53_slides")

# 取所有 slide 的图，排除 duplicate
_EXTRACT_SLIDES = r"""
(function(){
  var root = document.querySelector('#noteContainer')
          || document.querySelector('[role=dialog]')
          || document.body;
  var slides = [].slice.call(root.querySelectorAll('.swiper-slide'));
  var out = [];
  var seen = {};
  slides.forEach(function(sl, idx){
    var dup = /swiper-slide-duplicate/.test(String(sl.className || ''));
    var img = sl.querySelector('img');
    var src = img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '';
    if(!src) return;
    var key = src.split('?')[0];
    out.push({idx: idx, dup: dup, src: src, key: key});
  });
  // 去重：同一个 key 只留第一个非 duplicate
  var byKey = {}, order = [];
  out.forEach(function(x){
    if(!(x.key in byKey)){ byKey[x.key] = x; order.push(x.key); }
    else if(byKey[x.key].dup && !x.dup){ byKey[x.key] = x; }
  });
  return JSON.stringify({
    totalSlides: slides.length,
    uniq: order.map(function(k){ return byKey[k]; })
  });
})()
"""

# 兜底：如果 slide 结构取不到，用 img 的父级 .note-slider-img 反查
_EXTRACT_BY_CLASS = r"""
(function(){
  var root = document.querySelector('#noteContainer')
          || document.querySelector('[role=dialog]')
          || document.body;
  var out = [], seen = {};
  [].slice.call(root.querySelectorAll('.note-slider-img img, .swiper-slide img'))
    .forEach(function(img){
      var src = img.getAttribute('src') || img.getAttribute('data-src') || '';
      if(!src || src.indexOf('xhscdn') < 0) return;
      var k = src.split('?')[0];
      if(seen[k]) return;
      seen[k] = 1;
      out.push({src: src, key: k});
    });
  return JSON.stringify({count: out.length, items: out});
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
    from app.collectors.xhs import XhsNote, note_url

    open_tab(note_url(XhsNote(
        note_id=target["note_id"], title=target["title"],
        xsec_token=target["xsec_token"],
    )))
    time.sleep(12)

    from xhs_publish import Session  # type: ignore

    s = Session(port=PORT, prefer="xiaohongshu.com")
    try:
        d = json.loads(s.ev(_EXTRACT_SLIDES) or "{}")
        uniq = d.get("uniq") or []
        print(f"  slide 总数 {d.get('totalSlides')}，非重复 {len(uniq)} 张")
        for i, x in enumerate(uniq, 1):
            print(f"    [{i}] dup={int(x['dup'])} idx={x['idx']}  …{x['key'][-52:]}")

        if not uniq:
            d2 = json.loads(s.ev(_EXTRACT_BY_CLASS) or "{}")
            uniq = d2.get("items") or []
            print(f"  兜底路径：{len(uniq)} 张")

        OUT.mkdir(parents=True, exist_ok=True)
        for old in OUT.glob("*"):
            old.unlink()
        saved = 0
        for i, x in enumerate(uniq, 1):
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
                (OUT / f"s{i:02d}.webp").write_bytes(data)
                saved += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ [{i}] {str(exc)[:50]}")
        print(f"  已保存 {saved} 张到 {OUT}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
