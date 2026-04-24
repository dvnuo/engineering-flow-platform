from __future__ import annotations

import re
from urllib.parse import urlparse

_LINK_RE = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"(?<!\()\bhttps?://[^\s<>)\]]+", re.IGNORECASE)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def extract_markdown_links(text: str) -> list[str]:
    raw = str(text or "")
    links: list[str] = []
    for match in _LINK_RE.finditer(raw):
        links.append(match.group(1))
    for match in _BARE_URL_RE.finditer(raw):
        links.append(match.group(0).rstrip('.,;:!?)'))
    return _dedupe_keep_order(links)


def is_github_asset_url(url: str, *, github_base_host: str | None = None) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if not host or not path:
        return False

    if host == "github.com" and path.startswith("/user-attachments/assets/"):
        return True

    if host.endswith("githubusercontent.com"):
        # Common GitHub user-upload asset urls.
        markers = ("/user-attachments/", "/assets/", "/attachments/")
        if any(marker in path for marker in markers):
            return True

    if github_base_host:
        base_host = github_base_host.lower().strip()
        if host == base_host and path.startswith("/assets/"):
            return True

    return False


def extract_github_asset_urls(text: str, *, github_base_host: str | None = None) -> list[str]:
    links = extract_markdown_links(text)
    return [u for u in links if is_github_asset_url(u, github_base_host=github_base_host)]
