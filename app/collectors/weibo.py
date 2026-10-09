"""微博采集器：搜索页渲染 + XHR 拦截（地偶主源）。

对应 docs/02-采集方案-全自动.md §3.2。**三条实测结论决定了这里的实现方式**：

  1. `https://m.weibo.cn/u/{uid}`（个人主页）→ **强制跳 passport 登录**，不可用。
  2. `https://m.weibo.cn/api/container/getIndex?...`（纯 HTTP 直连）→ 返回 432 / `ok=-1`，
     **绝对不要直接打这个接口**（有反爬，打了也拿不到数据）。
  3. ✅ 唯一可行路径：CDP 打开搜索页 → **拦截页面自己发出的** `/api/container` XHR。
     实测 185,844 字节干净 JSON，16 条博文。

所以「搜索页」是唯一入口；即使我们已经知道某个账号 uid（账号发现拿到的），
也**仍然回到搜索页取内容**，不碰个人主页。

纯函数 `parse_search_payload` / `discover_accounts` 不依赖浏览器，可离线单测。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html as html_lib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.collectors._postprocess import clean_title, finalize, fix_relative_date, looks_like_event
from app.collectors.base import BrowserFetcher, HttpFetcher
from app.models import FetchResult, ParsedOccurrence
from app.parsers import text_zh
from app.utils import CST, content_hash, get_logger, now_cst, parse_ts

log = get_logger(__name__)

SOURCE_CODE = "weibo"
PLATFORM = "weibo"

# 搜索页基址（containerid 的取值由 search_url() 拼装并整体 URL 编码）
SEARCH_URL = "https://m.weibo.cn/search?containerid={kw}"
# 拦截目标：页面自己请求的容器接口
SEARCH_XHR_MATCH = "/api/container"
# containerid 的前缀：100103type=1&q=<关键词>（type=1 是综合搜索）
SEARCH_CONTAINER_PREFIX = "100103type=1&q="

# 发现用搜索词。
#
# ⚠️ 每个词 ≈33 秒（渲染移动端搜索页 + 拦截 XHR），所以**词表要精挑**：
#    实测对标（scripts/probe_weibo_keywords.py）后定下下面这些。
#
# 覆盖三类来源，都是售票平台拿不到的：
#   1. **聚合号**：「本周广州偶活速览」这类周更贴，一次覆盖整周免费/小型场次
#      （2026-10 实测：仅「广州 免费 公演」一词就出 11 条，含该聚合贴）
#   2. **地偶团体/企划**：团体官微发的演出预告（带阵容，是分类的命脉）
#   3. **ACG / 同人 演出**：ACG 乐队、同人 Only、术力口 Only 等
#      （实测：「广州 ACG 乐队」出「音爆ANISON 超次元ACG室内音乐节」「BO5乐队」）
#
# ⚠️ 场地词（地王广场）也放进来了：地王广场是**免费场地**（商场中庭），
#    演出不上售票平台，只能靠社交媒体。实测「地王广场」有产出，但偏快闪/主题店，
#    所以与「偶像」「公演」组合用，直接搜场地名会带进一堆商场营销内容。
SEARCH_KEYWORDS: list[str] = [
    # ---- 聚合速览（性价比最高，优先）----
    "广州 免费 公演",
    "广州 偶活速览",
    "深圳 偶活 速览",
    # ---- 地偶（各城）----
    "广州地偶",
    "深圳地偶",
    "珠海 地偶",
    "东莞 地偶",
    "佛山 地偶",
    "广州 生诞",
    # ---- 免费场地（商场/公共空间）----
    "地王广场 偶像",
    # ---- ACG / 同人 演出 ----
    "广州 ACG 乐队",
    "广州 同人 演出",
    "广州 术力口",
    "深圳 ACG 演出",
    # ---- 漫展里的地偶/乐队舞台 ----
    "东莞 萤火虫 偶像",
    "广州 漫展 偶像 舞台",
    "萤火虫 漫展 舞台",
    "CICF 地下偶像",
    # ---- ACG 音乐演出 ----
    "广州 ACG 音乐节",
    "广州 anisong",
    # ---- 免费场地 ----
    "广州 商场 演出",
    "广州 免费 偶像",
]

# 微博搜索页在 m 站（移动端）才有干净的卡片 JSON
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 用户主页模板：**仅用于记录 channel，不要拿它去抓正文**（会跳登录）
USER_URL_TEMPLATE = "https://m.weibo.cn/u/{uid}"

# --------------------------------------------------------------------------- #
# HTML 清洗
# --------------------------------------------------------------------------- #

_TAG_RE = re.compile(r"<[^>]+>")
# 微博表情是 <img alt="[doge]" ...>（部分不带 url-icon），alt 里的方括号文字要留下
_IMG_ALT_RE = re.compile(r"<img[^>]*\balt=\"([^\"]*)\"[^>]*>", re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_LINK_RE = re.compile(r"https?://[^\s，,。；;）)】\]]+")
_HASHTAG_RE = re.compile(r"#([^#\s]{1,40})#")
_REL_TIME_RE = re.compile(r"(?:刚刚|\d+\s*秒前|\d+\s*分钟前|\d+\s*小时前|\d+\s*天前)")


def strip_html(text: str | None) -> str:
    """微博 `mblog.text` 的 HTML → 纯文本。

    实测 `text` 里混着 `<a>`（话题/超话）、`<span class="url-icon"><img alt="[doge]"></span>`
    这类表情占位，直接进正则抽取会把标签名当文字 → 必须先清洗。
    表情的 `alt`（如 `[doge]`）是语义，保留。
    """
    if not text:
        return ""
    s = _SCRIPT_STYLE_RE.sub(" ", text)
    s = _IMG_ALT_RE.sub(lambda m: m.group(1), s)   # 表情 alt 先落地
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)  # 换行标签 → 真换行（标题取首行依赖它）
    s = _TAG_RE.sub("", s)
    s = html_lib.unescape(s)
    s = s.replace("\u200b", "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _pick_title(text: str, fallback: str) -> str:
    """博文标题：首行 → 收拾成短标题；首行不成器就顺次看后面的行。

    ⚠️ 实测坑：微博演出文案常把「标题+时间+地点+票价+阵容」全写在一行里，
    直接拿首行当标题会得到 80+ 字符的整段（并让 classify 误判品类），
    所以每行都过 `clean_title`。
    """
    for line in (text or "").splitlines():
        cand = clean_title(line)
        if 3 <= len(cand) <= 90:
            return cand
    cand = clean_title(text or "")
    return cand or text_zh.display_title(fallback or "")[:60]


# --------------------------------------------------------------------------- #
# 时间
# --------------------------------------------------------------------------- #

# 实测形态："Wed Aug 26 18:35:15 +0800 2026"（**年份在最后**，dateutil/fromisoformat 都吃不掉）
_WEIBO_TS_RE = re.compile(
    r"(?P<wday>[A-Za-z]{3})\s+(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<tz>[+-]\d{4})\s+(?P<year>\d{4})"
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_weibo_created_at(value: Any) -> dt.datetime | None:
    """解析 `mblog.created_at`。

    三种实测形态：
      * `"Wed Aug 26 18:35:15 +0800 2026"` —— 搜索接口的形态，**年份在最后**
      * unix 时间戳（int / 数字字符串）
      * `"刚刚"` / `"10分钟前"` / `"3小时前"` / `"昨天 12:30"` —— 相对时间
    """
    if value is None or value == "":
        return None
    now = now_cst()

    if isinstance(value, (int, float)):
        return parse_ts(value)

    s = str(value).strip()
    if not s:
        return None

    m = _WEIBO_TS_RE.search(s)
    if m:
        try:
            hour, minute, *sec = m.group("time").split(":")
            tz_sign = 1 if m.group("tz")[0] == "+" else -1
            tz = dt.timezone(
                tz_sign
                * dt.timedelta(hours=int(m.group("tz")[1:3]), minutes=int(m.group("tz")[3:5]))
            )
            return dt.datetime(
                int(m.group("year")),
                _MONTHS[m.group("mon").lower()],
                int(m.group("day")),
                int(hour),
                int(minute),
                int(sec[0]) if sec else 0,
                tzinfo=tz,
            )
        except (KeyError, ValueError, IndexError):
            return None  # 落到下面的兜底

    if s in ("刚刚", "刚才"):
        return now

    m = re.search(r"(\d+)\s*(秒|分钟|小时|天)前", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {
            "秒": dt.timedelta(seconds=n),
            "分钟": dt.timedelta(minutes=n),
            "小时": dt.timedelta(hours=n),
            "天": dt.timedelta(days=n),
        }[unit]
        return now - delta

    today = now.date()
    if "昨天" in s:
        day = today - dt.timedelta(days=1)
    elif "前天" in s:
        day = today - dt.timedelta(days=2)
    else:
        return parse_ts(s)
    tm = re.search(r"(\d{1,2}):(\d{2})", s)
    return dt.datetime.combine(
        day,
        dt.time(int(tm.group(1)), int(tm.group(2))) if tm else dt.time(0, 0),
        tzinfo=CST,
    )


# --------------------------------------------------------------------------- #
# payload 遍历
# --------------------------------------------------------------------------- #

def _iter_mblogs(node: Any) -> list[dict[str, Any]]:
    """递归收集所有 `mblog` dict（`cards` 层级实测会变，不写死路径）。"""
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        # 实测：博文可能**就在 cards 同层**（卡片自身有 mblog），也可能包在
        # {"card_type": 9, "mblog": {...}} 里；两种都靠「节点上有 mblog」覆盖。
        mblog = node.get("mblog")
        if isinstance(mblog, dict):
            out.append(mblog)
        for value in node.values():
            out.extend(_iter_mblogs(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_iter_mblogs(item))
    return out


def _post_pics(mblog: dict[str, Any]) -> list[str]:
    """只取真实配图。

    ⚠️ 实测坑：`pics[].url` 绝大多数是 `url-icon`（表情/小图标），
    真正的配图在 `pics[].large.url`；混进来会把海报 URL 存成表情图。
    """
    pics: list[str] = []
    for pic in mblog.get("pics") or []:
        if not isinstance(pic, dict):
            continue
        large = pic.get("large") or {}
        url = (large.get("url") if isinstance(large, dict) else None) or pic.get("url") or ""
        url = str(url)
        if not url:
            continue
        # 实测坑：pics 里混着表情/小图标（h5.sinaimg.cn/thumb150/…、"url-icon"），
        # 真正的配图在 large.url（wx*.sinaimg.cn/large/…）。不滤会把海报存成表情图。
        if "url-icon" in url or "/thumb" in url or "h5.sinaimg.cn" in url:
            continue
        if url not in pics:
            pics.append(url)
    return pics


def _user_of(mblog: dict[str, Any]) -> dict[str, Any]:
    user = mblog.get("user")
    return user if isinstance(user, dict) else {}


def _post_to_occurrence(
    mblog: dict[str, Any],
    *,
    keyword: str | None = None,
    city: str | None = None,
    now: dt.datetime | None = None,
) -> ParsedOccurrence | None:
    """单条 `mblog` → ParsedOccurrence；纯噪声博文返回 None。"""
    if not isinstance(mblog, dict):
        return None
    mid = mblog.get("id") or mblog.get("mid") or mblog.get("idstr")
    text = strip_html(mblog.get("text"))
    if not mid or not text:
        return None

    external_id = f"weibo:{mid}"
    created = parse_weibo_created_at(mblog.get("created_at"))
    user = _user_of(mblog)
    screen_name = user.get("screen_name") or ""

    # ⚠️ 关键：用**博文发出时间**作 `now`，否则「昨天/今晚」会相对抓取时刻漂移，
    #    每次重跑同一份 payload 都得出不同日期（快照重放必须幂等）。
    ref_now = created or now or now_cst()

    title = _pick_title(text, screen_name or "（未命名活动）")
    fields = text_zh.extract_from_text(
        text,
        source_code=SOURCE_CODE,
        source_url=f"https://m.weibo.cn/detail/{mid}",
        external_id=external_id,
        default_city=city,
        now=ref_now,
        title_hint=title,
    )
    if fields.get("date_precision") in (None, "tbd"):
        return None  # 没有可识别日期 → 不是演出预告（避免把日常博文灌进日历）

    # ⚠️ 实测坑：微博文案里的日期常常**没有年份**（「10月18日」「9日」），
    #    解析器补当前年时，若那天已过就会推到**下一年**。于是库内出现了
    #    「2027-06-07 深圳天气剧透# 9日局地偶有零星小雨」这种荒谬场次。
    #
    #    判据：演出不会提前太久预告。用**博文发出时间**做基准，超过 120 天的
    #    一律丢弃 —— 真演出提前 4 个月以上公告的情况极少，
    #    而「把已过的日子推到明年」正好会落在这个区间之外。
    start_at = fields.get("start_at")
    if start_at is not None and ref_now is not None:
        horizon = ref_now + dt.timedelta(days=120)
        # 统一时区后比较（SQLite 读回来可能是 naive）
        s_cmp = start_at if start_at.tzinfo else start_at.replace(tzinfo=CST)
        n_cmp = ref_now if ref_now.tzinfo else ref_now.replace(tzinfo=CST)
        if s_cmp > horizon:
            log.debug(
                "微博丢弃远期日期 %s（博文 %s）：%s",
                s_cmp.date(), n_cmp.date(), (fields.get("title_display") or "")[:30],
            )
            return None
    if _REL_TIME_RE.search(fields.get("title_display") or ""):
        # 标题不该是「10分钟前」这类相对时间
        fields["title_display"] = title
    # 统一在这里落地标题，避免下面 ParsedOccurrence(**fields, title_display=...) 重复传参
    fields["title_display"] = fields.get("title_display") or title
    hashtags = [h.strip() for h in _HASHTAG_RE.findall(text) if h.strip()][:8]
    pics = _post_pics(mblog)
    links = _LINK_RE.findall(text)
    # 只有一条外链且没有配图时，博文很可能就是活动详情页
    detail_url = next(
        (u for u in links if "weibo.cn" not in u and "weibo.com" not in u),
        f"https://m.weibo.cn/detail/{mid}",
    )
    reposts = mblog.get("reposts_count")
    comments = mblog.get("comments_count")
    attitudes = mblog.get("attitudes_count")

    # 话题标签（#现役广州地偶图鉴#）是很好的分类信号，与 classify 的 tags 合并去重
    fields["tags"] = list(dict.fromkeys([*(fields.get("tags") or []), *hashtags]))

    # ⚠️ 实测坑：`parse_datetime` 只要文本里有「今天/今晚」就给 date_only，
    #    所以「今天天气不错，出门散步」也能过上面那道日期检查。
    #    这里再要求「场地/阵容/票价/演出词」至少命中一个，宁缺勿错。
    if not looks_like_event(fields, text):
        return None

    occurrence = ParsedOccurrence(
        **fields,
        poster_url=pics[0] if pics else None,
        description=text[:800],
        confidence=0.75,
        extra={
            "channel": "weibo_search",
            "keyword": keyword,
            "mid": str(mid),
            "author": screen_name,
            "author_uid": str(user.get("id") or ""),
            "is_retweet": bool(mblog.get("retweeted_status")),
            "created_at": created.isoformat() if created else None,
            "created_at_raw": mblog.get("created_at"),
            "pics": pics,
            "links": links[:5],
            "detail_url": detail_url,
            "reposts_count": reposts,
            "comments_count": comments,
            "attitudes_count": attitudes,
            "content_hash": content_hash(text),
        },
    )
    # 「昨天/前天」在 text_zh 里没有对应词条，会被算成今天 → 这里按博文时间回退。
    # extra_context 带上正文：标题常只是口号（「扬帆！启航！」），
    # 真正的品类信号（生诞 / ONEMAN / Only）在正文里，不带上下文会误判成 other。
    return finalize(
        fix_relative_date(occurrence, text),
        extra_context=f"{screen_name} {' '.join(hashtags)} {text[:200]}",
    )


def parse_search_payload(
    payload: dict[str, Any],
    *,
    keyword: str | None = None,
    city: str | None = None,
    now: dt.datetime | None = None,
) -> list[ParsedOccurrence]:
    """微博搜索接口 JSON → ParsedOccurrence 列表（**纯函数，可离线单测**）。

    结构（实测）：`{"ok":1,"data":{"cards":[...]}}`，博文在同层或嵌套的
    `{"card_type":9,"mblog":{...}}` 里。`ok != 1` 或结构异常时返回已解析到的部分，
    不抛异常（一条脏数据不该让整轮抓取失败）。
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("ok") not in (1, "1", None):
        return []
    out: list[ParsedOccurrence] = []
    seen: set[str] = set()
    for mblog in _iter_mblogs(payload.get("data") if payload.get("data") is not None else payload):
        occ = _post_to_occurrence(mblog, keyword=keyword, city=city, now=now)
        if occ is None or occ.external_id in seen:
            continue
        seen.add(occ.external_id)
        out.append(occ)
    return out


