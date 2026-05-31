"""Provider contracts for the EFP runtime loop runner."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Awaitable, List, Protocol, Union

from ..llm.request import PreparedProviderRequest, ProviderRequest
from ..llm.events import LLMEvent
from ..session.models import Message
from ..tools.definition import ToolDef


ProviderOutput = Union[
    Mapping[str, Any],
    Iterable[LLMEvent],
    AsyncIterable[LLMEvent],
]
ProviderResult = Union[ProviderOutput, Awaitable[ProviderOutput]]


@dataclass(frozen=True)
class RuntimeRequest:
    """Structured provider input for one EFP runtime loop iteration."""

    session_id: str
    messages: List[Message]
    iteration: int
    max_iterations: int
    metadata: dict[str, Any] = field(default_factory=dict)
    provider_request: ProviderRequest = field(
        default_factory=lambda: ProviderRequest(messages=[])
    )
    prepared_request: PreparedProviderRequest = field(
        default_factory=lambda: PreparedProviderRequest(
            request=ProviderRequest(messages=[]),
        )
    )
    tools: List[ToolDef] = field(default_factory=list)


class LLMProvider(Protocol):
    """EFP runtime provider boundary.

    Implementations may return normalized LLM events directly or a
    non-streaming provider response accepted by DefaultLLMEventAdapter.
    """

    def invoke(self, request: RuntimeRequest) -> ProviderResult:
        ...


class ScriptedLLMProvider:
    """Deterministic provider for loop tests and local prototypes."""

    def __init__(self, responses: Iterable[ProviderOutput]):
        self._responses = list(responses)
        self.requests: List[RuntimeRequest] = []

    async def invoke(self, request: RuntimeRequest) -> ProviderOutput:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("ScriptedLLMProvider has no response left")
        return self._responses.pop(0)

    @property
    def remaining(self) -> int:
        return len(self._responses)


__all__ = [
    "LLMProvider",
    "ProviderOutput",
    "ProviderResult",
    "RuntimeRequest",
    "ScriptedLLMProvider",
]
