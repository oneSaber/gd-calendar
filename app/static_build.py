"""生成 GitHub Pages 用的**只读静态站点**。

为什么需要它：本项目是 FastAPI + SQLite 的**动态应用**（还有浏览器采集器），
而 GitHub Pages 只能托管静态文件。所以这里把数据库导出成 JSON，
让前端在「没有后端」的情况下也能完整浏览（筛选、月历、详情、来源跳转都可用）。

明确做不到的事（静态站点的固有限制，不是偷懒）：
  * **不能采集**：没有服务端，抓取逻辑跑不起来 → 数据靠本地生成后提交
  * **不能纠错投稿**：没有后端落库 → 隐藏入口
  * **不能更新数据**：同上
  * **.ics 订阅**：可以在浏览器里生成 .ics 下载（纯前端），但没有服务端订阅 URL

产物结构：
    docs/
      index.html  css/  js/           ← 直接复制 app/web
      data/occurrences.json           ← 场次（含 event/venue/lineup/tickets 展开）
      data/venues.json  stats.json  meta.json
      data/calendar/<YYYY-MM>.json    ← 月历点阵计数（前端零计算）

用法：
    python -m app.cli build-static                  # 输出到 docs/
    python -m app.cli build-static --out site       # 自定义目录
    python -m app.cli build-static --no-occurrences # 只出计数（体积小很多）
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas as S
from app.api import service
from app.config import settings
from app.db.models import Event, Occurrence, Venue
from app.utils import get_logger, now_cst

log = get_logger(__name__)

WEB_DIR = Path(__file__).parent / "web"

# 静态站点**只发布垂类内容**：地偶 / 女子乐队 / ACG。
#
# 为什么在导出阶段过滤而不是让前端筛：
#   1. 站点定位是「这个垂类的日历」，混进脱口秀、话剧、古典音乐会会稀释它；
#   2. 数据量小一个数量级，静态站更轻，也避免无关信息被转载。
# 判定是**或**关系（任一标记命中即保留），与界面上多选标记的「与」语义不同：
# 「与」是用户主动收窄，这里是站点范围。
VERTICAL_FLAGS = ("is_idol", "is_girl_band", "is_acg")


@dataclass
class PublishPolicy:
    """发布口径：**必须能证明这是演出**，不能只凭标题像演出。

    维护者定的规则（实测验证过它比标题可靠）：
      「检查演出名单，如果名单有团体就收录」

    依据：秀动的列表页**只在真有演出人员时才给阵容**，所以「阵容为空」
    本身就是强信号。实测对照：
      * 呆呆Otori 生诞祭        → 阵容 4 个地偶团体  ✅ 收录
      * 零~夜时巫女×京阿尼 ONLY → 阵容 4 个团体      ✅ 收录
      * 全职猎人同人only        → 阵容空            ❌ 排除
      * 卡拉彼丘同人ONLY·S1     → 阵容空            ❌ 排除
      * 金牌得主同人only        → 阵容空            ❌ 排除
    这些「同人ONLY」如果真有同人 Live，标题里根本没有线索能区分，
    阵容是唯一可靠判据。

    例外：`trusted` 里的名字是**已核实的地偶团体/企划**（见 artist_kb 的
    `_IDOL_GROUPS`），命中就无需阵容佐证 —— 那是事实，不是推断。
    """

    require_lineup: bool = True
    trusted: tuple[str, ...] = ()

    def accepts(self, item: Any) -> bool:
        """是否发布这条场次。"""
        ev = getattr(item, "event", None)
        if ev is None:
            return False
        if not any(bool(getattr(ev, f, False)) for f in VERTICAL_FLAGS):
            return False
        if not self.require_lineup:
            return True
        # 1) 已核实的具体团体/企划：无需阵容佐证
        title = (ev.title or "").lower()
        if any(t.lower() in title for t in self.trusted):
            return True
        # 2) 有其他演出人员 → 是演出
        return bool(getattr(item, "lineup", None))


# 静态站点里不该出现的文件（占位图/预览图之类）
_SKIP_COPY = {"preview.png"}


@dataclass
class BuildStats:
    occurrences: int = 0
    venues: int = 0
    months: int = 0
    files: list[str] = field(default_factory=list)
    total_bytes: int = 0

    def summary(self) -> str:
        mb = self.total_bytes / 1024 / 1024
        return (
            f"场次 {self.occurrences} · 场地 {self.venues} · 月份 {self.months} · "
            f"{len(self.files)} 个文件 / {mb:.1f} MB"
        )


def _dump(path: Path, payload: Any, stats: BuildStats) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ensure_ascii=False：中文直接存 UTF-8，文件小一半且可读
    # separators 去掉多余空格，进一步压体积
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    stats.files.append(path.name)
    stats.total_bytes += len(text.encode("utf-8"))


def _copy_web(out_dir: Path) -> None:
    """复制前端静态文件（保持相对路径，Pages 子目录下也能工作）。"""
    for src in WEB_DIR.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(WEB_DIR)
        if src.name in _SKIP_COPY or src.name.startswith("_"):
            continue
        # 测试产物不带走
        if src.suffix == ".png" and src.name.startswith("_preview"):
            continue
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _patch_index_for_static(out_dir: Path, features: dict[str, Any]) -> None:
    """给静态站的 index.html 注入运行时配置。

    注入**权威的**功能开关值，而不是让 `features.js` 里的默认值决定 ——
    那个文件是源码，可能和这次构建时的 `.env` 不一致。
    静态模式下 `api.js` 不会再请求 `/api/features`，所以这里必须是对的。
    """
    idx = out_dir / "index.html"
    html = idx.read_text(encoding="utf-8")
    cfg = json.dumps({"__STATIC__": True, "__FEATURES__": features}, ensure_ascii=False)
    inject = (
        f"<script>window.__DSH_CFG__={cfg};window.__STATIC__=true;"
        f"window.__FEATURES__=Object.assign({{}},window.__FEATURES__,"
        f"window.__DSH_CFG__.__FEATURES__);</script>"
    )
    # ⚠️ 必须放在 features.js **之后**：那个文件里是整体赋值
    # `window.__FEATURES__ = {...}`，放前面会被它整个覆盖掉（实测踩过）。
    marker = '<script src="js/features.js"></script>'
    if marker in html:
        html = html.replace(marker, marker + "\n" + inject, 1)
    else:  # 兜底：至少让 __STATIC__ 存在
        html = html.replace("</head>", inject + "\n</head>", 1)
    idx.write_text(html, encoding="utf-8")


def prepare_out_dir(out_dir: Path) -> None:
    """清空输出目录里**由本程序管理**的内容，但保留目录本身。

    为什么不直接 `shutil.rmtree(out_dir)`：Windows 下如果该目录正被占用
    （典型情况：用 `python -m http.server --directory docs` 预览静态站），
    删目录会抛 `PermissionError: [WinError 32] 另一个程序正在使用此文件`。
    只删子目录/文件就不会碰到这个限制。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    managed = ("data", "css", "js")
    for name in managed:
        d = out_dir / name
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    for name in ("index.html", ".nojekyll"):
        f = out_dir / name
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass  # 被占用就让它被覆盖写，不影响结果


