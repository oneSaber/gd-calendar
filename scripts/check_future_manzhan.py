"""核对：库里未来漫展场次的详情（场地/免费/是否进垂类）。"""

from __future__ import annotations

import sqlite3

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"
TODAY = "2026-10-09"

c = sqlite3.connect(DB)

print("=== 标题含漫展/同人/ONLY 的未来场次 ===")
rows = c.execute("""
    SELECT o.start_at, e.title_display, e.is_idol, e.is_acg, e.is_girl_band,
           e.kind, COALESCE(v.name,'（无场地）'), COALESCE(v.venue_type,'-'),
           o.is_free, o.price_min,
           (SELECT COUNT(*) FROM occurrence_artist oa WHERE oa.occurrence_id=o.id)
    FROM occurrence o
    JOIN event e ON e.id = o.event_id
    LEFT JOIN venue v ON v.id = o.venue_id
    WHERE o.start_at >= ?
      AND (e.title_display LIKE '%漫展%' OR e.title_display LIKE '%同人%'
           OR e.title_display LIKE '%ONLY%' OR e.title_display LIKE '%only%'
           OR e.title_display LIKE '%嘉年华%')
    ORDER BY o.start_at
""", (TODAY,)).fetchall()

print(f"  {len(rows)} 场")
for (start, title, iid, acg, gb, kind, vname, vtype, free, pmin, lineup) in rows:
    flags = f"{'地' if iid else '·'}{'女' if gb else '·'}{'A' if acg else '·'}"
    vert = "垂类" if (iid or acg or gb) else "**非垂类**"
    freev = "免费场地" if vtype in ("mall", "park", "campus") else vtype
    price = "免费" if free else (f"¥{pmin:.0f}" if pmin else "?")
    print(f"  {str(start)[:10]} [{flags}] {vert:8} {price:6} 阵容{lineup}  {freev}")
    print(f"      {vname[:34]:36} {title[:44]}")
