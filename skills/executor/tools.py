"""Tools for OpsClaw - Enable the agent to execute actions."""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from config import config

logger = logging.getLogger(__name__)


def _truncate_text(text: str, max_length: int = 200) -> str:
    """Truncate text for logging."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"... [{len(text) - max_length} chars truncated]"


class ToolResult:
    """Result from tool execution."""

    def __init__(
        self,
        success: bool,
        content: str = "",
        error: Optional[str] = None,
    ):
        self.success = success
        self.content = content
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "error": self.error,
        }

    def __str__(self) -> str:
        if self.success:
            return self.content
        return f"Error: {self.error}"


class Tool:
    """Base class for tools."""

    name: str = "base_tool"
    description: str = "A tool"
    parameters: Dict[str, Dict] = {}

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters."""
        return ToolResult(success=False, error="Not implemented")

    def get_schema(self) -> Dict[str, Any]:
        """Get the tool schema for LLM (OpenAI format)."""
        # Extract required fields from parameters
        required = [k for k, v in self.parameters.items() 
                   if v.get("required", False) or 
                   (k == "path" or k == "command" or k == "url" or k == "query")]
        
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": required if required else ["path"],
                },
            },
        }


class ExecTool(Tool):
    """Execute shell commands."""

    name = "exec"
    description = "Execute a shell command and return the output"
    parameters = {
        "command": {
            "type": "string",
            "description": "The command to execute",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (default: 30)",
            "default": 30,
        },
    }

    async def execute(self, command: str, timeout: int = 30) -> ToolResult:
        """Execute a shell command."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output = stdout.decode()
            error = stderr.decode()

            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    content=output,
                    error=error or f"Command failed with exit code {proc.returncode}",
                )

            return ToolResult(success=True, content=output or "(no output)")

        except asyncio.TimeoutError:
            return ToolResult(success=False, error="Command timed out")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ReadTool(Tool):
    """Read file contents."""

    name = "read"
    description = "Read the contents of a file"
    parameters = {
        "path": {
            "type": "string",
            "description": "Path to the file to read",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of lines to read (default: 100)",
            "default": 100,
        },
        "offset": {
            "type": "integer",
            "description": "Line number to start reading from (default: 1)",
            "default": 1,
        },
    }

    async def execute(self, path: str, limit: int = 100, offset: int = 1) -> ToolResult:
        """Read a file."""
        try:
            file_path = Path(path)
            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Apply offset (1-indexed to 0-indexed)
            start = max(0, offset - 1)
            end = start + limit
            content = "".join(lines[start:end])
            return ToolResult(success=True, content=content)

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WriteTool(Tool):
    """Write content to a file."""

    name = "write"
    description = "Create or overwrite a file with the given content"
    parameters = {
        "path": {
            "type": "string",
            "description": "Path to the file to write",
        },
        "content": {
            "type": "string",
            "description": "Content to write to the file",
        },
    }

    async def execute(self, path: str, content: str) -> ToolResult:
        """Write to a file."""
        try:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult(
                success=True, content=f"Successfully wrote to {path}"
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class EditTool(Tool):
    """Edit a file by replacing text."""

    name = "edit"
    description = "Make precise edits to a file by replacing exact text"
    parameters = {
        "path": {
            "type": "string",
            "description": "Path to the file to edit",
        },
        "oldText": {
            "type": "string",
            "description": "Exact text to find and replace",
        },
        "newText": {
            "type": "string",
            "description": "New text to replace with",
        },
    }

    async def execute(self, path: str, oldText: str, newText: str) -> ToolResult:
        """Edit a file."""
        try:
            file_path = Path(path)
            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            if oldText not in content:
                return ToolResult(
                    success=False, error="Text to replace not found in file"
                )

            new_content = content.replace(oldText, newText)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult(
                success=True, content=f"Successfully edited {path}"
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WebSearchTool(Tool):
    """Search the web using Brave Search API."""

    name = "web_search"
    description = "Search the web using Brave Search API"
    parameters = {
        "query": {
            "type": "string",
            "description": "The search query",
        },
        "count": {
            "type": "integer",
            "description": "Number of results (1-10, default: 5)",
            "default": 5,
        },
    }

    async def execute(self, query: str, count: int = 5) -> ToolResult:
        """Search the web."""
        api_key = config.web.get("brave_api_key", "")
        if not api_key:
            return ToolResult(
                success=False,
                error="Brave API key not configured. Set 'web.brave_api_key' in config.yaml",
            )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": min(count, 10)},
                    headers={"Accept": "application/json", "X-Subscription-Token": api_key},
                )

                if response.status_code != 200:
                    return ToolResult(
                        success=False, error=f"Search failed: {response.status_code}"
                    )

                data = response.json()
                results = data.get("web", {}).get("results", [])

                if not results:
                    return ToolResult(success=True, content="No results found")

                # Format results
                formatted = []
                for i, result in enumerate(results[:count], 1):
                    formatted.append(
                        f"{i}. {result.get('title', 'No title')}\n"
                        f"   URL: {result.get('url', 'No URL')}\n"
                        f"   Description: {result.get('description', 'No description')[:200]}"
                    )

                return ToolResult(success=True, content="\n\n".join(formatted))

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class WebFetchTool(Tool):
    """Fetch and extract readable content from a URL."""

    name = "web_fetch"
    description = "Fetch and extract readable content from a URL"
    parameters = {
        "url": {
            "type": "string",
            "description": "URL to fetch",
        },
        "extractMode": {
            "type": "string",
            "description": "Extract mode: 'markdown' or 'text' (default: markdown)",
            "default": "markdown",
        },
        "maxChars": {
            "type": "integer",
            "description": "Maximum characters to return (default: 50000)",
            "default": 50000,
        },
    }

    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int = 50000) -> ToolResult:
        """Fetch a URL."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, follow_redirects=True)

                if response.status_code != 200:
                    return ToolResult(
                        success=False, error=f"Fetch failed: {response.status_code}"
                    )

                # Simple content extraction (in real implementation, use readability)
                content = response.text[:maxChars]

                return ToolResult(success=True, content=f"Fetched {url}\n\n{content}")

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ImageTool(Tool):
    """Analyze an image with the configured image model."""

    name = "image"
    description = "Analyze an image with the configured image model"
    parameters = {
        "image": {
            "type": "string",
            "description": "Path or URL to the image",
        },
        "prompt": {
            "type": "string",
            "description": "Prompt for image analysis (default: 'Describe the image')",
            "default": "Describe the image",
        },
    }

    async def execute(self, image: str, prompt: str = "Describe the image") -> ToolResult:
        """Analyze an image."""
        # This would use the LLM client's image analysis capability
        # For now, return a placeholder
        return ToolResult(
            success=False,
            error="Image analysis not yet implemented. Requires LLM client support.",
        )


