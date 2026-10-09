"""小红书采集器：用**你自己的登录态**读公开笔记。

## 合规口径（与 `scripts/xhs_publish.py` 一致）

小红书全部页面（含搜索结果）都要求登录，所以采集必须借助登录态。本模块
**只做**下面这些事，与该脚本同一条路：

  * 用**持久化 profile**（`%LOCALAPPDATA%\\gd-calendar\\xhs-profile`），
    登录一次可复用；需要时由维护者**自己扫码**
  * 走平台自己的 UI 页面，只**读**页面公开可见的内容
  * 不注入 Cookie、不伪造签名、不逆向私有接口、不改 `navigator.webdriver` 等指纹

## 采集什么（垂直相关，不是全站爬取）

按关键词搜官方账号与笔记，抽取**演出情报**：
「广州地偶图鉴」这类聚合账号、团体验出预告、漫展舞台、免费场地排期。

## 与微博的差异

XHS 是重前端渲染的 SPA，搜索结果**不发可拦截的干净 JSON**
（实测 `intercept_xhr` 拿不到 `search/notes`），所以采用**读渲染后 DOM** 的方式：
让页面自己加载完，再从卡片元素里抽取字段。这也是本模块的核心取舍。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.utils import get_logger, now_cst

log = get_logger(__name__)

SOURCE_CODE = "xiaohongshu"

BASE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "gd-calendar"
PROFILE = BASE_DIR / "xhs-profile"
COOKIES = BASE_DIR / "xhs-cookies.json"

SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={kw}&type=51"
EXPLORE_URL = "https://www.xiaohongshu.com/explore"

# 关键词：与微博的选取逻辑一致 —— 只取能产出**演出情报**的词。
# XHS 上的地偶内容以「图鉴/汇总/情报」类笔记为主，所以词表偏「聚合」。
SEARCH_KEYWORDS: list[str] = [
    "广州地偶",
    "广州地偶图鉴",
    "地偶 演出 情报",
    "广州 偶像 公演",
    "地王广场 偶像",
    "ACG 乐队 演出",
    "地偶 生诞祭",
]

# 小红书域名特征（判断当前页面是否还在小红书）
XHS_HOST_RE = re.compile(r"(^|\.)xiaohongshu\.com$")

# 笔记卡片上常见的噪音文本（UI 文案，不是内容）
_UI_NOISE = {
    "登录", "关注", "点赞", "收藏", "评论", "分享", "展开", "收起", "更多",
    "创作中心", "业务合作", "发现", "RED", "直播", "发布", "通知", "消息",
}


@dataclass
class XhsNote:
    """一条笔记的最小可用字段（只留对做日历有用的）。"""

    note_id: str = ""
    title: str = ""
    author: str = ""
    text: str = ""
    url: str = ""
    keyword: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id, "title": self.title, "author": self.author,
            "text": self.text, "url": self.url, "keyword": self.keyword,
        }


@dataclass
class XhsResult:
    notes: list[XhsNote] = field(default_factory=list)
    logged_in: bool = False
    keywords_done: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class XhsBrowser:
    """复用 `scripts/xhs_publish.py` 的浏览器封装（持久 profile + CDP）。

    直接 import 那个脚本里的 `Session`，**不重复实现**登录与 CDP 细节 ——
    否则「发布」和「采集」两条路的登录行为迟早漂移。
    """

    def __init__(self, port: int = 9222) -> None:
        scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from xhs_publish import Session as _Session  # type: ignore

        self._impl = _Session(port=port)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._impl, name)


def launch_profile_browser(*, port: int = 9222, headless: bool = True) -> bool:
    """起一个用持久 profile 的浏览器（后台）。

    `headless=False` 时可见 —— **需要扫码时用可见窗口**，否则用户没法扫。
    """
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    # 从现有脚本里借浏览器探测逻辑，避免到处找路径
    scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from cdp_fetch import find_browser  # type: ignore

    exe = find_browser()
    if not exe:
        log.warning("找不到 Edge/Chrome，无法起浏览器")
        return False

    args = [
        str(exe),
        f"--user-data-dir={PROFILE}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if headless:
        args.append("--headless=new")
    args.append(EXPLORE_URL)
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        log.warning("启动浏览器失败：%s", exc)
        return False
    return True


# --------------------------------------------------------------------------- #
# DOM 抽取
# --------------------------------------------------------------------------- #

# 读搜索结果里的笔记卡片。属性选择器刻意写得宽 —— XHS 的 class 名是
# 构建产物（带 hash），不能依赖具体类名，只能靠结构 + `href` 特征。
_EXTRACT_NOTES_JS = r"""
(function(){
  var out = [];
  var seen = {};
  // 笔记链接统一形如 /explore/<note_id> 或 /search_result/<note_id>
  var anchors = [].slice.call(document.querySelectorAll('a[href*="/explore/"],a[href*="/search_result/"]'));
  anchors.forEach(function(a){
    var m = /(?:explore|search_result)\/([0-9a-fA-F]{16,32})/.exec(a.getAttribute('href') || '');
    if(!m) return;
    var id = m[1];
    if(seen[id]) return;
    seen[id] = 1;
    var card = a.closest('section,div');
    var text = (card ? card.innerText : a.innerText) || '';
    out.push({ note_id: id, href: a.getAttribute('href') || '', text: text.slice(0, 400) });
  });
  return JSON.stringify(out);
})()
"""


# 读笔记详情：点击卡片打开弹层后取正文。
# ⚠️ 实测坑：**直接 nav() 到 /explore/<id> 会 404**（XHS 的路由要求由站点
#    自身跳转），所以只能点搜索结果里的真实卡片。
_EXTRACT_DETAIL_JS = r"""
(function(){
  var t = document.body.innerText || '';
  var box = document.querySelector('[role=dialog]')
         || document.querySelector('#noteContainer')
         || document.querySelector('.note-detail-mask');
  var body = box ? box.innerText : t;
  return JSON.stringify({
    url: location.href, title: document.title,
    len: body.length, text: body.slice(0, 3000)
  });
})()
"""


def open_note_detail(br: XhsBrowser, note_id: str, *, wait: float = 5.0) -> str:
    """在**当前搜索结果页**上点击指定笔记卡片，返回弹层正文。

    返回空串表示没打开（调用方应跳过，不要瞎猜正文）。
    """
    click_js = (
        "(function(){"
        "var a = document.querySelector('a[href*=\"%s\"]');"
        "if(!a) return 'no-card';"
        "a.scrollIntoView({block:'center'});"
        "a.click();"
        "return 'clicked';"
        "})()" % note_id
    )
    try:
        r = br.ev(click_js)
    except Exception:  # noqa: BLE001
        return ""
    if r != "clicked":
        return ""
    time.sleep(wait)
    try:
        raw = br.ev(_EXTRACT_DETAIL_JS)
        d = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:  # noqa: BLE001
        return ""
    text = str(d.get("text") or "")
    if not text or "无法浏览" in text or "/404" in str(d.get("url") or ""):
        return ""
    return text

def parse_card_text(text: str) -> tuple[str, str]:
    """从卡片文本里分 (标题, 作者)。

    XHS 搜索卡片的文本结构（实测）：
        标题
        作者名
        时间（如「6天前」「07-2:」）
        点赞数

    所以：**第一行是标题，第二行是作者**。别用「哪行更像人名」去猜 ——
    实测作者名可能是「广州地偶图鉴」（像标题）或「🍠610c0c01」（像 ID），
    按位置取才稳。

    ⚠️ 踩过的坑：原来只用「以『的笔记』结尾 / 以 @ 开头」判定作者，
    结果 105 条笔记的 author **全部为空**。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return "", ""
    title = lines[0] if len(lines[0]) >= 2 else ""
    author = ""
    for ln in lines[1:]:
        if ln in _UI_NOISE:
            continue
        # 跳过时间（「6天前」「07-2:」「昨天」）与纯数字（点赞数）
        if _REL_TIME_CARD_RE.match(ln) or ln.isdigit():
            continue
        author = ln
        break
    return title, author


