# Tools Directory

## Directory Structure

```
tools/
├── __init__.py
├── subagent.py              # Sub-agent spawning and management
├── IMPLEMENTATION.md        # Implementation documentation
├── exec.py                  # Shell execution wrapper
├── process.py               # Process management
├── (other tool modules)
```

## How It Works

### 1. SubAgent System
```python
# tools/subagent.py

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor

@dataclass
class SubAgent:
    """Represents a spawned sub-agent."""
    agent_id: str
    task: str
    model: str = "gpt-4"
    session_key: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None

class SubAgentManager:
    """Manages sub-agent lifecycle."""
    
    def __init__(self, max_agents: int = 10):
        self.max_agents = max_agents
        self.agents: Dict[str, SubAgent] = {}
        self.executor = ThreadPoolExecutor(max_workers=max_agents)
    
    def spawn(self, task: str, model: str = "gpt-4") -> SubAgent:
        """Spawn a new sub-agent."""
        agent = SubAgent(
            agent_id=str(uuid.uuid4()),
            task=task,
            model=model
        )
        self.agents[agent.agent_id] = agent
        return agent
    
    def send(self, agent_id: str, message: str) -> bool:
        """Send message to sub-agent."""
        ...
    
    def terminate(self, agent_id: str) -> bool:
        """Terminate sub-agent."""
        ...
    
    def list_agents(self, status: str = None) -> List[SubAgent]:
        """List all agents, optionally filtered by status."""
        ...
```

### 2. Process Management
```python
# tools/process.py

import subprocess
import asyncio
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class ProcessResult:
    """Result of a process execution."""
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int

class ProcessManager:
    """Manage subprocess executions."""
    
    def __init__(self, timeout: int = 300, shell: bool = True):
        self.timeout = timeout
        self.shell = shell
        self.active_processes: Dict[str, subprocess.Popen] = {}
    
    def run(
        self,
        command: str,
        input: str = None,
        env: Dict[str, str] = None,
        workdir: str = None,
        timeout: int = None
    ) -> ProcessResult:
        """Run a command and wait for completion."""
        ...
    
    def spawn(
        self,
        command: str,
        env: Dict[str, str] = None,
        workdir: str = None
    ) -> str:
        """Spawn a background process. Returns process ID."""
        ...
    
    def kill(self, process_id: str) -> bool:
        """Kill a running process."""
        ...
    
    def get_output(self, process_id: str, timeout: int = 10) -> str:
        """Get process output."""
        ...
```

### 3. Shell Execution
```python
# tools/exec.py

from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class ExecConfig:
    """Shell execution configuration."""
    command: str
    workdir: str = None
    env: Dict[str, str] = None
    timeout: int = 300
    pty: bool = False
    background: bool = False
    capture_output: bool = True
    shell: bool = True

class ShellExecutor:
    """Execute shell commands with various options."""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
    
    def execute(self, config: ExecConfig) -> Dict[str, Any]:
        """Execute a shell command."""
        ...
    
    def execute_pty(self, command: str, workdir: str = None) -> Dict[str, Any]:
        """Execute with pseudo-terminal (required for interactive apps)."""
        ...
    
    def background(self, command: str, workdir: str = None) -> str:
        """Execute in background. Returns process ID."""
        ...
```

## What Problems It Solves

- **Sub-agent Management**: Spawn and communicate with independent agent sessions
- **Shell Execution**: Safe command execution with PTY support
- **Process Monitoring**: Track and control background processes
- **Resource Management**: Limit concurrent processes
- **Error Isolation**: Failures in tools don't crash main agent

## Configuration Options

### Core Tools Configuration (config.yaml)

```yaml
# config.yaml
tools:
  # Sub-agent settings
  subagent:
    enabled: true
    max_agents: 10
    default_model: "gpt-4"
    session_timeout: 3600      # seconds
    cleanup_interval: 300       # seconds
    idle_timeout: 1800         # seconds before cleanup
    models:
      primary: "gpt-4"
      fallback: "gpt-3.5-turbo"
      fast: "gpt-3.5-turbo"
  
  # Shell execution settings
  exec:
    default_timeout: 300       # seconds
    max_timeout: 3600         # maximum allowed timeout
    shell_enabled: true
    pty:
      enabled: true
      rows: 24
      cols: 80
    workdir:
      default: "/tmp"
      allowed:                 # Whitelist of allowed directories
        - "/tmp"
        - "/root"
        - "/home"
      blocked:                  # Blocked directories
        - "/etc"
        - "/bin"
        - "/usr/bin"
    env:
      allowed:                  # Whitelist of env vars
        - "PATH"
        - "HOME"
        - "USER"
      blocked:                  # Blocked env vars
        - "API_KEY*"
        - "SECRET*"
  
  # Process management
  process:
    max_processes: 50
    poll_interval: 1           # seconds
    cleanup_on_exit: true
    kill_timeout: 10          # seconds before force kill
  
  # Security settings
  security:
    sandbox_enabled: false
    allowed_commands:
      - "git"
      - "docker"
      - "python"
      - "pip"
      - "npm"
    blocked_commands:
      - "rm -rf /*"
      - "mkfs"
      - "dd if=/dev/zero"
    command_whitelist: false   # If true, only allow allowed_commands
```

### Per-Tool Configuration

```yaml
# Sub-agent specific
tools:
  subagent:
    # Memory limits
    memory_limit: "4GB"
    cpu_limit: "2"
    
    # Network
    network_access: true
    allowed_hosts:
      - "api.openai.com"
      - "api.anthropic.com"
    
    # Capabilities
    capabilities:
      - "read"
      - "write"
      - "execute"
      - "network"
    
    # Resource isolation
    isolation:
      type: "docker"           # docker, none
      image: "python:3.11-slim"
      network_policy: "restricted"
```

