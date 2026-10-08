"""采集任务的进程内管理器。

为什么需要它：一轮采集要 1–3 分钟，做成同步 HTTP 请求会超时、也不可能有进度。
所以「更新数据」按钮走 **启动任务 + 轮询状态**：

    POST /api/admin/update         → {job_id}
    GET  /api/admin/update/{id}    → {state, progress, message, result}

设计取舍：
  * 状态放在**进程内存**里，不落库 —— 这是单机本地工具，不是分布式任务队列；
    服务重启后任务状态丢失是可接受的（正在跑的采集本来也会断）。
  * 同一时刻只允许一个任务（SQLite 单写者，并发采集没有收益只有锁冲突）。
    重复启动返回既有任务，而不是报错 —— 按钮被点两下的体验更顺。
  * 服务关闭时会取消运行中的任务，避免 uvicorn 卡在退出。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils import get_logger, now_cst

log = get_logger(__name__)


@dataclass
class UpdateJob:
    id: str
    state: str = "pending"          # pending | running | done | error | cancelled
    stage: str = "pending"
    index: int = 0
    total: int = 0
    message: str = ""
    sources: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    # 当前阶段的开始时间（用于显示「已用 X 秒」）。
    # 为什么需要：单个来源可能要跑 2 分钟，只看 index/total 会一直停在 0%，
    # 用户会以为卡死了。有秒数在跳至少能证明它活着。
    stage_started: float = 0.0
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    # 进度日志（最近 N 条），让按钮下方能滚动显示发生了什么
    logs: list[str] = field(default_factory=list)
    _task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.state in ("pending", "running")

    def elapsed(self) -> float:
        """当前阶段已用秒数。"""
        if not self.stage_started:
            return 0.0
        return max(0.0, time.monotonic() - self.stage_started)

    def percent(self) -> int:
        if self.state == "done":
            return 100
        if not self.total:
            return 0
        # 入库阶段占最后 15%，避免「来源都跑完了却一直显示 80%」的观感
        base = int(self.index / self.total * 85)
        if self.stage == "ingest":
            base = max(base, 85)
        return min(base, 99)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "state": self.state,
            "stage": self.stage,
            "index": self.index,
            "total": self.total,
            "percent": self.percent(),
            "message": self.message,
            "elapsed": round(self.elapsed(), 1),
            "sources": self.sources,
            "cities": self.cities,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "logs": self.logs[-12:],
            "running": self.running,
        }


class UpdateJobManager:
    def __init__(self, *, keep: int = 20) -> None:
        self._jobs: dict[str, UpdateJob] = {}
        self._order: list[str] = []
        self._keep = keep
        self._lock = asyncio.Lock()
        # 显式记录当前任务，而不是靠遍历找 state —— 遍历会在
        # 「任务已创建但状态还没翻成 running」的窗口里漏判，导致重复启动。
        self._running_id: str = ""

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    def get(self, job_id: str) -> UpdateJob | None:
        return self._jobs.get(job_id)

    def current(self) -> UpdateJob | None:
        """当前正在跑的任务（没有则 None）。"""
        if not self._running_id:
            return None
        job = self._jobs.get(self._running_id)
        if job is None or not job.running:
            return None
        return job

    def latest(self) -> UpdateJob | None:
        for jid in reversed(self._order):
            if jid in self._jobs:
                return self._jobs[jid]
        return None

    # ------------------------------------------------------------------ #
    # 启动
    # ------------------------------------------------------------------ #

    async def start(
        self,
        *,
        sources: list[str] | None = None,
        cities: list[str] | None = None,
        use_browser: bool = True,
        fetch_posters: bool = False,
    ) -> UpdateJob:
        """启动一轮采集；已有任务在跑时**直接返回它**（按钮点两下不该报错）。"""
        async with self._lock:
            cur = self.current()
            if cur is not None:
                log.info("已有采集任务在跑（%s），复用", cur.id)
                return cur

            job = UpdateJob(
                id=uuid.uuid4().hex[:12],
                created_at=now_cst().isoformat(),
                sources=list(sources or []),
                cities=list(cities or []),
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            # ⚠️ 必须在创建后立刻占位：否则「创建完但协程还没开始跑」的窗口里，
            # 并发的第二次请求会认为没有任务在跑，于是重复启动。
            self._running_id = job.id
            self._trim()
            job._task = asyncio.create_task(
                self._run(job, sources=sources, cities=cities,
                          use_browser=use_browser, fetch_posters=fetch_posters)
            )
            return job

    def _trim(self) -> None:
        """裁掉最旧的历史任务，但**正在跑的那个绝不能删**。

        ⚠️ 踩过的坑：早期版本遇到 running 任务就 break，结果一旦保留队列里有
        运行中的任务就再也裁不动，_order 会无限增长。正确做法是**跳过**它，
        继续从前面删别的。
        """
        guard = 0
        while len(self._order) > self._keep and guard < len(self._order) * 2 + 8:
            guard += 1
            old = self._order[0]
            j = self._jobs.get(old)
            if j is not None and j.running:
                # 正在跑：跳过（挪到队尾）继续尝试裁别的
                self._order.pop(0)
                self._order.append(old)
                # 剩余项全在运行 → 没有可裁的了
                if all(
                    self._jobs.get(x) is not None and self._jobs[x].running
                    for x in self._order
                ):
                    break
                continue
            self._order.pop(0)
            self._jobs.pop(old, None)

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #

    async def _run(
        self,
        job: UpdateJob,
        *,
        sources: list[str] | None,
        cities: list[str] | None,
        use_browser: bool,
        fetch_posters: bool,
    ) -> None:
        from app.pipeline import _SOURCE_LABELS, run_pipeline

        job.state = "running"
        job.stage = "start"
        job.started_at = now_cst().isoformat()
        # 起点就记时：否则「已启动但还没收到第一个进度事件」的窗口里
        # elapsed 恒为 0，界面上看就是卡住不动。
        job.stage_started = time.monotonic()

        def on_progress(ev: dict[str, Any]) -> None:
            new_stage = str(ev.get("stage") or job.stage)
            if new_stage != job.stage:
                job.stage_started = time.monotonic()
            job.stage = new_stage
            if ev.get("index") is not None:
                job.index = int(ev.get("index") or 0)
            if ev.get("total"):
                job.total = int(ev["total"])
            msg = str(ev.get("message") or "")
            if msg:
                job.message = msg
                job.logs.append(f"{now_cst():%H:%M:%S} {msg}")
            if ev.get("stage") == "done":
                job.result = {
                    "parsed": ev.get("parsed", 0),
                    "created": ev.get("created", 0),
                    "updated": ev.get("updated", 0),
                }

        try:
            report = await run_pipeline(
                sources=sources,
                cities=cities,
                use_browser=use_browser,
                fetch_posters=fetch_posters,
                on_progress=on_progress,
            )
            st = report.stats
            job.result = {
                "per_source": dict(report.per_source),
                "errors": dict(report.errors),
                "duration_ms": report.duration_ms,
                "created": getattr(st, "created_occurrence", 0) if st else 0,
                "updated": getattr(st, "updated_occurrence", 0) if st else 0,
                "created_events": getattr(st, "created_event", 0) if st else 0,
                "conflicts": getattr(st, "conflicts", 0) if st else 0,
                "new_venues": getattr(st, "new_venues", 0) if st else 0,
                "new_artists": getattr(st, "new_artists", 0) if st else 0,
                "source_labels": {
                    k: _SOURCE_LABELS.get(k, k) for k in report.per_source
                },
            }
            job.state = "done"
            job.stage = "done"
            job.finished_at = now_cst().isoformat()
            if self._running_id == job.id:
                self._running_id = ""
            if not job.message:
                job.message = "采集完成"
            job.logs.append(f"{now_cst():%H:%M:%S} ✅ {job.message}")
            log.info("采集任务 %s 完成", job.id)
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.message = "任务已取消（服务正在关闭）"
            job.finished_at = now_cst().isoformat()
            if self._running_id == job.id:
                self._running_id = ""
            log.info("采集任务 %s 被取消", job.id)
            raise
        except Exception as exc:  # noqa: BLE001
            job.state = "error"
            if self._running_id == job.id:
                self._running_id = ""
            job.error = str(exc)
            job.message = f"采集失败：{exc}"
            job.finished_at = now_cst().isoformat()
            job.logs.append(f"{now_cst():%H:%M:%S} ❌ {job.message}")
            log.exception("采集任务 %s 失败", job.id)

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #

    async def shutdown(self) -> None:
        """取消仍在跑的任务，避免 uvicorn 退出时挂住。"""
        jobs = [j for j in self._jobs.values() if j.running]
        for job in jobs:
            if job._task is not None and not job._task.done():
                job._task.cancel()
        for job in jobs:
            if job._task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(job._task), timeout=3)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
        if jobs:
            log.info("已取消 %d 个未完成的采集任务", len(jobs))


# 进程级单例
manager = UpdateJobManager()
