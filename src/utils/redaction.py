"""Utilities for redacting sensitive data from logs."""

from __future__ import annotations

import re
from typing import Any
from collections.abc import Mapping, Sequence, Set as AbstractSet
from urllib.parse import urlsplit, urlunsplit

from src.utils.truncate import truncate_with_count

REDACTED = "***REDACTED***"

_SENSITIVE_KEYS = {
    "password", "passwd", "pwd",
    "token", "access_token", "refresh_token",
    "api_key", "apitoken", "api_token",
    "secret", "secret_key",
    "private_key", "ssh_key", "ssh_private_key",
    "authorization", "cookie", "session",
    "github_token", "github_api_token",
    "openai_api_key", "llm_api_key",
    "proxy_password",
}
_COMPACT_SENSITIVE_KEYS = {key.replace("_", "") for key in _SENSITIVE_KEYS}

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?:PRIVATE KEY|RSA PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END (?:PRIVATE KEY|RSA PRIVATE KEY|OPENSSH PRIVATE KEY)-----",
    re.IGNORECASE,
)

_TEXT_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(authorization\s*:\s*basic\s+)([^\s,;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(cookie\s*:\s*)([^\n\r]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\btoken\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bpassword\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bapi_key\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bsecret\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\baccess_token\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\brefresh_token\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\bsecret_key\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r'(?i)(["\']password["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r'(?i)(["\']token["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r'(?i)(["\']access_token["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r'(?i)(["\']refresh_token["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r'(?i)(["\']api_key["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r'(?i)(["\']secret["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r'(?i)(["\']secret_key["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    (re.compile(r"\bghp_[A-Za-z0-9_]+\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]+\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), REDACTED),
    (re.compile(r"\bxoxb-[A-Za-z0-9-]{8,}\b"), REDACTED),
]

_URL_CREDS_RE = re.compile(r"\bhttps?://[^\s/@:]+:[^\s/@]+@[^\s]+", re.IGNORECASE)


def _sanitize_url_credentials(text: str) -> str:
    def _replace(match: re.Match) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
            if parsed.username is None or parsed.hostname is None:
                return raw
            netloc = f"{parsed.username}:{REDACTED}@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        except Exception:
            return raw

    return _URL_CREDS_RE.sub(_replace, text)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in _SENSITIVE_KEYS:
        return True
    compact = normalized.replace("_", "")
    return compact in _COMPACT_SENSITIVE_KEYS


def redact_text(text: str) -> str:
    """Redact sensitive values embedded in free text."""
    if text is None:
        return ""
    value = str(text)
    value = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, value)
    value = _sanitize_url_credentials(value)
    for pattern, replacement in _TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _redact_value_internal(value: Any, *, seen: set[int], depth: int, max_depth: int) -> Any:
    if depth > max_depth:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return value

    is_container = isinstance(value, (Mapping, Sequence, AbstractSet)) and not isinstance(value, (str, bytes, bytearray))
    if is_container:
        obj_id = id(value)
        if obj_id in seen:
            return REDACTED
        seen.add(obj_id)
        try:
            if isinstance(value, Mapping):
                redacted: dict[Any, Any] = {}
                for k, v in value.items():
                    if _is_sensitive_key(k):
                        redacted[k] = REDACTED
                    else:
                        redacted[k] = _redact_value_internal(v, seen=seen, depth=depth + 1, max_depth=max_depth)
                return redacted
            if isinstance(value, tuple):
                return tuple(_redact_value_internal(item, seen=seen, depth=depth + 1, max_depth=max_depth) for item in value)
            if isinstance(value, Sequence):
                return [_redact_value_internal(item, seen=seen, depth=depth + 1, max_depth=max_depth) for item in value]
            if isinstance(value, AbstractSet):
                return {_redact_value_internal(item, seen=seen, depth=depth + 1, max_depth=max_depth) for item in value}
        finally:
            seen.discard(obj_id)
    return value


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive values for common container types."""
    return _redact_value_internal(value, seen=set(), depth=0, max_depth=20)


def safe_preview(value: Any, limit: int = 200) -> str:
    """Sanitize a value then produce a truncated preview string."""
    sanitized = redact_value(value)
    text = str(sanitized)
    text = redact_text(text)
    return truncate_with_count(text, limit)


def sanitize_exception_message(value: Any) -> str:
    """Sanitize arbitrary exception values for safe logging."""
    return safe_preview(value, limit=500)
