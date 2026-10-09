"""FastAPI 应用：REST + .ics 订阅 + 源健康度 + 前端静态站点。

启动：
    uvicorn app.api.main:app --reload --port 8000
前端：
    http://127.0.0.1:8000/            （app/web 静态站点）
文档：
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api import service
from app.api.schemas import (
    ArtistListOut,
    CalendarCountsOut,
    HealthOut,
    OccurrenceListOut,
    StatsOut,
    VenueListOut,
)
from app.config import settings
from app.db import dispose_db, get_engine, init_db, session_scope
from app.db.models import PosterExtraction
from app.utils import CST, get_logger, setup_logging

log = get_logger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# 中文枚举 → 展示名（前端只用英文枚举，这里给 .ics 用）
STATUS_ZH = {
    "announced": "待开票", "on_sale": "售票中", "sold_out": "售罄",
    "postponed": "已改期", "cancelled": "已取消", "finished": "已结束",
    "unknown": "待确认",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 先迁移（给已有表补列），再 create_all（建缺失的表）。
    # ⚠️ create_all 不会给已有表加列，所以版本升级时数据不会丢。
    from app.db.migrate import run_migrations

    engine = get_engine()
    async with engine.begin() as conn:
        await run_migrations(conn)
    await init_db()
    log.info("%s v%s 启动（%s）", settings.app_name, __version__, settings.database_url)
    yield
    from app.api import images
    from app.jobs.update_job import manager as update_manager

    # 先取消跑着的采集任务，再关连接池：否则 uvicorn 会等采集跑完才退出
    await update_manager.shutdown()
    await images.close_proxy()
    await dispose_db()


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="广东地下乐队与地下偶像演出日历 —— 全自动采集 + 归一化 + 日历订阅",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


async def get_session():
    async with session_scope() as s:
        yield s


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #

@app.get("/api/health", response_model=HealthOut, tags=["meta"])
async def health() -> HealthOut:
    return HealthOut(
        app=settings.app_name,
        version=__version__,
        database="sqlite" if settings.is_sqlite else "postgresql",
    )


# --------------------------------------------------------------------------- #
# 日历主查询
# --------------------------------------------------------------------------- #

@app.get("/api/occurrences", response_model=OccurrenceListOut, tags=["calendar"])
async def list_occurrences(
    session: AsyncSession = Depends(get_session),
    date_from: dt.date | None = Query(None, alias="from"),
    date_to: dt.date | None = Query(None, alias="to"),
    city: str | None = None,
    kind: str | None = None,
    is_idol: bool | None = None,
    is_girl_band: bool | None = None,
    is_acg: bool | None = None,
    flag: list[str] | None = Query(
        None,
        description="分类标记的「与」组合，可重复：flag=地偶&flag=acg 表示同时命中",
    ),
    venue_id: int | None = None,
    artist_id: int | None = None,
    artist_q: str | None = Query(
        None, description="按艺人/团体名搜索（含阵容里的原始写法）"
    ),
    price_max: float | None = None,
    status: str | None = None,
    q: str | None = None,
    include_finished: bool = False,
    exclude_other: bool = Query(
        True, description="排除「非演出」内容（脱口秀/话剧/展览等）"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> OccurrenceListOut:
    if date_from is None and date_to is None:
        # 默认：今天起 30 天
        today = dt.datetime.now(CST).date()
        date_from, date_to = today, today + dt.timedelta(days=30)

    query = service.OccurrenceQuery(
        date_from=date_from, date_to=date_to, city=city, kind=kind,
        is_idol=is_idol, is_girl_band=is_girl_band, is_acg=is_acg,
        flags_all=flag,
        venue_id=venue_id, artist_id=artist_id, artist_q=artist_q,
        price_max=price_max, status=status, q=q,
        page=page, page_size=page_size, include_finished=include_finished,
        exclude_other=exclude_other,
    )
    items, total = await service.query_occurrences(session, query)
    from app.utils import now_cst

    return OccurrenceListOut(
        items=items, total=total, page=page, page_size=page_size,
        generated_at=now_cst(),
    )


@app.get("/api/occurrences/{occ_id}", tags=["calendar"])
async def get_occurrence(occ_id: int, session: AsyncSession = Depends(get_session)):
    query = service.OccurrenceQuery(page_size=1, include_finished=True)
    items, _ = await service.query_occurrences(session, query)
    # 简单实现：直接查这一条
    from app.db.models import Occurrence

    occ = await session.get(Occurrence, occ_id)
    if occ is None:
        raise HTTPException(status_code=404, detail="场次不存在")
    q2 = service.OccurrenceQuery(
        date_from=None, date_to=None, include_finished=True, page_size=500
    )
    all_items, _ = await service.query_occurrences(session, q2)
    for it in all_items:
        if it.id == occ_id:
            return it
    raise HTTPException(status_code=404, detail="场次不存在")


@app.get("/api/calendar/counts", response_model=CalendarCountsOut, tags=["calendar"])
async def get_counts(
    month: str = Query(..., description="YYYY-MM"),
    city: str | None = None,
    exclude_other: bool = Query(
        True, description="与 /api/occurrences 保持同一口径；设为 false 才统计非演出内容"
    ),
    session: AsyncSession = Depends(get_session),
) -> CalendarCountsOut:
    try:
        days = await service.calendar_counts(
            session, month, city, exclude_other=exclude_other
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CalendarCountsOut(month=month, days=days)


# --------------------------------------------------------------------------- #
# 实体
# --------------------------------------------------------------------------- #

@app.get("/api/venues", response_model=VenueListOut, tags=["entities"])
async def get_venues(
    city: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> VenueListOut:
    return VenueListOut(items=await service.list_venues(session, city, limit))


@app.get("/api/artists", response_model=ArtistListOut, tags=["entities"])
async def get_artists(
    q: str | None = None,
    kind: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    with_upcoming: bool = Query(
        False, description="只返回有未来场次的艺人（前端搜索框用）"
    ),
    session: AsyncSession = Depends(get_session),
) -> ArtistListOut:
    return ArtistListOut(
        items=await service.list_artists(
            session, q, kind, limit, with_upcoming=with_upcoming
        )
    )


@app.get("/api/stats", response_model=StatsOut, tags=["meta"])
async def get_stats(session: AsyncSession = Depends(get_session)) -> StatsOut:
    from app.utils import now_cst

    occ, ev, ar, ve, sources = await service.overview_stats(session)
    return StatsOut(
        occurrences=occ, events=ev, artists=ar, venues=ve,
        sources=sources, generated_at=now_cst(),
    )


# --------------------------------------------------------------------------- #
# .ics 订阅
# --------------------------------------------------------------------------- #

def _ics_escape(text: str | None) -> str:
    """RFC5545 文本转义。"""
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """RFC5545 折行：每行不超过 75 字节（按 UTF-8 计）。"""
    out: list[str] = []
    buf = b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(buf) + len(b) > 73:
            out.append(buf.decode("utf-8"))
            buf = b""
        buf += b
    out.append(buf.decode("utf-8"))
    return "\r\n ".join(out)


def build_ics(items, calendar_name: str = "广东地下演出日历") -> str:
    """把场次列表转成 iCalendar。

    UID 用 occurrence.id@gdcalendar（稳定不变）—— 改期时更新 DTSTART 并递增 SEQUENCE，
    日历客户端会**更新**而不是重复添加。
    """
    from app.utils import now_cst

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GD Live Calendar//ZH//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
        f"X-WR-TIMEZONE:{settings.timezone}",
    ]
    stamp = now_cst().strftime("%Y%m%dT%H%M%SZ")

    for it in items:
        if it.start_at is None:
            continue
        start = it.start_at
        end = it.end_at or (start + dt.timedelta(hours=3))
        summary = it.event.title
        if it.venue:
            summary = f"{summary} @ {it.venue.name}"
        elif it.venue_raw:
            summary = f"{summary} @ {it.venue_raw}"

        desc_parts: list[str] = []
        if it.lineup:
            desc_parts.append("阵容：" + " / ".join(x.name for x in it.lineup))
        if it.price.is_free:
            desc_parts.append("票价：无料/免费")
        elif it.price.min is not None:
            desc_parts.append(
                f"票价：¥{it.price.min:.0f}"
                + (f"–{it.price.max:.0f}" if it.price.max and it.price.max != it.price.min else "")
            )
        if it.tickets:
            for tk in it.tickets[:6]:
                if tk.url:
                    desc_parts.append(f"{tk.name} {tk.channel or ''} {tk.url}".strip())
        if it.tokuten_note:
            desc_parts.append("特典：" + it.tokuten_note)
        desc_parts.append("状态：" + STATUS_ZH.get(it.status, it.status))
        if it.sources:
            desc_parts.append("来源：" + " / ".join(s.url for s in it.sources[:3]))
        desc_parts.append("由广东地下演出日历聚合，仅含公开事实信息")

        loc = it.venue.name if it.venue else (it.venue_raw or it.city)
        if it.venue and it.venue.address:
            loc = f"{loc}，{it.venue.address}"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{it.id}@gdcalendar",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={settings.timezone}:{start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID={settings.timezone}:{end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"LOCATION:{_ics_escape(loc)}",
            f"DESCRIPTION:{_ics_escape(chr(10).join(desc_parts))}",
            f"STATUS:{'CANCELLED' if it.status == 'cancelled' else 'CONFIRMED'}",
            "SEQUENCE:0",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


@app.get("/api/ics", tags=["subscribe"])
async def get_ics(
    session: AsyncSession = Depends(get_session),
    city: str | None = None,
    kind: str | None = None,
    is_idol: bool | None = None,
    is_girl_band: bool | None = None,
    is_acg: bool | None = None,
    flag: list[str] | None = Query(None, description="分类标记的「与」组合"),
    venue_id: int | None = None,
    artist_id: int | None = None,
    days: int = Query(120, ge=1, le=730),
):
    """一条 URL 就是一个日历。可粘进 iOS / Google 日历。"""
    today = dt.datetime.now(CST).date()
    query = service.OccurrenceQuery(
        date_from=today, date_to=today + dt.timedelta(days=days),
        city=city, kind=kind, is_idol=is_idol,
        is_girl_band=is_girl_band, is_acg=is_acg, flags_all=flag,
        venue_id=venue_id, artist_id=artist_id, page_size=500,
    )
    items, _ = await service.query_occurrences(session, query)
    parts: list[str] = []
    if city:
        parts.append(city)
    if is_girl_band:
        parts.append("女子乐队")
    if is_acg:
        parts.append("ACG")
    if is_idol:
        parts.append("地偶")
    name = ("".join(parts) + "演出日历") if parts else "广东地下演出日历"
    body = build_ics(items, name)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="gd-calendar.ics"'},
    )


@app.get("/api/export.csv", tags=["subscribe"])
async def export_csv(
    session: AsyncSession = Depends(get_session),
    date_from: dt.date | None = Query(None, alias="from"),
    date_to: dt.date | None = Query(None, alias="to"),
    city: str | None = None,
):
    """CSV 导出（给记者/研究者）。"""
    import csv
    import io

    today = dt.datetime.now(CST).date()
    query = service.OccurrenceQuery(
        date_from=date_from or today,
        date_to=date_to or today + dt.timedelta(days=90),
        city=city, page_size=500,
    )
    items, _ = await service.query_occurrences(session, query)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "date", "open_at", "start_at", "city", "venue", "district",
        "title", "kind", "is_idol", "is_girl_band", "is_acg", "price_min", "price_max", "is_free",
        "status", "lineup", "age_limit", "confidence", "sources",
    ])
    for it in items:
        w.writerow([
            it.start_at.date().isoformat() if it.start_at else "",
            it.open_at.isoformat() if it.open_at else "",
            it.start_at.isoformat() if it.start_at else "",
            it.city,
            it.venue.name if it.venue else (it.venue_raw or ""),
            it.venue.district if it.venue else "",
            it.event.title,
            it.event.kind,
            "1" if it.event.is_idol else "0",
            "1" if it.event.is_girl_band else "0",
            "1" if it.event.is_acg else "0",
            it.price.min if it.price.min is not None else "",
            it.price.max if it.price.max is not None else "",
            "1" if it.price.is_free else "0",
            it.status,
            " / ".join(x.name for x in it.lineup),
            it.age_limit or "",
            it.confidence,
            " ".join(s.url for s in it.sources),
        ])
    return PlainTextResponse(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gd-calendar.csv"'},
    )


@app.get("/api/sources", tags=["meta"])
async def sources_health(session: AsyncSession = Depends(get_session)):
    return {"items": await service.source_health(session)}


@app.get("/api/features", tags=["meta"])
async def get_features() -> dict:
    """功能开关（前端 js/features.js 是兜底，这里可覆盖）。

    为什么让后端也能控制：改一次 `.env` 就能统一生效，
    不用去动前端文件，也不用重新部署静态站。
    """
    return {
        "features": {
            "ics": bool(settings.feature_ics),
            "update": bool(settings.feature_update),
        }
    }


# --------------------------------------------------------------------------- #
# 海报图片代理
# --------------------------------------------------------------------------- #

@app.get("/api/archive/poster", tags=["media"], response_class=Response)
async def get_poster(
    url: str = Query(..., description="原站海报图片 URL"),
    session: AsyncSession = Depends(get_session),
):
    """代理海报图片：补正确 Referer、缓存到本地、同源返回。

    为什么必须存在：豆瓣图片有防盗链（不带 Referer 返回 418），
    前端直接引用原站 URL 一定裂图。
    """
    from app.api import images

    norm = images.normalize_image_url(url)
    if not norm:
        raise HTTPException(status_code=400, detail="无效的图片地址")
    if not images.host_allowed(norm):
        # 不是白名单图床 → 明确拒绝，避免退化成任意 URL 的开放代理
        raise HTTPException(status_code=403, detail="该图片域名不在允许列表内")

    # 先查磁盘缓存（免下载、免网络）
    cached = None
    row = await session.execute(
        select(PosterExtraction.image_sha256).where(
            PosterExtraction.image_url == norm,
            PosterExtraction.model == images.CACHE_MODEL,
            PosterExtraction.cache_path.isnot(None),
        ).limit(1)
    )
    sha = row.scalar_one_or_none()
    if sha:
        cached = images.ImageProxy.find_cached(sha)

    if cached is None:
        proxy = await images.get_proxy()
        cached = await proxy.get(norm, session)
        if cached is not None:
            await session.commit()

    if cached is None:
        raise HTTPException(status_code=404, detail="海报获取失败")

    return FileResponse(
        path=str(cached.path),
        media_type=cached.content_type,
        headers={
            # 图片内容按 sha 命名天然不可变，可长缓存
            "Cache-Control": "public, max-age=604800, immutable",
            "X-Poster-Cache": "hit" if cached.from_cache else "miss",
        },
    )


@app.get("/api/go", tags=["media"])
async def go_to_source(
    url: str = Query(..., description="要跳转的原站地址"),
):
    """跳转到原站（带来源标注），并做协议白名单校验。

    只允许 http/https，避免被当成开放重定向跳板。
    """
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="无效的跳转地址")

    # 原样跳到原站，不追加任何参数（不篡改对方链接）
    return RedirectResponse(url=raw, status_code=302)


# --------------------------------------------------------------------------- #
# 采集任务（页面上的「更新数据」按钮）
# --------------------------------------------------------------------------- #

@app.get("/api/admin/update", tags=["admin"])
async def update_status() -> dict:
    """查询当前/最近一次采集任务状态（页面加载时用它恢复按钮状态）。"""
    from app.jobs.update_job import manager

    cur = manager.current()
    latest = manager.latest()
    return {
        "running": cur is not None,
        "current": cur.snapshot() if cur else None,
        "latest": latest.snapshot() if latest else None,
    }


@app.post("/api/admin/update", tags=["admin"])
async def start_update(
    sources: str = Query(
        "", description="逗号分隔；留空用默认（showstart,douban，即 HTTP 源）"
    ),
    cities: str = Query("", description="逗号分隔；留空用配置里的全部启用城市"),
    use_browser: bool = Query(False, description="是否包含需要渲染的 B站/微博源"),
) -> dict:
    """启动一轮采集（后台跑，立即返回 job_id）。

    为什么不做成同步接口：一轮采集 1–3 分钟，HTTP 会超时，也没法显示进度。
    """
    from app.jobs.update_job import manager

    src = [s.strip() for s in sources.split(",") if s.strip()] or None
    cts = [c.strip() for c in cities.split(",") if c.strip()] or None
    job = await manager.start(sources=src, cities=cts, use_browser=use_browser)
    return {"job": job.snapshot()}


@app.get("/api/admin/update/{job_id}", tags=["admin"])
async def update_job_status(job_id: str) -> dict:
    from app.jobs.update_job import manager

    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在（服务可能已重启）")
    return {"job": job.snapshot()}


# --------------------------------------------------------------------------- #
# 前端静态站点（挂在最后，避免拦截 /api）
# --------------------------------------------------------------------------- #

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:  # pragma: no cover
    @app.get("/", include_in_schema=False)
    async def _no_web():
        return {"detail": "前端目录 app/web 不存在"}


def main() -> None:
    """便捷启动：python -m app.api.main"""
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
