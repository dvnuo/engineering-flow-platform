"""Skills and Tools executor for OpenClaw Mini.

This module provides the ability to execute skills and tools based on user requests.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from openclaw_mini.config import config

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


class Skill:
    """Base class for skills."""

    name: str = "base_skill"
    description: str = "A skill"
    parameters: Dict[str, Dict] = {}

    async def execute(self, **kwargs) -> SkillResult:
        """Execute the skill with given parameters."""
        return SkillResult(success=False, error="Not implemented")


class SkillsExecutor:
    """Execute skills and tools based on user requests."""

    def __init__(self):
        self.skills: Dict[str, Skill] = {}
        self.tools: Dict[str, Tool] = TOOLS
        self._load_skills()

    def _load_skills(self):
        """Load all available skills."""
        skills_dir = Path(__file__).parent

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and skill_dir.name.startswith("_"):
                continue  # Skip private directories

            skill_file = skill_dir / "skill.py"
            if skill_file.exists():
                self._import_skill(skill_dir.name)

    def _import_skill(self, skill_name: str):
        """Import a skill module."""
        try:
            module = __import__(
                f"openclaw_mini.skills.{skill_name}.skill",
                fromlist=[skill_name.title()],
            )

            # Get the skill class
            skill_class = getattr(module, skill_name.title().replace("_", ""), None)
            if skill_class:
                skill_instance = skill_class()
                self.skills[skill_instance.name] = skill_instance
                logger.info(f"Loaded skill: {skill_instance.name}")

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
        if "create test" in request_lower:
            return "test_case_generator"

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
            result = await skill.execute(**kwargs)
            logger.info(f"Skill {skill_name} executed: success={result.success}")
            return result
        except Exception as e:
            logger.error(f"Skill {skill_name} failed: {e}")
            return SkillResult(success=False, error=str(e))

    def get_all_capabilities(self) -> Dict[str, Any]:
        """Get all skills and tools for LLM context."""
        return {
            "skills": [self.get_skill_info(name) for name in self.skills],
            "tools": get_tools_schema(),
        }


# Global executor instance
skills_executor = SkillsExecutor()


def list_available_skills() -> List[str]:
    """List all available skills."""
    return skills_executor.list_skills()


def list_available_tools() -> List[str]:
    """List all available tools."""
    return skills_executor.list_tools()


def get_skill_info(skill_name: str) -> Optional[Dict]:
    """Get skill information."""
    return skills_executor.get_skill_info(skill_name)


async def execute_skill(skill_name: str, **kwargs) -> SkillResult:
    """Execute a skill by name."""
    return await skills_executor.execute_skill(skill_name, **kwargs)


def get_tools_schemas() -> List[Dict]:
    """Get all tool schemas for LLM."""
    return get_tools_schema()


async def execute_tool_by_name(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    return await execute_tool(name, **kwargs)
