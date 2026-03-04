"""Tests for Issue #290 memory enhancement features."""

import json
import os
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

from src.memory.event_log import EventLogger
from src.memory.validators import validate_memory_ops, sanitize_memory_ops


class TestEventLogger:
    """Tests for EventLogger."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_log_event(self, temp_workspace):
        """Test logging a single event."""
        logger = EventLogger(temp_workspace)
        logger.log_event("session123", 1, "user", "Hello world")

        # Check file was created
        log_path = Path(temp_workspace) / ".sessions" / "session123.jsonl"
        assert log_path.exists()

        # Check content
        with open(log_path) as f:
            event = json.loads(f.readline())

        assert event["session_id"] == "session123"
        assert event["turn_id"] == 1
        assert event["type"] == "user"
        assert event["content"] == "Hello world"

    def test_log_user_message(self, temp_workspace):
        """Test logging user message."""
        logger = EventLogger(temp_workspace)
        logger.log_user_message("session123", 1, "Test message")

        events = logger.get_session_events("session123")
        assert len(events) == 1
        assert events[0]["type"] == "user"
        assert events[0]["content"] == "Test message"

    def test_log_assistant_message(self, temp_workspace):
        """Test logging assistant message."""
        logger = EventLogger(temp_workspace)
        logger.log_assistant_message("session123", 1, "Response")

        events = logger.get_session_events("session123")
        assert len(events) == 1
        assert events[0]["type"] == "assistant"
        assert events[0]["content"] == "Response"

    def test_log_tool_call(self, temp_workspace):
        """Test logging tool call."""
        logger = EventLogger(temp_workspace)
        logger.log_tool_call(
            "session123", 1, "exec", {"command": "ls"}, "file listing"
        )

        events = logger.get_session_events("session123")
        assert len(events) == 1
        assert events[0]["type"] == "tool"
        assert events[0]["tool_name"] == "exec"
        assert events[0]["tool_args"] == {"command": "ls"}
        assert events[0]["tool_result"] == "file listing"

    def test_get_turn_events(self, temp_workspace):
        """Test getting events for a specific turn."""
        logger = EventLogger(temp_workspace)
        logger.log_user_message("session123", 1, "Hello")
        logger.log_assistant_message("session123", 1, "Hi there")
        logger.log_user_message("session123", 2, "Second turn")

        turn1_events = logger.get_turn_events("session123", 1)
        assert len(turn1_events) == 2

        turn2_events = logger.get_turn_events("session123", 2)
        assert len(turn2_events) == 1


class TestMemoryOpsValidators:
    """Tests for MemoryOps validation."""

    def test_valid_add_op(self):
        """Test valid ADD operation."""
        ops = [
            {
                "op": "ADD",
                "type": "fact",
                "content": "User prefers dark mode",
                "confidence": 0.8,
                "tags": ["preference", "ui"],
            }
        ]
        is_valid, error = validate_memory_ops(ops)
        assert is_valid
        assert error == ""

    def test_valid_noop(self):
        """Test valid NOOP."""
        ops = [{"op": "NOOP"}]
        is_valid, error = validate_memory_ops(ops)
        assert is_valid
        assert error == ""

    def test_invalid_op_type(self):
        """Test invalid operation type."""
        ops = [{"op": "INVALID", "type": "fact", "content": "test"}]
        is_valid, error = validate_memory_ops(ops)
        assert not is_valid
        assert "invalid op" in error.lower()

    def test_invalid_memory_type(self):
        """Test invalid memory type."""
        ops = [{"op": "ADD", "type": "invalid_type", "content": "test"}]
        is_valid, error = validate_memory_ops(ops)
        assert not is_valid
        assert "invalid type" in error.lower()

    def test_content_too_long(self):
        """Test content exceeding max length."""
        ops = [
            {
                "op": "ADD",
                "type": "fact",
                "content": "x" * 700,  # Max is 600
            }
        ]
        is_valid, error = validate_memory_ops(ops)
        assert not is_valid
        assert "too long" in error.lower()

    def test_too_many_ops(self):
        """Test exceeding max ops count."""
        ops = [{"op": "ADD", "type": "fact", "content": f"test{i}"} for i in range(10)]
        is_valid, error = validate_memory_ops(ops)
        assert not is_valid
        assert "too many ops" in error.lower()

    def test_confidence_out_of_range(self):
        """Test confidence out of 0-1 range."""
        ops = [{"op": "ADD", "type": "fact", "content": "test", "confidence": 1.5}]
        is_valid, error = validate_memory_ops(ops)
        assert not is_valid
        assert "confidence" in error.lower()

    def test_sanitize_ops(self):
        """Test sanitizing ops."""
        ops = [
            {
                "op": "ADD",
                "type": "fact",
                "content": "x" * 1000,  # Too long
                "confidence": 0.8,
                "tags": ["a", "b", "c", "d", "e", "f"],  # Too many
            }
        ]
        sanitized = sanitize_memory_ops(ops)

        assert len(sanitized) == 1
        assert len(sanitized[0]["content"]) == 600  # Truncated
        assert len(sanitized[0]["tags"]) == 5  # Limited


class TestMemoryUpdateManager:
    """Tests for MemoryUpdateManager."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "memory").mkdir()
            yield str(workspace)

    def test_apply_ops_to_daily_note(self, temp_workspace):
        """Test applying ops to daily note."""
        from src.memory.update_manager import MemoryUpdateManager

        manager = MemoryUpdateManager(temp_workspace)

        ops = [
            {
                "op": "ADD",
                "type": "fact",
                "content": "User prefers dark mode",
                "confidence": 0.8,
                "tags": ["preference"],
            }
        ]

        manager._apply_ops("session123", 1, ops)

        # Check daily note was created
        today = datetime.now().strftime("%Y-%m-%d")
        daily_note = Path(temp_workspace) / "memory" / f"{today}.md"
        assert daily_note.exists()

        content = daily_note.read_text()
        assert "Turn 1" in content
        assert "User prefers dark mode" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
