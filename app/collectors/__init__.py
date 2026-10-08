"""采集层。

各采集器统一接口（与 `app/pipeline.py` 的调用方式一致）：

    async def collect(fetcher, cities: list[str] | None = None) -> list[ParsedOccurrence]

  * HTTP 源（`showstart` / `douban`）传 `HttpFetcher`；
  * 浏览器源（`bilibili` / `weibo`）传 `BrowserFetcher`（也可传 None，内部自己起）。

浏览器源把「浏览器里的活儿」和「JSON/文本 → ParsedOccurrence 的转换」分开：
后者是纯函数（`bilibili.parse_listv2_payload` / `bilibili.parse_dynamic_text` /
`weibo.parse_search_payload` / `weibo.discover_accounts`），无浏览器也能单测。
采集器之间共用的收尾逻辑（标题清洗、分类校准、阵容过滤、假场次判定）在
`app/collectors/_postprocess.py`。
"""

from app.collectors.base import BrowserFetcher, HttpFetcher  # noqa: F401

__all__ = ["BrowserFetcher", "HttpFetcher"]
