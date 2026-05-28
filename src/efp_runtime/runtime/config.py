"""Configuration for the Runtime v2 high-level facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RuntimeConfig:
    """Static settings for an AgentRuntime instance."""

    workspace_root: str | Path | None = None
    max_iterations: int = 4
    max_context_parts: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if self.max_context_parts is not None and self.max_context_parts < 1:
            raise ValueError("max_context_parts must be at least 1")
        self.metadata = dict(self.metadata)


__all__ = ["RuntimeConfig"]
