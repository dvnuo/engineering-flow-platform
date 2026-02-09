"""Agent core implementation following modern agent loop patterns."""

import json
import logging
import os
import platform
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.agents.heartbeat import get_heartbeat, start_heartbeat, stop_heartbeat
from src.agents.llm import llm_client
from src.agents.memory import memory_system
from src.agents.thinking import ThinkLevel, normalize_think_level, format_runtime_info
from src.config import config
from src.sessions.manager import session_manager
from src.agents.executor import (
    skills_executor,
    SkillResult,
    get_tools_schemas,
    execute_tool_by_name,
)

logger = logging.getLogger(__name__)

# Debug logging is enabled when logger.level is DEBUG
# Set log_level: DEBUG in config.yaml to enable
# When DEBUG, complete input/output is logged (no truncation)

_DEBUG_ENABLED = None  # Lazy initialization


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled (logger is DEBUG level)."""
    global _DEBUG_ENABLED
    if _DEBUG_ENABLED is None:
        _DEBUG_ENABLED = logger.isEnabledFor(logging.DEBUG)
    return _DEBUG_ENABLED


def _format_content(content: str, prefix: str = "", max_length: int = 500) -> str:
    """Format content for logging. Truncated when debug is enabled, hidden when disabled."""
    if not content:
        return f"{prefix}(empty)"
    if _is_debug_enabled():
        # Debug enabled: show truncated content for readability
        if len(content) > max_length:
            return f"{prefix}{content[:max_length]}... [{len(content) - max_length} chars truncated]"
        return f"{prefix}{content}"
    # Debug disabled: don't log content at all
    return f"{prefix}(content hidden)"


class Agent:
    """Agent for processing messages with ReAct pattern (Reasoning + Acting)."""

    def __init__(
        self, 
        system_prompt: Optional[str] = None, 
        session_id: str = "default",
        think_level: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ):
        # Resolve thinking level
        self.think_level = normalize_think_level(think_level) or ThinkLevel.OFF
        
        # Store model for later use
        self.model = model
        
        # Initialize heartbeat if enabled
        self._heartbeat_enabled = config.heartbeat.get("enabled", False)
        if self._heartbeat_enabled:
            check_interval = config.heartbeat.get("check_interval", 300)
            self._heartbeat = get_heartbeat(self.think_level)
            # Set the check interval from config
            self._heartbeat.check_interval = check_interval
            logger.info(f"Heartbeat enabled - think_level={self.think_level.value}, interval={check_interval}s")
        
        # Build Engineering Flow Platform-style system prompt
        # NOTE: get_tools_schema() already includes INTEGRATION_TOOLS (JIRA + Confluence + GitHub tools)
        base_tools = get_tools_schemas()
        self.tools = base_tools  # Already contains all tools from TOOLS + INTEGRATION_TOOLS
        
        # Debug logging for tools initialization
        logger.debug(f"Tools initialized: count={len(self.tools)}, "
                    f"names={[t['function']['name'] for t in self.tools]}, "
                    f"think_level={self.think_level.value}")
        
        # Human-readable tool list
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
        
        # Build runtime information
        runtime_info = format_runtime_info(
            host="engineering-flow-platform",
            os_info=f"{platform.system()} {platform.release()}",
            arch=platform.machine(),
            node=platform.python_version(),
            model=self.model or "",
            default_model="",
            channel="",
            capabilities=[],
            think_level=self.think_level,
        )
        
        if system_prompt:
            # Custom prompt provided
            self.system_prompt = system_prompt
            prompt_source = "custom"
        elif memory_prompt:
            # Use memory files + basic structure
            self.system_prompt = f"""{memory_prompt}

## File & Execution Guidelines

When using file operations and shell commands, follow these guidelines:

1. **Read before Edit**: Always read a file before editing it
2. **Use Appropriate Tools**: Prefer `read`/`write`/`edit` over `exec cat`/`echo`
3. **Show Your Work**: Display file contents and explain changes
4. **Safety First**: Don't modify system files, don't delete without asking

See `workspace/TOOLS_FILE_EXEC.md` for detailed guidelines.

## Tooling

You have access to the following tools. When a user asks you to do something that requires a tool, you MUST use the appropriate tool. Do NOT explain how to do something—DO IT directly.

{tools_list}

## Runtime
{runtime_info}

## Current Date & Time
{current_time}
"""
            prompt_source = "memory"
        else:
            # Fallback to basic prompt
            self.system_prompt = f"""You are a helpful AI assistant that can execute commands, read/write files, and more.

## File & Execution Guidelines

When using file operations and shell commands, follow these guidelines:

1. **Read before Edit**: Always read a file before editing it
2. **Use Appropriate Tools**: Prefer `read`/`write`/`edit` over `exec cat`/`echo`
3. **Show Your Work**: Display file contents and explain changes
4. **Safety First**: Don't modify system files, don't delete without asking

See `workspace/TOOLS_FILE_EXEC.md` for detailed guidelines.

## Tooling

You have access to the following tools. When a user asks you to do something that requires a tool, you MUST use the appropriate tool. Do NOT explain how to do something—DO IT directly.

{tools_list}

## Runtime
{runtime_info}

## Guidelines

- When a user asks to run a command → use the exec tool
- When a user asks to read a file → use the read tool
- When a user asks to write/edit a file → use the write/edit tool
- Execute tools proactively—don't just talk about actions

## Current Date & Time
{current_time}
"""
            prompt_source = "fallback"
        
        # Debug logging for system prompt construction
        logger.debug(f"System prompt constructed: session={session_id}, "
                    f"include_memory={include_memory}, source={prompt_source}, "
                    f"length={len(self.system_prompt)}, tools={len(self.tools)}, "
                    f"think_level={self.think_level.value}")

    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Process a user message with ReAct pattern.
        
        Flow: User → Fast Lane Commands → LLM (with tools) → Tool Call → Execute → Result → LLM → Final Response
        
        Args:
            reasoning_replay: Enable reasoning_replay to see model's internal reasoning.
                When enabled, includes model's thinking process in response.
                Default: Uses config.llm.reasoning_replay setting.
        
        Returns:
            Dict with:
                - response: str - The assistant's response
                - reasoning: str - Model's internal reasoning (if reasoning_replay enabled)
                - usage: Dict - Token usage from LLM API (if track_usage=True)
        """
        usage_data = {}
        
        # Add user message to history
        await session_manager.add_message(session_id, "user", message)

        # Get conversation history
        messages = await session_manager.get_history(session_id)

        # ===== FAST LANE COMMANDS =====
        from src.agents.fastlane import process_fastlane_command
        
        fastlane_response = await process_fastlane_command(message, self)
        if fastlane_response:
            # Fast lane command processed, return the response
            await session_manager.add_message(session_id, "assistant", fastlane_response)
            return {"response": fastlane_response, "usage": usage_data}
        # ===== END FAST LANE =====

        # ===== MESSAGE COMPACTION =====
        # Check if messages need compaction to fit within token limits
        from src.agents.compaction import (
            compact_messages,
            estimate_messages_tokens,
            resolve_context_window_tokens,
            CompactionStats,
        )
        
        # Get max tokens from config or use default
        max_tokens = config.llm.get("max_tokens", 4000)
        
        # Estimate current token count
        # Convert session messages to AgentMessage format
        from src.agents.compaction import AgentMessage
        
        agent_messages = [
            AgentMessage(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                timestamp=msg.get("timestamp"),
            )
            for msg in messages
        ]
        
        current_tokens = estimate_messages_tokens(agent_messages)
        
        # Log compaction info
        logger.info(
            f"[{session_id}] Compaction check: "
            f"current_tokens={current_tokens}, max_tokens={max_tokens}"
        )
        
        # Compact messages if over limit
        compaction_stats: CompactionStats = None
        if current_tokens > max_tokens:
            logger.info(
                f"[{session_id}] Messages exceed token limit, compacting..."
            )
            
            # Get context window for the model
            context_window = resolve_context_window_tokens(
                config.llm.get("model", "gpt-3.5-turbo")
            )
            
            # Compact messages
            compacted_messages, compaction_stats = await compact_messages(
                messages=agent_messages,
                max_tokens=max_tokens,
                context_window=context_window,
                recent_count=5,
            )
            
            # Update messages for LLM call
            # Convert back to dict format for LLM
            messages = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                }
                for msg in compacted_messages
            ]
            
            logger.info(
                f"[{session_id}] Compaction complete: "
                f"kept_tokens={compaction_stats.kept_tokens}, "
                f"dropped_messages={compaction_stats.dropped_messages}, "
                f"summary={compaction_stats.summary[:100] if compaction_stats.summary else 'N/A'}..."
            )
        # ===== END MESSAGE COMPACTION =====

        # Debug logging for message received
        if _is_debug_enabled():
            logger.debug(f"=== [AGENT] MESSAGE RECEIVED ===")
            logger.debug(f"Session: {session_id}")
            logger.debug(f"User: {user_name}")
            logger.debug(f"Message length: {len(message)} chars")
            logger.debug(f"Message preview: {_format_content(message, max_length=300)}")
            logger.debug(f"System prompt length: {len(self.system_prompt)} chars")
            logger.debug(f"System prompt preview: {_format_content(self.system_prompt, max_length=300)}")
            logger.debug(f"Tools count: {len(self.tools)}")
            logger.debug(f"History messages: {len(messages)}")

        # ===== REACT PATTERN =====

        # Log thinking level for subagent tracking
        logger.info(f"[{session_id}] think_level={self.think_level.value}, model={self.model or ''}")
        
        # Resolve reasoning_replay from config if not provided
        enable_reasoning = reasoning_replay if reasoning_replay is not None else config.llm.get('reasoning_replay', False)
        logger.info(f"[{session_id}] reasoning_replay={enable_reasoning}")
        
        # Step 1: Call LLM with tools
        logger.debug(f"Calling LLM with {len(self.tools)} tools")
        
        llm_result = await llm_client.chat(
            messages=messages,
            system_prompt=self.system_prompt,
            tools=self.tools,
            reasoning_replay=enable_reasoning,
        )
        
        # Debug logging for LLM response
        if _is_debug_enabled():
            logger.debug(f"=== [AGENT] LLM RESPONSE ===")
            content = llm_result.get('content') or ''
            logger.debug(f"Content length: {len(content)} chars")
            logger.debug(f"Content: {_format_content(content, max_length=1000)}")
            
            # Log reasoning if present
            reasoning = llm_result.get('reasoning')
            if reasoning:
                logger.debug(f"Reasoning length: {len(reasoning)} chars")
                logger.debug(f"Reasoning: {_format_content(reasoning, max_length=2000)}")
            
            tool_calls = llm_result.get('tool_calls', [])
            logger.debug(f"Tool calls: {len(tool_calls)}")
            for tc in tool_calls:
                tc_name = tc.get('function', {}).get('name', 'unknown')
                tc_args = tc.get('function', {}).get('arguments', '')
                logger.debug(f"  - {tc_name}: {_format_content(tc_args, max_length=300)}")
            
            usage = llm_result.get('usage', {})
            logger.debug(f"Usage: {usage}")
        
        # Track usage if enabled
        if track_usage:
            usage_data = llm_result.get("usage", {})
        
        content = (llm_result.get("content") or "").strip()
        tool_calls = llm_result.get("tool_calls", [])
        
        # If no tool calls, return directly
        if not tool_calls:
            await session_manager.add_message(session_id, "assistant", content)
            result = {"response": content, "usage": usage_data}
            if enable_reasoning:
                result["reasoning"] = llm_result.get("reasoning", "")
            return result
        
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
            
            # Debug logging for tool result
            if _is_debug_enabled():
                logger.debug(f"=== [AGENT] TOOL RESULT ===")
                logger.debug(f"Tool: {tool_name}")
                logger.debug(f"Success: {tool_result.success}")
                logger.debug(f"Result: {_format_content(str(tool_result), max_length=1000)}")
            
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
        
        # Debug logging for final response
        if _is_debug_enabled():
            logger.debug(f"=== [AGENT] FINAL RESPONSE ===")
            logger.debug(f"Content length: {len(final_content)} chars")
            logger.debug(f"Content: {_format_content(final_content, max_length=1000)}")
            logger.debug(f"Usage: {final_result.get('usage', {})}")
        
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
        await session_manager.add_message(session_id, "assistant", final_content)
        
        # Return response with reasoning if enabled
        result = {"response": final_content, "usage": usage_data}
        if enable_reasoning:
            result["reasoning"] = llm_result.get("reasoning", "")
        
        return result

    async def _execute_skill(
        self,
        skill_name: str,
        message: str,
        session_id: str,
    ) -> str:
        """Execute a skill and return the result."""
        try:
            # Parse command and arguments from message
            # Support formats:
            # - "git pull repo_path=/path" (natural language)
            # - "git command=pull repo_path=/path" (explicit command)
            parts = message.split()
            if not parts:
                return "Error: Empty message"
            
            # Extract sub-command and arguments
            sub_command = None
            args = {}
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    value = value.strip("'\"")
                    if value.isdigit():
                        value = int(value)
                    args[key] = value
                elif sub_command is None:
                    # First non-key=value part is the command
                    sub_command = part.lower()
            
            # Default command if not found
            if sub_command is None:
                sub_command = "status"
            
            result = await skills_executor.execute_skill(
                skill_name,
                command=sub_command,
                message=message,
                **args
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

    async def clear_session(self, session_id: str) -> None:
        """Clear a session's history."""
        await session_manager.clear_history(session_id)

    async def get_session_info(self, session_id: str) -> Dict[str, any]:
        """Get information about a session."""
        info = await session_manager.get_session_info(session_id)
        return info or {"error": "Session not found"}


# Global agent instance
agent = Agent()
