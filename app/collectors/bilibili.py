"""B 站采集器：UP 主动态（场地自营宣发）+ 会员购（Only / 同人 / ACG）。

对应 docs/02-采集方案-全自动.md §3.3。**两条通道都不碰 B 站签名（wbi）**，
只用「CDP 渲染页面 / 真实点击 + 拦截页面自己发出的 XHR」：

  (a) 动态通道  space.bilibili.com/{mid}/dynamic
      实测 B10Live（mid=510676514）渲染出 7083 字符，含真实演出预告。
      ⚠️ 实测坑：`/{mid}/upload/video`（投稿页）渲染后为空 → **只采动态，不采投稿**。

  (b) 会员购通道  show.bilibili.com/platform/home.html?msource=pc_web
      页面默认城市不是广州，**必须真实点击「广州」**（`Input.dispatchMouseEvent`，
      `el.click()` 无效）才会发出 listV2，随后拦截 `/api/ticket/project`。
      ⚠️ 实测坑：`price_low / price_high` 单位是**分**（7800 → ¥78.00），必须 /100。

设计上把「浏览器里的活儿」和「JSON/文本 → ParsedOccurrence 的转换」彻底分开：
后者是纯函数（`parse_listv2_payload` / `parse_dynamic_text`），**无浏览器也能单测**。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from app.collectors._postprocess import (
    clean_title,
    finalize,
    fix_relative_date,
    looks_like_event,
    looks_like_venue_piece,
    sanitize_artists,
)
from app.collectors.base import BrowserFetcher, HttpFetcher
from app.models import ArtistIn, ParsedOccurrence, TicketIn
from app.parsers import text_zh
from app.utils import content_hash, get_logger, now_cst, parse_ts

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

SOURCE_CODE = "bilibili"
DYNAMIC_URL = "https://space.bilibili.com/{mid}/dynamic"
SHOW_HOME = "https://show.bilibili.com/platform/home.html?msource=pc_web"
SHOW_XHR_MATCH = "/api/ticket/project"
DYNAMIC_TEMPLATE = DYNAMIC_URL

# UP 主种子（mid）。实测 B10Live=510676514 动态页可渲染（6548 字符）。
# ⚠️ 默认**空**：实测这页的动态是「视频推广文案」，正文里没有活动日期
#    （只有发布时间与视频时长），采出来的场次日期不可信。
#    要启用动态通道，请显式传 `mids=`（或用 `--source bilibili` 之外的自定义入口），
#    并在补上「视频详情页」通道后再默认打开。
DYNAMIC_MIDS: list[int] = []
KNOWN_DYNAMIC_MIDS: dict[str, int] = {
    "B10Live": 510676514,
}

# 会员购城市 area 码（实测：点「广州」后发出的是 area=440100）
# 用行政区划码，不是秀动的 cityCode；无码的城市直接跳过（宁缺勿错）。
BILI_AREA_CODES: dict[str, str] = {
    "广州": "440100",
    "深圳": "440300",
    "佛山": "440600",
    "东莞": "441900",
    "珠海": "440400",
}

# 会员购价格单位是「分」—— 实测：7800 → ¥78.00
FEN_PER_YUAN = 100.0

# 移动端 UA：微博搜索页用（B 站仍用桌面 UA，动态页桌面版信息更全）
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 动态条目分隔标记：`\d+小时前` / `\d+分钟前` / `\d+天前` / `昨天` / `08-26`
# ⚠️ 不把 `\d{4}-\d{2}-\d{2}` 当分隔（日期在正文里更常见），只认 MM-DD。
_TIME_MARKER_RE = re.compile(
    r"(?<![\d\-/.:])"
    r"(?:\d+\s*(?:秒|分钟|小时|天)前"
    r"|昨天|前天"
    r"|\d{2}-\d{2})"
    r"(?![\d\-])"
)

# 视频时长 `01:01:52` / 进度条 `12:34 / 56:78`：既不是开演时间也不该留在标题里
_MEDIA_TIME_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}")

# 动态标记后面常见的动作词（属于「动态头」，不属于正文）
_DYNAMIC_VERB_RE = re.compile(
    r"^(?:\s*[·・|]\s*)?(?:投稿了视频|投稿了文章|发布了动态|发布了专栏|转发了动态|"
    r"转发了专栏|发布了视频|发表了动态|置顶)?\s*[:：]?\s*"
)

# 明显是动态头/互动数据而非正文的行
_NOISE_LINE_RE = re.compile(
    r"^\s*(?:\d+\s*(?:秒|分钟|小时|天)前|昨天|前天|\d{2}-\d{2})\b"
    r"|投稿了视频|发布了动态|转发了动态|发布了专栏"
    r"|^\s*(?:\d+(?:\.\d+)?[万亿]?)\s*(?:播放|点赞|投币|收藏|转发|评论|弹幕)"
    r"|^\s*(?:回复|点赞|转发|分享|举报|收起|展开|更多)\s*$"
)

# 标题候选行里必须出现的东西（否则多半是互动数据或纯装饰）
_TITLE_NEEDLE_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z]")
_TITLE_DATE_TIME_RE = re.compile(
    r"(?:20\d{2}\s*[年\-/.]\s*\d{1,2}|\d{1,2}\s*[月\-/.]\s*\d{1,2})"
    r"|\d{1,2}\s*[:：]\s*\d{2}"
)

# 正文里的链接（B 站动态常见 https://b23.tv/xxxx）
_URL_RE = re.compile(r"https?://[^\s，,。；;）)】\]]+")

# 阵容抽取用的辅助模式（与 text_zh 内部词表保持一致，用于判断「这次抽取可不可信」）
_AT_TOKEN_RE = re.compile(r"@[A-Za-z0-9_\u4e00-\u9fa5\-]{2,40}")
_LINEUP_LABELS = (
    "参演团队", "参演乐队", "参演阵容", "参演", "出演", "阵容", "演出乐队",
    "LINE UP", "LINEUP", "lineup", "嘉宾", "GUEST", "乐队", "共演", "拼盘",
)

# 阵容名里不该出现的字段词（说明抓到的是「时间：…」「票价：…」这类字段）
_FIELD_WORDS = (
    "时间", "日期", "地点", "场地", "地址", "入场", "开演", "开场", "票价",
    "门票", "价格", "参演", "阵容", "购票", "扫码", "主办", "公众号", "直播",
)
# 不是团体/艺人名的描述性词（实测被误抓过的）
_NON_ARTIST_WORDS = (
    "专场", "巡演", "拼盘", "演出", "公演", "领衔", "节目前瞻", "前瞻", "预告",
    "感谢", "大家", "今晚", "现场", "门票", "售票", "乐队们",
)

# 会员购条目里常见的通用标签，过滤掉（保留真正的品类标签）
_GENERIC_TAGS = ("演出", "门票", "票务", "活动")


# --------------------------------------------------------------------------- #
# 会员购：纯函数（可离线单测）
# --------------------------------------------------------------------------- #

def area_code_for(city: str) -> str | None:
    """城市 → 会员购 area 码；无码城市返回 None（调用方跳过）。"""
    if not city:
        return None
    return BILI_AREA_CODES.get(city.strip())


def fen_to_yuan(value: Any) -> float | None:
    """分 → 元。会员购的 `price_low/price_high` 单位是分（7800 → 78.0）。

    ⚠️ 实测坑：不做这个换算会把 ¥78 的票写成 ¥7800。
    """
    if value is None or value == "":
        return None
    try:
        fen = float(value)
    except (TypeError, ValueError):
        return None
    if fen < 0:
        return None
    return round(fen / FEN_PER_YUAN, 2)


def _split_tags(raw: Any) -> list[str]:
    """条目 tags 可能是 list、逗号分隔字符串或空。"""
    out: list[str] = []
    if isinstance(raw, str):
        parts = re.split(r"[,，/|｜;；\s]+", raw)
    elif isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict):
                parts.extend(str(item.get(k)) for k in ("name", "tag_name", "title") if item.get(k))
            else:
                parts.append(str(item))
    else:
        return []
    for p in parts:
        p = p.strip()
        if p and p not in _GENERIC_TAGS and p not in out:
            out.append(p)
    return out


def _clean_venue(raw: Any) -> str | None:
    """场地清洗：先走 text_zh.clean_venue（去城市前缀/尾部地址），再剥掉 B 站惯用的中括号前缀。"""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = re.sub(r"^[\[【(（]\s*[^\]】)）]{1,6}\s*[\]】)）]\s*", "", s)
    return text_zh.clean_venue(s)


def _clean_city(raw: Any) -> str | None:
    """城市归一：实测会员购回的是「广州市」（带「市」），与项目的城市枚举不一致。"""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = re.sub(r"市$", "", s)
    return s or None


def _digits(value: Any) -> int | None:
    """全是数字（可带小数）才返回，用于过滤 `price_text` 这类非价格字段。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 0 or num != int(num):
        return None
    return int(num)


