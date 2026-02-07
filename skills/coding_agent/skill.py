"""Coding Agent Skill - Run Codex, Claude Code, Pi, or OpenCode agents.

This skill provides tools for running coding agents via bash with PTY mode.
"""

from skills.decorator import SkillResult, skill


def coding_agent(
    command: str = "help",
    agent: str = "codex",
    prompt: str = "",
    mode: str = "full-auto",
    workdir: str = None,
    pty: bool = True,
    background: bool = False,
    timeout: int = 300,
) -> SkillResult:
    """Run a coding agent with PTY mode.
    
    Args:
        command: Action to perform (help, exec, review, batch, monitor)
        agent: Coding agent to use (codex, claude, opencode, pi)
        prompt: Task prompt for the agent
        mode: Execution mode (full-auto, yolo, vanilla)
        workdir: Working directory for the agent
        pty: Use pseudo-terminal (required for coding agents)
        background: Run in background
        timeout: Timeout in seconds
        
    Returns:
        SkillResult with output or error
    """
    import subprocess
    import sys
    from pathlib import Path
    
    if command == "help":
        return SkillResult(
            success=True,
            output=""""| # Coding Agent Commands

## Available Commands

### exec - Run a one-shot task
```
coding_agent command="exec" agent="codex" prompt="Build a REST API" mode="full-auto"
```

### review - Review code/PR
```
coding_agent command="review" agent="codex" prompt="Review the auth module"
```

### batch - Run multiple agents in parallel
```
coding_agent command="batch" agent="codex" prompts='["PR #1", "PR #2"]'
```

### monitor - Monitor running sessions
```
coding_agent command="monitor"
```

## Examples

```python
from skills.coding_agent.skill import coding_agent

# One-shot task
coding_agent(command="exec", agent="codex", prompt="Add tests")

# With workdir
coding_agent(command="exec", agent="claude", prompt="Fix bug", workdir="~/project")

# Background execution
coding_agent(command="exec", agent="codex", prompt="Build API", background=True)
```
""",
        )
    
    elif command == "exec":
        # Build the command
        agent_cmd = _get_agent_command(agent, prompt, mode)
        
        # Build bash command with PTY
        bash_cmd = ["bash"]
        if pty:
            bash_cmd.extend(["-c", f"python3 -c 'import pty; pty.spawn({repr(agent_cmd)})'"])
        else:
            bash_cmd.extend(["-c", " ".join(agent_cmd)])
        
        if workdir:
            bash_cmd.extend(["&&", f"cd {workdir}"])
        
        try:
            if background:
                # Run in background
                proc = subprocess.Popen(
                    bash_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                return SkillResult(
                    success=True,
                    output=f"Started coding agent in background (PID: {proc.pid})",
                    data={"pid": proc.pid, "agent": agent, "mode": mode},
                )
            else:
                # Run with timeout
                result = subprocess.run(
                    bash_cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return SkillResult(
                    success=result.returncode == 0,
                    output=result.stdout or result.stderr,
                    error=result.stderr if result.returncode != 0 else None,
                )
        except subprocess.TimeoutExpired:
            return SkillResult(
                success=False,
                error=f"Agent timed out after {timeout} seconds",
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "review":
        # Build review command
        review_prompt = f"Review the code. {prompt}"
        agent_cmd = _get_agent_command(agent, review_prompt, mode="vanilla")
        
        bash_cmd = ["bash"]
        if pty:
            bash_cmd.extend(["-c", f"python3 -c 'import pty; pty.spawn({repr(agent_cmd)})'"])
        else:
            bash_cmd.extend(["-c", " ".join(agent_cmd)])
        
        try:
            result = subprocess.run(
                bash_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return SkillResult(
                success=result.returncode == 0,
                output=result.stdout or result.stderr,
                error=result.stderr if result.returncode != 0 else None,
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "monitor":
        # Return monitoring guidance
        return SkillResult(
            success=True,
            output="""| # Monitoring Background Sessions

## Process Tool Actions

| Action | Description |
|--------|-------------|
| `list` | List all running sessions |
| `log` | Get session output |
| `poll` | Check if session is running |
| `kill` | Terminate session |

## Examples

```bash
# List running sessions
process action:list

# Get output
process action:log sessionId:XXX

# Check status
process action:poll sessionId:XXX

# Kill session
process action:kill sessionId:XXX
```
""",
        )
    
    elif command == "check":
        # Check if agent is available
        available = _check_agent_available(agent)
        if available:
            return SkillResult(
                success=True,
                output=f"{agent} is available",
                data={"agent": agent, "available": True},
            )
        else:
            return SkillResult(
                success=False,
                error=f"{agent} is not installed or not in PATH",
            )
    
    else:
        return SkillResult(
            success=False,
            error=f"Unknown command: {command}. Use help for available commands.",
        )


def _get_agent_command(agent: str, prompt: str, mode: str) -> list:
    """Get the command for the specified agent."""
    agents = {
        "codex": _codex_command(prompt, mode),
        "claude": _claude_command(prompt),
        "opencode": _opencode_command(prompt),
        "pi": _pi_command(prompt),
    }
    return agents.get(agent.lower(), _codex_command(prompt, mode))


def _codex_command(prompt: str, mode: str) -> list:
    """Build Codex CLI command."""
    cmd = ["codex"]
    
    if mode == "full-auto":
        cmd.extend(["exec", f"--full-auto '{prompt}'"])
    elif mode == "yolo":
        cmd.extend(["exec", f"--yolo '{prompt}'"])
    else:
        cmd.extend(["exec", f"'{prompt}'"])
    
    return cmd


def _claude_command(prompt: str) -> list:
    """Build Claude Code command."""
    return ["claude", prompt]


def _opencode_command(prompt: str) -> list:
    """Build OpenCode command."""
    return ["opencode", "run", f"'{prompt}'"]


def _pi_command(prompt: str) -> list:
    """Build Pi command."""
    return ["pi", f"'{prompt}'"]


def _check_agent_available(agent: str) -> bool:
    """Check if the specified agent is available."""
    import shutil
    agents = {
        "codex": "codex",
        "claude": "claude",
        "opencode": "opencode",
        "pi": "pi",
    }
    binary = agents.get(agent.lower(), agent)
    return shutil.which(binary) is not None


def git_worktree_manager(
    command: str = "help",
    branch: str = None,
    path: str = None,
    base_branch: str = "main",
) -> SkillResult:
    """Manage git worktrees for parallel coding tasks.
    
    Args:
        command: Action (help, create, list, remove, cleanup)
        branch: Branch name for worktree
        path: Path for worktree
        base_branch: Base branch for worktree
        
    Returns:
        SkillResult with output or error
    """
    import subprocess
    
    if command == "help":
        return SkillResult(
            success=True,
            output="""| # Git Worktree Manager

## Commands

### create - Create a new worktree
```
git_worktree_manager command="create" branch="fix/issue-123" path="/tmp/issue-123" base_branch="main"
```

### list - List all worktrees
```
git_worktree_manager command="list"
```

### remove - Remove a worktree
```
git_worktree_manager command="remove" path="/tmp/issue-123"
```

### cleanup - Remove all worktrees in /tmp
```
git_worktree_manager command="cleanup"
```

## Example: Parallel Issue Fixing

```python
from skills.coding_agent.skill import git_worktree_manager, coding_agent

# Create worktrees
git_worktree_manager(command="create", branch="fix/issue-78", path="/tmp/issue-78")
git_worktree_manager(command="create", branch="fix/issue-99", path="/tmp/issue-99")

# Run agents in parallel
coding_agent(command="exec", agent="codex", prompt="Fix issue #78", workdir="/tmp/issue-78", background=True)
coding_agent(command="exec", agent="codex", prompt="Fix issue #99", workdir="/tmp/issue-99", background=True)
```
""",
        )
    
    elif command == "create":
        if not branch or not path:
            return SkillResult(
                success=False,
                error="branch and path are required for create",
            )
        
        try:
            result = subprocess.run(
                ["git", "worktree", "add", "-b", branch, path, base_branch],
                capture_output=True,
                text=True,
            )
            return SkillResult(
                success=result.returncode == 0,
                output=result.stdout or f"Created worktree at {path}",
                error=result.stderr if result.returncode != 0 else None,
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "list":
        try:
            result = subprocess.run(
                ["git", "worktree", "list"],
                capture_output=True,
                text=True,
            )
            return SkillResult(
                success=True,
                output=result.stdout or "No worktrees found",
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "remove":
        if not path:
            return SkillResult(
                success=False,
                error="path is required for remove",
            )
        
        try:
            result = subprocess.run(
                ["git", "worktree", "remove", path],
                capture_output=True,
                text=True,
            )
            return SkillResult(
                success=result.returncode == 0,
                output=result.stdout or f"Removed worktree at {path}",
                error=result.stderr if result.returncode != 0 else None,
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "cleanup":
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            )
            worktrees = [line.split()[-1] for line in result.stdout.strip().split("\n") if line]
            removed = []
            for wt_path in worktrees:
                if wt_path.startswith("/tmp/"):
                    subprocess.run(["git", "worktree", "remove", wt_path], capture_output=True)
                    removed.append(wt_path)
            
            return SkillResult(
                success=True,
                output=f"Removed {len(removed)} worktrees from /tmp",
                data={"removed": removed},
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    else:
        return SkillResult(
            success=False,
            error=f"Unknown command: {command}",
        )
