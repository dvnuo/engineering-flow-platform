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
from src.memory.update_manager import MemoryUpdateManager
from src.agents.thinking import ThinkLevel, normalize_think_level, format_runtime_info
from src.config import config
from src.utils.truncate import truncate, truncate_with_count
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
        
        # Initialize Memory Update Manager for auto-memory
        self.memory_update_manager = MemoryUpdateManager(
            workspace=str(memory_system.workspace),
            llm_client=llm_client,
            memory_system=memory_system,
        )
        
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
        self.include_memory = (session_id == "main" or session_id.startswith("main") or 
                         session_id.startswith("webchat"))
        
        memory_prompt = memory_system.build_system_prompt(include_memory=self.include_memory)
        
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
                    f"include_memory={self.include_memory}, source={prompt_source}, "
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
        attached_images: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
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
        
        # Add user message to history (with attachments if any)
        extra = {}
        if attachments:
            extra["attachments"] = attachments  # Save file IDs, not base64
        await session_manager.add_message(
            session_id, "user", message,
            extra=extra if extra else None
        )

        # Get conversation history
        messages = await session_manager.get_history(session_id)
        
        # DEBUG: Log raw history
        logger.debug(f"[{session_id}] Raw history count: {len(messages)}")
        
        # Transform history messages to ensure proper format for LLM
        # This handles tool messages that were saved with tool_call_id
        transformed_messages = []
        for msg in messages:
            transformed = {"role": msg.get("role", "user"), "content": msg.get("content", "")}
            # Preserve tool_calls for assistant messages
            if msg.get("tool_calls"):
                transformed["tool_calls"] = msg["tool_calls"]
                logger.debug(f"[{session_id}] Found tool_calls in message: {msg.get('tool_calls')[0].get('id') if msg.get('tool_calls') else 'none'}")
            # Preserve tool_call_id for tool messages
            if msg.get("tool_call_id"):
                transformed["tool_call_id"] = msg["tool_call_id"]
                logger.debug(f"[{session_id}] Found tool_call_id in message: {msg.get('tool_call_id')}")
            transformed_messages.append(transformed)
        messages = transformed_messages
        
        logger.debug(f"[{session_id}] Transformed messages count: {len(messages)}")

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
        
        if matched_skills:
            # Use the best match
            best_skill = matched_skills[0]
            logger.info(f"[Skill] Matched skill: {best_skill.name}")
            
            # Set skill workdir for exec tool (async-safe via contextvars)
            if best_skill.path:
                set_skill_workdir(best_skill.path)
                logger.info(f"[Skill] Workdir: {best_skill.path}")
            
            skill_prompt = skill_registry.get_skill_prompt(best_skill)
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
        
        # Get context window for the model (not max_tokens which is for responses)
        model = config.llm.get("model", "gpt-5-mini")
        context_window = resolve_context_window_tokens(model)
        
        # Use 80% of context window as the limit for prompt history
        # This delays compaction by allowing more history before triggering it
        max_tokens = max(128000, int(context_window * 0.8))
        
        # Estimate current token count
        # Convert session messages to AgentMessage format
        from src.agents.compaction import AgentMessage
        
        agent_messages = [
            AgentMessage(
                role=msg.get("role", "user"),
                content=msg.get("content", ""),
                timestamp=msg.get("timestamp"),
                tool_calls=msg.get("tool_calls"),
                tool_use_id=msg.get("tool_call_id"),
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
            messages = []
            for msg in compacted_messages:
                msg_dict = {
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                }
                # Preserve tool_calls for assistant messages
                if msg.tool_calls:
                    msg_dict["tool_calls"] = msg.tool_calls
                # Preserve tool_call_id for tool messages
                if msg.tool_use_id:
                    msg_dict["tool_call_id"] = msg.tool_use_id
                messages.append(msg_dict)
            
            logger.info(
                f"[{session_id}] Compaction complete: "
                f"kept_tokens={compaction_stats.kept_tokens}, "
                f"dropped_messages={compaction_stats.dropped_messages}, "
                f"summary={truncate(compaction_stats.summary, 100) if compaction_stats.summary else 'N/A'}"
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
                include_memory=self.include_memory,
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
        
        # Get max iterations from config, default to 30
        max_tool_iterations = config.session.get("max_iterations", 30) if hasattr(config, 'session') else 30
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
        
        # ===== INJECT ATTACHED IMAGES =====
        if attached_images and len(messages) > 0:
            # Find the last user message and add images to it
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    user_content = messages[i].get("content", "")
                    # Build vision content for Responses API (input_image format)
                    if isinstance(user_content, list):
                        msg_content = []
                        for item in user_content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    msg_content.append({"type": "input_text", "text": item.get("text", "")})
                                elif item.get("type") == "image_url":
                                    img_url = item.get("image_url", {}).get("url") if isinstance(item.get("image_url"), dict) else str(item.get("image_url", ""))
                                    if img_url:
                                        msg_content.append({"type": "input_image", "image_url": img_url})
                                else:
                                    msg_content.append(item)
                    else:
                        msg_content = [{"type": "input_text", "text": str(user_content)}]
                    
                    for img in attached_images[:1]:
                        msg_content.append({"type": "input_image", "image_url": img})
                    messages[i] = {"role": "user", "content": msg_content}
                    logger.info(f"[Agent] Attached {min(len(attached_images), 1)} image(s) to user message (Responses format)")
                    break
        # ===== END IMAGE INJECTION =====

        # Convert messages to input_items for Responses API
        def _to_input_items(msgs):
            items = []
            for msg in msgs:
                role = msg.get("role", "user")
                if role == "tool":
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    conv = []
                    for item in content:
                        if isinstance(item, dict):
                            t = item.get("type", "")
                            if t in ("text", "input_text"):
                                # Only use input_text for user messages
                                if role == "user":
                                    conv.append({"type": "input_text", "text": item.get("text", "")})
                                else:
                                    # Assistant messages - use plain text
                                    conv.append(item.get("text", ""))
                            elif t in ("image_url", "input_image"):
                                img = item.get("image_url", {})
                                img_url = img.get("url") if isinstance(img, dict) else str(img)
                                if img_url:
                                    conv.append({"type": "input_image", "image_url": img_url})
                            else:
                                conv.append(item)
                        else:
                            # Plain text item
                            if role == "user":
                                conv.append({"type": "input_text", "text": str(item)})
                            else:
                                conv.append(str(item))
                    if conv:
                        items.append({"role": role, "content": conv})
                elif content:
                    # Plain text content - no wrapper for assistant
                    if role == "user":
                        items.append({"role": role, "content": [{"type": "input_text", "text": str(content)}]})
                    else:
                        items.append({"role": role, "content": str(content)})
            return items
        
        input_items = _to_input_items(messages)
        
        while iteration < max_tool_iterations:
            iteration += 1
            
            # Send iteration start event
            send_event("iteration_start", {"iteration": iteration, "total": max_tool_iterations})
            
            # Step 1: Call LLM with tools (include skill_prompt from first call)
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM")
            send_event("llm_thinking", {"message": "LLM is thinking..."})
            
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM with {len(input_items)} input_items")
            
            llm_result = await llm_client.responses(
                input_items=input_items,
                system_prompt=effective_system_prompt,
                tools=self.tools,
                reasoning_replay=enable_reasoning,
            )
            
            # Check for LLM configuration error
            if llm_result.get("error"):
                error_info = llm_result["error"]
                error_msg = error_info.get("message", "Unknown LLM error")
                logger.error(f"LLM error: {error_msg}")
                return {
                    "error": error_msg,
                    "error_type": error_info.get("type", "llm_error"),
                    "code": error_info.get("code", "")
                }
            
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
            function_calls = llm_result.get("function_calls", [])
            tool_calls = function_calls  # alias
            
            # If no function calls, we're done - return the response
            if not tool_calls:
                await session_manager.add_message(session_id, "assistant", content)
                result = {"response": content, "usage": usage_data}
                if enable_reasoning:
                    result["reasoning"] = llm_result.get("reasoning", "")
                
                # Send completion event
                send_event("complete", {
                    "response": truncate_with_count(content, 500),
                    "total_iterations": iteration
                })
                
                # Complete execution tracing
                tracer.complete_execution(content)
                
                # Get events for UI
                from src.skills import get_tracer
                tracer_instance = get_tracer()
                events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
                result["events"] = events
                
                # Trigger memory update (async, fire and forget)
                # We need to get the last user message and assistant response
                recent_messages = await session_manager.get_history(session_id)
                user_text = ""
                assistant_text = content
                for msg in reversed(recent_messages):
                    if msg.get("role") == "user":
                        user_text = msg.get("content", "")
                        break
                
                # Disabled: Turn-based memory writing (only backfill at startup)
                # if user_text and self.memory_update_manager:
                #     try:
                #         await self.memory_update_manager.on_turn_completed(
                #             session_id=session_id,
                #             turn_id=sum(1 for m in recent_messages if m.get("role") == "user"),
                #             user_text=user_text,
                #             assistant_text=assistant_text,
                #         )
                #     except Exception as e:
                #         logger.debug(f"Memory update failed: {e}")
                
                return result
            
            logger.info(f"[Tool Loop] Iteration {iteration}: LLM requested {len(tool_calls)} tool calls")
            
            # Add function_call to input_items for Responses API
            if function_calls:
                fc = function_calls[0]
                args = fc.get("arguments", {})
                args_str = args if isinstance(args, str) else json.dumps(args)
                input_items.append({
                    "type": "function_call",
                    "call_id": fc.get("call_id", ""),
                    "name": fc.get("name", ""),
                    "arguments": args_str,
                })
            
            # Save assistant message with tool_calls to history
            if tool_calls:
                await session_manager.add_message(
                    session_id, "assistant", 
                    content or "[Tool call]",
                    extra={"tool_calls": tool_calls}
                )
            
            # Execute each function call
            for fc in function_calls:
                call_id = fc.get("call_id", "")
                tool_name = fc.get("name", "")
                # Arguments can be dict or string - keep as string for API
                args_raw = fc.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {}
                else:
                    args = args_raw or {}
                
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
                result_preview = truncate_with_count(str(tool_result), 200)
                send_event("tool_result", {
                    "tool": tool_name,
                    "result": result_preview,
                    "success": tool_result.success
                })
                
                # Add function_call_output to input_items (Responses API)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": str(tool_result),
                })
                await session_manager.add_message(session_id, "assistant", f"[Tool {tool_name} result] {str(tool_result)}", extra={"tool_name": tool_name, "call_id": call_id})
                
                logger.info(f"Tool result: {truncate_with_count(str(tool_result), 200)}")
            
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
        events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
        
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
