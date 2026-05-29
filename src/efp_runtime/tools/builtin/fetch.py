"""HTTP(S) fetch tool for Runtime v2."""

from __future__ import annotations

import asyncio
from functools import partial
from html.parser import HTMLParser
import math
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_CHARS = 20000
MAX_TIMEOUT_SECONDS = 120
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_FORMAT = "markdown"
FETCH_FORMATS = {"markdown", "text", "html"}
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; EFP Runtime v2)"


def create_webfetch_tool(
    *,
    permission: PermissionMetadata | None = None,
    default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    default_max_chars: int = DEFAULT_MAX_CHARS,
) -> ToolDef:
    return _create_webfetch_tool(
        tool_id="webfetch",
        permission=permission,
        default_timeout_seconds=default_timeout_seconds,
        default_max_chars=default_max_chars,
    )


def _create_webfetch_tool(
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
        tool_output = {
            "url": output["url"],
            "status_code": output["status_code"],
            "content_type": output["content_type"],
            "content": output["content"],
            "bytes": output["bytes"],
            "truncated": output["truncated"],
            "original_chars": output["original_chars"],
        }
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            content=output["content"],
            output=tool_output,
            truncated=bool(output["truncated"]),
            metadata={
                "url": output["url"],
                "format": output["format"],
                "timeout": output["timeout"],
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
                "format": {
                    "type": "string",
                    "enum": ["markdown", "text", "html"],
                },
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

    format_name = _format(args.get("format", DEFAULT_FORMAT))
    timeout = _capped_timeout(
        _positive_number(args.get("timeout", default_timeout_seconds), "timeout")
    )
    max_chars = _non_negative_int(args.get("max_chars", default_max_chars), "max_chars")
    headers = _headers(args.get("headers"), format_name=format_name)
    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=timeout) as response:
            declared_bytes = _content_length(response.headers.get("Content-Length"))
            if declared_bytes is not None and declared_bytes > MAX_RESPONSE_BYTES:
                raise RuntimeError(
                    "Response is too large: Content-Length "
                    f"{declared_bytes} bytes exceeds {MAX_RESPONSE_BYTES} bytes."
                )
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError(
                    "Response is too large: body exceeds "
                    f"{MAX_RESPONSE_BYTES} bytes."
                )
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

    decoded = data.decode(_charset_from_content_type(content_type), errors="replace")
    content = _render_content(
        decoded,
        content_type=content_type,
        format_name=format_name,
        base_url=url,
    )
    original_chars = len(content)
    truncated = original_chars > max_chars
    visible_content = content[:max_chars] if truncated else content
    return {
        "url": url,
        "format": format_name,
        "timeout": timeout,
        "status_code": status_code,
        "content_type": content_type,
        "content": visible_content,
        "bytes": len(data),
        "truncated": truncated,
        "original_chars": original_chars,
    }


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    if not math.isfinite(float(value)) or value <= 0:
        raise ValueError(f"{name} must be greater than 0.")
    return float(value)


def _capped_timeout(value: float) -> float:
    return min(value, float(MAX_TIMEOUT_SECONDS))


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value < 0:
        raise ValueError(f"{name} must be greater than or equal to 0.")
    return value


def _format(value: Any) -> str:
    if value is None:
        return DEFAULT_FORMAT
    if not isinstance(value, str):
        raise ValueError("format must be a string.")
    if value not in FETCH_FORMATS:
        allowed = ", ".join(sorted(FETCH_FORMATS))
        raise ValueError(f"format must be one of: {allowed}.")
    return value


def _headers(value: Any, *, format_name: str) -> dict[str, str]:
    headers = _default_headers(format_name)
    if value is None:
        return headers
    if not isinstance(value, dict):
        raise ValueError("headers must be an object.")
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("headers keys must be strings.")
        if not isinstance(item, str):
            raise ValueError("headers values must be strings.")
        headers[key] = item
    return headers


def _default_headers(format_name: str) -> dict[str, str]:
    accept_by_format = {
        "markdown": "text/html,application/xhtml+xml,text/markdown,text/plain;q=0.9,*/*;q=0.8",
        "text": "text/plain,text/html;q=0.9,application/xhtml+xml;q=0.8,*/*;q=0.7",
        "html": "text/html,application/xhtml+xml,*/*;q=0.8",
    }
    return {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": accept_by_format[format_name],
        "Accept-Language": "en-US,en;q=0.9",
    }


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _charset_from_content_type(content_type: str) -> str:
    for part in content_type.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"


def _render_content(
    decoded: str,
    *,
    content_type: str,
    format_name: str,
    base_url: str,
) -> str:
    if not _is_html_content_type(content_type):
        return decoded
    if format_name == "html":
        return decoded
    if format_name == "text":
        return _html_to_text(decoded)
    return _html_to_markdown(decoded, base_url=base_url)


def _is_html_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return (
        media_type == "text/html"
        or media_type == "application/xhtml+xml"
        or media_type.endswith("+html")
    )


def _html_to_text(html: str) -> str:
    parser = _ReadableHTMLParser(mode="text")
    parser.feed(html)
    parser.close()
    return parser.render()


def _html_to_markdown(html: str, *, base_url: str) -> str:
    parser = _ReadableHTMLParser(mode="markdown", base_url=base_url)
    parser.feed(html)
    parser.close()
    return parser.render()


class _ReadableHTMLParser(HTMLParser):
    _SKIPPED_CONTAINER_TAGS = {
        "head",
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "embed",
    }
    _SKIPPED_VOID_TAGS = {
        "meta",
        "link",
    }
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hr",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }

    def __init__(self, *, mode: str, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self._mode = mode
        self._base_url = base_url
        self._parts: list[str] = []
        self._skip_depth = 0
        self._link_stack: list[dict[str, Any]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        if tag in self._SKIPPED_VOID_TAGS:
            return
        if tag in self._SKIPPED_CONTAINER_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if self._mode == "markdown":
            self._handle_markdown_starttag(tag, attrs)
        else:
            self._handle_text_starttag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIPPED_CONTAINER_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return

        if self._mode == "markdown":
            self._handle_markdown_endtag(tag)
        else:
            self._handle_text_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._append(data)

    def render(self) -> str:
        return _clean_rendered_text("".join(self._parts))

    def _handle_text_starttag(self, tag: str) -> None:
        if tag == "br":
            self._append("\n")
        elif tag == "li":
            self._break()
        elif tag in self._BLOCK_TAGS or _heading_level(tag) is not None:
            self._break()

    def _handle_text_endtag(self, tag: str) -> None:
        if tag == "li" or tag in self._BLOCK_TAGS or _heading_level(tag) is not None:
            self._break()

    def _handle_markdown_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        heading_level = _heading_level(tag)
        if heading_level is not None:
            self._break()
            self._append(f"{'#' * heading_level} ")
            return
        if tag == "a":
            self._link_stack.append(
                {
                    "href": _href_from_attrs(attrs, base_url=self._base_url),
                    "parts": [],
                }
            )
            return
        if tag == "br":
            self._append("\n")
            return
        if tag == "li":
            self._break()
            self._append("- ")
            return
        if tag in self._BLOCK_TAGS:
            self._break()

    def _handle_markdown_endtag(self, tag: str) -> None:
        if tag == "a" and self._link_stack:
            link = self._link_stack.pop()
            text = _inline_text("".join(link["parts"]))
            href = str(link["href"] or "")
            if text and href:
                self._append(f"[{text}]({href})")
            elif text:
                self._append(text)
            return
        if tag == "li" or tag in self._BLOCK_TAGS or _heading_level(tag) is not None:
            self._break()

    def _append(self, value: str) -> None:
        if not value:
            return
        if self._link_stack:
            self._link_stack[-1]["parts"].append(value)
            return
        self._parts.append(value)

    def _break(self) -> None:
        if self._link_stack:
            self._link_stack[-1]["parts"].append(" ")
            return
        if not self._parts:
            return
        current = "".join(self._parts)
        if current.endswith("\n\n"):
            return
        if current.endswith("\n"):
            self._parts.append("\n")
        else:
            self._parts.append("\n\n")


def _heading_level(tag: str) -> int | None:
    if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        level = int(tag[1])
        if 1 <= level <= 6:
            return level
    return None


def _href_from_attrs(
    attrs: list[tuple[str, str | None]],
    *,
    base_url: str,
) -> str:
    for key, value in attrs:
        if key.lower() == "href" and value:
            return urljoin(base_url, value)
    return ""


def _inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _clean_rendered_text(value: str) -> str:
    text = value.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()
