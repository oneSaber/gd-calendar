"""核对静态站导出的数据是否符合「只发垂类」的要求。"""

import json
from pathlib import Path

base = Path(r"D:\AI\gd-calendar\docs\data")
occ = json.loads((base / "occurrences.json").read_text(encoding="utf-8"))
st = json.loads((base / "stats.json").read_text(encoding="utf-8"))
ven = json.loads((base / "venues.json").read_text(encoding="utf-8"))

items = occ["items"]
print(f"  场次 {len(items)}   场地 {len(ven['items'])}")
print(f"  stats: occ={st['occurrences']} events={st['events']} "
      f"artists={st['artists']} venues={st['venues']}")
print()

# 不变式 1：每条都必须命中至少一个垂类标记
bad = [
    it for it in items
    if not (it["event"]["is_idol"] or it["event"]["is_girl_band"] or it["event"]["is_acg"])
]
print(f"  未命中垂类标记的场次: {len(bad)}  （必须为 0）")
for it in bad[:5]:
    print(f"    ✗ {it['event']['title'][:44]}")

# 不变式 2：stats 必须与明细一致
assert st["occurrences"] == len(items), "stats.occurrences 与明细不一致"
assert st["venues"] == len(ven["items"]), "stats.venues 与场地明细不一致"
print("  stats 与明细一致 ✓")
print()

seen = set()
print("  收录的活动：")
for it in sorted(items, key=lambda x: x["start_at"]):
    e = it["event"]
    if e["id"] in seen:
        continue
    seen.add(e["id"])
    tags = [t for t, f in (("地偶", e["is_idol"]), ("女子乐队", e["is_girl_band"]),
                           ("ACG", e["is_acg"])) if f]
    print(f"    [{'+'.join(tags):14}] {it['start_at'][:10]}  "
          f"{it['city']:4} {e['title'][:38]}")

print()
cities: dict[str, int] = {}
for it in items:
    cities[it["city"]] = cities.get(it["city"], 0) + 1
print("  城市分布:", cities)

# 不变式 3：月历计数之和 == 明细条数（同口径）
total_dots = 0
for f in sorted((base / "calendar").glob("*.json")):
    cal = json.loads(f.read_text(encoding="utf-8"))
    days = cal["by_city"].get("", {})
    s = sum(d["band"] + d["idol"] for d in days.values())
    total_dots += s
    print(f"  {cal['month']}: 点阵合计 {s} 场，{len(days)} 天有数据")
print(f"  月历点阵总计 {total_dots}  == 明细 {len(items)} -> "
      f"{'一致 ✓' if total_dots == len(items) else '不一致 ✗'}")
