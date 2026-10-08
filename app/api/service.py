"""查询服务：把 ORM 组装成前端契约。

放在 API 之外，便于 CLI 与测试直接复用。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    ArtistOut,
    DayCount,
    EventOut,
    LineupOut,
    OccurrenceOut,
    PriceOut,
    SourceHealthOut,
    SourceRefOut,
    TicketOut,
    VenueOut,
)
from app.db.models import (
    Artist,
    FetchLog,
    Occurrence,
    OccurrenceArtist,
    OccurrenceSource,
    Source,
    TicketTier,
)
from app.utils import CST, now_cst


@dataclass
class OccurrenceQuery:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    city: str | None = None
    kind: str | None = None
    is_idol: bool | None = None
    is_girl_band: bool | None = None
    is_acg: bool | None = None
    # 分类标记的「与」组合：传 ["女子乐队","acg"] 表示同时命中两者
    flags_all: list[str] | None = None
    venue_id: int | None = None
    artist_id: int | None = None
    price_max: float | None = None
    status: str | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 100
    include_finished: bool = False
    # 默认排除「非演出」内容（脱口秀/话剧/展览/动物园门票等），
    # 它们在源里占比很高但与本产品定位无关
    exclude_other: bool = True


def _day_bounds(d: dt.date | None) -> dt.datetime | None:
    if d is None:
        return None
    return dt.datetime.combine(d, dt.time(0, 0), tzinfo=CST)


def _to_occurrence_out(
    occ: Occurrence,
    event_title: str,
    event_meta: dict,
    venue: VenueOut | None,
    lineup: list[LineupOut],
    tickets: list[TicketOut],
    sources: list[SourceRefOut],
) -> OccurrenceOut:
    return OccurrenceOut(
        id=occ.id,
        event=EventOut(
            id=occ.event_id,
            title=event_title,
            series_key=event_meta.get("series_key"),
            series_vol=event_meta.get("series_vol"),
            kind=event_meta.get("kind", "other"),
            is_idol=bool(event_meta.get("is_idol")),
            is_girl_band=bool(event_meta.get("is_girl_band")),
            is_acg=bool(event_meta.get("is_acg")),
            poster_thumb=event_meta.get("poster_thumb"),
            poster_url=event_meta.get("poster_url"),
        ),
        city=occ.city,
        venue=venue,
        venue_raw=occ.venue_raw,
        open_at=occ.open_at,
        start_at=occ.start_at,
        end_at=occ.end_at,
        date_precision=occ.date_precision,
        status=occ.status,
        status_note=occ.status_note,
        price=PriceOut(
            min=float(occ.price_min) if occ.price_min is not None else None,
            max=float(occ.price_max) if occ.price_max is not None else None,
            currency=occ.currency or "CNY",
            is_free=bool(occ.is_free),
        ),
        age_limit=occ.age_limit,
        id_required=bool(occ.id_required),
        tokuten_note=occ.tokuten_note,
        schedule_note=occ.schedule_note,
        extra=occ.extra or {},
        lineup=lineup,
        tickets=tickets,
        confidence=float(occ.confidence or 1.0),
        verified=bool(occ.verified_by),
        sources=sources,
    )


async def query_occurrences(
    session: AsyncSession, q: OccurrenceQuery
) -> tuple[list[OccurrenceOut], int]:
    """日历主查询。"""
    from app.db.models import Artist as A
    from app.db.models import Event

    stmt = (
        select(Occurrence)
        .options(
            selectinload(Occurrence.venue),
            selectinload(Occurrence.artists),
            selectinload(Occurrence.tickets),
            selectinload(Occurrence.sources),
        )
        .join(Event, Event.id == Occurrence.event_id)
    )

    if q.date_from:
        stmt = stmt.where(Occurrence.start_at >= _day_bounds(q.date_from))
    if q.date_to:
        stmt = stmt.where(Occurrence.start_at < _day_bounds(q.date_to + dt.timedelta(days=1)))
    if not q.include_finished:
        # ⚠️ 过期判断必须按「天」，不能按「时刻」。
        #
        # 踩过的坑：豆瓣很多活动只给日期、没有时刻，我们统一存当天 00:00 并把
        # date_precision 标为 date_only。若用 `start_at >= now - 6h` 过滤，
        # 这类**当天**的活动会在当天下午就被隐藏 —— 日历点阵（走 counts，不过滤）
        # 还显示着，点进去却是空的。
        #
        # 正确语义：start_at 所在的那一天已经过去了，才算过期。
        today = now_cst().date()
        stmt = stmt.where(
            Occurrence.start_at >= dt.datetime.combine(today, dt.time(0, 0), tzinfo=CST)
        )
        stmt = stmt.where(Occurrence.status != "cancelled")
    if q.city:
        stmt = stmt.where(Occurrence.city == q.city)
    if q.kind:
        stmt = stmt.where(Event.kind == q.kind)
    elif q.exclude_other:
        stmt = stmt.where(Event.kind != "other")
    if q.is_idol is not None:
        stmt = stmt.where(Event.is_idol == q.is_idol)
    if q.is_girl_band is not None:
        stmt = stmt.where(Event.is_girl_band == q.is_girl_band)
    if q.is_acg is not None:
        stmt = stmt.where(Event.is_acg == q.is_acg)
    # 「与」组合：分类标记之间是 AND 关系（地偶 + ACG 之类的交叉场次）
    _FLAG_COLUMNS = {
        "地偶": Event.is_idol,
        "女子乐队": Event.is_girl_band,
        "acg": Event.is_acg,
        "idol": Event.is_idol,
        "girl_band": Event.is_girl_band,
    }
    for flag in q.flags_all or []:
        col = _FLAG_COLUMNS.get(flag.strip().lower()) or _FLAG_COLUMNS.get(flag.strip())
        if col is not None:
            stmt = stmt.where(col.is_(True))
    if q.venue_id:
        stmt = stmt.where(Occurrence.venue_id == q.venue_id)
    if q.status:
        stmt = stmt.where(Occurrence.status == q.status)
    if q.price_max is not None:
        stmt = stmt.where(
            (Occurrence.is_free.is_(True)) | (Occurrence.price_min <= q.price_max)
        )
    if q.q:
        like = f"%{q.q.strip()}%"
        stmt = stmt.where(Event.title_display.ilike(like))
    if q.artist_id:
        sub = select(OccurrenceArtist.occurrence_id).where(
            OccurrenceArtist.artist_id == q.artist_id
        )
        stmt = stmt.where(Occurrence.id.in_(sub))

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    stmt = (
        stmt.order_by(Occurrence.start_at.asc())
        .offset((q.page - 1) * q.page_size)
        .limit(q.page_size)
    )
    rows = (await session.execute(stmt)).scalars().unique().all()
    if not rows:
        return [], total

    # 一次性取回 event / artist 名称，避免 N+1
    event_ids = {o.event_id for o in rows}
    events = {
        e.id: e
        for e in (
            await session.execute(select(Event).where(Event.id.in_(event_ids)))
        ).scalars().all()
    }
    artist_ids = {a.artist_id for o in rows for a in o.artists if a.artist_id}
    artists = {}
    if artist_ids:
        artists = {
            a.id: a
            for a in (
                await session.execute(select(A).where(A.id.in_(artist_ids)))
            ).scalars().all()
        }
    source_ids = {s.source_id for o in rows for s in o.sources}
    sources = {}
    if source_ids:
        sources = {
            s.id: s
            for s in (
                await session.execute(select(Source).where(Source.id.in_(source_ids)))
            ).scalars().all()
        }

    items: list[OccurrenceOut] = []
    for o in rows:
        ev = events.get(o.event_id)
        lineup = [
            LineupOut(
                artist_id=a.artist_id,
                name=(artists[a.artist_id].name if a.artist_id in artists else a.artist_raw),
                role=a.role,
                order=a.billing_order,
                cancelled=bool(a.is_cancelled),
            )
            for a in sorted(o.artists, key=lambda x: (x.billing_order or 999, x.id))
        ]
        tickets = [
            TicketOut(
                name=t.name_raw, tier_type=t.tier_type,
                price=float(t.price) if t.price is not None else None,
                is_placeholder=bool(t.is_placeholder), status=t.status,
                channel=t.channel, url=t.purchase_url, note=t.note,
            )
            for t in sorted(o.tickets, key=lambda x: (x.price is None, x.price or 0))
        ]
        srcs = [
            SourceRefOut(
                code=sources[s.source_id].code if s.source_id in sources else "unknown",
                name=sources[s.source_id].name if s.source_id in sources else None,
                url=s.source_url, fetched_at=s.fetched_at, is_primary=bool(s.is_primary),
            )
            for s in o.sources
        ]
        venue = None
        if o.venue is not None:
            venue = VenueOut(
                id=o.venue.id, name=o.venue.name, city=o.venue.city,
                district=o.venue.district, address=o.venue.address,
                address_note=o.venue.address_note,
                lng=float(o.venue.lng) if o.venue.lng is not None else None,
                lat=float(o.venue.lat) if o.venue.lat is not None else None,
                capacity=o.venue.capacity, venue_type=o.venue.venue_type,
            )
        items.append(
            _to_occurrence_out(
                o,
                ev.title_display if ev else "（未知活动）",
                {
                    "series_key": ev.series_key if ev else None,
                    "series_vol": ev.series_vol if ev else None,
                    "kind": ev.kind if ev else "other",
                    "is_idol": ev.is_idol if ev else False,
                    "is_girl_band": ev.is_girl_band if ev else False,
                    "is_acg": ev.is_acg if ev else False,
                    "poster_thumb": ev.poster_thumb if ev else None,
                    "poster_url": ev.poster_url if ev else None,
                },
                venue, lineup, tickets, srcs,
            )
        )
    return items, total


async def calendar_counts(
    session: AsyncSession,
    month: str,
    city: str | None = None,
    *,
    exclude_other: bool = True,
) -> dict[str, DayCount]:
    """月历格子计数。

    ⚠️ 口径必须与 `/api/occurrences` **保持一致**，否则会出现
    「日历有点 → 点进去空白」的不一致（实测就踩了这个坑）：
    列表默认 `exclude_other=True` 会把 `kind='other'`（脱口秀/展览/旅游等
    非演出内容）过滤掉，而早期这里没有该过滤，于是点阵照旧显示圆点。

    按天在 Python 侧聚合，保证 SQLite / PostgreSQL 行为一致。
    """
    from app.db.models import Event

    try:
        y, m = (int(x) for x in month.split("-")[:2])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"month 格式应为 YYYY-MM: {month}") from exc
    start = dt.datetime(y, m, 1, tzinfo=CST)
    end = dt.datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=CST)

    stmt = (
        select(
            Occurrence.start_at,
            Event.is_idol,
            Event.is_girl_band,
            Event.is_acg,
        )
        .join(Event, Event.id == Occurrence.event_id)
        .where(Occurrence.start_at >= start, Occurrence.start_at < end,
               Occurrence.status != "cancelled")
    )
    if exclude_other:
        stmt = stmt.where(Event.kind != "other")
    if city:
        stmt = stmt.where(Occurrence.city == city)
    rows = (await session.execute(stmt)).all()

    days: dict[str, DayCount] = {}
    for start_at, is_idol, is_girl_band, is_acg in rows:
        if start_at is None:
            continue
        key = start_at.date().isoformat()
        d = days.setdefault(key, DayCount())
        # band / idol 是**互斥**的主分类（点阵颜色用），
        # girl_band / acg 是**正交标记**，可与前两者叠加计数。
        if is_idol:
            d.idol += 1
        else:
            d.band += 1
        if is_girl_band:
            d.girl_band += 1
        if is_acg:
            d.acg += 1
    return days


async def list_venues(session: AsyncSession, city: str | None = None, limit: int = 500):
    from app.db.models import Venue

    stmt = select(Venue).where(Venue.status == "active")
    if city:
        stmt = stmt.where(Venue.city == city)
    stmt = stmt.order_by(Venue.city.asc(), Venue.name.asc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        VenueOut(
            id=v.id, name=v.name, city=v.city, district=v.district,
            address=v.address, address_note=v.address_note,
            lng=float(v.lng) if v.lng is not None else None,
            lat=float(v.lat) if v.lat is not None else None,
            capacity=v.capacity, venue_type=v.venue_type,
        )
        for v in rows
    ]


async def list_artists(
    session: AsyncSession, q: str | None = None, kind: str | None = None, limit: int = 200
) -> list[ArtistOut]:
    stmt = select(Artist).where(Artist.status == "active")
    if kind:
        stmt = stmt.where(Artist.kind == kind)
    if q:
        stmt = stmt.where(Artist.name.ilike(f"%{q}%"))
    rows = (await session.execute(stmt.order_by(Artist.name.asc()).limit(limit))).scalars().all()
    return [
        ArtistOut(
            id=a.id, name=a.name, kind=a.kind, origin_city=a.origin_city,
            agency=a.agency, status=a.status, links=a.links or {},
            aliases=a.aliases or [],
        )
        for a in rows
    ]


async def source_health(session: AsyncSession) -> list[SourceHealthOut]:
    """源健康度：近 7 天采集条数与成功率。"""
    since = now_cst() - dt.timedelta(days=7)
    srcs = (await session.execute(select(Source).order_by(Source.priority.asc()))).scalars().all()
    logs = (
        await session.execute(select(FetchLog).where(FetchLog.created_at >= since))
    ).scalars().all()

    agg: dict[str, dict[str, int]] = {}
    for lg in logs:
        a = agg.setdefault(lg.source_code, {"items": 0, "ok": 0, "total": 0})
        a["items"] += lg.items_parsed or 0
        a["total"] += 1
        a["ok"] += 1 if lg.ok else 0

    out: list[SourceHealthOut] = []
    for s in srcs:
        a = agg.get(s.code, {"items": 0, "ok": 0, "total": 0})
        out.append(
            SourceHealthOut(
                code=s.code, name=s.name, kind=s.kind, enabled=bool(s.enabled),
                priority=s.priority, last_ok_at=s.last_ok_at, last_error=s.last_error,
                items_7d=a["items"],
                ok_rate_7d=round(a["ok"] / a["total"], 3) if a["total"] else None,
            )
        )
    return out


async def overview_stats(session: AsyncSession):
    from app.db.models import Event, Venue

    occ = (await session.execute(select(func.count()).select_from(Occurrence))).scalar_one()
    ev = (await session.execute(select(func.count()).select_from(Event))).scalar_one()
    ar = (await session.execute(select(func.count()).select_from(Artist))).scalar_one()
    ve = (await session.execute(select(func.count()).select_from(Venue))).scalar_one()
    return occ, ev, ar, ve, await source_health(session)
