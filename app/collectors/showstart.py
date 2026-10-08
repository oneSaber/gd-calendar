"""秀动 showstart 采集器（乐队主源）。

实测结论（docs/02-采集方案-全自动.md §3.1）：
  * PC 列表页是 Nuxt SSR，演出条目**直接内嵌在 HTML 里**，无需 JS / 登录 / 签名。
  * 条目结构：
      <a href="/event/291573" class="show-item item">
        <div class="title">…</div>
        <div class="artist">艺人：守麦乐队</div>
        <div class="price">价格：<span>¥49起</span></div>
        <div class="time">时间：2026/03/06 20:00</div>
        <div class="addr"><i class="el-icon-location"></i>[广州]游声场FreeField</div>
      </a>
  * **地偶靠关键词搜不到**（实测 keyword=地偶 广州 0 条），必须按 `siteId` 走场地维度。
  * SSR 里 `totalCount:N` 可用于监控覆盖率。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from app.collectors.base import HttpFetcher
from app.models import ArtistIn, ParsedOccurrence, TicketIn
from app.parsers import text_zh as tz
from app.utils import get_logger

log = get_logger(__name__)

BASE = "https://www.showstart.com"
SOURCE_CODE = "showstart"

# ---- 地区码（实测自 SSR 内嵌城市表；请求参数要用这些值）----
# 注意：SSR 内广州城市对象自身写作 cityCode:"S"，但请求必须用 "20"
CITY_CODES: dict[str, str] = {
    "广州": "20",
    "深圳": "755",
    "珠海": "756",
    "佛山": "757",
    "东莞": "769",
    "中山": "760",
    "惠州": "752",
    "汕头": "754",
}

# ---- 广东主要场地 siteId（实测，用于场地维度抓取——地偶的主要发现路径）----
VENUE_SITE_IDS: dict[str, tuple[str, str]] = {
    # siteId: (场地名, 城市)
    "7616006": ("MAOLivehouse广州（永庆坊店）", "广州"),
    "11339611": ("MAOLivehouse广州（永庆坊店CLUB厅）", "广州"),
    "19696071": ("MAO Livehouse广州中大店（一号馆）", "广州"),
    "19869102": ("MAO Livehouse广州中大店（二号馆）", "广州"),
    "15155307": ("MAO Livehouse广州（太古仓店）", "广州"),
    "1511060": ("疆进酒·OMNI SPACE 广州 1号馆", "广州"),
    "2918921": ("疆进酒·OMNI SPACE 广州 2号馆", "广州"),
    "3515": ("SDlivehouse", "广州"),
    "77038": ("SDlivehouse（北场馆）", "广州"),
    "3516": ("191space", "广州"),
    "3475533": ("太空间 Livehouse", "广州"),
    "26842328": ("声音共和 Livehouse（广州塔店）", "广州"),
    "5779821": ("声音共和 Livehouse", "广州"),
    "23969122": ("CH8蛙厂演艺中心（大学城店）", "广州"),
    "16229108": ("池沼 CHIZHAO LIVEHOUSE（广州科学城店）", "广州"),
    "963035": ("游声场 FreeField", "广州"),
    "12412487": ("光芒 enlightening", "广州"),
    "6634401": ("广州音乐唐人馆 livehouse", "广州"),
    "3519": ("深圳B10现场", "深圳"),
    "1318325": ("HOU LIVE 下沙店", "深圳"),
    "3340719": ("HOU LIVE · Z空间（欢乐港湾店）", "深圳"),
    "1002332": ("CH8-LIVEHOUSE（深圳粤海店）", "深圳"),
    "15141057": ("MAO Livehouse深圳（海上世界）", "深圳"),
    "19923528": ("MAO Livehouse深圳（雪花店）", "深圳"),
    "107725": ("SoFun Live 深圳", "深圳"),
    "25873690": ("ON AIR 音乐现场", "深圳"),
}

_TOTAL_RE = re.compile(r"totalCount:(\d+)")
_PRICE_INNER_RE = re.compile(r"<span[^>]*>(.*?)</span>", re.S)
_ADDR_CITY_RE = re.compile(r"^\[([^\]]{2,6})\]\s*(.*)$", re.S)
_ARTIST_PREFIX_RE = re.compile(r"^艺人[：:]\s*")

# 详情页海报抽取规则。
#
# ⚠️ 实测：这些规则**对目前的秀动无效** —— 详情页是 SPA 壳（~19KB，无 og:image），
# 列表页海报是懒加载。规则保留是为了将来站点改回 SSR 时能直接生效，
# 也是 `enrich_posters` 用来判断「站点是否已支持静态抓图」的探针。
_POSTER_PATTERNS = (
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
    re.compile(r'"poster"\s*:\s*"([^"]+)"'),
    re.compile(r'"cover(?:Url|URL|Image)?"\s*:\s*"([^"]+)"'),
)


def extract_poster_url(html: str) -> str | None:
    """从秀动详情页 HTML 里抽海报地址。纯函数，便于离线测试。"""
    for pat in _POSTER_PATTERNS:
        m = pat.search(html or "")
        if not m:
            continue
        raw = m.group(1).strip()
        if not raw:
            continue
        if raw.startswith("//"):
            raw = "https:" + raw
        elif raw.startswith("http://"):
            raw = "https://" + raw[len("http://"):]
        elif not raw.startswith("https://"):
            continue
        # 过滤掉站点 logo / 默认图
        low = raw.lower()
        if any(x in low for x in ("logo", "default", "placeholder", "avatar")):
            continue
        return raw
    return None


@dataclass
class ShowItem:
    """列表页里的一条原始条目。"""

    external_id: str
    url: str
    title: str
    artist_raw: str
    price_text: str
    time_text: str
    addr_text: str


def _txt(node) -> str:
    return node.text(strip=True) if node is not None else ""


def parse_list_html(html: str) -> tuple[list[ShowItem], int | None]:
    """解析秀动列表页 HTML，返回 (条目列表, totalCount)。

    用 selectolax 而不是正则：结构清晰、容错好，且实测 class 名稳定。
    """
    tree = HTMLParser(html)
    items: list[ShowItem] = []

    for a in tree.css("a.show-item.item"):
        href = a.attributes.get("href") or ""
        if not href.startswith("/event/"):
            continue
        ext = href.rsplit("/", 1)[-1]
        items.append(
            ShowItem(
                external_id=ext,
                url=BASE + href,
                title=_txt(a.css_first("div.title")),
                artist_raw=_txt(a.css_first("div.artist")),
                price_text=_PRICE_INNER_RE.search(
                    str(a.css_first("div.price") or "")
                ).group(1)
                if a.css_first("div.price") and _PRICE_INNER_RE.search(str(a.css_first("div.price")))
                else _txt(a.css_first("div.price")),
                time_text=_txt(a.css_first("div.time")),
                addr_text=_txt(a.css_first("div.addr")),
            )
        )

    m = _TOTAL_RE.search(html)
    total = int(m.group(1)) if m else None
    return items, total


def item_to_occurrence(item: ShowItem, default_city: str | None = None) -> ParsedOccurrence:
    """把列表条目转成 ParsedOccurrence。

    列表页字段有限（无阵容明细、无票种细分），但标题+时间+场地+起售价已足够进日历；
    详情页信息留给后续「状态回填」任务补齐。
    """
    city, venue_raw = default_city, None
    addr = tz.clean_text(item.addr_text)
    m = _ADDR_CITY_RE.match(addr)
    if m:
        city, venue_raw = m.group(1), m.group(2).strip()
    else:
        venue_raw = addr or None
        city = tz.parse_city(addr, default_city)

    venue_raw = tz.clean_venue(venue_raw) or venue_raw

    # 时间：秀动写「时间：2026/03/06 20:00」
    start_at, open_at, precision, _ = tz.parse_datetime(tz.clean_text(item.time_text))
    price_min, price_max, is_free, tickets = tz.parse_prices(item.price_text or "")

    title = tz.display_title(item.title)
    artist_raw = _ARTIST_PREFIX_RE.sub("", tz.clean_text(item.artist_raw)).strip()
    artists: list[ArtistIn] = []
    if artist_raw:
        norm = tz.normalize_name(artist_raw)
        if norm:
            artists.append(
                ArtistIn(name_raw=artist_raw, name_norm=norm, role="performer",
                         billing_order=1, source=SOURCE_CODE, confidence=0.9)
            )

    cls = tz.classify(title, artist_raw)
    series_key, series_vol = tz.parse_series(title)

    return ParsedOccurrence(
        source_code=SOURCE_CODE,
        source_url=item.url,
        external_id=item.external_id,
        title_raw=item.title or title,
        title_display=title,
        series_key=series_key,
        series_vol=series_vol,
        kind=cls.kind,
is_idol=cls.is_idol,
        is_girl_band=cls.is_girl_band,
        is_acg=cls.is_acg,
        tags=cls.tags,
        city=city,
        venue_raw=venue_raw,
        start_at=start_at,
        open_at=open_at,
        date_precision=precision,
        status="on_sale",
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        tickets=tickets,
        artists=artists,
        confidence=0.9,
        raw={
            "price_text": item.price_text,
            "time_text": item.time_text,
            "addr_text": item.addr_text,
            "artist_text": item.artist_raw,
        },
    )


class ShowstartCollector:
    """秀动采集器：城市维度 + 场地维度。"""

    source_code = SOURCE_CODE

    def __init__(self, max_pages: int = 3) -> None:
        self.max_pages = max_pages

    def city_url(self, city: str, page: int = 1) -> str:
        code = CITY_CODES.get(city)
        if not code:
            raise ValueError(f"未知城市（无地区码）: {city}")
        return f"{BASE}/event/list?cityCode={code}&pageNo={page}"

    def venue_url(self, site_id: str) -> str:
        # cityCode=0 表示不限城市（实测可用）
        return f"{BASE}/event/list?siteId={site_id}&cityCode=0"

    async def collect_city(self, http: HttpFetcher, city: str) -> list[ParsedOccurrence]:
        out: list[ParsedOccurrence] = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            url = self.city_url(city, page)
            res = await http.get(url)
            if not res.ok or not res.text:
                log.warning("秀动 %s 第 %d 页失败: %s", city, page, res.error or res.status)
                break
            items, total = parse_list_html(res.text)
            if page == 1:
                log.info("秀动 %s totalCount=%s", city, total)
            if not items:
                break
            for it in items:
                if it.external_id in seen:
                    continue
                seen.add(it.external_id)
                out.append(item_to_occurrence(it, default_city=city))
        log.info("秀动城市维度 %s 收集 %d 条", city, len(out))
        return out

    async def collect_venue(self, http: HttpFetcher, site_id: str) -> list[ParsedOccurrence]:
        """按场地抓取 —— **地偶场次的主要发现路径**。"""
        meta = VENUE_SITE_IDS.get(site_id)
        default_city = meta[1] if meta else None
        url = self.venue_url(site_id)
        res = await http.get(url)
        if not res.ok or not res.text:
            log.warning("秀动场地 %s 失败: %s", site_id, res.error or res.status)
            return []
        items, _ = parse_list_html(res.text)
        out = [item_to_occurrence(it, default_city=default_city) for it in items]
        log.info("秀动场地维度 siteId=%s(%s) 收集 %d 条", site_id, meta[0] if meta else "?", len(out))
        return out

    async def enrich_posters(
        self, http: HttpFetcher, occurrences: list[ParsedOccurrence], limit: int | None = None
    ) -> int:
        """尝试补秀动海报 —— **实测无效，默认不要开启**。

        实测结论（三处都试过）：
          1. 列表页条目的海报是懒加载的，SSR HTML 里只有 `<div class="image-slot">` 占位；
          2. 详情页 `/event/{id}` 也是 SPA 壳（实测仅 ~19KB，**没有 og:image**）；
          3. 前端 XHR `/api/web/activity/list` 直接调用被 WAF 拒
             （GET → `get_not_support`「方法受限」；POST → `参数不全`），
             逆向它的参数/签名不在本项目的合规范围内。
        → 秀动海报只能靠**浏览器渲染详情页**获取（成本高，尚未实现）。

        保留本方法是为了：将来接入渲染方案时可直接复用；
        现在保留它也能明确回答「秀动为什么没有海报」，而不是让人误以为漏做。
        """
        targets = [o for o in occurrences if not o.poster_url]
        if limit is not None:
            targets = targets[:limit]
        if not targets:
            return 0

        probes = targets[:3]
        hit = 0
        for occ in probes:
            try:
                res = await http.get(occ.source_url)
                if res.ok and res.text:
                    url = extract_poster_url(res.text)
                    if url:
                        occ.poster_url = url
                        hit += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("秀动海报探测失败 %s: %s", occ.source_url, exc)

        if hit == 0:
            log.info(
                "秀动海报：详情页为 SPA 壳，静态抓取拿不到（实测）。"
                "如需秀动海报，请走浏览器渲染方案（未实现）。已跳过其余 %d 场。",
                len(targets),
            )
            return 0

        # 万一站点改成 SSR 了，就正常跑完
        for occ in targets[len(probes):]:
            try:
                res = await http.get(occ.source_url)
                if res.ok and res.text:
                    url = extract_poster_url(res.text)
                    if url:
                        occ.poster_url = url
                        hit += 1
            except Exception as exc:  # noqa: BLE001
                log.debug("秀动海报抓取失败 %s: %s", occ.source_url, exc)
        log.info("秀动海报补全：%d/%d", hit, len(targets))
        return hit

    async def collect(
        self,
        http: HttpFetcher,
        cities: list[str] | None = None,
        venue_site_ids: list[str] | None = None,
    ) -> list[ParsedOccurrence]:
        """完整采集：城市维度 + 重点场地维度（去重）。"""
        out: list[ParsedOccurrence] = []
        seen: set[str] = set()

        for city in cities or ["广州", "深圳"]:
            if city not in CITY_CODES:
                log.info("跳过无地区码的城市: %s", city)
                continue
            for occ in await self.collect_city(http, city):
                if occ.external_id in seen:
                    continue
                seen.add(occ.external_id)
                out.append(occ)

        # 场地维度：能捞到关键词搜索漏掉的地偶场
        ids = venue_site_ids
        if ids is None:
            ids = [sid for sid, (_, c) in VENUE_SITE_IDS.items() if c in (cities or ["广州", "深圳"])]
        for sid in ids:
            for occ in await self.collect_venue(http, sid):
                if occ.external_id in seen:
                    continue
                seen.add(occ.external_id)
                out.append(occ)

        log.info("秀动合计 %d 条（已去重）", len(out))
        return out
