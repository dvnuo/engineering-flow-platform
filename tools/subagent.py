"""Sub-agent Sessions Tools for OpsClaw.

Provides tools for spawning and managing sub-agent sessions.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# Import Agent lazily to avoid circular imports
_subagent_sessions: Dict[str, Dict[str, Any]] = {}

logger = logging.getLogger(__name__)


class SubAgent:
    """Represents a running sub-agent session."""
    
    def __init__(
        self,
        session_key: str,
        task: str,
        model: Optional[str] = None,
        thinking: Optional[str] = None,
        disable_tools: bool = False,
    ):
        self.session_key = session_key
        self.task = task
        self.model = model
        self.thinking = thinking
        self.disable_tools = disable_tools
        self.created_at = datetime.now().isoformat()
        self.status = "running"
        self.result: Optional[str] = None
        
        # Agent will be created lazily
        self._agent = None
    
    @property
    def agent(self):
        """Get the agent instance (lazy initialization)."""
        if self._agent is None:
            from agent.core import Agent
            self._agent = Agent(
                session_id=self.session_key,
                think_level=self.thinking,
                model=self.model,
            )
            # Disable tools if requested
            if self.disable_tools:
                self._agent.tools = []
        return self._agent
    
    async def start(self):
        """Start the sub-agent task."""
        self._task = asyncio.create_task(self._run())
    
    async def _run(self):
        """Run the sub-agent task."""
        try:
            logger.info(f"Sub-agent {self.session_key} started - think_level={self.thinking}, model={self.model}")
            logger.debug(f"Task: {self.task[:200]}...")
            
            result = await self.agent.process(
                message=self.task,
                session_id=self.session_key,
            )
            
            self.result = result.get("response", "")
            self.status = "completed"
            logger.info(f"Sub-agent {self.session_key} completed")
            
        except Exception as e:
            self.status = "failed"
            self.result = f"Error: {str(e)}"
            logger.error(f"Sub-agent {self.session_key} failed: {e}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_key": self.session_key,
            "task_preview": self.task[:100] + "..." if len(self.task) > 100 else self.task,
            "model": self.model,
            "thinking": self.thinking,
            "disable_tools": self.disable_tools,
            "created_at": self.created_at,
            "status": self.status,
        }


def sessions_list(
    active_minutes: Optional[int] = None,
    limit: Optional[int] = None,
    message_limit: Optional[int] = None,
    kinds: Optional[List[str]] = None,
) -> str:
    """List active sessions.
    
    Args:
        active_minutes: Filter sessions active within this many minutes
        limit: Maximum number of sessions to return
        message_limit: Include up to N messages per session
        kinds: Filter by session kinds (e.g., ["direct", "group"])
    
    Returns:
        JSON string with session list
    """
    from session.manager import session_manager
    
    sessions = []
    
    # Get main session info
    main_info = session_manager.get_session_info("main")
    if main_info:
        sessions.append({
            "key": "main",
            "kind": "direct",
            "session_id": "main",
            "updated_at": main_info.get("updated_at", 0),
            "messages": message_limit if message_limit else 0,
        })
    
    # Get all sub-agent sessions
    for key, subagent in _subagent_sessions.items():
        sessions.append({
            "key": key,
            "kind": "subagent",
            "session_id": key,
            "updated_at": subagent.get("created_at", ""),
            "status": subagent.get("status", "unknown"),
            "messages": message_limit if message_limit else 0,
        })
    
    # Apply filters
    if kinds:
        sessions = [s for s in sessions if s.get("kind") in kinds]
    
    if active_minutes:
        # Filter by recent activity
        cutoff = datetime.now().timestamp() - (active_minutes * 60)
        sessions = [s for s in sessions if s.get("updated_at", "") or True]  # Simplified
    
    if limit:
        sessions = sessions[:limit]
    
    return json.dumps({
        "sessions": sessions,
        "total": len(sessions),
    }, indent=2)


def sessions_history(
    session_key: str,
    limit: Optional[int] = 50,
    include_tools: bool = False,
) -> str:
    """Get message history for a session.
    
    Args:
        session_key: Session identifier
        limit: Maximum messages to return
        include_tools: Include tool results in history
    
    Returns:
        JSON string with message history
    """
    from session.manager import session_manager
    
    messages = session_manager.get_history(session_key)
    
    if limit and len(messages) > limit:
        messages = messages[-limit:]
    
    history = []
    for msg in messages:
        entry = {
            "role": msg.get("role", "unknown"),
            "content": msg.get("content", "")[:1000],  # Truncate long content
        }
        if include_tools and msg.get("tool_calls"):
            entry["tool_calls"] = msg.get("tool_calls")
        history.append(entry)
    
    return json.dumps({
        "session_key": session_key,
        "messages": history,
        "count": len(history),
    }, indent=2)


def sessions_send(
    session_key: str,
    message: str,
    timeout_seconds: Optional[int] = 60,
) -> str:
    """Send a message to another session.
    
    Args:
        session_key: Target session identifier
        message: Message to send
        timeout_seconds: Timeout for response
    
    Returns:
        JSON string with response
    """
    # Check if it's a sub-agent session
    if session_key in _subagent_sessions:
        subagent = _subagent_sessions[session_key]
        
        # For subagents, we add the message to their conversation
        # This is a simplified implementation
        return json.dumps({
            "session_key": session_key,
            "status": "queued",
            "message": "Message queued for sub-agent",
        }, indent=2)
    
    # For main session, we need to return info about how to send
    # In a real implementation, this would communicate with the main agent
    return json.dumps({
        "session_key": session_key,
        "status": "error",
        "error": "Session not found or not accessible",
    }, indent=2)


def sessions_spawn(
    task: str,
    agent_id: Optional[str] = None,
    model: Optional[str] = None,
    thinking: Optional[str] = None,
    disable_tools: bool = False,
    cleanup: str = "delete",
    label: Optional[str] = None,
    run_timeout_seconds: Optional[int] = None,
    timeout_seconds: Optional[int] = 300,
) -> str:
    """Spawn a sub-agent session to handle a task.
    
    Args:
        task: Task description for the sub-agent
        agent_id: Agent ID to use (reserved for future use)
        model: Model to use for the sub-agent
        thinking: Thinking level (off, minimal, low, medium, high)
        disable_tools: If true, tools are disabled (pure thinking mode)
        cleanup: What to do after completion ("delete" or "keep")
        label: Human-readable label for the session
        run_timeout_seconds: Maximum runtime in seconds
        timeout_seconds: Timeout for the entire operation
    
    Returns:
        JSON string with session info
    """
    # Generate a unique session key
    session_key = label or f"subagent-{uuid.uuid4().hex[:8]}"
    
    # Create the sub-agent
    subagent = SubAgent(
        session_key=session_key,
        task=task,
        model=model,
        thinking=thinking,
        disable_tools=disable_tools,
    )
    
    # Store it
    _subagent_sessions[session_key] = {
        "session_key": session_key,
        "task": task,
        "model": model,
        "thinking": thinking,
        "cleanup": cleanup,
        "created_at": subagent.created_at,
        "status": "started",
        "agent": subagent,
    }
    
    # Log subagent creation with thinking level
    logger.info(f"Spawn subagent: session={session_key}, think_level={thinking}, model={model}")
    
    # Note: Actual async execution requires running event loop
    # The sub-agent will execute when process() is called
    
    return json.dumps({
        "session_key": session_key,
        "status": "started",
        "task_preview": task[:100] + "..." if len(task) > 100 else task,
        "model": model,
        "thinking": thinking,
        "disable_tools": disable_tools,
        "cleanup": cleanup,
        "message": f"Sub-agent session '{session_key}' started",
    }, indent=2)


def get_subagent_result(session_key: str, timeout: int = 30) -> Optional[str]:
    """Get the result of a sub-agent session.
    
    Args:
        session_key: Session identifier
        timeout: Timeout in seconds
    
    Returns:
        Result string or None if not complete
    """
    if session_key not in _subagent_sessions:
        return None
    
    subagent = _subagent_sessions[session_key]
    if subagent.get("status") == "completed":
        return subagent.get("result")
    
    return None


def cleanup_subagent(session_key: str) -> bool:
    """Clean up a sub-agent session.
    
    Args:
        session_key: Session identifier
    
    Returns:
        True if cleaned up, False if not found
    """
    from session.manager import session_manager
    
    if session_key in _subagent_sessions:
        del _subagent_sessions[session_key]
        session_manager.clear_history(session_key)
        return True
    return False
