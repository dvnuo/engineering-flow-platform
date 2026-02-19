# bash_tools/ - Shell Execution Tools

## Overview

The Bash Tools module provides shell command execution capabilities for the agent. It implements a security model with ALLOWLIST mode to control which commands can be executed.

## Structure

```
bash_tools/
├── api.py           # Shell command execution
├── bash_tools.py    # Bash tool implementations
└── __init__.py      # Module exports
```

## Components

### Shell Execution (`api.py`)
- Single `exec` tool for agent to run any Linux CLI command
- Security model: ALLOWLIST mode (default) with configurable bins
- Supports timeout, cwd override, and async execution

### Bash Tools (`bash_tools.py`)
- Built-in bash utility tools (cat, echo, ls, etc.)
- Sandboxed execution with security checks

## Quick Start

```python
from src.bash_tools.api import exec

# Execute a simple command
result = await exec("pwd")
print(result)  # Shows current working directory
```

## Configuration

```yaml
# In config.yaml
bash_tools:
  security: "allowlist"  # or "full" for unrestricted
  safe_bins:
    - cat
    - echo
    - ls
    - grep
    - find
```

## Dependencies

- Standard library: `asyncio`, `os`, `pathlib`, `logging`
- No external dependencies

## Development Guide

### Adding New Safe Commands

Edit `safe_bins` in bash_tools configuration or add to `DEFAULT_SAFE_BINS`:

```python
DEFAULT_SAFE_BINS = {
    'cat', 'echo', 'ls', 'grep', 'find', 'sed', 'awk',
    'head', 'tail', 'wc', 'sort', 'uniq', 'cut',
    # Add new commands here
}
```

### Security Modes

| Mode | Description |
|------|-------------|
| `allowlist` | Only commands in safe_bins (default) |
| `full` | Allow all commands (not recommended) |

### Best Practices

- Keep ALLOWLIST mode enabled in production
- Add only necessary commands to safe_bins
- Use skill-specific workdir for file operations
- Set appropriate timeouts for long-running commands
