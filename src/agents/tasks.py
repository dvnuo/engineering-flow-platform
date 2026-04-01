"""Lightweight tool task manager for skill runtime."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional

from src.agents.queue import execution_queue


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    tool_name: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result: Any = None


class TaskManager:
    def __init__(self, max_completed_tasks: int = 200):
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()
        self._max_completed_tasks = max_completed_tasks

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        return self._tasks.get(task_id)

    def _prune_completed_tasks(self) -> None:
        completed_ids = [
            task_id
            for task_id, task in self._tasks.items()
            if task.status in {"completed", "failed"}
        ]
        overflow = len(completed_ids) - self._max_completed_tasks
        if overflow <= 0:
            return
        completed_ids.sort(key=lambda task_id: self._tasks[task_id].finished_at or self._tasks[task_id].created_at)
        for task_id in completed_ids[:overflow]:
            self._tasks.pop(task_id, None)

    async def submit_tool_task(
        self,
        *,
        session_id: str,
        tool_name: str,
        coro_factory: Callable[[], Awaitable[Any]],
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> TaskRecord:
        task = TaskRecord(task_id=f"task_{uuid.uuid4().hex[:12]}", session_id=session_id, tool_name=tool_name)
        async with self._lock:
            self._tasks[task.task_id] = task

        if event_callback:
            event_callback(
                "task_queued",
                {
                    "task_id": task.task_id,
                    "tool": tool_name,
                    "session_id": session_id,
                    "status": task.status,
                    "created_at": task.created_at,
                },
            )

        async def _runner() -> Any:
            task.status = "running"
            task.started_at = datetime.utcnow().isoformat() + "Z"
            if event_callback:
                event_callback(
                    "task_started",
                    {
                        "task_id": task.task_id,
                        "tool": tool_name,
                        "session_id": session_id,
                        "status": task.status,
                        "started_at": task.started_at,
                    },
                )
            return await coro_factory()

        try:
            result = await execution_queue.enqueue(session_id, _runner)
            task.status = "completed"
            task.finished_at = datetime.utcnow().isoformat() + "Z"
            task.result = result
            self._prune_completed_tasks()
            if event_callback:
                event_callback(
                    "task_finished",
                    {
                        "task_id": task.task_id,
                        "tool": tool_name,
                        "session_id": session_id,
                        "status": task.status,
                        "finished_at": task.finished_at,
                    },
                )
            return task
        except Exception as exc:
            task.status = "failed"
            task.finished_at = datetime.utcnow().isoformat() + "Z"
            task.error = str(exc)
            self._prune_completed_tasks()
            if event_callback:
                event_callback(
                    "task_failed",
                    {
                        "task_id": task.task_id,
                        "tool": tool_name,
                        "session_id": session_id,
                        "status": task.status,
                        "finished_at": task.finished_at,
                        "error": str(exc),
                    },
                )
            raise

    async def run_tool_task(
        self,
        *,
        session_id: str,
        tool_name: str,
        coro_factory: Callable[[], Awaitable[Any]],
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Any:
        """Submit and resolve a tool task in the current request lifecycle."""
        task_record = await self.submit_tool_task(
            session_id=session_id,
            tool_name=tool_name,
            coro_factory=coro_factory,
            event_callback=event_callback,
        )
        return task_record.result


task_manager = TaskManager()
