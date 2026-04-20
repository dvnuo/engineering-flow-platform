"""Deprecated runtime automation watchers compatibility shim.

Runtime-side automation polling has been removed. Automation monitoring now
lives in Portal (control plane), and runtime only executes dispatched tasks.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Runtime polling is permanently disabled."""
    return False


async def start_automation_watchers() -> None:
    """No-op compatibility entrypoint.

    Kept only so legacy imports do not crash at runtime.
    """
    logger.warning(
        "start_automation_watchers() is deprecated and now a no-op; "
        "automation polling moved to Portal"
    )
    return None


async def stop_automation_watchers(task: asyncio.Task | None = None) -> None:
    """No-op compatibility entrypoint."""
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return None
