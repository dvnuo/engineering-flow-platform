"""Shell command execution tools."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default timeout for commands
DEFAULT_TIMEOUT = 60


async def exec(command: str, timeout: int = DEFAULT_TIMEOUT, workdir: Optional[str] = None) -> str:
    """Execute a shell command.
    
    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
    
    Returns:
        Command output (stdout + stderr)
    """
    if not command or not command.strip():
        return "Error: Empty command"
    
    # Validate working directory
    cwd = Path.cwd()
    if workdir:
        workdir_path = Path(workdir).resolve()
        # Only allow working directory under current project
        try:
            workdir_path.relative_to(cwd)
            actual_cwd = workdir_path
        except ValueError:
            # If not under cwd, use cwd
            actual_cwd = cwd
    else:
        actual_cwd = cwd
    
    try:
        # Execute command
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(actual_cwd)
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


def exec_sync(command: str, timeout: int = DEFAULT_TIMEOUT, workdir: Optional[str] = None) -> str:
    """Synchronous wrapper for exec (for simple commands).
    
    Args:
        command: Command to execute
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
    
    Returns:
        Command output
    """
    import subprocess
    
    if not command or not command.strip():
        return "Error: Empty command"
    
    # Validate working directory
    cwd = Path.cwd()
    if workdir:
        workdir_path = Path(workdir).resolve()
        try:
            workdir_path.relative_to(cwd)
            actual_cwd = str(workdir_path)
        except ValueError:
            actual_cwd = str(cwd)
    else:
        actual_cwd = str(cwd)
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=actual_cwd
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
                        "workdir": {"type": "string", "description": "Working directory (optional, defaults to current directory)"}
                    },
                    "required": ["command"]
                }
            }
        },
    ]
