from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ASK, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.runtime import ToolRuntime


RESPONSE_LIMIT_BYTES = 5 * 1024 * 1024


class FetchFormatHandler(BaseHTTPRequestHandler):
    html_body = """\
<!doctype html>
<html>
  <head>
    <title>Hidden title</title>
    <style>.secret { display: none; }</style>
    <script>window.bad = 1;</script>
  </head>
  <body>
    <h1>Main Title</h1>
    <p>Hello <a href="/docs">docs</a> and friends.</p>
    <ul>
      <li>First item</li>
      <li>Second item</li>
    </ul>
  </body>
</html>
"""
    plain_body = "plain response\n"
    long_body = "0123456789"
    declared_large_hits = 0

    def do_GET(self):  # noqa: N802 - http.server callback name.
        if self.path == "/html":
            self._send("text/html; charset=utf-8", self.html_body.encode("utf-8"))
            return
        if self.path == "/plain":
            self._send("text/plain; charset=utf-8", self.plain_body.encode("utf-8"))
            return
        if self.path == "/long":
            self._send("text/plain; charset=utf-8", self.long_body.encode("utf-8"))
            return
        if self.path == "/echo-headers":
            body = "\n".join(
                [
                    self.headers.get("User-Agent", ""),
                    self.headers.get("Accept", ""),
                    self.headers.get("Accept-Language", ""),
                ]
            )
            self._send("text/plain; charset=utf-8", body.encode("utf-8"))
            return
        if self.path == "/declared-too-large":
            type(self).declared_large_hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(RESPONSE_LIMIT_BYTES + 1))
            self.end_headers()
            return
        if self.path == "/actual-too-large":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            remaining = RESPONSE_LIMIT_BYTES + 1
            chunk = b"x" * 65536
            while remaining > 0:
                data = chunk[:remaining]
                self.wfile.write(data)
                remaining -= len(data)
            return
        self.send_error(404)

    def log_message(self, format, *args):  # noqa: A002 - matches stdlib signature.
        return

    def _send(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def local_http_server():
    FetchFormatHandler.declared_large_hits = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FetchFormatHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_webfetch_defaults_to_markdown_for_html(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch-markdown",
            tool_id="webfetch",
            args={"url": f"{local_http_server}/html"},
        )
    )

    assert result.status == "success"
    assert result.metadata["format"] == "markdown"
    assert "# Main Title" in result.content
    assert "Hello [docs](" in result.content
    assert "- First item" in result.content
    assert "window.bad" not in result.content
    assert "secret" not in result.content
    assert "Hidden title" not in result.content


@pytest.mark.asyncio
async def test_webfetch_text_format_extracts_visible_html_text(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch-text",
            tool_id="webfetch",
            args={"url": f"{local_http_server}/html", "format": "text"},
        )
    )

    assert result.status == "success"
    assert result.metadata["format"] == "text"
    assert "Main Title" in result.content
    assert "Hello docs and friends." in result.content
    assert "# Main Title" not in result.content
    assert "window.bad" not in result.content
    assert "secret" not in result.content
    assert "Hidden title" not in result.content


@pytest.mark.asyncio
async def test_webfetch_html_format_returns_raw_html(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch-html",
            tool_id="webfetch",
            args={"url": f"{local_http_server}/html", "format": "html"},
        )
    )

    assert result.status == "success"
    assert result.metadata["format"] == "html"
    assert "<script>window.bad = 1;</script>" in result.content
    assert "<style>.secret { display: none; }</style>" in result.content


@pytest.mark.parametrize("format_name", ["markdown", "text", "html"])
@pytest.mark.asyncio
async def test_webfetch_non_html_text_stays_text(
    tmp_path: Path,
    local_http_server: str,
    format_name: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id=f"call-webfetch-plain-{format_name}",
            tool_id="webfetch",
            args={"url": f"{local_http_server}/plain", "format": format_name},
        )
    )

    assert result.status == "success"
    assert result.content == FetchFormatHandler.plain_body


