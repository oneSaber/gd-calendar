#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口。

用法：
    python -m app.cli initdb                    建表
    python -m app.cli fetch [--sources a,b]     跑一轮采集
    python -m app.cli stats                     看统计
    python -m app.cli serve [--port 8000]       起 API + 前端
    python -m app.cli worker                    起调度器（长驻）
    python -m app.cli ics-preview               打印一段 .ics 预览
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from app.utils import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gd-calendar", description="广东地下演出日历 CLR")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("initdb", help="建表（幂等，自动补缺失的列）")

    sub.add_parser("migrate", help="只做结构迁移（给已有表补列）")

    b = sub.add_parser("backfill", help="按当前分类器重算历史数据的分类标记")
    b.add_argument("--dry-run", action="store_true", help="只看会改多少，不落库")

    # 演员知识库：按「演出人员」而不是标题来判定分类
    ak = sub.add_parser("artist-kb", help="同步演员知识库到 artist 表")
    ak.add_argument("--dry-run", action="store_true", help="只看会改多少，不落库")

    ba = sub.add_parser(
        "bili-accounts",
        help="为知识库里的垂类团体发现 B站 官方账号（供动态通道采集）",
    )
    ba.add_argument("--limit", type=int, default=0, help="只处理前 N 个（0=全部）")
    ba.add_argument("--dry-run", action="store_true", help="只搜索不写库")

    # 小红书：需要维护者自己的登录态（扫码一次可复用），只读公开笔记
    xh = sub.add_parser("xhs", help="小红书采集（需扫码登录；只读公开笔记）")
    xh.add_argument(
        "action", choices=["status", "login", "collect"],
        help="status=看登录态 / login=扫码登录 / collect=按关键词采集",
    )
    xh.add_argument("--keywords", default="", help="逗号分隔；留空用内置词表")
    xh.add_argument("--out", default="", help="采集结果输出 JSON 路径")
    xh.add_argument("--show", action="store_true", help="用可见窗口（扫码时必须）")

    rc = sub.add_parser(
        "reclassify", help="按阵容重算活动分类（比标题可靠，与标题判定取并集）"
    )
    rc.add_argument("--dry-run", action="store_true", help="只看会改多少，不落库")
    rc.add_argument("--report", action="store_true", help="打印改动明细")

    s = sub.add_parser("build-static", help="导出只读静态站点（给 GitHub Pages 用）")
    s.add_argument("--out", default="docs", help="输出目录，默认 docs/")
    s.add_argument("--no-occurrences", action="store_true",
                   help="只导出月历计数与统计，不含场次明细（体积小很多）")

    f = sub.add_parser("fetch", help="跑一轮采集")
    f.add_argument("--sources", default="showstart,douban",
                   help="逗号分隔：showstart,douban,bilibili,weibo")
    f.add_argument("--cities", default="", help="逗号分隔，留空用配置里的城市")
    f.add_argument("--no-browser", action="store_true", help="跳过浏览器源")
    f.add_argument("--posters", action="store_true",
                   help="尝试补秀动海报（实测：秀动详情页是 SPA 壳，静态抓不到，"
                        "仅作探测；豆瓣/B站海报默认就会带上）")

    sub.add_parser("stats", help="输出统计")

    s = sub.add_parser("serve", help="启动 API + 前端")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")

    w = sub.add_parser("worker", help="长驻：采集一次后进入定时调度")
    w.add_argument("--once", action="store_true", help="只跑一次不驻留")

    i = sub.add_parser("ics-preview", help="打印 .ics 预览")
    i.add_argument("--city", default="广州")
    i.add_argument("--limit", type=int, default=3)
    return p


async def cmd_migrate() -> int:
    """只做结构迁移（给已有表补列）。"""
    from app.db import get_engine
    from app.db.migrate import run_migrations

    engine = get_engine()
    async with engine.begin() as conn:
        applied = await run_migrations(conn)
    if applied:
        print(f"✅ 迁移完成，共 {len(applied)} 项：")
        for a in applied:
            print(f"   · {a}")
    else:
        print("✅ 数据库结构已是最新，无需迁移")
    return 0


async def cmd_initdb() -> int:
    from app.db import get_engine, init_db
    from app.db.migrate import run_migrations

    # ⚠️ 必须先补列再 create_all：create_all 只建缺失的表，不会给已有表加列
    engine = get_engine()
    async with engine.begin() as conn:
        applied = await run_migrations(conn)
    if applied:
        print(f"ℹ️  已为已有表补齐 {len(applied)} 个列")
    await init_db()
    print("✅ 建表完成")
    return 0


async def cmd_backfill(args) -> int:
    """按当前分类器重算历史数据的分类标记（女子乐队/ACG 等新增标记需要）。"""
    from app.db import session_scope
    from app.jobs.backfill import backfill_flags

    async with session_scope() as session:
        stats = await backfill_flags(session, dry_run=args.dry_run)
    print(("（演练）" if args.dry_run else "") + stats.summary())
    for s in stats.samples:
        print(f"   · {s}")
    return 0


