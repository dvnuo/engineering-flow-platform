"""Structured governance bus contracts and default implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import uuid

from src.agents.tool_result_policy import should_passthrough_tool_result
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
    return build_runtime_event(
        event_type="governance.audit",
        execution_type=request.execution_type,
        state=status,
        session_id=request.session_id,
        request_id=request.request_id,
        agent_id=request.agent_id,
        summary=audit_record.message,
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
        # Phase 2 transition note:
        # We keep the public after_execute hook stable, and explicitly carry
        # result validate/enforce semantics through internal helper functions.
        result, notes = self._normalize_execution_result_for_policy(request, result)
        result, policy_notes = self._apply_policy_post_validation(request, result)
        notes.extend(policy_notes)

        if notes:
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
