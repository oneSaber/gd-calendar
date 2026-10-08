"""采集器之间共用的解析后处理。

采集器把「源特有的结构」（B 站 JSON / 微博卡片 / 渲染文本）转成 ParsedOccurrence 后，
还要做几件**跟源无关**的收尾工作。放在这里是为了让 `bilibili.py` 与 `weibo.py`
用完全相同的规则，避免两边慢慢漂移。

为什么需要收尾：`text_zh.extract_from_text` 是「尽力而为」的字段抽取器，
它的 title 取自文案首行（博文首行往往是「标题+时间+地点+票价」一长串），
而 `kind / is_idol / series_key` 都依赖 title —— 标题没收拾干净，
分类和系列键就会跟着错（实测：标题里混进「2026年8月29日16:00」会被判成 rock_live）。
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata

from app.models import ParsedOccurrence
from app.parsers import text_zh
from app.utils import get_logger

log = get_logger(__name__)

# 标题里出现这些字段标签，说明后半段是结构化字段而不是标题
_FIELD_LABEL_RE = re.compile(
    r"(?:时间|日期|地点|场地|场馆|地址|入场|票价|门票|价格|参演|出演|阵容|嘉宾|"
    r"主办|购票|扫码|联系|咨询)"
)
# 标题里夹带的日期/时间碎片
_TITLE_DATETIME_RE = re.compile(
    r"(?:20\d{2}\s*[年\-/.]\s*\d{1,2}(?:\s*[月\-/.]\s*\d{1,2})?"
    r"|\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?"
    r"|\d{1,2}\s*[:：]\s*\d{2}(?::\d{2})?)"
)

# 标题长度上限（与 text_zh.extract_from_text 的 3~90 区间一致，从严取 60）
TITLE_MAX_LEN = 60


def clean_title(raw: str, *, max_len: int = TITLE_MAX_LEN) -> str:
    """把「标题+字段」的长串收拾成一个像标题的短串。

    步骤：去装饰 emoji → 在第一个字段标签处截断 → 去掉夹带的日期时间碎片 → 收尾。
    截断后太短（说明原文只是「时间：…」这种）就返回空串，交给调用方兜底。

    ⚠️ 实测坑：地偶账号爱用 Unicode 数学粗体写标题（`𝓒𝓞𝓓𝓔_𝓩`），
    只去 emoji 是去不掉的（它在字母数字符号区），必须走 NFKC 折叠。
    折叠会改变展示字形，但**原始写法一直保留在 `title_raw`**，所以不丢信息。
    ⚠️ 注意别用 `text_zh.normalize_title`：那是给去重键用的（会把标题压成小写），
    展示标题必须保留大小写（「ONLY」不能被写成「only」）。
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", raw)
    s = text_zh.display_title(s)
    m = _FIELD_LABEL_RE.search(s)
    if m and m.start() >= 2:
        s = s[: m.start()]
    s = _TITLE_DATETIME_RE.sub(" ", s)
    # 去掉替换后残留的时间碎片（「11月14日 20:00 开演，19:00 开场」→「周六 开演， 开场」；
    # 里面的「19:」是半个时间，必须一起清掉，否则会拼出莫名其妙的标题）
    s = re.sub(r"(?<![\d:])\d{1,2}\s*[:：](?!\d)", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -—·|/、,，。;；:：")
    if len(s) < 3:
        return ""
    if len(s) > max_len:
        # 优先在标点处断开，避免把词切一半
        head = re.split(r"[｜|/·。;；,，!！?？]", s)[0].strip()
        s = head if 3 <= len(head) <= max_len else s[:max_len].strip()
    return s


def finalize(occ: ParsedOccurrence, *, extra_context: str = "") -> ParsedOccurrence:
    """按最终标题重算分类与系列键。

    ⚠️ 必须在**标题定稿之后**调用：`classify` 的 `is_idol` 与 `kind` 都吃标题，
    标题里混进日期/票价会让分类翻转（实测「Z.T IDOL PARTY 时间：… 入场：48.8」
    不含地偶词时会被判成 other）。
    """
    title = occ.title_display or occ.title_raw or ""
    cls = text_zh.classify(f"{title} {extra_context}".strip(), occ.venue_raw or "")
    # classify 只在命中的分支里给 tag；保留采集器已经加的（话题标签等）
    merged = list(dict.fromkeys([*cls.tags, *(occ.tags or [])]))
    occ.kind = cls.kind  # type: ignore[assignment]
    occ.is_idol = cls.is_idol
    occ.is_girl_band = cls.is_girl_band
    occ.is_acg = cls.is_acg
    occ.tags = merged
    # 系列键必须跟着**定稿标题**重算：`extract_from_text` 之前是按未收拾的长标题算的
    # （实测会得到 'ztidolparty时间2026年8月29日1600…' 这种垃圾系列键）
    occ.series_key, occ.series_vol = text_zh.parse_series(title)
    return occ


# ⚠️ 这两个「兜底」曾经是必需的，现在 text_zh 已原生支持，留着会**双重生效**：
#   * `_RELATIVE_DAYS_BACK`（昨天/前天）→ 解析器已回退，采集器再退一次会多退一天
#   * `_TIME_RANGE_RE`（21:40–22:40）→ 解析器已拆成 start/end，采集器再纠一次会错位
# 因此这里改成**运行时能力探测**：只有当解析器不具备该能力时才兜底。
def _text_zh_has_relative_back() -> bool:
    from app.parsers import text_zh

    return bool(getattr(text_zh, "_RELATIVE_DAYS_BACK", None))


def _text_zh_handles_time_range() -> bool:
    from app.parsers import text_zh

    return bool(getattr(text_zh, "_TIME_RANGE_RE", None))


_NEED_RELATIVE_BACK_FALLBACK = not _text_zh_has_relative_back()
_NEED_TIME_RANGE_FALLBACK = not _text_zh_handles_time_range()

_RELATIVE_DAY_BACK: tuple[tuple[str, int], ...] = (("前天", 2), ("昨天", 1))


def fix_relative_date(occ: ParsedOccurrence, text: str) -> ParsedOccurrence:
    """按文案里的「前天/昨天」把被误判成「今天」的日期往回退。

    仅当 `text_zh` 不支持过去相对日时才生效（正常情况下 text_zh 已处理）。
    """
    if not _NEED_RELATIVE_BACK_FALLBACK:
        return occ
    if not text or _EXPLICIT_DATE_RE.search(text):
        return occ
    for word, back in _RELATIVE_DAY_BACK:
        if word not in text:
            continue
        delta = dt.timedelta(days=back)
        if occ.start_at is not None:
            occ.start_at = occ.start_at - delta
        if occ.open_at is not None:
            occ.open_at = occ.open_at - delta
        if occ.end_at is not None:
            occ.end_at = occ.end_at - delta
        break
    return occ


# 时间区间（实测写法）：`21:40–22:40` / `19:30-20:30` / `21:40 — 22:40`
# ⚠️ 实测坑：`text_zh.parse_datetime` 对「2026.10.24 21:40–22:40」返回
# `start=22:40, open=21:40` —— 它把**结束**当成了开演。文案里这种
# 「开演–结束」区间必须纠正，否则用户会看到错误的开演时间。
_TIME_RANGE_RE = re.compile(
    r"(?<!\d)(?P<h1>\d{1,2})\s*[:：]\s*(?P<m1>\d{2})\s*"
    r"[-–—~～至到]\s*"
    r"(?P<h2>\d{1,2})\s*[:：]\s*(?P<m2>\d{2})(?!\d)"
)


def fix_time_range(occ: ParsedOccurrence, text: str) -> ParsedOccurrence:
    """把「21:40–22:40」这类区间纠正为 start=21:40 / end=22:40。

    仅当 `text_zh` 不支持区间拆分时才生效（正常情况下 text_zh 已正确处理，
    再纠一次反而会错位）。
    """
    if not _NEED_TIME_RANGE_FALLBACK:
        return occ
    if not text or occ.start_at is None:
        return occ
    if any(w in text for w in ("开场", "结束", "入场", "OPEN", "open", "START", "开演")):
        return occ
    m = _TIME_RANGE_RE.search(text)
    if not m:
        return occ
    try:
        h1, m1, h2, m2 = (int(m.group(k)) for k in ("h1", "m1", "h2", "m2"))
    except (TypeError, ValueError):
        return occ
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 < 60 and 0 <= m2 < 60):
        return occ
    first = m1 + h1 * 60
    second = m2 + h2 * 60
    if first >= second:
        return occ  # 不是「早→晚」，不动
    start = occ.start_at.replace(hour=h1, minute=m1, second=0)
    end = occ.start_at.replace(hour=h2, minute=m2, second=0)
    # 只有当解析结果确实是「start 取晚的那个」时才认为是同一个区间的误解析
    if occ.start_at.hour * 60 + occ.start_at.minute != second:
        return occ
    occ.start_at = start
    if occ.open_at is not None and occ.open_at.hour * 60 + occ.open_at.minute == first:
        occ.open_at = None
    if occ.end_at is None:
        occ.end_at = end
    return occ