async def cmd_artist_kb(args) -> int:
    """把种子知识库同步进 artist 表（按 name_norm 去重，纠正占位分类）。"""
    from app.db import session_scope
    from app.normalize.artist_store import load_knowledge_base, sync_knowledge_base

    async with session_scope() as session:
        if args.dry_run:
            kb = await load_knowledge_base(session)
            print(f"（演练）当前可加载知识库条目：{len(kb)}")
            return 0
        stats = await sync_knowledge_base(session)
        kb = await load_knowledge_base(session)
    print(f"✅ 知识库同步完成：新增 {stats['added']} / 更新 {stats['updated']} / "
          f"跳过 {stats['skipped']}")
    print(f"   当前可加载条目：{len(kb)}（unknown 占位行不参与判定）")
    return 0


async def cmd_reclassify(args) -> int:
    """按阵容重算活动分类。这是**推断**，改动会写进 review_task 供复核。"""
    from app.db import session_scope
    from app.normalize.artist_store import reclassify_events

    async with session_scope() as session:
        res = await reclassify_events(session, dry_run=args.dry_run)

    tag = "（演练）" if args.dry_run else ""
    print(f"{tag}扫描 {res['events']} 个活动 → 改动 {res['changed']} 个")
    g = res["gained"]
    print(f"   新增标记：地偶 +{g['is_idol']} / 女子乐队 +{g['is_girl_band']} / ACG +{g['is_acg']}")
    print(f"   无阵容跳过：{res['no_lineup']}（保持标题判定不变）")
    print(f"   阵容里的陌生演员：{len(res['unknown_artists'])} 个")
    if args.report:
        print()
        print("   改动明细：")
        for s in res["samples"]:
            b = "".join(["地" if s["before"]["is_idol"] else "·",
                         "女" if s["before"]["is_girl_band"] else "·",
                         "A" if s["before"]["is_acg"] else "·"])
            a = "".join(["地" if s["after"]["is_idol"] else "·",
                         "女" if s["after"]["is_girl_band"] else "·",
                         "A" if s["after"]["is_acg"] else "·"])
            print(f"     [{b}] -> [{a}]  {(s['title'] or '')[:40]}")
            print(f"        阵容: {' | '.join(s['lineup'])}   票数: {s['votes']}")
    return 0


async def cmd_bili_accounts(args) -> int:
    """为知识库里的垂类团体发现 B站 官方账号。

    用途：B站会员购广东只有 ~17 条/城，但团体**官方账号**会发带阵容的演出预告。
    """
    from app.collectors.bili_accounts import discover_accounts
    from app.db import session_scope

    try:
        from app.collectors.base import BrowserFetcher
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 浏览器不可用：{exc}")
        return 1

    async with session_scope() as session:
        async with BrowserFetcher() as browser:
            res = await discover_accounts(
                session, browser,
                limit=args.limit or None,
                dry_run=args.dry_run,
            )
    tag = "（演练）" if args.dry_run else ""
    print(f"{tag}发现账号 {len(res.found)} 个，跳过 {len(res.skipped)} 个")
    for a in res.found:
        print(f"   ✓ {a.name}  mid={a.mid}")
    for s in res.skipped[:20]:
        print(f"   · 跳过 {s}")
    return 0


async def cmd_xhs(args) -> int:
    """小红书采集。需要维护者自己的登录态（扫码一次可复用）。

    合规：只读公开笔记，不注入 Cookie、不伪造签名、不改指纹 ——
    与 `scripts/xhs_publish.py`（发布助手）走同一条路。
    """
    from app.collectors import xhs

    action = args.action

    if action == "status":
        print("  小红书 profile:", xhs.PROFILE)
        print("  profile 存在:", xhs.PROFILE.exists())
        try:
            br = xhs.XhsBrowser()
            logged, names = br.logged_in()
            print(f"  登录态: {'已登录 ✓' if logged else '未登录'}")
            print(f"  cookie: {', '.join(names[:8])}")
            print(f"  当前页面: {br.url()}")
            br.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  无法连接浏览器：{exc}")
            print("  先起可见浏览器：python scripts/xhs_publish.py launch")
        return 0

    if action == "login":
        print("  ⚠️ 需要**可见窗口**扫码。若浏览器未运行，先执行：")
        print("     python scripts/xhs_publish.py launch")
        print("  然后跑：python -m app.cli xhs login")
        try:
            br = xhs.XhsBrowser()
            br.nav("https://www.xiaohongshu.com/login", wait=4.0)
            print("  已打开登录页，请在浏览器里扫码。")
            print("  等待登录（最多 180 秒）…")
            for i in range(60):
                time.sleep(3)
                ok, _ = br.logged_in()
                if ok:
                    print(f"  ✓ 登录成功（等待 {(i + 1) * 3} 秒）")
                    br.close()
                    return 0
                if i % 5 == 4:
                    print(f"    仍在等待…（{(i + 1) * 3}s）")
            print("  ✗ 超时未检测到登录")
            br.close()
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"  失败：{exc}")
            return 1

    # collect
    kws = [k.strip() for k in args.keywords.split(",") if k.strip()] or None
    res = xhs.collect(kws)
    print(f"  登录态: {'已登录' if res.logged_in else '⚠ 未登录（请先扫码）'}")
    print(f"  完成关键词: {len(res.keywords_done)} 个")
    print(f"  笔记: {len(res.notes)} 条")
    for n in res.notes[:15]:
        print(f"    [{n.author[:14] or '?'}] {n.title[:44]}")
    if res.errors:
        print(f"  错误 {len(res.errors)} 个：{res.errors[:3]}")
    out = Path(args.out) if args.out else Path("data") / "xhs_notes.json"
    xhs.save_notes(res.notes, out)
    print(f"  已保存: {out}")
    return 0 if res.logged_in else 2


