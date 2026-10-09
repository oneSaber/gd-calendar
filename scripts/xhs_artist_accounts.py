"""反向搜索：用艺人库里的团体名去小红书找**官方账号**。

维护者要求（第 3 点）：进一步搜索相关演出信息的团体，填充艺人库，
然后**反向搜索对应的小红书和微博**，交叉获取信息。

做法：对每个已知垂类团体，在小红书搜它的名字，统计哪些账号在发它的内容 ——
持续发布的那个通常就是官方号或核心粉丝号，可用于后续按账号追更。
"""

from __future__ import annotations

import collections
import json
import sys
import time

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.xhs import XhsBrowser, collect_search, nav_visible  # noqa: E402
from app.normalize.artist_kb import SEED_ARTISTS  # noqa: E402

# 每搜一个团体 ≈12 秒；先取知识库里最活跃的一批
LIMIT = 12


def main() -> None:
    names = [e.name for e in SEED_ARTISTS if e.kind in (
        "idol_group", "girl_band", "acg_unit"
    )][:LIMIT]
    print(f"  反向搜索 {len(names)} 个团体（每个 ≈12 秒）")
    print()

    br = XhsBrowser()
    result: dict[str, dict] = {}
    try:
        for name in names:
            try:
                notes, ok = collect_search(br, name, wait=8.0)
            except Exception as exc:  # noqa: BLE001
                print(f"  【{name}】失败：{str(exc)[:60]}")
                continue
            if not ok:
                print("  ⚠ 未登录，中止")
                break
            # 统计作者频次
            authors = collections.Counter(
                n.author for n in notes if n.author
            )
            top = authors.most_common(3)
            result[name] = {
                "notes": len(notes),
                "authors": top,
                "titles": [n.title[:40] for n in notes[:5]],
            }
            print(f"  【{name}】{len(notes)} 条")
            for a, c in top:
                print(f"        {c:2} 条  {a}")
            for t in result[name]["titles"][:3]:
                print(f"        · {t}")
            print()
    finally:
        br.close()

    out = sys.argv[1] if len(sys.argv) > 1 else "data/xhs_artist_accounts.json"
    from pathlib import Path

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  已保存: {out}")


main()