# 演出信号词：文案里命中才算「像演出」而不是日常博文
_EVENT_HINT_RE = re.compile(
    r"公演|生诞|生誕|生日|ONEMAN|One\s?Man|Only|only|LIVE|Live|live|专场|拼盘|巡演|"
    r"演出|音乐节|地偶|偶像|特典|チェキ|乐队|乐团|Livehouse|livehouse|门票|票价|入场|"
    r"开演|开场|参演|阵容|嘉宾|同人|ACG|咖啡|酒吧|空间|剧场|剧院"
)

# 文案里**真的写了日期**（年月日 / 月日，或 08-26 这种横杠月日）。
# ⚠️ 为什么必须区分：`parse_datetime` 对「今晚/今天/昨天」也会给出日期
# （相对当前时间），而动态页/微博正文里到处都是这类词。实测 B 站动态里
# 「视频时长 04:22」会被 parse_datetime 当成开演时间，
# 结果整条推广文变成一条「2026-10-08 04:22 开演」的假场次。
_EXPLICIT_DATE_RE = re.compile(
    r"20\d{2}\s*[年\-/.]\s*\d{1,2}"
    r"|(?<!\d)\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?(?!\d)"
    r"|(?<!\d)\d{2}-\d{2}(?![\d\-])"
)


def looks_like_event(
    fields: dict,
    text: str,
    *,
    venue: str | None = None,
    artists: list | None = None,
    min_text_len: int = 0,
    require_explicit_date: bool = False,
) -> bool:
    """是否值得当成一条演出（**必须真的解析出日期**，光有「3小时前」不算）。

    为什么这么保守：`text_zh.parse_datetime` 只要文本里有「今天 / 今晚」就会给出
    `date_only`，抓时间就能给出一个时间。实测踩到的两个假场次：
      * 「今天天气不错，出门散步」→ 当天 00:00 的场次；
      * B 站动态里的视频时长「04:22」→ 当天 04:22「开演」的场次。
    所以判定规则是：
      * `require_explicit_date=True`（动态页这类「只有发布时间、没有活动日期」的源）
        → 文案里必须真写了日期（年月日 / 月日 / 08-26），否则直接丢；
      * 日期是具体日期（exact）= 强信号，有场地/阵容/票价/演出词其一即可；
      * 日期只来自相对词（date_only）= 弱信号，**必须**再有场地/阵容/票价才算数。
    """
    if fields.get("date_precision") in (None, "tbd"):
        return False
    if len(text or "") < min_text_len:
        return False
    has_detail = bool(venue or fields.get("venue_raw") or artists or fields.get("artists"))
    has_price = fields.get("price_min") is not None
    if require_explicit_date and not _EXPLICIT_DATE_RE.search(text or ""):
        return False
    if fields.get("date_precision") == "date_only" and not has_detail and not has_price:
        # 只有「今晚/今天」这类相对词 → 判定为日常博文
        return False
    if has_detail or has_price:
        return True
    return bool(_EVENT_HINT_RE.search(f"{fields.get('title_display') or ''} {text}"))


