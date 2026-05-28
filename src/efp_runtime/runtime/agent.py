"""High-level Runtime v2 facade."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import json
from pathlib import Path
from typing import Any, Union

from ..event_bus import RuntimeEventBus
from ..llm.adapter import LLMEventAdapter
from ..loop.provider import LLMProvider
from ..loop.runner import LoopStatus, ProviderCallable, RuntimeLoopResult, RuntimeLoopRunner
from ..permissions import PermissionEvaluator
from ..prompt import resolve_prompt_references
from ..session.protocol import SessionStore
from ..session.store import InMemorySessionStore
from ..skills.commands import SkillCommandResult, parse_skill_commands
from ..skills.context import SkillContextBuilder
from ..skills.discovery import SkillDiscovery
from ..tools.builtin import create_core_tool_registry
from ..tools.registry import ToolRegistry
from ..tools.runtime import ToolRuntime
from .config import RuntimeConfig
from .run_state import RuntimeRunState


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
        store: SessionStore | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_runtime: ToolRuntime | None = None,
        permission_evaluator: PermissionEvaluator | None = None,
        adapter: LLMEventAdapter | None = None,
        skill_discovery: SkillDiscovery | None = None,
        skill_context_builder: SkillContextBuilder | None = None,
        event_bus: RuntimeEventBus | None = None,
        run_state: RuntimeRunState | None = None,
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
        self.skill_context_builder = _resolve_skill_context_builder(
            config=self.config,
            skill_discovery=skill_discovery,
            skill_context_builder=skill_context_builder,
        )
        self.active_skills = _unique_skill_names(self.config.active_skills)
        self.event_bus = event_bus or RuntimeEventBus()
        self.run_state = run_state or RuntimeRunState()

    async def run(
        self,
        user_text: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeLoopResult:
        skill_command = parse_skill_commands(user_text)
        active_skills = _apply_skill_command(self.active_skills, skill_command)
        resolved_session_id = session_id or self.store.create_session().session_id
        run_id = self.run_state.begin(resolved_session_id)
        try:
            context_messages = self._build_skill_context_messages(active_skills)
            self.active_skills = active_skills

            run_metadata = dict(self.config.metadata)
            run_metadata.update(metadata or {})
            run_metadata["run_id"] = run_id
            run_metadata["active_skills"] = list(active_skills)
            run_metadata["skill_command"] = {
                "add": list(skill_command.add),
                "clear": skill_command.clear,
                "cleaned_text": skill_command.cleaned_text,
            }
            user_parts = self._resolve_user_parts(skill_command.cleaned_text)
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=self.tool_runtime,
                max_iterations=self.config.max_iterations,
                max_context_parts=self.config.max_context_parts,
                event_bus=self.event_bus,
                is_cancelled=lambda: self.run_state.is_cancelled(resolved_session_id),
            )
            result = await runner.run(
                user_text=skill_command.cleaned_text,
                user_parts=user_parts,
                session_id=resolved_session_id,
                metadata=run_metadata,
                context_messages=context_messages,
            )
        except asyncio.CancelledError:
            self.run_state.finish(resolved_session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self.run_state.finish(resolved_session_id, LoopStatus.ERROR)
            raise

        self.run_state.finish(resolved_session_id, result.status)
        return result

    async def resume(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeLoopResult:
        self.store.get_session(session_id)
        run_id = self.run_state.begin(session_id)
        try:
            context_messages = self._build_skill_context_messages(self.active_skills)
            run_metadata = dict(self.config.metadata)
            run_metadata.update(metadata or {})
            run_metadata["run_id"] = run_id
            run_metadata["active_skills"] = list(self.active_skills)
            run_metadata["resume"] = True
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=self.tool_runtime,
                max_iterations=self.config.max_iterations,
                max_context_parts=self.config.max_context_parts,
                event_bus=self.event_bus,
                is_cancelled=lambda: self.run_state.is_cancelled(session_id),
            )
            result = await runner.run(
                user_text="",
                session_id=session_id,
                metadata=run_metadata,
                context_messages=context_messages,
                append_user_message=False,
            )
        except asyncio.CancelledError:
            self.run_state.finish(session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self.run_state.finish(session_id, LoopStatus.ERROR)
            raise

        self.run_state.finish(session_id, result.status)
        return result

    def approve_permission(self, request_id: str, *, always: bool = False):
        approve = getattr(self.tool_runtime.permission_evaluator, "approve", None)
        if not callable(approve):
            raise TypeError("permission evaluator does not support approve")
        return approve(request_id, always=always)

    def deny_permission(
        self,
        request_id: str,
        *,
        always: bool = False,
        reason: str | None = None,
    ):
        deny = getattr(self.tool_runtime.permission_evaluator, "deny", None)
        if not callable(deny):
            raise TypeError("permission evaluator does not support deny")
        return deny(request_id, always=always, reason=reason)

    def pending_permissions(self) -> list[dict[str, Any]]:
        pending = getattr(self.tool_runtime.permission_evaluator, "pending", None)
        if not callable(pending):
            raise TypeError("permission evaluator does not support pending")
        return [_permission_request_to_dict(request) for request in pending()]

    def pending_permission_requests(self) -> list[dict[str, Any]]:
        return self.pending_permissions()

    def cancel(self, session_id: str) -> bool:
        return self.run_state.cancel(session_id)

    def _build_skill_context_messages(self, active_skills: list[str]):
        if not active_skills:
            return []
        if self.skill_context_builder is None:
            raise ValueError(
                "Active skills require skill_context_builder, skill_discovery, "
                "or config.skill_directories"
            )
        return self.skill_context_builder.build_messages(active_skills)

    def _resolve_user_parts(self, user_text: str):
        if (
            not self.config.resolve_prompt_references
            or self.config.workspace_root is None
            or not user_text
        ):
            return None
        resolved_prompt = resolve_prompt_references(
            user_text,
            workspace_root=self.config.workspace_root,
            max_file_chars=self.config.max_prompt_reference_chars,
            max_directory_entries=self.config.max_prompt_directory_entries,
        )
        return resolved_prompt.parts


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
        skill_directories=list(config.skill_directories),
        active_skills=list(config.active_skills),
        include_skill_sidecar_content=config.include_skill_sidecar_content,
        max_skill_sidecar_chars=config.max_skill_sidecar_chars,
        resolve_prompt_references=config.resolve_prompt_references,
        max_prompt_reference_chars=config.max_prompt_reference_chars,
        max_prompt_directory_entries=config.max_prompt_directory_entries,
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


def _resolve_skill_context_builder(
    *,
    config: RuntimeConfig,
    skill_discovery: SkillDiscovery | None,
    skill_context_builder: SkillContextBuilder | None,
) -> SkillContextBuilder | None:
    if skill_context_builder is not None:
        return skill_context_builder

    discovery = skill_discovery
    if discovery is None and config.skill_directories:
        discovery = SkillDiscovery(config.skill_directories)
    if discovery is None:
        return None

    return SkillContextBuilder(
        discovery,
        include_sidecar_content=config.include_skill_sidecar_content,
        max_sidecar_chars=config.max_skill_sidecar_chars,
    )


def _apply_skill_command(
    active_skills: list[str],
    command: SkillCommandResult,
) -> list[str]:
    updated = [] if command.clear else list(active_skills)
    for name in command.add:
        normalized = str(name).strip()
        if normalized and normalized not in updated:
            updated.append(normalized)
    return updated


def _unique_skill_names(names: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for name in names:
        normalized = str(name).strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _permission_request_to_dict(request: Any) -> dict[str, Any]:
    if hasattr(request, "to_dict"):
        payload = request.to_dict()
    elif isinstance(request, Mapping):
        payload = dict(request)
    else:
        raise TypeError("pending permission request must be mapping-like")
    encoded = json.dumps(payload, sort_keys=True, default=str)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("pending permission request must encode to a JSON object")
    return decoded


__all__ = ["AgentRuntime"]
