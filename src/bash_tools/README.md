# bash_tools/ - Shell Execution Tools

## Overview

The Bash Tools module provides shell command execution capabilities for the agent using a discover + run pattern.

## Structure

```
bash_tools/
├── api.py           # Shell command execution
├── bash_tools.py    # Bash tool implementations
└── __init__.py      # Module exports
```

## Components

### Shell Execution (`api.py`)
Two tools for the agent:
- `discover_commands` - Discover available commands on the system
- `run_command` - Execute shell commands with security restrictions

### Security Features
- Working directory limited to `~/.efp/workspace`
- Dangerous commands blocked (rm, mkfs, dd, fdisk, etc.)
- Absolute path execution blocked
- Environment variable allowlist (no PATH override)
- Output size limits (200KB max)

## Quick Start

```python
from src.bash_tools.api import discover_commands, run_command

# Discover available commands
result = await discover_commands(prefix="git")
print(result)

# Run a command
result = await run_command(cmd="ls", args=["-la"])
print(result)
```

## Tools

### discover_commands

Discover available commands on the system.

```python
await discover_commands(
    prefix="gi",        # Filter by prefix (e.g., git, gist)
    contains="docker",  # Filter by name contains
    limit=200,          # Max results
)
```

### run_command

Execute a shell command with security restrictions.

```python
await run_command(
    cmd="git",
    args=["status"],
    cwd="/home/<runtime-user>/.efp/workspace",  # Must be in the runtime user's workspace
    timeout_ms=15000,
)
```

## Response Format

### discover_commands response:
```json
{
  "env": {"os": "linux", "shell": "bash", "path": [...]},
  "result": {
    "total_estimate": 7,
    "returned": 7,
    "commands": [
      {"name": "git", "type": "path"},
      {"name": "git-receive-pack", "type": "path"}
    ]
  }
}
```

### run_command response:
```json
{
  "ok": true,
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "duration_ms": 123,
  "truncated": {"stdout": false, "stderr": false}
}
```

## Error Codes

| Code | Description |
|------|-------------|
| E_POLICY_DENY | Command blocked by security policy |
| E_NOT_FOUND | Command not found |
| E_TIMEOUT | Command timed out |
| E_EXEC_FAILED | Execution failed |

## Configuration

No configuration required - security is built into the tools.

## Dependencies

- Standard library: `asyncio`, `os`, `pathlib`, `logging`
- No external dependencies