def _iso_like(value: Any) -> Any:
    """把「2026.10.01 19:30」「2026/10/01」这类本地写法规范成 ISO 风格，供 parse_ts 解析。

    `parse_ts` 只认 ISO；但实测同一个字段在不同版本里出现过点号/斜杠格式，
    所以统一在这里做一次替换，数字（unix）原样返回。
    """
    if isinstance(value, str) and value:
        return value.replace(".", "-").replace("/", "-")
    return value


_CLOCK_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")


def _combine_date_time(date_val: Any, unix_val: Any) -> tuple[dt.datetime | None, str]:
    """把「日期字符串 + 精确到秒的 unix 时间戳」拼成完整开演时间，并给出精度。

    ⚠️ 实测坑：`start_time` 只有日期（`"2026-10-18"`），真正的时刻在 `start_unix`
    （`1792287000` → 09:30+08:00）。只用 start_time 会把所有场次写成 00:00。
    反过来说：**只有日期、没有时刻**的条目（实测确实存在，如主题餐厅类）
    不能假装时间已知，精度要标 `date_only`。
    """
    date_part = parse_ts(date_val)
    unix_part = parse_ts(unix_val)
    has_clock = bool(_CLOCK_RE.search(date_val)) if isinstance(date_val, str) else False
    if date_part is not None and unix_part is not None:
        return (
            date_part.replace(
                hour=unix_part.hour, minute=unix_part.minute, second=unix_part.second
            ),
            "exact",
        )
    if date_part is not None:
        # 字符串里本身写了时刻（「2026.10.01 10:00」）→ exact；只有日期 → date_only
        return date_part, "exact" if has_clock else "date_only"
    if unix_part is not None:
        return unix_part, "exact"
    return None, "tbd"