# 卡片上的相对时间/日期串（「6天前」「1小时前」「07-2:」「昨天」）
_REL_TIME_CARD_RE = re.compile(
    r"^(\d+\s*(秒|分钟|小时|天|周|个月|月|年)前|昨天|前天|今天|"
    r"\d{1,2}-\d{1,2}.*|\d{4}-\d{1,2}-\d{1,2}.*|\d{1,2}:\d{2}.*)$"
)


# 已登录时导航栏会出现这些入口（实测截图确认）。
# ⚠️ 反过来**不能**用「页面含扫码字样」判断未登录：登录页的文案会残留在 DOM 里，
#    实测因此把「已登录」误判成「未登录」。
_LOGGED_IN_NAV = ("发布", "通知", "消息", "我")


def nav_visible(text: str) -> bool:
    """正文里是否出现已登录的导航栏入口（需同时命中 ≥3 个，避免误判）。"""
    return sum(1 for w in _LOGGED_IN_NAV if w in text) >= 3


def check_login(br: XhsBrowser) -> tuple[bool, str]:
    """可靠的登录判定：**看搜索页能不能看到内容**。

    ⚠️ 实测坑（两个方向都踩过）：
      * `Session.logged_in()` 只按 cookie **名字**判断 → 只在创作者中心登录过
        也会被判成已登录，但主站搜索页其实拿不到内容
      * 反过来用「页面含扫码字样」判未登录 → 登录页文案残留在 DOM 里，
        已登录也被误判

    所以判据改成：**页面上是否出现了只有登录后才有的导航入口**。
    """
    br.nav(SEARCH_URL.format(kw="地偶"), wait=9.0)
    time.sleep(2.5)
    try:
        body = str(br.ev("document.body.innerText.slice(0,600)") or "")
        url = str(br.url() or "")
    except Exception as exc:  # noqa: BLE001
        return False, f"读取页面失败：{exc}"

    if "/login" in url and "search_result" not in url:
        return False, "被重定向到登录页"
    if "登录后查看搜索结果" in body:
        return False, "主站未登录（只看到「登录后查看搜索结果」）"
    if not nav_visible(body):
        return False, "看不到登录后的导航栏（可能只登录了创作者中心）"
    return True, "主站已登录"


