"""采集任务管理器与 /api/admin/update 的测试。

关键不变式（都踩过坑）：
  * **同一时刻只允许一个任务**。按钮被点两下、或脚本与页面同时触发时，
    第二次必须**复用**既有任务而不是再起一个 —— 两个采集并发写 SQLite 会互相锁。
    早期实现靠「遍历找 state=='running'」，在「任务已创建但协程还没开始跑」
    的窗口里会漏判，所以现在用显式的 _running_id。
  * 启动接口必须**立刻返回**（不能等采集跑完），否则 HTTP 超时。
"""

from __future__ import annotations

import asyncio

import pytest

from app.jobs.update_job import UpdateJobManager


@pytest.fixture
async def mgr():
    m = UpdateJobManager(keep=5)
    yield m
    await m.shutdown()


async def _fake_pipeline_factory(events, delay=0.02):
    """返回一个假的 run_pipeline，按 events 逐个回调 on_progress。"""

    async def fake(*, on_progress=None, **kwargs):
        for ev in events:
            await asyncio.sleep(delay)
            if on_progress:
                on_progress(ev)

        class St:
            created_occurrence = 3
            updated_occurrence = 7
            created_event = 2
            conflicts = 1
            new_venues = 1
            new_artists = 4

        class Rep:
            per_source = {"showstart": 10}
            errors: dict = {}
            duration_ms = 1234
            stats = St()

            def render(self):
                return ""

        return Rep()

    return fake


class TestJobLifecycle:
    async def test_start_returns_immediately(self, mgr, monkeypatch):
        """启动接口不能等采集跑完。"""
        fake = await _fake_pipeline_factory([{"stage": "done", "index": 1, "total": 1,
                                             "message": "完成"}], delay=0.5)
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)

        loop = asyncio.get_running_loop()
        t0 = loop.time()
        job = await mgr.start(sources=["showstart"])
        elapsed = loop.time() - t0
        assert elapsed < 0.3, "start() 不应阻塞等待采集"
        assert job.running
        assert mgr.current() is job

    async def test_duplicate_start_reuses_job(self, mgr, monkeypatch):
        """⚠️ 核心不变式：并发启动必须复用同一任务。"""
        fake = await _fake_pipeline_factory(
            [{"stage": "source_done", "index": 1, "total": 1, "message": "ok"}],
            delay=0.4,
        )
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)

        j1 = await mgr.start()
        j2 = await mgr.start()
        j3 = await mgr.start()
        assert j1.id == j2.id == j3.id, "重复启动创建了新任务（并发保护失效）"

    async def test_duplicate_start_before_coroutine_runs(self, mgr, monkeypatch):
        """最刁钻的窗口：任务刚创建、协程还没被调度时就再次启动。"""
        fake = await _fake_pipeline_factory([], delay=0.3)
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)

        j1 = await mgr.start()
        # 不 await 任何东西，立刻再启动 —— 此时 _run 协程可能一次都没跑
        j2 = await mgr.start()
        assert j1.id == j2.id

    async def test_progress_updates(self, mgr, monkeypatch):
        fake = await _fake_pipeline_factory([
            {"stage": "source_start", "index": 0, "total": 2, "message": "秀动：采集中…"},
            {"stage": "source_done", "source": "showstart", "index": 1, "total": 2,
             "parsed": 10, "message": "秀动：解析 10 条"},
            {"stage": "done", "index": 2, "total": 2, "parsed": 10, "created": 3,
             "updated": 7, "message": "完成"},
        ], delay=0.05)
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)

        job = await mgr.start()
        for _ in range(60):
            await asyncio.sleep(0.05)
            if not job.running:
                break
        snap = job.snapshot()
        assert snap["state"] == "done"
        assert snap["percent"] == 100
        assert snap["result"]["created"] == 3
        assert snap["result"]["updated"] == 7
        assert any("秀动" in x for x in snap["logs"])

    async def test_elapsed_tracks_stage(self, mgr, monkeypatch):
        """长任务要有「已用秒数」，否则 0% 静止像卡死。"""
        fake = await _fake_pipeline_factory(
            [{"stage": "done", "index": 1, "total": 1, "message": "完成"}], delay=0.3
        )
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)
        job = await mgr.start()
        await asyncio.sleep(0.15)
        assert job.snapshot()["elapsed"] > 0
        for _ in range(40):
            await asyncio.sleep(0.05)
            if not job.running:
                break

    async def test_error_is_captured(self, mgr, monkeypatch):
        async def boom(*, on_progress=None, **kwargs):
            raise RuntimeError("采集炸了")

        monkeypatch.setattr("app.pipeline.run_pipeline", boom)
        job = await mgr.start()
        for _ in range(40):
            await asyncio.sleep(0.05)
            if not job.running:
                break
        assert job.state == "error"
        assert "采集炸了" in job.error

    async def test_can_start_again_after_finish(self, mgr, monkeypatch):
        """任务结束后必须能再次启动（_running_id 要清掉）。"""
        fake = await _fake_pipeline_factory(
            [{"stage": "done", "index": 1, "total": 1, "message": "完成"}], delay=0.02
        )
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)
        j1 = await mgr.start()
        for _ in range(40):
            await asyncio.sleep(0.05)
            if not j1.running:
                break
        assert mgr.current() is None
        j2 = await mgr.start()
        assert j2.id != j1.id

    async def test_get_unknown_job(self, mgr):
        assert mgr.get("nope") is None

    async def test_trim_keeps_running_job(self, mgr, monkeypatch):
        """超出保留上限时，正在跑的任务不能被裁掉。"""
        from app.jobs.update_job import UpdateJob

        fake = await _fake_pipeline_factory(
            [{"stage": "done", "index": 1, "total": 1, "message": "完成"}], delay=0.6
        )
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)
        running = await mgr.start()

        # 直接塞进 10 个已完成的历史任务（超过 keep=5），再触发裁剪
        for i in range(10):
            old = UpdateJob(id=f"old{i}", state="done")
            mgr._jobs[old.id] = old
            mgr._order.append(old.id)
        mgr._trim()

        assert mgr.get(running.id) is not None, "正在跑的任务被裁掉了"
        assert len(mgr._order) <= mgr._keep + 1
        # 清理，避免影响其它用例
        await mgr.shutdown()
        for k in [k for k in list(mgr._jobs) if k.startswith("old")]:
            mgr._jobs.pop(k, None)


