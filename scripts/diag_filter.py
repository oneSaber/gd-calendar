"""诊断：展会过滤误伤了哪些真实用例。"""

from app.parsers import text_zh as t

CASES = [
    "✦ REX狂想夜 邓晴桦&鸣岐 生日SP 📅 2026.09.24 🕖 19:00 入场 📍 广州市越秀区北京路纵一咖啡（2F）🎫 门票：无料入场",
    "广州·金牌得主同人only",
    "全职猎人同人only",
    "阿特拉斯同人ONLY展",
    "明日方舟ONLY同人展【音波共振",
    "深圳天气剧透# 9日局地偶有零星小雨渐转多云",
]

for s in CASES:
    c = t.classify(s)
    ex = t._EXHIBITION_RE.search(s)
    noise = t._NON_EVENT_NOISE_RE.search(s)
    weather = t._WEATHER_RE.search(s)
    print(f"  {s[:44]}")
    print(f"     is_idol={c.is_idol} is_acg={c.is_acg} kind={c.kind} tags={c.tags}")
    print(f"     展会命中={ex.group(0) if ex else None}  噪音={noise.group(0) if noise else None}  天气={weather.group(0) if weather else None}")
    print(f"     地偶词={[h for h in t.IDOL_HINTS if h.lower() in s.lower()]}")
