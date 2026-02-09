"""Tools API - File operations and shell execution tools.

遵循项目命名规范:
- api.py 包含所有工具实现
- __init__.py 导出工具函数

Security features inspired by OpenClaw:
https://github.com/openclaw/openclaw/tree/main/src/agents
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from .bash_tools import (
    ExecSecurity,
    ExecAsk,
    ExecSecurityConfig,
    ExecAllowlistEntry,
    evaluate_command,
    validate_environment,
    requires_approval,
    create_default_config,
    DEFAULT_SAFE_BINS,
    DANGEROUS_ENV_VARS,
    DANGEROUS_ENV_PREFIXES,
)

logger = logging.getLogger(__name__)

# Default timeout for commands
DEFAULT_TIMEOUT = 60


# ============ Security Configuration ============

# Global security config (can be overridden)
_security_config: Optional[ExecSecurityConfig] = None


def set_security_config(config: ExecSecurityConfig) -> None:
    """Set the global security configuration."""
    global _security_config
    _security_config = config
    logger.info(f"Security config updated: security={config.security.value}, ask={config.ask.value}")


def get_security_config() -> ExecSecurityConfig:
    """Get the current security configuration."""
    global _security_config
    return _security_config if _security_config is not None else create_default_config()


def reset_security_config() -> None:
    """Reset security config to defaults."""
    global _security_config
    _security_config = None
    logger.info("Security config reset to defaults")


# ============ File Operations ============

def read(file_path: str, limit: Optional[int] = None, offset: Optional[int] = None) -> str:
    """Read file contents.
    
    Args:
        file_path: Path to the file to read
        limit: Maximum number of lines to read (optional)
        offset: Line number to start reading from (optional, 1-indexed)
    
    Returns:
        File contents as string
    """
    path = Path(file_path)
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        start = (offset - 1) if offset else 0
        
        if limit:
            lines = lines[start:start + limit]
        elif offset:
            lines = lines[start:]
        
        content = ''.join(lines)
        
        line_count = len(lines)
        total_lines = len(open(path, 'r').readlines())
        
        header = f"File: {file_path}\nLines: {start + 1}-{start + line_count} of {total_lines}\n\n"
        return header + content
        
    except UnicodeDecodeError:
        return f"Error: Cannot read binary file: {file_path}"
    except FileNotFoundError:
        return f"Error: File not found: {file_path}"
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"


def write(file_path: str, content: str) -> str:
    """Create or overwrite a file.
    
    Args:
        file_path: Path to the file to write
        content: Content to write
    
    Returns:
        Success or error message
    """
    path = Path(file_path)
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"File written: {file_path}")
        return f"✅ File written: {file_path}"
        
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except Exception as e:
        return f"Error writing file: {e}"


def edit(file_path: str, oldText: str, newText: str) -> str:
    """Edit file contents by replacing text.
    
    Args:
        file_path: Path to the file to edit
        oldText: Text to find and replace
        newText: Replacement text
    
    Returns:
        Success or error message
    """
    path = Path(file_path)
    
    if not path.is_file():
        return f"Error: File not found: {file_path}"
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if oldText not in content:
            return f"Error: Text not found in file"
        
        new_content = content.replace(oldText, newText)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        logger.info(f"File edited: {file_path}")
        return f"✅ File edited: {file_path}"
        
    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except Exception as e:
        return f"Error editing file: {e}"


def list_dir(path: str = ".") -> str:
    """List directory contents.
    
    Args:
        path: Directory path (default: current directory)
    
    Returns:
        Directory listing
    """
    dir_path = Path(path)
    
    if not dir_path.is_dir():
        return f"Error: Directory not found: {path}"
    
    try:
        items = []
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                items.append(f"📄 {item.name}")
        
        if not items:
            return f"Directory is empty: {path}"
        
        return f"Directory: {path}\n\n" + "\n".join(items)
        
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error listing directory: {e}"


# ============ Shell Execution ============

async def exec(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    workdir: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    # Security parameters
    security: Optional[str] = None,
    ask: Optional[str] = None,
    host: Optional[str] = None,
) -> str:
    """Execute a shell command with security controls.
    
    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
        env: Environment variables (optional)
        security: Security mode (deny|allowlist|full)
        ask: Approval mode (off|on-miss|always)
        host: Execution host (gateway|sandbox|node)
    
    Returns:
        Command output (stdout + stderr) or error message
    
    Security Modes:
        - deny: Block all commands (default)
        - allowlist: Only allow commands in whitelist
        - full: Allow all commands
    
    Approval Modes:
        - off: No approval needed
        - on-miss: Request approval when not in allowlist
        - always: Always request approval
    
    Examples:
        exec(command="ls -la")
        exec(command="git status", security="allowlist")
        exec(command="pip install requests", security="allowlist", ask="on-miss")
    """
    if not command or not command.strip():
        return "Error: Empty command"
    
    # Build security config from parameters
    config = get_security_config()
    
    if security:
        try:
            config.security = ExecSecurity(security.lower())
        except ValueError:
            return f"Error: Invalid security mode '{security}'. Must be: deny, allowlist, or full"
    
    if ask:
        try:
            config.ask = ExecAsk(ask.lower())
        except ValueError:
            return f"Error: Invalid ask mode '{ask}'. Must be: off, on-miss, or always"
    
    # Validate environment variables
    if env:
        valid, error = validate_environment(env)
        if not valid:
            return f"Error: {error}"
    
    # Get current working directory
    cwd = os.getcwd()
    if workdir:
        workdir_path = Path(workdir).resolve()
        try:
            workdir_path.relative_to(Path.cwd())
            actual_cwd = str(workdir_path)
        except ValueError:
            actual_cwd = str(Path.cwd())
    else:
        actual_cwd = str(Path.cwd())
    
    # Check if command requires approval
    needs_approval, approval_reason = requires_approval(command, config, True, actual_cwd)
    
    if needs_approval:
        return (
            f"⚠️  Command requires approval\n"
            f"Reason: {approval_reason}\n"
            f"Security: {config.security.value}\n"
            f"Approval: {config.ask.value}\n\n"
            f"To approve, configure security settings:\n"
            f"- security=allowlist: Add command to allowlist\n"
            f"- security=full: Allow all commands (use with caution)"
        )
    
    # Evaluate command
    allowed, reason = evaluate_command(command, config, actual_cwd)
    
    if not allowed:
        return (
            f"🚫 Command blocked\n"
            f"Reason: {reason}\n"
            f"Security: {config.security.value}\n\n"
            f"To allow this command:\n"
            f"- Add to security allowlist\n"
            f"- Use security=full (not recommended)"
        )
    
    # Execute command
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    
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
            return f"Error: Command timed out after {timeout} seconds"
        
        output = []
        
        if stdout:
            output.append(stdout.decode('utf-8', errors='replace').strip())
        
        if stderr:
            stderr_text = stderr.decode('utf-8', errors='replace').strip()
            if stderr_text:
                output.append(f"STDERR:\n{stderr_text}")
        
        result = '\n'.join(output)
        
        if process.returncode != 0:
            result = f"Exit code: {process.returncode}\n\n{result}"
        
        logger.info(f"exec: {command} (exit: {process.returncode}, security: {config.security.value})")
        
        return result if result else "(no output)"
        
    except asyncio.CancelledError:
        return "Error: Command cancelled"
    except Exception as e:
        return f"Error executing command: {e}"


def exec_sync(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
    workdir: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    security: Optional[str] = None,
    ask: Optional[str] = None,
) -> str:
    """Synchronous wrapper for exec with security controls.
    
    Args:
        command: Command to execute
        timeout: Timeout in seconds (default: 60)
        workdir: Working directory (optional)
        env: Environment variables (optional)
        security: Security mode (deny|allowlist|full)
        ask: Approval mode (off|on-miss|always)
    
    Returns:
        Command output
    """
    import subprocess
    
    if not command or not command.strip():
        return "Error: Empty command"
    
    # Build security config from parameters
    config = get_security_config()
    
    if security:
        try:
            config.security = ExecSecurity(security.lower())
        except ValueError:
            return f"Error: Invalid security mode '{security}'. Must be: deny, allowlist, or full"
    
    if ask:
        try:
            config.ask = ExecAsk(ask.lower())
        except ValueError:
            return f"Error: Invalid ask mode '{ask}'. Must be: off, on-miss, or always"
    
    # Validate environment variables
    if env:
        valid, error = validate_environment(env)
        if not valid:
            return f"Error: {error}"
    
    # Get current working directory
    actual_cwd = str(Path.cwd())
    if workdir:
        workdir_path = Path(workdir).resolve()
        try:
            workdir_path.relative_to(Path.cwd())
            actual_cwd = str(workdir_path)
        except ValueError:
            actual_cwd = str(Path.cwd())
    
    # Check if command requires approval
    needs_approval, approval_reason = requires_approval(command, config, True, actual_cwd)
    
    if needs_approval:
        return (
            f"⚠️  Command requires approval\n"
            f"Reason: {approval_reason}\n"
            f"Security: {config.security.value}\n"
            f"Approval: {config.ask.value}"
        )
    
    # Evaluate command
    allowed, reason = evaluate_command(command, config, actual_cwd)
    
    if not allowed:
        return (
            f"🚫 Command blocked\n"
            f"Reason: {reason}\n"
            f"Security: {config.security.value}"
        )
    
    # Execute command
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    
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
            output.insert(0, f"Exit code: {result.returncode}")
        
        logger.info(f"exec_sync: {command} (exit: {result.returncode}, security: {config.security.value})")
        
        return '\n'.join(output) if output else "(no output)"
        
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds"
    except Exception as e:
        return f"Error executing command: {e}"


# ============ Tool Schemas ============

def get_tools_schemas() -> list:
    """Return tool schemas for LLM function calling."""
    return [
        # File tools
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read file contents. Shows line numbers and metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to file to read"},
                        "limit": {"type": "integer", "description": "Maximum lines to read (optional)"},
                        "offset": {"type": "integer", "description": "Start line number, 1-indexed (optional)"}
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write",
                "description": "Create or overwrite a file with content.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to file to write"},
                        "content": {"type": "string", "description": "Content to write"}
                    },
                    "required": ["file_path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "edit",
                "description": "Edit file by replacing old text with new text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to file to edit"},
                        "oldText": {"type": "string", "description": "Text to find and replace"},
                        "newText": {"type": "string", "description": "Replacement text"}
                    },
                    "required": ["file_path", "oldText", "newText"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List directory contents (files and folders).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path (default: current directory)"}
                    },
                    "required": ["path"]
                }
            }
        },
        # Exec tool with security
        {
            "type": "function",
            "function": {
                "name": "exec",
                "description": "Execute a shell command with security controls. Default: deny all.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"},
                        "workdir": {"type": "string", "description": "Working directory (optional)"},
                        "env": {"type": "object", "description": "Environment variables (optional)"},
                        "security": {
                            "type": "string",
                            "description": "Security mode: deny|allowlist|full (default: deny)"
                        },
                        "ask": {
                            "type": "string",
                            "description": "Approval mode: off|on-miss|always (default: on-miss)"
                        }
                    },
                    "required": ["command"]
                }
            }
        },
    ]


def get_security_info() -> dict:
    """Get information about the current security configuration."""
    config = get_security_config()
    return {
        "security": config.security.value,
        "ask": config.ask.value,
        "safe_bins": config.safe_bins,
        "allowlist_count": len(config.allowlist),
        "dangerous_env_vars": list(DANGEROUS_ENV_VARS),
        "dangerous_env_prefixes": DANGEROUS_ENV_PREFIXES,
    }


# Re-export security functions for external use
__all__ = [
    # File tools
    "read",
    "write",
    "edit",
    "list_dir",
    # Exec tools
    "exec",
    "exec_sync",
    # Tool schemas
    "get_tools_schemas",
    # Security functions
    "get_security_info",
    "set_security_config",
    "get_security_config",
    "reset_security_config",
    "validate_environment",
    # Security types
    "ExecSecurity",
    "ExecAsk",
    "ExecSecurityConfig",
    "ExecAllowlistEntry",
    "DEFAULT_SAFE_BINS",
    "DANGEROUS_ENV_VARS",
    "DANGEROUS_ENV_PREFIXES",
]
