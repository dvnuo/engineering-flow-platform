from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.permissions import ConfiguredPermissionBroker
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


class LocalFetchHandler(BaseHTTPRequestHandler):
    body = "alias response\n"

    def do_GET(self):  # noqa: N802 - http.server callback name.
        if self.path == "/ok":
            data = self.body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def log_message(self, format, *args):  # noqa: A002 - matches stdlib signature.
        return


@pytest.fixture
def local_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalFetchHandler)
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
async def test_core_registry_defaults_to_remaining_opencode_ids(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert {"webfetch", "todowrite"}.issubset(set(registry.ids()))
    assert {"fetch", "todo_write"}.isdisjoint(set(registry.ids()))
    assert registry.require("webfetch").permission.data["subject_arg"] == "url"


@pytest.mark.asyncio
async def test_core_registry_can_include_remaining_legacy_aliases(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path, include_legacy_aliases=True)

    assert {"fetch", "webfetch", "todo_write", "todowrite"}.issubset(
        set(registry.ids())
    )
    assert registry.require("webfetch").input_schema == registry.require(
        "fetch"
    ).input_schema
    assert registry.require("fetch").permission.data["subject_arg"] == "url"
    assert registry.require("webfetch").permission.data["subject_arg"] == "url"
    assert registry.require("todowrite").input_schema == registry.require(
        "todo_write"
    ).input_schema


@pytest.mark.asyncio
async def test_webfetch_uses_fetch_execution_and_metadata(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path, include_legacy_aliases=True))
    url = f"{local_http_server}/ok"

    fetch_result = await runtime.execute(
        ToolCall(id="call-fetch", tool_id="fetch", args={"url": url})
    )
    webfetch_result = await runtime.execute(
        ToolCall(id="call-webfetch", tool_id="webfetch", args={"url": url})
    )

    assert fetch_result.status == "success"
    assert webfetch_result.status == "success"
    assert webfetch_result.tool_name == "webfetch"
    assert webfetch_result.content == LocalFetchHandler.body
    assert webfetch_result.output == {
        "url": url,
        "status_code": 200,
        "content_type": "text/plain; charset=utf-8",
        "content": LocalFetchHandler.body,
        "bytes": len(LocalFetchHandler.body.encode("utf-8")),
        "truncated": False,
        "original_chars": len(LocalFetchHandler.body),
    }
    fetch_metadata = dict(fetch_result.metadata)
    webfetch_metadata = dict(webfetch_result.metadata)
    assert isinstance(fetch_metadata.pop("duration_ms"), int)
    assert isinstance(webfetch_metadata.pop("duration_ms"), int)
    assert webfetch_metadata == fetch_metadata
    assert {
        "url",
        "status_code",
        "content_type",
        "bytes",
        "truncated",
        "original_chars",
    }.issubset(webfetch_result.metadata)


@pytest.mark.asyncio
async def test_webfetch_rejects_non_http_urls(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-webfetch",
            tool_id="webfetch",
            args={"url": "file:///etc/passwd"},
        )
    )

    assert result.status == "error"
    assert result.success is False
    assert result.tool_name == "webfetch"
    assert "http:// or https://" in result.error


@pytest.mark.asyncio
async def test_todowrite_uses_todo_write_schema_and_normalizes_todos(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-todowrite",
            tool_id="todowrite",
            args={
                "todos": [
                    {"content": "Inspect aliases", "status": "completed"},
                    {"content": "Run tests", "status": "in_progress"},
                ]
            },
        ),
        context=ToolContext(session_id="session-alias"),
    )

    assert result.status == "success"
    assert result.tool_name == "todowrite"
    assert result.output == {
        "todos": [
            {
                "content": "Inspect aliases",
                "status": "completed",
                "priority": "medium",
            },
            {"content": "Run tests", "status": "in_progress", "priority": "medium"},
        ],
        "todo_count": 2,
        "active_todo_count": 1,
        "completed_todo_count": 1,
        "cancelled_todo_count": 0,
    }
    assert result.metadata["todo_count"] == 2
    assert result.metadata["active_todo_count"] == 1
    assert result.metadata["completed_todo_count"] == 1
    assert result.metadata["cancelled_todo_count"] == 0


