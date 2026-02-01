"""Agent core for OpenClaw Mini - Following OpenClaw's Agent Loop pattern."""

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
    """Agent for processing messages with ReAct pattern (Reasoning + Acting)."""

    def __init__(self, system_prompt: Optional[str] = None):
        # Build OpenClaw-style system prompt
        tools = get_tools_schemas()
        
        # Human-readable tool list (following OpenClaw's Tooling section)
        tools_list = "\n".join([
            f"- **{t['function']['name']}**: {t['function'].get('description', '')}"
            for t in tools
        ])
        
        default_prompt = f"""You are a helpful AI assistant that can execute commands, read/write files, search the web, and more.

## Tooling

You have access to the following tools. When a user asks you to do something that requires a tool, you MUST use the appropriate tool. Do NOT explain how to do something—DO IT directly.

{tools_list}

## Guidelines

- When a user asks to run a command → use the exec tool
- When a user asks to read a file → use the read tool
- When a user asks to write/edit a file → use the write/edit tool
- When a user asks to search → use web_search tool
- When a user asks to fetch a webpage → use web_fetch tool
- Execute tools proactively—don't just talk about actions

## Current Date & Time

Time zone: Asia/Hong_Kong

## Runtime

You are running in a Linux environment. Your workspace is /root/.openclaw/workspace.

---
Reply with a tool call (in JSON format) when you need to use a tool, or reply with your answer directly when no tool is needed.

When you use a tool, use this format:
{{
  "name": "tool-name",
  "arguments": {{"arg1": "value1", "arg2": "value2"}}
}}
"""
        
        self.system_prompt = system_prompt or default_prompt
        self.tools = tools

    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
    ) -> str:
        """Process a user message with ReAct pattern.
        
        Flow: User → LLM (with tools) → Tool Call → Execute → Result → LLM → Final Response
        """
        # Check if message matches a skill first
        skill_name = skills_executor.match_skill(message)
        if skill_name:
            logger.info(f"Matched skill: {skill_name}")
            return await self._execute_skill(skill_name, message, session_id)

        # Add user message to history
        session_manager.add_message(session_id, "user", message)

        # Get conversation history
        messages = session_manager.get_history(session_id)

        # ===== REACT PATTERN =====
        
        # Step 1: Call LLM with tools
        logger.debug(f"Calling LLM with {len(self.tools)} tools")
        result = await llm_client.chat(
            messages=messages,
            system_prompt=self.system_prompt,
            tools=self.tools
        )
        
        content = result.get("content", "").strip()
        tool_calls = result.get("tool_calls", [])
        
        # If no tool calls, return directly
        if not tool_calls:
            session_manager.add_message(session_id, "assistant", content)
            return content
        
        logger.info(f"LLM requested {len(tool_calls)} tool calls")
        
        # Step 2-4: Execute each tool and collect results
        # IMPORTANT: assistant message must contain tool_calls for OpenAI API
        assistant_msg = {"role": "assistant"}
        
        # If LLM returned tool_calls, include them in the assistant message
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
            # Content can be empty when using tool_calls
            assistant_msg["content"] = content if content else None
        
        messages.append(assistant_msg)
        
        # Execute each tool call
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("id", "unknown")
            function = tool_call.get("function", {})
            tool_name = function.get("name", "")
            
            try:
                args = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            
            logger.info(f"Executing tool: {tool_name} with args: {args}")
            
            # Execute the tool
            tool_result = await execute_tool_by_name(tool_name, **args)
            
            # Add tool result - MUST follow the assistant message with tool_calls
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": str(tool_result),
            })
            
            logger.info(f"Tool result: {str(tool_result)[:200]}")

        # Step 5: Get final response from LLM
        final_result = await llm_client.chat(
            messages=messages,
            system_prompt=self.system_prompt,
            tools=self.tools
        )
        
        final_content = final_result.get("content", "").strip()
        
        # Add final response to history
        session_manager.add_message(session_id, "assistant", final_content)
        
        return final_content

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
