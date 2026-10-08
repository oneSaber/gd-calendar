"""入库服务：把 ParsedOccurrence 归一化后写进数据库。

这是采集与存储之间的唯一入口，负责：
  * 场地/艺人实体对齐（不存在则创建）
  * event 与 occurrence 的创建与匹配（三层判重）
  * 多来源挂载（occurrence_source）与来源优先级
  * 冲突不静默覆盖：时间不一致 → 写 review_task
  * event.aliases 记录跨平台不同写法
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artist,
    Event,
    EventAlias,
    FetchLog,
    Occurrence,
    OccurrenceArtist,
    OccurrenceSource,
    ReviewTask,
    Source,
    TicketTier,
    Venue,
    VenueSourceMap,
)
from app.models import ParsedOccurrence
from app.normalize import (
    choose_existing,
    is_ambiguous_alias,
    match_venue,
    soft_fingerprint,
)
from app.parsers import text_zh as tz
from app.utils import aware, dt_equal, get_logger, now_cst

log = get_logger(__name__)

# 来源优先级：越小越可信（冲突仲裁用）
SOURCE_PRIORITY: dict[str, tuple[str, str, str, int]] = {
    # code: (name, kind, crawl_method, priority)
    "showstart": ("秀动", "ticketing", "http", 10),
    "bilibili": ("B站（动态/会员购）", "social", "cdp_render", 30),
    "weibo": ("微博", "social", "cdp_xhr", 40),
    "douban": ("豆瓣同城", "aggregator", "http", 50),
    "venue_site": ("场地官网", "venue", "http", 20),
    "huodong": ("活动网", "aggregator", "cdp_render", 60),
    "manual": ("人工录入", "manual", "manual", 0),
}


@dataclass
class IngestStats:
    seen: int = 0
    created_event: int = 0
    created_occurrence: int = 0
    updated_occurrence: int = 0
    skipped_no_time: int = 0
    skipped_past: int = 0
    skipped_not_event: int = 0
    merged_similar: int = 0
    conflicts: int = 0
    new_venues: int = 0
    new_artists: int = 0
    duplicate_events: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"收到 {self.seen} 条 → 新建活动 {self.created_event} / 新建场次 "
            f"{self.created_occurrence} / 更新 {self.updated_occurrence} / "
            f"相似合并 {self.merged_similar} / 冲突 {self.conflicts} / "
            f"缺时间丢弃 {self.skipped_no_time} / 已过期丢弃 {self.skipped_past} / "
            f"非事件丢弃 {self.skipped_not_event} / "
            f"新场地 {self.new_venues} / 新艺人 {self.new_artists}"
        )


class Ingestor:
    """把解析结果写库。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._venue_catalog: dict[str, list[tuple[int, str]]] | None = None
        self._artist_catalog: dict[tuple[str, str], int] = {}
        # 演员知识库（load_artist_catalog 里填充）。按**演出人员**判定分类。
        self._artist_kb = None
        # 同一轮内的内存索引：软指纹 → occurrence_id
        self._soft_index: dict[str, list[tuple[int, int, str]]] = {}
        # occurrence_id → 已有 start_at（用于冲突检测）
        self._occ_times: dict[int, dt.datetime | None] = {}

    # ------------------------------------------------------------------ #
    # 基础：来源 / 目录
    # ------------------------------------------------------------------ #

    async def ensure_sources(self) -> dict[str, int]:
        """确保 source 表里有基础来源记录，返回 code → id。"""
        existing = (await self.session.execute(select(Source))).scalars().all()
        by_code = {s.code: s for s in existing}
        for code, (name, kind, method, prio) in SOURCE_PRIORITY.items():
            if code not in by_code:
                s = Source(
                    code=code, name=name, kind=kind, crawl_method=method,
                    priority=prio, enabled=True,
                    rate_limit="1 req / 3s" if method == "http" else "12h 轮询",
                )
                self.session.add(s)
                by_code[code] = s
        await self.session.flush()
        return {code: s.id for code, s in by_code.items()}

    async def load_venue_catalog(self) -> dict[str, list[tuple[int, str]]]:
        """加载场地匹配目录：归一化名/别名 → [(venue_id, name)]。"""
        if self._venue_catalog is not None:
            return self._venue_catalog
        catalog: dict[str, list[tuple[int, str]]] = {}
        venues = (await self.session.execute(select(Venue))).scalars().all()
        for v in venues:
            keys = {tz.normalize_name(v.name)}
            for a in (v.aliases or []):
                keys.add(tz.normalize_name(a))
            for k in keys:
                if k:
                    catalog.setdefault(k, []).append((v.id, v.name))
        self._venue_catalog = catalog
        log.info("场地目录加载 %d 个键（%d 个场地）", len(catalog), len(venues))
        return catalog

    async def load_artist_catalog(self) -> None:
        rows = (await self.session.execute(select(Artist))).scalars().all()
        for a in rows:
            self._artist_catalog[(a.kind, a.name_norm)] = a.id
        # 同时加载演员知识库：分类要按**演出人员**判定，而不是只看标题。
        # 与 artist 表是同一份数据的两种视图（表用于持久化与人工维护，
        # 知识库对象用于快速匹配 别名/命名规则）。
        from app.normalize.artist_store import load_knowledge_base

        self._artist_kb = await load_knowledge_base(self.session)
        log.info("演员知识库加载 %d 条", len(self._artist_kb))

    def _invalidate_catalogs(self) -> None:
        self._venue_catalog = None

    # ------------------------------------------------------------------ #
    # 实体：场地 / 艺人
    # ------------------------------------------------------------------ #

    async def get_or_create_venue(
        self, name: str | None, city: str | None, stats: IngestStats,
        external: tuple[str, str] | None = None,
    ) -> int | None:
        if not name or not city:
            return None
        # ⚠️ 必须清洗：源里的 venue_raw 常带完整地址（豆瓣「场地名 + 街道号」），
        # 不清洗会让地址进场地表，并导致同一场地被拆成多条。
        name = tz.clean_venue(name) or name
        catalog = await self.load_venue_catalog()
        vid, score = match_venue(name, city, catalog)
        if vid is not None:
            if external:
                await self._link_venue_source(vid, external[0], external[1], name)
            return vid

        # 新建：注意「不同门牌 = 不同场地」，这里不做任何跨场地合并
        v = Venue(name=name, city=city, venue_type="livehouse", aliases=[])
        self.session.add(v)
        await self.session.flush()
        stats.new_venues += 1
        self._invalidate_catalogs()
        await self.load_venue_catalog()
        if external:
            await self._link_venue_source(v.id, external[0], external[1], name)
        log.info("新场地: %s (%s)", name, city)
        return v.id

    async def _link_venue_source(
        self, venue_id: int, source_code: str, external_id: str, name: str
    ) -> None:
        exists = (
            await self.session.execute(
                select(VenueSourceMap).where(
                    VenueSourceMap.source_code == source_code,
                    VenueSourceMap.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            self.session.add(
                VenueSourceMap(
                    venue_id=venue_id, source_code=source_code,
                    external_id=external_id, external_name=name,
                )
            )

    async def get_or_create_artist(
        self, name_raw: str, kind: str, stats: IngestStats
    ) -> int | None:
        norm = tz.normalize_name(name_raw)
        if not norm:
            return None
        key = (kind, norm)
        if key in self._artist_catalog:
            return self._artist_catalog[key]
        # 分类：**先查演员知识库**（按演出人员），再退回命名规则。
        # 为什么顺序重要：知识库是核实过的事实，而「恋时青空」「比邻星球」
        # 这类名字不含任何关键词，靠命名规则只会得到占位值。
        #
        # ⚠️ 兜底必须是 `unknown` 而不是 `band`：`band` 是**分类结论**，
        # `unknown` 才是「还没核实」。早期用 band 兜底，导致 282 个没核实过的
        # 演员全被当成「已知的乐队」，知识库形同失效（每个名字都"认识"）。
        real_kind = kind
        if kind in ("auto", None):
            real_kind = "unknown"
            kb = self._artist_kb
            if kb is not None:
                entry, how = kb.lookup(name_raw)
                if entry is not None and how in ("name", "alias"):
                    real_kind = entry.kind
                elif entry is not None and how == "pattern":
                    # 命名规则命中：记为**推断**结果，仍可与人工核实结果共存
                    real_kind = entry.kind
            if real_kind == "unknown":
                # 知识库与命名规则都没结论 → 用标题分类器最后一次尝试
                _, is_idol, _ = tz.classify(name_raw)
                if is_idol:
                    real_kind = "idol_group"
        key = (real_kind, norm)
        if key in self._artist_catalog:
            return self._artist_catalog[key]

        a = Artist(kind=real_kind, name=name_raw, name_norm=norm, aliases=[])
        self.session.add(a)
        await self.session.flush()
        self._artist_catalog[key] = a.id
        stats.new_artists += 1
        return a.id

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    async def ingest(
        self, occurrences: list[ParsedOccurrence], *, window_days: int = 400
    ) -> IngestStats:
        stats = IngestStats()
        sources = await self.ensure_sources()
        await self.load_venue_catalog()
        await self.load_artist_catalog()
        await self._preload_existing(window_days)

        now = now_cst()
        for occ in occurrences:
            stats.seen += 1
            if not occ.start_at:
                stats.skipped_no_time += 1
                continue
            # SQLite 读回的时间是 naive，统一补时区后再比较
            start = aware(occ.start_at)
            assert start is not None
            if start < now - dt.timedelta(days=1):
                stats.skipped_past += 1
                continue
            # 只过滤事件类型的噪音（如微博的「主催情报」公告）；
            # 豆瓣的脱口秀/话剧是真实活动，由 kind='other' + API 默认排除处理。
            if not tz.looks_like_event_title(occ.title_display or occ.title_raw):
                stats.skipped_not_event += 1
                continue
            try:
                await self._ingest_one(occ, start, sources, stats)
            except Exception as exc:  # noqa: BLE001
                log.exception("入库失败 %s: %s", occ.title_display or occ.title_raw, exc)
                stats.notes.append(f"入库失败: {occ.title_display or occ.title_raw} ({exc})")

        await self.session.flush()
        log.info("入库完成：%s", stats.summary())
        return stats

    async def _preload_existing(self, window_days: int) -> None:
        """预载未来时间窗内的场次，建立内存判重索引。"""
        now = now_cst()
        end = now + dt.timedelta(days=window_days)
        rows = (
            await self.session.execute(
                select(Occurrence.id, Occurrence.start_at, Occurrence.city,
                       Occurrence.event_id)
                .where(Occurrence.start_at >= now - dt.timedelta(days=1),
                       Occurrence.start_at <= end)
            )
        ).all()
        events = {
            e.id: e
            for e in (await self.session.execute(select(Event))).scalars().all()
        }
        for occ_id, start_at, city, event_id in rows:
            self._occ_times[occ_id] = aware(start_at)
            ev = events.get(event_id)
            if ev is None:
                continue
            day = aware(start_at).date().isoformat() if start_at else "nodate"
            venue_norm = ""  # 预载阶段用 event 标题 + 日期做弱索引
            key = soft_fingerprint(venue_norm, day, ev.title_norm)
            self._soft_index.setdefault(key, []).append((occ_id, event_id, ev.title_norm))
        log.info("预载存量场次 %d 条（判重索引 %d 键）", len(rows), len(self._soft_index))

    async def _ingest_one(
        self,
        occ: ParsedOccurrence,
        start: dt.datetime,
        sources: dict[str, int],
        stats: IngestStats,
    ) -> None:
        source_id = sources.get(occ.source_code)
        if source_id is None:
            s = Source(code=occ.source_code, name=occ.source_code, kind="social",
                       crawl_method="http", priority=100)
            self.session.add(s)
            await self.session.flush()
            sources[occ.source_code] = s.id
            source_id = s.id

        # ---- 0) 强键判重：同一来源同一 external_id 已存在则更新 ----
        if occ.external_id:
            existing_src = (
                await self.session.execute(
                    select(OccurrenceSource).where(
                        OccurrenceSource.source_id == source_id,
                        OccurrenceSource.external_id == occ.external_id,
                    )
                )
            ).scalar_one_or_none()
            if existing_src is not None:
                await self._update_occurrence(
                    existing_src.occurrence_id, occ, start, source_id, stats,
                    matched_by="source_key",
                )
                return

        # ---- 1) 场地 ----
        venue_id = await self.get_or_create_venue(
            occ.venue_raw, occ.city, stats,
            external=(occ.source_code, occ.venue_raw) if occ.venue_raw else None,
        )
        # 存进 occurrence 的也应是清洗后的名字（保持与场地表一致）
        clean_venue_raw = tz.clean_venue(occ.venue_raw) or occ.venue_raw

        title_norm = tz.normalize_title(occ.title_display or occ.title_raw)
        day = start.date().isoformat()

        # ---- 2) 弱指纹匹配（同场地同日同标题）----
        venue_norm = tz.normalize_name(occ.venue_raw or "")
        key = soft_fingerprint(venue_norm, day, title_norm)
        candidates = self._soft_index.get(key, [])
        matched = candidates[0] if candidates else None
        if matched is None:
            # 退一步：同城同日候选里做标题相似度
            same_day = [
                (oid, eid, tn)
                for k, v in self._soft_index.items()
                for (oid, eid, tn) in v
                if (aware(self._occ_times.get(oid)) or start).date() == start.date()
            ]
            res = choose_existing([(oid, tn) for oid, _eid, tn in same_day], title_norm)
            if res.kind in ("exact", "similar") and res.matched_id is not None:
                matched = next(
                    (t for t in same_day if t[0] == res.matched_id), None
                )
                if res.kind == "similar":
                    stats.merged_similar += 1

        if matched is not None:
            await self._update_occurrence(
                matched[0], occ, start, source_id, stats,
                matched_by="fingerprint", venue_id=venue_id,
            )
            return

        # ---- 3) 新建 event + occurrence ----
        event = Event(
            title_raw=occ.title_raw,
            title_display=occ.title_display or tz.display_title(occ.title_raw),
            title_norm=title_norm,
            series_key=occ.series_key,
            series_vol=occ.series_vol,
            kind=occ.kind,
            is_idol=occ.is_idol,
            is_girl_band=occ.is_girl_band,
            is_acg=occ.is_acg,
            tags=occ.tags,
            description=occ.description,
            poster_url=occ.poster_url,
            external_ids={occ.source_code: occ.external_id} if occ.external_id else {},
        )
        if occ.organizer:
            event.organizer_id = await self.get_or_create_artist(occ.organizer, "organizer", stats)
        self.session.add(event)
        await self.session.flush()
        stats.created_event += 1

        o = Occurrence(
            event_id=event.id,
            venue_id=venue_id,
            venue_raw=clean_venue_raw,
            city=occ.city or "广州",
            start_at=start,
            open_at=occ.open_at,
            end_at=occ.end_at,
            date_precision=occ.date_precision,
            status=occ.status if occ.status != "unknown" else "on_sale",
            status_note=occ.status_note,
            price_min=occ.price_min,
            price_max=occ.price_max,
            is_free=occ.is_free,
            age_limit=occ.age_limit,
            id_required=occ.id_required,
            tokuten_note=occ.tokuten_note,
            schedule_note=occ.schedule_note,
            extra=occ.extra,
            confidence=occ.confidence,
        )
        self.session.add(o)
        await self.session.flush()
        stats.created_occurrence += 1

        await self._write_children(o.id, occ, src=occ.source_code, stats=stats)
        self.session.add(
            OccurrenceSource(
                occurrence_id=o.id, source_id=source_id,
                external_id=occ.external_id or None,
                source_url=occ.source_url, is_primary=True,
            )
        )
        self._index_new(o.id, event.id, title_norm, venue_norm, day, start)

    async def _update_occurrence(
        self,
        occ_id: int,
        occ: ParsedOccurrence,
        start: dt.datetime,
        source_id: int,
        stats: IngestStats,
        *,
        matched_by: str = "",
        venue_id: int | None = None,
    ) -> None:
        o = await self.session.get(Occurrence, occ_id)
        if o is None:
            return
        old_start = aware(o.start_at)

        # 冲突检测：同一场次两个来源给出的时间不一致 → 不静默覆盖
        if old_start and start and not dt_equal(old_start, start):
            stats.conflicts += 1
            self.session.add(
                ReviewTask(
                    occurrence_id=o.id,
                    reason="conflict",
                    payload={
                        "matched_by": matched_by,
                        "existing_start_at": old_start.isoformat(),
                        "incoming_start_at": start.isoformat(),
                        "incoming_source": occ.source_code,
                        "incoming_url": occ.source_url,
                    },
                )
            )
            log.info("时间冲突 occ=%s: %s vs %s（%s）", o.id, old_start, start, occ.source_code)
        else:
            o.start_at = start

        # 空值不覆盖已有值（后到的信息更少时不要破坏数据）
        for attr in ("open_at", "end_at", "venue_raw", "age_limit",
                     "tokuten_note", "schedule_note", "status_note"):
            new = getattr(occ, attr, None)
            if new and not getattr(o, attr, None):
                setattr(o, attr, new)
        if occ.price_min is not None and o.price_min is None:
            o.price_min = occ.price_min
        if occ.price_max is not None and o.price_max is None:
            o.price_max = occ.price_max
        if occ.is_free:
            o.is_free = True
        if venue_id and not o.venue_id:
            o.venue_id = venue_id
        # 状态以更「新」的信息为准（售罄/改期/取消是强信号）
        if occ.status in ("sold_out", "postponed", "cancelled") and occ.status != o.status:
            o.status = occ.status
        o.confidence = max(float(o.confidence or 0), occ.confidence)

        # 分类标记是「一旦判定为真就保留」：某些源标题信息更少，
        # 不应把别的源已判定出的标记覆盖掉（例如秀动标了 ACG，微博那条没标）。
        ev = await self.session.get(Event, o.event_id)
        if ev is not None:
            if occ.is_idol and not ev.is_idol:
                ev.is_idol = True
            if occ.is_girl_band and not ev.is_girl_band:
                ev.is_girl_band = True
            if occ.is_acg and not ev.is_acg:
                ev.is_acg = True

        await self._write_children(o.id, occ, src=occ.source_code, append=True, stats=stats)

        exists = (
            await self.session.execute(
                select(OccurrenceSource).where(
                    OccurrenceSource.occurrence_id == o.id,
                    OccurrenceSource.source_id == source_id,
                    OccurrenceSource.external_id == (occ.external_id or None),
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            self.session.add(
                OccurrenceSource(
                    occurrence_id=o.id, source_id=source_id,
                    external_id=occ.external_id or None,
                    source_url=occ.source_url, is_primary=False,
                )
            )
        stats.updated_occurrence += 1

    async def _write_children(
        self, occ_id: int, occ: ParsedOccurrence, *, src: str,
        append: bool = False, stats: IngestStats | None = None,
    ) -> None:
        """写阵容与票种。注意：阵容带 source —— 实测各源阵容不一致。"""
        if append:
            existing = (
                await self.session.execute(
                    select(OccurrenceArtist).where(OccurrenceArtist.occurrence_id == occ_id)
                )
            ).scalars().all()
            have = {(a.artist_raw, a.source) for a in existing}
        else:
            have = set()

        billing = 0
        for idx, a in enumerate(occ.artists, start=1):
            # ⚠️ 实测：秀动「艺人」字段会把多组用 `/` `&` `×` 连成一串
            # （如「娜娜捏口俱乐部/ReaLume/恋时青空/DigitalDuel」「体熊专科/JASON KUI」）。
            # 入库层必须再拆一次，否则阵容会变成一条无意义的巨串。
            for raw in tz.split_lineup_names(a.name_raw):
                if (raw, src) in have:
                    continue
                billing += 1
                aid = await self.get_or_create_artist(raw, "auto", stats or IngestStats())
                self.session.add(
                    OccurrenceArtist(
                        occurrence_id=occ_id, artist_id=aid, artist_raw=raw,
                        role=a.role or "performer",
                        billing_order=billing,
                        source=src, confidence=a.confidence,
                        is_cancelled=a.is_cancelled, note=a.note,
                    )
                )
                have.add((raw, src))

        if not append:
            for tk in occ.tickets:
                self.session.add(
                    TicketTier(
                        occurrence_id=occ_id, name_raw=tk.name_raw,
                        tier_type=tk.tier_type, price=tk.price,
                        is_placeholder=tk.is_placeholder,
                        status=tk.status, channel=tk.channel or occ.source_code,
                        purchase_url=tk.purchase_url,
                        includes_tokuten_count=tk.includes_tokuten_count,
                        duration_per_ticket=tk.duration_per_ticket,
                        includes_benefits=tk.includes_benefits, note=tk.note,
                    )
                )

    def _index_new(
        self, occ_id: int, event_id: int, title_norm: str,
        venue_norm: str, day: str, start: dt.datetime,
    ) -> None:
        self._occ_times[occ_id] = start
        key = soft_fingerprint(venue_norm, day, title_norm)
        self._soft_index.setdefault(key, []).append((occ_id, event_id, title_norm))

    # ------------------------------------------------------------------ #
    # 采集留痕
    # ------------------------------------------------------------------ #

    async def log_fetch(
        self, source_code: str, *, ok: bool, url: str | None = None,
        status: int | None = None, items: int = 0, new: int = 0,
        duration_ms: int | None = None, error: str | None = None,
        channel_id: int | None = None,
    ) -> None:
        self.session.add(
            FetchLog(
                source_code=source_code, channel_id=channel_id, url=url, ok=ok,
                http_status=status, items_parsed=items, items_new=new,
                duration_ms=duration_ms, error=error,
            )
        )

    async def mark_source_ok(self, code: str) -> None:
        s = (await self.session.execute(select(Source).where(Source.code == code))).scalar_one_or_none()
        if s is not None:
            s.last_ok_at = now_cst()
            s.last_error = None
