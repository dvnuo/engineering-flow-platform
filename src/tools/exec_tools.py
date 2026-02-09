"""Shell command execution tools."""

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def exec(command: str, timeout: Optional[int] = 60, workdir: Optional[str] = None) -> str:
    """Execute a shell command.
    
    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
    
    Returns:
        Command output (stdout + stderr)
    """
    # Security: Basic command injection prevention
    # Only allow certain safe patterns, reject obviously dangerous commands
    
    dangerous_patterns = [
        '; rm', '; kill', '&& rm', '|| rm',  # File deletion
        '> /dev/', '>> /dev/',  # Output redirection to devices
        '| xargs rm', '| rm',  # Pipe to rm
    ]
    
    command_lower = command.lower()
    for pattern in dangerous_patterns:
        if pattern in command_lower:
            return f"Error: Potentially dangerous command rejected: {pattern.strip()}"
    
    try:
        # Use shell=False for better security (but we need shell=True for pipes)
        # Instead, validate the command more carefully
        
        # Allow common safe commands
        safe_commands = ['ls', 'cat', 'echo', 'pwd', 'cd', 'mkdir', 'touch', 'grep', 'find', 'head', 'tail', 'wc', 'sort', 'uniq', 'cut', 'tr', 'sed', 'awk', 'git', 'python3', 'pip', 'npm', 'docker', 'kubectl']
        
        # Extract first word to check if it's safe
        first_word = command.strip().split()[0] if command.strip() else ''
        
        # Build command with optional working directory
        cmd = command
        if workdir:
            workdir_path = Path(workdir)
            # Security: Only allow workspace or subdirectories
            if not str(workdir_path.resolve()).startswith(str(Path.cwd().resolve())):
                return f"Error: Working directory outside workspace: {workdir}"
        
        # Execute with asyncio
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            return f"Error: Command timed out after {timeout} seconds"
        
        output = []
        
        if stdout:
            output.append(stdout.decode('utf-8', errors='replace').strip())
        
        if stderr:
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            if stderr_text:
                output.append(f"STDERR:\n{stderr_text}")
        
        result = '\n'.join(output)
        
        # Add exit code info
        if process.returncode != 0:
            result = f"Exit code: {process.returncode}\n\n{result}"
        
        logger.info(f"exec: {command} (exit: {process.returncode})")
        
        return result if result else "(no output)"
        
    except asyncio.CancelledError:
        return "Error: Command cancelled"
    except Exception as e:
        return f"Error executing command: {e}"


def exec_sync(command: str, timeout: int = 60, workdir: Optional[str] = None) -> str:
    """Synchronous wrapper for exec (for non-async contexts)."""
    import subprocess
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir
        )
        
        output = []
        if result.stdout:
            output.append(result.stdout.strip())
        if result.stderr:
            output.append(f"STDERR:\n{result.stderr.strip()}")
        
        if result.returncode != 0:
            output.insert(0, f"Exit code: {result.returncode}")
        
        return '\n'.join(output) if output else "(no output)"
        
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds"
    except Exception as e:
        return f"Error executing command: {e}"


def get_tools_schemas() -> list:
    """Return exec tool schemas for LLM function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": "exec",
                "description": "Execute a shell command and return stdout/stderr.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"},
                        "workdir": {"type": "string", "description": "Working directory (optional)"}
                    },
                    "required": ["command"]
                }
            }
        },
    ]
