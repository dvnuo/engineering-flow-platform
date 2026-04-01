"""Helpers for skill runtime behavior inside the unified tool loop."""

from __future__ import annotations

import logging
import os
import inspect
from importlib import import_module
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src import ToolResult
from src.agents.tasks import TaskManager, task_manager
from src.skills.runtime import (
    EffectivePromptAssembly,
    ReferenceAttachment,
    SkillRuntimeConfig,
    assemble_effective_prompt,
    attach_skill_references,
)

logger = logging.getLogger(__name__)


HookEventCallback = Optional[Callable[[str, Dict[str, Any]], None]]
HookContext = Dict[str, Any]
def _approved_hook_prefixes() -> Tuple[str, ...]:
    prefixes = ["src.hooks."]
    if os.getenv("SKILL_RUNTIME_ENABLE_TEST_HOOKS") == "1":
        prefixes.append("tests.")
    return tuple(prefixes)
SUPPORTED_HOOK_EFFECT_KEYS = {"modified_args", "short_circuit_result", "result_override"}


@dataclass
class HookEffects:
    modified_args: Dict[str, Any] = field(default_factory=dict)
    short_circuit_result: Optional[ToolResult] = None
    result_override: Optional[ToolResult] = None
    invoked_hooks: List[str] = field(default_factory=list)


def resolve_prompt_execution_boundary(assembly: EffectivePromptAssembly) -> Tuple[str, str]:
    """Single prompt serialization boundary for LLM requests.

    Returns:
        (final_system_prompt, boundary_mode)
    """
    # Current LLM wrapper does not expose a separate developer channel,
    # so developer instructions are serialized into system prompt exactly once.
    return assembly.serialized_system_prompt, "merged_once_into_system"


def get_effective_skill_runtime_prompt(
    *,
    base_system_prompt: str,
    runtime_config: Optional[SkillRuntimeConfig],
) -> EffectivePromptAssembly:
    """Build layered prompt assembly for the current request."""
    return assemble_effective_prompt(base_system_prompt=base_system_prompt, runtime_config=runtime_config)


def get_skill_reference_attachment(runtime_config: Optional[SkillRuntimeConfig]) -> ReferenceAttachment:
    return attach_skill_references(runtime_config)


def build_skill_runtime_event_payload(
    *,
    runtime_config: SkillRuntimeConfig,
    reference_attachment: ReferenceAttachment,
    prompt_assembly: EffectivePromptAssembly,
    prompt_boundary_mode: str,
    verbose: bool = False,
) -> Dict[str, Any]:
    payload = {
        "skill": runtime_config.skill_name,
        "allowed_tools": runtime_config.allowed_tools,
        "task_tools": runtime_config.task_tools,
        "hooks": runtime_config.hooks,
        "references": [os.path.basename(ref) for ref in reference_attachment.references],
        "reference_context": (
            f"{len(reference_attachment.references)} reference(s) available"
            if reference_attachment.references
            else "References: none"
        ),
        "reference_count": len(reference_attachment.references),
        "attachment_mode": reference_attachment.attachment_mode,
        "prompt_boundary_mode": prompt_boundary_mode,
    }
    if verbose:
        payload["prompt_layers"] = {
            "system_rules_text": prompt_assembly.system_rules_text,
            "developer_instructions_text": prompt_assembly.developer_instructions_text,
            "reference_context_text": prompt_assembly.reference_context_text,
        }
    return payload


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


def _resolve_hook_callable(hook_name: str):
    normalized = (hook_name or "").strip()
    if ":" not in normalized:
        return None
    _, target = normalized.split(":", 1)
    if "." not in target:
        return None
    approved_prefixes = _approved_hook_prefixes()
    if not any(target.startswith(prefix) for prefix in approved_prefixes):
        logger.warning("[SkillHook] Rejected unapproved hook target: %s (allowed=%s)", target, approved_prefixes)
        return None
    module_name, func_name = target.rsplit(".", 1)
    module = import_module(module_name)
    return getattr(module, func_name)


