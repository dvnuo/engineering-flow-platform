"""
Exec Security Module

Provides security controls for command execution.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import os
import re


class ExecSecurity(Enum):
    """Security modes for command execution."""
    DENY = "deny"
    ALLOWLIST = "allowlist"
    FULL = "full"


class ExecAsk(Enum):
    """Approval workflow for unknown commands."""
    OFF = "off"
    ON_MISS = "on-miss"
    ALWAYS = "always"


# Default safe binaries (text processing utilities and VCS)
DEFAULT_SAFE_BINS = [
    # Text processing
    "jq", "grep", "cut", "sort", "uniq",
    "head", "tail", "tr", "wc", "cat", "less", "more",
    "sed", "awk", "perl", "find", "xargs",
    "vi", "vim", "echo", "printf", "date",
    # Version control
    "git", "gh",
    # Code program
    "python3", "pip",
    # File operations
    "ls", "pwd", "cd", "mkdir", "rm", "cp", "mv", "touch",
    "tar", "zip", "unzip", "gzip", "bzip2",
    # Network
    "curl", "wget",
    # System
    "ps", "df", "du", "free", "top",
]

# Dangerous environment variables that can alter execution flow
# or inject code when running on non-sandboxed hosts.
DANGEROUS_ENV_VARS = {
    # Library injection
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    # macOS library injection
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    # Node.js code injection
    "NODE_OPTIONS",
    "NODE_PATH",
    # Python code injection
    "PYTHONPATH",
    "PYTHONHOME",
    # Ruby/Perl injection
    "RUBYLIB",
    "PERL5LIB",
    # Shell execution
    "BASH_ENV",
    "ENV",
    # Other dangerous variables
    "GCONV_PATH",
    "IFS",
    "SSLKEYLOGFILE",
}

# Dangerous environment variable prefixes
DANGEROUS_ENV_PREFIXES = ["DYLD_", "LD_"]


@dataclass
class ExecAllowlistEntry:
    """An entry in the command allowlist."""
    pattern: str
    id: Optional[str] = None
    last_used_at: Optional[int] = None
    last_used_command: Optional[str] = None
    last_resolved_path: Optional[str] = None


@dataclass
class ExecSecurityConfig:
    """Configuration for exec security."""
    security: ExecSecurity = ExecSecurity.DENY
    ask: ExecAsk = ExecAsk.ON_MISS
    safe_bins: list[str] = field(default_factory=lambda: DEFAULT_SAFE_BINS.copy())
    allowlist: list[ExecAllowlistEntry] = field(default_factory=list)


def parse_first_token(command: str) -> Optional[str]:
    """
    Parse the first token from a shell command, respecting quotes.
    
    Examples:
        "ls -la" -> "ls"
        "'echo hello'" -> "echo hello"
        '"echo world"' -> "echo world"
    """
    trimmed = command.strip()
    if not trimmed:
        return None
    
    first = trimmed[0]
    if first in ('"', "'"):
        end = trimmed.find(first, 1)
        if end > 1:
            return trimmed[1:end]
        return trimmed[1:]
    
    match = re.match(r'^[^\s]+', trimmed)
    return match.group(0) if match else None


def resolve_command_path(command: str, cwd: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve the executable path for a command.
    
    Returns:
        Tuple of (executable_name, resolved_path)
    """
    raw_executable = parse_first_token(command)
    if not raw_executable:
        return None, None
    
    # Handle relative/absolute paths
    if raw_executable.startswith("~"):
        expanded = os.path.expanduser(raw_executable)
    else:
        expanded = raw_executable
    
    # Absolute or relative path
    if "/" in expanded or "\\" in expanded:
        if os.path.isabs(expanded):
            if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return os.path.basename(expanded), expanded
        else:
            base = cwd or os.getcwd()
            candidate = os.path.abspath(os.path.join(base, expanded))
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return os.path.basename(candidate), candidate
        return None, None
    
    # Search in PATH
    env_path = os.environ.get("PATH", "") or ""
    for directory in env_path.split(os.pathsep):
        if not directory:
            continue
        candidate = os.path.join(directory, raw_executable)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return raw_executable, candidate
    
    return raw_executable, None


def glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob pattern to a regex pattern."""
    regex = "^"
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                regex += ".*"
                i += 2
                continue
            regex += "[^/]*"
            i += 1
            continue
        if ch == "?":
            regex += "."
            i += 1
            continue
        regex += re.escape(ch)
        i += 1
    regex += "$"
    return re.compile(regex, re.IGNORECASE)


def matches_pattern(pattern: str, target: str) -> bool:
    """Check if a target matches a glob pattern."""
    if not pattern.strip():
        return False
    
    expanded = pattern
    if pattern.startswith("~"):
        expanded = os.path.expanduser(pattern)
    
    # Handle wildcards
    if "*" not in expanded and "?" not in expanded:
        return expanded.lower() == target.lower()
    
    regex = glob_to_regex(expanded)
    return bool(regex.match(target))


def match_allowlist(
    entries: list[ExecAllowlistEntry],
    command: str,
    cwd: Optional[str] = None
) -> Optional[ExecAllowlistEntry]:
    """
    Check if a command matches any entry in the allowlist.
    
    Returns the matching entry if found, None otherwise.
    """
    if not entries:
        return None
    
    executable, resolved_path = resolve_command_path(command, cwd)
    if not resolved_path:
        return None
    
    for entry in entries:
        pattern = entry.pattern.strip()
        if not pattern:
            continue
        
        # Only match patterns with paths
        if "/" not in pattern and "\\" not in pattern and "~" not in pattern:
            continue
        
        if matches_pattern(pattern, resolved_path):
            return entry
    
    return None


def evaluate_command(
    command: str,
    config: ExecSecurityConfig,
    cwd: Optional[str] = None
) -> tuple[bool, str]:
    """
    Evaluate whether a command should be allowed.
    
    Returns:
        Tuple of (allowed: bool, reason: str)
    """
    if config.security == ExecSecurity.FULL:
        return True, "security=full: all commands allowed"
    
    if config.security == ExecSecurity.DENY:
        return False, "security=deny: all commands blocked by default"
    
    # ALLOWLIST mode
    executable, resolved_path = resolve_command_path(command, cwd)
    
    if not resolved_path:
        return False, f"command not found: {executable}"
    
    # Check safe bins first
    if executable and executable in config.safe_bins:
        return True, f"safe bin: {executable}"
    
    # Check allowlist
    if match_allowlist(config.allowlist, command, cwd):
        return True, f"matched allowlist: {resolved_path}"
    
    return False, f"not in allowlist: {executable}"


def validate_environment(env: dict[str, str]) -> tuple[bool, Optional[str]]:
    """
    Validate environment variables for dangerous values.
    
    Returns:
        Tuple of (valid: bool, error_message: Optional[str])
    """
    for key in env.keys():
        upper_key = key.upper()
        
        # Check prefixes
        for prefix in DANGEROUS_ENV_PREFIXES:
            if upper_key.startswith(prefix):
                return False, f"Security Violation: '{key}' is forbidden during host execution"
        
        # Check exact matches
        if upper_key in DANGEROUS_ENV_VARS:
            return False, f"Security Violation: '{key}' is forbidden during host execution"
        
        # Check PATH modification
        if upper_key == "PATH":
            return False, "Security Violation: Custom 'PATH' variable is forbidden during host execution"
    
    return True, None


def requires_approval(
    command: str,
    config: ExecSecurityConfig,
    analysis_ok: bool,
    cwd: Optional[str] = None
) -> tuple[bool, str]:
    """
    Determine if a command requires user approval.
    
    Returns:
        Tuple of (requires_approval: bool, reason: str)
    """
    if config.ask == ExecAsk.OFF:
        return False, "ask=off: approval disabled"
    
    if config.security == ExecSecurity.FULL:
        return False, "security=full: no approval needed"
    
    allowed, reason = evaluate_command(command, config, cwd)
    if allowed:
        return False, f"already allowed: {reason}"
    
    if config.ask == ExecAsk.ALWAYS:
        return True, "ask=always: user approval required"
    
    # ON_MISS: require approval if not in allowlist
    if not analysis_ok:
        return True, "ask=on-miss: command analysis failed"
    
    return True, "ask=on-miss: command not in allowlist"


def create_default_config() -> ExecSecurityConfig:
    """Create a default security configuration."""
    return ExecSecurityConfig(
        security=ExecSecurity.ALLOWLIST,
        ask=ExecAsk.OFF,
        safe_bins=DEFAULT_SAFE_BINS.copy(),
        allowlist=[]
    )
