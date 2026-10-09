"""从已采集的小红书正文里找**免费场地 + 未来日期**的排期。

免费场地（地王广场 / 商场中庭）的演出不上售票平台，
所以这些正文是唯一线索来源。本脚本把「场地 + 日期 + 可能的活动名」一起列出来，
供人工确认或后续解析入库。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path(r"D:\AI\gd-calendar\data\xhs_notes.json")

# 免费场地关键词（与 venue_type.FREE_TYPES 的语义一致）
FREE_HINTS = re.compile(
    r"地王广场|idol空间|Idol空间|IDOL空间|商场|中庭|广场|公园|车站旁|"
    r"艺术馆|艺术广场|音乐馆|文化馆|漫展|会展",
)
# 日期：9/30、10/3、10月3日、2026-10-03
DATE_RE = re.compile(
    r"(?<!\d)("
    r"\d{1,2}\s*/\s*\d{1,2}"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"
    r"|\d{4}\s*[-年]\s*\d{1,2}\s*[-月]\s*\d{1,2}"
    r")(?!\d)"
)


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    items = [i for i in data.get("items") or [] if i.get("body")]
    print(f"  有正文的笔记 {len(items)} 条")

    hits = []
    for it in items:
        body = it["body"]
        venues = sorted(set(FREE_HINTS.findall(body)))
        dates = sorted(set(DATE_RE.findall(body)))
        if venues and dates:
            hits.append((it, venues, dates))

    print(f"  同时含「免费场地线索」与「日期」的: {len(hits)} 条")
    print()
    for it, venues, dates in hits:
        print(f"  === {it['title'][:52]}")
        print(f"      作者 {it.get('author')}")
        print(f"      场地词 {venues}")
        print(f"      日期   {dates[:10]}")
        # 摘出含场地或日期的行，便于人工核对
        for ln in it["body"].splitlines():
            s = ln.strip()
            if len(s) < 4:
                continue
            if FREE_HINTS.search(s) or DATE_RE.search(s):
                print(f"        · {s[:72]}")
        print()


main()
