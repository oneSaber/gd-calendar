"""实测：用微博搜索发现「发演出情报的账号」（供艺人库与频道表扩充）。

维护者要求：进一步搜索相关演出信息的团体，填充艺人库，
然后反向搜索对应的小红书和微博账号，交叉获取信息。

本脚本只做**第一步：发现账号**。产出候选 UID + 昵称，供人工/后续逻辑决定
哪些进频道表。
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.weibo import WeiboCollector, discover_accounts  # noqa: E402

# 候选关键词：围绕「谁在发布演出情报」
KEYWORDS = [
    "地王广场",
    "广州地偶图鉴",
    "偶活速览",
]


async def main() -> None:
    col = WeiboCollector()
    for kw in KEYWORDS:
        try:
            payloads, _occ = await col._fetch(kw, wait=15.0)
        except Exception as exc:  # noqa: BLE001
            print(f"  【{kw}】失败：{str(exc)[:70]}")
            continue

        accounts: dict[str, str] = {}
        for p in payloads:
            for a in discover_accounts(p):
                uid = str(a.get("external_id") or "").replace("uid:", "")
                name = str(a.get("display_name") or "")
                if uid and uid not in accounts:
                    accounts[uid] = name
        print(f"  【{kw}】发现 {len(accounts)} 个账号")
        for uid, name in list(accounts.items())[:14]:
            print(f"      uid={uid:14} {name[:28]}")
        print()


asyncio.run(main())
