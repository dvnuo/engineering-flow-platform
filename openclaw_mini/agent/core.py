"""Agent core for OpenClaw Mini."""

import json
import logging
from typing import Any, Dict, List, Optional

from openclaw_mini.agent.llm import llm_client
from openclaw_mini.session.manager import session_manager
from openclaw_mini.skills.executor import skills_executor, SkillResult

logger = logging.getLogger(__name__)


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
        # Check if message matches a skill
        skill_name = skills_executor.match_skill(message)
        if skill_name:
            logger.info(f"Matched skill: {skill_name} for message: {message[:50]}...")
            return await self._execute_skill(skill_name, message, session_id)

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

    async def _execute_skill(
        self,
        skill_name: str,
        message: str,
        session_id: str,
    ) -> str:
        """Execute a skill and return the result."""
        try:
            # Parse skill parameters from message (simplified)
            result = await skills_executor.execute_skill(
                skill_name,
                message=message,
                session_id=session_id,
            )

            if result.success:
                # Format the output
                if result.data:
                    return f"✅ {result.output}\n\n```\n{json.dumps(result.data, indent=2, ensure_ascii=False)}\n```"
                return f"✅ {result.output}"
            else:
                return f"❌ {result.error}"

        except Exception as e:
            logger.error(f"Skill execution failed: {e}")
            return f"❌ 执行失败: {str(e)}"

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
