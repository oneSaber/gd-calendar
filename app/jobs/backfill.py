"""历史数据回填：按**当前**分类器重算 event 上的分类标记。

为什么需要：分类器会演进（例如新增「女子乐队」「ACG」两个标记），
但已有 event 行不会自动带上新标记 —— 用户就会看到「筛女子乐队一条都没有」。
这里用当前规则重算全部历史行，保证「新旧数据口径一致」。

用法：
    python -m app.cli backfill            # 重算分类标记
    python -m app.cli backfill --dry-run  # 只看会改多少，不落库
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event
from app.parsers import text_zh as tz
from app.utils import get_logger

log = get_logger(__name__)


@dataclass
class BackfillStats:
    total: int = 0
    changed: int = 0
    idol: int = 0
    girl_band: int = 0
    acg: int = 0
    samples: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"扫描 {self.total} 个活动 → 改动 {self.changed} 个；"
            f"当前标记分布：地偶 {self.idol} / 女子乐队 {self.girl_band} / ACG {self.acg}"
        )


async def backfill_flags(session: AsyncSession, *, dry_run: bool = False) -> BackfillStats:
    """按标题重算 is_idol / is_girl_band / is_acg 与 kind。

    ⚠️ 用标题（而非整篇 description）作为判定输入：与采集时的口径一致
    （采集器也是拿定稿标题 + 少量上下文来分类的）。
    """
    stats = BackfillStats()
    rows = (await session.execute(select(Event))).scalars().all()
    stats.total = len(rows)

    for ev in rows:
        title = ev.title_display or ev.title_raw or ""
        cls = tz.classify(title)

        if cls.is_idol:
            stats.idol += 1
        if cls.is_girl_band:
            stats.girl_band += 1
        if cls.is_acg:
            stats.acg += 1

        # ⚠️ 用**并集**而不是覆盖（实测踩过的坑）。
        #
        # 采集时的判定输入比标题多：`extract_from_text` 会带**场地上下文**
        # 与采集器传入的 `extra_context`。只用标题回填会把那些信号清掉 ——
        # 实测「10月东莞萤火虫# 地下偶像【夏日青空】」原本 `is_acg=True`
        # （来自场地/上下文），回填成 False 后**标记就丢了**。
        #
        # 取舍：
        #   * 并集 → 上下文带来的标记不会被误清（安全）
        #   * 代价 → 词表收紧产生的**过期误标**不能靠回填清掉；
        #     那类清理交给 `reclassify`（按阵容，会显式给结论）或手动处理，
        #     比在这里冒险覆盖要好。
        new_flags = (
            bool(ev.is_idol) or cls.is_idol,
            bool(ev.is_girl_band) or cls.is_girl_band,
            bool(ev.is_acg) or cls.is_acg,
        )
        old_flags = (bool(ev.is_idol), bool(ev.is_girl_band), bool(ev.is_acg))
        if new_flags != old_flags:
            stats.changed += 1
            if len(stats.samples) < 8:
                stats.samples.append(
                    f"{title[:34]}  {old_flags} → {new_flags}"
                )
            if not dry_run:
                ev.is_idol, ev.is_girl_band, ev.is_acg = new_flags

    if not dry_run:
        await session.flush()
    log.info("回填%s：%s", "（演练）" if dry_run else "", stats.summary())
    return stats
