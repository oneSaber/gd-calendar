"""验证新增的搜索 API（跑在真实服务上）。"""

from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8000"


def show(label: str, url: str, pick) -> None:
    try:
        r = httpx.get(url, timeout=25)
        d = r.json()
        items = d.get("items") or []
        print(f"  {label}")
        print(f"    HTTP {r.status_code}  total={d.get('total', len(items))}  items={len(items)}")
        for it in items[:5]:
            print(f"      {pick(it)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}  ✗ {str(exc)[:90]}")


print("=== 1) 艺人自动补全（有未来场次，按场次排序）===")
show("全部", f"{BASE}/api/artists?with_upcoming=1&limit=8",
     lambda i: f"{i.get('upcoming'):2} 场  {i.get('name')[:28]:30} {i.get('kind')}")

print()
print("=== 2) 艺人名字搜索 ===")
for kw in ("恋音", "月匙", "DigitalDuel"):
    show(f"q={kw}", f"{BASE}/api/artists?with_upcoming=1&q={kw}",
         lambda i: f"{i.get('upcoming'):2} 场  {i.get('name')}")

print()
print("=== 3) 按艺人名筛场次（artist_q，搜阵容）===")
for kw in ("恋音契约", "夏日青空", "DigitalDuel"):
    show(f"artist_q={kw}",
         f"{BASE}/api/occurrences?artist_q={kw}&from=2026-09-01&to=2027-01-31",
         lambda i: f"{i['event']['title'][:44]}")

print()
print("=== 4) 关键词 q（只搜标题）对照 ===")
for kw in ("恋音契约", "夏日青空"):
    try:
        a = httpx.get(f"{BASE}/api/occurrences?q={kw}&from=2026-09-01&to=2027-01-31", timeout=25).json()
        b = httpx.get(f"{BASE}/api/occurrences?artist_q={kw}&from=2026-09-01&to=2027-01-31", timeout=25).json()
        print(f"  「{kw}」标题 {a.get('total', len(a.get('items', [])))} 场 / 阵容 {b.get('total', len(b.get('items', [])))} 场")
    except Exception as exc:  # noqa: BLE001
        print(f"  「{kw}」✗ {str(exc)[:60]}")

print()
print("=== 5) 日期范围筛选 ===")
for fr, to in [("2026-10-01", "2026-10-31"), ("2026-11-01", "2026-11-30")]:
    try:
        d = httpx.get(f"{BASE}/api/occurrences?from={fr}&to={to}&page_size=200", timeout=25).json()
        print(f"  {fr} ~ {to} → {d.get('total', len(d.get('items', [])))} 场")
    except Exception as exc:  # noqa: BLE001
        print(f"  {fr} ✗ {str(exc)[:60]}")
