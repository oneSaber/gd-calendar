"""采集流水线：编排「采集 → 解析 → 归一化 → 入库」。

设计原则：
  * 一个源失败不影响其它源（异常被捕获并记入 FetchLog）。
  * 浏览器源共用同一个 BrowserFetcher 实例（启动成本高）。
  * 采集范围受 settings.future_window_days 约束。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.collectors.base import BrowserFetcher, HttpFetcher
from app.config import settings
from app.db import session_scope
from app.models import ParsedOccurrence
from app.store import IngestStats, Ingestor
from app.utils import get_logger, ms_since

# 来源机器码 → 界面显示名（进度提示要用中文，不能把 showstart 直接甩给用户）
_SOURCE_LABELS: dict[str, str] = {
    "showstart": "秀动",
    "douban": "豆瓣同城",
    "bilibili": "B站",
    "weibo": "微博",
}

log = get_logger(__name__)


@dataclass
class PipelineReport:
    per_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    stats: IngestStats = field(default_factory=IngestStats)
    duration_ms: int = 0

    def render(self) -> str:
        lines = ["=" * 70, "采集报告", "=" * 70]
        for code, n in sorted(self.per_source.items(), key=lambda x: -x[1]):
            lines.append(f"  {code:16} 解析 {n:5} 条")
        for code, err in self.errors.items():
            lines.append(f"  {code:16} ❌ {err[:110]}")
        lines.append("-" * 70)
        lines.append("  " + self.stats.summary())
        lines.append(f"  总耗时 {self.duration_ms / 1000:.1f}s")
        if self.stats.notes:
            lines.append("  备注：")
            for n in self.stats.notes[:10]:
                lines.append(f"    · {n}")
        return "\n".join(lines)


async def _collect_showstart(
    http: HttpFetcher, cities: list[str], *, fetch_posters: bool = False
) -> list[ParsedOccurrence]:
    from app.collectors.showstart import ShowstartCollector

    col = ShowstartCollector(max_pages=2)
    items = await col.collect(http, cities=cities)
    if fetch_posters:
        # 列表页拿不到海报（懒加载），需逐场访问详情页；默认关闭以控制耗时
        await col.enrich_posters(http, items)
    return items


async def _collect_douban(http: HttpFetcher, cities: list[str]) -> list[ParsedOccurrence]:
    from app.collectors.douban import DoubanCollector

    return await DoubanCollector().collect(http, cities=cities)


async def _collect_bilibili(browser: BrowserFetcher | None, cities: list[str]):
    if browser is None:
        return []
    from app.collectors.bilibili import BilibiliCollector

    return await BilibiliCollector().collect(browser, cities=cities)


async def _collect_weibo(browser: BrowserFetcher | None, cities: list[str]):
    if browser is None:
        return []
    from app.collectors.weibo import WeiboCollector

    return await WeiboCollector().collect(browser, cities=cities)


async def run_pipeline(
    *,
    sources: list[str] | None = None,
    cities: list[str] | None = None,
    use_browser: bool = True,
    fetch_posters: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> PipelineReport:
    """跑一轮完整采集。sources 默认全部 HTTP 源。

    `on_progress` 会在每个阶段被调用，参数形如：
        {"stage": "source_done", "source": "showstart", "index": 1, "total": 4,
         "parsed": 246, "message": "秀动：解析 246 条"}
    网页上的「更新数据」按钮靠它显示进度。
    """
    t0 = time.monotonic()
    cities = cities or [c for c in settings.enabled_cities]
    selected = sources or ["showstart", "douban"]
    report = PipelineReport()
    collected: list[ParsedOccurrence] = []

    http_sources = {
        "showstart": _collect_showstart,
        "douban": _collect_douban,
    }
    browser_sources = {
        "bilibili": _collect_bilibili,
        "weibo": _collect_weibo,
    }

    def emit(**kw: Any) -> None:
        if on_progress is None:
            return
        try:
            on_progress(kw)
        except Exception:  # noqa: BLE001
            # 进度回调绝不能影响采集本身
            log.debug("进度回调异常", exc_info=True)

    pending = [c for c in selected if c in http_sources or c in browser_sources]
    emit(stage="start", total=len(pending), index=0,
         message=f"开始采集（{len(cities)} 个城市，{len(pending)} 个来源）")

    async with HttpFetcher() as http:
        for code in selected:
            if code not in http_sources and code not in browser_sources:
                continue
            try:
                # 先报「开始」：单个来源可能要跑 2 分钟，期间必须让界面知道它活着
                emit(stage="source_start", source=code,
                     index=len(report.per_source), total=len(pending),
                     message=f"{_SOURCE_LABELS.get(code, code)}：采集中…")
                if code == "showstart":
                    items = await _collect_showstart(
                        http, cities, fetch_posters=fetch_posters
                    )
                elif code in http_sources:
                    items = await http_sources[code](http, cities)
                else:
                    continue  # 浏览器源稍后统一处理
                report.per_source[code] = len(items)
                collected.extend(items)
                log.info("[%s] 采集 %d 条", code, len(items))
                emit(stage="source_done", source=code, index=len(report.per_source),
                     total=len(pending), parsed=len(items),
                     message=f"{_SOURCE_LABELS.get(code, code)}：解析 {len(items)} 条")
            except Exception as exc:  # noqa: BLE001
                report.errors[code] = str(exc)
                log.exception("[%s] 采集失败", code)
                emit(stage="source_error", source=code, index=len(report.per_source),
                     total=len(pending), message=f"{_SOURCE_LABELS.get(code, code)}：失败")

    needs_browser = [c for c in selected if c in browser_sources]
    if needs_browser:
        if not use_browser:
            log.info("跳过浏览器源（use_browser=False）：%s", needs_browser)
        else:
            try:
                async with BrowserFetcher() as browser:
                    for code in needs_browser:
                        try:
                            emit(stage="source_start", source=code,
                                 index=len(report.per_source), total=len(pending),
                                 message=f"{_SOURCE_LABELS.get(code, code)}：渲染页面中…")
                            items = await browser_sources[code](browser, cities)
                            report.per_source[code] = len(items)
                            collected.extend(items)
                            log.info("[%s] 采集 %d 条", code, len(items))
                            emit(stage="source_done", source=code,
                                 index=len(report.per_source), total=len(pending),
                                 parsed=len(items),
                                 message=f"{_SOURCE_LABELS.get(code, code)}：解析 {len(items)} 条")
                        except Exception as exc:  # noqa: BLE001
                            report.errors[code] = str(exc)
                            log.exception("[%s] 采集失败", code)
                            emit(stage="source_error", source=code,
                                 index=len(report.per_source), total=len(pending),
                                 message=f"{_SOURCE_LABELS.get(code, code)}：失败")
            except Exception as exc:  # noqa: BLE001
                for code in needs_browser:
                    report.errors[code] = f"浏览器启动失败: {exc}"
                log.exception("浏览器启动失败，跳过浏览器源")

    log.info("共采集 %d 条，开始入库", len(collected))
    emit(stage="ingest", index=len(pending), total=len(pending),
         message=f"入库中（收到 {len(collected)} 条）…")
    async with session_scope() as session:
        ing = Ingestor(session)
        report.stats = await ing.ingest(collected)
        for code, n in report.per_source.items():
            await ing.log_fetch(code, ok=code not in report.errors, items=n)
            if code not in report.errors:
                await ing.mark_source_ok(code)
        for code, err in report.errors.items():
            await ing.log_fetch(code, ok=False, error=err)

    report.duration_ms = ms_since(t0)
    log.info("\n%s", report.render())
    from app.utils import now_cst as _now

    st = report.stats
    emit(
        stage="done",
        index=len(pending),
        total=len(pending),
        parsed=len(collected),
        created=getattr(st, "created_occurrence", 0) if st else 0,
        updated=getattr(st, "updated_occurrence", 0) if st else 0,
        message=(
            f"完成：收到 {len(collected)} 条 · 新增 {getattr(st, 'created_occurrence', 0) if st else 0} "
            f"/ 更新 {getattr(st, 'updated_occurrence', 0) if st else 0} · "
            f"耗时 {report.duration_ms / 1000:.0f} 秒"
        ),
        finished_at=_now().isoformat(),
    )
    return report
