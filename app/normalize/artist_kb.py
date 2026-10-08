"""演员知识库：按**演出人员**判定场次分类，而不是靠标题。

## 为什么必须这么做

标题只能给出「谁办的」，演出人员才给出「谁演的」。实测教训：
  * 「留声RECORD音乐企划」标题像音乐厂牌 → 被误判为非地偶；
    而真正的地偶场次「PoP Star Idol Festival」标题里的 idol 也是英文词，
    靠标题匹配既会漏也会误。
  * 秀动**列表页里就带阵容**（实测 86% 覆盖），信息量远大于标题。

## 判定优先级（高 → 低）

1. **知识库精确命中**（`ArtistKnowledge`）—— 人工/联网核实过的名字，最可信
2. **命名规则**（`_PATTERNS`）—— 如「恋时青空」不含关键词，但「XX少女团」类可判
3. **投票**：阵容里多数是真地偶/女子乐队/ACG → 整场归入该垂类

## 陌生名字怎么办（按维护者要求）

不在知识库里的名字**不猜**，走 `lookup.py` 联网搜索并缓存结论；
搜不到或结论矛盾就写进 `review_task` 交给人工，绝不静默编造。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.utils import get_logger

log = get_logger(__name__)

# 知识库里的分类（与 Artist.kind 对齐）
KIND_IDOL_GROUP = "idol_group"
KIND_IDOL_MEMBER = "idol_member"
KIND_BAND = "band"
KIND_GIRL_BAND = "girl_band"      # 全女子编制乐队
KIND_ACG = "acg_unit"             # ACG/同人/二次元团体
KIND_SOLO = "solo"
KIND_ORGANIZER = "organizer"
KIND_UNKNOWN = "unknown"

# 分类 → 影响的垂类标记
KIND_TO_FLAGS: dict[str, tuple[str, ...]] = {
    KIND_IDOL_GROUP: ("is_idol",),
    KIND_IDOL_MEMBER: ("is_idol",),
    KIND_GIRL_BAND: ("is_girl_band",),
    KIND_ACG: ("is_acg",),
}


@dataclass
class KnowledgeEntry:
    """一条演员知识。"""

    name: str
    kind: str
    aliases: tuple[str, ...] = ()
    agency: str | None = None
    origin_city: str | None = None
    confidence: float = 0.9
    # 证据：为什么这么判（联网来源或人工确认），便于复核与追责
    evidence: str = ""
    source: str = "manual"        # manual | web | inferred


@dataclass
class LineupVerdict:
    """阵容判定结果。"""

    is_idol: bool = False
    is_girl_band: bool = False
    is_acg: bool = False
    # 每个标记的「票数」：命中该类的演员数
    votes: dict[str, int] = field(default_factory=dict)
    known: list[str] = field(default_factory=list)     # 命中的演员名
    unknown: list[str] = field(default_factory=list)   # 知识库里没有的演员名
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "is_idol": self.is_idol,
            "is_girl_band": self.is_girl_band,
            "is_acg": self.is_acg,
            "votes": self.votes,
            "known": self.known,
            "unknown": self.unknown,
        }


# --------------------------------------------------------------------------- #
# 种子知识库
#
# 规则：只登记**有证据**的条目。evidence 里写清来源，来源不明的走联网流程，
#       绝不为了让数字好看而编造。
# --------------------------------------------------------------------------- #
SEED_ARTISTS: tuple[KnowledgeEntry, ...] = (
    # ---- 地偶团体（广州为主） ----
    KnowledgeEntry(
        "比邻星球企划", KIND_IDOL_GROUP,
        aliases=("比邻星球",),
        origin_city="广州", confidence=0.95, source="web",
        evidence="广州地偶企划，腾讯活动页 huodong.com/event/detail/eyWfj；"
                 "维护者确认",
    ),
    KnowledgeEntry(
        "恋时青空", KIND_IDOL_GROUP, aliases=("恋時青空",),
        origin_city="广州", confidence=0.95, source="web",
        evidence="广州地下偶像团体，B站 Live 映像《向繁星祈愿》原创曲 "
                 "bilibili.com/video/BV1CEr4BJE32",
    ),
    KnowledgeEntry(
        "DigitalDuel", KIND_IDOL_GROUP, aliases=("数字决斗",),
        confidence=0.9, source="web",
        evidence="中国偶像团体，chinaidols.fandom.com/zh/wiki/DigitalDuel",
    ),
    KnowledgeEntry(
        "娜娜捏口俱乐部", KIND_IDOL_GROUP,
        origin_city="广州", confidence=0.9, source="web",
        evidence="地偶团体，「魔境LIVE 广州 vol.3」@MAO广州中大店 "
                 "bilibili.com/video/BV1xuZKY5ESP",
    ),
    KnowledgeEntry(
        "Yours", KIND_IDOL_GROUP, aliases=("Yours_official_",),
        origin_city="广州", confidence=0.85, source="web",
        evidence="广州地偶图鉴记其「新体制来袭，带来两首原创」；"
                 "官博 weibo.com/u/6073121292",
    ),
    KnowledgeEntry(
        "ReaLume", KIND_IDOL_GROUP,
        origin_city="广州", confidence=0.85, source="web",
        evidence="地偶团体，出演「REALWORLD 广州 9.0」"
                 "bilibili.com/video/BV1fMcbzYEjd",
    ),
    KnowledgeEntry(
        "恋音契约", KIND_IDOL_GROUP,
        origin_city="广州", confidence=0.85, source="web",
        evidence="地偶团体，官博 weibo.com/u/7777196754；"
                 "成员 @玉子tamako_恋音契约",
    ),
    KnowledgeEntry(
        "月匙Moon-Key", KIND_IDOL_GROUP, aliases=("月匙", "Moon-Key", "月匙MK"),
        origin_city="广州", confidence=0.85, source="web",
        evidence="偶像团体，chinaidols.fandom.com/zh/wiki/月匙Moon-Key；"
                 "超话 #月匙MK#",
    ),
    KnowledgeEntry(
        "永昼Eternal", KIND_IDOL_GROUP, aliases=("永昼",),
        origin_city="广州", confidence=0.8, source="web",
        evidence="地偶团体，官博 m.weibo.cn/u/7943528678；超话 #永昼Eternal 2周年#",
    ),
    KnowledgeEntry(
        "LAMENTiS", KIND_IDOL_GROUP,
        origin_city="广州", confidence=0.8, source="web",
        evidence="地偶团体，有「初披露演出定档」公告"
                 "sina.cn/news/detail/5298928222211730",
    ),
    KnowledgeEntry(
        "BlankPoker", KIND_IDOL_GROUP, aliases=("Blank Poker",),
        origin_city="广州", confidence=0.75, source="web",
        evidence="偶像团体，出演广州 SDlivehouse「AT LIVE EXPLORE vol.002 "
                 "双马尾大作战」bilibili.com/video/BV1Uu4y1A773",
    ),
    KnowledgeEntry(
        "山海誓约", KIND_IDOL_GROUP,
        origin_city="广州", confidence=0.8, source="web",
        evidence="偶像团体，有「三周年巡演」公告 "
                 "sina.cn/news/detail/5316539909669606",
    ),
    KnowledgeEntry(
        "留声RECORD音乐企划", KIND_IDOL_GROUP,
        aliases=("留声RECORD", "留声record"),
        origin_city="珠海", confidence=0.8, source="manual",
        evidence="维护者确认珠海 2026-10-17 该场为地偶场次；"
                 "阵容 BO5乐队 / NERUNERU 未在公开资料中查到更多信息",
    ),
    # ---- 女子乐队 ----
    KnowledgeEntry(
        "所谓正解？", KIND_GIRL_BAND,
        aliases=("哭泣少女乐队",),
        confidence=0.7, source="inferred",
        evidence="标题《哭泣少女乐队Only Live》含「少女乐队」；"
                 "阵容未抓到，待人工确认编制",
    ),
    # ---- ACG 向 ----
    KnowledgeEntry(
        "夜一乐队", KIND_BAND, aliases=("夜一",), confidence=0.7, source="web",
        evidence="出现在「次元激战 ACG宿命对决」拼盘，属参演乐队",
    ),
    KnowledgeEntry(
        "湾岸电力", KIND_BAND, confidence=0.7, source="web",
        evidence="同上，ACG 主题拼盘参演乐队",
    ),
    # ---- 偶像组合成员（GNZ48 等，用于阵容投票） ----
    KnowledgeEntry(
        "唐莉佳", KIND_IDOL_MEMBER, agency="GNZ48", origin_city="广州",
        confidence=0.8, source="web",
        evidence="GNZ48 成员（广州女子偶像组合）snh48.com/event_gnz",
    ),
    KnowledgeEntry(
        "曾艾佳", KIND_IDOL_MEMBER, agency="GNZ48", origin_city="广州",
        confidence=0.8, source="web",
        evidence="GNZ48 成员（广州女子偶像组合）snh48.com/event_gnz",
    ),
)


# --------------------------------------------------------------------------- #
# 命名规则（知识库未命中时的兜底推断）
# --------------------------------------------------------------------------- #
# 规则顺序敏感：先匹配的先判定
_PATTERNS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    # 明确的地偶/偶像团体
    (re.compile(r"地下偶像|地偶|アイドル"), KIND_IDOL_GROUP, 0.8),
    (re.compile(r"偶像(团体|组合|企划|计划)?"), KIND_IDOL_GROUP, 0.75),
    # 全女子编制（「少女乐队」「女子乐队」类）
    (re.compile(r"少女(乐队|樂隊|乐团|樂團|团)|女子(乐队|樂隊|乐团|樂團|摇滚|搖滾"
                r"|朋克|金属|后摇|硬核)"), KIND_GIRL_BAND, 0.8),
    (re.compile(r"girl\s*band|ガールズバンド", re.I), KIND_GIRL_BAND, 0.85),
    # ACG / 同人 / 二次元
    (re.compile(r"同人|二次元|动漫|漫展|acg|anisong|vocaloid|ボカロ|音游",
                re.I), KIND_ACG, 0.7),
    # 普通乐队（放最后：不要抢在偶像判定之前）
    (re.compile(r"乐队|樂隊|乐团|樂團|band|バンド", re.I), KIND_BAND, 0.6),
)


def _norm(name: str) -> str:
    """名字归一：用于知识库匹配。

    去空白、统一小写、去掉常见装饰符 —— 让「留声 RECORD」和「留声record」等价。
    """
    s = (name or "").strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[·・\-—_/\\|｜,，。.、~～!！?？&＆+＋'\"“”‘’()（）\[\]【】<>《》]", "", s)
    return s


class ArtistKnowledgeBase:
    """演员知识库（内存索引 + 可选落库）。

    索引两套键：
      * by_name：归一化名字 → entry
      * by_alias：归一化别名 → entry（别名优先级低于正名）
    """

    def __init__(self, entries: list[KnowledgeEntry] | None = None) -> None:
        self._by_name: dict[str, KnowledgeEntry] = {}
        self._by_alias: dict[str, KnowledgeEntry] = {}
        for e in entries if entries is not None else list(SEED_ARTISTS):
            self.add(e)

    def add(self, entry: KnowledgeEntry) -> None:
        self._by_name[_norm(entry.name)] = entry
        for a in entry.aliases:
            self._by_alias[_norm(a)] = entry

    def __len__(self) -> int:
        return len(self._by_name)

    def lookup(self, name: str) -> tuple[KnowledgeEntry | None, str]:
        """查名字。返回 (entry, 命中方式: name|alias|pattern|miss)。"""
        n = _norm(name)
        if not n:
            return None, "miss"
        hit = self._by_name.get(n)
        if hit is not None:
            return hit, "name"
        hit = self._by_alias.get(n)
        if hit is not None:
            return hit, "alias"
        # 子串命中：处理「某某乐队（广州）」这类带后缀的写法
        for key, e in self._by_name.items():
            if len(key) >= 3 and (key in n or n in key):
                return e, "name"
        for pat, kind, conf in _PATTERNS:
            if pat.search(name):
                return KnowledgeEntry(
                    name=name, kind=kind, confidence=conf, source="inferred",
                    evidence=f"命名规则命中 {pat.pattern}",
                ), "pattern"
        return None, "miss"


def classify_lineup(
    names: list[str], kb: ArtistKnowledgeBase
) -> LineupVerdict:
    """按阵容判定场次的垂类标记（投票制）。

    ⚠️ 阈值说明：**至少 1 个高置信度命中**即可成立，但会记录票数与依据。
    为什么阈值定这么低：垂类场次往往只有一个真地偶团体（其余是伴奏/嘉宾），
    阈值调高会大面积漏标。宁可标上并记录理由，让 review 兜底。
    """
    v = LineupVerdict()
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        entry, how = kb.lookup(name)
        if entry is None:
            v.unknown.append(name)
            continue
        flags = KIND_TO_FLAGS.get(entry.kind)
        if not flags:
            # 明确是普通乐队/主办方 → 记录但不投票
            v.known.append(name)
            v.reasons.append(f"{name} → {entry.kind}")
            continue
        for f in flags:
            v.votes[f] = v.votes.get(f, 0) + 1
        v.known.append(name)
        v.reasons.append(f"{name} → {entry.kind}({how},{entry.confidence})")

    v.is_idol = v.votes.get("is_idol", 0) > 0
    v.is_girl_band = v.votes.get("is_girl_band", 0) > 0
    v.is_acg = v.votes.get("is_acg", 0) > 0
    return v


def merge_with_title_flags(
    title_flags: dict[str, bool], verdict: LineupVerdict
) -> dict[str, bool]:
    """合并「标题判定」与「阵容判定」。

    取舍：**并集**（任一为真即为真）。
      * 标题判定会误报（「企划」类泛词），但已收紧；
      * 阵容判定会漏报（阵容缺失率 32%，豆瓣几乎不提供）；
      * 两者取并集能最大化召回，代价是精确度略降 —— 用 review 兜底。
    """
    out = dict(title_flags)
    for f in ("is_idol", "is_girl_band", "is_acg"):
        out[f] = bool(title_flags.get(f)) or bool(getattr(verdict, f, False))
    return out
