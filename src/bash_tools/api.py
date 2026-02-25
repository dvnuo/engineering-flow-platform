"""Shell execution tools for LLM agent.

Only one tool: exec - Agent can use any Linux CLI command directly.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from .bash_tools import (
    ExecSecurity,
    ExecSecurityConfig,
    evaluate_command,
    validate_environment,
    create_default_config,
    DEFAULT_SAFE_BINS,
    DANGEROUS_ENV_VARS,
    DANGEROUS_ENV_PREFIXES,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60

# Cache for skill workdir getter (lazy import to avoid circular imports)
_skill_workdir_getter = None


def _get_skill_workdir_getter():
    """Get the skill workdir getter function (lazy import to avoid circular imports)."""
    global _skill_workdir_getter
    if _skill_workdir_getter is None:
        try:
            from src.agents.core import get_skill_workdir
            _skill_workdir_getter = get_skill_workdir
        except ImportError:
            _skill_workdir_getter = lambda: None
    return _skill_workdir_getter


# ============ Security Configuration ============

_security_config: Optional[ExecSecurityConfig] = None


def set_security_config(config: ExecSecurityConfig) -> None:
    """Set the global security configuration."""
    global _security_config
    _security_config = config
    logger.info(f"Security config: {config.security.value}")


def get_security_config() -> ExecSecurityConfig:
    """Get the current security configuration."""
    global _security_config
    return _security_config if _security_config is not None else create_default_config()


# ============ Shell Execution ============

async def exec(command: str, args: list = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Execute a shell command.
    
    Args:
        command: Shell command to execute (or command name if args provided)
        args: Optional list of arguments for safe execution (recommended)
              Example: command="gh", args=["pr", "edit", "235", "--body", "new description"]
        timeout: Timeout in seconds (default: 60)
    
    Returns:
        Command output or error message
    """
    if not command or not command.strip():
        return "Error: Empty command"
    
    config = get_security_config()
    
    # Get working directory - prefer skill workdir if set (async-safe via contextvars)
    actual_cwd = str(Path.cwd())
    skill_workdir_getter = _get_skill_workdir_getter()
    if skill_workdir_getter:
        skill_workdir = skill_workdir_getter()
        if skill_workdir:
            skill_path = Path(skill_workdir)
            if skill_path.exists() and skill_path.is_dir():
                actual_cwd = skill_workdir
                logger.debug(f"[exec] Using skill dir: {actual_cwd}")
            else:
                logger.debug(f"[exec] Skill dir not found: {skill_workdir}")
    
    # Use args array if provided (safer)
    if args and isinstance(args, list):
        # Build command from command + args
        full_command = [command] + args
        command_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in full_command)
        
        allowed, reason = evaluate_command(command_str, config, actual_cwd)
        if not allowed:
            return f"Blocked: {reason}\n\nTo allow: security=full"
        
        merged_env = os.environ.copy()
        
        try:
            process = await asyncio.create_subprocess_exec(
                *full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=actual_cwd,
                env=merged_env
            )
        except FileNotFoundError:
            return f"Error: Command not found: {command}"
        except Exception as e:
            return f"Error: {e}"
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            return f"Error: Timeout after {timeout}s"
        
        output = []
        if stdout:
            output.append(stdout.decode('utf-8', errors='replace').strip())
        if stderr:
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            if stderr_text and "Warning:" not in stderr_text:
                output.append(f"STDERR:\n{stderr_text}")
        
        result = '\n'.join(output)
        
        if process.returncode != 0:
            result = f"Exit: {process.returncode}\n\n{result}"
        
        return result if result else "(no output)"
    
    # Fallback: use shell command string
    allowed, reason = evaluate_command(command, config, actual_cwd)
    
    if not allowed:
        return f"Blocked: {reason}\n\nTo allow: security=full"
    
    merged_env = os.environ.copy()
    
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=actual_cwd,
            env=merged_env
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            return f"Error: Timeout after {timeout}s"
        
        output = []
        if stdout:
            output.append(stdout.decode('utf-8', errors='replace').strip())
        if stderr:
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            if stderr_text and "Warning:" not in stderr_text:
                output.append(f"STDERR:\n{stderr_text}")
        
        result = '\n'.join(output)
        
        if process.returncode != 0:
            result = f"Exit: {process.returncode}\n\n{result}"
        
        return result if result else "(no output)"
        
    except Exception as e:
        return f"Error: {e}"


