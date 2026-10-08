"""采集层基座：HTTP 抓取、CDP 浏览器抓取、限速与缓存。

设计要点（对应 docs/02-采集方案-全自动.md §2）：
  * 路线 A 纯 HTTP —— 秀动 / 豆瓣 / 场地官网（SSR 站点，无需浏览器）
  * 路线 B CDP 渲染 + XHR 拦截 + 真实点击 —— B站 / 微博 / 需要 JS 的页面
  * 所有抓取结果统一返回 FetchResult，并写入 page_cache / raw_snapshot 供溯源
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.models import FetchResult
from app.utils import content_hash, get_logger, ms_since

log = get_logger(__name__)

# 每个域名的最后请求时间：实现「单域名最小间隔」
_last_hit: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc


async def _throttle(url: str, min_interval: float | None = None) -> None:
    host = _host(url)
    gap = min_interval if min_interval is not None else settings.rate_limit_seconds
    # 豆瓣按其 robots 声明的 Crawl-delay
    if "douban.com" in host:
        gap = max(gap, settings.douban_crawl_delay)
    lock = _locks.setdefault(host, asyncio.Lock())
    async with lock:
        last = _last_hit.get(host, 0.0)
        wait = gap - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_hit[host] = time.monotonic()


class HttpFetcher:
    """纯 HTTP 抓取（httpx）。用于 SSR 站点。"""

    def __init__(self, timeout: float | None = None, user_agent: str | None = None) -> None:
        self.timeout = timeout or settings.http_timeout
        self.user_agent = user_agent or settings.user_agent
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpFetcher:
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(settings.http_retries),
        wait=wait_exponential(multiplier=1, min=1, max=12),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        assert self._client is not None, "HttpFetcher 需在 async with 中使用"
        return await self._client.get(url)

    async def get(
        self,
        url: str,
        *,
        min_interval: float | None = None,
        expect_json: bool = False,
    ) -> FetchResult:
        t0 = time.monotonic()
        await _throttle(url, min_interval)
        try:
            resp = await self._get(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("HTTP 失败 %s: %s", url, exc)
            return FetchResult(url=url, ok=False, error=str(exc), duration_ms=ms_since(t0))

        text = resp.text
        payload: Any = None
        if expect_json:
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                payload = None

        return FetchResult(
            url=url,
            ok=resp.status_code == 200,
            status=resp.status_code,
            mode="http",
            text=text,
            payload=payload,
            content_hash=content_hash(text),
            duration_ms=ms_since(t0),
        )

    async def get_json(self, url: str, **kw: Any) -> FetchResult:
        return await self.get(url, expect_json=True, **kw)


# --------------------------------------------------------------------------- #
# CDP 浏览器（委托给 scripts/cdp_fetch.py 的实现，这里提供异步包装与缓存）
# --------------------------------------------------------------------------- #

class BrowserFetcher:
    """CDP 无头浏览器抓取：渲染 DOM / 拦截 XHR / 真实点击。

    实现复用 scripts/cdp_fetch.py 与 scripts/cdp_xhr.py（仅标准库，无 Playwright 依赖）。
    浏览器进程较重，建议整个采集轮次复用同一个实例。
    """

    def __init__(self, headless: bool | None = None) -> None:
        self.headless = settings.browser_headless if headless is None else headless
        self._browser = None

    async def __aenter__(self) -> BrowserFetcher:
        self._browser = await asyncio.to_thread(self._start)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._browser is not None:
            await asyncio.to_thread(self._browser.close)
            self._browser = None

    def _start(self):
        import sys
        from pathlib import Path

        scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from cdp_fetch import Browser  # type: ignore

        return Browser(headless=self.headless)

    async def render(self, url: str, wait: float | None = None) -> FetchResult:
        """渲染页面并取回渲染后的文本。"""
        t0 = time.monotonic()
        wait = settings.browser_wait_seconds if wait is None else wait
        try:
            data = await asyncio.to_thread(self._browser.render, url, wait)
        except Exception as exc:  # noqa: BLE001
            log.warning("浏览器渲染失败 %s: %s", url, exc)
            return FetchResult(url=url, ok=False, mode="cdp_render",
                               error=str(exc), duration_ms=ms_since(t0))
        text = (data or {}).get("text", "") or ""
        return FetchResult(
            url=(data or {}).get("url", url),
            ok=bool(text),
            mode="cdp_render",
            text=text,
            payload={"title": (data or {}).get("title")},
            content_hash=content_hash(text),
            duration_ms=ms_since(t0),
        )

    async def intercept_xhr(
        self, url: str, match: str, wait: float | None = None
    ) -> FetchResult:
        """打开页面并拦截其自身发出的、URL 含 match 的 XHR 响应。

        这是替代「逆向接口签名」的关键手段：让页面自己请求，我们抄它的 JSON。
        """
        t0 = time.monotonic()
        wait = settings.browser_wait_seconds * 2 if wait is None else wait
        try:
            import sys
            from pathlib import Path

            scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
            if str(scripts) not in sys.path:
                sys.path.insert(0, str(scripts))
            from cdp_xhr import capture_xhr  # type: ignore

            hits = await asyncio.to_thread(capture_xhr, url, match, wait)
        except Exception as exc:  # noqa: BLE001
            log.warning("XHR 拦截失败 %s: %s", url, exc)
            return FetchResult(url=url, ok=False, mode="cdp_xhr",
                               error=str(exc), duration_ms=ms_since(t0))

        payload: Any = []
        for h in hits or []:
            body = h.get("body", "")
            try:
                import json as _json

                payload.append({"url": h.get("url"), "json": _json.loads(body)})
            except Exception:  # noqa: BLE001
                payload.append({"url": h.get("url"), "text": body})

        return FetchResult(
            url=url,
            ok=bool(hits),
            mode="cdp_xhr",
            text=None,
            payload=payload,
            content_hash=content_hash(str(payload)[:20000]),
            duration_ms=ms_since(t0),
        )

    async def screenshot(self, url: str, path: str, wait: float | None = None) -> FetchResult:
        """渲染并截图，供视觉模型兜底分析。"""
        t0 = time.monotonic()
        wait = settings.browser_wait_seconds if wait is None else wait
        try:
            await asyncio.to_thread(self._browser.render, url, wait)
            await asyncio.to_thread(self._browser.screenshot, path)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(url=url, ok=False, mode="screenshot",
                               error=str(exc), duration_ms=ms_since(t0))
        return FetchResult(url=url, ok=True, mode="screenshot",
                           screenshot_path=path, duration_ms=ms_since(t0))


__all__ = ["HttpFetcher", "BrowserFetcher"]
