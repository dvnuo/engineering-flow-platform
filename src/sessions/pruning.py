"""Session pruning for Engineering Flow Platform.

Automatically prunes old tool results from session context while preserving key information.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config import config, resolve_model_limits
from src.efp_runtime.session.gateway_facade import runtime_v2_session_manager as session_manager

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
        
        # Separate by role, keeping original indices
        system_indices = [i for i, m in enumerate(history) if m.get("role") == "system"]
        user_indices = [i for i, m in enumerate(history) if m.get("role") == "user"]
        assistant_indices = [i for i, m in enumerate(history) if m.get("role") == "assistant"]
        tool_indices = [i for i, m in enumerate(history) if m.get("role") == "tool"]
        
        # Calculate how many to keep
        max_total = self.config["max_messages"]
        # Budget: user + assistant + tool = max_total
        num_user = min(len(user_indices), max_total // 3)
        num_assistant = min(len(assistant_indices), max_total // 3)
        num_tool = min(len(tool_indices), max_total // 3)
        
        # Keep recent by indices (not timestamps to avoid collisions)
        recent_user_indices = set(user_indices[-num_user:] if num_user > 0 else [])
        recent_assistant_indices = set(assistant_indices[-num_assistant:] if num_assistant > 0 else [])
        
        # Collect tool_call_ids from recent assistant messages
        recent_tool_call_ids = set()
        for idx in recent_assistant_indices:
            msg = history[idx]
            for tc in msg.get("tool_calls", []):
                recent_tool_call_ids.add(tc.get("id", ""))
        
        # Keep tool results associated with recent assistant messages
        recent_tool_indices = set()
        for idx in tool_indices:
            msg = history[idx]
            tool_call_id = msg.get("tool_call_id", "")
            if tool_call_id in recent_tool_call_ids:
                recent_tool_indices.add(idx)
        
        # Also keep recent standalone tool results
        standalone_tool_indices = [i for i in tool_indices if i not in recent_tool_call_ids]
        recent_tool_indices.update(standalone_tool_indices[-num_tool:] if num_tool > 0 else [])
        
        # Build set of all indices to keep
        keep_indices = set(system_indices) if self.config["preserve_system_prompt"] else set()
        keep_indices.update(recent_user_indices)
        keep_indices.update(recent_assistant_indices)
        keep_indices.update(recent_tool_indices)
        
        # Rebuild history in original order
        pruned_history = [history[i] for i in range(len(history)) if i in keep_indices]
        
        logger.info(
            f"Pruned session {session_id}: "
            f"{len(history)} -> {len(pruned_history)} messages"
        )
        
        await session_manager.replace_history(session_id, pruned_history)
        
        return {
            "pruned": True,
            "original_count": len(history),
            "pruned_count": len(history) - len(pruned_history),
            "remaining_count": len(pruned_history),
            "preserved_user": len(recent_user_indices),
            "preserved_assistant": len(recent_assistant_indices),
            "preserved_tool": len(recent_tool_indices),
        }
    
    async def should_prune(self, session_id: str) -> bool:
        """Check if a session should be pruned."""
        session = await session_manager.get_session(session_id)
        history = session.get("history", [])
        return len(history) > self.config["max_messages"]


class SessionCompactor:
    """Compacts old conversation history into summaries."""
    
    def __init__(self, max_context_tokens: Optional[int] = None, model: Optional[str] = None):
        self.max_context_tokens = self._resolve_max_context_tokens(max_context_tokens, model=model)

    @staticmethod
    def _resolve_max_context_tokens(max_context_tokens: Optional[int], model: Optional[str] = None) -> int:
        try:
            if max_context_tokens is not None and int(max_context_tokens) > 0:
                return int(max_context_tokens)
        except Exception:
            pass
        llm_cfg = config.llm if isinstance(config.llm, dict) else {}
        configured_model = str(model or llm_cfg.get("model") or "").strip()
        limits = resolve_model_limits(configured_model or None)
        prompt_tokens = int(limits.get("max_prompt_tokens") or 0)
        if prompt_tokens > 0:
            return prompt_tokens
        # Emergency fallback only when limits cannot be resolved.
        return 60000
    
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
        
        await session_manager.replace_history(session_id, [summary_msg] + recent_messages)
        
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
