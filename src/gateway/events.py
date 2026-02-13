"""WebSocket endpoint for real-time agent events."""

import asyncio
import json
import logging
from aiohttp import web
from aiohttp.web import WebSocketResponse

from .event_bus import event_bus

logger = logging.getLogger(__name__)


async def handle_websocket(request: web.Request) -> WebSocketResponse:
    """WebSocket endpoint for real-time events.
    
    GET /api/events
    Connects to the event bus and streams agent events to the client.
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    # Create a queue for this connection
    queue = asyncio.Queue()
    
    # Register this connection as a listener
    await event_bus.add_listener(queue)
    
    logger.info(f"WebSocket client connected. Total listeners: {len(event_bus._listeners)}")
    
    async def read_events():
        """Background task to read from queue and send to WebSocket."""
        while not ws.closed:
            try:
                # Wait for event from queue with timeout
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                await ws.send_str(event)
            except asyncio.TimeoutError:
                # No event, continue checking
                continue
            except Exception as e:
                logger.error(f"Error reading from queue: {e}")
                break
    
    try:
        # Send welcome message
        await ws.send_str(json.dumps({
            "type": "connected",
            "message": "Connected to EFP event bus"
        }))
        
        # Start background task to read events from queue
        event_reader = asyncio.create_task(read_events())
        
        # Also handle incoming messages from client
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    # Handle client messages if needed
                    if data.get("type") == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))
                except Exception as e:
                    logger.error(f"Error parsing WebSocket message: {e}")
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
                break
        
        # Cancel event reader
        event_reader.cancel()
        
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    
    finally:
        # Unregister this connection
        await event_bus.remove_listener(queue)
        logger.info(f"WebSocket client disconnected. Total listeners: {len(event_bus._listeners)}")
    
    return ws


def setup_event_routes(app: web.Application):
    """Set up event routes."""
    app.router.add_get('/api/events', handle_websocket)
    logger.info("WebSocket event route registered: GET /api/events")
