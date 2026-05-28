"""Injectable foreground task tool for EFP Runtime v2."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field
from html import escape
from typing import TYPE_CHECKING, Any, Awaitable, Protocol, Union

from ...permissions import ALLOW, ASK, PermissionMetadata
from ...types import ToolResult, new_id
from ..definition import ToolContext, ToolDef

if TYPE_CHECKING:
    from ...agents.background_tasks import BackgroundTaskManager


BACKGROUND_UNSUPPORTED_MESSAGE = (
    "Background task execution is not supported by this runtime yet."
)
DEFAULT_TASK_TOOL_DESCRIPTION = (
    "Delegate a task to an injected Runtime v2 task runner."
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
    description: str | None = None,
    allow_background: bool = False,
    background_manager: "BackgroundTaskManager | None" = None,
) -> ToolDef:
    """Create the Runtime v2 task tool around an injected runner."""

    if runner is None:
        raise ValueError("runner is required")
    resolved_background_manager = background_manager
    if allow_background and resolved_background_manager is None:
        from ...agents.background_tasks import BackgroundTaskManager

        resolved_background_manager = BackgroundTaskManager()

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        request = _request_from_args(args, context)
        if request.background:
            if not allow_background:
                return _task_error_result(
                    request=request,
                    tool_id=tool_id,
                    message=BACKGROUND_UNSUPPORTED_MESSAGE,
                )
            if resolved_background_manager is None:
                return _task_error_result(
                    request=request,
                    tool_id=tool_id,
                    message="Background task execution requires a task manager.",
                )
            try:
                record = resolved_background_manager.start(request, runner)
            except Exception as exc:  # noqa: BLE001 - task tool normalizes manager failures.
                return _task_error_result(
                    request=request,
                    tool_id=tool_id,
                    message=str(exc) or exc.__class__.__name__,
                )
            return _task_background_started_result(
                request=request,
                tool_id=tool_id,
                record=record,
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
        description=(
            DEFAULT_TASK_TOOL_DESCRIPTION
            if description is None
            else str(description)
        ),
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
        runtime_metadata=(
            {"background_task_manager": resolved_background_manager}
            if resolved_background_manager is not None
            else {}
        ),
    )


def create_task_status_tool(
    manager: "BackgroundTaskManager",
    tool_id: str = "task_status",
) -> ToolDef:
    """Create a tool for listing, reading, or draining background task status."""

    if manager is None:
        raise ValueError("manager is required")

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        task_id = _optional_str(args.get("task_id"))
        session_id = _optional_str(args.get("session_id"))
        drain = bool(args.get("drain", False))
        try:
            if task_id:
                record = manager.get(task_id)
                output = _background_record_payload(manager, record)
                return ToolResult(
                    call_id="",
                    tool_name=tool_id,
                    content=_format_task_status_detail(output),
                    output=output,
                    metadata=_status_metadata(output),
                )
            records = (
                manager.drain_completed(session_id=session_id)
                if drain
                else manager.list(session_id=session_id)
            )
            tasks = [
                _background_record_payload(manager, record) for record in records
            ]
            output = {
                "tasks": tasks,
                "count": len(tasks),
                "session_id": session_id,
                "drain": drain,
            }
            return ToolResult(
                call_id="",
                tool_name=tool_id,
                content=_format_task_status_list(tasks, drain=drain),
                output=output,
                metadata={
                    "background": True,
                    "background_task": True,
                    "count": len(tasks),
                    "session_id": session_id,
                    "drain": drain,
                },
            )
        except KeyError as exc:
            return _tool_error_result(tool_id=tool_id, message=_exception_text(exc))

    return ToolDef(
        id=tool_id,
        description="Read status and results from background subagent tasks.",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "session_id": {"type": "string"},
                "drain": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="task",
            resource="subagent",
            risk="low",
        ),
        metadata={"task_status_tool": True},
        runtime_metadata={"background_task_manager": manager},
    )


def create_task_cancel_tool(
    manager: "BackgroundTaskManager",
    tool_id: str = "task_cancel",
) -> ToolDef:
    """Create a tool for cancelling background subagent tasks."""

    if manager is None:
        raise ValueError("manager is required")

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        task_id = str(args["task_id"])
        try:
            record = manager.cancel(task_id)
        except KeyError as exc:
            return _tool_error_result(tool_id=tool_id, message=_exception_text(exc))
        output = _background_record_payload(manager, record)
        return ToolResult(
            call_id="",
            tool_name=tool_id,
            content=_format_task_cancel_content(output),
            output=output,
            metadata=_status_metadata(output),
        )

    return ToolDef(
        id=tool_id,
        description="Cancel a running background subagent task.",
        input_schema={
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ASK,
            reason="Cancelling a background task requires approval.",
            category="task",
            resource="subagent",
            risk="medium",
        ),
        metadata={"task_cancel_tool": True},
        runtime_metadata={"background_task_manager": manager},
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


def _task_background_started_result(
    *,
    request: TaskToolRequest,
    tool_id: str,
    record: Any,
) -> ToolResult:
    task_id = str(record.task_id)
    content = _format_task_running(
        task_id=task_id,
        description=request.description,
    )
    metadata = _result_metadata(request, task_id=task_id)
    metadata["background_task"] = True
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
            "background": True,
            "state": "running",
            "started_at": getattr(record, "started_at", None),
            "session_id": request.session_id,
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


def _tool_error_result(*, tool_id: str, message: str) -> ToolResult:
    text = str(message)
    return ToolResult(
        call_id="",
        tool_name=tool_id,
        status="error",
        success=False,
        error=text,
        content=text,
        output={"error": text},
        metadata={"background": True, "background_task": True, "error": text},
    )


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


def _format_task_running(*, task_id: str, description: str) -> str:
    return "\n".join(
        [
            f'<task id="{escape(task_id, quote=True)}" state="running">',
            "<task_background>",
            description,
            f'Use task_status with task_id="{escape(task_id, quote=True)}" to check progress.',
            "</task_background>",
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


def _format_task_cancelled(*, task_id: str, text: str) -> str:
    return "\n".join(
        [
            f'<task id="{escape(task_id, quote=True)}" state="cancelled">',
            "<task_cancelled>",
            text,
            "</task_cancelled>",
            "</task>",
        ]
    )


def _format_task_status_detail(output: Mapping[str, Any]) -> str:
    task_id = str(output.get("task_id") or "")
    state = str(output.get("state") or "")
    text = str(output.get("text") or output.get("error") or "")
    if state == "completed":
        return _format_task_result(task_id=task_id, text=text)
    if state == "error":
        return _format_task_error(task_id=task_id, text=text)
    if state == "cancelled":
        return _format_task_cancelled(task_id=task_id, text=text or "Task cancelled.")
    return _format_task_running(
        task_id=task_id,
        description=str(output.get("description") or ""),
    )


def _format_task_status_list(tasks: list[Mapping[str, Any]], *, drain: bool) -> str:
    if not tasks:
        if drain:
            return "No completed background tasks to drain."
        return "No background tasks."
    lines = ["<tasks>"]
    for task in tasks:
        attrs = [
            f'id="{escape(str(task.get("task_id") or ""), quote=True)}"',
            f'state="{escape(str(task.get("state") or ""), quote=True)}"',
        ]
        session_id = task.get("session_id")
        if session_id is not None:
            attrs.append(f'session_id="{escape(str(session_id), quote=True)}"')
        lines.append(f"<task {' '.join(attrs)}>")
        text = task.get("text") or task.get("error") or task.get("description") or ""
        if text:
            lines.append(str(text))
        lines.append("</task>")
    lines.append("</tasks>")
    return "\n".join(lines)


def _format_task_cancel_content(output: Mapping[str, Any]) -> str:
    return (
        f"background task {output.get('task_id')} is {output.get('state')}."
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


def _background_record_payload(manager: Any, record: Any) -> dict[str, Any]:
    converter = getattr(manager, "record_to_dict", None)
    if callable(converter):
        return converter(record)
    result = getattr(record, "result", None)
    result_payload = None
    if result is not None:
        result_payload = {
            "task_id": getattr(result, "task_id", None),
            "text": getattr(result, "text", ""),
            "state": getattr(result, "state", ""),
            "metadata": dict(getattr(result, "metadata", {}) or {}),
        }
    payload = {
        "task_id": getattr(record, "task_id", None),
        "description": getattr(record, "description", ""),
        "prompt": getattr(record, "prompt", ""),
        "subagent_type": getattr(record, "subagent_type", ""),
        "session_id": getattr(record, "session_id", None),
        "started_at": getattr(record, "started_at", None),
        "finished_at": getattr(record, "finished_at", None),
        "state": getattr(record, "state", ""),
        "background": True,
        "result": result_payload,
        "error": getattr(record, "error", None),
        "metadata": dict(getattr(record, "metadata", {}) or {}),
    }
    if result_payload is not None:
        payload["text"] = result_payload["text"]
        payload["result_metadata"] = dict(result_payload["metadata"])
    return payload


def _status_metadata(output: Mapping[str, Any]) -> dict[str, Any]:
    metadata = {
        "background": True,
        "background_task": True,
        "task_id": output.get("task_id"),
        "state": output.get("state"),
        "session_id": output.get("session_id"),
    }
    result_metadata = output.get("result_metadata")
    if isinstance(result_metadata, Mapping):
        metadata["task_result_metadata"] = dict(result_metadata)
    return metadata


def _exception_text(exc: BaseException) -> str:
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc) or exc.__class__.__name__


__all__ = [
    "BACKGROUND_UNSUPPORTED_MESSAGE",
    "DEFAULT_TASK_TOOL_DESCRIPTION",
    "TaskToolRequest",
    "TaskToolResult",
    "TaskToolRunner",
    "create_task_cancel_tool",
    "create_task_status_tool",
    "create_task_tool",
]
