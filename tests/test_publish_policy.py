"""静态站发布口径的测试：**必须能证明这是演出**，不能只凭标题像演出。

维护者定的规则（实测验证过它比标题可靠）：
    「检查演出名单，如果名单有团体就收录」

依据：秀动的列表页**只在真有演出人员时才给阵容**，所以「阵容为空」是强信号。
实测对照（都在下面固化成用例）：
    * 呆呆Otori 生诞祭        → 阵容 4 个地偶团体  ✅
    * 零~夜时巫女×京阿尼 ONLY → 阵容 4 个团体      ✅
    * 全职猎人同人only        → 阵容空            ❌
    * 卡拉彼丘同人ONLY·S1     → 阵容空            ❌
    * 金牌得主同人only        → 阵容空            ❌

例外：已核实的具体团体/企划名（artist_kb 的 `_IDOL_GROUPS`）无需阵容佐证 ——
那是事实而不是推断。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.static_build import PublishPolicy


def item(title: str, *, lineup=None, is_idol=False, is_acg=False, is_girl_band=False):
    """造一个查询结果项（模拟 OccurrenceOut）。

    lineup 传字符串列表即可，内部转成带 `.name` 的对象 —— 与真实
    `LineupOut` 的字段形状一致（注意是 `name`，不是 `id`）。
    """
    names = [
        n if hasattr(n, "name") else SimpleNamespace(name=n)
        for n in (lineup or [])
    ]
    return SimpleNamespace(
        event=SimpleNamespace(
            title=title, is_idol=is_idol, is_girl_band=is_girl_band, is_acg=is_acg
        ),
        lineup=names,
    )


@pytest.fixture
def policy():
    from app.parsers.text_zh import _IDOL_GROUPS

    return PublishPolicy(require_lineup=True, trusted=tuple(_IDOL_GROUPS))


class TestRequireLineup:
    def test_vertical_with_lineup_is_published(self, policy):
        assert policy.accepts(
            item("呆呆Otori 生诞祭", lineup=["DigitalDuel", "恋时青空"], is_idol=True)
        ) is True

    def test_non_vertical_is_rejected(self, policy):
        """普通摇滚场次不进垂类静态站（即使有阵容）。"""
        assert policy.accepts(
            item("某乐队巡演", lineup=["某乐队"], is_idol=False)
        ) is False

    @pytest.mark.parametrize("title", [
        "全职猎人同人only",
        "卡拉彼丘同人ONLY·S1",
        "金牌得主同人only",
        "2026型月同人ONLY·Lostbelt",
        "月亮计划only同人·coffee",
    ])
    def test_no_lineup_is_rejected(self, policy, title):
        """⚠️ 核心规则：没有演出名单 → 无法证明是演出 → 不发布。

        这些「同人ONLY」如果真有同人 Live，标题里根本没有线索能区分，
        阵容是唯一可靠判据。

        注意 BanG Dream! **不在**这一组：它属于「音乐/偶像动画品牌」例外，
        由 TestMusicIdolFranchiseRule 覆盖。
        """
        assert policy.accepts(item(title, lineup=[], is_idol=True)) is False

    def test_trusted_group_needs_no_lineup(self, policy):
        """已核实的具体团体/企划名：是事实，不靠阵容佐证。"""
        assert policy.accepts(item("比邻星球企划｜云响·回声", lineup=[], is_idol=True)) is True
        assert policy.accepts(item("留声RECORD音乐企划", lineup=[], is_idol=True)) is True

    def test_trusted_is_case_insensitive(self, policy):
        assert policy.accepts(item("留声record音乐企划", lineup=[], is_idol=True)) is True

    def test_policy_can_be_disabled(self):
        """可以整体关掉阵容要求（用于排查/对比）。"""
        p = PublishPolicy(require_lineup=False)
        assert p.accepts(item("随便什么", lineup=[], is_idol=True)) is True

    def test_acg_and_girl_band_also_need_lineup(self, policy):
        assert policy.accepts(item("某 ACG 展", lineup=[], is_acg=True)) is False
        assert policy.accepts(item("某女子乐队", lineup=[], is_girl_band=True)) is False

    def test_acg_with_lineup_published(self, policy):
        assert policy.accepts(
            item("次元激战 ACG宿命对决", lineup=["夜一乐队"], is_acg=True)
        ) is True


class TestMusicIdolFranchiseRule:
    """特殊规则：日本**音乐 / 偶像动画**的 only 展保留。

    维护者判断：BanG Dream! 这类品牌内容本身就是垂类（乐队/偶像企划），
    即使列表页没给演出名单也应当保留。

    ⚠️ 只放音乐/偶像向：游戏、少年漫、特摄的 only 展是普通漫展，不进垂类。
    这条区分实测必要 —— 库里 16 条「同人/only」只有 2 条属于音乐/偶像向。
    """

    @pytest.mark.parametrize("title,expected", [
        ("BanG Dream!梦想协奏曲同人Only·终章如初", "BanG Dream!"),
        ("所谓正解？【哭泣少女乐队Only Live", "哭泣少女乐队"),
        ("轻音少女 only展", "轻音少女"),
        ("LoveLive! 同人only", "LoveLive"),
        ("偶像大师 only", "偶像大师"),
        ("孤独摇滚 同人only", "孤独摇滚"),
        ("赛马娘 only", "赛马娘"),
        ("少女歌剧 同人only", "少女歌剧"),
        ("プロセカ only", "プロセカ"),
    ])
    def test_music_idol_brands_match(self, title, expected):
        from app.static_build import match_music_idol_franchise

        assert match_music_idol_franchise(title) == expected

    @pytest.mark.parametrize("title", [
        "2026型月同人ONLY·Lostbelt",       # FGO 是游戏
        "卡拉彼丘同人ONLY·S1",             # 射击游戏
        "全职猎人同人only",                 # 少年漫
        "明日方舟ONLY同人展",               # 手游
        "第五人格ONLY同人茶话会",           # 手游
        "高达only同人展",                   # 机器人动画（非音乐）
        "箱中奇遇·重返未来:1999 同人ONLY展", # 手游
        "阴阳师only同人展",                 # 手游
        "特摄同人ONLY嘉年华",               # 特摄
    ])
    def test_non_music_brands_do_not_match(self, title):
        from app.static_build import match_music_idol_franchise

        assert match_music_idol_franchise(title) is None

    def test_franchise_event_published_without_lineup(self, policy):
        """音乐动画 only 展：无阵容也发布。"""
        assert policy.accepts(
            item("BanG Dream!梦想协奏曲同人Only·终章如初", lineup=[], is_acg=True)
        ) is True

    def test_rule_can_be_disabled(self):
        p = PublishPolicy(require_lineup=True, use_franchise_rule=False)
        assert p.accepts(
            item("BanG Dream!同人Only", lineup=[], is_acg=True)
        ) is False


class TestNonPerformerNames:
    """⚠️ 实测坑：阵容字段会混进**应援物/周边名**，不能当成演出人员。

    例：「koyo生诞祭应援」的阵容写成「Koyo_Digitalduel-1018生诞祭版」——
    带日期数字码与「生诞祭版」版本后缀，是应援物名而不是团体。
    """

    @pytest.mark.parametrize("name", [
        "Koyo_Digitalduel-1018生诞祭版",
        "某某应援周边",
        "限定版特典",
        "XXXX-2026",
        "某团ver.2",
    ])
    def test_non_performer_rejected(self, name):
        from app.static_build import _looks_like_performer

        assert _looks_like_performer(name) is False

    @pytest.mark.parametrize("name", [
        "DigitalDuel", "恋时青空", "娜娜捏口俱乐部", "月匙Moon-Key",
        "Ringo乐队", "明日重启", "BO5乐队", "NERUNERU", "唐莉佳",
    ])
    def test_real_performer_accepted(self, name):
        from app.static_build import _looks_like_performer

        assert _looks_like_performer(name) is True

    def test_item_with_only_merch_lineup_is_rejected(self, policy):
        assert policy.accepts(
            item("koyo生诞祭应援", lineup=["Koyo_Digitalduel-1018生诞祭版"], is_idol=True)
        ) is False
