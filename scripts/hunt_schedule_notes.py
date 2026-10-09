"""定向采集：找「华南地偶活动汇总」这类**汇总帖的最新版**。

## 为什么

实测发现 `KingMaker` 的汇总帖是**最完整的排期来源** —— 一行一场，
含日期 / OPEN 时间 / 场地 / 票价（「无料」= 免费）。这比逐个源抓列表页精准得多。

但抓到的第一条是国庆（10.1–10.7），**已过期**。汇总帖会持续更新，
所以要找到最新那一条。

做法：用「汇总」类关键词搜小红书，把**正文里含未来日期**的帖子挑出来。
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.xhs import (  # noqa: E402
    XhsBrowser,
    check_login,
    collect_search,
    fetch_note_body,
)

# 汇总帖类关键词（比「广州地偶」更精准：这类词命中的就是排期表）
KEYWORDS = [
    "华南地偶 汇总",
    "广州 偶活 汇总",
    "广州 偶活 排期",
    "地偶 活动汇总",
    "广州 地偶 本周",
]

TODAY = dt.date(2026, 10, 9)
OUT = Path(r"D:\AI\gd-calendar\data\xhs_schedule_notes.json")

# 排期行：含日期 + OPEN 时间 + 场地
_SCHED_LINE = re.compile(
    r"(?P<date>\d{1,2}\.\d{1,2}|\d{1,2}/\d{1,2}|\d{1,2}月\d{1,2}日)"
    r".*?(?:OPEN\s*(?P<open>\d{1,2}[:：]\d{2}))?"
    r".*?(?P<venue>[^\s（(]{2,20})"
    r"(?P<free>[（(]无料[）)]|[（(]\d+起[）)])?",
)


def _month_day(text: str, base: dt.date) -> dt.date | None:
    """把「10.1」「10/1」「10月1日」解析成日期（补当前年）。"""
    m = re.match(r"^(\d{1,2})[./月](\d{1,2})", text)
    if not m:
        return None
    mo, day = int(m.group(1)), int(m.group(2))
    try:
        d = dt.date(base.year, mo, day)
    except ValueError:
        return None
    # 跨年：若解析出的日期比基准早 3 个月以上，认为属于下一年
    if (base - d).days > 90:
        try:
            d = dt.date(base.year + 1, mo, day)
        except ValueError:
            return None
    return d


def main() -> None:
    br = XhsBrowser()
    notes: list[dict] = []
    seen: set[str] = set()
    try:
        ok, why = check_login(br)
        print(f"  登录态: {why}")
        if not ok:
            return
        for kw in KEYWORDS:
            try:
                found, good = collect_search(br, kw, wait=8.0)
            except Exception as exc:  # noqa: BLE001
                print(f"  【{kw}】失败 {str(exc)[:50]}")
                continue
            if not good:
                print("  ⚠ 掉登录态，中止")
                break
            fresh = [n for n in found if n.note_id not in seen]
            for n in fresh:
                seen.add(n.note_id)
            print(f"  【{kw}】{len(found)} 条（新 {len(fresh)}）")
            # 只抓标题像「汇总/速览/排期」的正文（省时间）
            todo = [
                n for n in fresh
                if any(k in (n.title or "")
                       for k in ("汇总", "速览", "排期", "时间表", "一览", "偶活"))
            ]
            print(f"      其中像排期表的 {len(todo)} 条，抓正文…")
            for n in todo[:6]:
                body = fetch_note_body(br, n)
                if not body:
                    continue
                # 找未来日期的排期行
                rows = []
                for ln in body.splitlines():
                    s = ln.strip()
                    if len(s) < 6:
                        continue
                    m = _SCHED_LINE.search(s)
                    if not m:
                        continue
                    d = _month_day(m.group("date"), TODAY)
                    if d is None or d < TODAY:
                        continue
                    rows.append({
                        "date": d.isoformat(),
                        "open": m.group("open") or "",
                        "free": "无料" in (m.group("free") or ""),
                        "line": s[:120],
                    })
                if rows:
                    notes.append({
                        "title": n.title, "author": n.author,
                        "url": n.url, "rows": rows,
                    })
                    print(f"        ✓ {n.title[:34]} 未来排期 {len(rows)} 行")
    finally:
        try:
            br.close()
        except Exception:  # noqa: BLE001
            pass

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"generated_at": TODAY.isoformat(), "items": notes},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"  共 {len(notes)} 条帖子含未来排期，已保存 {OUT}")
    for n in notes:
        print(f"    【{n['title'][:36]}】{len(n['rows'])} 行")
        for r in n["rows"][:4]:
            free = "免费" if r["free"] else "售票"
            print(f"        {r['date']} OPEN {r['open']:>5} [{free}] {r['line'][:52]}")


main()
