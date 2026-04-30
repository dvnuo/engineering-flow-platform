"""Skill Registry - Central storage and discovery of all available skills.

Responsibilities:
- Load skills at startup
- Index trigger keywords
- Provide skill metadata to prompt builder
- Support skill versioning and deprecation
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from collections.abc import Mapping
import os
from dataclasses import dataclass, field

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    """Resolve repository root for local fallback discovery."""
    return Path(__file__).resolve().parents[2]


def resolve_project_skills_dir() -> Path:
    """Resolve project skills directory from env, mounted path, or repo fallback."""
    env = os.getenv("EFP_SKILLS_DIR")
    if env and env.strip():
        return Path(env).expanduser()

    app_skills = Path("/app/skills")
    if app_skills.exists():
        return app_skills

    repo_skills = _repo_root() / "skills"
    if repo_skills.exists():
        return repo_skills

    return Path("skills")


def resolve_user_skills_dir() -> Path:
    """Resolve user skill override directory from env or ~/.efp/skills."""
    env = os.getenv("EFP_USER_SKILLS_DIR")
    if env and env.strip():
        return Path(env).expanduser()
    return Path.home() / ".efp" / "skills"

# Module-level YAML instance
_yaml = YAML()


@dataclass
class Skill:
    """Skill definition from YAML file."""
    name: str
    description: str
    version: str = "1.0.0"
    owner: str = ""
    triggers: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    strategy: List[str] = field(default_factory=list)
    output_format: str = "markdown"
    deprecated: bool = False
    path: str = ""  # Directory containing skill.md
    source_file: str = ""
    body: str = ""
    when_to_use: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    model: str = ""
    hooks: List[str] = field(default_factory=list)
    task_tools: List[str] = field(default_factory=list)
    risk_level: str = ""
    planning_mode: str = "auto"
    staging_mode: str = "auto"
    execution_style: str = ""
    ask_user_policy: str = ""
    active_skill_conflict_policy: str = ""

    # Compiled patterns for fast matching
    trigger_patterns: List[re.Pattern] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Skill":
        """Create Skill from dictionary (parsed YAML)."""
        # Support both "trigger" and "triggers" keys
        triggers = data.get("trigger") or data.get("triggers", [])
        patterns = [re.compile(re.escape(t), re.IGNORECASE) for t in triggers]
        
        return cls(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            owner=data.get("owner", ""),
            triggers=triggers,
            tools=data.get("tools", []),
            strategy=data.get("strategy", []),
            output_format=data.get("output_format", "markdown"),
            deprecated=data.get("deprecated", False),
            when_to_use=data.get("when_to_use", []) or [],
            references=data.get("references", []) or [],
            model=data.get("model", "") or "",
            hooks=data.get("hooks", []) or [],
            task_tools=data.get("task_tools", []) or [],
            risk_level=data.get("risk_level", "") or "",
            planning_mode=str(data.get("planning_mode", "auto") or "auto"),
            staging_mode=str(data.get("staging_mode", "auto") or "auto"),
            execution_style=str(data.get("execution_style", "") or ""),
            ask_user_policy=str(data.get("ask_user_policy", "") or ""),
            active_skill_conflict_policy=str(data.get("active_skill_conflict_policy", "") or ""),
            trigger_patterns=patterns,
        )


class SkillRegistry:
    """Central registry for all available skills.
    
    Supports two skill directories:
    1. Project skills: <project>/skills/
    2. User skills: ~/.efp/skills/ (user skills override project skills)
    """
    
    def __init__(self, project_skills_dir: str | Path | None = None, user_skills_dir: str | Path | None = None):
        project_dir = resolve_project_skills_dir() if project_skills_dir is None else Path(project_skills_dir)
        user_dir = resolve_user_skills_dir() if user_skills_dir is None else Path(user_skills_dir)
        self.project_skills_dir = Path(project_dir).expanduser()
        self.user_skills_dir = Path(user_dir).expanduser()
        self.skills: Dict[str, Skill] = {}
        self._initialized = False
    
    def load_skills(self) -> int:
        """Load all skills from project and user directories.
        
        User skills in ~/.efp/skills override project skills with the same name.
        
        Returns:
            Number of skills loaded
        """
        # Validate paths - user dir should not be inside project dir
        try:
            project_resolved = self.project_skills_dir.resolve()
            user_resolved = self.user_skills_dir.resolve()
            
            # Check if user dir is inside project dir
            if str(user_resolved).startswith(str(project_resolved)) and user_resolved != project_resolved:
                logger.warning(f"User skills dir is inside project dir - skipping user skills")
                self.user_skills_dir = Path("/nonexistent/user/skills")  # Invalid path
        except Exception as e:
            logger.debug(f"Path validation skipped: {e}")
        
        # Load project skills first
        project_skills = {}
        project_count = self._load_skills_into(project_skills, self.project_skills_dir, override=False)
        
        # Count overrides
        overridden = []
        
        # Load user skills (may override project skills)
        user_skills = {}
        user_count = self._load_skills_into(user_skills, self.user_skills_dir, override=True)
        
        # Merge: user skills override project skills
        self.skills = {**project_skills, **user_skills}
        
        # Count overrides
        for skill_name in user_skills:
            if skill_name in project_skills:
                overridden.append(skill_name)
        
        self._initialized = True
        
        # Log summary
        override_msg = f" ({len(overridden)} overridden)" if overridden else ""
        logger.info(f"Skill registry: {len(self.skills)} skills ({project_count} project + {user_count} user{override_msg})")
        
        if overridden:
            logger.info(f"  Overridden skills: {', '.join(overridden)}")
        
        return len(self.skills)
    
    def _load_skills_into(self, skills_dict: Dict, skills_dir: Path, override: bool) -> int:
        """Load skills into the provided dictionary.
        
        Args:
            skills_dict: Dictionary to load skills into
            skills_dir: Directory containing skills
            override: If True, skills can override existing entries
            
        Returns:
            Number of skills loaded
        """
        if not skills_dir.exists():
            return 0
        
        loaded = 0
        skill_files = []
        
        # Pattern 1: Single file skills (e.g., review-pr.md)
        for f in skills_dir.glob("*.md"):
            if f.name.lower() != "readme.md":
                skill_files.append((f, override))
        
        # Pattern 2: Directory-based skills (e.g., skill_creator/skill.md)
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_file = skill_dir / "skill.md"
                if skill_file.exists():
                    skill_files.append((skill_file, override))
        
        for skill_file, can_override in skill_files:
            try:
                skill = self._load_skill_file(skill_file)
                if skill:
                    skill_name = skill.name
                    
                    # Check for duplicates
                    if skill_name in skills_dict:
                        if not can_override:
                            continue  # Skip duplicate
                    
                    skills_dict[skill_name] = skill
                    loaded += 1
                    source = "user" if can_override else "project"
                    logger.debug(f"Loaded: {skill.name} v{skill.version} ({source})")
                    
            except Exception as e:
                logger.error(f"Failed to load skill {skill_file}: {e}")
        
        return loaded
        """Load skills from a specific directory.
        
        Args:
            skills_dir: Directory containing skills
            override: If True, these skills can override existing ones with same name
            
        Returns:
            Number of skills loaded
        """
        if not skills_dir.exists():
            if override:
                logger.debug(f"User skills directory not found: {skills_dir}")
            return 0
        
        loaded = 0
        skill_files = []
        
        # Pattern 1: Single file skills (e.g., review-pr.md)
        for f in skills_dir.glob("*.md"):
            if f.name.lower() != "readme.md":
                skill_files.append((f, override))
        
        # Pattern 2: Directory-based skills (e.g., skill_creator/skill.md)
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_file = skill_dir / "skill.md"
                if skill_file.exists():
                    skill_files.append((skill_file, override))
        
        for skill_file, can_override in skill_files:
            try:
                skill = self._load_skill_file(skill_file)
                if skill:
                    skill_name = skill.name
                    
                    # Check if skill exists and override is allowed
                    if skill_name in self.skills:
                        if can_override:
                            logger.info(f"Overriding skill '{skill_name}' with user version")
                        else:
                            logger.debug(f"Skipping duplicate skill: {skill_name}")
                            continue
                    
                    self.skills[skill_name] = skill
                    loaded += 1
                    source = "user" if can_override else "project"
                    logger.debug(f"Loaded skill: {skill.name} v{skill.version} ({source})")
                    
            except Exception as e:
                logger.error(f"Failed to load skill {skill_file}: {e}")
        
        return loaded
    
    def _parse_markdown_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        lines = content.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            return {}, content

        closing_index = None
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                closing_index = idx
                break

        if closing_index is None:
            return {}, content

        frontmatter_text = "".join(lines[1:closing_index])
        try:
            parsed = _yaml.load(frontmatter_text)
        except Exception as exc:
            logger.warning("Failed to parse skill markdown frontmatter: %s", exc)
            body = "".join(lines[closing_index + 1 :]).lstrip("\n")
            return {}, body
        if parsed is None:
            frontmatter: Dict[str, Any] = {}
        elif isinstance(parsed, Mapping):
            frontmatter = dict(parsed)
        else:
            logger.warning("Invalid skill frontmatter type: %s. Expected mapping.", type(parsed).__name__)
            frontmatter = {}
        body = "".join(lines[closing_index + 1 :]).lstrip("\n")
        return frontmatter, body

    def _load_skill_file(self, file_path: Path) -> Optional[Skill]:
        """Load a single skill from YAML or MD file."""
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        body = ""
        if file_path.suffix == ".md":
            data, body = self._parse_markdown_frontmatter(content)
        else:
            data = _yaml.load(content)

        if not data:
            return None

        skill = Skill.from_dict(data)
        skill.path = str(file_path.parent.resolve())
        skill.source_file = str(file_path.resolve())
        skill.body = body

        return skill
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get skill by name."""
        return self.skills.get(name)
    
    def list_skills(self) -> List[Skill]:
        """List all loaded skills."""
        return list(self.skills.values())
    
    def list_active_skills(self) -> List[Skill]:
        """List all non-deprecated skills."""
        return [s for s in self.skills.values() if not s.deprecated]
    
    def match_skill(self, user_message: str) -> List[Skill]:
        """Match user message against skill triggers.
        
        Args:
            user_message: Raw user input
            
        Returns:
            List of matched skills, ranked by match quality
        """
        matches = []
        message_lower = user_message.lower()
        
        for skill in self.list_active_skills():
            # Check explicit invocation first: /skill <name>
            if user_message.strip().startswith("/skill "):
                explicit_name = user_message.strip()[7:].strip().lower()
                if skill.name.lower() == explicit_name:
                    matches.append((skill, 1.0))  # Perfect match
                    continue
            
            # Check trigger patterns (from skill.yaml)
            for pattern in skill.trigger_patterns:
                if pattern.search(message_lower):
                    # Simple scoring: longer trigger = better match
                    score = len(pattern.pattern) / 100.0
                    score = min(score, 0.9)  # Cap at 0.9
                    matches.append((skill, score))
                    break
            
            # Auto-detected trigger: /<skill-name> (no explicit trigger needed)
            auto_trigger = f"/{skill.name.lower()}"
            # Exact match to avoid over-matching (e.g., /test should not match skill "test-ref")
            if message_lower.strip() == auto_trigger or message_lower.strip().startswith(f"{auto_trigger} "):
                matches.append((skill, 0.85))  # Slightly lower than explicit triggers
        
        # Sort by score descending
        matches.sort(key=lambda x: x[1], reverse=True)
        
        return [s for s, score in matches]
    
    def get_allowed_tools(self, skill_name: str) -> List[str]:
        """Get list of tools allowed for a specific skill."""
        skill = self.get_skill(skill_name)
        return skill.tools if skill else []
    
    def get_skill_prompt(self, skill: Skill) -> str:
        """Backward-compatible prompt summary."""
        from src.skills.runtime import build_skill_prompt_blocks, summarize_skill_references

        references = summarize_skill_references(skill)
        blocks = build_skill_prompt_blocks(skill, references=references)
        return "\n\n".join(
            part for part in [blocks.system_rules, blocks.developer_instructions, blocks.references_summary] if part
        )

    def get_skill_runtime_config(self, skill: Skill, globally_allowed_tool_names=None):
        from src.skills.runtime import build_skill_runtime_config

        return build_skill_runtime_config(skill, globally_allowed_tool_names=globally_allowed_tool_names)

    def get_reference_file_list(self, skill: Skill) -> List[str]:
        from src.skills.runtime import summarize_skill_references

        return summarize_skill_references(skill)
    
    def get_all_skill_summaries(self) -> List[Dict]:
        """Get summary of all skills for frontend/UI."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "version": s.version,
                "owner": s.owner,
                "triggers": s.triggers[:3],  # First 3 triggers
                "tool_count": len(s.tools),
            }
            for s in self.list_active_skills()
        ]


# Global registry instance (loads both project and user skills)
skill_registry = SkillRegistry(
    project_skills_dir=None,
    user_skills_dir=None,
)


def load_all_skills(skills_dir: str | Path | None = None) -> SkillRegistry:
    """Convenience function to load skills from a single directory.
    
    Note: For loading both project and user skills with override,
    use SkillRegistry directly.
    """
    registry = SkillRegistry(skills_dir)
    registry.load_skills()
    return registry


if __name__ == "__main__":
    # Demo: Load and list skills
    import sys
    logging.basicConfig(level=logging.INFO)
    
    skills_dir = sys.argv[1] if len(sys.argv) > 1 else "skills"
    registry = load_all_skills(skills_dir)
    
    print(f"\n=== Skill Registry ===")
    print(f"Loaded {len(registry.list_skills())} skills\n")
    
    for skill in registry.list_skills():
        print(f"  {skill.name} v{skill.version}")
        print(f"    Triggers: {skill.triggers[:2]}...")
        print(f"    Tools: {skill.tools}")
        print()
