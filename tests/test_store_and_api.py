"""集成测试：判重 / 入库 / API / .ics。

数据库隔离由仓库根目录的 `conftest.py` 统一负责（它在任何 app 模块导入前
就把 DATABASE_URL 指向临时目录，并有二次门禁校验）。
本文件**不再**自行覆盖环境变量，避免两处不一致。
"""

from __future__ import annotations

import datetime as dt

import pytest

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.main import app, build_ics  # noqa: E402
from app.db import dispose_db, init_db, session_scope  # noqa: E402
from app.db.models import Event, Occurrence, ReviewTask, Venue  # noqa: E402
from app.models import ArtistIn, ParsedOccurrence, TicketIn  # noqa: E402
from app.normalize import (  # noqa: E402
    choose_existing,
    is_ambiguous_alias,
    match_venue,
    title_similarity,
    venue_fingerprint,
)
from app.store import Ingestor  # noqa: E402
from app.utils import CST  # noqa: E402


def occ(
    title: str,
    *,
    ext: str = "1",
    start: dt.datetime | None = None,
    venue: str | None = "SDlivehouse",
    city: str = "广州",
    source: str = "showstart",
    is_idol: bool = False,
    price: float | None = 120.0,
    lineup: list[str] | None = None,
) -> ParsedOccurrence:
    from app.parsers import text_zh as tz

    cls = tz.classify(title)
    return ParsedOccurrence(
        source_code=source,
        source_url=f"https://example.com/{ext}",
        external_id=ext,
        title_raw=title,
        title_display=title,
        kind=cls.kind,
        is_idol=is_idol or cls.is_idol,
        is_girl_band=cls.is_girl_band,
        is_acg=cls.is_acg,
        tags=cls.tags,
        city=city,
        venue_raw=venue,
        start_at=start or dt.datetime(2026, 11, 1, 20, 0, tzinfo=CST),
        date_precision="exact",
        price_min=price,
        price_max=price,
        tickets=[TicketIn(name_raw="预售票", tier_type="预售", price=price)] if price else [],
        artists=[
            ArtistIn(name_raw=n, name_norm=tz.normalize_name(n), billing_order=i + 1, source=source)
            for i, n in enumerate(lineup or [])
        ],
        confidence=0.9,
    )


@pytest.fixture(scope="module", autouse=True)
def _prepare_db():
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(init_db(drop=True))
    yield
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(dispose_db())


async def _ingest(items: list[ParsedOccurrence]):
    async with session_scope() as session:
        return await Ingestor(session).ingest(items)


class TestFlagPersistence:
    """分类标记的入库行为。

    两条不变式：
      1. 新建 event 时必须写入标记（否则「筛女子乐队一条都没有」）。
      2. 更新已有 event 时，标记**只增不减** —— 某些源标题信息更少
         （例如微博那条没写 ACG），不能把别的源已判定出的标记抹掉。
    """

    async def test_new_event_writes_flags(self):
        await _ingest([occ("全女子编制 女子摇滚之夜", ext="flg1")])
        async with session_scope() as s:
            ev = (
                await s.execute(select(Event).where(Event.title_display == "全女子编制 女子摇滚之夜"))
            ).scalars().first()
        assert ev is not None
        assert ev.is_girl_band is True
        assert ev.is_acg is False

    async def test_acg_flag_written(self):
        await _ingest([occ("VOCALOID ONLY LIVE 初音未来", ext="flg2")])
        async with session_scope() as s:
            ev = (
                await s.execute(select(Event).where(Event.title_display.like("VOCALOID%")))
            ).scalars().first()
        assert ev is not None and ev.is_acg is True

    async def test_flags_are_monotonic_on_update(self):
        """第二个源信息更少时，已有标记不能被抹掉。"""
        title = "女子乐队 ACG 联合演出"
        await _ingest([occ(title, ext="flg3", source="showstart")])
        async with session_scope() as s:
            ev = (await s.execute(select(Event).where(Event.title_display == title))).scalars().first()
            assert ev.is_girl_band is True and ev.is_acg is True

        # 同一个活动，来自另一个源，标题降级成「某演出」（两个标记都丢了）
        await _ingest([occ("某演出", ext="flg3b", source="weibo")])
        async with session_scope() as s:
            ev2 = (
                await s.execute(select(Event).where(Event.title_display == title))
            ).scalars().first()
            if ev2 is not None:
                assert ev2.is_girl_band is True, "女子乐队标记被第二个源抹掉了"
                assert ev2.is_acg is True, "ACG 标记被第二个源抹掉了"


