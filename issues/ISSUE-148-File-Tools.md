# Add Basic File Operation Tools (read, write, exec)

## Summary
Currently, the Engineering Flow Platform lacks basic file operation tools like `read`, `write`, and `exec`. This limits the agent's ability to work with local files and execute shell commands.

## Problem
When users ask the agent to read or write files, they get a response like:
> "I don't have a file write tool here..."

### Current Available Tools
- GitHub tools (3): `github_get_issue`, `github_search_issues`, `github_add_comment`
- Jira tools (3): `jira_get_issue`, `jira_search`, `jira_add_comment`
- Confluence tools (2): `confluence_get_page`, `confluence_search`
- Git tools (4): `git_status`, `git_commit`, `git_push`, `git_clone`

### Missing Tools
- `read` - Read file contents
- `write` - Create or overwrite files
- `exec` - Execute shell commands

---

## Reference: OpenClaw Implementation

OpenClaw implements these tools using the `@mariozechner/pi-coding-agent` package:

```typescript
import {
  codingTools,
  createEditTool,
  createReadTool,
  createWriteTool,
  readTool,
} from "@mariozechner/pi-coding-agent";
```

### Key Files in OpenClaw
- `/src/agents/pi-tools.ts` - Tool orchestration
- `/src/agents/pi-tools.read.ts` - Read tool implementation
- `/src/agents/bash-tools.exec.ts` - Exec tool implementation (52KB)

GitHub: https://github.com/openclaw/openclaw/tree/main/src/agents

---

## Proposed Implementation

### Recommended: Native Python Implementation

Implement the tools directly in Python for engineering-flow-platform:

```python
# src/tools/file_tools.py

async def read(file_path: str, limit: int = None, offset: int = None) -> str:
    """Read file contents."""
    ...

async def write(file_path: str, content: str) -> str:
    """Create or overwrite file."""
    ...

async def edit(file_path: str, oldText: str, newText: str) -> str:
    """Edit file contents."""
    ...
```

### Tool Schema Examples

#### read Tool
```json
{
  "name": "read",
  "description": "Read file contents",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": { "type": "string", "description": "Path to file" },
      "limit": { "type": "integer", "description": "Max lines" },
      "offset": { "type": "integer", "description": "Start line" }
    },
    "required": ["file_path"]
  }
}
```

#### write Tool
```json
{
  "name": "write",
  "description": "Create or overwrite file",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": { "type": "string", "description": "Path to file" },
      "content": { "type": "string", "description": "Content" }
    },
    "required": ["file_path", "content"]
  }
}
```

#### exec Tool
```json
{
  "name": "exec",
  "description": "Execute shell command",
  "parameters": {
    "type": "object",
    "properties": {
      "command": { "type": "string", "description": "Shell command" },
      "timeout": { "type": "integer", "description": "Timeout in seconds" }
    },
    "required": ["command"]
  }
}
```

---

## Security Considerations

- Path traversal protection
- Command injection prevention
- Timeout limits for exec
- Workspace boundary validation

---

## Files to Modify

- `src/tools/file_tools.py` (new)
- `src/tools/exec_tools.py` (new)
- `src/__init__.py` (register tools)
- `src/agents/core.py` (include in system prompt)

---

## Priority

🔴 High - These are fundamental tools for a coding assistant.
