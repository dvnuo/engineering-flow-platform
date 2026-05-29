"""Uniform tool execution flow for EFP Runtime v2."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from ..events import RuntimeEvent
from ..permissions import ALLOW, ASK, DENY, PermissionBroker, PermissionEvaluator
from ..types import ToolCall, ToolResult
from .definition import OutputPolicy, ToolContext, ValidationError
from .registry import ToolRegistry
from .truncation import ToolOutputTruncator, TruncationLimits


class ToolRuntime:
    """Execute registered tools through one normalized path."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_evaluator: PermissionEvaluator | None = None,
        default_output_policy: OutputPolicy | None = None,
        output_truncator: ToolOutputTruncator | None = None,
    ):
        self.registry = registry
        self.permission_evaluator = permission_evaluator or PermissionBroker()
        self.default_output_policy = default_output_policy or OutputPolicy()
        self.output_truncator = output_truncator

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        context: ToolContext | None = None,
    ) -> ToolResult:
        context = context or ToolContext()
        tool_id = tool_call.tool_name
        tool = self.registry.get(tool_id)
        if tool is None:
            message = f"Unknown tool: {tool_id}"
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_id,
                status="error",
                success=False,
                error=message,
                content=message,
                events=[
                    RuntimeEvent(
                        type="tool.error",
                        message="Unknown tool.",
                        payload=_tool_event_payload(
                            tool_id=tool_id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                            extra={"error": message},
                        ),
                    )
                ],
            )

        context = _tool_execution_context(
            context,
            tool_call=tool_call,
            tool_name=tool.id,
        )

        try:
            args = tool.validate_args(tool_call.arguments)
        except ValidationError as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="validation_error",
                success=False,
                error=str(exc),
                content=str(exc),
                events=[
                    RuntimeEvent(
                        type="tool.validation_error",
                        message="Tool arguments failed validation.",
                        payload=_tool_event_payload(
                            tool_id=tool.id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                            extra={"error": str(exc)},
                        ),
                    )
                ],
            )

        decision = await self.permission_evaluator.evaluate(
            tool_id=tool.id,
            args=args,
            metadata=tool.permission,
            context=context,
        )
        if decision.action == DENY:
            message = decision.reason or "Tool execution denied."
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="permission_denied",
                success=False,
                error=message,
                content=message,
                events=[
                    RuntimeEvent(
                        type="tool.permission_denied",
                        message=message,
                        payload=_tool_event_payload(
                            tool_id=tool.id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                        ),
                    )
                ],
            )
        if decision.action == ASK:
            request_payload = decision.request.to_dict() if decision.request else None
            message = decision.reason or "Tool execution requires permission."
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="permission_requested",
                success=False,
                content=message,
                metadata={"permission_request": request_payload},
                events=[
                    RuntimeEvent(
                        type="tool.permission_requested",
                        message=message,
                        payload=_tool_event_payload(
                            tool_id=tool.id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                            extra={"permission_request": request_payload},
                        ),
                    )
                ],
            )
        if decision.action != ALLOW:
            message = f"Unknown permission decision: {decision.action}"
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="permission_denied",
                success=False,
                error=message,
                content=message,
                events=[
                    RuntimeEvent(
                        type="tool.permission_denied",
                        message=message,
                        payload=_tool_event_payload(
                            tool_id=tool.id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                        ),
                    )
                ],
            )

        if await context.is_cancelled():
            message = "Tool execution cancelled."
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="cancelled",
                success=False,
                error=message,
                content=message,
                events=[
                    RuntimeEvent(
                        type="tool.cancelled",
                        message=message,
                        payload=_tool_event_payload(
                            tool_id=tool.id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                        ),
                    )
                ],
            )

        started_event = RuntimeEvent(
            type="tool.started",
            message="Tool execution started.",
            payload=_tool_event_payload(
                tool_id=tool.id,
                tool_call_id=tool_call.call_id,
                context=context,
                include_session_id=True,
                extra={"arg_keys": _argument_keys(args)},
            ),
        )
        started_at = time.monotonic()
        try:
            raw_output = tool.execute(args, context)
            if inspect.isawaitable(raw_output):
                raw_output = await raw_output
            else:
                raise TypeError("Tool execute callable must return an awaitable.")
        except Exception as exc:  # noqa: BLE001 - runtime boundary normalizes tool failures.
            duration_ms = _duration_ms(started_at)
            message = str(exc) or exc.__class__.__name__
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="error",
                success=False,
                error=message,
                content=message,
                metadata={"duration_ms": duration_ms},
                events=[
                    started_event,
                    RuntimeEvent(
                        type="tool.error",
                        message="Tool execution failed.",
                        payload=_tool_event_payload(
                            tool_id=tool.id,
                            tool_call_id=tool_call.call_id,
                            context=context,
                            include_session_id=True,
                            extra={
                                "error": message,
                                "error_type": exc.__class__.__name__,
                                "status": "error",
                                "success": False,
                                "duration_ms": duration_ms,
                            },
                        ),
                    )
                ],
            )

        duration_ms = _duration_ms(started_at)
        policy = _merge_output_policy(self.default_output_policy, tool.output_policy)
        return self._normalize_result(
            call_id=tool_call.call_id,
            tool_name=tool.id,
            raw_output=raw_output,
            policy=policy,
            context=context,
            started_event=started_event,
            duration_ms=duration_ms,
        )

    def _normalize_result(
        self,
        *,
        call_id: str,
        tool_name: str,
        raw_output: Any,
        policy: OutputPolicy,
        context: ToolContext,
        started_event: RuntimeEvent,
        duration_ms: int,
    ) -> ToolResult:
        context_metadata = _context_metadata_updates(context)
        if isinstance(raw_output, ToolResult):
            if _has_tool_truncation_metadata(raw_output):
                content = raw_output.content
                truncated = raw_output.truncated or raw_output.metadata.get("truncated") is True
                metadata = _preserved_tool_result_metadata(
                    raw_output.content,
                    raw_output.metadata,
                    truncated=truncated,
                )
            else:
                content, truncated, metadata = _apply_output_policy(
                    raw_output.content,
                    policy,
                    output_truncator=self.output_truncator,
                )
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=raw_output.status,
                success=raw_output.success,
                content=content,
                output=raw_output.output if policy.include_raw_output else None,
                error=raw_output.error,
                attachments=raw_output.attachments,
                metadata={
                    **metadata,
                    **context_metadata,
                    **raw_output.metadata,
                    "duration_ms": duration_ms,
                },
                truncated=raw_output.truncated or truncated,
                events=[
                    started_event,
                    *raw_output.events,
                    RuntimeEvent(
                        type="tool.completed",
                        message="Tool execution completed.",
                        payload=_tool_event_payload(
                            tool_id=tool_name,
                            tool_call_id=call_id,
                            context=context,
                            include_session_id=True,
                            extra={
                                "status": raw_output.status,
                                "success": raw_output.success,
                                "duration_ms": duration_ms,
                            },
                        ),
                    ),
                ],
            )

        content = _stringify_output(raw_output)
        content, truncated, metadata = _apply_output_policy(
            content,
            policy,
            output_truncator=self.output_truncator,
        )
        return ToolResult(
            call_id=call_id,
            tool_name=tool_name,
            status="success",
            success=True,
            content=content,
            output=raw_output if policy.include_raw_output else None,
            metadata={**metadata, **context_metadata, "duration_ms": duration_ms},
            truncated=truncated,
            events=[
                started_event,
                RuntimeEvent(
                    type="tool.completed",
                    message="Tool execution completed.",
                    payload=_tool_event_payload(
                        tool_id=tool_name,
                        tool_call_id=call_id,
                        context=context,
                        include_session_id=True,
                        extra={
                            "status": "success",
                            "success": True,
                            "duration_ms": duration_ms,
                        },
                    ),
                )
            ],
        )


