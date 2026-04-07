"""Structured governance bus contracts and default implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import uuid

from src.agents.tool_result_policy import should_passthrough_tool_result
from src.runtime.capability_registry import get_capability_registry
from src.runtime.contracts import ExecutionRequest, ExecutionResult, make_execution_result
from src.runtime.events import build_runtime_event
from src.utils.redaction import safe_preview

_ALLOWED_RESULT_STATUSES = {"success", "error", "blocked", "queued", "started"}


@dataclass
class GovernanceAuditRecord:
    """Structured governance audit metadata.

    `metadata` is intentionally dict-shaped so callers can copy into artifacts or
    runtime event payloads without additional translation layers.
    """

    audit_ref: str
    stage: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GovernanceDecision:
    """Outcome from governance checks."""

    allowed: bool = True
    reason: Optional[str] = None
    audit_record: Optional[GovernanceAuditRecord] = None
    result: Optional[ExecutionResult] = None


class GovernanceBus:
    """Explicit governance boundary for execution lifecycle decisions."""

    async def before_execute(self, request: ExecutionRequest) -> GovernanceDecision:
        return GovernanceDecision(allowed=True)

    async def after_execute(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult | GovernanceDecision:
        return result

    async def on_error(self, request: ExecutionRequest, error: Exception) -> Optional[GovernanceAuditRecord]:
        return None


def make_governance_audit_record(
    *,
    request: ExecutionRequest,
    stage: str,
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> GovernanceAuditRecord:
    suffix = uuid.uuid4().hex[:10]
    return GovernanceAuditRecord(
        audit_ref=f"gov-{request.request_id}-{stage}-{suffix}",
        stage=stage,
        message=message,
        metadata=dict(metadata or {}),
    )


def governance_audit_runtime_event(
    *,
    request: ExecutionRequest,
    status: str,
    audit_record: GovernanceAuditRecord,
    detail_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    task_id = metadata.get("task_id") if isinstance(metadata.get("task_id"), str) and metadata.get("task_id").strip() else None
    if task_id is None:
        payload_task_id = request.input_payload.get("task_id")
        task_id = payload_task_id.strip() if isinstance(payload_task_id, str) and payload_task_id.strip() else None
    return build_runtime_event(
        event_type="governance.audit",
        execution_type=request.execution_type,
        state=status,
        session_id=request.session_id,
        request_id=request.request_id,
        agent_id=request.agent_id,
        summary=audit_record.message,
        task_id=task_id,
        detail_payload={
            "audit_ref": audit_record.audit_ref,
            "stage": audit_record.stage,
            "status": status,
            **audit_record.metadata,
            **(detail_payload or {}),
        },
        legacy_payload={"legacy_type": "governance_audit"},
    )


class DefaultGovernanceBus(GovernanceBus):
    """Minimal, data-driven governance defaults for Phase 2 adoption."""

    async def before_execute(self, request: ExecutionRequest) -> GovernanceDecision:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        policy_profile = request.policy_profile_id or metadata.get("policy_profile_id") or "default"
        capability_context = _resolve_capability_context(request)

        capability_decision = _evaluate_capability_constraints(
            metadata=metadata,
            capability_id=capability_context.get("capability_id"),
            capability_type=capability_context.get("capability_type"),
            action_id=capability_context.get("action_id"),
        )
        if capability_decision is not None:
            deny_reason = capability_decision.get("reason") or "capability_policy_blocked"
            audit = make_governance_audit_record(
                request=request,
                stage="before_execute",
                message=capability_decision.get("message") or "Blocked by capability allow/deny policy",
                metadata={
                    "policy_profile_id": policy_profile,
                    "rule": "capability_policy",
                    "execution_type": request.execution_type,
                    "capability_id": capability_context.get("capability_id"),
                    "capability_type": capability_context.get("capability_type"),
                    "action_id": capability_context.get("action_id"),
                    "deny_reason": deny_reason,
                },
            )
            return GovernanceDecision(allowed=False, reason=deny_reason, audit_record=audit)

        if metadata.get("auto_run") is True and metadata.get("governance_require_explicit_allow") is True:
            if metadata.get("governance_allow_auto_run") is not True:
                audit = make_governance_audit_record(
                    request=request,
                    stage="before_execute",
                    message="Blocked auto_run execution without explicit allow",
                    metadata={
                        "policy_profile_id": policy_profile,
                        "rule": "auto_run_guard",
                        "execution_type": request.execution_type,
                    },
                )
                return GovernanceDecision(allowed=False, reason="auto_run_guard_blocked", audit_record=audit)

        if request.execution_type in {"event", "task"} and metadata.get("external_triggered") is True:
            target = (
                metadata.get("governance_target")
                or request.input_payload.get("target_execution_type")
                or request.input_payload.get("tool_name")
                or request.execution_type
            )
            blocklist = metadata.get("governance_external_blocklist") or []
            allowlist = metadata.get("governance_external_allowlist") or []
            if isinstance(blocklist, list) and target in blocklist:
                audit = make_governance_audit_record(
                    request=request,
                    stage="before_execute",
                    message="Blocked by external trigger blocklist",
                    metadata={"rule": "external_blocklist", "target": target, "policy_profile_id": policy_profile},
                )
                return GovernanceDecision(allowed=False, reason="external_blocklist", audit_record=audit)
            if isinstance(allowlist, list) and allowlist and target not in allowlist:
                audit = make_governance_audit_record(
                    request=request,
                    stage="before_execute",
                    message="Blocked by external trigger allowlist",
                    metadata={"rule": "external_allowlist", "target": target, "policy_profile_id": policy_profile},
                )
                return GovernanceDecision(allowed=False, reason="external_allowlist", audit_record=audit)

        return GovernanceDecision(allowed=True)

    async def after_execute(self, request: ExecutionRequest, result: ExecutionResult) -> ExecutionResult:
        # after_execute ordering contract (Phase 2 closeout):
        # 1) normalize/validate ExecutionResult contract
        # 2) apply policy enforcement/hints
        # 3) append governance audit event for any amendments
        result, notes = self._validate_result_contract(request, result)
        result, policy_notes = self._apply_policy_enforcement(request, result)
        notes.extend(policy_notes)
        return self._append_governance_audit_event(request, result, notes)

    def _validate_result_contract(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
    ) -> tuple[ExecutionResult, list[str]]:
        return self._normalize_execution_result_for_policy(request, result)

    def _normalize_execution_result_for_policy(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
    ) -> tuple[ExecutionResult, list[str]]:
        """Normalize execution result fields before policy post-processing."""
        notes: list[str] = []

        if request.execution_type in {"task", "event"} and result.status not in _ALLOWED_RESULT_STATUSES:
            result.status = "error"
            notes.append("invalid_status_coerced")

        if request.execution_type in {"task", "event"} and not isinstance(result.output_payload, dict):
            result.output_payload = {"value": safe_preview(result.output_payload, 200)}
            notes.append("output_payload_normalized")
        return result, notes

    def _apply_policy_post_validation(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
    ) -> tuple[ExecutionResult, list[str]]:
        """Apply non-blocking policy hints after base result normalization."""
        notes: list[str] = []
        if request.execution_type in {"tool", "task"}:
            metadata = request.metadata if isinstance(request.metadata, dict) else {}
            latest_user_message = str(metadata.get("latest_user_message") or "")
            tool_calls_count = int(metadata.get("tool_calls_count") or 0)
            tool_name = str(request.input_payload.get("tool_name") or metadata.get("tool_name") or "")
            if tool_name:
                try:
                    passthrough = should_passthrough_tool_result(
                        tool_name=tool_name,
                        tool_result=type("_ResultLike", (), {
                            "success": result.status == "success",
                            "content": result.output_payload.get("content") or result.output_payload.get("output") or "",
                            "error": result.output_payload.get("error"),
                        })(),
                        latest_user_message=latest_user_message,
                        tool_calls_count=tool_calls_count,
                    )
                    if passthrough:
                        result.artifacts.setdefault("governance", {})["tool_result_passthrough_recommended"] = True
                except Exception:
                    # Policy wiring must stay non-blocking.
                    pass
        return result, notes

    def _apply_policy_enforcement(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
    ) -> tuple[ExecutionResult, list[str]]:
        return self._apply_policy_post_validation(request, result)

    def _append_governance_audit_event(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
        notes: list[str],
    ) -> ExecutionResult:
        if not notes:
            return result
        audit = make_governance_audit_record(
            request=request,
            stage="after_execute",
            message="Governance post-processing amended execution result",
            metadata={"notes": notes},
        )
        result.audit_ref = result.audit_ref or audit.audit_ref
        result.runtime_events.append(
            governance_audit_runtime_event(
                request=request,
                status=result.status,
                audit_record=audit,
                detail_payload={"notes": notes},
            )
        )
        return result

    async def on_error(self, request: ExecutionRequest, error: Exception) -> Optional[GovernanceAuditRecord]:
        return make_governance_audit_record(
            request=request,
            stage="on_error",
            message="Governance captured execution error",
            metadata={
                "execution_type": request.execution_type,
                "error_type": error.__class__.__name__,
                "error_preview": safe_preview(str(error), 200),
            },
        )


def build_default_governance_bus() -> GovernanceBus:
    return DefaultGovernanceBus()


class NoopGovernanceBus(GovernanceBus):
    """Explicit no-op governance implementation for compatibility paths."""


def _as_lower_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip().lower()
        if text:
            normalized.append(text)
    return normalized


def _resolve_capability_context(request: ExecutionRequest) -> Dict[str, Optional[str]]:
    payload = request.input_payload if isinstance(request.input_payload, dict) else {}
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    task_type = str(payload.get("task_type") or "").strip().lower()

    capability_id: Optional[str] = None
    action_id: Optional[str] = None
    if request.execution_type == "task":
        if task_type == "adapter_action_task":
            action_id = str(payload.get("action_id") or "").strip().lower() or None
            capability_id = action_id
        elif task_type == "jira_workflow_review_task":
            capability_id = "adapter:jira:read_issue"
            action_id = capability_id
        elif task_type == "github_review_task":
            capability_id = "adapter:github:review_pull_request"
            action_id = capability_id
        elif task_type == "tool_task":
            tool_name = str(payload.get("tool_name") or "").strip().lower()
            capability_id = f"tool:{tool_name}" if tool_name else None
        elif task_type == "delegation_task":
            skill_name = str(payload.get("skill_name") or "").strip().lower()
            capability_id = f"skill:{skill_name}" if skill_name else None
    elif request.execution_type == "tool":
        tool_name = str(payload.get("tool_name") or metadata.get("tool_name") or "").strip().lower()
        capability_id = f"tool:{tool_name}" if tool_name else None
    elif request.execution_type == "skill":
        skill_name = str(payload.get("skill_name") or "").strip().lower()
        capability_id = f"skill:{skill_name}" if skill_name else None

    descriptor = get_capability_registry().get(capability_id) if capability_id else None
    capability_type = descriptor.type if descriptor is not None else _infer_capability_type_from_id(capability_id)
    return {
        "capability_id": capability_id,
        "capability_type": capability_type,
        "action_id": action_id,
    }


def _infer_capability_type_from_id(capability_id: Optional[str]) -> Optional[str]:
    text = str(capability_id or "").strip().lower()
    if text.startswith("adapter:"):
        return "adapter_action"
    if text.startswith("tool:"):
        return "tool"
    if text.startswith("skill:"):
        return "skill"
    if text.startswith("channel_action:"):
        return "channel_action"
    return None


def _evaluate_capability_constraints(
    *,
    metadata: Dict[str, Any],
    capability_id: Optional[str],
    capability_type: Optional[str],
    action_id: Optional[str],
) -> Optional[Dict[str, str]]:
    denied_capability_ids = _normalize_constraint_capability_ids(
        metadata.get("denied_capability_ids"),
        capability_type=capability_type,
        action_id=action_id,
    )
    allowed_capability_ids = _normalize_constraint_capability_ids(
        metadata.get("allowed_capability_ids"),
        capability_type=capability_type,
        action_id=action_id,
    )
    denied_capability_types = _normalize_constraint_capability_types(metadata.get("denied_capability_types"))
    allowed_capability_types = _normalize_constraint_capability_types(metadata.get("allowed_capability_types"))
    denied_adapter_actions = _normalize_action_constraints(
        metadata.get("denied_adapter_actions"),
        metadata.get("denied_actions"),
    )
    allowed_adapter_actions = _normalize_action_constraints(
        metadata.get("allowed_adapter_actions"),
        metadata.get("allowed_actions"),
    )

    normalized_capability_id = str(capability_id or "").strip().lower()
    normalized_capability_type = str(capability_type or "").strip().lower()
    normalized_action_id = str(action_id or "").strip().lower()
    normalized_action_name = normalized_action_id.split(":")[-1] if normalized_action_id else ""

    if _matches_capability_constraint(
        constraints=denied_capability_ids,
        capability_id=normalized_capability_id,
        capability_type=normalized_capability_type,
        action_name=normalized_action_name,
    ):
        return {"reason": "denied_capability_ids", "message": f"Capability blocked: {normalized_capability_id}"}
    if denied_capability_types and normalized_capability_type and normalized_capability_type in denied_capability_types:
        return {"reason": "denied_capability_types", "message": f"Capability type blocked: {normalized_capability_type}"}
    if _matches_action_constraint(
        constraints=denied_adapter_actions,
        action_id=normalized_action_id,
        action_name=normalized_action_name,
    ):
        return {"reason": "denied_adapter_actions", "message": f"Adapter action blocked: {normalized_action_id}"}

    if allowed_capability_ids and not _matches_capability_constraint(
        constraints=allowed_capability_ids,
        capability_id=normalized_capability_id,
        capability_type=normalized_capability_type,
        action_name=normalized_action_name,
    ):
        return {"reason": "allowed_capability_ids", "message": "Capability not in allowlist"}
    if allowed_capability_types and (not normalized_capability_type or normalized_capability_type not in allowed_capability_types):
        return {"reason": "allowed_capability_types", "message": "Capability type not in allowlist"}
    if allowed_adapter_actions and not _matches_action_constraint(
        constraints=allowed_adapter_actions,
        action_id=normalized_action_id,
        action_name=normalized_action_name,
    ):
        return {"reason": "allowed_adapter_actions", "message": "Adapter action not in allowlist"}

    return None


def evaluate_capability_constraint_decision(
    *,
    metadata: Dict[str, Any],
    capability_id: Optional[str],
    capability_type: Optional[str],
    action_id: Optional[str],
) -> Optional[Dict[str, str]]:
    return _evaluate_capability_constraints(
        metadata=metadata,
        capability_id=capability_id,
        capability_type=capability_type,
        action_id=action_id,
    )


def _normalize_constraint_capability_ids(value: Any, *, capability_type: Optional[str], action_id: Optional[str]) -> list[str]:
    entries = _as_lower_str_list(value)
    normalized: list[str] = []
    for entry in entries:
        if ":" in entry:
            normalized.append(entry)
            continue
        expanded = _expand_capability_name_candidates(
            entry,
            capability_type=capability_type,
            action_id=action_id,
        )
        normalized.extend(expanded)
    return sorted(set(normalized))


def _normalize_constraint_capability_types(value: Any) -> list[str]:
    alias_map = {
        "action": "adapter_action",
        "adapter": "adapter_action",
        "channel": "channel_action",
        "tool": "tool",
        "skill": "skill",
        "adapter_action": "adapter_action",
        "channel_action": "channel_action",
    }
    normalized: list[str] = []
    for item in _as_lower_str_list(value):
        mapped = alias_map.get(item)
        if mapped:
            normalized.append(mapped)
    return sorted(set(normalized))


def _expand_capability_name_candidates(
    name: str,
    *,
    capability_type: Optional[str],
    action_id: Optional[str],
) -> list[str]:
    normalized_name = str(name or "").strip().lower()
    normalized_type = str(capability_type or "").strip().lower()
    if not normalized_name:
        return []
    if normalized_type == "tool":
        return [f"tool:{normalized_name}"]
    if normalized_type == "skill":
        return [f"skill:{normalized_name}"]
    if normalized_type == "channel_action":
        return [f"channel_action:{normalized_name}"]
    if normalized_type == "adapter_action":
        return [f"adapter_action_name:{normalized_name}"]
    if str(action_id or "").strip().lower():
        return [f"adapter_action_name:{normalized_name}"]
    return [normalized_name]


def _normalize_action_constraints(*values: Any) -> list[str]:
    normalized: list[str] = []
    for value in values:
        normalized.extend(_as_lower_str_list(value))
    return sorted(set(normalized))


def _matches_capability_constraint(
    *,
    constraints: list[str],
    capability_id: str,
    capability_type: str,
    action_name: str,
) -> bool:
    if not constraints:
        return False
    for constraint in constraints:
        if constraint == capability_id:
            return True
        if constraint.startswith("adapter_action_name:") and capability_type == "adapter_action":
            if constraint.split(":", 1)[1] == action_name:
                return True
    return False


def _matches_action_constraint(*, constraints: list[str], action_id: str, action_name: str) -> bool:
    if not constraints:
        return False
    return action_id in constraints or action_name in constraints
