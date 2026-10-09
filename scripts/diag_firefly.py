"""诊断：萤火虫漫展的地偶舞台为什么掉出垂类。

时间线（从日志与线上数据推断）：
  1. 微博采到 6 个「10月东莞萤火虫# 地下偶像【X】」场次 → 线上静态站显示
     `[地偶+ACG]`，说明当时 is_idol=1 且 is_acg=1
  2. 跑 backfill 后变成 (True, False, False)
  3. 现在 is_acg=0 → 静态站不再收录

要查清：ACG 标记是**采集时由场地/品类上下文**给的，还是别处给的？
如果同一个活动在**这次**采集里也被更新过，为什么没恢复？
"""

from __future__ import annotations

import sqlite3

from app.parsers import text_zh as t

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"


def main() -> None:
    c = sqlite3.connect(DB)

    print("=== 萤火虫活动的完整状态（含场地与来源）===")
    rows = c.execute("""
        SELECT e.id, e.title_display, e.is_idol, e.is_acg, e.is_girl_band,
               e.kind, v.name AS venue,
               (SELECT GROUP_CONCAT(DISTINCT s.code)
                FROM occurrence_source os JOIN source s ON s.id=os.source_id
                WHERE os.occurrence_id = o.id) AS sources,
               (SELECT COUNT(*) FROM occurrence_artist oa WHERE oa.occurrence_id=o.id) AS lineup
        FROM event e
        JOIN occurrence o ON o.event_id = e.id
        LEFT JOIN venue v ON v.id = o.venue_id
        WHERE e.title_display LIKE '%萤火虫%'
        ORDER BY e.title_display
    """).fetchall()
    for (eid, title, iid, acg, gb, kind, venue, sources, lineup) in rows:
        print(f"  [地偶={iid} ACG={acg} 女子={gb}] kind={kind} 阵容={lineup}")
        print(f"      {title[:50]}")
        print(f"      场地={venue!r}  来源={sources}")

    print()
    print("=== 用「标题 + 场地上下文」模拟采集时的判定 ===")
    for (title, venue) in [(r[1], r[6]) for r in rows]:
        a = t.classify(title)
        b = t.classify(f"{title} {venue or ''}".strip(), venue or "")
        print(f"  {title[:42]}")
        print(f"      仅标题      : 地偶={a.is_idol} ACG={a.is_acg}")
        print(f"      标题+场地上下文: 地偶={b.is_idol} ACG={b.is_acg}  (场地={venue!r})")


main()