async def cmd_build_static(args) -> int:
    """生成 GitHub Pages 用的只读静态站点。"""
    from pathlib import Path

    from app.db import session_scope
    from app.static_build import build_static_site, prepare_out_dir

    out = Path(args.out).resolve()
    root = Path(__file__).resolve().parent.parent
    # 安全闸：拒绝往源码目录写（下面的清理会删文件，必须先确认目标不是自己家）
    if out == root or out.name in ("app", "tests", "scripts"):
        print(f"❌ 拒绝写入 {out}（疑似源码目录）")
        return 1

    # ⚠️ 不整个 rmtree 掉输出目录：Windows 下若该目录正被占用
    # （例如用 `python -m http.server --directory docs` 预览），
    # 删目录会直接 PermissionError。改为只清理「本程序管理的子目录」。
    prepare_out_dir(out)

    async with session_scope() as session:
        stats = await build_static_site(
            session, out, include_occurrences=not args.no_occurrences
        )
    print(f"✅ 静态站点已生成: {out}")
    print(f"   {stats.summary()}")
    print(f"   本地预览: python -m http.server 8080 --directory \"{out}\"")
    return 0


async def cmd_fetch(args) -> int:
    from app.pipeline import run_pipeline

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    cities = [c.strip() for c in args.cities.split(",") if c.strip()] or None
    report = await run_pipeline(
        sources=sources, cities=cities, use_browser=not args.no_browser,
        fetch_posters=getattr(args, "posters", False),
    )
    print(report.render())
    return 0 if not report.errors else 1


async def cmd_stats() -> int:
    from app.api import service
    from app.db import session_scope

    async with session_scope() as session:
        occ, ev, ar, ve, sources = await service.overview_stats(session)
        print("=" * 66)
        print("数据统计")
        print("=" * 66)
        print(f"  场次 {occ}  活动 {ev}  艺人 {ar}  场地 {ve}")
        print("-" * 66)
        print(f"  {'来源':16} {'近7天条数':>10} {'成功率':>8}  最后成功")
        for s in sources:
            rate = f"{s.ok_rate_7d:.0%}" if s.ok_rate_7d is not None else "—"
            last = s.last_ok_at.strftime("%m-%d %H:%M") if s.last_ok_at else "从未"
            print(f"  {s.name[:16]:16} {s.items_7d:>10} {rate:>8}  {last}")
    return 0


async def cmd_ics_preview(args) -> int:
    from app.api.main import build_ics
    from app.api import service
    from app.db import session_scope
    import datetime as dt

    from app.utils import CST

    today = dt.datetime.now(CST).date()
    async with session_scope() as session:
        items, _ = await service.query_occurrences(
            session,
            service.OccurrenceQuery(date_from=today, date_to=today + dt.timedelta(days=60),
                                    city=args.city, page_size=args.limit),
        )
    body = build_ics(items, f"{args.city}演出日历")
    print(body[:2000])
    return 0


async def cmd_worker(args) -> int:
    from app.jobs import start_scheduler
    from app.pipeline import run_pipeline

    print("▶ 先跑一轮采集…")
    await run_pipeline()
    if args.once:
        return 0
    start_scheduler()
    print("⏳ 调度器运行中，Ctrl+C 退出")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        from app.jobs import shutdown_scheduler

        shutdown_scheduler()
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = _build_parser().parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "app.api.main:app", host=args.host, port=args.port,
            reload=args.reload, log_level="info",
        )
        return 0

    handlers = {
        "initdb": lambda: cmd_initdb(),
        "migrate": lambda: cmd_migrate(),
        "backfill": lambda: cmd_backfill(args),
        "artist-kb": lambda: cmd_artist_kb(args),
        "bili-accounts": lambda: cmd_bili_accounts(args),
        "xhs": lambda: cmd_xhs(args),
        "reclassify": lambda: cmd_reclassify(args),
        "build-static": lambda: cmd_build_static(args),
        "fetch": lambda: cmd_fetch(args),
        "stats": lambda: cmd_stats(),
        "worker": lambda: cmd_worker(args),
        "ics-preview": lambda: cmd_ics_preview(args),
    }
    try:
        return asyncio.run(handlers[args.cmd]())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
