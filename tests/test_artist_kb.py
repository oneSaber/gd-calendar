"""演员知识库与「按阵容判定」的测试。

## 为什么要有这层

维护者要求：**分析不能完全靠标题，优先检查演出人员**。实测依据：
  * 「留声RECORD音乐企划」标题像音乐厂牌 → 靠标题判错过一次；
  * 「恋时青空」「比邻星球企划」「月匙Moon-Key」这些**真地偶团体**的名字里
    一个地偶关键词都没有 → 靠标题永远认不出来，只能靠知识库；
  * 秀动列表页带阵容，覆盖率实测 86%（241/355），信息量大于标题。

## 必须守住的边界

1. 知识库未收录的名字**不能瞎猜**（要进 unknown，交给 review/联网），
   否则每个陌生演员都会被当成某类，投票就失去意义。
2. 「地偶场」与「女子乐队」互斥口径不变：女子偶像算地偶，不算女子乐队。
"""

from __future__ import annotations

import pytest

from app.normalize.artist_kb import (
    KIND_ACG,
    KIND_BAND,
    KIND_GIRL_BAND,
    KIND_IDOL_GROUP,
    KIND_UNKNOWN,
    ArtistKnowledgeBase,
    KnowledgeEntry,
    classify_lineup,
    merge_with_title_flags,
)


@pytest.fixture
def kb():
    return ArtistKnowledgeBase()


class TestLookup:
    def test_exact_name(self, kb):
        entry, how = kb.lookup("恋时青空")
        assert entry is not None and entry.kind == KIND_IDOL_GROUP
        assert how == "name"

    def test_alias(self, kb):
        entry, how = kb.lookup("比邻星球")     # 是「比邻星球企划」的别名
        assert entry is not None and entry.kind == KIND_IDOL_GROUP
        assert how in ("name", "alias")

    @pytest.mark.parametrize("text", [
        "留声RECORD音乐企划", "留声record音乐企划", "留声 RECORD 音乐企划",
        "留声RECORD", "留声record",
    ])
    def test_case_and_space_normalized(self, kb, text):
        """⚠️ 实测 bug：小写关键字配原始文本比对，
        「留声RECORD」（全大写）永远匹配不上。归一化必须大小写与空格无关。"""
        entry, how = kb.lookup(text)
        assert entry is not None, f"{text} 没匹配上"
        assert entry.kind == KIND_IDOL_GROUP

    def test_moon_key_variants(self, kb):
        for text in ("月匙Moon-Key", "月匙", "Moon-Key", "月匙MK"):
            entry, _ = kb.lookup(text)
            assert entry is not None and entry.kind == KIND_IDOL_GROUP, text

    def test_band_pattern(self, kb):
        entry, how = kb.lookup("某个不认识的乐队")
        assert entry is not None and entry.kind == KIND_BAND
        assert how == "pattern"

    def test_unknown_is_not_guessed(self, kb):
        """陌生名字必须返回 miss，不能瞎猜一个分类。"""
        entry, how = kb.lookup("JORDANN")
        assert how == "miss"
        assert entry is None

    def test_personal_name_is_not_guessed(self, kb):
        """纯人名（如「张泽历」）不应被命名规则误判成偶像/乐队。"""
        entry, how = kb.lookup("张泽历")
        assert how == "miss"

    def test_empty(self, kb):
        assert kb.lookup("")[1] == "miss"
        assert kb.lookup("   ")[1] == "miss"


class TestLineupVoting:
    def test_all_idol_lineup(self, kb):
        v = classify_lineup(
            ["DigitalDuel", "ReaLume", "娜娜捏口俱乐部", "恋时青空"], kb
        )
        assert v.is_idol is True
        assert v.votes["is_idol"] == 4
        assert v.unknown == []

    def test_known_idol_festival(self, kb):
        v = classify_lineup(
            ["DigitalDuel", "LAMENTiS", "Yours", "恋时青空", "恋音契约", "月匙Moon-Key"],
            kb,
        )
        assert v.is_idol is True
        assert v.votes["is_idol"] == 6

    def test_plain_band_lineup_is_not_idol(self, kb):
        """普通乐队阵容不能被判成地偶。"""
        v = classify_lineup(["布衣乐队", "碎梦飞跃"], kb)
        assert v.is_idol is False
        assert v.is_girl_band is False
        assert v.is_acg is False

    def test_unknown_names_are_collected(self, kb):
        v = classify_lineup(["DigitalDuel", "完全没听过的名字"], kb)
        assert v.is_idol is True                 # 已知的那个仍投票
        assert "完全没听过的名字" in v.unknown   # 陌生的被收集，供后续核实

    def test_empty_lineup(self, kb):
        v = classify_lineup([], kb)
        assert not v.is_idol and not v.is_girl_band and not v.is_acg
        assert v.unknown == []

    def test_girl_band_kind(self, kb):
        kb.add(KnowledgeEntry("全女子测试团", KIND_GIRL_BAND))
        v = classify_lineup(["全女子测试团"], kb)
        assert v.is_girl_band is True
        assert v.is_idol is False

    def test_acg_kind(self, kb):
        kb.add(KnowledgeEntry("某同人乐团", KIND_ACG))
        v = classify_lineup(["某同人乐团"], kb)
        assert v.is_acg is True

    def test_votes_can_stack(self, kb):
        """一个 ACG 女子乐队能同时给两个标记投票（标记是正交的）。"""
        kb.add(KnowledgeEntry("测试双标记团", KIND_GIRL_BAND,
                              aliases=("双标记别名",)))
        kb.add(KnowledgeEntry("测试ACG团", KIND_ACG))
        v = classify_lineup(["测试双标记团", "测试ACG团"], kb)
        assert v.is_girl_band is True and v.is_acg is True
        assert v.votes == {"is_girl_band": 1, "is_acg": 1}

    def test_idol_member_counts_as_idol(self, kb):
        """偶像组合成员（如 GNZ48 的唐莉佳）出现也算地偶证据。"""
        v = classify_lineup(["唐莉佳", "曾艾佳"], kb)
        assert v.is_idol is True
        assert v.votes["is_idol"] == 2


class TestMergeWithTitle:
    def test_union(self):
        """标题与阵容取**并集**：标题判定会误报，阵容判定会漏报（阵容缺失 32%）。"""
        out = merge_with_title_flags(
            {"is_idol": False, "is_girl_band": False, "is_acg": True},
            classify_lineup(["DigitalDuel"], ArtistKnowledgeBase()),
        )
        assert out["is_idol"] is True     # 阵容给出
        assert out["is_acg"] is True      # 标题给出

    def test_neither(self):
        out = merge_with_title_flags(
            {"is_idol": False, "is_girl_band": False, "is_acg": False},
            classify_lineup([], ArtistKnowledgeBase()),
        )
        assert out == {"is_idol": False, "is_girl_band": False, "is_acg": False}
