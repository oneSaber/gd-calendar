"""查场地库现状：地王广场在不在、有多少名字其实是活动名。"""

from __future__ import annotations

import sqlite3

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"
c = sqlite3.connect(DB)

print("=== 地王广场相关 ===")
rows = c.execute(
    "SELECT id, name, city, venue_type FROM venue WHERE name LIKE '%地王%'"
).fetchall()
if rows:
    for r in rows:
        print("  ", r)
else:
    print("  ✗ 不在库里")

print()
print("=== 疑似「活动名被当场地」的噪音 ===")
import re

NOISE = re.compile(r"《|》|沉浸式|脱口秀|喜剧|售票|演出展览|博物馆|动物园|美术馆|俱乐部")
rows = c.execute("SELECT id, name, city, venue_type FROM venue ORDER BY name").fetchall()
noise = [r for r in rows if NOISE.search(r[1] or "")]
print(f"  {len(noise)} / {len(rows)} 个：")
for r in noise[:24]:
    n = c.execute(
        "SELECT COUNT(*) FROM occurrence WHERE venue_id = ?", (r[0],)
    ).fetchone()[0]
    print(f"    [{r[3] or '-':11}] {r[1][:52]:54} ({r[2]})  {n} 场")

print()
print("=== 各类型下挂的场次数 ===")
for t, n, occ in c.execute("""
    SELECT COALESCE(v.venue_type,'(空)'), COUNT(DISTINCT v.id), COUNT(o.id)
    FROM venue v LEFT JOIN occurrence o ON o.venue_id = v.id
    GROUP BY 1 ORDER BY 3 DESC
"""):
    print(f"  {t:12} {n:3} 个场地  {occ:4} 场")
