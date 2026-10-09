"""现状审计：对照目标要求，列出「已有什么 / 缺什么」。

目标：ACG 音乐演出 + 漫展垂类日历，支持**日期选择**与**艺人搜索**，
信源含票务网站 + 小红书 + 微博。

审计范围：
  1. 数据：漫展 / 免费场地 是否已有数据与标记
  2. 前端：日期选择、艺人搜索 是否已实现
  3. 信源：各源在库里的实际产出
  4. 垂类覆盖面：漫展场次有多少、是否被漏掉
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ROOT = Path(r"D:\AI\gd-calendar")
DB = ROOT / "data" / "gd_calendar.db"


def main() -> None:
    c = sqlite3.connect(DB)
    q = lambda s: c.execute(s).fetchall()  # noqa: E731

    print("=" * 68)
    print("  1) 数据现状")
    print("=" * 68)
    for label, sql in [
        ("场次", "SELECT COUNT(*) FROM occurrence"),
        ("活动", "SELECT COUNT(*) FROM event"),
        ("艺人", "SELECT COUNT(*) FROM artist"),
        ("场地", "SELECT COUNT(*) FROM venue"),
        ("垂类活动", "SELECT COUNT(*) FROM event WHERE is_idol=1 OR is_girl_band=1 OR is_acg=1"),
    ]:
        print(f"  {label:10} {q(sql)[0][0]}")

    print()
    print("  场地类型分布（venue_type）:")
    for t, n in q("SELECT COALESCE(venue_type,'（未分类）'), COUNT(*) "
                  "FROM venue GROUP BY 1 ORDER BY 2 DESC"):
        print(f"    {str(t):16} {n}")

    print()
    print("=" * 68)
    print("  2) 漫展覆盖（目标明确要「漫展」）")
    print("=" * 68)
    MANZHAN = ("漫展", "萤火虫", "CICF", "AGF", "同人", "only", "Only", "ONLY",
               "动漫", "二次元", "comic", "Comic", "COMIC", "嘉年华")
    rows = q("SELECT id, title_display, is_idol, is_acg, is_girl_band FROM event")
    hits = [r for r in rows if any(k in (r[1] or "") for k in MANZHAN)]
    print(f"  标题含漫展类关键词的活动: {len(hits)}")
    in_vertical = [r for r in hits if r[2] or r[3] or r[4]]
    print(f"    其中属垂类: {len(in_vertical)}")
    print(f"    未进垂类  : {len(hits) - len(in_vertical)}  ← 可能是漏掉的")
    for r in (hits[:20]):
        flag = "垂类" if (r[2] or r[3] or r[4]) else "**未进**"
        print(f"      [{flag}] {str(r[1])[:58]}")

    print()
    print("=" * 68)
    print("  3) 免费场地线索（库内场地名匹配）")
    print("=" * 68)
    FREE_HINT = ("广场", "空间", "商场", "中庭", "车站", "公园", "湖", "大学",
                 "文化馆", "图书馆", "创意园", "园区", "天街", "城")
    for name, city, n in q("""
        SELECT v.name, v.city, COUNT(o.id) FROM venue v
        LEFT JOIN occurrence o ON o.venue_id = v.id
        GROUP BY v.id ORDER BY 3 DESC
    """):
        if any(k in (name or "") for k in FREE_HINT):
            print(f"    {n:3} 场  {name} ({city})")

    print()
    print("=" * 68)
    print("  4) 前端能力（对照目标要求）")
    print("=" * 68)
    web = ROOT / "app" / "web"
    all_js = "\n".join(
        p.read_text(encoding="utf-8") for p in (web / "js").glob("*.js")
    )
    html = (web / "index.html").read_text(encoding="utf-8")
    checks = [
        ("日期选择（date input）", 'type="date"' in html or "type='date'" in html),
        ("日期范围 from/to", "from" in all_js and "to" in all_js),
        ("月历视图", "calendar" in all_js.lower() or "month" in all_js.lower()),
        ("城市筛选", "city" in all_js),
        ("标记筛选（地偶/女子/ACG）", "flag" in all_js),
        ("关键词搜索", "q=" in all_js or "keyword" in all_js),
        ("艺人搜索", "artist" in all_js.lower()),
        ("艺人维度 API", False),
    ]
    for label, ok in checks:
        print(f"    {'✓' if ok else '✗'} {label}")

    print()
    print("  API 路由:")
    api = (ROOT / "app" / "api" / "main.py").read_text(encoding="utf-8")
    for m in re.findall(r'@router\.(get|post)\("([^"]+)"', api):
        print(f"    {m[0].upper():5} {m[1]}")

    print()
    print("  「artist」在 API 里的出现次数:",
          len(re.findall(r"artist", api, re.I)))


main()