async def build_static_site(
    session: AsyncSession,
    out_dir: Path,
    *,
    include_occurrences: bool = True,
    vertical_only: bool = True,
    policy: PublishPolicy | None = None,
) -> BuildStats:
    stats = BuildStats()
    if policy is None:
        from app.parsers.text_zh import _IDOL_GROUPS

        policy = PublishPolicy(require_lineup=True, trusted=tuple(_IDOL_GROUPS))

    # ---- 1) 场次（含展开的关联对象，前端零 join）----
    items, total = await service.query_occurrences(
        session,
        service.OccurrenceQuery(
            page=1, page_size=10_000, include_finished=True,
            exclude_other=False,  # 先全取，再按垂类标记过滤
        ),
    )
    if vertical_only:
        before = len(items)
        items = [
            i for i in items
            if any(bool(getattr(i.event, f, False)) for f in VERTICAL_FLAGS)
        ]
        log.info("垂类过滤（地偶/女子乐队/ACG）：%d → %d 场次", before, len(items))

    # 发布口径：必须能证明是演出（有阵容，或命中已核实的团体名）
    before = len(items)
    rejected = [i for i in items if not policy.accepts(i)]
    if policy.require_lineup:
        items = [i for i in items if policy.accepts(i)]
        log.info(
            "发布口径（需阵容佐证）：%d → %d 场次，排除 %d 条无法证明是演出的内容",
            before, len(items), len(rejected),
        )
        for i in rejected[:10]:
            log.info("  排除（无阵容佐证）：%s", (i.event.title or "")[:48])

    log.info("静态导出：%d 场次（库内共 %d）", len(items), total)

    if include_occurrences:
        payload = {
            "generated_at": S.iso_cst(now_cst()),
            "total": len(items),
            "items": [json.loads(i.model_dump_json()) for i in items],
        }
        _dump(out_dir / "data" / "occurrences.json", payload, stats)
        stats.occurrences = len(items)

    # ---- 2) 场地：只保留垂类场次真正用到的 ----
    used_venue_ids = {i.venue.id for i in items if i.venue and i.venue.id}
    venues = await service.list_venues(session, limit=5000)
    if vertical_only:
        venues = [v for v in venues if v.id in used_venue_ids]
    _dump(
        out_dir / "data" / "venues.json",
        {"items": [json.loads(v.model_dump_json()) for v in venues]},
        stats,
    )
    stats.venues = len(venues)

    # ---- 3) 统计（页脚）----
    # ⚠️ 不能直接用 overview_stats：那是**全库**计数，静态站只发垂类，
    # 页脚写「已收录 355 场」会与实际可见的 12 场对不上。
    occ_n = len(items)
    ev_n = len({i.event.id for i in items if i.event})
    ar_n = len({a.artist_id for i in items for a in (i.lineup or []) if a.artist_id})
    ve_n = len(venues)
    _, _, _, _, health = await service.overview_stats(session)
    _dump(
        out_dir / "data" / "stats.json",
        {
            "occurrences": occ_n,
            "events": ev_n,
            "artists": ar_n,
            "venues": ve_n,
            "generated_at": S.iso_cst(now_cst()),
            "sources": [json.loads(h.model_dump_json()) for h in health],
        },
        stats,
    )

    # ---- 4) 月历计数：从**已过滤的 items** 现算 ----
    #
    # ⚠️ 不能调 service.calendar_counts：那个查库、不知道垂类过滤，
    # 会让点阵显示有圆点、点进去却是空的（正是之前修过的「口径漂移」）。
    # 这里统一从同一份 items 推导，保证与列表口径一致。
    def counts_for(rows: list[Any], month: str) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for it in rows:
            if not it.start_at or it.start_at.strftime("%Y-%m") != month:
                continue
            if it.status == "cancelled":
                continue
            d = out.setdefault(it.start_at.strftime("%Y-%m-%d"),
                               {"band": 0, "idol": 0, "girl_band": 0, "acg": 0, "followed": 0})
            ev = it.event
            if ev and ev.is_idol:
                d["idol"] += 1
            else:
                d["band"] += 1
            if ev and ev.is_girl_band:
                d["girl_band"] += 1
            if ev and ev.is_acg:
                d["acg"] += 1
        return out

    months = sorted({i.start_at.strftime("%Y-%m") for i in items if i.start_at})
    for m in months:
        by_city: dict[str, Any] = {
            "": counts_for(items, m),
        }
        for city in sorted({i.city for i in items if i.city}):
            by_city[city] = counts_for([i for i in items if i.city == city], m)
        _dump(
            out_dir / "data" / "calendar" / f"{m}.json",
            {"month": m, "by_city": by_city},
            stats,
        )
        stats.months += 1

    # ---- 5) 功能开关（静态站固定关掉后端相关的）----
    features = {
        "ics": bool(settings.feature_ics),
        # 静态站没有后端，采集与投稿一定不可用
        "update": False,
        "submit": False,
        "static": True,
    }
    _dump(out_dir / "data" / "meta.json", {
        "generated_at": S.iso_cst(now_cst()),
        "app_version": "0.1.0",
        "features": features,
        "months": months,
        "cities": sorted({i.city for i in items if i.city}),
    }, stats)

    # ---- 6) 前端文件 + 静态标记 ----
    _copy_web(out_dir)
    _patch_index_for_static(out_dir, features)
    # Pages 默认会用 Jekyll 处理站点，下划线开头的文件会被忽略 —— 关掉它
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    log.info("静态站点已生成：%s（%s）", out_dir, stats.summary())
    return stats
