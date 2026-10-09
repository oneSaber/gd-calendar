"""广州地偶图鉴转录数据的测试。

数据来源：小红书「广州地偶图鉴」的《现役53团最全图鉴》（更新 2026-07-24），
由 `scripts/xhs_note_images_all.py` 抓图 + 视觉读表转录。

这一组测试守三件事：
  1. **数据完整性**：序号不能重复、缺号要显式记录（不假装完整）
  2. **简繁别名**：图鉴是繁体、演出信息是简体，别名必须能打通
  3. **并入知识库**：转录的团要真的能被 `ArtistKnowledgeBase` 查到
"""

from __future__ import annotations

import pytest

from app.normalize import gz_idol_roster as roster
from app.normalize.artist_kb import SEED_ARTISTS, ArtistKnowledgeBase


class TestRosterData:
    def test_has_entries(self):
        assert len(roster.ROSTER) >= 40

    def test_numbers_unique(self):
        nums = [r[0] for r in roster.ROSTER]
        assert len(nums) == len(set(nums)), "序号重复"

    def test_is_complete(self):
        """图鉴共 53 组，必须**全部**转录（无缺口）。

        ⚠️ 曾经这里是「故意断言缺口存在」（缺 #35–#43），用来提醒补齐。
        补齐后改成这个断言 —— 它同样能防回归：漏了一组就挂。

        当时误判缺口的经过值得记住：抓图混进了推荐流，而且我把
        **两套提取结果**里的第 6 张搞混了（`t06_640x675` vs `s06`），
        凭空造出一个不存在的缺口。教训：两套结果先 hash 比对。
        """
        nums = {r[0] for r in roster.ROSTER}
        missing = [n for n in range(1, 54) if n not in nums]
        assert missing == [], f"缺号：{missing}"
        assert len(roster.ROSTER) == 53

    def test_member_total_matches_declaration(self):
        """⭐ 最有力的正确性证据：人数合计必须等于图鉴声明的 282。

        53 个独立数字加起来刚好对上报头，说明转录没有错行/漏行。
        """
        assert roster.member_sum() == roster.DECLARED_TOTAL_MEMBERS == 282

    def test_declared_totals(self):
        """图鉴自己声明 53 组 / 282 人。"""
        assert roster.DECLARED_TOTAL_GROUPS == 53
        assert roster.DECLARED_TOTAL_MEMBERS == 282

    @pytest.mark.parametrize("idx", [0, 5, 20, len(roster.ROSTER) - 1])
    def test_row_shape(self, idx):
        r = roster.ROSTER[idx]
        assert len(r) == 7
        num, name, style, debut, shows, members, rating = r
        assert isinstance(num, int) and 1 <= num <= 53
        assert name and len(name) >= 2
        assert isinstance(members, int) and members > 0
        assert rating

    def test_ratings_have_expected_tiers(self):
        """T1/T2/T3/T4 与丙/丁级都应出现（图鉴的分级结构）。"""
        ratings = {r[6] for r in roster.ROSTER}
        joined = " ".join(ratings)
        for tier in ("T1", "T2", "T3", "T4", "丙级"):
            assert tier in joined, f"缺 {tier}"


class TestAliases:
    """⚠️ 核心：图鉴用繁体，演出信息用简体，不打通就会漏判。"""

    @pytest.mark.parametrize("trad,simple", [
        ("娜娜捏口俱樂部", "娜娜捏口俱乐部"),
        ("戀音契約", "恋音契约"),
        ("山海誓約", "山海誓约"),
        ("終焉藍星", "终焉蓝星"),
        ("水葬放課後", "水葬放课后"),
        ("弥漫星雲", "弥漫星云"),
        ("胧月", "朧月"),
    ])
    def test_traditional_has_simplified_alias(self, trad, simple):
        assert trad in roster.names(), f"{trad} 不在名册"
        assert simple in roster.aliases_for(trad), (
            f"{trad} 缺简体别名 {simple} —— 判定时会漏"
        )

    def test_alias_for_strips_style_suffix(self):
        """带英文/风格后缀的名要能取到短名。"""
        a = roster.aliases_for("迷光 MIRAST")
        assert "MIRAST" in a
        assert "迷光" in a

    def test_aliases_for_unknown_name(self):
        assert roster.aliases_for("空色轨迹") == ()

    def test_short_head_not_used(self):
        """短名 < 2 字时不能当别名（会误匹配）。"""
        for name in roster.names():
            for a in roster.aliases_for(name):
                assert len(a) >= 2, f"{name} 的别名 {a!r} 过短"


class TestKnowledgeEntries:
    def test_to_entries_shape(self):
        ents = roster.to_knowledge_entries()
        assert len(ents) == len(roster.ROSTER)
        e = ents[0]
        for k in ("name", "aliases", "style", "debut", "shows",
                  "members", "rating", "num", "evidence"):
            assert k in e, f"缺字段 {k}"

    def test_evidence_non_empty(self):
        """`load_knowledge_base()` 只加载有 evidence 的条目 —— 不能空。"""
        for e in roster.to_knowledge_entries():
            assert e["evidence"] and len(e["evidence"]) >= 20

    def test_evidence_mentions_source(self):
        e = roster.to_knowledge_entries()[0]
        assert "图鉴" in e["evidence"]


class TestMergedIntoKb:
    def test_kb_contains_roster(self):
        names = {e.name for e in SEED_ARTISTS}
        for n in roster.names():
            assert n in names, f"知识库缺 {n}"

    def test_kb_size_grew(self):
        """合并后应显著大于原始的 18 条种子。"""
        assert len(SEED_ARTISTS) >= 60

    @pytest.mark.parametrize("query", [
        "娜娜捏口俱乐部", "恋音契约", "山海誓约", "月匙Moon-Key",
        "恋时青空", "空白扑克", "极夜NightFell", "终焉蓝星", "水葬放课后",
    ])
    def test_simplified_queries_resolve(self, query):
        """简体查询必须能命中（这是实际演出文本里的写法）。"""
        kb = ArtistKnowledgeBase()
        entry, how = kb.lookup(query)
        assert entry is not None, f"{query} 未命中知识库"
        assert how in ("name", "alias", "pattern")

    def test_roster_entries_are_idol_group(self):
        from app.normalize.artist_kb import KIND_IDOL_GROUP

        by_name = {e.name: e for e in SEED_ARTISTS}
        for n in roster.names():
            assert by_name[n].kind == KIND_IDOL_GROUP
