"""Lightweight governance hooks for runtime execution boundaries."""

from __future__ import annotations

from typing import Any, Dict

from src.runtime.contracts import ExecutionRequest, ExecutionResult


class GovernanceHooks:
    """Minimal no-op governance layer.

    Phase 1 scope:
    - Safe-by-default and dependency-free.
    - Hook points are additive and non-blocking by design.
    - Supports in-place metadata/result enrichment for future policy expansion.
    """

    def before_execute(self, request: ExecutionRequest) -> Dict[str, Any]:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        metadata.setdefault("governance", {})
        metadata["governance"].setdefault("before_execute_called", True)
        request.metadata = metadata
        return {"governance": {"before_execute_called": True}}

    def after_execute(self, request: ExecutionRequest, result: ExecutionResult) -> Dict[str, Any]:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        metadata.setdefault("governance", {})
        metadata["governance"].setdefault("after_execute_called", True)
        request.metadata = metadata
        return {"governance": {"after_execute_called": True}}

    def on_error(self, request: ExecutionRequest, error: Exception) -> Dict[str, Any]:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        metadata.setdefault("governance", {})
        metadata["governance"]["last_error_type"] = error.__class__.__name__
        request.metadata = metadata
        return {"governance": {"error_type": error.__class__.__name__}}
