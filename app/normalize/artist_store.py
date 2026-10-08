"""演员知识库的**落库**层：把知识库条目写进 artist 表并回填历史数据。

设计取舍：复用既有的 `artist` 表，而不是新建一张平行表 ——
`Artist.kind` 早就定义了 `band|idol_group|idol_member|solo|organizer|dj`，
`aliases` / `agency` / `origin_city` 字段也都在，只是从没被填过。
另起一张表会造成「同一个概念两处存储」，迟早漂移。

流程：
  1. `sync_knowledge_base()` 把 `artist_kb.SEED_ARTISTS` 写进 artist 表
     （按 (kind, name_norm) 去重；已存在则补齐 aliases/agency/evidence 等空缺）
  2. `reclassify_events()` 按**阵容**重算 event 的垂类标记，
     并在 review_task 里记录依据，便于人工复核
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artist, Event, Occurrence, OccurrenceArtist, ReviewTask
from app.normalize.artist_kb import (
    SEED_ARTISTS,
    ArtistKnowledgeBase,
    KnowledgeEntry,
    classify_lineup,
)
from app.parsers import text_zh as tz
from app.utils import get_logger, now_cst

log = get_logger(__name__)

FLAG_FIELDS = ("is_idol", "is_girl_band", "is_acg")


async def load_knowledge_base(session: AsyncSession) -> ArtistKnowledgeBase:
    """从数据库加载知识库（种子 + 人工/联网补充的都在一起）。

    ⚠️ 只加载**有明确分类结论**的条目，三类排除在外：
      * `kind` 为空 / `auto` / `unknown` —— 采集时没能核实的占位记录。
        早期把 `band` 当占位默认值，结果 282 个没核实过的演员全被当成
        「已知乐队」，知识库形同失效（每个名字都"认识"，投票失去意义）。
      * `kind='organizer'` —— 主办方不是演出人员，不参与垂类投票。

    判断「是否已核实」的依据是 `links.evidence`：
    有证据（人工确认或联网来源）才认为是结论。这是**刻意保守**的设计 ——
    宁可少认几个，也不让未核实的数据冒充事实。
    """
    kb = ArtistKnowledgeBase(entries=[])
    rows = (await session.execute(select(Artist))).scalars().all()
    skipped = 0
    for a in rows:
        if not a.kind or a.kind in ("unknown", "auto", "organizer"):
            skipped += 1
            continue
        links = a.links or {}
        # 需要证据才算「已核实」；没有证据的只当规则推断，不覆盖规则判定
        if not links.get("evidence"):
            skipped += 1
            continue
        kb.add(
            KnowledgeEntry(
                name=a.name,
                kind=a.kind,
                aliases=tuple(a.aliases or []),
                agency=a.agency,
                origin_city=a.origin_city,
                confidence=float(links.get("confidence", 0.8)),
                evidence=str(links.get("evidence", "")),
                source=str(links.get("source", "db")),
            )
        )
    if skipped:
        log.info("知识库：跳过 %d 个未核实条目（无 kind 结论或无 evidence）", skipped)
    return kb


async def sync_knowledge_base(session: AsyncSession) -> dict[str, int]:
    """把种子知识库写进 artist 表。返回统计。"""
    stats = {"added": 0, "updated": 0, "skipped": 0}
    existing = {
        (a.kind, a.name_norm): a
        for a in (await session.execute(select(Artist))).scalars().all()
    }

    for entry in SEED_ARTISTS:
        norm = tz.normalize_name(entry.name)
        if not norm:
            continue
        # 种子条目的 kind 可能与库里已有的占位 kind（如 'band'）不同 ——
        # 按 name_norm 找已有行，命中就纠正它的 kind，而不是产生重复演员
        found = None
        for (k, n), a in existing.items():
            if n == norm:
                found = a
                break

        links = {
            "evidence": entry.evidence,
            "source": entry.source,
            "confidence": entry.confidence,
            "verified_at": now_cst().isoformat(),
        }
        if found is None:
            a = Artist(
                kind=entry.kind,
                name=entry.name,
                name_norm=norm,
                aliases=list(entry.aliases),
                agency=entry.agency,
                origin_city=entry.origin_city,
                status="active",
                links=links,
            )
            session.add(a)
            existing[(entry.kind, norm)] = a
            stats["added"] += 1
        else:
            changed = False
            if found.kind != entry.kind:
                found.kind = entry.kind          # 纠正占位分类
                changed = True
            if entry.aliases and not set(entry.aliases) <= set(found.aliases or []):
                found.aliases = sorted(set(found.aliases or []) | set(entry.aliases))
                changed = True
            if entry.agency and found.agency != entry.agency:
                found.agency = entry.agency
                changed = True
            if entry.origin_city and found.origin_city != entry.origin_city:
                found.origin_city = entry.origin_city
                changed = True
            if changed or not (found.links or {}).get("evidence"):
                found.links = {**(found.links or {}), **links}
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

    await session.flush()
    log.info("知识库同步：新增 %(added)d / 更新 %(updated)d / 跳过 %(skipped)d", stats)
    return stats


async def reclassify_events(
    session: AsyncSession, *, dry_run: bool = False
) -> dict[str, object]:
    """按**阵容**重算全部 event 的垂类标记（与标题判定取并集）。

    返回统计与改动样本。每条改动都会写进 review_task，附带依据，
    因为「按阵容重分类」是推断而非事实，必须可复核、可回滚。
    """
    kb = await load_knowledge_base(session)
    events = (await session.execute(select(Event))).scalars().all()

    # 一次性把「场次 → 阵容」读进来，避免 N+1 查询
    rows = (
        await session.execute(
            select(Occurrence.event_id, OccurrenceArtist.artist_raw).join(
                OccurrenceArtist, OccurrenceArtist.occurrence_id == Occurrence.id
            )
        )
    ).all()
    lineups: dict[int, list[str]] = {}
    for ev_id, raw in rows:
        if ev_id and raw:
            lineups.setdefault(ev_id, []).append(raw)

    result: dict[str, object] = {
        "events": len(events),
        "changed": 0,
        "gained": {"is_idol": 0, "is_girl_band": 0, "is_acg": 0},
        "samples": [],
        "no_lineup": 0,
        "unknown_artists": set(),
    }

    for ev in events:
        names = lineups.get(ev.id, [])
        if not names:
            result["no_lineup"] = int(result["no_lineup"]) + 1
            # 没有阵容：保持标题判定结果不变（不能凭标题之外的信息乱改）
            continue
        verdict = classify_lineup(names, kb)
        result["unknown_artists"].update(verdict.unknown)  # type: ignore[union-attr]

        before = {f: bool(getattr(ev, f)) for f in FLAG_FIELDS}
        after = {
            f: before[f] or bool(getattr(verdict, f))
            for f in FLAG_FIELDS
        }
        if after == before:
            continue
        result["changed"] = int(result["changed"]) + 1
        for f in FLAG_FIELDS:
            if after[f] and not before[f]:
                result["gained"][f] += 1  # type: ignore[index]

        if len(result["samples"]) < 15:  # type: ignore[arg-type]
            result["samples"].append(  # type: ignore[union-attr]
                {
                    "title": ev.title_display,
                    "before": before,
                    "after": after,
                    "lineup": names,
                    "votes": verdict.votes,
                }
            )
        if dry_run:
            continue

        ev.is_idol = after["is_idol"]
        ev.is_girl_band = after["is_girl_band"]
        ev.is_acg = after["is_acg"]
        # 记录依据：这是**推断**而非事实，必须可复核、可回滚。
        # ReviewTask 的字段是 reason/payload（没有 target_type 之类）。
        session.add(
            ReviewTask(
                reason=f"按阵容重分类 event#{ev.id}",
                status="auto",
                payload={
                    "event_id": ev.id,
                    "title": ev.title_display,
                    "lineup": names,
                    "votes": verdict.votes,
                    "reasons": verdict.reasons,
                    "before": before,
                    "after": after,
                },
            )
        )

    if not dry_run:
        await session.flush()
    result["unknown_artists"] = sorted(result["unknown_artists"])  # type: ignore[arg-type]
    log.info(
        "按阵容重分类%s：扫描 %d 个活动，改动 %d 个（新增标记 地偶+%d 女子+%d ACG+%d），"
        "无阵容跳过 %d",
        "（演练）" if dry_run else "",
        result["events"], result["changed"],
        result["gained"]["is_idol"], result["gained"]["is_girl_band"],
        result["gained"]["is_acg"], result["no_lineup"],
    )
    return result
