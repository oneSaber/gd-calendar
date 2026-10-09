"""中文演出文案解析器 —— 本项目的核心技术资产。

把微博博文 / 场地动态 / 票务页脚注这类自由文本，抽成 ParsedOccurrence。

设计原则：
  1. **抽不到就留空**，绝不猜测（宁可 tbd 也不要错）。
  2. 每个字段带置信度，低置信度进人工队列。
  3. 全部规则都有实测样本支撑（见 docs/02-采集方案-全自动.md §5.1）。

实测踩过的坑（都有对应用例）：
  * 「入场：48.8 1D」里的 1D 是票种代号（1 Drink），不是价格 → 必须按票种语法解析。
  * 「¥0.01 普通票」是无料占位票 → is_placeholder，且归入「无料」。
  * 标题里有「Vol.33.0」这种小数期号 → series_vol 必须是 TEXT。
  * 标题里有全角冒号「红白色乐队：深夜频道」→ 不能用冒号切标题。
  * SYNAPSE 系列用 Unicode 数学粗体（𝗦𝗬𝗡𝗔𝗣𝗦𝗘）→ 归一化要能剥掉。
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Iterable

from dateutil import parser as dateparser

from app.models import ArtistIn, TicketIn

CST = dt.timezone(dt.timedelta(hours=8))

# 广东城市（可扩展）
GUANGDONG_CITIES = (
    "广州", "深圳", "佛山", "东莞", "珠海", "中山", "惠州", "汕头",
    "江门", "肇庆", "湛江", "茂名", "清远", "韶关", "梅州", "潮州",
    "揭阳", "阳江", "河源", "云浮", "汕尾",
)
CITY_ALIASES = {
    "羊城": "广州", "穗": "广州", "鹏城": "深圳", "禅城": "佛山",
    "莞城": "东莞", "香洲": "珠海",
}

VENUE_TYPE_WORDS = (
    "livehouse", "live house", "live bar", "展演中心", "演艺中心",
    "音乐现场", "艺术空间", "空间", "剧场", "剧院", "场馆", "俱乐部",
    "club", "bar", "酒馆", "咖啡", "书店", "广场", "公园", "仓",
)

# --------------------------------------------------------------------------- #
# 文本归一化
# --------------------------------------------------------------------------- #

_EMOJI_PATTERNS = [
    r"[\U0001F000-\U0001FAFF]",          # 各类 emoji 主区（含 🧱 U+1F9F1）
    r"[\U00002600-\U000027BF]",          # 杂项符号 + 装饰符（含 ➕ U+2795）
    r"[\U0000FE00-\U0000FE0F]",          # 变体选择符
    r"[\U0001F1E6-\U0001F1FF]",          # 区域指示符（国旗）
    r"[\U00002190-\U000021FF]",          # 箭头
    r"[\U00002B00-\U00002BFF]",          # 杂项符号与箭头
    r"[\U00002000-\U0000206F]",          # 通用标点（含零宽）
    r"[\U0000E000-\U0000F8FF]",          # 私用区
    r"[\U000023E9-\U000023FA]",          # 媒体控制符号
]
_EMOJI_RE = re.compile("|".join(_EMOJI_PATTERNS))
_ZW_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_WS_RE = re.compile(r"\s+")

# 装饰性字符（注意：不要包含 + ＋ 这类语义字符，避免误伤「VIP+」）
_DECOR_CHARS = "★☆♪♬✦✧◆◇○●◎※§¶†‡"
_DECOR_LEAD_RE = re.compile(rf"^[\s\-—–·•*#=~～>»「『【\[（(◇◆{_DECOR_CHARS}]+")
_DECOR_TAIL_RE = re.compile(rf"[\s\-—–·•*#=~～<«」』】\]）)◇◆{_DECOR_CHARS}]+$")

# 全角 → 半角
_FULLWIDTH_MAP = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}
_IDEOGRAPHIC_SPACE_RE = re.compile(r"[\u3000\u00a0]")


def strip_emoji(text: str) -> str:
    """去掉 emoji、零宽字符与表意空格。"""
    if not text:
        return ""
    out = _ZW_RE.sub("", _EMOJI_RE.sub("", text))
    return _IDEOGRAPHIC_SPACE_RE.sub(" ", out)


def strip_decor(text: str) -> str:
    """去掉首尾装饰符与多余空白（保留 VIP+ 这类语义加号）。"""
    if not text:
        return ""
    out = text
    for _ in range(3):
        out = _DECOR_TAIL_RE.sub("", _DECOR_LEAD_RE.sub("", out.strip()))
    return _WS_RE.sub(" ", out).strip()


def to_halfwidth(text: str) -> str:
    """全角转半角（保留中日文）。"""
    if not text:
        return ""
    return "".join(chr(_FULLWIDTH_MAP.get(ord(c), ord(c))) for c in text)


# 语义标点归一化：这些符号**带含义**（时间区间、分隔符），
# 但它们的码位落在 emoji 的「通用标点」区间里，会被 strip_emoji 误删。
# 必须先把它们换成 ASCII 等价物，再做 emoji 清理。
# 实测踩坑：`21:40–22:40` 里的 en-dash 被删掉后变成 `21:4022:40`，区间再也匹配不到。
_PUNCT_NORMALIZE = {
    "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2212": "-", "\u2010": "-", "\u2011": "-",
    "\uFF5E": "~", "\u301C": "~",
    "\uFF0B": "+",
    "\uFF0F": "/", "\uFF3C": "\\",
    "\uFF06": "&", "\uFF5C": "|",
    "\u00D7": "x", "\u2715": "x",
    "\u2022": " ", "\u00B7": " ",
}
_PUNCT_RE = re.compile("|".join(map(re.escape, _PUNCT_NORMALIZE)))


def normalize_punct(text: str) -> str:
    """把带语义的 Unicode 标点换成 ASCII，避免被 emoji 清理误删。"""
    if not text:
        return ""
    return _PUNCT_RE.sub(lambda m: _PUNCT_NORMALIZE[m.group(0)], text)


def clean_text(text: str) -> str:
    """标准清洗顺序：**先标点归一化 → 再 strip_emoji → 最后 to_halfwidth**。

    顺序很重要：
      1. 标点归一化：保住 en-dash/全角加号等语义符号
      2. strip_emoji：去掉真 emoji 与零宽字符
      3. to_halfwidth：全角转半角
    """
    if not text:
        return ""
    return to_halfwidth(strip_emoji(normalize_punct(text)))


def normalize_name(name: str) -> str:
    """实体名归一化：用于匹配与唯一键。

    'MAO Livehouse广州' → 'maolivehouse广州'
    '𝗦𝗬𝗡𝗔𝗣𝗦𝗘'         → 'synapse'

    注意：NFKC 会把全角括号折成半角、全角加号折成 '+'，这是**期望行为**
    （用来消除同一个场地的不同排版写法）。展示用的原名存在 name_raw / aliases。
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)   # Unicode 数学粗体在这里被折叠
    s = to_halfwidth(s)
    s = strip_emoji(s).lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    # 先去掉括注（NFKC 已把全角括号折成半角），再去标点
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    # 标点里包含阵容分隔符（/ × & ＋ |），这样「体熊专科/JASON KUI」与
    # 「体熊专科 / Jason Kui」会归一化成同一个键，避免产生重复艺人
    s = re.sub(r"[!-/:-@\[-`{-~．。、，,·・×✕＆|｜＋_—–]+", "", s)
    return s.strip()


def normalize_title(title: str) -> str:
    """标题归一化：用于跨平台去重（保留中日文，去轮次标记）。"""
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", title)
    s = clean_text(s)
    s = strip_decor(s)
    s = re.sub(r"(?i)\b(vol|volume|no)\.?\s*\d+(\.\d+)?\b", "", s)
    s = re.sub(r"第\s*[0-9一二三四五六七八九十]+\s*[期弹回届]", "", s)
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[\"'“”‘’《》〈〉【】\[\]（）()!-/:-@{-~．。、，,·・|]+", "", s)
    return s.strip().lower()


def display_title(title: str) -> str:
    """给人看的标题：去装饰 emoji，但保留文字与原有括号内容。"""
    if not title:
        return ""
    return strip_decor(strip_emoji(title))


# --------------------------------------------------------------------------- #
# 时间解析
# --------------------------------------------------------------------------- #

_DATE_PATTERNS = [
    # 2026年8月29日 / 2026-08-29 / 2026.09.24 / 2026/03/06
    re.compile(r"(?P<y>20\d{2})\s*[年\-/.]\s*(?P<m>\d{1,2})\s*[月\-/.]\s*(?P<d>\d{1,2})\s*日?"),
    # 8月29日 / 8/29 / 08-29（无年份 → 补未来最近一次）
    re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*[月\-/.]\s*(?P<d>\d{1,2})\s*日?(?!\d)"),
]

_WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
}

_RELATIVE_DAYS = {
    "今天": 0, "今日": 0, "今晚": 0, "本日": 0,
    "明天": 1, "明日": 1, "明晚": 1,
    "后天": 2, "后晚": 2,
    "大后天": 3,
}
# ⚠️ 过去日也必须支持：微博里「昨天 20:00 见」非常常见。
# 若只认未来日，parse_datetime 会落进 `date is None and times → 今天` 的兜底，
# 把昨天的活动算成今天（实测：now=08-25 09:12 时「昨天 20:00」→ 08-25 20:00，应为 08-24）。
_RELATIVE_DAYS_BACK = {
    "昨天": 1, "昨日": 1, "昨晚": 1,
    "前天": 2, "前日": 2,
    "大前天": 3,
}

_TIME_LABELS = {
    "open": ("开场", "入场", "OPEN", "open", "Open", "DOOR", "door", "票检", "检票"),
    "start": ("开演", "开始", "START", "start", "Start", "开唱", "演出开始"),
}

# 时间：19:30 / 19：30 / 19点30 / 19点 / 晚8点 / 晚上8点半
# ⚠️ 关键：结尾加 (?!日)，否则「2026年10月31日」里的 31 会被当成「31点」
_TIME_RE = re.compile(
    r"(?:(?P<ampm>上午|中午|下午|傍晚|晚上|晚|凌晨|早上|早)\s*)?"
    r"(?P<h>\d{1,2})\s*(?::|：|点)\s*(?P<mi>\d{1,2}|半)?\s*分?(?!日)"
)

