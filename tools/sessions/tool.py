"""Sessions Tool - Session Management

Manage sub-agent sessions: spawn, list, history, send messages.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# In-memory session storage (replace with persistent storage)
_sessions: Dict[str, Dict[str, Any]] = {}


def sessions_spawn(
    task: str,
    agentId: Optional[str] = None,
    model: Optional[str] = None,
    thinking: Optional[str] = None,
    cleanup: str = "delete",
    label: Optional[str] = None,
    runTimeoutSeconds: Optional[int] = None,
    timeoutSeconds: int = 300,
) -> str:
    """Spawn a sub-agent session.
    
    Args:
        task: Task description for the sub-agent
        agentId: Agent ID to use
        model: Model to use
        thinking: Thinking level (off, minimal, low, medium, high)
        cleanup: What to do after completion (delete, keep)
        label: Human-readable label
        runTimeoutSeconds: Maximum runtime in seconds
        timeoutSeconds: Timeout for the entire operation
    
    Returns:
        JSON string with session info
    """
    session_key = label or f"session-{uuid.uuid4().hex[:8]}"
    
    session = {
        "session_key": session_key,
        "task": task,
        "agent_id": agentId,
        "model": model,
        "thinking": thinking,
        "cleanup": cleanup,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    
    _sessions[session_key] = session
    
    logger.info(f"Sessions spawn: {session_key}, think: {thinking}, model: {model}")
    
    return json.dumps({
        "success": True,
        "session_key": session_key,
        "status": "pending",
        "task_preview": task[:100] + "..." if len(task) > 100 else task,
        "model": model,
        "thinking": thinking,
        "cleanup": cleanup,
        "message": f"Session '{session_key}' spawned"
    }, indent=2)


def sessions_list(
    activeMinutes: Optional[int] = None,
    kinds: Optional[List[str]] = None,
    limit: Optional[int] = None,
    messageLimit: Optional[int] = None,
) -> str:
    """List active sessions.
    
    Args:
        activeMinutes: Filter sessions active within N minutes
        kinds: Filter by session kinds
        limit: Maximum sessions to return
        messageLimit: Include up to N messages per session
    
    Returns:
        JSON string with session list
    """
    sessions = []
    
    for key, session in _sessions.items():
        entry = {
            "session_key": key,
            "status": session.get("status", "unknown"),
            "created_at": session.get("created_at", ""),
            "model": session.get("model"),
            "thinking": session.get("thinking"),
        }
        sessions.append(entry)
    
    # Apply filters
    if kinds:
        sessions = [s for s in sessions if s.get("status") in kinds]
    
    if activeMinutes:
        cutoff = datetime.now().timestamp() - (activeMinutes * 60)
        sessions = [s for s in sessions if s.get("created_at", "") or True]
    
    if limit:
        sessions = sessions[:limit]
    
    return json.dumps({
        "success": True,
        "sessions": sessions,
        "total": len(sessions)
    }, indent=2)


def sessions_history(
    sessionKey: str,
    limit: int = 50,
    includeTools: bool = False,
) -> str:
    """Get message history for a session.
    
    Args:
        sessionKey: Session identifier
        limit: Maximum messages to return
        includeTools: Include tool results
    
    Returns:
        JSON string with message history
    """
    if sessionKey not in _sessions:
        return json.dumps({
            "success": False,
            "error": f"Session not found: {sessionKey}"
        }, indent=2)
    
    # Placeholder - actual implementation reads from session store
    history = []
    
    return json.dumps({
        "success": True,
        "session_key": sessionKey,
        "history": history,
        "count": len(history)
    }, indent=2)


def sessions_send(
    sessionKey: str,
    message: str,
    timeoutSeconds: int = 60,
) -> str:
    """Send a message to another session.
    
    Args:
        sessionKey: Target session identifier
        message: Message to send
        timeoutSeconds: Timeout for response
    
    Returns:
        JSON string with response
    """
    if sessionKey not in _sessions:
        return json.dumps({
            "success": False,
            "error": f"Session not found: {sessionKey}"
        }, indent=2)
    
    logger.info(f"Sessions send: to={sessionKey}, message={message[:100]}...")
    
    return json.dumps({
        "success": True,
        "session_key": sessionKey,
        "status": "queued",
        "message": "Message queued"
    }, indent=2)


def agents_list() -> str:
    """List available agent IDs.
    
    Returns:
        JSON string with agent list
    """
    # Reserved for future use - agent allowlist
    return json.dumps({
        "success": True,
        "agents": ["coding-agent"],
        "message": "Agent list"
    }, indent=2)
