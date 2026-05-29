"""Runtime v2 parity manifest for the audited opencode dev head.

This module is intentionally data-only. Tests compare it with the live Runtime
v2 tool registry so default and conditional surfaces drift visibly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ParityStatus = Literal["done", "conditional", "excluded", "remaining"]


@dataclass(frozen=True)
class SurfaceEntry:
    """One tool-surface parity entry."""

    status: ParityStatus
    reason: str
    next_action: str | None = None


@dataclass(frozen=True)
class CapabilityEntry:
    """One capability-group parity entry."""

    status: ParityStatus
    summary: str
    next_action: str | None = None


OPENCODE_UPSTREAM_REPO = "https://github.com/anomalyco/opencode"
OPENCODE_DEV_HEAD = "031f82adc89e254bed8bc7a3a88fde5c4066dc8b"

DEFAULT_CORE_TOOL_IDS = (
    "apply_patch",
    "bash",
    "edit",
    "glob",
    "grep",
    "invalid",
    "read",
    "repo_clone",
    "repo_overview",
    "todowrite",
    "webfetch",
    "write",
)

LEGACY_ALIAS_TOOL_IDS = (
    "fetch",
    "list_dir",
    "read_file",
    "shell_exec",
    "shell_kill",
    "shell_status",
    "skill_list",
    "task_cancel",
    "task_status",
    "todo_write",
    "write_file",
)

OPTIONAL_CONDITIONAL_TOOL_IDS: dict[str, SurfaceEntry] = {
    "fetch": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface.",
    ),
    "list_dir": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface.",
    ),
    "lsp": SurfaceEntry(
        status="conditional",
        reason="Enabled by RuntimeConfig.enable_lsp_tool, include_lsp_tool, or an injected LSP client.",
    ),
    "plan_exit": SurfaceEntry(
        status="conditional",
        reason="Enabled for Runtime v2 plan mode or include_plan_tool.",
    ),
    "question": SurfaceEntry(
        status="conditional",
        reason="Enabled by RuntimeConfig.enable_question_tool or include_question_tool.",
    ),
    "read_file": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface.",
    ),
    "shell_exec": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface.",
    ),
    "shell_kill": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface when background shell jobs are enabled.",
    ),
    "shell_status": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface when background shell jobs are enabled.",
    ),
    "skill": SurfaceEntry(
        status="conditional",
        reason="Enabled when skill discovery or skill directories are configured.",
    ),
    "skill_list": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface when skill discovery is configured.",
    ),
    "task": SurfaceEntry(
        status="conditional",
        reason="Enabled when a task runner is injected into the registry.",
    ),
    "task_cancel": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface when background task execution is enabled.",
    ),
    "task_status": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface when background task execution is enabled.",
    ),
    "todo_write": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface.",
    ),
    "websearch": SurfaceEntry(
        status="conditional",
        reason="Registered only when callers inject a provider-neutral websearch runner; default core registry leaves it disabled.",
    ),
    "write_file": SurfaceEntry(
        status="conditional",
        reason="Enabled by the legacy alias surface.",
    ),
}

EXCLUDED_TOOL_IDS: dict[str, SurfaceEntry] = {
    "external_protocol_tools": SurfaceEntry(
        status="excluded",
        reason="External protocol tool surfaces are outside the Runtime v2 parity scope.",
    ),
}

CAPABILITY_GROUPS: dict[str, CapabilityEntry] = {
    "loop": CapabilityEntry(
        status="done",
        summary="AgentRuntime, RuntimeLoopRunner, terminal tools, resume, and pause statuses are covered by Runtime v2 tests.",
    ),
    "permissions": CapabilityEntry(
        status="done",
        summary="Tool permission metadata, broker decisions, deny/ask/allow flow, and resume after approval are implemented.",
    ),
    "tool lifecycle": CapabilityEntry(
        status="done",
        summary="Validation, execution, output normalization, truncation, terminal results, and runtime events share one tool path.",
    ),
    "skills": CapabilityEntry(
        status="conditional",
        summary="Skill discovery, active skill context, the skill tool, and slash activation are enabled when skills are configured.",
    ),
    "commands": CapabilityEntry(
        status="done",
        summary="Built-in, configured, file-backed, and skill-backed slash commands expand through the Runtime v2 command registry.",
    ),
    "context/compaction": CapabilityEntry(
        status="done",
        summary="System context, instructions, skill context, budget compaction, automatic session compaction, and pruning are implemented.",
    ),
    "Copilot provider": CapabilityEntry(
        status="done",
        summary="Runtime v2 defaults to the Copilot provider family and uses Copilot model profiles for context budgets.",
    ),
    "session state": CapabilityEntry(
        status="done",
        summary="In-memory and file-backed sessions, todos, checkpoints, retry state, and query helpers are implemented.",
    ),
}

__all__ = [
    "CAPABILITY_GROUPS",
    "DEFAULT_CORE_TOOL_IDS",
    "EXCLUDED_TOOL_IDS",
    "LEGACY_ALIAS_TOOL_IDS",
    "OPENCODE_DEV_HEAD",
    "OPENCODE_UPSTREAM_REPO",
    "OPTIONAL_CONDITIONAL_TOOL_IDS",
    "CapabilityEntry",
    "ParityStatus",
    "SurfaceEntry",
]
