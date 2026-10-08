"""海报图片代理与本地缓存。

为什么必须要代理（实测结论）：
  * 豆瓣图片有防盗链：不带 Referer 直接请求返回 **418**，
    带 `Referer: https://www.douban.com/` 才返回 200。
    所以前端直接 `<img src="https://img3.doubanio.com/...">` 一定加载失败。
  * 因此由后端按「域名 → 正确 Referer」去取图，缓存到本地，再以同源地址提供给前端。

另外两个实测坑：
  * B站封面是**协议相对 URL**（`//i2.hdslb.com/...`），必须补 `https:`。
  * 图片体积要限制（秀动/豆瓣的海报动辄几 MB），只缓存列表页缩略图。

合规说明：图片仅用于展示「这场演出」的识别信息，缓存为本地副本仅供本站展示；
详情页同时给出**原站跳转链接**，不修改、不二次分发原图。
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DATA_DIR, settings
from app.db.models import PosterExtraction
from app.utils import get_logger, now_cst, sha256_bytes

log = get_logger(__name__)

POSTER_DIR = DATA_DIR / "posters"
# 缓存用的「模型」标记：这条记录只代表图片缓存，不代表做过视觉抽取
CACHE_MODEL = "cache"

# 允许代理的图片域名（白名单，避免被当成任意 URL 的开放代理）
ALLOWED_HOSTS: tuple[str, ...] = (
    "doubanio.com",            # 豆瓣图片 CDN
    "hdslb.com",               # B 站图片 CDN
    "showstart.com",           # 秀动（详情页海报，后续扩展用）
    "showstartcdn.com",
)

# 域名 → 请求图片时必须带的 Referer（防盗链）
REFERER_MAP: dict[str, str] = {
    "doubanio.com": "https://www.douban.com/",
    "hdslb.com": "https://show.bilibili.com/",
    "showstart.com": "https://www.showstart.com/",
    "showstartcdn.com": "https://www.showstart.com/",
}

MAX_BYTES = 6 * 1024 * 1024          # 单图上限 6MB
MAX_WIDTH_HINT = 1000                # 仅用于说明，不做压缩
FETCH_TIMEOUT = 20.0
# 同一张图短时间内的并发请求合并，避免重复下载
_inflight: dict[str, asyncio.Future] = {}


def normalize_image_url(url: str | None) -> str | None:
    """补全协议相对 URL（B站返回 `//i2.hdslb.com/...`）。"""
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        # 统一升级到 https，避免混合内容被浏览器拦截
        return "https://" + u[len("http://"):]
    if u.startswith("https://"):
        return u
    return None


def host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def referer_for(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for key, ref in REFERER_MAP.items():
        if host == key or host.endswith("." + key):
            return ref
    return None


@dataclass
class CachedImage:
    path: Path
    content_type: str
    byte_size: int
    from_cache: bool = False


def _guess_ext(content_type: str, url: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(ct) if ct else None
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"
    if not ext:
        suffix = Path(urlparse(url).path).suffix.lower()
        ext = suffix if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"
    return ext


class ImageProxy:
    """带缓存的图片获取器。"""

    def __init__(self) -> None:
        POSTER_DIR.mkdir(parents=True, exist_ok=True)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ImageProxy:
        self._client = httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ #
    # 磁盘缓存
    # ------------------------------------------------------------------ #

    @staticmethod
    def find_cached(image_sha: str) -> CachedImage | None:
        """按 sha256 找已缓存的图片文件（扩展名无关）。"""
        for p in POSTER_DIR.glob(f"{image_sha}.*"):
            if p.is_file() and p.stat().st_size > 0:
                ct = mimetypes.guess_type(p.name)[0] or "image/jpeg"
                return CachedImage(path=p, content_type=ct, byte_size=p.stat().st_size,
                                   from_cache=True)
        return None

    # ------------------------------------------------------------------ #
    # 获取
    # ------------------------------------------------------------------ #

    async def fetch_bytes(self, url: str) -> tuple[bytes, str]:
        """抓取图片原始字节，返回 (bytes, content_type)。"""
        assert self._client is not None, "ImageProxy 需在 async with 中使用"
        headers = {}
        ref = referer_for(url)
        if ref:
            headers["Referer"] = ref
        resp = await self._client.get(url, headers=headers)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if not ct.startswith("image/"):
            raise ValueError(f"不是图片内容: {ct!r}")
        data = resp.content
        if not data:
            raise ValueError("空响应")
        if len(data) > MAX_BYTES:
            raise ValueError(f"图片过大: {len(data)} bytes")
        return data, ct

    async def get(
        self,
        url: str,
        session: AsyncSession | None = None,
        *,
        force: bool = False,
    ) -> CachedImage | None:
        """获取图片（优先磁盘缓存）。

        磁盘缓存以内容 sha256 命名 —— 同一张图被多个场次引用时只存一份；
        并发请求同一 URL 会合并为一次下载。
        """
        norm = normalize_image_url(url)
        if not norm or not host_allowed(norm):
            return None

        # 1) 先看图床 URL 是否已有缓存记录（免下载）
        if not force:
            probe = await self._lookup_by_url(session, norm)
            if probe:
                hit = self.find_cached(probe)
                if hit:
                    return hit

        # 2) 并发合并：同一 URL 只下一次
        if norm in _inflight:
            try:
                return await asyncio.shield(_inflight[norm])
            except Exception:  # noqa: BLE001
                return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        _inflight[norm] = fut
        try:
            result = await self._download_and_store(norm, session)
            if not fut.done():
                fut.set_result(result)
            return result
        except Exception as exc:  # noqa: BLE001
            if not fut.done():
                fut.set_exception(exc)
                # 防止 "Future exception was never retrieved"
                fut.exception()
            log.info("海报获取失败 %s: %s", norm[:80], exc)
            return None
        finally:
            _inflight.pop(norm, None)

    async def _lookup_by_url(self, session: AsyncSession | None, url: str) -> str | None:
        if session is None:
            return None
        row = (
            await session.execute(
                select(PosterExtraction.image_sha256)
                .where(PosterExtraction.image_url == url,
                       PosterExtraction.model == CACHE_MODEL,
                       PosterExtraction.cache_path.isnot(None))
                .limit(1)
            )
        ).scalar_one_or_none()
        return row

    async def _download_and_store(
        self, url: str, session: AsyncSession | None
    ) -> CachedImage | None:
        data, ct = await self.fetch_bytes(url)
        sha = sha256_bytes(data)
        ext = _guess_ext(ct, url)
        path = POSTER_DIR / f"{sha}{ext}"

        if not path.exists():
            # 先写临时文件再改名，避免并发下读到半个文件
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)

        if session is not None:
            exists = (
                await session.execute(
                    select(PosterExtraction.id).where(
                        PosterExtraction.image_sha256 == sha,
                        PosterExtraction.model == CACHE_MODEL,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                session.add(
                    PosterExtraction(
                        image_url=url,
                        image_sha256=sha,
                        model=CACHE_MODEL,
                        raw_json={},
                        cache_path=path.name,
                        content_type=ct,
                        byte_size=len(data),
                        fetched_at=now_cst(),
                    )
                )

        return CachedImage(path=path, content_type=ct or "image/jpeg",
                           byte_size=len(data))


_proxy: ImageProxy | None = None


async def get_proxy() -> ImageProxy:
    """进程级共享的代理实例（复用连接池）。"""
    global _proxy
    if _proxy is None:
        _proxy = ImageProxy()
        await _proxy.__aenter__()
    return _proxy


async def close_proxy() -> None:
    global _proxy
    if _proxy is not None:
        await _proxy.__aexit__(None, None, None)
        _proxy = None


def prefetch_occurrence_posters(limit: int = 40) -> None:
    """预留：批量预热海报（可选，未在主流程调用）。"""


__all__ = [
    "ALLOWED_HOSTS", "CACHE_MODEL", "CachedImage", "ImageProxy", "POSTER_DIR",
    "close_proxy", "get_proxy", "host_allowed", "normalize_image_url", "referer_for",
]
