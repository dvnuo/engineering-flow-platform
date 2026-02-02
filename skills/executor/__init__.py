"""Skills and Tools executor for OpenClaw Mini."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config
from skills.decorator import skill, SkillResult

logger = logging.getLogger(__name__)

# Import tools
from .tools import (
    ToolResult,
    Tool,
    TOOLS,
    get_tool_names,
    get_tool,
    get_tools_schema,
    execute_tool,
)


class Skill:
    """Base class for skills."""

    name: str = "base_skill"
    description: str = "A skill"
    parameters: Dict[str, Dict] = {}

    async def execute(self, **kwargs) -> SkillResult:
        """Execute the skill with given parameters."""
        return SkillResult(success=False, error="Not implemented")


# Global executor instance (lazy initialization)
_skills_executor: Optional['SkillsExecutor'] = None


def _get_executor() -> 'SkillsExecutor':
    """Get or create the skills executor (lazy initialization)."""
    global _skills_executor
    if _skills_executor is None:
        _skills_executor = SkillsExecutor()
    return _skills_executor


class SkillsExecutor:
    """Execute skills and tools based on user requests."""

    def __init__(self):
        self.skills: Dict[str, Any] = {}
        self.tools: Dict[str, Tool] = TOOLS
        self._load_skills()

    def _load_skills(self):
        """Load all available skills."""
        # Path(__file__) is /root/codew/skills/executor/__init__.py
        # We need /root/codew/skills which is parent.parent
        skills_dir = Path(__file__).parent.parent

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith("_"):
                skill_file = skill_dir / "skill.py"
                if skill_file.exists():
                    self._import_skill(skill_dir.name)

    def _import_skill(self, skill_name: str):
        """Import a skill module."""
        try:
            module = __import__(
                f"skills.{skill_name}.skill",
                fromlist=[skill_name],
            )

            # Get the skill class or decorated function
            skill_class_name_lower = skill_name.lower()
            skill_class = getattr(module, skill_class_name_lower, None)
            
            if skill_class is None:
                skill_class_name_title = skill_name.title().replace("_", "")
                skill_class = getattr(module, skill_class_name_title, None)
            
            if skill_class is not None and callable(skill_class):
                if hasattr(skill_class, 'name') and hasattr(skill_class, 'description'):
                    self.skills[skill_class.name] = skill_class
                    logger.info(f"Loaded skill: {skill_class.name}")
                elif hasattr(skill_class, 'name') and hasattr(skill_class, 'execute'):
                    self.skills[skill_class.name] = skill_class()
                    logger.info(f"Loaded skill: {skill_class.name}")
                else:
                    logger.warning(f"Skill {skill_name}: no name/description attributes")
            else:
                logger.warning(f"Skill {skill_name}: could not find callable skill class or function")

        except Exception as e:
            logger.error(f"Failed to load skill {skill_name}: {e}")

    def list_skills(self) -> List[str]:
        """List all available skills."""
        return list(self.skills.keys())

    def list_tools(self) -> List[str]:
        """List all available tools."""
        return list(self.tools.keys())

    def get_skill_info(self, skill_name: str) -> Optional[Dict]:
        """Get information about a skill."""
        skill = self.skills.get(skill_name)
        if skill:
            return {
                "name": skill.name,
                "description": skill.description,
                "parameters": skill.parameters,
            }
        return None

    def match_skill(self, request: str) -> Optional[str]:
        """Match a user request to a skill name."""
        request_lower = request.lower()

        # Direct skill mentions
        if "create test" in request_lower or "generate test" in request_lower:
            return "test_case_generator"
        
        # Summarize skill
        if any(phrase in request_lower for phrase in [
            "summarize", "summarise", "summary of", "what's this about",
            "what is this about", "tl;dr", "tldr"
        ]):
            return "summarize"
        
        # Cron/Scheduler skill
        if any(phrase in request_lower for phrase in [
            "schedule", "remind me", "set a reminder", "cron job",
            "recurring", "repeat every"
        ]):
            return "cron"
        
        # Weather skill
        if any(phrase in request_lower for phrase in [
            "weather", "temperature", "forecast", "how's the weather"
        ]):
            return "weather"

        return None

    async def execute_skill(
        self,
        skill_name: str,
        **kwargs,
    ) -> SkillResult:
        """Execute a skill by name."""
        skill = self.skills.get(skill_name)
        if not skill:
            return SkillResult(success=False, error=f"Skill not found: {skill_name}")

        try:
            if hasattr(skill, 'execute') and callable(getattr(skill, 'execute', None)):
                result = await skill.execute(**kwargs)
            elif callable(skill):
                result = await skill(**kwargs)
            else:
                return SkillResult(success=False, error=f"Skill {skill_name} is not callable")
            
            if isinstance(result, SkillResult):
                logger.info(f"Skill {skill_name} executed: success={result.success}")
                return result
            else:
                logger.info(f"Skill {skill_name} executed")
                return SkillResult(success=True, output=str(result))
        except Exception as e:
            logger.error(f"Skill {skill_name} failed: {e}")
            return SkillResult(success=False, error=str(e))

    def get_all_capabilities(self) -> Dict[str, Any]:
        """Get all skills and tools for LLM context."""
        return {
            "skills": [self.get_skill_info(name) for name in self.skills],
            "tools": get_tools_schema(),
        }


def list_available_skills() -> List[str]:
    """List all available skills."""
    return _get_executor().list_skills()


def list_available_tools() -> List[str]:
    """List all available tools."""
    return _get_executor().list_tools()


def get_skill_info(skill_name: str) -> Optional[Dict]:
    """Get skill information."""
    return _get_executor().get_skill_info(skill_name)


async def execute_skill(skill_name: str, **kwargs) -> SkillResult:
    """Execute a skill by name."""
    return await _get_executor().execute_skill(skill_name, **kwargs)


def get_tools_schemas() -> List[Dict]:
    """Get all tool schemas for LLM."""
    return get_tools_schema()


async def execute_tool_by_name(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    return await execute_tool(name, **kwargs)
