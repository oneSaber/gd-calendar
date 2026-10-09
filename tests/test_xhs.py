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
