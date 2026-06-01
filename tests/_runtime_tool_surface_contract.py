from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ContractStatus = Literal["implemented", "conditional", "excluded", "remaining"]


@dataclass(frozen=True)
class SurfaceContractEntry:
    """One EFP runtime tool-surface contract entry."""

    status: ContractStatus
    reason: str
    next_action: str | None = None


@dataclass(frozen=True)
class CapabilityContractEntry:
    """One EFP runtime capability contract entry."""

    status: ContractStatus
    summary: str
    next_action: str | None = None


EXPECTED_DEFAULT_CORE_TOOL_IDS = (
    "apply_patch",
    "bash",
    "edit",
    "glob",
    "grep",
    "invalid",
    "read",
    "skill",
    "task",
    "todowrite",
    "webfetch",
    "write",
)

REMOVED_LEGACY_TOOL_IDS = {
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
}

CONDITIONAL_TOOL_IDS: dict[str, SurfaceContractEntry] = {
    "lsp": SurfaceContractEntry(
        status="conditional",
        reason="Enabled by RuntimeConfig.enable_lsp_tool, include_lsp_tool, or an injected LSP client.",
    ),
    "plan_exit": SurfaceContractEntry(
        status="conditional",
        reason="Enabled for EFP runtime plan mode or include_plan_tool.",
    ),
    "question": SurfaceContractEntry(
        status="conditional",
        reason="Enabled by RuntimeConfig.enable_question_tool or include_question_tool.",
    ),
    "repo_clone": SurfaceContractEntry(
        status="conditional",
        reason="Disabled by default; registered only when repository scout tools are explicitly requested.",
    ),
    "repo_overview": SurfaceContractEntry(
        status="conditional",
        reason="Disabled by default; registered only when repository scout tools are explicitly requested.",
    ),
    "websearch": SurfaceContractEntry(
        status="conditional",
        reason="Registered only when callers inject a provider-neutral websearch runner; default core registry leaves it disabled.",
    ),
}

EXCLUDED_RUNTIME_SURFACES: dict[str, SurfaceContractEntry] = {
    "external_protocol_tools": SurfaceContractEntry(
        status="excluded",
        reason="External protocol tool surfaces are outside the EFP runtime scope.",
    ),
    "mcp": SurfaceContractEntry(
        status="excluded",
        reason="MCP servers and MCP-hosted tools are explicitly outside the EFP runtime scope.",
    ),
}

CAPABILITY_GROUPS: dict[str, CapabilityContractEntry] = {
    "loop": CapabilityContractEntry(
        status="implemented",
        summary="AgentRuntime, RuntimeLoopRunner, terminal tools, resume, and pause statuses are covered by EFP runtime tests.",
    ),
    "permissions": CapabilityContractEntry(
        status="implemented",
        summary="Tool permission metadata, broker decisions, deny/ask/allow flow, and resume after approval are implemented.",
    ),
    "tool lifecycle": CapabilityContractEntry(
        status="implemented",
        summary="Validation, execution, output normalization, truncation, terminal results, and runtime events share one tool path.",
    ),
    "skills": CapabilityContractEntry(
        status="conditional",
        summary="Skill discovery, active skill context, the skill tool, and slash activation are enabled when skills are configured.",
    ),
    "commands": CapabilityContractEntry(
        status="implemented",
        summary="Built-in, configured, file-backed, and skill-backed slash commands expand through the EFP runtime command registry.",
    ),
    "context/compaction": CapabilityContractEntry(
        status="implemented",
        summary="System context, instructions, skill context, budget compaction, automatic session compaction, and pruning are implemented.",
    ),
    "Copilot provider": CapabilityContractEntry(
        status="implemented",
        summary="EFP runtime defaults to the Copilot provider family and uses Copilot model profiles for context budgets.",
    ),
    "session state": CapabilityContractEntry(
        status="implemented",
        summary="In-memory and file-backed sessions, todos, checkpoints, summary diffs, revert/unrevert, retry state, and query helpers are implemented.",
    ),
    "legacy boundary": CapabilityContractEntry(
        status="implemented",
        summary=(
            "EFP runtime is independent from legacy core imports, default tool "
            "surfaces exclude legacy Python tool aliases, and repository-level "
            "deletion of old src/agents, src/runtime, src/sessions, and "
            "src/skills trees remains a separate migration item."
        ),
    ),
}
