"""Permission primitives for Runtime v2 tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .types import new_id


PermissionAction = str
ALLOW: PermissionAction = "allow"
DENY: PermissionAction = "deny"
ASK: PermissionAction = "ask"


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
class PermissionRequest:
    """Structured request emitted when a tool requires user approval."""

    id: str
    tool_id: str
    args: dict[str, Any]
    reason: str = ""
    metadata: PermissionMetadata = field(default_factory=PermissionMetadata)

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        reason: str = "",
    ) -> "PermissionRequest":
        return cls(
            id=new_id("perm"),
            tool_id=tool_id,
            args=dict(args),
            reason=reason or metadata.reason,
            metadata=metadata,
        )

    @property
    def request_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_id": self.tool_id,
            "args": dict(self.args),
            "reason": self.reason,
            "metadata": {
                "action": self.metadata.action,
                "reason": self.metadata.reason,
                "category": self.metadata.category,
                "resource": self.metadata.resource,
                "risk": self.metadata.risk,
                "data": dict(self.metadata.data),
            },
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
    """Default evaluator based only on a tool's static permission metadata."""

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
            )
            return PermissionDecision.ask(request)
        return PermissionDecision.deny(f"Unknown permission action: {action}")
