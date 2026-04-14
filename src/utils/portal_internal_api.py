"""Portal internal API request helpers."""

from __future__ import annotations

import os
from typing import Dict

from src.config import config as global_config


def get_portal_internal_base_url() -> str:
    env_value = str(os.getenv("PORTAL_INTERNAL_BASE_URL") or "").strip()
    if env_value:
        return env_value.rstrip("/")
    config_value = global_config.get("server.portal_internal_base_url", "")
    return str(config_value or "").strip().rstrip("/")


def get_portal_agent_id() -> str:
    env_value = str(os.getenv("PORTAL_AGENT_ID") or "").strip()
    if env_value:
        return env_value
    config_value = global_config.get("server.portal_agent_id", "")
    return str(config_value or "").strip()


def is_portal_internal_configured() -> bool:
    return bool(get_portal_internal_base_url() and get_portal_agent_id())


def build_portal_internal_url(path: str) -> str:
    normalized_path = f"/{str(path or '').lstrip('/')}"
    return f"{get_portal_internal_base_url()}{normalized_path}"


def build_portal_internal_api_headers(include_content_type: bool = True) -> Dict[str, str]:
    if include_content_type:
        return {"Content-Type": "application/json"}
    return {}
