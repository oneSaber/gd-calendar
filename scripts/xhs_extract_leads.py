"""从抓到的正文里提取**可用线索**（日期 / 场地 / 团体 / 票价），并成报告。

## 为什么需要

小红书正文能读了（需要 `xsec_token`），但正文是非结构化中文 + UI 噪音
（"已关注"、评论区、推荐词）。直接入库会把噪音当数据，所以先做**半结构化提取**
→ 出报告供人工扫一眼 → 确认的部分再进库。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SRC = Path(r"D:\AI\gd-calendar\data\xhs_notes.json")
OUT = Path(r"D:\AI\gd-calendar\data\xhs_leads.md")

# 正文里的 UI 噪音行（笔记正文之外的东西）
_NOISE_LINE = re.compile(
    r"^(已关注|关注|评论|发送|取消|编辑于|作者声明|共\s*\d+\s*条评论|猜你想搜|"
    r"这是一片荒地|置顶|回复|展开|收起|\d+/\d+|"
    r"\d{1,2}-\d{1,2}\s*(广东|中国香港|北京|上海)?|\d+\s*$)",
)

# 场地特征词（用来从正文里捞场地）
_VENUE_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9·]{2,18}?"
    r"(?:idol空间|Idol空间|IDOL空间|地王广场|livehouse|Livehouse|LIVEHOUSE|"
    r"演艺中心|剧场|剧院|音乐厅|空间|广场|馆|Live\s?House|MAO|MAOlive))",
)

# 日期：9/30、10/1、10月3日、2026-09-19…
_DATE_RE = re.compile(
    r"(?<!\d)("
    r"\d{1,2}\s*/\s*\d{1,2}"          # 9/30
    r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号]"  # 10月3日
    r"|\d{4}\s*[-年]\s*\d{1,2}\s*[-月]\s*\d{1,2}"  # 2026-09-19
    r")(?!\d)"
)

# 票价：50元 / ¥80 / 80元
_PRICE_RE = re.compile(r"(?:¥|￥)?\s*(\d{1,4})\s*元")
# 团体名（书名号/引号里，或 【】里）
_GROUP_RE = re.compile(r"[「『【]([^」』】]{2,20})[」』】]")


def clean_body(body: str) -> str:
    """去掉 UI 噪音行，只留正文。"""
    out = []
    for ln in (body or "").splitlines():
        s = ln.strip()
        if not s or _NOISE_LINE.match(s):
            continue
        # 评论区常以「作者」开头
        if s == "作者" or s.startswith("作者\n"):
            continue
        out.append(s)
    return "\n".join(out)


def extract(note: dict) -> dict:
    body = clean_body(note.get("body") or "")
    return {
        "title": note.get("title") or "",
        "author": note.get("author") or "",
        "url": note.get("url") or "",
        "dates": sorted(set(_DATE_RE.findall(body)))[:12],
        "venues": sorted(set(v.strip() for v in _VENUE_RE.findall(body)))[:10],
        "prices": sorted(set(_PRICE_RE.findall(body)))[:8],
        "groups": sorted(set(_GROUP_RE.findall(body)))[:14],
        "body": body,
    }


def main() -> None:
    if not SRC.exists():
        print(f"  ✗ 找不到 {SRC}")
        return
    data = json.loads(SRC.read_text(encoding="utf-8"))
    items = data.get("items") or []
    with_body = [i for i in items if i.get("body")]
    print(f"  笔记 {len(items)} 条，其中 {len(with_body)} 条有正文")

    ex = [extract(i) for i in with_body]
    # 按信息量排序：日期+场地+团体 越多越靠前
    ex.sort(key=lambda e: -(len(e["dates"]) + 2 * len(e["venues"]) + len(e["groups"])))

    L: list[str] = []
    L.append("# 小红书线索（正文已自动提取）")
    L.append("")
    L.append(f"采集时间：{data.get('generated_at', '')}")
    L.append(f"笔记 {len(items)} 条，其中 **{len(with_body)} 条抓到了正文**")
    L.append("")
    L.append("> 正文可读的前提是带上 `xsec_token`（见 SPEC.md 的说明）。")
    L.append("> 下面是从正文里自动提取的日期/场地/团体/票价 —— **供快速扫读**，")
    L.append("> 确认无误的部分再录入数据库。")
    L.append("")

    # 汇总：所有场地词频
    from collections import Counter

    allv = Counter(v for e in ex for v in e["venues"])
    if allv:
        L.append("## 场地出现频次（候选场地库）")
        L.append("")
        L.append("| 次数 | 场地 |")
        L.append("| --- | --- |")
        for v, n in allv.most_common(24):
            L.append(f"| {n} | {v} |")
        L.append("")

    L.append("## 逐条线索（按信息量排序）")
    L.append("")
    for e in ex:
        L.append(f"### [{e['title']}]({e['url']})")
        L.append("")
        L.append(f"作者：{e['author']}")
        if e["dates"]:
            L.append(f"- **日期**：{'、'.join(e['dates'])}")
        if e["venues"]:
            L.append(f"- **场地**：{'、'.join(e['venues'])}")
        if e["groups"]:
            L.append(f"- **团体/名称**：{'、'.join(e['groups'])}")
        if e["prices"]:
            L.append(f"- **价格（元）**：{'、'.join(e['prices'])}")
        L.append("")
        L.append("<details><summary>正文</summary>")
        L.append("")
        L.append("```")
        L.append(e["body"][:1800])
        L.append("```")
        L.append("</details>")
        L.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"  已写出 {OUT}（{len(L)} 行）")

    # 控制台摘要
    print()
    print("  === 场地线索（含地王广场？）===")
    for v, n in allv.most_common(12):
        mark = " ★" if "地王" in v else ""
        print(f"    {n:3}  {v}{mark}")


main()