def exec_sync(command: str, args: list = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Synchronous exec wrapper.
    
    Args:
        command: Shell command to execute (or command name if args provided)
        args: Optional list of arguments for safe execution (recommended)
        timeout: Timeout in seconds
    """
    import subprocess
    
    if not command or not command.strip():
        return "Error: Empty command"
    
    config = get_security_config()
    actual_cwd = str(Path.cwd())
    
    # Use args array if provided (safer)
    if args and isinstance(args, list):
        # Build command from command + args
        full_command = [command] + args
        command_str = " ".join(f'"{arg}"' if " " in arg else arg for arg in full_command)
        
        allowed, reason = evaluate_command(command_str, config, actual_cwd)
        if not allowed:
            return f"Blocked: {reason}"
        
        merged_env = os.environ.copy()
        
        try:
            result = subprocess.run(
                full_command,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=actual_cwd,
                env=merged_env
            )
        except FileNotFoundError:
            return f"Error: Command not found: {command}"
        except subprocess.TimeoutExpired:
            return f"Error: Timeout after {timeout}s"
        except Exception as e:
            return f"Error: {e}"
        
        output = []
        if result.stdout:
            output.append(result.stdout.strip())
        if result.stderr:
            output.append(f"STDERR:\n{result.stderr.strip()}")
        
        if result.returncode != 0:
            output.insert(0, f"Exit: {result.returncode}")
        
        return '\n'.join(output) if output else "(no output)"
    
    # Fallback: use shell command string
    allowed, reason = evaluate_command(command, config, actual_cwd)
    
    if not allowed:
        return f"Blocked: {reason}"
    
    merged_env = os.environ.copy()
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=actual_cwd,
            env=merged_env
        )
        
        output = []
        if result.stdout:
            output.append(result.stdout.strip())
        if result.stderr:
            output.append(f"STDERR:\n{result.stderr.strip()}")
        
        if result.returncode != 0:
            output.insert(0, f"Exit: {result.returncode}")
        
        return '\n'.join(output) if output else "(no output)"
        
    except subprocess.TimeoutExpired:
        return f"Error: Timeout after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


# ============ Tool Schema ============

def get_tools_schemas() -> list:
    """Return tool schema for LLM.
    
    Only one tool: exec - Agent uses Linux CLI directly.
    
    Use 'args' array for safe execution (recommended):
      exec(command="gh", args=["pr", "edit", "235", "--body", "new description"])
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "exec",
                "description": """Execute a Linux shell command.

**Safe Execution (Recommended):** Use command + args array
- exec(command="gh", args=["pr", "edit", "235", "--body", "new description"])
- exec(command="git", args=["commit", "-m", "fix: bug fix"])
- exec(command="python", args=["-m", "http.server", "8080"])

**Legacy (shell string):**
- exec(command="gh pr edit 235 --body 'new description'")

**File:**
- cat file, head -n 20 file, tail -n 10 file
- echo "text" > file, cat > file <<EOF
- sed -i 's/old/new/g' file, awk '{print $1}' file

**Dir:**
- ls -la, find . -name "*.py", tree
- cd dir, pwd, mkdir -p dir, rm -rf dir

**Git:**
- git status, git add ., git commit -m "msg", git push
- git log --oneline -10, git diff, git checkout -b branch

**GitHub (gh):**
- gh repo view, gh issue list, gh pr list
- gh pr view 123, gh pr checkout 123
- **Supports GitHub Enterprise** - uses config from ~/gh/hosts.yml

**Search:**
- grep -r "pattern" ., rg "pattern" --type py
- jq '.key' file.json, yq '.key' file.yaml

**System:**
- ps aux, df -h, free -h, curl, wget""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute (e.g., gh, git, python)"},
                        "args": {
                            "type": "array", 
                            "items": {"type": "string"},
                            "description": "Command arguments as array (recommended for safety)"
                        },
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"}
                    },
                    "required": ["command"]
                }
            }
        },
    ]


__all__ = [
    "exec",
    "exec_sync",
    "get_tools_schemas",
    "set_security_config",
    "get_security_config",
    "DEFAULT_SAFE_BINS",
]
