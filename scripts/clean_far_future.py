"""删除日期荒谬的场次（微博补年份导致的 2027 场次）。

⚠️ 这些不是真实演出：微博文案里日期没有年份，解析器补当前年时把已过的日子
推到了下一年，产生「2027-06-07 深圳天气剧透」这种记录。
`_post_to_occurrence` 已加远期日期检查（>120 天丢弃），这里清理历史残留。

只删 occurrence（及其关联的 occurrence_source / occurrence_artist / ticket_tier），
**不删 event** —— event 可能被别的 occurrence 引用。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from sqlalchemy import delete, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import (  # noqa: E402
    Occurrence,
    OccurrenceArtist,
    OccurrenceSource,
    TicketTier,
)

# 判定阈值：晚于这个日期的场次都认为是解析错误
CUTOFF = "2027-01-01"


async def main() -> None:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Occurrence.id, Occurrence.start_at, Occurrence.city)
                .where(Occurrence.start_at >= CUTOFF)
                .order_by(Occurrence.start_at)
            )
        ).all()
        print(f"  待删除 {len(rows)} 条：")
        ids = []
        for oid, start, city in rows:
            print(f"    id={oid} {start} {city}")
            ids.append(oid)
        if not ids:
            return
        for model in (OccurrenceArtist, OccurrenceSource, TicketTier):
            await s.execute(delete(model).where(model.occurrence_id.in_(ids)))
        await s.execute(delete(Occurrence).where(Occurrence.id.in_(ids)))

    async with session_scope() as s:
        left = (
            await s.execute(select(Occurrence.id).where(Occurrence.start_at >= CUTOFF))
        ).scalars().all()
        print(f"  清理后剩余远期场次：{len(left)}")


asyncio.run(main())
