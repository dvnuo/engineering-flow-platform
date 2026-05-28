"""LSP protocol boundary for Runtime v2 code navigation tools."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol


LSP_OPERATIONS = (
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
)


@dataclass(frozen=True)
class LSPPosition:
    """A 1-based source position accepted at the Runtime v2 tool boundary."""

    file_path: str
    line: int
    character: int


@dataclass(frozen=True)
class LSPRequest:
    """Provider-neutral request sent from the Runtime v2 lsp tool to a client."""

    operation: str
    file_path: str | None = None
    position: LSPPosition | None = None
    query: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class LSPClient(Protocol):
    """Injectable Runtime v2 LSP client boundary.

    Implementations may also expose ``is_available(file_path=None)``. The
    helper below treats clients without that method as available.
    """

    async def execute(self, request: LSPRequest) -> Any:
        ...

    def is_available(self, file_path: str | None = None) -> bool | Awaitable[bool]:
        ...


async def is_lsp_client_available(
    client: LSPClient | None,
    file_path: str | None,
) -> bool:
    """Return whether an injected LSP client can serve a file.

    ``is_available`` is intentionally optional at runtime so simple adapters only
    need to implement ``execute``.
    """

    if client is None:
        return False

    checker = getattr(client, "is_available", None)
    if checker is None:
        return True

    result = checker(file_path) if callable(checker) else checker
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


__all__ = [
    "LSPClient",
    "LSPPosition",
    "LSPRequest",
    "LSP_OPERATIONS",
    "is_lsp_client_available",
]
