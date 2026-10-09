"""诊断艺人库里的垃圾条目（会污染艺人搜索）。"""

from __future__ import annotations

import asyncio
import re
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import Artist, Occurrence, OccurrenceArtist  # noqa: E402

# 明显不是艺人的模式
JUNK = [
    re.compile(r"官宣|话题|抽奖|转发|关注|@"),
    re.compile(r"^[\d\W_]+$"),
    re.compile(r"生诞祭版|应援|周边|特典|限定版|ver\.?\s*\d", re.I),
]


async def main() -> None:
    async with session_scope() as s:
        arts = (await s.execute(select(Artist))).scalars().all()
        print(f"  艺人总数 {len(arts)}")
        junk = [a for a in arts if any(p.search(a.name or "") for p in JUNK)]
        print(f"  疑似垃圾 {len(junk)} 个：")
        for a in junk:
            n = len(
                (await s.execute(
                    select(OccurrenceArtist.occurrence_id).where(
                        OccurrenceArtist.artist_id == a.id
                    )
                )).scalars().all()
            )
            print(f"    [{a.kind:11}] {a.name[:44]!r}  {n} 场")

        print()
        print("  === 查「夏日青空」相关艺人 ===")
        hits = (await s.execute(
            select(Artist).where(Artist.name.ilike("%夏日%"))
        )).scalars().all()
        for a in hits:
            print(f"    [{a.kind}] {a.name!r} aliases={a.aliases}")

        print()
        print("  === 阵容里含「夏日青空」的原始写法 ===")
        raws = (await s.execute(
            select(OccurrenceArtist.artist_raw).where(
                OccurrenceArtist.artist_raw.ilike("%夏日青空%")
            )
        )).scalars().all()
        print(f"    {sorted(set(r for r in raws if r))}")


asyncio.run(main())
