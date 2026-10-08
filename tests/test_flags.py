"""女子乐队 / ACG 独立标记的测试。

设计要点（都在测试里锁住）：
  * 两个标记与 `kind` / `is_idol` **正交**，可以叠加（ACG 女子乐队三个都为真）。
  * 关键词是**收紧过的** —— 曾经用「痛」「女声摇滚」这类泛词，实测把
    「澈心之痛」「友北五重奏《弦·无界》」误标，测试里保留这两条反例防回归。
  * `classify` 向后兼容：仍可按 `(kind, is_idol, tags)` 三元组解包。
"""

from __future__ import annotations

import pytest

from app.parsers import text_zh as t


class TestClassifyResult:
    def test_returns_object_with_flags(self):
        r = t.classify("女子乐队 ACG 专场")
        assert r.is_girl_band is True
        assert r.is_acg is True

    def test_backward_compatible_unpacking(self):
        """旧写法 `kind, is_idol, tags = classify(...)` 必须继续可用。"""
        kind, is_idol, tags = t.classify("地偶定期公演 vol.3")
        assert isinstance(kind, str)
        assert isinstance(is_idol, bool)
        assert isinstance(tags, list)

    def test_as_dict(self):
        d = t.classify("ACG 同人音乐会").as_dict()
        assert set(d) >= {"kind", "is_idol", "is_girl_band", "is_acg", "tags"}


class TestGirlBand:
    @pytest.mark.parametrize("text", [
        "纯女子乐队专场",
        "全女子编制 女子摇滚之夜",
        "ガールズバンド ワンマン",
        "少女乐队 Only Live",          # 同时是 ACG
        "女子朋克 拼盘",
        "Girl Band Night",
    ])
    def test_positive(self, text):
        assert t.classify(text).is_girl_band is True

    @pytest.mark.parametrize("text", [
        # ⚠️ 实测假阳性：古典/爵士场次也写「女声」，但编制不是女子乐队
        "东方古典与北美爵士的巧妙融合一《弦·无界》友北五重奏音乐会",
        # 女子偶像属于地偶，不算女子乐队
        "女子偶像团体定期公演",
        "地偶定期公演 生诞祭",
        "普通摇滚乐队巡演",
    ])
    def test_negative(self, text):
        assert t.classify(text).is_girl_band is False

    def test_idol_overlap_still_flagged_as_idol(self):
        """「女子偶像」应仍被标为地偶（只是不标女子乐队）。"""
        r = t.classify("女子偶像团体定期公演")
        assert r.is_idol is True
        assert r.is_girl_band is False


class TestIdolPrecision:
    """地偶判定的精确性。

    ⚠️ 实测误判：「企划」被当作地偶词，于是
      「留声RECORD音乐企划」「马赫mood x 杜逸风…5周年特别企划专场」
    都被判成地偶，混进了只发垂类的静态站。「企划」只是「项目/厂牌」的意思，
    音乐厂牌、说唱专场、戏剧企划都会用 —— 已从 IDOL_HINTS 移除。

    ⚠️ 但移除泛词会**漏标**真正的地偶企划（实测「比邻星球企划｜云响·回声」）。
    正确处理是补**具体厂牌/组合名**（`_IDOL_GROUPS`），而不是把泛词放回去：
    具体名字指向唯一，不会连带误伤。下面两组用例锁住这个边界。
    """

    @pytest.mark.parametrize("text", [
        "留声RECORD音乐企划",
        "马赫mood x 杜逸风「糟糕的日子里」5周年特别企划专场广州站",
        "某某厂牌年度企划演出",
    ])
    def test_pure_plan_word_is_not_idol(self, text):
        assert t.classify(text).is_idol is False

    @pytest.mark.parametrize("text", [
        "比邻星球企划｜云响·回声",       # 具体地偶企划名，必须认
        "比邻星球 定期公演",
    ])
    def test_known_idol_group_name_is_idol(self, text):
        assert t.classify(text).is_idol is True

    def test_idol_groups_override_score(self):
        """具体厂牌名优先级高于分数：即使只有名字、没有任何地偶词也要认。"""
        r = t.classify("比邻星球企划")
        assert r.is_idol is True

    @pytest.mark.parametrize("text", [
        "地偶定期公演 vol.3",
        "呆呆Otori · 2026 生诞祭",
        "PoP Star Idol Festival Vol.7",
        "偶像企划联合公演",          # 有「偶像」这个强信号，仍应判为地偶
    ])
    def test_real_idol_signal_still_detected(self, text):
        assert t.classify(text).is_idol is True


class TestAcg:
    @pytest.mark.parametrize("text", [
        "次元激战 ACG宿命对决",
        "VOCALOID ONLY LIVE",
        "初音未来 主题演唱会",
        "动漫主题曲音乐会",
        "同人音乐祭",
        "东方Project 同人Live",
        "赛马娘 Only Live",
        "零~夜时巫女一周年X京阿尼ONLY LIVE",
    ])
    def test_positive(self, text):
        assert t.classify(text).is_acg is True

    @pytest.mark.parametrize("text", [
        # ⚠️ 实测假阳性：单字「痛」曾把情绪化乐队名误标
        "澈心之痛2026深圳专场",
        "痛仰乐队 巡演 广州站",
        # ⚠️ 实测假阳性：「op主题曲」曾命中无关文本
        "东方古典与北美爵士的巧妙融合一《弦·无界》友北五重奏音乐会",
        # 交响/古典演绎动漫曲目仍算 ACG，但纯古典不算
        "贝多芬第九交响曲音乐会",
        "普通摇滚乐队拼盘",
        "脱口秀开放麦",
    ])
    def test_negative(self, text):
        assert t.classify(text).is_acg is False


class TestOrthogonality:
    def test_flags_stack(self):
        """一个 ACG 女子乐队：三个标记都为真。"""
        r = t.classify("所谓正解？【哭泣少女乐队Only Live】")
        assert r.is_idol is True
        assert r.is_girl_band is True
        assert r.is_acg is True

    def test_tags_include_flag_labels(self):
        r = t.classify("女子乐队 ACG 专场")
        assert "女子乐队" in r.tags
        assert "ACG" in r.tags

    def test_plain_band_has_no_flags(self):
        r = t.classify("某乐队 2026 巡演 广州站")
        assert (r.is_girl_band, r.is_acg) == (False, False)


class TestExtractFromTextFlags:
    def test_extract_carries_flags(self):
        """extract_from_text 的结果字典必须带上新标记（入库链路依赖它）。"""
        d = t.extract_from_text(
            "哭泣少女乐队 Only Live\n时间：2026年10月11日 15:30\n地点：MAO Livehouse 广州"
        )
        for key in ("is_idol", "is_girl_band", "is_acg"):
            assert key in d, f"缺少字段 {key}"
