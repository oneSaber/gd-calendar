"""核对「日本音乐/偶像动画品牌」白名单的匹配边界。"""

from app.static_build import match_music_idol_franchise

# (文本, 期望命中的品牌)
CASES = [
    # 音乐 / 偶像动画：应命中
    ("BanG Dream!梦想协奏曲同人Only·终章如初", "BanG Dream!"),
    ("所谓正解？【哭泣少女乐队Only Live", "哭泣少女乐队"),
    ("轻音少女 only展", "轻音少女"),
    ("LoveLive! 同人only", "LoveLive"),
    ("偶像大师 only", "偶像大师"),
    ("孤独摇滚 同人only", "孤独摇滚"),
    ("赛马娘 only", "赛马娘"),
    ("少女歌剧 同人only", "少女歌剧"),
    ("D4DJ 同人only", "D4DJ"),
    ("プロセカ only", "プロセカ"),
    # 游戏 / 少年漫 / 特摄：不应命中（这些的 only 展是普通漫展）
    ("2026型月同人ONLY·Lostbelt", None),
    ("卡拉彼丘同人ONLY·S1", None),
    ("全职猎人同人only", None),
    ("明日方舟ONLY同人展【音波共振", None),
    ("第五人格ONLY同人茶话会", None),
    ("高达only同人展", None),
    ("箱中奇遇·重返未来:1999 同人ONLY展", None),
    ("阿特拉斯同人ONLY展", None),
    ("阴阳师only同人展·霜月繁花", None),
    ("特摄同人ONLY嘉年华 1st", None),
    ("金牌得主同人only", None),
    ("SAKO4明日方舟Only同人展", None),
    # 无关内容
    ("某乐队巡演广州站", None),
    ("", None),
]

ok = 0
for text, want in CASES:
    got = match_music_idol_franchise(text)
    good = got == want
    if good:
        ok += 1
    mark = "OK  " if good else "FAIL"
    print(f"  {mark} 命中={str(got):18} 期望={str(want):18} {text[:42]}")
print(f"  {ok}/{len(CASES)} 通过")
