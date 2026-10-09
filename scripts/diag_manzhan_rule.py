"""诊断：「漫展」规则矛盾。

现象：`漫展` 同时在 `ACG_HINTS`（算 ACG）和 `_EXHIBITION_RE`（当非演出噪音）里。
实测「东莞萤火虫漫展 地下偶像」→ is_idol=False, is_acg=False（两个标记都没了）。

目标要求「包含漫展」，所以漫展里的**演出**不该被当噪音清掉。
本脚本先产出证据，再决定怎么改。
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.parsers import text_zh as t  # noqa: E402

CASES = [
    # 漫展 + 明确演出信号（应该保留）
    "东莞萤火虫漫展 地下偶像舞台",
    "萤火虫漫展 地偶演出",
    "CICF 地下偶像专场",
    "AGF 舞台 乐队",
    "漫展 anisong 演唱会",
    # 漫展本身（不是演出 → 不该进日历，但 ACG 标记可保留）
    "广州XX漫展",
    "萤火虫动漫游戏嘉年华",
    # 同人展（既有 ACG 属性，又可能含演出）
    "BanG Dream!同人Only",
    "全职猎人同人only",
]

print("  === 关键词归属检查 ===")
print(f"  「漫展」在 ACG_HINTS: {'漫展' in t.ACG_HINTS}")
print(f"  「漫展」在 _EXHIBITION_RE: {bool(t._EXHIBITION_RE.search('漫展'))}")
print(f"  「动漫」在 ACG_HINTS: {'动漫' in t.ACG_HINTS}")
print(f"  「萤火虫」在 ACG_HINTS: {'萤火虫' in t.ACG_HINTS}")
print()

print("  === 逐例判定 ===")
for s in CASES:
    c = t.classify(s)
    ex = bool(t._EXHIBITION_RE.search(s))
    neg = bool(t._NON_EVENT_NOISE_RE.search(s))
    print(f"    is_idol={str(c.is_idol):5} is_acg={str(c.is_acg):5} "
          f"展会={str(ex):5} 噪音={str(neg):5}  {s}")
