"""Agent core for OpenClaw Mini."""

from typing import Any, Dict, List, Optional

from openclaw_mini.agent.llm import llm_client
from openclaw_mini.session.manager import session_manager


class Agent:
    """Simple agent for processing messages and generating responses."""

    def __init__(self, system_prompt: Optional[str] = None):
        self.system_prompt = system_prompt or (
            "You are a helpful AI assistant. "
            "Keep your responses concise and helpful."
        )

    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
    ) -> str:
        """Process a user message and generate a response."""
        # Get conversation history
        history = session_manager.get_history(session_id)

        # Add user message to history
        session_manager.add_message(session_id, "user", message)

        # Get context from history
        messages = session_manager.get_history(session_id)

        # Call LLM
        response = await llm_client.chat(messages, self.system_prompt)

        # Add assistant response to history
        session_manager.add_message(session_id, "assistant", response)

        return response

    async def process_with_context(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> str:
        """Process a message with additional context."""
        # Build full message with context
        full_message = f"Context: {context}\n\nUser: {message}"
        return await self.process(full_message, context.get("session_id", "default"))

    def clear_session(self, session_id: str) -> None:
        """Clear a session's history."""
        session_manager.clear_history(session_id)

    def get_session_info(self, session_id: str) -> Dict[str, any]:
        """Get information about a session."""
        info = session_manager.get_session_info(session_id)
        return info or {"error": "Session not found"}


# Global agent instance
agent = Agent()
