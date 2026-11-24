"""Whether attached images are sent to the chat model as image content.

Every chat model is treated as having no vision, whichever provider the
profile chats on: by default a chat turn never inlines attachments as image
parts. The gateway lists the images in the "Attached files" block instead
(see ``src/gateway/attachment_handoff.py``) and the agent reads them with the
inspect-image CLI when the Image analysis connector is configured, or through
the attached-files analysis skill when it is not.

``EFP_CHAT_VISION`` is the deployment switch that turns inlining on, for any
provider: an operator sets it (the Portal writes it into every agent pod from
its own setting of the same name) when the chat models of the deployment do
accept images. ``EFP_COPILOT_VISION_VIA_CHAT`` is the older name of the same
switch and is still read. There is no member-facing control.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

CHAT_VISION_ENV = "EFP_CHAT_VISION"
# The switch's first name, from when only Copilot had lost vision. A synonym.
COPILOT_VISION_VIA_CHAT_ENV = "EFP_COPILOT_VISION_VIA_CHAT"
CHAT_VISION_ENV_VARS = (CHAT_VISION_ENV, COPILOT_VISION_VIA_CHAT_ENV)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def chat_vision_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True when the deployment switch is on under either of its names."""
    env = os.environ if environ is None else environ
    return any(str(env.get(name, "") or "").strip().lower() in _TRUE_VALUES for name in CHAT_VISION_ENV_VARS)


def chat_provider_accepts_images(
    llm_config: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when a chat request may carry image parts.

    The provider in ``llm_config`` no longer decides (the argument stays for
    the callers): only the deployment switch does, see ``chat_vision_enabled``.
    """
    return chat_vision_enabled(environ)
