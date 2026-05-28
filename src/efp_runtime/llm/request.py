"""Provider-neutral request contracts for Runtime v2 LLM clients."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class RequestReasoning:
    """Reasoning content kept separate from model-visible chat text."""

    text: str
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class RequestContext:
    """Non-chat context that a provider adapter can project explicitly."""

    type: str
    text: str | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class RequestAttachment:
    """Attachment reference metadata for provider adapters."""

    attachment_id: str
    mime_type: str
    filename: str | None = None
    url: str | None = None
    text_ref: str | None = None
    metadata: JsonObject = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class RequestToolCall:
    """Structured tool call emitted by an assistant message."""

    call_id: str
    tool_name: str
    arguments: JsonObject = field(default_factory=dict)
    arguments_text: str = ""
    status: str = "pending"
    call_type: str = "function"
    raw: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class RequestToolResult:
    """Structured tool result paired to a previous tool call id."""

    call_id: str
    tool_name: str
    content: str = ""
    output: Any = None
    success: bool = True
    error: str | None = None
    status: str = "success"
    truncated: bool = False
    attachments: list[RequestAttachment] = field(default_factory=list)
    events: list[Any] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class RequestMessagePart:
    """One ordered part inside a provider-neutral request message."""

    type: str
    text: str | None = None
    reasoning: RequestReasoning | None = None
    tool_call: RequestToolCall | None = None
    tool_result: RequestToolResult | None = None
    context: RequestContext | None = None
    attachment: RequestAttachment | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class RequestMessage:
    """A rendered v2 message with ordered structured parts."""

    role: str
    parts: list[RequestMessagePart] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

    @property
    def content(self) -> list[RequestMessagePart]:
        return [part for part in self.parts if part.text is not None]

    @property
    def text(self) -> str:
        return "\n".join(part.text or "" for part in self.content)

    @property
    def reasoning(self) -> list[RequestReasoning]:
        return [part.reasoning for part in self.parts if part.reasoning is not None]

    @property
    def tool_calls(self) -> list[RequestToolCall]:
        return [part.tool_call for part in self.parts if part.tool_call is not None]

    @property
    def tool_results(self) -> list[RequestToolResult]:
        return [part.tool_result for part in self.parts if part.tool_result is not None]

    @property
    def context(self) -> list[RequestContext]:
        return [part.context for part in self.parts if part.context is not None]

    @property
    def attachments(self) -> list[RequestAttachment]:
        attachments = [part.attachment for part in self.parts if part.attachment is not None]
        for result in self.tool_results:
            attachments.extend(result.attachments)
        return attachments


@dataclass(frozen=True)
class RequestToolSchema:
    """Provider-neutral tool definition metadata."""

    id: str
    name: str
    description: str
    json_schema: JsonObject
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderRequest:
    """Rendered request payload ready for a provider-specific adapter."""

    messages: list[RequestMessage]
    tools: list[RequestToolSchema] = field(default_factory=list)
    metadata: JsonObject = field(default_factory=dict)

    def to_dict(self) -> JsonObject:
        return asdict(self)


@dataclass(frozen=True)
class PreparedProviderRequest:
    """Rendered request plus preparation metadata."""

    request: ProviderRequest
    compaction_applied: bool = False
    compaction_metadata: JsonObject = field(default_factory=dict)

