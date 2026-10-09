"""列出阵容里的**未知演员**，按出现频次排序（用于扩艺人库）。

背景：垂类判定的命脉是「阵容里有没有已知垂类团体」。知识库现在只有 15 个
地偶团体，而阵容里有 245+ 个未知名字。**高频出现的才是真团体**
（低频的多是拼写差异、个人名、或一次性嘉宾）。

用法：
    python scripts/unknown_artists.py            # 全部
    python scripts/unknown_artists.py --limit 40 # 只看前 40
    python scripts/unknown_artists.py --vertical # 只看垂类场次里的
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"

# 明显不是团体的名字模式（个人名/占位/噪音）
import re  # noqa: E402

_NOISE_PATTERNS = [
    re.compile(r"^[\d\W_]+$"),            # 纯符号/数字
    re.compile(r"^.{1,1}$"),               # 单字
    re.compile(r"待定|待公布|未定|TBD", re.I),
    re.compile(r"应援|周边|特典|限定版|生诞祭版|版本|ver\.?\s*\d", re.I),
]


def is_noise(name: str) -> bool:
    return any(p.search(name) for p in _NOISE_PATTERNS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--vertical", action="store_true",
                    help="只看地偶/女子乐队/ACG 场次里的演员")
    args = ap.parse_args()

    c = sqlite3.connect(DB)
    where = ""
    if args.vertical:
        where = ("WHERE e.is_idol=1 OR e.is_girl_band=1 OR e.is_acg=1")

    rows = c.execute(f"""
        SELECT a.name, a.kind, COUNT(DISTINCT o.event_id) AS n
        FROM artist a
        JOIN occurrence_artist oa ON oa.artist_id = a.id
        JOIN occurrence o ON o.id = oa.occurrence_id
        JOIN event e ON e.id = o.event_id
        {where}
        GROUP BY a.id
        ORDER BY n DESC
    """).fetchall()

    unknown = [(n, k, cnt) for (n, k, cnt) in rows if k in ("unknown", "band")]
    unknown = [(n, k, cnt) for (n, k, cnt) in unknown if not is_noise(n)]

    print(f"  {'（仅垂类）' if args.vertical else '（全部）'}未知/待核实演员 "
          f"{len(unknown)} 个，按场次数排序：")
    print()
    print(f"  {'场次':>4}  {'当前 kind':10} 名字")
    for n, k, cnt in unknown[: args.limit]:
        print(f"  {cnt:4}  {k:10} {n}")

    # 附带：已知垂类团体的场次数（对照用）
    known = [(n, k, cnt) for (n, k, cnt) in rows
             if k in ("idol_group", "girl_band", "acg_unit")]
    if known:
        print()
        print("  对照：已核实垂类团体")
        for n, k, cnt in known[:15]:
            print(f"  {cnt:4}  {k:10} {n}")


main()
