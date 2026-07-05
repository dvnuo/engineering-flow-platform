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
    # Server-side ping keeps idle event streams alive through intermediaries
    # with read timeouts (parity with the opencode adapter's event socket).
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    # Create a bounded queue for this connection; the bus drops the oldest
    # event when a viewer stops draining (e.g. half-open connection).
    queue = asyncio.Queue(maxsize=200)

    query = request.rel_url.query
    filter_keys = ("session_id", "task_id", "group_id", "coordination_run_id", "agent_id", "request_id")
    filters = {key: str(query.get(key, "")).strip() for key in filter_keys if str(query.get(key, "")).strip()}
    replay_requested = str(query.get("replay", "")).strip().lower() in {"1", "true", "yes", "on"}
    replay_limit = _parse_replay_limit(query.get("replay_limit"))
    last_event_at = str(query.get("last_event_at", "")).strip() or None

    # Register this connection as a listener
    await event_bus.add_listener(queue, filters=filters)

    logger.info(
        "WebSocket client connected. filters=%s total_listeners=%d",
        filters,
        len(event_bus._listeners),
    )
    
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
            "message": "Connected to EFP event bus",
            "filters": filters,
        }))

        if replay_requested:
            replayed_events = await event_bus.replay_events(
                filters=filters,
                replay_limit=replay_limit,
                last_event_at=last_event_at,
            )
            for event in replayed_events:
                await ws.send_str(event)
        
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


def _parse_replay_limit(value, default: int = 100) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0
    return min(parsed, 500)



def emit_gateway_event(event_type: str, data: dict) -> None:
    """Emit gateway-compatible events from non-websocket code paths."""
    try:
        from .event_bus import emit_agent_event_sync

        emit_agent_event_sync(event_type, data)
    except Exception as exc:
        logger.debug(f"emit_gateway_event failed for {event_type}: {exc}")


def emit_skill_runtime_event(event_type: str, data: dict) -> None:
    """Thin adapter for skill runtime/task events."""
    emit_gateway_event(event_type, data)
