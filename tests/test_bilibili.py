"""B 站采集器单测。

覆盖两条通道的**纯函数**部分（不需要浏览器/网络）：

  * `parse_listv2_payload`  —— 会员购 listV2 JSON → ParsedOccurrence
  * `parse_dynamic_text`    —— UP 主动态渲染文本 → ParsedOccurrence

fixture 按 docs/02-采集方案-全自动.md §3.3 的**实测结构**构造：
字段名一律用真实字段名（`project_name / venue_name / price_low / price_high /
sale_start_time / start_time / end_time / cover / jump_url / city / district_name /
tags / id`），**不编造字段**；只有取值做了精简。
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.collectors import bilibili as b

CST = dt.timezone(dt.timedelta(hours=8))
# 固定「现在」：实测调研时间是 2026-10-08，用它断言相对时间与开票状态
NOW = dt.datetime(2026, 10, 8, 20, 0, tzinfo=CST)


# --------------------------------------------------------------------------- #
# 会员购 fixture（结构照抄实测响应）
# --------------------------------------------------------------------------- #

def _listv2(*items: dict) -> dict:
    """包成实测的响应外壳 `{"errno":0,"data":{"result":[...]}}`。"""
    return {"errno": 0, "data": {"result": list(items)}}


ITEM_RANGE = {
    "project_name": "卡拉彼丘同人ONLY·S1",
    "venue_name": "广州市·CH8蛙厂演艺中心（大学城店）",
    "price_low": 7800,        # 分 → ¥78.00（实测单位是分）
    "price_high": 12800,      # 分 → ¥128.00
    "sale_start_time": "2026-08-01 12:00:00",
    "start_time": "2026.10.01 10:00",   # 实测就是点号格式
    "end_time": "2026.10.01 18:00",
    "cover": "//i0.hdslb.com/bfs/ticket/cover1.jpg",
    "jump_url": "//show.bilibili.com/platform/detail.html?id=800001",
    "city": "广州",
    "district_name": "番禺区",
    "tags": ["同人", "ACG"],
    "id": 800001,
}

ITEM_SINGLE = {
    "project_name": "2026型月同人ONLY·Lostbelt",
    "venue_name": "广州市·琶洲保利世贸博览馆",
    "price_low": 6000,
    "price_high": 6000,
    "sale_start_time": "2026-11-01 12:00:00",   # 未来 → 尚未开票
    "start_time": "2026.11.14 09:30",
    "end_time": "2026.11.14 17:00",
    "cover": "",
    "jump_url": "",
    "city": "广州",
    "district_name": "海珠区",
    "tags": [],
    "id": 800002,
}

ITEM_FREE = {
    "project_name": "金牌得主同人only",
    "venue_name": "广州市·MAO Livehouse广州（永庆坊店）",
    "price_low": 0,
    "price_high": 0,
    "sale_start_time": "2026-09-01 12:00:00",
    "start_time": "2026.10.05 13:00",
    "end_time": "",
    "cover": "",
    "jump_url": "",
    "city": "广州",
    "district_name": "荔湾区",
    "tags": "同人",
    "id": 800003,
}

# 实测（2026-10-08 真机 CDP 抓到的 listV2 条目，字段按原样精简）：
#   * `start_time` / `end_time` **只有日期**，精确时刻在 `start_unix`
#   * `sale_start_time` 是 **unix 秒**（不是字符串）
#   * `city` 回的是「广州市」（带「市」）
#   * 同时有 `id` 与 `project_id`（值相同）
#   * 另有 `sale_flag`（「预售中」）、`third_category_name`（「Only同人展」）、`wish`、`venueId`
ITEM_LIVE = {
    "id": 1005948,
    "project_id": 1005948,
    "project_name": "广州·卡拉彼丘同人ONLY·S1",
    "venue_name": "健康港星河COCO Park",
    "price_low": 7800,
    "price_high": 12800,
    "sale_start_time": 1789117200,
    "sale_end_time": 1792310400,
    "start_time": "2026-10-18",
    "end_time": "2026-10-18",
    "start_unix": 1792287000,
    "cover": "//i0.hdslb.com/bfs/openplatform/202609/x.png",
    "jump_url": (
        "https://mall.bilibili.com/neul-next/ticket-renovation/detail.html"
        "?id=1005948&noTitleBar=1"
    ),
    "city": "广州市",
    "cityId": 440100,
    "district_name": "荔湾区",
    "venueId": 17481,
    "tags": [{"name": "独家"}],
    "third_category_name": "Only同人展",
    "sale_flag": "预售中",
    "wish": 222,
    "coordinate": '{"type":"GD","coor":"113.261536,23.059092"}',
    "isFree": False,
}


# --------------------------------------------------------------------------- #
# 会员购：JSON → ParsedOccurrence
# --------------------------------------------------------------------------- #

class TestListV2Payload:
    def test_price_is_fen_converted_to_yuan(self):
        """⚠️ 实测坑：price_low/price_high 单位是分（7800 → ¥78.00）。

        不换算会把 ¥78 的票写成 ¥7800 —— 这是本采集器最容易犯的错，必须锁死。
        """
        occ = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0]
        assert occ.price_min == 78.0
        assert occ.price_max == 128.0
        assert [t.price for t in occ.tickets] == [78.0, 128.0]

    def test_fen_to_yuan_helper(self):
        assert b.fen_to_yuan(7800) == 78.0
        assert b.fen_to_yuan("12800") == 128.0
        assert b.fen_to_yuan(990) == 9.9
        assert b.fen_to_yuan(0) == 0.0
        assert b.fen_to_yuan(1) == 0.01          # ¥0.01 占位票
        assert b.fen_to_yuan(None) is None
        assert b.fen_to_yuan("") is None
        assert b.fen_to_yuan("abc") is None
        assert b.fen_to_yuan(-100) is None
        # 浮点误差不该漏出来
        assert b.fen_to_yuan(3333) == 33.33

    def test_price_range_and_single_price(self):
        range_occ = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0]
        assert range_occ.price_min == 78.0 and range_occ.price_max == 128.0
        assert [t.name_raw for t in range_occ.tickets] == ["最低价", "最高价"]

        single_occ = b.parse_listv2_payload(_listv2(ITEM_SINGLE), "广州")[0]
        assert single_occ.price_min == single_occ.price_max == 60.0
        assert len(single_occ.tickets) == 1
        assert single_occ.tickets[0].price == 60.0

    def test_free_when_price_zero(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_FREE), "广州")[0]
        assert occ.price_min == 0.0 and occ.price_max == 0.0
        assert occ.is_free is True
        assert occ.tickets[0].tier_type == "无料"

    def test_missing_price_leaves_none(self):
        item = dict(ITEM_SINGLE, price_low=None, price_high=None)
        occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
        assert occ.price_min is None and occ.price_max is None
        assert occ.tickets == []
        assert occ.is_free is False

    def test_reversed_range_is_swapped(self):
        item = dict(ITEM_RANGE, price_low=20000, price_high=9000)
        occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
        assert occ.price_min == 90.0 and occ.price_max == 200.0

    def test_is_idol_by_classify(self):
        """ONLY 同人场 → is_idol=True，kind 落到地偶族（classify 判定）。"""
        range_occ = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0]
        assert range_occ.is_idol is True
        assert range_occ.kind == "idol_taiban"
        assert "Only" in range_occ.tags
        assert "同人" in range_occ.tags and "ACG" in range_occ.tags

        # 纯乐队/Livehouse 标题不该被判成地偶
        band = dict(ITEM_SINGLE, project_name="文雀【两语】巡演 广州站", tags=[], id=800009)
        band_occ = b.parse_listv2_payload(_listv2(band), "广州")[0]
        assert band_occ.is_idol is False
        assert band_occ.kind == "tour_stop"

    def test_band_vs_idol_on_plain_livehouse_title(self):
        """没有任何地偶词、只有场地名的条目不当地偶（宁缺勿错）。"""
        item = dict(ITEM_SINGLE, project_name="周末室内乐之夜", tags=[], id=800010)
        occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
        assert occ.is_idol is False

    def test_external_id_is_stable_and_namespaced(self):
        """external_id 必须可复现（同输入同输出），否则每轮采集都会新增重复场次。"""
        first = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0].external_id
        second = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0].external_id
        assert first == second == "show:800001"
        assert b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0].source_code == "bilibili"

    def test_items_deduplicated_by_external_id(self):
        payload = _listv2(ITEM_RANGE, dict(ITEM_RANGE))
        assert len(b.parse_listv2_payload(payload, "广州")) == 1

    def test_pages_merge_and_dedupe(self):
        page1 = _listv2(ITEM_RANGE)
        page2 = _listv2(dict(ITEM_RANGE), ITEM_SINGLE)
        occ = b.parse_listv2_pages([page1, page2], "广州")
        assert [o.external_id for o in occ] == ["show:800001", "show:800002"]

    def test_time_parsing_dotted_format(self):
        """带时刻的 `start_time`（「2026.10.01 10:00」）→ exact。"""
        occ = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0]
        assert occ.start_at == dt.datetime(2026, 10, 1, 10, 0, tzinfo=CST)
        assert occ.end_at == dt.datetime(2026, 10, 1, 18, 0, tzinfo=CST)
        assert occ.date_precision == "exact"

    def test_date_only_when_no_clock_time(self):
        """只有日期（无 start_unix、无时刻）→ 精度必须是 date_only 而不是假装 00:00 开演。"""
        item = dict(ITEM_SINGLE, start_time="2026-09-19", end_time="", start_unix=None, id=800030)
        occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
        assert occ.date_precision == "date_only"
        assert occ.start_at == dt.datetime(2026, 9, 19, 0, 0, tzinfo=CST)

    def test_empty_end_time_is_none(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_FREE), "广州")[0]
        assert occ.start_at is not None
        assert occ.end_at is None

    def test_status_announced_before_sale_start(self):
        """开票时间在未来 → announced；已过 → on_sale。"""
        occ = b.parse_listv2_payload(_listv2(ITEM_SINGLE), "广州")[0]
        assert occ.status == "announced"
        assert "2026-11-01" in (occ.status_note or "")

        past = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0]
        assert past.status == "on_sale"

    def test_sold_out_flag(self):
        item = dict(ITEM_RANGE, is_sold_out=1, id=800011)
        assert b.parse_listv2_payload(_listv2(item), "广州")[0].status == "sold_out"

    def test_venue_city_and_poster(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_RANGE), "广州")[0]
        assert occ.city == "广州"
        # 场地保留城市前缀与门牌/馆别（clean_venue 只在后面跟地址时才剥城市）
        assert occ.venue_raw == "广州CH8蛙厂演艺中心(大学城店)"
        # 协议相对 URL 必须补上 scheme，否则前端拼不出图
        assert occ.poster_url == "https://i0.hdslb.com/bfs/ticket/cover1.jpg"
        assert occ.source_url == "https://show.bilibili.com/platform/detail.html?id=800001"
        assert occ.raw["price_low_fen"] == 7800
        assert occ.raw["district_name"] == "番禺区"

    def test_fallback_detail_url_when_jump_url_missing(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_SINGLE), "广州")[0]
        assert occ.source_url == "https://show.bilibili.com/platform/detail.html?id=800002"

    def test_city_falls_back_to_argument(self):
        item = dict(ITEM_RANGE, city="", id=800012)
        assert b.parse_listv2_payload(_listv2(item), "深圳")[0].city == "深圳"

    # ---- 脏数据不该抛异常 ----

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"errno": -1, "data": {"result": [ITEM_RANGE]}},
            {"errno": 0},
            {"errno": 0, "data": None},
            {"errno": 0, "data": {"result": None}},
            {"errno": 0, "data": {"result": "oops"}},
            {"errno": 0, "data": {"result": [None, 123, "x", {}]}},
            {"errno": 0, "data": {"result": [{"id": 1}]}},            # 缺 project_name
            {"errno": 0, "data": {"result": [{"project_name": "无 id"}]}},
            [],
            "not a dict",
        ],
    )
    def test_malformed_payload_returns_empty(self, payload):
        assert b.parse_listv2_payload(payload, "广州") == []

    def test_partial_items_still_parsed(self):
        payload = _listv2({}, ITEM_RANGE, {"id": 2})
        occ = b.parse_listv2_payload(payload, "广州")
        assert [o.external_id for o in occ] == ["show:800001"]

    def test_no_venue_no_price_still_ok(self):
        item = {"project_name": "某同人ONLY", "id": 800013, "venue_name": "", "price_low": None}
        occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
        assert occ.venue_raw is None
        assert occ.price_min is None
        assert occ.date_precision == "tbd"


# --------------------------------------------------------------------------- #
# 会员购：真机实测条目（字段形态与文档不同，专门锁死）
# --------------------------------------------------------------------------- #

class TestLiveItemShape:
    def test_start_unix_gives_real_clock_time(self):
        """⚠️ 实测坑：`start_time` 只有日期（`"2026-10-18"`），
        真正的时刻在 `start_unix`（1792287000 → 09:30+08:00）。
        只用 start_time 会把所有场次写成 00:00（把「几点开演」变成错的）。"""
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.start_at == dt.datetime(2026, 10, 18, 9, 30, tzinfo=CST)
        assert occ.date_precision == "exact"
        # 若回归成「只读 start_time」，这里会是 00:00
        assert occ.start_at.hour != 0

    def test_sale_start_time_is_unix_seconds(self):
        """⚠️ 实测：`sale_start_time` 返回 **unix 秒**（1789117200），不是字符串。"""
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.raw["sale_start_time"] == 1789117200
        assert occ.status == "on_sale"
        assert occ.status_note == "预售中"

    def test_city_suffix_stripped(self):
        """实测 city 回的是「广州市」，要归一成「广州」。"""
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.city == "广州"

    def test_project_id_and_id_both_accepted(self):
        assert b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0].external_id == "show:1005948"
        only_project_id = dict(ITEM_LIVE)
        only_project_id.pop("id")
        assert (
            b.parse_listv2_payload(_listv2(only_project_id), "广州")[0].external_id
            == "show:1005948"
        )

    def test_title_strips_city_display_prefix(self):
        """标题前缀「广州·」是会员购的展示写法，标题本体不该带它。"""
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.title_display == "卡拉彼丘同人ONLY·S1"
        assert occ.title_raw == "广州·卡拉彼丘同人ONLY·S1"   # 原始写法保留在 title_raw

    def test_third_category_name_feeds_classification(self):
        """`third_category_name="Only同人展"` 是真实品类 → 必须参与分类。

        ⚠️ 本用例的期望在加入「展会识别」后调整过（原期望是 `is_idol=True`）：
        「同人ONLY展」是**展览**而非演出，本项目明确「非演出内容默认不进日历」，
        因此它被归为 `other` 且不带垂类标记 —— 由 `exclude_other` 挡在日历之外。

        但品类名**仍然参与分类**这一点必须成立（否则这个字段就白读了）：
        它会被写进 `tags`，并在 `raw` 里留档供后续复核。
        """
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.kind == "other", "同人展是展览，不是演出"
        assert occ.is_idol is False
        assert "Only同人展" in occ.tags, "品类名必须进入 tags（参与分类的证据）"
        assert "独家" in occ.tags
        assert occ.raw.get("third_category_name") == "Only同人展"

    def test_mall_detail_url_preserved(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.source_url.startswith("https://mall.bilibili.com/neul-next/ticket-renovation/")

    def test_extra_metadata_from_live_fields(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert occ.raw["venue_id"] == 17481
        assert occ.raw["wish"] == 222
        assert occ.raw["sale_flag"] == "预售中"
        assert occ.raw["third_category_name"] == "Only同人展"
        assert occ.raw["district_name"] == "荔湾区"
        assert occ.raw["project_id"] == 1005948

    def test_price_fen_still_converted_for_live_item(self):
        occ = b.parse_listv2_payload(_listv2(ITEM_LIVE), "广州")[0]
        assert (occ.price_min, occ.price_max) == (78.0, 128.0)

    def test_non_numeric_price_fields_ignored(self):
        """`price_text` 之类的非数字字段不能被当成分。"""
        item = dict(ITEM_SINGLE, price_text="¥60起", id=800020)
        occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
        assert occ.price_min == 60.0     # 仍以 price_low 为准（6000 分）

    def test_sale_flag_maps_to_status(self):
        for flag, expected in (
            ("预售中", "on_sale"), ("热卖中", "on_sale"),
            ("即将开售", "announced"), ("已售罄", "sold_out"), ("已结束", "finished"),
        ):
            item = dict(ITEM_LIVE, sale_flag=flag)
            occ = b.parse_listv2_payload(_listv2(item), "广州")[0]
            assert occ.status == expected, flag

    def test_empty_sale_flag_falls_back_to_sale_start_time(self):
        future = dict(ITEM_LIVE, sale_flag="", sale_start_time=1800000000)  # 2027 年
        assert b.parse_listv2_payload(_listv2(future), "广州")[0].status == "announced"
        past = dict(ITEM_LIVE, sale_flag="", sale_start_time=1789117200)
        assert b.parse_listv2_payload(_listv2(past), "广州")[0].status == "on_sale"


# --------------------------------------------------------------------------- #
# area 码
# --------------------------------------------------------------------------- #

class TestAreaCodes:
    def test_guangdong_codes(self):
        """实测：点「广州」后发出的是 area=440100（行政区划码，不是秀动的 cityCode）。"""
        assert b.area_code_for("广州") == "440100"
        assert b.area_code_for("深圳") == "440300"
        assert b.area_code_for("佛山") == "440600"
        assert b.area_code_for("东莞") == "441900"
        assert b.area_code_for("珠海") == "440400"

    def test_unknown_city_returns_none(self):
        # 没有实测码的城市必须返回 None（调用方跳过，绝不瞎猜）
        assert b.area_code_for("中山") is None
        assert b.area_code_for("") is None
        assert b.area_code_for("   ") is None

    def test_city_whitespace_tolerated(self):
        assert b.area_code_for(" 广州 ") == "440100"


# --------------------------------------------------------------------------- #
# UP 主动态：渲染文本 → ParsedOccurrence
# --------------------------------------------------------------------------- #

# 按实测 B10Live（mid=510676514）动态页的 innerText 形态构造（精简）
DYNAMIC_TEXT = """B10Live
深圳B10现场 B10 Live官方账号 官方网站 www.b10live.cn
B10Live
4小时前 · 投稿了视频 01:01:52

