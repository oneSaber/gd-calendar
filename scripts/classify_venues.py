"""给库里所有场地补 `venue_type`（原来 148 个全是 livehouse 占位值）。

用法：
    python scripts/classify_venues.py --dry-run   # 只看分布
    python scripts/classify_venues.py             # 落库
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

sys.path.insert(0, r"D:\AI\gd-calendar")

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import Venue  # noqa: E402
from app.normalize.venue_type import (  # noqa: E402
    FREE_TYPES,
    classify_venue_type,
    is_free_venue,
)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    async with session_scope() as s:
        venues = (await s.execute(select(Venue))).scalars().all()
        print(f"  场地 {len(venues)} 个")

        before = Counter((v.venue_type or "（空）") for v in venues)
        print("  分类前:", dict(before))
        print()

        dist: Counter[str] = Counter()
        free_list: list[tuple[str, str, str]] = []
        unknown: list[tuple[str, str]] = []
        for v in venues:
            kind, conf = classify_venue_type(v.name, v.address)
            dist[kind] += 1
            if is_free_venue(kind):
                free_list.append((v.name, v.city, kind))
            if kind == "unknown":
                unknown.append((v.name, v.city))
            if not args.dry_run:
                v.venue_type = kind

        print("  分类后:", dict(dist))
        print()
        print(f"  === 免费场地（{', '.join(sorted(FREE_TYPES))} 类，共 {len(free_list)} 个）===")
        for name, city, kind in sorted(free_list, key=lambda x: x[2])[:30]:
            print(f"    [{kind:6}] {name[:52]} ({city})")
        print()
        print(f"  === 判不出来（{len(unknown)} 个，保留 unknown 不猜）===")
        for name, city in unknown[:14]:
            print(f"    {name[:56]} ({city})")

        if not args.dry_run:
            await s.flush()
            print()
            print("  ✅ 已落库")


asyncio.run(main())
