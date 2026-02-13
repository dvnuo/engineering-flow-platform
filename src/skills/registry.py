"""Skill Registry - Central storage and discovery of all available skills.

Reference: https://github.com/dvnuo/engineering-flow-platform/issues/169

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
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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
    
    # Compiled patterns for fast matching
    trigger_patterns: List[re.Pattern] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Skill":
        """Create Skill from dictionary (parsed YAML)."""
        triggers = data.get("trigger", [])
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
            trigger_patterns=patterns,
        )


class SkillRegistry:
    """Central registry for all available skills."""
    
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, Skill] = {}
        self._initialized = False
    
    def load_skills(self) -> int:
        """Load all skills from skills directory.
        
        Supports both .skill.yaml and .skill.md (with frontmatter) formats.
        
        Returns:
            Number of skills loaded
        """
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return 0
        
        loaded = 0
        # Support both .skill.yaml and .skill.md formats
        skill_files = list(self.skills_dir.glob("*.skill.yaml")) + list(self.skills_dir.glob("*.skill.md"))
        
        for skill_file in skill_files:
            try:
                skill = self._load_skill_file(skill_file)
                if skill:
                    self.skills[skill.name] = skill
                    loaded += 1
                    logger.info(f"Loaded skill: {skill.name} v{skill.version}")
            except Exception as e:
                logger.error(f"Failed to load skill {skill_file}: {e}")
        
        self._initialized = True
        logger.info(f"Skill registry initialized: {loaded} skills loaded")
        return loaded
    
    def _load_skill_file(self, file_path: Path) -> Optional[Skill]:
        """Load a single skill from YAML or MD file."""
        import yaml
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if it's a markdown file with frontmatter
        if file_path.suffix == '.md' and content.startswith('---'):
            # Extract frontmatter between first two ---
            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                data = yaml.safe_load(frontmatter)
            else:
                data = {}
        else:
            # Plain YAML file
            data = yaml.safe_load(content)
        
        if not data:
            return None
        
        skill = Skill.from_dict(data)
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
            
            # Check trigger patterns
            for pattern in skill.trigger_patterns:
                if pattern.search(message_lower):
                    # Simple scoring: longer trigger = better match
                    score = len(pattern.pattern) / 100.0
                    score = min(score, 0.9)  # Cap at 0.9
                    matches.append((skill, score))
                    break
        
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
            "",
            f"Allowed tools: {', '.join(skill.tools)}",
            "You MUST only use tools from this list.",
        ])
        
        return "\n".join(prompt_parts)
    
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


# Global registry instance
skill_registry = SkillRegistry()


def load_all_skills(skills_dir: str = "skills") -> SkillRegistry:
    """Convenience function to load all skills."""
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
