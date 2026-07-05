"""Event bus for real-time agent events.

In-memory event bus that allows WebSocket connections to receive
agent events in real-time.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Mapping

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

    def __init__(self, *, history_limit: int = 1000):
        self._listeners: List[_ListenerRecord] = []
        self._history: Deque[str] = deque(maxlen=max(1, int(history_limit)))
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
        event_data = _event_record(event_type, data, ts=time.time())
        event = _serialize_event_record(event_data)

        async with self._lock:
            self._history.append(event)
            listeners = list(self._listeners)

        for listener in listeners:
            if not self._event_matches_filters(event_type, data, listener.filters):
                continue
            self._offer(listener.queue, event)

    @staticmethod
    def _offer(queue: asyncio.Queue, event: str) -> None:
        """Enqueue without blocking; drop the oldest event when the viewer is not draining."""
        try:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        except Exception as e:
            logger.error(f"Error sending event to listener: {e}")

    def emit_sync(self, event_type: str, data: Dict[str, Any]):
        """Synchronous emit for use in callbacks.

        This is called from sync callbacks, so we need to be careful about the event loop.
        """
        logger.info(f"[EventBus] emit_sync: {event_type}")

        event_data = _event_record(event_type, data, ts=time.time())
        event = _serialize_event_record(event_data)

        listeners = list(self._listeners)
        self._history.append(event)

        for listener in listeners:
            if not self._event_matches_filters(event_type, data, listener.filters):
                continue
            self._offer(listener.queue, event)

    async def replay_events(
        self,
        *,
        filters: Dict[str, str] | None = None,
        replay_limit: int = 100,
        last_event_at: str | None = None,
    ) -> List[str]:
        """Return recent cached events matching the same filters used by live listeners."""

        normalized_filters = self._normalize_filters(filters)
        limit = _normalize_replay_limit(replay_limit)
        async with self._lock:
            history = list(self._history)

        matching: List[str] = []
        for event in history:
            parsed = _parse_event_record(event)
            if parsed is None:
                continue
            data = parsed.get("data")
            if not isinstance(data, dict):
                continue
            event_type = str(parsed.get("type") or "")
            if not self._event_matches_filters(event_type, data, normalized_filters):
                continue
            if not _event_is_after(parsed, last_event_at):
                continue
            matching.append(event)
        if limit <= 0:
            return []
        return matching[-limit:]


def _event_record(event_type: str, data: Mapping[str, Any], *, ts: float) -> dict[str, Any]:
    return {
        "type": event_type,
        "data": dict(data),
        "ts": ts,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _serialize_event_record(record: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(record), ensure_ascii=False, default=str)


def _parse_event_record(event: str) -> dict[str, Any] | None:
    import json

    try:
        parsed = json.loads(event)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_replay_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 100
    if parsed < 0:
        return 0
    return min(parsed, 500)


def _event_is_after(event: Mapping[str, Any], last_event_at: str | None) -> bool:
    if not last_event_at:
        return True
    marker = str(last_event_at).strip()
    if not marker:
        return True
    try:
        marker_float = float(marker)
    except ValueError:
        marker_float = None
    if marker_float is not None:
        try:
            return float(event.get("ts") or 0) > marker_float
        except (TypeError, ValueError):
            return True
    created_at = event.get("created_at")
    if not created_at:
        return True
    return str(created_at) > marker


# Global event bus instance
event_bus = EventBus()


async def emit_agent_event(event_type: str, data: Dict[str, Any]):
    """Emit an agent event to all connected clients."""
    await event_bus.emit(event_type, data)


def emit_agent_event_sync(event_type: str, data: Dict[str, Any]):
    """Synchronous version for use in callbacks."""
    event_bus.emit_sync(event_type, data)
