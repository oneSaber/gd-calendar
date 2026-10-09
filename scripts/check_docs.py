"""文档一致性检查：SPEC.md / README.md 里引用的数字是否与实际一致。

## 为什么需要它

文档里的数字（场次数、艺人数、垂类数、测试数）会随采集变化而**过期**。
这个脚本把「文档声称的」与「数据库实际的」对照，避免发布失真的文档。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(r"D:\AI\gd-calendar")
DB = ROOT / "data" / "gd_calendar.db"


def main() -> None:
    c = sqlite3.connect(DB)
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731

    actual = {
        "场次": q("SELECT COUNT(*) FROM occurrence"),
        "活动": q("SELECT COUNT(*) FROM event"),
        "艺人": q("SELECT COUNT(*) FROM artist"),
        "场地": q("SELECT COUNT(*) FROM venue"),
        # 口径 = event 表里的垂类活动数（event 表天然去重）
        "垂类活动": q(
            "SELECT COUNT(*) FROM event "
            "WHERE is_idol=1 OR is_girl_band=1 OR is_acg=1"
        ),
        "待复核": q("SELECT COUNT(*) FROM review_task"),
    }
    published = len(
        json.loads(
            (ROOT / "docs/data/occurrences.json").read_text(encoding="utf-8")
        )["items"]
    )

    print("=== 数据库实际 ===")
    for k, v in actual.items():
        print(f"  {k:8} {v}")
    print(f"  {'发布场次':8} {published}（静态站）")

    # 测试函数数（不含参数化展开，仅作数量级参照）
    total_tests = 0
    for f in (ROOT / "tests").glob("test_*.py"):
        total_tests += len(
            re.findall(
                r"^\s*(?:async )?def test_", f.read_text(encoding="utf-8"), re.M
            )
        )
    print(f"  {'测试函数':8} {total_tests}（不含参数化展开）")

    print()
    print("=== 文档里的数字核对 ===")
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    checks = [
        ("SPEC 场次 426", "426" in spec, actual["场次"], 426),
        ("SPEC 活动 430", "430" in spec, actual["活动"], 430),
        ("SPEC 艺人 344", "344" in spec, actual["艺人"], 344),
        ("SPEC 场地 148", "148" in spec, actual["场地"], 148),
        ("SPEC 垂类 22", "22" in spec, actual["垂类活动"], 22),
        ("SPEC 发布 18", "**18**" in spec, published, 18),
        ("SPEC 复核 52", "52" in spec, actual["待复核"], 52),
        ("README 提到 SPEC.md", "SPEC.md" in readme, 1, 1),
        ("README 提到 DATASOURCES", "DATASOURCES" in readme, 1, 1),
    ]
    bad = 0
    for label, present, act, claimed in checks:
        ok = present and act == claimed
        if not ok:
            bad += 1
        print(f"  {'OK  ' if ok else 'FAIL'} {label}  (实际={act}, 文档={claimed})")

    print()
    if bad:
        print(f"  ⚠️ {bad} 项不一致 —— 请更新文档后再发布")
    else:
        print("  ✅ 文档数字与数据库一致")


main()
