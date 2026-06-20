from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.lsp import LSPPosition, LSPRequest, LSP_OPERATIONS
from efp_runtime.models import ToolCall
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.builtin.lsp import NO_LSP_CLIENT_MESSAGE
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


class RecordingLSPClient:
    def __init__(self, result: Any = None) -> None:
        self.result = [] if result is None else result
        self.requests: list[LSPRequest] = []

    async def execute(self, request: LSPRequest) -> Any:
        self.requests.append(request)
        return self.result


class AsyncUnavailableLSPClient(RecordingLSPClient):
    def __init__(self) -> None:
        super().__init__([{"uri": "file:///unused.py"}])
        self.available_checks: list[str | None] = []

    async def is_available(self, file_path: str | None = None) -> bool:
        self.available_checks.append(file_path)
        return False


def test_core_registry_does_not_include_lsp_by_default(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert "lsp" not in registry.ids()


def test_core_registry_can_include_lsp_with_flag_or_client(tmp_path: Path):
    flag_registry = create_core_tool_registry(tmp_path, include_lsp_tool=True)
    client_registry = create_core_tool_registry(
        tmp_path,
        lsp_client=RecordingLSPClient(),
    )

    assert "lsp" in flag_registry.ids()
    assert "lsp" in client_registry.ids()


@pytest.mark.asyncio
async def test_lsp_go_to_definition_builds_request_and_counts_results(tmp_path: Path):
    target = tmp_path / "pkg" / "app.py"
    target.parent.mkdir()
    target.write_text("def main():\n    pass\n", encoding="utf-8")
    client = RecordingLSPClient(
        [
            {
                "uri": target.as_uri(),
                "range": {"start": {"line": 1, "character": 4}},
            }
        ]
    )
    runtime = ToolRuntime(create_core_tool_registry(tmp_path, lsp_client=client))

    result = await runtime.execute(
        ToolCall(
            id="call-lsp",
            tool_id="lsp",
            args={
                "operation": "goToDefinition",
                "filePath": "pkg/app.py",
                "line": 2,
                "character": 5,
            },
        )
    )

    assert result.status == "success"
    assert result.output["operation"] == "goToDefinition"
    assert result.output["file_path"] == "pkg/app.py"
    assert result.output["line"] == 2
    assert result.output["character"] == 5
    assert result.output["result_count"] == 1

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.operation == "goToDefinition"
    assert request.file_path == str(target.resolve())
    assert request.position == LSPPosition(
        file_path=str(target.resolve()),
        line=2,
        character=5,
    )
    assert request.query is None
    assert request.metadata["workspace_relative_path"] == "pkg/app.py"
    assert request.metadata["zero_based_line"] == 1
    assert request.metadata["zero_based_character"] == 4


@pytest.mark.asyncio
async def test_lsp_workspace_symbol_uses_query_without_file_path(tmp_path: Path):
    client = RecordingLSPClient([])
    runtime = ToolRuntime(create_core_tool_registry(tmp_path, lsp_client=client))

    result = await runtime.execute(
        ToolCall(
            id="call-lsp",
            tool_id="lsp",
            args={"operation": "workspaceSymbol", "query": "Runtime"},
        )
    )

    assert result.status == "success"
    assert result.output["operation"] == "workspaceSymbol"
    assert result.output["file_path"] is None
    assert result.output["query"] == "Runtime"
    assert result.output["result"] == []
    assert result.output["result_count"] == 0
    assert result.output["message"] == "No results found for workspaceSymbol"
    assert "No results found for workspaceSymbol" in result.content

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.operation == "workspaceSymbol"
    assert request.file_path is None
    assert request.position is None
    assert request.query == "Runtime"


@pytest.mark.asyncio
async def test_lsp_missing_file_returns_error_without_calling_client(tmp_path: Path):
    client = RecordingLSPClient([{"contents": "unused"}])
    runtime = ToolRuntime(create_core_tool_registry(tmp_path, lsp_client=client))

    result = await runtime.execute(
        ToolCall(
            id="call-lsp",
            tool_id="lsp",
            args={
                "operation": "hover",
                "filePath": "missing.py",
                "line": 1,
                "character": 1,
            },
        )
    )

    assert result.status == "error"
    assert result.success is False
    assert "File does not exist: missing.py" in result.error
    assert client.requests == []


@pytest.mark.asyncio
async def test_lsp_without_client_or_unavailable_client_returns_standard_error(
    tmp_path: Path,
):
    target = tmp_path / "app.py"
    target.write_text("print('hello')\n", encoding="utf-8")

    no_client_runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, include_lsp_tool=True)
    )
    no_client_result = await no_client_runtime.execute(
        ToolCall(
            id="call-lsp-no-client",
            tool_id="lsp",
            args={
                "operation": "hover",
                "filePath": "app.py",
                "line": 1,
                "character": 1,
            },
        )
    )

    assert no_client_result.status == "error"
    assert no_client_result.error == NO_LSP_CLIENT_MESSAGE

    unavailable_client = AsyncUnavailableLSPClient()
    unavailable_runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, lsp_client=unavailable_client)
    )
    unavailable_result = await unavailable_runtime.execute(
        ToolCall(
            id="call-lsp-unavailable",
            tool_id="lsp",
            args={
                "operation": "hover",
                "filePath": "app.py",
                "line": 1,
                "character": 1,
            },
        )
    )

    assert unavailable_result.status == "error"
    assert unavailable_result.error == NO_LSP_CLIENT_MESSAGE
    assert unavailable_client.available_checks == [str(target.resolve())]
    assert unavailable_client.requests == []


@pytest.mark.asyncio
async def test_agent_runtime_can_expose_lsp_provider_schema(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
            enable_lsp_tool=True,
        ),
        lsp_client=RecordingLSPClient(),
    )

    result = await runtime.run("Inspect code.", session_id="session-lsp")

    assert result.status == LoopStatus.COMPLETED
    assert "lsp" in runtime.tool_runtime.registry.ids()
    request = provider.requests[0]
    schema = next(tool for tool in request.provider_request.tools if tool.id == "lsp")
    assert schema.json_schema["properties"]["operation"]["enum"] == list(LSP_OPERATIONS)


def test_lsp_import_boundary():
    code = """
import json
import sys
from pathlib import Path

from efp_runtime.lsp import LSP_OPERATIONS, LSPPosition, LSPRequest
from efp_runtime.tools.builtin import create_lsp_tool

tool = create_lsp_tool(Path(".").resolve())
request = LSPRequest(
    operation="hover",
    file_path="app.py",
    position=LSPPosition(file_path="app.py", line=1, character=1),
)
legacy_modules = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
    "tool_id": tool.id,
    "operation_count": len(LSP_OPERATIONS),
    "request_operation": request.operation,
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "tool_id": "lsp",
        "operation_count": 9,
        "request_operation": "hover",
        "legacy_loaded": [],
    }
