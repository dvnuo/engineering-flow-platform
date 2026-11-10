"""In-memory registry for resumable chat executions."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .chat_run_journal import ChatRunJournal


logger = logging.getLogger(__name__)

TERMINAL_STATES = {"completed", "failed", "cancelled"}
CANCELLED_STATUSES = {"cancelled", "canceled"}
RETAINED_EVENT_LIST_KEYS = ("events", "runtime_events", "thinking_events")
RETAINED_EVENT_TAIL_ITEMS = 100
DEFAULT_STALE_RUNNING_SECONDS = 6 * 3600
# How long a finished run's journal stays, for a viewer still catching up on
# its last events. After that the stored session is the only account of it.
JOURNAL_RETENTION_SECONDS = 300
# How often ``wait_terminal`` looks again while a stop settles.
_WAIT_POLL_SECONDS = 0.05

# A callback the run registers so a stop can reach it: asking the runtime to
# end the run at its next checkpoint. Sync, or returning an awaitable.
Canceller = Callable[[], Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp ('Z' or offset suffix); None when unparseable."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def compact_final_payload(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    """Drop full event streams from a retained terminal payload.

    Live viewers already received the complete stream over SSE. The registry
    only serves late reconnects, and the chat UI merges at most the last 100
    events from a final payload, so retaining the full per-delta event list
    for up to ``max_records`` runs only accumulates memory.
    """
    compact = dict(payload or {})
    for key in RETAINED_EVENT_LIST_KEYS:
        value = compact.get(key)
        if isinstance(value, list) and len(value) > RETAINED_EVENT_TAIL_ITEMS:
            compact[key] = value[-RETAINED_EVENT_TAIL_ITEMS:]
            compact[f"{key}_count"] = len(value)
            compact[f"{key}_truncated"] = True
    return compact


def payload_says_cancelled(payload: Dict[str, Any] | None) -> bool:
    """Whether a terminal payload describes a run that ended because it was stopped."""
    if not isinstance(payload, dict):
        return False
    for key in ("completion_state", "status"):
        value = str(payload.get(key) or "").strip().lower()
        if value in CANCELLED_STATUSES:
            return True
    return False


def _invoke_canceller(canceller: Canceller, request_id: str) -> None:
    try:
        result = canceller()
    except Exception:  # noqa: BLE001 - a failing canceller must not block the stop.
        logger.warning("Chat run canceller failed | request_id=%s", request_id, exc_info=True)
        return
    if inspect.isawaitable(result):
        try:
            asyncio.ensure_future(result)
        except RuntimeError:
            logger.debug("Chat run canceller returned an awaitable outside a loop | request_id=%s", request_id)


@dataclass
class ChatRunRecord:
    request_id: str
    session_id: str
    engine: str = "native"
    state: str = "running"
    started_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    latest_event_at: str = ""
    latest_event_seq: int = 0
    replay_available: bool = True
    detached_viewers: int = 0
    final_payload: Optional[Dict[str, Any]] = None
    error_payload: Optional[Dict[str, Any]] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    # A stop was asked for. ``cancel_mode`` says how far it had to go:
    # "cooperative" (the run ended at a checkpoint) or "forced" (its task was
    # cancelled outright after the grace period).
    cancel_requested: bool = False
    cancel_requested_at: str = ""
    cancel_mode: str = ""
    cancellers: List[Canceller] = field(default_factory=list, repr=False)
    # Every event of the run under its sequence number, for a viewer that
    # joins late. None once it has been dropped: ``journal_expires_at``
    # (monotonic) is set when the run ends.
    journal: Optional[ChatRunJournal] = field(default_factory=ChatRunJournal, repr=False)
    journal_expires_at: float = field(default=0.0, repr=False)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_payload(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "engine": self.engine,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "state": self.state,
            "terminal": self.terminal,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "latest_event_at": self.latest_event_at,
            "latest_event_seq": self.latest_event_seq,
            "replay_available": self.replay_available,
            "detached_viewers": self.detached_viewers,
            "final_payload": self.final_payload,
            "error_payload": self.error_payload,
            "cancel_requested": self.cancel_requested,
            "cancel_mode": self.cancel_mode,
        }


class ChatRunRegistry:
    def __init__(
        self,
        *,
        max_records: int = 512,
        stale_running_seconds: float = DEFAULT_STALE_RUNNING_SECONDS,
        journal_retention_seconds: float = JOURNAL_RETENTION_SECONDS,
    ) -> None:
        self._records: Dict[str, ChatRunRecord] = {}
        self._max_records = max_records
        self._stale_running_seconds = max(0.0, float(stale_running_seconds))
        self._journal_retention_seconds = max(0.0, float(journal_retention_seconds))

    def start(self, *, session_id: str, request_id: str, engine: str = "native") -> ChatRunRecord:
        existing = self.get(request_id, session_id=session_id)
        if existing is not None:
            return existing
        record = ChatRunRecord(request_id=request_id, session_id=session_id, engine=engine)
        self._records[request_id] = record
        self._fail_stale_running_records()
        self._prune_terminal_records()
        self._drop_expired_journals()
        return record

    def _fail_stale_running_records(self) -> None:
        """Mark long-inactive non-terminal records failed so they become prunable.

        A record left in ``running`` this long means the producing run died
        before ``complete()``/``fail()`` (for example the request handler was
        cancelled by a client disconnect); otherwise ``record_event`` would
        have refreshed ``updated_at``.
        """
        if self._stale_running_seconds <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._stale_running_seconds)
        for record in self._records.values():
            if record.terminal:
                continue
            updated_at = _parse_iso_timestamp(record.updated_at)
            if updated_at is None or updated_at >= cutoff:
                continue
            record.state = "failed"
            record.error_payload = {
                "error": "chat_run_stale",
                "detail": "No run activity before the stale timeout; a terminal outcome was never recorded.",
                "session_id": record.session_id,
                "request_id": record.request_id,
            }
            record.updated_at = utc_now_iso()
            record.task = None
            record.cancellers.clear()
            self._retire_journal(record)

    def _retire_journal(self, record: ChatRunRecord) -> None:
        """Start the clock on a finished run's journal."""
        if record.journal is None or record.journal_expires_at:
            return
        record.journal_expires_at = time.monotonic() + self._journal_retention_seconds
        try:
            asyncio.get_running_loop().call_later(
                self._journal_retention_seconds, self._drop_journal, record
            )
        except RuntimeError:
            # No loop to time it here: the next run to start sweeps it.
            pass

    @staticmethod
    def _drop_journal(record: ChatRunRecord) -> None:
        record.journal = None

    def _drop_expired_journals(self) -> None:
        now = time.monotonic()
        for record in self._records.values():
            if record.journal is not None and 0 < record.journal_expires_at <= now:
                record.journal = None

    def _prune_terminal_records(self) -> None:
        if len(self._records) <= self._max_records:
            return
        overflow = len(self._records) - self._max_records
        terminal_records = sorted(
            (record for record in self._records.values() if record.terminal),
            key=lambda record: record.updated_at,
        )
        for record in terminal_records[:overflow]:
            self._records.pop(record.request_id, None)

    def get(self, request_id: str, *, session_id: str | None = None) -> ChatRunRecord | None:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            return None
        record = self._records.get(normalized_request_id)
        if record is None:
            return None
        if session_id and record.session_id != session_id:
            return None
        return record

    def active_for_session(self, session_id: str) -> ChatRunRecord | None:
        """The run still in flight for a session, if any.

        One chat run per session: a second message while one is answering is
        refused (or interrupts the first) instead of the two runs writing the
        same history at once. A record whose task already ended without a
        terminal outcome is finalized here rather than counted as active.
        """
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return None
        for record in list(self._records.values()):
            if record.session_id != normalized_session_id or record.terminal:
                continue
            task = record.task
            if task is not None and task.done():
                self.fail(
                    record.request_id,
                    {
                        "error": "chat_run_lost",
                        "detail": "The run's task ended without recording an outcome.",
                        "session_id": record.session_id,
                        "request_id": record.request_id,
                    },
                )
                continue
            return record
        return None

    def live_run_for_session(self, session_id: str) -> Dict[str, Any] | None:
        """What a page opening the session needs to join the run in flight, if any.

        ``last_seq`` is how far the run has got: the page asks the event socket
        for everything after the sequence number it already has.
        """
        record = self.active_for_session(session_id)
        if record is None:
            return None
        return {
            "request_id": record.request_id,
            "state": record.state,
            "started_at": record.started_at,
            "last_seq": record.latest_event_seq,
        }

    def journal_for(self, request_id: str, *, session_id: str | None = None) -> ChatRunJournal | None:
        """The run's journal while it is kept; None once it is gone, or for an unknown run."""
        record = self.get(request_id, session_id=session_id)
        if record is None or record.journal is None:
            return None
        if 0 < record.journal_expires_at <= time.monotonic():
            record.journal = None
            return None
        return record.journal

    def attach_task(self, request_id: str, task: asyncio.Task) -> None:
        record = self.get(request_id)
        if record is not None:
            record.task = task
            record.updated_at = utc_now_iso()

    def register_canceller(self, request_id: str, canceller: Canceller) -> None:
        """Let a stop reach the run: ``canceller`` asks it to end at its next checkpoint."""
        record = self.get(request_id)
        if record is None or record.terminal:
            return
        record.cancellers.append(canceller)
        if record.cancel_requested:
            # The run registered after the stop was asked for: tell it now.
            _invoke_canceller(canceller, request_id)

    def record_event(self, request_id: str, event: Dict[str, Any]) -> int:
        """Number the run's next event and keep it in the run's journal.

        The number is written onto the event (``run_seq``), so call this before
        the event is sent anywhere: a viewer orders what it receives by it, and
        asks for what it missed by it. Returns 0, and leaves the event alone,
        when the run is unknown or already over.
        """
        record = self.get(request_id)
        if record is None or record.terminal:
            return 0
        record.latest_event_seq += 1
        seq = record.latest_event_seq
        created_at = event.get("created_at") if isinstance(event, dict) else None
        record.latest_event_at = str(created_at or utc_now_iso())
        record.updated_at = utc_now_iso()
        if isinstance(event, dict):
            event["run_seq"] = seq
            if record.journal is not None:
                record.journal.append(seq, event)
        return seq

    def mark_detached(self, request_id: str) -> None:
        record = self.get(request_id)
        if record is None or record.terminal:
            return
        record.detached_viewers += 1
        record.updated_at = utc_now_iso()

    def complete(self, request_id: str, final_payload: Dict[str, Any]) -> None:
        record = self.get(request_id)
        if record is None:
            return
        # A run that ended because it was stopped is "cancelled", whichever
        # path recorded it. One that finished its answer just as the stop
        # arrived did complete: the answer landed, and that is what it says.
        record.state = "cancelled" if payload_says_cancelled(final_payload) else "completed"
        record.final_payload = compact_final_payload(final_payload)
        record.updated_at = utc_now_iso()
        record.task = None
        record.cancellers.clear()
        self._retire_journal(record)

    def fail(self, request_id: str, error_payload: Dict[str, Any]) -> None:
        record = self.get(request_id)
        if record is None:
            return
        if record.state == "cancelled":
            return
        # Whatever broke while the run was being stopped, the member asked
        # for a stop and got one: the record says cancelled, with the error
        # kept for diagnostics.
        record.state = "cancelled" if record.cancel_requested else "failed"
        record.error_payload = compact_final_payload(error_payload)
        record.updated_at = utc_now_iso()
        record.task = None
        record.cancellers.clear()
        self._retire_journal(record)

    def request_cancel(self, request_id: str) -> ChatRunRecord | None:
        """Ask the run to stop at its next checkpoint (cooperative).

        The runtime ends the run on its own: the shell tool kills its command,
        the streaming reply is cut off and what streamed so far is kept, the
        usage is booked and the session is left consistent. ``cancel`` is the
        forced fallback for a run that does not get there in time.
        """
        record = self.get(request_id)
        if record is None or record.terminal:
            return record
        if not record.cancel_requested:
            record.cancel_requested = True
            record.cancel_requested_at = utc_now_iso()
            record.cancel_mode = "cooperative"
        record.state = "cancelling"
        record.updated_at = utc_now_iso()
        for canceller in list(record.cancellers):
            _invoke_canceller(canceller, request_id)
        return record

    def cancel(self, request_id: str) -> bool:
        """Stop the run now: mark it cancelled and cancel its task (forced)."""
        record = self.get(request_id)
        if record is None or record.terminal:
            return False
        record.state = "cancelled"
        if not record.cancel_requested:
            record.cancel_requested = True
            record.cancel_requested_at = utc_now_iso()
        record.cancel_mode = "forced"
        record.updated_at = utc_now_iso()
        task = record.task
        record.task = None
        record.cancellers.clear()
        self._retire_journal(record)
        if task is not None and not task.done():
            task.cancel()
        return True

    async def wait_terminal(self, request_id: str, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for the run to reach a terminal state."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(timeout))
        while True:
            record = self.get(request_id)
            if record is None or record.terminal:
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(_WAIT_POLL_SECONDS)


chat_run_registry = ChatRunRegistry()
