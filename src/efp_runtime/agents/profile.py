"""Agent profile contracts for EFP runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentProfile:
    """Configuration profile used when selecting a EFP runtime subagent."""

    name: str
    description: str = ""
    prompt: str = ""
    tools: dict[str, bool] | None = None
    active_skills: list[str] = field(default_factory=list)
    max_iterations: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = str(self.name).strip()
        if not self.name:
            raise ValueError("agent profile name is required")
        if self.max_iterations is not None and self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

        self.description = str(self.description or "")
        self.prompt = str(self.prompt or "")
        self.tools = _copy_tools(self.tools)
        self.active_skills = _copy_skill_names(self.active_skills)
        self.metadata = dict(self.metadata)


def _copy_tools(tools: dict[str, bool] | None) -> dict[str, bool] | None:
    if tools is None:
        return None

    copied: dict[str, bool] = {}
    for tool_id, enabled in tools.items():
        if not isinstance(enabled, bool):
            raise TypeError("profile tools must map tool ids to bool values")
        copied[str(tool_id)] = enabled
    return copied


def _copy_skill_names(names: list[str]) -> list[str]:
    copied: list[str] = []
    for name in names:
        normalized = str(name).strip()
        if normalized and normalized not in copied:
            copied.append(normalized)
    return copied


__all__ = ["AgentProfile"]
