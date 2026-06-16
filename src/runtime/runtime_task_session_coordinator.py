"""Session-scoped coordination for async runtime task execution."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Dict, Optional


@dataclass(frozen=True)
class RuntimeTaskSessionDecision:
    action: str
    active_task_id: Optional[str] = None
    queue_position: Optional[int] = None
    pending_count: int = 0
    interrupt_seq: Optional[int] = None


@dataclass(frozen=True)
class RuntimeTaskSessionSnapshot:
    session_id: str
    task_id: str
    lane_status: Optional[str]
    active_task_id: Optional[str]
    queue_position: Optional[int]
    pending_count: int
    interrupt_seq: Optional[int]


@dataclass
class _PendingTask:
    admitted_seq: int
    delivery: str = "steer"


@dataclass
class _SessionLane:
    active_task_id: Optional[str] = None
    active_seq: Optional[int] = None
    pending: "OrderedDict[str, _PendingTask]" = None  # type: ignore[assignment]
    interrupt_seq: Optional[int] = None
    stopping: bool = False
    blocked: bool = False

    def __post_init__(self) -> None:
        if self.pending is None:
            self.pending = OrderedDict()


class RuntimeTaskSessionCoordinator:
    """Coordinate one active task execution lane per runtime task session.

    This mirrors the important long-task property from OpenCode's
    SessionRunCoordinator: a session owns one active drain, while additional
    wakeups are represented as coalesced pending work.
    """

    def __init__(self) -> None:
        self._lanes: Dict[str, _SessionLane] = {}
        self._lock = RLock()

    def schedule(
        self,
        session_id: Optional[str],
        task_id: str,
        *,
        admitted_seq: int = 0,
        delivery: str = "steer",
    ) -> RuntimeTaskSessionDecision:
        if not session_id:
            return RuntimeTaskSessionDecision(action="start")
        with self._lock:
            lane = self._lanes.setdefault(session_id, _SessionLane())
            if not self._is_after_interrupt(lane, admitted_seq):
                return self._decision("suppressed", lane)

            if lane.active_task_id is None:
                lane.active_task_id = task_id
                lane.active_seq = admitted_seq
                lane.stopping = False
                lane.blocked = False
                lane.pending.pop(task_id, None)
                return self._decision("start", lane)

            if lane.active_task_id == task_id:
                return self._decision("active", lane)

            if task_id not in lane.pending:
                lane.pending[task_id] = _PendingTask(admitted_seq=admitted_seq, delivery=_normalize_delivery(delivery))
            return self._decision("queued", lane, task_id=task_id)

    def hold_for_user_input(self, session_id: Optional[str], task_id: str) -> RuntimeTaskSessionDecision:
        if not session_id:
            return RuntimeTaskSessionDecision(action="held")
        with self._lock:
            lane = self._lanes.setdefault(session_id, _SessionLane())
            if lane.active_task_id == task_id:
                lane.blocked = True
                lane.stopping = False
            return self._decision("held", lane, task_id=task_id)

    def complete(self, session_id: Optional[str], task_id: str) -> Optional[str]:
        if not session_id:
            return None
        with self._lock:
            lane = self._lanes.get(session_id)
            if lane is None:
                return None
            if lane.active_task_id != task_id:
                lane.pending.pop(task_id, None)
                if lane.active_task_id is None and not lane.pending:
                    self._lanes.pop(session_id, None)
                return None
            lane.active_task_id = None
            lane.active_seq = None
            lane.stopping = False
            lane.blocked = False
            next_task_id = self._promote_next(lane)
            if lane.active_task_id is None and not lane.pending:
                self._lanes.pop(session_id, None)
            return next_task_id

    def cancel(self, session_id: Optional[str], task_id: str, *, admitted_seq: int = 0) -> RuntimeTaskSessionDecision:
        if not session_id:
            return RuntimeTaskSessionDecision(action="cancelled")
        with self._lock:
            lane = self._lanes.get(session_id)
            if lane is None:
                return RuntimeTaskSessionDecision(action="cancelled")
            if lane.active_task_id == task_id:
                lane.interrupt_seq = self._max_seq(lane.interrupt_seq, admitted_seq)
                lane.stopping = True
                lane.blocked = False
                self._suppress_pending_at_or_before(lane, admitted_seq)
                return self._decision("interrupting", lane, task_id=task_id)
            if task_id in lane.pending:
                lane.pending.pop(task_id, None)
                if lane.active_task_id is None and not lane.pending:
                    self._lanes.pop(session_id, None)
                return self._decision("cancelled_pending", lane, task_id=task_id)
            return self._decision("cancelled", lane, task_id=task_id)

    def snapshot(self, session_id: Optional[str], task_id: str) -> RuntimeTaskSessionSnapshot:
        resolved_session_id = session_id or ""
        with self._lock:
            lane = self._lanes.get(resolved_session_id)
            if lane is None:
                return RuntimeTaskSessionSnapshot(
                    session_id=resolved_session_id,
                    task_id=task_id,
                    lane_status=None,
                    active_task_id=None,
                    queue_position=None,
                    pending_count=0,
                    interrupt_seq=None,
                )
            lane_status: Optional[str] = None
            if lane.active_task_id == task_id:
                lane_status = "blocked" if lane.blocked else "interrupting" if lane.stopping else "active"
            elif task_id in lane.pending:
                lane_status = "queued"
            return RuntimeTaskSessionSnapshot(
                session_id=resolved_session_id,
                task_id=task_id,
                lane_status=lane_status,
                active_task_id=lane.active_task_id,
                queue_position=self._queue_position(lane, task_id),
                pending_count=len(lane.pending),
                interrupt_seq=lane.interrupt_seq,
            )

    def active_task_id(self, session_id: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        with self._lock:
            lane = self._lanes.get(session_id)
            return lane.active_task_id if lane is not None else None

    def clear(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        with self._lock:
            self._lanes.pop(session_id, None)

    def reset(self) -> None:
        with self._lock:
            self._lanes.clear()

    def _promote_next(self, lane: _SessionLane) -> Optional[str]:
        for task_id, pending in list(lane.pending.items()):
            if not self._is_after_interrupt(lane, pending.admitted_seq):
                lane.pending.pop(task_id, None)
        selected_task_id: Optional[str] = None
        selected_pending: Optional[_PendingTask] = None
        for task_id, pending in lane.pending.items():
            if pending.delivery == "steer":
                selected_task_id = task_id
                selected_pending = pending
                break
        if selected_task_id is None:
            for task_id, pending in lane.pending.items():
                selected_task_id = task_id
                selected_pending = pending
                break
        if selected_task_id is None or selected_pending is None:
            return None
        lane.pending.pop(selected_task_id, None)
        lane.active_task_id = selected_task_id
        lane.active_seq = selected_pending.admitted_seq
        lane.stopping = False
        lane.blocked = False
        return selected_task_id
        return None

    def _suppress_pending_at_or_before(self, lane: _SessionLane, seq: int) -> None:
        if seq <= 0:
            return
        for task_id, pending in list(lane.pending.items()):
            if pending.admitted_seq <= seq:
                lane.pending.pop(task_id, None)

    def _is_after_interrupt(self, lane: _SessionLane, seq: int) -> bool:
        return lane.interrupt_seq is None or (seq > 0 and seq > lane.interrupt_seq)

    def _queue_position(self, lane: _SessionLane, task_id: str) -> Optional[int]:
        for index, candidate in enumerate(lane.pending.keys(), start=1):
            if candidate == task_id:
                return index
        return None

    def _decision(self, action: str, lane: _SessionLane, *, task_id: Optional[str] = None) -> RuntimeTaskSessionDecision:
        return RuntimeTaskSessionDecision(
            action=action,
            active_task_id=lane.active_task_id,
            queue_position=self._queue_position(lane, task_id or ""),
            pending_count=len(lane.pending),
            interrupt_seq=lane.interrupt_seq,
        )

    def _max_seq(self, left: Optional[int], right: int) -> Optional[int]:
        if right <= 0:
            return left
        if left is None:
            return right
        return max(left, right)


def _normalize_delivery(value: str) -> str:
    return "queue" if str(value or "").strip().lower() == "queue" else "steer"


__all__ = [
    "RuntimeTaskSessionCoordinator",
    "RuntimeTaskSessionDecision",
    "RuntimeTaskSessionSnapshot",
]
