"""Shell execution tools for LLM agent - discover + run pattern.

Two tools:
- discover_commands: Find available commands on the system
- run_command: Execute a command with workspace restrictions
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.credential_resolver import ToolCredentialEnv

logger = logging.getLogger(__name__)

# Constants
WORKSPACE_ROOT = Path.home() / ".efp" / "workspace"
DEFAULT_TIMEOUT_MS = 15000
MAX_OUTPUT_BYTES = 200000

# Common commands to prioritize (for default discovery)
COMMON_COMMANDS = [
    "ls", "cat", "grep", "rg", "find", "tree", "head", "tail", "wc",
    "git", "gh", "curl", "wget", "ps", "df", "du", "free", "top",
    "sed", "awk", "jq", "yq", "python", "python3", "pip", "npm",
    "cd", "pwd", "mkdir", "rm", "cp", "mv", "chmod", "chown",
    "ssh", "scp", "rsync", "tar", "zip", "unzip",
    "docker", "kubectl", "helm", "terraform",
    "vi", "vim", "nano", "code",
    "echo", "printf", "date", "whoami", "id",
]


def get_workspace_dir() -> str:
    """Get the workspace directory."""
    try:
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        return str(WORKSPACE_ROOT)
    except Exception as e:
        logger.warning(f"Failed to create workspace: {e}")
        return os.getcwd()


def _get_path_dirs() -> List[Path]:
    """Get list of directories in PATH."""
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return [Path(p) for p in path_env.split(":") if p]


async def discover_commands(
    prefix: str = None,
    contains: str = None,
    sources: List[str] = None,
    include_paths: bool = False,
    include_version: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """Discover available commands on the system.
    
    Args:
        prefix: Filter by command prefix (e.g., "gi" -> git, gist)
        contains: Filter by command name contains
        sources: ["path", "builtin", "alias"] - what to search
        include_paths: Include full paths in response
        include_version: Try to get version info (slower)
        limit: Maximum commands to return
    
    Returns:
        Dict with env info and list of commands
    """
    sources = sources or ["path"]
    commands = []
    seen = set()  # Track seen commands across all sources
    
    # A) PATH commands (primary source)
    if "path" in sources:
        path_dirs = _get_path_dirs()
        
        for path_dir in path_dirs:
            if not path_dir.is_dir():
                continue
            try:
                for entry in path_dir.iterdir():
                    name = entry.name
                    if name in seen:
                        continue
                    if not entry.is_file() or not os.access(entry, os.X_OK):
                        continue
                    
                    # Apply filters
                    if prefix and not name.startswith(prefix):
                        continue
                    if contains and contains not in name:
                        continue
                    
                    seen.add(name)
                    cmd_info = {"name": name, "type": "path"}
                    
                    if include_paths:
                        cmd_info["paths"] = [str(entry)]
                    
                    commands.append(cmd_info)
            except PermissionError:
                continue
    
    # B) Builtins (optional)
    if "builtin" in sources:
        try:
            result = await asyncio.to_thread(subprocess.run,
                ["bash", "-lc", "compgen -b"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for name in result.stdout.strip().split("\n"):
                    if name and name not in seen:
                        if prefix and not name.startswith(prefix):
                            continue
                        if contains and contains not in name:
                            continue
                        commands.append({"name": name, "type": "builtin"})
        except Exception as e:
            logger.debug(f"Failed to get builtins: {e}")
    
    # C) Aliases (optional, usually skipped)
    # Skipped by default to avoid leaking personal aliases
    
    # Sort: common commands first, then alphabetical
    common_set = set(COMMON_COMMANDS)
    commands.sort(key=lambda c: (
        0 if c["name"] in common_set else 1,
        c["name"]
    ))
    
    # Capture total before limiting
    total_estimate = len(commands)
    
    # Apply limit
    commands = commands[:limit]
    
    # Get version for selected commands (if requested)
    if include_version and commands:
        for cmd_info in commands[:10]:  # Only first 10 to avoid slow
            name = cmd_info["name"]
            cmd_info["version"] = None  # Default
            try:
                result = await asyncio.to_thread(subprocess.run,
                    [name, "--version"],
                    capture_output=True, text=True, timeout=1
                )
                if result.returncode == 0:
                    # Take first line as version
                    version = result.stdout.strip().split("\n")[0][:100]
                    cmd_info["version"] = version
            except Exception:
                cmd_info["version"] = None
    
    # Build response
    return {
        "env": {
            "os": "linux",
            "shell": "bash",
            "path": [str(p) for p in _get_path_dirs()],
        },
        "result": {
            "total_estimate": total_estimate,
            "returned": len(commands),
            "commands": commands
        }
    }


def _validate_cwd(cwd: str = None) -> str:
    """Validate and resolve working directory."""
    if cwd is None:
        cwd = get_workspace_dir()
    
    # Expand ~ to home directory
    if cwd.startswith("~"):
        cwd = str(Path.home() / cwd[2:].lstrip("/"))
    
    # Resolve path
    resolved = Path(cwd).resolve()
    workspace_resolved = WORKSPACE_ROOT.resolve()
    
    # Check if within workspace
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError:
        # Not in workspace, use workspace root
        logger.warning(f"cwd {cwd} not in workspace, using {WORKSPACE_ROOT}")
        cwd = str(workspace_resolved)
        resolved = Path(cwd).resolve()
    
    # Check if directory exists
    if not resolved.exists():
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"cwd {cwd} cannot be created, using {WORKSPACE_ROOT}")
            cwd = str(workspace_resolved)
    elif not resolved.is_dir():
        logger.warning(f"cwd {cwd} is not a directory, using {WORKSPACE_ROOT}")
        cwd = str(workspace_resolved)
    
    return cwd


def _empty_credential_env():
    from src.runtime.credential_resolver import ToolCredentialEnv

    return ToolCredentialEnv()


def build_env_for_command(cmd: str, args: List[str] = None, cwd: str = None):
    from src.runtime.credential_resolver import build_env_for_command as _build_env_for_command

    return _build_env_for_command(cmd, args=args, cwd=cwd)


def _build_credential_env(cmd: str, args: List[str], cwd: str):
    from src.runtime.credential_resolver import ToolCredentialEnv

    normalized_cmd = (cmd or "").strip()
    if normalized_cmd not in {"git", "gh"}:
        return ToolCredentialEnv()
    return build_env_for_command(normalized_cmd, args=args, cwd=cwd)


async def run_command(
    cmd: str,
    args: List[str] = None,
    cwd: str = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    env: Dict[str, str] = None,
) -> Dict[str, Any]:
    """Execute a shell command with security restrictions.
    
    Args:
        cmd: Command to execute
        args: Command arguments as array (recommended)
        cwd: Working directory (must be in workspace)
        timeout_ms: Timeout in milliseconds
        max_output_bytes: Limit output size
        env: Environment variables (allowlist)
    
    Returns:
        Dict with exit_code, stdout, stderr, duration_ms, truncated
    """
    # Validate args is a list of strings
    if args is not None and not isinstance(args, list):
        return {
            "ok": False,
            "error": "E_BAD_ARGS",
            "exit_code": 1,
            "stdout": "",
            "stderr": "args must be a list of strings",
            "duration_ms": 0,
            "truncated": {"stdout": False, "stderr": False},
        }
    args = args or []
    
    # Validate working directory
    cwd = _validate_cwd(cwd)
    
    # Security: Block dangerous commands
    # Block dangerous commands (direct and via shell wrappers)
    dangerous_cmds = {
        "mkfs", "dd", "fdisk", "parted", "shutdown", "reboot", "halt", "poweroff", "init"
    }
    # Block shell wrappers that can bypass restrictions
    if cmd in dangerous_cmds:
        return {
            "ok": False,
            "error": "E_POLICY_DENY",
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Command '{cmd}' is blocked for safety",
            "duration_ms": 0,
            "truncated": {"stdout": False, "stderr": False},
        }
    # Block shell wrappers - only check when args[0] is a shell flag
    # This allows common commands like "grep -i pattern" but blocks "bash -c"
    # shell_wrapper_cmds = {"bash", "sh", "zsh", "dash", "sudo", "su"}
    shell_wrapper_cmds = {}
    if cmd in shell_wrapper_cmds and args:
        if args[0] in ("-c", "-i", "-l", "--login"):
            return {
                "ok": False,
                "error": "E_POLICY_DENY",
                "exit_code": 1,
                "stdout": "",
                "stderr": "Shell wrapper execution is not allowed",
                "duration_ms": 0,
                "truncated": {"stdout": False, "stderr": False},
            }
    
    # Block absolute paths
    import os
    if os.path.isabs(cmd):
        return {
            "ok": False,
            "error": "E_POLICY_DENY",
            "exit_code": 1,
            "stdout": "",
            "stderr": "Absolute paths are not allowed",
            "duration_ms": 0,
            "truncated": {"stdout": False, "stderr": False},
        }
    
    # Security: Block env PATH override
    allowed_keys = {"LC_ALL", "LANG", "HOME", "USER"}  # No PATH!
    safe_env = os.environ.copy()
    if env:
        for k, v in env.items():
            if k in allowed_keys:
                safe_env[k] = v
    
    credential_env = _empty_credential_env()
    try:
        credential_env = _build_credential_env(cmd, args=args, cwd=cwd)
    except Exception as exc:
        logger.exception("Failed to prepare credential environment for command: %s", cmd)
        return {
            "ok": False,
            "error": "E_CREDENTIAL_ENV_FAILED",
            "exit_code": -1,
            "stdout": "",
            "stderr": credential_env.redact_text(str(exc)),
            "duration_ms": 0,
            "truncated": {"stdout": False, "stderr": False},
        }
    safe_env.update(credential_env.env)

    # Execute
    start_time = asyncio.get_event_loop().time()

    try:
        try:
            process = await asyncio.create_subprocess_exec(
                cmd, *args,
                cwd=cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=safe_env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_ms / 1000
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {
                    "ok": False,
                    "error": "E_TIMEOUT",
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": credential_env.redact_text("Command timed out"),
                    "duration_ms": timeout_ms,
                    "truncated": {"stdout": False, "stderr": False},
                }

            duration_ms = int((asyncio.get_running_loop().time() - start_time) * 1000)

            stdout_full_text = stdout.decode("utf-8", errors="replace")
            stderr_full_text = stderr.decode("utf-8", errors="replace")
            stdout_redacted = credential_env.redact_text(stdout_full_text)
            stderr_redacted = credential_env.redact_text(stderr_full_text)
            stdout_text = stdout_redacted[:max_output_bytes]
            stderr_text = stderr_redacted[:max_output_bytes]

            is_success = process.returncode == 0
            return {
                "ok": is_success,
                "exit_code": process.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "duration_ms": duration_ms,
                "truncated": {
                    "stdout": len(stdout) > max_output_bytes,
                    "stderr": len(stderr) > max_output_bytes,
                },
                "meta": {
                    "cmd": credential_env.redact_text(cmd),
                    "args": credential_env.redact_args(args),
                    "cwd": cwd,
                }
            }
        finally:
            credential_env.cleanup()
    except FileNotFoundError:
        return {
            "ok": False,
            "error": "E_NOT_FOUND",
            "exit_code": 127,
            "stdout": "",
            "stderr": credential_env.redact_text(f"Command not found: {cmd}"),
            "duration_ms": 0,
            "truncated": {"stdout": False, "stderr": False},
        }
    except Exception as e:
        logger.exception(f"run_command failed: {cmd}")
        return {
            "ok": False,
            "error": "E_EXEC_FAILED",
            "exit_code": -1,
            "stdout": "",
            "stderr": credential_env.redact_text(str(e)),
            "duration_ms": 0,
            "truncated": {"stdout": False, "stderr": False},
        }


def get_tools_schemas() -> list:
    """Return tool schemas for LLM."""
    return [
        {
            "type": "function",
            "function": {
                "name": "discover_commands",
                "description": """Discover available commands on the system.

