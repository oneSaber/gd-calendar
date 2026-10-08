"""审计：移除「企划」地偶词后，哪些活动可能被漏标。

不能只修用户指出的那一条 —— 要看清同类风险的全貌。
"""

import sqlite3
from pathlib import Path

DB = Path(r"D:\AI\gd-calendar\data\gd_calendar.db")
c = sqlite3.connect(DB)
q = lambda s, a=(): c.execute(s, a).fetchall()  # noqa: E731

print("=== 1) 标题含「企划」的活动，及当前标记 ===")
rows = q("""SELECT id, title_display, is_idol, is_girl_band, is_acg, kind
            FROM event WHERE title_display LIKE '%企划%'
            ORDER BY is_idol DESC, title_display""")
for r in rows:
    flags = "".join(["地" if r[2] else "·", "女" if r[3] else "·", "A" if r[4] else "·"])
    print(f"  [{flags}] kind={r[5]:14} {r[1][:52]}")
print(f"  共 {len(rows)} 条")

print()
print("=== 2) 用户指出的那条：比邻星球企划 ===")
rows = q("""SELECT e.id, e.title_display, e.is_idol, e.kind, o.start_at, o.city, v.name
            FROM event e
            LEFT JOIN occurrence o ON o.event_id = e.id
            LEFT JOIN venue v ON v.id = o.venue_id
            WHERE e.title_display LIKE '%比邻星球%'""")
for r in rows:
    print(f"  id={r[0]} is_idol={r[2]} kind={r[3]}")
    print(f"    {r[1]}")
    print(f"    {r[4]}  {r[5]}  {r[6]}")

print()
print("=== 3) 库里所有含「星球」的活动（看关键词会不会太宽）===")
rows = q("SELECT title_display, is_idol FROM event WHERE title_display LIKE '%星球%'")
for r in rows:
    print(f"  is_idol={r[1]!s:5} {r[0][:56]}")

print()
print("=== 4) 库里含「偶像 / 地偶 / idol」但未被标为地偶的（潜在漏标）===")
rows = q("""SELECT title_display, is_idol, kind FROM event
            WHERE is_idol = 0
              AND (title_display LIKE '%偶像%' OR title_display LIKE '%地偶%'
                   OR lower(title_display) LIKE '%idol%')""")
for r in rows:
    print(f"  kind={r[2]:14} {r[0][:56]}")
print(f"  共 {len(rows)} 条")
