"""微博采集器单测。

覆盖 `parse_search_payload` / `discover_accounts` / `strip_html` / `parse_weibo_created_at`
等**纯函数**（不需要浏览器、不需要网络）。

fixture 按 docs/02-采集方案-全自动.md §3.2 的**实测结构**构造：
`{"ok":1,"data":{"cards":[{"card_type":9,"mblog":{...}}]}}`，
`mblog` 字段名一律用真实的：`text / created_at / user.screen_name / user.id /
pics[].large.url / id / reposts_count / comments_count / attitudes_count`。

两条真实风格的文案（任务指定的样本）必须能被解析出来：
  * `Z.T IDOL PARTY时间：2026年8月29日16:00地点：广州天河入场：48.8 1D参演团队：@NEXUS_Official @Ventur_Official`
  * `扬帆！启航！✦ REX狂想夜 邓晴桦&鸣岐 生日SP 📅 2026.09.24 🕖 19:00 入场 📍 广州市越秀区北京路纵一咖啡（2F）🎫 门票：无料入场`
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import weibo as w

CST = dt.timezone(dt.timedelta(hours=8))

# 实测 `created_at` 形态：**年份在最后**（dateutil / fromisoformat 都吃不掉）
CREATED_ZTI = "Wed Aug 26 18:35:15 +0800 2026"
CREATED_REX = "Tue Aug 25 09:12:00 +0800 2026"

# ---- 真实风格文案 ①：地偶联合公演（@阵列阵容 + 1D 票种）----
TEXT_ZTI = (
    'Z.T IDOL PARTY<span class="url-icon">'
    '<img alt="[打call]" src="https://h5.sinaimg.cn/thumb150/emoji.png"/></span> '
    "时间：2026年8月29日16:00"
    "地点：广州天河"
    "入场：48.8 1D"
    '参演团队：<a href="//weibo.com/n/NEXUS_Official">@NEXUS_Official</a> '
    '<a href="//weibo.com/n/Ventur_Official">@Ventur_Official</a> '
    '<a href="//weibo.com/n/Ascendant_official">@Ascendant_official</a> '
    "#广州地偶#"
)

# ---- 真实风格文案 ②：生诞 SP + 无料入场（含转义实体与 emoji 字段标记）----
TEXT_REX = (
    '扬帆！启航！<span class="url-icon">'
    '<img alt="[给你小心心]" src="https://h5.sinaimg.cn/thumb150/heart.png"/></span>✦ '
    "REX狂想夜 邓晴桦&amp;鸣岐 生日SP "
    "📅 2026.09.24 🕖 19:00 入场 "
    "📍 广州市越秀区北京路纵一咖啡（2F）"
    "🎫 门票：无料入场"
)

# ---- 文案 ③：乐队场（价格区间 + 明确阵容标签）----
TEXT_BAND = "8月30日 19:30 广州 SDlivehouse 门票：预售80 现场100 演出乐队：@Band_A @Band_B"

# ---- 文案 ④：日常博文（有日期词但没有场地/阵容/票价，必须被丢弃）----
TEXT_NOISE = "今天天气不错，出门散步🌤"


def _mblog(
    mid: str,
    text: str,
    created_at: str,
    screen_name: str,
    uid: int,
    *,
    pics: list[dict] | None = None,
    **extra: object,
) -> dict:
    """按实测结构拼一条 mblog。"""
    payload = {
        "id": mid,
        "created_at": created_at,
        "text": text,
        "user": {"id": uid, "screen_name": screen_name},
        "pics": pics or [],
        "reposts_count": 12,
        "comments_count": 3,
        "attitudes_count": 40,
    }
    payload.update(extra)
    return payload


def _cards(*items: dict, ok: int = 1) -> dict:
    """包成实测响应外壳；博文既可能直接在 cards 同层，也可能嵌在 card_group 里。"""
    return {"ok": ok, "data": {"cards": list(items)}}


PIC_ICON = {"url": "https://h5.sinaimg.cn/thumb150/icon.png",
            "large": {"url": "https://h5.sinaimg.cn/thumb150/icon.png"}}
PIC_POSTER = {"url": "https://wx1.sinaimg.cn/orj360/poster.jpg",
              "large": {"url": "https://wx1.sinaimg.cn/large/poster.jpg"}}

PAYLOAD = _cards(
    {"card_type": 9, "mblog": _mblog(
        "5000000000000001", TEXT_ZTI, CREATED_ZTI, "YELO_Official", 7000000001,
        pics=[PIC_ICON, PIC_POSTER])},
    {"card_type": 9, "mblog": _mblog(
        "5000000000000002", TEXT_REX, CREATED_REX, "REX狂想曲", 7000000002)},
    {"card_type": 9, "mblog": _mblog(
        "5000000000000003", TEXT_NOISE, "Mon Aug 24 10:00:00 +0800 2026", "路人甲", 7000000003)},
    {"card_type": 11, "card_group": [
        {"card_type": 9, "mblog": _mblog(
            "5000000000000004", TEXT_BAND, "Sun Aug 23 10:00:00 +0800 2026",
            "SDlivehouse", 7000000004)}]},
)


# --------------------------------------------------------------------------- #
# HTML 清洗
# --------------------------------------------------------------------------- #

class TestStripHtml:
    def test_removes_tags_and_keeps_text(self):
        assert w.strip_html("时间：2026年8月29日16:00") == "时间：2026年8月29日16:00"
        out = w.strip_html('Z.T IDOL PARTY<a href="//weibo.com/n/x">@NEXUS_Official</a>')
        assert out == "Z.T IDOL PARTY@NEXUS_Official"

    def test_keeps_emoji_alt_text(self):
        """微博表情是 `<span class="url-icon"><img alt="[doge]">`，
        alt 里的方括号文字是语义（实测），要保留；标签本身不能留在文本里。"""
        out = w.strip_html('<span class="url-icon"><img alt="[doge]" src="x.png"/></span>笑死')
        assert out == "[doge]笑死"
        assert "<" not in out

    def test_unescapes_entities(self):
        assert w.strip_html("邓晴桦&amp;鸣岐") == "邓晴桦&鸣岐"
        assert w.strip_html("&lt;SP&gt;") == "<SP>"

    def test_br_becomes_newline(self):
        # 换行要留给「取首行当标题」用
        assert w.strip_html("第一行<br/>第二行") == "第一行\n第二行"

    def test_removes_zero_width_and_script(self):
        out = w.strip_html("<script>alert(1)</script>正\u200b文")
        assert "alert" not in out and "\u200b" not in out
        assert out.endswith("正文")

    def test_empty_input(self):
        assert w.strip_html(None) == ""
        assert w.strip_html("") == ""


# --------------------------------------------------------------------------- #
# created_at 解析
# --------------------------------------------------------------------------- #

class TestCreatedAt:
    def test_weibo_format_year_at_end(self):
        """⚠️ 实测坑：`"Wed Aug 26 18:35:15 +0800 2026"` 的年份在**最后**，
        `datetime.fromisoformat` / `dateutil` 都解析不出来（会退化成当前年）。"""
        got = w.parse_weibo_created_at(CREATED_ZTI)
        assert got == dt.datetime(2026, 8, 26, 18, 35, 15, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    def test_unix_timestamp(self):
        got = w.parse_weibo_created_at(1756204515)
        assert got is not None and got.year == 2025

    def test_relative_minutes_and_hours(self):
        now = w.now_cst()
        got = w.parse_weibo_created_at("10分钟前")
        assert got is not None
        assert abs((now - got).total_seconds() - 600) < 5

        got = w.parse_weibo_created_at("3小时前")
        assert got is not None
        assert abs((now - got).total_seconds() - 3 * 3600) < 5

    def test_just_now(self):
        assert w.parse_weibo_created_at("刚刚") is not None

    def test_yesterday(self):
        today = w.now_cst().date()
        got = w.parse_weibo_created_at("昨天 12:30")
        assert got is not None
        assert got.date() == today - dt.timedelta(days=1)
        assert (got.hour, got.minute) == (12, 30)

    @pytest.mark.parametrize("value", [None, "", "   ", "不是时间", {}, []])
    def test_bad_input_returns_none(self, value):
        assert w.parse_weibo_created_at(value) in (None,) or isinstance(
            w.parse_weibo_created_at(value), dt.datetime
        )


# --------------------------------------------------------------------------- #
# 搜索 payload → ParsedOccurrence
# --------------------------------------------------------------------------- #

class TestParseSearchPayload:
    def test_parses_all_real_posts(self):
        occ = w.parse_search_payload(PAYLOAD)
        assert [o.external_id for o in occ] == [
            "weibo:5000000000000001",
            "weibo:5000000000000002",
            "weibo:5000000000000004",   # card_group 里的嵌套博文也要能挖出来
        ]
        assert all(o.source_code == "weibo" for o in occ)

    def test_chinese_datetime_parsing(self):
        """「2026年8月29日16:00」→ 精确时间；「2026.09.24 + 19:00 入场」同理。"""
        occ = {o.external_id: o for o in w.parse_search_payload(PAYLOAD)}
        zti = occ["weibo:5000000000000001"]
        assert zti.start_at == dt.datetime(2026, 8, 29, 16, 0, tzinfo=CST)
        assert zti.date_precision == "exact"

        rex = occ["weibo:5000000000000002"]
        assert rex.start_at == dt.datetime(2026, 9, 24, 19, 0, tzinfo=CST)
        assert rex.date_precision == "exact"

    def test_lineup_extraction_from_at_tokens(self):
        zti = next(o for o in w.parse_search_payload(PAYLOAD)
                   if o.external_id == "weibo:5000000000000001")
        assert [a.name_raw for a in zti.artists] == [
            "NEXUS_Official", "Ventur_Official", "Ascendant_official",
        ]
        # @ 前缀必须剥掉，顺序即 billing_order；name_norm 会去掉下划线（normalize_name 的既定行为）
        assert all(not a.name_raw.startswith("@") for a in zti.artists)
        assert [a.billing_order for a in zti.artists] == [1, 2, 3]
        assert zti.artists[0].name_norm == "nexusofficial"

    def test_city_and_venue(self):
        occ = {o.external_id: o for o in w.parse_search_payload(PAYLOAD)}
        zti = occ["weibo:5000000000000001"]
        assert zti.city == "广州"
        assert zti.venue_raw == "广州天河"

        rex = occ["weibo:5000000000000002"]
        assert rex.city == "广州"
        # 「广州市越秀区北京路纵一咖啡（2F）」→ 去城市前缀（NFKC 会把全角括号折成半角）
        assert rex.venue_raw == "越秀区北京路纵一咖啡(2F)"

    def test_prices_and_free_entry(self):
        occ = {o.external_id: o for o in w.parse_search_payload(PAYLOAD)}
        zti = occ["weibo:5000000000000001"]
        # ⚠️ 实测坑：「入场：48.8 1D」里的 1D 是票种（1 Drink），不是价格 → 只有 48.8
        assert zti.price_min == zti.price_max == 48.8

        rex = occ["weibo:5000000000000002"]
        assert rex.is_free is True
        assert rex.price_min == rex.price_max == 0.0
        assert rex.tickets[0].tier_type == "无料"

    def test_price_range_from_band_post(self):
        band = next(o for o in w.parse_search_payload(PAYLOAD)
                    if o.external_id == "weibo:5000000000000004")
        assert band.price_min == 80.0 and band.price_max == 100.0
        assert band.city == "广州"
        assert band.is_idol is False

    def test_idol_classification(self):
        occ = {o.external_id: o for o in w.parse_search_payload(PAYLOAD)}
        # 生诞 SP → 地偶生日场
        rex = occ["weibo:5000000000000002"]
        assert rex.is_idol is True
        assert rex.kind == "idol_birthday"
        assert "生诞" in rex.tags
        # 联合公演（IDOL PARTY + 参演团队）→ 地偶
        assert occ["weibo:5000000000000001"].is_idol is True

    def test_hashtags_merged_into_tags(self):
        zti = next(o for o in w.parse_search_payload(PAYLOAD)
                   if o.external_id == "weibo:5000000000000001")
        assert "广州地偶" in zti.tags

    def test_title_is_cleaned_not_whole_text(self):
        """标题不能被整段文案污染（实测会把 80+ 字符的整段塞进 title_display）。

        注意：表情的 `alt`（这里是 `[打call]`）是**语义**，按实测被保留在文本里，
        所以标题里会带它 —— 断言只锁「没有被「时间：/地点：/入场：」污染」。
        """
        occ = {o.external_id: o for o in w.parse_search_payload(PAYLOAD)}
        zti = occ["weibo:5000000000000001"]
        assert zti.title_display.startswith("Z.T IDOL PARTY")
        assert "时间" not in zti.title_display
        assert "地点" not in zti.title_display
        assert "入场" not in zti.title_display
        assert len(zti.title_display) < 40
        rex = occ["weibo:5000000000000002"]
        assert "REX狂想夜" in rex.title_display
        assert "2026.09.24" not in rex.title_display
        assert "无料" not in rex.title_display
        assert "📍" not in rex.title_display

    def test_poster_url_skips_emoji_icons(self):
        """⚠️ 实测坑：pics 里混着 `url-icon` / `thumb150` 表情图，
        真配图在 `pics[].large.url`。取错会把海报存成表情图。"""
        zti = next(o for o in w.parse_search_payload(PAYLOAD)
                   if o.external_id == "weibo:5000000000000001")
        assert zti.poster_url == "https://wx1.sinaimg.cn/large/poster.jpg"
        assert zti.extra["pics"] == ["https://wx1.sinaimg.cn/large/poster.jpg"]

    def test_extra_metadata(self):
        zti = next(o for o in w.parse_search_payload(PAYLOAD)
                   if o.external_id == "weibo:5000000000000001")
        assert zti.extra["author"] == "YELO_Official"
        assert zti.extra["author_uid"] == "7000000001"
        assert zti.extra["created_at"] == "2026-08-26T18:35:15+08:00"
        assert zti.extra["created_at_raw"] == CREATED_ZTI
        assert zti.extra["reposts_count"] == 12
        assert zti.extra["is_retweet"] is False
        assert zti.extra["detail_url"] == "https://m.weibo.cn/detail/5000000000000001"

    def test_keyword_carried_into_extra(self):
        occ = w.parse_search_payload(PAYLOAD, keyword="广州地偶")
        assert all(o.extra["keyword"] == "广州地偶" for o in occ)

    def test_default_city_used_when_text_has_no_city(self):
        payload = _cards({"card_type": 9, "mblog": _mblog(
            "6000000000000001", "8月30日 19:30 开演 门票：预售80", CREATED_ZTI,
            "某场地", 8000000001)})
        occ = w.parse_search_payload(payload, city="深圳")
        assert occ and occ[0].city == "深圳"

    def test_relative_time_uses_post_time_not_now(self):
        """⚠️ 幂等性：`now` 必须取博文发出时间，否则「昨天」会随抓取时刻漂移，
        同一份快照重放会得出不同日期。"""
        payload = _cards({"card_type": 9, "mblog": _mblog(
            "6000000000000002", "昨天 20:00 广州 SDlivehouse 演出乐队：@Band_A",
            "Tue Aug 25 09:12:00 +0800 2026", "SDlivehouse", 8000000002)})
        first = w.parse_search_payload(payload)[0].start_at
        second = w.parse_search_payload(payload)[0].start_at
        assert first == second == dt.datetime(2026, 8, 24, 20, 0, tzinfo=CST)

    def test_noise_post_is_dropped(self):
        """有「今天」这种相对日期词但没有场地/阵容/票价的日常博文必须丢掉。"""
        occ = w.parse_search_payload(PAYLOAD)
        assert not any(o.external_id == "weibo:5000000000000003" for o in occ)

    def test_dedupes_same_mblog(self):
        mblog = _mblog("7000000000000001", TEXT_REX, CREATED_REX, "REX狂想曲", 7000000000000002)
        payload = _cards({"card_type": 9, "mblog": mblog}, {"card_type": 9, "mblog": mblog})
        assert len(w.parse_search_payload(payload)) == 1

    def test_external_id_stable(self):
        a = [o.external_id for o in w.parse_search_payload(PAYLOAD)]
        b = [o.external_id for o in w.parse_search_payload(PAYLOAD)]
        assert a == b

    def test_retweet_flag(self):
        payload = _cards({"card_type": 9, "mblog": _mblog(
            "8000000000000001", TEXT_REX, CREATED_REX, "转发的人", 9000000001,
            retweeted_status={"id": "123"})})
        occ = w.parse_search_payload(payload)
        assert occ[0].extra["is_retweet"] is True

    # ---- 脏数据 / 边界 ----

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"ok": -1, "data": {"cards": []}},          # 实测直连接口就是这么返回的
            {"ok": 0},
            {"ok": 1},
            {"ok": 1, "data": None},
            {"ok": 1, "data": {}},
            {"ok": 1, "data": {"cards": None}},
            {"ok": 1, "data": {"cards": [None, 1, "x"]}},
            {"ok": 1, "data": {"cards": [{"card_type": 9}]}},          # 缺 mblog
            {"ok": 1, "data": {"cards": [{"card_type": 9, "mblog": {"id": "1"}}]}},  # 缺 text
            [],
            "not a dict",
        ],
    )
    def test_malformed_payload_returns_empty(self, payload):
        assert w.parse_search_payload(payload) == []

    def test_unparsable_created_at_falls_back_to_now(self):
        payload = _cards({"card_type": 9, "mblog": _mblog(
            "9000000000000001", TEXT_REX, "时间未知", "REX狂想曲", 9000000000000002)})
        occ = w.parse_search_payload(payload)
        assert occ and occ[0].start_at == dt.datetime(2026, 9, 24, 19, 0, tzinfo=CST)
        assert occ[0].extra["created_at"] is None


# --------------------------------------------------------------------------- #
# 账号发现
# --------------------------------------------------------------------------- #

class TestDiscoverAccounts:
    def test_discovers_authors(self):
        accounts = w.discover_accounts(PAYLOAD)
        ids = [a["external_id"] for a in accounts]
        assert ids == [
            "uid:7000000001", "uid:7000000002", "uid:7000000003", "uid:7000000004",
        ]
        names = {a["display_name"] for a in accounts}
        assert {"YELO_Official", "REX狂想曲", "SDlivehouse"} <= names

    def test_account_fields_match_channel_contract(self):
        """返回的 dict 要能直接喂 ChannelIn（见 app/models.py）。"""
        account = next(a for a in w.discover_accounts(PAYLOAD)
                       if a["external_id"] == "uid:7000000001")
        assert account["platform"] == "weibo"
        assert account["channel_type"] == "idol_group"
        assert account["fetch_mode"] == "cdp_xhr"
        assert account["match_pattern"] == w.SEARCH_XHR_MATCH
        assert account["is_idol"] is True
        assert isinstance(account["priority"], int)
        assert account["discovered_from"] == "weibo_search"
        # ⚠️ url_template 指向**搜索页**：个人主页实测强制跳登录，不能用
        assert account["url_template"] == w.SEARCH_URL
        assert "search" in account["url_template"]
        assert "{uid}" not in account["url_template"]

    def test_accounts_deduped(self):
        accounts = w.discover_accounts(PAYLOAD)
        assert len(accounts) == len({a["external_id"] for a in accounts})

    def test_city_from_account_name(self):
        payload = _cards({"card_type": 9, "mblog": _mblog(
            "9100000000000001", TEXT_REX, CREATED_REX, "广州地偶揭示板", 9100000000000002)})
        account = w.discover_accounts(payload)[0]
        assert account["city"] == "广州"

    @pytest.mark.parametrize("payload", [{}, {"ok": -1}, {"ok": 1, "data": None}, [], "x"])
    def test_malformed_returns_empty(self, payload):
        assert w.discover_accounts(payload) == []

    def test_user_without_name_skipped(self):
        payload = _cards({"card_type": 9, "mblog": _mblog(
            "9200000000000001", TEXT_REX, CREATED_REX, "", 9200000000000002)})
        assert w.discover_accounts(payload) == []


# --------------------------------------------------------------------------- #
# 搜索 URL 与关键词表
# --------------------------------------------------------------------------- #

class TestSearchUrl:
    def test_keyword_is_url_encoded(self):
        """⚠️ `containerid` 整体要 URL 编码：把中文直接拼进去会被 m 站拒掉（空卡片）。"""
        url = w.search_url("广州地偶")
        assert url.startswith("https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D")
        assert "%E5%B9%BF%E5%B7%9E%E5%9C%B0%E5%81%B6" in url
        assert "广州地偶" not in url
        assert "%3D" in url and "%26" in url

    def test_html_escapable_chars_encoded(self):
        url = w.search_url("广州 livehouse")
        assert " " not in url

    def test_keywords_still_cover_documented_cases(self):
        """关键词表**不冻结具体列表**，而是断言它仍覆盖设计要求的几类来源。

        为什么改成这样：原先断言写死了 8 个词，扩展词表就会挂测试。
        但词表本来就该随实测（`scripts/probe_weibo_keywords.py`）演进 ——
        该守的是**覆盖范围**，不是具体字符串。

        设计要求（维护者提出）：
          1. 微博上的地偶信息（含各城，不只广州/深圳）
          2. ACG 乐队 / 同人 演出
          3. **免费场地**（地王广场这类商场中庭，不上售票平台）
        """
        kws = w.SEARCH_KEYWORDS

        # 1) 地偶：至少覆盖广州 + 两个其他城市
        assert any("广州" in k and "地偶" in k for k in kws), "缺广州地偶"
        other_cities = [
            c for c in ("深圳", "珠海", "东莞", "佛山", "中山", "惠州", "汕头")
            if any(c in k and "地偶" in k for k in kws)
        ]
        assert len(other_cities) >= 2, f"地偶覆盖城市太少：{other_cities}"

        # 2) ACG / 同人 演出
        assert any("ACG" in k for k in kws), "缺 ACG 关键词"
        assert any("同人" in k or "术力口" in k for k in kws), "缺同人/术力口关键词"

        # 3) 免费场地 + 聚合速览（实测性价比最高的两类）
        assert any("地王广场" in k for k in kws), "缺免费场地（地王广场）关键词"
        assert any("偶活" in k or "免费" in k for k in kws), "缺聚合速览/免费公演关键词"

        assert w.SEARCH_XHR_MATCH == "/api/container"


class TestPayloadsFromHits:
    def test_parses_json_bodies(self):
        hits = [{"url": "https://m.weibo.cn/api/container/getIndex?x=1",
                 "body": '{"ok":1,"data":{"cards":[]}}'},
                {"url": "https://m.weibo.cn/api/container/getIndex?x=2", "body": "not json"},
                {"url": "https://m.weibo.cn/other", "body": "{}"}]
        payloads = w.payloads_from_hits(hits)
        # 非 JSON 的被跳过；能解析的都进来（是否相关由 ok 字段决定）
        assert len(payloads) == 2
        assert payloads[0]["ok"] == 1

    def test_empty_hits(self):
        assert w.payloads_from_hits([]) == []
        assert w.payloads_from_hits(None) == []


# --------------------------------------------------------------------------- #
# 调度层接口契约（app/pipeline.py 直接用这些）
# --------------------------------------------------------------------------- #

class TestDispatchContract:
    """`app/pipeline.py` 里写的是 `WeiboCollector().collect(browser, cities=cities)`。"""

    def test_collect_signature_is_browser_first(self):
        import inspect

        sig = inspect.signature(w.WeiboCollector.collect)
        params = list(sig.parameters)
        assert params[1] == "browser", f"第一个位置参数必须是 browser，实际是 {params[1]}"
        assert params[2] == "cities"
        assert sig.parameters["cities"].default is None

    @pytest.mark.asyncio
    async def test_collect_does_not_multiply_cities_by_keywords(self, monkeypatch):
        """⚠️ 关键词自带城市，不能再乘一遍城市列表（否则 CDP 会话数 ×5）。"""
        calls: list[str] = []

        async def fake_collect_all(self, keywords=None, *, city=None, wait=None):
            calls.append(str(city))
            return []

        monkeypatch.setattr(w.WeiboCollector, "collect_all", fake_collect_all)
        collector = w.WeiboCollector()
        occ = await collector.collect(browser=None, cities=["广州", "深圳", "佛山"])
        assert occ == []
        assert calls == ["广州"], f"每个城市都跑了一遍关键词表：{calls}"

    def test_collector_exposes_expected_attributes(self):
        collector = w.WeiboCollector()
        assert collector.source_code == "weibo"
        assert hasattr(collector, "collect_search")
        assert hasattr(collector, "collect_all")
        assert hasattr(collector, "discover")
        assert hasattr(collector, "aclose")
