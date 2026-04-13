"""Shared internal API key lookup and header construction helpers."""

from __future__ import annotations

import os
from typing import Dict

from src.config import config as global_config


def get_portal_internal_api_key() -> str:
    env_key = str(os.getenv("PORTAL_INTERNAL_API_KEY") or "").strip()
    if env_key:
        return env_key
    config_key = global_config.get("server.portal_internal_api_key", "")
    return str(config_key or "").strip()


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


def get_portal_internal_auth_token() -> str:
    env_value = str(os.getenv("PORTAL_INTERNAL_AUTH_TOKEN") or "").strip()
    if env_value:
        return env_value
    config_value = global_config.get("server.portal_internal_auth_token", "")
    return str(config_value or "").strip()


def get_runtime_internal_api_key() -> str:
    env_key = str(os.getenv("RUNTIME_INTERNAL_API_KEY") or "").strip()
    if env_key:
        return env_key
    config_key = global_config.get("server.runtime_internal_api_key", "")
    return str(config_key or "").strip()


def build_portal_internal_api_headers(include_content_type: bool = True) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json"
    token = get_portal_internal_auth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
