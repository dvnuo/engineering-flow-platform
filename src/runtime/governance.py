"""Lightweight governance hooks for runtime execution boundaries."""

from __future__ import annotations

from src.runtime.contracts import ExecutionRequest, ExecutionResult


class GovernanceHooks:
    """Minimal no-op governance extension points.

    Phase 1 scope:
    - Safe-by-default and dependency-free.
    - Hook points are additive and non-blocking by design.
    - Default hooks intentionally perform no mutation.
    - Future policy/enrichment behavior can be layered on top later.
    """

    def before_execute(self, request: ExecutionRequest) -> None:
        """Called before handler resolution. Default implementation is no-op."""
        return None

    def after_execute(self, request: ExecutionRequest, result: ExecutionResult) -> None:
        """Called after result normalization. Default implementation is no-op."""
        return None

    def on_error(self, request: ExecutionRequest, error: Exception) -> None:
        """Called when handler execution raises. Default implementation is no-op."""
        return None
