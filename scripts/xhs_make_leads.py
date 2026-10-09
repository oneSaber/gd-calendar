"""把小红书采集结果整理成人可读的线索报告（Markdown）。

## 为什么需要它

实测**无法自动读取笔记正文**（直接导航 404、点击卡片不打开弹层），
而演出时间/地点/阵容都在正文里。所以流程改成**半自动**：

  1. 本工具产出「按优先级排序的线索清单」（标题 + 作者 + 链接）
  2. 维护者点开链接（已登录，正文可见），确认后录入

这样比继续硬啃防抓取更省时间，也不会拿到错的正文。
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, r"D:\AI\gd-calendar")

from app.normalize.info_accounts import (  # noqa: E402
    XHS_INFO_ACCOUNTS,
    WEIBO_INFO_ACCOUNTS,
)

# 高价值线索的判据（标题关键词 -> 为什么值得看）
LEAD_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("速览", "汇总", "汇总帖", "图鉴", "盘点", "时间表", "排期", "日历"),
     "**聚合信息**：一次覆盖多场次，优先看"),
    (("地王", "免费", "无料", "自由入场"),
     "**免费场地**：不上售票平台，只有这类内容才有"),
    (("场馆", "场地", "livehouse", "Livehouse"),
     "**场馆情报**：可用于扩场地库"),
    (("团体", "成员", "招募", "毕业", "出道", "生诞", "生日"),
     "**团体动态**：可用于扩艺人库"),
]


def classify(title: str) -> tuple[str, int]:
    """返回 (线索说明, 优先级)；优先级数字越小越优先。"""
    for i, (keys, why) in enumerate(LEAD_PATTERNS):
        if any(k in title for k in keys):
            return why, i
    return "", 99


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/xhs_notes.json")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/xhs_leads.md")
    data = json.loads(src.read_text(encoding="utf-8"))
    items = data.get("items") or []
    print(f"  读入 {len(items)} 条笔记")

    # 按账号聚合：一眼看出哪些号值得追
    by_author: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_author[it.get("author") or "（未知）"].append(it)

    # 情报账号命中情况
    known_xhs = {a.name for a in XHS_INFO_ACCOUNTS}
    known_weibo = {a.name for a in WEIBO_INFO_ACCOUNTS}

    lines: list[str] = []
    lines.append("# 小红书线索清单（半自动核对用）")
    lines.append("")
    lines.append(f"采集时间：{data.get('generated_at', '')}")
    lines.append("")
    lines.append("> ⚠️ **正文无法自动读取**（XHS 防抓取：导航 404、点击不打开弹层）。")
    lines.append("> 所以这里只给标题 + 作者 + 链接，**点开确认后再录入**。")
    lines.append("")

    # 1) 已知情报账号
    hits = [(n, v) for n, v in by_author.items() if n in known_xhs]
    if hits:
        lines.append("## 一、已知情报账号的内容（优先看）")
        lines.append("")
        for name, notes in sorted(hits, key=lambda kv: -len(kv[1])):
            lines.append(f"### {name}（{len(notes)} 条）")
            lines.append("")
            for n in notes:
                why, _ = classify(n.get("title") or "")
                tail = f"　— {why}" if why else ""
                lines.append(f"- [{n.get('title') or '(无标题)'}]({n.get('url')}){tail}")
            lines.append("")

    # 2) 按线索类型分组的其他笔记
    grouped: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        if (it.get("author") or "") in known_xhs:
            continue
        why, prio = classify(it.get("title") or "")
        if why:
            grouped[why].append(it)

    if grouped:
        lines.append("## 二、按线索类型分组")
        lines.append("")
        for why, notes in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"### {why}（{len(notes)} 条）")
            lines.append("")
            for n in notes:
                lines.append(
                    f"- [{n.get('title') or '(无标题)'}]({n.get('url')})"
                    f"　— @{n.get('author') or '?'}"
                )
            lines.append("")

    # 3) 高频账号（可能是漏掉的聚合号）
    lines.append("## 三、高频账号（可能是漏掉的聚合号）")
    lines.append("")
    lines.append("| 条数 | 账号 | 已是已知情报号 |")
    lines.append("| --- | --- | --- |")
    for name, notes in Counter(
        it.get("author") or "（未知）" for it in items
    ).most_common(20):
        mark = "✓" if name in known_xhs else ""
        lines.append(f"| {notes} | {name} | {mark} |")
    lines.append("")

    # 4) 待核实的微博账号
    pending = [a for a in WEIBO_INFO_ACCOUNTS if "待" in a.evidence or "待核实" in a.evidence]
    if pending:
        lines.append("## 四、待核实的微博账号")
        lines.append("")
        for a in pending:
            uid = f"（uid={a.uid}）" if a.uid else ""
            lines.append(f"- **{a.name}**{uid}　— {a.evidence}")
        lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"  已写出: {out}（{len(lines)} 行）")


main()