# 实测 sale_flag 文案 → 项目状态枚举
_SALE_FLAG_STATUS: dict[str, str] = {
    "预售中": "on_sale",
    "热卖中": "on_sale",
    "售票中": "on_sale",
    "已开售": "on_sale",
    "已售罄": "sold_out",
    "售罄": "sold_out",
    "即将开售": "announced",
    "待开售": "announced",
    "已结束": "finished",
    "已取消": "cancelled",
    "延期": "postponed",
}


def _sold_out(item: dict[str, Any]) -> bool:
    """售罄判定：会员购条目带 `is_sold_out` / `sale_status` 之类的标志（不同版本字段名不一）。"""
    for key in ("is_sold_out", "sold_out", "isSoldOut"):
        if item.get(key) in (True, 1, "1"):
            return True
    for key in ("sale_status", "saleStatus", "status"):
        val = str(item.get(key) or "").lower()
        if val in ("sold_out", "soldout", "售罄", "3"):
            return True
    return False


def _item_to_occurrence(item: dict[str, Any], city: str) -> ParsedOccurrence | None:
    """单条 listV2 result → ParsedOccurrence（字段缺失时尽力而为）。"""
    if not isinstance(item, dict):
        return None
    # 实测同一条里既有 `id` 也有 `project_id`（值相同）；两个都可能缺，都认
    project_id = item.get("id") or item.get("project_id")
    name = (item.get("project_name") or item.get("name") or "").strip()
    if not project_id or not name:
        return None

    # 城市：优先条目自报（页面切换可能串数据），否则用请求的 city
    city_name = _clean_city(item.get("city")) or _clean_city(city) or city
    venue = _clean_venue(item.get("venue_name"))

    # ⚠️ 实测坑：`price_text` 不是分（可能是文案），只有 `price_low/price_high`
    #    是**分**（7800 → ¥78.00）。取错字段会把票价算成荒谬的值。
    low = fen_to_yuan(_digits(item.get("price_low")))
    high = fen_to_yuan(_digits(item.get("price_high")))
    # 价格区间兜底：只有 low 时视作单一票价；high < low 说明字段颠倒了
    if low is not None and high is not None and high < low:
        low, high = high, low

    tickets: list[TicketIn] = []
    if low is None and high is None:
        tickets = []
    elif high is None or abs(low - high) < 1e-9:  # type: ignore[operator]
        single = low if low is not None else high
        tickets = [TicketIn(name_raw="票价", price=single, tier_type="无料" if single == 0 else None)]
    else:
        tickets = [
            TicketIn(name_raw="最低价", price=low),
            TicketIn(name_raw="最高价", price=high),
        ]
    price_min = min(p for p in (low, high) if p is not None) if tickets else None
    price_max = max(p for p in (low, high) if p is not None) if tickets else None
    is_free = price_max == 0.0

    # 时间：`start_time` 只有日期，精确时刻在 `start_unix`；两者都可能是点号/斜杠格式或 unix 秒
    start_at, start_precision = _combine_date_time(
        _iso_like(item.get("start_time")), item.get("start_unix") or item.get("start_unix_time")
    )
    end_at = parse_ts(_iso_like(item.get("end_time")))
    # ⚠️ `sale_start_time` 实测是 **unix 秒**（1789117200），不是字符串
    sale_start = parse_ts(item.get("sale_start_time"))

    # 标题里的「广州·」前缀是会员购的展示前缀，去掉后标题更干净
    clean_name = re.sub(r"^[\u4e00-\u9fa5]{2,4}市?\s*[·・]\s*", "", name)

    tags: list[str] = []
    # `third_category_name` 实测是「Only同人展」这类真实品类 → 优先用它做分类上下文
    category = str(item.get("third_category_name") or "").strip()
    if category:
        tags.append(category)
    for t in _split_tags(item.get("tags")):
        if t not in tags:
            tags.append(t)

    # 状态：先看 `sale_flag`（实测文案「预售中 / 热卖中」），再退回开票时间判断
    sale_flag = str(item.get("sale_flag") or "").strip()
    status: str = _SALE_FLAG_STATUS.get(sale_flag, "on_sale")
    if _sold_out(item):
        status = "sold_out"
    elif (not sale_flag) and sale_start is not None and sale_start > now_cst():
        status = "announced"

    url = str(item.get("jump_url") or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if not url:
        url = f"https://show.bilibili.com/platform/detail.html?id={project_id}"

    # cover 也常是协议相对 URL（//i0.hdslb.com/…），补上 scheme 免得下游拼不出图
    cover = str(item.get("cover") or "").strip()
    if cover.startswith("//"):
        cover = "https:" + cover

    # 标题里的「（已结束）」之类后缀、以及「广州·」展示前缀都不属于标题本体
    title_display = clean_title(
        re.sub(r"[（(](?:已结束|已取消|已下架)[）)]", "", clean_name)
    ) or clean_name
    series_key, series_vol = text_zh.parse_series(title_display)

    occurrence = ParsedOccurrence(
        source_code=SOURCE_CODE,
        source_url=url,
        external_id=f"show:{project_id}",
        title_raw=name,
        title_display=title_display,
        series_key=series_key,
        series_vol=series_vol,
        city=city_name or None,
        venue_raw=venue,
        start_at=start_at,
        end_at=end_at,
        date_precision=start_precision,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        status_note=sale_flag or (
            f"会员购开票时间 {sale_start:%Y-%m-%d %H:%M}" if sale_start is not None else None
        ),
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        tickets=tickets,
        # 采集器已收集的标签（B站三级品类等）；classify 的词由 finalize 合并进来
        tags=tags,
        poster_url=cover or None,
        confidence=0.9 if start_at else 0.6,
        raw={
            "channel": "show_listV2",
            "project_id": project_id,
            "price_low_fen": item.get("price_low"),
            "price_high_fen": item.get("price_high"),
            "district_name": item.get("district_name"),
            "sale_start_time": item.get("sale_start_time"),
            "sale_flag": sale_flag,
            "start_unix": item.get("start_unix"),
            "third_category_name": category,
            "venue_id": item.get("venueId") or item.get("venue_id"),
            "wish": item.get("wish"),
            "coordinate": item.get("coordinate"),
        },
    )
    # 标题定稿后再校准分类（分类词主要看标题，这里把场地名/品类名也带上做上下文；
    # `tags` 已经挂在 occurrence 上，finalize 会把 classify 的词与它合并去重）
    return finalize(occurrence, extra_context=f"{venue or ''} {category}")


def parse_listv2_payload(payload: dict[str, Any], city: str) -> list[ParsedOccurrence]:
    """会员购 `listV2` 响应 JSON → ParsedOccurrence 列表（**纯函数，可离线单测**）。

    结构（实测）：`{"errno":0,"data":{"result":[{...}]}}`。
    兼容 `errno != 0`、`data.result` 缺失、条目不是 dict 的情况——一律返回已解析到的部分，
    绝不抛异常（采集层一条脏数据不该让整轮抓取失败）。
    """
    if not isinstance(payload, dict) or payload.get("errno") not in (0, "0", None):
        return []
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    items = data.get("result") or data.get("list") or []
    if not isinstance(items, list):
        return []
    out: list[ParsedOccurrence] = []
    seen: set[str] = set()
    for item in items:
        occ = _item_to_occurrence(item, city)
        if occ is None or occ.external_id in seen:
            continue
        seen.add(occ.external_id)
        out.append(occ)
    return out


def parse_listv2_pages(payloads: list[Any], city: str) -> list[ParsedOccurrence]:
    """把多页 listV2 响应合并解析（按 external_id 去重，保留首次出现）。"""
    out: list[ParsedOccurrence] = []
    seen: set[str] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for occ in parse_listv2_payload(payload, city):
            if occ.external_id in seen:
                continue
            seen.add(occ.external_id)
            out.append(occ)
    return out


# --------------------------------------------------------------------------- #
# 会员购：CDP「点击城市 + 拦截」
# --------------------------------------------------------------------------- #

def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "scripts"


def _import_scripts() -> None:
    scripts = str(_scripts_dir())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)


