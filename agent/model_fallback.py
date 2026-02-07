"""Model Fallback - Automatic Model Degradation

Automatically fallback to alternative models when the primary model fails.
Inspired by OpenClaw's model-fallback.ts.

Usage:
```python
from agent.model_fallback import with_model_fallback, FALLBACK_ORDER

result = await with_model_fallback(
    task=lambda: agent.process(message="..."),
    candidates=FALLBACK_ORDER
)
```
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type, Union

logger = logging.getLogger(__name__)


class FallbackError(Exception):
    """Error during fallback attempt."""
    
    def __init__(self, message: str, attempts: List[Dict] = None):
        super().__init__(message)
        self.attempts = attempts or []


class ModelCandidate:
    """Represents a model candidate for fallback."""
    
    def __init__(
        self,
        provider: str,
        model: str,
        priority: int = 0,
        weight: float = 1.0,
    ):
        self.provider = provider
        self.model = model
        self.priority = priority
        self.weight = weight
    
    def __repr__(self) -> str:
        return f"{self.provider}/{self.model}"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "priority": self.priority,
            "weight": self.weight,
        }


class FallbackAttempt:
    """Record of a fallback attempt."""
    
    def __init__(
        self,
        candidate: ModelCandidate,
        error: Optional[str] = None,
        reason: Optional[str] = None,
        success: bool = False,
        duration_ms: float = 0.0,
    ):
        self.candidate = candidate
        self.error = error
        self.reason = reason
        self.success = success
        self.duration_ms = duration_ms
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.candidate.provider,
            "model": self.candidate.model,
            "error": self.error,
            "reason": self.reason,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }


# Errors that should NOT trigger fallback
SKIP_FALLBACK_ERRORS = [
    "authentication",
    "rate_limit",  # Rate limits are provider-specific
    "quota_exceeded",
    "invalid_request",
    "context_length_exceeded",  # This won't be fixed by changing models
    "permission_denied",
    "invalid_api_key",
]

# Errors that SHOULD trigger fallback
FALLBACK_ERRORS = [
    "connection",
    "timeout",
    "service_unavailable",
    "server_error",
    "model_overloaded",
    "unknown",
]


def _contains_word(text: str, word: str) -> bool:
    """Check if text contains word as a whole word."""
    import re
    return bool(re.search(r'\b' + re.escape(word) + r'\b', text.lower()))


def classify_fallback_error(error: Exception) -> str:
    """Classify an error to determine if fallback should occur.
    
    Args:
        error: The exception that occurred
        
    Returns:
        Classification string for the error
    """
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()
    
    # Check for errors that should skip fallback (use word boundaries)
    skip_words = ["authentication", "rate limit", "quota exceeded", 
                  "invalid request", "context length", "permission denied",
                  "invalid api key"]
    
    for word in skip_words:
        if word in error_str:
            return "skip"
    
    # Check for errors that should trigger fallback
    fallback_words = ["connection refused", "timed out", "timeout", "service unavailable",
                      "server error", "model overloaded", "unknown"]
    
    for word in fallback_words:
        if word in error_str:
            return "fallback"
    
    # Default: don't fallback for unknown errors
    return "skip"


def should_skip_fallback(reason: str) -> bool:
    """Check if fallback should be skipped for this error reason.
    
    Args:
        reason: Error classification reason
        
    Returns:
        True if fallback should be skipped
    """
    return reason == "skip"


async def with_model_fallback(
    task: Callable[[], Awaitable[Any]],
    candidates: List[ModelCandidate],
    max_retries: int = 3,
) -> Any:
    """Execute a task with automatic model fallback.
    
    Args:
        task: Async function to execute
        candidates: List of model candidates in fallback order
        max_retries: Maximum retries per candidate
        
    Returns:
        Result of the successful task
        
    Raises:
        FallbackError: All models failed
    """
    if not candidates:
        return await task()
    
    attempts: List[FallbackAttempt] = []
    
    for candidate in candidates:
        logger.info(f"Trying model: {candidate.provider}/{candidate.model}")
        
        last_error = None
        for attempt in range(max_retries):
            try:
                result = await asyncio.wait_for(
                    task(),
                    timeout=60.0 * (attempt + 1)  # Progressive timeout
                )
                logger.info(f"Success: {candidate.provider}/{candidate.model}")
                return result
                
            except asyncio.TimeoutError as e:
                last_error = str(e)
                logger.warning(
                    f"Timeout {candidate.provider}/{candidate.model} "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                
            except Exception as e:
                last_error = str(e)
                error_type = classify_fallback_error(e)
                
                if should_skip_fallback(error_type):
                    logger.warning(
                        f"Skipping fallback for {candidate.provider}/{candidate.model}: {e}"
                    )
                    break  # Don't retry, skip to next candidate
                
                logger.warning(
                    f"Error {candidate.provider}/{candidate.model} "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                
                # Wait before retry
                await asyncio.sleep(1.0 * (attempt + 1))
        
        # Record this candidate as failed
        reason = classify_fallback_error(Exception(last_error)) if last_error else "unknown"
        attempts.append(FallbackAttempt(
            candidate=candidate,
            error=last_error,
            reason=reason,
            success=False,
        ))
    
    # All candidates failed
    error_messages = [
        f"{a.candidate.provider}/{a.candidate.model}: {a.error or 'unknown error'}"
        for a in attempts
    ]
    
    logger.error(f"All models failed: {error_messages}")
    
    raise FallbackError(
        f"All {len(candidates)} models failed",
        attempts=[a.to_dict() for a in attempts]
    )


# Predefined fallback order for common scenarios
FALLBACK_ORDER: List[ModelCandidate] = [
    ModelCandidate(provider="openai", model="gpt-4o", priority=0, weight=1.0),
    ModelCandidate(provider="openai", model="gpt-4o-mini", priority=1, weight=0.5),
    ModelCandidate(provider="anthropic", model="claude-sonnet-4", priority=2, weight=0.8),
    ModelCandidate(provider="anthropic", model="claude-haiku-3-5", priority=3, weight=0.4),
]

# Fast fallback (less aggressive)
FAST_FALLBACK: List[ModelCandidate] = [
    ModelCandidate(provider="openai", model="gpt-4o", priority=0, weight=1.0),
    ModelCandidate(provider="openai", model="gpt-4o-mini", priority=1, weight=0.5),
]

# Budget fallback (prioritize cheaper models)
BUDGET_FALLBACK: List[ModelCandidate] = [
    ModelCandidate(provider="openai", model="gpt-4o-mini", priority=0, weight=0.5),
    ModelCandidate(provider="anthropic", model="claude-haiku-3-5", priority=1, weight=0.4),
    ModelCandidate(provider="ollama", model="llama3", priority=2, weight=0.3),
]

# Local fallback (use local models when possible)
LOCAL_FALLBACK: List[ModelCandidate] = [
    ModelCandidate(provider="ollama", model="llama3", priority=0, weight=0.3),
    ModelCandidate(provider="ollama", model="mistral", priority=1, weight=0.3),
    ModelCandidate(provider="openai", model="gpt-4o-mini", priority=2, weight=0.5),
]


def get_fallback_order(name: str = "default") -> List[ModelCandidate]:
    """Get predefined fallback order by name.
    
    Args:
        name: Fallback order name (default, fast, budget, local)
        
    Returns:
        List of ModelCandidates
    """
    orders = {
        "default": FALLBACK_ORDER,
        "fast": FAST_FALLBACK,
        "budget": BUDGET_FALLBACK,
        "local": LOCAL_FALLBACK,
    }
    return orders.get(name, FALLBACK_ORDER)


# Convenience function for common use case
async def chat_with_fallback(
    chat_func: Callable[[str, str], Awaitable[Any]],
    message: str,
    fallback_order: List[ModelCandidate] = None,
) -> Any:
    """Chat with automatic fallback.
    
    Args:
        chat_func: Async function taking (provider, model) and returning response
        message: Message to send
        fallback_order: Custom fallback order (uses FALLBACK_ORDER if None)
        
    Returns:
        Chat response
    """
    candidates = fallback_order or FALLBACK_ORDER
    
    async def task():
        # Try the first candidate
        first = candidates[0]
        return await chat_func(first.provider, first.model)
    
    return await with_model_fallback(task=task, candidates=candidates[1:])


# Export convenience
__all__ = [
    "FallbackError",
    "ModelCandidate",
    "FallbackAttempt",
    "with_model_fallback",
    "classify_fallback_error",
    "should_skip_fallback",
    "FALLBACK_ORDER",
    "FAST_FALLBACK",
    "BUDGET_FALLBACK",
    "LOCAL_FALLBACK",
    "get_fallback_order",
    "chat_with_fallback",
]
