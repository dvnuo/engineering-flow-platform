"""Agent core for OpenClaw Mini."""

import json
import logging
from typing import Any, Dict, List, Optional

from openclaw_mini.agent.llm import llm_client
from openclaw_mini.session.manager import session_manager
from openclaw_mini.skills.executor import (
    skills_executor,
    SkillResult,
    get_tools_schemas,
    execute_tool_by_name,
)

logger = logging.getLogger(__name__)


class Agent:
    """Agent for processing messages and executing tools."""

    def __init__(self, system_prompt: Optional[str] = None):
        default_prompt = """You are a helpful AI assistant. You can use tools to help answer questions and perform tasks.

Available tools:
- exec: Execute shell commands
- read: Read file contents
- write: Create or overwrite files
- edit: Make precise edits to files
- web_search: Search the web
- web_fetch: Fetch webpage content
- image: Analyze images

When you need to use a tool, respond with a tool call in the specified format."""
        
        self.system_prompt = system_prompt or default_prompt

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

        # Add user message to history
        session_manager.add_message(session_id, "user", message)

        # Get conversation history
        messages = session_manager.get_history(session_id)

        # Get tool schemas
        tools = get_tools_schemas()

        # Call LLM with tools
        result = await llm_client.chat(messages, self.system_prompt, tools=tools)
        content = result.get("content", "")
        tool_calls = result.get("tool_calls", [])

        # If LLM wants to use a tool, execute it
        if tool_calls:
            logger.info(f"LLM requested {len(tool_calls)} tool calls")
            
            # Build messages for tool execution
            messages.append({"role": "assistant", "content": content})
            
            # Execute each tool call
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id", "")
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                args = json.loads(function.get("arguments", "{}"))
                
                logger.info(f"Executing tool: {tool_name} with args: {args}")
                
                # Execute tool
                tool_result = await execute_tool_by_name(tool_name, **args)
                
                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "content": str(tool_result),
                })

            # Get final response from LLM
            final_result = await llm_client.chat(messages, self.system_prompt, tools=tools)
            content = final_result.get("content", "")

        # Add assistant response to history
        session_manager.add_message(session_id, "assistant", content)

        return content

    async def _execute_skill(
        self,
        skill_name: str,
        message: str,
        session_id: str,
    ) -> str:
        """Execute a skill and return the result."""
        try:
            result = await skills_executor.execute_skill(
                skill_name,
                message=message,
                session_id=session_id,
            )

            if result.success:
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
