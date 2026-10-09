"""验证场地类型筛选（免费场地 / 漫展场馆）。"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.api import service  # noqa: E402
from app.db import session_scope  # noqa: E402


async def main() -> None:
    async with session_scope() as s:
        print("=== 按场地类型筛场次 ===")
        for vt in ("free", "livehouse", "convention", "theater", "mall", "park"):
            q = service.OccurrenceQuery(venue_type=vt, page_size=200)
            items, total = await service.query_occurrences(s, q)
            sample = [i.event.title[:30] for i in items[:2]]
            print(f"  {vt:12} → {total:3} 场   {sample}")

        print()
        print("=== 免费场地的具体场次 ===")
        q = service.OccurrenceQuery(venue_type="free", page_size=50)
        items, total = await service.query_occurrences(s, q)
        for it in items[:12]:
            vname = (it.venue.name if it.venue else "?")[:34]
            print(f"  {it.start_at.date()}  {vname:36} {it.event.title[:30]}")


asyncio.run(main())
