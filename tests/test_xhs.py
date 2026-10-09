"""小红书采集器的纯函数测试（不联网、不碰浏览器）。"""

from __future__ import annotations

import pytest

from app.collectors import xhs


class TestParseCardText:
    """卡片文本按**位置**取字段 —— 实测按「哪行像人名」猜会全空。"""

    @pytest.mark.parametrize("text,title,author", [
        ("标题在这\n作者名\n6天前\n63", "标题在这", "作者名"),
        ("广州国庆偶活速览🎤25场先收藏\n广州地偶图鉴\n6天前\n63",
         "广州国庆偶活速览🎤25场先收藏", "广州地偶图鉴"),
        ("国庆&中秋 华南地偶活动汇总帖\nKingMaker\n2天前\n16",
         "国庆&中秋 华南地偶活动汇总帖", "KingMaker"),
        ("1005广州男地偶——格希直拍\n🍠610c0c01\n1天前\n1",
         "1005广州男地偶——格希直拍", "🍠610c0c01"),
        ("广州地王广场也有地偶看？！\n芋泥..\n07-2:\n1729",
         "广州地王广场也有地偶看？！", "芋泥.."),
        ("标题\n作者\n昨天\n5", "标题", "作者"),
        ("标题\n作者\n2026-10-01\n5", "标题", "作者"),
    ])
    def test_title_line1_author_line2(self, text, title, author):
        assert xhs.parse_card_text(text) == (title, author)

    def test_single_line_only_title(self):
        assert xhs.parse_card_text("单行标题") == ("单行标题", "")

    def test_empty(self):
        assert xhs.parse_card_text("") == ("", "")
        assert xhs.parse_card_text(None) == ("", "")

    def test_author_skips_time_and_likes(self):
        """时间（「6天前」「07-2:」）与点赞数不能被当作者。"""
        _, author = xhs.parse_card_text("T\n6天前\n123")
        assert author == "", f"时间/数字被当成作者：{author!r}"


class TestNavVisible:
    """登录判定看**登录后才有的导航入口**，不看 cookie、不看扫码字样。"""

    def test_logged_in_nav(self):
        assert xhs.nav_visible("首页 发布 通知 消息 我") is True

    def test_login_page(self):
        assert xhs.nav_visible("登录后查看搜索结果 扫码登录 手机号登录") is False

    def test_partial_nav_is_not_enough(self):
        """只命中 1~2 个不算 —— 避免页面上偶然出现「我」就判成已登录。"""
        assert xhs.nav_visible("全部 图文 视频 用户 我") is False


class TestKeywords:
    def test_covers_required_topics(self):
        """词表断言覆盖范围而不是具体字符串（词表该随实测演进）。"""
        kws = xhs.SEARCH_KEYWORDS
        assert any("地偶" in k for k in kws), "缺地偶"
        assert any("图鉴" in k or "情报" in k for k in kws), "缺图鉴/情报类聚合词"
        assert any("地王广场" in k for k in kws), "缺免费场地（地王广场）"
        assert any("ACG" in k for k in kws), "缺 ACG"


class TestXsecToken:
    """⚠️ `xsec_token` 是打开笔记正文的**必需参数**。

    这一组守着一个**代价很大的误判**：我曾判定「小红书笔记正文
    不可自动读取（防抓取）」，依据是 `/explore/<id>` 返回 404。
    真因是缺 token —— 见 SPEC.md 里的三组对照实验（A 404 / B 可读 / C 可读）。
    """

    def test_token_regex_extracts(self):
        href = (
            "/search_result/6abbc42a000000001303e73f"
            "?xsec_token=AB-WSSl_b76IsnKRUYWAHQz4gtV2NW7l6EJ2U2PYOHXig="
            "&xsec_source=pc_search"
        )
        m = xhs._XSEC_TOKEN_RE.search(href)
        assert m is not None
        assert m.group(1).startswith("AB-WSSl")

    def test_token_regex_no_match(self):
        assert xhs._XSEC_TOKEN_RE.search(
            "/explore/6abbc42a000000001303e73f"
        ) is None

    def test_note_url_includes_token_when_present(self):
        n = xhs.XhsNote(note_id="6abbc42a000000001303e73f", xsec_token="AB-xyz=")
        url = xhs.note_url(n)
        assert "xsec_token=AB-xyz=" in url
        assert "xsec_source=pc_search" in url
        assert url.startswith("https://www.xiaohongshu.com/explore/")

    def test_note_url_without_token_still_builds(self):
        """没有 token 时仍给出 URL（调用方应预期它可能 404）。"""
        n = xhs.XhsNote(note_id="abc123")
        assert xhs.note_url(n) == "https://www.xiaohongshu.com/explore/abc123"

    def test_note_dict_serializes_token_and_body(self):
        n = xhs.XhsNote(note_id="x", xsec_token="tok", body="正文")
        d = n.as_dict()
        assert d["xsec_token"] == "tok"
        assert d["body"] == "正文"


class TestExtractNotesJs:
    """卡片提取脚本必须**优先保留带 token 的链接**。

    实测坑：同一条笔记在页面里有 `/explore/<id>`（无 token）与
    `/search_result/<id>?xsec_token=…`（有 token）两个链接。
    按「先出现者胜」去重 → 20 条笔记 0 条带 token，正文全抓不到。
    """

    def test_script_prefers_token_link(self):
        js = xhs._EXTRACT_NOTES_JS
        assert "xsec_token=" in js, "提取脚本没检查 token"
        assert "hasTok" in js, "提取脚本缺少「优先带 token」的逻辑"

    def test_script_keeps_longest_card_text(self):
        assert "byId[id].text.length" in xhs._EXTRACT_NOTES_JS


class TestNoteBodySelector:
    def test_detail_selector_targets_note_container(self):
        js = xhs._EXTRACT_DETAIL_JS
        assert "#noteContainer" in js or "role=dialog" in js


class TestCollectSignature:
    def test_collect_accepts_with_body(self):
        """`with_body` 参数必须在 —— 标题里没有演出时间/地点/阵容。"""
        import inspect

        sig = inspect.signature(xhs.collect)
        assert "with_body" in sig.parameters
        assert "body_limit" in sig.parameters
        assert sig.parameters["with_body"].default is False