def _merge_output_policy(default: OutputPolicy, override: OutputPolicy) -> OutputPolicy:
    policy_defaults = OutputPolicy()
    direction = (
        default.truncation_direction
        if override.truncation_direction == policy_defaults.truncation_direction
        else override.truncation_direction
    )
    archive_full_output = (
        default.archive_full_output
        if override.archive_full_output == policy_defaults.archive_full_output
        else override.archive_full_output
    )
    return OutputPolicy(
        max_chars=override.max_chars if override.max_chars is not None else default.max_chars,
        max_lines=override.max_lines if override.max_lines is not None else default.max_lines,
        max_bytes=override.max_bytes if override.max_bytes is not None else default.max_bytes,
        truncation_direction=direction,
        archive_full_output=archive_full_output,
        truncate=override.truncate,
        include_raw_output=override.include_raw_output,
    )


def _stringify_output(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    if is_dataclass(output):
        output = asdict(output)
    try:
        return json.dumps(output, indent=2, sort_keys=True, default=str)
    except TypeError:
        return str(output)


def _apply_output_policy(
    content: str,
    policy: OutputPolicy,
    *,
    output_truncator: ToolOutputTruncator | None = None,
) -> tuple[str, bool, dict[str, Any]]:
    if output_truncator is None:
        return _apply_char_output_policy(content, policy)

    if not policy.truncate:
        metadata = _output_size_metadata(content, truncated=False)
        if _exceeds_output_policy(content, policy, output_truncator):
            metadata["over_limit"] = True
        return content, False, metadata

    limits = _policy_truncation_limits(policy, output_truncator)
    result = output_truncator.truncate(
        content,
        limits=limits,
        allow_archive=policy.archive_full_output,
    )
    visible_content = result.content
    truncated = result.truncated
    metadata = dict(result.metadata)

    if policy.max_chars is None or len(visible_content) <= policy.max_chars:
        return visible_content, truncated, metadata

    visible_content, truncated_chars = _truncate_by_chars(
        visible_content,
        policy.max_chars,
    )
    metadata["truncated"] = True
    metadata["truncated_chars"] = truncated_chars
    metadata["truncated_by"] = _append_truncated_by(
        metadata.get("truncated_by"),
        "chars",
    )
    return visible_content, True, metadata


def _apply_char_output_policy(
    content: str,
    policy: OutputPolicy,
) -> tuple[str, bool, dict[str, Any]]:
    metadata: dict[str, Any] = {"original_chars": len(content)}
    if policy.max_chars is None or len(content) <= policy.max_chars:
        return content, False, metadata
    if not policy.truncate:
        metadata["over_limit"] = True
        return content, False, metadata

    truncated_content, truncated_chars = _truncate_by_chars(content, policy.max_chars)
    metadata["truncated_chars"] = truncated_chars
    return truncated_content, True, metadata


def _truncate_by_chars(content: str, max_chars: int) -> tuple[str, int]:
    if max_chars <= 0:
        return "", len(content)
    marker = "\n[truncated]"
    keep_chars = max(max_chars - len(marker), 0)
    truncated_content = content[:keep_chars].rstrip()
    if keep_chars < max_chars:
        truncated_content = f"{truncated_content}{marker}"[:max_chars]
    return truncated_content, len(content) - len(truncated_content)


def _has_tool_truncation_metadata(result: ToolResult) -> bool:
    return result.truncated is True or "truncated" in result.metadata


def _preserved_tool_result_metadata(
    content: str,
    tool_metadata: Mapping[str, Any],
    *,
    truncated: bool,
) -> dict[str, Any]:
    metadata = _output_size_metadata(content, truncated=truncated)
    metadata.update(dict(tool_metadata))
    return metadata


def _context_metadata_updates(context: ToolContext) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for update in context.metadata_updates:
        if not isinstance(update, Mapping):
            continue
        metadata = update.get("metadata")
        if isinstance(metadata, Mapping):
            merged.update(dict(metadata))
        if "title" in update and update["title"] is not None:
            merged["title"] = str(update["title"])
    return merged


def _output_size_metadata(content: str, *, truncated: bool) -> dict[str, Any]:
    return {
        "original_chars": len(content),
        "original_bytes": len(content.encode("utf-8")),
        "original_lines": len(content.splitlines()),
        "truncated": truncated,
    }


def _policy_truncation_limits(
    policy: OutputPolicy,
    output_truncator: ToolOutputTruncator,
) -> TruncationLimits:
    default_limits = output_truncator.limits
    policy_defaults = OutputPolicy()
    direction = policy.truncation_direction or default_limits.direction
    if (
        policy.truncation_direction == policy_defaults.truncation_direction
        and default_limits.direction != policy_defaults.truncation_direction
    ):
        direction = default_limits.direction
    return TruncationLimits(
        max_lines=(
            policy.max_lines if policy.max_lines is not None else default_limits.max_lines
        ),
        max_bytes=(
            policy.max_bytes if policy.max_bytes is not None else default_limits.max_bytes
        ),
        direction=direction,
    )


def _exceeds_output_policy(
    content: str,
    policy: OutputPolicy,
    output_truncator: ToolOutputTruncator,
) -> bool:
    limits = _policy_truncation_limits(policy, output_truncator)
    if limits.max_lines is not None and len(content.splitlines()) > limits.max_lines:
        return True
    if limits.max_bytes is not None and len(content.encode("utf-8")) > limits.max_bytes:
        return True
    return policy.max_chars is not None and len(content) > policy.max_chars


def _append_truncated_by(value: Any, reason: str) -> list[str]:
    if isinstance(value, list):
        reasons = [str(item) for item in value]
    elif value:
        reasons = [str(value)]
    else:
        reasons = []
    if reason not in reasons:
        reasons.append(reason)
    return reasons


def _tool_execution_context(
    context: ToolContext,
    *,
    tool_call: ToolCall,
    tool_name: str,
) -> ToolContext:
    metadata = context.to_metadata()
    message_id = _first_value(context.message_id, metadata.get("message_id"))
    tool_call_id = _first_value(
        tool_call.call_id,
        context.tool_call_id,
        metadata.get("tool_call_id"),
    )
    resolved_tool_name = _first_value(
        tool_name,
        context.tool_name,
        metadata.get("tool_name"),
    )
    run_id = _first_value(context.run_id, metadata.get("run_id"))
    iteration = context.iteration

    _put_metadata(metadata, "message_id", message_id)
    _put_metadata(metadata, "tool_call_id", tool_call_id)
    _put_metadata(metadata, "tool_name", resolved_tool_name)
    _put_metadata(metadata, "run_id", run_id)
    _put_metadata(metadata, "iteration", iteration)

    return ToolContext(
        session_id=context.session_id,
        request_id=context.request_id,
        message_id=str(message_id) if message_id is not None else None,
        metadata=metadata,
        tool_call_id=str(tool_call_id) if tool_call_id is not None else None,
        tool_name=str(resolved_tool_name) if resolved_tool_name is not None else None,
        run_id=str(run_id) if run_id is not None else None,
        iteration=iteration,
        extra=context.extra,
        messages=context.messages,
        agent=context.agent,
        metadata_updates=context.metadata_updates,
        cancel_requested=context.cancel_requested,
        ask_requester=context.ask_requester,
    )


def _tool_event_payload(
    *,
    tool_id: str,
    tool_call_id: str,
    context: ToolContext,
    extra: Mapping[str, Any] | None = None,
    include_session_id: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "tool_name": tool_id,
    }
    if include_session_id:
        session_id = _first_value(context.session_id, context.metadata.get("session_id"))
        if session_id is not None:
            payload["session_id"] = session_id
    run_id = _first_value(context.run_id, context.metadata.get("run_id"))
    if run_id is not None:
        payload["run_id"] = run_id
    if context.iteration is not None:
        payload["iteration"] = context.iteration
    if extra:
        payload.update(dict(extra))
    return payload


def _argument_keys(args: Mapping[str, Any]) -> list[str]:
    return sorted(str(key) for key in args)


def _duration_ms(started_at: float) -> int:
    return max(0, int(round((time.monotonic() - started_at) * 1000)))


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _put_metadata(metadata: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    existing = metadata.get(key)
    if existing is None or existing == "":
        metadata[key] = value
