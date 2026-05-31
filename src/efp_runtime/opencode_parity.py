"""EFP runtime parity manifest for the audited opencode dev head.

This module is intentionally data-only. Tests compare it with the live runtime
tool registry so default and conditional surfaces drift visibly.
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
OPENCODE_DEV_HEAD = "16cae9a32329b65116869d2fd3c1fac27c4adcb6"
OPENCODE_DEV_TREE = "754a57c6f0e0413c1541b320a482f85708cffb88"
OPENCODE_AUDITED_AT = "2026-05-29T15:52:46Z"

DEFAULT_CORE_TOOL_IDS = (
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

OPTIONAL_CONDITIONAL_TOOL_IDS: dict[str, SurfaceEntry] = {
    "lsp": SurfaceEntry(
        status="conditional",
        reason="Enabled by RuntimeConfig.enable_lsp_tool, include_lsp_tool, or an injected LSP client.",
    ),
    "plan_exit": SurfaceEntry(
        status="conditional",
        reason="Enabled for EFP runtime plan mode or include_plan_tool.",
    ),
    "question": SurfaceEntry(
        status="conditional",
        reason="Enabled by RuntimeConfig.enable_question_tool or include_question_tool.",
    ),
    "repo_clone": SurfaceEntry(
        status="conditional",
        reason="Disabled by default; registered only when repository scout tools are explicitly requested.",
    ),
    "repo_overview": SurfaceEntry(
        status="conditional",
        reason="Disabled by default; registered only when repository scout tools are explicitly requested.",
    ),
    "websearch": SurfaceEntry(
        status="conditional",
        reason="Registered only when callers inject a provider-neutral websearch runner; default core registry leaves it disabled.",
    ),
}

EXCLUDED_TOOL_IDS: dict[str, SurfaceEntry] = {
    "external_protocol_tools": SurfaceEntry(
        status="excluded",
        reason="External protocol tool surfaces are outside the EFP runtime parity scope.",
    ),
    "mcp": SurfaceEntry(
        status="excluded",
        reason="MCP servers and MCP-hosted tools are explicitly outside this replacement runtime scope.",
    ),
}

CAPABILITY_GROUPS: dict[str, CapabilityEntry] = {
    "loop": CapabilityEntry(
        status="done",
        summary="AgentRuntime, RuntimeLoopRunner, terminal tools, resume, and pause statuses are covered by EFP runtime tests.",
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
        summary="Built-in, configured, file-backed, and skill-backed slash commands expand through the EFP runtime command registry.",
    ),
    "context/compaction": CapabilityEntry(
        status="done",
        summary="System context, instructions, skill context, budget compaction, automatic session compaction, and pruning are implemented.",
    ),
    "Copilot provider": CapabilityEntry(
        status="done",
        summary="EFP runtime defaults to the Copilot provider family and uses Copilot model profiles for context budgets.",
    ),
    "session state": CapabilityEntry(
        status="done",
        summary="In-memory and file-backed sessions, todos, checkpoints, summary diffs, revert/unrevert, retry state, and query helpers are implemented.",
    ),
    "legacy boundary": CapabilityEntry(
        status="done",
        summary=(
            "EFP runtime is independent from legacy core imports, default tool "
            "surfaces exclude legacy Python tool aliases, and repository-level "
            "deletion of old src/agents, src/runtime, src/sessions, and "
            "src/skills trees remains a separate migration item."
        ),
    ),
}

__all__ = [
    "CAPABILITY_GROUPS",
    "DEFAULT_CORE_TOOL_IDS",
    "EXCLUDED_TOOL_IDS",
    "OPENCODE_AUDITED_AT",
    "OPENCODE_DEV_HEAD",
    "OPENCODE_DEV_TREE",
    "OPENCODE_UPSTREAM_REPO",
    "OPTIONAL_CONDITIONAL_TOOL_IDS",
    "CapabilityEntry",
    "ParityStatus",
    "SurfaceEntry",
]
