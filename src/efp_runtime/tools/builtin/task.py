"""Injectable foreground task tool for EFP Runtime v2."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from html import escape
from typing import Any, Awaitable, Protocol, Union

from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult, new_id
from ..definition import ToolContext, ToolDef


BACKGROUND_UNSUPPORTED_MESSAGE = (
    "Background task execution is not supported by this runtime yet."
)

TaskToolResponse = Union[str, "TaskToolResult", Mapping[str, Any]]
TaskToolRunnerReturn = Union[TaskToolResponse, Awaitable[TaskToolResponse]]


@dataclass(frozen=True)
class TaskToolRequest:
    """Structured task request passed to an injected task runner."""

    description: str
    prompt: str
    subagent_type: str
    task_id: str
    command: str | None = None
    background: bool = False
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class TaskToolResult:
    """Structured task result returned by an injected task runner."""

    task_id: str | None = None
    text: str = ""
    state: str = "completed"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


class TaskToolRunner(Protocol):
    """Callable boundary for foreground subagent/task execution."""

    def __call__(self, request: TaskToolRequest) -> TaskToolRunnerReturn:
        ...


def create_task_tool(
    runner: TaskToolRunner,
    *,
    tool_id: str = "task",
    allow_background: bool = False,
) -> ToolDef:
    """Create the Runtime v2 task tool around an injected runner."""

    if runner is None:
        raise ValueError("runner is required")

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        request = _request_from_args(args, context)
        if request.background:
            message = BACKGROUND_UNSUPPORTED_MESSAGE
            if allow_background:
                message = "Background task execution is not implemented by this task tool yet."
            return _task_error_result(
                request=request,
                tool_id=tool_id,
                message=message,
            )

        try:
            raw_response = runner(request)
            if inspect.isawaitable(raw_response):
                raw_response = await raw_response
        except Exception as exc:  # noqa: BLE001 - task tool normalizes runner failures.
            return _task_error_result(
                request=request,
                tool_id=tool_id,
                message=str(exc) or exc.__class__.__name__,
            )

        result = _normalize_task_response(raw_response, request)
        state = _normalize_state(result.state)
        if state == "error":
            return _task_error_result(
                request=request,
                tool_id=tool_id,
                message=result.text,
                result=result,
            )
        if state != "completed":
            return _task_error_result(
                request=request,
                tool_id=tool_id,
                message=f"Unsupported foreground task state: {result.state}",
                result=result,
            )

        task_id = result.task_id or request.task_id
        content = _format_task_result(task_id=task_id, text=result.text)
        metadata = _result_metadata(request, result=result, task_id=task_id)
        return ToolResult(
            call_id="",
            tool_name=tool_id,
            status="success",
            success=True,
            content=content,
            output={
                "task_id": task_id,
                "description": request.description,
                "subagent_type": request.subagent_type,
                "background": request.background,
                "state": "completed",
                "text": result.text,
                "metadata": dict(result.metadata),
            },
            metadata=metadata,
        )

    return ToolDef(
        id=tool_id,
        description="Delegate a foreground task to an injected Runtime v2 task runner.",
        input_schema={
            "type": "object",
            "required": ["description", "prompt", "subagent_type"],
            "properties": {
                "description": {"type": "string"},
                "prompt": {"type": "string"},
                "subagent_type": {"type": "string"},
                "task_id": {"type": "string"},
                "command": {"type": "string"},
                "background": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="task",
            resource="subagent",
            risk="medium",
        ),
        metadata={"task_tool": True, "allow_background": allow_background},
    )


def _request_from_args(args: Mapping[str, Any], context: ToolContext) -> TaskToolRequest:
    metadata = dict(context.metadata)
    if context.request_id is not None:
        metadata["request_id"] = context.request_id
    return TaskToolRequest(
        description=str(args["description"]),
        prompt=str(args["prompt"]),
        subagent_type=str(args["subagent_type"]),
        task_id=str(args.get("task_id") or new_id("task")),
        command=_optional_str(args.get("command")),
        background=bool(args.get("background", False)),
        session_id=context.session_id,
        metadata=metadata,
    )


def _normalize_task_response(
    response: TaskToolResponse,
    request: TaskToolRequest,
) -> TaskToolResult:
    if isinstance(response, TaskToolResult):
        return response
    if isinstance(response, str):
        return TaskToolResult(task_id=request.task_id, text=response, state="completed")
    if isinstance(response, Mapping):
        return TaskToolResult(
            task_id=_optional_str(response.get("task_id")) or request.task_id,
            text=str(response.get("text", "")),
            state=str(response.get("state") or "completed"),
            metadata=_metadata_mapping(response.get("metadata")),
        )
    return TaskToolResult(
        task_id=request.task_id,
        text=str(response),
        state="completed",
    )


def _task_error_result(
    *,
    request: TaskToolRequest,
    tool_id: str,
    message: str,
    result: TaskToolResult | None = None,
) -> ToolResult:
    task_id = (result.task_id if result is not None else None) or request.task_id
    text = str(message)
    metadata = _result_metadata(request, result=result, task_id=task_id)
    content = _format_task_error(task_id=task_id, text=text)
    return ToolResult(
        call_id="",
        tool_name=tool_id,
        status="error",
        success=False,
        error=text,
        content=content,
        output={
            "task_id": task_id,
            "description": request.description,
            "subagent_type": request.subagent_type,
            "background": request.background,
            "state": "error",
            "error": text,
            "metadata": dict(result.metadata) if result is not None else {},
        },
        metadata=metadata,
    )


def _result_metadata(
    request: TaskToolRequest,
    *,
    task_id: str,
    result: TaskToolResult | None = None,
) -> dict[str, Any]:
    metadata = {
        "task_id": task_id,
        "description": request.description,
        "subagent_type": request.subagent_type,
        "background": request.background,
    }
    if request.command is not None:
        metadata["command"] = request.command
    if result is not None and result.metadata:
        metadata["task_result_metadata"] = dict(result.metadata)
    return metadata


def _format_task_result(*, task_id: str, text: str) -> str:
    return "\n".join(
        [
            f'<task id="{escape(task_id, quote=True)}" state="completed">',
            "<task_result>",
            text,
            "</task_result>",
            "</task>",
        ]
    )


def _format_task_error(*, task_id: str, text: str) -> str:
    return "\n".join(
        [
            f'<task id="{escape(task_id, quote=True)}" state="error">',
            "<task_error>",
            text,
            "</task_error>",
            "</task>",
        ]
    )


def _normalize_state(state: str | None) -> str:
    return str(state or "").strip().lower()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _metadata_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}
