"""诊断：萤火虫漫展地偶舞台的 ACG 标记为什么被回填清掉。

背景：这些活动原本带 ACG 标记（线上静态站显示过 `[地偶+ACG]`），
但 `backfill` 把它们改成 `(True, False, False)`。
需要确认这是**修正了误标**还是**引入了漏标**。
"""

from __future__ import annotations

import sqlite3

from app.parsers import text_zh as t

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"

TITLES = [
    "10月东莞萤火虫# 地下偶像【夏日青空】",
    "10月东莞萤火虫# 地下偶像【Smoky Candy】",
    "10月东莞萤火虫# 地下偶像【木木林檎】",
    "10月东莞萤火虫# 【白夜预言】",
    "10月东莞萤火虫# 地下偶像【Chosen-Star】",
    "10月东莞萤火虫# 地下偶像【REX狂想曲】",
]


def main() -> None:
    print("=== 标题分类器现在的判定 ===")
    for s in TITLES:
        c = t.classify(s)
        hits = [h for h in t.ACG_HINTS if h.lower() in s.lower()]
        print(f"  is_idol={c.is_idol} is_acg={c.is_acg}  ACG词命中={hits}")
        print(f"    {s}")

    print()
    print("=== 库里这些活动的实际标记 ===")
    c = sqlite3.connect(DB)
    for (title, is_idol, is_acg, is_gb) in c.execute(
        "SELECT title_display, is_idol, is_acg, is_girl_band FROM event "
        "WHERE title_display LIKE '%萤火虫%' ORDER BY title_display"
    ):
        print(f"  [地偶={int(is_idol)} ACG={int(is_acg)} 女子={int(is_gb)}] {title[:52]}")

    print()
    print("=== 关键问题：「萤火虫」是否该算 ACG 关键词 ===")
    print("  ACG_HINTS 片段:", [h for h in t.ACG_HINTS][:24])
    print("  「萤火虫」在 ACG_HINTS 里:", any("萤火虫" in h for h in t.ACG_HINTS))
    print("  「漫展」在 ACG_HINTS 里:", any("漫展" in h for h in t.ACG_HINTS))


main()
