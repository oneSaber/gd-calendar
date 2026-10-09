"""探测：从小红书笔记详情页提取**图片 URL**（供读图/OCR）。

## 背景

「现役53团最全图鉴」正文说「时间、票价、地点及出演团体**已放进图里**」，
文字只有元数据。要拿到 53 个团体名必须**读图**。

小红书图片是 CDN 链接（`sns-webpic-qc.xhscdn.com`），
需要先提取 URL → 下载到本地 → 才能用视觉读。
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
OUT_DIR = Path(r"D:\AI\gd-calendar\data\xhs_images")

# 从详情页提取图片 src。
# ⚠️ 小红书用懒加载：图片可能只在 `src` / `data-src` / `<source srcset>` 里，
#    所以要三个地方都看，并按宽度挑最大的一张。
_EXTRACT_IMAGES_JS = r"""
(function(){
  var out = [];
  function push(u, w){
    if(!u || u.indexOf('xhscdn') < 0) return;
    out.push({url: u, w: w || 0});
  }
  [].slice.call(document.querySelectorAll('img')).forEach(function(img){
    push(img.getAttribute('src'), img.naturalWidth || 0);
    push(img.getAttribute('data-src'), img.naturalWidth || 0);
    var ss = img.getAttribute('srcset') || '';
    ss.split(',').forEach(function(part){
      var bits = part.trim().split(/\s+/);
      if(bits[0]) push(bits[0], parseInt(bits[1]) || 0);
    });
  });
  // 去重（按去掉查询参数后的路径）
  var m = {}, uniq = [];
  out.forEach(function(x){
    var k = x.url.split('?')[0];
    if(!m[k] || (x.w > m[k].w)){ m[k] = x; }
  });
  Object.keys(m).forEach(function(k){ uniq.push(m[k]); });
  return JSON.stringify(uniq);
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
    # 从已采集的数据里取那条笔记的带 token URL
    notes = json.loads(
        Path(r"D:\AI\gd-calendar\data\xhs_notes.json").read_text(encoding="utf-8")
    )["items"]
    target = next(
        (n for n in notes if "53团" in (n.get("title") or "") and n.get("xsec_token")),
        None,
    )
    if target is None:
        print("  ✗ 数据里找不到带 token 的「53团图鉴」笔记")
        return

    from app.collectors.xhs import XhsNote, note_url

    note = XhsNote(
        note_id=target["note_id"],
        title=target["title"],
        xsec_token=target["xsec_token"],
    )
    url = note_url(note)
    print(f"  目标：{target['title'][:46]}")
    print(f"  URL：{url[:104]}")

    open_tab(url)
    time.sleep(11)

    from xhs_publish import Session  # type: ignore

    s = Session(port=PORT, prefer="xiaohongshu.com")
    try:
        print(f"  当前页: {s.url()[:90]}")
        imgs = json.loads(s.ev(_EXTRACT_IMAGES_JS) or "[]")
        print(f"  提取到图片 {len(imgs)} 张")
        for i, im in enumerate(imgs, 1):
            print(f"    [{i}] w={im.get('w')}  {str(im.get('url'))[:92]}")

        if not imgs:
            return
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, im in enumerate(imgs, 1):
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
                with urllib.request.urlopen(req, timeout=25) as r:
                    data = r.read()
                # 从 URL 猜扩展名；小红书常是 webp
                ext = ".webp" if ".webp" in u else ".jpg"
                p = OUT_DIR / f"note53_{i:02d}{ext}"
                p.write_bytes(data)
                saved.append(p)
                print(f"    ✓ 保存 {p.name}（{len(data) // 1024} KB）")
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ [{i}] 下载失败：{str(exc)[:60]}")
        print()
        print(f"  共保存 {len(saved)} 张到 {OUT_DIR}")
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass


main()
