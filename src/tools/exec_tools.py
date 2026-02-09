"""Shell command execution tools."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Security: Define allowed workspace directory
ALLOWED_WORKSPACE = Path.cwd().resolve()

# Security: Dangerous patterns to block
DANGEROUS_PATTERNS = [
    # Command separators that allow chaining
    ';', '&&', '||', '|', '\n', '\r',
    # Redirection
    '>', '>>', '<', '<<',
    # Command substitution
    '$(', '`',  # Backtick
    # Environment variable (block command substitution)
    '$',
    # File deletion commands
    'rm', 'del', 'erase', 'unlink', 'shred', 'mkfs',
    # Process operations
    'kill', 'pkill', 'xkill',
    # Network operations
    'wget', 'curl', 'nc', 'netcat', 'ssh', 'scp',
    # Sudo
    'sudo', 'su ',
]


def _validate_command(command: str) -> tuple[bool, str]:
    """Validate command for security.
    
    Returns:
        (is_valid, error_message)
    """
    if not command or not command.strip():
        return False, "Empty command"
    
    command_lower = command.lower().strip()
    
    # Check for dangerous patterns
    for pattern in DANGEROUS_PATTERNS:
        if pattern in command_lower:
            # Special handling for $ (environment variable)
            if pattern == '$':
                # Allow $ in simple cases like $PATH but block command substitution
                if '$(' in command or '`' in command:
                    return False, f"Dangerous pattern detected: {pattern}"
                continue
            return False, f"Dangerous pattern detected: {pattern}"
    
    # Check for suspicious patterns
    suspicious = [
        '..',  # Path traversal
        '/etc/', '/root/', '/bin/', '/usr/', '/sbin/',  # System directories
        'cp /', 'mv /', 'ln -',  # Destructive file ops
    ]
    
    for pattern in suspicious:
        if pattern in command_lower:
            return False, f"Suspicious pattern detected: {pattern}"
    
    return True, ""


async def exec(command: str, timeout: Optional[int] = 60, workdir: Optional[str] = None) -> str:
    """Execute a shell command.
    
    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
    
    Returns:
        Command output (stdout + stderr)
    """
    # Validate command
    is_valid, error = _validate_command(command)
    if not is_valid:
        return f"Error: {error}"
    
    # Validate working directory
    if workdir:
        workdir_path = Path(workdir).resolve()
        try:
            workdir_path.relative_to(ALLOWED_WORKSPACE)
        except ValueError:
            return f"Error: Working directory outside workspace: {workdir}"
    else:
        workdir_path = ALLOWED_WORKSPACE
    
    try:
        # Use shell=True for command with pipes/redirects, but command is validated
        # Split command into program and arguments for safer execution
        parts = command.strip().split()
        
        if not parts:
            return "Error: Empty command"
        
        program = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        
        # Execute with asyncio
        process = await asyncio.create_subprocess_exec(
            program,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir_path)
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
    except FileNotFoundError:
        return f"Error: Command not found: {command.split()[0]}"
    except Exception as e:
        return f"Error executing command: {e}"


def exec_sync(command: str, timeout: int = 60, workdir: Optional[str] = None) -> str:
    """Synchronous wrapper for exec (for simple commands).
    
    Note: For commands with pipes/redirections, use async exec() instead.
    
    Args:
        command: Command to execute (simple commands only)
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
    
    Returns:
        Command output
    """
    import subprocess
    
    # Validate command
    is_valid, error = _validate_command(command)
    if not is_valid:
        return f"Error: {error}"
    
    # Validate working directory
    if workdir:
        workdir_path = Path(workdir).resolve()
        try:
            workdir_path.relative_to(ALLOWED_WORKSPACE)
        except ValueError:
            return f"Error: Working directory outside workspace: {workdir}"
        workdir = str(workdir_path)
    
    try:
        # Split command for safer execution (no shell=True)
        parts = command.strip().split()
        if not parts:
            return "Error: Empty command"
        
        program = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        
        result = subprocess.run(
            [program] + args,
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
    except FileNotFoundError:
        return f"Error: Command not found: {command.split()[0]}"
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