class GitTool(Tool):
    """Execute git commands."""

    name = "git"
    description = "Execute git commands (clone, status, commit, push, pull, branch, log, etc.)"
    parameters = {
        "command": {
            "type": "string",
            "description": "Git command to execute (e.g., 'clone', 'status', 'commit', 'push', 'pull', 'branch', 'log', 'checkout')",
        },
        "args": {
            "type": "string",
            "description": "Additional arguments for the command (space-separated)",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (default: 30)",
            "default": 30,
        },
    }

    async def execute(self, command: str, args: str = "", timeout: int = 30) -> ToolResult:
        """Execute a git command."""
        full_command = f"git {command}"
        if args:
            full_command += f" {args}"
        
        try:
            proc = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output = stdout.decode()
            error = stderr.decode()

            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    content=output,
                    error=error or f"git {command} failed with exit code {proc.returncode}",
                )

            return ToolResult(success=True, content=output or "(no output)")

        except asyncio.TimeoutError:
            return ToolResult(success=False, error="git command timed out")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class GhTool(Tool):
    """Execute gh CLI commands."""

    name = "gh"
    description = "Execute GitHub CLI commands (repo clone, issue list, pr checks, run list, api, etc.)"
    parameters = {
        "command": {
            "type": "string",
            "description": "gh command to execute (e.g., 'repo clone', 'issue list', 'pr checks', 'run list', 'api')",
        },
        "args": {
            "type": "string",
            "description": "Additional arguments for the command (space-separated)",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (default: 30)",
            "default": 30,
        },
    }

    async def execute(self, command: str, args: str = "", timeout: int = 30) -> ToolResult:
        """Execute a gh command."""
        full_command = f"gh {command}"
        if args:
            full_command += f" {args}"
        
        try:
            proc = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            output = stdout.decode()
            error = stderr.decode()

            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    content=output,
                    error=error or f"gh {command} failed with exit code {proc.returncode}",
                )

            return ToolResult(success=True, content=output or "(no output)")

        except asyncio.TimeoutError:
            return ToolResult(success=False, error="gh command timed out")
        except FileNotFoundError:
            return ToolResult(success=False, error="gh CLI not found. Install with: brew install gh (macOS) or apt install gh (Linux)")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# Registry of all available tools
# Note: web_search and web_fetch removed for focused toolset
TOOLS = {
    "exec": ExecTool(),
    "read": ReadTool(),
    "write": WriteTool(),
    "edit": EditTool(),
    "image": ImageTool(),
    "git": GitTool(),
    "gh": GhTool(),
}


