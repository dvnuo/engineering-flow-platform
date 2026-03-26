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
from typing import Dict, List, Optional, Any
import os
from dataclasses import dataclass, field

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

# Module-level YAML instance
_yaml = YAML()


@dataclass
class SkillStep:
    """A single step in a skill workflow (Issue #362).
    
    Aligns with Issue #362's proposed design.
    
    Step Types:
    - llm: Default. LLM reasoning with optional tools (tool calls allowed)
    - tool: Execute a specific tool and return result
    - user_input: Wait for user to provide input
    - review: LLM reviews output and decides pass/fail
    """
    id: str
    title: str
    objective: str
    type: str = "llm"  # llm, tool, user_input, review
    instructions: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)  # Files to load for this step
    completion_check: List[str] = field(default_factory=list)  # Validation rules
    next_step: Optional[str] = None  # ID of next step, or None if final
    
    @classmethod
    def from_dict(cls, data: Dict) -> "SkillStep":
        """Create SkillStep from dictionary."""
        return cls(
            id=data.get("id", ""),
            title=data.get("title", data.get("name", "")),
            objective=data.get("objective", data.get("description", "")),
            type=data.get("type", "llm"),  # Default to llm
            instructions=data.get("instructions", []),
            allowed_tools=data.get("allowed_tools", data.get("required_tools", [])),
            references=data.get("references", []),
            completion_check=data.get("completion_check", data.get("validation", [])),
            next_step=data.get("next_step"),
        )


@dataclass
class Skill:
    """Skill definition from YAML file.
    
    Supports two execution modes:
    1. Legacy (strategy-based): Single prompt injection via strategy list
    2. Step-based (steps): Step-orchestrated execution via steps
    
    Step mode takes precedence if both are defined.
    """
    name: str
    description: str
    version: str = "1.0.0"
    owner: str = ""
    triggers: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    strategy: List[str] = field(default_factory=list)  # Legacy: single-prompt injection
    steps: List[SkillStep] = field(default_factory=list)  # Step-based execution (Issue #362)
    output_format: str = "markdown"
    deprecated: bool = False
    path: str = ""  # Directory containing skill.md
    
    # Compiled patterns for fast matching
    trigger_patterns: List[re.Pattern] = field(default_factory=list)
    
    @property
    def has_steps(self) -> bool:
        """Check if skill defines step-based execution (Issue #362)."""
        return len(self.steps) > 0
    
    # Backward compatibility alias
    @property
    def has_workflow(self) -> bool:
        """Check if skill defines step-based execution (backward compat alias)."""
        return self.has_steps
    
    def get_step(self, step_id: str) -> Optional[SkillStep]:
        """Get step by ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None
    
    def get_first_step(self) -> Optional[SkillStep]:
        """Get the first step in the workflow."""
        return self.steps[0] if self.steps else None
    
    def to_dict(self) -> Dict:
        """Convert skill to dictionary for serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "owner": self.owner,
            "triggers": self.triggers,
            "tools": self.tools,
            "strategy": self.strategy,
            "steps": [
                {
                    "id": s.id,
                    "title": s.title,
                    "objective": s.objective,
                    "type": s.type,
                    "instructions": s.instructions,
                    "allowed_tools": s.allowed_tools,
                    "references": s.references,
                    "completion_check": s.completion_check,
                    "next_step": s.next_step,
                }
                for s in self.steps
            ],
            "output_format": self.output_format,
            "deprecated": self.deprecated,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Skill":
        """Create Skill from dictionary (parsed YAML)."""
        # Support both "trigger" and "triggers" keys
        triggers = data.get("trigger") or data.get("triggers", [])
        patterns = [re.compile(re.escape(t), re.IGNORECASE) for t in triggers]
        
        # Parse steps (Issue #362) - support both "steps" and "workflow" for backward compat
        steps_data = data.get("steps", data.get("workflow", []))
        steps = [SkillStep.from_dict(s) for s in steps_data]
        
        return cls(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            owner=data.get("owner", ""),
            triggers=triggers,
            tools=data.get("tools", []),
            strategy=data.get("strategy", []),
            steps=steps,
            output_format=data.get("output_format", "markdown"),
            deprecated=data.get("deprecated", False),
            trigger_patterns=patterns,
        )


