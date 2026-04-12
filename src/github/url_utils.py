"""Utilities for GitHub URL normalization."""

from typing import Optional
from urllib.parse import urlsplit, urlunsplit


PUBLIC_GITHUB_WEB_HOST = "github.com"
PUBLIC_GITHUB_API_BASE = "https://api.github.com"
PUBLIC_GITHUB_API_HOST = "api.github.com"


def normalize_github_api_base_url(raw: Optional[str]) -> str:
    """Normalize configured GitHub base URL to a deterministic API base URL.

    Rules:
    - Empty/None => https://api.github.com
    - https://github.com => https://api.github.com
    - Enterprise host/root => https://<host>/api/v3
    - Preserve already-correct enterprise /api/v3 base
    - Remove trailing slash on the returned base URL
    """
    value = (raw or "").strip()
    if not value:
        return PUBLIC_GITHUB_API_BASE

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlsplit(value)
    host = parsed.hostname
    if not host:
        return PUBLIC_GITHUB_API_BASE

    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"

    path = (parsed.path or "").rstrip("/")

    if host.lower() == PUBLIC_GITHUB_WEB_HOST:
        return PUBLIC_GITHUB_API_BASE
    if host.lower() == PUBLIC_GITHUB_API_HOST:
        return PUBLIC_GITHUB_API_BASE

    if path == "":
        path = "/api/v3"
    elif path.lower() == "/api/v3":
        path = "/api/v3"

    return urlunsplit(("https", netloc, path, "", ""))