13th JAZZ 节目前瞻｜Hungry Ghosts: 疾风烈火
✦ 挪威自由即兴王牌鼓手 Paal Nilssen-Love 领衔
时间：2026年11月7日 20:00
地点：深圳B10现场
票价：预售180 现场220

昨天 12:30

海朋森 ｢锈湖｣ 广州专场
11月14日 周六 20:00 开演，19:00 开场
深圳B10现场
演出乐队：海朋森 晕盖

08-20

今晚的演出非常精彩，感谢大家！
"""


class TestDynamicText:
    def test_blocks_split_by_time_marker(self):
        occ = b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
        # 三个含日期的块：13th JAZZ / 11月14日 / （08-20 那条没有日期，被丢掉）
        assert len(occ) == 2
        titles = [o.title_display for o in occ]
        assert any("Hungry Ghosts" in t for t in titles)
        assert any("海朋森" in t for t in titles)

    def test_source_and_external_id(self):
        occ = b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
        first = occ[0]
        assert first.source_code == "bilibili"
        assert first.source_url == "https://space.bilibili.com/510676514/dynamic"
        assert first.external_id.startswith("dynamic:510676514:")
        assert first.raw["mid"] == "510676514"

    def test_external_id_is_stable(self):
        a = [o.external_id for o in b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)]
        b_ = [o.external_id for o in b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)]
        assert a == b_ and len(a) == len(set(a))

    def test_video_duration_not_parsed_as_open_time(self):
        """⚠️ 实测坑：动态头里的视频时长 `01:01:52` 会被 parse_datetime 当成开演时间
        （open_at 变成 01:01），必须先去媒体时长再解析。"""
        occ = b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
        assert all((o.open_at or o.start_at).hour != 1 for o in occ if o.start_at)
        jazz = next(o for o in occ if "Hungry Ghosts" in o.title_display)
        assert jazz.start_at == dt.datetime(2026, 11, 7, 20, 0, tzinfo=CST)

    def test_prices_and_venue(self):
        jazz = next(
            o for o in b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
            if "Hungry Ghosts" in o.title_display
        )
        assert jazz.city == "深圳"
        assert jazz.venue_raw == "深圳B10现场"
        assert jazz.price_min == 180.0 and jazz.price_max == 220.0
        assert {t.tier_type for t in jazz.tickets} == {"预售", "现场"}

    def test_open_and_start_labeled_times(self):
        second = next(
            o for o in b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
            if "海朋森" in o.title_display
        )
        assert second.start_at == dt.datetime(2026, 11, 14, 20, 0, tzinfo=CST)
        assert second.open_at == dt.datetime(2026, 11, 14, 19, 0, tzinfo=CST)

    def test_lineup_extracted(self):
        second = next(
            o for o in b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
            if "海朋森" in o.title_display
        )
        assert [a.name_raw for a in second.artists] == ["海朋森", "晕盖"]

    def test_block_without_date_is_dropped(self):
        """只有时间标记（08-20）没有活动日期的块必须丢掉，否则会灌出一堆 tbd 场次。"""
        occ = b.parse_dynamic_text(DYNAMIC_TEXT, 510676514, now=NOW)
        assert all(o.date_precision != "tbd" for o in occ)
        assert not any("感谢大家" in (o.title_display or "") for o in occ)

    def test_text_without_marker_returns_empty(self):
        assert b.parse_dynamic_text("只有正文，没有任何时间标记", 510676514, now=NOW) == []
        assert b.parse_dynamic_text("", 510676514, now=NOW) == []

    def test_looks_like_event_requires_explicit_date(self):
        """动态通道要求正文里**真的写了日期**（发布时间/视频时长不算）。"""
        fields = {"date_precision": "tbd"}
        assert b._looks_like_event(fields, "深圳B10现场 明天见", "4小时前") is False

        fields_ok = {"date_precision": "exact", "venue_raw": "深圳B10现场"}
        # 只有相对时间（昨天/今晚）而没有具体日期 → 丢弃
        assert b._looks_like_event(fields_ok, "深圳B10现场 昨晚的演出很棒", "昨天") is False
        # 正文里写了具体日期 + 有场地 → 保留
        assert b._looks_like_event(
            fields_ok, "2026年11月7日 20:00 深圳B10现场 演出", "4小时前"
        ) is True
        # 太短的块也丢掉
        assert b._looks_like_event(fields_ok, "短", "4小时前") is False

    def test_looks_like_event_yes_but_only_date_label(self):
        """11月7日（无「日」字也算）这种写法要认。"""
        fields = {"date_precision": "exact", "venue_raw": "深圳B10现场"}
        assert b._looks_like_event(fields, "11月7日 20:00 深圳B10现场", "昨天") is True

    def test_fallback_venue_from_plain_text(self):
        """没有「地点：」标签时，从文本里捞场地（B10 动态常只写「深圳B10现场」）。"""
        text = "3天前\n\n2026年11月20日 20:00 开演\n深圳B10现场\n演出乐队：某乐队"
        occ = b.parse_dynamic_text(text, 123, now=NOW)
        assert len(occ) == 1
        assert occ[0].venue_raw == "深圳B10现场"
        assert occ[0].city == "深圳"

    def test_url_in_block_becomes_source_url(self):
        text = "2天前\n\n11月20日 20:00 深圳B10现场\n购票 https://b23.tv/abc123"
        occ = b.parse_dynamic_text(text, 123, now=NOW)
        assert occ[0].source_url == "https://b23.tv/abc123"

    def test_mid_in_external_id_differs_per_account(self):
        a = b.parse_dynamic_text(DYNAMIC_TEXT, 1, now=NOW)[0].external_id
        c = b.parse_dynamic_text(DYNAMIC_TEXT, 2, now=NOW)[0].external_id
        assert a != c


# --------------------------------------------------------------------------- #
# 调度层接口契约（app/pipeline.py 直接用这些）
# --------------------------------------------------------------------------- #

class TestDispatchContract:
    """`app/pipeline.py` 里写的是 `BilibiliCollector().collect(browser, cities=cities)`。

    ⚠️ 这条约定必须和 `ShowstartCollector.collect` / `DoubanCollector.collect` 同形，
    否则调度层会在 import 之后运行到一半才炸。
    """

    def test_collect_signature_is_browser_first(self):
        import inspect

        sig = inspect.signature(b.BilibiliCollector.collect)
        params = list(sig.parameters)
        assert params[1] == "browser", f"第一个位置参数必须是 browser，实际是 {params[1]}"
        assert params[2] == "cities"
        assert sig.parameters["cities"].default is None

    def test_dynamic_mids_defaults_to_empty(self):
        """动态通道默认不跑（实测拿不到可信日期，见 `_looks_like_event`）。"""
        assert b.DYNAMIC_MIDS == []
        assert b.KNOWN_DYNAMIC_MIDS["B10Live"] == 510676514

    @pytest.mark.asyncio
    async def test_collect_skips_city_without_area_code(self):
        """无 area 码的城市必须直接跳过，不能发请求（也不能报错）。"""
        collector = b.BilibiliCollector()
        occ = await collector.collect(browser=None, cities=["中山"], mids=[])
        assert occ == []

    def test_collector_exposes_expected_attributes(self):
        collector = b.BilibiliCollector()
        assert collector.source_code == "bilibili"
        assert hasattr(collector, "collect_dynamics")
        assert hasattr(collector, "collect_show")
        assert hasattr(collector, "aclose")


# --------------------------------------------------------------------------- #
# 共用后处理（_postprocess）的回归项
# --------------------------------------------------------------------------- #

class TestPostprocessRegression:
    def test_event_hint_regex_is_reachable(self):
        """⚠️ 回归：`_EVENT_HINT_RE` 缺失时，只有当「场地/阵容/票价」全为空才会触发，
        单测很容易全绿却在真机上炸 NameError（实测在微博真实 payload 上踩到）。
        这里显式走一遍「没有任何细节字段」的分支。"""
        from app.collectors import _postprocess as pp

        fields = {"date_precision": "exact", "title_display": "某地偶公演"}
        assert pp.looks_like_event(fields, "某地偶公演 场地待定") is True
        assert pp.looks_like_event({"date_precision": "exact", "title_display": "随便"}, "无信号") is False

    def test_time_range_corrected(self):
        """时间区间「21:40-22:40」必须解析为 start=21:40 / end=22:40。

        历史：该 bug 最初在采集器里用 `_postprocess.fix_time_range` 兜住；
        后来已在 `text_zh.parse_datetime` **源头修好**，采集器兜底逻辑改为
        运行时能力探测（解析器支持时自动跳过），避免双重生效导致错位。
        """
        from app.collectors import _postprocess as pp
        from app.models import ParsedOccurrence
        from app.parsers import text_zh

        text = "📅 2026.10.24 21:40–22:40"
        start, open_at, prec, end = text_zh.parse_datetime(text, now=NOW)
        # 解析器已直接给出正确语义
        assert (start.hour, start.minute) == (21, 40)
        assert end is not None and (end.hour, end.minute) == (22, 40)
        assert pp._NEED_TIME_RANGE_FALLBACK is False, "解析器已支持时不应再启用兜底"

        occ = ParsedOccurrence(
            source_code="bilibili", source_url="u", external_id="e",
            title_raw="t", start_at=start, open_at=open_at,
            date_precision=prec, end_at=end,
        )
        occ = pp.fix_time_range(occ, text)
        # 兜底不再改动（避免把 start 又改成 22:40）
        assert (occ.start_at.hour, occ.start_at.minute) == (21, 40)
        assert occ.end_at is not None and (occ.end_at.hour, occ.end_at.minute) == (22, 40)

    def test_time_range_not_touched_when_labels_present(self):
        from app.collectors import _postprocess as pp
        from app.models import ParsedOccurrence

        occ = ParsedOccurrence(
            source_code="x", source_url="u", external_id="e", title_raw="t",
            start_at=NOW, open_at=None, date_precision="exact",
        )
        out = pp.fix_time_range(occ, "19:00 开场 / 20:00 开演")
        assert out.start_at == NOW  # 有显式标签 → 不动

    def test_sanitize_artists_drops_prose(self):
        from app.collectors import _postprocess as pp
        from app.models import ArtistIn

        raw = [
            ArtistIn(name_raw="NEXUS_Official"),
            ArtistIn(name_raw="早已与法国创意音乐的复兴紧密相连"),   # 正文长句（≥30 字被长度挡住）
            ArtistIn(name_raw="内页里则这样写道:人们或许可以将"),
            ArtistIn(name_raw="2026年11月7日"),
            ArtistIn(name_raw="17:40"),
            ArtistIn(name_raw="转发"),
            ArtistIn(name_raw="A"),
            ArtistIn(name_raw="海朋森"),
            ArtistIn(name_raw="海朋森"),          # 重复
        ]
        kept = [a.name_raw for a in pp.sanitize_artists(raw)]
        assert kept == ["NEXUS_Official", "海朋森"]

    def test_venue_piece_filter(self):
        from app.collectors import _postprocess as pp

        assert pp.looks_like_venue_piece("深圳B10现场") is True
        assert pp.looks_like_venue_piece("191space") is True
        assert pp.looks_like_venue_piece("MAO Livehouse") is True
        assert pp.looks_like_venue_piece("流动的诗意:Joëlle") is False
        assert pp.looks_like_venue_piece("13th") is False          # 以数字开头 → 视频标题片段
        assert pp.looks_like_venue_piece("B10") is False           # 太短
        assert pp.looks_like_venue_piece("Mariam Rezaei 是一位屡获奖项的作曲家") is False
