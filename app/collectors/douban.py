"""豆瓣同城采集器（补充源 + 地理坐标来源）。

实测结论（docs/02-采集方案-全自动.md §3.5）：
  * 列表页 SSR 且带 schema.org 微数据：`itemprop="startDate" / "endDate" / "location" / "geo"`。
  * **官方 RSS 可用**：`/location/{city}/events/feed/weekly` 返回 50 条，description 内含
    「开始时间 / 结束时间 / 地点」。
  * ⚠️ 有资料称存在 `feed/future-1001`（小型现场）子分类，**实测返回 404**，不要用。
  * robots 未禁 `/location/*/events/*` 与 `/event/{id}/`，但声明 `Crawl-delay: 5`（由 HttpFetcher 强制）。
  * 噪声大：实测混有脱口秀、音乐剧、粤剧、长隆野生动物世界 → 必须靠 classify 过滤。
"""

from __future__ import annotations

import html as html_mod
import re

import feedparser
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.collectors.base import HttpFetcher
from app.models import ParsedOccurrence
from app.parsers import text_zh as tz
from app.utils import get_logger, parse_ts

log = get_logger(__name__)

SOURCE_CODE = "douban"

# 城市 slug（豆瓣用拼音）
CITY_SLUGS: dict[str, str] = {
    "广州": "guangzhou",
    "深圳": "shenzhen",
    "佛山": "foshan",
    "东莞": "dongguan",
    "珠海": "zhuhai",
    "中山": "zhongshan",
    "惠州": "huizhou",
    "汕头": "shantou",
}

# 只采音乐类
FEED_PATH = "/location/{slug}/events/feed/weekly"
LIST_PATH = "/location/{slug}/events/future-music"

_LOC_RE = re.compile(r"^([\u4e00-\u9fa5]{2,4})\s+(.*)$")
_START_RE = re.compile(r"开始时间[：:]\s*([^<\n]+)")
_END_RE = re.compile(r"结束时间[：:]\s*([^<\n]+)")
_VENUE_RE = re.compile(r"地点[：:]\s*([^<\n]+)")
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"')


def parse_rss(xml_text: str, default_city: str | None = None) -> list[ParsedOccurrence]:
    """解析豆瓣城市 RSS。纯函数，便于离线单测。"""
    feed = feedparser.parse(xml_text)
    out: list[ParsedOccurrence] = []

    for entry in feed.entries:
        title = html_mod.unescape((entry.get("title") or "").strip())
        link = (entry.get("link") or "").strip()
        desc = entry.get("description") or entry.get("summary") or ""
        ext = link.rstrip("/").rsplit("/", 1)[-1] if link else tz.normalize_name(title)

        start_at = end_at = None
        m = _START_RE.search(desc)
        if m:
            start_at = parse_ts(m.group(1).strip())
        m = _END_RE.search(desc)
        if m:
            end_at = parse_ts(m.group(1).strip())

        city, venue = default_city, None
        m = _VENUE_RE.search(desc)
        if m:
            loc = html_mod.unescape(m.group(1).strip())
            city = tz.parse_city(loc, default_city)
            if city and loc.startswith(city):
                venue = tz.clean_venue(re.sub(rf"^{city}市?\s*", "", loc))
            else:
                venue = tz.clean_venue(loc)

        poster = None
        mi = _IMG_RE.search(desc)
        if mi:
            poster = mi.group(1)

        cls = tz.classify(title)
        series_key, series_vol = tz.parse_series(title)

        # 豆瓣 RSS 没有票价；时间精度按是否含时分判断
        precision = "exact" if (start_at and (start_at.hour or start_at.minute)) else "date_only"

        out.append(
            ParsedOccurrence(
                source_code=SOURCE_CODE,
                source_url=link,
                external_id=ext,
                title_raw=title,
                title_display=tz.display_title(title),
                series_key=series_key,
                series_vol=series_vol,
                kind=cls.kind,
is_idol=cls.is_idol,
                is_girl_band=cls.is_girl_band,
                is_acg=cls.is_acg,
                tags=cls.tags,
                city=city,
                venue_raw=venue,
                start_at=start_at,
                end_at=end_at,
                date_precision=precision,
                status="on_sale",
                poster_url=poster,
                description=tz.display_title(re.sub(r"<[^>]+>", " ", desc))[:300],
                confidence=0.8,
                raw={"rss_description": desc[:500]},
            )
        )
    return out


