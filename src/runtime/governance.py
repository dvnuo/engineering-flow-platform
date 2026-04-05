"""Governance compatibility layer.

Phase 2 introduces a structured GovernanceBus while preserving the legacy
GovernanceHooks shape used by existing call sites and tests.
"""

from __future__ import annotations

from typing import Any, Optional
import inspect

from src.runtime.contracts import ExecutionRequest, ExecutionResult
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
        await _call_maybe_awaitable(self._hooks.after_execute, request, result)
        return result

    async def on_error(self, request: ExecutionRequest, error: Exception) -> None:
        await _call_maybe_awaitable(self._hooks.on_error, request, error)
        return None


async def _call_maybe_awaitable(callable_obj: Any, *args: Any) -> Any:
    value = callable_obj(*args)
    if inspect.isawaitable(value):
        return await value
    return value


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
