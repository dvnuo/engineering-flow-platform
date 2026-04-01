"""Execution queue for per-session task serialization.

Prevents concurrent execution within the same session.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, Optional

logger = logging.getLogger(__name__)


class ExecutionQueue:
    """Per-session execution queue for serializing tasks.
    
    Each session has its own queue, ensuring tasks are executed
    one at a time while allowing parallel execution across sessions.
    """
    
    def __init__(self, global_queue: bool = False):
        """Initialize the execution queue.
        
        Args:
            global_queue: If True, also serialize through a global queue
        """
        self._session_queues: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._global_queue: Optional[asyncio.Queue] = asyncio.Queue() if global_queue else None
        self._running: Dict[str, bool] = defaultdict(bool)
        self._results: Dict[str, Any] = {}
        self._errors: Dict[str, Exception] = {}
        self._task_counter = 0
        self._lock = asyncio.Lock()
    
    async def enqueue(
        self,
        session_id: str,
        coro: Callable[..., Coroutine],
        *args,
        **kwargs
    ) -> Any:
        """Enqueue a task for a session.
        
        Tasks for the same session will be executed sequentially.
        Tasks for different sessions can run in parallel.
        
        Args:
            session_id: The session to queue this task under
            coro: The coroutine to execute
            *args, **kwargs: Arguments to pass to the coroutine
            
        Returns:
            The result of the coroutine
            
        Raises:
            Exception: Any exception raised by the coroutine
        """
        async with self._lock:
            queue = self._session_queues[session_id]
            
            # Create a unique task ID
            self._task_counter += 1
            task_id = f"{session_id}:{self._task_counter}"
            
            # Check if we need to start processing
            needs_start = not self._running.get(session_id, False)
            
            await queue.put((task_id, coro, args, kwargs))
            
            if needs_start:
                self._running[session_id] = True
                asyncio.create_task(self._process_queue(session_id))
        
        # Wait for result
        while task_id not in self._results and task_id not in self._errors:
            await asyncio.sleep(0.01)
        
        if task_id in self._errors:
            error = self._errors.pop(task_id)
            raise error
        
        return self._results.pop(task_id)
    
    async def _process_queue(self, session_id: str) -> None:
        """Process tasks in the session queue."""
        queue = self._session_queues.get(session_id)
        if queue is None:
            return
        
        while not queue.empty():
            # Check global queue if enabled
            if self._global_queue:
                await self._global_queue.get()
            
            try:
                task_id, coro, args, kwargs = await queue.get()
                
                # Execute task
                try:
                    result = await coro(*args, **kwargs)
                    self._results[task_id] = result
                except Exception as e:
                    self._errors[task_id] = e
                    logger.error(f"Task error in {session_id}: {e}")
                finally:
                    queue.task_done()
                    
                    # Release global slot
                    if self._global_queue:
                        self._global_queue.task_done()
            except asyncio.CancelledError:
                break
        
        async with self._lock:
            self._running[session_id] = False
            if queue.empty():
                del self._session_queues[session_id]
    

    async def enqueue_task(
        self,
        session_id: str,
        coro: Callable[..., Coroutine],
        *args,
        **kwargs
    ) -> Any:
        """Task-focused alias used by skill runtime task manager."""
        return await self.enqueue(session_id, coro, *args, **kwargs)

    async def get_queue_status(self, session_id: str) -> Dict[str, Any]:
        """Get the current status of a session queue."""
        queue = self._session_queues.get(session_id)
        
        if queue is None:
            return {
                "session_id": session_id,
                "queue_size": 0,
                "running": False,
                "waiting": False,
            }
        
        return {
            "session_id": session_id,
            "queue_size": queue.qsize(),
            "running": self._running.get(session_id, False),
            "waiting": queue.qsize() > 0,
        }
    
    async def list_all_queues(self) -> Dict[str, Dict[str, Any]]:
        """List status of all session queues."""
        status = {}
        for session_id in self._session_queues:
            status[session_id] = await self.get_queue_status(session_id)
        return status
    
    async def clear_session(self, session_id: str) -> int:
        """Clear all pending tasks for a session."""
        queue = self._session_queues.get(session_id)
        if queue is None:
            return 0
        
        cleared = 0
        while not queue.empty():
            try:
                await queue.get()
                queue.task_done()
                cleared += 1
            except asyncio.CancelledError:
                break
        
        async with self._lock:
            self._running[session_id] = False
            del self._session_queues[session_id]
        
        return cleared
    
    async def clear_all(self) -> int:
        """Clear all session queues."""
        total = 0
        for session_id in list(self._session_queues.keys()):
            total += await self.clear_session(session_id)
        return total
    
    def get_active_sessions(self) -> int:
        """Get the number of active (non-empty) queues."""
        return len(self._session_queues)


# Global execution queue
execution_queue = ExecutionQueue()