@pytest.mark.asyncio
async def test_todo_write_and_todowrite_share_registry_store(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path, include_legacy_aliases=True)
    runtime = ToolRuntime(registry)
    todo_store = registry.require("todo_write").runtime_metadata["todos_by_session"]
    alias_store = registry.require("todowrite").runtime_metadata["todos_by_session"]

    assert alias_store is todo_store

    await runtime.execute(
        ToolCall(
            id="call-todo-write",
            tool_id="todo_write",
            args={"todos": [{"content": "From EFP id", "status": "pending"}]},
        ),
        context=ToolContext(session_id="session-shared"),
    )
    assert todo_store["session-shared"] == [
        {"content": "From EFP id", "status": "pending", "priority": "medium"}
    ]

    await runtime.execute(
        ToolCall(
            id="call-todowrite",
            tool_id="todowrite",
            args={
                "todos": [
                    {
                        "content": "From alias",
                        "status": "completed",
                        "priority": "high",
                    }
                ]
            },
        ),
        context=ToolContext(session_id="session-shared"),
    )
    assert todo_store["session-shared"] == [
        {"content": "From alias", "status": "completed", "priority": "high"}
    ]


@pytest.mark.asyncio
async def test_webfetch_category_permission_denies_fetch_ids(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, include_legacy_aliases=True),
        permission_evaluator=ConfiguredPermissionBroker({"webfetch": "deny"}),
    )

    results = [
        await runtime.execute(
            ToolCall(
                id="call-fetch",
                tool_id="fetch",
                args={"url": "http://127.0.0.1/blocked"},
            )
        ),
        await runtime.execute(
            ToolCall(
                id="call-webfetch",
                tool_id="webfetch",
                args={"url": "http://127.0.0.1/blocked"},
            )
        ),
    ]

    assert [result.status for result in results] == [
        "permission_denied",
        "permission_denied",
    ]
    assert {result.tool_name for result in results} == {"fetch", "webfetch"}
    assert {result.error for result in results} == {
        "Permission denied by runtime config: webfetch"
    }


@pytest.mark.asyncio
async def test_webfetch_nested_permission_matches_url_for_fetch_ids(
    tmp_path: Path,
    local_http_server: str,
):
    blocked_url = f"{local_http_server}/blocked"
    allowed_url = f"{local_http_server}/ok"
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, include_legacy_aliases=True),
        permission_evaluator=ConfiguredPermissionBroker(
            {
                "webfetch": {
                    "*": "allow",
                    blocked_url: "deny",
                }
            }
        ),
    )

    fetch_denied = await runtime.execute(
        ToolCall(id="call-fetch", tool_id="fetch", args={"url": blocked_url})
    )
    webfetch_denied = await runtime.execute(
        ToolCall(id="call-webfetch", tool_id="webfetch", args={"url": blocked_url})
    )
    allowed = await runtime.execute(
        ToolCall(id="call-webfetch-ok", tool_id="webfetch", args={"url": allowed_url})
    )

    assert [fetch_denied.status, webfetch_denied.status] == [
        "permission_denied",
        "permission_denied",
    ]
    assert allowed.status == "success"
    assert allowed.content == LocalFetchHandler.body


@pytest.mark.asyncio
async def test_todowrite_category_permission_denies_todo_ids(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, include_legacy_aliases=True),
        permission_evaluator=ConfiguredPermissionBroker({"todowrite": "deny"}),
    )
    args = {"todos": [{"content": "Blocked", "status": "pending"}]}

    results = [
        await runtime.execute(
            ToolCall(id="call-todo-write", tool_id="todo_write", args=args)
        ),
        await runtime.execute(
            ToolCall(id="call-todowrite", tool_id="todowrite", args=args)
        ),
    ]

    assert [result.status for result in results] == [
        "permission_denied",
        "permission_denied",
    ]
    assert {result.tool_name for result in results} == {"todo_write", "todowrite"}
    assert {result.error for result in results} == {
        "Permission denied by runtime config: todowrite"
    }


@pytest.mark.asyncio
async def test_plan_mode_allows_remaining_legacy_aliases_when_enabled(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "planned"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            include_legacy_tool_aliases=True,
            max_iterations=1,
        ),
    )

    result = await runtime.run("Plan.", session_id="session-plan-aliases")

    assert result.status == LoopStatus.COMPLETED
    request_tool_ids = [tool.id for tool in provider.requests[0].tools]
    assert {"fetch", "webfetch", "todo_write", "todowrite"}.issubset(
        request_tool_ids
    )

    disabled_provider = ScriptedLLMProvider([{"content": "planned"}])
    disabled_runtime = AgentRuntime(
        provider=disabled_provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            include_legacy_tool_aliases=True,
            disabled_tools=["webfetch", "todowrite"],
            max_iterations=1,
        ),
    )

    disabled_result = await disabled_runtime.run(
        "Plan with disabled aliases.",
        session_id="session-plan-disabled-aliases",
    )

    assert disabled_result.status == LoopStatus.COMPLETED
    disabled_tool_ids = [tool.id for tool in disabled_provider.requests[0].tools]
    assert "fetch" in disabled_tool_ids
    assert "todo_write" in disabled_tool_ids
    assert "webfetch" not in disabled_tool_ids
    assert "todowrite" not in disabled_tool_ids
