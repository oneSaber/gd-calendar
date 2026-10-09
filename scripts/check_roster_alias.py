"""验证：图鉴转录并入知识库后，简体/繁体两种写法都能命中。"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.normalize.artist_kb import SEED_ARTISTS, ArtistKnowledgeBase  # noqa: E402

print(f"  知识库条目: {len(SEED_ARTISTS)}")
kb = ArtistKnowledgeBase()

print()
print("  === 简繁别名匹配（关键：演出信息里是简体，图鉴是繁体） ===")
tests = [
    "娜娜捏口俱乐部", "娜娜捏口俱樂部", "恋音契约", "戀音契約",
    "山海誓约", "山海誓約", "月匙Moon-Key", "月匙 Moon-Key",
    "恋时青空", "恋时青空 AzuraToki", "空白扑克", "BlankPoker",
    "极夜NightFell", "極夜NightFell", "终焉蓝星", "水葬放课后",
    "DigitalDuel", "ReaLume", "空色轨迹", "NovaNight",
]
ok = 0
for t in tests:
    e, how = kb.lookup(t)
    good = e is not None
    if good:
        ok += 1
    mark = "OK  " if good else "FAIL"
    name = e.name if e else "—"
    print(f"    {mark} {t:22} → {name:24} [{how}]")
print(f"  {ok}/{len(tests)} 命中")