class TestAdminApi:
    @pytest.fixture
    async def client(self):
        import httpx

        from app.api.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    async def test_status_when_idle(self, client):
        r = await client.get("/api/admin/update")
        assert r.status_code == 200
        body = r.json()
        assert "running" in body
        assert "current" in body and "latest" in body

    async def test_start_and_poll(self, client, monkeypatch):
        fake = await _fake_pipeline_factory([
            {"stage": "source_start", "index": 0, "total": 1, "message": "秀动：采集中…"},
            {"stage": "done", "index": 1, "total": 1, "message": "完成", "created": 1,
             "updated": 2},
        ], delay=0.05)
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)

        r = await client.post("/api/admin/update", params={"cities": "广州"})
        assert r.status_code == 200
        job = r.json()["job"]
        assert job["job_id"]
        assert job["running"] is True

        # 立刻查状态应能查到同一个任务
        r2 = await client.get(f"/api/admin/update/{job['job_id']}")
        assert r2.status_code == 200
        assert r2.json()["job"]["job_id"] == job["job_id"]

        for _ in range(60):
            await asyncio.sleep(0.05)
            j = (await client.get(f"/api/admin/update/{job['job_id']}")).json()["job"]
            if not j["running"]:
                break
        assert j["state"] == "done"
        assert j["percent"] == 100

    async def test_unknown_job_404(self, client):
        r = await client.get("/api/admin/update/deadbeef")
        assert r.status_code == 404

    async def test_duplicate_post_reuses(self, client, monkeypatch):
        fake = await _fake_pipeline_factory(
            [{"stage": "done", "index": 1, "total": 1, "message": "完成"}], delay=0.5
        )
        monkeypatch.setattr("app.pipeline.run_pipeline", fake)
        a = (await client.post("/api/admin/update")).json()["job"]
        b = (await client.post("/api/admin/update")).json()["job"]
        assert a["job_id"] == b["job_id"]
