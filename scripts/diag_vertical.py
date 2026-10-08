"""诊断：垂类过滤为什么没生效。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.api import service  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.static_build import VERTICAL_FLAGS  # noqa: E402


async def main() -> None:
    async with session_scope() as session:
        items, total = await service.query_occurrences(
            session,
            service.OccurrenceQuery(
                page=1, page_size=10_000, include_finished=True, exclude_other=False
            ),
        )
    print(f"  取到 {len(items)} 条（库内 {total}）")
    it = items[0]
    print(f"  item 类型: {type(it).__name__}")
    print(f"  item.event 类型: {type(it.event).__name__}")
    print(f"  event 字段: {sorted(it.event.model_dump().keys())}")
    print()
    keep = []
    for i in items:
        ev = i.event
        hits = {f: bool(getattr(ev, f, False)) for f in VERTICAL_FLAGS}
        if any(hits.values()):
            keep.append((i, hits))
    print(f"  命中垂类的场次: {len(keep)}")
    for i, hits in keep[:20]:
        tags = [k for k, v in hits.items() if v]
        print(f"    [{'+'.join(tags):22}] {i.start_at:%Y-%m-%d} {i.event.title[:36]}")
    print()
    print("  getattr 直接试：")
    ev = items[0].event
    for f in VERTICAL_FLAGS:
        print(f"    getattr(ev, {f!r}, 'MISSING') = {getattr(ev, f, 'MISSING')!r}")


asyncio.run(main())
