"""Utilities for redacting sensitive data from logs."""

from __future__ import annotations

import re
from typing import Any
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
    return normalized in _SENSITIVE_KEYS


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


def redact_value(value: Any) -> Any:
    """Recursively redact sensitive values for common container types."""
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                redacted[k] = REDACTED
            else:
                redacted[k] = redact_value(v)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, set):
        return {redact_value(item) for item in value}
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_preview(value: Any, limit: int = 200) -> str:
    """Sanitize a value then produce a truncated preview string."""
    sanitized = redact_value(value)
    return truncate_with_count(str(sanitized), limit)


def sanitize_exception_message(value: Any) -> str:
    """Sanitize arbitrary exception values for safe logging."""
    return safe_preview(value, limit=500)

