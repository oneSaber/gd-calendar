"""通用工具：内容指纹、时间、日志。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import sys
from typing import Any

from app.config import settings

CST = dt.timezone(dt.timedelta(hours=8))


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #

def setup_logging(level: str | None = None) -> None:
    """统一日志配置；Windows 控制台强制 UTF-8，避免中文/¥ 抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    logging.basicConfig(
        level=getattr(logging, (level or ("DEBUG" if settings.debug else "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # 第三方库噪声抑制：aiosqlite 会把每条 SQL 都打出来
    for noisy in ("aiosqlite", "asyncio", "urllib3", "httpx", "httpcore", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# --------------------------------------------------------------------------- #
# 内容指纹
# --------------------------------------------------------------------------- #

def content_hash(*parts: Any) -> str:
    """稳定内容指纹（用于跳过无变化的二次解析/渲染）。"""
    h = hashlib.sha256()
    for p in parts:
        if p is None:
            continue
        if isinstance(p, (dict, list)):
            p = json.dumps(p, ensure_ascii=False, sort_keys=True, default=str)
        h.update(str(p).encode("utf-8"))
    return h.hexdigest()[:32]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 时间
# --------------------------------------------------------------------------- #

def now_cst() -> dt.datetime:
    return dt.datetime.now(CST)


def to_cst(value: dt.datetime | None) -> dt.datetime | None:
    """统一到 Asia/Shanghai。naive 时间按本地（东八区）解释。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=CST)
    return value.astimezone(CST)


def aware(value: dt.datetime | None) -> dt.datetime | None:
    """确保 datetime 带时区。

    ⚠️ 关键取舍：**SQLite 不保存时区**，写进去的 aware datetime 读出来是 naive，
    与内存里的 aware 值比较会抛 `TypeError: can't subtract offset-naive and
    offset-aware datetimes`。所有从库里读出的时间都必须先过这个函数。
    PostgreSQL 的 timestamptz 会保留时区，但统一走这里也无害。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=CST)
    return value


def dt_equal(a: dt.datetime | None, b: dt.datetime | None, tolerance_sec: float = 60) -> bool:
    """比较两个时间是否「基本同一时刻」，容忍 naive/aware 混用。"""
    a2, b2 = aware(a), aware(b)
    if a2 is None or b2 is None:
        return a2 is b2
    return abs((a2 - b2).total_seconds()) <= tolerance_sec


_CN_DATE_RE = re.compile(
    r"(?P<y>20\d{2})\s*[年\-/.]\s*(?P<m>\d{1,2})\s*(?:[月\-/.]\s*(?P<d>\d{1,2})\s*日?)?"
    r"(?:\s*(?P<H>\d{1,2})\s*[:：时]\s*(?P<M>\d{1,2})?)?"
)
_CN_MD_RE = re.compile(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
                       r"(?:\s*(?P<H>\d{1,2})\s*[:：时]\s*(?P<M>\d{1,2})?)?")


def _cn_datetime(s: str) -> dt.datetime | None:
    """解析中文日期时间（豆瓣 RSS 写的是「2026年10月8日」，fromisoformat 不认）。"""
    m = _CN_DATE_RE.search(s)
    if m:
        y, mo = int(m.group("y")), int(m.group("m"))
        d = int(m.group("d") or 1)
        hh = int(m.group("H") or 0)
        mm = int(m.group("M") or 0)
        try:
            return dt.datetime(y, mo, d, hh, mm, tzinfo=CST)
        except ValueError:
            return None
    m = _CN_MD_RE.search(s)
    if m:
        mo, d = int(m.group("m")), int(m.group("d"))
        hh = int(m.group("H") or 0)
        mm = int(m.group("M") or 0)
        now = dt.datetime.now(CST)
        year = now.year
        try:
            if dt.datetime(year, mo, d, tzinfo=CST).date() < now.date():
                year += 1
            return dt.datetime(year, mo, d, hh, mm, tzinfo=CST)
        except ValueError:
            return None
    return None


def parse_ts(value: Any) -> dt.datetime | None:
    """解析多种时间戳形态：ISO 字符串、中文日期、unix 秒/毫秒、date。"""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return to_cst(value)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time(0, 0), tzinfo=CST)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e11:  # 毫秒
            ts /= 1000.0
        try:
            return dt.datetime.fromtimestamp(ts, tz=CST)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if re.fullmatch(r"\d{10,13}", s):
            return parse_ts(int(s))
        try:
            return to_cst(dt.datetime.fromisoformat(s.replace("Z", "+00:00")))
        except ValueError:
            pass
        # 中文日期（豆瓣 / 秀动 / 微博文案）
        return _cn_datetime(s)
    return None


def future_window(days: int | None = None) -> tuple[dt.datetime, dt.datetime]:
    """采集窗口：现在 → N 天后（只抓未来场次）。"""
    start = now_cst()
    return start, start + dt.timedelta(days=days or settings.future_window_days)


def ms_since(t0: float) -> int:
    import time

    return int((time.monotonic() - t0) * 1000)
