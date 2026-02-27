"""Session Memory - Automatic session summarization and storage.

This module provides automatic session summarization and storage
to daily memory files.

The process is transparent to users - sessions are automatically
summarized and stored when they end or are evicted from memory.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.sessions.manager import session_manager

logger = logging.getLogger(__name__)

# Default workspace memory directory
DEFAULT_MEMORY_DIR = Path.home() / ".efp" / "workspace" / "memory"


async def summarize_session(
    session_id: str,
    messages: List[Dict[str, Any]],
) -> str:
    """Summarize a session's conversation messages.
    
    Currently uses a lightweight, heuristic approach (no external LLM call).
    TODO: Integrate actual LLM-based summarization for richer summaries.
    
    Args:
        session_id: The session ID
        messages: List of conversation messages
        
    Returns:
        Summary text generated from the conversation messages
    """
    # Build conversation text for summarization
    conversation = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            # Truncate long content for summarization prompt
            if len(content) > 500:
                content = content[:500] + "..."
            conversation.append(f"{role.upper()}: {content}")
    
    if not conversation:
        return ""
    
    # conversation_text = "\n".join(conversation)  # TODO: use for LLM prompt when implemented
    
    # Extract key information
    summary_parts = []
    
    # Find user requests
    user_requests = [msg["content"] for msg in messages if msg.get("role") == "user" and msg.get("content")]
    if user_requests:
        first_request = user_requests[0][:200]
        summary_parts.append(f"**User Request**: {first_request}")
    
    # Find tool usage
    tool_calls = []
    for msg in messages:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if content:
                # Extract tool name from content if possible
                tool_calls.append(content[:100])
    
    if tool_calls:
        summary_parts.append(f"**Tools Used**: {len(tool_calls)} operations")
    
    # Find decisions/changes
    decision_keywords = ["decided", "decided to", "change", "modified", "updated", "created", "fixed"]
    decisions = []
    for msg in messages:
        content = msg.get("content", "").lower()
        for keyword in decision_keywords:
            if keyword in content:
                # Extract sentence containing keyword
                sentences = msg.get("content", "").split(".")
                for sentence in sentences:
                    if keyword in sentence.lower():
                        decisions.append(sentence.strip()[:100])
                        break
        if len(decisions) >= 3:
            break
    
    if decisions:
        summary_parts.append(f"**Decisions**: {decisions[0]}")
    
    return "\n".join(summary_parts) if summary_parts else "Session completed."


async def save_session_summary(
    session_id: str,
    workspace_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Save session summary to daily memory file.
    
    This function is called automatically when a session ends.
    It summarizes the conversation and saves to memory.
    
    Args:
        session_id: The session ID to summarize
        workspace_dir: Workspace directory (defaults to ~/.efp/workspace)
        
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
            logger.debug(f"No history to summarize for session {session_id}")
            return None
        
        # Skip if too few messages (not worth summarizing)
        user_messages = [m for m in history if m.get("role") == "user"]
        if len(user_messages) < 2:
            logger.debug(f"Session {session_id} too short to summarize ({len(user_messages)} user messages)")
            return None
        
        # Generate summary (heuristic-based, TODO: use LLM)
        summary = await summarize_session(session_id, history)
        
        if not summary:
            summary = "Session completed."
        
        # Get source channel
        channel = session.get("channel", "webchat")
        
        # Get session metadata
        created_at = session.get("created_at", datetime.now().isoformat())
        
        # Build memory content (matching existing memory format)
        today = datetime.now().strftime("%Y-%m-%d")
        memory_file = memory_dir / f"{today}.md"
        
        # Read existing content if file exists
        existing_content = ""
        if memory_file.exists():
            try:
                with open(memory_file, "r", encoding="utf-8") as f:
                    existing_content = f.read()
            except Exception:
                pass
        
        # Build new session entry
        session_entry = _build_session_entry(session_id, channel, created_at, summary)
        
        # Append to daily memory file
        if existing_content:
            # Check if we already have content
            if "# " + today in existing_content:
                # Append to existing date section
                new_content = existing_content + "\n" + session_entry
            else:
                # Add new date section
                new_content = existing_content + "\n\n" + f"# {today}\n\n" + session_entry
        else:
            # New file
            new_content = f"# {today}\n\n{session_entry}"
        
        # Write to file
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        logger.info(f"Saved session summary for {session_id} to {memory_file}")
        return memory_file
        
    except Exception as e:
        logger.error(f"Failed to save session summary for {session_id}: {e}")
        return None


def _build_session_entry(
    session_id: str,
    channel: str,
    created_at: str,
    summary: str,
) -> str:
    """Build a session entry in memory format.
    
    Args:
        session_id: Session ID
        channel: Source channel (webchat, discord, etc.)
        created_at: Session creation timestamp
        summary: Heuristic-based summary
        
    Returns:
        Formatted session entry
    """
    # Format time from ISO string
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M")
    except Exception:
        time_str = created_at[:16]
    
    lines = [
        f"## Session: {time_str}",
        "",
        f"- **Channel**: {channel}",
        f"- **Summary**: {summary}",
        "",
    ]
    
    return "\n".join(lines)


async def auto_save_sessions(
    active_session_ids: List[str],
    workspace_dir: Optional[Path] = None,
) -> List[Path]:
    """Auto-save multiple sessions (called during eviction).
    
    This is typically called when the session manager needs to evict
    old sessions - we save their summaries before they're removed.
    
    Args:
        active_session_ids: List of session IDs that are about to be evicted
        workspace_dir: Workspace directory
        
    Returns:
        List of saved file paths
    """
    saved_files = []
    
    for session_id in active_session_ids:
        file_path = await save_session_summary(session_id, workspace_dir)
        if file_path:
            saved_files.append(file_path)
    
    if saved_files:
        logger.info(f"Auto-saved {len(saved_files)} session summaries")
    
    return saved_files
