"""Session Memory Hook - Save session context to memory files.

This module provides functionality to save conversation history to daily memory files,
similar to OpenClaw's session-memory hook.

Trigger: Called when user starts a new session or explicitly saves.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.sessions.manager import session_manager

logger = logging.getLogger(__name__)

# Default workspace memory directory
DEFAULT_MEMORY_DIR = Path.home() / ".efp/workspace" / "memory"


async def save_session_to_memory(
    session_id: str,
    workspace_dir: Optional[Path] = None,
    messages_limit: int = 15,
) -> Optional[Path]:
    """Save session conversation to daily memory file.
    
    Args:
        session_id: The session ID to save
        workspace_dir: Workspace directory (defaults to ~/.efp/workspace)
        messages_limit: Number of recent messages to include
        
    Returns:
        Path to the created memory file, or None if failed
    """
    if workspace_dir is None:
        workspace_dir = Path.home() / ".efp" / "workspace"
    
    memory_dir = workspace_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Get session history
        session = await session_manager.get_session(session_id)
        history = session.get("history", [])
        
        if not history:
            logger.info(f"No history to save for session {session_id}")
            return None
        
        # Get last N messages (user/assistant pairs)
        messages = history[-messages_limit:] if messages_limit > 0 else history
        
        # Generate slug from first user message
        slug = _generate_slug(messages)
        
        # Create filename: YYYY-MM-DD-slug.md
        today = datetime.now().strftime("%Y-%m-%d")
        filename = f"{today}-{slug}.md"
        memory_file = memory_dir / filename
        
        # Build content
        content = _build_memory_content(session_id, messages)
        
        # Write to file
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write(content)
        
        logger.info(f"Saved session {session_id} to {memory_file}")
        return memory_file
        
    except Exception as e:
        logger.error(f"Failed to save session {session_id} to memory: {e}")
        return None


def _generate_slug(messages: list) -> str:
    """Generate a descriptive slug from messages.
    
    Uses simple keyword extraction from first user message.
    Falls back to timestamp if no keywords found.
    """
    # Find first user message
    user_message = None
    for msg in messages:
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break
    
    if not user_message:
        # Fallback to timestamp-based slug
        return datetime.now().strftime("%H%M")
    
    # Simple slug generation: take first few words, lowercase, alphanumeric only
    words = user_message.split()[:3]
    if not words:
        return datetime.now().strftime("%H%M")
    
    # Clean words: lowercase, keep only alphanumeric
    slug_parts = []
    for word in words:
        cleaned = "".join(c for c in word.lower() if c.isalnum())
        if cleaned:
            slug_parts.append(cleaned)
    
    if not slug_parts:
        return datetime.now().strftime("%H%M")
    
    slug = "-".join(slug_parts[:3])
    
    # Truncate if too long
    if len(slug) > 50:
        slug = slug[:50]
    
    return slug


def _build_memory_content(session_id: str, messages: list) -> str:
    """Build memory file content from messages."""
    lines = []
    
    # Header
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"# Session: {now}")
    lines.append("")
    lines.append(f"- **Session ID**: {session_id}")
    lines.append(f"- **Source**: webchat")
    lines.append("")
    
    # Messages
    lines.append("## Conversation")
    lines.append("")
    
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        
        if not content:
            continue
            
        # Truncate long content for memory file
        if len(content) > 2000:
            content = content[:2000] + "..."
        
        lines.append(f"**{role.upper()}**: {content}")
        lines.append("")
    
    return "\n".join(lines)


async def save_and_clear_session(
    session_id: str,
    workspace_dir: Optional[Path] = None,
    messages_limit: int = 15,
) -> dict:
    """Save session to memory and then clear it.
    
    This is the main function to call when user wants to start fresh.
    
    Returns:
        Dict with 'success', 'file_path', and optional 'error'
    """
    # Save first
    file_path = await save_session_to_memory(
        session_id=session_id,
        workspace_dir=workspace_dir,
        messages_limit=messages_limit,
    )
    
    # Then clear
    await session_manager.clear_history(session_id)
    
    if file_path:
        return {
            "success": True,
            "file_path": str(file_path),
            "message": f"Session saved to {file_path.name}"
        }
    else:
        # Even if save failed, we still cleared the history
        return {
            "success": True,
            "file_path": None,
            "message": "Session cleared (no content to save)"
        }
