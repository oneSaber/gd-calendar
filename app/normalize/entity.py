"""实体对齐与判重（对应 docs/01-总体设计.md §3.4）。

三层判重 + 冲突不静默覆盖：
  1. 强键：source_code + external_id（入库时查 occurrence_source）
  2. 指纹：场地 + 日期 + 归一化标题
  3. 相似：同城同日同场地，标题相似度 > 阈值 → 合并但提示冲突
  4. 绝不合并：同场地同日但标题完全不相似 → 视为「一天两场」，保留两条

场地匹配的铁律（实测教训）：
  **门牌/仓位不同即不同场地**。「MAO Livehouse 广州（太古仓店）」（太古仓 4 号仓）
  与「太空间 Livehouse」（太古仓码头 5 号仓）前缀完全相同，绝不能被合并。
  因此「太古仓」不允许作为任何场地的别名。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.utils import content_hash

# 这些词太泛，不能单独作为别名/匹配依据
_STOP_WORDS = {
    "太古仓", "livehouse", "live house", "现场", "剧场", "空间", "音乐现场",
    "广州", "深圳", "佛山", "东莞", "珠海", "中山", "惠州", "汕头",
}

# 仓位/门牌：用于「不同门牌 = 不同场地」的判定
_UNIT_RE = re.compile(r"(\d+)\s*号仓|(\d+)\s*号馆|(\d+)\s*号厅|(\d+)\s*店")

TITLE_SIMILARITY_THRESHOLD = 0.65


def venue_fingerprint(name: str) -> str:
    """场地指纹：归一化名 + 仓位号（保证 4 号仓 ≠ 5 号仓）。

    必须做全角→半角归一化，否则「（太古仓）」与「(太古仓)」会得到不同指纹，
    同一个场地会被拆成两个。
    """
    import unicodedata

    from app.parsers.text_zh import to_halfwidth

    norm = to_halfwidth(unicodedata.normalize("NFKC", name or ""))
    norm = re.sub(r"[\s\u3000]+", "", norm.lower())
    m = _UNIT_RE.search(norm)
    unit = "".join(g for g in (m.groups() if m else ()) if g)
    return f"{norm}#{unit}"


def title_similarity(a: str, b: str) -> float:
    """标题相似度（0-1）。

    用标准库 difflib，避免引入 pg_trgm 依赖；对中文按字符比较效果可接受。
    """
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def occurrence_fingerprint(
    venue_norm: str | None, day_iso: str | None, title_norm: str, lineup: list[str] | None = None
) -> str:
    """指纹 = hash(归一化场地 + 日期 + 归一化标题 + 排序后的阵容)。"""
    return content_hash(venue_norm or "", day_iso or "nodate", title_norm,
                        ",".join(sorted(lineup or [])))


def soft_fingerprint(venue_norm: str | None, day_iso: str | None, title_norm: str) -> str:
    """弱指纹：不含阵容，用于「同场地同日同标题」的快速命中。"""
    return content_hash(venue_norm or "", day_iso or "nodate", title_norm)


@dataclass
class MatchResult:
    kind: str          # exact | similar | none
    score: float = 0.0
    matched_id: int | None = None
    reason: str = ""


def choose_existing(
    candidates: list[tuple[int, str]],
    title_norm: str,
    *,
    threshold: float = TITLE_SIMILARITY_THRESHOLD,
) -> MatchResult:
    """在候选（id, title_norm）里选最匹配的一个。

    只有「足够相似」才合并；否则返回 none，由调用方新建一条 occurrence
    （这保证了「一天两场」不会被错误合并）。
    """
    if not candidates:
        return MatchResult(kind="none")
    best: tuple[float, int, str] | None = None
    for cid, ctitle in candidates:
        if ctitle == title_norm:
            return MatchResult(kind="exact", score=1.0, matched_id=cid, reason="标题归一化后完全一致")
        score = title_similarity(title_norm, ctitle)
        if best is None or score > best[0]:
            best = (score, cid, ctitle)
    assert best is not None
    score, cid, ctitle = best
    if score >= threshold:
        return MatchResult(
            kind="similar", score=round(score, 3), matched_id=cid,
            reason=f"标题相似度 {score:.2f} ≥ {threshold}",
        )
    return MatchResult(
        kind="none", score=round(score, 3),
        reason=f"最高相似度 {score:.2f} < {threshold}，视为不同场次（可能是一天两场）",
    )


# --------------------------------------------------------------------------- #
# 场地名匹配
# --------------------------------------------------------------------------- #

def match_venue(
    venue_raw: str | None,
    city: str | None,
    catalog: dict[str, list[tuple[int, str]]],
) -> tuple[int | None, float]:
    """把场地原文匹配到场地表。

    catalog: {归一化键: [(venue_id, name), ...]}，键包含：
      - 场地归一化名
      - 每个别名的归一化名
    返回 (venue_id | None, 置信度)。
    """
    if not venue_raw:
        return None, 0.0
    from app.parsers.text_zh import normalize_name

    norm = normalize_name(venue_raw)
    if not norm:
        return None, 0.0

    # 1) 精确命中
    hits = catalog.get(norm)
    if hits:
        vid, _name = hits[0]
        return vid, 1.0

    # 2) 包含匹配（「MAO永庆坊」→「MAOlivehouse广州永庆坊店」）
    #    注意：key 可能很长（完整场地名），所以下限放到 2；靠「仓位号一致」做否决保护。
    best: tuple[float, int] | None = None
    for key, items in catalog.items():
        if len(key) < 2:
            continue
        if key in norm or norm in key:
            score = min(len(key), len(norm)) / max(len(key), len(norm))
            # 仓位号冲突则否决（4 号仓 ≠ 5 号仓）
            if venue_fingerprint(key) != venue_fingerprint(norm):
                continue
            if best is None or score > best[0]:
                best = (score, items[0][0])
    if best is not None and best[0] >= 0.34:
        return best[1], round(best[0], 2)
    return None, 0.0


def is_ambiguous_alias(alias: str) -> bool:
    """判断别名是否过于泛化（禁止入库为别名）。

    实测：「太古仓」会同时指向 MAO 太古仓店与太空间，必须禁止。
    """
    key = (alias or "").strip().lower()
    return key in _STOP_WORDS or len(key) <= 2
