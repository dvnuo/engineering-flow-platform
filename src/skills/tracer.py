"""Skill Execution Tracer - Audit and replay capability.

Reference: https://github.com/dvnuo/engineering-flow-platform/issues/169

Responsibilities:
- FR-7: Execution Trace - Log matched skill, tool calls, input/output
- FR-8: Replay Capability - Support replaying tool sequence for debugging
"""

import logging
import uuid
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Record of a single tool invocation."""
    tool_name: str
    arguments: Dict[str, Any]
    result: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


@dataclass
class SkillExecution:
    """Complete record of a skill execution session."""
    execution_id: str
    session_id: str
    user_message: str
    matched_skill: Optional[str]
    skill_prompt: str
    
    tool_calls: List[ToolCall] = field(default_factory=list)
    thinking_events: List[Dict] = field(default_factory=list)  # LLM thinking events
    
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_at: Optional[str] = None
    final_response: Optional[str] = None
    
    @property
    def total_tool_calls(self) -> int:
        return len(self.tool_calls)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for logging/storage."""
        return {
            "execution_id": self.execution_id,
            "session_id": self.session_id,
            "user_message": self.user_message,
            "matched_skill": self.matched_skill,
            "skill_prompt": self.skill_prompt,
            "tool_calls": [asdict(tc) for tc in self.tool_calls],
            "thinking_events": self.thinking_events,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "final_response": self.final_response,
            "total_tool_calls": self.total_tool_calls,
        }


