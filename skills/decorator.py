"""Skill decorator and utilities for Engineering Flow Platform."""

from typing import Any, Dict, Optional


class SkillResult:
    """Result from skill execution."""

    def __init__(
        self,
        success: bool,
        output: str = "",
        error: Optional[str] = None,
        data: Optional[Dict] = None,
    ):
        self.success = success
        self.output = output
        self.error = error
        self.data = data or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "data": self.data,
        }

    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"Error: {self.error}"


def skill(name: str, description: str = ""):
    """Decorator to register a skill function."""
    def decorator(func):
        func.name = name
        func.description = description
        return func
    return decorator
