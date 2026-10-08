"""内部数据契约（Pydantic）。

采集器 → 解析器 → 归一化 → 入库 之间统一用这些模型传递，
避免各层直接拼 dict 导致字段漂移。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal[
    "rock_live", "doujin_live", "oneman", "taiban",
    "idol_regular", "idol_birthday", "idol_taiban",
    "festival", "tour_stop", "other",
]

DatePrecision = Literal["exact", "date_only", "month_only", "tbd"]

OccurrenceStatus = Literal[
    "announced", "on_sale", "sold_out", "postponed",
    "cancelled", "finished", "unknown",
]

FetchMode = Literal["http", "cdp_render", "cdp_xhr", "cdp_click", "rss"]


class TicketIn(BaseModel):
    """票种。"""

    model_config = ConfigDict(extra="ignore")

    name_raw: str
    tier_type: str | None = None          # 无料|预售|现场|VIP|学生|双人|通票|特典券|チェキ
    price: float | None = None
    is_placeholder: bool = False          # ¥0.01 占位票
    currency: str = "CNY"
    status: str = "unknown"
    channel: str | None = None
    purchase_url: str | None = None
    limit_per_user: int | None = None
    includes_tokuten_count: int | None = None
    duration_per_ticket: str | None = None
    includes_benefits: list[str] = Field(default_factory=list)
    note: str | None = None


class ArtistIn(BaseModel):
    """阵容成员（原始写法 + 归一化名）。"""

    model_config = ConfigDict(extra="ignore")

    name_raw: str
    name_norm: str | None = None
    role: str = "performer"               # performer|headliner|guest|host|dj|mc
    billing_order: int | None = None
    source: str = ""
    confidence: float = 0.8
    is_cancelled: bool = False
    note: str | None = None


class ParsedOccurrence(BaseModel):
    """一场演出（解析后的中间产物，尚未入库）。

    这是全系统的核心交换模型。
    """

    model_config = ConfigDict(extra="ignore")

    # 来源与身份
    source_code: str
    source_url: str
    external_id: str

    # 标题
    title_raw: str
    title_display: str | None = None
    series_key: str | None = None
    series_vol: str | None = None

    # 分类
    kind: Kind = "other"
    is_idol: bool = False
    # 与 kind/is_idol 正交的独立标记（可叠加）
    is_girl_band: bool = False
    is_acg: bool = False
    tags: list[str] = Field(default_factory=list)

    # 时间地点
    city: str | None = None
    venue_raw: str | None = None
    venue_id: int | None = None
    start_at: dt.datetime | None = None
    open_at: dt.datetime | None = None
    end_at: dt.datetime | None = None
    date_precision: DatePrecision = "tbd"

    # 票务
    status: OccurrenceStatus = "on_sale"
    status_note: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    currency: str = "CNY"
    is_free: bool = False
    tickets: list[TicketIn] = Field(default_factory=list)

    # 地偶专属
    age_limit: str | None = None
    id_required: bool = False
    tokuten_note: str | None = None
    schedule_note: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    # 阵容与主办
    artists: list[ArtistIn] = Field(default_factory=list)
    organizer: str | None = None

    # 素材与元信息
    poster_url: str | None = None
    description: str | None = None

    # 质量控制
    confidence: float = 0.8
    verified_by: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def fingerprint_source(self) -> str:
        """指纹输入：场地 + 日期 + 标题核心 + 排序后的阵容。"""
        day = self.start_at.date().isoformat() if self.start_at else "nodate"
        venue = (self.venue_raw or "").strip()
        lineup = ",".join(sorted(a.name_raw for a in self.artists))
        return f"{venue}|{day}|{(self.title_display or self.title_raw)}|{lineup}"


class ChannelIn(BaseModel):
    """一个待监控对象（场地账号 / 团体 / 搜索词）。"""

    model_config = ConfigDict(extra="ignore")

    platform: str
    channel_type: str
    external_id: str
    display_name: str | None = None
    fetch_mode: FetchMode = "http"
    url_template: str
    match_pattern: str | None = None
    city: str | None = None
    is_idol: bool = False
    priority: int = 100
    discovered_from: str | None = None


class FetchResult(BaseModel):
    """采集层统一返回。"""

    model_config = ConfigDict(extra="ignore")

    url: str
    ok: bool
    status: int | None = None
    mode: str = "http"
    text: str | None = None
    payload: Any = None
    screenshot_path: str | None = None
    content_hash: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    from_cache: bool = False