# 阵容名里不该出现的字段词 / 描述性词（实测被误抓过的）
_FIELD_WORDS = (
    "时间", "日期", "地点", "场地", "地址", "入场", "开演", "开场", "票价",
    "门票", "价格", "参演", "阵容", "购票", "扫码", "主办", "公众号", "直播",
)
_NON_ARTIST_WORDS = (
    "专场", "巡演", "拼盘", "演出", "公演", "领衔", "节目前瞻", "前瞻", "预告",
    "感谢", "大家", "今晚", "现场", "门票", "售票", "乐队们", "转发", "评论",
    "点赞", "展开", "收起", "关注", "投稿", "合集",
)


# 句子标点：出现就说明抓到的是一句话（或字段）而不是场地/艺人名
_SENTENCE_PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:…—～~\"'“”‘’（）()《》\[\]]")


def sanitize_artists(artists: list) -> list:
    """丢掉明显不是阵容名的抽取结果（**两个采集器共用**）。

    ⚠️ 实测坑：`text_zh.parse_lineup` 在没有「参演/阵容」标签时会退化成
    「按空格切开整段」，于是一整段推广文案会被切成
    `['暗含着流动的诗意', '内页里则这样写道:人们或许可以将这张录音称为魔法', …]`
    这种垃圾阵容。脏阵容比空阵容危害大得多（污染 artist 表与去重指纹），
    所以这里逐条过滤：长度、空格、句子标点、字段词、描述性词、纯数字开头。
    """
    out: list = []
    seen: set[str] = set()
    for artist in artists or []:
        name = (getattr(artist, "name_raw", "") or "").strip()
        if not (2 <= len(name) <= 30):
            continue
        if "@" in name or "://" in name:
            continue
        # 名字里不会有句子标点（带标点的是被切碎的正文）
        if _SENTENCE_PUNCT_RE.search(name):
            continue
        # 中文名里不该带空格（带空格的是被切碎的整句）；纯拉丁名可以带空格
        if " " in name and len(name) > 3 and not re.fullmatch(r"[A-Za-z0-9 .\-_&']+", name):
            continue
        if re.match(r"^(?:20\d{2}|\d{1,4}\s*[年月日元]|\d{1,2}:\d{2})", name):
            continue
        if any(w in name for w in _FIELD_WORDS):
            continue
        if any(w in name for w in _NON_ARTIST_WORDS):
            continue
        # 中文名不会太长（实测正文长句片段会落进 15~30 字这个区间）
        if len(name) > 15 and not re.search(r"[A-Za-z_]", name):
            continue
        norm = getattr(artist, "name_norm", None) or text_zh.normalize_name(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(artist.model_copy(update={"name_norm": norm, "billing_order": len(out) + 1}))
    return out


# 场地名里的通用类型词（用于判断「这一小段像不像场地」）
_VENUE_WORD_RE = re.compile(
    r"livehouse|live\s*house|live\s*bar|club|bar|空间|现场|剧场|剧院|场馆|中心|"
    r"音乐厅|艺术馆|美术馆|酒馆|咖啡|书店|广场|公园|仓库|仓|展演|演艺",
    re.I,
)


def looks_like_venue_piece(piece: str) -> bool:
    """判断一小段文本像不像场地名（供「没有地点标签」时的兜底使用）。

    ⚠️ 实测坑：`text_zh.looks_like_venue` 太宽（「有字母且 ≤24 字」就算），
    在动态正文里会把「流动的诗意:Joëlle」「13th」这类片段当场地。
    这里再加硬条件：不含句子标点、含场地类型词，或「够长 + 有字母」的
    短专名（要求**至少 4 字符且有字母**，把「13th」这种视频标题片段挡掉）。
    """
    if not piece:
        return False
    s = piece.strip()
    if not (4 <= len(s) <= 24):
        return False
    if _SENTENCE_PUNCT_RE.search(s):
        return False
    if _VENUE_WORD_RE.search(s):
        return True
    # 「13th」「1st」这类是视频标题片段（序数词），不是场地
    if re.match(r"^\d{1,3}\s*(?:st|nd|rd|th)\b", s, re.I):
        return False
    # 纯拉丁/数字的短专名（如「191space」「MAO Livehouse」）也算
    return bool(re.search(r"[A-Za-z]", s) and re.fullmatch(r"[A-Za-z0-9&'\- ]+", s))


__all__ = [
    "TITLE_MAX_LEN",
    "clean_title",
    "finalize",
    "fix_relative_date",
    "fix_time_range",
    "looks_like_event",
    "looks_like_venue_piece",
    "sanitize_artists",
]
