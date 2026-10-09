"""打印「华南地偶活动汇总帖」等含免费场地排期的笔记正文全文。

用于人工核对：地王广场的「无料」场次具体是哪几天、几点、哪个团。
"""

from __future__ import annotations

import json
from pathlib import Path

SRC = Path(r"D:\AI\gd-calendar\data\xhs_notes.json")
TARGETS = ("华南地偶活动汇总", "广州国庆偶活速览", "中秋节3日11场")

data = json.loads(SRC.read_text(encoding="utf-8"))
for it in data.get("items") or []:
    title = it.get("title") or ""
    if not any(t in title for t in TARGETS):
        continue
    body = it.get("body") or ""
    if not body:
        continue
    print("=" * 74)
    print(f"  【{title}】")
    print(f"  作者 {it.get('author')}   正文 {len(body)} 字")
    print(f"  {it.get('url')}")
    print("=" * 74)
    print(body[:2600])
    print()
