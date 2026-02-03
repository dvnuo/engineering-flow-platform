"""Skills and Tools executor for OpenClaw Mini.

This module provides the ability to execute skills and tools based on user requests.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config

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


def skill(name: str = None, description: str = "", parameters: Dict[str, Dict] = None):
    """Decorator to convert an async function into a Skill class.
    
    Args:
        name: Skill name (defaults to function name)
        description: Skill description
        parameters: Parameter schema (optional)
    
    Usage:
        @skill(name="summarize", description="Summarize content")
        async def summarize(url: str = None, text: str = None):
            ...
    """
    def decorator(func):
        # Extract parameter info from function signature
        import inspect
        sig = inspect.signature(func)
        param_dict = {}
        for param_name, param in sig.parameters.items():
            param_dict[param_name] = {
                "type": "string",
                "description": f"Parameter: {param_name}"
            }
        
        # Store the function name to avoid descriptor issues
        func_name = func.__name__
        
        class DecoratedSkill(Skill):
            @property
            def name(self):
                return name or func_name
            
            @property
            def description(self):
                return description or func.__doc__ or ""
            
            @property
            def parameters(self):
                return parameters or param_dict
            
            async def execute(self, **kwargs):
                # Filter out session_id and other unexpected kwargs
                filtered_kwargs = {k: v for k, v in kwargs.items() 
                                   if k in sig.parameters}
                # Use func directly, not as an attribute to avoid binding
                return await func(**filtered_kwargs)
        
        return DecoratedSkill()
    
    return decorator


class SkillsExecutor:
    """Execute skills and tools based on user requests."""

    def __init__(self):
        self.skills: Dict[str, Skill] = {}
        self.tools: Dict[str, Tool] = TOOLS
        self._load_skills()

    def _load_skills(self):
        """Load all available skills."""
        # Navigate up from executor/ to skills/
        skills_dir = Path(__file__).parent.parent
        logger.debug(f"Loading skills from: {skills_dir}")

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and skill_dir.name.startswith("_"):
                continue

            skill_file = skill_dir / "skill.py"
            if skill_file.exists():
                logger.debug(f"Found skill file: {skill_file}")
                self._import_skill(skill_dir.name)

    def _import_skill(self, skill_name: str):
        """Import a skill module."""
        try:
            module = __import__(
                f"skills.{skill_name}.skill",
                fromlist=[skill_name],
            )

            # Get the skill - could be a class or a decorated function
            # Try lowercase (for @skill decorated functions) first
            skill_class = getattr(module, skill_name.lower(), None)
            
            # If not found, try title case (for class-based skills)
            if skill_class is None:
                skill_class = getattr(module, skill_name.title().replace("_", ""), None)
            
            # Handle decorated function (has name and description attributes)
            if skill_class is not None and callable(skill_class):
                if hasattr(skill_class, 'name') and hasattr(skill_class, 'description'):
                    # It's a @skill decorated function
                    self.skills[skill_class.name] = skill_class
                    logger.info(f"Loaded skill: {skill_class.name}")
                elif isinstance(skill_class, Skill):
                    # It's a Skill class instance
                    self.skills[skill_class.name] = skill_class
                    logger.info(f"Loaded skill: {skill_class.name}")
                else:
                    # Look for any Skill instance or decorated function in the module
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, Skill):
                            self.skills[attr.name] = attr
                            logger.info(f"Loaded skill: {attr.name}")
                            break
                        elif callable(attr) and hasattr(attr, 'name') and hasattr(attr, 'description'):
                            self.skills[attr.name] = attr
                            logger.info(f"Loaded skill: {attr.name}")
                            break
            else:
                # Look for any Skill instance in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, Skill):
                        self.skills[attr.name] = attr
                        logger.info(f"Loaded skill: {attr.name}")
                        break
                    elif callable(attr) and hasattr(attr, 'name') and hasattr(attr, 'description'):
                        self.skills[attr.name] = attr
                        logger.info(f"Loaded skill: {attr.name}")
                        break

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
            # Handle both Skill class instances and @skill decorated functions
            if hasattr(skill, 'execute') and callable(skill.execute):
                result = await skill.execute(**kwargs)
            elif callable(skill):
                result = await skill(**kwargs)
            else:
                return SkillResult(success=False, error=f"Skill {skill_name} is not callable")
            
            # Safely log success status
            success = isinstance(result, SkillResult) and result.success
            logger.info(f"Skill {skill_name} executed: success={success}")
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


# Global executor instance - immediate initialization
# Unified pattern: All helper functions use skills_executor directly
# No lazy loading needed because:
#   1. SkillsExecutor is lightweight
#   2. HTTP clients are created on channel import anyway
#   3. Lazy loading adds complexity without significant benefit
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
    """Execute a skill by name.
    
    Note: Filters out session_id to prevent TypeError since skills
    don't accept session_id as a parameter.
    """
    # Filter out session_id and other unexpected kwargs
    filtered_kwargs = {k: v for k, v in kwargs.items() if k not in ('session_id',)}
    return await skills_executor.execute_skill(skill_name, **filtered_kwargs)


def get_tools_schemas() -> List[Dict]:
    """Get all tool schemas for LLM."""
    return get_tools_schema()


async def execute_tool_by_name(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    return await execute_tool(name, **kwargs)
