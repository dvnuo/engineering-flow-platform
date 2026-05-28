"""High-level Runtime v2 facade."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Union

from ..llm.adapter import LLMEventAdapter
from ..loop.provider import LLMProvider
from ..loop.runner import ProviderCallable, RuntimeLoopResult, RuntimeLoopRunner
from ..permissions import PermissionEvaluator
from ..session.store import InMemorySessionStore
from ..tools.builtin import create_core_tool_registry
from ..tools.registry import ToolRegistry
from ..tools.runtime import ToolRuntime
from .config import RuntimeConfig


class AgentRuntime:
    """Convenience facade that wires store, tools, renderer, and loop runner."""

    def __init__(
        self,
        *,
        provider: Union[LLMProvider, ProviderCallable],
        config: RuntimeConfig | None = None,
        workspace_root: str | Path | None = None,
        max_iterations: int | None = None,
        max_context_parts: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        store: InMemorySessionStore | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_runtime: ToolRuntime | None = None,
        permission_evaluator: PermissionEvaluator | None = None,
        adapter: LLMEventAdapter | None = None,
    ) -> None:
        self.config = _resolve_config(
            config,
            workspace_root=workspace_root,
            max_iterations=max_iterations,
            max_context_parts=max_context_parts,
            metadata=metadata,
        )
        self.provider = provider
        self.adapter = adapter
        self.store = store or InMemorySessionStore()
        self.tool_runtime = _resolve_tool_runtime(
            workspace_root=self.config.workspace_root,
            tool_registry=tool_registry,
            tool_runtime=tool_runtime,
            permission_evaluator=permission_evaluator,
        )

    async def run(
        self,
        user_text: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeLoopResult:
        run_metadata = dict(self.config.metadata)
        run_metadata.update(metadata or {})
        runner = RuntimeLoopRunner(
            store=self.store,
            provider=self.provider,
            adapter=self.adapter,
            tool_runtime=self.tool_runtime,
            max_iterations=self.config.max_iterations,
            max_context_parts=self.config.max_context_parts,
        )
        return await runner.run(
            user_text=user_text,
            session_id=session_id,
            metadata=run_metadata,
        )


def _resolve_config(
    config: RuntimeConfig | None,
    *,
    workspace_root: str | Path | None,
    max_iterations: int | None,
    max_context_parts: int | None,
    metadata: Mapping[str, Any] | None,
) -> RuntimeConfig:
    if config is None:
        return RuntimeConfig(
            workspace_root=workspace_root,
            max_iterations=max_iterations if max_iterations is not None else 4,
            max_context_parts=max_context_parts,
            metadata=dict(metadata or {}),
        )

    resolved_metadata = dict(config.metadata)
    resolved_metadata.update(metadata or {})
    return RuntimeConfig(
        workspace_root=workspace_root if workspace_root is not None else config.workspace_root,
        max_iterations=max_iterations if max_iterations is not None else config.max_iterations,
        max_context_parts=(
            max_context_parts if max_context_parts is not None else config.max_context_parts
        ),
        metadata=resolved_metadata,
    )


def _resolve_tool_runtime(
    *,
    workspace_root: str | Path | None,
    tool_registry: ToolRegistry | None,
    tool_runtime: ToolRuntime | None,
    permission_evaluator: PermissionEvaluator | None,
) -> ToolRuntime:
    if tool_runtime is not None:
        if tool_registry is not None and tool_registry is not tool_runtime.registry:
            raise ValueError(
                "tool_registry must match tool_runtime.registry when both are provided"
            )
        return tool_runtime

    registry = tool_registry
    if registry is None:
        registry = (
            create_core_tool_registry(workspace_root)
            if workspace_root is not None
            else ToolRegistry()
        )
    return ToolRuntime(registry, permission_evaluator=permission_evaluator)


__all__ = ["AgentRuntime"]
