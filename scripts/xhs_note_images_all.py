"""滚动笔记轮播，把所有图片都加载出来（XHS 懒加载，首屏只有 3-4 张）。

笔记标称 `1/6`（6 张图），但首次提取只拿到 3 张表格页 ——
其余在轮播后面，需要**逐张切换**触发加载。
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
OUT_DIR = Path(r"D:\AI\gd-calendar\data\xhs_note53")

# 每次切换后收集容器内所有图片
_COLLECT = r"""
(function(){
  var root = document.querySelector('#noteContainer')
          || document.querySelector('[role=dialog]')
          || document.body;
  var out = [];
  [].slice.call(root.querySelectorAll('img')).forEach(function(img){
    var src = img.getAttribute('src') || img.getAttribute('data-src') || '';
    if(src.indexOf('xhscdn') < 0) return;
    if(/avatar/i.test(src)) return;
    if(img.closest('[class*=avatar]')) return;
    var w = img.naturalWidth || 0, h = img.naturalHeight || 0;
    if(w < 500) return;
    out.push({url: src, w: w, h: h});
  });
  return JSON.stringify(out);
})()
"""

# 点「下一张」：XHS 轮播的右箭头
_NEXT = r"""
(function(){
  var cands = [].slice.call(document.querySelectorAll(
    '.arrow-right,.swiper-button-next,.next,[class*=arrow-right],[class*=next]'
  ));
  for(var i=0;i<cands.length;i++){
    var e = cands[i];
    var r = e.getBoundingClientRect();
    if(r.width > 0 && r.height > 0){ e.click(); return 'clicked:' + (e.className||'').slice(0,30); }
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
    found: dict[str, dict] = {}
    try:
        for step in range(8):
            imgs = json.loads(s.ev(_COLLECT) or "[]")
            for im in imgs:
                k = im["url"].split("?")[0]
                found.setdefault(k, im)
            print(f"  第 {step} 次收集：本步 {len(imgs)} 张，累计 {len(found)} 张")
            r = s.ev(_NEXT)
            if r == "no-next":
                print("  （找不到「下一张」按钮，停止）")
                break
            time.sleep(3)

        imgs = list(found.values())
        imgs.sort(key=lambda x: -(x.get("w", 0) * x.get("h", 0)))
        print()
        print(f"  合计 {len(imgs)} 张大图")
        for i, im in enumerate(imgs, 1):
            print(f"    [{i}] {im['w']}x{im['h']}")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for old in OUT_DIR.glob("*"):
            old.unlink()
        for i, im in enumerate(imgs, 1):
            u = im["url"]
            if u.startswith("//"):
                u = "https:" + u
            try:
                req = urllib.request.Request(u, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.xiaohongshu.com/",
                })
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                p = OUT_DIR / f"t{i:02d}_{im['w']}x{im['h']}.webp"
                p.write_bytes(data)
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ [{i}] {str(exc)[:50]}")
        print(f"  已保存到 {OUT_DIR}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