# 时间区间：21:40–22:40 / 21:40~22:40 / 21:40-22:40 / 21:40 至 22:40
# 第二个时间是**结束**时间，必须拆出来当 end_at，不能当开演。
_TIME_RANGE_RE = re.compile(
    r"(?:(?P<ap1>上午|中午|下午|傍晚|晚上|晚|凌晨|早上|早)\s*)?"
    r"(?P<h1>\d{1,2})\s*[:：]\s*(?P<m1>\d{1,2})"
    r"\s*(?:[-–—~～]|至|到)\s*"
    # t2 是整个「第二个时间」的跨度：用于把结束时间从「取最晚时间为开演」里排除
    r"(?P<t2>"
    r"(?:(?P<ap2>上午|中午|下午|傍晚|晚上|晚|凌晨|早上|早)\s*)?"
    r"(?P<h2>\d{1,2})\s*[:：]\s*(?P<m2>\d{1,2})"
    r")"
)


def _to_min(raw: str | None) -> int:
    if raw in (None, ""):
        return 0
    if raw == "半":
        return 30
    try:
        return int(raw)
    except ValueError:
        return 0


def _apply_ampm(hour: int, ampm: str | None) -> int:
    if not ampm:
        return hour
    if ampm in ("下午", "傍晚", "晚上", "晚") and hour < 12:
        return hour + 12
    if ampm == "中午" and hour < 12:
        return hour + 12 if hour < 3 else hour
    if ampm in ("凌晨", "早上", "早", "上午") and hour == 12:
        return 0
    return hour


def _fix_year(month: int, day: int, now: dt.datetime) -> int:
    """缺年份时补「未来最近一次」：若今年该日期已过（留 1 天缓冲），用明年。"""
    year = now.year
    try:
        candidate = dt.datetime(year, month, day, tzinfo=CST)
    except ValueError:
        return year
    if candidate.date() < (now - dt.timedelta(days=1)).date():
        year += 1
    return year


def parse_datetime(
    text: str, now: dt.datetime | None = None
) -> tuple[dt.datetime | None, dt.datetime | None, str, dt.datetime | None]:
    """从文本里解析 (开演时间, 开场时间, 精度, 结束时间)。

    优先级：显式年份 > 隐式月日 > 相对日（今天/明天/昨天…）> 周几。
    """
    if not text:
        return None, None, "tbd", None
    now = now or dt.datetime.now(CST)
    clean = clean_text(text)

    date: dt.date | None = None
    precision = "tbd"
    end_at: dt.datetime | None = None

    m = _DATE_PATTERNS[0].search(clean)
    if m:
        try:
            date = dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
            precision = "date_only"
        except ValueError:
            date = None
    if date is None:
        m = _DATE_PATTERNS[1].search(clean)
        if m:
            mo, d = int(m.group("m")), int(m.group("d"))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                try:
                    date = dt.date(_fix_year(mo, d, now), mo, d)
                    precision = "date_only"
                except ValueError:
                    date = None
    if date is None:
        for word, delta in _RELATIVE_DAYS.items():
            if word in clean:
                date = (now + dt.timedelta(days=delta)).date()
                precision = "date_only"
                break
    if date is None:
        for word, delta in _RELATIVE_DAYS_BACK.items():
            if word in clean:
                date = (now - dt.timedelta(days=delta)).date()
                precision = "date_only"
                break
    if date is None:
        m = re.search(r"(?:周|星期|礼拜)\s*([一二三四五六日天])", clean)
        if m:
            target = _WEEKDAY_MAP[m.group(1)]
            delta = (target - now.weekday()) % 7
            if delta == 0:
                delta = 7
            date = (now + dt.timedelta(days=delta)).date()
            precision = "date_only"

    # 时间
    open_at = start_at = None
    times: list[tuple[int, int]] = []
    for tm in _TIME_RE.finditer(clean):
        try:
            h = int(tm.group("h"))
        except (TypeError, ValueError):
            continue
        # ⚠️ 关键：日期里的数字会被误当小时（如「10月31日」→ h=31），必须丢弃
        if not (0 <= h <= 23):
            continue
        mi_raw = tm.group("mi")
        if mi_raw == "半":
            mi = 30
        elif mi_raw is None:
            mi = 0
        else:
            try:
                mi = int(mi_raw)
            except ValueError:
                continue
        if not (0 <= mi < 60):
            continue
        h = _apply_ampm(h, tm.group("ampm"))
        if h > 23:
            h -= 24
        times.append((h, mi))

    # 时间区间「21:40–22:40」「21:40~22:40」：第二个时间是**结束**，
    # 不能参与「取最晚时间为开演」的推断（否则会把结束当开演）。
    range_spans: list[tuple[int, int]] = []
    range_specs: list[tuple[int, int, int, int]] = []
    for rm in _TIME_RANGE_RE.finditer(clean):
        h1, m1 = int(rm.group("h1")), _to_min(rm.group("m1"))
        h2, m2 = int(rm.group("h2")), _to_min(rm.group("m2"))
        if not (0 <= h1 <= 23 and 0 <= h2 <= 23):
            continue
        h1 = _apply_ampm(h1, rm.group("ap1"))
        h2 = _apply_ampm(h2, rm.group("ap2"))
        if h2 <= h1:
            h2 = min(h1 + 1, 23)
        range_spans.append(rm.span())
        range_specs.append((rm.start(), rm.end(), h2, m2))
        range_spans.append(rm.span("t2"))

    # 只有时间没有日期时，按「今天」处理（微博常见：「今晚 19:00 开演」）
    if date is None and times:
        date = now.date()

    if times and date:
        # 优先找带标签的时间。
        # ⚠️ 关键：标签的时间通常在标签**之前**（「19:00 开场」），
        #    所以要从标签位置**向前**找最近的时间；向后找会把下一个时间吞进来
        #    （「19:00 开场 / 20:00 开演」中「开场」之后是 20:00，会污染开场时间）。
        def _time_span_for_label(
            text: str, label: str, day: dt.date
        ) -> tuple[int, int, dt.datetime] | None:
            """定位标签对应的时间：返回 (起点, 终点, 时间)，供调用方屏蔽已认领区间。

            规则：只有**紧邻**标签之后的时间才属于该标签（如「OPEN 18:30」「开场：19:30」），
            否则取标签**之前**最近的时间。
            反例（写成「取全局最近」会踩的坑）：
              「19:00 开场 / 20:00 开演」中「开场」离 20:00 更近，全局最近会选错。
            """
            def _mk(tm: re.Match[str]) -> dt.datetime | None:
                try:
                    hh = int(tm.group("h"))
                except (TypeError, ValueError):
                    return None
                if not (0 <= hh <= 23):
                    return None
                mi_r = tm.group("mi")
                if mi_r == "半":
                    mm_ = 30
                elif mi_r is None:
                    mm_ = 0
                else:
                    try:
                        mm_ = int(mi_r)
                    except ValueError:
                        return None
                if not (0 <= mm_ < 60):
                    return None
                hh = _apply_ampm(hh, tm.group("ampm"))
                if hh > 23:
                    hh -= 24
                return dt.datetime.combine(day, dt.time(hh, mm_), tzinfo=CST)

            def _nearest(seg: str, base: int, offset: int) -> tuple[int, int, dt.datetime] | None:
                best = None
                for tm in _TIME_RE.finditer(seg):
                    v = _mk(tm)
                    if v is None:
                        continue
                    dist = abs(base - tm.start())
                    if best is None or dist < best[0]:
                        best = (dist, offset + tm.start(), offset + tm.end(), v)
                return best

            other_positions: list[int] = []
            for other, other_labels in _TIME_LABELS.items():
                for ol in other_labels:
                    if ol == label:
                        continue
                    p = text.find(ol)
                    if p >= 0:
                        other_positions.append(p)

            for lm in re.finditer(re.escape(label), text):
                tail_start = lm.end()
                gap = text[tail_start: tail_start + 2]
                if re.fullmatch(r"\s*[:：]?\s*", gap):
                    tail_end = min(len(text), tail_start + 16)
                    for p in other_positions:
                        if tail_start <= p < tail_end:
                            tail_end = p
                    fwd = _nearest(text[tail_start: tail_end], 0, tail_start)
                    if fwd is not None:
                        return fwd[1], fwd[2], fwd[3]

                head_start = max(0, lm.start() - 24)
                head = text[head_start: lm.start()]
                back = _nearest(head, len(head), head_start)
                if back is not None:
                    return back[1], back[2], back[3]

                # 兜底：向后找一个
                tail_end = min(len(text), tail_start + 16)
                for p in other_positions:
                    if tail_start <= p < tail_end:
                        tail_end = p
                fwd = _nearest(text[tail_start: tail_end], 0, tail_start)
                if fwd is not None:
                    return fwd[1], fwd[2], fwd[3]
            return None

        # 显式标签（开演/开场/START/OPEN）优先于「取最晚时间」的兜底。
        # 关键：**已被某个标签认领的时间要从文本里屏蔽掉**，否则
        #       「OPEN 18:30 / START 19:30」里 START 会去前面抓到 18:30。
        start_labeled = False
        work = clean
        for kind, labels in _TIME_LABELS.items():
            for label in labels:
                if label not in work:
                    continue
                span = _time_span_for_label(work, label, date)
                if span is None:
                    continue
                s, e, val = span
                if kind == "open":
                    if open_at is None:
                        open_at = val
                else:  # start：显式开演标签可以覆盖兜底值
                    start_at = val
                    start_labeled = True
                work = work[:s] + " " * (e - s) + work[e:]

        if not start_labeled:
            # 没有显式「开演」标签：取**未被认领**的时间里最晚的那个作为开演。
            # ⚠️ 不能回退到 `times`：区间「21:40-22:40」里两个时间都被区间认领了，
            #    回退会把**结束**时间（22:40）当成开演。此时应取区间**起点**。
            rest = [
                (int(m.group("h")), _to_min(m.group("mi")))
                for m in _TIME_RE.finditer(work)
                if m.group("mi") != "半"
                and 0 <= int(m.group("h")) <= 23
                and not any(m.start() >= rs and m.end() <= re_ for rs, re_ in range_spans)
            ]
            if rest:
                h, mi = max(rest)
            elif range_specs:
                # 全部时间都属于某个区间 → 用区间起点（第一个区间的第一个时间）
                h = int(_TIME_RE.search(clean[range_specs[0][0]: range_specs[0][1]]).group("h"))
                mi = _to_min(_TIME_RE.search(clean[range_specs[0][0]: range_specs[0][1]]).group("mi"))
            else:
                h, mi = max(times)
            start_at = dt.datetime.combine(date, dt.time(h, mi), tzinfo=CST)
        if open_at is None and len(times) >= 2:
            h, mi = min(times)
            candidate = dt.datetime.combine(date, dt.time(h, mi), tzinfo=CST)
            if candidate < start_at:
                open_at = candidate
        precision = "exact"

        # 结束时间：显式区间「21:40–22:40 / 21:40~22:40」里的第二个时间
        for _rs, _re, hh, mm_ in range_specs:
            cand = dt.datetime.combine(date, dt.time(hh, mm_), tzinfo=CST)
            if cand > start_at:
                end_at = cand
                break
    elif date:
        # 只有日期没有时间：起点落到当天 00:00，并用 date_precision 表达「精度只到天」
        start_at = dt.datetime.combine(date, dt.time(0, 0), tzinfo=CST)
        precision = "date_only"

    # 不变式：开场一定早于开演
    if open_at and start_at and open_at > start_at:
        open_at, start_at = start_at, open_at

    return start_at, open_at, precision, end_at


