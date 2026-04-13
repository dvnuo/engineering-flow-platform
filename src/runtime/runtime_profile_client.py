"""Portal runtime-profile bootstrap client."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from aiohttp import ClientSession

from src.config import config
from src.utils.internal_api_keys import (
    build_portal_internal_api_headers,
    get_portal_agent_id,
    get_portal_internal_base_url,
)

logger = logging.getLogger(__name__)


async def bootstrap_runtime_profile_from_portal() -> bool:
    """Best-effort bootstrap for runtime profile overlay from Portal internal API."""
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

    runtime_profile_id = payload.get("runtime_profile_id")
    revision = payload.get("revision")
    runtime_profile_context = payload.get("runtime_profile_context")

    if isinstance(runtime_profile_context, dict) and runtime_profile_context:
        updated_sections = config.set_managed_overlay(runtime_profile_id, revision, runtime_profile_context)
        logger.info(
            "Runtime profile overlay applied from portal bootstrap: agent_id=%s profile_id=%s revision=%s sections=%s",
            agent_id,
            runtime_profile_id,
            revision,
            updated_sections,
        )
        return True

    config.clear_managed_overlay()
    logger.info(
        "Runtime profile overlay cleared from portal bootstrap: agent_id=%s profile_id=%s",
        agent_id,
        runtime_profile_id,
    )
    return True


def bootstrap_runtime_profile_sync() -> bool:
    """Sync wrapper for startup path (best effort)."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bootstrap_runtime_profile_from_portal())
    logger.warning("Runtime profile bootstrap skipped in running event loop during sync init")
    return False
