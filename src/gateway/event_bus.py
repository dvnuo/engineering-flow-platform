"""Event bus for real-time agent events.

In-memory event bus that allows WebSocket connections to receive
agent events in real-time.
"""

import asyncio
import logging
from typing import Callable, Dict, List, Any

logger = logging.getLogger(__name__)


class EventBus:
    """Simple in-memory event bus for agent events."""
    
    def __init__(self):
        self._listeners: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()
    
    async def add_listener(self, queue: asyncio.Queue):
        """Add a listener (WebSocket connection) to receive events."""
        async with self._lock:
            self._listeners.append(queue)
            logger.info(f"Event listener added. Total listeners: {len(self._listeners)}")
    
    async def remove_listener(self, queue: asyncio.Queue):
        """Remove a listener."""
        async with self._lock:
            if queue in self._listeners:
                self._listeners.remove(queue)
                logger.info(f"Event listener removed. Total listeners: {len(self._listeners)}")
    
    async def emit(self, event_type: str, data: Dict[str, Any]):
        """Emit an event to all listeners."""
        import json
        event = json.dumps({
            "type": event_type,
            "data": data,
            "ts": asyncio.get_event_loop().time()
        })
        
        async with self._lock:
            listeners = list(self._listeners)
        
        for listener in listeners:
            try:
                listener.put_nowait(event)
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
        
        # Add directly to all listener queues - this works from sync context
        # We iterate over a copy of the list
        listeners = list(self._listeners)
        
        for listener in listeners:
            try:
                # Try non-blocking put
                listener.put_nowait(event)
                logger.info(f"[EventBus] Event sent to listener")
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
