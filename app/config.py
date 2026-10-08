"""全局配置。

所有可调参数集中在 Settings，通过环境变量或 .env 覆盖。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 项目根目录：app/config.py -> app -> 项目根
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
SHOT_DIR = DATA_DIR / "shots"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("GD_ENV_FILE", str(ROOT_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "广东地下演出日历"
    env: str = "dev"
    debug: bool = True
    timezone: str = "Asia/Shanghai"

    # ---- 数据库 ----
    database_url: str = f"sqlite+aiosqlite:///{(DATA_DIR / 'gd_calendar.db').as_posix()}"

    # ---- 抓取 ----
    http_timeout: float = 30.0
    http_retries: int = 3
    rate_limit_seconds: float = 3.0
    douban_crawl_delay: float = 5.0
    user_agent: str = (
        "GDLiveCalendarBot/0.1 (+https://example.com/gd-calendar; contact: you@example.com)"
    )
    browser_headless: bool = True
    browser_width: int = 1440
    browser_height: int = 2200
    browser_wait_seconds: float = 6.0

    # ---- 视觉抽取 ----
    vision_enabled: bool = False
    vision_model: str = "deepseek-v4-flash-vision-exp"
    vision_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str = ""

    # ---- 调度 ----
    scheduler_enabled: bool = True
    fetch_http_interval_hours: int = 6
    fetch_browser_interval_hours: int = 12
    fetch_venue_interval_hours: int = 12
    refresh_status_interval_hours: int = 12

    # ---- 页面功能开关 ----
    # 关掉后前端**隐藏入口**；对应 API 仍然存在，脚本与接口调用不受影响
    # （例如 feature_update=False 只是藏起按钮，`更新数据.bat` 照常可用）。
    # 前端 js/features.js 里有一份同名的静态兜底值，保证后端不可用时开关依然生效。
    feature_ics: bool = False       # 「＋ 订阅这个日历」与「加进我的日历 (.ics)」
    feature_update: bool = False    # 页面上的「↻ 更新数据」按钮

    # ---- 范围 ----
    future_window_days: int = 90
    # ⚠️ 必须加 NoDecode：pydantic-settings 默认对 list 字段强制走 **JSON 解码**，
    #    于是 .env 里写 `ENABLED_CITIES=广州,深圳` 会直接抛
    #    `SettingsError: error parsing value for field "enabled_cities"`，
    #    而且是**在字段校验器之前**失败，`field_validator(mode="before")` 救不了。
    #    NoDecode 让原始字符串进来，交给下面的校验器按逗号切分。
    enabled_cities: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "广州", "深圳", "佛山", "东莞", "珠海", "中山", "惠州", "汕头",
        ]
    )

    @field_validator("enabled_cities", mode="before")
    @classmethod
    def _split_cities(cls, v):
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_dirs(self) -> None:
        for d in (DATA_DIR, SNAPSHOT_DIR, SHOT_DIR):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
