"""聚合号 / 场地情报号 / 日程工具号的清单。

## 为什么单独放一个模块

这些账号**不是演出团体**，所以不该进 `artist_kb.SEED_ARTISTS`（那会影响
「按阵容判定分类」的逻辑）。但它们是**情报来源**：图鉴、速览、场馆统计、
排期盘点 —— 免费场地（地王广场这类）的排期只能靠它们。

## 来源

反向搜索与账号发现的**实测**结果（2026-10）：
  * `scripts/xhs_artist_accounts.py` —— 按团体名反查小红书账号
  * `scripts/probe_weibo_accounts.py` —— 微博搜索发现发布账号

## 维护方式

新增账号时**必须**在 `evidence` 里写明发现它的关键词或笔记标题，
否则无法复核它为什么值得追。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InfoAccount:
    """一个情报账号。"""

    name: str
    platform: str          # xhs | weibo
    kind: str              # aggregator（图鉴/速览）| venue（场馆情报）| tool（排期工具）| organizer
    evidence: str          # 发现依据（关键词 / 笔记标题），便于复核
    uid: str | None = None  # 微博 uid（小红书用账号名即可）


# 小红书：聚合与情报号
XHS_INFO_ACCOUNTS: tuple[InfoAccount, ...] = (
    InfoAccount(
        "广州地偶图鉴", "xhs", "aggregator",
        evidence="搜「广州地偶」时 14 条命中，含「2026广州地偶图鉴更新第27弹」"
                 "「广州国庆偶活速览🎤25场先收藏」——周更聚合，覆盖整周场次",
    ),
    InfoAccount(
        "KingMaker", "xhs", "aggregator",
        evidence="「国庆&中秋 华南地偶活动汇总帖」",
    ),
    InfoAccount(
        "千城忆梦", "xhs", "venue",
        evidence="「广州地下偶像常去场馆统计」——场馆维度情报",
    ),
    InfoAccount(
        "广州城市活动Pro", "xhs", "aggregator",
        evidence="「广州漫展人集合！🎉8月—11月广州漫展时间表」",
    ),
    InfoAccount(
        "飞鸟与海", "xhs", "tool",
        evidence="「地偶演出太难找，我做了一个演出日历」——同类项目，可参考口径",
    ),
    InfoAccount(
        "不取对象", "xhs", "tool",
        evidence="「2026 ACG&二游 音乐会/演唱会排期盘点」",
    ),
    InfoAccount(
        "小快子📷（摄影版）", "xhs", "aggregator",
        evidence="反向搜索 12 个团体时出现 8 次——跨团体高频的现场记录者，"
                 "可用于发现新团体",
    ),
    InfoAccount(
        "悠哉日常大王", "xhs", "aggregator",
        evidence="反向搜索时出现 13 次（最高频），覆盖 DigitalDuel / LAMENTiS 等",
    ),
)

# 微博：聚合与情报号
WEIBO_INFO_ACCOUNTS: tuple[InfoAccount, ...] = (
    InfoAccount(
        "Garry_Liu_", "weibo", "aggregator",
        evidence="「广州地偶图鉴」系列作者——搜「广州地偶图鉴」「偶活速览」"
                 "两个词都命中它",
        uid="5861861144",
    ),
    InfoAccount(
        "CrossingX克珞星", "weibo", "aggregator",
        evidence="搜「偶活速览」命中",
    ),
    InfoAccount(
        "元气弹偶像工作室", "weibo", "organizer",
        evidence="联网搜索「地王广场 地偶」时的结果之一，疑似主办方/工作室，"
                 "**待人工确认**后再作为采集目标",
    ),
    InfoAccount(
        "神启Apotheosis_OFFICIAL", "weibo", "aggregator",
        evidence="搜「地王广场」命中（uid=9185021276）——疑似地偶团体官号，"
                 "**待核实**是否团体",
        uid="9185021276",
    ),
    InfoAccount(
        "龙亦瑞工作室", "weibo", "organizer",
        evidence="搜「地王广场」命中（uid=9150487106），疑似主办/工作室，"
                 "**待核实**",
        uid="9150487106",
    ),
)


def by_platform(platform: str) -> list[InfoAccount]:
    """按平台取账号（platform: xhs | weibo）。"""
    src = XHS_INFO_ACCOUNTS if platform == "xhs" else WEIBO_INFO_ACCOUNTS
    return list(src)


def aggregator_names(platform: str = "xhs") -> list[str]:
    """只取聚合号（图鉴/速览类）——性价比最高的一类。"""
    return [a.name for a in by_platform(platform) if a.kind == "aggregator"]
