"""从演员知识库发现 B站 官方账号（供动态通道采集）。

## 现状：**实验性，尚未跑通**（如实记录，不假装可用）

目标很明确且有价值：地偶团体的**官方 B站账号**会发带阵容的演出预告，
这是「按演出人员分类」的理想数据源。

实测到的障碍（2026-10）：
  1. `search.bilibili.com/upuser` 的结果**不发我能拦截的 XHR**
     （`intercept_xhr(url, "search/type")` 返回 ok=False、0 条 payload），
     所以拿不到 JSON 里的 mid。
  2. 该页面的 `render()` 文本里**确实有 mid**（实测能看到
     「恋时青空_Official」等），但文本是「昵称 + 粉丝数」的混合行，
     靠文本解析太脆弱 —— 按本项目「宁缺勿错」的原则，宁可为空也不猜。
  3. `BrowserFetcher` 只有 render / intercept_xhr / screenshot，
     **没有 `cmd`**，无法直接执行 JS 去读 DOM 属性。

保留本模块的价值：
  * `_name_matches()` 的名字匹配逻辑是**可用的**（能过滤「壁纸站」这类同名噪音），
    有测试覆盖；
  * 等找到可靠的 mid 获取途径（例如渲染时捕获 `space.bilibili.com/<mid>`
    链接、或用 CDP 直接读 DOM），本模块的落库与调度逻辑可以直接复用。

在此之前，B站 的**会员购通道**（已跑通）仍是 B站 的主力来源。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artist
from app.utils import get_logger, now_cst

log = get_logger(__name__)

SEARCH_URL = "https://search.bilibili.com/upuser?keyword={kw}"

# 知识库里可能混进应援物/周边名（如「Koyo_Digitalduel-1018生诞祭版」），
# 这些不是团体，拿去搜账号只会浪费请求。
_NON_GROUP_RE = re.compile(
    r"应援|應援|周边|周邊|特典|物贩|物販|限定版|生诞祭版|生誕祭版|"
    r"纪念版|紀念版|ver\.?\s*\d|_\d{4}|-\d{4}|\d{4}版",
    re.I,
)

# 搜索结果里的通用噪音账号（同名但无关）
_NOISE_ACCOUNTS = {"壁纸站", "哔哩哔哩", "bilibili", "官方", "直播"}

_READ_USERS = r"""
(function(){
  var out = [];
  [].slice.call(document.querySelectorAll('a[href*="space.bilibili.com"]')).forEach(function(a){
    var m = /space\.bilibili\.com\/(\d+)/.exec(a.getAttribute('href') || '');
    if(!m) return;
    var txt = (a.innerText || '').trim();
    if(txt) out.push({mid: m[1], text: txt.slice(0, 80)});
  });
  var seen = {}, uniq = [];
  out.forEach(function(x){ if(!seen[x.mid]){ seen[x.mid]=1; uniq.push(x); } });
  return JSON.stringify(uniq.slice(0, 8));
})()
"""


@dataclass
class DiscoveredAccount:
    mid: str
    name: str
    matched: bool


@dataclass
class DiscoveryResult:
    found: list[DiscoveredAccount] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _name_matches(group_name: str, account_name: str) -> bool:
    """账号名与团体名是否对得上（容忍 _Official 之类的后缀）。"""
    g = re.sub(r"[\s_\-·・]+", "", (group_name or "").lower())
    a = re.sub(r"[\s_\-·・]+", "", (account_name or "").lower())
    if not g or not a:
        return False
    if a in _NOISE_ACCOUNTS:
        return False
    a_core = re.sub(r"(official|公式|官方|_official)$", "", a)
    return g in a_core or a_core in g


async def search_up_accounts(browser: object, group_name: str, *, wait: float = 9.0) -> list[dict]:
    """在 B站 搜 UP 主，返回 [{mid, text}]。

    ⚠️ 实现要点（踩过的坑）：
      * `BrowserFetcher.render()` 返回**渲染后的文本**，不是 HTML ——
        所以没法在里面找 `space.bilibili.com/<mid>` 链接。
      * `BrowserFetcher` 只有 render / intercept_xhr / screenshot，**没有 `cmd`**，
        不能直接执行 JS。
    因此改用 `intercept_xhr` 拦搜索接口的 JSON（里面有 mid 与昵称）。
    """
    import json

    url = SEARCH_URL.format(kw=group_name)
    # ⚠️ match 必须**具体**：写成 `web-interface` 会连 suggest / nav 一起拦下来
    # （实测拦截到的全是 suggest 与 nav，一条账号都提取不到）。
    # UP 主搜索的真实接口是 `x/web-interface/wbi/search/type`。
    res = await browser.intercept_xhr(  # type: ignore[attr-defined]
        url, "search/type", wait
    )
    # ⚠️ intercept_xhr 的 payload 是 **list**：[{"url": ..., "json": ...}]
    # （不是 dict），别写错。
    out: list[dict] = []
    for hit in res.payload or []:
        data = hit.get("json") if isinstance(hit, dict) else None
        if not isinstance(data, dict):
            continue
        for u in ((data.get("data") or {}).get("result") or []):
            if isinstance(u, dict) and u.get("mid"):
                out.append({"mid": str(u["mid"]), "text": str(u.get("uname") or "")})
    if out:
        return out

    # 兜底：渲染文本里按行找「昵称 + 粉丝数」太脆弱，宁可为空也不猜
    await asyncio.sleep(0.5)
    return []


async def discover_accounts(
    session: AsyncSession,
    browser: object,
    *,
    kinds: tuple[str, ...] = ("idol_group", "girl_band", "acg_unit"),
    limit: int | None = None,
    dry_run: bool = False,
) -> DiscoveryResult:
    """为知识库里的垂类团体发现 B站 官方账号，写进 artist.links。"""
    rows = (
        await session.execute(select(Artist).where(Artist.kind.in_(kinds)))
    ).scalars().all()
    # 知识库里可能混进应援物/周边名（如「Koyo_Digitalduel-1018生诞祭版」），
    # 这些不是团体，不该拿去搜账号。
    rows = [a for a in rows if not _NON_GROUP_RE.search(a.name or "")]
    if limit:
        rows = rows[:limit]

    out = DiscoveryResult()
    for a in rows:
        links = dict(a.links or {})
        if links.get("bilibili_mid"):
            out.skipped.append(f"{a.name}（已有 mid）")
            continue
        try:
            users = await search_up_accounts(browser, a.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("搜索 %s 的 B站 账号失败：%s", a.name, exc)
            out.skipped.append(f"{a.name}（搜索异常）")
            continue

        hit = next(
            (u for u in users if _name_matches(a.name, u.get("text", ""))), None
        )
        if hit is None:
            out.skipped.append(f"{a.name}（未找到匹配账号）")
            continue

        out.found.append(
            DiscoveredAccount(mid=hit["mid"], name=hit["text"], matched=True)
        )
        log.info("发现账号：%s → mid=%s (%s)", a.name, hit["mid"], hit["text"])
        if not dry_run:
            links["bilibili_mid"] = str(hit["mid"])
            links["bilibili_name"] = hit["text"]
            links["bilibili_found_at"] = now_cst().isoformat()
            a.links = links

    if not dry_run:
        await session.flush()
    return out


