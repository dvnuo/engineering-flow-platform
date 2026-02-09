"""File operation tools for reading, writing, and editing files."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Security: Define allowed workspace directories
# Order matters: more specific paths first
ALLOWED_WORKSPACE_DIRS = [
    Path.cwd().resolve(),  # Current working directory
    Path.home() / ".efp" / "workspace",  # EFP workspace
    Path.home() / ".efp",  # EFP base (for sessions, memory)
    Path("/tmp"),  # Temporary files (commonly used)
    Path.home(),  # User home directory
]


def _validate_path(file_path: str, allow_temp: bool = True) -> tuple[bool, str, Path]:
    """Validate file path for security.
    
    Args:
        file_path: Path to validate
        allow_temp: Whether to allow /tmp directory
    
    Returns:
        (is_valid, error_message, resolved_path)
    """
    if not file_path or not file_path.strip():
        return False, "Empty path", Path(".")
    
    path = Path(file_path)
    
    # Resolve to absolute path
    try:
        resolved = path.resolve()
    except (OSError, ValueError) as e:
        return False, f"Invalid path: {e}", path
    
    # Check path traversal attempts
    if ".." in Path(file_path).parts:
        return False, "Path traversal not allowed (..)", resolved
    
    # Build allowed dirs list based on context
    allowed_dirs = ALLOWED_WORKSPACE_DIRS.copy()
    if not allow_temp:
        # Remove /tmp if not allowed in this context
        allowed_dirs = [d for d in allowed_dirs if str(d) != "/tmp"]
    
    # Check if path is within allowed directories
    is_allowed = False
    for allowed_dir in allowed_dirs:
        try:
            resolved.relative_to(allowed_dir.resolve())
            is_allowed = True
            break
        except ValueError:
            continue
    
    if not is_allowed:
        return False, f"Path outside allowed workspace: {file_path}", resolved
    
    return True, "", resolved


def read(file_path: str, limit: Optional[int] = None, offset: Optional[int] = None) -> str:
    """Read file contents.
    
    Args:
        file_path: Path to the file to read
        limit: Maximum number of lines to read (optional)
        offset: Line number to start reading from (optional, 1-indexed)
    
    Returns:
        File contents as string
    """
    # Validate path
    is_valid, error, resolved_path = _validate_path(file_path)
    if not is_valid:
        return f"Error: {error}"
    
    # Security: Check if file exists and is file
    if not resolved_path.is_file():
        return f"Error: File not found: {file_path}"
    
    try:
        with open(resolved_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Handle offset (convert from 1-indexed to 0-indexed)
        start = (offset - 1) if offset else 0
        
        # Handle limit
        if limit:
            lines = lines[start:start + limit]
        elif offset:
            lines = lines[start:]
        else:
            lines = lines
        
        content = ''.join(lines)
        
        # Add metadata header
        line_count = len(lines)
        total_lines = len(open(resolved_path, 'r').readlines())
        
        header = f"File: {resolved_path}\nLines: {start + 1}-{start + line_count} of {total_lines}\n\n"
        return header + content
        
    except UnicodeDecodeError:
        return f"Error: Cannot read binary file: {file_path}"
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
    # Validate path
    is_valid, error, resolved_path = _validate_path(file_path)
    if not is_valid:
        return f"Error: {error}"
    
    try:
        # Create parent directories if needed
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(resolved_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        logger.info(f"File written: {resolved_path}")
        return f"✅ File written: {resolved_path}"
        
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
    # Validate path
    is_valid, error, resolved_path = _validate_path(file_path)
    if not is_valid:
        return f"Error: {error}"
    
    if not resolved_path.is_file():
        return f"Error: File not found: {file_path}"
    
    try:
        with open(resolved_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if oldText not in content:
            return f"Error: Text not found in file"
        
        new_content = content.replace(oldText, newText)
        
        with open(resolved_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        logger.info(f"File edited: {resolved_path}")
        return f"✅ File edited: {resolved_path}"
        
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
    # Validate path
    is_valid, error, resolved_path = _validate_path(path)
    if not is_valid:
        return f"Error: {error}"
    
    if not resolved_path.is_dir():
        return f"Error: Directory not found: {path}"
    
    try:
        items = []
        for item in sorted(resolved_path.iterdir()):
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                items.append(f"📄 {item.name}")
        
        if not items:
            return f"Directory is empty: {path}"
        
        return f"Directory: {resolved_path}\n\n" + "\n".join(items)
        
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as e:
        return f"Error listing directory: {e}"


def get_tools_schemas() -> list:
    """Return file tool schemas for LLM function calling."""
    return [
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
    ]


# Backward compatibility alias
file_tools = {
    "read": read,
    "write": write,
    "edit": edit,
    "list_dir": list_dir,
}
