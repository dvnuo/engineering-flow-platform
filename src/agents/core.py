"""Agent core implementation following modern agent loop patterns."""

import contextvars
import json
import logging
import os
import platform
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

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
    ToolResult,
)

logger = logging.getLogger(__name__)

# Context variable for skill workdir - async-safe
_skill_workdir: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('skill_workdir', default=None)


def set_skill_workdir(path: Optional[str]) -> None:
    """Set the current skill working directory (async-safe)."""
    _skill_workdir.set(path)
    if path:
        logger.debug(f"[Skill] Workdir: {path}")


def get_skill_workdir() -> Optional[str]:
    """Get the current skill working directory (async-safe)."""
    return _skill_workdir.get()


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
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Process a user message with ReAct pattern.
        
        Flow: User → Fast Lane Commands → LLM (with tools) → Tool Call → Execute → Result → LLM → Final Response
        
        Args:
            reasoning_replay: Enable reasoning_replay to see model's internal reasoning.
                When enabled, includes model's thinking process in response.
                Default: Uses config.llm.reasoning_replay setting.
            stream_callback: Optional callback for streaming events (tool calls, progress, etc.)
        
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

        # ===== SKILL MATCHING (FR-1, FR-2) =====
        from src.skills import skill_registry, get_tracer
        
        # Initialize skill registry if needed
        if not skill_registry._initialized:
            skill_registry.load_skills()
        
        # Match user message against skill triggers
        matched_skills = skill_registry.match_skill(message)
        
        # Start execution tracing
        tracer = get_tracer()
        execution_id = tracer.start_execution(
            session_id=session_id,
            user_message=message,
            matched_skill=matched_skills[0].name if matched_skills else None,
        )
        
        # Build skill prompt if matched (FR-3: Dynamic Skill Injection)
        skill_prompt = ""
        allowed_tools = set()  # Tool whitelist per skill (FR-5)
        
        if matched_skills:
            # Use the best match
            best_skill = matched_skills[0]
            logger.info(f"[Skill] Matched skill: {best_skill.name}")
            
            # Set skill workdir for exec tool (async-safe via contextvars)
            if best_skill.path:
                set_skill_workdir(best_skill.path)
                logger.info(f"[Skill] Workdir: {best_skill.path}")
            
            skill_prompt = skill_registry.get_skill_prompt(best_skill)
            allowed_tools = set(best_skill.tools)
            
            # Log matched skill
            tracer.log_tool_call(
                tool_name="skill_matched",
                arguments={"skill": best_skill.name},
                result=f"Matched skill: {best_skill.name}",
            )
        # ===== END SKILL MATCHING =====

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
        
        # ===== BUILD EFFECTIVE SYSTEM PROMPT (with Skill Guidance + Semantic Context) =====
        effective_system_prompt = self.system_prompt
        
        # FR-3: Dynamic Skill Injection - Include skill prompt from FIRST call
        if skill_prompt:
            effective_system_prompt = f"{self.system_prompt}\n\n## Skill Guidance\n\n{skill_prompt}"
            logger.info(f"[Skill] Injected skill guidance for: {matched_skills[0].name}")
        
        # Semantic Context Search - Find relevant memory context
        semantic_context = ""
        try:
            semantic_context = memory_system.build_context_with_search(
                query=message,
                include_memory=include_memory,
                limit=3,
                score_threshold=0.3,
            )
            if semantic_context:
                effective_system_prompt = f"{effective_system_prompt}\n\n## Relevant Context (Semantic Search)\n\n{semantic_context}"
                logger.info(f"[Memory] Added semantic context from search")
        except Exception as e:
            logger.debug(f"[Memory] Semantic search failed: {e}")
        
        # ===== TOOL LOOP (REACT Pattern) =====
        # Continue calling LLM until it stops requesting tools
        # This is the proper agent loop, not a single-step execution
        
        max_tool_iterations = 10  # Prevent infinite loops
        iteration = 0
        
        # Helper function to send stream events
        # Supports both simple callbacks and asyncio.Queue
        def send_event(event_type: str, data: dict):
            """Send event via stream_callback and event bus."""
            # Also log to tracer for persistence
            if event_type == 'llm_thinking':
                try:
                    from src.skills import get_tracer
                    tracer_instance = get_tracer()
                    message = data.get('message', '')
                    if message:
                        tracer_instance.log_thinking(message)
                except Exception:
                    pass  # Tracer may not be initialized
            
            # Emit to event bus for WebSocket clients
            try:
                from src.gateway.event_bus import emit_agent_event_sync
                emit_agent_event_sync(event_type, data)
            except Exception as e:
                logger.info(f"Event bus emit error: {e}")
            
            # Also send via callback if provided
            if stream_callback:
                import json
                event = json.dumps({"type": event_type, **data})
                try:
                    # Check if it's an asyncio.Queue
                    if hasattr(stream_callback, 'put'):
                        # It's a queue - put the event (will be read by API)
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                # We're in an async context, schedule the put
                                asyncio.create_task(stream_callback.put(event))
                            else:
                                # Loop not running, put directly
                                stream_callback.put_nowait(event)
                        except RuntimeError:
                            stream_callback.put_nowait(event)
                    else:
                        # Regular callback
                        stream_callback(event)
                except Exception as e:
                    logger.debug(f"Stream event error: {e}")
        
        # Send skill matched event
        if matched_skills:
            send_event("skill_matched", {"skill": matched_skills[0].name})
        
        while iteration < max_tool_iterations:
            iteration += 1
            
            # Send iteration start event
            send_event("iteration_start", {"iteration": iteration, "total": max_tool_iterations})
            
            # Step 1: Call LLM with tools (include skill_prompt from first call)
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM")
            send_event("llm_thinking", {"message": "LLM is thinking..."})
            
            llm_result = await llm_client.chat(
                messages=messages,
                system_prompt=effective_system_prompt,
                tools=self.tools,
                reasoning_replay=enable_reasoning,
            )
            
            # Debug logging for LLM response
            if _is_debug_enabled():
                logger.debug(f"=== [AGENT] LLM RESPONSE (iter {iteration}) ===")
                content = llm_result.get('content') or ''
                logger.debug(f"Content length: {len(content)} chars")
                
                tool_calls = llm_result.get('tool_calls', [])
                logger.debug(f"Tool calls: {len(tool_calls)}")
                for tc in tool_calls:
                    tc_name = tc.get('function', {}).get('name', 'unknown')
                    logger.debug(f"  - {tc_name}")
            
            # Track usage
            if track_usage:
                iter_usage = llm_result.get("usage", {})
                if usage_data:
                    usage_data = {
                        "prompt_tokens": usage_data.get("prompt_tokens", 0) + iter_usage.get("prompt_tokens", 0),
                        "completion_tokens": usage_data.get("completion_tokens", 0) + iter_usage.get("completion_tokens", 0),
                        "total_tokens": usage_data.get("total_tokens", 0) + iter_usage.get("total_tokens", 0),
                    }
                else:
                    usage_data = iter_usage
            
            content = (llm_result.get("content") or "").strip()
            tool_calls = llm_result.get("tool_calls", [])
            
            # If no tool calls, we're done - return the response
            if not tool_calls:
                await session_manager.add_message(session_id, "assistant", content)
                result = {"response": content, "usage": usage_data}
                if enable_reasoning:
                    result["reasoning"] = llm_result.get("reasoning", "")
                
                # Send completion event
                send_event("complete", {
                    "response": content[:500] if content else "",
                    "total_iterations": iteration
                })
                
                # Complete execution tracing
                tracer.complete_execution(content)
                
                # Get events for UI
                from src.skills import get_tracer
                tracer_instance = get_tracer()
                events = tracer_instance.get_events_for_ui(limit=10)
                result["events"] = events
                
                return result
            
            logger.info(f"[Tool Loop] Iteration {iteration}: LLM requested {len(tool_calls)} tool calls")
            
            # Step 2: Execute each tool and collect results
            # IMPORTANT: assistant message must contain tool_calls for OpenAI API
            assistant_msg = {"role": "assistant"}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
                assistant_msg["content"] = content if content else None
            
            messages.append(assistant_msg)
            
            # Execute each tool call
            for tool_call in tool_calls:
                tool_call_id = tool_call.get("id", "unknown")
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                
                # Parse arguments
                try:
                    args = json.loads(function.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                
                # Send tool call start event
                send_event("tool_call", {
                    "tool": tool_name,
                    "args": args,
                    "status": "executing"
                })
                
                # ===== CONFIRMATION GATE (FR-4) =====
                # Check if this is a write operation that requires confirmation
                write_tools = {'github_comment_pr', 'github_add_comment', 'jira_add_comment', 
                              'git_commit', 'git_push', 'jira_transition'}
                
                if tool_name in write_tools:
                    logger.info(f"[Confirmation] Tool '{tool_name}' requires confirmation")
                    send_event("confirmation", {
                        "tool": tool_name,
                        "message": f"Write operation '{tool_name}' requires confirmation",
                        "auto_confirm": True
                    })
                    # For now, auto-confirm in default mode (can be made interactive later)
                    # TODO: Implement actual user confirmation flow
                    logger.info(f"[Confirmation] Auto-confirming write operation: {tool_name}")
                
                # Execute the tool
                logger.info(f"Executing tool: {tool_name} with args: {args}")
                tool_result = await execute_tool_by_name(tool_name, **args)
                tracer.log_tool_call(
                    tool_name=tool_name,
                    arguments=args,
                    result=str(tool_result),
                    success=tool_result.success,
                )
                
                # Send tool result event
                result_preview = str(tool_result)[:200]
                send_event("tool_result", {
                    "tool": tool_name,
                    "result": result_preview,
                    "success": tool_result.success
                })
                
                # Add tool result - MUST follow the assistant message with tool_calls
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(tool_result),
                })
                
                logger.info(f"Tool result: {str(tool_result)[:200]}")
            
            # Send iteration complete event
            send_event("iteration_end", {"iteration": iteration})
            
            # Loop continues - LLM will decide next action based on tool results
            # This is the key: don't return after one tool call, let LLM decide
        
        # Safety: max iterations reached
        logger.warning(f"[Tool Loop] Max iterations ({max_tool_iterations}) reached")
        await session_manager.add_message(session_id, "assistant", "Task completed after maximum iterations.")
        
        # Send completion event
        send_event("complete", {
            "response": "Task completed (max iterations reached)",
            "total_iterations": max_tool_iterations,
            "note": "max_iterations"
        })
        
        tracer.complete_execution("max_iterations_reached")
        
        # Get events for UI
        from src.skills import get_tracer
        tracer_instance = get_tracer()
        events = tracer_instance.get_events_for_ui(limit=10)
        
        return {"response": "Task completed (max iterations reached)", "usage": usage_data or {}, "events": events}

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
