"""Runtime skill configuration and prompt block builders."""

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
        refs.extend(
            str(p)
            for p in sorted(skill_dir.glob("*.md"))
            if p.is_file() and p.name.lower() != "skill.md"
        )
    return refs


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
    references_summary = "References: " + (", ".join(Path(r).name for r in refs) if refs else "none")

    return SkillPromptBlocks(
        system_rules=system_rules,
        developer_instructions="\n".join(developer_parts),
        references_summary=references_summary,
    )


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
