"""探测轮播的可点元素（找「下一张」到底在哪）。

指示器显示 `1/6` 说明有 6 张图，但常规选择器找不到下一张按钮。
先把弹层里的可点元素全列出来。
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

_DUMP = r"""
(function(){
  var root = document.querySelector('#noteContainer')
          || document.querySelector('[role=dialog]')
          || document.body;
  var out = [];
  [].slice.call(root.querySelectorAll('*')).forEach(function(e){
    var c = String(e.className || '');
    var r = e.getBoundingClientRect();
    if(r.width < 10 || r.height < 10) return;
    // 只列「像箭头/按钮」的：类名或角色像，或是 svg/i
    var likeArrow = /arrow|next|prev|swiper|indicator|dot|carousel|slider|btn|button/i.test(c);
    var isBtn = e.tagName === 'BUTTON' || e.getAttribute('role') === 'button'
             || e.tagName === 'SVG' || e.tagName === 'I';
    if(!likeArrow && !isBtn) return;
    out.push({
      tag: e.tagName, cls: c.slice(0, 60),
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
      aria: e.getAttribute('aria-label') || ''
    });
  });
  // 去重
  var m = {}, uniq = [];
  out.forEach(function(x){
    var k = x.tag + '|' + x.cls + '|' + x.x + ',' + x.y;
    if(!m[k]){ m[k] = 1; uniq.push(x); }
  });
  // 主图与容器尺寸，用于推算点击坐标
  var img = null, best = 0;
  [].slice.call(root.querySelectorAll('img')).forEach(function(i){
    var src = i.getAttribute('src') || '';
    if(src.indexOf('xhscdn') < 0 || /avatar/i.test(src)) return;
    var r = i.getBoundingClientRect();
    var a = r.width * r.height;
    if(a > best){ best = a; img = {x: Math.round(r.x), y: Math.round(r.y),
                                 w: Math.round(r.width), h: Math.round(r.height)}; }
  });
  return JSON.stringify({count: uniq.length, items: uniq.slice(0, 30), mainImage: img});
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
        d = json.loads(s.ev(_DUMP) or "{}")
        print(f"  候选可点元素 {d.get('count')} 个；主图 {d.get('mainImage')}")
        print()
        for it in d.get("items", []):
            print(f"    <{it['tag']}> ({it['x']},{it['y']}) {it['w']}x{it['h']} "
                  f"cls={it['cls']!r} aria={it['aria']!r}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