def discover_accounts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从搜索 payload 里发现发布账号（供 `channel` 表自动扩充）。

    返回可直接喂给 `ChannelIn` 的 dict 列表：
      `platform / channel_type / external_id / display_name / fetch_mode / url_template /
        match_pattern / city / is_idol / priority / discovered_from`

    ⚠️ `url_template` 指向**搜索页**而不是个人主页：实测个人主页强制跳登录，
    所以 channel 仍然通过搜索页取内容（这也是为什么 `fetch_mode="cdp_xhr"`）。
    """
    if not isinstance(payload, dict):
        return []
    accounts: dict[str, dict[str, Any]] = {}
    for mblog in _iter_mblogs(payload.get("data") if payload.get("data") is not None else payload):
        user = _user_of(mblog)
        uid = user.get("id")
        name = (user.get("screen_name") or "").strip()
        if not uid or not name:
            continue
        key = str(uid)
        if key in accounts:
            continue
        accounts[key] = {
            "platform": PLATFORM,
            "channel_type": "idol_group",
            "external_id": f"uid:{uid}",
            "display_name": name,
            # 个人主页不可用 → 仍然走搜索页
            "fetch_mode": "cdp_xhr",
            "url_template": SEARCH_URL,
            "match_pattern": SEARCH_XHR_MATCH,
            "city": text_zh.parse_city(name),
            "is_idol": True,
            "priority": 120,
            "discovered_from": "weibo_search",
        }
    return list(accounts.values())


# --------------------------------------------------------------------------- #
# 浏览器通道（搜索页渲染 + XHR 拦截）
# --------------------------------------------------------------------------- #

def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "scripts"


def search_url(keyword: str) -> str:
    """关键词 → 搜索页 URL。

    ⚠️ 实测坑：`containerid` 的值是 `100103type=1&q=<关键词>`，它作为**一个查询参数值**
    必须整体 URL 编码（`%3D` / `%26`），而且只能编码一次 ——
    实测把已编码的串再编码一次（`containerid=100103type%3D1%26q%3D100103type%3D1%26q%3D…`）
    页面照常返回 `ok:1`，但 `cards` 是空的（**静默失败**，不报错）。
    """
    container = f"{SEARCH_CONTAINER_PREFIX}{keyword}"
    return SEARCH_URL.format(kw=quote(container, safe=""))


def capture_search_xhr(
    keyword: str,
    *,
    wait: float = 16.0,
    ua: str = MOBILE_UA,
) -> list[dict[str, Any]]:
    """CDP 打开微博搜索页，拦截页面自己发出的 `/api/container` 响应。

    ⚠️ 为什么不用纯 HTTP：实测 `m.weibo.cn/api/container/getIndex` 直连返回 432 / `ok=-1`。
    页面自己请求时带着它自己的 cookie/指纹，所以能拿到 185KB 干净 JSON。

    ⚠️ 实测坑：`scripts/cdp_xhr.capture_xhr` 在**收到响应事件的那一刻**就调
    `Network.getResponseBody`，而搜索页此时还在加载 → 实测报
    `-32000 No data found for resource with given identifier`（响应体还没落地/已丢弃），
    结果一个都没拿到。这里改成自己实现的两段式：
      ① 监听阶段只登记命中的 requestId；
      ② 页面静下来之后再取 body（带重试）。
    """
    scripts = str(_scripts_dir())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from cdp_fetch import Browser  # type: ignore  # 仅标准库实现

    url = search_url(keyword)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    br = Browser(headless=True, width=430, height=932, ua=ua)
    try:
        br.cmd("Page.enable")
        br.cmd("Runtime.enable")
        br.cmd("Network.enable")
        # 必须在 navigate 之前 Network.enable，否则会漏掉首个请求（docs §7 注意 2）
        br.cmd("Page.navigate", {"url": url})

        # ① 只登记，不取 body
        deadline = time.time() + max(6.0, wait)
        scrolled = False
        while time.time() < deadline:
            try:
                msg = json.loads(br.ws.recv())  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001  超时/关闭 → 结束本轮
                break
            if msg.get("method") == "Network.responseReceived":
                params = msg.get("params") or {}
                response = params.get("response") or {}
                rurl = response.get("url") or ""
                rid = params.get("requestId")
                if SEARCH_XHR_MATCH in rurl and rid and rid not in seen:
                    seen.add(rid)
                    hits.append({
                        "url": rurl,
                        "status": response.get("status"),
                        "request_id": rid,
                        "body": "",
                    })
            # 懒加载列表必须先滚动再取 DOM/继续拦（docs §7 注意 3）
            if not scrolled and time.time() > deadline - max(4.0, wait * 0.4):
                scrolled = True
                try:
                    br.cmd("Runtime.evaluate", {
                        "expression": "window.scrollTo(0, document.body.scrollHeight)",
                        "returnByValue": True,
                    })
                except Exception:  # noqa: BLE001
                    pass

        # ② 页面静下来后取 body（带重试）
        for hit in hits:
            rid = hit["request_id"]
            for attempt in range(3):
                try:
                    body = br.cmd("Network.getResponseBody", {"requestId": rid})
                    hit["body"] = body.get("body", "") or ""
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt + 1 >= 3:
                        log.debug("微博取响应体失败 %s: %s", hit["url"][:80], exc)
                    else:
                        time.sleep(0.6)
        log.info("微博搜索「%s」：拦截到 %d 个响应", keyword, len(hits))
    except Exception as exc:  # noqa: BLE001
        # ⚠️ 实测坑：CDP 的 WebSocket 可能中途断开（`sock.sendall` 直接抛异常），
        # 这不该让整轮采集崩掉 —— 返回已经拦到的部分。
        log.warning("微博搜索「%s」：CDP 会话中断（%s），返回已拦到的 %d 个响应",
                    keyword, exc, len(hits))
    finally:
        try:
            br.close()
        except Exception:  # noqa: BLE001
            pass
    return hits


def payloads_from_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """拦截结果 → JSON payload 列表（跳过取 body 失败/非 JSON 的响应）。"""
    out: list[dict[str, Any]] = []
    for hit in hits or []:
        try:
            payload = json.loads(hit.get("body") or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


# --------------------------------------------------------------------------- #
# 采集器
# --------------------------------------------------------------------------- #

class WeiboCollector:
    """微博采集器（搜索页 + XHR 拦截）。

    与调度层（`app/pipeline.py`）的约定和 `ShowstartCollector` 一致：

        occ = await WeiboCollector().collect(browser, cities=["广州", "深圳"])

    也可以自己管理生命周期 / 直接跑某个关键词：

        async with WeiboCollector() as c:
            occ = await c.collect_search("广州地偶", city="广州")
            accounts = await c.discover(SEARCH_KEYWORDS)   # 供 channel 表扩充

    纯函数 `parse_search_payload` / `discover_accounts` / `strip_html` 不需要浏览器。
    """

    source_code = SOURCE_CODE
    keywords = SEARCH_KEYWORDS

    def __init__(
        self,
        *,
        browser: BrowserFetcher | None = None,
        http: HttpFetcher | None = None,
        ua: str = MOBILE_UA,
        wait: float = 16.0,
    ) -> None:
        self.browser = browser
        self.http = http
        self._own_http = http is None
        self.ua = ua
        self.wait = wait

    async def __aenter__(self) -> WeiboCollector:
        if self.http is None:
            self.http = await HttpFetcher(user_agent=self.ua).__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self.http is not None and self._own_http:
            await self.http.__aexit__(None, None, None)
            self.http = None

    async def collect(
        self,
        browser: BrowserFetcher | None = None,
        cities: list[str] | None = None,
        *,
        keywords: list[str] | None = None,
        wait: float | None = None,
    ) -> list[ParsedOccurrence]:
        """调度层入口：跑关键词表并去重。

        `browser` 只是调用约定（真实抓取走 `capture_search_xhr`，它自己起一个
        移动端尺寸的 CDP 会话）；传入也不会被本方法关闭。
        ⚠️ 实测：个人主页 `m.weibo.cn/u/{uid}` 强制跳登录，纯 HTTP 打
        `/api/container` 返回 432，所以**唯一**可行路径就是搜索页 + XHR 拦截。

        ⚠️ 为什么**不**按城市再乘一遍关键词：`SEARCH_KEYWORDS` 里每个词已经自带城市
        （「广州地偶」「深圳地偶」…），再乘一遍城市会把 CDP 会话数放大 5 倍
        （每个搜索页 ≈33s，实测），却拿到几乎相同的博文。
        `cities` 只用作字段解析的兜底城市。
        """
        if browser is not None:
            self.browser = browser
        default_city = (cities or [None])[0]
        wait = self.wait if wait is None else wait
        out: list[ParsedOccurrence] = []
        seen: set[str] = set()
        for occ in await self.collect_all(keywords, city=default_city, wait=wait):
            if occ.external_id in seen:
                continue
            seen.add(occ.external_id)
            out.append(occ)
        log.info("微博合计 %d 条（已去重）", len(out))
        return out

    async def collect_search(
        self,
        keyword: str,
        city: str | None = None,
        *,
        wait: float | None = None,
        now: dt.datetime | None = None,
    ) -> list[ParsedOccurrence]:
        """单个关键词：渲染搜索页 + 拦截 → ParsedOccurrence 列表。"""
        payloads, _ = await self._fetch(keyword, wait=self.wait if wait is None else wait)
        out: list[ParsedOccurrence] = []
        seen: set[str] = set()
        for payload in payloads:
            for occ in parse_search_payload(payload, keyword=keyword, city=city, now=now):
                if occ.external_id in seen:
                    continue
                seen.add(occ.external_id)
                out.append(occ)
        return out

    async def collect_all(
        self,
        keywords: list[str] | None = None,
        *,
        city: str | None = None,
        wait: float | None = None,
    ) -> list[ParsedOccurrence]:
        """跑整张关键词表（多词结果按 external_id 去重）。"""
        wait = self.wait if wait is None else wait
        out: list[ParsedOccurrence] = []
        seen: set[str] = set()
        for kw in keywords or self.keywords:
            for occ in await self.collect_search(kw, city=city, wait=wait):
                if occ.external_id in seen:
                    continue
                seen.add(occ.external_id)
                out.append(occ)
        return out

    async def discover(
        self,
        keywords: list[str] | None = None,
        *,
        wait: float | None = None,
    ) -> list[dict[str, Any]]:
        """账号发现：从多条搜索里收集发布账号（去重，可直接写 `channel`）。"""
        wait = self.wait if wait is None else wait
        accounts: dict[str, dict[str, Any]] = {}
        for kw in keywords or self.keywords:
            _, payloads = await self._fetch(kw, wait=wait)
            for payload in payloads:
                for acc in discover_accounts(payload):
                    accounts.setdefault(str(acc.get("external_id")), acc)
        log.info("微博账号发现：%d 个", len(accounts))
        return list(accounts.values())

    async def _fetch(
        self, keyword: str, *, wait: float
    ) -> tuple[list[dict[str, Any]], FetchResult]:
        """统一入口：CDP 拦截（唯一的可行路径）。"""
        try:
            hits = await asyncio.to_thread(capture_search_xhr, keyword, wait=wait, ua=self.ua)
        except Exception as exc:  # noqa: BLE001  无浏览器/被风控时不该炸掉整轮
            log.warning("微博搜索「%s」失败：%s", keyword, exc)
            return [], FetchResult(
                url=search_url(keyword), ok=False, mode="cdp_xhr", error=str(exc)
            )
        result = FetchResult(
            url=search_url(keyword),
            ok=bool(hits),
            mode="cdp_xhr",
            payload=hits,
            content_hash=content_hash([h.get("url") for h in hits]),
        )
        return payloads_from_hits(hits), result


__all__ = [
    "PLATFORM",
    "SEARCH_KEYWORDS",
    "SEARCH_URL",
    "SEARCH_XHR_MATCH",
    "SOURCE_CODE",
    "USER_URL_TEMPLATE",
    "WeiboCollector",
    "capture_search_xhr",
    "discover_accounts",
    "parse_search_payload",
    "parse_weibo_created_at",
    "payloads_from_hits",
    "search_url",
    "strip_html",
]
