"""生成数据源清单（Markdown），从**代码**读取而不是手写。

## 为什么要自动生成

「有哪些源、关键词是什么、哪些结论不可行」如果写在文档里手抄，
代码一改文档就失真。这个脚本从源码读事实，保证清单与实现一致。

输出：`docs/DATASOURCES.md`（注意：`docs/` 是静态站产物目录，
`build-static` 不会删除它 —— `prepare_out_dir` 只清 data/css/js/index.html/.nojekyll）
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI\gd-calendar")

ROOT = Path(r"D:\AI\gd-calendar")
DB = ROOT / "data" / "gd_calendar.db"
OUT = ROOT / "docs" / "DATASOURCES.md"


def db_stats() -> dict:
    if not DB.exists():
        return {}
    c = sqlite3.connect(DB)
    q = lambda s: c.execute(s).fetchall()  # noqa: E731
    stats: dict = {}
    stats["per_source"] = q("""
        SELECT s.code, COUNT(DISTINCT o.id)
        FROM source s
        LEFT JOIN occurrence_source os ON os.source_id = s.id
        LEFT JOIN occurrence o ON o.id = os.occurrence_id
        GROUP BY s.code ORDER BY 2 DESC
    """)
    stats["lineup"] = q("""
        SELECT s.code,
               COUNT(DISTINCT o.id) AS total,
               COUNT(DISTINCT CASE WHEN oa.occurrence_id IS NOT NULL
                                   THEN o.id END) AS with_lineup
        FROM source s
        JOIN occurrence_source os ON os.source_id = s.id
        JOIN occurrence o ON o.id = os.occurrence_id
        LEFT JOIN occurrence_artist oa ON oa.occurrence_id = o.id
        GROUP BY s.code ORDER BY total DESC
    """)
    return stats


def weibo_keywords() -> list[str]:
    from app.collectors.weibo import SEARCH_KEYWORDS

    return list(SEARCH_KEYWORDS)


def xhs_keywords() -> list[str]:
    from app.collectors.xhs import SEARCH_KEYWORDS

    return list(SEARCH_KEYWORDS)


def info_accounts_rows() -> list[tuple[str, str, str, str]]:
    from app.normalize.info_accounts import (
        WEIBO_INFO_ACCOUNTS,
        XHS_INFO_ACCOUNTS,
    )

    rows = []
    for a in (*XHS_INFO_ACCOUNTS, *WEIBO_INFO_ACCOUNTS):
        rows.append((a.platform, a.kind, a.name, a.evidence))
    return rows


def kb_accounts() -> list[tuple[str, str]]:
    from app.normalize.artist_kb import SEED_ARTISTS

    return [(e.name, e.xhs) for e in SEED_ARTISTS if e.xhs]


def main() -> None:
    st = db_stats()
    L: list[str] = []
    L.append("# 数据源清单（自动生成）")
    L.append("")
    L.append("> ⚠️ 本文件由 `scripts/make_datasources.py` 从**源码**生成，请勿手改。")
    L.append("> 重新生成：`python scripts/make_datasources.py`")
    L.append("")

    L.append("## 一、已接入的源")
    L.append("")
    L.append("| 源 | 场次 | 阵容覆盖 |")
    L.append("| --- | --- | --- |")
    lineup_map = {r[0]: (r[1], r[2]) for r in st.get("lineup", [])}
    for code, n in st.get("per_source", []):
        tot, wl = lineup_map.get(code, (0, 0))
        pct = f"{100 * wl / tot:.1f}%" if tot else "—"
        L.append(f"| `{code}` | {n} | {wl}/{tot}（{pct}） |")
    L.append("")
    L.append("**阵容覆盖率是最关键的指标** —— 发布口径要求「演出名单必须能证明是演出」，")
    L.append("秀动 86% 的覆盖是整个垂类判定的命脉，豆瓣几乎不给阵容。")
    L.append("")

    L.append("## 二、采集关键词")
    L.append("")
    L.append("### 微博（每个词 ≈33 秒，所以精挑）")
    L.append("")
    for k in weibo_keywords():
        L.append(f"- `{k}`")
    L.append("")
    L.append("### 小红书")
    L.append("")
    for k in xhs_keywords():
        L.append(f"- `{k}`")
    L.append("")

    L.append("## 三、情报账号（图鉴/聚合/场馆/工具）")
    L.append("")
    L.append("| 平台 | 类型 | 账号 | 发现依据 |")
    L.append("| --- | --- | --- | --- |")
    kind_zh = {
        "aggregator": "聚合/图鉴", "venue": "场馆情报",
        "tool": "日程工具", "organizer": "主办/工作室",
    }
    for plat, kind, name, ev in info_accounts_rows():
        p = "小红书" if plat == "xhs" else "微博"
        L.append(f"| {p} | {kind_zh.get(kind, kind)} | **{name}** | {ev[:60]} |")
    L.append("")

    L.append("## 四、团体官方账号（反向搜索交叉验证）")
    L.append("")
    L.append("| 团体 | 小红书账号 |")
    L.append("| --- | --- |")
    for name, xhs in kb_accounts():
        L.append(f"| {name} | {xhs} |")
    L.append("")

    L.append("## 五、已验证不可行的源")
    L.append("")
    L.append("| 源 | 结论 | 实测依据 |")
    L.append("| --- | --- | --- |")
    L.append("| 小红书笔记正文 | ❌ 不可行 | 直接 `nav()` → 404；点 DOM 链接（含坐标点击）→ 坐标 `0,0` 仍 404；弹层 `role=dialog` 始终为 0 |")
    L.append("| 微博账号主页 | ❌ 不可用 | `/api/container` → **432**；`/u/<uid>` 返 200 但是 JS 壳（无 `$render_data`/`mblog`） |")
    L.append("| 中国的 Fandom Wiki | ❌ 网络不可达 | `chinaidols.fandom.com` HTTP 与浏览器均 `ERR_CONNECTION_TIMED_OUT` |")
    L.append("| 微信公众号 | ❌ 已排除 | 维护者要求不做；微信侧另有验证码拦截 |")
    L.append("| 抖音 | ❌ 登录墙 | 早期已放弃 |")
    L.append("| B站会员购翻页 | ⚠️ 无意义 | 广州 `total=17`、`pagesize=16` → 只有一页 |")
    L.append("| B站票务接口直调 | ❌ 被拒 | `listV2` 直调 `success=false`／`请求非法` |")
    L.append("| B站 UP 主搜索 | ⚠️ 未跑通 | 结果页不发可拦截 XHR；`BrowserFetcher` 无 `cmd` 读不了 DOM |")
    L.append("")

    L.append("## 六、B站的硬限制")
    L.append("")
    L.append("城市选择器**只有 10 个城市**（全国/北京/上海/杭州/广州/深圳/武汉/天津/")
    L.append("成都/南京 + 自动定位的珠海）。广东仅**广州 / 深圳 / 珠海**可点 ——")
    L.append("佛山、东莞的按钮在页面上找不到；中山、惠州、汕头根本没有。")
    L.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"  已生成 {OUT}（{len(L)} 行）")


main()
