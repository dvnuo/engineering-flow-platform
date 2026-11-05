"""Copilot's per-response quota headers, kept for the quota endpoint.

Every chat answer from the Copilot API carries the member's current quota
snapshot as a header: ``x-quota-snapshot-premium_interactions`` (a
``premium_models`` variant and ``chat`` for free plans exist) holding a
URL-encoded ``ent=<entitlement>&rem=<percent remaining>&ov=<overage>
&ovPerm=<true|false>&rst=<reset date>`` string, plus
``x-usage-ratelimit-session`` / ``x-usage-ratelimit-weekly`` in the same
shape. This is how VS Code refreshes its status bar without asking
``copilot_internal/user`` again. The transport records the latest one per
credential here and ``GET /api/copilot/quota`` reports it as ``live``.

An entitlement of ``-1`` means the per-user quota is unlimited (a pooled
organization allotment); the header then carries no usable balance.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl

QUOTA_HEADER_PREFIX = "x-quota-snapshot-"
# VS Code reads premium_models first, then premium_interactions; chat is the
# free plan's quota.
QUOTA_SNAPSHOT_PRIORITY = ("premium_models", "premium_interactions", "chat", "completions")
RATE_LIMIT_HEADERS = {
    "x-usage-ratelimit-session": "session",
    "x-usage-ratelimit-weekly": "weekly",
}

_lock = threading.Lock()
_latest: dict[str, dict[str, Any]] = {}


def credential_key(credential: Any) -> str | None:
    """The cache key for a credential: its digest, never the token itself."""

    if not isinstance(credential, str) or not credential.strip():
        return None
    return hashlib.sha256(credential.strip().encode("utf-8")).hexdigest()


def parse_quota_header(value: Any) -> dict[str, Any] | None:
    """One ``x-quota-snapshot-*`` header value as numbers, or None when it says nothing."""

    text = str(value or "").strip()
    if not text:
        return None
    params = dict(parse_qsl(text, keep_blank_values=True))
    if not params:
        return None
    entitlement = _number(params.get("ent"))
    unlimited = entitlement is not None and entitlement < 0
    return {
        "entitlement": None if unlimited or entitlement is None else entitlement,
        "unlimited": unlimited,
        "percent_remaining": _number(params.get("rem")),
        "overage_count": _number(params.get("ov")),
        "overage_permitted": (
            str(params.get("ovPerm", "")).strip().lower() == "true" if "ovPerm" in params else None
        ),
        "reset_date": str(params.get("rst") or "").strip() or None,
    }


def record_quota_headers(credential: Any, headers: Any) -> dict[str, Any] | None:
    """Remember the quota header(s) of one Copilot answer for ``credential``.

    ``headers`` is whatever the HTTP client hands back (``http.client``'s
    message, a mapping or ``(name, value)`` pairs); names are matched case
    insensitively. Returns the record kept, or None when the answer carried
    no quota header.
    """

    key = credential_key(credential)
    if key is None or headers is None:
        return None
    snapshots: dict[str, dict[str, Any]] = {}
    rate_limits: dict[str, dict[str, Any]] = {}
    for name, value in _header_items(headers):
        lowered = name.strip().lower()
        if lowered.startswith(QUOTA_HEADER_PREFIX):
            parsed = parse_quota_header(value)
            if parsed is not None:
                snapshots[lowered[len(QUOTA_HEADER_PREFIX):]] = parsed
        elif lowered in RATE_LIMIT_HEADERS:
            parsed = parse_quota_header(value)
            if parsed is not None:
                rate_limits[RATE_LIMIT_HEADERS[lowered]] = parsed
    if not snapshots:
        return None
    chosen_id = next(
        (identifier for identifier in QUOTA_SNAPSHOT_PRIORITY if identifier in snapshots),
        next(iter(snapshots)),
    )
    record = {
        "id": chosen_id,
        **snapshots[chosen_id],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "snapshots": snapshots,
        "rate_limits": rate_limits,
    }
    with _lock:
        _latest[key] = record
    return record


def latest_quota_header(credential: Any) -> dict[str, Any] | None:
    """The quota snapshot the latest chat answer carried for ``credential``, or None."""

    key = credential_key(credential)
    if key is None:
        return None
    with _lock:
        record = _latest.get(key)
    return dict(record) if record is not None else None


def clear_quota_headers() -> None:
    with _lock:
        _latest.clear()


def _header_items(headers: Any) -> list[tuple[str, str]]:
    items = getattr(headers, "items", None)
    if callable(items):
        try:
            return [(str(name), str(value)) for name, value in items()]
        except Exception:  # noqa: BLE001 - an odd header object says nothing.
            return []
    if isinstance(headers, Mapping):
        return [(str(name), str(value)) for name, value in headers.items()]
    try:
        return [(str(name), str(value)) for name, value in headers]
    except Exception:  # noqa: BLE001
        return []


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


__all__ = [
    "QUOTA_HEADER_PREFIX",
    "QUOTA_SNAPSHOT_PRIORITY",
    "RATE_LIMIT_HEADERS",
    "clear_quota_headers",
    "credential_key",
    "latest_quota_header",
    "parse_quota_header",
    "record_quota_headers",
]
