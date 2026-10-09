"""实测新增的搜索能力：按艺人名筛场次 + 有场次的艺人自动补全。"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.api import service  # noqa: E402
from app.db import session_scope  # noqa: E402


async def main() -> None:
    async with session_scope() as s:
        print("=== 1) 艺人自动补全（只返回有未来场次的，按场次排序）===")
        arts = await service.list_artists(s, with_upcoming=True, limit=500)
        print(f"  有未来场次的艺人: {len(arts)}")
        for a in arts[:16]:
            print(f"    {a.upcoming:2} 场  {a.name[:30]:32} kind={a.kind}")

        print()
        print("=== 2) 按名字搜艺人 ===")
        for kw in ("恋音", "DigitalDuel", "月匙", "夏日"):
            hits = await service.list_artists(s, q=kw, with_upcoming=True)
            names = [f"{h.name}({h.upcoming})" for h in hits[:4]]
            print(f"  「{kw}」→ {len(hits)} 个: {names}")

        print()
        print("=== 3) 按艺人名筛场次（artist_q）===")
        for kw in ("恋音契约", "DigitalDuel", "Smoky Candy", "月匙"):
            q = service.OccurrenceQuery(artist_q=kw, page_size=50)
            items, total = await service.query_occurrences(s, q)
            titles = [i.event.title[:26] for i in items[:3]]
            print(f"  「{kw}」→ {total} 场")
            for t in titles:
                print(f"      {t}")

        print()
        print("=== 4) 对照：关键词 q（只搜标题）===")
        for kw in ("恋音契约", "夏日青空"):
            q = service.OccurrenceQuery(q=kw, page_size=50)
            _items, total = await service.query_occurrences(s, q)
            q2 = service.OccurrenceQuery(artist_q=kw, page_size=50)
            _i2, total2 = await service.query_occurrences(s, q2)
            print(f"  「{kw}」标题匹配 {total} 场 / 艺人匹配 {total2} 场")


asyncio.run(main())