def click_by_text(br: Any, text: str, tag: str = "*") -> bool:
    """在页面里找到 innerText 等于 text 的可见元素并**派发真实鼠标事件**。

    为什么不用 `el.click()`：部分前端框架只监听真实指针事件，
    实测 B 站会员购的城市切换按钮对 JS `click()` 无反应（见 scripts/cdp_interact.py）。
    """
    expr = f"""
    (() => {{
      const want = {json.dumps(text)};
      const els = [...document.querySelectorAll({json.dumps(tag)})];
      const hit = els.find(e => (e.innerText || '').trim() === want && e.offsetParent !== null);
      if (!hit) return null;
      const r = hit.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return null;
      return {{x: r.left + r.width / 2, y: r.top + r.height / 2,
               tag: hit.tagName, cls: (hit.className || '').toString().slice(0, 60)}};
    }})()
    """
    res = br.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    pt = (res.get("result") or {}).get("value")
    if not pt:
        return False
    for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
        br.cmd(
            "Input.dispatchMouseEvent",
            {
                "type": typ,
                "x": pt["x"],
                "y": pt["y"],
                "button": "left",
                "clickCount": 1 if typ != "mouseMoved" else 0,
            },
        )
    log.debug("点击「%s」→ %s.%s @ (%.0f,%.0f)", text, pt["tag"], pt["cls"], pt["x"], pt["y"])
    return True


def _cursor(hits: list[dict[str, Any]]) -> int:
    return len(hits)


def _drain(
    br: Any,
    hits: list[dict[str, Any]],
    seen: set[str],
    match: str,
    deadline: float,
) -> None:
    """在 deadline 之前不断读 CDP 事件流，记录 URL 含 match 的请求。

    ⚠️ 实测坑（两次都踩到）：`Network.getResponseBody` 在页面**仍在加载**时会直接失败
    （`-32000 No data found for resource with given identifier`），因为响应体已被丢弃。
    所以这里只**登记**命中的 requestId，真正的取 body 放到 `_collect_bodies()` 里、
    等页面静下来之后再做。
    """
    while time.time() < deadline:
        try:
            msg = json.loads(br.ws.recv())  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001  超时或连接关闭：直接结束这一轮
            return
        if msg.get("method") != "Network.responseReceived":
            continue
        params = msg.get("params") or {}
        response = params.get("response") or {}
        url = response.get("url") or ""
        rid = params.get("requestId")
        if match not in url or not rid or rid in seen:
            continue
        seen.add(rid)
        hits.append({"url": url, "status": response.get("status"), "request_id": rid, "body": ""})


