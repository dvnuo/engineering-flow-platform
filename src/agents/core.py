"""Agent core implementation following modern agent loop patterns."""

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import platform
import re
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.agents.skill_mode import (
    SkillSession,
    _build_skill_mode_system_prompt,
    _build_skill_mode_user_prompt,
    _extract_skill_artifacts,
    _merge_skill_artifacts,
    _parse_skill_control_marker,
    _update_skill_memory_summary,
    generate_initial_skill_plan,
)

from src.agents.heartbeat import get_heartbeat, start_heartbeat, stop_heartbeat
from src.agents.llm import (
    _normalize_provider_key,
    llm_client,
    is_vision_model,
    get_vision_fallback_model,
)
from src.agents.memory import memory_system
from src.memory.update_manager import MemoryUpdateManager
from src.agents.thinking import ThinkLevel, normalize_think_level, format_runtime_info
from src.config import config
from src.utils.truncate import truncate, truncate_with_count
from src.sessions.manager import session_manager
from src.sessions.persistence import session_persistence
from src.agents.executor import (
    skills_executor,
    SkillResult,
    get_tools_schemas,
    execute_tool_by_name,
    ToolResult,
)
from src.agents.tool_result_policy import should_passthrough_tool_result

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


def _hash_text(value: Any, max_len: int = 800) -> str:
    text = str(value or "")
    if len(text) > max_len:
        text = text[:max_len]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _normalize_tool_args(args_str: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(args_str) if isinstance(args_str, str) and args_str.strip() else {}
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {"raw": str(parsed)}


def _build_progress_signature(
    skill_session: SkillSession,
    normalized_args: Dict[str, Any],
    output_text: str,
) -> Dict[str, str]:
    last_step = skill_session.completed_steps[-1] if skill_session.completed_steps else {}
    last_step_summary = f"{last_step.get('type', '')}:{str(last_step.get('result', ''))[:240]}"
    args_sig = _hash_text(json.dumps(normalized_args, sort_keys=True, ensure_ascii=False), max_len=500)
    output_sig = _hash_text(output_text, max_len=1000)
    artifacts_sig = _hash_text(json.dumps(skill_session.artifacts or {}, sort_keys=True, ensure_ascii=False), max_len=1000)
    step_sig = _hash_text(last_step_summary, max_len=400)
    state_signature = "|".join([output_sig, artifacts_sig, step_sig])
    return {
        "state_signature": state_signature,
        "args_signature": args_sig,
        "output_signature": output_sig,
    }


def _is_lookup_only_skill(skill: Any, skill_session: SkillSession, message: str) -> bool:
    lookup_words = ("get", "list", "query", "search", "read", "fetch", "show", "find", "issue", "file", "info")
    generate_words = ("generate", "create", "write", "modify", "output", "testcase", "code", "doc", "scaffold", "produce")
    haystack = " ".join([
        str(getattr(skill, "name", "") or ""),
        str(getattr(skill, "description", "") or ""),
        str(skill_session.goal or ""),
        str(message or ""),
    ]).lower()
    tokens = re.findall(r"[a-z0-9_]+", haystack)
    if any(word in tokens for word in generate_words):
        return False
    return any(word in tokens for word in lookup_words)


@dataclass
class SkillTurnState:
    round_index: int = 0
    llm_call_count: int = 0
    tool_round_count: int = 0
    transition: str = "tool_followup"
    has_function_calls: bool = False
    has_readonly_success: bool = False
    has_write_call: bool = False
    lookup_only_hint: bool = False


@dataclass
class FinalizerResult:
    state: str
    attempts: int
    parsed_action: str
    raw_output: str
    termination_reason: str
    fallback_used: bool = False


def _evaluate_skill_progress(
    *,
    skill_session: SkillSession,
    tool_name: str,
    normalized_args: Dict[str, Any],
    output_text: str,
) -> Dict[str, Any]:
    progress_data = _build_progress_signature(
        skill_session=skill_session,
        normalized_args=normalized_args,
        output_text=output_text,
    )
    progressed = progress_data["state_signature"] != skill_session.last_progress_signature
    if progressed:
        reason = "progressed"
    elif skill_session.last_tool_name == tool_name and skill_session.last_tool_args_signature == progress_data["args_signature"] and skill_session.last_tool_output_signature == progress_data["output_signature"]:
        reason = "repeated_same_tool_output"
    else:
        reason = "no_state_delta"
    return {
        "progressed": progressed,
        "reason": reason,
        "state_signature": progress_data["state_signature"],
        "args_signature": progress_data["args_signature"],
        "output_signature": progress_data["output_signature"],
    }


async def _run_skill_finalizer(
    *,
    input_items: List[Dict[str, Any]],
    system_prompt: str,
    provider: str,
    model: Optional[str],
    skill_session: SkillSession,
    track_usage: bool,
    usage_data: Dict[str, Any],
    remaining_llm_budget: int,
) -> tuple[FinalizerResult, Dict[str, Any]]:
    raw_output = ""
    fallback_used = False
    parsed_action = "execute"
    termination_reason = "finalizer_terminal_failed"
    max_attempts = min(2, max(0, remaining_llm_budget))
    for attempt in range(max_attempts):
        skill_session.finalizer_state = "running"
        skill_session.finalizer_attempts += 1
        logger.info("[SkillMode][Finalizer] state=running attempt=%s", skill_session.finalizer_attempts)
        prompt = "Do not call tools. Return exactly one control marker on the first line: [FINISH] or [ASK_USER]."
        if attempt == 1:
            prompt += " STRICT: marker must be first line and body must be plain text."
        items = list(input_items)
        items.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})
        result = await llm_client.responses(
            input_items=items,
            system_prompt=system_prompt,
            tools=None,
            reasoning_replay=False,
            provider=_normalize_provider_key(provider),
            max_tokens=64000,
            **({"model": model} if model else {}),
        )
        skill_session.llm_call_count += 1
        if not result.get("error"):
            if track_usage:
                iter_usage = result.get("usage", {}) or {}
                usage_data = {
                    "prompt_tokens": usage_data.get("prompt_tokens", 0) + iter_usage.get("prompt_tokens", 0),
                    "completion_tokens": usage_data.get("completion_tokens", 0) + iter_usage.get("completion_tokens", 0),
                    "total_tokens": usage_data.get("total_tokens", 0) + iter_usage.get("total_tokens", 0),
                }
            candidate = (result.get("content") or "").strip()
            parsed_action, _ = _parse_skill_control_marker(candidate)
            if parsed_action in {"ask_user", "finish"}:
                skill_session.finalizer_state = "succeeded"
                termination_reason = "finalizer_succeeded"
                raw_output = candidate
                logger.info("[SkillMode][Finalizer] state=succeeded")
                return FinalizerResult("succeeded", skill_session.finalizer_attempts, parsed_action, raw_output, termination_reason, False), usage_data
        skill_session.finalizer_state = "retryable_failed" if skill_session.finalizer_attempts < 2 else "terminal_failed"
        logger.warning("[SkillMode][Finalizer] state=%s", skill_session.finalizer_state)
    skill_session.finalizer_state = "terminal_failed"
    fallback_used = True
    if skill_session.completed_steps:
        summary = "; ".join(str(step.get("result", ""))[:120] for step in skill_session.completed_steps[-3:] if step.get("result")) or "Skill execution reached a stable stopping point."
        raw_output = f"[FINISH]\n{summary}"
    elif skill_session.pending_question:
        raw_output = f"[ASK_USER]\n{skill_session.pending_question}"
    else:
        raw_output = "[FINISH]\nSkill execution completed with fallback summary."
    parsed_action, _ = _parse_skill_control_marker(raw_output)
    return FinalizerResult("terminal_failed", skill_session.finalizer_attempts, parsed_action, raw_output, termination_reason, fallback_used), usage_data


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
        user_message_id = await session_manager.add_message(
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
            return {"response": fastlane_response, "usage": usage_data, "user_message_id": user_message_id}
        # ===== END FAST LANE =====

        # ===== SKILL MODE ROUTING =====
        from src.skills import skill_registry

        # Initialize skill registry once (shared with skill matching below)
        if not skill_registry._initialized:
            skill_registry.load_skills()

        active_skill_state = await session_manager.get_active_skill_session(session_id)
        if active_skill_state:
            return await self._continue_skill_mode(
                message=message,
                session_id=session_id,
                user_message_id=user_message_id,
                skill_state=active_skill_state,
                track_usage=track_usage,
                stream_callback=stream_callback,
            )

        matched_for_mode = skill_registry.match_skill(message)
        if matched_for_mode:
            return await self._start_skill_mode(
                message=message,
                session_id=session_id,
                user_message_id=user_message_id,
                skill=matched_for_mode[0],
                track_usage=track_usage,
                stream_callback=stream_callback,
            )

        # ===== END SKILL MODE ROUTING =====

        # ===== SKILL MATCHING (FR-1, FR-2) =====
        from src.skills import get_tracer
        
        # Reuse the match result from skill-mode routing (if any)
        matched_skills = matched_for_mode
        
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
            normalize_compaction_threshold,
            CompactionStats,
        )
        
        # Get context window for the model (not max_tokens which is for responses)
        model = config.llm.get("model", "gpt-5-mini")
        context_window = resolve_context_window_tokens(model)
        
        # Use 80% of context window as the limit for prompt history
        # This delays compaction by allowing more history before triggering it
        max_tokens = int(context_window * 0.8)
        
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
                config.llm.get("model", "gpt-5-mini")
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
        
        # Get compaction threshold from config (default 80%)
        # This determines when to trigger compaction during tool loops
        # Normalize and validate the threshold value
        raw_compaction_threshold = config.session.get("compaction_threshold", 0.8) if hasattr(config, 'session') else 0.8
        compaction_threshold_pct = normalize_compaction_threshold(raw_compaction_threshold)
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
                
                # Handle tool_call_id for tool result messages BEFORE skipping tool role
                tool_call_id = msg.get("tool_call_id", "")
                if tool_call_id and role == "tool":
                    content = msg.get("content", "")
                    items.append({
                        "type": "function_call_output",
                        "call_id": tool_call_id,
                        "output": str(content) if content else "",
                    })
                    continue
                
                if role == "tool":
                    continue
                
                # Handle tool_calls from assistant messages - convert to function_call for Responses API
                tool_calls = msg.get("tool_calls", [])
                if tool_calls and role == "assistant":
                    # First add assistant content (chronological order)
                    content = msg.get("content", "")
                    if content:
                        if isinstance(content, list):
                            items.append({"role": role, "content": content})
                        else:
                            items.append({"role": role, "content": str(content)})
                    # Then add function_call items
                    for tc in tool_calls:
                        call_id = tc.get("id", "")
                        func = tc.get("function", {})
                        name = func.get("name", "")
                        args = func.get("arguments", {})
                        args_str = args if isinstance(args, str) else json.dumps(args)
                        items.append({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": args_str,
                        })
                    continue
                
                # Handle tool_call_id for other messages (fallback)
                if tool_call_id:
                    content = msg.get("content", "")
                    items.append({
                        "type": "function_call_output",
                        "call_id": tool_call_id,
                        "output": str(content) if content else "",
                    })
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
        
        # Token threshold for compaction (configurable, default 80% of context_window)
        # This is the TRIGGER threshold - compaction runs when token count exceeds this
        compaction_threshold = int(context_window * compaction_threshold_pct)
        
        # Keep track of messages for compaction during loop
        # IMPORTANT: Start fresh for each request to avoid carrying over
        # tool_calls and tool_results from previous requests/iterations.
        # loop_messages will be rebuilt as we go through the tool loop.
        loop_messages = messages.copy()
        
        while iteration < max_tool_iterations:
            iteration += 1
            
            # ===== COMPACTION IN LOOP =====
            # Build AgentMessage list once for token estimation and compaction
            agent_msgs_for_compact = [
                AgentMessage(
                    role=m.get("role", "user"),
                    content=m.get("content", ""),
                    timestamp=m.get("timestamp"),
                    tool_calls=m.get("tool_calls"),
                    tool_use_id=m.get("tool_call_id"),
                )
                for m in loop_messages
            ]
            
            current_tokens = estimate_messages_tokens(agent_msgs_for_compact)
            
            if current_tokens > compaction_threshold and iteration > 1:
                logger.info(
                    f"[Tool Loop] Iteration {iteration}: Messages ({current_tokens}) exceed "
                    f"threshold ({compaction_threshold}), compacting..."
                )
                
                compacted_messages, compaction_stats = await compact_messages(
                    messages=agent_msgs_for_compact,
                    max_tokens=compaction_threshold,
                    context_window=context_window,
                    recent_count=5,
                )
                
                # Convert back to dict format
                loop_messages = []
                for msg in compacted_messages:
                    msg_dict = {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                    }
                    if msg.tool_calls:
                        msg_dict["tool_calls"] = msg.tool_calls
                    if msg.tool_use_id:
                        msg_dict["tool_call_id"] = msg.tool_use_id
                    loop_messages.append(msg_dict)
                
                logger.info(
                    f"[Tool Loop] Compaction complete: "
                    f"kept_tokens={compaction_stats.kept_tokens}, "
                    f"dropped_messages={compaction_stats.dropped_messages}"
                )
            
            # Keep input_items in sync with loop_messages (possibly compacted)
            input_items = _to_input_items(loop_messages)
            # ===== END COMPACTION IN LOOP =====
            
            # Send iteration start event
            send_event("iteration_start", {"iteration": iteration, "total": max_tool_iterations})
            
            # Step 1: Call LLM with tools (include skill_prompt from first call)
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM")
            
            # Build context info for thinking display (without relying on model reasoning)
            context_info = []
            if iteration == 1:
                # Show user message on first iteration
                for item in input_items:
                    # Handle both formats: {'type': 'message', 'role': ...} or {'role': ..., 'content': ...}
                    role = item.get("role", "")
                    if role == "user":
                        content = item.get("content", "")
                        if isinstance(content, list):
                            text = " ".join([c.get("text", str(c)) for c in content])
                        else:
                            text = str(content)
                        context_info.append(f"User: {text[:200]}")
            if context_info:
                send_event("llm_thinking", {"message": " | ".join(context_info), "iteration": iteration})
            else:
                send_event("llm_thinking", {"message": f"Iteration {iteration}: Processing...", "iteration": iteration})
            
            logger.debug(f"[Tool Loop] Iteration {iteration}: Calling LLM with {len(input_items)} input_items")
            
            # Check if any message contains images - if so, use vision model
            # Use model explicitly set in agent, otherwise let provider decide
            current_model = self.model or config.llm.get("model")
            
            # Resolve provider: use config if set, otherwise use llm_client's default
            config_provider = config.llm.get("provider")
            if config_provider and isinstance(config_provider, str) and config_provider.strip():
                provider = config_provider.lower()
            else:
                provider = (getattr(llm_client, "default_provider", None) or "openai").lower()
            
            # Check if messages contain images (handle both top-level and nested in content)
            has_images = False
            for item in input_items:
                # Handle top-level image items
                if item.get("type") == "input_image":
                    has_images = True
                    break
                # Handle images nested inside a message's content list
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "input_image":
                            has_images = True
                            break
                    if has_images:
                        break
            
            # Switch to vision model if current model doesn't support images
            effective_model = current_model
            if has_images:
                if current_model:
                    # Explicit model set but may not support vision
                    if not is_vision_model(provider, current_model):
                        fallback = get_vision_fallback_model(provider)
                        if fallback:
                            logger.info(f"[Tool Loop] Message contains images, switching from {current_model} to {fallback}")
                            effective_model = fallback
                else:
                    # No explicit model, use provider's vision default
                    fallback = get_vision_fallback_model(provider)
                    if fallback:
                        logger.info(f"[Tool Loop] Message contains images, using vision fallback {fallback}")
                        effective_model = fallback
            
            # Only pass model if explicitly set
            llm_kwargs = dict(
                input_items=input_items,
                system_prompt=effective_system_prompt,
                tools=self.tools,
                reasoning_replay=enable_reasoning,
            )
            if effective_model:
                llm_kwargs["model"] = effective_model
            
            # Pass provider to ensure correct LLM client routing
            if provider:
                llm_kwargs["provider"] = _normalize_provider_key(provider)
            
            llm_result = await llm_client.responses(**llm_kwargs)
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
            
            # Save intermediate chatlog after EVERY LLM call (for recovery on interruption)
            # Use asyncio.to_thread to avoid blocking the event loop
            async def save_chatlog():
                try:
                    from src.skills import get_tracer
                    tracer_instance = get_tracer()
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    
                    chatlog_data = {
                        "session_id": session_id,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                        "iteration": iteration,
                        "llm_debug": {
                            "llm_request": llm_result.get("_llm_debug", {}),
                            "thinking_events": all_events,
                        },
                        "thinking_events": all_events,
                    }
                    chatlog_dir = os.path.join(session_persistence.storage_dir, "chatlogs")
                    os.makedirs(chatlog_dir, exist_ok=True)
                    # Use raw session_id to match webchat.py's approach
                    chatlog_file = os.path.join(chatlog_dir, f"{session_id}.json")
                    # Atomic write: write to temp file first, then replace
                    import uuid
                    temp_chatlog_file = chatlog_file + f".{uuid.uuid4().hex[:8]}.tmp"
                    with open(temp_chatlog_file, "w") as f:
                        json.dump(chatlog_data, f, indent=2)
                    os.replace(temp_chatlog_file, chatlog_file)
                except Exception as e:
                    logger.debug(f"Failed to save intermediate chatlog: {e}")
            
            # Run chatlog save in background thread to avoid blocking
            asyncio.create_task(save_chatlog())

            # If no function calls, we're done - return the response
            if not tool_calls:
                # Fallback: if content is empty, try to use the latest tool result
                fallback_content = content
                if not content.strip():
                    # Find the latest tool result in loop_messages
                    for msg in reversed(loop_messages):
                        if msg.get("role") == "tool" and msg.get("content"):
                            fallback_content = str(msg.get("content"))
                            break
                    # If still empty, provide a default prompt
                    if not fallback_content.strip():
                        fallback_content = "Operation completed, but no detailed result was returned."
                await session_manager.add_message(session_id, "assistant", fallback_content)
                result = {"response": fallback_content, "usage": usage_data, "user_message_id": user_message_id}
                if enable_reasoning:
                    reasoning_content = llm_result.get("reasoning", "")
                    result["reasoning"] = reasoning_content
                    # Send actual thinking content if reasoning is available
                    if reasoning_content:
                        send_event("llm_thinking", {
                            "message": reasoning_content[:500],  # Truncate for display
                            "thinking": reasoning_content,  # Full thinking for storage
                            "iteration": iteration
                        })
                        # Also log to tracer for persistence
                        try:
                            from src.skills import get_tracer
                            tracer_instance = get_tracer()
                            tracer_instance.log_thinking(reasoning_content)
                        except Exception:
                            pass
                else:
                    # No reasoning_replay: show context info instead
                    user_msg = ""
                    for item in input_items:
                        if item.get("type") == "message" and item.get("role") == "user":
                            user_msg = item.get("content", "")[:200]
                            break
                    if user_msg:
                        send_event("llm_thinking", {
                            "message": f"User: {user_msg}",
                            "context": "user_message",
                            "iteration": iteration
                        })
                # Send completion event
                send_event("complete", {
                    "response": truncate_with_count(fallback_content, 500),
                    "total_iterations": iteration
                })
                # Complete execution tracing
                tracer.complete_execution(fallback_content)
                # Get events for UI
                from src.skills import get_tracer
                tracer_instance = get_tracer()
                events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
                result["events"] = events
                # Add complete thinking flow to debug info
                if llm_result and "_llm_debug" in llm_result:
                    # Get all events from tracer for complete flow
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    result["_llm_debug"] = {
                        "llm_request": llm_result["_llm_debug"],
                        "thinking_events": all_events,
                        "final_response": fallback_content,
                    }
                # Trigger memory update (async, fire and forget)
                # We need to get the last user message and assistant response
                recent_messages = await session_manager.get_history(session_id)
                user_text = ""
                assistant_text = fallback_content
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
            
            # Send actual thinking content if reasoning is available (for tool call iterations too)
            if enable_reasoning:
                reasoning_content = llm_result.get("reasoning", "")
                if reasoning_content:
                    send_event("llm_thinking", {
                        "message": reasoning_content[:500],
                        "thinking": reasoning_content,
                        "iteration": iteration
                    })
                    # Also log to tracer for persistence
                    try:
                        from src.skills import get_tracer
                        tracer_instance = get_tracer()
                        tracer_instance.log_thinking(reasoning_content)
                    except Exception:
                        pass
            
            # Check if LLM wants to call tools
            if not tool_calls:
                # No tool calls - return the response
                if enable_reasoning:
                    reasoning_content = llm_result.get("reasoning", "")
                    if reasoning_content:
                        send_event("llm_thinking", {
                            "message": reasoning_content[:500],
                            "thinking": reasoning_content,
                            "iteration": iteration
                        })
                
                # Build final result
                result = {
                    "content": content,
                    "role": "assistant",
                    "events": events,
                    "user_message_id": user_message_id,
                }
                
                # Add complete thinking flow to debug info
                if llm_result and "_llm_debug" in llm_result:
                    # Get all events from tracer for complete flow
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    result["_llm_debug"] = {
                        "llm_request": llm_result["_llm_debug"],
                        "thinking_events": all_events,
                        "final_response": content,
                    }
                
                return result
            
            # Record tool calls in loop_messages (ALL function calls, not just first);
            # input_items will be rebuilt from loop_messages on next iteration.
            # Convert Responses API format to Chat format for compaction compatibility.
            if tool_calls:
                chat_format_tool_calls = []
                for tc in tool_calls:
                    call_id = tc.get("call_id", "")
                    name = tc.get("name", "")
                    args = tc.get("arguments", {})
                    args_str = args if isinstance(args, str) else json.dumps(args)
                    chat_format_tool_calls.append({
                        "id": call_id,
                        "function": {
                            "name": name,
                            "arguments": args_str
                        }
                    })
                assistant_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": chat_format_tool_calls,
                }
                loop_messages.append(assistant_msg)
                
                # NOTE: Do NOT save assistant message with tool_calls to session history here.
                # The final assistant response (without tool_calls) will be saved AFTER
                # tool execution completes. Saving tool_calls to history causes issues
                # because subsequent LLM calls see the tool_calls in history and return
                # new tool_calls, creating duplicates.
            
            # Note: Tool execution info is sent via WebSocket events and saved 
            # to session metadata via tracer (thinking_events). No message is saved
            # here - the final LLM response will be saved after tool execution.
            
            # Execute each function call
            executed_tool_results: List[tuple[str, ToolResult]] = []
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
                
                # Add tool result to loop_messages for the NEXT LLM call in the current request
                # IMPORTANT: Append to the END of loop_messages, not a specific position.
                # 
                # The issue with inserting at a specific position (i+1 after assistant with tool_calls)
                # is that loop_messages may contain old messages from conversation history.
                # Inserting at i+1 would place the tool result BEFORE those old user messages,
                # resulting in: assistant(tool_calls) -> tool_result -> user(history) [WRONG]
                #
                # By appending to the end, we get:
                #   ... old messages ... -> assistant(tool_calls) -> tool_result [CORRECT]
                # The tool result naturally comes after the assistant message in the iteration order.
                tool_result_msg = {
                    "role": "tool",
                    "content": str(tool_result),
                    "tool_call_id": call_id,
                }
                
                # Append tool result to end of loop_messages
                loop_messages.append(tool_result_msg)
                
                # NOTE: We do NOT save tool results to session history.
                # Tool results in session history cause ordering issues because:
                # 1. Assistant message with tool_calls is NOT saved (to prevent duplicate tool_calls)
                # 2. Tool result is saved separately
                # 3. When history is loaded, the order becomes wrong: user -> tool -> assistant
                # 
                # Instead, tool results stay in loop_messages for the current request's
                # execution context and are passed directly to subsequent LLM calls.
                
                logger.info(f"Tool result: {truncate_with_count(str(tool_result), 200)}")
                executed_tool_results.append((tool_name, tool_result))

            # Narrow passthrough shortcut for direct Jira detail retrieval requests.
            if len(executed_tool_results) == 1:
                single_tool_name, single_tool_result = executed_tool_results[0]
                if should_passthrough_tool_result(
                    latest_user_message=message,
                    tool_name=single_tool_name,
                    tool_result=single_tool_result,
                    tool_calls_count=len(function_calls),
                ):
                    passthrough_content = str(single_tool_result.content)
                    await session_manager.add_message(session_id, "assistant", passthrough_content)
                    send_event("complete", {
                        "response": truncate_with_count(passthrough_content, 500),
                        "total_iterations": iteration
                    })
                    tracer.complete_execution(passthrough_content)
                    from src.skills import get_tracer
                    tracer_instance = get_tracer()
                    events = tracer_instance.get_events_for_ui(limit=10, session_id=session_id)
                    return {
                        "response": passthrough_content,
                        "usage": usage_data,
                        "events": events,
                        "user_message_id": user_message_id,
                    }
            
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
        
        return {"response": "Task completed (max iterations reached)", "usage": usage_data or {}, "events": events, "user_message_id": user_message_id}

    async def _start_skill_mode(
        self,
        message: str,
        session_id: str,
        user_message_id: str,
        skill: Any,
        track_usage: bool = True,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Start a new lightweight skill-mode session."""
        from src.skills import get_tracer
        tracer = get_tracer()
        
        logger.debug(f"[SkillMode] ===== _start_skill_mode BEGIN =====")
        logger.debug(f"[SkillMode] message='{message[:200]}...'")
        logger.debug(f"[SkillMode] session_id={session_id}, skill={skill.name if skill else None}")
        
        usage_data: Dict[str, Any] = {}

        def send_skill_event(event_type: str, data: dict):
            """Send skill event via stream_callback if available, and also emit to event_bus for WebSocket."""
            import json
            event = json.dumps({"type": event_type, "data": data})
            
            # Send via stream_callback (for SSE)
            if stream_callback:
                try:
                    if hasattr(stream_callback, 'put'):
                        stream_callback.put_nowait(event)
                    else:
                        stream_callback(event)
                except Exception:
                    pass  # Ignore callback errors
            
            # Also emit to event_bus for WebSocket listeners
            try:
                from src.gateway.event_bus import event_bus
                event_bus.emit_sync(event_type, data)
            except Exception:
                pass  # Ignore if event_bus not available

        if skill.path:
            set_skill_workdir(skill.path)

        # Log skill mode entry
        tracer.log_skill_mode_entry(skill.name, message, session_id)
        send_skill_event("skill_mode_start", {"skill": skill.name, "message": message[:100]})

        # Generate initial plan (always returns 3-tuple: goal, steps, usage)
        tracer.log_skill_mode_step("GENERATE_PLAN", "started", f"Creating plan for: {message[:50]}...")
        send_skill_event("skill_step", {"step": "GENERATE_PLAN", "status": "started", "detail": f"Creating plan..."})
        
        goal, steps, plan_usage = await generate_initial_skill_plan(skill, message, model=self.model)
        
        tracer.log_skill_mode_step("GENERATE_PLAN", "completed", f"Goal: {goal[:50]}...")
        send_skill_event("skill_step", {"step": "GENERATE_PLAN", "status": "completed", "detail": f"Goal: {goal[:100]}..."})

        skill_session = SkillSession(
            skill_name=skill.name,
            original_user_request=message,
            goal=goal,
            plan=steps,
            status="active",
        )

        await session_manager.set_active_skill_session(session_id, skill_session.to_dict())

        # Merge plan generation usage into usage_data
        if track_usage and plan_usage:
            usage_data = {
                "prompt_tokens": usage_data.get("prompt_tokens", 0) + plan_usage.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0) + plan_usage.get("completion_tokens", 0),
                "total_tokens": usage_data.get("total_tokens", 0) + plan_usage.get("total_tokens", 0),
            }

        send_skill_event("skill_session_start", {"skill": skill.name, "goal": goal[:100]})

        return await self._continue_skill_mode(
            message=message,
            session_id=session_id,
            user_message_id=user_message_id,
            skill_state=skill_session.to_dict(),
            track_usage=track_usage,
            usage_data=usage_data,
            skill=skill,
            stream_callback=stream_callback,
        )

    async def _continue_skill_mode(
        self,
        message: str,
        session_id: str,
        user_message_id: str,
        skill_state: Dict[str, Any],
        track_usage: bool = True,
        usage_data: Optional[Dict[str, Any]] = None,
        skill: Any = None,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Continue an existing lightweight skill-mode session."""
        from src.skills import get_tracer
        tracer = get_tracer()
        
        logger.debug(f"[SkillMode] ===== _continue_skill_mode BEGIN =====")
        logger.debug(f"[SkillMode] message='{message[:200]}...'")
        logger.debug(f"[SkillMode] session_id={session_id}, skill_state keys={list(skill_state.keys()) if skill_state else None}")
        
        usage_data = usage_data or {}

        def send_skill_event(event_type: str, data: dict):
            """Send skill event via stream_callback if available, and also emit to event_bus for WebSocket."""
            import json
            event = json.dumps({"type": event_type, "data": data})
            logger.debug(f"[SkillMode] [EVENT] type={event_type}, data={json.dumps(data)[:200]}")
            
            # Send via stream_callback (for SSE)
            if stream_callback:
                try:
                    if hasattr(stream_callback, 'put'):
                        stream_callback.put_nowait(event)
                    else:
                        stream_callback(event)
                except Exception:
                    pass  # Ignore callback errors
            
            # Also emit to event_bus for WebSocket listeners
            try:
                from src.gateway.event_bus import event_bus
                event_bus.emit_sync(event_type, data)
            except Exception:
                pass  # Ignore if event_bus not available

        from src.skills import skill_registry

        skill_session = SkillSession.from_dict(skill_state)
        skill = skill or skill_registry.get_skill(skill_session.skill_name)
        if not skill:
            await session_manager.set_active_skill_session(session_id, None)
            fallback = "Skill session was cleared because the skill definition is unavailable."
            await session_manager.add_message(session_id, "assistant", fallback)
            events = tracer.get_events_for_ui(limit=10, session_id=session_id)
            return {"response": fallback, "usage": usage_data, "events": events, "user_message_id": user_message_id}

        logger.debug(f"[SkillMode] skill={skill.name}, skill_session.status={skill_session.status}")
        logger.debug(f"[SkillMode] skill_session.completed_steps count={len(skill_session.completed_steps)}")
        logger.debug(f"[SkillMode] skill_session.memory_summary length={len(skill_session.memory_summary) if skill_session.memory_summary else 0}")

        # Fresh user turn: reset progress-window fields while preserving accumulated session content
        skill_session.no_progress_count = 0
        skill_session.last_progress_signature = ""
        skill_session.last_tool_name = ""
        skill_session.last_tool_args_signature = ""
        skill_session.last_tool_output_signature = ""
        
        if skill.path:
            set_skill_workdir(skill.path)

        from src.agents.skill_mode import compact_skill_session_async, compact_skill_session_sync, estimate_tokens
        from src.agents.compaction import resolve_context_window_tokens
        
        provider = (config.llm.get("provider") or getattr(llm_client, "default_provider", "openai")).lower()
        max_skill_tool_rounds = 6  # Increased for longer skill execution
        max_skill_llm_calls = 10
        max_skill_compaction_budget = 32000  # 12% of 264K context for completed_steps
        
        # Resolve skill mode context window (similar to regular chat)
        model = self.model or config.llm.get("model", "gpt-5-mini")
        context_window = resolve_context_window_tokens(model)
        # Trigger compaction at 80% of context window
        skill_max_tokens = int(context_window * 0.8)
        
        raw_output = ""
        should_finalize_without_tools = False
        finalize_reason = ""
        skill_session.execution_mode = ""
        turn_state = SkillTurnState(
            llm_call_count=skill_session.llm_call_count,
            tool_round_count=skill_session.tool_round_count,
            lookup_only_hint=_is_lookup_only_skill(skill, skill_session, message),
        )

        user_prompt = _build_skill_mode_user_prompt(message, skill_session)
        input_items: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}]

        for round_num in range(max_skill_tool_rounds):
            logger.info(f"[SkillMode] ===== Round {round_num + 1}/{max_skill_tool_rounds} =====")
            turn_state.round_index = round_num
            turn_state.tool_round_count = skill_session.tool_round_count
            logger.info(
                "[SkillMode][Decision] round_start=%s execution_mode=%s no_progress_count=%s",
                round_num + 1,
                skill_session.execution_mode or "(unset)",
                skill_session.no_progress_count,
            )

            # Enforce max LLM call cap before making the next LLM request.
            if skill_session.llm_call_count >= max_skill_llm_calls:
                should_finalize_without_tools = True
                finalize_reason = "max_llm_calls"
                turn_state.transition = "max_llm_calls"
                break
            
            # Track tool activity within this round for counting semantics.
            round_tool_calls = []
            executed_any_tool_this_round = False
            
            # Estimate current token count from completed_steps and memory_summary
            current_steps_tokens = sum(
                estimate_tokens(step.get('result', '')) 
                for step in skill_session.completed_steps
            )
            memory_tokens = estimate_tokens(skill_session.memory_summary or '')
            current_tokens = current_steps_tokens + memory_tokens
            
            logger.debug(
                f"[SkillMode] Compaction check: "
                f"current_tokens={current_tokens}, max_tokens={skill_max_tokens}, "
                f"steps={len(skill_session.completed_steps)}, memory_chars={len(skill_session.memory_summary or '')}"
            )
            
            # Compact if over limit (same trigger as regular chat)
            if current_tokens > skill_max_tokens:
                logger.info(f"[SkillMode] Session exceeds token limit, compacting...")
                send_skill_event("skill_compaction", {"status": "started", "current_tokens": current_tokens, "max_tokens": skill_max_tokens})
                try:
                    skill_session = await compact_skill_session_async(skill_session, max_skill_compaction_budget)
                except Exception as compaction_err:
                    logger.warning(f"[SkillMode] Async compaction failed: {compaction_err}, using sync fallback")
                    skill_session = compact_skill_session_sync(skill_session, max_chars=4000)
                send_skill_event("skill_compaction", {"status": "completed", "remaining_steps": len(skill_session.completed_steps)})
            
            # Update token count after potential compaction
            if current_tokens > skill_max_tokens:
                current_steps_tokens = sum(
                    estimate_tokens(step.get('result', '')) 
                    for step in skill_session.completed_steps
                )
                memory_tokens = estimate_tokens(skill_session.memory_summary or '')
                current_tokens = current_steps_tokens + memory_tokens
                logger.info(
                    f"[SkillMode] After compaction: "
                    f"current_tokens={current_tokens}, steps_count={len(skill_session.completed_steps)}"
                )
            
            # Build prompts with potentially compacted session
            system_prompt = _build_skill_mode_system_prompt(skill, skill_session)
            
            logger.debug(f"[SkillMode] system_prompt length={len(system_prompt)}")
            logger.debug(f"[SkillMode] input_items count={len(input_items)}")
            
            # Get tools - use skill's tools if available, otherwise fall back to all tools
            skill_tool_names = getattr(skill, 'tools', []) or []
            
            # If skill defines tool names (List[str]), convert to schemas
            # If skill defines tool schemas (List[Dict]), use directly
            if skill_tool_names and isinstance(skill_tool_names[0], str):
                # Convert tool names to schemas
                all_tool_schemas = get_tools_schemas()
                available_tools = [t for t in all_tool_schemas if t.get("function", {}).get("name") in skill_tool_names]
                logger.debug(f"[SkillMode] Converted {len(skill_tool_names)} tool names to {len(available_tools)} tool schemas")
            elif not skill_tool_names and self.tools:
                available_tools = self.tools
            else:
                available_tools = skill_tool_names if skill_tool_names and isinstance(skill_tool_names[0], dict) else []
            
            logger.debug(f"[SkillMode] available_tools count={len(available_tools)}")
            if available_tools and isinstance(available_tools[0], dict):
                logger.debug(f"[SkillMode] tools: {[t.get('function', {}).get('name') or t.get('name') for t in available_tools]}")
            
            llm_kwargs = {
                "input_items": input_items,
                "system_prompt": system_prompt,
                "tools": available_tools,  # Use skill's tools or fall back to all tools
                "reasoning_replay": False,
                "provider": _normalize_provider_key(provider),
                "max_tokens": 64000,  # Max output tokens for skill mode
            }
            if self.model:
                llm_kwargs["model"] = self.model

            logger.debug(f"[SkillMode] Calling LLM with model={llm_kwargs.get('model')}, max_tokens={llm_kwargs.get('max_tokens')}")
            llm_result = await llm_client.responses(**llm_kwargs)
            skill_session.llm_call_count += 1
            turn_state.llm_call_count = skill_session.llm_call_count
            logger.debug(f"[SkillMode] LLM response keys={llm_result.keys() if llm_result else None}")
            logger.debug(f"[SkillMode] LLM response content length={len(llm_result.get('content', '') or '')}")

            if llm_result.get("error"):
                error_info = llm_result["error"]
                error_msg = error_info.get("message", "Unknown LLM error")
                logger.error(f"[SkillMode] LLM error: {error_msg}")
                return {
                    "error": error_msg,
                    "error_type": error_info.get("type", "llm_error"),
                    "code": error_info.get("code", ""),
                    "user_message_id": user_message_id,
                }

            if track_usage:
                iter_usage = llm_result.get("usage", {}) or {}
                usage_data = {
                    "prompt_tokens": usage_data.get("prompt_tokens", 0) + iter_usage.get("prompt_tokens", 0),
                    "completion_tokens": usage_data.get("completion_tokens", 0) + iter_usage.get("completion_tokens", 0),
                    "total_tokens": usage_data.get("total_tokens", 0) + iter_usage.get("total_tokens", 0),
                }

            raw_output = (llm_result.get("content") or "").strip()
            function_calls = llm_result.get("function_calls", []) or llm_result.get("tool_calls", []) or []
            turn_state.has_function_calls = bool(function_calls)

            logger.debug(f"[SkillMode] raw_output='{raw_output[:300]}...'")
            logger.debug(f"[SkillMode] function_calls count={len(function_calls)}")
            
            # Log full response if content is empty for debugging
            if not raw_output and not function_calls:
                logger.warning(f"[SkillMode] WARNING: LLM returned empty content AND no function_calls!")
                logger.warning(f"[SkillMode] Full llm_result keys: {llm_result.keys() if llm_result else None}")
                logger.warning(f"[SkillMode] llm_result: {str(llm_result)[:500]}")

            if not function_calls:
                should_finalize_without_tools = True
                if turn_state.has_readonly_success and not turn_state.has_write_call and turn_state.lookup_only_hint:
                    finalize_reason = "lookup_complete"
                else:
                    finalize_reason = "no_function_calls"
                turn_state.transition = "finalizing"
                break

            # Keep assistant text context if model returned text together with tool calls
            if raw_output:
                input_items.append({"role": "assistant", "content": raw_output})

            for call in function_calls:
                call_id = call.get("id") or call.get("call_id") or f"skill_call_{len(input_items)}"
                function_payload = call.get("function", {}) or {}
                tool_name = function_payload.get("name") or call.get("name", "")
                args_raw = function_payload.get("arguments", {}) or call.get("arguments", {})
                args_str = args_raw if isinstance(args_raw, str) else json.dumps(args_raw, ensure_ascii=False)

                logger.debug(f"[SkillMode] [TOOL_CALL] tool={tool_name}, args_str='{args_str[:200]}...'")

                input_items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": tool_name,
                    "arguments": args_str,
                })
                
                round_tool_calls.append((tool_name, args_str[:100]))

                normalized_args = _normalize_tool_args(args_str)
                # ===== CONFIRMATION GATE (skill-mode) =====
                # Check if this is a write operation that requires confirmation
                write_tools = {'github_comment_pr', 'github_add_comment', 'jira_add_comment', 
                              'git_commit', 'git_push', 'jira_transition'}
                
                if tool_name in write_tools:
                    turn_state.has_write_call = True
                    logger.info(f"[Confirmation][SkillMode] Tool '{tool_name}' requires confirmation, auto-confirming")
                
                logger.debug(f"[SkillMode] [TOOL_EXEC] Executing tool={tool_name}, parsed_args={normalized_args}")
                executed_any_tool_this_round = True
                try:
                    tool_result: ToolResult = await execute_tool_by_name(tool_name, **normalized_args)
                    output_text = str(tool_result)
                    logger.debug(f"[SkillMode] [TOOL_RESULT] tool={tool_name}, result length={len(output_text)}, preview='{output_text[:200]}...'")
                except Exception as tool_exc:
                    output_text = f"Error: Tool '{tool_name}' failed with {tool_exc}"
                    logger.error(f"[SkillMode] [TOOL_ERROR] tool={tool_name}, error={tool_exc}")

                input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_text,
                })
                
                logger.info(f"[SkillMode] [TOOL_FEEDBACK] Added to input_items, output length={len(output_text)}, first 300 chars: '{output_text[:300]}'")
                
                # Log tool call for skill mode
                tracer.log_tool_call(tool_name, args_str, output_text)
                
                if not output_text.startswith("Error:"):
                    readonly_markers = ("get", "list", "query", "search", "fetch", "read", "issue", "pr", "file")
                    write_markers = ("comment", "create", "update", "push", "transition", "commit", "delete", "write")
                    lower_tool_name = tool_name.lower()
                    is_write_tool = any(marker in lower_tool_name for marker in write_markers)
                    if any(marker in lower_tool_name for marker in readonly_markers) and not is_write_tool:
                        turn_state.has_readonly_success = True
                        skill_session.execution_mode = "readonly_lookup"
                    elif is_write_tool:
                        skill_session.execution_mode = "producing_output"
                    skill_session.completed_steps.append(
                        {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": output_text[:1200],
                        }
                    )
                    skill_session.memory_summary = _update_skill_memory_summary(
                        skill_session, message, f"{tool_name}: {output_text[:800]}"
                    )
                    progress_eval = _evaluate_skill_progress(
                        skill_session=skill_session,
                        tool_name=tool_name,
                        normalized_args=normalized_args,
                        output_text=output_text,
                    )
                    if not progress_eval["progressed"]:
                        skill_session.no_progress_count += 1
                        logger.info(
                            "[SkillMode][Progress] unchanged tool=%s reason=%s no_progress_count=%s",
                            tool_name,
                            progress_eval["reason"],
                            skill_session.no_progress_count,
                        )
                    else:
                        skill_session.no_progress_count = 0
                        skill_session.last_progress_signature = progress_eval["state_signature"]
                        logger.info("[SkillMode][Progress] changed tool=%s", tool_name)
                    skill_session.last_tool_name = tool_name
                    skill_session.last_tool_args_signature = progress_eval["args_signature"]
                    skill_session.last_tool_output_signature = progress_eval["output_signature"]
                    await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
                
                # Stream tool call event for real-time UI updates
                send_skill_event("tool_call", {
                    "tool": tool_name,
                    "args": args_str[:500] if args_str else "",
                    "result": output_text[:500] if output_text else "",
                    "status": "completed" if not output_text.startswith("Error:") else "error"
                })

            # Log tools called in this round
            logger.info(f"[SkillMode] Round {round_num + 1} tools called: {round_tool_calls}")
            if executed_any_tool_this_round:
                skill_session.tool_round_count += 1
                turn_state.tool_round_count = skill_session.tool_round_count
            
            if should_finalize_without_tools:
                break

            if skill_session.no_progress_count >= 2:
                should_finalize_without_tools = True
                finalize_reason = "no_progress"
                turn_state.transition = "no_progress"
                break

            if round_num >= max_skill_tool_rounds - 1:
                should_finalize_without_tools = True
                finalize_reason = "max_tool_rounds"
                turn_state.transition = "max_tool_rounds"
                break

        if should_finalize_without_tools:
            turn_state.transition = "finalizing" if turn_state.transition == "tool_followup" else turn_state.transition
            skill_session.finalizer_attempts = 0
            skill_session.finalizer_state = "idle"
            remaining_llm_budget = max(0, max_skill_llm_calls - skill_session.llm_call_count)
            finalizer_result, usage_data = await _run_skill_finalizer(
                input_items=input_items,
                system_prompt=system_prompt,
                provider=provider,
                model=self.model,
                skill_session=skill_session,
                track_usage=track_usage,
                usage_data=usage_data,
                remaining_llm_budget=remaining_llm_budget,
            )
            raw_output = finalizer_result.raw_output
            if finalizer_result.state == "terminal_failed":
                turn_state.transition = "terminal_failed"

        if not raw_output:
            # No function calls and no text output after max rounds
            # Check if we accomplished something via tool calls
            completed = skill_session.completed_steps if skill_session else []
            logger.info(f"[SkillMode] Loop ended with {len(completed)} completed steps")
            if completed:
                logger.info(f"[SkillMode] completed_steps details: {completed}")
                # Generate a summary from completed steps
                summaries = []
                for step in completed[-5:]:  # Last 5 steps
                    if step.get("type") == "execute" and step.get("result"):
                        summaries.append(step["result"][:200])  # Truncate each
                if summaries:
                    raw_output = f"[FINISH] Successfully completed {len(completed)} steps:\n" + "\n".join(f"- {s}" for s in summaries)
                    logger.info(f"[SkillMode] Auto-generated finish from {len(completed)} completed steps")
                else:
                    raw_output = "I could not produce a final skill-mode response. The model did not return any output or valid response."
            else:
                raw_output = "I could not produce a final skill-mode response. The model did not return any output or valid response."

        action, body = _parse_skill_control_marker(raw_output)
        if should_finalize_without_tools and not skill_session.termination_reason:
            if skill_session.finalizer_state == "succeeded":
                skill_session.termination_reason = finalize_reason or "finalizer_succeeded"
            elif skill_session.finalizer_state == "terminal_failed":
                skill_session.termination_reason = finalize_reason or "finalizer_terminal_failed"
            elif finalize_reason:
                skill_session.termination_reason = finalize_reason
            skill_session.transition = turn_state.transition
            await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
        
        # Log skill mode action with raw output for debugging
        logger.info(f"[SkillMode] Parsed action={action}, body_preview='{body[:100] if body else ''}', raw_output_preview='{raw_output[:100] if raw_output else ''}'")
        tracer.log_skill_mode_action(action, body)

        if action == "ask_user":
            question = body.strip() or "Please provide the minimum necessary information to continue."
            skill_session.status = "waiting_user"
            skill_session.execution_mode = "waiting_user"
            skill_session.termination_reason = "ask_user"
            skill_session.transition = "ask_user"
            skill_session.pending_question = question
            skill_session.memory_summary = _update_skill_memory_summary(skill_session, message, question)
            tracer.log_skill_mode_step("ASK_USER", "completed", f"Question: {question[:50]}...")
            send_skill_event("skill_step", {"step": "ASK_USER", "status": "completed", "detail": question[:200]})
            await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
            await session_manager.add_message(session_id, "assistant", question)
            # Get events for UI
            events = tracer.get_events_for_ui(limit=10, session_id=session_id)
            send_skill_event("skill_complete", {"reason": "ask_user", "question": question[:200]})
            logger.info("[SkillMode][Terminate] reason=%s", skill_session.termination_reason)
            logger.debug(f"[SkillMode] ===== _continue_skill_mode END (ASK_USER) =====")
            return {"response": question, "usage": usage_data, "events": events, "user_message_id": user_message_id}

        if action == "finish":
            final_text = body.strip() or "Skill task completed."
            skill_session.status = "finished"
            skill_session.execution_mode = "producing_output"
            if skill_session.finalizer_state == "succeeded":
                skill_session.termination_reason = finalize_reason or "finalizer_succeeded"
            elif skill_session.finalizer_state == "terminal_failed":
                skill_session.termination_reason = finalize_reason or "finalizer_terminal_failed"
            elif finalize_reason in {"lookup_complete", "no_function_calls", "no_progress", "max_tool_rounds", "max_llm_calls"}:
                skill_session.termination_reason = finalize_reason
            else:
                skill_session.termination_reason = "finish"
            skill_session.transition = skill_session.transition or "finish"
            skill_session.completed_steps.append(
                {
                    "type": "terminal_snapshot",
                    "transition": skill_session.transition,
                    "termination_reason": skill_session.termination_reason,
                    "finalizer_state": skill_session.finalizer_state,
                    "finalizer_attempts": skill_session.finalizer_attempts,
                    "tool_round_count": skill_session.tool_round_count,
                    "llm_call_count": skill_session.llm_call_count,
                    "execution_mode": skill_session.execution_mode,
                }
            )
            skill_session.completed_steps.append({"type": "finish", "result": final_text})
            terminal_snapshot = {
                "status": skill_session.status,
                "transition": skill_session.transition,
                "termination_reason": skill_session.termination_reason,
                "finalizer_state": skill_session.finalizer_state,
                "finalizer_attempts": skill_session.finalizer_attempts,
                "tool_round_count": skill_session.tool_round_count,
                "llm_call_count": skill_session.llm_call_count,
                "execution_mode": skill_session.execution_mode,
            }
            tracer.log_skill_mode_step("FINISH", "completed", f"Result: {final_text[:50]}...")
            tracer.log_skill_mode_complete(final_text)
            send_skill_event("skill_step", {"step": "FINISH", "status": "completed", "detail": final_text[:200]})
            send_skill_event("skill_complete", {"reason": "finish", "result": final_text[:200]})
            await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
            await session_manager.set_active_skill_session(session_id, None)
            await session_manager.add_message(
                session_id,
                "assistant",
                final_text,
                extra={"terminal_skill_session": terminal_snapshot},
            )
            # Get events for UI
            events = tracer.get_events_for_ui(limit=10, session_id=session_id)
            logger.info("[SkillMode][Terminate] reason=%s", skill_session.termination_reason)
            logger.debug(f"[SkillMode] ===== _continue_skill_mode END (FINISH) =====")
            return {"response": final_text, "usage": usage_data, "events": events, "user_message_id": user_message_id}

        # default: execute
        was_waiting_user = skill_session.status == "waiting_user"
        result_text = body.strip() if body.strip() else raw_output
        tracer.log_skill_mode_step("EXECUTE", "completed", f"Result preview: {result_text[:50]}...")
        send_skill_event("skill_step", {"step": "EXECUTE", "status": "completed", "detail": result_text[:200]})
        if len(result_text) < 30:
            result_text = f"{result_text}\n\n(Continuing skill execution, will ask if more info is needed.)"

        skill_session.status = "active"
        skill_session.execution_mode = "producing_output"
        skill_session.termination_reason = ""
        skill_session.transition = "tool_followup"
        skill_session.pending_question = None
        skill_session.completed_steps.append(
            {
                "type": "execute",
                "result": result_text,
            }
        )
        try:
            turn_artifacts = _extract_skill_artifacts(
                skill_session=skill_session,
                user_message=message,
                latest_result=result_text,
                was_waiting_user=was_waiting_user,
            )
            skill_session.artifacts = _merge_skill_artifacts(skill_session.artifacts, turn_artifacts)
        except Exception as artifact_exc:
            logger.warning(f"[SkillMode] Artifact extraction failed: {artifact_exc}")
        skill_session.memory_summary = _update_skill_memory_summary(skill_session, message, result_text)

        await session_manager.set_active_skill_session(session_id, skill_session.to_dict())
        await session_manager.add_message(session_id, "assistant", result_text)
        # Get events for UI
        events = tracer.get_events_for_ui(limit=10, session_id=session_id)
        return {"response": result_text, "usage": usage_data, "events": events, "user_message_id": user_message_id}

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
