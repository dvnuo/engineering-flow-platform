"""Workspace bootstrap helpers for Runtime v2."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agents.registry import AgentRegistry
    from .commands import CommandRegistry
    from .compaction.controller import CompactionSummarizer
    from .config_loader import RuntimeConfigLoadResult
    from .event_bus import RuntimeEventBus
    from .instructions import InstructionContextBuilder
    from .llm.adapter import LLMEventAdapter
    from .loop.runner import ProviderCallable
    from .loop.provider import LLMProvider
    from .lsp import LSPClient
    from .permissions import PermissionEvaluator
    from .questions import QuestionBroker
    from .runtime.agent import AgentRuntime
    from .runtime.run_state import RuntimeRunState
    from .session.protocol import SessionStore
    from .skills.context import SkillContextBuilder
    from .skills.discovery import SkillDiscovery
    from .system_prompt import SystemPromptBuilder
    from .tools.registry import ToolRegistry
    from .tools.runtime import ToolRuntime


_UNSET = object()


@dataclass
class RuntimeWorkspace:
    """Loaded Runtime v2 workspace configuration and registries."""

    workspace_root: Path
    load_result: "RuntimeConfigLoadResult"

    @property
    def config(self):
        return self.load_result.config

    @property
    def agent_registry(self):
        return self.load_result.agent_registry

    @property
    def command_registry(self):
        return self.load_result.command_registry


def load_runtime_workspace(
    workspace_root: str | Path,
    *,
    paths: Any = None,
    include_defaults: bool = True,
) -> RuntimeWorkspace:
    """Load workspace config, command registry, and agent registry once."""

    from .config_loader import load_runtime_config

    loaded = load_runtime_config(
        workspace_root,
        paths=paths,
        include_defaults=include_defaults,
    )
    root = Path(loaded.config.workspace_root).expanduser().resolve(strict=False)
    return RuntimeWorkspace(workspace_root=root, load_result=loaded)


def create_agent_runtime_from_workspace(
    *,
    provider: "LLMProvider | ProviderCallable",
    workspace_root: str | Path,
    paths: Any = None,
    include_defaults: bool = True,
    max_iterations: int | None = None,
    max_context_parts: int | None = None,
    max_context_chars: int | None = None,
    context_reserve_chars: int | None = None,
    metadata: Mapping[str, Any] | None = None,
    store: "SessionStore | None" = None,
    tool_registry: "ToolRegistry | None" = None,
    tool_runtime: "ToolRuntime | None" = None,
    permission_evaluator: "PermissionEvaluator | None" = None,
    adapter: "LLMEventAdapter | None" = None,
    skill_discovery: "SkillDiscovery | None" = None,
    skill_context_builder: "SkillContextBuilder | None" = None,
    instruction_context_builder: "InstructionContextBuilder | None" = None,
    system_prompt_builder: "SystemPromptBuilder | None" = None,
    command_registry: "CommandRegistry | None | object" = _UNSET,
    event_bus: "RuntimeEventBus | None" = None,
    run_state: "RuntimeRunState | None" = None,
    compaction_summarizer: "CompactionSummarizer | None" = None,
    question_broker: "QuestionBroker | None" = None,
    lsp_client: "LSPClient | None" = None,
    agent_registry: "AgentRegistry | None | object" = _UNSET,
    default_agent: str | None | object = _UNSET,
) -> "AgentRuntime":
    """Create an AgentRuntime from local workspace config and injected provider."""

    from .runtime.agent import AgentRuntime

    workspace = load_runtime_workspace(
        workspace_root,
        paths=paths,
        include_defaults=include_defaults,
    )
    loaded = workspace.load_result
    resolved_agent_registry = (
        loaded.agent_registry if agent_registry is _UNSET else agent_registry
    )
    resolved_command_registry = (
        loaded.command_registry if command_registry is _UNSET else command_registry
    )
    resolved_default_agent = (
        getattr(resolved_agent_registry, "default_agent", None)
        if default_agent is _UNSET
        else default_agent
    )

    return AgentRuntime(
        provider=provider,
        config=loaded.config,
        max_iterations=max_iterations,
        max_context_parts=max_context_parts,
        max_context_chars=max_context_chars,
        context_reserve_chars=context_reserve_chars,
        metadata=metadata,
        store=store,
        tool_registry=tool_registry,
        tool_runtime=tool_runtime,
        permission_evaluator=permission_evaluator,
        adapter=adapter,
        skill_discovery=skill_discovery,
        skill_context_builder=skill_context_builder,
        instruction_context_builder=instruction_context_builder,
        system_prompt_builder=system_prompt_builder,
        command_registry=resolved_command_registry,
        event_bus=event_bus,
        run_state=run_state,
        compaction_summarizer=compaction_summarizer,
        question_broker=question_broker,
        lsp_client=lsp_client,
        agent_registry=resolved_agent_registry,
        default_agent=resolved_default_agent,
    )


__all__ = [
    "RuntimeWorkspace",
    "create_agent_runtime_from_workspace",
    "load_runtime_workspace",
]
