"""Permission primitives for Runtime v2 tool execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import fnmatch
import hashlib
import json
from typing import Any, Protocol

from .shell_permissions import shell_permission_metadata, shell_permission_patterns
from .types import utc_now_iso


PermissionAction = str
ALLOW: PermissionAction = "allow"
DENY: PermissionAction = "deny"
ASK: PermissionAction = "ask"
PermissionRuleScope = str
ONCE: PermissionRuleScope = "once"
ALWAYS: PermissionRuleScope = "always"
AGENT_PERMISSION_OVERLAY_METADATA_KEY = "agent_permission_overlay"
AGENT_PERMISSION_OVERLAY_SOURCE_KEY = "agent_permission_overlay_source"
AGENT_PERMISSION_OVERLAY_SOURCE = "agent_profile"


@dataclass(frozen=True)
class PermissionMetadata:
    """Static permission metadata carried by a tool definition."""

    action: PermissionAction = ALLOW
    reason: str = ""
    category: str = ""
    resource: str = ""
    risk: str = "low"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionConfigRule:
    """A normalized runtime permission config entry."""

    key: str
    action: PermissionAction
    reason: str = ""
    risk: str | None = None
    patterns: tuple[str, ...] = ()
    subject_pattern: str | None = None
    order: int = 0


@dataclass(frozen=True)
class PermissionConfigMatch:
    """The config rule selected for one permission evaluation."""

    rule: PermissionConfigRule
    match_type: str
    subject: str | None = None


class PermissionConfig:
    """Opencode-style runtime permission config matcher."""

    def __init__(self, permissions: Mapping[str, Any] | None = None):
        self._rules = tuple(_permission_config_rules(permissions or {}))

    @property
    def rules(self) -> tuple[PermissionConfigRule, ...]:
        return self._rules

    def match(
        self,
        *,
        tool_id: str,
        metadata: PermissionMetadata,
        args: Mapping[str, Any] | None = None,
    ) -> PermissionConfigMatch | None:
        tool_id = str(tool_id)
        subject = _permission_subject(args or {}, metadata)

        exact = self._match_exact(tool_id)
        if exact is not None:
            return PermissionConfigMatch(rule=exact, match_type="exact")

        exact_subject = self._match_exact_subject(tool_id, subject)
        if exact_subject is not None:
            return PermissionConfigMatch(
                rule=exact_subject,
                match_type="exact_subject",
                subject=subject,
            )

        wildcard = self._match_wildcard(tool_id)
        if wildcard is not None:
            return PermissionConfigMatch(rule=wildcard, match_type="wildcard")

        wildcard_subject = self._match_wildcard_subject(tool_id, subject)
        if wildcard_subject is not None:
            return PermissionConfigMatch(
                rule=wildcard_subject,
                match_type="wildcard_subject",
                subject=subject,
            )

        category = self._match_category(tool_id=tool_id, metadata=metadata)
        if category is not None:
            return PermissionConfigMatch(rule=category, match_type="category")

        category_subject = self._match_category_subject(
            tool_id=tool_id,
            metadata=metadata,
            subject=subject,
        )
        if category_subject is not None:
            return PermissionConfigMatch(
                rule=category_subject,
                match_type="category_subject",
                subject=subject,
            )

        fallback = self._match_fallback()
        if fallback is not None:
            return PermissionConfigMatch(rule=fallback, match_type="fallback")

        fallback_subject = self._match_fallback_subject(subject)
        if fallback_subject is not None:
            return PermissionConfigMatch(
                rule=fallback_subject,
                match_type="fallback_subject",
                subject=subject,
            )

        return None

    def _match_exact(self, tool_id: str) -> PermissionConfigRule | None:
        for rule in self._rules:
            if rule.subject_pattern is None and rule.key == tool_id:
                return rule
        return None

    def _match_exact_subject(
        self,
        tool_id: str,
        subject: str | None,
    ) -> PermissionConfigRule | None:
        if subject is None:
            return None
        return _most_specific_subject_rule(
            rule
            for rule in self._rules
            if rule.key == tool_id and _subject_rule_matches(rule, subject)
        )

    def _match_wildcard(self, tool_id: str) -> PermissionConfigRule | None:
        matches = [
            rule
            for rule in self._rules
            if rule.subject_pattern is None
            and _is_permission_wildcard(rule.key)
            and rule.key != "*"
            and fnmatch.fnmatchcase(tool_id, rule.key)
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda rule: (-len(rule.key), rule.order))[0]

    def _match_wildcard_subject(
        self,
        tool_id: str,
        subject: str | None,
    ) -> PermissionConfigRule | None:
        if subject is None:
            return None
        return _most_specific_subject_rule(
            rule
            for rule in self._rules
            if _is_permission_wildcard(rule.key)
            and rule.key != "*"
            and fnmatch.fnmatchcase(tool_id, rule.key)
            and _subject_rule_matches(rule, subject)
        )

    def _match_category(
        self,
        *,
        tool_id: str,
        metadata: PermissionMetadata,
    ) -> PermissionConfigRule | None:
        category = metadata.category or ""
        for rule in self._rules:
            if rule.subject_pattern is not None:
                continue
            if _is_permission_wildcard(rule.key):
                continue
            if _permission_category_matches(
                rule.key,
                tool_id=tool_id,
                category=category,
            ):
                return rule
        return None

    def _match_category_subject(
        self,
        *,
        tool_id: str,
        metadata: PermissionMetadata,
        subject: str | None,
    ) -> PermissionConfigRule | None:
        if subject is None:
            return None
        category = metadata.category or ""
        return _most_specific_subject_rule(
            rule
            for rule in self._rules
            if not _is_permission_wildcard(rule.key)
            and _permission_category_matches(
                rule.key,
                tool_id=tool_id,
                category=category,
            )
            and _subject_rule_matches(rule, subject)
        )

    def _match_fallback(self) -> PermissionConfigRule | None:
        for rule in self._rules:
            if rule.subject_pattern is None and rule.key == "*":
                return rule
        return None

    def _match_fallback_subject(
        self,
        subject: str | None,
    ) -> PermissionConfigRule | None:
        if subject is None:
            return None
        return _most_specific_subject_rule(
            rule
            for rule in self._rules
            if rule.key == "*" and _subject_rule_matches(rule, subject)
        )


def is_permission_subject_hidden(
    tool_permissions: Mapping[str, Any] | PermissionConfig | None,
    *,
    tool_id: str,
    category: str,
    subject: str,
    resource: str = "context",
) -> bool:
    """Return true only when runtime config selects a deny rule for a subject."""

    if tool_permissions is None:
        return False
    config = (
        tool_permissions
        if isinstance(tool_permissions, PermissionConfig)
        else PermissionConfig(tool_permissions)
    )
    match = config.match(
        tool_id=tool_id,
        metadata=PermissionMetadata(
            category=category,
            resource=resource,
            data={"subject": subject},
        ),
        args={},
    )
    return match is not None and match.rule.action == DENY


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
            else _request_patterns(
                tool_id=tool_id,
                args=args,
                metadata=metadata,
                context=context,
            )
        )
        request_metadata = dict(metadata.data)
        request_metadata.update(_args_request_metadata(tool_id, args, metadata))
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

        pending_request = self._matching_pending_request(
            tool_id=tool_id,
            args=args,
            metadata=metadata,
        )
        if pending_request is not None:
            return PermissionDecision.ask(pending_request)

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

    def _matching_pending_request(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
    ) -> PermissionRequest | None:
        normalized_args = dict(args)
        for request in self._pending.values():
            if request.tool_id != tool_id:
                continue
            if request.category != metadata.category:
                continue
            if request.args != normalized_args:
                continue
            return request
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


class ConfiguredPermissionBroker(PermissionBroker):
    """Permission broker with opencode-style runtime config overrides."""

    def __init__(
        self,
        tool_permissions: Mapping[str, Any] | None = None,
        rules: Iterable[PermissionRule] | None = None,
    ):
        super().__init__(rules)
        self.permission_config = PermissionConfig(tool_permissions)

    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: Any = None,
    ) -> PermissionDecision:
        match = self.permission_config.match(
            tool_id=tool_id,
            args=args,
            metadata=metadata,
        )
        overlay_match = _context_permission_overlay_match(
            context,
            tool_id=tool_id,
            args=args,
            metadata=metadata,
        )
        if overlay_match is not None:
            return await super().evaluate(
                tool_id=tool_id,
                args=args,
                metadata=_metadata_from_permission_config(
                    metadata,
                    overlay_match,
                    source=AGENT_PERMISSION_OVERLAY_METADATA_KEY,
                    deny_reason_label="agent permission overlay",
                ),
                context=context,
            )
        if match is None:
            return await super().evaluate(
                tool_id=tool_id,
                args=args,
                metadata=metadata,
                context=context,
            )
        return await super().evaluate(
            tool_id=tool_id,
            args=args,
            metadata=_metadata_from_permission_config(metadata, match),
            context=context,
        )


def is_permission_subject_visible(
    tool_permissions: Mapping[str, Any] | None,
    *,
    tool_id: str,
    category: str,
    resource: str = "",
    subject: str,
) -> bool:
    """Return whether a subject should be shown for a permissioned tool."""

    match = PermissionConfig(tool_permissions).match(
        tool_id=tool_id,
        args={},
        metadata=PermissionMetadata(
            category=category,
            resource=resource,
            data={"subject": subject},
        ),
    )
    return match is None or match.rule.action != DENY


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


def _permission_subject(
    args: Mapping[str, Any],
    metadata: PermissionMetadata,
) -> str | None:
    subject = metadata.data.get("subject")
    if isinstance(subject, str) and subject:
        return subject

    subject_arg = metadata.data.get("subject_arg")
    if not isinstance(subject_arg, str) or not subject_arg:
        return None
    value = args.get(subject_arg)
    if isinstance(value, str) and value:
        return value
    return None


def _subject_rule_matches(rule: PermissionConfigRule, subject: str) -> bool:
    pattern = rule.subject_pattern
    return pattern is not None and fnmatch.fnmatchcase(subject, pattern)


def _most_specific_subject_rule(
    rules: Iterable[PermissionConfigRule],
) -> PermissionConfigRule | None:
    matches = list(rules)
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda rule: (-(len(rule.subject_pattern or "")), rule.order),
    )[0]


def normalize_tool_permissions(
    permissions: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a copied and validated runtime permission config mapping."""

    if permissions is None:
        return {}
    if not isinstance(permissions, Mapping):
        raise TypeError("tool_permissions must be a mapping")

    normalized: dict[str, Any] = {}
    for raw_key, raw_value in permissions.items():
        if not isinstance(raw_key, str):
            raise TypeError("tool_permissions keys must be strings")
        key = raw_key.strip()
        if not key:
            raise ValueError("tool_permissions keys must not be empty")

        if isinstance(raw_value, str):
            normalized[key] = _validate_permission_action(raw_value, key=key)
            continue

        if not isinstance(raw_value, Mapping):
            raise TypeError(
                "tool_permissions values must be strings or mappings"
            )

        value = dict(raw_value)
        if "action" in value:
            normalized[key] = _normalize_direct_permission_mapping(key, value)
            continue

        normalized[key] = _normalize_subject_permission_mapping(key, value)
    return normalized


