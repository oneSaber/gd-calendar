"""把已确认的**免费/公共演出场地**固化进场地库。

## 为什么需要

免费场地（商场中庭、广场、公园、艺术馆）的演出不上售票平台，
所以抓取时常常连带场地信息一起丢。把这些场地**先登记进库**有两个好处：
  1. 免费场地筛选立刻有用（哪怕场次还没采到）
  2. 后续采集到同名场地时能命中已有记录（`venue.city + name` 唯一约束），
     场地分类不会每次重算

## 数据来源

`data/xhs_leads.md` 正文提取 + 前面几轮的实测证据
（地王广场见 SPEC 的「免费场地的特殊之处」）。**只登记有证据的**，
不确定的不写。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import Venue  # noqa: E402
from app.normalize.venue_type import classify_venue_type  # noqa: E402

# (名称, 城市, 证据)
SEED_VENUES: tuple[tuple[str, str, str], ...] = (
    (
        "地王广场",
        "广州",
        "小红书多条笔记：「广州地王广场也有地偶看？！」「地王地偶重开」"
        "「地王广场偶像活动开摊！」；微博逐字证据「TIME：26.9.17 "
        "VENUE：广州 地王广场」；小红书「广州国庆偶活速览」提到"
        "「9/30 Faelune Fairy 在地王广场 idol 空间」",
    ),
    (
        "地王广场 idol空间",
        "广州",
        "同上。地王广场内的专门演出空间（商场中庭），免费入场",
    ),
    (
        "智慧城广百广场",
        "广州",
        "小红书正文提取 3 次；库内已有 2 场同人展挂此场地（商场）",
    ),
    (
        "白云艺术广场",
        "广州",
        "小红书正文提取 2 次（公共艺术广场，通常免费）",
    ),
    (
        "MSH沉浸式音乐馆",
        "广州",
        "小红书正文提取 2 次（音乐馆，性质待确认，先按 studio 记）",
    ),
    (
        "您好艺术馆",
        "广州",
        "小红书正文提取 2 次（艺术馆，通常免费或低价）",
    ),
)


async def main() -> None:
    async with session_scope() as s:
        added = 0
        existed = 0
        for name, city, evidence in SEED_VENUES:
            row = (await s.execute(
                select(Venue).where(Venue.city == city, Venue.name == name)
            )).scalar_one_or_none()
            if row is not None:
                existed += 1
                print(f"    · 已存在: {name}（type={row.venue_type}）")
                continue
            kind, conf = classify_venue_type(name)
            s.add(Venue(
                name=name, city=city, venue_type=kind,
                # 备注里存证据，便于复核「这个场地怎么进库的」
                address_note=f"（种子登记，置信度 {conf}）{evidence}"[:400],
                official_url=None,
                status="active",
            ))
            added += 1
            print(f"    ✓ 新增 {name}（{city}）type={kind} conf={conf}")

        await s.flush()
        print()
        print(f"  新增 {added} 个，已存在 {existed} 个")

    async with session_scope() as s:
        rows = (await s.execute(
            select(Venue).where(Venue.name.in_([v[0] for v in SEED_VENUES]))
        )).scalars().all()
        print()
        print("  登记结果:")
        for v in rows:
            print(f"    [{v.venue_type or '-':10}] {v.name} ({v.city})")


asyncio.run(main())
