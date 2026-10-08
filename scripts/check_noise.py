"""核对噪音过滤与垂类判定的边界。"""

from app.parsers import text_zh as t

# (文本, 期望 is_idol, 期望 is_acg)
CASES = [
    # 非演出噪音：必须全部排除
    ("深圳天气剧透# 9日局地偶有零星小雨渐转多云", False, False),
    ("阿特拉斯同人ONLY展", False, False),
    ("全职猎人同人only", False, False),
    ("明日方舟ONLY同人展【音波共振", False, False),
    ("Re:Comi! 本周情报", False, False),
    ("koyo生诞祭应援", False, False),
    ("箱中奇遇·重返未来:1999 同人ONLY展", False, False),
    # 真演出：必须保留
    ("零~夜时巫女一周年X京阿尼ONLY LIVE", True, True),
    ("次元激战 ACG宿命对决 ｜ 湾岸电力 VS 夜一乐队", False, True),
    ("所谓正解？【哭泣少女乐队Only Live", True, True),
    ("PoP Star Idol Festival Vol.7", True, False),
    ("留声RECORD音乐企划", True, False),
    ("呆呆Otori · 2026 生诞祭 ·阴雨天", True, False),
    ("比邻星球企划｜云响·回声", True, False),
]

ok = 0
for text, want_idol, want_acg in CASES:
    c = t.classify(text)
    good = (c.is_idol == want_idol and c.is_acg == want_acg)
    if good:
        ok += 1
    mark = "OK  " if good else "FAIL"
    print(f"  {mark} 地偶={str(c.is_idol):5} ACG={str(c.is_acg):5} "
          f"期望 地偶={str(want_idol):5} ACG={str(want_acg):5}  {text[:40]}")
print(f"  {ok}/{len(CASES)} 通过")
