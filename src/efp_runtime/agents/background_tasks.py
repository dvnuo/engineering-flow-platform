"""In-process background subagent task management for Runtime v2."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

from ..tools.builtin.task import (
    TaskToolRequest,
    TaskToolResult,
    TaskToolRunner,
    _normalize_state,
    _normalize_task_response,
)
from ..types import utc_now_iso


FINAL_BACKGROUND_TASK_STATES = {"completed", "error", "cancelled"}


@dataclass
class BackgroundTaskRecord:
    """State tracked for one background task in the current process."""

    task_id: str
    description: str
    prompt: str
    subagent_type: str
    session_id: str | None
    started_at: str
    finished_at: str | None
    state: str
    result: TaskToolResult | None
    error: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)


class BackgroundTaskManager:
    """Manage process-local background task runners."""

    def __init__(self) -> None:
        self._records: dict[str, BackgroundTaskRecord] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._drained: set[str] = set()

    def start(
        self,
        request: TaskToolRequest,
        runner: TaskToolRunner,
    ) -> BackgroundTaskRecord:
        """Start a runner in the background and return its record."""

        task_id = str(request.task_id)
        if task_id in self._records:
            raise ValueError(f"Background task already exists: {task_id}")
        record = BackgroundTaskRecord(
            task_id=task_id,
            description=request.description,
            prompt=request.prompt,
            subagent_type=request.subagent_type,
            session_id=request.session_id,
            started_at=utc_now_iso(),
            finished_at=None,
            state="running",
            result=None,
            error=None,
            metadata=_request_metadata(request),
        )
        task = asyncio.create_task(self._run(record, request, runner))
        self._records[task_id] = record
        self._tasks[task_id] = task
        return record

    def get(self, task_id: str) -> BackgroundTaskRecord:
        """Return a task record or raise KeyError when unknown."""

        return self._require(task_id)

    def list(self, session_id: str | None = None) -> list[BackgroundTaskRecord]:
        """Return known tasks, optionally limited to one parent session."""

        records = list(self._records.values())
        if session_id is not None:
            records = [
                record for record in records if record.session_id == str(session_id)
            ]
        return sorted(records, key=lambda record: record.started_at)

    def cancel(self, task_id: str) -> BackgroundTaskRecord:
        """Cancel a running task and return its current record."""

        record = self._require(task_id)
        if record.state != "running":
            return record
        task = self._tasks.get(record.task_id)
        if task is not None and not task.done():
            task.cancel()
        self._finish(
            record,
            state="cancelled",
            result=TaskToolResult(
                task_id=record.task_id,
                text="Task cancelled.",
                state="cancelled",
            ),
            error=None,
        )
        return record

    def drain_completed(
        self,
        session_id: str | None = None,
    ) -> list[BackgroundTaskRecord]:
        """Return final-state records once per drain call."""

        drained: list[BackgroundTaskRecord] = []
        for record in self.list(session_id=session_id):
            if record.state not in FINAL_BACKGROUND_TASK_STATES:
                continue
            if record.task_id in self._drained:
                continue
            self._drained.add(record.task_id)
            drained.append(record)
        return drained

    def record_to_dict(self, record: BackgroundTaskRecord) -> dict[str, Any]:
        """Return a JSON-compatible task record payload."""

        return background_task_record_to_dict(record)

    async def _run(
        self,
        record: BackgroundTaskRecord,
        request: TaskToolRequest,
        runner: TaskToolRunner,
    ) -> None:
        try:
            raw_response = runner(request)
            if inspect.isawaitable(raw_response):
                raw_response = await raw_response
            result = _normalize_task_response(raw_response, request)
            state = _normalize_state(result.state)
            if state == "completed":
                self._finish(record, state="completed", result=result, error=None)
                return
            if state == "error":
                self._finish(
                    record,
                    state="error",
                    result=result,
                    error=result.text or "Background task failed.",
                )
                return
            if state == "cancelled":
                self._finish(record, state="cancelled", result=result, error=None)
                return
            self._finish(
                record,
                state="error",
                result=TaskToolResult(
                    task_id=request.task_id,
                    text=f"Unsupported background task state: {result.state}",
                    state="error",
                    metadata={"task_result_metadata": dict(result.metadata)},
                ),
                error=f"Unsupported background task state: {result.state}",
            )
        except asyncio.CancelledError:
            self._finish(
                record,
                state="cancelled",
                result=TaskToolResult(
                    task_id=request.task_id,
                    text="Task cancelled.",
                    state="cancelled",
                ),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - manager normalizes runner failures.
            message = str(exc) or exc.__class__.__name__
            self._finish(
                record,
                state="error",
                result=TaskToolResult(
                    task_id=request.task_id,
                    text=message,
                    state="error",
                    metadata={"error_type": exc.__class__.__name__},
                ),
                error=message,
            )

    def _finish(
        self,
        record: BackgroundTaskRecord,
        *,
        state: str,
        result: TaskToolResult | None,
        error: str | None,
    ) -> None:
        if record.state == "cancelled" and state != "cancelled":
            return
        record.state = state
        record.result = result
        record.error = error
        if record.finished_at is None:
            record.finished_at = utc_now_iso()

    def _require(self, task_id: str) -> BackgroundTaskRecord:
        try:
            return self._records[str(task_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown background task: {task_id}") from exc


def background_task_record_to_dict(record: BackgroundTaskRecord) -> dict[str, Any]:
    result = _task_result_payload(record.result)
    payload = {
        "task_id": record.task_id,
        "description": record.description,
        "prompt": record.prompt,
        "subagent_type": record.subagent_type,
        "session_id": record.session_id,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "state": record.state,
        "background": True,
        "result": result,
        "error": record.error,
        "metadata": dict(record.metadata),
    }
    if result is not None:
        payload["text"] = result["text"]
        payload["result_metadata"] = dict(result["metadata"])
    return payload


def _task_result_payload(result: TaskToolResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "task_id": result.task_id,
        "text": result.text,
        "state": result.state,
        "metadata": dict(result.metadata),
    }


def _request_metadata(request: TaskToolRequest) -> dict[str, Any]:
    metadata = {
        "task_id": request.task_id,
        "description": request.description,
        "subagent_type": request.subagent_type,
        "session_id": request.session_id,
        "background": True,
    }
    if request.command is not None:
        metadata["command"] = request.command
    if request.metadata:
        metadata["request_metadata"] = dict(request.metadata)
    return metadata


__all__ = [
    "BackgroundTaskManager",
    "BackgroundTaskRecord",
    "FINAL_BACKGROUND_TASK_STATES",
    "background_task_record_to_dict",
]
