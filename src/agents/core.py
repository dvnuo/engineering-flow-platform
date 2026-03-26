"""Agent core implementation following modern agent loop patterns."""

import asyncio
import contextvars
import json
import logging
import os
import platform
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

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

logger = logging.getLogger(__name__)

# Issue #362: Workflow execution modes
class ExecutionMode(str, Enum):
    """Execution modes for the agent."""
    CHAT = "chat"           # Normal chat mode
    WORKFLOW = "workflow"   # Step-based workflow mode


@dataclass
class ActiveWorkflow:
    """Represents an active workflow in a session (Issue #362).
    
    This is stored in the session state to persist workflow execution
    across multiple messages in the same session.
    """
    workflow_id: str
    skill_name: str
    step_ids: List[str]
    current_step_index: int = 0
    shared_state: Dict[str, Any] = field(default_factory=dict)
    step_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    retry_counts: Dict[str, int] = field(default_factory=dict)
    status: str = "active"  # active, completed, failed, cancelled
    final_summary: Optional[str] = None
    
    @property
    def current_step_id(self) -> Optional[str]:
        """Get the current step ID."""
        if 0 <= self.current_step_index < len(self.step_ids):
            return self.step_ids[self.current_step_index]
        return None
    
    @property
    def is_last_step(self) -> bool:
        """Check if current step is the last step."""
        return self.current_step_index >= len(self.step_ids) - 1
    
    def get_retry_count(self, step_id: str) -> int:
        """Get retry count for a step."""
        return self.retry_counts.get(step_id, 0)
    
    def increment_retry(self, step_id: str) -> int:
        """Increment retry count and return new count."""
        self.retry_counts[step_id] = self.get_retry_count(step_id) + 1
        return self.retry_counts[step_id]


@dataclass
class StepExecutionResult:
    """Result of executing a single workflow step (Issue #362)."""
    step_id: str
    status: str  # success, needs_retry, failed
    summary: str
    artifacts: Dict[str, Any] = field(default_factory=dict)
    next_step: Optional[str] = None
    raw_content: str = ""
    validation_passed: bool = False
    validation_message: str = ""
    
    @classmethod
    def from_json(cls, step_id: str, json_data: Dict, raw_content: str = "") -> "StepExecutionResult":
        """Create from parsed JSON data."""
        return cls(
            step_id=step_id,
            status=json_data.get("status", "failed"),
            summary=json_data.get("summary", ""),
            artifacts=json_data.get("artifacts", {}),
            next_step=json_data.get("next_step"),
            raw_content=raw_content,
        )
    
    @classmethod
    def parse_error(cls, step_id: str, error: str, raw_content: str = "") -> "StepExecutionResult":
        """Create for JSON parse error."""
        return cls(
            step_id=step_id,
            status="needs_retry",
            summary=f"Failed to parse step output: {error}",
            raw_content=raw_content,
            validation_passed=False,
            validation_message=error,
        )


