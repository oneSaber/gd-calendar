"""核对地偶判定：具体厂牌名 vs 泛词。"""

from app.parsers import text_zh as t

CASES = [
    ("留声RECORD音乐企划", True),
    ("留声record音乐企划", True),
    ("留声 RECORD 音乐企划", True),
    ("比邻星球企划｜云响·回声", True),
    ("马赫mood x 杜逸风「糟糕的日子里」5周年特别企划专场广州站", False),
    ("某某音乐企划", False),
    ("地偶定期公演 vol.3", True),
    ("呆呆Otori · 2026 生诞祭", True),
]

ok = 0
for text, want in CASES:
    got = t.classify(text).is_idol
    mark = "OK  " if got == want else "FAIL"
    if got == want:
        ok += 1
    print(f"  {mark} is_idol={str(got):5} 期望={str(want):5}  {text[:44]}")
print(f"  {ok}/{len(CASES)} 通过")
