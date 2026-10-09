"""找免费场地的**未来场次**（当前为 0，需要定位缺口在哪）。

免费场地（地王广场/商场/公园）的演出不上售票平台，所以：
  * 秀动/豆瓣几乎不可能有
  * 只能来自小红书/微博

本脚本回答：库内有哪些活动提到免费场地？它们的日期是否已过？
以判断「缺口是没采到」还是「采到了但已过期」。
"""

from __future__ import annotations

import datetime as dt
import sqlite3

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"
FROM_DB = "2026-10-09"  # 当前基准日

c = sqlite3.connect(DB)
FREE_VENues = ("地王广场", "智慧城广百广场", "白云艺术广场", "MSH沉浸式音乐馆", "您好艺术馆")

print("=== 免费场地名下的场次 ===")
for name in FREE_VENues:
    rows = c.execute("""
        SELECT o.start_at, e.title_display, o.city
        FROM occurrence o
        JOIN event e ON e.id = o.event_id
        JOIN venue v ON v.id = o.venue_id
        WHERE v.name LIKE ?
        ORDER BY o.start_at
    """, (f"%{name}%",)).fetchall()
    print(f"  「{name}」{len(rows)} 场")
    for start, title, city in rows:
        print(f"      {str(start)[:10]}  {title[:44]}")

print()
print("=== 所有提到「地王广场」的活动/场次（含标题里提到但场地字段不是它的）===")
rows = c.execute("""
    SELECT o.start_at, e.title_display, e.is_idol, e.is_acg,
           COALESCE(v.name, '（无场地）')
    FROM occurrence o
    JOIN event e ON e.id = o.event_id
    LEFT JOIN venue v ON v.id = o.venue_id
    WHERE e.title_display LIKE '%地王%' OR e.description LIKE '%地王%'
    ORDER BY o.start_at
""").fetchall()
print(f"  {len(rows)} 条")
for start, title, iid, acg, vname in rows:
    print(f"    {str(start)[:10]}  [{int(iid)}{int(acg)}] {vname[:22]:24} {title[:40]}")

print()
print("=== 数据库里最近的发布日期分布（看是否只有已过期的）===")
for d, n in c.execute("""
    SELECT substr(start_at,1,10), COUNT(*) FROM occurrence
    WHERE start_at >= ? GROUP BY 1 ORDER BY 1 LIMIT 12
""", (FROM_DB,)):
    print(f"    {d}  {n} 场")

print()
print(f"  （基准日 {FROM_DB}；库内未来场次总数:",
      c.execute("SELECT COUNT(*) FROM occurrence WHERE start_at >= ?", (FROM_DB,)).fetchone()[0], "）")