# Max retries per step
MAX_RETRIES_PER_STEP = 2

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
        
        Issue #362: This method now checks for active workflow first.
        If session has an active workflow, it continues that workflow.
        """
        # Issue #362: Check for active workflow state first
        workflow_state = await session_manager.get_workflow_state(session_id)
        if workflow_state and workflow_state.get("mode") == ExecutionMode.WORKFLOW.value:
            # Continue active workflow
            return await self._continue_active_workflow(
                message=message,
                session_id=session_id,
                user_name=user_name,
                track_usage=track_usage,
                reasoning_replay=reasoning_replay,
                stream_callback=stream_callback,
                attached_images=attached_images,
                attachments=attachments,
                workflow_state=workflow_state,
            )
        
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
        skill_workflow = None  # Issue #362: Step-orchestrated workflow for skills
        
        if matched_skills:
            # Use the best match
            best_skill = matched_skills[0]
            logger.info(f"[Skill] Matched skill: {best_skill.name}")
            
            # Set skill workdir for exec tool (async-safe via contextvars)
            if best_skill.path:
                set_skill_workdir(best_skill.path)
                logger.info(f"[Skill] Workdir: {best_skill.path}")
            
            # Issue #362: Check if skill has step-based workflow
            if best_skill.has_steps:
                # Create workflow state
                workflow_id = f"wf_{session_id[:8]}_{uuid.uuid4().hex[:8]}"
                step_ids = [step.id for step in best_skill.steps]
                workflow_state = {
                    "mode": ExecutionMode.WORKFLOW.value,
                    "workflow_id": workflow_id,
                    "skill_name": best_skill.name,
                    "step_ids": step_ids,
                    "current_step_index": 0,
                    "shared_state": {},
                    "step_outputs": {},
                    "retry_counts": {},
                    "status": "active",
                }
                
                # Save workflow state
                await session_manager.set_workflow_state(session_id, workflow_state)
                
                logger.info(f"[Skill] Created workflow: {best_skill.name} ({workflow_id}) with {len(best_skill.steps)} steps")
                
                # Log workflow start
                tracer.log_tool_call(
                    tool_name="workflow_started",
                    arguments={"workflow_id": workflow_id, "skill": best_skill.name, "steps": len(best_skill.steps)},
                    result=f"Started workflow: {best_skill.name}",
                )
                
                # Immediately continue workflow execution
                return await self._continue_active_workflow(
                    message=message,
                    session_id=session_id,
                    user_name=user_name,
                    track_usage=track_usage,
                    reasoning_replay=reasoning_replay,
                    stream_callback=stream_callback,
                    attached_images=attached_images,
                    attachments=attachments,
                    workflow_state=workflow_state,
                )
            else:
                skill_prompt = skill_registry.get_skill_prompt(best_skill)
                skill_workflow = None
            
            # Log matched skill
            tracer.log_tool_call(
                tool_name="skill_matched",
                arguments={"skill": best_skill.name},
                result=f"Matched skill: {best_skill.name}" + (f" (workflow started)" if skill_workflow is None and best_skill.has_steps else ""),
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
        
        # Issue #362: Step-based skill execution
        workflow_step_index = 0  # Current step index
        workflow_context = {}    # Workflow execution context for passing data between steps
        
        if skill_workflow:
            # Step-based mode: Use get_step_prompt() for structured execution
            from src.skills import workflow_executor
            workflow_context = workflow_executor.create_context(
                workflow_id=f"wf_{session_id[:8]}",
                skill_name=matched_skills[0].name,
                user_message=message,
                session_id=session_id,
            )
            workflow_executor.register_context(workflow_context)
            
            # Build step prompt using registry's get_step_prompt (Issue #362)
            current_step = skill_workflow[0]
            step_prompt = skill_registry.get_step_prompt(
                skill=matched_skills[0],
                step=current_step,
                context={"previous_results": workflow_context.get_all_outputs() if hasattr(workflow_context, 'get_all_outputs') else {}}
            )
            
            effective_system_prompt = f"{self.system_prompt}\n\n{step_prompt}"
            logger.info(f"[Workflow] Starting workflow '{matched_skills[0].name}' with {len(skill_workflow)} steps")
            logger.info(f"[Workflow] Step 1: {current_step.id} - {current_step.title}")
        elif skill_prompt:
            # Legacy prompt injection mode
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
            # Issue #362: Filter tools based on current workflow step
            effective_tools = self.tools
            if skill_workflow and workflow_step_index < len(skill_workflow):
                current_step = skill_workflow[workflow_step_index]
                if current_step.allowed_tools:
                    # Filter to only allowed tools for this step (Issue #362)
                    allowed = set(current_step.allowed_tools)
                    effective_tools = [t for t in self.tools if t.get("function", {}).get("name", "") in allowed]
                    logger.info(f"[Workflow] Step {workflow_step_index + 1}: Filtering tools to {current_step.allowed_tools}")
            
            llm_kwargs = dict(
                input_items=input_items,
                system_prompt=effective_system_prompt,
                tools=effective_tools,
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
            
            # Issue #362: Step-based execution - Parse structured JSON result
            # Only process step transition when there are no pending tool calls
            step_result_data = None
            step_needs_retry = False
            
            if skill_workflow and workflow_context and not tool_calls:
                # Try to parse JSON result from LLM response
                step_result_data = self._parse_step_result(content)
                
                if step_result_data:
                    status = step_result_data.get("status", "")
                    summary = step_result_data.get("summary", "")
                    artifacts = step_result_data.get("artifacts", {})
                    next_step_id = step_result_data.get("next_step")
                    
                    logger.info(f"[Workflow] Step {workflow_step_index + 1} result: status={status}, summary={summary[:100]}")
                    
                    # Store artifacts in workflow context for next steps
                    if hasattr(workflow_context, 'shared_state') and artifacts:
                        workflow_context.shared_state.update(artifacts)
                    
                    # Check if step needs retry
                    if status == "needs_retry":
                        step_needs_retry = True
                        logger.warning(f"[Workflow] Step {workflow_step_index + 1} requested retry: {summary}")
                    elif status == "success":
                        # Validate completion criteria
                        completed_step = skill_workflow[workflow_step_index] if workflow_step_index < len(skill_workflow) else None
                        validation_passed, validation_msg = self._validate_step_result(step_result_data, completed_step)
                        
                        if not validation_passed:
                            step_needs_retry = True
                            logger.warning(f"[Workflow] Step {workflow_step_index + 1} validation failed: {validation_msg}")
                        
                        # Determine next step
                        if not step_needs_retry:
                            # Move to next step
                            if next_step_id:
                                # Find next step by ID
                                for i, s in enumerate(skill_workflow):
                                    if s.id == next_step_id:
                                        workflow_step_index = i
                                        break
                                else:
                                    # Next step ID not found, advance by index
                                    workflow_step_index += 1
                            else:
                                # No next_step specified, advance by index
                                workflow_step_index += 1
                            
                            if workflow_step_index >= len(skill_workflow):
                                # Workflow complete
                                logger.info(f"[Workflow] All {len(skill_workflow)} steps completed")
                                effective_system_prompt = self.system_prompt
                                skill_workflow = None
                            else:
                                # Move to next step
                                current_step = skill_workflow[workflow_step_index]
                                logger.info(f"[Workflow] Step {workflow_step_index + 1}/{len(skill_workflow)}: {current_step.id} - {current_step.title}")
                                
                                # Build next step prompt using registry's get_step_prompt
                                step_prompt = skill_registry.get_step_prompt(
                                    skill=matched_skills[0],
                                    step=current_step,
                                    context={"previous_results": workflow_context.get_all_outputs() if hasattr(workflow_context, 'get_all_outputs') else {}}
                                )
                                effective_system_prompt = f"{self.system_prompt}\n\n{step_prompt}"
                                
                                # Log step transition
                                tracer.log_tool_call(
                                    tool_name="workflow_step_complete",
                                    arguments={"step": workflow_step_index, "step_id": current_step.id, "step_name": current_step.title},
                                    result=f"Completed step {workflow_step_index}: {current_step.title}",
                                )
                
                elif "STEP_COMPLETE" in content.upper():
                    # Backward compatibility: treat STEP_COMPLETE as success
                    workflow_step_index += 1
                    if workflow_step_index >= len(skill_workflow):
                        logger.info(f"[Workflow] All {len(skill_workflow)} steps completed")
                        effective_system_prompt = self.system_prompt
                        skill_workflow = None
                    else:
                        current_step = skill_workflow[workflow_step_index]
                        step_prompt = skill_registry.get_step_prompt(
                            skill=matched_skills[0],
                            step=current_step,
                            context={}
                        )
                        effective_system_prompt = f"{self.system_prompt}\n\n{step_prompt}"
            
            # Issue #362: Retry logic - if step needs retry, inject error message
            if step_needs_retry:
                retry_msg = f"Step validation failed: {validation_msg}\n\nPlease fix the issues and retry. Return JSON with status 'success' when complete."
                content = retry_msg  # This will be added to the next LLM call
                send_event("workflow_validation_failed", {
                    "step": workflow_step_index + 1,
                    "message": validation_msg,
                })
            
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
                await session_manager.add_message(session_id, "assistant", content)
                result = {"response": content, "usage": usage_data, "user_message_id": user_message_id}
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
                
                # Add complete thinking flow to debug info
                if llm_result and "_llm_debug" in llm_result:
                    # Get all events from tracer for complete flow
                    all_events = tracer_instance.get_events_for_ui(limit=50, session_id=session_id)
                    result["_llm_debug"] = {
                        "llm_request": llm_result["_llm_debug"],
                        "thinking_events": all_events,
                        "final_response": content,
                    }
                
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

    def _parse_step_result(self, content: str) -> Optional[Dict]:
        """Issue #362: Parse structured JSON result from LLM step response.
        
        Args:
            content: Raw LLM response content
            
        Returns:
            Parsed dict with status, summary, artifacts, next_step, or None if not valid JSON
        """
        if not content:
            return None
        
        import re
        
        # Try to extract JSON from content
        # Handle: ```json ... ``` or plain JSON
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try plain JSON
            json_str = content.strip()
        
        try:
            result = json.loads(json_str)
            # Validate required fields
            if not isinstance(result, dict):
                return None
            if "status" not in result:
                logger.warning(f"[Workflow] Step result missing 'status' field")
                return None
            return result
        except json.JSONDecodeError as e:
            logger.debug(f"[Workflow] Failed to parse step result JSON: {e}")
            return None

    def _validate_step_result(self, step_result: Dict, step: Any) -> tuple:
        """Issue #362: Validate step result against completion criteria.
        
        Args:
            step_result: Parsed step result dict with status, summary, artifacts
            step: SkillStep being validated
            
        Returns:
            (passed, message) tuple
        """
        if not step:
            return (True, "No step to validate")
        
        # Validate status field
        status = step_result.get("status", "")
        if status not in ("success", "needs_retry", "failed"):
            return (False, f"Invalid status '{status}'. Must be 'success', 'needs_retry', or 'failed'")
        
        # Validate summary exists
        summary = step_result.get("summary", "")
        if not summary or len(summary.strip()) < 5:
            return (False, "Summary is missing or too short (min 5 chars)")
        
        # Validate completion_check criteria
        if step.completion_check:
            artifacts = step_result.get("artifacts", {})
            for check in step.completion_check:
                # Format: "artifacts.key" or "field"
                # Remove "exists" suffix if present
                check_clean = check.strip()
                if check_clean.endswith(" exists"):
                    check_clean = check_clean[:-7].strip()
                
                if "." in check_clean:
                    # artifacts.key format
                    parts = check_clean.split(".")
                    if len(parts) >= 2 and parts[0] == "artifacts":
                        key = parts[1]
                        if key not in artifacts:
                            return (False, f"Completion check failed: '{check}' - required artifact '{key}' not found")
                else:
                    # Simple field check (summary, status, etc.)
                    if check_clean not in step_result:
                        return (False, f"Completion check failed: required field '{check_clean}' not found")
        
        return (True, "Validation passed")

    def _validate_step_output(self, content: str, validation_rule: str) -> tuple:
        """Issue #362: Validate step output against validation rule (legacy).
        
        Args:
            content: The step output to validate
            validation_rule: Validation rule string (e.g., "must contain X", "must be valid JSON")
            
        Returns:
            (passed, message) tuple
        """
        content_lower = content.lower()
        
        # Rule: "must contain <text>"
        if validation_rule.startswith("must contain"):
            required_text = validation_rule[len("must contain"):].strip().strip('"\'')
            if required_text.lower() in content_lower:
                return (True, f"Found required text: {required_text}")
            else:
                return (False, f"Output must contain '{required_text}' but it was not found")
        
        # Rule: "must be valid json"
        if validation_rule == "must be valid json":
            import json
            try:
                # Try to parse content as JSON
                json.loads(content)
                return (True, "Valid JSON")
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                import re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
                if json_match:
                    try:
                        json.loads(json_match.group(1))
                        return (True, "Valid JSON in code block")
                    except:
                        pass
                return (False, "Output must be valid JSON but parsing failed")
        
        # Rule: "must not be empty"
        if validation_rule == "must not be empty":
            if content and len(content.strip()) > 0:
                return (True, "Content is not empty")
            return (False, "Output must not be empty")
        
        # Rule: "length > <n>"
        if validation_rule.startswith("length >"):
            try:
                min_len = int(validation_rule.split(">")[1].strip())
                if len(content) > min_len:
                    return (True, f"Content length {len(content)} > {min_len}")
                return (False, f"Output length must be > {min_len} but was {len(content)}")
            except ValueError:
                pass
        
        # Unknown rule - pass by default
        logger.warning(f"[Workflow] Unknown validation rule: {validation_rule}")
        return (True, f"Validation rule '{validation_rule}' recognized")

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

    # =========================================================================
    # Issue #362: Workflow Execution Methods
    # =========================================================================
    
    async def _continue_active_workflow(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        attached_images: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        workflow_state: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Continue an active workflow (Issue #362)."""
        try:
            return await self._continue_active_workflow_impl(
                message=message,
                session_id=session_id,
                user_name=user_name,
                track_usage=track_usage,
                reasoning_replay=reasoning_replay,
                stream_callback=stream_callback,
                attached_images=attached_images,
                attachments=attachments,
                workflow_state=workflow_state,
            )
        except Exception as e:
            logger.error(f"[Workflow] Error in _continue_active_workflow: {e}")
            import traceback
            tb = traceback.format_exc()
            logger.error(f"[Workflow] Traceback: {tb}")
            return {
                "response": f"Workflow error: {str(e)}\n\nPlease check logs for details.",
                "usage": {},
                "user_message_id": None,
            }
    
    async def _continue_active_workflow_impl(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        attached_images: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        workflow_state: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Continue an active workflow (Issue #362).
        
        This method handles continuing a workflow from where it left off.
        It reads the workflow state, executes the current step, handles validation,
        and transitions to the next step or finalizes the workflow.
        """
        from src.skills import skill_registry, get_tracer
        
        # Reconstruct ActiveWorkflow from state with defensive checks
        try:
            active_workflow = ActiveWorkflow(
                workflow_id=workflow_state.get("workflow_id", f"wf_{session_id[:8]}_unknown"),
                skill_name=workflow_state.get("skill_name", "unknown"),
                step_ids=workflow_state.get("step_ids", []),
                current_step_index=workflow_state.get("current_step_index", 0),
                shared_state=workflow_state.get("shared_state", {}),
                step_outputs=workflow_state.get("step_outputs", {}),
                retry_counts=workflow_state.get("retry_counts", {}),
                status=workflow_state.get("status", "active"),
            )
        except Exception as e:
            logger.error(f"[Workflow] Failed to reconstruct workflow: {e}")
            return {
                "response": f"Workflow state error: {str(e)}",
                "usage": {},
                "user_message_id": None,
            }
        
        # Get skill
        skill = skill_registry.get_skill(active_workflow.skill_name)
        if not skill or not skill.has_steps:
            # Skill not found or not step-based, fail workflow
            return await self._finalize_workflow_failure(
                session_id=session_id,
                skill=skill,
                workflow=active_workflow,
                error_message=f"Skill '{active_workflow.skill_name}' not found or not step-based",
            )
        
        # Get current step
        if active_workflow.current_step_index >= len(skill.steps):
            # No more steps, workflow should have been finalized
            return await self._finalize_workflow_success(
                session_id=session_id,
                skill=skill,
                workflow=active_workflow,
            )
        
        current_step = skill.steps[active_workflow.current_step_index]
        
        # Log step continuation
        logger.info(f"[Workflow] Continuing: step {active_workflow.current_step_index + 1}/{len(skill.steps)}: {current_step.id}")
        
        # Execute the step
        step_result = await self._execute_workflow_step(
            skill=skill,
            step=current_step,
            workflow=active_workflow,
            message=message,
            session_id=session_id,
            user_name=user_name,
            track_usage=track_usage,
            reasoning_replay=reasoning_replay,
            stream_callback=stream_callback,
            attached_images=attached_images,
            attachments=attachments,
        )
        
        # Handle step result
        if step_result.status == "success" and step_result.validation_passed:
            # Step succeeded, save outputs
            active_workflow.step_outputs[current_step.id] = {
                "summary": step_result.summary,
                "artifacts": step_result.artifacts,
                "raw_content": step_result.raw_content,
            }
            active_workflow.shared_state.update(step_result.artifacts)
            
            # Determine next step
            next_step_index = None
            next_step_id = step_result.next_step or current_step.next_step
            
            if next_step_id:
                # Explicit next_step by ID
                for i, s in enumerate(skill.steps):
                    if s.id == next_step_id:
                        next_step_index = i
                        break
                if next_step_index is None:
                    return await self._finalize_workflow_failure(
                        session_id=session_id,
                        skill=skill,
                        workflow=active_workflow,
                        error_message=f"Next step '{next_step_id}' not found",
                    )
            elif active_workflow.current_step_index < len(skill.steps) - 1:
                # No explicit next, advance by index
                next_step_index = active_workflow.current_step_index + 1
            else:
                # Last step - workflow complete
                return await self._finalize_workflow_success(
                    session_id=session_id,
                    skill=skill,
                    workflow=active_workflow,
                    step_summary=step_result.summary,
                )
            
            # Advance to next step
            active_workflow.current_step_index = next_step_index
            await self._save_workflow_state(session_id, active_workflow)
            
            # Return response showing what was completed
            return {
                "response": f"**Step '{current_step.title}' completed.**\n\n{step_result.summary}\n\nAdvancing to next step...",
                "usage": {},
                "user_message_id": None,
            }
        
        elif step_result.status == "needs_retry":
            # Check retry count
            retry_count = active_workflow.increment_retry(current_step.id)
            if retry_count > MAX_RETRIES_PER_STEP:
                return await self._finalize_workflow_failure(
                    session_id=session_id,
                    skill=skill,
                    workflow=active_workflow,
                    error_message=f"Max retries ({MAX_RETRIES_PER_STEP}) exceeded for step '{current_step.title}'",
                )
            
            # Save retry count
            await self._save_workflow_state(session_id, active_workflow)
            
            return {
                "response": f"Step validation failed: {step_result.validation_message}\n\nPlease try again. (Retry {retry_count}/{MAX_RETRIES_PER_STEP})",
                "usage": {},
                "user_message_id": None,
            }
        
        else:
            # Step failed
            return await self._finalize_workflow_failure(
                session_id=session_id,
                skill=skill,
                workflow=active_workflow,
                error_message=step_result.summary,
            )
    
    async def _execute_workflow_step(
        self,
        skill,
        step,
        workflow: ActiveWorkflow,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        attached_images: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
    ) -> StepExecutionResult:
        """Execute a single workflow step (Issue #362).
        
        This method actually calls the LLM to execute the step:
        - llm type: Build step prompt and call LLM, parse JSON result
        - tool type: Execute a specific tool
        - user_input type: Wait for user input
        - review type: LLM reviews previous output
        """
        from src.skills import skill_registry
        
        logger.info(f"[Workflow] Executing step type={step.type}, step_id={step.id}")
        
        # Handle different step types
        if step.type == "user_input":
            # Wait for user to provide input
            return await self._execute_step_user_input(step, workflow)
        
        elif step.type == "tool":
            # Execute a specific tool
            return await self._execute_step_tool(step, workflow, message)
        
        elif step.type == "review":
            # LLM reviews the previous step output
            return await self._execute_step_review(skill, step, workflow, message, session_id)
        
        else:  # Default: llm
            # Call LLM to execute the step
            return await self._execute_step_llm(
                skill, step, workflow, message, session_id,
                user_name, track_usage, reasoning_replay
            )
    
    async def _execute_step_llm(
        self,
        skill,
        step,
        workflow: ActiveWorkflow,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
    ) -> StepExecutionResult:
        """Execute an LLM-based workflow step (Issue #362)."""
        from src.skills import skill_registry
        
        try:
            # Build step prompt
            context = {
                "previous_results": workflow.step_outputs,
                "shared_state": workflow.shared_state,
            }
            step_prompt = skill_registry.get_step_prompt(skill, step, context)
            
            # Build effective system prompt with step guidance
            effective_system_prompt = self.system_prompt
            if step_prompt:
                effective_system_prompt = f"{self.system_prompt}\n\n{step_prompt}"
            
            # Get conversation history
            messages = await session_manager.get_history(session_id)
            
            # Transform messages (recent ones only)
            transformed_messages = []
            for msg in messages[-10:]:
                transformed = {"role": msg.get("role", "user"), "content": msg.get("content", "")}
                if msg.get("tool_calls"):
                    transformed["tool_calls"] = msg["tool_calls"]
                if msg.get("tool_call_id"):
                    transformed["tool_call_id"] = msg["tool_call_id"]
                transformed_messages.append(transformed)
            
            # Add user message
            if message:
                transformed_messages.append({"role": "user", "content": message})
            
            # Filter tools by allowed_tools
            tools = self.tools
            if step.allowed_tools:
                allowed_set = set(step.allowed_tools)
                tools = [t for t in self.tools if t.get("function", {}).get("name", "") in allowed_set]
                logger.info(f"[Workflow] Step {step.id}: tools filtered to {step.allowed_tools}")
            
            # Call LLM with extended timeout for workflow steps
            llm_kwargs = {
                "input_items": transformed_messages,
                "system_prompt": effective_system_prompt,
                "tools": tools,
            }
            
            enable_reasoning = reasoning_replay if reasoning_replay is not None else config.llm.get("reasoning_replay", False)
            if enable_reasoning:
                llm_kwargs["reasoning_replay"] = True
            
            # Execute LLM call (may involve multiple tool call loops)
            llm_content = await self._call_llm_with_tools(llm_kwargs)
            
            # Parse JSON result from LLM output
            json_result = self._parse_step_result(llm_content)
            if not json_result:
                return StepExecutionResult.parse_error(step.id, "Output is not valid JSON", llm_content)
            
            # Create step result
            step_result = StepExecutionResult.from_json(step.id, json_result, llm_content)
            
            # Validate completion_check
            validation_passed, validation_msg = self._validate_step_result(json_result, step)
            step_result.validation_passed = validation_passed
            step_result.validation_message = validation_msg
            
            if not validation_passed:
                step_result.status = "needs_retry"
            
            return step_result
            
        except Exception as e:
            logger.error(f"[Workflow] Step execution error: {e}")
            return StepExecutionResult(
                step_id=step.id,
                status="failed",
                summary=f"Step execution error: {str(e)}",
            )
    
    async def _call_llm_with_tools(self, llm_kwargs: Dict) -> str:
        """Call LLM with tool execution loop (Issue #362).
        
        This handles the back-and-forth between LLM and tool execution.
        """
        max_iterations = 10  # Prevent infinite loops
        iteration = 0
        messages = llm_kwargs.get("input_items", [])
        system_prompt = llm_kwargs.get("system_prompt", "")
        tools = llm_kwargs.get("tools", [])
        effective_system_prompt = system_prompt
        
        while iteration < max_iterations:
            iteration += 1
            
            # Call LLM
            llm_result = await llm_client.responses(
                input_items=messages,
                system_prompt=effective_system_prompt,
                tools=tools,
            )
            
            content = (llm_result.get("content") or "").strip()
            tool_calls = llm_result.get("function_calls", [])
            
            # If no tool calls, return the content
            if not tool_calls:
                return content
            
            # Execute tool calls
            for call in tool_calls:
                func = call.get("function", {})
                name = func.get("name", "")
                args = func.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args)
                
                try:
                    result = await execute_tool_by_name(name, **args)
                    tool_output = result.output if hasattr(result, 'output') else str(result)
                except Exception as e:
                    tool_output = f"Error: {str(e)}"
                
                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "content": tool_output,
                    "tool_call_id": call.get("id"),
                })
            
            # Update system prompt to null after first call (context is in messages)
            effective_system_prompt = None
        
        # Max iterations reached
        return content + "\n\n[Max tool iterations reached]"
    
    async def _execute_step_user_input(
        self,
        step,
        workflow: ActiveWorkflow,
    ) -> StepExecutionResult:
        """Handle user_input step type - wait for user to provide input."""
        # Store step metadata
        workflow.shared_state["_current_step_id"] = step.id
        workflow.shared_state["_step_title"] = step.title
        workflow.shared_state["_step_objective"] = step.objective
        
        return StepExecutionResult(
            step_id=step.id,
            status="success",
            summary=f"**Step: {step.title}**\n\n{step.objective}\n\nWaiting for your input...",
            validation_passed=True,
            validation_message="Awaiting user input",
        )
    
    async def _execute_step_tool(
        self,
        step,
        workflow: ActiveWorkflow,
        message: str,
    ) -> StepExecutionResult:
        """Handle tool step type - execute a specific tool and return result."""
        # For tool type, we expect a tool name in the step config or message
        tool_name = step.allowed_tools[0] if step.allowed_tools else None
        
        if not tool_name and message:
            # Try to extract tool name from message
            tool_name = message.strip().split()[0] if message.strip() else None
        
        if not tool_name:
            return StepExecutionResult(
                step_id=step.id,
                status="needs_retry",
                summary=f"Tool step requires a tool name. Please specify which tool to use.",
                validation_passed=False,
                validation_message="No tool specified",
            )
        
        try:
            result = await execute_tool_by_name(tool_name)
            tool_output = result.output if hasattr(result, 'output') else str(result)
            
            return StepExecutionResult(
                step_id=step.id,
                status="success",
                summary=f"Tool '{tool_name}' executed successfully",
                artifacts={tool_name: tool_output},
                validation_passed=True,
                validation_message="Tool execution successful",
            )
        except Exception as e:
            return StepExecutionResult(
                step_id=step.id,
                status="failed",
                summary=f"Tool execution failed: {str(e)}",
                validation_passed=False,
                validation_message=str(e),
            )
    
    async def _execute_step_review(
        self,
        skill,
        step,
        workflow: ActiveWorkflow,
        message: str,
        session_id: str,
    ) -> StepExecutionResult:
        """Handle review step type - LLM reviews previous step output."""
        # Get previous step output to review
        previous_output = None
        if workflow.step_outputs:
            # Get the last step's output
            last_step_id = list(workflow.step_outputs.keys())[-1] if workflow.step_outputs else None
            if last_step_id:
                previous_output = workflow.step_outputs[last_step_id]
        
        # Build review prompt
        review_prompt = f"""## Review Step: {step.title}

Please review the previous step's output and determine if it meets the requirements.

Previous Output:
{json.dumps(previous_output, indent=2) if previous_output else 'No previous output'}

Your Task:
{step.objective}

Provide your review in JSON format:
{{"status": "pass|fail", "summary": "Brief review summary", "feedback": "Specific feedback if any"}}
"""
        
        try:
            # Call LLM for review
            messages = [{"role": "user", "content": review_prompt}]
            llm_result = await llm_client.responses(
                input_items=messages,
                system_prompt=self.system_prompt,
                tools=self.tools,
            )
            
            content = (llm_result.get("content") or "").strip()
            
            # Parse review JSON
            json_result = self._parse_step_result(content)
            if not json_result:
                return StepExecutionResult.parse_error(step.id, "Review output is not valid JSON", content)
            
            status = json_result.get("status", "fail")
            
            return StepExecutionResult(
                step_id=step.id,
                status="success" if status == "pass" else "needs_retry",
                summary=json_result.get("summary", ""),
                artifacts={"review_status": status, "feedback": json_result.get("feedback", "")},
                validation_passed=(status == "pass"),
                validation_message=json_result.get("feedback", ""),
            )
            
        except Exception as e:
            return StepExecutionResult(
                step_id=step.id,
                status="failed",
                summary=f"Review step error: {str(e)}",
                validation_passed=False,
                validation_message=str(e),
            )
    
    async def _finalize_workflow_success(
        self,
        session_id: str,
        skill,
        workflow: ActiveWorkflow,
        step_summary: str = None,
    ) -> Dict[str, Any]:
        """Finalize workflow with success status (Issue #362).
        
        Outputs:
        - Skill name
        - Each step's summary
        - Key artifacts
        - Final user-readable result
        """
        from src.skills import get_tracer
        
        # Build detailed final summary
        summary_parts = [f"# ✅ Workflow Completed: {skill.name}\n"]
        summary_parts.append(f"Successfully completed {len(workflow.step_ids)} steps.\n")
        
        # Step details
        summary_parts.append("## Steps Completed:\n")
        for i, step_id in enumerate(workflow.step_ids):
            step_info = workflow.step_outputs.get(step_id, {})
            step_summary_text = step_info.get("summary", "No summary") if isinstance(step_info, dict) else str(step_info)
            summary_parts.append(f"**Step {i+1}: {step_id}**")
            summary_parts.append(f"  {step_summary_text[:200]}")
            
            # Show key artifacts
            if isinstance(step_info, dict) and step_info.get("artifacts"):
                artifacts = step_info.get("artifacts", {})
                for key, value in list(artifacts.items())[:3]:
                    summary_parts.append(f"  - {key}: {str(value)[:100]}")
            summary_parts.append("")
        
        # Key artifacts across all steps
        all_artifacts = {}
        for step_id, step_info in workflow.step_outputs.items():
            if isinstance(step_info, dict) and step_info.get("artifacts"):
                all_artifacts.update(step_info.get("artifacts", {}))
        
        if all_artifacts:
            summary_parts.append("## Key Artifacts Generated:\n")
            for key, value in list(all_artifacts.items())[:5]:
                summary_parts.append(f"- **{key}**: {str(value)[:150]}")
            summary_parts.append("")
        
        # Final summary from last step
        if step_summary:
            summary_parts.append(f"## Final Result\n{step_summary}\n")
        
        final_summary = "\n".join(summary_parts)
        workflow.final_summary = final_summary
        workflow.status = "completed"
        
        # Log completion
        tracer = get_tracer()
        tracer.log_tool_call(
            tool_name="workflow_completed",
            arguments={
                "workflow_id": workflow.workflow_id, 
                "steps": len(workflow.step_ids),
                "step_outputs": list(workflow.step_outputs.keys()),
            },
            result="Workflow completed successfully",
        )
        
        # Clear workflow state
        await session_manager.set_workflow_state(session_id, None)
        
        # Add to chat history
        await session_manager.add_message(session_id, "assistant", final_summary)
        
        logger.info(f"[Workflow] Completed: {workflow.workflow_id}")
        
        return {
            "response": final_summary,
            "usage": {},
            "user_message_id": None,
        }
    
    async def _finalize_workflow_failure(
        self,
        session_id: str,
        skill,
        workflow: ActiveWorkflow,
        error_message: str,
    ) -> Dict[str, Any]:
        """Finalize workflow with failure status (Issue #362).
        
        Outputs:
        - Failed step
        - Error reason
        - Completed steps
        - Last validation message
        """
        from src.skills import get_tracer
        
        summary_parts = [f"# ❌ Workflow Failed: {skill.name if skill else workflow.skill_name}\n"]
        
        # Failed step
        failed_step_id = workflow.step_ids[workflow.current_step_index] if workflow.current_step_index < len(workflow.step_ids) else "unknown"
        summary_parts.append(f"**Failed at step**: {failed_step_id}\n")
        summary_parts.append(f"**Error**: {error_message}\n")
        
        # Completed steps
        summary_parts.append(f"## Completed Steps ({len(workflow.step_outputs)}/{len(workflow.step_ids)})\n")
        for i, step_id in enumerate(workflow.step_ids):
            if step_id in workflow.step_outputs:
                step_info = workflow.step_outputs.get(step_id, {})
                step_summary = step_info.get("summary", "No summary")[:100] if isinstance(step_info, dict) else str(step_info)
                summary_parts.append(f"{i+1}. **{step_id}** ✓\n   {step_summary}")
        
        # Last validation info
        if workflow.retry_counts.get(failed_step_id, 0) > 0:
            summary_parts.append(f"\n**Validation failed after {workflow.retry_counts.get(failed_step_id, 0)} retries**")
        
        final_summary = "\n".join(summary_parts)
        workflow.status = "failed"
        workflow.final_summary = final_summary
        
        # Log failure
        tracer = get_tracer()
        tracer.log_tool_call(
            tool_name="workflow_failed",
            arguments={
                "workflow_id": workflow.workflow_id, 
                "error": error_message,
                "failed_step": failed_step_id,
                "completed_steps": list(workflow.step_outputs.keys()),
            },
            result=f"Workflow failed at step {failed_step_id}: {error_message}",
        )
        
        # Clear workflow state
        await session_manager.set_workflow_state(session_id, None)
        
        # Add to chat history
        await session_manager.add_message(session_id, "assistant", final_summary)
        
        logger.info(f"[Workflow] Failed: {workflow.workflow_id} - {error_message}")
        
        return {
            "response": final_summary,
            "usage": {},
            "user_message_id": None,
        }
    
    async def _save_workflow_state(self, session_id: str, workflow: ActiveWorkflow, **kwargs) -> None:
        """Save workflow state to session (Issue #362)."""
        state = {
            "mode": ExecutionMode.WORKFLOW.value,
            "workflow_id": workflow.workflow_id,
            "skill_name": workflow.skill_name,
            "step_ids": workflow.step_ids,
            "current_step_index": workflow.current_step_index,
            "shared_state": workflow.shared_state,
            "step_outputs": workflow.step_outputs,
            "retry_counts": workflow.retry_counts,
            "status": workflow.status,
            "_awaiting_response": workflow.shared_state.get("_awaiting_response", False),
        }
        await session_manager.set_workflow_state(session_id, state)
    
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
