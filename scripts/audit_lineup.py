"""摸清阵容数据质量：分析要基于它，先看清有什么、缺什么。"""

import sqlite3
from pathlib import Path

DB = Path(r"D:\AI\gd-calendar\data\gd_calendar.db")
c = sqlite3.connect(DB)
q = lambda s, a=(): c.execute(s, a).fetchall()  # noqa: E731

print("=== 1) artist.kind 的取值分布（应该都是 unknown 吧）===")
for r in q("SELECT kind, COUNT(*) FROM artist GROUP BY kind ORDER BY 2 DESC"):
    print(f"  {r[0]!r:20} {r[1]}")

print()
print("=== 2) artist 里有多少带 aliases / agency / status ===")
for col in ("aliases", "agency", "name_en", "origin_city", "status"):
    n = q(f"SELECT COUNT(*) FROM artist WHERE {col} IS NOT NULL AND {col} != '' AND {col} != '[]'")[0][0]
    print(f"  {col:14} 非空 {n} / 279")

print()
print("=== 3) 当前 9 场垂类演出的阵容明细 ===")
rows = q("""SELECT e.title_display, o.start_at, o.city,
                   GROUP_CONCAT(oa.artist_raw, ' | ') AS lineup
            FROM occurrence o
            JOIN event e ON e.id = o.event_id
            LEFT JOIN occurrence_artist oa ON oa.occurrence_id = o.id
            WHERE e.is_idol = 1 OR e.is_girl_band = 1 OR e.is_acg = 1
            GROUP BY o.id ORDER BY o.start_at""")
for r in rows:
    print(f"  {r[1][:10]} {r[2]:4} {(r[0] or '')[:30]:32}")
    print(f"        阵容: {r[3] or '（无）'}")

print()
print("=== 4) 没有阵容数据的场次有多少（分析盲区）===")
n_no = q("""SELECT COUNT(*) FROM occurrence o
            WHERE NOT EXISTS (SELECT 1 FROM occurrence_artist oa WHERE oa.occurrence_id = o.id)""")[0][0]
print(f"  {n_no} / 355 场次没有阵容")

print()
print("=== 5) 各来源的阵容覆盖率（哪个源不给名单）===")
rows = q("""SELECT s.code,
                   COUNT(DISTINCT o.id) AS occ,
                   COUNT(DISTINCT CASE WHEN oa.id IS NOT NULL THEN o.id END) AS with_lineup
            FROM occurrence o
            JOIN occurrence_source os ON os.occurrence_id = o.id
            JOIN source s ON s.id = os.source_id
            LEFT JOIN occurrence_artist oa ON oa.occurrence_id = o.id
            GROUP BY s.code ORDER BY 2 DESC""")
for r in rows:
    pct = (r[2] * 100 // r[1]) if r[1] else 0
    print(f"  {r[0]:12} {r[2]:4}/{r[1]:4} 有阵容 ({pct}%)")

print()
print("=== 6) 阵容名样本（看格式是否干净，能不能当数据库键）===")
rows = q("""SELECT artist_raw, COUNT(*) n FROM occurrence_artist
            GROUP BY artist_raw ORDER BY n DESC LIMIT 30""")
for r in rows:
    print(f"  {r[0][:40]:42} 出现 {r[1]} 次")
