"""Uniform tool execution flow for EFP Runtime v2."""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from ..events import RuntimeEvent
from ..permissions import ALLOW, ASK, DENY, PermissionEvaluator, StaticPermissionEvaluator
from ..types import ToolCall, ToolResult
from .definition import OutputPolicy, ToolContext, ValidationError
from .registry import ToolRegistry


class ToolRuntime:
    """Execute registered tools through one normalized path."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_evaluator: PermissionEvaluator | None = None,
        default_output_policy: OutputPolicy | None = None,
    ):
        self.registry = registry
        self.permission_evaluator = permission_evaluator or StaticPermissionEvaluator()
        self.default_output_policy = default_output_policy or OutputPolicy()

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
                        payload={"tool_id": tool_id},
                    )
                ],
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
                        payload={"tool_id": tool.id, "error": str(exc)},
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
                        payload={"tool_id": tool.id},
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
                        payload={"tool_id": tool.id, "permission_request": request_payload},
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
                        payload={"tool_id": tool.id},
                    )
                ],
            )

        try:
            raw_output = tool.execute(args, context)
            if inspect.isawaitable(raw_output):
                raw_output = await raw_output
            else:
                raise TypeError("Tool execute callable must return an awaitable.")
        except Exception as exc:  # noqa: BLE001 - runtime boundary normalizes tool failures.
            message = str(exc) or exc.__class__.__name__
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool.id,
                status="error",
                success=False,
                error=message,
                content=message,
                events=[
                    RuntimeEvent(
                        type="tool.error",
                        message="Tool execution failed.",
                        payload={"tool_id": tool.id, "error": message},
                    )
                ],
            )

        policy = _merge_output_policy(self.default_output_policy, tool.output_policy)
        return self._normalize_result(
            call_id=tool_call.call_id,
            tool_name=tool.id,
            raw_output=raw_output,
            policy=policy,
        )

    def _normalize_result(
        self,
        *,
        call_id: str,
        tool_name: str,
        raw_output: Any,
        policy: OutputPolicy,
    ) -> ToolResult:
        if isinstance(raw_output, ToolResult):
            content, truncated, metadata = _apply_output_policy(raw_output.content, policy)
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=raw_output.status,
                success=raw_output.success,
                content=content,
                output=raw_output.output if policy.include_raw_output else None,
                error=raw_output.error,
                metadata={**raw_output.metadata, **metadata},
                truncated=raw_output.truncated or truncated,
                events=[
                    *raw_output.events,
                    RuntimeEvent(
                        type="tool.completed",
                        message="Tool execution completed.",
                        payload={"tool_id": tool_name},
                    ),
                ],
            )

        content = _stringify_output(raw_output)
        content, truncated, metadata = _apply_output_policy(content, policy)
        return ToolResult(
            call_id=call_id,
            tool_name=tool_name,
            status="success",
            success=True,
            content=content,
            output=raw_output if policy.include_raw_output else None,
            metadata=metadata,
            truncated=truncated,
            events=[
                RuntimeEvent(
                    type="tool.completed",
                    message="Tool execution completed.",
                    payload={"tool_id": tool_name},
                )
            ],
        )


def _merge_output_policy(default: OutputPolicy, override: OutputPolicy) -> OutputPolicy:
    return OutputPolicy(
        max_chars=override.max_chars if override.max_chars is not None else default.max_chars,
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


def _apply_output_policy(content: str, policy: OutputPolicy) -> tuple[str, bool, dict[str, Any]]:
    metadata: dict[str, Any] = {"original_chars": len(content)}
    if policy.max_chars is None or len(content) <= policy.max_chars:
        return content, False, metadata
    if not policy.truncate:
        metadata["over_limit"] = True
        return content, False, metadata
    if policy.max_chars <= 0:
        metadata["truncated_chars"] = len(content)
        return "", True, metadata

    marker = "\n[truncated]"
    keep_chars = max(policy.max_chars - len(marker), 0)
    truncated_content = content[:keep_chars].rstrip()
    if keep_chars < policy.max_chars:
        truncated_content = f"{truncated_content}{marker}"[: policy.max_chars]
    metadata["truncated_chars"] = len(content) - len(truncated_content)
    return truncated_content, True, metadata