class ExecutionTracer:
    """Tracer for skill execution events."""
    
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.current_execution: Optional[SkillExecution] = None
        self.execution_history: List[SkillExecution] = []
        self.max_history: int = 100
    
    def start_execution(
        self,
        session_id: str,
        user_message: str,
        matched_skill: Optional[str] = None,
        skill_prompt: str = "",
    ) -> str:
        """Start tracking a new skill execution.
        
        Returns:
            execution_id for this session
        """
        execution_id = str(uuid.uuid4())[:8]
        
        self.current_execution = SkillExecution(
            execution_id=execution_id,
            session_id=session_id,
            user_message=user_message,
            matched_skill=matched_skill,
            skill_prompt=skill_prompt,
        )
        
        if self.enabled:
            logger.info(f"[Tracer] Execution started: {execution_id}")
            logger.info(f"[Tracer]  User message: {user_message[:100]}...")
            logger.info(f"[Tracer]  Matched skill: {matched_skill}")
        
        return execution_id
    
    def log_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: str,
        duration_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
    ):
        """Log a tool call during execution."""
        if not self.current_execution:
            return
        
        tool_call = ToolCall(
            tool_name=tool_name,
            arguments=arguments,
            result=result[:500] if result else "",  # Truncate for logging
            duration_ms=duration_ms,
            success=success,
            error=error,
        )
        
        self.current_execution.tool_calls.append(tool_call)
        
        if self.enabled:
            status = "OK" if success else "ERROR"
            logger.info(f"[Tracer]  [{status}] {tool_name} ({duration_ms:.1f}ms)")

    def log_thinking(self, thinking: str):
        """Log LLM thinking/reasoning during execution.
        
        Args:
            thinking: The LLM's internal reasoning/thinking
        """
        if not self.current_execution:
            return
        
        thinking_event = {
            "timestamp": datetime.utcnow().isoformat(),
            "thinking": thinking[:500] if thinking else "",
        }
        self.current_execution.thinking_events.append(thinking_event)
        
        if self.enabled:
            logger.debug(f"[Tracer] Thinking: {thinking[:100]}...")
    
    def complete_execution(self, final_response: str):
        """Mark execution as complete."""
        if not self.current_execution:
            return
        
        self.current_execution.completed_at = datetime.utcnow().isoformat()
        self.current_execution.final_response = final_response
        
        if self.enabled:
            logger.info(f"[Tracer] Execution complete: {self.current_execution.execution_id}")
            logger.info(f"[Tracer]  Total tool calls: {self.current_execution.total_tool_calls}")
        
        # Add to history
        self.execution_history.append(self.current_execution)
        
        # Trim history
        if len(self.execution_history) > self.max_history:
            self.execution_history = self.execution_history[-self.max_history:]
        
        self.current_execution = None
    
    def get_execution(self, execution_id: str) -> Optional[SkillExecution]:
        """Get execution by ID."""
        for exec in self.execution_history:
            if exec.execution_id == execution_id:
                return exec
        return None
    
    def get_recent_executions(self, limit: int = 10) -> List[Dict]:
        """Get recent executions for replay/debugging."""
        return [
            {
                "execution_id": e.execution_id,
                "session_id": e.session_id,
                "matched_skill": e.matched_skill,
                "started_at": e.started_at,
                "total_tool_calls": e.total_tool_calls,
                "completed": e.completed_at is not None,
            }
            for e in self.execution_history[-limit:]
        ]
    
    def get_events_for_ui(self, limit: int = 10, event_types: List[str] = None) -> List[Dict]:
        """Get events formatted for UI display.
        
        Args:
            limit: Maximum number of executions to include
            event_types: Optional list of event types to include (e.g., ['skill_matched', 'tool_call', 'tool_result', 'llm_thinking', 'complete'])
                       If None, all event types are included
        
        Returns:
            List of events with type, data, and display info
        """
        # Default event types if not specified
        if event_types is None:
            event_types = ['skill_matched', 'llm_thinking', 'tool_call', 'tool_result', 'complete']
        
        # Configurable icons (can be customized per skill)
        icons = {
            'skill_matched': '🎯',
            'llm_thinking': '🤔',
            'tool_call': '🔧',
            'tool_result': '✅',
            'complete': '🎉',
        }
        
        events = []
        
        for execution in self.execution_history[-limit:]:
            # Skill matched
            if 'skill_matched' in event_types and execution.matched_skill:
                events.append({
                    "type": "skill_matched",
                    "data": {"skill": execution.matched_skill},
                    "timestamp": execution.started_at,
                    "display": {
                        "icon": icons.get('skill_matched', '🎯'),
                        "name": "Skill Matched",
                        "message": f"Skill: {execution.matched_skill}"
                    }
                })
            
            # LLM thinking events
            if 'llm_thinking' in event_types:
                for thinking in execution.thinking_events:
                    events.append({
                        "type": "llm_thinking",
                        "data": {"thinking": thinking.get('thinking', '')[:500]},
                        "timestamp": thinking.get('timestamp', ''),
                        "display": {
                            "icon": icons.get('llm_thinking', '🤔'),
                            "name": "LLM Thinking",
                            "message": thinking.get('thinking', '')[:100] + '...' if len(thinking.get('thinking', '')) > 100 else thinking.get('thinking', '')
                        }
                    })
            
            # Tool calls
            for tool_call in execution.tool_calls:
                if 'tool_call' in event_types:
                    events.append({
                        "type": "tool_call",
                        "data": {"tool": tool_call.tool_name, "args": tool_call.arguments},
                        "timestamp": tool_call.timestamp,
                        "display": {
                            "icon": icons.get('tool_call', '🔧'),
                            "name": "Tool Call",
                            "message": f"Calling: {tool_call.tool_name}"
                        }
                    })
                
                # Tool result
                if 'tool_result' in event_types:
                    events.append({
                        "type": "tool_result",
                        "data": {"tool": tool_call.tool_name, "result": tool_call.result[:300], "success": tool_call.success},
                        "timestamp": tool_call.timestamp,
                        "display": {
                            "icon": "✅" if tool_call.success else "❌",
                            "name": "Tool Result",
                            "message": f"{'✅' if tool_call.success else '❌'} {tool_call.tool_name}"
                        }
                    })
            
            # Complete
            if 'complete' in event_types and execution.completed_at:
                events.append({
                    "type": "complete",
                    "data": {"response": execution.final_response, "total_iterations": execution.total_tool_calls},
                    "timestamp": execution.completed_at,
                    "display": {
                        "icon": icons.get('complete', '🎉'),
                        "name": "Complete",
                        "message": "Execution complete"
                    }
                })
        
        return events
    
    def replay_execution(self, execution_id: str) -> Optional[Dict]:
        """Replay an execution for debugging.
        
        FR-8: Replay Capability
        """
        execution = self.get_execution(execution_id)
        if not execution:
            return None
        
        replay_data = {
            "original_execution": execution.to_dict(),
            "replay_timestamp": datetime.utcnow().isoformat(),
        }
        
        if self.enabled:
            logger.info(f"[Tracer] Replaying execution: {execution_id}")
        
        return replay_data


# Global tracer instance
execution_tracer = ExecutionTracer()


def get_tracer() -> ExecutionTracer:
    """Get the global execution tracer."""
    return execution_tracer
