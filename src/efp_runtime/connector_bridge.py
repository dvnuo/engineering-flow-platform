"""Connector bridge broker for EFP runtime tools that wait on a Portal page.

A *connector* is a per-user capability that lives outside the agent pod, for
example a bridge program on the user's own PC that drives their browser. The
runtime cannot reach it directly; the Portal chat page relays the request and
posts the result back. This module holds the in-process rendezvous for that
round trip:

1. a tool registers a request id and publishes a ``tool.connector_requested``
   runtime event (projected to ``connector.request`` on the events socket),
2. the tool awaits :meth:`ConnectorBridgeBroker.wait`,
3. the page executes the request locally and POSTs to
   ``/api/sessions/{session_id}/connectors/respond``,
4. the gateway calls :meth:`ConnectorBridgeBroker.resolve` and the tool
   returns the payload as its result.

Unlike the ``question`` tool this does not suspend the run: the tool blocks
inside its own execution, so a multi-step browser task costs one run, not one
run per step. The gateway is a single long-lived process, which is why a
module-level broker is enough to connect the tool and the endpoint.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from .types import utc_now_iso


DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_TIMEOUT_SECONDS = 120.0
MIN_TIMEOUT_SECONDS = 5.0
_CANCEL_POLL_SECONDS = 0.5


class ConnectorBridgeTimeout(Exception):
    """Raised when no response arrived before the request timeout."""


class ConnectorBridgeCancelled(Exception):
    """Raised when the run was cancelled while waiting for a response."""


@dataclass
class ConnectorRequest:
    """One outstanding request to a connector bridge."""

    request_id: str
    session_id: Optional[str]
    connector_type: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    target_client_id: Optional[str] = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.request_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "connector_type": self.connector_type,
            "action": self.action,
            "params": dict(self.params),
            "target_client_id": self.target_client_id,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class _Pending:
    request: ConnectorRequest
    loop: asyncio.AbstractEventLoop
    future: "asyncio.Future[dict[str, Any]]"
    registered_at: float


def clamp_timeout(value: Any, default: float = DEFAULT_TIMEOUT_SECONDS) -> float:
    """Return a timeout in seconds inside the supported window."""

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = default
    if seconds != seconds:  # NaN
        seconds = default
    return max(MIN_TIMEOUT_SECONDS, min(MAX_TIMEOUT_SECONDS, seconds))


def new_request_id() -> str:
    return f"cr_{uuid4().hex[:24]}"


class ConnectorBridgeBroker:
    """Rendezvous between a waiting tool and the endpoint that resolves it.

    Thread-safe: ``resolve`` may be called from any thread or loop; the result
    is handed to the waiting loop with ``call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    # ---- registration -----------------------------------------------------
    def register(self, request: ConnectorRequest) -> ConnectorRequest:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        with self._lock:
            if request.request_id in self._pending:
                raise ValueError(f"connector request already pending: {request.request_id}")
            self._pending[request.request_id] = _Pending(
                request=request,
                loop=loop,
                future=future,
                registered_at=time.monotonic(),
            )
        return request

    def create(
        self,
        *,
        session_id: Optional[str],
        connector_type: str,
        action: str,
        params: Mapping[str, Any] | None = None,
        target_client_id: Optional[str] = None,
        timeout_seconds: Any = DEFAULT_TIMEOUT_SECONDS,
        metadata: Mapping[str, Any] | None = None,
        request_id: Optional[str] = None,
    ) -> ConnectorRequest:
        request = ConnectorRequest(
            request_id=request_id or new_request_id(),
            session_id=session_id,
            connector_type=connector_type,
            action=action,
            params=dict(params or {}),
            target_client_id=target_client_id,
            timeout_seconds=clamp_timeout(timeout_seconds),
            metadata=dict(metadata or {}),
        )
        return self.register(request)

    # ---- inspection -------------------------------------------------------
    def pending(self, session_id: Optional[str] = None) -> list[ConnectorRequest]:
        with self._lock:
            items = [item.request for item in self._pending.values()]
        if session_id is None:
            return items
        return [item for item in items if item.session_id == session_id]

    def get(self, request_id: str) -> Optional[ConnectorRequest]:
        with self._lock:
            item = self._pending.get(request_id)
        return item.request if item is not None else None

    # ---- resolution -------------------------------------------------------
    def resolve(self, request_id: str, payload: Mapping[str, Any]) -> bool:
        """Hand ``payload`` to the waiting tool. Returns False when unknown."""

        with self._lock:
            item = self._pending.pop(request_id, None)
        if item is None:
            return False
        result = dict(payload)

        def _set() -> None:
            if not item.future.done():
                item.future.set_result(result)

        _call_on_loop(item.loop, _set)
        return True

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            item = self._pending.pop(request_id, None)
        if item is None:
            return False

        def _cancel() -> None:
            if not item.future.done():
                item.future.cancel()

        _call_on_loop(item.loop, _cancel)
        return True

    def clear(self) -> None:
        with self._lock:
            items = list(self._pending.values())
            self._pending.clear()
        for item in items:
            _call_on_loop(item.loop, lambda fut=item.future: fut.cancel() if not fut.done() else None)

    # ---- waiting ------------------------------------------------------------
    async def wait(
        self,
        request_id: str,
        *,
        timeout_seconds: Optional[float] = None,
        cancel_requested: Callable[[], bool | Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        """Wait for ``resolve``; raise on timeout or run cancellation."""

        with self._lock:
            item = self._pending.get(request_id)
        if item is None:
            raise KeyError(f"connector request is not pending: {request_id}")
        timeout = (
            clamp_timeout(timeout_seconds)
            if timeout_seconds is not None
            else item.request.timeout_seconds
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ConnectorBridgeTimeout(request_id)
                slice_seconds = min(_CANCEL_POLL_SECONDS, remaining)
                done, _ = await asyncio.wait({item.future}, timeout=slice_seconds)
                if done:
                    if item.future.cancelled():
                        raise ConnectorBridgeCancelled(request_id)
                    return item.future.result()
                if await _is_cancel_requested(cancel_requested):
                    raise ConnectorBridgeCancelled(request_id)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
            if not item.future.done():
                item.future.cancel()


def _call_on_loop(loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        callback()
        return
    if loop.is_closed():
        return
    loop.call_soon_threadsafe(callback)


async def _is_cancel_requested(
    cancel_requested: Callable[[], bool | Awaitable[bool]] | None,
) -> bool:
    if cancel_requested is None:
        return False
    try:
        value = cancel_requested()
        if inspect.isawaitable(value):
            value = await value
    except Exception:
        return False
    return bool(value)


_DEFAULT_BROKER = ConnectorBridgeBroker()


def get_connector_bridge_broker() -> ConnectorBridgeBroker:
    """Return the process-wide broker shared by tools and the gateway."""

    return _DEFAULT_BROKER


__all__ = [
    "ConnectorBridgeBroker",
    "ConnectorBridgeCancelled",
    "ConnectorBridgeTimeout",
    "ConnectorRequest",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_TIMEOUT_SECONDS",
    "clamp_timeout",
    "get_connector_bridge_broker",
    "new_request_id",
]
