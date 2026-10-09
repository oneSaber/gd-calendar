"""验证场地类型 API（跑在真实服务上）。"""

from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8000"
RANGE = "from=2026-09-01&to=2027-01-31"

print("=== 场地类型筛选 ===")
for vt in ("", "free", "livehouse", "convention", "theater", "mall", "park", "campus"):
    url = f"{BASE}/api/occurrences?{RANGE}" + (f"&venue_type={vt}" if vt else "")
    try:
        d = httpx.get(url, timeout=25).json()
        items = d.get("items") or []
        total = d.get("total", len(items))
        label = vt or "(全部)"
        sample = ""
        if items:
            v = (items[0].get("venue") or {}).get("name") or "?"
            sample = f"   例: {v[:30]}"
        print(f"  {label:11} → {total:4} 场{sample}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {vt:11} ✗ {str(exc)[:70]}")

print()
print("=== 免费场地的具体内容 ===")
d = httpx.get(f"{BASE}/api/occurrences?{RANGE}&venue_type=free&page_size=50", timeout=25).json()
for it in (d.get("items") or [])[:12]:
    v = (it.get("venue") or {}).get("name") or "?"
    city = it.get("city") or ""
    print(f"  {str(it.get('start_at'))[:10]}  {city:4} {v[:34]:36} {it['event']['title'][:30]}")
