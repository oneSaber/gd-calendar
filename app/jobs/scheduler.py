"""定时调度（APScheduler）。

频率按 docs/02-采集方案-全自动.md §4 设置：
  HTTP 源 6h · 浏览器源 12h · 状态回填 12h · 过期流转 1d
"""

from __future__ import annotations

import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, update

from app.config import settings
from app.db import session_scope
from app.db.models import Occurrence
from app.utils import get_logger, now_cst

log = get_logger(__name__)
_scheduler: AsyncIOScheduler | None = None


async def job_http_sources() -> None:
    from app.pipeline import run_pipeline

    log.info("⏰ 定时任务：HTTP 源采集")
    await run_pipeline(sources=["showstart", "douban"], use_browser=False)


async def job_browser_sources() -> None:
    from app.pipeline import run_pipeline

    log.info("⏰ 定时任务：浏览器源采集（B站 / 微博）")
    await run_pipeline(sources=["bilibili", "weibo"], use_browser=True)


async def job_expire() -> None:
    """过期流转：start_at 已过的标为 finished。"""
    async with session_scope() as session:
        res = await session.execute(
            update(Occurrence)
            .where(
                Occurrence.start_at < now_cst() - dt.timedelta(hours=12),
                Occurrence.status.in_(["on_sale", "announced", "sold_out", "unknown"]),
            )
            .values(status="finished")
        )
        log.info("⏰ 定时任务：过期流转 %s 条", res.rowcount)


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    sched = AsyncIOScheduler(timezone=settings.timezone)
    sched.add_job(
        job_http_sources,
        IntervalTrigger(hours=settings.fetch_http_interval_hours),
        id="http_sources", max_instances=1, coalesce=True,
    )
    sched.add_job(
        job_browser_sources,
        IntervalTrigger(hours=settings.fetch_browser_interval_hours),
        id="browser_sources", max_instances=1, coalesce=True,
    )
    sched.add_job(
        job_expire,
        CronTrigger(hour=4, minute=30),
        id="expire", max_instances=1, coalesce=True,
    )
    sched.start()
    _scheduler = sched
    log.info(
        "调度器已启动：HTTP 每 %dh / 浏览器每 %dh / 过期流转每天 04:30",
        settings.fetch_http_interval_hours, settings.fetch_browser_interval_hours,
    )
    return sched


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
