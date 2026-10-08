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
    """造一个查询结果项（模拟 OccurrenceOut）。"""
    return SimpleNamespace(
        event=SimpleNamespace(
            title=title, is_idol=is_idol, is_girl_band=is_girl_band, is_acg=is_acg
        ),
        lineup=lineup or [],
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
        "BanG Dream!梦想协奏曲同人Only·终章如初",
        "月亮计划only同人·coffee",
    ])
    def test_no_lineup_is_rejected(self, policy, title):
        """⚠️ 核心规则：没有演出名单 → 无法证明是演出 → 不发布。

        这些「同人ONLY」如果真有同人 Live，标题里根本没有线索能区分，
        阵容是唯一可靠判据。
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
