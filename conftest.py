"""pytest 全局配置与**安全门禁**。

⚠️ 为什么需要这个文件：
集成测试会调用 `init_db(drop=True)`。如果测试进程连到开发库（`data/gd_calendar.db`），
就会把开发数据清空成测试 fixture —— 这个坑真实发生过一次。

因此这里在**任何 app 模块被导入之前**强制把数据库指向临时目录，
并在 `pytest_configure` 里做二次校验：一旦发现仍指向项目 `data/` 目录，直接终止测试。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- 1) 在任何 app 导入之前覆盖数据库路径 ----
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="gd-calendar-tests-"))
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///" + (_TMP_ROOT / "test.db").as_posix()
)
# 指向一个不存在的 env 文件，避免读到开发者的 .env
os.environ["GD_ENV_FILE"] = str(_TMP_ROOT / ".env.none")
os.environ["VISION_ENABLED"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DEBUG"] = "false"


def pytest_configure(config) -> None:
    """二次校验：绝不能连到项目的 data/ 目录。"""
    from app.config import settings

    url = settings.database_url
    project_data = (ROOT / "data").as_posix()
    if project_data in url:
        raise SystemExit(
            "❌ 测试安全门禁拦截：DATABASE_URL 指向项目 data/ 目录，"
            "继续运行会清空开发数据。\n"
            f"   database_url = {url}\n"
            "   请确认 conftest.py 在 app 导入前设置了 DATABASE_URL。"
        )
    print(f"\n[tests] 使用临时数据库: {url}")