# Registry of function-based tools (Jira, Confluence, GitHub, Git)
# Import at module level for direct function access
# NOTE: Import order matters to avoid circular imports:
#   skills.executor.tools -> src.tools.* -> src.integrations.* -> channel.* -> skills.executor

# Expected function tool names for validation
_EXPECTED_TOOL_NAMES = [
    "jira_get_issue",
    "jira_search",
    "jira_add_comment",
    "confluence_get_page",
    "confluence_search",
    "github_get_issue",
    "github_search_issues",
    "github_add_comment",
    "git_status",
    "git_commit",
    "git_push",
]

try:
    from src.tools.jira import (
        jira_get_issue,
        jira_search,
        jira_add_comment,
    )
    from src.tools.confluence import (
        confluence_get_page,
        confluence_search,
    )
    from src.tools.github import (
        github_get_issue,
        github_search_issues,
        github_add_comment,
    )
    from src.tools.git import (
        git_status,
        git_commit,
        git_push,
    )
    
    # Map function names to functions for execution
    FUNCTION_TOOLS = {
        "jira_get_issue": jira_get_issue,
        "jira_search": jira_search,
        "jira_add_comment": jira_add_comment,
        "confluence_get_page": confluence_get_page,
        "confluence_search": confluence_search,
        "github_get_issue": github_get_issue,
        "github_search_issues": github_search_issues,
        "github_add_comment": github_add_comment,
        "git_status": git_status,
        "git_commit": git_commit,
        "git_push": git_push,
    }
    
    # Validate all tools loaded at startup
    if len(FUNCTION_TOOLS) != len(_EXPECTED_TOOL_NAMES):
        missing_tools = [tool for tool in _EXPECTED_TOOL_NAMES if tool not in FUNCTION_TOOLS]
        loaded = list(FUNCTION_TOOLS.keys())
        logger.error(f"Missing tools: {missing_tools}. Loaded: {loaded}")
    else:
        logger.info(f"All {len(FUNCTION_TOOLS)} function tools loaded successfully.")

except ImportError as e:
    logger.error(f"FATAL: Failed to load function tools: {e}")
    logger.error("All Jira/Confluence/GitHub/Git tools will be UNUSABLE.")
    FUNCTION_TOOLS = {}


def _load_function_tools():
    """Lazy load function-based tools to avoid circular imports.
    
    NOTE: FUNCTION_TOOLS is now loaded at module level.
    This function kept for backward compatibility.
    """
    pass


def get_tool_names() -> list:
    """Get list of all tool names."""
    _load_function_tools()
    return list(TOOLS.keys()) + list(FUNCTION_TOOLS.keys())


def get_tool(name: str) -> Optional[Tool]:
    """Get a tool by name."""
    return TOOLS.get(name)


def get_tools_schema() -> list:
    """Get all tool schemas for LLM.
    
    Returns a list of OpenAI function call schemas.
    Each schema is a dict with 'type' and 'function' keys.
    """
    _load_function_tools()
    from src.tools import get_all_tools
    
    # Class-based tools (edit, exec, read, write, image, etc.)
    class_schemas = [tool.get_schema() for tool in TOOLS.values()]
    
    # Function-based tools (Jira, Confluence, GitHub, Git)
    # get_all_tools() already returns proper schema dicts
    function_schemas = get_all_tools()
    
    return class_schemas + function_schemas


async def execute_tool(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name with debug logging."""
    _load_function_tools()
    
    # Debug: Log tool execution
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"=== [TOOL] EXECUTION ===")
        logger.debug(f"Tool: {name}")
        logger.debug(f"Args: {kwargs}")
    
    # Check function-based tools first
    if name in FUNCTION_TOOLS:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Type: function-based")
        
        try:
            result = await FUNCTION_TOOLS[name](**kwargs)
            
            # Debug: Log result
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Result: success={not result.startswith('Error')}")
                if len(str(result)) > 200:
                    logger.debug(f"Result preview: {str(result)[:200]}...")
                else:
                    logger.debug(f"Result: {result}")
            
            return ToolResult(success=not result.startswith("Error"), content=result)
        except Exception as e:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Error: {type(e).__name__}: {e}")
            return ToolResult(success=False, error=str(e))
    
    # Fall back to class-based tools
    tool = TOOLS.get(name)
    if not tool:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Result: Tool not found")
        return ToolResult(success=False, error=f"Tool not found: {name}")

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Type: class-based")
    
    result = await tool.execute(**kwargs)
    
    # Debug: Log result
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Result: success={result.success}")
    
    return result
