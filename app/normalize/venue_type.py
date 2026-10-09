"""场地类型分类：从场地名 + 地址推断类型。

## 为什么需要

库里 148 个场地**全部**标成 `livehouse`（占位值），这让「免费场地 / 漫展场馆 /
商场中庭」这类关键区分完全丢失。而本项目的目标之一就是**免费演出场地**：
地王广场、商场中庭、公园、车站旁这类场地不上售票平台，只能靠社交媒体发现。

## 分类口径

按「是否售票 / 场地性质」分，而不是按建筑形态：

| 类型 | 说明 | 典型 |
| --- | --- | --- |
| `livehouse` | 专业演出场地，售票 | MAO / SDlivehouse / 太空间 |
| `mall` | 商场/中庭/广场，多为**免费** | 地王广场、正佳广场、无限极广场 |
| `park` | 公园/户外，多为免费 | 海心沙、海珠湖 |
| `convention` | 会展中心/展馆 | 广交会展馆、松山湖展演中心 |
| `theater` | 剧院/剧场/音乐厅 | 星海音乐厅、南山文化馆剧院 |
| `bar` | 酒吧/Live Bar | ROOTS HOUSE |
| `campus` | 高校/文化馆/图书馆 | 中大、文化馆 |
| `cinema` | 影院 | 中影南方影城 |
| `studio` | 排练房/工作室/剧场空间 | 野生神奇剧场 |
| `unknown` | 判断不了（**不猜**） | |

`is_free_venue` 只对 `mall` / `park` / `campus` 为真 —— 这几类**通常不售票**，
是本项目要重点覆盖的免费场地。
"""

from __future__ import annotations

import re

# 类型 -> (关键正则, 置信度)。**顺序敏感**：先匹配的先判定。
#
# ⚠️ 顺序踩过的坑（实测）：`campus` 里的「中大」会命中
# 「MAO Livehouse广州**中大**店」，把它误判成免费场地 —— 实际它是
# 要买票的 Livehouse。同理「大学城」会命中「CH8蛙厂演艺中心(大学城店)」。
# 所以：**专业演出场地的规则必须排在 campus 之前**，且 campus 不用歧义简称。
RULES: tuple[tuple[str, re.Pattern[str], float], ...] = (
    # 专业演出场地 —— 优先级最高，因为它的名字里常带地名（中大/大学城/广场）
    ("livehouse", re.compile(
        r"livehouse|live\s?house|live\s?bar|living\s?house|"
        r"MAO|SDlivehouse|太空间|池沼|CHIZHAO|"
        r"HOU\s?LIVE|TU凸|191space|声音共和|B10|飞livehouse|"
        r"CH8|OMNI\s?SPACE|疆进酒|ALSOLIVE|SoFun\s?Live|ON\s?AIR音乐现场|"
        r"光芒enlightening|西瓜剧场|音乐现场",
        re.I), 0.95),
    # 会展/展馆（漫展主场）
    ("convention", re.compile(
        r"会展中心|展览中心|展馆|博览|国际会展|展演中心|会议中心|"
        r"琶洲|广交会|保利世贸|intertextile",
        re.I), 0.9),
    # 剧院 / 剧场 / 音乐厅
    ("theater", re.compile(
        r"剧院|剧场|音乐厅|大戏院|艺术中心|文化中心|演出厅|歌剧院|"
        r"文化馆|礼堂|体育馆|体育中心|赛车场",
        re.I), 0.9),
    # 影院
    ("cinema", re.compile(r"影城|电影院|CGV|万达影|飞扬影城|中影", re.I), 0.9),
    # 酒吧 / Live Bar
    ("bar", re.compile(r"酒吧|酒馆|ROOTS HOUSE|清吧|PUB", re.I), 0.8),
    # 商场 / 广场 / 中庭（免费场地主力）
    ("mall", re.compile(
        r"广场|商场|中庭|购物中心|天街|万科|正佳|天环|时尚天河|中华广场|"
        r"无限极|欢乐港湾|皇庭|CPARK|智汇PARK|城市广场|商业",
        re.I), 0.85),
    # 公园 / 户外（免费）
    ("park", re.compile(
        r"公园|湖畔|海珠湖|海心沙|绿道|沙滩|江边|湿地",
        re.I), 0.85),
    # 高校 / 图书馆（免费）—— 只用明确词，不用「中大」这种歧义简称
    ("campus", re.compile(
        r"大学|学院|图书馆|校园|学生活动中心|大学城",
        re.I), 0.8),
    # 排练房 / 工作室 / 小空间
    ("studio", re.compile(
        r"工作室|排练|studio|空间|基地|创客|创意园|园区|影视城",
        re.I), 0.7),
)

# 这些类型**通常不售票**（免费场地）
FREE_TYPES = frozenset({"mall", "park", "campus"})


def classify_venue_type(name: str | None, address: str | None = None) -> tuple[str, float]:
    """返回 (类型, 置信度)。判断不了返回 ("unknown", 0.0) —— **不猜**。"""
    hay = f"{name or ''} {address or ''}".strip()
    if not hay:
        return "unknown", 0.0
    for kind, pat, conf in RULES:
        if pat.search(hay):
            return kind, conf
    return "unknown", 0.0


def is_free_venue(venue_type: str | None) -> bool:
    """该类型的场地是否通常免费入场。"""
    return (venue_type or "") in FREE_TYPES
