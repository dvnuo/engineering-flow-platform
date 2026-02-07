"""Skill decorator and utilities for OpsClaw.

Supports OpenClaw-style skill structure:
- SKILL.md with YAML frontmatter + Markdown documentation
- scripts/ for executable scripts
- references/ for reference documents
- assets/ for templates and resources
"""

import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillResult:
    """Result from skill execution."""

    def __init__(
        self,
        success: bool,
        output: str = "",
        error: Optional[str] = None,
        data: Optional[Dict] = None,
    ):
        self.success = success
        self.output = output
        self.error = error
        self.data = data or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "data": self.data,
        }

    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"Error: {self.error}"


class SkillMetadata:
    """Skill metadata parsed from SKILL.md YAML frontmatter."""
    
    def __init__(self, data: Dict[str, Any]):
        self.name = data.get("name", "")
        self.description = data.get("description", "")
        self.emoji = data.get("metadata", {}).get("emoji", "")
        self.requires = data.get("metadata", {}).get("requires", {})
        self.bins = self.requires.get("bins", [])
        self.any_bins = self.requires.get("anyBins", [])
        self.env = self.requires.get("env", [])
        self.config = self.requires.get("config", [])
        self.install = data.get("install", [])
        self.markdown = ""  # Content after frontmatter
    
    @property
    def full_description(self) -> str:
        """Get description with emoji."""
        if self.emoji:
            return f"{self.emoji} {self.description}"
        return self.description


def parse_skill_metadata(skill_path: str) -> Optional[SkillMetadata]:
    """Parse skill metadata from SKILL.md YAML frontmatter.
    
    Args:
        skill_path: Path to skill directory
        
    Returns:
        SkillMetadata object or None if not found/invalid
    """
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    
    if not skill_md.exists():
        return None
    
    try:
        with open(skill_md, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Parse YAML frontmatter
        # Pattern: --- \n ... YAML ... \n --- \n Markdown
        frontmatter_match = re.match(
            r'^---\s*\n(.*?)\n---\s*\n(.*)$',
            content,
            re.DOTALL
        )
        
        if frontmatter_match:
            yaml_content = frontmatter_match.group(1).strip()
            markdown_content = frontmatter_match.group(2).strip()
            
            data = yaml.safe_load(yaml_content) or {}
            metadata = SkillMetadata(data)
            metadata.markdown = markdown_content
            return metadata
        else:
            # Fallback: try to extract name from first H1
            first_line = content.split('\n')[0]
            if first_line.startswith('# '):
                metadata = SkillMetadata({
                    "name": first_line[2:].strip().split()[0].lower(),
                    "description": first_line[2:].strip()
                })
                metadata.markdown = content
                return metadata
        
        return None
        
    except Exception as e:
        print(f"Error parsing skill metadata from {skill_path}: {e}")
        return None


def get_all_skills(skills_dir: str = "skills") -> List[Dict[str, Any]]:
    """Get all skills from skills directory.
    
    Args:
        skills_dir: Path to skills directory
        
    Returns:
        List of skill metadata dictionaries
    """
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []
    
    skills = []
    
    for skill_dir in skills_path.iterdir():
        if not skill_dir.is_dir():
            continue
        
        metadata = parse_skill_metadata(str(skill_dir))
        if metadata:
            skills.append({
                "name": metadata.name,
                "description": metadata.full_description,
                "path": str(skill_dir),
                "emoji": metadata.emoji,
                "requires": {
                    "bins": metadata.bins,
                    "any_bins": metadata.any_bins,
                    "env": metadata.env,
                    "config": metadata.config
                },
                "install": metadata.install,
                "markdown": metadata.markdown
            })
    
    return skills


def get_skill_names() -> List[str]:
    """Get list of all skill names."""
    skills = get_all_skills()
    return [s["name"] for s in skills]


def get_skill_metadata(skill_name: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a specific skill."""
    skills = get_all_skills()
    for skill in skills:
        if skill["name"] == skill_name:
            return skill
    return None


def check_skill_requirements(skill_name: str) -> Dict[str, Any]:
    """Check if a skill's requirements are met.
    
    Args:
        skill_name: Name of the skill
        
    Returns:
        Dictionary with check results
    """
    import shutil
    import os
    
    metadata = get_skill_metadata(skill_name)
    if not metadata:
        return {"success": False, "error": f"Skill not found: {skill_name}"}
    
    result = {
        "skill": skill_name,
        "bins": {"required": metadata["requires"]["bins"], "available": [], "missing": []},
        "any_bins": {"required": metadata["requires"]["any_bins"], "available": [], "missing": []},
        "env": {"required": metadata["requires"]["env"], "set": [], "missing": []},
        "config": {"required": metadata["requires"]["config"], "exists": [], "missing": []},
        "ready": True
    }
    
    # Check bins
    for bin_name in metadata["requires"]["bins"]:
        if shutil.which(bin_name):
            result["bins"]["available"].append(bin_name)
        else:
            result["bins"]["missing"].append(bin_name)
            result["ready"] = False
    
    # Check any_bins (at least one must be available)
    for bin_name in metadata["requires"]["any_bins"]:
        if shutil.which(bin_name):
            result["any_bins"]["available"].append(bin_name)
    if metadata["requires"]["any_bins"] and not result["any_bins"]["available"]:
        result["any_bins"]["missing"] = metadata["requires"]["any_bins"]
        result["ready"] = False
    
    # Check env vars
    for env_name in metadata["requires"]["env"]:
        if os.environ.get(env_name):
            result["env"]["set"].append(env_name)
        else:
            result["env"]["missing"].append(env_name)
            result["ready"] = False
    
    # Check config files
    for config_path in metadata["requires"]["config"]:
        expanded_path = os.path.expanduser(config_path)
        if os.path.exists(expanded_path):
            result["config"]["exists"].append(config_path)
        else:
            result["config"]["missing"].append(config_path)
            result["ready"] = False
    
    return result


__all__ = [
    "SkillResult",
    "skill",
    "SkillMetadata",
    "parse_skill_metadata",
    "get_all_skills",
    "get_skill_names",
    "get_skill_metadata",
    "check_skill_requirements",
]
