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
    doom_loop_threshold: int | None = 3
    max_context_parts: int | None = None
    max_context_chars: int | None = None
    context_reserve_chars: int = 0
    enable_compaction_summarizer: bool = False
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] = field(default_factory=list)
    runtime_mode: str = "build"
    enable_plan_tool: bool | None = None
    plan_mode_read_only: bool = True
    enable_question_tool: bool = False
    enable_lsp_tool: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    include_default_system_prompt: bool = True
    system_prompt_texts: list[str] = field(default_factory=list)
    system_prompt_paths: list[str | Path] = field(default_factory=list)
    max_system_prompt_chars: int = 20000
    include_runtime_reminders: bool = True
    instruction_paths: list[str | Path] = field(default_factory=list)
    instruction_texts: list[str] = field(default_factory=list)
    include_default_instructions: bool = True
    attach_read_instructions: bool = True
    max_instruction_chars: int = 20000
    skill_directories: list[str | Path] = field(default_factory=list)
    active_skills: list[str] = field(default_factory=list)
    include_skill_sidecar_content: bool = False
    max_skill_sidecar_chars: int = 4000
    resolve_prompt_references: bool = True
    max_prompt_reference_chars: int = 20000
    max_prompt_directory_entries: int = 200
    tool_output_max_lines: int | None = 2000
    tool_output_max_bytes: int | None = 50 * 1024
    tool_output_truncation_direction: str = "head"
    archive_truncated_tool_outputs: bool = True
    tool_output_dir: str | Path | None = None

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.doom_loop_threshold is not None and self.doom_loop_threshold < 2:
            raise ValueError("doom_loop_threshold must be at least 2 or None")
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
        if self.max_system_prompt_chars < 0:
            raise ValueError("max_system_prompt_chars must be greater than or equal to 0")
        if self.max_instruction_chars < 0:
            raise ValueError("max_instruction_chars must be greater than or equal to 0")
        if self.tool_output_max_lines is not None and self.tool_output_max_lines < 0:
            raise ValueError("tool_output_max_lines must be greater than or equal to 0 or None")
        if self.tool_output_max_bytes is not None and self.tool_output_max_bytes < 0:
            raise ValueError("tool_output_max_bytes must be greater than or equal to 0 or None")
        if self.tool_output_truncation_direction not in ("head", "tail"):
            raise ValueError("tool_output_truncation_direction must be 'head' or 'tail'")
        if self.runtime_mode not in ("build", "plan"):
            raise ValueError("runtime_mode must be 'build' or 'plan'")
        self.enabled_tools = (
            None if self.enabled_tools is None else list(self.enabled_tools)
        )
        self.enable_compaction_summarizer = bool(self.enable_compaction_summarizer)
        self.enable_plan_tool = (
            None if self.enable_plan_tool is None else bool(self.enable_plan_tool)
        )
        self.plan_mode_read_only = bool(self.plan_mode_read_only)
        self.enable_question_tool = bool(self.enable_question_tool)
        self.enable_lsp_tool = bool(self.enable_lsp_tool)
        self.disabled_tools = list(self.disabled_tools)
        self.metadata = dict(self.metadata)
        self.include_default_system_prompt = bool(self.include_default_system_prompt)
        self.system_prompt_texts = list(self.system_prompt_texts)
        self.system_prompt_paths = list(self.system_prompt_paths)
        self.include_runtime_reminders = bool(self.include_runtime_reminders)
        self.instruction_paths = list(self.instruction_paths)
        self.instruction_texts = list(self.instruction_texts)
        self.include_default_instructions = bool(self.include_default_instructions)
        self.attach_read_instructions = bool(self.attach_read_instructions)
        self.skill_directories = list(self.skill_directories)
        self.active_skills = list(self.active_skills)
        self.archive_truncated_tool_outputs = bool(self.archive_truncated_tool_outputs)


__all__ = ["RuntimeConfig"]
