"""静态站筛选的**纯函数**验证（不依赖 __STATIC__ 探测）。

## 为什么改这种方式

上一版脚本直接访问 `http://127.0.0.1:8096/docs/index.html`，
但 `__STATIC__` 只在 `file:` 协议或 `.github.io` 域名下为真 ——
所以本地 HTTP 下前端走的是**后端模式**（去请求 /api），页面当然是空的。
**是测试写错了，不是功能坏了。**

正确做法：直接测 `static-data.js` 的 `filterOccurrences`
（静态站与后端语义一致的那层），它已有 Node 测试覆盖。
这里用真实快照数据再跑一遍，确认线上快照能筛出预期结果。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\AI\gd-calendar")
SNAP = ROOT / "docs" / "data" / "occurrences.json"

data = json.loads(SNAP.read_text(encoding="utf-8"))
items = data.get("items") or []
print(f"  线上快照: {len(items)} 场次")

# 场地类型分布（从快照里看）
from collections import Counter  # noqa: E402

dist = Counter(((it.get("venue") or {}).get("venue_type") or "（无）") for it in items)
print(f"  场地类型分布: {dict(dist)}")

free = [it for it in items
        if ((it.get("venue") or {}).get("venue_type") or "") in ("mall", "park", "campus")]
print(f"  免费场地场次: {len(free)}")
for it in free:
    v = (it.get("venue") or {}).get("name") or "?"
    print(f"    {str(it.get('start_at'))[:10]}  {v[:34]:36} {it['event']['title'][:30]}")

# 用 Node 跑真正的 filterOccurrences（与前端同一份代码）
js = """
import { readFileSync } from 'node:fs';
const { filterOccurrences } = await import('file:///%s/app/web/js/static-data.js');
const snap = JSON.parse(readFileSync('%s', 'utf-8'));
const all = snap.items || [];
const p = (o) => JSON.stringify(o);
console.log(p({ total: all.length }));
console.log(p({ free: filterOccurrences(all, { venue_type: 'free', include_finished: true }).length }));
console.log(p({ livehouse: filterOccurrences(all, { venue_type: 'livehouse', include_finished: true }).length }));
console.log(p({ artist_lianyin: filterOccurrences(all, { artist_q: '恋音', include_finished: true }).length }));
console.log(p({ dates_oct: filterOccurrences(all, { from: '2026-10-01', to: '2026-10-31', include_finished: true }).length }));
""" % (str(ROOT).replace("\\", "/"), str(SNAP).replace("\\", "/"))

out = subprocess.run(
    ["node", "--input-type=module", "-e", js],
    capture_output=True, text=True, encoding="utf-8",
)
if out.returncode != 0:
    print("  ✗ Node 执行失败:", out.stderr[:400])
    sys.exit(1)
print()
print("  === 静态站本地过滤（与前端同一份代码）===")
for line in out.stdout.strip().splitlines():
    print("   ", line)
