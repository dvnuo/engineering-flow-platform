"""Shared proxy configuration helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlparse, urlunparse


DEFAULT_NO_PROXY = "localhost,127.0.0.1,169.254.169.254,.svc.cluster.local"


def proxy_url_with_credentials(url: Any, username: Any = None, password: Any = None) -> str:
    """Return proxy URL with configured credentials inserted when possible."""

    proxy_url = "" if url is None else str(url)
    if username and password:
        parsed = urlparse(proxy_url)
        hostport = parsed.netloc.rsplit("@", 1)[-1]
        if parsed.scheme and parsed.netloc and hostport:
            encoded_username = quote(str(username), safe="")
            encoded_password = quote(str(password), safe="")
            netloc = f"{encoded_username}:{encoded_password}@{hostport}"
            proxy_url = urlunparse(
                (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
            )
    return proxy_url


def no_proxy_value(proxy_config: Mapping[str, Any], default: str = DEFAULT_NO_PROXY) -> str:
    """Return no_proxy/noProxy from a proxy config, falling back to the runtime default."""

    raw = proxy_config.get("no_proxy")
    if raw is None or str(raw).strip() == "":
        raw = proxy_config.get("noProxy")
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw)
