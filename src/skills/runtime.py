"""Runtime skill configuration and layered prompt assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from src.skills.registry import Skill


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
    task_tools: List[str] = field(default_factory=list)
    model_override: Optional[str] = None
    hooks: List[str] = field(default_factory=list)
    workdir: str = ""
    prompt_blocks: SkillPromptBlocks = field(default_factory=SkillPromptBlocks)
    references: List[str] = field(default_factory=list)


def summarize_skill_references(skill: Skill) -> List[str]:
    if skill.references:
        return [str(ref) for ref in skill.references if str(ref).strip()]
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


def build_skill_prompt_blocks(skill: Skill) -> SkillPromptBlocks:
    allowed_tools = ", ".join(skill.tools) if skill.tools else "all tools"
    task_tools = ", ".join(skill.task_tools) if skill.task_tools else "none"
    system_rules = (
        f"Active skill: {skill.name}. Runtime policy: only use allowed tools ({allowed_tools}). "
        f"Task-capable tools: {task_tools}."
    )

    strategy = "\n".join(f"- {item}" for item in (skill.strategy or []))
    body = (skill.body or "").strip()
    body_compact = "\n".join(line.rstrip() for line in body.splitlines()[:40]).strip()

    developer_parts = [
        f"Skill: {skill.name}",
        f"Description: {skill.description}",
    ]
    if strategy:
        developer_parts.extend(["Strategy:", strategy])
    if body_compact:
        developer_parts.extend(["Instructions:", body_compact])

    refs = summarize_skill_references(skill)
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


def build_skill_runtime_config(skill: Skill) -> SkillRuntimeConfig:
    prompt_blocks = build_skill_prompt_blocks(skill)
    references = summarize_skill_references(skill)
    return SkillRuntimeConfig(
        skill_name=skill.name,
        allowed_tools=list(skill.tools or []),
        task_tools=list(skill.task_tools or []),
        model_override=skill.model or None,
        hooks=list(skill.hooks or []),
        workdir=skill.path or "",
        prompt_blocks=prompt_blocks,
        references=references,
    )
