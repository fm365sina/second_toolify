from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    notion_token: str
    notion_parent_page_id: str
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    timezone: str = "Asia/Shanghai"
    max_most_used: int = 150
    max_new: int = 120
    selected_min: int = 100
    selected_max: int = 140
    headless: bool = True
    notion_skip_if_exists: bool = True


class ConfigError(ValueError):
    """Raised when required config is missing or invalid."""


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer") from exc


def load_settings() -> Settings:
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    notion_token = os.getenv("NOTION_TOKEN", "").strip()
    notion_parent_page_id = os.getenv("NOTION_PARENT_PAGE_ID", "").strip()

    missing = []
    if not deepseek_api_key:
        missing.append("DEEPSEEK_API_KEY")
    if not notion_token:
        missing.append("NOTION_TOKEN")
    if not notion_parent_page_id:
        missing.append("NOTION_PARENT_PAGE_ID")

    if missing:
        raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    settings = Settings(
        deepseek_api_key=deepseek_api_key,
        notion_token=notion_token,
        notion_parent_page_id=notion_parent_page_id,
        deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat",
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        or "https://api.deepseek.com",
        timezone=os.getenv("REPORT_TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
        max_most_used=_int_env("MAX_MOST_USED", 150),
        max_new=_int_env("MAX_NEW_AIS", 120),
        selected_min=_int_env("SELECTED_MIN", 100),
        selected_max=_int_env("SELECTED_MAX", 140),
        headless=_bool_env("PLAYWRIGHT_HEADLESS", True),
        notion_skip_if_exists=_bool_env("NOTION_SKIP_IF_EXISTS", True),
    )

    if settings.max_most_used <= 0 or settings.max_new <= 0:
        raise ConfigError("MAX_MOST_USED and MAX_NEW_AIS must be positive integers")
    if settings.selected_min <= 0 or settings.selected_max <= 0:
        raise ConfigError("SELECTED_MIN and SELECTED_MAX must be positive integers")
    if settings.selected_max < settings.selected_min:
        raise ConfigError("SELECTED_MAX must be greater than or equal to SELECTED_MIN")

    return settings