def _coerce_tool_result(value: Any) -> Optional[ToolResult]:
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, dict):
        if "success" in value or "content" in value or "error" in value:
            return ToolResult(
                success=bool(value.get("success", True)),
                content=str(value.get("content", "")),
                error=value.get("error"),
            )
    if isinstance(value, str):
        return ToolResult(success=True, content=value)
    return None


def dispatch_skill_hook(*, hook_name: str, context: HookContext) -> Dict[str, Any]:
    """Execute a hook target when available and return runtime metadata."""
    try:
        hook_fn = _resolve_hook_callable(hook_name)
        if not hook_fn:
            return {"applied": True, "mode": "event_only", "hook_effects": {}}
        if inspect.iscoroutinefunction(hook_fn):
            logger.warning("[SkillHook] Rejected unsupported async hook callable: %s", hook_name)
            return {
                "applied": False,
                "mode": "unsupported_async_hook",
                "error": "async_hook_not_supported",
                "hook_effects": {},
            }

        hook_result = hook_fn(context)
        if inspect.isawaitable(hook_result):
            logger.warning("[SkillHook] Rejected unsupported async hook result: %s", hook_name)
            return {
                "applied": False,
                "mode": "unsupported_async_hook",
                "error": "async_hook_result_not_supported",
                "hook_effects": {},
            }
    except Exception as exc:
        logger.warning("[SkillHook] Dispatch failed for %s: %s", hook_name, exc)
        return {"applied": False, "mode": "error", "error": str(exc), "hook_effects": {}}

    hook_effects = {}
    if isinstance(hook_result, dict):
        hook_effects = {k: v for k, v in hook_result.items() if k in SUPPORTED_HOOK_EFFECT_KEYS}
    return {
        "applied": True,
        "mode": "callable",
        "hook_result": hook_result if hook_result is not None else "",
        "hook_effects": hook_effects,
    }


def apply_skill_hooks(
    *,
    runtime_config: Optional[SkillRuntimeConfig],
    stage: str,
    session_id: str,
    tool_name: str,
    payload: Optional[Dict[str, Any]] = None,
    event_callback: HookEventCallback = None,
) -> HookEffects:
    """Apply configured runtime hooks in a safe/no-op manner.

    Hook strings can be `pre_tool`, `post_tool`, or namespaced (`pre_tool:trace`).
    """
    if not runtime_config or not runtime_config.hooks:
        return HookEffects()

    effects = HookEffects()
    for hook in runtime_config.hooks:
        if not _hook_applies_to_stage(hook, stage):
            continue
        try:
            hook_context: HookContext = {
                "session_id": session_id,
                "skill_name": runtime_config.skill_name,
                "tool_name": tool_name,
                "stage": stage,
                "payload": payload or {},
            }
            dispatch_result = dispatch_skill_hook(hook_name=hook, context=hook_context)

            hook_effects = dispatch_result.get("hook_effects", {}) if isinstance(dispatch_result, dict) else {}
            if stage == "pre_tool" and isinstance(hook_effects, dict):
                if isinstance(hook_effects.get("modified_args"), dict):
                    effects.modified_args.update(hook_effects["modified_args"])
                if hook_effects.get("short_circuit_result") is not None:
                    effects.short_circuit_result = _coerce_tool_result(hook_effects.get("short_circuit_result"))
            if stage == "post_tool" and isinstance(hook_effects, dict):
                if hook_effects.get("result_override") is not None:
                    effects.result_override = _coerce_tool_result(hook_effects.get("result_override"))

            if event_callback:
                event_callback(
                    "skill_hook",
                    {
                        "status": "applied" if dispatch_result.get("applied", False) else "failed",
                        "hook": hook,
                        "stage": stage,
                        "tool": tool_name,
                        "skill": runtime_config.skill_name,
                        "payload": payload or {},
                        "dispatch": dispatch_result,
                    },
                )
            if dispatch_result.get("applied", False):
                effects.invoked_hooks.append(hook)
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
    return effects


async def run_skill_tool_with_task_boundary(
    *,
    runtime_config: Optional[SkillRuntimeConfig],
    session_id: str,
    tool_name: str,
    execute_direct: Callable[[], Awaitable[Any]],
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
