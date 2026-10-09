"""实测微博候选搜索词的产出量（决定关键词表怎么扩）。

⚠️ 每个关键词 ≈33 秒（渲染搜索页 + 拦截 XHR），所以这个词表要精挑。
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.weibo import WeiboCollector  # noqa: E402

# 候选：地偶垂类 + ACG 乐队 + 免费场地（地王广场这类）
CANDIDATES = [
    # 现状（对照基线）
    "广州地偶",
    # 场地维度：免费演出场地
    "地王广场",
    "广州 地王广场",
    "地王广场 偶像",
    # ACG 乐队
    "广州 ACG 乐队",
    "广州 同人 演出",
    # 免费 / 公演
    "广州 免费 公演",
    "广州 偶像 公演",
    # 其他城市的地偶
    "珠海 地偶",
    "东莞 地偶",
]


async def main() -> None:
    col = WeiboCollector()
    print(f"  候选关键词 {len(CANDIDATES)} 个，预计耗时 {len(CANDIDATES) * 33 / 60:.1f} 分钟")
    print()
    for kw in CANDIDATES:
        t0 = time.monotonic()
        try:
            occ = await col.collect_search(kw, wait=14.0)
        except Exception as exc:  # noqa: BLE001
            print(f"  【{kw:16}】失败：{str(exc)[:60]}")
            continue
        dt = time.monotonic() - t0
        titles = [ (o.title_display or "")[:30] for o in occ[:2] ]
        print(f"  【{kw:16}】{len(occ):3} 条  {dt:5.1f}s")
        for t in titles:
            print(f"        · {t}")


asyncio.run(main())
