"""Copilot plan quota for the runtime's credential (``GET /copilot_internal/user``).

GitHub's VS Code extension reads this endpoint for its usage indicator. It is
not a documented API: the fields below (``quota_snapshots`` keyed by quota id
with ``entitlement`` / ``remaining`` / ``percent_remaining`` / ``unlimited``,
``quota_reset_date``, ``copilot_plan``) are what the endpoint has been observed
to return, so the parser is defensive and a renamed field degrades to
``available: false`` with a reason rather than an error. Whether an AI-credits
snapshot exists, and under which id, is unverified; a snapshot whose id
contains ``credit`` is surfaced as ``credits``, nothing else is guessed. The
answer is cached briefly per credential so a Portal that polls does not hammer
GitHub, and failures are cached for a shorter while.

Only a GitHub source token can ask ``api.github.com``; a Copilot plugin token
(what the runtime holds after the exchange) cannot, and says so.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from src.efp_runtime.llm.provider import (
    GITHUB_SOURCE_TOKEN_PREFIXES,
    GitHubCopilotHTTPTransport,
    _copilot_plugin_headers,
    _normalize_base_url,
    _open_request,
    _read_http_error_body,
    _redact_secret,
    build_proxy_opener,
)

logger = logging.getLogger(__name__)

SOURCE = "copilot_internal_user"
QUOTA_PATH = "/copilot_internal/user"
CACHE_TTL_SECONDS = 60.0
FAILURE_TTL_SECONDS = 15.0
REQUEST_TIMEOUT_SECONDS = 10.0

_USER_AGENT = "GitHubCopilotChat/0.41.0"
_EDITOR_VERSION = "vscode/1.133.0"
_EDITOR_PLUGIN_VERSION = "copilot-chat/0.41.0"
_INTEGRATION_ID = "vscode-chat"

_SNAPSHOT_LABELS = {
    "premium_interactions": "Premium requests",
    "chat": "Chat",
    "completions": "Code completions",
}
_ENTITLEMENT_KEYS = ("entitlement", "quota_entitlement", "limit", "included")
_REMAINING_KEYS = ("remaining", "quota_remaining")
_PERCENT_KEYS = ("percent_remaining", "quota_percent_remaining")

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def copilot_quota_for_runtime(*, force: bool = False) -> dict[str, Any]:
    """The quota for the credential this runtime chats with (env token, else ``llm.api_key``)."""

    credential = _runtime_credential()
    if not credential:
        return _unavailable("no Copilot credential configured")
    return fetch_copilot_quota(credential, opener=_runtime_opener(), force=force)


def fetch_copilot_quota(
    credential: str | None,
    *,
    github_api_base_url: str | None = None,
    opener: urllib_request.OpenerDirector | None = None,
    timeout: float | None = REQUEST_TIMEOUT_SECONDS,
    force: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Fetch (or serve from the short cache) the quota payload for ``credential``."""

    token = (credential or "").strip()
    if not token:
        return _unavailable("no Copilot credential configured")
    if not token.startswith(GITHUB_SOURCE_TOKEN_PREFIXES):
        return _unavailable(
            "the configured credential is a Copilot plugin token; "
            "the GitHub token is needed to read the plan quota"
        )
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    current = time.monotonic() if now is None else now
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and not force and current < cached[0]:
            return {**cached[1], "cached": True}
    payload = _fetch(
        token,
        github_api_base_url=github_api_base_url,
        opener=opener,
        timeout=timeout,
    )
    ttl = CACHE_TTL_SECONDS if payload.get("available") else FAILURE_TTL_SECONDS
    with _cache_lock:
        _cache[key] = (current + ttl, payload)
    return {**payload, "cached": False}


def clear_quota_cache() -> None:
    with _cache_lock:
        _cache.clear()


def parse_copilot_quota(payload: Any, *, fetched_at: str | None = None) -> dict[str, Any]:
    """Project the endpoint's answer onto the contract the Portal reads."""

    stamp = fetched_at or _now_iso()
    if not isinstance(payload, Mapping):
        return _unavailable("unexpected quota payload", fetched_at=stamp)
    snapshots = _snapshots(payload.get("quota_snapshots"))
    credits = next((snapshot for snapshot in snapshots if "credit" in snapshot["id"]), None)
    return {
        "available": True,
        "source": SOURCE,
        "fetched_at": stamp,
        "plan": _first_text(
            payload.get("copilot_plan"),
            payload.get("plan"),
            payload.get("access_type_sku"),
            payload.get("sku"),
        ),
        "reset_date": _first_text(
            payload.get("quota_reset_date"),
            payload.get("quota_reset_date_utc"),
            payload.get("reset_date"),
        ),
        "snapshots": snapshots,
        "credits": credits,
        "error": None,
        "raw_keys": sorted(str(key) for key in payload.keys()),
    }