class SkillRegistry:
    """Central registry for all available skills.
    
    Supports two skill directories:
    1. Project skills: <project>/skills/
    2. User skills: ~/.efp/skills/ (user skills override project skills)
    """
    
    def __init__(self, project_skills_dir: str = "skills", user_skills_dir: str = "~/.efp/skills"):
        self.project_skills_dir = Path(project_skills_dir)
        self.user_skills_dir = Path(user_skills_dir).expanduser()
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
    
    def _load_skill_file(self, file_path: Path) -> Optional[Skill]:
        """Load a single skill from YAML or MD file."""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if it's a markdown file with frontmatter
        if file_path.suffix == '.md' and content.startswith('---'):
            # Extract frontmatter between first two ---
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                data = _yaml.load(frontmatter)
            else:
                data = {}
        else:
            # Plain YAML file
            data = _yaml.load(content)
        
        if not data:
            return None
        
        skill = Skill.from_dict(data)
        skill.path = str(file_path.parent.resolve())  # Store the directory path
        skill_file = file_path.name
        
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
        """Generate skill prompt for LLM injection.
        
        Reference: FR-3 Dynamic Skill Injection
        """
        prompt_parts = [
            f"Skill: {skill.name}",
            f"Description: {skill.description}",
            "",
            "When activated, you MUST follow this strategy:",
        ]
        
        for step in skill.strategy:
            prompt_parts.append(f"  {step}")
        
        prompt_parts.extend([
            "",
            f"Output format: {skill.output_format}",
        ])
        
        return "\n".join(prompt_parts)
    
    def get_step_prompt(
        self,
        skill: Skill,
        step: SkillStep,
        context: Dict[str, Any] = None,
    ) -> str:
        """Issue #362: Generate step-specific prompt for workflow execution.
        
        Builds a prompt that includes:
        - skill name
        - current step id/title
        - objective
        - instructions
        - allowed tools
        - relevant references (loaded from files)
        - completion criteria
        - required output schema (JSON)
        
        Args:
            skill: The skill this step belongs to
            step: The step to generate prompt for
            context: Optional context dict with previous step results
            
        Returns:
            Formatted prompt string for the step
        """
        context = context or {}
        prompt_parts = []
        
        # Header
        prompt_parts.append(f"## Skill: {skill.name}")
        prompt_parts.append(f"### Current Step: {step.id} - {step.title}")
        prompt_parts.append("")
        
        # Objective
        prompt_parts.append("### Objective")
        prompt_parts.append(step.objective)
        prompt_parts.append("")
        
        # Instructions
        if step.instructions:
            prompt_parts.append("### Instructions")
            for i, instruction in enumerate(step.instructions, 1):
                prompt_parts.append(f"{i}. {instruction}")
            prompt_parts.append("")
        
        # Allowed tools
        if step.allowed_tools:
            prompt_parts.append("### Allowed Tools")
            prompt_parts.append("You MUST only use these tools for this step:")
            for tool in step.allowed_tools:
                prompt_parts.append(f"- `{tool}`")
            prompt_parts.append("")
        
        # References - Issue #362: Progressive reference loading (with security fixes)
        if step.references and skill.path:
            prompt_parts.append("### References")
            prompt_parts.append("Load and consider the following reference files:")
            MAX_REF_SIZE = 10000  # 10KB per file cap
            for ref_file in step.references:
                # Security: reject absolute paths
                if Path(ref_file).is_absolute():
                    prompt_parts.append(f"- {ref_file} (rejected: absolute path not allowed)")
                    continue
                
                # Security: resolve path and verify it's within skill.path (no traversal)
                ref_path = (Path(skill.path) / ref_file).resolve()
                skill_path_resolved = Path(skill.path).resolve()
                if not str(ref_path).startswith(str(skill_path_resolved) + str(Path.sep)):
                    prompt_parts.append(f"- {ref_file} (rejected: path traversal not allowed)")
                    continue
                
                if ref_path.exists():
                    try:
                        # Security: check file size before reading
                        file_size = ref_path.stat().st_size
                        if file_size > MAX_REF_SIZE:
                            prompt_parts.append(f"- {ref_file} (skipped: file too large, {file_size} bytes)")
                            continue
                        
                        with open(ref_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        prompt_parts.append(f"\n#### {ref_file}")
                        prompt_parts.append(f"```\n{content[:2000]}...\n```" if len(content) > 2000 else f"```\n{content}\n```")
                    except Exception as e:
                        logger.warning(f"[Skill] Failed to load reference {ref_file}: {e}")
                        prompt_parts.append(f"- {ref_file} (failed to load)")
                else:
                    prompt_parts.append(f"- {ref_file} (not found)")
            prompt_parts.append("")
        
        # Completion criteria
        if step.completion_check:
            prompt_parts.append("### Completion Criteria")
            prompt_parts.append("Before responding, verify:")
            for check in step.completion_check:
                prompt_parts.append(f"- [ ] {check}")
            prompt_parts.append("")
        
        # Previous step context
        if context.get("previous_results"):
            prompt_parts.append("### Previous Step Results")
            for prev_step_id, prev_result in context["previous_results"].items():
                prompt_parts.append(f"**From {prev_step_id}:**")
                if isinstance(prev_result, dict):
                    for key, value in prev_result.items():
                        prompt_parts.append(f"- {key}: {str(value)[:500]}")
                else:
                    prompt_parts.append(f"- {str(prev_result)[:500]}")
            prompt_parts.append("")
        
        # Output schema (Issue #362)
        prompt_parts.append("### Required Output Format")
        prompt_parts.append("You MUST respond with valid JSON in this exact format:")
        prompt_parts.append("""
```json
{
  "status": "success|needs_retry|failed",
  "summary": "Brief summary of what was accomplished in this step",
  "artifacts": {
    // Key-value pairs of outputs from this step
  },
  "next_step": "next_step_id"  // or null if this is the final step
}
```""")
        prompt_parts.append("")
        
        # Important instruction
        prompt_parts.append("**Important:** Complete ONLY this step. Do not finish the entire task in one response.")
        prompt_parts.append("")
        
        return "\n".join(prompt_parts)
    
    def get_step_references(self, skill: Skill, step: SkillStep) -> Dict[str, str]:
        """Issue #362: Load reference files for a step.
        
        Args:
            skill: The skill this step belongs to
            step: The step to load references for
            
        Returns:
            Dict mapping filename to content
        """
        references = {}
        
        if not step.references or not skill.path:
            return references
        
        for ref_file in step.references:
            ref_path = Path(skill.path) / ref_file
            if ref_path.exists():
                try:
                    with open(ref_path, 'r', encoding='utf-8') as f:
                        references[ref_file] = f.read()
                except Exception as e:
                    logger.warning(f"[Skill] Failed to load reference {ref_file}: {e}")
        
        return references
    
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
    project_skills_dir="skills",
    user_skills_dir=str(Path.home() / ".efp" / "skills")
)


def load_all_skills(skills_dir: str = "skills") -> SkillRegistry:
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
