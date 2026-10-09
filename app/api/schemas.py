"""API 响应模型（前端契约）。

字段命名与 docs/01-总体设计.md §5.2 完全一致；前端 app/web/js 按此消费。
契约要点：**confidence 与 sources 必须透出** —— 界面的「待确认」与「来源」标签全靠它。
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def iso_cst(value: dt.datetime | None) -> str | None:
    """统一按带时区的 ISO8601 输出。

    ⚠️ 关键取舍：SQLite 不保存时区，读回来是 naive。若直接序列化，
    前端 `new Date("2026-10-09T00:00:00")` 会按**浏览器本地时区**解析，
    在东八区以外会整体偏 8 小时。这里显式补上 +08:00。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return value.isoformat()


# 与 app/api/images.py 的 ALLOWED_HOSTS 对应：能代理的图片域名
_PROXYABLE_IMAGE_HOSTS = ("doubanio.com", "hdslb.com", "showstart.com", "showstartcdn.com")


def poster_display_url(url: str | None) -> str | None:
    """把原站海报 URL 换成**本站代理地址**。

    为什么必须代理（实测）：豆瓣图片有防盗链 —— 不带 Referer 直接请求返回 418，
    前端 `<img src="https://img3.doubanio.com/...">` 必然裂图。
    代理后由后端补正确的 Referer 并缓存到本地。

    非白名单域名原样返回（例如将来加了没有防盗链的图床）。
    """
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith(("http://", "https://")):
        return None
    from urllib.parse import urlparse

    host = (urlparse(u).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in _PROXYABLE_IMAGE_HOSTS):
        from urllib.parse import quote

        return f"/api/archive/poster?url={quote(u, safe='')}"
    return u


class VenueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str | None = None
    district: str | None = None
    address: str | None = None
    address_note: str | None = None
    lng: float | None = None
    lat: float | None = None
    capacity: int | None = None
    venue_type: str | None = None


class EventOut(BaseModel):
    id: int
    title: str
    series_key: str | None = None
    series_vol: str | None = None
    kind: str
    is_idol: bool
    # 与 is_idol 正交的独立标记，可叠加（一个 ACG 女子乐队两个都真）
    is_girl_band: bool = False
    is_acg: bool = False
    # 二次元漫展 / 同人展（与 is_acg 的音乐语义分开）
    is_doujin_expo: bool = False
    poster_thumb: str | None = None
    # 海报：原站地址经本站代理后的可直链 URL（None 表示该场次没有海报）
    poster_url: str | None = None

    _s_poster = field_serializer("poster_url")(poster_display_url)


class LineupOut(BaseModel):
    artist_id: int | None = None
    name: str
    role: str
    order: int | None = None
    cancelled: bool = False


class TicketOut(BaseModel):
    name: str
    tier_type: str | None = None
    price: float | None = None
    is_placeholder: bool = False
    status: str = "unknown"
    channel: str | None = None
    url: str | None = None
    note: str | None = None


class PriceOut(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str = "CNY"
    is_free: bool = False


class SourceRefOut(BaseModel):
    code: str
    name: str | None = None
    url: str
    fetched_at: dt.datetime | None = None
    is_primary: bool = False

    _s_fetched = field_serializer("fetched_at")(iso_cst)


class OccurrenceOut(BaseModel):
    id: int
    event: EventOut
    city: str
    venue: VenueOut | None = None
    venue_raw: str | None = None
    open_at: dt.datetime | None = None
    start_at: dt.datetime | None = None
    end_at: dt.datetime | None = None
    date_precision: str = "exact"
    status: str
    status_note: str | None = None
    price: PriceOut
    age_limit: str | None = None
    id_required: bool = False
    tokuten_note: str | None = None
    schedule_note: str | None = None
    extra: dict = Field(default_factory=dict)
    lineup: list[LineupOut] = Field(default_factory=list)
    tickets: list[TicketOut] = Field(default_factory=list)
    confidence: float = 1.0
    verified: bool = False
    sources: list[SourceRefOut] = Field(default_factory=list)

    _s_open = field_serializer("open_at")(iso_cst)
    _s_start = field_serializer("start_at")(iso_cst)
    _s_end = field_serializer("end_at")(iso_cst)


class OccurrenceListOut(BaseModel):
    items: list[OccurrenceOut]
    total: int
    page: int = 1
    page_size: int = 50
    generated_at: dt.datetime

    _s_gen = field_serializer("generated_at")(iso_cst)


class DayCount(BaseModel):
    band: int = 0
    idol: int = 0
    # 与 band/idol 正交，可同时计数（一天的数据点数可能多于当天场次数）
    girl_band: int = 0
    acg: int = 0
    followed: int = 0


class CalendarCountsOut(BaseModel):
    month: str
    days: dict[str, DayCount] = Field(default_factory=dict)


class ArtistOut(BaseModel):
    id: int
    name: str
    kind: str
    origin_city: str | None = None
    agency: str | None = None
    status: str = "active"
    links: dict = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    upcoming: int = 0
    past: int = 0


class VenueListOut(BaseModel):
    items: list[VenueOut]


class ArtistListOut(BaseModel):
    items: list[ArtistOut]


class SourceHealthOut(BaseModel):
    code: str
    name: str
    kind: str
    enabled: bool
    priority: int
    last_ok_at: dt.datetime | None = None
    last_error: str | None = None
    items_7d: int = 0
    ok_rate_7d: float | None = None

    _s_ok = field_serializer("last_ok_at")(iso_cst)


class StatsOut(BaseModel):
    occurrences: int
    events: int
    artists: int
    venues: int
    sources: list[SourceHealthOut]
    generated_at: dt.datetime

    _s_gen = field_serializer("generated_at")(iso_cst)


class HealthOut(BaseModel):
    status: str = "ok"
    app: str
    version: str
    database: str
