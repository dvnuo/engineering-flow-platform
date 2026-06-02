"""Portal runtime-profile bootstrap client."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from aiohttp import ClientSession

from src.config import Config, config
from src.utils.portal_internal_api import (
    build_portal_internal_api_headers,
    get_portal_agent_id,
    get_portal_internal_base_url,
)

logger = logging.getLogger(__name__)


def _warn_if_external_config_failed() -> None:
    status = config.get_external_config_status()
    if not status.get("success"):
        logger.warning(
            "Runtime profile external CLI config sync failed during portal bootstrap: operation=%s error=%s",
            status.get("operation"),
            status.get("error"),
        )


def _extract_runtime_profile_overlay(
    payload: Dict[str, Any],
) -> Tuple[Optional[str], Optional[int], Optional[Dict[str, Any]], bool]:
    """Extract managed runtime-profile snapshot fields from portal response.

    Expected structured Portal payload:
    {
      "runtime_profile_id": "...",
      "runtime_profile_context": {
        "runtime_profile_id": "...",
        "revision": 3,
        "config": {...},
        "...": "portal control-plane metadata (ignored by runtime)"
      }
    }

    Runtime only consumes ``runtime_profile_context.config`` (managed config body)
    and ``runtime_profile_context.revision``. Extra Portal-side ownership /
    default metadata (for example ``owner_user_id`` or ``is_default``)
    remains control-plane-only and is ignored here.

    Returns: (runtime_profile_id, revision, managed_config, clear_flag)
    """
    runtime_profile_id = payload.get("runtime_profile_id")
    runtime_profile_context = payload.get("runtime_profile_context")

    if runtime_profile_context is None:
        return runtime_profile_id, None, None, True

    if not isinstance(runtime_profile_context, dict):
        return None, None, None, False

    context_profile_id = runtime_profile_context.get("runtime_profile_id")
    if runtime_profile_id in (None, "") and context_profile_id not in (None, ""):
        runtime_profile_id = context_profile_id

    # Preferred structured response: runtime_profile_context.config
    context_config = runtime_profile_context.get("config")
    if isinstance(context_config, dict):
        revision = runtime_profile_context.get("revision")
        if revision is None:
            revision = payload.get("revision")
        return runtime_profile_id, revision, context_config, False

    # Legacy direct-config compatibility: runtime_profile_context is itself config-like
    legacy_keys = set(runtime_profile_context.keys())
    if legacy_keys & Config.MANAGED_OVERLAY_SECTIONS:
        revision = payload.get("revision")
        if revision is None:
            revision = runtime_profile_context.get("revision")
        return runtime_profile_id, revision, runtime_profile_context, False

    return None, None, None, False


async def bootstrap_runtime_profile_from_portal() -> bool:
    """Best-effort bootstrap for managed runtime-profile apply from Portal internal API."""
    base_url = get_portal_internal_base_url()
    agent_id = get_portal_agent_id()
    if not base_url or not agent_id:
        logger.debug(
            "Runtime profile bootstrap skipped: missing portal config (base_url=%s, agent_id=%s)",
            bool(base_url),
            bool(agent_id),
        )
        return False

    url = f"{base_url}/api/internal/agents/{agent_id}/runtime-context"
    headers = build_portal_internal_api_headers(include_content_type=False)
    try:
        async with ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.warning(
                        "Runtime profile bootstrap failed: status=%s agent_id=%s body=%s",
                        response.status,
                        agent_id,
                        body[:500],
                    )
                    return False
                payload = await response.json(content_type=None)
    except Exception:
        logger.warning("Runtime profile bootstrap request failed for agent_id=%s", agent_id, exc_info=True)
        return False

    if not isinstance(payload, dict):
        logger.warning("Runtime profile bootstrap ignored non-object response for agent_id=%s", agent_id)
        return False

    runtime_profile_id, revision, overlay_config, clear_flag = _extract_runtime_profile_overlay(payload)
    if clear_flag:
        try:
            config.clear_managed_overlay()
        except Exception:
            logger.warning(
                "Runtime profile bootstrap failed to clear profile config: agent_id=%s profile_id=%s",
                agent_id,
                runtime_profile_id,
                exc_info=True,
            )
            return False
        _warn_if_external_config_failed()
        logger.info(
            "Runtime profile config cleared from portal bootstrap: agent_id=%s profile_id=%s",
            agent_id,
            runtime_profile_id,
        )
        return True

    if isinstance(overlay_config, dict):
        try:
            updated_sections = config.set_managed_overlay(runtime_profile_id, revision, overlay_config)
        except Exception:
            logger.warning(
                "Runtime profile bootstrap failed to apply profile config: agent_id=%s profile_id=%s revision=%s",
                agent_id,
                runtime_profile_id,
                revision,
                exc_info=True,
            )
            return False
        _warn_if_external_config_failed()
        logger.info(
            "Runtime profile config applied from portal bootstrap: agent_id=%s profile_id=%s revision=%s sections=%s",
            agent_id,
            runtime_profile_id,
            revision,
            updated_sections,
        )
        return True

    logger.warning(
        "Runtime profile bootstrap ignored malformed payload for agent_id=%s payload_keys=%s",
        agent_id,
        sorted(payload.keys()),
    )
    return False


def bootstrap_runtime_profile_sync() -> bool:
    """Sync wrapper for startup path (best effort)."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bootstrap_runtime_profile_from_portal())
    logger.warning("Runtime profile bootstrap skipped in running event loop during sync init")
    return False