def _collect_bodies(br: Any, hits: list[dict[str, Any]], *, retries: int = 3) -> None:
    """页面加载结束后再取响应体（带重试，应对「资源还没准备好」）。"""
    for hit in hits:
        rid = hit.get("request_id")
        if not rid:
            continue
        for attempt in range(retries):
            try:
                body = br.cmd("Network.getResponseBody", {"requestId": rid})
                hit["body"] = body.get("body", "") or ""
                break
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 >= retries:
                    log.debug("取响应体失败 %s: %s", str(hit.get("url"))[:80], exc)
                else:
                    time.sleep(0.5)


def fetch_show_projects(
    city: str,
    *,
    wait: float = 14.0,
    max_pages: int = 3,
    headless: bool | None = None,
) -> list[dict[str, Any]]:
    """CDP 打开会员购首页 → 点「城市」→ 点城市名 → 拦截 `/api/ticket/project`。

    返回拦截到的原始响应（`{"url","status","body"}`），由调用方用
    `parse_listv2_pages` 转成 ParsedOccurrence。浏览器相关代码集中在这里，
    便于在无浏览器环境（CI）里跳过。
    """
    area = area_code_for(city)
    if area is None:
        log.info("会员购：城市「%s」暂无实测 area 码，跳过", city)
        return []

    _import_scripts()
    from cdp_fetch import Browser  # type: ignore  # 仅标准库实现

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    br = Browser(headless=True if headless is None else headless, width=1440, height=1400)
    try:
        br.cmd("Page.enable")
        br.cmd("Runtime.enable")
        br.cmd("Network.enable")
        br.cmd("Page.navigate", {"url": SHOW_HOME})
        time.sleep(max(6.0, wait * 0.5))

        # 首页会自带一次「默认城市」的列表请求；先把它收掉，避免与目标城市混淆
        _drain(br, hits, seen, SHOW_XHR_MATCH, time.time() + 6)
        hits.clear()

        # 打开城市选择器，再点目标城市
        if not click_by_text(br, city):
            click_by_text(br, "城市")  # 选择器没展开时先展开
            time.sleep(1.5)
            if not click_by_text(br, city):
                log.warning("会员购：页面上找不到可点的「%s」", city)
                return []

        for page in range(max(1, max_pages)):
            before = _cursor(hits)
            _drain(br, hits, seen, SHOW_XHR_MATCH, time.time() + wait)
            if _cursor(hits) == before and page == 0:
                log.warning("会员购：点击「%s」后没有拦到 %s", city, SHOW_XHR_MATCH)
                break
            if page + 1 >= max_pages:
                break
            # 翻页：找「下一页」真实点击（找不到就停在第一页，绝不拼接口）
            if not click_by_text(br, "下一页") and not click_by_text(br, "下页"):
                break
            time.sleep(1.0)

        # 页面已经静下来，这时再取响应体（加载中取会失败，见 `_drain` 的注释）
        _collect_bodies(br, hits)
    except Exception as exc:  # noqa: BLE001
        # ⚠️ 实测坑：CDP 的 WebSocket 可能在导航/点击中途断开
        # （`sock.sendall` 直接抛异常），这不该让整轮采集崩掉 —— 返回已拦到的部分。
        log.warning("会员购 %s：CDP 会话中断（%s），返回已拦到的 %d 个响应", city, exc, len(hits))
    finally:
        try:
            br.close()
        except Exception:  # noqa: BLE001
            pass
    log.info("会员购 %s：拦截到 %d 个响应", city, len(hits))
    return hits


# --------------------------------------------------------------------------- #
# UP 主动态：切块 + 纯函数解析
# --------------------------------------------------------------------------- #

def _strip_marker(chunk: str) -> tuple[str | None, str]:
    """拆出块首的时间标记，返回 (标记原文, 去掉标记后的正文)。

    ⚠️ 标记后面的「· 投稿了视频」是动态头，必须一起去掉，
    否则它会变成标题，并且 `01:01:52` 这种视频时长会被 parse_datetime 误当开演时间
    （实测踩坑：open_at 被写成 01:01）。
    """
    m = _TIME_MARKER_RE.search(chunk)
    if not m:
        return None, chunk.strip()
    marker = m.group(0).replace(" ", "")
    body = chunk[m.end():]
    body = _DYNAMIC_VERB_RE.sub("", body.lstrip())
    return marker, body.strip()


