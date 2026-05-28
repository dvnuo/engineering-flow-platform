"""Configuration for the Runtime v2 high-level facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RuntimeConfig:
    """Static settings for an AgentRuntime instance."""

    workspace_root: str | Path | None = None
    max_iterations: int = 4
    max_context_parts: int | None = None
    max_context_chars: int | None = None
    context_reserve_chars: int = 0
    enable_compaction_summarizer: bool = False
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    instruction_paths: list[str | Path] = field(default_factory=list)
    instruction_texts: list[str] = field(default_factory=list)
    include_default_instructions: bool = True
    max_instruction_chars: int = 20000
    skill_directories: list[str | Path] = field(default_factory=list)
    active_skills: list[str] = field(default_factory=list)
    include_skill_sidecar_content: bool = False
    max_skill_sidecar_chars: int = 4000
    resolve_prompt_references: bool = True
    max_prompt_reference_chars: int = 20000
    max_prompt_directory_entries: int = 200

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.max_context_parts is not None and self.max_context_parts < 1:
            raise ValueError("max_context_parts must be at least 1")
        if self.max_context_chars is not None and self.max_context_chars < 1:
            raise ValueError("max_context_chars must be at least 1")
        if self.context_reserve_chars < 0:
            raise ValueError("context_reserve_chars must be at least 0")
        if self.max_prompt_reference_chars < 0:
            raise ValueError("max_prompt_reference_chars must be greater than or equal to 0")
        if self.max_prompt_directory_entries < 0:
            raise ValueError("max_prompt_directory_entries must be greater than or equal to 0")
        if self.max_instruction_chars < 0:
            raise ValueError("max_instruction_chars must be greater than or equal to 0")
        self.enabled_tools = (
            None if self.enabled_tools is None else list(self.enabled_tools)
        )
        self.enable_compaction_summarizer = bool(self.enable_compaction_summarizer)
        self.disabled_tools = list(self.disabled_tools)
        self.metadata = dict(self.metadata)
        self.instruction_paths = list(self.instruction_paths)
        self.instruction_texts = list(self.instruction_texts)
        self.include_default_instructions = bool(self.include_default_instructions)
        self.skill_directories = list(self.skill_directories)
        self.active_skills = list(self.active_skills)


__all__ = ["RuntimeConfig"]