def parse_list_html(html_text: str, default_city: str | None = None) -> list[ParsedOccurrence]:
    """解析豆瓣同城列表页（schema.org 微数据）。用于 RSS 不可用时的兜底。"""
    tree = HTMLParser(html_text)
    out: list[ParsedOccurrence] = []

    for li in tree.css("li.list-entry"):
        a = li.css_first("div.title a")
        if a is None:
            continue
        link = a.attributes.get("href") or ""
        title_node = li.css_first('span[itemprop="summary"]')
        title = (title_node.text(strip=True) if title_node else a.text(strip=True)) or ""
        ext = link.rstrip("/").rsplit("/", 1)[-1] if link else tz.normalize_name(title)

        start_at = end_at = None
        sn = li.css_first('time[itemprop="startDate"]')
        if sn is not None:
            start_at = parse_ts(sn.attributes.get("datetime") or "")
        en = li.css_first('time[itemprop="endDate"]')
        if en is not None:
            end_at = parse_ts(en.attributes.get("datetime") or "")

        loc_meta = li.css_first('meta[itemprop="location"]')
        loc = loc_meta.attributes.get("content") if loc_meta is not None else None
        city, venue = default_city, None
        if loc:
            city = tz.parse_city(loc, default_city)
            rest = re.sub(rf"^{city}市?\s*", "", loc) if city and loc.startswith(city) else loc
            # 豆瓣地点形如「荔湾区 平安大戏院 华林街道十甫路125号」
            parts = rest.split()
            venue = tz.clean_venue(" ".join(parts[:2])) if parts else None

        img = li.css_first("img")
        poster = None
        if img is not None:
            poster = img.attributes.get("data-lazy") or img.attributes.get("src")

        cls = tz.classify(title)
        series_key, series_vol = tz.parse_series(title)
        precision = "exact" if (start_at and (start_at.hour or start_at.minute)) else "date_only"

        out.append(
            ParsedOccurrence(
                source_code=SOURCE_CODE,
                source_url=link,
                external_id=ext,
                title_raw=title,
                title_display=tz.display_title(title),
                series_key=series_key,
                series_vol=series_vol,
                kind=cls.kind,
is_idol=cls.is_idol,
                is_girl_band=cls.is_girl_band,
                is_acg=cls.is_acg,
                tags=cls.tags,
                city=city,
                venue_raw=venue,
                start_at=start_at,
                end_at=end_at,
                date_precision=precision,
                status="on_sale",
                poster_url=poster,
                confidence=0.8,
            )
        )
    return out


class DoubanCollector:
    source_code = SOURCE_CODE

    def rss_url(self, city: str) -> str:
        slug = CITY_SLUGS.get(city)
        if not slug:
            raise ValueError(f"未知城市（无豆瓣 slug）: {city}")
        return f"https://www.douban.com{FEED_PATH.format(slug=slug)}"

    def list_url(self, city: str) -> str:
        slug = CITY_SLUGS.get(city)
        if not slug:
            raise ValueError(f"未知城市（无豆瓣 slug）: {city}")
        return f"https://www.douban.com{LIST_PATH.format(slug=slug)}"

    async def collect_city(self, http: HttpFetcher, city: str) -> list[ParsedOccurrence]:
        """优先 RSS（结构化最省力），失败再退回列表页。"""
        res = await http.get(self.rss_url(city))
        if res.ok and res.text and "<rss" in res.text[:2000]:
            items = parse_rss(res.text, default_city=city)
            log.info("豆瓣 RSS %s 解析 %d 条", city, len(items))
            if items:
                return items
        log.info("豆瓣 %s RSS 不可用，退回列表页", city)
        res = await http.get(self.list_url(city))
        if not res.ok or not res.text:
            log.warning("豆瓣 %s 列表页失败: %s", city, res.error or res.status)
            return []
        items = parse_list_html(res.text, default_city=city)
        log.info("豆瓣列表页 %s 解析 %d 条", city, len(items))
        return items

    async def collect(
        self, http: HttpFetcher, cities: list[str] | None = None
    ) -> list[ParsedOccurrence]:
        out: list[ParsedOccurrence] = []
        seen: set[str] = set()
        for city in cities or ["广州", "深圳"]:
            if city not in CITY_SLUGS:
                continue
            for occ in await self.collect_city(http, city):
                key = f"{occ.city}|{occ.external_id}"
                if key in seen:
                    continue
                seen.add(key)
                out.append(occ)
        log.info("豆瓣合计 %d 条（已去重）", len(out))
        return out
