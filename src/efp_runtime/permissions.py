"""Permission primitives for Runtime v2 tool execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Protocol

from .types import utc_now_iso


PermissionAction = str
ALLOW: PermissionAction = "allow"
DENY: PermissionAction = "deny"
ASK: PermissionAction = "ask"
PermissionRuleScope = str
ONCE: PermissionRuleScope = "once"
ALWAYS: PermissionRuleScope = "always"


@dataclass(frozen=True)
class PermissionMetadata:
    """Static permission metadata carried by a tool definition."""

    action: PermissionAction = ALLOW
    reason: str = ""
    category: str = ""
    resource: str = ""
    risk: str = "low"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(init=False, frozen=True)
class PermissionRequest:
    """Structured request emitted when a tool requires user approval."""

    request_id: str
    session_id: str | None
    tool_id: str
    action: PermissionAction
    category: str
    resource: str
    risk: str
    reason: str
    args: dict[str, Any]
    patterns: list[str]
    created_at: str
    metadata: dict[str, Any]

    def __init__(
        self,
        *,
        request_id: str | None = None,
        id: str | None = None,
        session_id: str | None = None,
        tool_id: str,
        action: PermissionAction | None = None,
        category: str | None = None,
        resource: str | None = None,
        risk: str | None = None,
        reason: str = "",
        args: Mapping[str, Any] | None = None,
        patterns: Iterable[Any] | str | None = None,
        created_at: str | None = None,
        metadata: PermissionMetadata | Mapping[str, Any] | None = None,
    ) -> None:
        permission_metadata = metadata if isinstance(metadata, PermissionMetadata) else None
        request_metadata = (
            dict(permission_metadata.data)
            if permission_metadata is not None
            else dict(metadata or {})
        )
        resolved_action = action or (permission_metadata.action if permission_metadata else ASK)
        resolved_category = category if category is not None else (
            permission_metadata.category if permission_metadata else ""
        )
        resolved_resource = resource if resource is not None else (
            permission_metadata.resource if permission_metadata else ""
        )
        resolved_risk = risk if risk is not None else (
            permission_metadata.risk if permission_metadata else "low"
        )
        resolved_reason = reason or (
            permission_metadata.reason if permission_metadata else ""
        )
        resolved_args = dict(args or {})
        resolved_patterns = _normalize_patterns(patterns)
        resolved_request_id = request_id or id or _make_request_id(
            session_id=session_id,
            tool_id=tool_id,
            action=resolved_action,
            category=resolved_category,
            resource=resolved_resource,
            risk=resolved_risk,
            reason=resolved_reason,
            args=resolved_args,
            patterns=resolved_patterns,
            metadata=request_metadata,
        )

        object.__setattr__(self, "request_id", resolved_request_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "action", resolved_action)
        object.__setattr__(self, "category", resolved_category)
        object.__setattr__(self, "resource", resolved_resource)
        object.__setattr__(self, "risk", resolved_risk)
        object.__setattr__(self, "reason", resolved_reason)
        object.__setattr__(self, "args", resolved_args)
        object.__setattr__(self, "patterns", resolved_patterns)
        object.__setattr__(self, "created_at", created_at or utc_now_iso())
        object.__setattr__(self, "metadata", request_metadata)

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        reason: str = "",
        context: Any = None,
        patterns: Iterable[Any] | str | None = None,
    ) -> "PermissionRequest":
        request_patterns = (
            _normalize_patterns(patterns)
            if patterns is not None
            else _request_patterns(args=args, metadata=metadata, context=context)
        )
        request_metadata = dict(metadata.data)
        request_metadata.update(_context_request_metadata(context))
        return cls(
            session_id=_context_session_id(context),
            tool_id=tool_id,
            args=dict(args),
            action=metadata.action,
            category=metadata.category,
            resource=metadata.resource,
            risk=metadata.risk,
            reason=reason or metadata.reason,
            patterns=request_patterns,
            metadata=request_metadata,
        )

    @property
    def id(self) -> str:
        return self.request_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "id": self.request_id,
            "session_id": self.session_id,
            "tool_id": self.tool_id,
            "action": self.action,
            "category": self.category,
            "resource": self.resource,
            "risk": self.risk,
            "reason": self.reason,
            "args": _json_safe(dict(self.args)),
            "patterns": list(self.patterns),
            "created_at": self.created_at,
            "metadata": _json_safe(dict(self.metadata)),
        }


@dataclass(frozen=True)
class PermissionRule:
    """A broker rule that can approve, deny, or re-ask matching tool calls."""

    action: PermissionAction
    tool_id: str | None = None
    category: str | None = None
    patterns: Iterable[Any] | str | None = None
    scope: PermissionRuleScope = ALWAYS
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in (ALLOW, DENY, ASK):
            raise ValueError(f"Unknown permission rule action: {self.action}")
        if self.scope not in (ONCE, ALWAYS):
            raise ValueError(f"Unknown permission rule scope: {self.scope}")
        if not self.tool_id and not self.category:
            raise ValueError("PermissionRule requires tool_id or category.")
        object.__setattr__(self, "patterns", tuple(_normalize_patterns(self.patterns)))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "category": self.category,
            "action": self.action,
            "patterns": list(self.patterns or ()),
            "scope": self.scope,
            "reason": self.reason,
            "metadata": _json_safe(dict(self.metadata)),
        }


@dataclass(frozen=True)
class PermissionDecision:
    """Permission evaluator result."""

    action: PermissionAction
    reason: str = ""
    request: PermissionRequest | None = None

    @classmethod
    def allow(cls, reason: str = "") -> "PermissionDecision":
        return cls(action=ALLOW, reason=reason)

    @classmethod
    def deny(cls, reason: str = "") -> "PermissionDecision":
        return cls(action=DENY, reason=reason)

    @classmethod
    def ask(cls, request: PermissionRequest, reason: str = "") -> "PermissionDecision":
        return cls(action=ASK, reason=reason or request.reason, request=request)


class PermissionEvaluator(Protocol):
    """Protocol for async permission evaluators."""

    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: Any = None,
    ) -> PermissionDecision:
        ...


class StaticPermissionEvaluator:
    """Evaluator based only on a tool's static permission metadata."""

    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: Any = None,
    ) -> PermissionDecision:
        action = metadata.action or ALLOW
        if action == ALLOW:
            return PermissionDecision.allow(metadata.reason)
        if action == DENY:
            return PermissionDecision.deny(metadata.reason or "Tool execution denied.")
        if action == ASK:
            request = PermissionRequest.create(
                tool_id=tool_id,
                args=args,
                metadata=metadata,
                reason=metadata.reason or "Tool execution requires permission.",
                context=context,
            )
            return PermissionDecision.ask(request)
        return PermissionDecision.deny(f"Unknown permission action: {action}")


