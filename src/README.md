# src/ - Implementation Code

> All implementation code following OpenClaw architecture

## Directory Structure

```
src/                          # Flat structure (no subdirectories like tools/, integrations/)
├── __init__.py               # Tool exports & utilities (ToolResult, Tool, TOOLS)
│
├── executor/                 # Skill & SubAgent execution engine
│   ├── __init__.py          # SkillsExecutor, execute_skill()
│   ├── subagent.py          # SubAgent spawning & management
│   └── subagent_schemas.py  # SubAgent tool schemas
│
├── git/                     # Git tool implementation
│   └── __init__.py          # git_status(), git_commit(), git_push()
│
├── github/                  # GitHub tool implementation
│   └── __init__.py          # github_get_issue(), github_search_issues()
│
├── jira/                    # Jira tool implementation
│   └── __init__.py          # jira_get_issue(), jira_search()
│
├── confluence/               # Confluence tool implementation
│   └── __init__.py          # confluence_get_page(), confluence_search()
│
└── skill_creator/           # Skill creation tool
    └── scripts/
        ├── init_skill.py
        └── package_skill.py
```

## Architecture Principle

**All implementation code lives in `src/`**

This follows the [OpenClaw](https://github.com/openclaw/openclaw) pattern:
- `skills/` - Declarative SKILL.md only
- `src/` - All implementation code (flat structure)

## Module Exports

### src/__init__.py

Exports all tools and utilities:

```python
from src import (
    # Utilities
    ToolResult,     # Tool execution result
    Tool,           # Base tool class
    TOOLS,          # Tool registry
    
    # Functions
    get_all_tools,      # Get all tool schemas
    get_tool_names,     # Get tool names list
    get_tool,           # Get single tool schema
    get_tools_schema,   # Get schema for LLM
    execute_tool,       # Execute a tool by name
    
    # Tool modules
    git,
    github,
    jira,
    confluence,
)
```

## Tool Structure

Each tool module follows this pattern:

```python
# src/<tool>/__init__.py

from .api import ClientClass

# Global instance (for shared state)
client = ClientClass()

# Tool functions
async def tool_name(param: str) -> str:
    """Tool description."""
    return await client.method(param)

# Schema for LLM
def get_tools_schemas() -> list:
    """Return OpenAI tool schema."""
    return [...]

# Re-export client for advanced use
__all__ = ["client", "tool_name", "get_tools_schemas"]
```

## Example: Git Tool

```python
# src/git/__init__.py

from .api import GitClient, setup_ssh_key, setup_git_user

git_client = GitClient()

async def git_status(workspace: str = ".") -> str:
    """Get git status."""
    return await git_client.status(workspace)

async def git_commit(message: str, workspace: str = ".") -> str:
    """Create a commit."""
    return await git_client.commit(message, workspace)

def get_tools_schemas() -> list:
    """Return git tool schemas."""
    return [...]

__all__ = ["git_client", "git_status", "git_commit", "get_tools_schemas"]
```

## SubAgent System

### src/executor/subagent.py

Manages sub-agent sessions:

```python
from src.executor.subagent import SubAgent, sessions_spawn, sessions_list

# Spawn a sub-agent
result = await sessions_spawn(
    task="Analyze this code",
    model="gpt-4",
    session_key="subagent-123"
)
```

## Tool Execution Flow

```
Agent Request
    ↓
Match Skill (SKILL.md)
    ↓
Call Tool Function (src/<tool>/)
    ↓
Return ToolResult
```

## Adding a New Tool

### 1. Create Tool Module

Create `src/my_tool/__init__.py`:

```python
from .api import MyClient

client = MyClient()

async def my_tool(param: str) -> str:
    """My tool description."""
    return await client.do_something(param)

def get_tools_schemas() -> list:
    """Return tool schema."""
    return [...]

__all__ = ["client", "my_tool", "get_tools_schemas"]
```

### 2. Export from src/__init__.py

```python
from .my_tool import my_tool, get_tools_schemas

__all__ = [
    ...
    "my_tool",
    "get_tools_schemas",
]
```

## Related

- [skills/](../skills/) - Declarative skill definitions
- [OpenClaw src/](https://github.com/openclaw/openclaw/tree/main/src)
