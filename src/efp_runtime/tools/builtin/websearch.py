"""Provider-neutral websearch boundary for EFP runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
import inspect
import json
from typing import Any, Literal, Protocol

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


DEFAULT_NUM_RESULTS = 8
MIN_NUM_RESULTS = 1
MAX_NUM_RESULTS = 20
DEFAULT_LIVECRAWL = "fallback"
DEFAULT_SEARCH_TYPE = "auto"
LivecrawlMode = Literal["fallback", "preferred"]
WebSearchType = Literal["auto", "fast", "deep"]
_LIVECRAWL_VALUES = {"fallback", "preferred"}
_SEARCH_TYPE_VALUES = {"auto", "fast", "deep"}
_ALLOWED_ARGS = {
    "query",
    "numResults",
    "livecrawl",
    "type",
    "contextMaxCharacters",
}
_MISSING = object()


@dataclass(frozen=True)
class WebSearchRequest:
    """Normalized request passed to an injected websearch runner."""

    query: str
    num_results: int = DEFAULT_NUM_RESULTS
    livecrawl: LivecrawlMode = DEFAULT_LIVECRAWL
    type: WebSearchType = DEFAULT_SEARCH_TYPE
    context_max_characters: int | None = None


class WebSearchRunner(Protocol):
    """Callable boundary for provider-neutral websearch execution."""

    def __call__(self, request: WebSearchRequest) -> Any | Awaitable[Any]:
        ...


def create_websearch_tool(
    runner: WebSearchRunner,
    *,
    permission: PermissionMetadata | None = None,
) -> ToolDef:
    """Create the conditional websearch tool backed by an injected runner."""

    if runner is None:
        raise ValueError("websearch runner is required.")

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        request = _request_from_args(args)
        runner_result = runner(request)
        if inspect.isawaitable(runner_result):
            runner_result = await runner_result
        content = _content_from_runner_result(runner_result)
        output = _stable_output(request, content, runner_result)
        metadata = _stable_metadata(request, runner_result)
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name="websearch",
            content=content,
            output=output,
            metadata=metadata,
        )

    return ToolDef(
        id="websearch",
        description="Search the web through an injected provider-neutral runner.",
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "numResults": {
                    "type": "integer",
                    "minimum": MIN_NUM_RESULTS,
                    "maximum": MAX_NUM_RESULTS,
                },
                "livecrawl": {
                    "type": "string",
                    "enum": sorted(_LIVECRAWL_VALUES),
                },
                "type": {
                    "type": "string",
                    "enum": sorted(_SEARCH_TYPE_VALUES),
                },
                "contextMaxCharacters": {
                    "type": "integer",
                    "minimum": 0,
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=_websearch_permission(permission),
    )


def _websearch_permission(
    permission: PermissionMetadata | None,
) -> PermissionMetadata:
    resolved = permission or PermissionMetadata(
        action=ALLOW,
        category="websearch",
        resource="query",
        risk="medium",
    )
    data = dict(resolved.data)
    data.setdefault("subject_arg", "query")
    return PermissionMetadata(
        action=resolved.action,
        reason=resolved.reason,
        category=resolved.category or "websearch",
        resource=resolved.resource or "query",
        risk=resolved.risk or "medium",
        data=data,
    )


def _request_from_args(args: Mapping[str, Any]) -> WebSearchRequest:
    extra = sorted(set(args) - _ALLOWED_ARGS)
    if extra:
        raise ValueError(f"Unexpected argument(s): {', '.join(extra)}")

    query = args.get("query")
    if not isinstance(query, str):
        raise ValueError("query must be a string.")
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty.")

    num_results = _integer_arg(
        args,
        "numResults",
        default=DEFAULT_NUM_RESULTS,
    )
    if num_results < MIN_NUM_RESULTS or num_results > MAX_NUM_RESULTS:
        raise ValueError(
            f"numResults must be between {MIN_NUM_RESULTS} and {MAX_NUM_RESULTS}."
        )

    livecrawl = args.get("livecrawl", DEFAULT_LIVECRAWL)
    if livecrawl not in _LIVECRAWL_VALUES:
        allowed = ", ".join(sorted(_LIVECRAWL_VALUES))
        raise ValueError(f"livecrawl must be one of: {allowed}.")

    search_type = args.get("type", DEFAULT_SEARCH_TYPE)
    if search_type not in _SEARCH_TYPE_VALUES:
        allowed = ", ".join(sorted(_SEARCH_TYPE_VALUES))
        raise ValueError(f"type must be one of: {allowed}.")

    context_max_characters = _optional_integer_arg(args, "contextMaxCharacters")
    if context_max_characters is not None and context_max_characters < 0:
        raise ValueError("contextMaxCharacters must be greater than or equal to 0.")

    return WebSearchRequest(
        query=query,
        num_results=num_results,
        livecrawl=livecrawl,
        type=search_type,
        context_max_characters=context_max_characters,
    )


def _integer_arg(
    args: Mapping[str, Any],
    name: str,
    *,
    default: int,
) -> int:
    if name not in args:
        return default
    value = args[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    return value


def _optional_integer_arg(args: Mapping[str, Any], name: str) -> int | None:
    value = args.get(name, _MISSING)
    if value is _MISSING:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    return value


def _content_from_runner_result(value: Any) -> str:
    if isinstance(value, str):
        return value
    data = _mapping_from_result(value)
    if data is not None:
        if data.get("content") is not None:
            return _content_text(data["content"])
        if data.get("output") is not None:
            return _content_text(data["output"])
        return _json_text(data)
    return _content_text(value)


def _stable_output(
    request: WebSearchRequest,
    content: str,
    runner_result: Any,
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "query": request.query,
        "num_results": request.num_results,
        "livecrawl": request.livecrawl,
        "type": request.type,
        "context_max_characters": request.context_max_characters,
        "content": content,
    }
    provider = _provider_from_result(runner_result)
    if provider is not None:
        output["provider"] = provider
    return output


def _stable_metadata(
    request: WebSearchRequest,
    runner_result: Any,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"query": request.query}
    provider = _provider_from_result(runner_result)
    if provider is not None:
        metadata["provider"] = provider
    return metadata


def _provider_from_result(value: Any) -> Any:
    data = _mapping_from_result(value)
    if data is None:
        return None
    provider = data.get("provider")
    nested_metadata = data.get("metadata")
    if provider is None and isinstance(nested_metadata, Mapping):
        provider = nested_metadata.get("provider")
    if provider is None:
        return None
    if isinstance(provider, (str, int, float, bool)):
        return provider
    return str(provider)


def _mapping_from_result(value: Any) -> dict[str, Any] | None:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, (Mapping, list)):
        return _json_text(value)
    return str(value)


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


__all__ = [
    "WebSearchRequest",
    "WebSearchRunner",
    "create_websearch_tool",
]
