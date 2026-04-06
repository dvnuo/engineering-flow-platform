"""Governance compatibility layer.

Phase 2 introduces a structured GovernanceBus while preserving the legacy
GovernanceHooks shape used by existing call sites and tests.
"""

from __future__ import annotations

from typing import Any, Optional
import inspect

from src.runtime.contracts import ExecutionRequest, ExecutionResult, make_execution_result
from src.runtime.governance_bus import GovernanceBus, GovernanceDecision, NoopGovernanceBus


class GovernanceHooks:
    """Legacy no-op governance extension points."""

    def before_execute(self, request: ExecutionRequest) -> None:
        return None

    def after_execute(self, request: ExecutionRequest, result: ExecutionResult) -> None:
        return None

    def on_error(self, request: ExecutionRequest, error: Exception) -> None:
        return None


class LegacyGovernanceHooksAdapter(GovernanceBus):
    """Adapts legacy GovernanceHooks into the GovernanceBus contract."""

    def __init__(self, hooks: GovernanceHooks):
        self._hooks = hooks

    def __getattr__(self, name: str) -> Any:
        return getattr(self._hooks, name)

    async def before_execute(self, request: ExecutionRequest) -> GovernanceDecision:
        await _call_maybe_awaitable(self._hooks.before_execute, request)
        return GovernanceDecision(allowed=True)

    async def after_execute(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        hook_result = await _call_maybe_awaitable(self._hooks.after_execute, request, result)
        if isinstance(hook_result, GovernanceDecision):
            if hook_result.result is not None:
                return _coerce_execution_result_contract(request, hook_result.result)
            if hook_result.allowed:
                return _coerce_execution_result_contract(request, result)
            return make_execution_result(
                request_id=request.request_id,
                status="blocked",
                output_payload={"error": hook_result.reason or "blocked_by_legacy_governance"},
            )
        if isinstance(hook_result, ExecutionResult):
            return _coerce_execution_result_contract(request, hook_result)
        return _coerce_execution_result_contract(request, result)

    async def on_error(self, request: ExecutionRequest, error: Exception) -> None:
        await _call_maybe_awaitable(self._hooks.on_error, request, error)
        return None


async def _call_maybe_awaitable(callable_obj: Any, *args: Any) -> Any:
    value = callable_obj(*args)
    if inspect.isawaitable(value):
        return await value
    return value


def _coerce_execution_result_contract(request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
    output_payload = result.output_payload if isinstance(result.output_payload, dict) else {"value": str(result.output_payload)}
    status = result.status if isinstance(result.status, str) and result.status.strip() else "error"
    artifacts = result.artifacts if isinstance(result.artifacts, dict) else {}
    runtime_events = result.runtime_events if isinstance(result.runtime_events, list) else []
    return make_execution_result(
        request_id=request.request_id,
        status=status,
        output_payload=output_payload,
        artifacts=artifacts,
        runtime_events=runtime_events,
        next_action_hint=result.next_action_hint,
        audit_ref=result.audit_ref,
    )


def as_governance_bus(governance: Optional[Any]) -> GovernanceBus:
    if governance is None:
        return NoopGovernanceBus()
    if isinstance(governance, GovernanceBus):
        return governance
    if isinstance(governance, GovernanceHooks):
        return LegacyGovernanceHooksAdapter(governance)
    # keep compatibility for duck-typed governance implementations
    if all(hasattr(governance, name) for name in ("before_execute", "after_execute", "on_error")):
        return LegacyGovernanceHooksAdapter(governance)  # type: ignore[arg-type]
    return NoopGovernanceBus()
