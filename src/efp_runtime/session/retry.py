"""Minimal retry hooks for the EFP runtime loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool = False
    delay_seconds: float = 0.0
    reason: Optional[str] = None


class RetryPolicy(Protocol):
    def decide(self, error: BaseException, attempt: int) -> RetryDecision:
        ...


class NoRetryPolicy:
    def decide(self, error: BaseException, attempt: int) -> RetryDecision:
        return RetryDecision(should_retry=False, reason=str(error))
