"""Built-in Runtime v2 agent profiles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .profile import AgentProfile


DEFAULT_AGENT_PROFILE_NAMES = ("general", "build", "plan", "explore", "scout")

_MUTATING_PERMISSION_TOOL_IDS = (
    "edit",
    "write",
    "apply_patch",
    "bash",
    "task",
)

_READ_ONLY_PERMISSION = {
    tool_id: "deny" for tool_id in _MUTATING_PERMISSION_TOOL_IDS
}

_READ_FOCUSED_ASK_PERMISSION = {
    tool_id: "ask" for tool_id in _MUTATING_PERMISSION_TOOL_IDS
}


_DEFAULT_AGENT_PROFILE_DATA: tuple[dict[str, Any], ...] = (
    {
        "name": "general",
        "description": "Balanced default agent for general Runtime v2 work.",
        "prompt": (
            "Handle the task end to end. Clarify assumptions when needed, use "
            "tools deliberately, and keep the final answer focused on the result."
        ),
        "metadata": {"mode": "general", "built_in": True},
    },
    {
        "name": "build",
        "description": "Implementation-focused agent for code changes.",
        "prompt": (
            "Implement the requested change. Inspect the relevant code first, "
            "make targeted edits, and verify with appropriate checks."
        ),
        "metadata": {"mode": "build", "built_in": True},
    },
    {
        "name": "plan",
        "description": "Read-only planning and review agent.",
        "prompt": (
            "Analyze the request and produce a concrete plan or review. Do not "
            "modify files or run mutating commands."
        ),
        "metadata": {
            "mode": "plan",
            "built_in": True,
            "permission": deepcopy(_READ_ONLY_PERMISSION),
        },
    },
    {
        "name": "explore",
        "description": "Codebase exploration agent that asks before mutation.",
        "prompt": (
            "Explore the codebase to answer the question or map the relevant "
            "implementation. Prefer read and search tools, and ask before "
            "actions that may change state."
        ),
        "metadata": {
            "mode": "explore",
            "built_in": True,
            "permission": deepcopy(_READ_FOCUSED_ASK_PERMISSION),
        },
    },
    {
        "name": "scout",
        "description": "Lightweight search and summarization agent.",
        "prompt": (
            "Search narrowly, summarize the relevant facts, and stop once the "
            "answer is supported by inspected context."
        ),
        "max_iterations": 2,
        "metadata": {
            "mode": "scout",
            "built_in": True,
            "permission": deepcopy(_READ_ONLY_PERMISSION),
        },
    },
)


def default_agent_profiles() -> list[AgentProfile]:
    """Return fresh built-in Runtime v2 agent profiles."""

    return [AgentProfile(**deepcopy(data)) for data in _DEFAULT_AGENT_PROFILE_DATA]


__all__ = ["DEFAULT_AGENT_PROFILE_NAMES", "default_agent_profiles"]