### Environment Variables

```bash
# Sub-agent
SUBAGENT_DEFAULT_MODEL=gpt-4
SUBAGENT_MAX_AGENTS=10

# Execution
EXEC_DEFAULT_TIMEOUT=300
EXEC_PTY_ENABLED=true

# Security
SANDBOX_ENABLED=false
ALLOWED_COMMANDS=git,docker,python
```

## How to Run

### Test Tools
```bash
# Test subagent functionality
pytest tests/test_subagent.py -v

# Test shell execution
pytest tests/test_exec.py -v

# Test process management
pytest tests/test_process.py -v

# Run all tool tests
pytest tests/test_tools*.py -v
```

### Manual Tool Usage

```python
from tools.subagent import SubAgentManager

# Spawn a sub-agent
manager = SubAgentManager()
agent = manager.spawn("Analyze this code", model="gpt-4")

# Send message
manager.send(agent.agent_id, "Please analyze the code in /path/to/file")

# Get result
result = manager.get_result(agent.agent_id)

# Terminate
manager.terminate(agent.agent_id)
```

```python
from tools.exec import ShellExecutor

executor = ShellExecutor()

# Simple command
result = executor.execute("echo 'Hello World'")

# With PTY (for interactive apps)
result = executor.execute_pty("codex exec 'Build a REST API'", workdir="/tmp")

# Background execution
pid = executor.background("long-running-task")
```

## Development Principles

### 1. Tool Pattern
```python
# tools/my_tool.py

from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None
    data: Dict[str, Any] = None

class MyTool:
    """Description of my tool."""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
    
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters."""
        try:
            result = self._do_execution(**kwargs)
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
    
    def _do_execution(self, **kwargs) -> str:
        """Internal execution logic."""
        ...
```

### 2. Error Handling
```python
class ToolError(Exception):
    """Base tool error."""
    pass

class CommandNotAllowedError(ToolError):
    """Command is not in whitelist."""
    pass

class TimeoutError(ToolError):
    """Execution timed out."""
    pass

class ProcessError(ToolError):
    """Process exited with non-zero code."""
    def __init__(self, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr
```

### 3. Security Considerations
```python
# Always validate commands
ALLOWED_COMMANDS = {"git", "docker", "python"}

def safe_execute(command: str) -> str:
    cmd = command.split()[0]
    if cmd not in ALLOWED_COMMANDS:
        raise CommandNotAllowedError(f"Command {cmd} not allowed")
    
    # Sanitize input
    sanitized = sanitize_input(command)
    
    # Execute in sandbox
    return sandbox_execute(sanitized)
```

### 4. Testing Standards
```python
class TestMyTool:
    def test_execute_success(self):
        """Test successful execution."""
        tool = MyTool()
        result = tool.execute(param="value")
        assert result.success is True
        assert "expected" in result.output
    
    def test_execute_failure(self):
        """Test error handling."""
        tool = MyTool()
        result = tool.execute(param="invalid")
        assert result.success is False
        assert result.error is not None
    
    def test_sandbox(self):
        """Test sandbox isolation."""
        ...
```

## API Reference

### SubAgentManager (tools/subagent.py)

```python
class SubAgentManager:
    """Manages sub-agent lifecycle."""
    
    def spawn(self, task: str, model: str = "gpt-4") -> SubAgent:
        """Spawn a new sub-agent."""
        ...
    
    def send(self, agent_id: str, message: str) -> bool:
        """Send message to running agent."""
        ...
    
    def receive(self, agent_id: str, timeout: int = 30) -> str:
        """Receive message from agent."""
        ...
    
    def terminate(self, agent_id: str) -> bool:
        """Terminate a sub-agent."""
        ...
    
    def list(self, status: str = None) -> List[SubAgent]:
        """List all agents."""
        ...
    
    def get_status(self, agent_id: str) -> Dict[str, Any]:
        """Get agent status."""
        ...
```

### ShellExecutor (tools/exec.py)

```python
class ShellExecutor:
    """Execute shell commands."""
    
    def execute(
        self,
        command: str,
        workdir: str = None,
        env: Dict[str, str] = None,
        timeout: int = None,
        capture_output: bool = True
    ) -> ExecResult:
        """Execute command."""
        ...
    
    def execute_pty(
        self,
        command: str,
        workdir: str = None,
        timeout: int = None
    ) -> ExecResult:
        """Execute with pseudo-terminal."""
        ...
    
    def background(
        self,
        command: str,
        workdir: str = None
    ) -> str:
        """Execute in background. Returns process ID."""
        ...
```

## Troubleshooting

### Sub-agent Not Responding
```python
from tools.subagent import SubAgentManager

manager = SubAgentManager()

# Check agent status
status = manager.get_status(agent_id)
print(status)

# Force terminate if stuck
manager.terminate(agent_id)
```

### Shell Command Failures
```python
from tools.exec import ShellExecutor

executor = ShellExecutor()

# Execute with full output
result = executor.execute(
    command="git status",
    capture_output=True,
    timeout=30
)

print(f"Return code: {result.returncode}")
print(f"Stdout: {result.stdout}")
print(f"Stderr: {result.stderr}")
```

### Process Not Ending
```bash
# List running processes
ps aux | grep python

# Kill stuck process
kill -9 <PID>

# Or use process manager
from tools.process import ProcessManager
manager = ProcessManager()
manager.kill(process_id)
```