# --------------------------------------------------------------------------- #
# 判重与实体对齐（纯函数）
# --------------------------------------------------------------------------- #

class TestDedupLogic:
    def test_venue_fingerprint_distinguishes_units(self):
        """实测铁律：太古仓 4 号仓（MAO）与 5 号仓（太空间）是两个场地。"""
        a = venue_fingerprint("MAO Livehouse广州(太古仓店)4号仓")
        b = venue_fingerprint("太空间Livehouse太古仓码头5号仓")
        assert a != b

    def test_venue_fingerprint_same_for_same_unit(self):
        a = venue_fingerprint("MAO Livehouse 广州（太古仓店）4号仓")
        b = venue_fingerprint("mao livehouse广州(太古仓店)4号仓")
        assert a == b

    def test_choose_existing_exact(self):
        res = choose_existing([(1, "abc"), (2, "xyz")], "abc")
        assert res.kind == "exact" and res.matched_id == 1

    def test_choose_existing_similar(self):
        res = choose_existing([(1, "星屑公演vol12")], "星屑公演vol13")
        assert res.kind == "similar" and res.score > 0.6

    def test_choose_existing_rejects_dissimilar(self):
        """同场地同日但标题完全不同 → 不合并（可能是一天两场）。"""
        res = choose_existing([(1, "噪音拼盘")], "jazz night")
        assert res.kind == "none"
        assert res.matched_id is None

    def test_choose_existing_empty(self):
        assert choose_existing([], "x").kind == "none"

    def test_title_similarity_bounds(self):
        assert title_similarity("", "x") == 0.0
        assert title_similarity("abc", "abc") == 1.0

    def test_match_venue_exact_and_alias(self):
        # 目录键 = 场地规范名 + 别名的归一化形式（真实目录就是这么建的）
        catalog = {
            "sdlivehouse": [(7, "SDlivehouse")],
            "maolivehouse广州永庆坊店": [(9, "MAO Livehouse广州（永庆坊店）")],
            "mao永庆坊": [(9, "MAO Livehouse广州（永庆坊店）")],
        }
        vid, conf = match_venue("SD Livehouse", "广州", catalog)
        assert vid == 7 and conf == 1.0
        # 别名精确命中
        vid2, _ = match_venue("MAO 永庆坊", "广州", catalog)
        assert vid2 == 9
        # 包含匹配（写入的名字比目录名短）
        vid3, conf3 = match_venue("MAOLivehouse广州永庆坊店", "广州", catalog)
        assert vid3 == 9 and conf3 == 1.0

    def test_match_venue_rejects_unit_mismatch(self):
        """包含匹配时仓位号不同必须否决。"""
        catalog = {"maolivehouse广州4号仓": [(1, "MAO 4号仓")]}
        vid, _ = match_venue("MAO Livehouse广州5号仓", "广州", catalog)
        assert vid is None

    def test_ambiguous_alias_blocked(self):
        # 「太古仓」会同时指向 MAO 太古仓店与太空间 → 禁止作别名
        assert is_ambiguous_alias("太古仓") is True
        assert is_ambiguous_alias("livehouse") is True
        assert is_ambiguous_alias("MAO永庆坊") is False


# --------------------------------------------------------------------------- #
# 入库
# --------------------------------------------------------------------------- #

