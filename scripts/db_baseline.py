"""打印库内基线统计（采集前后对比用）。"""

from __future__ import annotations

import sqlite3
import sys

DB = r"D:\AI\gd-calendar\data\gd_calendar.db"


def main() -> None:
    c = sqlite3.connect(DB)
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731

    print("  场次", q("SELECT COUNT(*) FROM occurrence"),
          "| 活动", q("SELECT COUNT(*) FROM event"))
    print("  艺人", q("SELECT COUNT(*) FROM artist"),
          "| 场地", q("SELECT COUNT(*) FROM venue"))
    print("  垂类: 地偶", q("SELECT COUNT(*) FROM event WHERE is_idol=1"),
          "女子乐队", q("SELECT COUNT(*) FROM event WHERE is_girl_band=1"),
          "ACG", q("SELECT COUNT(*) FROM event WHERE is_acg=1"))
    print("  未来场次(>=2026-10-09)",
          q("SELECT COUNT(*) FROM occurrence WHERE start_at>='2026-10-09'"))
    print()
    print("  各来源场次:")
    for code, n in c.execute(
        "SELECT s.code, COUNT(DISTINCT o.id) FROM occurrence o "
        "JOIN occurrence_source os ON os.occurrence_id=o.id "
        "JOIN source s ON s.id=os.source_id GROUP BY s.code ORDER BY 2 DESC"
    ):
        print(f"    {code:12} {n}")
    print()
    print("  演员 kind 分布:")
    for kind, n in c.execute(
        "SELECT kind, COUNT(*) FROM artist GROUP BY kind ORDER BY 2 DESC"
    ):
        print(f"    {kind:14} {n}")
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        return


main()