def parse_iso_datetime(text: str) -> dt.datetime | None:
    """解析 ISO8601（豆瓣 RSS / 秀动详情页用）。"""
    if not text:
        return None
    try:
        val = dateparser.parse(text)
    except (ValueError, OverflowError, TypeError):
        return None
    if val is None:
        return None
    if val.tzinfo is None:
        val = val.replace(tzinfo=CST)
    return val


# --------------------------------------------------------------------------- #
# 价格解析
# --------------------------------------------------------------------------- #

_FREE_WORDS = ("无料", "免费", "免费入场", "免票", "0元", "零元", "无料入场")

_CURRENCY = r"[¥￥]"

# 价格里**不允许**当票种标签的词（日期/时间/地点等字段名）
_PRICE_LABEL_BLOCK = (
    "时间", "日期", "地点", "场地", "地址", "年", "月", "日", "号",
    "开场", "开演", "电话", "扫码", "微信", "公众号", "地铁", "楼", "层",
)
# 明确的票价引导词
_PRICE_LEAD_WORDS = (
    "票价", "价格", "门票", "入场", "通票", "预售", "现场", "早鸟", "盲鸟",
    "全价", "当日", "学生", "双人", "优享", "adv", "door", "vip", "vvip",
    "普通票", "特典券", "チェキ", "拍立得", "a票", "b票", "元",
)


def _label_ok(label: str | None, amount: str, text_after: str) -> bool:
    """判断「标签+数字」是否真的是价格。"""
    low = (label or "").lower().strip()
    # 「2026年」「8月」「29日」这类是日期
    if low in ("年", "月", "日", "号"):
        return False
    if any(b in low for b in _PRICE_LABEL_BLOCK):
        return False
    # 数字后面紧跟「:」说明是时间（16:00）
    if text_after.startswith(":") or text_after.startswith("："):
        return False
    # 数字后面紧跟「月/日/年」说明是日期
    if text_after[:1] in ("月", "日", "年", "号"):
        return False
    # 带币种、带「元/起」、或标签明显是票价词 → 通过
    if detect_tier_type(label) is not None:
        return True
    if any(w in low for w in _PRICE_LEAD_WORDS):
        return True
    return False

# 票种关键词（顺序敏感：VIP+ 要在 VIP 之前，通票要在具体票种之后兜底）
_TIER_KEYWORDS: list[tuple[str, str]] = [
    ("vip+", "VIP"), ("vvip", "VIP"), ("vip", "VIP"),
    ("早鸟", "早鸟"), ("盲鸟", "盲鸟"), ("学生", "学生"), ("双人", "双人"),
    ("两日通票", "通票"), ("通票", "通票"), ("优享", "优享"), ("pro", "PRO"),
    ("预售", "预售"), ("adv", "预售"), ("advance", "预售"),
    ("现场", "现场"), ("door", "现场"), ("全价", "现场"), ("当日", "现场"),
    ("特典券", "特典券"), ("チェキ", "チェキ"), ("拍立得", "チェキ"),
    ("普通票", "普通"), ("普通", "普通"), ("a票", "A票"), ("b票", "B票"),
    ("无料", "无料"), ("免费", "无料"),
]


def detect_tier_type(label: str | None) -> str | None:
    """把票种原文映射为标准枚举。"""
    if not label:
        return None
    low = label.lower().strip()
    if not low:
        return None
    for kw, tier in _TIER_KEYWORDS:
        if kw in low:
            return tier
    return None


# 统一价格模式：可选标签 + 可选币种 + 数字 + 可选「元/起」
#   ①  ¥180起 / ¥0.01 / ¥9.90
#   ②  80元 / 100 起
#   ③  预售80 / 现场100 / VIP ¥237 / 普通票 ¥0.01
# 合法性由 _label_ok() 判定（排除日期、时间、地点等误抓）。
_PRICE_RE = re.compile(
    r"(?P<label>[A-Za-z\u4e00-\u9fa5+]{0,8})\s*[:：]?\s*"
    r"(?P<sym>[¥￥])?\s*"
    r"(?P<amount>\d{1,4}(?:\.\d{1,2})?)\s*(?:元|块)?\s*"
    r"(?P<suffix>起|以上)?"
)

# 明显是日期/年份的四位数字
_YEAR_LIKE_RE = re.compile(r"^(?:19|20)\d{2}$")
_DATE_TAIL_RE = re.compile(r"^[年月日号]")


def _tier_from_neighbors(clean: str, start: int, end: int) -> str | None:
    """从价格数字前后取票种词。

    处理实测写法：
      '¥237 VIP / ¥457 VIP+'  → 取数字**后面**的词（VIP / VIP+）
      '预售80 现场100'         → 取数字**前面**的词
      '普通票 ¥0.01'           → 前面
    """
    after = clean[end: end + 14]
    m = re.match(r"\s*(?P<t>[A-Za-z\u4e00-\u9fa5][A-Za-z0-9+\u4e00-\u9fa5]{0,7})", after)
    if m:
        cand = m.group("t")
        # 排除误抓的字段名
        if cand and not re.match(r"^(时间|地点|场地|地址|入场|开场|开演|门票|价格|票价|电话|扫码)", cand):
            if detect_tier_type(cand) or cand.lower() in ("vip", "vvip", "pro", "only"):
                return cand
    before = clean[max(0, start - 12): start]
    m = re.search(r"(?P<t>[A-Za-z\u4e00-\u9fa5][A-Za-z0-9+\u4e00-\u9fa5]{0,7})\s*[:：]?\s*$", before)
    if m:
        cand = m.group("t")
        if cand and not re.match(r"^(时间|地点|场地|地址|入场|开场|开演|门票|价格|票价)", cand):
            if detect_tier_type(cand):
                return cand
    return None


_AMOUNT = r"\d{1,4}(?:\.\d{1,2})?"

# ① 币种优先：¥180起 / ¥0.01 / ¥9.90 / 门票 ¥80
_MONEY_RE = re.compile(rf"(?P<sym>[¥￥])\s*(?P<amount>{_AMOUNT})\s*(?:元|块)?\s*(?P<suffix>起|以上)?")
# ② 引导词 + 数字（预售80 / 现场100 / 早鸟票 80 / 普通票 80）
_LABEL_AMOUNT_RE = re.compile(
    rf"(?P<label>[A-Za-z\u4e00-\u9fa5+]{{1,10}})\s*[:：]?\s*(?P<amount>{_AMOUNT})"
    r"(?=\s*元|\s*起|\s*以上|[\s/|｜,，、;；]|$)"
)
# ③ 数字 + 元 / 起
_YUAN_RE = re.compile(rf"(?P<amount>{_AMOUNT})\s*(?P<unit>元|块)\s*(?P<suffix>起|以上)?")
# ④ 裸数字 + 起（如「150 起」）
_BARE_QI_RE = re.compile(rf"(?P<amount>{_AMOUNT})\s*(?P<suffix>起|以上)")
# 区间
_RANGE_RE = re.compile(rf"(?P<lo>{_AMOUNT})\s*(?:[-~至]|到)\s*(?P<hi>{_AMOUNT})")


def _num(raw: str, tail_after: str) -> float | None:
    """数字转 float，并挡掉时间/日期形态。"""
    if not raw:
        return None
    if tail_after[:1] in (":", "："):
        return None
    if tail_after[:1] in ("月", "日", "年", "号"):
        return None
    if _YEAR_LIKE_RE.match(raw) and _DATE_TAIL_RE.match(tail_after or " "):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _make_ticket(amount: float, label: str | None) -> TicketIn:
    is_placeholder = abs(amount - 0.01) < 1e-9
    name = (label or "").strip() or "票价"
    if is_placeholder:
        return TicketIn(name_raw=name, tier_type="无料", price=amount, is_placeholder=True)
    return TicketIn(name_raw=name, tier_type=detect_tier_type(name), price=amount)