class PermissionBroker:
    """Stateful permission evaluator with pending requests and approval rules."""

    def __init__(self, rules: Iterable[PermissionRule] | None = None):
        self._rules: list[PermissionRule] = []
        self._pending: dict[str, PermissionRequest] = {}
        for rule in rules or ():
            self.add_rule(rule)

    @property
    def rules(self) -> list[PermissionRule]:
        return list(self._rules)

    def add_rule(self, rule: PermissionRule) -> None:
        self._rules.append(rule)

    def pending(self) -> list[PermissionRequest]:
        return list(self._pending.values())

    def get(self, request_id: str) -> PermissionRequest | None:
        return self._pending.get(request_id)

    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: Any = None,
    ) -> PermissionDecision:
        rule = self._matching_rule(
            tool_id=tool_id,
            args=args,
            metadata=metadata,
        )
        if rule is not None:
            if rule.action == ASK:
                return self._ask(
                    tool_id=tool_id,
                    args=args,
                    metadata=metadata,
                    context=context,
                    reason=rule.reason or metadata.reason,
                    patterns=rule.patterns,
                )
            return self._decision_from_rule(rule)

        action = metadata.action or ALLOW
        if action == ALLOW:
            return PermissionDecision.allow(metadata.reason)
        if action == DENY:
            return PermissionDecision.deny(metadata.reason or "Tool execution denied.")
        if action == ASK:
            return self._ask(
                tool_id=tool_id,
                args=args,
                metadata=metadata,
                context=context,
                reason=metadata.reason or "Tool execution requires permission.",
            )
        return PermissionDecision.deny(f"Unknown permission action: {action}")

    def approve(self, request_id: str, *, always: bool = False) -> PermissionDecision:
        request = self._take_pending(request_id)
        self._rules.append(_rule_from_request(request, action=ALLOW, always=always))
        return PermissionDecision.allow("Permission approved.")

    def deny(
        self,
        request_id: str,
        *,
        always: bool = False,
        reason: str | None = None,
    ) -> PermissionDecision:
        request = self._take_pending(request_id)
        deny_reason = reason or "Permission denied."
        self._rules.append(
            _rule_from_request(
                request,
                action=DENY,
                always=always,
                reason=deny_reason,
            )
        )
        return PermissionDecision.deny(deny_reason)

    def _take_pending(self, request_id: str) -> PermissionRequest:
        request = self._pending.pop(request_id, None)
        if request is None:
            raise KeyError(f"Unknown permission request: {request_id}")
        return request

    def _matching_rule(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
    ) -> PermissionRule | None:
        args_text = _stringify_args(args)
        for index in range(len(self._rules) - 1, -1, -1):
            rule = self._rules[index]
            if not _rule_matches(
                rule,
                tool_id=tool_id,
                category=metadata.category,
                args_text=args_text,
            ):
                continue
            if rule.scope == ONCE:
                del self._rules[index]
            return rule
        return None

    def _decision_from_rule(self, rule: PermissionRule) -> PermissionDecision:
        if rule.action == ALLOW:
            return PermissionDecision.allow(rule.reason)
        if rule.action == DENY:
            return PermissionDecision.deny(rule.reason or "Tool execution denied.")
        return PermissionDecision.deny(f"Unknown permission action: {rule.action}")

    def _ask(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: Any,
        reason: str,
        patterns: Iterable[Any] | str | None = None,
    ) -> PermissionDecision:
        request_metadata = PermissionMetadata(
            action=ASK,
            reason=reason,
            category=metadata.category,
            resource=metadata.resource,
            risk=metadata.risk,
            data=dict(metadata.data),
        )
        request = PermissionRequest.create(
            tool_id=tool_id,
            args=args,
            metadata=request_metadata,
            reason=reason,
            context=context,
            patterns=patterns,
        )
        existing = self._pending.get(request.request_id)
        if existing is None:
            self._pending[request.request_id] = request
        else:
            request = existing
        return PermissionDecision.ask(request)