class TestIngest:
    async def test_create_event_occurrence_venue(self):
        stats = await _ingest([
            occ("噪音拼盘 #37", ext="1001", lineup=["A队", "B队"]),
        ])
        assert stats.created_occurrence == 1
        assert stats.new_venues >= 0
        async with session_scope() as s:
            rows = (await s.execute(select(Occurrence))).scalars().all()
            assert any(r.city == "广州" for r in rows)

    async def test_idempotent_by_source_key(self):
        """同一来源同一 external_id 重复采集 → 只更新，不新建。"""
        stats1 = await _ingest([occ("重复场次", ext="2001")])
        stats2 = await _ingest([occ("重复场次", ext="2001")])
        assert stats1.created_occurrence == 1
        assert stats2.created_occurrence == 0
        assert stats2.updated_occurrence == 1

    async def test_dedup_across_sources_merges(self):
        """跨源同场次 → 合并为一条 occurrence，并挂两个来源。"""
        base = dt.datetime(2026, 11, 5, 20, 0, tzinfo=CST)
        await _ingest([occ("跨界联合演出", ext="3001", start=base, source="showstart")])
        stats = await _ingest([
            occ("跨界联合演出", ext="3002", start=base, source="douban")
        ])
        # 不应新建 occurrence
        assert stats.created_occurrence == 0

    async def test_two_shows_same_day_same_venue_not_merged(self):
        """同一天同场地但标题完全不同 → 必须保留两条（下午场 + 夜场）。"""
        base = dt.datetime(2026, 11, 7, 14, 0, tzinfo=CST)
        s1 = await _ingest([occ("午后不插电", ext="4001", start=base)])
        s2 = await _ingest([
            occ("深夜重金属之夜", ext="4002", start=base.replace(hour=21))
        ])
        assert s1.created_occurrence == 1
        assert s2.created_occurrence == 1

    async def test_time_conflict_creates_review_task(self):
        """同来源同 external_id 但时间变了 → 记 review_task，不静默覆盖。"""
        base = dt.datetime(2026, 11, 9, 20, 0, tzinfo=CST)
        await _ingest([occ("时间冲突场", ext="5001", start=base)])
        stats = await _ingest([
            occ("时间冲突场", ext="5001", start=base.replace(hour=13, minute=30))
        ])
        assert stats.conflicts == 1
        async with session_scope() as s:
            tasks = (await s.execute(select(ReviewTask))).scalars().all()
            assert any(t.reason == "conflict" for t in tasks)

    async def test_skip_no_time_and_past(self):
        no_time = occ("没有时间的活动", ext="6001")
        no_time.start_at = None
        past = occ("很久以前", ext="6002",
                   start=dt.datetime(2020, 1, 1, 20, 0, tzinfo=CST))
        stats = await _ingest([no_time, past])
        assert stats.skipped_no_time == 1
        assert stats.skipped_past == 1

    async def test_idol_classification_persisted(self):
        await _ingest([occ("呆呆Otori · 2026 生诞祭", ext="7001")])
        async with session_scope() as s:
            ev = (
                await s.execute(select(Event).where(Event.title_display.like("%生诞祭%")))
            ).scalars().first()
            assert ev is not None
            assert ev.is_idol is True
            assert ev.kind == "idol_birthday"

    async def test_venue_address_not_polluting_name(self):
        """实测坑：豆瓣「地点」写成「场地名 + 完整地址」，不能让地址进场地表。"""
        await _ingest([
            occ("某演出", ext="8001",
                venue="一支麦小剧场-正佳广场店 天河南街道天河路228号正佳广场5层")
        ])
        async with session_scope() as s:
            vs = (await s.execute(select(Venue))).scalars().all()
            assert all("街道" not in (v.name or "") for v in vs)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestApi:
    async def test_health(self, client):
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_occurrences_shape(self, client):
        r = await client.get(
            "/api/occurrences",
            params={"from": "2026-11-01", "to": "2026-11-30", "page_size": 5},
        )
        assert r.status_code == 200
        data = r.json()
        assert "items" in data and "total" in data and "generated_at" in data
        if data["items"]:
            it = data["items"][0]
            for key in ("id", "event", "city", "price", "status", "confidence", "sources"):
                assert key in it, f"契约缺字段 {key}"

    async def test_datetime_has_timezone_offset(self, client):
        """SQLite 不存时区，API 必须显式补 +08:00，否则前端会偏 8 小时。"""
        r = await client.get(
            "/api/occurrences",
            params={"from": "2026-11-01", "to": "2026-11-30", "page_size": 20},
        )
        items = [i for i in r.json()["items"] if i.get("start_at")]
        assert items, "应有带时间的场次"
        assert all("+08:00" in i["start_at"] for i in items)

    async def test_default_excludes_other_kind(self, client):
        """默认排除「非演出」内容（脱口秀/话剧等）。"""
        r1 = await client.get(
            "/api/occurrences",
            params={"from": "2026-11-01", "to": "2026-11-30", "page_size": 100},
        )
        r2 = await client.get(
            "/api/occurrences",
            params={"from": "2026-11-01", "to": "2026-11-30", "page_size": 100,
                    "exclude_other": "false"},
        )
        assert r1.json()["total"] <= r2.json()["total"]

    async def test_is_idol_filter(self, client):
        r = await client.get(
            "/api/occurrences",
            params={"from": "2026-10-01", "to": "2027-12-31",
                    "is_idol": "true", "page_size": 50},
        )
        assert r.status_code == 200
        assert all(i["event"]["is_idol"] for i in r.json()["items"])

    async def test_calendar_counts(self, client):
        r = await client.get("/api/calendar/counts", params={"month": "2026-11"})
        assert r.status_code == 200
        body = r.json()
        assert body["month"] == "2026-11"
        assert isinstance(body["days"], dict)
        for _day, c in body["days"].items():
            assert set(c) >= {"band", "idol", "followed"}

    async def test_calendar_counts_bad_month(self, client):
        r = await client.get("/api/calendar/counts", params={"month": "bad"})
        assert r.status_code == 400

    async def test_event_out_carries_flag_fields(self, client):
        """响应契约必须带 is_girl_band / is_acg（前端标签与筛选依赖它）。"""
        r = await client.get("/api/occurrences", params={"page_size": 5})
        assert r.status_code == 200
        for item in r.json()["items"]:
            ev = item["event"]
            assert "is_girl_band" in ev
            assert "is_acg" in ev
            assert isinstance(ev["is_girl_band"], bool)
            assert isinstance(ev["is_acg"], bool)

    async def test_flag_filter_is_and(self, client):
        """flag 多值语义是「与」：返回的每一条都必须同时命中所有标记。"""
        r = await client.get(
            "/api/occurrences",
            params={"page_size": 200, "flag": ["女子乐队", "acg"]},
        )
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["event"]["is_girl_band"] is True
            assert item["event"]["is_acg"] is True

    async def test_flag_filter_single(self, client):
        r = await client.get("/api/occurrences", params={"page_size": 200, "flag": "acg"})
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["event"]["is_acg"] is True

    async def test_is_flag_boolean_params(self, client):
        """也支持直接布尔参数（前端两种都可用）。"""
        r = await client.get(
            "/api/occurrences", params={"page_size": 200, "is_girl_band": "true"}
        )
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["event"]["is_girl_band"] is True

    async def test_counts_include_flag_numbers(self, client):
        r = await client.get("/api/calendar/counts", params={"month": "2026-11"})
        assert r.status_code == 200
        for c in r.json()["days"].values():
            assert "girl_band" in c and "acg" in c

    async def test_counts_flag_not_exceeding_total(self, client):
        """标记是正交的，但**不可能超过当天总场次**。

        注意 band+idol 是互斥主分类；girl_band/acg 是叠加标记，
        所以只能断言 girl_band <= total 与 acg <= total。
        """
        r = await client.get("/api/calendar/counts", params={"month": "2026-11"})
        for day, c in r.json()["days"].items():
            total = c["band"] + c["idol"]
            assert c["girl_band"] <= total, f"{day}: girl_band {c['girl_band']} > total {total}"
            assert c["acg"] <= total, f"{day}: acg {c['acg']} > total {total}"

    async def test_ics_flag_naming(self, client):
        """订阅地偶+ACG 时日历名要反映筛选，避免订阅后认不出。"""
        r = await client.get(
            "/api/ics", params={"is_idol": "true", "is_acg": "true", "days": 30}
        )
        assert r.status_code == 200
        assert "ACG" in r.text
        assert "地偶" in r.text

    async def test_counts_matches_list_on_same_filter(self, client):
        """⚠️ 口径一致性：日历点阵有数据的某天，列表按同口径查必须也能查到。

        实测踩过的坑：`/api/occurrences` 默认 `exclude_other=True`（过滤掉
        `kind='other'` 的脱口秀/展览等非演出内容），而 `/api/calendar/counts`
        早期没有这个参数 → 日历显示圆点、点进去却是空的。
        这个测试锁住「点阵数 == 列表数」这个不变式。
        """
        r = await client.get("/api/calendar/counts", params={"month": "2026-11"})
        assert r.status_code == 200
        days = r.json()["days"]
        # 取有圆点的日子里最多的那几天来核对
        busy = sorted(
            ((d, c["band"] + c["idol"]) for d, c in days.items() if c["band"] + c["idol"] > 0),
            key=lambda x: -x[1],
        )[:5]
        assert busy, "测试库里 2026-11 应有带数据的日期"
        for day, dots in busy:
            lr = await client.get(
                "/api/occurrences", params={"from": day, "to": day, "page_size": 200}
            )
            assert lr.status_code == 200
            listed = lr.json()["total"]
            assert listed == dots, (
                f"{day}: 日历点阵 {dots} 与列表 {listed} 不一致（口径漂移）"
            )

    async def test_exclude_other_consistent_between_endpoints(self, client):
        """两边都必须响应 exclude_other，且口径一致。"""
        for flag in ("true", "false"):
            r = await client.get(
                "/api/calendar/counts",
                params={"month": "2026-11", "exclude_other": flag},
            )
            days = r.json()["days"]
            dots = sum(c["band"] + c["idol"] for c in days.values())
            lr = await client.get(
                "/api/occurrences",
                params={"from": "2026-11-01", "to": "2026-11-30",
                        "page_size": 500, "exclude_other": flag},
            )
            listed = lr.json()["total"]
            assert dots == listed, (
                f"exclude_other={flag}: 日历 {dots} vs 列表 {listed}"
            )

    async def test_same_day_date_only_not_hidden(self, client):
        """⚠️ 只有日期、没有时刻的活动（date_precision=date_only）在当天不能被隐藏。

        实测踩过的坑：这类活动统一存当天 00:00，而旧的过期过滤是
        `start_at >= now - 6h`。于是**当天下午**再查，它们就被当成过期隐藏了。
        现在按「天」判断过期，当天任何时刻都应可见。
        """
        import datetime as _dt

        from app.db.models import Occurrence
        from app.utils import CST

        # 造一条「今天 00:00 + date_only」的场次
        today = _dt.datetime.now(CST).date()
        start = _dt.datetime.combine(today, _dt.time(0, 0), tzinfo=CST)
        await _ingest([
            occ("当天无时刻的展览", ext="9001", start=start, venue="某美术馆")
        ])
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(Occurrence).where(Occurrence.start_at == start)
                )
            ).scalars().first()
            if row is not None:
                row.date_precision = "date_only"
                await s.commit()

        r = await client.get(
            "/api/occurrences",
            params={"from": today.isoformat(), "to": today.isoformat(),
                    "page_size": 200, "exclude_other": "false"},
        )
        assert r.status_code == 200
        ids = [i["id"] for i in r.json()["items"]]
        assert row is not None and row.id in ids, (
            "当天 00:00 的 date_only 场次被误判为过期隐藏了"
        )

    async def test_venues_and_stats(self, client):
        r = await client.get("/api/venues", params={"limit": 5})
        assert r.status_code == 200 and "items" in r.json()
        r2 = await client.get("/api/stats")
        assert r2.status_code == 200
        body = r2.json()
        assert body["venues"] >= 1 and "sources" in body

    async def test_ics_is_valid_calendar(self, client):
        r = await client.get("/api/ics", params={"days": 120})
        assert r.status_code == 200
        assert "text/calendar" in r.headers["content-type"]
        body = r.text
        assert body.startswith("BEGIN:VCALENDAR")
        assert body.rstrip().endswith("END:VCALENDAR")
        assert "BEGIN:VEVENT" in body
        assert "DTSTART;TZID=Asia/Shanghai:" in body

    async def test_csv_export(self, client):
        r = await client.get("/api/export.csv", params={"from": "2026-11-01"})
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert r.text.lstrip("\ufeff").startswith("date,open_at,start_at")

    async def test_404_for_missing_occurrence(self, client):
        r = await client.get("/api/occurrences/999999")
        assert r.status_code == 404


