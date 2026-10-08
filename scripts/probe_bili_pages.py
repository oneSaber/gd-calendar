"""实测：B站会员购按城市能翻多少页（决定 max_pages 上限）。

用采集器自己的 CDP 通道跑，因为接口直调会被拒（需要浏览器上下文）。
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.collectors.bilibili import (  # noqa: E402
    area_code_for,
    fetch_show_projects,
    json_payloads,
    parse_listv2_pages,
)

CITIES = ["广州", "深圳", "珠海", "佛山", "东莞"]


def main() -> None:
    print("  area 码表:", {c: area_code_for(c) for c in CITIES})
    print()
    for city in CITIES:
        for pages in (1, 3, 6):
            hits = fetch_show_projects(city, wait=6.0, max_pages=pages)
            occ = parse_listv2_pages(json_payloads(hits), city)
            print(f"  {city:4} max_pages={pages}  拦截 {len(hits):2} 次  解析 {len(occ):3} 条")
        print()


main()
