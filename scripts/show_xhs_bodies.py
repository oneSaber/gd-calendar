"""查看抓到的正文内容（验证情报质量）。"""

from __future__ import annotations

import json
from pathlib import Path

d = json.loads(
    Path(r"D:\AI\gd-calendar\data\xhs_test.json").read_text(encoding="utf-8")
)
items = d["items"]
tok = sum(1 for i in items if i.get("xsec_token"))
body = sum(1 for i in items if i.get("body"))
print(f"  笔记 {len(items)} 条 | 有 token {tok} 条 | 有正文 {body} 条")
print()

for i in items:
    b = i.get("body") or ""
    if not b:
        continue
    print("=" * 70)
    print(f"  【{i['title'][:50]}】  by {i['author']}")
    print(f"  正文 {len(b)} 字")
    print("=" * 70)
    print(b[:2000])
    print()
