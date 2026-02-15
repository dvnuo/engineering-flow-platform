"""Tools module - File operations and shell execution tools."""

from .api import (
    exec,
    exec_sync,
    get_tools_schemas,
    get_security_info,
    set_security_config,
    get_security_config,
    reset_security_config,
    validate_environment,
    ExecSecurity,
    ExecAsk,
    ExecSecurityConfig,
    ExecAllowlistEntry,
    DEFAULT_SAFE_BINS,
    DANGEROUS_ENV_VARS,
    DANGEROUS_ENV_PREFIXES,
)


def get_all_tools() -> list:
    """Get all tool schemas."""
    return get_tools_schemas()


def execute_tool(name: str, **kwargs) -> str:
    """Execute a tool by name.
    
    All file operations map to shell commands under the hood.
    """
    command = ""
    
    if name == "read":
        file_path = kwargs.get("file_path", "")
        limit = kwargs.get("limit")
        offset = kwargs.get("offset")
        command = f"cat '{file_path}'"
        if offset:
            command += f" | tail -n +{offset}"
        if limit:
            command += f" | head -n {limit}"
    
    elif name == "write":
        file_path = kwargs.get("file_path", "")
        content = kwargs.get("content", "")
        # Escape single quotes in content
        escaped_content = content.replace("'", "'\\''")
        command = f"echo '{escaped_content}' > '{file_path}'"
    
    elif name == "edit":
        file_path = kwargs.get("file_path", "")
        oldText = kwargs.get("oldText", "")
        newText = kwargs.get("newText", "")
        # Use sed for editing - escape special characters
        escaped_old = oldText.replace("'", "'\\''").replace("/", "\\/").replace("&", "\\&")
        escaped_new = newText.replace("'", "'\\''").replace("/", "\\/").replace("&", "\\&")
        command = f"sed -i \"s/{escaped_old}/{escaped_new}/g\" '{file_path}'"
    
    elif name == "list_dir":
        path = kwargs.get("path", ".")
        command = f"ls -la '{path}'"
    
    elif name == "exec":
        command = kwargs.get("command", "")
        return exec_sync(
            command,
            kwargs.get("timeout", 60),
            kwargs.get("workdir"),
            kwargs.get("env"),
            kwargs.get("security")
        )
    
    else:
        return f"Error: Unknown tool: {name}"
    
    # Execute the constructed command
    return exec_sync(command, kwargs.get("timeout", 60), kwargs.get("workdir"), kwargs.get("env"), kwargs.get("security"))


__all__ = [
    "exec",
    "exec_sync",
    "get_all_tools",
    "get_tools_schemas",
    "execute_tool",
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
