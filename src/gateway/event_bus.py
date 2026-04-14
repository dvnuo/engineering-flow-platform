"""Event bus for real-time agent events.

In-memory event bus that allows WebSocket connections to receive
agent events in real-time.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_SUPPORTED_FILTER_KEYS = {
    "agent_id",
    "session_id",
    "task_id",
    "group_id",
    "coordination_run_id",
    "request_id",
}


@dataclass
class _ListenerRecord:
    queue: asyncio.Queue
    filters: Dict[str, str]


class EventBus:
    """Simple in-memory event bus for agent events."""

    def __init__(self):
        self._listeners: List[_ListenerRecord] = []
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize_filters(filters: Dict[str, str] | None = None) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        for key, value in (filters or {}).items():
            if key not in _SUPPORTED_FILTER_KEYS:
                continue
            normalized_value = str(value).strip() if value is not None else ""
            if normalized_value:
                normalized[key] = normalized_value
        return normalized

    @staticmethod
    def _event_matches_filters(event_type: str, data: Dict[str, Any], filters: Dict[str, str]) -> bool:
        del event_type  # reserved for future event-type specific matching
        if not filters:
            return True

        for key, expected_value in filters.items():
            if key == "agent_id":
                candidates = [data.get("agent_id")]
            elif key == "session_id":
                candidates = [data.get("session_id")]
            elif key == "task_id":
                candidates = [data.get("task_id"), data.get("current_task_id"), data.get("portal_task_id")]
            elif key == "group_id":
                candidates = [data.get("group_id"), data.get("portal_group_id")]
            elif key == "coordination_run_id":
                candidates = [
                    data.get("coordination_run_id"),
                    data.get("current_coordination_run_id"),
                    data.get("portal_coordination_run_id"),
                ]
            elif key == "request_id":
                candidates = [data.get("request_id"), data.get("execution_id")]
            else:
                return False

            comparable_candidates = [str(candidate).strip() for candidate in candidates if candidate is not None and str(candidate).strip()]
            if not comparable_candidates:
                return False
            if expected_value not in comparable_candidates:
                return False

        return True

    async def add_listener(self, queue: asyncio.Queue, filters: Dict[str, str] | None = None):
        """Add a listener (WebSocket connection) to receive events."""
        listener_record = _ListenerRecord(queue=queue, filters=self._normalize_filters(filters))
        async with self._lock:
            self._listeners.append(listener_record)
            logger.info("Event listener added. Total listeners: %d", len(self._listeners))

    async def remove_listener(self, queue: asyncio.Queue):
        """Remove a listener."""
        async with self._lock:
            self._listeners = [listener for listener in self._listeners if listener.queue is not queue]
            logger.info("Event listener removed. Total listeners: %d", len(self._listeners))

    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event to matching listeners."""
        import json

        event = json.dumps({
            "type": event_type,
            "data": data,
            "ts": asyncio.get_event_loop().time()
        })

        async with self._lock:
            listeners = list(self._listeners)

        for listener in listeners:
            if not self._event_matches_filters(event_type, data, listener.filters):
                continue
            try:
                listener.queue.put_nowait(event)
            except Exception as e:
                logger.error(f"Error sending event to listener: {e}")

    def emit_sync(self, event_type: str, data: Dict[str, Any]):
        """Synchronous emit for use in callbacks.

        This is called from sync callbacks, so we need to be careful about the event loop.
        """
        import json
        logger.info(f"[EventBus] emit_sync: {event_type}")

        event = json.dumps({
            "type": event_type,
            "data": data,
            "ts": 0
        })

        listeners = list(self._listeners)

        for listener in listeners:
            if not self._event_matches_filters(event_type, data, listener.filters):
                continue
            try:
                listener.queue.put_nowait(event)
                logger.info("[EventBus] Event sent to listener")
            except Exception as e:
                logger.error(f"[EventBus] Error: {e}")


# Global event bus instance
event_bus = EventBus()


async def emit_agent_event(event_type: str, data: Dict[str, Any]):
    """Emit an agent event to all connected clients."""
    await event_bus.emit(event_type, data)


def emit_agent_event_sync(event_type: str, data: Dict[str, Any]):
    """Synchronous version for use in callbacks."""
    event_bus.emit_sync(event_type, data)
