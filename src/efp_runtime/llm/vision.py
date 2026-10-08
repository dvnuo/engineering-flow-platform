"""Which chat providers accept image content.

GitHub Copilot's models no longer accept image input on the enterprise plan,
so a Copilot chat turn must not inline attachments as image parts: the gateway
hands the files to the agent for the inspect-image CLI instead (see
``src/gateway/image_handoff.py``). AI Platform chat/completions is multimodal.
``EFP_COPILOT_VISION_VIA_CHAT=1`` flips Copilot back on the day vision returns.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

COPILOT_VISION_VIA_CHAT_ENV = "EFP_COPILOT_VISION_VIA_CHAT"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def normalize_provider_id(value: Any) -> str:
    """``ai_platform`` / ``github_copilot`` from any alias the profile may carry."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text == "ai_platform":
        return "ai_platform"
    if text in {"", "github_copilot", "github", "copilot"}:
        return "github_copilot"
    return text


def chat_provider_accepts_images(
    llm_config: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when the configured chat provider can take image parts in a request."""
    provider = normalize_provider_id((llm_config or {}).get("provider"))
    if provider == "ai_platform":
        return True
    env = os.environ if environ is None else environ
    return str(env.get(COPILOT_VISION_VIA_CHAT_ENV, "") or "").strip().lower() in _TRUE_VALUES