def parse_prices(text: str) -> tuple[float | None, float | None, bool, list[TicketIn]]:
    """解析价格。

    返回 (price_min, price_max, is_free, tickets)。

    三段扫描（顺序即优先级，已占用的字符会被抹掉，避免重复计数）：
      ① 币种  ¥180起 / ¥0.01 / 门票 ¥80
      ② 引导词+数字  预售80 / 现场100 / 早鸟票 80
      ③ 数字+元/起   80元 / 150 起
      ④ 区间兜底     早鸟票 80-150

    覆盖实测形态，并明确排除三类噪音：
      「2026年8月29日16:00」的日期与时间、「48.8 1D」的票种代号。
    """
    if not text:
        return None, None, False, []
    clean = clean_text(text)

    if any(w in clean for w in _FREE_WORDS):
        # 无料场：价格归零，且不再把文案里的其他数字当票价
        return 0.0, 0.0, True, [TicketIn(name_raw="无料入场", tier_type="无料", price=0.0)]

    chars = list(clean)

    def blank(start: int, end: int) -> None:
        for i in range(max(0, start), min(end, len(chars))):
            chars[i] = " "

    tickets: list[TicketIn] = []
    amounts: list[float] = []
    seen: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(not (e <= a or s >= b) for a, b in seen)

    # ① 币种
    for m in _MONEY_RE.finditer(clean):
        s, e = m.span()
        if overlaps(s, e):
            continue
        amount = _num(m.group("amount"), clean[e: e + 1])
        if amount is None:
            continue
        # 票种名：看数字后面（¥237 VIP）
        tier = _tier_from_neighbors(clean, s, e) or ""
        tickets.append(_make_ticket(amount, tier))
        amounts.append(0.0 if abs(amount - 0.01) < 1e-9 else amount)
        seen.append((s, e))
        blank(s, e)

    # ② 引导词 + 数字
    for m in _LABEL_AMOUNT_RE.finditer(clean):
        s, e = m.span()
        if overlaps(s, e):
            continue
        label = (m.group("label") or "").strip()
        if not _label_ok(label, m.group("amount"), clean[e: e + 1]):
            continue
        amount = _num(m.group("amount"), clean[e: e + 1])
        if amount is None:
            continue
        tickets.append(_make_ticket(amount, label))
        amounts.append(0.0 if abs(amount - 0.01) < 1e-9 else amount)
        seen.append((s, e))
        blank(s, e)

    # ③ 数字 + 元 / 起
    for pattern in (_YUAN_RE, _BARE_QI_RE):
        for m in pattern.finditer(clean):
            s, e = m.span()
            if overlaps(s, e):
                continue
            amount = _num(m.group("amount"), clean[e: e + 1])
            if amount is None:
                continue
            tier = _tier_from_neighbors(clean, s, e) or ""
            tickets.append(_make_ticket(amount, tier))
            amounts.append(0.0 if abs(amount - 0.01) < 1e-9 else amount)
            seen.append((s, e))
            blank(s, e)

    # ④ 区间兜底（在剩余文本里找，避免把日期数字当区间）
    if not amounts:
        tail = "".join(chars)
        rng = _RANGE_RE.search(tail)
        if rng:
            lo, hi = float(rng.group("lo")), float(rng.group("hi"))
            if 0 < lo < 3000 and lo < hi:
                tickets.append(TicketIn(name_raw="最低价", price=lo))
                tickets.append(TicketIn(name_raw="最高价", price=hi))
                amounts.extend([lo, hi])

    if not amounts:
        return None, None, False, tickets
    return min(amounts), max(amounts), False, tickets


# --------------------------------------------------------------------------- #
# 城市 / 场地
# --------------------------------------------------------------------------- #

# 「漫展 + 演出」的判据：漫展场馆里的**舞台/专场/演出**是真实演出，
# 不该被展会判据清掉。
# ⚠️ 实测坑：「漫展」同时在 ACG_HINTS（算 ACG）和 _EXHIBITION_RE（当噪音），
# 于是「东莞萤火虫漫展 地下偶像舞台」两个标记都被清掉 —— 而目标明确要求
# 「包含漫展」，漫展里的地偶/乐队舞台正是要收录的内容。
_PERF_IN_MALL_RE = re.compile(
    r"舞台|专场|演出|出演|拼盘|演唱会|音乐节|live|Live|LIVE|"
    r"爬台|oneman|OneMan|ONEMAN|公演|生诞|生日会|ani\s?song|anisong",
    re.I,
)

# 二次元漫展 / 同人展的判据。
#
# ## 为什么单独一个标记而不是塞进 is_acg
#
# `is_acg` 在本项目里的语义是「**ACG 音乐演出**」相关（anisong / 乐队 /
# 同人 Live）。而游戏同人展（明日方舟 ONLY、第五人格 ONLY…）是
# **周边与本子市集**，不是音乐演出 —— 混进 `is_acg` 会污染那个语义。
#
# 但维护者要求**包含漫展**（口径 B），所以用一个独立标记表达
# 「这是二次元展会」，让前端可以分别筛选、也让发布口径能单独处理。
#
# 实测：库里 22 场未来漫展里只有 4 场属垂类 —— 其余 18 场是游戏/少年漫
# 同人展，此前既不带 is_acg 也不进日历。
_DOUJIN_EXPO_RE = re.compile(
    r"漫展|同人\s*only|同人\s*ONLY|only\s*同人|ONLY\s*同人|"
    r"同人展|同人祭|only展|ONLY展|嘉年华|comic\s*up|Comic\s*Up|"
    r"动漫展|动漫游戏|次元展|漫游展",
    re.I,
)

# 场地/日期「待定」类占位词（`is_undecided` 用）。
_UNDECIDED_WORDS = (
    "待定", "待公布", "秘密", "未定", "TBD", "tbd", "？？", "??", "待确认",
)

# ⚠️ 微博「展开全文」的截断标记，**不是**场地名。
# 实测被当成场地插进库里的垃圾：`...全文`、`活动舞台 ...全文`、`活 ...全文`、
# `活动舞台(5.1号馆 ...全文`。
# 变体很多（带点/不带点；在括号后/空格后），所以判据放宽到
# 「结尾出现『全文』，且前面是空白、标点或省略号」。
_WEIBO_TRUNC_RE = re.compile(r"[\s.…·、,，)）】\]]+全文\s*$")

# ⚠️ 场地名长度下限：**2 个字符**。
# 实测出现过 `活`、`凝固` 这种被切剩的碎片。2 字以下一律判为解析噪音。
_VENUE_MIN_LEN = 2

# 场地特征词：短名（< 4 字）必须含其中之一，否则判为碎片噪音。
# 实测 `凝固` 这种既是短名又无场地特征 —— 是正文片段而不是场地。
_SHORT_NAME_HINT_RE = re.compile(
    r"厅|馆|场|城|园|院|楼|店|吧|仓|台|中心|空间|广场|剧场|剧院|酒馆|咖啡|"
    r"live|house|club|bar|space|arena|hall",
    re.I,
)

# ⚠️ 只有品类泛词、没有任何专名的「场地」是解析噪音，不是场地。
# 实测：`演出`、`活动`、`舞台`、`活动舞台(5.1号馆)`、`活动舞台(5.1号馆`（截断后括号不闭合）。
# 判据：整串由「泛词 + 可选场馆类型词 + 可选括号编号」构成时判为噪音。
# 括号允许不闭合 —— 微博截断会切掉右括号，不能因此放过。
_VENUE_GENERIC_RE = re.compile(
    r"^(?:活动|演出|现场|舞台|会场|展台|摊位|活动舞台|演出舞台)"
    r"(?:区|厅|馆|台|场地)?"
    r"(?:[（(][^）)]{0,12}[）)]?)?$"
)

# 信息载体的 emoji：本身就带语义，抽取阶段要保留
_INFO_EMOJI = ("📍", "🗓", "🎫", "⏰", "🕖", "🕗", "🕘", "🎪", "🏟")

# 场地片段里的结构性停止词（避免把后面的字段吞进来）
_VENUE_STOP_RE = re.compile(
    r"(?:[（(]?\d{1,2}[:：]\d{2}|入场|门票|票价|价格|时间|日期|阵容|嘉宾|参演|出演|"
    r"主办|购票|扫码|抽奖|详情|转发|关注|报名|联系|咨询|地点|场地|地址)"
)


def is_undecided(text: str | None) -> bool:
    if not text:
        return True
    return any(w in text for w in _UNDECIDED_WORDS)


def parse_city(text: str, default: str | None = None) -> str | None:
    """从文本识别城市。支持 '[广州]' 前缀与别名。"""
    if not text:
        return default
    clean = clean_text(text)
    m = re.match(r"^\s*[\[【(（]\s*([\u4e00-\u9fa5]{2,4})\s*[\]】)）]", clean)
    if m:
        name = m.group(1)
        if name in GUANGDONG_CITIES:
            return name
        if name in CITY_ALIASES:
            return CITY_ALIASES[name]
    for city in GUANGDONG_CITIES:
        if city in clean:
            return city
    for alias, city in CITY_ALIASES.items():
        if alias in clean:
            return city
    return default


_VENUE_STRIP_PREFIX = re.compile(
    r"^\s*(?:[\[【(（][^\]】)）]{1,6}[\]】)）]\s*)?"
    r"(?:地点|场地|场馆|地址|venue|Venue|Venue:|地\s*点|时间|日期|Date)\s*[:：]?\s*"
)
_VENUE_TRAILING = re.compile(
    r"\s*(?:\(?\s*(?:地铁|交通|近|近邻|步行|对面|旁边|楼上|楼下|B?\d+\s*层|"
    r"F\d|L\d|\d+\s*楼|\d+F)\s*[^,，。;；]{0,20})?$"
)


# 场地名特征词：含这些词的片段**属于场地名**，不能被当作地址切掉
_VENUE_NAME_HINT_RE = re.compile(
    r"(?i)live\s*house|livespace|space|剧场|剧院|戏院|演艺中心|艺术中心|文化中心|音乐厅|"
    r"美术馆|书店|会展|体育馆|球场|舞台|展厅|咖啡|小剧场"
)
# 「N号馆 / N号仓」这类**场馆分部标志**：亚洲场地名常见，且是区分不同场地的关键信息，
# 必须保留在场地名里（如「疆进酒·OMNI SPACE 广州 2号馆」）。
# ⚠️ 反例：「东城街道33小镇工业路6号6栋106」——「6号」后面没有「馆/仓」，靠街道/路/栋命中。
_VENUE_UNIT_RE = re.compile(r"\d+\s*号(馆|仓)")
# 明确的地址特征词
_ADDRESS_STRONG_RE = re.compile(
    r"[市省区县镇村]\s|街道|路|街\d|巷|栋|幢|层|楼|室|铺|"
    r"\d+\s*[FfMm]\b|地铁|公交|对面|附近|旁边|入口|出口|大道|工业|园区|科技园|创意园"
)
# 孤立的「N号厅/N号楼/N号店」片段
_UNIT_ONLY_RE = re.compile(r"^\d+\s*号(厅|楼|店)$")


def _looks_like_address(seg: str) -> bool:
    """判断片段是否更像地址而非场地名。

    实测需要处理的形态：
      「广东艺术剧院 广州大道中1229号」→ 前半是场地，后半是地址
      「一支麦小剧场-正佳广场店 天河南街道天河路228号正佳广场5层5D088」→ 同上
    实测**不能**误切的形态（踩过的坑）：
      「MAO Livehouse广州中大店(二号馆)」→ 含「馆」但不是地址，切了会变成场地名 "MAO"
      「疆进酒·OMNI SPACE 广州 2号馆」→ 「2号馆」是分部标志，切了会与 1 号馆混淆
    """
    if not seg:
        return False
    if _VENUE_UNIT_RE.search(seg):
        # 含「N号馆/N号仓」→ 是场馆分部，不是地址
        return False
    if _VENUE_NAME_HINT_RE.search(seg):
        return False
    if _ADDRESS_STRONG_RE.search(seg):
        return True
    return bool(re.match(r"^\d", seg)) and len(seg) > 6


