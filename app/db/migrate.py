"""轻量数据库迁移。

为什么需要它：`Base.metadata.create_all` **只会建缺失的表，不会给已有表加列**。
本项目在开发中陆续加了这些列，若要求用户删库重建，就会丢掉已采集的数据：

  * `poster_extraction.cache_path` / `content_type` / `byte_size` / `fetched_at`（海报缓存）
  * `event.is_girl_band` / `event.is_acg`（女子乐队 / ACG 独立标记）

本模块按「模型定义 vs 实际表结构」做差集，自动 `ALTER TABLE ADD COLUMN` 并回填默认值。

限制（如实说明）：目前只处理 **SQLite**（默认开发/单机场景）。
PostgreSQL 下请用 Alembic 或手工 DDL —— 这里只做提示，不做半吊子兼容。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Numeric, Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.db.models import ALL_MODELS
from app.utils import get_logger

log = get_logger(__name__)


def _sql_type(col: Any) -> str:
    """把模型列类型映射到 SQLite 的列类型。"""
    t = col.type
    if isinstance(t, Boolean):
        return "BOOLEAN"
    if isinstance(t, Integer):
        return "INTEGER"
    if isinstance(t, Numeric):
        return "NUMERIC"
    if isinstance(t, DateTime):
        return "DATETIME"
    if isinstance(t, Text):
        return "TEXT"
    # JSON / ARRAY 在 SQLite 下落成 TEXT
    return "TEXT"


async def _table_columns(conn: AsyncConnection) -> dict[str, set[str]]:
    """读取现有表与列（SQLite 下同步 inspect 包成 async 调用）。"""

    def _read(sync_conn) -> dict[str, set[str]]:
        insp = inspect(sync_conn)
        return {
            t: {c["name"] for c in insp.get_columns(t)}
            for t in insp.get_table_names()
        }

    return await conn.run_sync(_read)


async def run_migrations(conn: AsyncConnection, *, backfill: bool = True) -> list[str]:
    """补齐缺失的列，返回所做操作的描述列表。"""
    if not settings.is_sqlite:
        log.warning(
            "自动迁移仅支持 SQLite（当前 %s）。请改用 Alembic 或手工 DDL。",
            settings.database_url.split("://", 1)[0],
        )
        return []

    applied: list[str] = []
    existing = await _table_columns(conn)

    # ---- 1) 新增缺失的列 ----
    for model in ALL_MODELS:
        table = model.__tablename__
        if table not in existing:
            continue  # 缺表交给 create_all
        have = existing[table]
        for col in model.__table__.columns:
            if col.name in have or col.primary_key:
                continue
            type_sql = _sql_type(col)
            stmt = f'ALTER TABLE "{table}" ADD COLUMN "{col.name}" {type_sql}'
            # 非空布尔列直接给 DEFAULT 0，避免历史行是 NULL
            if isinstance(col.type, Boolean) and not col.nullable:
                stmt += " NOT NULL DEFAULT 0"
            try:
                await conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                log.warning("迁移失败 %s.%s: %s", table, col.name, exc)
                continue
            applied.append(f"{table}.{col.name} 新增（{type_sql}）")
            log.info("迁移：新增列 %s.%s %s", table, col.name, type_sql)

    # ---- 2) 回填：布尔列不该有 NULL（历史行可能是 NULL）----
    if backfill:
        existing = await _table_columns(conn)
        for model in ALL_MODELS:
            table = model.__tablename__
            if table not in existing:
                continue
            for col in model.__table__.columns:
                if col.name not in existing[table]:
                    continue
                if not isinstance(col.type, Boolean):
                    continue
                try:
                    res = await conn.execute(
                        text(
                            f'UPDATE "{table}" SET "{col.name}" = 0 '
                            f'WHERE "{col.name}" IS NULL'
                        )
                    )
                    if res.rowcount:
                        applied.append(f"{table}.{col.name} 回填 False（{res.rowcount} 行）")
                except Exception as exc:  # noqa: BLE001
                    log.warning("回填失败 %s.%s: %s", table, col.name, exc)

    if applied:
        log.info("迁移完成，共 %d 项改动", len(applied))
    else:
        log.info("数据库结构已是最新，无需迁移")
    return applied
