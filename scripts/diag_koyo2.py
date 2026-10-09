"""诊断：koyo生诞祭应援 为什么从垂类掉出去。

上一轮它是**已发布**的（reclassify 按阵容给了 is_idol=True）。
本轮采集后它的场地从「In MAO Live House 广州太古仓」变成
「海珠区南洲路154号侨建大厦」，且不再在垂类里。
"""

from __future__ import annotations

import sqlite3

from app.parsers import text_zh as t

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"


def main() -> None:
    c = sqlite3.connect(DB)

    print("=== koyo 相关活动 ===")
    rows = c.execute("""
        SELECT e.id, e.title_display, e.is_idol, e.is_acg, e.kind,
               v.name AS venue,
               (SELECT GROUP_CONCAT(a.name, ' | ')
                FROM occurrence_artist oa JOIN artist a ON a.id=oa.artist_id
                WHERE oa.occurrence_id=o.id) AS lineup
        FROM event e
        JOIN occurrence o ON o.event_id = e.id
        LEFT JOIN venue v ON v.id = o.venue_id
        WHERE e.title_display LIKE '%koyo%' OR e.title_display LIKE '%Koyo%'
        ORDER BY e.title_display
    """).fetchall()
    for (eid, title, iid, acg, kind, venue, lineup) in rows:
        print(f"  [地偶={iid} ACG={acg}] kind={kind}")
        print(f"      {title[:54]}")
        print(f"      场地: {venue!r}")
        print(f"      阵容: {str(lineup)[:110]}")

    print()
    print("=== 用标题分类（应援字样会被当非演出）===")
    for (title,) in [(r[1],) for r in rows]:
        a = t.classify(title)
        print(f"  is_idol={a.is_idol} tags={a.tags}  {title[:44]}")

    print()
    print("=== 阵容里的名字能否命中知识库 ===")
    from app.normalize.artist_kb import ArtistKnowledgeBase

    kb = ArtistKnowledgeBase()
    for (lineup,) in [(r[6],) for r in rows]:
        for name in (lineup or "").split(" | "):
            name = name.strip()
            if not name:
                continue
            entry, how = kb.lookup(name)
            mark = f"{entry.name} ({entry.kind})" if entry else "—"
            print(f"    {name[:38]:40} → {mark}  [{how}]")


main()