def _fallback_title(body: str) -> str | None:
    """从正文里挑一个像标题的行（extract_from_text 会取第一行，而第一行常是互动数据）。

    每行先过 `clean_title`：B 站动态正文常写成「标题 时间 地点 票价」一长串，
    直接当标题会把整条动态塞进 title_display（实测 90 字符以上，且会污染分类）。
    """
    for line in body.splitlines():
        cand = clean_title(_MEDIA_TIME_RE.sub(" ", text_zh.display_title(line)))
        if not (4 <= len(cand) <= 90):
            continue
        if _NOISE_LINE_RE.search(cand):
            continue
        # 纯日期/时间行不是标题
        if _TITLE_DATE_TIME_RE.search(cand) and len(_TITLE_DATE_TIME_RE.sub("", cand).strip()) < 4:
            continue
        if not _TITLE_NEEDLE_RE.search(cand):
            continue
        return cand
    return None


def _fallback_lineup(text: str) -> list[ArtistIn]:
    """`parse_lineup` 抽不到时的兜底：@阵列 + 「XX乐队：A B C」冒号片段。

    ⚠️ 实测坑：`text_zh.parse_lineup` 只要文本里出现「乐队 / 阵容」这类词就会
    退化成「按空格切整段」。实测动态正文里「除了大量**乐队**和合作项目之外」
    被切成 `['合作项目之外', 'Áron 还以作曲家', '现场乐手的身份']` 这种垃圾阵容。
    所以这里只在**强信号**成立时才信它的输出：
      * `@` 阵列（微博式）；
      * 分隔符阵列（× / ✕ / ＋）；
      * **标签后紧跟冒号**（「演出乐队：海朋森 晕盖」）——只出现「乐队」二字不算。
    """
    labels_at = re.search(
        r"(?:" + "|".join(re.escape(x) for x in _LINEUP_LABELS) + r")\s*[:：]", text
    )
    artists = text_zh.parse_lineup(text)
    if artists and (_AT_TOKEN_RE.search(text) or labels_at or re.search(r"[×✕＋]", text)):
        cleaned = sanitize_artists(artists)
        if cleaned:
            return cleaned  # type: ignore[return-value]

    # 整段兜底：只从**带标签的**冒号片段里切（避免把「时间：…」当阵容）
    label_segments: list[str] = []
    for label in _LINEUP_LABELS:
        m = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n]{{2,120}})", text)
        if m:
            label_segments.append(m.group(1).strip())

    blobs: list[str] = []
    for segment in label_segments:
        blobs.extend(p for p in re.split(r"\s+", segment) if p)
        blobs.extend(p for p in re.split(r"\s*[/、,，;；|｜×✕＋＆&]\s*", segment) if p)
    # 没有任何标签 → 只认分隔符阵列（「A × B」「A / B」），不按空格乱切
    for segment in re.findall(r"[^\n]{2,120}", text):
        if re.search(r"[×✕＋]", segment):
            blobs.extend(p for p in re.split(r"\s*[×✕＋]\s*", segment) if p)
    # 标签片段与分隔符片段会重叠（同一个名字被切两次）→ 先去重，billing_order 才不会跳号
    blobs = list(dict.fromkeys(b.strip() for b in blobs if b.strip()))

    out: list[ArtistIn] = []
    for blob in blobs:
        name = blob.strip(" ·-—@")
        if not name or "#" in name:
            continue
        kept = sanitize_artists([ArtistIn(name_raw=name)])
        if kept:
            artist = kept[0]
            artist.billing_order = len(out) + 1
            out.append(artist)
    return out


def _fallback_venue(text: str, city: str | None) -> str | None:
    """没有「地点：」标签时，从文本里捞一个像场地的名字（B10 动态常只写「深圳B10现场」）。

    ⚠️ 实测坑：`text_zh.looks_like_venue` 太宽，会把「流动的诗意:Joëlle」这类
    正文片段当场地 → 改用 `looks_like_venue_piece`（要求含场地类型词、
    不含句子标点）。宁可为空，也不能把正文当场地写进 venue_raw。
    """
    for line in text.splitlines():
        for piece in re.split(r"[\s·|｜,，。;；]+", line):
            piece = piece.strip()
            if not looks_like_venue_piece(piece):
                continue
            # ⚠️ 不要在这里自己剥城市前缀：`text_zh.clean_venue` 已经会**判断性地**剥
            # （只在后面确实跟地址时才剥）。此前这里无条件 `re.sub(rf"^{city}市?", ...)`
            # 把「深圳B10现场」切成了「B10现场」、把「广州CH8蛙厂演艺中心」切成了
            # 「CH8蛙厂演艺中心」—— 丢了城市信息，也让不同城市的同名场地难以区分。
            venue = text_zh.clean_venue(piece)
            if venue:
                return venue
    return None


def _looks_like_event(
    fields: dict[str, Any],
    body: str,
    marker: str | None,
    *,
    venue: str | None = None,
    artists: list[ArtistIn] | None = None,
) -> bool:
    """是否值得当成一条演出。**必须真的解析出日期**，光有「3小时前」不算。

    ⚠️ 实测坑（动态页特有）：把「发布时间」或「视频时长」当活动日期。
      * 动态块里「20分钟前」是**发布时间**，不是活动时间；
      * 「04:22」是**视频时长**，被 parse_datetime 当成开演时间 →
        实测产出一条「2026-10-08 04:22 开演」的假场次。
    所以动态通道额外要求：正文里必须**真的写了日期**（`require_explicit_date=True`）。
    实测代价：B10Live 动态页 12 个块里 0 条是真正带日期的活动预告
    （都是视频推广文案）→ 全部被正确丢弃。这类活动日期在视频详情页里，
    需要另开采集通道，**不能**用发布时间凑数。
    """
    if len(body) < 8:
        return False
    return looks_like_event(fields, body, venue=venue, artists=artists,
                            require_explicit_date=True)


