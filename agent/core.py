"""Agent core for OpenClaw Mini - Following OpenClaw's Agent Loop pattern."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.llm import llm_client
from agent.memory import memory_system
from session.manager import session_manager
from skills.executor import (
    _get_executor,
    SkillResult,
    get_tools_schemas,
    execute_tool_by_name,
)
from tools.integration import INTEGRATION_TOOLS

logger = logging.getLogger(__name__)


class Agent:
    """Agent for processing messages with ReAct pattern (Reasoning + Acting)."""

    def __init__(self, system_prompt: Optional[str] = None, session_id: str = "default"):
        # Build OpenClaw-style system prompt
        base_tools = get_tools_schemas()
        # Combine base tools with integration tools (Jira, Confluence)
        self.tools = base_tools + INTEGRATION_TOOLS
        
        # Human-readable tool list (following OpenClaw's Tooling section)
        tools_list = "\n".join([
            f"- **{t['function']['name']}**: {t['function'].get('description', '')}"
            for t in self.tools
        ])
        
        # Load memory files for system prompt
        # For main session (includes memory), include MEMORY.md
        # For other sessions, exclude memory for security
        include_memory = (session_id == "main" or session_id.startswith("main") or 
                         session_id == "webchat" or "discord" in session_id)
        
        memory_prompt = memory_system.build_system_prompt(include_memory=include_memory)
        
        # Current date/time for the prompt
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        if system_prompt:
            # Custom prompt provided
            self.system_prompt = system_prompt
        elif memory_prompt:
            # Use memory files + basic structure
            self.system_prompt = f"""{memory_prompt}

## Tooling

You have access to the following tools. When a user asks you to do something that requires a tool, you MUST use the appropriate tool. Do NOT explain how to do something—DO IT directly.

{tools_list}

## Current Date & Time
{current_time}
"""
        else:
            # Fallback to basic prompt
            self.system_prompt = f"""You are a helpful AI assistant that can execute commands, read/write files, search the web, and more.

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
{current_time}
"""
        
        self.tools = self.tools  # Already set above

    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
    ) -> Dict[str, Any]:
        """Process a user message with ReAct pattern.
        
        Flow: User → LLM (with tools) → Tool Call → Execute → Result → LLM → Final Response
        
        Returns:
            Dict with:
                - response: str - The assistant's response
                - usage: Dict - Token usage from LLM API (if track_usage=True)
        """
        usage_data = {}
        
        # Check if message matches a skill first
        skill_name = _get_executor().match_skill(message)
        if skill_name:
            logger.info(f"Matched skill: {skill_name}")
            result = await self._execute_skill(skill_name, message, session_id)
            return {"response": result, "usage": usage_data}

        # Add user message to history
        session_manager.add_message(session_id, "user", message)

        # Get conversation history
        messages = session_manager.get_history(session_id)

        # ===== REACT PATTERN =====
        
        # Step 1: Call LLM with tools
        logger.debug(f"Calling LLM with {len(self.tools)} tools")
        llm_result = await llm_client.chat(
            messages=messages,
            system_prompt=self.system_prompt,
            tools=self.tools
        )
        
        # Track usage if enabled
        if track_usage:
            usage_data = llm_result.get("usage", {})
        
        content = (llm_result.get("content") or "").strip()
        tool_calls = llm_result.get("tool_calls", [])
        
        # If no tool calls, return directly
        if not tool_calls:
            session_manager.add_message(session_id, "assistant", content)
            return {"response": content, "usage": usage_data}
        
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
        
        final_content = (final_result.get("content") or "").strip()
        
        # Track final usage and merge
        if track_usage:
            final_usage = final_result.get("usage", {})
            if usage_data:
                usage_data = {
                    "prompt_tokens": usage_data.get("prompt_tokens", 0) + final_usage.get("prompt_tokens", 0),
                    "completion_tokens": usage_data.get("completion_tokens", 0) + final_usage.get("completion_tokens", 0),
                    "total_tokens": usage_data.get("total_tokens", 0) + final_usage.get("total_tokens", 0),
                }
            else:
                usage_data = final_usage
        
        # Add final response to history
        session_manager.add_message(session_id, "assistant", final_content)
        
        return {"response": final_content, "usage": usage_data}

    async def _execute_skill(
        self,
        skill_name: str,
        message: str,
        session_id: str,
    ) -> str:
        """Execute a skill and return the result."""
        try:
            # Note: session_id is used for tracking but not passed to skills
            # Skills don't accept session_id as a parameter
            result = await _get_executor().execute_skill(
                skill_name,
                message=message,
            )

            if result.success:
                if result.data:
                    return f"Done! {result.output}\n\n```\n{json.dumps(result.data, indent=2, ensure_ascii=False)}\n```"
                return f"Done! {result.output}"
            else:
                return f"Error: {result.error}"

        except Exception as e:
            logger.error(f"Skill execution failed: {e}")
            return f"Execution failed: {str(e)}"

    async def process_with_context(
        self,
        message: str,
        context: Dict[str, Any],
    ) -> str:
        """Process a message with additional context."""
        full_message = f"Context: {context}\n\nUser: {message}"
        result = await self.process(full_message, context.get("session_id", "default"))
        return result["response"]

    def clear_session(self, session_id: str) -> None:
        """Clear a session's history."""
        session_manager.clear_history(session_id)

    def get_session_info(self, session_id: str) -> Dict[str, any]:
        """Get information about a session."""
        info = session_manager.get_session_info(session_id)
        return info or {"error": "Session not found"}


# Global agent instance
agent = Agent()
