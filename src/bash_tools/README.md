# bash_tools/ - Shell Execution Tools

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
