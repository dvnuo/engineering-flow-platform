"""Thinking levels for Engineering Flow Platform Agent - Following OpenClaw's Thinking pattern."""

from enum import Enum
from typing import Optional


class ThinkLevel(str, Enum):
    """Thinking level enum matching OpenClaw's ThinkLevel."""
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class ReasoningLevel(str, Enum):
    """Reasoning visibility level."""
    OFF = "off"
    ON = "on"
    STREAM = "stream"


# Models that support xhigh thinking
XHIGH_MODEL_REFS = [
    "openai/gpt-5.2",
    "openai-codex/gpt-5.2-codex",
    "openai-codex/gpt-5.1-codex",
]


def normalize_think_level(raw: Optional[str]) -> Optional[ThinkLevel]:
    """Normalize user-provided thinking level strings to the canonical enum."""
    if not raw:
        return None
    
    key = raw.lower().strip()
    
    off_aliases = ["off", "disable", "disabled", "false", "no", "0"]
    if key in off_aliases:
        return ThinkLevel.OFF
    
    on_aliases = ["on", "enable", "enabled", "true", "yes", "1"]
    if key in on_aliases:
        return ThinkLevel.LOW
    
    minimal_aliases = ["min", "minimal"]
    if key in minimal_aliases:
        return ThinkLevel.MINIMAL
    
    low_aliases = ["low", "thinkhard", "think-hard", "think_hard"]
    if key in low_aliases:
        return ThinkLevel.LOW
    
    medium_aliases = ["mid", "med", "medium", "thinkharder", "think-harder", "harder"]
    if key in medium_aliases:
        return ThinkLevel.MEDIUM
    
    high_aliases = ["high", "ultra", "ultrathink", "think-hard", "thinkhardest", "highest", "max"]
    if key in high_aliases:
        return ThinkLevel.HIGH
    
    xhigh_aliases = ["xhigh", "x-high", "x_high"]
    if key in xhigh_aliases:
        return ThinkLevel.XHIGH
    
    # Default alias
    if key == "think":
        return ThinkLevel.MINIMAL
    
    return None


def supports_xhigh_thinking(provider: Optional[str] = None, model: Optional[str] = None) -> bool:
    """Check if the model supports xhigh thinking."""
    if not model:
        return False
    
    model_key = model.strip().lower()
    provider_key = provider.strip().lower() if provider else None
    
    # Check full provider/model reference
    for ref in XHIGH_MODEL_REFS:
        if provider_key:
            full_ref = f"{provider_key}/{model_key}"
            if full_ref == ref.lower():
                return True
        else:
            # Check just model id
            ref_model = ref.split("/")[-1].lower()
            if ref_model == model_key:
                return True
    
    return False


def list_thinking_levels(provider: Optional[str] = None, model: Optional[str] = None) -> list:
    """List available thinking levels for the given provider/model."""
    levels = [ThinkLevel.OFF, ThinkLevel.MINIMAL, ThinkLevel.LOW, ThinkLevel.MEDIUM, ThinkLevel.HIGH]
    
    if supports_xhigh_thinking(provider, model):
        levels.append(ThinkLevel.XHIGH)
    
    return levels


def format_thinking_levels(provider: Optional[str] = None, model: Optional[str] = None, separator: str = ", ") -> str:
    """Format thinking levels as a human-readable string."""
    return separator.join(list_thinking_levels(provider, model))


def format_runtime_info(
    host: str = "engineering-flow-platform",
    os_info: str = "",
    arch: str = "",
    node: str = "",
    model: str = "",
    default_model: str = "",
    channel: str = "",
    capabilities: list = None,
    think_level: ThinkLevel = ThinkLevel.OFF,
) -> str:
    """Format runtime info for system prompt."""
    if capabilities is None:
        capabilities = []
    
    parts = []
    
    if host:
        parts.append(f"host={host}")
    
    if os_info:
        arch_suffix = f" ({arch})" if arch else ""
        parts.append(f"os={os_info}{arch_suffix}")
    elif arch:
        parts.append(f"arch={arch}")
    
    if node:
        parts.append(f"node={node}")
    
    if model:
        parts.append(f"model={model}")
    
    if default_model:
        parts.append(f"default_model={default_model}")
    
    if channel:
        parts.append(f"channel={channel}")
    
    if channel and capabilities:
        parts.append(f"capabilities={','.join(capabilities) if capabilities else 'none'}")
    
    # Add thinking level
    parts.append(f"thinking={think_level.value}")
    
    return " | ".join(filter(None, parts))
