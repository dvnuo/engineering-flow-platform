"""Skills and Tools executor for Engineering Flow Platform.

This module provides the ability to execute skills and tools based on user requests.
"""

import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import config
from src.runtime.tool_filtering import is_tool_name_enabled_for_llm

logger = logging.getLogger(__name__)

# Import tools
from src import (
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
                result = await func(**filtered_kwargs)
                
                # Wrap string results in SkillResult for backward compatibility
                if isinstance(result, str):
                    return SkillResult(success=True, output=result)
                return result
        
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
        # Navigate up from executor/ to project root, then to skills/
        skills_dir = Path(__file__).parent.parent.parent / "skills"
        logger.debug(f"Loading skills from: {skills_dir}")

        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and skill_dir.name.startswith("_"):
                continue

            skill_file = skill_dir / "skill.py"
            if skill_file.exists():
                logger.debug(f"Found skill file: {skill_file}")
                # Import skill in isolated try-except to avoid failing entire load
                try:
                    self._import_skill(skill_dir.name, skill_file)
                except Exception as e:
                    logger.warning(f"Failed to load skill {skill_dir.name}: {e}")
                    continue

    def _import_skill(self, skill_name: str, skill_file: Path | None = None):
        """Import a skill module."""
        try:
            try:
                module = __import__(
                    f"skills.{skill_name}.skill",
                    fromlist=[skill_name],
                )
            except Exception:
                if skill_file is None:
                    raise
                module_name = f"skills.dynamic_{skill_name.replace('-', '_')}.skill"
                spec = importlib.util.spec_from_file_location(module_name, str(skill_file))
                if spec is None or spec.loader is None:
                    raise ImportError(f"Cannot build import spec for {skill_file}")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

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
            "recurring", "repeat every", "cron status", "list cron",
            "cron jobs", "show cron", "get cron"
        ]):
            return "cron"
        
        # Git skill
        if any(phrase in request_lower for phrase in [
            "git status", "git commit", "git push", "git pull",
            "git branch", "git checkout", "git log", "git diff",
            "git add", "git clone",
            "check git", "show git", "run git",
            "update repo", "update the repo", "update repository",
            "sync repo", "sync the repo", "sync repository"
        ]):
            return "git"
        
        # GitHub skill (for GitHub API and repository workflows)
        if any(phrase in request_lower for phrase in [
            "github clone", "gh clone", "clone repo", "clone repository",
            "github issue", "gh issue", "list issues", "list prs",
            "github pr", "gh pr", "pr checks", "workflow run",
            "github run", "gh run", "github api", "gh api",
            "github pr list", "github run view"
        ]):
            return "github"

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
    """Execute a skill by name via runtime bus boundary (phase1)."""
    use_execution_bus = bool(kwargs.pop("_use_execution_bus", True))
    if use_execution_bus:
        # Important recursion boundary:
        # execute_skill -> ExecutionBus(skill) -> run_skill_execution (direct), never back into execute_skill.
        from src.runtime.chat_orchestration_adapter import execute_skill_orchestration

        result = await execute_skill_orchestration(
            source_ref="executor.execute_skill",
            session_id=kwargs.get("session_id"),
            input_payload={
                "skill_name": skill_name,
                "kwargs": kwargs,
            },
            metadata={"entrypoint": "executor.execute_skill"},
        )
        payload = result.output_payload
        output_value = payload.get("output")
        return SkillResult(
            success=result.status == "success" and not payload.get("error"),
            output="" if output_value is None else str(output_value),
            error=payload.get("error"),
            data=payload.get("data") if isinstance(payload.get("data"), dict) else {},
        )
    return await run_skill_execution(skill_name, **kwargs)


async def run_skill_execution(skill_name: str, **kwargs) -> SkillResult:
    """Skill execution helper with ExecutionBus-first routing.

    Compatibility mode:
    - Set EFP_ALLOW_LEGACY_DIRECT_EXECUTION=true to bypass bus routing.
    - ExecutionBus internals set `_via_execution_bus=True` to avoid recursion.
    """
    via_execution_bus = bool(kwargs.pop("_via_execution_bus", False))
    allow_legacy_direct = os.getenv("EFP_ALLOW_LEGACY_DIRECT_EXECUTION", "").strip().lower() == "true"

    if not via_execution_bus and not allow_legacy_direct:
        return await execute_skill(skill_name, _use_execution_bus=True, **kwargs)

    # Filter runtime control/internal kwargs before forwarding to concrete skill implementations.
    filtered_kwargs = {
        k: v
        for k, v in kwargs.items()
        if k != "session_id" and not k.startswith("_")
    }
    return await skills_executor.execute_skill(skill_name, **filtered_kwargs)


def get_tools_schemas() -> List[Dict]:
    """Get all tool schemas for LLM."""
    return get_tools_schema()


async def execute_tool_by_name(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    if not is_tool_name_enabled_for_llm(name, config.llm or {}):
        message = f"Tool '{name}' is disabled by llm.tools policy."
        logger.warning("[Tool Policy] Denied tool execution: tool=%s reason=llm.tools_policy", name)
        return ToolResult(success=False, content=message, error=message)
    return await execute_tool(name, **kwargs)
