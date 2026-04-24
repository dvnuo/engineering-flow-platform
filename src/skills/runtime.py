"""Runtime skill configuration and layered prompt assembly."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set

from src.config import config
from src.runtime.response_flow_policy import resolve_skill_behavior_defaults
from src.skills.registry import Skill

logger = logging.getLogger(__name__)


@dataclass
class SkillPromptBlocks:
    system_rules: str = ""
    developer_instructions: str = ""
    references_summary: str = ""


@dataclass
class SkillPromptLayers:
    system_rules_text: str = ""
    developer_instructions_text: str = ""
    reference_context_text: str = ""


@dataclass
class EffectivePromptAssembly:
    base_system_prompt: str
    system_rules_text: str = ""
    developer_instructions_text: str = ""
    reference_context_text: str = ""
    serialized_system_prompt: str = ""
    serialized_developer_prompt: str = ""


@dataclass
class ReferenceAttachment:
    references: List[str] = field(default_factory=list)
    context_text: str = ""
    attachment_mode: str = "compact"


@dataclass
class SkillRuntimeConfig:
    skill_name: str
    allowed_tools: List[str] = field(default_factory=list)
    allowed_tools_set: Set[str] = field(default_factory=set)
    tool_policy_declared: bool = False
    task_tools: List[str] = field(default_factory=list)
    model_override: Optional[str] = None
    hooks: List[str] = field(default_factory=list)
    workdir: str = ""
    planning_mode: str = "auto"
    staging_mode: str = "auto"
    execution_style: str = "direct"
    ask_user_policy: str = "blocked_only"
    active_skill_conflict_policy: str = "auto_switch_direct"
    prompt_blocks: SkillPromptBlocks = field(default_factory=SkillPromptBlocks)
    references: List[str] = field(default_factory=list)


_PRIORITY_SECTION_ORDER = [
    "output contract",
    "reference usage",
    "constraints",
    "rules",
    "tool policy",
    "acceptance",
    "quality",
    "strategy",
    "workflow",
    "process",
    "steps",
    "instructions",
]
_CRITICAL_SECTION_KEYWORDS = {"output contract", "reference usage", "constraints", "rules", "tool policy"}
_MIN_CRITICAL_SECTION_CHARS = 300
_MAX_INTRO_SECTION_CHARS = 1200
_SECTION_TRUNCATED_NOTE = "[Section truncated due to skill contract budget.]"

_SKILL_CONTRACT_TRUNCATED_NOTE = (
    "[Skill contract truncated: retained priority sections such as output contract, constraints, "
    "reference usage, and workflow.]"
)


def compile_skill_prompt_contract(skill: Skill, *, max_chars: int = 12000) -> str:
    body = (skill.body or "").strip()
    if not body:
        return ""
    if max_chars <= 0:
        max_chars = 12000
    if len(body) <= max_chars:
        return body

    lines = body.splitlines()
    section_pattern = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
    parsed_sections: List[dict] = []
    current_lines: List[str] = []
    current_heading = ""

    for line in lines:
        match = section_pattern.match(line)
        if match:
            parsed_sections.append({"heading": current_heading, "lines": current_lines})
            current_heading = match.group(2).strip().lower()
            current_lines = [line]
            continue
        current_lines.append(line)
    parsed_sections.append({"heading": current_heading, "lines": current_lines})

    intro_sections: List[dict] = []
    remaining_sections: List[dict] = []
    for index, section in enumerate(parsed_sections):
        if not section.get("lines"):
            continue
        normalized = "\n".join(line.rstrip() for line in section.get("lines", [])).strip("\n")
        if not normalized:
            continue
        heading = str(section.get("heading", ""))
        record = {
            "heading": heading,
            "text": normalized,
            "rank": _section_priority_rank(heading),
            "index": index,
        }
        if not heading:
            intro_sections.append(record)
        else:
            remaining_sections.append(record)

    if remaining_sections:
        intro_sections.append(remaining_sections[0])
        remaining_sections = remaining_sections[1:]

    priority_sections = [section for section in remaining_sections if section["rank"] < len(_PRIORITY_SECTION_ORDER)]
    non_priority_sections = [section for section in remaining_sections if section["rank"] >= len(_PRIORITY_SECTION_ORDER)]
    priority_sections.sort(key=lambda section: (section["rank"], section["index"]))
    ordered_sections = intro_sections + priority_sections + non_priority_sections

    compiled_parts: List[str] = []
    used_chars = 0
    truncated = False
    pending_critical_sections = [section for section in ordered_sections if _is_critical_section(section)]
    critical_sections_added = 0

    for section in ordered_sections:
        section_text = section["text"]
        if not section_text:
            continue
        joiner = "\n\n" if compiled_parts else ""
        heading = str(section.get("heading", ""))
        is_intro = not heading
        is_critical = _is_critical_section(section)
        is_low_priority_process = _is_low_priority_process_section(section)

        remaining_critical_count = len(pending_critical_sections) - (1 if is_critical else 0)
        reserve_for_remaining = remaining_critical_count * _MIN_CRITICAL_SECTION_CHARS
        available_total = max_chars - used_chars - len(joiner)
        available_for_this = available_total - reserve_for_remaining
        if is_critical and available_for_this <= 0:
            available_for_this = max(0, available_total)

        if available_total <= 0 or available_for_this <= 0:
            truncated = True
            if is_critical and available_total > 0:
                minimal_text = _truncate_section_text(
                    section_text,
                    available_total,
                    preserve_heading=True,
                    max_body_chars=max(80, available_total // 3),
                )
                if minimal_text:
                    compiled_parts.append(f"{joiner}{minimal_text}" if joiner else minimal_text)
                    used_chars += len(joiner) + len(minimal_text)
                    critical_sections_added += 1
                    pending_critical_sections = [item for item in pending_critical_sections if item is not section]
            continue

        if is_intro:
            limited_intro_text = _limit_intro_section_text(section_text, cap=_MAX_INTRO_SECTION_CHARS)
            if limited_intro_text != section_text:
                truncated = True
            section_text = limited_intro_text

        if len(section_text) <= available_for_this:
            compiled_parts.append(f"{joiner}{section_text}" if joiner else section_text)
            used_chars += len(joiner) + len(section_text)
            if is_critical:
                critical_sections_added += 1
                pending_critical_sections = [item for item in pending_critical_sections if item is not section]
            continue

        if is_critical:
            truncated_text = _truncate_section_text(
                section_text,
                available_for_this,
                preserve_heading=True,
                max_body_chars=max(120, available_for_this // 2),
            )
            if truncated_text:
                compiled_parts.append(f"{joiner}{truncated_text}" if joiner else truncated_text)
                used_chars += len(joiner) + len(truncated_text)
                critical_sections_added += 1
                pending_critical_sections = [item for item in pending_critical_sections if item is not section]
                truncated = True
                continue
        elif is_low_priority_process and critical_sections_added < len([s for s in ordered_sections if _is_critical_section(s)]):
            truncated = True
            continue
        elif not is_low_priority_process:
            truncated_text = _truncate_section_text(section_text, available_for_this, preserve_heading=False, max_body_chars=available_for_this)
            if truncated_text:
                compiled_parts.append(f"{joiner}{truncated_text}" if joiner else truncated_text)
                used_chars += len(joiner) + len(truncated_text)
                truncated = True
                continue
        truncated = True

    compiled = "".join(compiled_parts).strip()
    if not truncated:
        return compiled
    if not compiled:
        return _SKILL_CONTRACT_TRUNCATED_NOTE[:max_chars].strip()
    return _append_contract_truncation_note(compiled, max_chars=max_chars)


def _section_priority_rank(heading: str) -> int:
    heading_text = str(heading or "").strip().lower()
    if not heading_text:
        return len(_PRIORITY_SECTION_ORDER) + 10
    for idx, keyword in enumerate(_PRIORITY_SECTION_ORDER):
        if keyword in heading_text:
            return idx
    return len(_PRIORITY_SECTION_ORDER) + 10


def _truncate_section_text(
    section_text: str,
    available: int,
    *,
    preserve_heading: bool = False,
    max_body_chars: Optional[int] = None,
) -> str:
    if available <= 0:
        return ""
    if len(section_text) <= available:
        return section_text
    if available <= len(_SECTION_TRUNCATED_NOTE) + 1:
        return section_text[:available].rstrip()
    keep_len = available - len(_SECTION_TRUNCATED_NOTE) - 1
    if keep_len <= 0:
        return section_text[:available].rstrip()
    if preserve_heading:
        lines = section_text.splitlines()
        heading_line = lines[0] if lines else ""
        body_text = "\n".join(lines[1:]).strip()
        heading_with_newline = f"{heading_line}\n" if heading_line else ""
        remaining_for_body = keep_len - len(heading_with_newline)
        if max_body_chars is not None:
            remaining_for_body = min(remaining_for_body, max_body_chars)
        if remaining_for_body <= 0:
            minimal = heading_line[:keep_len].rstrip() if heading_line else section_text[:keep_len].rstrip()
            return f"{minimal}\n{_SECTION_TRUNCATED_NOTE}"
        body_part = body_text[:remaining_for_body].rstrip()
        combined = f"{heading_with_newline}{body_part}".rstrip()
        return f"{combined}\n{_SECTION_TRUNCATED_NOTE}"
    truncated_body = section_text[:keep_len].rstrip()
    return f"{truncated_body}\n{_SECTION_TRUNCATED_NOTE}"


def _limit_intro_section_text(section_text: str, *, cap: int) -> str:
    if cap <= 0 or len(section_text) <= cap:
        return section_text
    if cap <= len(_SECTION_TRUNCATED_NOTE) + 1:
        return section_text[:cap].rstrip()
    keep_len = cap - len(_SECTION_TRUNCATED_NOTE) - 1
    return f"{section_text[:keep_len].rstrip()}\n{_SECTION_TRUNCATED_NOTE}"


def _is_critical_section(section: dict) -> bool:
    rank = int(section.get("rank", len(_PRIORITY_SECTION_ORDER) + 10))
    return rank < len(_PRIORITY_SECTION_ORDER) and _PRIORITY_SECTION_ORDER[rank] in _CRITICAL_SECTION_KEYWORDS


def _is_low_priority_process_section(section: dict) -> bool:
    rank = int(section.get("rank", len(_PRIORITY_SECTION_ORDER) + 10))
    if rank >= len(_PRIORITY_SECTION_ORDER):
        return False
    keyword = _PRIORITY_SECTION_ORDER[rank]
    return keyword in {"workflow", "process", "steps", "instructions"}


def _append_contract_truncation_note(compiled: str, *, max_chars: int) -> str:
    note = _SKILL_CONTRACT_TRUNCATED_NOTE
    combined = f"{compiled}\n{note}"
    if len(combined) <= max_chars:
        return combined
    available = max_chars - len(note) - 1
    if available <= 0:
        return compiled[:max_chars].rstrip()
    return f"{compiled[:available].rstrip()}\n{note}"


def summarize_skill_references(skill: Skill) -> List[str]:
    fallback_to_cwd = False
    base_dir = Path(getattr(skill, "path", "") or "").resolve() if getattr(skill, "path", "") else None
    if base_dir is None:
        source_file = Path(getattr(skill, "source_file", "") or "")
        base_dir = source_file.parent.resolve() if str(source_file).strip() else None
    if base_dir is None:
        base_dir = Path.cwd()
        fallback_to_cwd = True

    if skill.references:
        refs: List[str] = []
        for ref in skill.references:
            ref_text = str(ref or "").strip()
            if not ref_text:
                continue
            ref_path = Path(ref_text)
            if ref_path.is_absolute() or base_dir is None:
                refs.append(str(ref_path))
            else:
                refs.append(str((base_dir / ref_path).resolve()))
        if fallback_to_cwd:
            logger.debug("[SkillRuntime] Resolved explicit references relative to cwd because skill path/source_file were empty.")
        return refs
    if not skill.path:
        return []

    skill_dir = Path(skill.path)
    refs: List[str] = []
    references_dir = skill_dir / "references"
    if references_dir.exists():
        refs.extend(str(p) for p in sorted(references_dir.glob("*")) if p.is_file())
    else:
        source_file = Path(getattr(skill, "source_file", "") or "")
        source_name = source_file.name.lower()
        source_stem = source_file.stem.lower()
        if source_name == "skill.md":
            refs.extend(str(p) for p in sorted(skill_dir.glob("ref-*.md")) if p.is_file())
        elif source_stem:
            for pattern in (f"ref-{source_stem}*.md", f"{source_stem}.ref*.md"):
                refs.extend(str(p) for p in sorted(skill_dir.glob(pattern)) if p.is_file())
    return refs


def build_reference_context(reference_paths: List[str], max_items: int = 8) -> str:
    if not reference_paths:
        return "References: none"
    names = [Path(item).name for item in reference_paths[:max_items]]
    suffix = "" if len(reference_paths) <= max_items else f" (+{len(reference_paths) - max_items} more)"
    return f"Available references: {', '.join(names)}{suffix}"


def build_skill_prompt_blocks(
    skill: Skill,
    references: Optional[List[str]] = None,
    allowed_tools: Optional[List[str]] = None,
    task_tools: Optional[List[str]] = None,
    tool_policy_declared: Optional[bool] = None,
    runtime_config: Optional[SkillRuntimeConfig] = None,
) -> SkillPromptBlocks:
    declared_policy = bool(skill.tools) if tool_policy_declared is None else bool(tool_policy_declared)
    effective_allowed_tools = list(skill.tools or []) if allowed_tools is None else list(allowed_tools or [])
    effective_task_tools = list(skill.task_tools or []) if task_tools is None else list(task_tools or [])
    if declared_policy:
        allowed_tools_text = ", ".join(effective_allowed_tools) if effective_allowed_tools else "none"
    else:
        allowed_tools_text = "all currently available tools"
    task_tools_text = ", ".join(effective_task_tools) if effective_task_tools else "none"
    resolved_execution_style = str((runtime_config.execution_style if runtime_config else getattr(skill, "execution_style", "direct")) or "direct")
    resolved_planning_mode = str((runtime_config.planning_mode if runtime_config else getattr(skill, "planning_mode", "auto")) or "auto")
    resolved_staging_mode = str((runtime_config.staging_mode if runtime_config else getattr(skill, "staging_mode", "auto")) or "auto")
    resolved_conflict_policy = str(
        (
            runtime_config.active_skill_conflict_policy
            if runtime_config
            else getattr(skill, "active_skill_conflict_policy", "auto_switch_direct")
        )
        or "auto_switch_direct"
    )
    # Active-skill conflict handling intent:
    # - direct + always_ask: explicit special case (keep context and ask continue/switch)
    # - direct default: do not force ask; allow direct switch on clear new request
    # - stepwise/required modes: continue only on clear continuation; avoid sticky swallowing
    direct_mode = resolved_execution_style == "direct" and resolved_planning_mode != "required" and resolved_staging_mode != "required"
    if direct_mode and resolved_conflict_policy == "always_ask":
        continuity_rule = (
            "For direct skills, continue this active skill when the user clearly continues the same request."
        )
        switching_rule = (
            "For direct skills with active_skill_conflict_policy=always_ask, do not auto-switch on a clear new request. "
            "Keep this active skill context and ask the user to choose: continue current skill or switch to the new request. "
            "Proceed only after the user clearly confirms continue/switch."
        )
    elif direct_mode:
        continuity_rule = (
            "For direct skills, continue this active skill when the user clearly continues the same request."
        )
        switching_rule = (
            "For direct skills, if the user gives a clear new request, treat that as leaving this prior skill and handle the new request directly without asking for switch permission."
        )
    else:
        continuity_rule = (
            "For stepwise/required-plan/required-staging skills, continue the flow when the user clearly continues."
        )
        switching_rule = (
            "For stepwise/required-plan/required-staging skills, allow switching/leaving when the latest request is clearly different; "
            "only ask continue-vs-switch if the latest user turn is genuinely ambiguous."
        )
    system_rules = (
        f"Active skill: {skill.name}.\n"
        "Stay within the skill's instructions, constraints, output contract, reference usage, and allowed tool policy while this skill is active.\n"
        f"{continuity_rule}\n"
        f"{switching_rule}\n"
        "Do not invent tool results, references, or external facts that were not provided.\n"
        f"Runtime policy: only use allowed tools ({allowed_tools_text}).\n"
        f"Task-capable tools: {task_tools_text}."
    )
    if runtime_config and (
        runtime_config.execution_style != "direct"
        or runtime_config.planning_mode != "auto"
        or runtime_config.staging_mode != "auto"
        or runtime_config.ask_user_policy != "blocked_only"
        or runtime_config.active_skill_conflict_policy != "auto_switch_direct"
    ):
        system_rules += (
            f"\nSkill behavior: execution_style={runtime_config.execution_style}, "
            f"planning_mode={runtime_config.planning_mode}, staging_mode={runtime_config.staging_mode}, "
            f"ask_user_policy={runtime_config.ask_user_policy}, "
            f"active_skill_conflict_policy={runtime_config.active_skill_conflict_policy}."
        )

    strategy = "\n".join(f"- {item}" for item in (skill.strategy or []))
    body = (skill.body or "").strip()
    body_compact = compile_skill_prompt_contract(skill)

    developer_parts = [
        f"Skill: {skill.name}",
        f"Description: {skill.description}",
    ]
    if strategy:
        developer_parts.extend(["Strategy:", strategy])
    if body_compact:
        developer_parts.extend(["Instructions:", body_compact])

    refs = list(references or [])
    references_summary = build_reference_context(refs)

    return SkillPromptBlocks(
        system_rules=system_rules,
        developer_instructions="\n".join(developer_parts),
        references_summary=references_summary,
    )


def assemble_skill_prompt_layers(runtime_config: Optional[SkillRuntimeConfig]) -> SkillPromptLayers:
    if not runtime_config:
        return SkillPromptLayers()
    return SkillPromptLayers(
        system_rules_text=(runtime_config.prompt_blocks.system_rules or "").strip(),
        developer_instructions_text=(runtime_config.prompt_blocks.developer_instructions or "").strip(),
        reference_context_text=(runtime_config.prompt_blocks.references_summary or "").strip(),
    )


def serialize_prompt_layers(base_system_prompt: str, layers: SkillPromptLayers) -> EffectivePromptAssembly:
    system_sections = [base_system_prompt.strip()]
    if layers.system_rules_text:
        system_sections.append(f"## Skill Runtime Rules\n{layers.system_rules_text}")
    if layers.developer_instructions_text:
        system_sections.append(f"## Skill Developer Instructions\n{layers.developer_instructions_text}")
    if layers.reference_context_text:
        system_sections.append(f"## Skill References\n{layers.reference_context_text}")

    serialized_system = "\n\n".join(section for section in system_sections if section).strip()
    serialized_developer = layers.developer_instructions_text

    return EffectivePromptAssembly(
        base_system_prompt=base_system_prompt,
        system_rules_text=layers.system_rules_text,
        developer_instructions_text=layers.developer_instructions_text,
        reference_context_text=layers.reference_context_text,
        serialized_system_prompt=serialized_system,
        serialized_developer_prompt=serialized_developer,
    )


def assemble_effective_prompt(base_system_prompt: str, runtime_config: Optional[SkillRuntimeConfig]) -> EffectivePromptAssembly:
    layers = assemble_skill_prompt_layers(runtime_config)
    return serialize_prompt_layers(base_system_prompt, layers)


def attach_skill_references(runtime_config: Optional[SkillRuntimeConfig]) -> ReferenceAttachment:
    refs = list((runtime_config.references if runtime_config else []) or [])
    return ReferenceAttachment(references=refs, context_text=build_reference_context(refs), attachment_mode="compact")


def build_skill_runtime_config(
    skill: Skill,
    globally_allowed_tool_names: Optional[Iterable[str]] = None,
) -> SkillRuntimeConfig:
    llm_cfg = config.llm if isinstance(config.llm, dict) else {}
    response_flow_cfg = llm_cfg.get("response_flow") if isinstance(llm_cfg.get("response_flow"), dict) else {}
    resolved_execution_style, resolved_ask_user_policy, resolved_conflict_policy = resolve_skill_behavior_defaults(
        response_flow_cfg,
        execution_style=str(getattr(skill, "execution_style", "") or ""),
        ask_user_policy=str(getattr(skill, "ask_user_policy", "") or ""),
        active_skill_conflict_policy=str(getattr(skill, "active_skill_conflict_policy", "") or ""),
    )
    references = summarize_skill_references(skill)
    raw_skill_tools = list(skill.tools or [])
    raw_task_tools = list(skill.task_tools or [])
    tool_policy_declared = bool(raw_skill_tools)

    if globally_allowed_tool_names is None:
        effective_allowed_tools = list(raw_skill_tools)
        if raw_skill_tools:
            raw_skill_tool_set = set(raw_skill_tools)
            effective_task_tools = [tool_name for tool_name in raw_task_tools if tool_name in raw_skill_tool_set]
        else:
            effective_task_tools = list(raw_task_tools)
    else:
        allowset = {str(tool_name) for tool_name in globally_allowed_tool_names}
        if tool_policy_declared:
            effective_allowed_tools = [tool_name for tool_name in raw_skill_tools if tool_name in allowset]
        else:
            effective_allowed_tools = []
        effective_task_tools = [tool_name for tool_name in raw_task_tools if tool_name in allowset]
        if tool_policy_declared:
            effective_allowed_tool_set = set(effective_allowed_tools)
            effective_task_tools = [tool_name for tool_name in effective_task_tools if tool_name in effective_allowed_tool_set]

    runtime_config = SkillRuntimeConfig(
        skill_name=skill.name,
        allowed_tools=effective_allowed_tools,
        allowed_tools_set=set(effective_allowed_tools),
        tool_policy_declared=tool_policy_declared,
        task_tools=effective_task_tools,
        model_override=skill.model or None,
        hooks=list(skill.hooks or []),
        workdir=skill.path or "",
        planning_mode=str(getattr(skill, "planning_mode", "auto") or "auto"),
        staging_mode=str(getattr(skill, "staging_mode", "auto") or "auto"),
        execution_style=resolved_execution_style,
        ask_user_policy=resolved_ask_user_policy,
        active_skill_conflict_policy=resolved_conflict_policy,
        references=references,
    )
    prompt_blocks = build_skill_prompt_blocks(
        skill,
        references=references,
        allowed_tools=effective_allowed_tools,
        task_tools=effective_task_tools,
        tool_policy_declared=tool_policy_declared,
        runtime_config=runtime_config,
    )
    runtime_config.prompt_blocks = prompt_blocks
    return runtime_config