def _plausible_venue_name(seg: str) -> bool:
    """判断片段是否已构成「可信的场地名主体」。

    太短（如 "MAO"）时不认为已找到主体 —— 否则会把
    「MAO Livehouse广州中大店」切成 "MAO" + 地址。
    """
    if not seg:
        return False
    if _VENUE_NAME_HINT_RE.search(seg):
        return True
    return len(seg) >= 4


def split_venue_address(raw: str | None) -> tuple[str | None, str | None]:
    """把「场地名 + 地址」拆开。

    只有当「已积累出可信场地名主体」且「当前片段确实像地址」时才切分，
    避免把场地名本身切断。
    """
    if not raw:
        return None, None
    parts = [p for p in re.split(r"[\s\u3000]+", raw.strip()) if p]
    if not parts:
        return None, None
    name_parts: list[str] = []
    addr_parts: list[str] = []
    for p in parts:
        if addr_parts:
            addr_parts.append(p)
            continue
        is_addr = _looks_like_address(p)
        # 纯「N号馆」片段：只有已识别出场地主体时才归为门牌
        if not is_addr and _UNIT_ONLY_RE.match(p) and _plausible_venue_name(" ".join(name_parts)):
            is_addr = True
        if is_addr and _plausible_venue_name(" ".join(name_parts)):
            addr_parts.append(p)
        else:
            name_parts.append(p)
    name = " ".join(name_parts).strip() or None
    addr = " ".join(addr_parts).strip() or None
    return name, addr


_DATE_ONLY_RE = re.compile(r"^[\d\s\-/.:：年月日()（）()周星期上午下午晚点分秒至到~～]+$")

# 去掉城市名后「剩下部分仍是地址」的判据。
# 用于决定 `clean_venue` 该不该剥掉城市前缀：
#   「广州市越秀区北京路纵一咖啡」→ 剩下「越秀区北京路…」像地址 ✔ 该剥
#   「广州天河入场：48.8」        → 剩下「天河入场…」  像地址 ✔ 该剥
#   「珠海乐坊」                  → 剩下「乐坊」       不像地址 ✘ 不能剥
#   「深圳B10现场」               → 剩下「B10现场」    不像地址 ✘ 不能剥
_ADDRESS_TAIL_RE = re.compile(
    r"^[东南西北中]?[\u4e00-\u9fa5]{1,3}[区县镇村乡街道]"
    r"|^\d+\s*[号栋幢座层楼室铺]"
    r"|^[^\s]{0,6}(路|街|巷|大道|大道中|里|弄)"
)


def is_date_like(text: str | None) -> bool:
    """判断字符串是否只是日期/时间（没有真实场地或标题内容）。

    实测坑：微博文案把日期写进「地点：」前面时，会让
    `2026/10/18 (周日)` 变成一条场地记录。
    """
    if not text:
        return True
    s = text.strip()
    if not s:
        return True
    return bool(_DATE_ONLY_RE.match(s))


def clean_venue(raw: str | None) -> str | None:
    """清洗场地原文：去标签、去城市前缀、剥离尾部地址。

    ⚠️ 实测坑（豆瓣）：`地点` 字段常写成「场地名 + 完整地址」，
    例如「好说喜剧脱口秀演出 东城街道鸿福东路1号民盈国贸中心4层CGV影城IMAX店」。
    若不剥离，场地表会被地址污染，且每周同一场地会被当成新场地重复插入。
    """
    if not raw:
        return None
    s = clean_text(raw).strip()
    # ⚠️ 先剥掉微博「展开全文」截断标记 —— 它是**文案截断点**而不是内容。
    # 实测不处理会插进 `...全文`、`活动舞台 ...全文` 这种垃圾场地。
    s = _WEIBO_TRUNC_RE.sub("", s).strip()
    if not s:
        return None
    s = s.lstrip("".join(_INFO_EMOJI) + " @:：")
    s = _VENUE_STRIP_PREFIX.sub("", s)
    # 在下一个字段名处截断（「广州天河入场：48.8」→「广州天河」）
    stop = _VENUE_STOP_RE.search(s)
    if stop and stop.start() > 0:
        s = s[: stop.start()]
    s = s.strip(" ,，。;；-—·")
    if not s or is_undecided(s):
        return None
    # 纯日期/时间串不是场地
    if is_date_like(s):
        return None
    # 去城市前缀 —— ⚠️ 只在「去掉后剩下的部分确实像地址」时才去。
    # 实测坑：无脑剥会把场地名本身切坏：
    #   「珠海乐坊」→「乐坊」、「深圳B10现场」→「B10现场」、
    #   「珠海华发中演大剧院」→「华发中演大剧院」（不同城市的同名场地会因此混淆）
    for city in GUANGDONG_CITIES:
        m = re.match(rf"^{city}市?", s)
        if not m:
            continue
        rest = s[m.end():].strip()
        if not rest:
            continue
        # 两种情况才剥城市前缀：
        #   a) 剩下部分是地址（「广州市越秀区北京路…」）
        #   b) 剩下部分后面紧跟字段词
        if _ADDRESS_TAIL_RE.search(rest) or _VENUE_STOP_RE.search(rest):
            s = rest
        else:
            # 保留城市，但统一成「广州CH8…」这种紧凑写法：
            #   去掉「市」后缀与中间空格（B站回的是「广州市 CH8蛙厂演艺中心」）
            s = city + rest
        break
    s = s.strip(" ,，。;；-—·")
    if not s or is_date_like(s):
        return None
    # ⚠️ 只有品类泛词的「场地」是解析噪音（实测：`演出`、`活动舞台(5.1号馆)`）。
    # 这类名字指向不了任何具体地点，插进库只会污染场地表。
    if _VENUE_GENERIC_RE.match(s):
        return None
    # ⚠️ 过短的碎片也是噪音（实测 `活`、`凝固` —— 被截断标记切剩下的）
    if len(s) < _VENUE_MIN_LEN:
        return None
    # ⚠️ 短名（< 4 字）必须含场地特征词，否则是正文碎片。
    # 实测 `凝固`（2 字、无特征）被判成场地 —— 它其实来自微博正文。
    if len(s) < 4 and not _SHORT_NAME_HINT_RE.search(s):
        return None
    # 剥离尾部地址
    name, _addr = split_venue_address(s)
    s = (name or s).strip()
    # 仍过长说明还混着地址：截到第一个明显分隔
    if len(s) > 40:
        s = re.split(r"[，,。;；]", s)[0].strip()
    if not s or is_date_like(s):
        return None
    return s or None


def looks_like_venue(text: str) -> bool:
    """粗判是否像场地名（用于从自由文本里挑场地）。"""
    if not text or len(text) < 2 or len(text) > 40:
        return False
    low = text.lower()
    if any(w.lower() in low for w in VENUE_TYPE_WORDS):
        return True
    # 中英混排的专有名（如 SDlivehouse / MAO / 191space）
    return bool(re.search(r"[A-Za-z]", text)) and len(text) <= 24


# --------------------------------------------------------------------------- #
# 阵容
# --------------------------------------------------------------------------- #

_LINEUP_LABELS = (
    "参演团队", "参演乐队", "参演阵容", "参演", "出演", "阵容", "LINE UP", "LINEUP",
    "line up", "lineup", "Line Up", "GUEST", "Guest", "guest", "嘉宾", "出席成员",
    "艺人", "乐队", "团体", "CAST", "cast", "共演", "拼盘",
)
_LINEUP_STOP = re.compile(
    r"(?:#|门票|票价|价格|时间|地点|场地|地址|入场|开场|开演|主办|购票|扫码|"
    r"抽奖|详情|转发|关注|报名|联系|咨询)"
)
_LINEUP_SPLIT = re.compile(r"\s*(?:[/、,，;；|｜]|&|＆|×|✕|＋|\+|(?<=\s)[xX](?=\s)|和)\s*")
_AT_TOKEN_RE = re.compile(r"@[A-Za-z0-9_\u4e00-\u9fa5\-]{2,40}")


def clean_artist_name(name: str) -> str:
    """清洗单个阵容名：去 @、去角色括注、去尾部符号。"""
    s = clean_text(name).strip()
    s = re.sub(r"^@+", "", s)
    s = re.sub(
        r"\s*[（(][^）)]{0,30}(主演|共演|嘉宾|GUEST|guest|MC|mc|DJ|dj|主持)[^）)]{0,30}[）)]\s*",
        "", s,
    )
    s = re.sub(r"^@+", "", s)
    s = re.sub(r"[\s\u3000]+", " ", s)
    return s.strip(" ·-—,，、/|")