def parse_dynamic_text(
    text: str,
    mid: str | int,
    *,
    now: dt.datetime | None = None,
    source_url: str | None = None,
    page_title: str | None = None,
) -> list[ParsedOccurrence]:
    """UP 主动态渲染文本 → ParsedOccurrence 列表（**纯函数，可离线单测**）。

    为什么按时间标记切块：动态页没有稳定的 DOM 结构（B 站前端改版频繁），
    但每条动态卡片头部一定有 `4小时前 / 昨天 / 08-26` 这类时间标记，
    用它在 `innerText` 上切块比依赖 class 名稳得多。
    """
    if not text:
        return []
    now = now or now_cst()
    url = source_url or DYNAMIC_URL.format(mid=mid)
    page_title = page_title or ""

    matches = list(_TIME_MARKER_RE.finditer(text))
    if not matches:
        return []

    # 第一条动态的正文可能从标记**之前**就开始（昵称/认证行），但头部噪音居多：
    # 从标记后开始取，宁可漏掉头部也不把「B10Live 深圳B10现场 …」当正文。
    blocks: list[tuple[str | None, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start(): end]
        marker, body = _strip_marker(block)
        if body:
            blocks.append((marker, body))

    out: list[ParsedOccurrence] = []
    seen_ids: set[str] = set()
    for marker, body in blocks:
        # 视频时长不是时间；去掉后再解析，避免 open_at 被写成 01:01
        cleaned = _MEDIA_TIME_RE.sub(" ", body)
        external_id = f"dynamic:{mid}:{content_hash(body)}"
        if external_id in seen_ids:
            continue

        fields = text_zh.extract_from_text(
            cleaned,
            source_code=SOURCE_CODE,
            source_url=url,
            external_id=external_id,
            now=now,
        )
        # venue/artists 要先算，`looks_like_event` 依赖它们判定「这条是不是演出」
        # （许多动态根本没有「地点：」标签，场地只能靠兜底从正文里捞）
        venue = fields.get("venue_raw") or _fallback_venue(cleaned, fields.get("city"))
        # ⚠️ 即使 extract_from_text 给了 artists 也要过一遍过滤：无标签时
        #    parse_lineup 会按空格切整段，产出一堆垃圾阵容。
        artists = sanitize_artists(fields.get("artists") or []) or _fallback_lineup(cleaned)

        if not _looks_like_event(fields, cleaned, marker, venue=venue, artists=artists):
            continue

        # 标题：动态正文没有标题字段，只能从正文里挑一行像标题的
        title = _fallback_title(cleaned)
        if not title:
            title = clean_title(fields.get("title_display") or "")
        if not title:
            continue
        fields["title_display"] = title
        # 标题变了，系列/期号要按新标题重算（否则 series_key 会是整段正文的哈希式拼接）
        fields["series_key"], fields["series_vol"] = text_zh.parse_series(title)

        # 动态里若带外链（b23.tv / 购票页），source_url 换成它，便于回溯到原始动态
        link = _URL_RE.search(cleaned)
        # ⚠️ 所有覆盖都在 fields 里做完：ParsedOccurrence(**fields, x=...) 里 x 若已在
        #    fields 中会直接 TypeError（extract_from_text 已经填了 source_url/external_id/title_display）
        fields["source_url"] = link.group(0) if link else url
        fields["venue_raw"] = venue
        fields["artists"] = artists
        fields["description"] = cleaned[:800]
        fields["confidence"] = 0.7 if fields.get("start_at") else 0.4

        seen_ids.add(external_id)
        occurrence = ParsedOccurrence(
            **fields,
            raw={
                "channel": "dynamic",
                "mid": str(mid),
                "time_marker": marker,
                "page_title": page_title,
                "content_hash": content_hash(body),
            },
        )
        out.append(finalize(fix_relative_date(occurrence, cleaned), extra_context=cleaned[:200]))
    log.info("动态解析 mid=%s：%d 块 → %d 条", mid, len(blocks), len(out))
    return out


# --------------------------------------------------------------------------- #
# 采集器
# --------------------------------------------------------------------------- #

class BilibiliCollector:
    """B 站采集器（动态通道 + 会员购通道）。

    与调度层（`app/pipeline.py`）的约定和 `ShowstartCollector` 一致：

        occ = await BilibiliCollector().collect(browser, cities=["广州", "深圳"])

    也可以自己管浏览器生命周期：

        async with BilibiliCollector() as c:
            occ = await c.collect_dynamics([510676514])
            occ += await c.collect_show(["广州", "深圳"])

    浏览器只在 CDP 通道里用；纯函数 `parse_dynamic_text` / `parse_listv2_payload`
    不需要浏览器，可在 CI 里直接单测。
    """

    source_code = SOURCE_CODE

    def __init__(
        self,
        *,
        browser: BrowserFetcher | None = None,
        http: HttpFetcher | None = None,
        headless: bool | None = None,
        show_wait: float = 14.0,
        show_max_pages: int = 3,
    ) -> None:
        self.browser = browser
        self._own_browser = browser is None
        self._headless = headless
        self.http = http
        self._own_http = http is None
        self.show_wait = show_wait
        self.show_max_pages = show_max_pages

    async def __aenter__(self) -> BilibiliCollector:
        if self.browser is None:
            self.browser = await BrowserFetcher(headless=self._headless).__aenter__()
        if self.http is None:
            self.http = await HttpFetcher().__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self.browser is not None and self._own_browser:
            await self.browser.__aexit__(None, None, None)
            self.browser = None
        if self.http is not None and self._own_http:
            await self.http.__aexit__(None, None, None)
            self.http = None

    # ---- (a) 动态通道 ----

    async def collect_dynamics(
        self,
        mids: list[str | int] | tuple[str | int, ...],
        *,
        wait: float | None = None,
        now: dt.datetime | None = None,
    ) -> list[ParsedOccurrence]:
        """采集一批 UP 主的动态页（场地自营宣发，乐队为主）。"""
        if self.browser is None:
            raise RuntimeError("BilibiliCollector 需在 async with 中使用（或注入 browser）")
        out: list[ParsedOccurrence] = []
        for mid in mids:
            url = DYNAMIC_URL.format(mid=mid)
            res = await self.browser.render(url, wait)
            if not res.ok or not res.text:
                log.warning("动态页渲染失败 mid=%s: %s", mid, res.error)
                continue
            title = str((res.payload or {}).get("title") or "")
            out.extend(parse_dynamic_text(res.text, mid, now=now, source_url=url, page_title=title))
        return out

    # ---- (b) 会员购通道 ----

    async def collect_show(
        self,
        cities: list[str] | tuple[str, ...],
        *,
        wait: float | None = None,
        max_pages: int | None = None,
    ) -> list[ParsedOccurrence]:
        """采集会员购广东各市列表（Only / 同人 / ACG，地偶邻域）。"""
        wait = self.show_wait if wait is None else wait
        max_pages = self.show_max_pages if max_pages is None else max_pages
        out: list[ParsedOccurrence] = []
        for city in cities:
            if area_code_for(city) is None:
                log.info("会员购：跳过无 area 码的城市 %s", city)
                continue
            hits = await asyncio.to_thread(
                fetch_show_projects, city, wait=wait, max_pages=max_pages,
                headless=self._headless,
            )
            payloads = json_payloads(hits)
            occ = parse_listv2_pages(payloads, city)
            log.info("会员购 %s：%d 条", city, len(occ))
            out.extend(occ)
        return out

    # ---- 调度层入口（与 ShowstartCollector.collect 同形） ----

    async def collect(
        self,
        browser: BrowserFetcher | None = None,
        cities: list[str] | None = None,
        *,
        mids: list[str | int] | None = None,
        wait: float | None = None,
    ) -> list[ParsedOccurrence]:
        """跑两条通道并去重（调度层最常用）。

        `browser` 由调度层传入并复用（它的生命周期归调度层管，本方法不关它）。
        ⚠️ 实测：动态页当前**拿不到可靠活动日期**（见 `_looks_like_event` 的说明），
        所以默认只跑会员购通道；想同时采动态要显式给 `mids`（或用 DYNAMIC_MIDS 种子）。
        """
        if browser is not None:
            self.browser = browser
            self._own_browser = False
        elif self.browser is None:
            self.browser = await BrowserFetcher(headless=self._headless).__aenter__()

        out: list[ParsedOccurrence] = []
        seen: set[str] = set()

        ids = DYNAMIC_MIDS if mids is None else mids
        try:
            for occ in await self.collect_dynamics(ids, wait=wait, now=None):
                if occ.external_id not in seen:
                    seen.add(occ.external_id)
                    out.append(occ)
        except Exception as exc:  # noqa: BLE001  单条通道失败不该毁掉整轮
            log.warning("B 站动态通道失败：%s", exc)

        for occ in await self.collect_show(cities or ["广州", "深圳"], wait=wait):
            if occ.external_id not in seen:
                seen.add(occ.external_id)
                out.append(occ)
        log.info("B 站合计 %d 条（已去重）", len(out))
        return out


def json_payloads(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从拦截结果里挑出 listV2 的 JSON（`listconf` 是过滤器配置，不参与解析）。

    公开函数（不带下划线）：它是「拦截结果 → 可解析 payload」的关键一步，
    调度层/排查脚本也需要用它。
    """
    out: list[dict[str, Any]] = []
    for hit in hits or []:
        url = hit.get("url") or ""
        if "listV2" not in url and "project/list" not in url:
            continue
        try:
            payload = json.loads(hit.get("body") or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


__all__ = [
    "BilibiliCollector",
    "BILI_AREA_CODES",
    "DYNAMIC_MIDS",
    "KNOWN_DYNAMIC_MIDS",
    "SHOW_HOME",
    "SHOW_XHR_MATCH",
    "SOURCE_CODE",
    "area_code_for",
    "click_by_text",
    "fetch_show_projects",
    "fen_to_yuan",
    "json_payloads",
    "parse_dynamic_text",
    "parse_listv2_pages",
    "parse_listv2_payload",
    "sanitize_artists",
]
