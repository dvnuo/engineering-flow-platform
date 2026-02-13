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
