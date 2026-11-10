"""WebSocket endpoint for real-time agent events."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web
from aiohttp.web import WebSocketResponse

from .chat_run_registry import chat_run_registry
from .event_bus import event_bus

logger = logging.getLogger(__name__)


class _RunAttach:
    """One socket following one chat run from a sequence number on.

    The run's journal supplies what the viewer missed before it connected and
    whatever its queue dropped afterwards; a message off the bus only has to
    say that there is something new. So the viewer gets every event of the
    run once and in order, or is told from where on it does (``resync``).
    """

    def __init__(self, request_id: str, session_id: str, after_seq: int) -> None:
        self.request_id = request_id
        self.session_id = session_id
        self.after_seq = after_seq
        # The highest sequence number this socket has been sent.
        self.last_sent_seq = after_seq

    def _journal(self):
        return chat_run_registry.journal_for(self.request_id, session_id=self.session_id or None)

    def snapshot(self) -> Tuple[Dict[str, Any], List[str]]:
        """What the viewer is told on connecting, and the events it is behind by.

        ``found`` is false while the run has not started -- a page opens its
        socket as it sends the message -- and once the journal is gone.
        """
        record = chat_run_registry.get(self.request_id, session_id=self.session_id or None)
        journal = self._journal()
        described: Dict[str, Any] = {
            "request_id": self.request_id,
            "found": journal is not None,
            "state": record.state if record is not None else "",
            "after_seq": self.after_seq,
        }
        if journal is None:
            return described, []
        described["first_seq"] = journal.first_seq
        described["last_seq"] = journal.last_seq
        return described, self._catch_up(journal)

    def relay(self, serialized: str) -> List[str]:
        """What to send for one message off the bus."""
        seq = _run_seq_of(serialized, self.request_id)
        if not seq:
            return [serialized]
        if seq <= self.last_sent_seq:
            # Already sent from the journal.
            return []
        if seq == self.last_sent_seq + 1:
            self.last_sent_seq = seq
            return [serialized]
        # Events went missing between the run and this socket: its queue drops
        # the oldest when the viewer is slow. The journal still has them.
        journal = self._journal()
        if journal is None:
            self.last_sent_seq = seq
            return [_resync_message(self.request_id, "journal_unavailable", seq), serialized]
        return self._catch_up(journal)

    def _catch_up(self, journal) -> List[str]:
        messages: List[str] = []
        if not journal.can_serve_after(self.last_sent_seq):
            # The journal dropped its oldest records to stay under its cap.
            messages.append(_resync_message(self.request_id, "journal_truncated", journal.first_seq))
        messages.extend(
            _journal_message(event, ts) for event, ts in journal.records_after(self.last_sent_seq)
        )
        self.last_sent_seq = max(self.last_sent_seq, journal.last_seq)
        return messages


def _journal_message(event: Dict[str, Any], ts: float) -> str:
    """A journal record in the shape the bus sends an event in."""
    return json.dumps(
        {
            "type": str(event.get("type") or event.get("event_type") or "runtime_event"),
            "data": event,
            "ts": ts,
            "created_at": datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        ensure_ascii=False,
        default=str,
    )


def _resync_message(request_id: str, reason: str, first_seq: int) -> str:
    """Tells the viewer that the run's events before ``first_seq`` are not coming."""
    return json.dumps(
        {
            "type": "resync",
            "data": {"request_id": request_id, "reason": reason, "first_seq": first_seq},
        }
    )


def _run_seq_of(serialized: str, request_id: str) -> int:
    """The sequence number a bus message carries for this run; 0 when it has none."""
    if '"run_seq"' not in serialized:
        return 0
    try:
        record = json.loads(serialized)
    except (TypeError, ValueError):
        return 0
    data = record.get("data") if isinstance(record, dict) else None
    if not isinstance(data, dict) or str(data.get("request_id") or "") != request_id:
        return 0
    try:
        return max(0, int(data.get("run_seq") or 0))
    except (TypeError, ValueError):
        return 0


def _parse_after_seq(value) -> Optional[int]:
    """The sequence number a viewer says it has; None when it sent none."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        return max(0, int(text))
    except ValueError:
        return None


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
    # A viewer that names a run and the sequence number it has reached follows
    # that run from its journal. Any other socket works as it always has.
    after_seq = _parse_after_seq(query.get("after_seq"))
    attach = None
    if after_seq is not None and filters.get("request_id"):
        attach = _RunAttach(filters["request_id"], filters.get("session_id", ""), after_seq)

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
                if attach is None:
                    await ws.send_str(event)
                    continue
                for message in attach.relay(event):
                    await ws.send_str(message)
            except asyncio.TimeoutError:
                # No event, continue checking
                continue
            except Exception as e:
                logger.error(f"Error reading from queue: {e}")
                break
    
    try:
        # Send welcome message
        welcome = {
            "type": "connected",
            "message": "Connected to EFP event bus",
            "filters": filters,
        }
        catch_up: List[str] = []
        if attach is not None:
            # Taken after the listener was added and with no await in it: an
            # event is either in what the journal hands over here or still to
            # come off the queue, where one that is in both is told apart by
            # its number.
            welcome["attach"], catch_up = attach.snapshot()
        await ws.send_str(json.dumps(welcome))

        if attach is not None:
            # The journal is the replay: every event the viewer is behind by,
            # not the bus history's tail of what all runs emitted last.
            for event in catch_up:
                await ws.send_str(event)
        elif replay_requested:
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
