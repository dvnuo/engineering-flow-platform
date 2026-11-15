"""Portal internal API request helpers."""

from __future__ import annotations

import os
from typing import Dict

from src.config import config as global_config

# The per-assistant secret the Portal injects into the pod. The Portal sends it
# on every request it makes to this runtime (src/gateway/portal_trust.py checks
# it), and the runtime sends it back on every call to the Portal's
# /api/internal endpoints. A pod created before the Portal injected it has none.
PORTAL_TRUST_TOKEN_ENV = "EFP_PORTAL_TRUST_TOKEN"
PORTAL_TRUST_TOKEN_HEADER = "X-Portal-Trust-Token"


def get_portal_trust_token() -> str:
    """The Portal trust token of this pod; empty when the Portal injected none."""
    return str(os.getenv(PORTAL_TRUST_TOKEN_ENV) or "").strip()


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
    headers: Dict[str, str] = {}
    if include_content_type:
        headers["Content-Type"] = "application/json"
    # How the Portal tells this assistant's runtime from any other pod.
    trust_token = get_portal_trust_token()
    if trust_token:
        headers[PORTAL_TRUST_TOKEN_HEADER] = trust_token
    return headers
