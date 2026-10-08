"""诊断：koyo生诞祭応援 为什么没被放行。"""

from app.parsers import text_zh as t

TITLE = "⋆。+° koyo生诞祭应援 ༉+ ̊."
LINEUP = "Koyo_Digitalduel-1018生诞祭版"

c = t.classify(TITLE)
print(f"  title = {TITLE!r}")
print(f"  is_idol={c.is_idol} is_girl_band={c.is_girl_band} is_acg={c.is_acg} kind={c.kind}")
print(f"  tags={c.tags}")
print(f"  地偶词命中={[h for h in t.IDOL_HINTS if h.lower() in TITLE.lower()]}")
print(f"  乐队词命中={[h for h in t.BAND_HINTS if h.lower() in TITLE.lower()]}")
print(f"  展会判据命中={t._EXHIBITION_RE.search(TITLE)}")
print(f"  噪音判据命中={t._NON_EVENT_NOISE_RE.search(TITLE)}")
print()
print(f"  阵容 = {LINEUP!r}")
from app.normalize.artist_kb import ArtistKnowledgeBase
kb = ArtistKnowledgeBase()
e, how = kb.lookup(LINEUP)
print(f"  知识库查阵容整体: entry={e.name if e else None} how={how}")
# 子串命中测试
print(f"  含 'DigitalDuel'? {'digitalduel' in LINEUP.lower()}")
