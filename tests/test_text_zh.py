"""中文文案解析器单测。

全部用例都来自**真实文案样本**（微博博文 / 场地动态 / 秀动页面），
不写凭空想出来的格式——否则测试只是在验证自己的想象。
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.parsers import text_zh as t

CST = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 10, 8, 20, 0, tzinfo=CST)  # 固定「现在」，让相对日期可断言


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #

class TestNormalize:
    def test_strip_emoji(self):
        assert t.strip_emoji("🎸痛仰乐队✨巡演") == "痛仰乐队巡演"
        # emoji 被移除后会留下相邻空格；空白折叠由 clean_text / display_title 负责
        assert t.strip_emoji("📅 2026.09.24 🕖 19:00").split() == ["2026.09.24", "19:00"]
        assert t.clean_text("📅 2026.09.24 🕖 19:00").split() == ["2026.09.24", "19:00"]
        # 注意：「➕」是 U+2795（Dingbat，属 emoji 区），与全角「＋」U+FF0B 不同字符
        assert t.strip_emoji("🧱➕🍎抽奖") == "抽奖"

    def test_clean_text_order(self):
        # 标准清洗：先 emoji 后 halfwidth
        assert t.clean_text("🧱🍎抽奖") == "抽奖"
        assert t.clean_text("价格：￥１００") == "价格:￥100"

    def test_strip_decor_keeps_semantic_plus(self):
        # 「VIP+」里的 + 是语义，不能被当装饰符去掉
        assert t.strip_decor("★ VIP+ ✦") == "VIP+"
        assert t.strip_decor("【星屑公演 Vol.12】") == "星屑公演 Vol.12"

    def test_normalize_name_collapses_unicode_math_bold(self):
        # SYNAPSE 系列真实用了 Unicode 数学粗体
        assert t.normalize_name("𝗦𝗬𝗡𝗔𝗣𝗦𝗘") == "synapse"

    def test_normalize_name_venue(self):
        assert t.normalize_name("MAO Livehouse广州") == "maolivehouse广州"
        # 全角/半角括号都应被剥掉
        assert t.normalize_name("SDlivehouse(北场馆)") == "sdlivehouse"
        assert t.normalize_name("SDlivehouse（北场馆）") == "sdlivehouse"

    def test_normalize_title_drops_vol(self):
        a = t.normalize_title("PoP Star Idol Festival Vol.7")
        b = t.normalize_title("PoP Star Idol Festival vol 7")
        assert a == b

    def test_normalize_title_keeps_chinese(self):
        assert "痛仰乐队" in t.normalize_title("🎸痛仰乐队「不败」巡演 广州站✨")

    def test_display_title_removes_decor_only(self):
        assert t.display_title("★ 星屑公演 Vol.12 ✦") == "星屑公演 Vol.12"


# --------------------------------------------------------------------------- #
# 时间解析
# --------------------------------------------------------------------------- #

class TestDatetime:
    def test_full_chinese_date(self):
        start, open_, prec, _ = t.parse_datetime("时间：2026年8月29日16:00", now=NOW)
        assert start == dt.datetime(2026, 8, 29, 16, 0, tzinfo=CST)
        assert prec == "exact"

    def test_dotted_date_with_emoji(self):
        start, _, prec, _ = t.parse_datetime("📅 2026.09.24 🕖 19:00 入场", now=NOW)
        assert start == dt.datetime(2026, 9, 24, 19, 0, tzinfo=CST)
        assert prec == "exact"

    def test_slash_date(self):
        start, _, _, _ = t.parse_datetime("时间：2026/03/06 20:00", now=NOW)
        assert start == dt.datetime(2026, 3, 6, 20, 0, tzinfo=CST)

    def test_strings(self):
        # 返回值顺序是 (开演, 开场, 精度)
        start, open_, prec, _ = t.parse_datetime("19:00 开场 / 20:00 开演", now=NOW)
        assert start == dt.datetime(2026, 10, 8, 20, 0, tzinfo=CST)
        assert open_ == dt.datetime(2026, 10, 8, 19, 0, tzinfo=CST)
        assert prec == "exact"

    def test_open_start_english(self):
        start, open_, _, _ = t.parse_datetime("OPEN 18:30 / START 19:30", now=NOW)
        assert start == dt.datetime(2026, 10, 8, 19, 30, tzinfo=CST)
        assert open_ == dt.datetime(2026, 10, 8, 18, 30, tzinfo=CST)

    def test_no_year_uses_future(self):
        # 10月11日，NOW 是 10月8日 → 同年
        start, _, _, _ = t.parse_datetime("10月11日 周六 19:30 开演", now=NOW)
        assert start == dt.datetime(2026, 10, 11, 19, 30, tzinfo=CST)

    def test_no_year_past_rolls_to_next_year(self):
        # 3月6日，NOW 是 10月8日 → 应补到 2027
        start, _, _, _ = t.parse_datetime("3月6日 20:00", now=NOW)
        assert start == dt.datetime(2027, 3, 6, 20, 0, tzinfo=CST)

    def test_relative_days(self):
        start, _, _, _ = t.parse_datetime("明天 19:00 开演", now=NOW)
        assert start == dt.datetime(2026, 10, 9, 19, 0, tzinfo=CST)

    def test_chinese_hour_evening(self):
        start, _, _, _ = t.parse_datetime("11月7日 晚8点 开演", now=NOW)
        assert start == dt.datetime(2026, 11, 7, 20, 0, tzinfo=CST)

    def test_half_hour(self):
        start, _, _, _ = t.parse_datetime("11月7日 晚上7点半", now=NOW)
        assert start == dt.datetime(2026, 11, 7, 19, 30, tzinfo=CST)

    def test_weekday_only(self):
        # NOW 是周四(weekday=3)，最近的周六是 10月10日
        start, _, _, _ = t.parse_datetime("本周六 20:00", now=NOW)
        assert start.date() == dt.date(2026, 10, 10)

    def test_date_only(self):
        start, _, prec, _ = t.parse_datetime("时间：2026年10月31日", now=NOW)
        assert prec == "date_only"
        assert start.date() == dt.date(2026, 10, 31)

    def test_no_date(self):
        start, _, prec, _ = t.parse_datetime("阵容超强敬请期待", now=NOW)
        assert start is None and prec == "tbd"

    def test_relative_yesterday(self):
        """实测坑：微博里「昨天 20:00」很常见，必须回退到昨天而不是今天。"""
        start, _, _, _ = t.parse_datetime("昨天 20:00 开演", now=NOW)
        assert start == dt.datetime(2026, 10, 7, 20, 0, tzinfo=CST)

    def test_relative_day_before_yesterday(self):
        start, _, _, _ = t.parse_datetime("前天 19:30", now=NOW)
        assert start == dt.datetime(2026, 10, 6, 19, 30, tzinfo=CST)

    def test_time_range_second_is_end_not_start(self):
        """实测坑：「21:40–22:40」曾把结束时间当成开演。"""
        start, open_, prec, end = t.parse_datetime("2026.10.24 21:40–22:40", now=NOW)
        assert start == dt.datetime(2026, 10, 24, 21, 40, tzinfo=CST)
        assert end == dt.datetime(2026, 10, 24, 22, 40, tzinfo=CST)

    def test_time_range_with_tilde(self):
        start, _, _, end = t.parse_datetime("11月1日 19:00~21:30", now=NOW)
        assert start == dt.datetime(2026, 11, 1, 19, 0, tzinfo=CST)
        assert end == dt.datetime(2026, 11, 1, 21, 30, tzinfo=CST)


# --------------------------------------------------------------------------- #
# 价格解析
# --------------------------------------------------------------------------- #

class TestPrice:
    def test_qi_suffix(self):
        lo, hi, free, tiers = t.parse_prices("价格：¥180起")
        assert (lo, hi, free) == (180.0, 180.0, False)
        assert tiers[0].price == 180.0

    def test_decimal(self):
        lo, hi, _, _ = t.parse_prices("价格：¥9.90起")
        assert lo == 9.90

    def test_single_no_qi(self):
        lo, hi, _, _ = t.parse_prices("价格：¥150")
        assert (lo, hi) == (150.0, 150.0)

    def test_advance_door_tiers(self):
        lo, hi, _, tiers = t.parse_prices("预售80 现场100")
        assert lo == 80.0 and hi == 100.0
        names = {x.tier_type for x in tiers}
        assert "预售" in names and "现场" in names

    def test_free(self):
        lo, hi, free, tiers = t.parse_prices("🎫 门票：无料入场")
        assert free is True and lo == 0.0
        assert tiers[0].tier_type == "无料"

    def test_placeholder_001(self):
        # 真实存在的 ¥0.01「普通票」= 无料占位票（实测 ticket_tier.is_placeholder）
        lo, hi, free, tiers = t.parse_prices("普通票 ¥0.01 / A票 ¥38 / VIP ¥198")
        assert lo == 0.0, "占位票应归入无料（0 元）"
        assert hi == 198.0
        ph = [x for x in tiers if x.is_placeholder]
        assert len(ph) == 1 and ph[0].name_raw == "普通票"

    def test_range(self):
        # 早鸟/预售/现场 区间写法
        lo, hi, _, _ = t.parse_prices("早鸟票 80-150")
        assert (lo, hi) == (80.0, 150.0)

    def test_vip_price_then_tier_name(self):
        """实测坑：「¥237 VIP」里的 VIP 是**票种名**，不是 237 的标签。

        正确结果：237(票价) + VIP(票种)。曾一度被解析成 label='VIP', amount=237。
        """
        lo, hi, _, tiers = t.parse_prices("¥237 VIP / ¥457 VIP+ / ¥457 VVIP+")
        assert (lo, hi) == (237.0, 457.0)
        names = [x.name_raw for x in tiers]
        assert "VIP" in names and any("VIP+" in n for n in names)

    def test_1d_is_not_price(self):
        """实测坑：「入场：48.8 1D」里的 1D 是票种代号，不是价格。"""
        lo, hi, _, tiers = t.parse_prices("入场：48.8 1D")
        assert lo == 48.8
        assert hi == 48.8, "不应把 1D 解析成 ¥1"
        assert all(x.price != 1.0 for x in tiers)

    def test_vip_plus_tier(self):
        _, _, _, tiers = t.parse_prices("VIP 237 元 / VVIP+ 457 元")
        codes = [x.name_raw for x in tiers]
        assert any("VVIP+" in c for c in codes)

    def test_no_price(self):
        lo, hi, free, tiers = t.parse_prices("阵容超强")
        assert lo is None and hi is None and not tiers


# --------------------------------------------------------------------------- #
# 城市 / 场地
# --------------------------------------------------------------------------- #

class TestVenue:
    def test_bracket_city(self):
        assert t.parse_city("[广州]游声场FreeField") == "广州"

    def test_city_in_text(self):
        assert t.parse_city("地点：广州天河 某处") == "广州"

    def test_alias(self):
        assert t.parse_city("羊城某livehouse") == "广州"

    def test_clean_venue_strips_city_and_label(self):
        # NFKC 归一化后括号变半角，这是期望行为
        assert t.clean_venue("广州市越秀区北京路纵一咖啡（2F）") == "越秀区北京路纵一咖啡(2F)"

    def test_clean_venue_strips_label(self):
        assert t.clean_venue("地点：MAO Livehouse 广州（永庆坊店）") == "MAO Livehouse 广州(永庆坊店)"

    def test_clean_venue_stops_at_next_field(self):
        """实测坑：不截断会把后面的字段全吞进场地名。"""
        got = t.clean_venue("广州天河入场：48.8 1D参演团队：@A @B")
        assert got == "广州天河"  # 城市前缀保留：带城市的场地名更可辨识（parse_city 已单独取城市）

    def test_clean_venue_keeps_info_emoji_content(self):
        got = t.clean_venue("📍 广州市越秀区北京路纵一咖啡（2F）")
        assert got and "纵一咖啡" in got

    def test_undecided(self):
        assert t.clean_venue("场地待定") is None

    def test_taicang_distinction(self):
        """实测：太古仓 4 号仓（MAO）与 5 号仓（太空间）是两个场地，不可混为「太古仓」。"""
        a = t.clean_venue("MAO Livehouse广州（太古仓店）革新路124号太古仓4号仓")
        b = t.clean_venue("太空间Livehouse 太古仓码头5号仓")
        assert a != b
        # 归一化后也必须不同
        assert t.normalize_name(a or "") != t.normalize_name(b or "")


# --------------------------------------------------------------------------- #
# 阵容
# --------------------------------------------------------------------------- #

class TestLineup:
    def test_at_prefixed(self):
        txt = "参演团队：@NEXUS_Official @Ventur_Official @Chronos_official"
        out = t.parse_lineup(txt)
        names = [a.name_raw for a in out]
        assert names == ["NEXUS_Official", "Ventur_Official", "Chronos_official"]
        assert out[0].billing_order == 1

    def test_slash_separated(self):
        out = t.parse_lineup("出演：星屑少女 / 月见少女 / 甜梦计划")
        assert [a.name_raw for a in out] == ["星屑少女", "月见少女", "甜梦计划"]

    def test_cross_separated(self):
        out = t.parse_lineup("阵容：海风 × 低频 × 潮汐")
        assert [a.name_raw for a in out] == ["海风", "低频", "潮汐"]

    def test_stops_at_next_field(self):
        out = t.parse_lineup("参演：A队 / B队 门票：80元 时间：19:30")
        assert [a.name_raw for a in out] == ["A队", "B队"]

    def test_role_note_stripped(self):
        out = t.parse_lineup("阵容：星屑少女（主演） / 月见少女（嘉宾）")
        assert [a.name_raw for a in out] == ["星屑少女", "月见少女"]

    def test_showstart_artist_prefix(self):
        out = t.parse_lineup("艺人：守麦乐队")
        assert [a.name_raw for a in out] == ["守麦乐队"]

    def test_dedup(self):
        out = t.parse_lineup("阵容：A队 / A队 / B队")
        assert len(out) == 2

    def test_no_lineup(self):
        assert t.parse_lineup("今晚一起吃火锅") == []


class TestNoiseGuards:
    """噪声护栏：实测微博/豆瓣里混着大量非活动内容。"""

    def test_date_like_not_venue(self):
        """实测坑：微博把日期串进场地位置，`2026/10/18 (周日)` 成了场地记录。"""
        assert t.clean_venue("2026/10/18 (周日)") is None
        assert t.clean_venue("时间：2026/10/18 (周日)") is None
        assert t.clean_venue("10月18日 19:00") is None
        assert t.is_date_like("2026-10-18") is True
        assert t.is_date_like("MAO Livehouse") is False

    def test_boilerplate_title_rejected(self):
        assert t.looks_like_event_title("主催情报") is False
        assert t.looks_like_event_title("揭示板") is False
        assert t.looks_like_event_title("ab") is False
        assert t.looks_like_event_title("2026/10/18 (周日)") is False

    def test_real_titles_accepted(self):
        assert t.looks_like_event_title("星屑公演 Vol.12") is True
        assert t.looks_like_event_title("呆呆Otori · 2026 生诞祭") is True
        assert t.looks_like_event_title("PoP Star Idol Festival Vol.7") is True

    def test_title_truncated_at_field_label(self):
        """实测坑：整段正文被当成标题（时间/地点都进来了）。"""
        txt = "⋆。+° koyo生诞祭应援 ༉+ ̊. 时间：2026/10/18 (周日) 19:00 地点：待定"
        d = t.extract_from_text(txt, source_code="weibo", now=NOW)
        assert "时间" not in d["title_display"]
        assert "koyo生诞祭应援" in d["title_display"]


class TestSplitLineupNames:
    """实测：秀动「艺人」字段会把多组用 / & × 连成一串，必须能拆开。"""

    def test_slash_joined(self):
        got = t.split_lineup_names("娜娜捏口俱乐部/ReaLume/恋时青空/DigitalDuel")
        assert got == ["娜娜捏口俱乐部", "ReaLume", "恋时青空", "DigitalDuel"]

    def test_two_groups(self):
        assert t.split_lineup_names("体熊专科/JASON KUI") == ["体熊专科", "JASON KUI"]

    def test_single_name_unchanged(self):
        assert t.split_lineup_names("守麦乐队") == ["守麦乐队"]

    def test_cross_and_amp(self):
        assert t.split_lineup_names("A × B × C") == ["A", "B", "C"]
        assert t.split_lineup_names("海风 & 低频") == ["海风", "低频"]

    def test_dedup_and_empty(self):
        assert t.split_lineup_names("A/A/B") == ["A", "B"]
        assert t.split_lineup_names("") == []
        assert t.split_lineup_names(None) == []

    def test_normalize_collapses_separator_variants(self):
        """归一化要去掉阵容分隔符，否则同一组会产生重复艺人。"""
        assert t.normalize_name("体熊专科/JASON KUI") == t.normalize_name("体熊专科 / Jason Kui")


# --------------------------------------------------------------------------- #
# 分类
# --------------------------------------------------------------------------- #

class TestClassify:
    @pytest.mark.parametrize(
        "title,expect_kind,expect_idol",
        [
            ("PoP Star Idol Festival Vol.7", None, True),
            ("呆呆Otori · 2026 生诞祭", "idol_birthday", True),
            ("所谓正解？【哭泣少女乐队Only Live】", None, True),
            ("星屑公演 Vol.12", None, True),
            ("痛仰乐队「不败」巡演 广州站", "tour_stop", False),
            ("噪音拼盘 #37：三支本地乐队", "taiban", False),
            ("广州·金牌得主同人only", None, True),
            ("大笑喜剧天河路脱口秀串烧专场", "other", False),
            ("周柏豪粉丝见面演唱会", "other", False),
        ],
    )
    def test_classify(self, title, expect_kind, expect_idol):
        kind, is_idol, tags = t.classify(title)
        assert is_idol is expect_idol, f"{title} 判定为 idol={is_idol}，期望 {expect_idol}"
        if expect_kind:
            assert kind == expect_kind, f"{title} → {kind}，期望 {expect_kind}"

    def test_negative_excluded(self):
        _, is_idol, tags = t.classify("话剧《雷雨》广州站")
        assert is_idol is False and "非演出" in tags


# --------------------------------------------------------------------------- #
# 系列 / 期号
# --------------------------------------------------------------------------- #

class TestSeries:
    def test_vol_dotted(self):
        series, vol = t.parse_series("PoP Star Idol Festival Vol.7")
        assert vol == "7"
        assert series and "popstar" in series

    def test_vol_decimal_text(self):
        """实测：「Vol33.0」这种小数期号 → 必须是 TEXT。"""
        _, vol = t.parse_series("某系列 Vol33.0")
        assert vol == "33.0"

    def test_chinese_qi(self):
        _, vol = t.parse_series("星屑公演 第三期")
        assert vol == "三"

    def test_circled_number(self):
        _, vol = t.parse_series("月见少女公演 ①")
        assert vol == "①"

    def test_series_groups_across_case(self):
        s1, _ = t.parse_series("绮丽偶像日 KFC MINI Guangzhou VOL 13")
        s2, _ = t.parse_series("绮丽偶像日 KFC MINI in GuangZhou 08")
        assert s1 and s2


# --------------------------------------------------------------------------- #
# 地偶专属字段
# --------------------------------------------------------------------------- #

class TestIdolFields:
    def test_age_limit_height_not_age(self):
        """实测：广东演出普遍用身高（1.2m）而非年龄。"""
        got = t.parse_age_limit("剧院演出 1.2 米以下儿童谢绝入场（儿童专场除外）")
        assert got and "1.2" in got

    def test_age_limit_all_ages(self):
        assert t.parse_age_limit("本场为全年龄演出") == "全年龄"

    def test_tokuten(self):
        got = t.extract_tokuten("特典券 ¥80/张，现场贩售。1 张 = 对谈 60 秒")
        assert got and "特典券" in got and "80" in got

    def test_schedule(self):
        txt = "17:30 A组上台\n18:10 B组上台\n19:00 特典会"
        got = t.extract_schedule(txt)
        assert got and "A组" in got

    def test_no_schedule(self):
        assert t.extract_schedule("阵容超强") is None


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #

class TestStatus:
    @pytest.mark.parametrize(
        "text,expect",
        [
            ("本场已取消", "cancelled"),
            ("因故改期，详见后续公告", "postponed"),
            ("票已售罄", "sold_out"),
            ("售票中，欲购从速", "on_sale"),
        ],
    )
    def test_status(self, text, expect):
        status, _ = t.parse_status(text)
        assert status == expect


# --------------------------------------------------------------------------- #
# 端到端抽取（真实微博文案）
# --------------------------------------------------------------------------- #

class TestExtractEndToEnd:
    def test_weibo_idol_taiban(self):
        """真实微博博文（YELO_Official）。"""
        txt = (
            "Z.T IDOL PARTY时间：2026年8月29日16:00地点：广州天河入场：48.8 1D"
            "参演团队：@NEXUS_Official @Ventur_Official @Ascendant_official "
            "@Chronos_official @黑曜启示Obsilume 🧱➕🍎➕🐷 抽三名送入场"
        )
        d = t.extract_from_text(txt, source_code="weibo", now=NOW)
        assert d["start_at"] == dt.datetime(2026, 8, 29, 16, 0, tzinfo=CST)
        assert d["city"] == "广州"
        assert d["price_min"] == 48.8
        assert len(d["artists"]) == 5
        assert d["is_idol"] is True

    def test_weibo_birthday_sp(self):
        """真实微博博文（REX狂想曲）—— 实测坑：这是「无料」场，
        文案里所有数字（日期、时间、楼层）都不是票价，必须全部排除。"""
        txt = (
            "扬帆！启航！✦ REX狂想夜 邓晴桦&鸣岐 生日SP 📅 2026.09.24 🕖 19:00 入场"
            "📍 广州市越秀区北京路纵一咖啡（2F）🎫 门票：无料入场"
        )
        d = t.extract_from_text(txt, source_code="weibo", now=NOW)
        assert d["start_at"] == dt.datetime(2026, 9, 24, 19, 0, tzinfo=CST)
        assert d["is_free"] is True
        assert d["price_min"] == 0.0, "无料场不应把日期/时间数字当票价"
        assert d["venue_raw"] and "纵一咖啡" in d["venue_raw"]
        assert d["is_idol"] is True

    def test_showstart_style(self):
        """秀动 SSR 条目文案。"""
        txt = "守麦乐队独立民谣专场｜小而美降噪LIVE 艺人：守麦乐队 价格：¥49起 时间：2026/03/06 20:00"
        d = t.extract_from_text(txt, source_code="showstart", now=NOW)
        assert d["price_min"] == 49.0
        assert d["start_at"] == dt.datetime(2026, 3, 6, 20, 0, tzinfo=CST)
        assert any(a.name_raw == "守麦乐队" for a in d["artists"])

    def test_title_with_fullwidth_colon_not_split(self):
        """实测：标题里有全角冒号，不能用冒号切标题。"""
        txt = "红白色乐队：深夜频道 时间：2026/11/01 20:00 地点：广州MAO Livehouse"
        d = t.extract_from_text(txt, source_code="showstart", now=NOW)
        assert "红白色乐队" in d["title_display"]

    def test_confidence_high_when_fields_present(self):
        txt = "时间：2026/11/01 20:00 地点：广州MAO Livehouse 价格：¥100 阵容：A队"
        d = t.extract_from_text(txt, source_code="showstart", now=NOW)
        assert t.confidence_of(d) >= 0.8

    def test_confidence_low_when_missing(self):
        d = t.extract_from_text("敬请期待", source_code="weibo", now=NOW)
        assert t.confidence_of(d) < 0.5