def _normalize_direct_permission_mapping(
    key: str,
    value: dict[Any, Any],
) -> dict[str, Any]:
    action = value.get("action")
    if action is None:
        raise ValueError(f"tool_permissions[{key!r}] mapping requires an action")
    normalized = dict(value)
    normalized["action"] = _validate_permission_action(action, key=key)
    if "reason" in normalized and normalized["reason"] is not None:
        normalized["reason"] = str(normalized["reason"])
    if "risk" in normalized and normalized["risk"] is not None:
        normalized["risk"] = str(normalized["risk"])
    if "patterns" in normalized:
        normalized["patterns"] = _normalize_patterns(normalized["patterns"])
    elif "pattern" in normalized:
        normalized["patterns"] = _normalize_patterns(normalized["pattern"])
    return normalized


def _normalize_subject_permission_mapping(
    key: str,
    value: dict[Any, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_pattern, raw_rule in value.items():
        if not isinstance(raw_pattern, str):
            raise TypeError(
                f"tool_permissions[{key!r}] subject patterns must be strings"
            )
        subject_pattern = raw_pattern.strip()
        if not subject_pattern:
            raise ValueError(
                f"tool_permissions[{key!r}] subject patterns must not be empty"
            )

        nested_key = f"{key!r}][{subject_pattern!r}"
        if isinstance(raw_rule, str):
            normalized[subject_pattern] = _validate_permission_action(
                raw_rule,
                key=nested_key,
            )
            continue

        if not isinstance(raw_rule, Mapping):
            raise TypeError(
                f"tool_permissions[{key!r}][{subject_pattern!r}] "
                "nested permission values must be strings or mappings"
            )

        nested_rule = dict(raw_rule)
        unsupported = sorted(set(nested_rule) - {"action", "reason", "risk"})
        if unsupported:
            raise ValueError(
                f"tool_permissions[{key!r}][{subject_pattern!r}] "
                f"has unsupported key(s): {', '.join(unsupported)}"
            )
        action = nested_rule.get("action")
        if action is None:
            raise ValueError(
                f"tool_permissions[{key!r}][{subject_pattern!r}] "
                "mapping requires an action"
            )
        nested_rule["action"] = _validate_permission_action(action, key=nested_key)
        if "reason" in nested_rule and nested_rule["reason"] is not None:
            nested_rule["reason"] = str(nested_rule["reason"])
        if "risk" in nested_rule and nested_rule["risk"] is not None:
            nested_rule["risk"] = str(nested_rule["risk"])
        normalized[subject_pattern] = nested_rule
    return normalized


def normalize_agent_permission_overlay(
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return normalized permission metadata from an agent profile."""

    if not metadata:
        return {}
    if not isinstance(metadata, Mapping):
        raise ValueError("agent profile metadata must be a mapping")
    if "permission" not in metadata:
        return {}

    permissions = metadata.get("permission")
    if permissions is None:
        return {}
    if not isinstance(permissions, Mapping):
        raise ValueError("agent profile permission metadata must be a mapping")
    try:
        return normalize_tool_permissions(permissions)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid agent profile permission metadata: {exc}") from exc


def merge_tool_permission_configs(
    base_permissions: Mapping[str, Any] | None,
    overlay_permissions: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return normalized permissions with overlay keys replacing base keys."""

    merged = normalize_tool_permissions(base_permissions)
    merged.update(normalize_tool_permissions(overlay_permissions))
    return merged


def _permission_config_rules(
    permissions: Mapping[str, Any],
) -> list[PermissionConfigRule]:
    normalized = normalize_tool_permissions(permissions)
    rules: list[PermissionConfigRule] = []
    order = 0
    for key, value in normalized.items():
        if isinstance(value, str):
            rules.append(
                PermissionConfigRule(
                    key=key,
                    action=value,
                    order=order,
                )
            )
            order += 1
            continue

        if "action" in value:
            rules.append(
                PermissionConfigRule(
                    key=key,
                    action=value["action"],
                    reason=str(value.get("reason") or ""),
                    risk=(
                        None
                        if value.get("risk") is None
                        else str(value.get("risk"))
                    ),
                    patterns=tuple(_normalize_patterns(value.get("patterns"))),
                    order=order,
                )
            )
            order += 1
            continue

        for subject_pattern, subject_value in value.items():
            if isinstance(subject_value, str):
                rules.append(
                    PermissionConfigRule(
                        key=key,
                        action=subject_value,
                        subject_pattern=subject_pattern,
                        order=order,
                    )
                )
                order += 1
                continue

            rules.append(
                PermissionConfigRule(
                    key=key,
                    action=subject_value["action"],
                    reason=str(subject_value.get("reason") or ""),
                    risk=(
                        None
                        if subject_value.get("risk") is None
                        else str(subject_value.get("risk"))
                    ),
                    subject_pattern=subject_pattern,
                    order=order,
                )
            )
            order += 1
    return rules


def _validate_permission_action(value: Any, *, key: str) -> PermissionAction:
    if not isinstance(value, str):
        raise TypeError(f"tool_permissions[{key!r}] action must be a string")
    if value not in (ALLOW, ASK, DENY):
        raise ValueError(
            f"tool_permissions[{key!r}] action must be 'allow', 'ask', or 'deny'"
        )
    return value


_PERMISSION_CATEGORY_ALIASES: dict[str, frozenset[str]] = {
    "read": frozenset({"read"}),
    "edit": frozenset({"write", "edit", "apply_patch"}),
    "glob": frozenset({"glob"}),
    "grep": frozenset({"grep"}),
    "bash": frozenset({"bash"}),
    "task": frozenset({"task"}),
    "todowrite": frozenset({"todowrite"}),
    "webfetch": frozenset({"webfetch"}),
    "websearch": frozenset({"websearch", "web_search"}),
    "lsp": frozenset({"lsp"}),
    "skill": frozenset({"skill"}),
    "question": frozenset({"question"}),
    "doom_loop": frozenset(),
}


_PERMISSION_ALIAS_METADATA_CATEGORIES: dict[str, frozenset[str]] = {
    "bash": frozenset({"shell"}),
    "task": frozenset({"task"}),
    "lsp": frozenset({"lsp"}),
    "skill": frozenset({"skill"}),
    "question": frozenset({"question"}),
    "doom_loop": frozenset({"doom_loop"}),
}


def _is_permission_wildcard(key: str) -> bool:
    return any(char in key for char in "*?[")


def _permission_category_matches(
    key: str,
    *,
    tool_id: str,
    category: str,
) -> bool:
    if category and key == category:
        return True
    alias_tool_ids = _PERMISSION_CATEGORY_ALIASES.get(key)
    if alias_tool_ids is not None and tool_id in alias_tool_ids:
        return True
    alias_categories = _PERMISSION_ALIAS_METADATA_CATEGORIES.get(key)
    if alias_categories is not None and category in alias_categories:
        return True
    return False


def _metadata_from_permission_config(
    metadata: PermissionMetadata,
    match: PermissionConfigMatch,
    *,
    source: str | None = None,
    deny_reason_label: str = "runtime config",
) -> PermissionMetadata:
    rule = match.rule
    data = dict(metadata.data)
    data["permission_config_key"] = rule.key
    data["permission_config_match"] = match.match_type
    if source:
        data["permission_config_source"] = source
    if rule.patterns:
        data["patterns"] = list(rule.patterns)
    if rule.subject_pattern is not None:
        data["permission_config_subject_pattern"] = rule.subject_pattern
        if match.subject is not None:
            data["permission_subject"] = match.subject

    if rule.action == DENY:
        reason = rule.reason or f"Permission denied by {deny_reason_label}: {rule.key}"
    else:
        reason = rule.reason or metadata.reason

    return PermissionMetadata(
        action=rule.action,
        reason=reason,
        category=metadata.category,
        resource=metadata.resource,
        risk=rule.risk or metadata.risk,
        data=data,
    )


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
    tool_id: str,
    args: Mapping[str, Any],
    metadata: PermissionMetadata,
    context: Any,
) -> list[str]:
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
    if (
        not patterns
        and metadata.data.get("permission_config_subject_pattern") is not None
    ):
        subject = _permission_subject(args, metadata)
        if subject:
            patterns = [subject]
    if not patterns and _is_shell_permission(tool_id, metadata):
        patterns = shell_permission_patterns(args)
    return patterns


def _args_request_metadata(
    tool_id: str,
    args: Mapping[str, Any],
    metadata: PermissionMetadata,
) -> dict[str, Any]:
    if _is_fetch_permission(tool_id, metadata):
        return _fetch_request_metadata(args)

    if not _is_shell_permission(tool_id, metadata):
        return {}

    request_metadata = shell_permission_metadata(args)
    explicit_patterns = _normalize_patterns(metadata.data.get("patterns"))
    if not explicit_patterns:
        explicit_patterns = _normalize_patterns(metadata.data.get("pattern"))
    if explicit_patterns:
        request_metadata["permission_patterns"] = explicit_patterns
    return request_metadata


def _is_shell_permission(tool_id: str, metadata: PermissionMetadata) -> bool:
    return tool_id == "bash" or metadata.category == "shell"


def _is_fetch_permission(tool_id: str, metadata: PermissionMetadata) -> bool:
    return tool_id == "webfetch" or (
        metadata.category == "network" and metadata.resource == "url"
    )


def _fetch_request_metadata(args: Mapping[str, Any]) -> dict[str, Any]:
    request_metadata: dict[str, Any] = {}

    url = _string_arg(args, "url")
    if url:
        request_metadata["url"] = url

    request_metadata["format"] = _string_arg(args, "format") or "markdown"

    timeout = args.get("timeout")
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
        request_metadata["timeout"] = min(float(timeout), 120.0)
    elif timeout is not None:
        request_metadata["timeout"] = str(timeout)

    return request_metadata


def _string_arg(args: Mapping[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    return str(value)


def _preview_text(value: str, *, max_chars: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return f"{text[: max_chars - 3]}..."


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


def _context_permission_overlay_match(
    context: Any,
    *,
    tool_id: str,
    args: Mapping[str, Any],
    metadata: PermissionMetadata,
) -> PermissionConfigMatch | None:
    context_metadata = _context_metadata(context)
    if (
        context_metadata.get(AGENT_PERMISSION_OVERLAY_SOURCE_KEY)
        != AGENT_PERMISSION_OVERLAY_SOURCE
    ):
        return None
    overlay = context_metadata.get(AGENT_PERMISSION_OVERLAY_METADATA_KEY)
    if overlay is None:
        return None
    return PermissionConfig(overlay).match(
        tool_id=tool_id,
        args=args,
        metadata=metadata,
    )


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
