"""数据库模型（ORM）。

设计要点（对应 docs/01-总体设计.md §2 与 §3）：
  * event / occurrence 分离 —— 日历的最小单位是 occurrence。
  * 票价用 Numeric(8,2) —— 实测存在 ¥0.01 占位票与 ¥9.90。
  * age_limit / series_vol 用 TEXT —— 存在「1.2m 以下儿童谢绝入场」「Vol.13」「①」。
  * 阵容带 source + confidence —— 实测秀动结构化字段与详情文本不一致。
  * channel 驱动全自动采集，page_cache 用内容指纹跳过重复渲染。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# 醒目：SQLite 用 JSON，PostgreSQL 用 JSONB。用 variant 让同一套模型两边都能跑。
JsonType = JSON().with_variant(JSONB(), "postgresql")
StrArrayType = JSON().with_variant(ARRAY(Text()), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------- #
# 实体层
# --------------------------------------------------------------------------- #

class Venue(Base, TimestampMixin):
    __tablename__ = "venue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_en: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(StrArrayType, default=list)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    district: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    # 门牌/仓位消歧：「太古仓 4 号仓」vs「太古仓码头 5 号仓」
    address_note: Mapped[str | None] = mapped_column(Text)
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    capacity: Mapped[int | None] = mapped_column(Integer)
    venue_type: Mapped[str | None] = mapped_column(Text)
    wechat_name: Mapped[str | None] = mapped_column(Text)
    official_url: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="active")

    source_maps: Mapped[list[VenueSourceMap]] = relationship(
        back_populates="venue", cascade="all, delete-orphan"
    )
    occurrences: Mapped[list[Occurrence]] = relationship(back_populates="venue")

    __table_args__ = (UniqueConstraint("city", "name", name="venue_city_name_uniq"),)


class VenueSourceMap(Base):
    """场地在各源的 ID 映射，如秀动 siteId=7616006。"""

    __tablename__ = "venue_source_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        ForeignKey("venue.id", ondelete="CASCADE"), nullable=False
    )
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    external_name: Mapped[str | None] = mapped_column(Text)

    venue: Mapped[Venue] = relationship(back_populates="source_maps")

    __table_args__ = (
        UniqueConstraint("source_code", "external_id", name="venue_source_map_uniq"),
    )


class Artist(Base, TimestampMixin):
    """乐队 / 地偶团体 / 成员 / 主办方。"""

    __tablename__ = "artist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # band|idol_group|idol_member|solo|organizer|dj
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    name_en: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(StrArrayType, default=list)
    origin_city: Mapped[str | None] = mapped_column(Text)
    agency: Mapped[str | None] = mapped_column(Text)
    debut_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="active")
    links: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    avatar_url: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("kind", "name_norm", name="artist_kind_namenorm_uniq"),
        Index("artist_name_norm_idx", "name_norm"),
    )


class ArtistMember(Base):
    """地偶团体 ↔ 成员（含毕业）。"""

    __tablename__ = "artist_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("artist.id", ondelete="CASCADE"))
    member_id: Mapped[int] = mapped_column(ForeignKey("artist.id", ondelete="CASCADE"))
    joined_at: Mapped[dt.date | None] = mapped_column(Date)
    left_at: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="active")

    __table_args__ = (
        UniqueConstraint("group_id", "member_id", name="artist_member_uniq"),
    )


class Event(Base, TimestampMixin):
    """活动 / 系列（稳定身份）。"""

    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_raw: Mapped[str] = mapped_column(Text, nullable=False)
    title_display: Mapped[str] = mapped_column(Text, nullable=False)
    title_norm: Mapped[str] = mapped_column(Text, nullable=False)
    series_key: Mapped[str | None] = mapped_column(Text)
    series_vol: Mapped[str | None] = mapped_column(Text)  # TEXT！
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    is_idol: Mapped[bool] = mapped_column(Boolean, default=False)
    # 与 is_idol 正交的独立标记：可叠加（一个 ACG 女子乐队两个都为真）
    is_girl_band: Mapped[bool] = mapped_column(Boolean, default=False)
    is_acg: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    poster_url: Mapped[str | None] = mapped_column(Text)
    poster_thumb: Mapped[str | None] = mapped_column(Text)
    organizer_id: Mapped[int | None] = mapped_column(ForeignKey("artist.id"))
    tags: Mapped[list[str]] = mapped_column(StrArrayType, default=list)
    external_ids: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    occurrences: Mapped[list[Occurrence]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    aliases: Mapped[list[EventAlias]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("event_title_norm_idx", "title_norm"),
        Index("event_kind_idx", "kind"),
        Index("event_is_idol_idx", "is_idol"),
        Index("event_is_girl_band_idx", "is_girl_band"),
        Index("event_is_acg_idx", "is_acg"),
    )


class EventAlias(Base):
    __tablename__ = "event_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_norm: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, default="")

    event: Mapped[Event] = relationship(back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("alias_norm", "source", name="event_alias_uniq"),
    )


class Occurrence(Base, TimestampMixin):
    """场次 —— 日历的最小单位。"""

    __tablename__ = "occurrence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"))
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venue.id"))
    venue_raw: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str] = mapped_column(Text, nullable=False)

    start_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    open_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    date_precision: Mapped[str] = mapped_column(Text, default="exact")

    status: Mapped[str] = mapped_column(Text, default="on_sale")
    status_note: Mapped[str | None] = mapped_column(Text)

    price_min: Mapped[float | None] = mapped_column(Numeric(8, 2))
    price_max: Mapped[float | None] = mapped_column(Numeric(8, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)

    age_limit: Mapped[str | None] = mapped_column(Text)  # TEXT：可能是「1.2m 以下」
    id_required: Mapped[bool] = mapped_column(Boolean, default=False)
    tokuten_note: Mapped[str | None] = mapped_column(Text)   # 特典会规则
    schedule_note: Mapped[str | None] = mapped_column(Text)  # 分钟级流程
    extra: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)

    confidence: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)
    verified_by: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(Text)

    event: Mapped[Event] = relationship(back_populates="occurrences")
    venue: Mapped[Venue | None] = relationship(back_populates="occurrences")
    artists: Mapped[list[OccurrenceArtist]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )
    tickets: Mapped[list[TicketTier]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )
    sources: Mapped[list[OccurrenceSource]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("occ_start_idx", "start_at"),
        Index("occ_city_start_idx", "city", "start_at"),
        Index("occ_venue_start_idx", "venue_id", "start_at"),
        Index("occ_fingerprint_idx", "fingerprint"),
    )


class OccurrenceArtist(Base):
    """本场阵容（含登场顺序）。带 source —— 实测各源阵容不一致。"""

    __tablename__ = "occurrence_artist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("occurrence.id", ondelete="CASCADE")
    )
    artist_id: Mapped[int | None] = mapped_column(ForeignKey("artist.id"))
    artist_raw: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, default="performer")
    billing_order: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)

    occurrence: Mapped[Occurrence] = relationship(back_populates="artists")

    __table_args__ = (
        UniqueConstraint(
            "occurrence_id", "artist_raw", "role", "source", name="occ_artist_uniq"
        ),
    )


class TicketTier(Base):
    __tablename__ = "ticket_tier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("occurrence.id", ondelete="CASCADE")
    )
    name_raw: Mapped[str] = mapped_column(Text, nullable=False)
    tier_type: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Numeric(8, 2))
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False)  # ¥0.01 占位票
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    sales_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    sales_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="unknown")
    channel: Mapped[str | None] = mapped_column(Text)
    purchase_url: Mapped[str | None] = mapped_column(Text)
    limit_per_user: Mapped[int | None] = mapped_column(Integer)
    includes_tokuten_count: Mapped[int | None] = mapped_column(Integer)
    duration_per_ticket: Mapped[str | None] = mapped_column(Text)  # TEXT：「约 60 秒」
    includes_benefits: Mapped[list[str]] = mapped_column(StrArrayType, default=list)
    note: Mapped[str | None] = mapped_column(Text)

    occurrence: Mapped[Occurrence] = relationship(back_populates="tickets")


# --------------------------------------------------------------------------- #
# 采集与溯源层
# --------------------------------------------------------------------------- #

class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # ticketing|venue|social|aggregator
    base_url: Mapped[str | None] = mapped_column(Text)
    crawl_method: Mapped[str] = mapped_column(Text, default="http")
    priority: Mapped[int] = mapped_column(Integer, default=100)  # 越小越优先
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    robots_note: Mapped[str | None] = mapped_column(Text)
    rate_limit: Mapped[str | None] = mapped_column(Text)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )


class Channel(Base):
    """监控对象：场地账号 / 偶像团体 / 主办 / 聚合页 / 搜索词。

    这是「全自动」的核心配置表，支持由搜索结果自动扩充。
    """

    __tablename__ = "channel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    channel_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    fetch_mode: Mapped[str] = mapped_column(Text, nullable=False)  # http|cdp_render|cdp_xhr|cdp_click|rss
    url_template: Mapped[str] = mapped_column(Text, nullable=False)
    match_pattern: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    is_idol: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    discovered_from: Mapped[str | None] = mapped_column(Text)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "platform", "channel_type", "external_id", name="channel_uniq"
        ),
    )


class RawSnapshot(Base):
    """原始抓取快照：解析器可回归重跑。"""

    __tablename__ = "raw_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channel.id"))
    external_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    html_path: Mapped[str | None] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )

    __table_args__ = (
        Index("raw_snapshot_dedup_idx", "source_id", "url", "content_hash"),
    )


class PageCache(Base):
    """浏览器渲染结果缓存：浏览器源很贵，用内容指纹跳过无变化渲染。"""

    __tablename__ = "page_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channel.id"))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, default="render")
    text_content: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    html_path: Mapped[str | None] = mapped_column(Text)
    shot_path: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )

    __table_args__ = (
        Index("page_cache_dedup_idx", "channel_id", "url", "content_hash"),
    )


class OccurrenceSource(Base):
    """场次 ←→ 来源：一条场次可能来自多个源，用于展示与冲突仲裁。"""

    __tablename__ = "occurrence_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(
        ForeignKey("occurrence.id", ondelete="CASCADE")
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id"))
    external_id: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    occurrence: Mapped[Occurrence] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint(
            "occurrence_id", "source_id", "external_id", name="occ_source_uniq"
        ),
    )


class PosterExtraction(Base):
    """海报视觉抽取结果（截图分析路线的落地表）。

    同时承担**海报图片缓存**的职责：`image_sha256` 是缓存文件名，
    `cache_path` 指向磁盘上的文件。这样「抓图 → 缓存 → 展示」与
    「抓图 → 视觉模型 → 结构化」共用一套去重与存储。
    """

    __tablename__ = "poster_extraction"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int | None] = mapped_column(ForeignKey("occurrence.id"))
    image_url: Mapped[str | None] = mapped_column(Text)
    image_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    # ---- 图片缓存（新增）----
    cache_path: Mapped[str | None] = mapped_column(Text)       # 相对 data/posters 的路径
    content_type: Mapped[str | None] = mapped_column(Text)
    byte_size: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )

    __table_args__ = (
        UniqueConstraint("image_sha256", "model", name="poster_extraction_uniq"),
    )


# --------------------------------------------------------------------------- #
# 运营层
# --------------------------------------------------------------------------- #

class Subscription(Base):
    __tablename__ = "subscription"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_key: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, default="ics")  # ics|email|webpush
    target: Mapped[str | None] = mapped_column(Text)
    filter: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    last_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )


class ReviewTask(Base):
    """待人工确认队列（低置信度 / 场地未匹配 / 冲突）。"""

    __tablename__ = "review_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("occurrence.id", ondelete="CASCADE")
    )
    raw_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("raw_snapshot.id"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(Text, default="open")
    assignee: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )


class FetchLog(Base):
    """每次采集的留痕：源健康度看板的数据来源。"""

    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    items_parsed: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )

    __table_args__ = (Index("fetch_log_source_idx", "source_code", "created_at"),)


ALL_MODELS = [
    Venue, VenueSourceMap, Artist, ArtistMember, Event, EventAlias, Occurrence,
    OccurrenceArtist, TicketTier, Source, Channel, RawSnapshot, PageCache,
    OccurrenceSource, PosterExtraction, Subscription, ReviewTask, FetchLog,
]