def collect_search(
    br: XhsBrowser, keyword: str, *, wait: float = 8.0
) -> tuple[list[XhsNote], bool]:
    """搜一个关键词，返回 (笔记列表, 是否已登录)。

    登录判定走 `check_login` 的同一套标志位（看导航栏，不看 cookie）——
    未登录时**不猜、不绕**，直接返回空并让调用方提示维护者扫码。
    """
    url = SEARCH_URL.format(kw=keyword)
    br.nav(url, wait=wait)
    time.sleep(2.5)

    try:
        body = str(br.ev("document.body.innerText.slice(0,600)") or "")
        cur = str(br.url() or "")
    except Exception:  # noqa: BLE001
        return [], False
    if "登录后查看搜索结果" in body or not nav_visible(body):
        return [], False
    if "/login" in cur and "search_result" not in cur:
        return [], False

    notes_raw = br.ev(_EXTRACT_NOTES_JS)
    try:
        items = json.loads(notes_raw) if isinstance(notes_raw, str) else []
    except Exception:  # noqa: BLE001
        items = []

    out: list[XhsNote] = []
    for it in items:
        title, author = parse_card_text(it.get("text") or "")
        out.append(
            XhsNote(
                note_id=str(it.get("note_id") or ""),
                title=title,
                author=author,
                text=(it.get("text") or "")[:400],
                url=f"https://www.xiaohongshu.com{it.get('href') or ''}",
                keyword=keyword,
            )
        )
    return out, True


def collect(
    keywords: list[str] | None = None,
    *,
    wait: float = 8.0,
    limit_per_kw: int = 20,
) -> XhsResult:
    """按关键词采集（需要已登录的持久 profile）。"""
    res = XhsResult()
    kws = keywords or SEARCH_KEYWORDS
    br = XhsBrowser()
    seen: set[str] = set()
    try:
        for kw in kws:
            try:
                notes, ok = collect_search(br, kw, wait=wait)
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"{kw}: {exc}")
                log.warning("小红书搜索「%s」失败：%s", kw, exc)
                continue
            if not ok:
                res.logged_in = False
                log.warning("小红书未登录：请先 `python scripts/xhs_publish.py login` 扫码")
                return res
            res.logged_in = True
            res.keywords_done.append(kw)
            for n in notes[:limit_per_kw]:
                if n.note_id and n.note_id not in seen:
                    seen.add(n.note_id)
                    res.notes.append(n)
            log.info("小红书「%s」：%d 条笔记", kw, len(notes))
    finally:
        try:
            br.close()
        except Exception:  # noqa: BLE001
            pass
    log.info("小红书合计 %d 条笔记（去重后）", len(res.notes))
    return res


def save_notes(notes: list[XhsNote], path: Path) -> None:
    """把笔记落地成 JSON（供人工/后续解析，不直接进库）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": now_cst().isoformat(),
        "count": len(notes),
        "items": [n.as_dict() for n in notes],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
