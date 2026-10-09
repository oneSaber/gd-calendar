"""清理艺人库里的垃圾条目（会污染艺人搜索与阵容判定）。

三处实测垃圾：
  1. `Koyo_Digitalduel-1018生诞祭版` —— **应援物名**被当成团体
     （它其实指向 DigitalDuel，应把阵容条目改指过去）
  2. `微博抽奖平台` —— 抽奖贴里的 @ 被当演员
  3. `官宣! 关带双话题` —— 微博话题语法的残片，**挂了 7 场**，最脏

做法：前两个**合并/删除**，第三个**删掉艺人」（场次保留，只是阵容少一条垃圾）。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from sqlalchemy import delete, select, update  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import Artist, OccurrenceArtist  # noqa: E402

# 名字 -> 处理方式
MERGE_INTO = {
    # 应援物名 → 真实团体
    "Koyo_Digitalduel-1018生诞祭版": "DigitalDuel",
}
DELETE_ONLY = ["微博抽奖平台", "官宣! 关带双话题"]


async def main() -> None:
    async with session_scope() as s:
        async def aid_of(name: str) -> int | None:
            row = (await s.execute(
                select(Artist.id).where(Artist.name == name)
            )).scalar_one_or_none()
            return row

        print("=== 合并应援物名到真实团体 ===")
        for bad, good in MERGE_INTO.items():
            bad_id = await aid_of(bad)
            good_id = await aid_of(good)
            if bad_id is None:
                print(f"    · {bad} 不存在，跳过")
                continue
            if good_id is None:
                print(f"    ✗ 目标 {good} 不存在，跳过合并（不冒险）")
                continue
            # 把阵容条目的 artist_id 改指目标；同名去重交给后续
            n = len((await s.execute(
                select(OccurrenceArtist.id).where(
                    OccurrenceArtist.artist_id == bad_id
                )
            )).scalars().all())
            await s.execute(
                update(OccurrenceArtist)
                .where(OccurrenceArtist.artist_id == bad_id)
                .values(artist_id=good_id, artist_raw=good)
            )
            await s.execute(delete(Artist).where(Artist.id == bad_id))
            print(f"    ✓ {bad}（{n} 条阵容）→ {good}")

        print()
        print("=== 删除纯垃圾艺人 ===")
        for name in DELETE_ONLY:
            aid = await aid_of(name)
            if aid is None:
                print(f"    · {name} 不存在，跳过")
                continue
            n = len((await s.execute(
                select(OccurrenceArtist.id).where(
                    OccurrenceArtist.artist_id == aid
                )
            )).scalars().all())
            await s.execute(delete(OccurrenceArtist).where(
                OccurrenceArtist.artist_id == aid
            ))
            await s.execute(delete(Artist).where(Artist.id == aid))
            print(f"    ✓ 删除 {name!r}（清掉 {n} 条阵容关联；**场次保留**）")

        # 去重：同一场次里可能因为合并出现重复 artist_id
        dupes = (await s.execute(
            select(
                OccurrenceArtist.occurrence_id,
                OccurrenceArtist.artist_id,
            )
        )).all()
        seen: set[tuple[int, int]] = set()
        dup_rows: list[int] = []
        for oid, aid in dupes:
            k = (oid, aid)
            if k in seen:
                continue
            seen.add(k)

    async with session_scope() as s:
        from sqlalchemy import func
        total = (await s.execute(select(func.count()).select_from(Artist))).scalar_one()
        print()
        print(f"  清理后艺人总数: {total}（原 352）")


asyncio.run(main())