def _fetch(
    token: str,
    *,
    github_api_base_url: str | None,
    opener: urllib_request.OpenerDirector | None,
    timeout: float | None,
) -> dict[str, Any]:
    endpoint = "{0}{1}".format(
        _normalize_base_url(
            github_api_base_url,
            default=GitHubCopilotHTTPTransport.DEFAULT_GITHUB_API_BASE_URL,
        ),
        QUOTA_PATH,
    )
    request = urllib_request.Request(
        endpoint,
        headers={
            "Authorization": "Bearer {0}".format(token),
            "Accept": "application/json",
            **_copilot_plugin_headers(
                user_agent=_USER_AGENT,
                editor_version=_EDITOR_VERSION,
                editor_plugin_version=_EDITOR_PLUGIN_VERSION,
                integration_id=_INTEGRATION_ID,
            ),
        },
        method="GET",
    )
    fetched_at = _now_iso()
    try:
        with _open_request(opener, request, timeout=timeout) as response:
            raw_body = response.read()
    except urllib_error.HTTPError as exc:
        detail = _redact_secret((_read_http_error_body(exc) or "").strip(), token)[:300]
        return _unavailable(
            "GitHub answered HTTP {0}{1}".format(
                getattr(exc, "code", "?"),
                ": " + detail if detail else "",
            ),
            fetched_at=fetched_at,
        )
    except urllib_error.URLError as exc:
        reason = _redact_secret(str(getattr(exc, "reason", exc)), token)
        return _unavailable("GitHub unreachable: {0}".format(reason), fetched_at=fetched_at)
    except TimeoutError:
        return _unavailable("GitHub did not answer in time", fetched_at=fetched_at)
    except Exception as exc:  # noqa: BLE001 - informational endpoint; never raise.
        return _unavailable(
            "quota request failed: {0}".format(_redact_secret(str(exc), token)),
            fetched_at=fetched_at,
        )
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unavailable("GitHub returned a non-JSON quota payload", fetched_at=fetched_at)
    return parse_copilot_quota(payload, fetched_at=fetched_at)


def _snapshots(raw: Any) -> list[dict[str, Any]]:
    items: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            if isinstance(value, Mapping):
                items.append((str(key), value))
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, Mapping):
                continue
            identifier = _first_text(value.get("quota_id"), value.get("id"), value.get("name"))
            if identifier:
                items.append((identifier, value))
    snapshots: list[dict[str, Any]] = []
    for identifier, value in items:
        snapshot_id = identifier.strip().lower()
        entitlement = _number(value, *_ENTITLEMENT_KEYS)
        remaining = _number(value, *_REMAINING_KEYS)
        used = None
        if entitlement is not None and remaining is not None:
            used = round(max(0.0, entitlement - remaining), 2)
        percent_remaining = _number(value, *_PERCENT_KEYS)
        if percent_remaining is None and entitlement and remaining is not None:
            percent_remaining = round(100.0 * remaining / entitlement, 1)
        snapshots.append(
            {
                "id": snapshot_id,
                "label": _SNAPSHOT_LABELS.get(
                    snapshot_id,
                    snapshot_id.replace("_", " ").strip().capitalize() or snapshot_id,
                ),
                "entitlement": entitlement,
                "remaining": remaining,
                "used": used,
                "percent_remaining": percent_remaining,
                "unlimited": bool(value.get("unlimited")),
                "overage_count": _number(value, "overage_count"),
                "overage_permitted": (
                    bool(value.get("overage_permitted"))
                    if "overage_permitted" in value
                    else None
                ),
            }
        )
    return snapshots


def _unavailable(error: str, *, fetched_at: str | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "source": SOURCE,
        "fetched_at": fetched_at or _now_iso(),
        "plan": None,
        "reset_date": None,
        "snapshots": [],
        "credits": None,
        "error": error,
        "raw_keys": [],
    }


def _runtime_credential() -> str | None:
    for name in ("EFP_GITHUB_COPILOT_TOKEN", "GITHUB_COPILOT_TOKEN"):
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    try:
        from src.config import config

        llm = config.llm if isinstance(config.llm, Mapping) else {}
    except Exception:  # noqa: BLE001 - no config, no credential.
        return None
    value = llm.get("api_key")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _runtime_opener() -> urllib_request.OpenerDirector | None:
    try:
        from src.gateway.runtime_chat import _llm_proxy_setting

        return build_proxy_opener(
            _llm_proxy_setting(GitHubCopilotHTTPTransport.DEFAULT_GITHUB_API_BASE_URL)
        )
    except Exception:  # noqa: BLE001 - follow the environment when the proxy plan is unavailable.
        return None


def _number(mapping: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number:
            continue
        return round(number, 4)
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CACHE_TTL_SECONDS",
    "FAILURE_TTL_SECONDS",
    "SOURCE",
    "clear_quota_cache",
    "copilot_quota_for_runtime",
    "fetch_copilot_quota",
    "parse_copilot_quota",
]