Use this BEFORE running a command if you're unsure what commands exist or their exact names.

**Filters:**
- prefix: Filter by command prefix (e.g., "gi" -> git, gist)
- contains: Filter by name contains (e.g., "docker")
- limit: Max results (default 200)

**Examples:**
- discover_commands(prefix="gi")  # Find git, gist
- discover_commands(contains="docker")  # All docker commands
- discover_commands()  # Get common commands""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {"type": "string", "description": "Command prefix filter"},
                        "contains": {"type": "string", "description": "Command name contains filter"},
                        "limit": {"type": "integer", "description": "Max results (default 200)"}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": """Execute a Linux shell command.

**IMPORTANT:** First use discover_commands if you're unsure what command to use!

**Usage:**
- run_command(cmd="git", args=["status"])
- run_command(cmd="ls", args=["-la"])
- run_command(cmd="grep", args=["-r", "pattern", "."])

**Restrictions:**
- cwd must be within ~/.efp/workspace, if cwd us set, prefer commands relative to cwd, such as `ls -la` instead of `ls -la /path`
- stdin is disabled (no interactive commands)
- Output truncated at 200KB

**Best Practices:**
- Use args array (not shell strings) for safety
- Filter output with head/tail/wc when large
- Check exit_code in response""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "Command to execute"},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command arguments as array"
                        },
                        "cwd": {"type": "string", "description": "Working directory (default: workspace)"},
                        "timeout_ms": {"type": "integer", "description": "Timeout in ms (default 15000)"}
                    },
                    "required": ["cmd"]
                }
            }
        },
    ]


__all__ = [
    "discover_commands",
    "run_command",
    "get_tools_schemas",
    "get_workspace_dir",
]
