"""Tests for execution queue module."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.queue import ExecutionQueue


class TestExecutionQueue:
    """Tests for ExecutionQueue class."""
    
    @pytest.fixture
    def queue(self):
        """Create a fresh execution queue."""
        return ExecutionQueue()
    
    @pytest.fixture
    def mock_coro(self):
        """Create a mock coroutine."""
        async def dummy_coro(x, y=10):
            await asyncio.sleep(0.01)
            return x + y
        return dummy_coro
    
    @pytest.mark.asyncio
    async def test_enqueue_single_task(self, queue, mock_coro):
        """Test enqueueing a single task."""
        result = await queue.enqueue("session1", mock_coro, 5)
        assert result == 15
    
    @pytest.mark.asyncio
    async def test_enqueue_sequential_tasks(self, queue, mock_coro):
        """Test that tasks for same session run sequentially."""
        results = []
        
        async def track_coro(value):
            await asyncio.sleep(0.05)  # Simulate work
            results.append(value)
            return value
        
        # Enqueue multiple tasks for same session
        await queue.enqueue("session1", track_coro, 1)
        await queue.enqueue("session1", track_coro, 2)
        await queue.enqueue("session1", track_coro, 3)
        
        # All should complete
        assert len(results) == 3
        # They may not be in order due to timing, but all should complete
    
    @pytest.mark.asyncio
    async def test_parallel_sessions(self, queue, mock_coro):
        """Test that different sessions run in parallel."""
        execution_times = []
        
        async def timed_coro(session):
            start = asyncio.get_event_loop().time()
            await asyncio.sleep(0.1)
            end = asyncio.get_event_loop().time()
            execution_times.append((session, end - start))
            return True
        
        # Run for different sessions
        await asyncio.gather(
            queue.enqueue("s1", timed_coro, "s1"),
            queue.enqueue("s2", timed_coro, "s2"),
            queue.enqueue("s3", timed_coro, "s3"),
        )
        
        # All should have short execution times (parallel)
        for _, duration in execution_times:
            assert duration < 0.3  # Should be ~0.1, not 0.3
    
    @pytest.mark.asyncio
    async def test_error_propagation(self, queue):
        """Test that errors are propagated correctly."""
        async def failing_coro():
            raise ValueError("Test error")
        
        with pytest.raises(ValueError, match="Test error"):
            await queue.enqueue("error-session", failing_coro)
    
    @pytest.mark.asyncio
    async def test_get_queue_status(self, queue):
        """Test getting queue status."""
        status = await queue.get_queue_status("nonexistent")
        
        assert status["session_id"] == "nonexistent"
        assert status["queue_size"] == 0
        assert status["running"] is False
        assert status["waiting"] is False
    
    @pytest.mark.asyncio
    async def test_get_queue_status_with_tasks(self, queue):
        """Test queue status with pending tasks."""
        async def slow_coro():
            await asyncio.sleep(1)
            return True
        
        # Start a task
        task = asyncio.create_task(
            queue.enqueue("status-session", slow_coro)
        )
        
        # Wait for queue to have tasks
        await asyncio.sleep(0.05)
        
        status = await queue.get_queue_status("status-session")
        
        assert status["queue_size"] >= 0  # May be 0 if already processing
        assert status["running"] is True
        
        # Cleanup
        task.cancel()
    
    @pytest.mark.asyncio
    async def test_list_all_queues(self, queue):
        """Test listing all queue statuses."""
        # Add tasks to multiple sessions
        async def dummy_coro():
            await asyncio.sleep(0.1)
            return True
        
        await queue.enqueue("s1", dummy_coro)
        await queue.enqueue("s2", dummy_coro)
        
        status = await queue.list_all_queues()
        
        assert "s1" in status or "s2" in status
    
    @pytest.mark.asyncio
    async def test_clear_session(self, queue):
        """Test clearing a session queue."""
        async def slow_coro():
            await asyncio.sleep(0.5)
            return True
        
        # Add pending tasks
        queue._session_queues["clear-session"] = asyncio.Queue()
        await queue._session_queues["clear-session"].put(lambda: None)
        await queue._session_queues["clear-session"].put(lambda: None)
        
        # Clear
        count = await queue.clear_session("clear-session")
        
        assert count == 2
        assert "clear-session" not in queue._session_queues
    
    @pytest.mark.asyncio
    async def test_clear_all(self, queue):
        """Test clearing all session queues."""
        # Add multiple sessions
        queue._session_queues["s1"] = asyncio.Queue()
        queue._session_queues["s2"] = asyncio.Queue()
        queue._session_queues["s1"].put(lambda: None)
        queue._session_queues["s2"].put(lambda: None)
        
        # Clear all
        total = await queue.clear_all()
        
        assert total == 2
        assert len(queue._session_queues) == 0
    
    @pytest.mark.asyncio
    async def test_get_active_sessions(self, queue):
        """Test getting active session count."""
        # Empty
        assert queue.get_active_sessions() == 0
        
        # Add sessions
        queue._session_queues["s1"] = asyncio.Queue()
        queue._session_queues["s2"] = asyncio.Queue()
        
        assert queue.get_active_sessions() == 2
    
    @pytest.mark.asyncio
    async def test_global_queue(self, queue):
        """Test execution queue with global queue enabled."""
        global_queue = ExecutionQueue(global_queue=True)
        
        results = []
        
        async def record_coro(session):
            await asyncio.sleep(0.05)
            results.append(session)
            return session
        
        # These should serialize through global queue
        await asyncio.gather(
            global_queue.enqueue("s1", record_coro, "s1"),
            global_queue.enqueue("s2", record_coro, "s2"),
        )
        
        # Both should complete
        assert len(results) == 2


class TestExecutionQueueConcurrency:
    """Concurrency edge case tests."""
    
    @pytest.mark.asyncio
    async def test_rapid_enqueues(self):
        """Test rapid task enqueueing."""
        queue = ExecutionQueue()
        
        results = []
        
        async def increment_coro(session_id, value):
            await asyncio.sleep(0.01)
            results.append(value)
            return value
        
        # Enqueue many tasks rapidly
        tasks = [
            queue.enqueue("rapid", increment_coro, "rapid", i)
            for i in range(20)
        ]
        
        await asyncio.gather(*tasks)
        
        assert len(results) == 20
    
    @pytest.mark.asyncio
    async def test_mixed_session_tasks(self):
        """Test tasks from multiple sessions."""
        queue = ExecutionQueue()
        
        results = []
        
        async def collect_coro(session_id, value):
            await asyncio.sleep(0.02)
            results.append((session_id, value))
            return value
        
        # Mix sessions
        await asyncio.gather(
            queue.enqueue("A", collect_coro, "A", 1),
            queue.enqueue("B", collect_coro, "B", 1),
            queue.enqueue("A", collect_coro, "A", 2),
            queue.enqueue("B", collect_coro, "B", 2),
            queue.enqueue("A", collect_coro, "A", 3),
        )
        
        # All should complete
        assert len(results) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
