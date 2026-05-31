"""In-process event bus for EFP runtime sessions."""

from __future__ import annotations

import asyncio
from threading import RLock
from typing import List, Optional

from .events import RuntimeEvent


_CLOSED = object()


class RuntimeEventSubscription:
    """Async subscription for RuntimeEventBus realtime events."""

    def __init__(self, bus: "RuntimeEventBus", session_id: Optional[str] = None):
        self._bus = bus
        self._session_id = session_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def __aiter__(self) -> "RuntimeEventSubscription":
        return self

    async def __anext__(self) -> RuntimeEvent:
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item

    async def get(self) -> RuntimeEvent:
        """Return the next event for consumers that prefer queue-style access."""

        return await self.__anext__()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unsubscribe(self)
        self._queue.put_nowait(_CLOSED)

    def _publish(self, event: RuntimeEvent) -> None:
        if self._closed:
            return
        if self._session_id is not None and event.session_id != self._session_id:
            return
        self._queue.put_nowait(event)


class RuntimeEventBus:
    """Synchronous RuntimeEvent publisher with replayable history."""

    def __init__(self) -> None:
        self._history: List[RuntimeEvent] = []
        self._subscribers: List[RuntimeEventSubscription] = []
        self._lock = RLock()

    def publish(self, event: RuntimeEvent) -> RuntimeEvent:
        """Record an event and push it to current subscribers."""

        with self._lock:
            self._history.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber._publish(event)
        return event

    def subscribe(self, session_id: Optional[str] = None) -> RuntimeEventSubscription:
        """Subscribe to future events, optionally scoped to one session."""

        subscription = RuntimeEventSubscription(self, session_id=session_id)
        with self._lock:
            self._subscribers.append(subscription)
        return subscription

    def history(self, session_id: Optional[str] = None) -> List[RuntimeEvent]:
        """Return events published so far, optionally filtered by session."""

        with self._lock:
            if session_id is None:
                return list(self._history)
            return [event for event in self._history if event.session_id == session_id]

    def _unsubscribe(self, subscription: RuntimeEventSubscription) -> None:
        with self._lock:
            if subscription in self._subscribers:
                self._subscribers.remove(subscription)


__all__ = ["RuntimeEventBus", "RuntimeEventSubscription"]