class TestIcsBuilder:
    def test_escape_and_uid_stability(self):
        """UID 必须稳定（改期时客户端应更新而不是重复添加）。"""
        from app.api.schemas import EventOut, OccurrenceOut, PriceOut

        def mk(uid: int, start: dt.datetime):
            return OccurrenceOut(
                id=uid,
                event=EventOut(id=1, title="标题,含逗号;分号", kind="rock_live", is_idol=False),
                city="广州",
                start_at=start,
                status="on_sale",
                price=PriceOut(min=100, max=100),
            )

        a = build_ics([mk(42, dt.datetime(2026, 11, 1, 20, 0, tzinfo=CST))])
        b = build_ics([mk(42, dt.datetime(2026, 11, 2, 21, 0, tzinfo=CST))])
        assert "UID:42@gdcalendar" in a and "UID:42@gdcalendar" in b
        assert "20261101T200000" in a
        assert "20261102T210000" in b
        # 逗号与分号必须转义
        assert "\\," in a and "\\;" in a

    def test_skips_occurrence_without_time(self):
        from app.api.schemas import EventOut, OccurrenceOut, PriceOut

        item = OccurrenceOut(
            id=1, event=EventOut(id=1, title="X", kind="other", is_idol=False),
            city="广州", start_at=None, status="on_sale", price=PriceOut(),
        )
        body = build_ics([item])
        assert "BEGIN:VEVENT" not in body
