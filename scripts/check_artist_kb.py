"""用真实阵容测试演员知识库的判定效果。"""

import sqlite3
from pathlib import Path

import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.normalize.artist_kb import ArtistKnowledgeBase, classify_lineup  # noqa: E402

kb = ArtistKnowledgeBase()
print(f"  知识库条目: {len(kb)}")

DB = Path(r"D:\AI\gd-calendar\data\gd_calendar.db")
c = sqlite3.connect(DB)
q = lambda s, a=(): c.execute(s, a).fetchall()  # noqa: E731

print()
print("=== 按阵容判定当前垂类演出 ===")
rows = q("""SELECT e.title_display, o.id, o.start_at, o.city,
                   GROUP_CONCAT(oa.artist_raw, '|') AS lineup,
                   e.is_idol, e.is_girl_band, e.is_acg
            FROM occurrence o
            JOIN event e ON e.id = o.event_id
            LEFT JOIN occurrence_artist oa ON oa.occurrence_id = o.id
            WHERE e.is_idol = 1 OR e.is_girl_band = 1 OR e.is_acg = 1
            GROUP BY o.id ORDER BY o.start_at""")
for title, oid, start, city, lineup, ti, tg, ta in rows:
    names = [x for x in (lineup or "").split("|") if x.strip()]
    v = classify_lineup(names, kb)
    title_f = {"is_idol": bool(ti), "is_girl_band": bool(tg), "is_acg": bool(ta)}
    marks = []
    for f, cn in (("is_idol", "地"), ("is_girl_band", "女"), ("is_acg", "A")):
        if title_f[f] and getattr(v, f):
            marks.append(f"{cn}=双方")
        elif getattr(v, f):
            marks.append(f"{cn}=阵容")
        elif title_f[f]:
            marks.append(f"{cn}=标题")
    print(f"  {(title or '')[:34]:36} {start[:10]} {city}")
    print(f"      阵容({len(names)}): {' | '.join(names) if names else '（无）'}")
    print(f"      判定: {' '.join(marks) if marks else '（无标记）'}   票数={v.votes}")
    if v.unknown:
        print(f"      陌生: {' | '.join(v.unknown)}")

print()
print("=== 全库：阵容里出现过的演员名，知识库能认多少 ===")
rows = q("""SELECT DISTINCT artist_raw FROM occurrence_artist""")
known = pat = miss = 0
miss_names = []
for (name,) in rows:
    e, how = kb.lookup(name)
    if how in ("name", "alias"):
        known += 1
    elif how == "pattern":
        pat += 1
    else:
        miss += 1
        miss_names.append(name)
print(f"  精确命中 {known} · 规则命中 {pat} · 完全不认 {miss}（共 {len(rows)}）")
print()
print("  完全不认的前 25 个（这些需要联网核实）：")
for n in miss_names[:25]:
    print(f"    {n}")