def _lineup_from_at_tokens(text: str) -> list[ArtistIn]:
    """从 '@A @B @C' 形式抽阵容（微博常见）。"""
    out: list[ArtistIn] = []
    seen: set[str] = set()
    for m in _AT_TOKEN_RE.finditer(text):
        name = clean_artist_name(m.group(0))
        if not name:
            continue
        norm = normalize_name(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(
            ArtistIn(name_raw=name, name_norm=norm, role="performer",
                     billing_order=len(out) + 1)
        )
    return out


def split_lineup_names(raw: str | None) -> list[str]:
    """把「一组里塞了多个团体」的字符串拆成多个名字。

    实测（秀动 `artist` 字段）：
      「娜娜捏口俱乐部/ReaLume/恋时青空/DigitalDuel」→ 4 个团体
      「体熊专科/JASON KUI」→ 2 个
      「吹波糖/闪星TWINKLESTA」→ 2 个
    但「AC/DC」这种含斜杠的单个名字会误拆 —— 这是可接受的取舍：
    地偶/乐队场景里「A/B/C」几乎总是多组联合，而名字里带斜杠的极少。
    """
    if not raw:
        return []
    clean = clean_text(raw)
    if not clean:
        return []
    parts = _LINEUP_SPLIT.split(clean)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        name = clean_artist_name(p)
        if not name or len(name) > 40:
            continue
        if not re.search(r"[\u4e00-\u9fa5A-Za-z]", name):
            continue
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    # 拆不出来就返回原值，保证不丢信息
    return out or [clean_artist_name(clean) or clean]


def parse_lineup(text: str) -> list[ArtistIn]:
    """从文本抽阵容，保留书写顺序为 billing_order。

    优先级：带标签的片段 > '@' 阵列 > 「A × B × C」阵列。
    """
    if not text:
        return []
    clean = clean_text(text)

    segment: str | None = None
    for label in _LINEUP_LABELS:
        idx = clean.find(label)
        if idx >= 0:
            tail = clean[idx + len(label):]
            tail = re.sub(r"^\s*[:：]\s*", "", tail)
            if tail.strip():
                segment = tail
                break

    if segment is None:
        # 无标签：先看 '@' 阵列，再看「A × B × C」
        ats = _lineup_from_at_tokens(clean)
        if len(ats) >= 2:
            return ats
        if re.search(r"[×✕]|＋|(?<=\s)[/|｜](?=\s)", clean):
            segment = clean
        else:
            return ats  # 可能只有 1 个 @，或为空

    # 截断到下一个字段标签
    stop = _LINEUP_STOP.search(segment)
    if stop and stop.start() > 0:
        segment = segment[: stop.start()]
    segment = segment.strip()
    if not segment:
        return []

    # 片段里若主要是 '@' token，直接用它（避免被空格切碎）
    ats = _lineup_from_at_tokens(segment)
    if len(ats) >= 2:
        return ats

    parts = _LINEUP_SPLIT.split(segment)
    out: list[ArtistIn] = []
    seen: set[str] = set()
    for p in parts:
        name = clean_artist_name(p)
        if not name or len(name) > 40:
            continue
        if not re.search(r"[\u4e00-\u9fa5A-Za-z]", name):
            continue
        norm = normalize_name(name)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(
            ArtistIn(
                name_raw=name,
                name_norm=norm,
                role="performer",
                billing_order=len(out) + 1,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #

_STATUS_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("cancelled", ("取消", "中止", "取消场次")),
    ("postponed", ("改期", "延期", "顺延", "时间变更")),
    ("sold_out", ("售罄", "已售完", "sold out", "SOLD OUT", "无票")),
    ("on_sale", ("售票中", "开票", "预售中", "热卖", "购票中")),
    ("announced", ("即将公布", "待开票", "预告")),
    ("finished", ("已结束", "已举办")),
]


def parse_status(text: str) -> tuple[str, str | None]:
    """解析场次状态与备注。"""
    if not text:
        return "unknown", None
    clean = to_halfwidth(text)
    for status, words in _STATUS_PATTERNS:
        for w in words:
            if w in clean:
                note = None
                m = re.search(rf"{re.escape(w)}[^\n。；;]{{0,40}}", clean)
                if m and len(m.group(0)) > len(w):
                    note = m.group(0).strip()
                return status, note
    return "unknown", None


# --------------------------------------------------------------------------- #
# 分类（乐队 / 地偶）
# --------------------------------------------------------------------------- #

IDOL_HINTS = (
    "地偶", "地下偶像", "公演", "定期公演", "生诞", "生誕", "生日祭", "生日sp",
    "卒业", "卒業", "毕业公演", "披露", "特典", "チェキ", "拍立得", "握手会",
    "idol", "oneman", "one man", "only live", "onlylive", "アイドル",
    "联合公演", "联合live", "偶像", "少女", "女团", "男团", "偶像团体",
)
# ⚠️ 收紧了两个过松的词（实测）：
#   * 单字「only」→ 只保留「only live」。曾经「only」单独当地偶词，
#     于是「全职猎人同人only」「阿特拉斯同人ONLY展」都被判成地偶。
#     「only」本身只是「限定」的意思，漫展/周边/签售都爱用。
#   * 「同人」→ 它是**品类**（ACG）而不是「偶像」信号。
#     靠它判地偶会把所有同人展拉进垂类。
#
# 但完全去掉「only」会让「○○Only Live」失去信号，所以补一条**精确的正向模式**：
# 只认「only live / onlylive」这种明确表示演出的写法，**不认单独的「同人only」**。
#
# ⚠️ 反复试过让「同人only」当地偶信号，最后放弃 —— 因为它根本无法区分：
#     「金牌得主同人only」（运动漫）与「全职猎人同人only」（少年漫）
#     标题格式**完全相同**。硬猜只会两边得罪。
#   歧义交给「发布口径」解决：**有演出名单就收录**（见 static_build.PublishPolicy）。
_IDOL_ONLY_LIVE_RE = re.compile(r"only\s*live|onlylive|only\s*公演", re.I)
# ⚠️ 曾把「企划」当地偶词，导致误判（实测）：
#   「留声RECORD音乐企划」「马赫mood x 杜逸风…5周年特别企划专场」
#   都被判成地偶，进而混进垂类静态站。
# 「企划」只是「项目/厂牌」的意思，音乐厂牌、说唱专场、戏剧企划都会用 —— 已移除。
#
# ⚠️ 但移除它会**漏标**真正的地偶企划。解决方式不是把泛词放回去（会连带误判），
# 而是补**具体厂牌/组合名**：这类名字指向明确，不会误伤别的活动。
#
# 维护方式：发现漏标就往这里加名字，并同步在 tests/test_flags.py 里加用例。
_IDOL_GROUPS = (
    "比邻星球",      # 地偶企划（广州，声音共和 Livehouse）
    "留声record",    # 珠海乐坊，2026-10-17（由维护者确认为地偶场次）
)

# ⚠️ 非演出噪音：这些不是活动，更不是演出。
# 实测踩过的坑：「深圳天气剧透# 9日局地偶有零星小雨渐转多云」
# 里的「地偶」其实是「局地 / 偶有」断词，被当成地下偶像 → 误进垂类。
# 判据放在 `classify` 里：命中「地偶」但整句是天气/情报类文案 → 撤销地偶判定。
_WEATHER_RE = re.compile(
    # ⚠️ 不要放单字「晴」「阴」——实测「邓晴桦」这种人名会被误伤，
    # 把人名里的字当成天气。
    r"天气|气温|降雨|降水|小雨|中雨|大雨|暴雨|阵雨|多云|阴天|台风|"
    r"冷空气|降温|升温|湿度|空气质量|紫外线|转多云|渐转"
)
_NON_EVENT_NOISE_RE = re.compile(
    r"剧透|预报|预告|情报|速报|周报|日报|汇总|盘点|科普|招募|征集|"
    r"应援|返图|repo|测评|攻略|排期表|交通|停车|门票攻略"
)

# ⚠️ 展会识别：漫展 / 同人展 / only展 是**展览**不是演出。
#
# 本项目明确「非演出内容默认不进日历」，所以这类必须整体撤销垂类标记。
# 实测踩过的坑：`only` 在地偶词表里（来自「only live」），于是
# 「全职猎人同人only」「阿特拉斯同人ONLY展」都被判成地偶。
#
# ⚠️ 判据必须**绑定「展」字**：实测「广州·金牌得主同人only」是**同人 Live**
#    （是演出，测试预期 is_idol=True），不能因为出现「同人only」就排除。
#    踩过的坑：写成 `only\s*同人展` 时，正则会先匹配到「only同人」而漏掉后面的
#    「展」，等于没绑定 —— 同人 Live 被误杀。必须让「展」紧跟其后。
_EXHIBITION_RE = re.compile(
    r"漫展|同人展|only展|展销|展会|艺术展|主题展|"
    r"only\s*同人\s*展|同人\s*only\s*展|"
    r"茶话会|嘉年华|签售|周边|抽奖|图鉴|谷子|摆摊|摊位",
    re.I,
)
BAND_HINTS = (
    "乐队", "樂隊", "band", "巡演", "巡回", "拼盘", "livehouse",
    "独立", "摇滚", "后摇", "后朋", "金属", "民谣", "朋克", "硬核", "噪音",
    "器乐", "数学摇", "盯鞋", "shoegaze", "不插电", "unplugged",
)
NEGATIVE_HINTS = (
    "脱口秀", "相声", "话剧", "音乐剧", "儿童剧", "戏曲", "粤剧", "昆曲",
    "交响", "钢琴独奏", "展览", "快闪", "主题餐厅", "见面会", "签售会",
    "讲座", "沙龙", "电竞", "动漫展", "漫展",
)

_IDOL_REGULAR = ("定期公演", "定期", "vol.", "vol ", "例会", "月例")
_IDOL_BIRTHDAY = ("生诞", "生誕", "生日祭", "生日sp", "生诞祭", "生日公演")
# 注意：'专场' 不作为 Oneman 判定词 —— 脱口秀/话剧也大量使用「专场」
_ONEMAN = ("oneman", "one man", "one-man", "ワンマン")
_TAIBAN = ("拼盘", "联合", "対バン", "taiban", "对盘", "共演")
_FESTIVAL = ("音乐节", "festival", "fes", "fes.", "フェス")
_TOUR = ("巡演", "巡回", "tour")

# 巡演站标题：去掉「XX巡演 城市站」再分类，避免把巡演场误判为专场
_TOUR_STOP_RE = re.compile(r"[^\s]{0,20}(巡演|巡回|tour)[^\s]{0,20}(站)?")
_ROLE_SUFFIX_WORDS = ("专场", "拼盘", "演出", "现场", "公演")

# --------------------------------------------------------------------------- #
# 独立标记：女子乐队 / ACG
#
# 与 kind / is_idol 正交 —— 一个 ACG 女子乐队可以同时命中多个标记，
# 「与」关系（AND）由调用方按需组合，不做互斥。
# --------------------------------------------------------------------------- #

# 女子乐队：全女子编制的乐队。**必须**命中才能标，避免把「女生」当关键词误伤。
#
# ⚠️ 关键词取舍（被实测假阳性教育过）：
#   * 不放「女声摇滚」「女生乐队」这类泛词 ——「女声」只说明主唱性别，
#     古典/爵士/民谣场次也常这么写。实测「友北五重奏《弦·无界》」曾被误标。
#   * 只保留明确表示**全女子编制**的说法。
GIRL_BAND_HINTS = (
    "女子乐队", "女子樂隊", "全女子", "女子摇滚", "女子搖滾",
    "女子后摇", "女子朋克", "女子金属", "女子硬核", "女子indie",
    "girl band", "girlband", "all girl", "all-girl", "ガールズバンド",
    "少女乐队", "少女樂隊", "女子乐团", "女子樂團",
    "高校轻音", "バンドやろうよ",
)
# 女子偶像/少女偶像属于「地偶」而不是「女子乐队」——命中这些则不标女子乐队。
# 代价（如实说明）：「女子偶像×女子乐队拼盘」这类交叉场次不会标成女子乐队。
# 这是有意的取舍：宁可漏标，也不把纯地偶场次误标进女子乐队筛选。
_GIRL_BAND_NEGATIVE = (
    "地偶", "地下偶像", "偶像", "女团", "男团", "偶像团体", "偶像企划",
    "生诞", "生誕", "生日祭", "定期公演", "特典会", "チェキ",
)

# ACG：动画 / 漫画 / 游戏 / 二次元 / 同人 / 宅向
#
# ⚠️ 关键词取舍（同样是实测教训）：
#   * 不用单字「痛」——「澈心之痛」这种情绪化乐队名会被误伤；只保留「痛车/痛包」。
#   * 不用「op主题曲」——会命中「…融合一《弦·无界》」这类无关文本。
#   * 用「only live / only展」而不是裸「only」——后者太容易误伤。
ACG_HINTS = (
    "acg", "二次元", "2.5次元", "anime", "anisong", "动漫", "動漫",
    "同人", "漫展", "痛车", "痛車", "痛包", "东方project", "東方project",
    "touhou", "ボカロ", "vocaloid", "初音未来", "初音ミク",
    "音游", "音ゲー", "游戏音乐", "遊戲音樂", "galgame", "gal game",
    "视觉小说", "动画主题曲", "動畫主題曲", "动漫主题曲", "动漫歌曲",
    "赛马娘", "偶像大师", "lovelive", "ラブライブ", "bang dream", "bangdream",
    "孤独摇滚", "轻音少女", "少女乐队", "少女樂隊", "only live", "only展",
    "同人音乐", "同人音樂", "角色扮演演出",
)
_ACG_NEGATIVE = (
    "脱口秀", "相声", "话剧", "音乐剧", "儿童剧", "戏曲", "粤剧",
    "交响", "钢琴独奏", "展览", "讲座", "沙龙",
    # ⚠️ 实测误判：漫展/同人展/only展是**展览**不是演出。
    # 本项目明确「非演出内容默认不进日历」，所以 ACG 标记只应落在
    # ACG 相关的**演出**上（同人 Live / ACG 乐队 / 音游 Live），
    # 不能因为标题里有「同人」「only」就把展会拉进垂类。
    "漫展", "同人展", "only展", "同人only", "同人ONLY", "茶话会", "嘉年华",
    "签售", "周边", "抽奖", "情报", "应援", "图鉴", "repo", "返图",
)


class ClassifyResult:
    """分类结果。

    既可直接取属性（result.is_idol / result.is_girl_band / result.is_acg），
    也可**按三元组解包** `kind, is_idol, tags = classify(...)` ——
    保留这个向后兼容是为了不破坏已有的 40+ 处调用点。
    """

    __slots__ = (
        "kind", "is_idol", "is_girl_band", "is_acg", "is_doujin_expo", "tags",
    )

    def __init__(
        self,
        kind: str,
        is_idol: bool,
        is_girl_band: bool,
        is_acg: bool,
        tags: list[str],
        # ⚠️ 带默认值：本类有 40+ 处调用点，新字段不能强制传参
        is_doujin_expo: bool = False,
    ) -> None:
        self.kind = kind
        self.is_idol = is_idol
        self.is_girl_band = is_girl_band
        self.is_acg = is_acg
        self.is_doujin_expo = is_doujin_expo
        self.tags = tags

    def __iter__(self):
        """兼容旧写法：`kind, is_idol, tags = classify(...)`。"""
        yield self.kind
        yield self.is_idol
        yield self.tags

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "is_idol": self.is_idol,
            "is_girl_band": self.is_girl_band,
            "is_acg": self.is_acg,
            "is_doujin_expo": self.is_doujin_expo,
            "tags": self.tags,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ClassifyResult(kind={self.kind!r}, is_idol={self.is_idol}, "
            f"is_girl_band={self.is_girl_band}, is_acg={self.is_acg}, "
            f"tags={self.tags!r})"
        )


# 演员知识库的惰性缓存（在 _lineup_kb() 里填充；False 表示不可用）
_LINEUP_KB: Any = None


def _lineup_kb():
    """演员知识库的惰性单例（**延迟导入**避免与 artist_kb 循环依赖）。"""
    global _LINEUP_KB
    if _LINEUP_KB is None:
        try:
            from app.normalize.artist_kb import ArtistKnowledgeBase

            _LINEUP_KB = ArtistKnowledgeBase()
        except Exception:  # noqa: BLE001
            # 知识库不可用时退化为空库：不影响标题判定
            _LINEUP_KB = False
    return _LINEUP_KB or None


def classify(
    title: str, extra: str = "", lineup: Any = None
) -> ClassifyResult:
    """分类：返回 ClassifyResult（可解包成 (kind, is_idol, tags, is_doujin_expo)）。

    判定顺序（实测调优）：
      1. 命中排除词且无强地偶信号 → other + 标签「非演出」
      2. 地偶信号多于乐队信号 → 地偶族
      3. 否则 → 乐队族

    同时独立判定两个正交标记（可叠加）：
      * `is_girl_band` —— 女子乐队（全女子编制）
      * `is_acg`       —— ACG / 二次元 / 同人 / 宅向

    `lineup`（可选）：演出人员名单。若其中含**已知垂类团体**（地偶/女子乐队/ACG），
    则视为「确实是演出」，**压过**展会/应援/情报类噪音判据。
    实测场景：「koyo生诞祭应援」标题含「应援」，但阵容是
    「Koyo_Digitalduel-1018生诞祭版」（含地偶团体 DigitalDuel）。
    """
    hay = f"{title} {extra}"
    low = hay.lower()
    # ⚠️ 不能用 `or title` 兜底：调用方常传整段文案作 title，那样 title_low 会覆盖标题
    title_low = (title or "").lower()
    tags: list[str] = []

    # 二次元漫展 / 同人展（口径 B：维护者要求纳入垂类）。
    #
    # ⚠️ 与 `is_acg` **分开**：后者的语义是「ACG **音乐演出**」
    #    （anisong / 乐队 / 同人 Live）。游戏同人展（明日方舟 ONLY 等）
    #    是周边与本子市集，混进 is_acg 会污染那个语义。
    #
    # 实测：库里 22 场未来漫展里只有 4 场属垂类 —— 其余 18 场是
    # 游戏/少年漫同人展，此前既不带 is_acg 也不进日历。
    is_doujin_expo = bool(_DOUJIN_EXPO_RE.search(hay))
    if is_doujin_expo:
        tags.append("漫展")

    idol_score = sum(1 for h in IDOL_HINTS if h.lower() in low)
    band_score = sum(1 for h in BAND_HINTS if h.lower() in low)
    negative = any(h in hay for h in NEGATIVE_HINTS)

    is_idol = idol_score > band_score
    # 「Only Live」是明确的**演出**写法（区别于「Only 展」），单独作为一个正向信号。
    # 注意这里**不认**单独的「同人only」—— 它在演出与展会之间毫无区分度（见上面的注释）。
    if _IDOL_ONLY_LIVE_RE.search(hay):
        is_idol = True
    # 具体厂牌/组合名一律判为地偶：这些名字指向唯一，不需要靠分数博弈。
    # ⚠️ 必须用 low（已小写）来比对：真实标题里有「留声RECORD」这种全大写写法，
    # 拿小写关键字去搜原始 hay 会漏标（实测踩过）。同时容忍中间的空格。
    if any(g.lower() in low for g in _IDOL_GROUPS):
        is_idol = True
    else:
        squashed = re.sub(r"\s+", "", low)
        if any(re.sub(r"\s+", "", g.lower()) in squashed for g in _IDOL_GROUPS):
            is_idol = True

    # ⚠️ 展会 / 非演出噪音：撤销**地偶**标记（它不是偶像演出）。
    #
    # 实测踩过的两处误判：
    #   1. 「深圳天气剧透# 9日局**地偶**有零星小雨」——「局地/偶有」断词被当成地下偶像
    #   2. 「全职猎人同人only」——「only」在地偶词表里（源自「only live」），
    #      于是漫展/同人展被判成地偶；而本项目明确「非演出不进日历」
    #
    # 取舍：**只撤销 is_idol，保留 is_acg**。理由：
    #   * ACG 是「内容品类」（同人/二次元），展会确实属于该品类，信息不该丢；
    #   * 「不把展会放进日历」由 `kind='other'` + `exclude_other` 负责。
    #
    # ⚠️ **阵容优先**：若标题同时含已核实厂牌名、或调用方给了 lineup 且其中
    #    含已知垂类团体，则以事实为准，不因出现「应援/情报」等字样误杀。
    #    实测：「koyo生诞祭应援」标题含「应援」被当噪音，但阵容是
    #    「Koyo_Digitalduel-1018生诞祭版」（含地偶团体 DigitalDuel）—— 确实是演出。
    _has_known_group = any(g.lower() in low for g in _IDOL_GROUPS)
    if lineup:
        kb_view = _lineup_kb()
        for raw_name in (lineup if kb_view is not None else ()):
            entry, how = kb_view.lookup(str(raw_name))
            if entry is not None and how in ("name", "alias") and entry.kind in (
                "idol_group", "girl_band", "acg_unit"
            ):
                _has_known_group = True
                break
    # ⚠️ **漫展里的舞台是演出**：标题同时出现「漫展/展会词」和「舞台/专场/演出」
    #    时判定为**真实演出**，不被展会判据清掉。
    #    实测：「东莞萤火虫漫展 地下偶像舞台」原本两个标记都被清 —— 但目标
    #    明确要求「包含漫展」，漫展里的地偶/乐队舞台正是要收录的内容。
    _perf_in_expo = bool(_EXHIBITION_RE.search(hay)) and bool(
        _PERF_IN_MALL_RE.search(hay)
    )
    if not _has_known_group and not _perf_in_expo and (
        _EXHIBITION_RE.search(hay)
        or _NON_EVENT_NOISE_RE.search(hay)
        or _WEATHER_RE.search(hay)
    ):
        is_idol = False
        if "非演出" not in tags:
            tags.append("非演出")
    elif _perf_in_expo and "漫展舞台" not in tags:
        # 标记出来，便于前端/复核时区分「独立场次」与「漫展爬台」
        tags.append("漫展舞台")


    # ---- 女子乐队：命中 + 不是偶像场 ----
    is_girl_band = (
        any(h.lower() in low for h in GIRL_BAND_HINTS)
        and not any(h in hay for h in _GIRL_BAND_NEGATIVE)
    )
    if is_girl_band:
        tags.append("女子乐队")

    # ---- ACG：命中 + 不是纯话剧/古典类 ----
    is_acg = (
        any(h.lower() in low for h in ACG_HINTS)
        and not any(h in hay for h in _ACG_NEGATIVE)
    )
    if is_acg:
        tags.append("ACG")

    # 从识别用的文本里剥掉「巡演…站」与角色后缀词，避免 kind 误判
    probe = _TOUR_STOP_RE.sub(" ", title_low)
    probe = probe + " " + (extra or "").lower()

    if is_idol:
        if any(k in low for k in _IDOL_BIRTHDAY):
            tags.append("生诞")
            return ClassifyResult("idol_birthday", True, is_girl_band, is_acg, tags, is_doujin_expo)
        if any(k in low for k in _ONEMAN):
            tags.append("Oneman")
            return ClassifyResult("oneman", True, is_girl_band, is_acg, tags, is_doujin_expo)
        if any(k in low for k in _TAIBAN) or "only" in low:
            tags.append("Only" if "only" in low else "联合")
            return ClassifyResult("idol_taiban", True, is_girl_band, is_acg, tags, is_doujin_expo)
        if any(k in low for k in _IDOL_REGULAR):
            tags.append("定期公演")
            return ClassifyResult("idol_regular", True, is_girl_band, is_acg, tags, is_doujin_expo)
        # 同时出现多个地偶信号（如「联合公演」「定期公演」）也归为定期公演
        if idol_score >= 2:
            tags.append("公演")
            return ClassifyResult("idol_regular", True, is_girl_band, is_acg, tags, is_doujin_expo)
        return ClassifyResult("doujin_live", True, is_girl_band, is_acg, tags, is_doujin_expo)

    if any(k in low for k in _FESTIVAL):
        tags.append("音乐节")
        return ClassifyResult("festival", False, is_girl_band, is_acg, tags, is_doujin_expo)
    if any(k in low for k in _TOUR):
        tags.append("巡演")
        return ClassifyResult("tour_stop", False, is_girl_band, is_acg, tags, is_doujin_expo)
    if "专场" in probe and band_score > 0:
        tags.append("专场")
        return ClassifyResult("oneman", False, is_girl_band, is_acg, tags, is_doujin_expo)
    if any(k in low for k in _ONEMAN):
        tags.append("专场")
        return ClassifyResult("oneman", False, is_girl_band, is_acg, tags, is_doujin_expo)
    if any(k in low for k in _TAIBAN):
        tags.append("拼盘")
        return ClassifyResult("taiban", False, is_girl_band, is_acg, tags, is_doujin_expo)
    if band_score > 0:
        return ClassifyResult("rock_live", False, is_girl_band, is_acg, tags, is_doujin_expo)
    if negative:
        tags.append("非演出")
    return ClassifyResult("other", False, is_girl_band, is_acg, tags, is_doujin_expo)


_TITLE_STOP_RE = re.compile(
    r"(?:时间|日期|地点|场地|场馆|地址|入场|开场|开演|票价|价格|门票|参演|出演|阵容|"
    r"主办|购票|扫码|抽奖|嘉宾)"
)
# 纯信息性、不是活动名的标题
_BOILERPLATE_TITLES = ("主催情报", "情报", "揭示板", "公告", "通知", "汇总", "图鉴")


def looks_like_event_title(title: str | None) -> bool:
    """粗判标题是否像一个活动名（用于丢弃「主催情报」这类噪音）。"""
    if not title:
        return False
    s = strip_decor(clean_text(title))
    if len(s) < 4:
        return False
    # 至少要有 2 个中日文字符或 4 个纯字母数字
    han = len(re.findall(r"[\u4e00-\u9fa5]", s))
    if han < 2 and len(re.sub(r"[^A-Za-z0-9]", "", s)) < 4:
        return False
    core = re.sub(r"[\s\W_]+", "", s)
    if core in _BOILERPLATE_TITLES:
        return False
    if is_date_like(s):
        return False
    return True


def _extract_title(text: str, fallback: str) -> str:
    """从文案里抽标题：取首个非空行，并在字段标签处截断。

    实测坑：微博文案常把「时间/地点/阵容」写在同一行，
    若不截断，标题会变成整段正文（90 字符上限也只是截断而不是切干净）。
    """
    for line in (text or "").splitlines():
        cand = display_title(line)
        if not (3 <= len(cand) <= 120):
            continue
        m = _TITLE_STOP_RE.search(cand)
        if m and m.start() >= 3:
            cand = cand[: m.start()].strip(" -—·|/、,，")
        cand = cand[:90].strip()
        if cand:
            return cand
    return fallback


# --------------------------------------------------------------------------- #
# 系列 / 期号
# --------------------------------------------------------------------------- #

_SERIES_VOL_RE = re.compile(
    r"(?i)(?:vol|volume|no)\.?\s*(\d+(?:\.\d+)?)"
    r"|第\s*([0-9一二三四五六七八九十]+)\s*[期弹回届]"
    r"|([①②③④⑤⑥⑦⑧⑨⑩])"
)


def parse_series(title: str) -> tuple[str | None, str | None]:
    """从标题抽系列键与期号。

    实测：「PoP Star Idol Festival Vol.7」→ series='popstaridolfestival', vol='7'
          「《花束》午前4时 定期公演 VOL.3」→ vol='3'
          「绮丽偶像日 KFC MINI in GuangZhou 08」→ MINI 08 这类需人工/词表辅助
    """
    if not title:
        return None, None
    clean = strip_decor(clean_text(title))
    vol: str | None = None
    m = _SERIES_VOL_RE.search(clean)
    if m:
        vol = m.group(1) or m.group(2) or m.group(3)

    series_src = _SERIES_VOL_RE.sub(" ", clean)
    series_src = re.sub(r"[《》「」【】\[\]（）()“”\"']", " ", series_src)
    series_src = re.sub(r"\s+", " ", series_src).strip()
    # 取前 3~6 个实词作为系列键
    words = [w for w in re.split(r"[\s　/|·・]+", series_src) if len(w) >= 2]
    series = "".join(words[:3]) if words else None
    if series:
        series = normalize_name(series)[:40] or None
    return series, vol


# --------------------------------------------------------------------------- #
# 地偶专属字段
# --------------------------------------------------------------------------- #

_TOKUTEN_RE = re.compile(
    r"(特典券|チェキ券|拍立得券)[^\n。；;]{0,60}", re.I
)
_SCHEDULE_HINT = re.compile(
    r"(\d{1,2}[:：]\d{2})[^\n]{0,10}(上台|登场|演出|开场|开演)"
)
_AGE_PATTERNS = [
    (re.compile(r"1\.2\s*(?:米|m|M)\s*以下[^\n。；;]{0,20}"), None),
    (re.compile(r"(\d+)\s*(?:岁|周?岁)\s*(?:以下|以上)"), None),
    (re.compile(r"(全年龄|全年齢|全年龄向)"), None),
    (re.compile(r"(18\s*\+|十八禁|未成年[^\n]{0,10}禁止)"), None),
]


def parse_age_limit(text: str) -> str | None:
    if not text:
        return None
    for pat, _ in _AGE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    return None


def extract_tokuten(text: str) -> str | None:
    m = _TOKUTEN_RE.search(text or "")
    return m.group(0).strip() if m else None


def extract_schedule(text: str) -> str | None:
    if not text:
        return None
    hits = _SCHEDULE_HINT.findall(text)
    if len(hits) >= 2:
        lines = [ln.strip() for ln in re.split(r"[\n/|；;]", text) if _SCHEDULE_HINT.search(ln)]
        return " / ".join(lines[:8]) if lines else None
    return None


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #

def extract_from_text(
    text: str,
    *,
    source_code: str = "text",
    source_url: str = "",
    external_id: str = "",
    default_city: str | None = None,
    now: dt.datetime | None = None,
    title_hint: str | None = None,
) -> dict:
    """从自由文本抽取所有可识别字段。

    返回 dict（可直接喂给 ParsedOccurrence），每个字段独立尽力而为。
    """
    now = now or dt.datetime.now(CST)
    clean = clean_text(text or "")

    # ---- 标题 ----
    title = title_hint
    if not title:
        title = _extract_title(text or "", display_title(clean)[:90])
    if not title:
        title = display_title(clean)[:90] or "（未命名活动）"

    # ---- 各字段 ----
    start_at, open_at, precision, end_at = parse_datetime(clean, now=now)
    price_min, price_max, is_free, tickets = parse_prices(clean)
    city = parse_city(clean, default=default_city)

    # 场地：显式标签优先（clean 里 emoji 已被去掉），再退回原始文本里的 📍 / 🏟
    venue = None
    m = re.search(
        r"(?:地点|场地|场馆|地址|venue)\s*[:：]?\s*@?\s*(?P<v>[^\n，,。;；|｜#]{2,40})",
        clean,
        re.I,
    )
    if m:
        venue = clean_venue(m.group("v"))
    if venue is None:
        m = re.search(r"(?:📍|🏟|🎪)\s*(?P<v>[^\n，,。;；|｜#]{2,40})", text or "")
        if m:
            venue = clean_venue(m.group("v"))

    lineup = parse_lineup(clean)
    status, status_note = parse_status(clean)
    cls = classify(f"{title} {clean[:120]}")
    series_key, series_vol = parse_series(title)

    organizer = None
    m = re.search(r"(?:主办|主办方|出品|呈现|presented by)\s*[:：]?\s*([^\n，,。;；|｜]{2,30})", clean, re.I)
    if m:
        organizer = m.group(1).strip()

    # 展期类活动（有起止但无具体时间）不当作单场演出
    return {
        "source_code": source_code,
        "source_url": source_url,
        "external_id": external_id or "",
        "title_raw": (text or "").strip()[:200] or title,
        "title_display": title,
        "series_key": series_key,
        "series_vol": series_vol,
        "kind": cls.kind,
        "is_idol": cls.is_idol,
        "is_girl_band": cls.is_girl_band,
        "is_acg": cls.is_acg,
        "tags": cls.tags,
        "city": city,
        "venue_raw": venue,
        "start_at": start_at,
        "open_at": open_at,
        "end_at": end_at,
        "date_precision": precision,
        "status": status,
        "status_note": status_note,
        "price_min": price_min,
        "price_max": price_max,
        "is_free": is_free,
        "tickets": tickets,
        "age_limit": parse_age_limit(clean),
        "tokuten_note": extract_tokuten(clean),
        "schedule_note": extract_schedule(clean),
        "artists": lineup,
        "organizer": organizer,
    }


def confidence_of(fields: dict) -> float:
    """按字段完整度给一个整体置信度（用于决定是否进人工队列）。"""
    score = 0.0
    weights = {
        "start_at": 0.45,
        "venue_raw": 0.25,
        "city": 0.10,
        "price_min": 0.10,
        "artists": 0.10,
    }
    for key, w in weights.items():
        val = fields.get(key)
        if val not in (None, "", [], {}):
            score += w
    if fields.get("date_precision") == "tbd":
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 2)
