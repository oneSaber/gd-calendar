"""清理库里被污染的场地记录（微博「…全文」截断标记被当成场地）。

做法：把这些场地下挂的 occurrence.venue_id 置空（保留场次本身，只是没场地），
再删掉场地行。**不删场次** —— 场次信息本身可能是有效的，只是场地解析错了。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from sqlalchemy import delete, select, update  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import Occurrence, Venue  # noqa: E402
from app.parsers import text_zh as tz  # noqa: E402


async def main() -> None:
    async with session_scope() as s:
        venues = (await s.execute(select(Venue))).scalars().all()
        bad = [v for v in venues if tz.clean_venue(v.name) is None or tz.clean_venue(v.name) != v.name]
        # 只处理「清洗后为空」的（明确是噪音）；改名的先不动，避免误伤
        noise = [v for v in venues if tz.clean_venue(v.name) is None]

        print(f"  场地总数 {len(venues)}，明确噪音 {len(noise)} 个：")
        for v in noise:
            n = (
                await s.execute(
                    select(Occurrence.id).where(Occurrence.venue_id == v.id)
                )
            ).scalars().all()
            print(f"    【{v.name}】({v.city}) 下挂 {len(n)} 场 → 置空场地后删除")
            if n:
                await s.execute(
                    update(Occurrence)
                    .where(Occurrence.venue_id == v.id)
                    .values(venue_id=None)
                )
            await s.execute(delete(Venue).where(Venue.id == v.id))

        if bad and len(bad) > len(noise):
            print(f"  另有 {len(bad) - len(noise)} 个可被清洗改名的场地（未处理，避免误伤）")

    async with session_scope() as s:
        left = (await s.execute(select(Venue))).scalars().all()
        print(f"  清理后场地数：{len(left)}")


asyncio.run(main())