@pytest.mark.asyncio
async def test_webfetch_rejects_invalid_format(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch-invalid-format",
            tool_id="webfetch",
            args={"url": "http://example.test", "format": "pdf"},
        )
    )

    assert result.status == "validation_error"
    assert "format" in result.error
    assert "markdown" in result.error


@pytest.mark.asyncio
async def test_fetch_timeout_above_limit_is_capped_in_metadata(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-fetch-timeout-cap",
            tool_id="fetch",
            args={"url": f"{local_http_server}/plain", "timeout": 999},
        )
    )

    assert result.status == "success"
    assert result.metadata["timeout"] == 120.0


@pytest.mark.asyncio
async def test_fetch_permission_request_includes_url_format_and_timeout(
    tmp_path: Path,
):
    permission = PermissionMetadata(
        action=ASK,
        category="network",
        resource="url",
        risk="medium",
        reason="Review URL access.",
    )
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, fetch_permission=permission)
    )

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch-permission",
            tool_id="webfetch",
            args={
                "url": "https://example.test/page",
                "format": "text",
                "timeout": 999,
            },
        )
    )

    request = result.metadata["permission_request"]
    assert result.status == "permission_requested"
    assert request["category"] == "network"
    assert request["resource"] == "url"
    assert request["risk"] == "medium"
    assert request["metadata"]["url"] == "https://example.test/page"
    assert request["metadata"]["format"] == "text"
    assert request["metadata"]["timeout"] == 120.0


@pytest.mark.asyncio
async def test_fetch_rejects_declared_response_over_five_mib(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-fetch-declared-large",
            tool_id="fetch",
            args={"url": f"{local_http_server}/declared-too-large"},
        )
    )

    assert result.status == "error"
    assert "Content-Length" in result.error
    assert "too large" in result.error
    assert FetchFormatHandler.declared_large_hits == 1


@pytest.mark.asyncio
async def test_fetch_rejects_actual_response_over_five_mib(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-fetch-actual-large",
            tool_id="fetch",
            args={"url": f"{local_http_server}/actual-too-large"},
        )
    )

    assert result.status == "error"
    assert "body exceeds" in result.error


@pytest.mark.asyncio
async def test_webfetch_max_chars_truncates_visible_content(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch-max-chars",
            tool_id="webfetch",
            args={"url": f"{local_http_server}/long", "max_chars": 4},
        )
    )

    assert result.status == "success"
    assert result.content == "0123"
    assert result.truncated is True
    assert result.metadata["truncated"] is True
    assert result.metadata["original_chars"] == len(FetchFormatHandler.long_body)
    assert result.output["original_chars"] == len(FetchFormatHandler.long_body)


@pytest.mark.asyncio
async def test_fetch_caller_headers_override_defaults(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-fetch-headers",
            tool_id="fetch",
            args={
                "url": f"{local_http_server}/echo-headers",
                "headers": {
                    "User-Agent": "custom-agent",
                    "Accept": "application/custom",
                    "Accept-Language": "zz-ZZ",
                },
            },
        )
    )

    assert result.status == "success"
    assert result.content.splitlines() == [
        "custom-agent",
        "application/custom",
        "zz-ZZ",
    ]


@pytest.mark.asyncio
async def test_fetch_and_webfetch_keep_called_tool_names(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))
    url = f"{local_http_server}/plain"

    fetch_result = await runtime.execute(
        ToolCall(id="call-fetch-name", tool_id="fetch", args={"url": url})
    )
    webfetch_result = await runtime.execute(
        ToolCall(id="call-webfetch-name", tool_id="webfetch", args={"url": url})
    )

    assert fetch_result.status == "success"
    assert webfetch_result.status == "success"
    assert fetch_result.tool_name == "fetch"
    assert webfetch_result.tool_name == "webfetch"
    assert fetch_result.content == FetchFormatHandler.plain_body
    assert webfetch_result.content == FetchFormatHandler.plain_body
