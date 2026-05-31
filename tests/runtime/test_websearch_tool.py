from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ConfiguredPermissionBroker
from efp_runtime.tools.builtin import (
    WebSearchRequest,
    create_core_tool_registry,
    create_websearch_tool,
)
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


def test_default_core_registry_does_not_include_websearch(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert "websearch" not in registry.ids()


def test_core_registry_registers_websearch_when_runner_is_injected(tmp_path: Path):
    def runner(request: WebSearchRequest) -> str:
        return f"results for {request.query}"

    registry = create_core_tool_registry(tmp_path, websearch_runner=runner)

    assert "websearch" in registry.ids()


def test_websearch_include_flag_requires_runner(tmp_path: Path):
    with pytest.raises(
        ValueError,
        match="websearch_runner is required when include_websearch_tool is true.",
    ):
        create_core_tool_registry(tmp_path, include_websearch_tool=True)


@pytest.mark.asyncio
async def test_websearch_executes_runner_with_normalized_request(tmp_path: Path):
    captured: list[WebSearchRequest] = []

    def runner(request: WebSearchRequest) -> dict[str, str]:
        captured.append(request)
        return {
            "provider": "unit-provider",
            "content": f"Found result for {request.query}",
        }

    runtime = ToolRuntime(create_core_tool_registry(tmp_path, websearch_runner=runner))
    result = await runtime.execute(
        ToolCall(
            id="call-websearch",
            tool_id="websearch",
            args={
                "query": " EFP runtime ",
                "numResults": 3,
                "livecrawl": "preferred",
                "type": "deep",
                "contextMaxCharacters": 2048,
            },
        )
    )

    assert captured == [
        WebSearchRequest(
            query="EFP runtime",
            num_results=3,
            livecrawl="preferred",
            type="deep",
            context_max_characters=2048,
        )
    ]
    assert result.status == "success"
    assert result.content == "Found result for EFP runtime"
    assert result.output == {
        "query": "EFP runtime",
        "num_results": 3,
        "livecrawl": "preferred",
        "type": "deep",
        "context_max_characters": 2048,
        "content": "Found result for EFP runtime",
        "provider": "unit-provider",
    }
    metadata = dict(result.metadata)
    assert isinstance(metadata.pop("duration_ms"), int)
    assert metadata["query"] == "EFP runtime"
    assert metadata["provider"] == "unit-provider"


@pytest.mark.asyncio
async def test_websearch_executes_async_runner(tmp_path: Path):
    captured: list[WebSearchRequest] = []

    async def runner(request: WebSearchRequest) -> dict[str, object]:
        captured.append(request)
        return {
            "output": "async search output",
            "metadata": {"provider": "async-provider"},
        }

    runtime = ToolRuntime(create_core_tool_registry(tmp_path, websearch_runner=runner))
    result = await runtime.execute(
        ToolCall(
            id="call-websearch-async",
            tool_id="websearch",
            args={"query": "runtime docs"},
        )
    )

    assert captured == [WebSearchRequest(query="runtime docs")]
    assert result.status == "success"
    assert result.content == "async search output"
    assert result.output["query"] == "runtime docs"
    assert result.output["num_results"] == 8
    assert result.output["livecrawl"] == "fallback"
    assert result.output["type"] == "auto"
    assert result.output["context_max_characters"] is None
    assert result.metadata["provider"] == "async-provider"


@pytest.mark.asyncio
async def test_websearch_accepts_dataclass_runner_result(tmp_path: Path):
    @dataclass(frozen=True)
    class RunnerResult:
        output: dict[str, object]
        provider: str

    def runner(request: WebSearchRequest) -> RunnerResult:
        return RunnerResult(
            output={"items": [{"title": request.query}]},
            provider="dataclass-provider",
        )

    runtime = ToolRuntime(create_core_tool_registry(tmp_path, websearch_runner=runner))
    result = await runtime.execute(
        ToolCall(
            id="call-websearch-dataclass",
            tool_id="websearch",
            args={"query": "dataclass output"},
        )
    )

    assert result.status == "success"
    assert '"title": "dataclass output"' in result.content
    assert result.output["content"] == result.content
    assert result.metadata["provider"] == "dataclass-provider"


def test_websearch_permission_metadata_category_and_subject_arg(tmp_path: Path):
    def runner(request: WebSearchRequest) -> str:
        return request.query

    registry = create_core_tool_registry(tmp_path, websearch_runner=runner)
    permission = registry.require("websearch").permission

    assert permission.category == "websearch"
    assert permission.resource == "query"
    assert permission.risk == "medium"
    assert permission.data["subject_arg"] == "query"


@pytest.mark.asyncio
async def test_websearch_permission_config_can_control_by_query(tmp_path: Path):
    called = False

    def runner(request: WebSearchRequest) -> str:
        nonlocal called
        called = True
        return request.query

    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, websearch_runner=runner),
        permission_evaluator=ConfiguredPermissionBroker(
            {"websearch": {"private*": "deny"}}
        ),
    )
    result = await runtime.execute(
        ToolCall(
            id="call-private-websearch",
            tool_id="websearch",
            args={"query": "private topic"},
        )
    )

    assert result.status == "permission_denied"
    assert result.error == "Permission denied by runtime config: websearch"
    assert called is False


@pytest.mark.asyncio
async def test_websearch_empty_query_raises_value_error_directly():
    tool = create_websearch_tool(lambda request: "unused")

    with pytest.raises(ValueError, match="query must not be empty."):
        await tool.execute({"query": "   "}, ToolContext())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"query": ""}, "query must not be empty."),
        ({"query": "runtime", "type": "slow"}, "Argument 'type' must be one of"),
        (
            {"query": "runtime", "livecrawl": "always"},
            "Argument 'livecrawl' must be one of",
        ),
        (
            {"query": "runtime", "numResults": 0},
            "Argument 'numResults' must be greater than or equal to 1.",
        ),
        (
            {"query": "runtime", "numResults": 21},
            "Argument 'numResults' must be less than or equal to 20.",
        ),
        (
            {"query": "runtime", "contextMaxCharacters": -1},
            "Argument 'contextMaxCharacters' must be greater than or equal to 0.",
        ),
    ],
)
async def test_websearch_rejects_invalid_args(
    tmp_path: Path,
    args: dict[str, object],
    message: str,
):
    called = False

    def runner(request: WebSearchRequest) -> str:
        nonlocal called
        called = True
        return "unused"

    runtime = ToolRuntime(create_core_tool_registry(tmp_path, websearch_runner=runner))
    result = await runtime.execute(
        ToolCall(id="call-invalid-websearch", tool_id="websearch", args=args)
    )

    assert result.success is False
    assert message in (result.error or result.content)
    assert called is False
