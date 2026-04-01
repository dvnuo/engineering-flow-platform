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
    def __init__(self):
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

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
            event_callback("task_started", {"task_id": task.task_id, "tool": tool_name, "session_id": session_id})

        async def _runner() -> Any:
            task.status = "running"
            task.started_at = datetime.utcnow().isoformat() + "Z"
            return await coro_factory()

        try:
            result = await execution_queue.enqueue(session_id, _runner)
            task.status = "completed"
            task.finished_at = datetime.utcnow().isoformat() + "Z"
            task.result = result
            if event_callback:
                event_callback("task_finished", {"task_id": task.task_id, "tool": tool_name, "session_id": session_id})
            return task
        except Exception as exc:
            task.status = "failed"
            task.finished_at = datetime.utcnow().isoformat() + "Z"
            task.error = str(exc)
            if event_callback:
                event_callback("task_failed", {"task_id": task.task_id, "tool": tool_name, "session_id": session_id, "error": str(exc)})
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
