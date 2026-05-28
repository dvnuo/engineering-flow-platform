"""HTTP(S) fetch tool for Runtime v2."""

from __future__ import annotations

import asyncio
from functools import partial
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_CHARS = 20000


def create_fetch_tool(
    *,
    permission: PermissionMetadata | None = None,
    default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    default_max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolDef:
    return _create_fetch_tool(
        tool_id="fetch",
        permission=permission,
        default_timeout_seconds=default_timeout_seconds,
        default_max_chars=default_max_chars,
    )


def create_webfetch_tool(
    *,
    permission: PermissionMetadata | None = None,
    default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    default_max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolDef:
    return _create_fetch_tool(
        tool_id="webfetch",
        permission=permission,
        default_timeout_seconds=default_timeout_seconds,
        default_max_chars=default_max_chars,
    )


def _create_fetch_tool(
    *,
    tool_id: str,
    permission: PermissionMetadata | None,
    default_timeout_seconds: float,
    default_max_chars: int,
) -> ToolDef:
    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None,
            partial(
                _fetch_url,
                args,
                default_timeout_seconds=default_timeout_seconds,
                default_max_chars=default_max_chars,
            ),
        )
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            content=output["content"],
            output=output,
            truncated=bool(output["truncated"]),
            metadata={
                "url": output["url"],
                "status_code": output["status_code"],
                "content_type": output["content_type"],
                "bytes": output["bytes"],
                "truncated": output["truncated"],
                "original_chars": output["original_chars"],
            },
        )

    return ToolDef(
        id=tool_id,
        description="Fetch content from an HTTP or HTTPS URL.",
        input_schema={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "timeout": {"type": "number"},
                "max_chars": {"type": "integer"},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=_fetch_permission(permission),
    )


def _fetch_permission(permission: PermissionMetadata | None) -> PermissionMetadata:
    resolved = permission or PermissionMetadata(
        action=ALLOW,
        category="network",
        resource="url",
        risk="medium",
    )
    data = dict(resolved.data)
    data.setdefault("subject_arg", "url")
    return PermissionMetadata(
        action=resolved.action,
        reason=resolved.reason,
        category=resolved.category,
        resource=resolved.resource,
        risk=resolved.risk,
        data=data,
    )


def _fetch_url(
    args: dict[str, Any],
    *,
    default_timeout_seconds: float,
    default_max_chars: int,
) -> dict[str, Any]:
    url = args["url"]
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://.")
    if not parsed.netloc:
        raise ValueError("URL must include a host.")

    timeout = _positive_number(args.get("timeout", default_timeout_seconds), "timeout")
    max_chars = _non_negative_int(args.get("max_chars", default_max_chars), "max_chars")
    headers = _headers(args.get("headers"))
    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read()
            status_code = int(response.getcode())
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP error {exc.code}: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"Request timed out after {timeout:g} seconds.") from exc
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.timeout):
            raise RuntimeError(f"Request timed out after {timeout:g} seconds.") from exc
        raise RuntimeError(f"Request failed: {reason}") from exc

    text = data.decode(_charset_from_content_type(content_type), errors="replace")
    original_chars = len(text)
    truncated = original_chars > max_chars
    content = text[:max_chars] if truncated else text
    return {
        "url": url,
        "status_code": status_code,
        "content_type": content_type,
        "content": content,
        "bytes": len(data),
        "truncated": truncated,
        "original_chars": original_chars,
    }


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0.")
    return float(value)


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0.")
    return value


def _headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("headers must be an object.")
    headers: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("headers keys must be strings.")
        if not isinstance(item, str):
            raise ValueError("headers values must be strings.")
        headers[key] = item
    return headers


def _charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"
