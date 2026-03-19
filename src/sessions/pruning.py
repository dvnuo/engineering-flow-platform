"""Session pruning for Engineering Flow Platform.

Automatically prunes old tool results from session context while preserving key information.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.sessions.manager import session_manager

logger = logging.getLogger(__name__)


# Configuration for pruning
DEFAULT_CONFIG = {
    "max_messages": 20,           # Keep last N messages
    "max_tool_results": 10,       # Keep last N tool results
    "preserve_system_prompt": True,
    "preserve_user_messages": True,
    "summarize_older_than": 5,    # Messages older than this get summarized
}


class SessionPruner:
    """Prunes old tool results from session context."""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
    
    async def prune(self, session_id: str) -> Dict[str, Any]:
        """Prune a session and return pruning statistics.
        
        Returns:
            Dict with pruned_count, preserved_count, messages_remaining
        """
        session = await session_manager.get_session(session_id)
        history = session.get("history", [])
        
        if len(history) <= self.config["max_messages"]:
            return {
                "pruned": False,
                "reason": "session_within_limits",
                "messages": len(history),
            }
        
        # Build a map of tool_call_id to tool_result
        tool_call_id_to_result = {}
        for m in history:
            if m.get("role") == "tool" and m.get("tool_call_id"):
                tool_call_id_to_result[m["tool_call_id"]] = m
        
        # Separate by role
        system_msgs = [m for m in history if m.get("role") == "system"]
        user_msgs = [m for m in history if m.get("role") == "user"]
        assistant_msgs = [m for m in history if m.get("role") == "assistant"]
        tool_msgs = [m for m in history if m.get("role") == "tool"]
        
        # Keep recent user messages
        recent_user = user_msgs[-self.config["max_messages"]//2:] if self.config["preserve_user_messages"] else []
        
        # Keep recent assistant messages AND their associated tool results
        recent_assistant = assistant_msgs[-self.config["max_messages"]//2:]
        
        # Collect tool_call_ids from recent assistant messages
        recent_tool_call_ids = set()
        for msg in recent_assistant:
            for tc in msg.get("tool_calls", []):
                recent_tool_call_ids.add(tc.get("id", ""))
        
        # Keep only tool results that are associated with recent assistant messages
        recent_tool = []
        for msg in tool_msgs:
            tool_call_id = msg.get("tool_call_id", "")
            if tool_call_id in recent_tool_call_ids:
                recent_tool.append(msg)
        
        # Also keep some recent standalone tool results (not associated with assistant)
        standalone_tools = [m for m in tool_msgs if m.get("role") == "tool" and m.get("tool_call_id", "") not in recent_tool_call_ids]
        recent_standalone_tool = standalone_tools[-self.config["max_tool_results"]:]
        recent_tool.extend(recent_standalone_tool)
        
        # Build pruned history maintaining original order
        pruned_history = []
        
        # Add system messages
        if self.config["preserve_system_prompt"]:
            pruned_history.extend(system_msgs)
        
        # Rebuild history in order, including only recent messages
        # Create a set of timestamps to include
        recent_timestamps = set()
        for msg in recent_user:
            recent_timestamps.add(msg.get("timestamp", ""))
        for msg in recent_assistant:
            recent_timestamps.add(msg.get("timestamp", ""))
        for msg in recent_tool:
            recent_timestamps.add(msg.get("timestamp", ""))
        
        # Add all recent messages (they maintain their original order in the conversation)
        for msg in history:
            ts = msg.get("timestamp", "")
            if ts in recent_timestamps:
                pruned_history.append(msg)
        
        logger.info(
            f"Pruned session {session_id}: "
            f"{len(history)} -> {len(pruned_history)} messages"
        )
        
        # Update session with pruned history
        session["history"] = pruned_history
        
        return {
            "pruned": True,
            "original_count": len(history),
            "pruned_count": len(history) - len(pruned_history),
            "remaining_count": len(pruned_history),
            "preserved_user": len(recent_user),
            "preserved_assistant": len(recent_assistant),
            "preserved_tool": len(recent_tool),
        }
    
    async def should_prune(self, session_id: str) -> bool:
        """Check if a session should be pruned."""
        session = await session_manager.get_session(session_id)
        history = session.get("history", [])
        return len(history) > self.config["max_messages"]


class SessionCompactor:
    """Compacts old conversation history into summaries."""
    
    def __init__(self, max_context_tokens: int = 60000):
        self.max_context_tokens = max_context_tokens
    
    async def compact(
        self,
        session_id: str,
        summary_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compact session history by summarizing older messages.
        
        Args:
            session_id: The session to compact
            summary_prompt: Optional custom summary instructions
            
        Returns:
            Dict with compact success status and new summary
        """
        session = await session_manager.get_session(session_id)
        history = session.get("history", [])
        
        if len(history) <= 10:
            return {
                "compact": False,
                "reason": "session_too_short",
            }
        
        # Separate messages to compact (not the last 10)
        messages_to_compact = history[:-10]
        recent_messages = history[-10:]
        
        if not messages_to_compact:
            return {
                "compact": False,
                "reason": "no_old_messages",
            }
        
        # Build conversation for summarization
        conversation_text = self._build_conversation_text(messages_to_compact)
        
        # Create summary (simplified - in real implementation would call LLM)
        summary = await self._generate_summary(
            conversation_text,
            summary_prompt or "Summarize the key points of this conversation"
        )
        
        # Create summary message
        summary_msg = {
            "role": "system",
            "content": f"[Summary of earlier conversation]:\n\n{summary}",
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": {
                "type": "compaction_summary",
                "original_messages": len(messages_to_compact),
                "compacted_at": datetime.utcnow().isoformat(),
            },
        }
        
        # Replace old messages with summary
        session["history"] = [summary_msg] + recent_messages
        
        logger.info(
            f"Compacted session {session_id}: "
            f"condensed {len(messages_to_compact)} messages into summary"
        )
        
        return {
            "compact": True,
            "original_messages": len(messages_to_compact),
            "summary_length": len(summary),
            "new_history_length": len(session["history"]),
        }
    
    def _build_conversation_text(self, messages: List[Dict]) -> str:
        """Build a text representation of messages for summarization."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            # Keep full content (no truncation to preserve data)
            
            lines.append(f"{role.upper()}: {content}")
        
        return "\n\n".join(lines)
    
    async def _generate_summary(
        self,
        conversation: str,
        prompt: str
    ) -> str:
        """Generate a summary of the conversation.
        
        In a full implementation, this would call an LLM.
        For now, returns a placeholder.
        """
        # Placeholder - would call LLM in production
        line_count = len(conversation.split("\n"))
        
        return (
            f"This section of the conversation contained {line_count} messages. "
            "Key topics discussed were processed and summarized here for context. "
            "(Full summarization requires LLM integration)"
        )


# Global instances
session_pruner = SessionPruner()
session_compactor = SessionCompactor()
