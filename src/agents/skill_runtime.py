"""Helpers for skill runtime behavior inside the unified tool loop."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src import ToolResult
from src.agents.tasks import TaskManager, task_manager
from src.skills.runtime import EffectivePromptAssembly, SkillRuntimeConfig, assemble_effective_prompt

logger = logging.getLogger(__name__)


HookEventCallback = Optional[Callable[[str, Dict[str, Any]], None]]


def get_effective_skill_runtime_prompt(
    *,
    base_system_prompt: str,
    runtime_config: Optional[SkillRuntimeConfig],
) -> EffectivePromptAssembly:
    """Build layered prompt assembly for the current request."""
    return assemble_effective_prompt(base_system_prompt=base_system_prompt, runtime_config=runtime_config)


def build_skill_tool_denied_result(runtime_config: SkillRuntimeConfig, tool_name: str, reason: str = "not_allowed") -> ToolResult:
    return ToolResult(
        success=False,
        content=(
            f"Tool '{tool_name}' is denied by active skill policy for skill "
            f"'{runtime_config.skill_name}' ({reason}). Allowed tools: {runtime_config.allowed_tools}"
        ),
    )


def _hook_applies_to_stage(hook_name: str, stage: str) -> bool:
    normalized = (hook_name or "").strip().lower()
    if not normalized:
        return False
    return normalized == stage or normalized.startswith(f"{stage}:")


def apply_skill_hooks(
    *,
    runtime_config: Optional[SkillRuntimeConfig],
    stage: str,
    tool_name: str,
    payload: Optional[Dict[str, Any]] = None,
    event_callback: HookEventCallback = None,
) -> List[str]:
    """Apply configured runtime hooks in a safe/no-op manner.

    Hook strings can be `pre_tool`, `post_tool`, or namespaced (`pre_tool:trace`).
    """
    if not runtime_config or not runtime_config.hooks:
        return []

    invoked: List[str] = []
    for hook in runtime_config.hooks:
        if not _hook_applies_to_stage(hook, stage):
            continue
        try:
            if event_callback:
                event_callback(
                    "skill_hook",
                    {
                        "status": "applied",
                        "hook": hook,
                        "stage": stage,
                        "tool": tool_name,
                        "skill": runtime_config.skill_name,
                        "payload": payload or {},
                    },
                )
            invoked.append(hook)
        except Exception as exc:  # defensive: hooks must not break requests
            logger.warning("[SkillHook] Failed hook=%s stage=%s tool=%s err=%s", hook, stage, tool_name, exc)
            if event_callback:
                event_callback(
                    "skill_hook",
                    {
                        "status": "failed",
                        "hook": hook,
                        "stage": stage,
                        "tool": tool_name,
                        "skill": runtime_config.skill_name,
                        "error": str(exc),
                    },
                )
    return invoked


async def run_skill_tool_with_task_boundary(
    *,
    runtime_config: Optional[SkillRuntimeConfig],
    session_id: str,
    tool_name: str,
    execute_direct: Callable[[], Any],
    event_callback: HookEventCallback = None,
    tasks: TaskManager = task_manager,
):
    """Execute tool directly or through task boundary when task-capable."""
    if runtime_config and tool_name in set(runtime_config.task_tools):
        return await tasks.run_tool_task(
            session_id=session_id,
            tool_name=tool_name,
            coro_factory=execute_direct,
            event_callback=event_callback,
        )
    return await execute_direct()
