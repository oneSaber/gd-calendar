"""核对噪音过滤与垂类判定的边界。

设计原则（反复调优后定下来的）：
  * 分类器只做**能证明**的判断。「同人only」在演出与展会之间毫无区分度
    （「金牌得主同人only」与「全职猎人同人only」标题格式完全相同），
    所以分类器不认它 —— 歧义交给「发布口径」：**有演出名单就收录**。
  * 「Only Live」是明确的演出写法，单独作为正向信号。
"""

from app.parsers import text_zh as t

# (文本, 期望 is_idol, 期望 is_acg, 说明)
CASES = [
    # --- 非演出噪音，必须排除 ---
    ("深圳天气剧透# 9日局地偶有零星小雨渐转多云", False, False, "「局地/偶有」断词"),
    ("阿特拉斯同人ONLY展", False, False, "游戏展"),
    ("全职猎人同人only", False, False, "少年漫，无演出证据"),
    ("明日方舟ONLY同人展【音波共振", False, False, "手游展"),
    ("Re:Comi! 本周情报", False, False, "情报贴"),
    ("koyo生诞祭应援", False, False, "应援贴"),
    ("箱中奇遇·重返未来:1999 同人ONLY展", False, False, "手游展"),
    ("第五人格ONLY同人茶话会", False, False, "手游茶话会"),
    ("高达only同人展", False, False, "机器人动画展"),
    ("阴阳师only同人展·霜月繁花", False, False, "手游展"),
    ("特摄同人ONLY嘉年华 1st", False, False, "特摄展"),
    ("金牌得主同人only", False, False, "运动漫，无演出证据（与上面格式相同）"),
    ("2026型月同人ONLY·Lostbelt", False, False, "FGO 游戏展"),
    # --- 明确是演出的，必须保留 ---
    ("零~夜时巫女一周年X京阿尼ONLY LIVE", True, True, "Only Live"),
    ("所谓正解？【哭泣少女乐队Only Live", True, True, "Only Live + 少女乐队"),
    ("PoP Star Idol Festival Vol.7", True, False, "idol"),
    ("留声RECORD音乐企划", True, False, "已核实厂牌"),
    ("呆呆Otori · 2026 生诞祭 ·阴雨天", True, False, "生诞祭"),
    ("比邻星球企划｜云响·回声", True, False, "已核实厂牌"),
    ("次元激战 ACG宿命对决 ｜ 湾岸电力 VS 夜一乐队", False, True, "ACG 演出，非偶像"),
    ("BanG Dream!梦想协奏曲同人Only·终章如初", False, True, "音乐动画品牌（ACG）"),
]

ok = 0
fails = []
for text, want_idol, want_acg, why in CASES:
    c = t.classify(text)
    good = (c.is_idol == want_idol and c.is_acg == want_acg)
    if good:
        ok += 1
    else:
        fails.append(text)
    mark = "OK  " if good else "FAIL"
    print(f"  {mark} 地偶={str(c.is_idol):5} ACG={str(c.is_acg):5}  {text[:40]:42} [{why}]")
print(f"  {ok}/{len(CASES)} 通过")
if fails:
    print("  失败:")
    for f in fails:
        print(f"    {f}")
