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


@dataclass
class SessionSnapshot:
    snapshot_version: str
    session_id: str
    persisted_session: Dict[str, Any] = field(default_factory=dict)
    runtime_state: Dict[str, Any] = field(default_factory=dict)
    reconstructed_state: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class DelegationRequest:
    delegation_id: str
    objective: str
    visibility: str
    group_id: Optional[str] = None
    parent_agent_id: Optional[str] = None
    assignee_agent_id: Optional[str] = None
    scoped_context_ref: Optional[str] = None
    input_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    expected_output_schema: Dict[str, Any] = field(default_factory=dict)
    deadline: Optional[str] = None
    retry_policy: Dict[str, Any] = field(default_factory=dict)
    skill_name: Optional[str] = None
    skill_kwargs: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegationResult:
    delegation_id: str
    status: str
    assignee_agent_id: Optional[str] = None
    summary: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    next_recommendation: Optional[str] = None
    audit_trace: Dict[str, Any] = field(default_factory=dict)
    raw_result: Dict[str, Any] = field(default_factory=dict)


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
    normalized_input_payload = {} if input_payload is None else dict(input_payload)
    normalized_metadata = {} if metadata is None else dict(metadata)
    normalized_context_ref = None if context_ref is None else dict(context_ref)
    return ExecutionRequest(
        request_id=request_id or str(uuid.uuid4()),
        source_type=source_type,
        source_ref=source_ref,
        agent_id=agent_id,
        session_id=session_id,
        execution_type=execution_type,
        input_payload=normalized_input_payload,
        context_ref=normalized_context_ref,
        policy_profile_id=policy_profile_id,
        metadata=normalized_metadata,
    )


def make_session_snapshot(
    *,
    snapshot_version: str,
    session_id: str,
    persisted_session: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    reconstructed_state: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> SessionSnapshot:
    return SessionSnapshot(
        snapshot_version=snapshot_version,
        session_id=session_id,
        persisted_session=dict(persisted_session or {}),
        runtime_state=dict(runtime_state or {}),
        reconstructed_state=dict(reconstructed_state or {}),
        created_at=created_at,
        updated_at=updated_at,
    )


def make_delegation_request(
    *,
    delegation_id: str,
    objective: str,
    visibility: str,
    group_id: Optional[str] = None,
    parent_agent_id: Optional[str] = None,
    assignee_agent_id: Optional[str] = None,
    scoped_context_ref: Optional[str] = None,
    input_artifacts: Optional[List[Dict[str, Any]]] = None,
    expected_output_schema: Optional[Dict[str, Any]] = None,
    deadline: Optional[str] = None,
    retry_policy: Optional[Dict[str, Any]] = None,
    skill_name: Optional[str] = None,
    skill_kwargs: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> DelegationRequest:
    return DelegationRequest(
        delegation_id=delegation_id,
        objective=objective,
        visibility=visibility,
        group_id=group_id,
        parent_agent_id=parent_agent_id,
        assignee_agent_id=assignee_agent_id,
        scoped_context_ref=scoped_context_ref,
        input_artifacts=list(input_artifacts or []),
        expected_output_schema=dict(expected_output_schema or {}),
        deadline=deadline,
        retry_policy=dict(retry_policy or {}),
        skill_name=skill_name,
        skill_kwargs=dict(skill_kwargs or {}),
        metadata=dict(metadata or {}),
    )


def make_delegation_result(
    *,
    delegation_id: str,
    status: str,
    assignee_agent_id: Optional[str] = None,
    summary: Optional[str] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    blockers: Optional[List[str]] = None,
    next_recommendation: Optional[str] = None,
    audit_trace: Optional[Dict[str, Any]] = None,
    raw_result: Optional[Dict[str, Any]] = None,
) -> DelegationResult:
    return DelegationResult(
        delegation_id=delegation_id,
        status=status,
        assignee_agent_id=assignee_agent_id,
        summary=summary,
        artifacts=list(artifacts or []),
        blockers=list(blockers or []),
        next_recommendation=next_recommendation,
        audit_trace=dict(audit_trace or {}),
        raw_result=dict(raw_result or {}),
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
    normalized_output_payload = {} if output_payload is None else dict(output_payload)
    normalized_artifacts = {} if artifacts is None else dict(artifacts)
    normalized_runtime_events = [] if runtime_events is None else list(runtime_events)
    return ExecutionResult(
        request_id=request_id,
        status=status,
        output_payload=normalized_output_payload,
        artifacts=normalized_artifacts,
        runtime_events=normalized_runtime_events,
        next_action_hint=next_action_hint,
        audit_ref=audit_ref,
    )
