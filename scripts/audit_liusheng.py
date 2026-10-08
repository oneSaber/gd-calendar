"""确认「留声RECORD」的完整信息，并排查同类漏标。"""

import sqlite3
from pathlib import Path

DB = Path(r"D:\AI\gd-calendar\data\gd_calendar.db")
c = sqlite3.connect(DB)
q = lambda s, a=(): c.execute(s, a).fetchall()  # noqa: E731

print("=== 1) 留声RECORD 完整信息 ===")
rows = q("""SELECT e.id, e.title_display, e.title_raw, e.is_idol, e.is_girl_band,
                   e.is_acg, e.kind, o.start_at, o.city, v.name, o.status,
                   o.price_min, os.source_url
            FROM event e
            LEFT JOIN occurrence o ON o.event_id = e.id
            LEFT JOIN venue v ON v.id = o.venue_id
            LEFT JOIN occurrence_source os ON os.occurrence_id = o.id
            WHERE e.title_display LIKE '%留声%' OR e.title_display LIKE '%RECORD%'""")
for r in rows:
    print(f"  event.id={r[0]}  kind={r[6]}  标记(地/女/A)={int(r[3])}{int(r[4])}{int(r[5])}")
    print(f"    title_display: {r[1]}")
    print(f"    title_raw    : {r[2]}")
    print(f"    时间: {r[7]}   城市: {r[8]}   场地: {r[9]}")
    print(f"    状态: {r[10]}   最低价: {r[11]}")
    print(f"    来源: {r[12]}")

print()
print("=== 2) 该活动的阵容（若阵容里有地偶团体，是最强证据）===")
rows = q("""SELECT oa.artist_raw, oa.role
            FROM occurrence_artist oa
            JOIN occurrence o ON o.id = oa.occurrence_id
            JOIN event e ON e.id = o.event_id
            WHERE e.title_display LIKE '%留声%'""")
for r in rows:
    print(f"    {r[0]}  ({r[1]})")
if not rows:
    print("    （无阵容数据）")

print()
print("=== 3) 同类排查：kind=other 且标题像演出的活动 ===")
print("    （这些不在垂类里，但若含地偶线索就是漏标）")
rows = q("""SELECT title_display, kind FROM event
            WHERE is_idol=0 AND is_girl_band=0 AND is_acg=0
              AND (kind='other' OR kind='doujin_live')
            ORDER BY title_display""")
print(f"    共 {len(rows)} 条，抽样看含「企划/公演/live/idol/偶像」等线索的：")
kw = ("企划", "公演", "live", "idol", "偶像", "特典", "生诞", "生日", "定期",
      "同人", "二次元", "acg", "女子", "girl")
hit = [r for r in rows if any(k in r[0].lower() for k in kw)]
for r in hit:
    print(f"    [{r[1]:12}] {r[0][:58]}")
print(f"    命中线索 {len(hit)} 条")
