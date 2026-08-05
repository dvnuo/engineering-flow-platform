"""Configuration for the EFP runtime high-level facade."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..permissions import normalize_tool_permissions
from ..usage import validate_usage_pricing


@dataclass
class RuntimeConfig:
    """Static settings for an AgentRuntime instance."""

    workspace_root: str | Path | None = None
    max_iterations: int | None = None
    doom_loop_threshold: int | None = 3
    default_provider_id: str = "github-copilot"
    default_model: str = "gpt-5.4"
    max_context_parts: int | None = None
    max_context_chars: int | None = None
    max_context_tokens: int | None = None
    context_reserve_chars: int = 0
    context_reserve_tokens: int | None = None
    compaction_auto: bool = True
    # Off by default, and deliberately NOT implied by the context-budget size
    # knobs above. A context budget sizes the in-memory provider request; the
    # render-time compactor (context/render.py:prepare_history_for_request)
    # keeps every request inside it on its own, with no persistence. This flag
    # additionally rewrites the STORED session through
    # SessionStore.replace_history - irreversibly, discarding the transcript the
    # user reads in the Portal. "I want a smaller prompt" and "please rewrite my
    # transcripts" are different requests, so they get different knobs.
    compaction_rewrite_stored_history: bool = False
    compaction_prune: bool = True
    compaction_tail_turns: int = 2
    compaction_preserve_recent_chars: int | None = None
    compaction_preserve_recent_tokens: int | None = None
    compaction_reserved_chars: int | None = None
    compaction_tool_output_max_chars: int = 2000
    compaction_prune_min_chars: int = 20000
    compaction_prune_protect_chars: int = 40000
    enable_compaction_summarizer: bool = False
    provider_max_retries: int = 2
    provider_retry_backoff_seconds: float = 0.0
    provider_retry_backoff_multiplier: float = 2.0
    enable_context_overflow_retry: bool = True
    # Off by default: the workspace snapshot it drives is the single most
    # expensive thing on the request path (it walks and byte-copies the whole
    # workspace to the PVC before the LLM call, once per run), and nothing
    # consumes what it produces. ``AgentRuntime.revert_session`` has no HTTP
    # route, no Portal UI and no production caller in any EFP repo — the
    # capability arrived with the Runtime v2 opencode source replacement
    # (#521) as a port of opencode's revert, but the client that drives it in
    # opencode was never ported. Operators who want it can set this field per
    # agent from the Portal (it is in PORTAL_MANAGED_RUNTIME_FIELDS); wiring a
    # revert UI should flip it back on, ideally alongside deferring the
    # capture until a workspace-mutating tool actually runs.
    enable_session_revert_snapshots: bool = False
    emit_llm_stream_events: bool = True
    track_usage: bool = True
    usage_pricing: dict[str, float] = field(default_factory=dict)
    enabled_tools: list[str] | None = None
    disabled_tools: list[str] = field(default_factory=list)
    model_aware_tool_selection: bool = True
    tool_permissions: dict[str, Any] = field(default_factory=dict)
    runtime_mode: str = "build"
    enable_plan_tool: bool | None = None
    plan_mode_read_only: bool = True
    enable_question_tool: bool = False
    enable_lsp_tool: bool = False
    inject_background_task_results: bool = True
    structured_output_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    include_default_system_prompt: bool = False
    include_environment_context: bool = False
    system_prompt_texts: list[str] = field(default_factory=list)
    system_prompt_paths: list[str | Path] = field(default_factory=list)
    max_system_prompt_chars: int = 20000
    include_runtime_reminders: bool = False
    instruction_paths: list[str | Path] = field(default_factory=list)
    instruction_texts: list[str] = field(default_factory=list)
    include_default_instructions: bool = True
    attach_read_instructions: bool = True
    max_instruction_chars: int = 20000
    skill_directories: list[str | Path] = field(default_factory=list)
    active_skills: list[str] = field(default_factory=list)
    include_skill_sidecar_content: bool = False
    max_skill_sidecar_chars: int = 4000
    command_directories: list[str | Path] = field(default_factory=list)
    enable_command_expansion: bool = True
    max_command_chars: int = 20000
    resolve_prompt_references: bool = True
    max_prompt_reference_chars: int = 20000
    max_prompt_directory_entries: int = 200
    tool_output_max_lines: int | None = 2000
    tool_output_max_bytes: int | None = 50 * 1024
    tool_output_truncation_direction: str = "head"
    archive_truncated_tool_outputs: bool = True
    tool_output_dir: str | Path | None = None

    def __post_init__(self) -> None:
        if self.max_iterations is not None and self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.doom_loop_threshold is not None and self.doom_loop_threshold < 2:
            raise ValueError("doom_loop_threshold must be at least 2 or None")
        # The three size caps are strictly positive. A bare ``< 1`` comparison
        # let ``True`` through as a 1-part/1-char budget and raised a fieldless
        # TypeError on a string; the shared validator names the field either way.
        self.max_context_parts = _validate_optional_positive_int(
            self.max_context_parts,
            "max_context_parts",
        )
        self.max_context_chars = _validate_optional_positive_int(
            self.max_context_chars,
            "max_context_chars",
        )
        self.default_provider_id = _validate_non_empty_string(
            self.default_provider_id,
            "default_provider_id",
        )
        self.default_model = _validate_non_empty_string(
            self.default_model,
            "default_model",
        )
        # Zero is rejected, not coerced to None. ``max_context_chars`` already
        # rejects it and the two knobs are interchangeable expressions of the
        # same budget, so accepting ``max_context_tokens: 0`` - which yields a
        # 1-character budget, i.e. every request reduced to system context plus
        # the latest turn - would be indefensible. Coercing to None instead
        # would need a second representation of "unset" (``max_context_chars is
        # None`` is load-bearing in the reserve, preserve-recent and metadata
        # branches) and would leave the config file saying 0 while the runtime
        # silently ran the catalog default. To turn the knob off, omit the key.
        self.max_context_tokens = _validate_optional_positive_int(
            self.max_context_tokens,
            "max_context_tokens",
        )
        # Reserves stay non-negative: 0 is meaningful and distinct from unset
        # ("no response headroom", versus "use the model's declared reserve").
        # The shared validator does tighten the TYPE here - a bare ``< 0`` let
        # ``True`` and ``1000.0`` through, where the sibling
        # ``context_reserve_tokens`` below has always rejected both. Deliberate:
        # the two knobs feed the same arithmetic and should not disagree about
        # what a reserve is.
        self.context_reserve_chars = _validate_non_negative_int(
            self.context_reserve_chars,
            "context_reserve_chars",
        )
        self.context_reserve_tokens = _validate_optional_non_negative_int(
            self.context_reserve_tokens,
            "context_reserve_tokens",
        )
        self.compaction_tail_turns = _validate_non_negative_int(
            self.compaction_tail_turns,
            "compaction_tail_turns",
        )
        self.compaction_preserve_recent_chars = _validate_optional_non_negative_int(
            self.compaction_preserve_recent_chars,
            "compaction_preserve_recent_chars",
        )
        self.compaction_preserve_recent_tokens = _validate_optional_non_negative_int(
            self.compaction_preserve_recent_tokens,
            "compaction_preserve_recent_tokens",
        )
        self.compaction_reserved_chars = _validate_optional_non_negative_int(
            self.compaction_reserved_chars,
            "compaction_reserved_chars",
        )
        self.compaction_tool_output_max_chars = _validate_non_negative_int(
            self.compaction_tool_output_max_chars,
            "compaction_tool_output_max_chars",
        )
        self.compaction_prune_min_chars = _validate_non_negative_int(
            self.compaction_prune_min_chars,
            "compaction_prune_min_chars",
        )
        self.compaction_prune_protect_chars = _validate_non_negative_int(
            self.compaction_prune_protect_chars,
            "compaction_prune_protect_chars",
        )
        if self.provider_max_retries < 0:
            raise ValueError("provider_max_retries must be greater than or equal to 0")
        if self.provider_retry_backoff_seconds < 0:
            raise ValueError(
                "provider_retry_backoff_seconds must be greater than or equal to 0"
            )
        if self.provider_retry_backoff_multiplier < 1:
            raise ValueError(
                "provider_retry_backoff_multiplier must be greater than or equal to 1"
            )
        if self.max_prompt_reference_chars < 0:
            raise ValueError("max_prompt_reference_chars must be greater than or equal to 0")
        if self.max_prompt_directory_entries < 0:
            raise ValueError("max_prompt_directory_entries must be greater than or equal to 0")
        if self.max_system_prompt_chars < 0:
            raise ValueError("max_system_prompt_chars must be greater than or equal to 0")
        if self.max_instruction_chars < 0:
            raise ValueError("max_instruction_chars must be greater than or equal to 0")
        if self.max_command_chars < 0:
            raise ValueError("max_command_chars must be greater than or equal to 0")
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
        self.compaction_auto = bool(self.compaction_auto)
        # Deliberately stricter than its neighbours, which all coerce with
        # bool(). This flag alone authorises rewriting the stored session, and
        # bool() fails OPEN: the string "false" is truthy, so a value that
        # stringified anywhere on the way in would silently start destroying
        # transcripts. The size caps in this same class reject rather than
        # coerce for the weaker reason that coercion ignores what the operator
        # wrote; that argument is strictly stronger here.
        self.compaction_rewrite_stored_history = _validate_bool(
            self.compaction_rewrite_stored_history,
            "compaction_rewrite_stored_history",
        )
        self.compaction_prune = bool(self.compaction_prune)
        self.enable_compaction_summarizer = bool(self.enable_compaction_summarizer)
        self.enable_context_overflow_retry = bool(self.enable_context_overflow_retry)
        self.enable_session_revert_snapshots = bool(
            self.enable_session_revert_snapshots
        )
        self.emit_llm_stream_events = bool(self.emit_llm_stream_events)
        self.track_usage = bool(self.track_usage)
        self.usage_pricing = validate_usage_pricing(self.usage_pricing)
        self.enable_plan_tool = (
            None if self.enable_plan_tool is None else bool(self.enable_plan_tool)
        )
        self.model_aware_tool_selection = bool(self.model_aware_tool_selection)
        self.plan_mode_read_only = bool(self.plan_mode_read_only)
        self.enable_question_tool = bool(self.enable_question_tool)
        self.enable_lsp_tool = bool(self.enable_lsp_tool)
        self.inject_background_task_results = bool(
            self.inject_background_task_results
        )
        if self.structured_output_schema is not None:
            if not isinstance(self.structured_output_schema, Mapping):
                raise ValueError(
                    "structured_output_schema must be an object schema or None"
                )
            self.structured_output_schema = deepcopy(
                dict(self.structured_output_schema)
            )
        self.disabled_tools = list(self.disabled_tools)
        self.tool_permissions = normalize_tool_permissions(self.tool_permissions)
        self.metadata = dict(self.metadata)
        self.include_default_system_prompt = bool(self.include_default_system_prompt)
        self.include_environment_context = bool(self.include_environment_context)
        self.system_prompt_texts = list(self.system_prompt_texts)
        self.system_prompt_paths = list(self.system_prompt_paths)
        self.include_runtime_reminders = bool(self.include_runtime_reminders)
        self.instruction_paths = list(self.instruction_paths)
        self.instruction_texts = list(self.instruction_texts)
        self.include_default_instructions = bool(self.include_default_instructions)
        self.attach_read_instructions = bool(self.attach_read_instructions)
        self.skill_directories = list(self.skill_directories)
        self.active_skills = list(self.active_skills)
        self.command_directories = list(self.command_directories)
        self.enable_command_expansion = bool(self.enable_command_expansion)
        self.archive_truncated_tool_outputs = bool(self.archive_truncated_tool_outputs)


def _validate_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return int(value)


def _validate_optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_non_negative_int(value, field_name)


def _validate_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return int(value)


def _validate_optional_positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_positive_int(value, field_name)


def _validate_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _validate_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


__all__ = ["RuntimeConfig"]
