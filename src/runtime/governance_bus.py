"""Structured governance bus contracts and default implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import uuid

from src.agents.tool_result_policy import should_passthrough_tool_result
from src.runtime.capability_registry import get_capability_registry
from src.runtime.contracts import ExecutionRequest, ExecutionResult, make_execution_result
from src.runtime.events import build_runtime_event
from src.runtime.task_capability_contracts import resolve_task_capability_contract
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
            capability_aliases=capability_context.get("capability_aliases") or [],
            capability_type=capability_context.get("capability_type"),
            action_id=capability_context.get("action_id"),
            execution_type=request.execution_type,
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

        identity_decision = _evaluate_identity_binding_constraints(
            request=request,
            metadata=metadata,
            capability_id=capability_context.get("capability_id"),
            capability_type=capability_context.get("capability_type"),
            requires_identity_binding=bool(capability_context.get("requires_identity_binding")),
        )
        if identity_decision is not None:
            deny_reason = identity_decision.get("reason") or "missing_identity_binding"
            audit = make_governance_audit_record(
                request=request,
                stage="before_execute",
                message=identity_decision.get("message") or "Blocked by identity binding policy",
                metadata={
                    "policy_profile_id": policy_profile,
                    "rule": "identity_binding",
                    "execution_type": request.execution_type,
                    "capability_id": capability_context.get("capability_id"),
                    "capability_type": capability_context.get("capability_type"),
                    "deny_reason": deny_reason,
                    **(identity_decision.get("metadata") or {}),
                },
            )
            return GovernanceDecision(allowed=False, reason=deny_reason, audit_record=audit)

        mutation_decision = _evaluate_mutation_tool_constraints(
            metadata=metadata,
            capability_context=capability_context,
        )
        if mutation_decision is not None:
            deny_reason = mutation_decision.get("reason") or "mutation_tool_requires_explicit_allow"
            audit = make_governance_audit_record(
                request=request,
                stage="before_execute",
                message=mutation_decision.get("message") or "Blocked mutation tool without explicit allow",
                metadata={
                    "policy_profile_id": policy_profile,
                    "execution_type": request.execution_type,
                    **(mutation_decision.get("metadata") or {}),
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


def run_pre_tool_hooks(
    *,
    runtime_config: Optional[Any],
    session_id: str,
    tool_name: str,
    payload: Optional[Dict[str, Any]] = None,
    event_callback: Any = None,
) -> Any:
    """Governance boundary facade for pre-tool hook invocation."""
    try:
        from src.agents.skill_runtime import apply_skill_hooks
        return apply_skill_hooks(
            runtime_config=runtime_config,
            stage="pre_tool",
            session_id=session_id,
            tool_name=tool_name,
            payload=payload,
            event_callback=event_callback,
        )
    except Exception:
        from src.agents.skill_runtime import HookEffects

        return HookEffects()


def run_post_tool_hooks(
    *,
    runtime_config: Optional[Any],
    session_id: str,
    tool_name: str,
    payload: Optional[Dict[str, Any]] = None,
    event_callback: Any = None,
) -> Any:
    """Governance boundary facade for post-tool hook invocation."""
    try:
        from src.agents.skill_runtime import apply_skill_hooks
        return apply_skill_hooks(
            runtime_config=runtime_config,
            stage="post_tool",
            session_id=session_id,
            tool_name=tool_name,
            payload=payload,
            event_callback=event_callback,
        )
    except Exception:
        from src.agents.skill_runtime import HookEffects

        return HookEffects()


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
    capability_aliases: list[str] = []
    action_id: Optional[str] = None
    if request.execution_type == "task":
        if task_type in {"adapter_action_task", "jira_workflow_review_task", "github_review_task", "triggered_event_task", "delegation_task"}:
            # Governance intentionally follows the same canonical wrapper-task
            # contract used by execution to avoid split sources of truth.
            plan = resolve_task_capability_contract(task_type, payload)
            capability_id = str(plan.get("primary_capability_id") or plan.get("capability_id") or "").strip().lower() or None
            action_id = str(plan.get("action_id") or capability_id or "").strip().lower() or None
            if task_type == "github_review_task":
                # Secondary adapter actions for this wrapper task are gated at invocation
                # time inside the runtime handler rather than at task admission.
                action_id = None
        elif task_type == "tool_task":
            tool_name = str(payload.get("tool_name") or "").strip().lower()
            descriptor, resolved_capability_id, aliases = _resolve_tool_descriptor_by_name(tool_name)
            capability_id = resolved_capability_id
            capability_aliases = aliases
    elif request.execution_type == "tool":
        tool_name = str(payload.get("tool_name") or metadata.get("tool_name") or "").strip().lower()
        descriptor, resolved_capability_id, aliases = _resolve_tool_descriptor_by_name(tool_name)
        capability_id = resolved_capability_id
        capability_aliases = aliases
    elif request.execution_type == "skill":
        skill_name = str(payload.get("skill_name") or "").strip().lower()
        capability_id = f"skill:{skill_name}" if skill_name else None

    descriptor = locals().get("descriptor")
    if descriptor is None:
        descriptor = get_capability_registry().get(capability_id) if capability_id else None
    descriptor_metadata = descriptor.metadata if descriptor is not None and isinstance(descriptor.metadata, dict) else {}
    policy_tags = list(descriptor.policy_tags or []) if descriptor is not None else []
    if isinstance(descriptor_metadata.get("policy_tags"), list):
        for tag in descriptor_metadata.get("policy_tags") or []:
            if tag not in policy_tags:
                policy_tags.append(tag)
    mutation = bool(descriptor_metadata.get("mutation"))
    risk_level = descriptor_metadata.get("risk_level")
    tool_source = descriptor_metadata.get("tool_source")
    external_tool = bool(descriptor_metadata.get("external_tool"))
    capability_type = descriptor.type if descriptor is not None else _infer_capability_type_from_id(capability_id)
    if request.execution_type == "task" and task_type == "github_review_task":
        capability_type = "adapter_action"
    return {
        "capability_id": capability_id,
        "capability_aliases": capability_aliases,
        "capability_type": capability_type,
        "action_id": action_id,
        "tool_name": str(payload.get("tool_name") or metadata.get("tool_name") or "").strip() or None,
        "tool_id": descriptor_metadata.get("tool_id") or capability_id,
        "policy_tags": policy_tags,
        "mutation": mutation,
        "risk_level": risk_level,
        "external_tool": external_tool,
        "tool_source": tool_source,
        "requires_identity_binding": bool(descriptor.requires_identity_binding) if descriptor is not None else False,
    }


def _resolve_tool_descriptor_by_name(tool_name: str):
    registry = get_capability_registry()
    normalized = str(tool_name or "").strip().lower()
    if not normalized:
        return None, None, []
    canonical_id = f"tool:{normalized}"
    aliases = [canonical_id]
    descriptor = registry.get(canonical_id)
    if descriptor is not None:
        if descriptor.capability_id not in aliases:
            aliases.append(descriptor.capability_id)
        return descriptor, descriptor.capability_id, aliases
    for candidate in registry.list_by_type("tool"):
        candidate_name = str(getattr(candidate, "name", "") or "").strip().lower()
        metadata = getattr(candidate, "metadata", {}) if isinstance(getattr(candidate, "metadata", {}), dict) else {}
        metadata_tool_name = str(metadata.get("tool_name") or "").strip().lower()
        if candidate_name == normalized or metadata_tool_name == normalized:
            if candidate.capability_id not in aliases:
                aliases.append(candidate.capability_id)
            return candidate, candidate.capability_id, aliases
    return None, canonical_id, aliases


def _permission_decision_allows_mutation(metadata: Dict[str, Any]) -> bool:
    decision = str(metadata.get("permission_decision") or metadata.get("tool_permission_decision") or "").strip().lower()
    return metadata.get("governance_allow_mutation") is True or decision in {"allow", "approved", "allow_once", "allow_always"}


def _evaluate_mutation_tool_constraints(*, metadata: Dict[str, Any], capability_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if str(capability_context.get("capability_type") or "").lower() != "tool":
        return None
    if not capability_context.get("external_tool"):
        return None
    tags = {str(x).lower() for x in capability_context.get("policy_tags") or []}
    risk = str(capability_context.get("risk_level") or "").lower()
    mutation = bool(capability_context.get("mutation")) or risk in {"high", "critical"} or "mutation" in tags or "write" in tags
    if not mutation:
        return None
    if _permission_decision_allows_mutation(metadata):
        return None
    return {
        "reason": "mutation_tool_requires_explicit_allow",
        "message": "External mutation tool requires explicit governance allow",
        "metadata": {
            "rule": "mutation_tool_requires_explicit_allow",
            "capability_id": capability_context.get("capability_id"),
            "capability_type": capability_context.get("capability_type"),
            "tool_name": capability_context.get("tool_name"),
            "tool_id": capability_context.get("tool_id"),
            "mutation": capability_context.get("mutation"),
            "risk_level": capability_context.get("risk_level"),
            "policy_tags": capability_context.get("policy_tags") or [],
            "tool_source": capability_context.get("tool_source"),
            "external_tool": capability_context.get("external_tool"),
        },
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
    capability_aliases: Optional[list[str]] = None,
    capability_type: Optional[str],
    action_id: Optional[str],
    execution_type: Optional[str] = None,
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
    candidate_capability_ids = {normalized_capability_id} if normalized_capability_id else set()
    for alias in capability_aliases or []:
        alias_id = str(alias or "").strip().lower()
        if alias_id:
            candidate_capability_ids.add(alias_id)
    normalized_capability_type = str(capability_type or "").strip().lower()
    normalized_action_id = str(action_id or "").strip().lower()
    normalized_action_name = normalized_action_id.split(":")[-1] if normalized_action_id else ""
    normalized_execution_type = str(execution_type or "").strip().lower()
    has_capability_id_context = bool(normalized_capability_id or normalized_action_id)
    has_capability_type_context = bool(normalized_capability_type)
    is_chat_request_without_capability_context = (
        normalized_execution_type == "chat"
        and not has_capability_id_context
        and not has_capability_type_context
    )

    if any(
        _matches_capability_constraint(
            constraints=denied_capability_ids,
            capability_id=candidate_id,
            capability_type=normalized_capability_type,
            action_name=normalized_action_name,
        )
        for candidate_id in (candidate_capability_ids or {normalized_capability_id})
    ):
        return {"reason": "denied_capability_ids", "message": f"Capability blocked: {normalized_capability_id}"}
    if denied_capability_types and normalized_capability_type and normalized_capability_type in denied_capability_types:
        return {"reason": "denied_capability_types", "message": f"Capability type blocked: {normalized_capability_type}"}
    if normalized_action_id and _matches_action_constraint(
        constraints=denied_adapter_actions,
        action_id=normalized_action_id,
        action_name=normalized_action_name,
    ):
        return {"reason": "denied_adapter_actions", "message": f"Adapter action blocked: {normalized_action_id}"}

    # Important:
    # Chat executions can carry allowed_capability_ids as tool-loop metadata.
    # A chat request itself has no resolved capability_id; do not block the
    # chat admission just because tool allowlist metadata is present.
    # The allowlist is still enforced for real tool/skill/task requests because
    # those requests have resolved capability context.
    if allowed_capability_ids and not is_chat_request_without_capability_context:
        if not has_capability_id_context:
            return {"reason": "allowed_capability_ids", "message": "Capability not in allowlist"}
        if not any(
            _matches_capability_constraint(
                constraints=allowed_capability_ids,
                capability_id=candidate_id,
                capability_type=normalized_capability_type,
                action_name=normalized_action_name,
            )
            for candidate_id in (candidate_capability_ids or {normalized_capability_id})
        ):
            return {"reason": "allowed_capability_ids", "message": "Capability not in allowlist"}
    if allowed_capability_types and not is_chat_request_without_capability_context:
        if not has_capability_type_context or normalized_capability_type not in allowed_capability_types:
            return {"reason": "allowed_capability_types", "message": "Capability type not in allowlist"}
    if normalized_action_id and allowed_adapter_actions and not _matches_action_constraint(
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
    execution_type: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    return _evaluate_capability_constraints(
        metadata=metadata,
        capability_id=capability_id,
        capability_type=capability_type,
        action_id=action_id,
        execution_type=execution_type,
    )


def _normalize_constraint_capability_ids(value: Any, *, capability_type: Optional[str], action_id: Optional[str]) -> list[str]:
    entries = _as_lower_str_list(value)
    normalized: list[str] = []
    for entry in entries:
        if not entry:
            continue
        normalized.append(entry)
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


def _evaluate_identity_binding_constraints(
    *,
    request: ExecutionRequest,
    metadata: Dict[str, Any],
    capability_id: Optional[str],
    capability_type: Optional[str],
    requires_identity_binding: bool,
) -> Optional[Dict[str, Any]]:
    normalized_type = str(capability_type or "").strip().lower()
    if not requires_identity_binding or normalized_type not in {"adapter_action", "channel_action"}:
        return None
    if not _is_external_or_task_like_request(request):
        return None

    binding = _extract_identity_binding(metadata)
    if not binding:
        return {"reason": "missing_identity_binding", "message": "Missing required identity binding metadata"}

    expected_systems = _expected_identity_binding_systems(capability_id=capability_id, capability_type=normalized_type)
    binding_system = str(binding.get("system_type") or "").strip().lower()
    if expected_systems and binding_system and binding_system not in expected_systems:
        return {
            "reason": "identity_binding_system_mismatch",
            "message": "Identity binding system does not match capability target",
            "metadata": {"expected_system_type": sorted(expected_systems), "provided_system_type": binding_system},
        }
    return None


def _is_external_or_task_like_request(request: ExecutionRequest) -> bool:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    if request.execution_type in {"event", "subagent"}:
        return True
    if request.source_type in {"github", "jira", "portal", "internal"}:
        return True
    return metadata.get("external_triggered") is True


def _extract_identity_binding(metadata: Dict[str, Any]) -> Optional[Dict[str, str]]:
    nested = metadata.get("identity_binding")
    if isinstance(nested, dict):
        system_type = str(nested.get("system_type") or "").strip().lower()
        binding_id = str(nested.get("id") or nested.get("identity_binding_id") or "").strip()
        external_account_id = str(nested.get("external_account_id") or "").strip()
        if system_type and (binding_id or external_account_id):
            return {
                "id": binding_id,
                "system_type": system_type,
                "external_account_id": external_account_id,
            }

    system_type = str(metadata.get("identity_binding_system_type") or "").strip().lower()
    binding_id = str(metadata.get("identity_binding_id") or "").strip()
    external_account_id = str(metadata.get("identity_binding_external_account_id") or "").strip()
    if system_type and (binding_id or external_account_id):
        return {
            "id": binding_id,
            "system_type": system_type,
            "external_account_id": external_account_id,
        }
    return None


def _expected_identity_binding_systems(*, capability_id: Optional[str], capability_type: str) -> set[str]:
    normalized_id = str(capability_id or "").strip().lower()
    if capability_type == "adapter_action" and normalized_id.startswith("adapter:"):
        parts = normalized_id.split(":")
        if len(parts) > 1 and parts[1]:
            return {parts[1]}
    if capability_type == "channel_action" and normalized_id.startswith("channel_action:"):
        name = normalized_id.split(":", 1)[1]
        if name.startswith("jira_"):
            return {"jira"}
        if name.startswith("confluence_"):
            return {"confluence"}
    return set()
