"""Runtime execution contracts (internal)."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class ExecutionRequest:
    request_id: str
    source_type: str
    source_ref: Optional[str]
    agent_id: Optional[str]
    session_id: Optional[str]
    execution_type: str
    input_payload: Dict[str, Any] = field(default_factory=dict)
    context_ref: Optional[Dict[str, Any]] = None
    policy_profile_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    request_id: str
    status: str
    output_payload: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    runtime_events: List[Dict[str, Any]] = field(default_factory=list)
    next_action_hint: Optional[str] = None
    audit_ref: Optional[str] = None


def make_execution_request(
    *,
    source_type: str,
    execution_type: str,
    input_payload: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    context_ref: Optional[Dict[str, Any]] = None,
    policy_profile_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        request_id=request_id or str(uuid.uuid4()),
        source_type=source_type,
        source_ref=source_ref,
        agent_id=agent_id,
        session_id=session_id,
        execution_type=execution_type,
        input_payload=input_payload or {},
        context_ref=context_ref,
        policy_profile_id=policy_profile_id,
        metadata=metadata or {},
    )


def make_execution_result(
    *,
    request_id: str,
    status: str,
    output_payload: Optional[Dict[str, Any]] = None,
    artifacts: Optional[Dict[str, Any]] = None,
    runtime_events: Optional[List[Dict[str, Any]]] = None,
    next_action_hint: Optional[str] = None,
    audit_ref: Optional[str] = None,
) -> ExecutionResult:
    return ExecutionResult(
        request_id=request_id,
        status=status,
        output_payload=output_payload or {},
        artifacts=artifacts or {},
        runtime_events=runtime_events or [],
        next_action_hint=next_action_hint,
        audit_ref=audit_ref,
    )
