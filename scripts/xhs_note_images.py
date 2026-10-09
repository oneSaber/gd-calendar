"""只提取**当前笔记**的轮播图（不要混进侧栏推荐 / 头像 / 图标）。

## 为什么需要重写

第一次提取拿到 77 张图，其中混进了：
  * 侧栏「推荐」流的封面（实测抽到一张福州乐队招募海报）
  * 用户头像、图标、表情（大量 1-4 KB 小图）

所以判据要收紧：
  * 只要**详情弹层容器内**的图（`#noteContainer` / `[role=dialog]`）
  * 排除头像（URL 里带 `avatar`，或在 `[class*=avatar]` 内）
  * 只要够大的（`naturalWidth >= 600`）—— 图鉴是长图，宽度都很大
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

_EXTRACT = r"""
(function(){
  // ⚠️ 必须限定在**详情容器**内：整页扫描会把侧栏推荐也抓进来（实测踩过）
  var root = document.querySelector('#noteContainer')
          || document.querySelector('[role=dialog]')
          || document.querySelector('[class*=note-detail]')
          || document.body;
  var out = [];
  [].slice.call(root.querySelectorAll('img')).forEach(function(img){
    var src = img.getAttribute('src') || img.getAttribute('data-src') || '';
    if(src.indexOf('xhscdn') < 0) return;
    // 排除头像
    if(/avatar/i.test(src)) return;
    if(img.closest('[class*=avatar]')) return;
    var w = img.naturalWidth || 0, h = img.naturalHeight || 0;
    out.push({url: src, w: w, h: h});
  });
  // 按去掉 query 的路径去重，保留最大的一张
  var m = {};
  out.forEach(function(x){
    var k = x.url.split('?')[0];
    if(!m[k] || x.w * x.h > m[k].w * m[k].h) m[k] = x;
  });
  var arr = Object.keys(m).map(function(k){ return m[k]; });
  arr.sort(function(a, b){ return b.w * b.h - a.w * a.h; });
  return JSON.stringify({
    container: root.id || root.getAttribute('role') || (root.className || '').slice(0, 40),
    total: arr.length,
    images: arr
  });
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
        print("  ✗ 找不到带 token 的「53团图鉴」")
        return

    from app.collectors.xhs import XhsNote, note_url

    url = note_url(
        XhsNote(
            note_id=target["note_id"],
            title=target["title"],
            xsec_token=target["xsec_token"],
        )
    )
    print(f"  目标：{target['title'][:46]}")
    open_tab(url)
    time.sleep(12)

    from xhs_publish import Session  # type: ignore

    s = Session(port=PORT, prefer="xiaohongshu.com")
    try:
        d = json.loads(s.ev(_EXTRACT) or "{}")
        print(f"  容器：{d.get('container')}  候选图 {d.get('total')} 张")
        imgs = d.get("images") or []
        # 图鉴是长图：只要宽度够大的
        big = [i for i in imgs if i.get("w", 0) >= 600]
        print(f"  宽度 >=600 的：{len(big)} 张")
        for i, im in enumerate(big[:12], 1):
            print(f"    [{i}] {im['w']}x{im['h']}  {str(im['url'])[:86]}")

        if not big:
            return
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for old in OUT_DIR.glob("*"):
            old.unlink()
        saved = 0
        for i, im in enumerate(big, 1):
            u = im["url"]
            if u.startswith("//"):
                u = "https:" + u
            try:
                req = urllib.request.Request(
                    u,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://www.xiaohongshu.com/",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                p = OUT_DIR / f"p{i:02d}_{im['w']}x{im['h']}.webp"
                p.write_bytes(data)
                saved += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ [{i}] {str(exc)[:50]}")
        print()
        print(f"  保存 {saved} 张到 {OUT_DIR}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
