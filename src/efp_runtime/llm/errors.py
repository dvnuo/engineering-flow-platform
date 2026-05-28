"""Provider-neutral error types for Runtime v2 LLM boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional


class ProviderError(Exception):
    """Base provider error with retry metadata independent of provider SDKs."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.code = code
        self.metadata = dict(metadata or {})


class ProviderTransientError(ProviderError):
    """Retryable provider failure such as a temporary transport outage."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        code: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            code=code,
            metadata=metadata,
        )


class ProviderContextOverflowError(ProviderError):
    """Retryable provider failure indicating the request exceeded context limits."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        code: Optional[str] = "context_overflow",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            code=code,
            metadata=metadata,
        )


class ProviderFatalError(ProviderError):
    """Non-retryable provider failure."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            retryable=retryable,
            code=code,
            metadata=metadata,
        )


__all__ = [
    "ProviderContextOverflowError",
    "ProviderError",
    "ProviderFatalError",
    "ProviderTransientError",
]