def _rule_from_request(
    request: PermissionRequest,
    *,
    action: PermissionAction,
    always: bool,
    reason: str = "",
) -> PermissionRule:
    scope = ALWAYS if always else ONCE
    patterns: Iterable[Any] | str | None
    if always:
        patterns = request.patterns
    else:
        patterns = request.patterns or [_stringify_args(request.args)]
    return PermissionRule(
        tool_id=request.tool_id,
        category=request.category or None,
        action=action,
        patterns=patterns,
        scope=scope,
        reason=reason or request.reason,
    )


def _rule_matches(
    rule: PermissionRule,
    *,
    tool_id: str,
    category: str,
    args_text: str,
) -> bool:
    if rule.tool_id and rule.tool_id != tool_id:
        return False
    if rule.category and rule.category != category:
        return False

    patterns = tuple(rule.patterns or ())
    if not patterns:
        return True
    return any(pattern == "*" or pattern in args_text for pattern in patterns)


def _make_request_id(
    *,
    session_id: str | None,
    tool_id: str,
    action: PermissionAction,
    category: str,
    resource: str,
    risk: str,
    reason: str,
    args: Mapping[str, Any],
    patterns: Iterable[Any],
    metadata: Mapping[str, Any],
) -> str:
    payload = {
        "session_id": session_id,
        "tool_id": tool_id,
        "action": action,
        "category": category,
        "resource": resource,
        "risk": risk,
        "reason": reason,
        "args": dict(args),
        "patterns": list(patterns),
        "metadata": dict(metadata),
    }
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return f"perm_{digest[:24]}"


def _request_patterns(
    *,
    args: Mapping[str, Any],
    metadata: PermissionMetadata,
    context: Any,
) -> list[str]:
    del args
    patterns = _normalize_patterns(metadata.data.get("patterns"))
    if not patterns:
        patterns = _normalize_patterns(metadata.data.get("pattern"))
    if patterns:
        return patterns

    context_metadata = _context_metadata(context)
    permission_hints = context_metadata.get("permission") if context_metadata else None
    if isinstance(permission_hints, Mapping):
        patterns = _normalize_patterns(permission_hints.get("patterns"))
        if not patterns:
            patterns = _normalize_patterns(permission_hints.get("pattern"))
    if not patterns and context_metadata:
        patterns = _normalize_patterns(context_metadata.get("permission_patterns"))
    return patterns


def _context_request_metadata(context: Any) -> dict[str, Any]:
    context_metadata = _context_metadata(context)
    if not context_metadata:
        return {}

    permission_hints = context_metadata.get("permission")
    if isinstance(permission_hints, Mapping):
        request_metadata = permission_hints.get("metadata")
        if isinstance(request_metadata, Mapping):
            return dict(request_metadata)

    request_metadata = context_metadata.get("permission_metadata")
    if isinstance(request_metadata, Mapping):
        return dict(request_metadata)
    return {}


def _context_metadata(context: Any) -> Mapping[str, Any]:
    metadata = getattr(context, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _context_session_id(context: Any) -> str | None:
    session_id = getattr(context, "session_id", None)
    return str(session_id) if session_id is not None else None


def _normalize_patterns(patterns: Iterable[Any] | str | None) -> list[str]:
    if patterns is None:
        return []
    if isinstance(patterns, str):
        return [patterns]
    if isinstance(patterns, bytes):
        return [patterns.decode("utf-8", errors="replace")]
    if isinstance(patterns, Iterable):
        return [str(pattern) for pattern in patterns if pattern is not None]
    return [str(patterns)]


def _stringify_args(args: Mapping[str, Any]) -> str:
    return _stable_json(dict(args))


def _stable_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))
