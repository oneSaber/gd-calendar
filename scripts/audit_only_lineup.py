"""检查「同人ONLY / 应援」类的实际阵容，按维护者规则判断：有演出团体就收录。"""

import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")
from app.normalize.artist_kb import ArtistKnowledgeBase, classify_lineup  # noqa: E402

kb = ArtistKnowledgeBase()
DB = Path(r"D:\AI\gd-calendar\data\gd_calendar.db")
c = sqlite3.connect(DB)
q = lambda s, a=(): c.execute(s, a).fetchall()  # noqa: E731

print("=== 争议条目：同人ONLY / 应援 类的阵容实况 ===")
rows = q("""SELECT e.id, e.title_display, e.is_idol, e.is_acg,
                   GROUP_CONCAT(oa.artist_raw, ' | ') AS lineup,
                   COUNT(oa.id) AS n
            FROM event e
            LEFT JOIN occurrence o ON o.event_id = e.id
            LEFT JOIN occurrence_artist oa ON oa.occurrence_id = o.id
            WHERE e.title_display LIKE '%同人%' OR e.title_display LIKE '%ONLY%'
               OR e.title_display LIKE '%only%' OR e.title_display LIKE '%应援%'
               OR e.title_display LIKE '%生诞祭%'
            GROUP BY e.id
            ORDER BY n DESC, e.title_display""")
for eid, title, ti, ta, lineup, n in rows:
    flags = "".join(["地" if ti else "·", "A" if ta else "·"])
    names = [x.strip() for x in (lineup or "").split("|") if x.strip()]
    v = classify_lineup(names, kb) if names else None
    known = f"知识库命中 {v.votes}" if v and v.votes else ""
    unknown = f"陌生 {len(v.unknown)}" if v and v.unknown else ""
    verdict = "✅ 有团体 → 收录" if names else "❌ 无团体 → 排除"
    print(f"  [{flags}] 阵容{len(names)}人  {verdict}")
    print(f"      {title[:56]}")
    if names:
        print(f"      名单: {' | '.join(names)}")
    else:
        print("      名单: （空 —— 没有任何演出人员）")
    print()

print("=== 统计：标题带 only/同人/应援 的活动里，有多少完全没有阵容 ===")
n_all = q("""SELECT COUNT(*) FROM event
             WHERE title_display LIKE '%同人%' OR title_display LIKE '%ONLY%'
                OR title_display LIKE '%only%' OR title_display LIKE '%应援%'""")[0][0]
n_none = q("""SELECT COUNT(*) FROM event e
              WHERE (e.title_display LIKE '%同人%' OR e.title_display LIKE '%ONLY%'
                     OR e.title_display LIKE '%only%' OR e.title_display LIKE '%应援%')
                AND NOT EXISTS (SELECT 1 FROM occurrence o
                                JOIN occurrence_artist oa ON oa.occurrence_id = o.id
                                WHERE o.event_id = e.id)""")[0][0]
print(f"  共 {n_all} 条，其中 {n_none} 条**完全没有阵容**（按规则应排除）")
