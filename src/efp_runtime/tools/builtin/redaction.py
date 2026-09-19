"""Secret redaction for built-in tool output.

The ``bash`` tool hands raw process output to the model and archives it under
the workspace. Troubleshooting CLIs (aws, kubectl, pgsql, ...) can print
credentials, tokens, or private keys, so both copies pass through this filter
first. It is deliberately pattern-based and self-contained: the runtime
package must not import ``src.utils`` (see tests/runtime/test_import_boundary),
and a false positive costs a ``***REDACTED***`` in an otherwise readable
output, while a miss leaks a secret into the transcript and the workspace.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "***REDACTED***"

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN (?:[A-Z ]*?)PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]*?)PRIVATE KEY-----",
    re.IGNORECASE,
)

# key=value / key: value / "key": "value" forms for well-known secret names.
_SECRET_KEY_NAMES = (
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "session_token",
    "sessionkey",
    "session_key",
    "api_key",
    "apikey",
    "api_token",
    "secret",
    "secret_key",
    "client_secret",
    "private_key",
    "aws_secret_access_key",
    "aws_session_token",
)
_KEY_GROUP = "|".join(re.escape(name) for name in _SECRET_KEY_NAMES)

_TEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|splunk|token)\s+)([^\s,;\"']+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)((?:x-api-key|x-auth-token|cookie|set-cookie)\s*:\s*)([^\n\r]+)"), r"\1" + REDACTED),
    # "password": "value" / 'token': 'value' (JSON and YAML flow style)
    (re.compile(r'(?i)(["\'](?:' + _KEY_GROUP + r')["\']\s*:\s*["\'])([^"\']*)(["\'])'), r"\1" + REDACTED + r"\3"),
    # password=value / token: value / PGPASSWORD=value / AWS_SECRET_ACCESS_KEY=value
    (re.compile(r"(?i)(\b(?:" + _KEY_GROUP + r")\s*[=:]\s*)([^\s&\"',;]+)"), r"\1" + REDACTED),
    (re.compile(r"(?i)(\b(?:pgpassword|ad_pass|saml2aws_password|gh_token|github_token|gh_enterprise_token)\s*=\s*)([^\s\"',;]+)"), r"\1" + REDACTED),
    # Well-known token shapes.
    (re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bxox[bpa]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), REDACTED),
]

_URL_CREDS_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@[^\s]+", re.IGNORECASE)


def _sanitize_url_credentials(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return raw
        if parsed.username is None or parsed.hostname is None:
            return raw
        netloc = f"{parsed.username}:{REDACTED}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    return _URL_CREDS_RE.sub(_replace, text)


def redact_tool_output(text: str | None) -> str:
    """Return ``text`` with embedded credentials replaced by ``***REDACTED***``."""

    if not text:
        return ""
    value = str(text)
    value = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, value)
    value = _sanitize_url_credentials(value)
    for pattern, replacement in _TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    return value
