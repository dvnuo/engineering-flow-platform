"""Tests for SessionManager."""

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from src.efp_runtime.session.gateway_facade import RuntimeSessionManager


@pytest.fixture
def fresh_session_manager():
    """Create a fresh session manager for isolation."""
    manager = RuntimeSessionManager()
    yield manager


@pytest.fixture
def temp_session_id():
    """Generate a unique session ID for each test."""
    import uuid
    return f"test_session_{uuid.uuid4().hex[:8]}"


class TestSessionManagerBasic:
    """Basic session management tests."""

    @pytest.mark.asyncio
    async def test_add_message(self, fresh_session_manager, temp_session_id):
        """Test adding messages to session."""
        await fresh_session_manager.clear_history(temp_session_id)
        await fresh_session_manager.add_message(temp_session_id, "user", "Hello")
        history = await fresh_session_manager.get_history(temp_session_id)
        assert len(history) == 1
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_get_history(self, fresh_session_manager, temp_session_id):
        """Test getting conversation history."""
        await fresh_session_manager.clear_history(temp_session_id)
        await fresh_session_manager.add_message(temp_session_id, "user", "Hello")
        await fresh_session_manager.add_message(temp_session_id, "assistant", "Hi there!")
        history = await fresh_session_manager.get_history(temp_session_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_clear_history(self, fresh_session_manager, temp_session_id):
        """Test clearing session history."""
        await fresh_session_manager.add_message(temp_session_id, "user", "Hello")
        await fresh_session_manager.clear_history(temp_session_id)
        history = await fresh_session_manager.get_history(temp_session_id)
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_list_sessions(self, fresh_session_manager, temp_session_id):
        """Test listing active sessions."""
        await fresh_session_manager.clear_history(temp_session_id)
        await fresh_session_manager.add_message(temp_session_id, "user", "test")
        sessions = await fresh_session_manager.list_sessions()
        assert temp_session_id in sessions


class TestSessionManagerIsolation:
    """Session isolation tests."""

    @pytest.mark.asyncio
    async def test_session_isolation(self, fresh_session_manager):
        """Test that different sessions have isolated histories."""
        import uuid
        session_a = f"isolation_a_{uuid.uuid4().hex[:8]}"
        session_b = f"isolation_b_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_a)
        await fresh_session_manager.clear_history(session_b)
        await fresh_session_manager.add_message(session_a, "user", "secret A")
        await fresh_session_manager.add_message(session_b, "user", "secret B")
        
        history_a = await fresh_session_manager.get_history(session_a)
        history_b = await fresh_session_manager.get_history(session_b)
        
        assert history_a[0]["content"] == "secret A"
        assert history_b[0]["content"] == "secret B"

    @pytest.mark.asyncio
    async def test_session_with_prefix(self, fresh_session_manager):
        """Test sessions with ID prefixes."""
        import uuid
        prefix = f"test_{uuid.uuid4().hex[:8]}"
        session_id = f"{prefix}_session"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "test message")
        
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1


class TestSessionManagerHistory:
    """History management tests."""

    @pytest.mark.asyncio
    async def test_history_limit(self, fresh_session_manager):
        """Test history size limit."""
        import uuid
        session_id = f"limit_test_{uuid.uuid4().hex[:8]}"
        fresh_session_manager.max_history = 5
        
        await fresh_session_manager.clear_history(session_id)
        for i in range(10):
            await fresh_session_manager.add_message(session_id, "user", f"user_{i}")
            await fresh_session_manager.add_message(session_id, "assistant", f"assistant_{i}")
        
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 10

    @pytest.mark.asyncio
    async def test_history_timestamps(self, fresh_session_manager):
        """Test that history includes timestamps."""
        import uuid
        session_id = f"ts_test_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "hello")
        history = await fresh_session_manager.get_history(session_id)
        
        assert len(history) == 1
        assert "timestamp" in history[0]


class TestSessionManagerInfo:
    """Session info tests."""

    @pytest.mark.asyncio
    async def test_get_session_info(self, fresh_session_manager):
        """Test getting session information."""
        import uuid
        session_id = f"info_test_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "test")
        info = await fresh_session_manager.get_session_info(session_id)
        
        assert "history_count" in info
        assert info["history_count"] == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent_session_info(self, fresh_session_manager):
        """Test getting info for nonexistent session."""
        import uuid
        session_id = f"nonexistent_{uuid.uuid4().hex[:8]}"
        
        info = await fresh_session_manager.get_session_info(session_id)
        assert info is None

    @pytest.mark.asyncio
    async def test_get_existing_session_returns_none_for_missing_session(self, fresh_session_manager):
        import uuid

        session_id = f"missing_existing_{uuid.uuid4().hex[:8]}"
        existing = await fresh_session_manager.get_existing_session(session_id)
        assert existing is None

    @pytest.mark.asyncio
    async def test_rename_session_persists_custom_session_name(self, fresh_session_manager):
        import uuid

        session_id = f"rename_meta_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.get_session(session_id)

        renamed = await fresh_session_manager.rename_session(session_id, "  Renamed Session  ")
        restored = await fresh_session_manager.get_session(session_id)

        assert renamed == "Renamed Session"
        assert restored["metadata"]["custom_session_name"] == "Renamed Session"

    @pytest.mark.asyncio
    async def test_delete_session_removes_session_from_memory_and_returns_true(self, fresh_session_manager):
        import uuid

        session_id = f"delete_existing_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.get_session(session_id)
        assert session_id in fresh_session_manager.sessions

        fresh_session_manager.persistence_enabled = False
        deleted = await fresh_session_manager.delete_session(session_id)

        assert deleted is True
        assert session_id not in fresh_session_manager.sessions
        assert await fresh_session_manager.get_existing_session(session_id) is None

    @pytest.mark.asyncio
    async def test_delete_session_returns_false_when_missing(self, fresh_session_manager):
        import uuid

        session_id = f"delete_missing_{uuid.uuid4().hex[:8]}"
        fresh_session_manager.persistence_enabled = False
        deleted = await fresh_session_manager.delete_session(session_id)
        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_session_removes_chatlog_file_and_returns_true(self, fresh_session_manager, tmp_path):
        import uuid

        session_id = f"delete_chatlog_{uuid.uuid4().hex[:8]}"
        chatlogs_dir = fresh_session_manager.artifacts.storage_dir / "chatlogs"
        chatlogs_dir.mkdir(parents=True, exist_ok=True)
        chatlog_file = chatlogs_dir / f"{session_id}.json"
        chatlog_file.write_text('{"session_id":"x"}', encoding="utf-8")

        fresh_session_manager.persistence_enabled = False
        deleted = await fresh_session_manager.delete_session(session_id)

        assert deleted is True
        assert not chatlog_file.exists()

    @pytest.mark.asyncio
    async def test_delete_session_calls_file_context_cleanup(self, fresh_session_manager, monkeypatch):
        import uuid
        from src.hooks.file_context.storage import storage as file_context_storage

        session_id = f"delete_file_context_{uuid.uuid4().hex[:8]}"
        called = {"session_id": None}

        def _fake_delete_session(sid):
            called["session_id"] = sid
            return 2

        monkeypatch.setattr(file_context_storage, "delete_session", _fake_delete_session)
        fresh_session_manager.persistence_enabled = False

        deleted = await fresh_session_manager.delete_session(session_id)

        assert deleted is True
        assert called["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_delete_session_returns_true_when_only_orphan_artifacts_exist(self, fresh_session_manager, tmp_path):
        import uuid
        session_id = f"orphan_artifacts_{uuid.uuid4().hex[:8]}"
        chatlogs_dir = fresh_session_manager.artifacts.storage_dir / "chatlogs"
        chatlogs_dir.mkdir(parents=True, exist_ok=True)
        orphan_chatlog = chatlogs_dir / f"{session_id}.json"
        orphan_chatlog.write_text("{}", encoding="utf-8")

        fresh_session_manager.persistence_enabled = False
        deleted = await fresh_session_manager.delete_session(session_id)

        assert deleted is True
        assert not orphan_chatlog.exists()

    @pytest.mark.asyncio
    async def test_set_last_execution_id_updates_in_memory_and_schedules_metadata_persist(self, fresh_session_manager):
        import uuid

        session_id = f"exec_meta_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.get_session(session_id)
        await fresh_session_manager.set_last_execution_id(session_id, "req-123")
        session = await fresh_session_manager.get_session(session_id)

        assert session["metadata"]["last_execution_id"] == "req-123"
        assert session["updated_at"]

    @pytest.mark.asyncio
    async def test_get_session_returns_detached_snapshot(self, fresh_session_manager):
        import uuid

        session_id = f"snapshot_meta_{uuid.uuid4().hex[:8]}"
        session = await fresh_session_manager.get_session(session_id)
        session["channel"] = "chat"
        session["history"] = [{"id": "m1", "content": "before"}]
        session["metadata"] = {"pending_delegations": [{"delegation_id": "d1"}]}

        await fresh_session_manager.set_last_execution_id(session_id, "req-1")
        restored = await fresh_session_manager.get_session(session_id)

        assert restored["channel"] == ""
        assert restored["history"] == []
        assert restored["metadata"]["last_execution_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_add_pending_delegation_ignores_corrupted_non_dict_entries(self, fresh_session_manager):
        import uuid

        session_id = f"pending_corrupt_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.replace_metadata_keys(
            session_id,
            {"pending_delegations": [None, "bad", {"delegation_id": "d1", "x": 1}, 123]},
        )

        await fresh_session_manager.add_pending_delegation(session_id, {"delegation_id": "d2", "y": 2})

        session = await fresh_session_manager.get_session(session_id)
        pending = session["metadata"]["pending_delegations"]
        assert all(isinstance(item, dict) for item in pending)
        assert {"delegation_id": "d1", "x": 1} in pending
        assert {"delegation_id": "d2", "y": 2} in pending

    @pytest.mark.asyncio
    async def test_add_pending_delegation_replaces_existing_same_delegation_id(self, fresh_session_manager):
        import uuid

        session_id = f"pending_replace_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.replace_metadata_keys(
            session_id,
            {"pending_delegations": [None, {"delegation_id": "d1", "x": 1}, {"delegation_id": "d2", "old": True}]},
        )

        await fresh_session_manager.add_pending_delegation(session_id, {"delegation_id": "d2", "y": 2})

        session = await fresh_session_manager.get_session(session_id)
        pending = session["metadata"]["pending_delegations"]
        assert all(isinstance(item, dict) for item in pending)
        d2_items = [item for item in pending if item.get("delegation_id") == "d2"]
        assert len(d2_items) == 1
        assert d2_items[0] == {"delegation_id": "d2", "y": 2}

    @pytest.mark.asyncio
    async def test_metadata_update_surfaces_store_failure(self, fresh_session_manager, monkeypatch, caplog):
        import uuid

        session_id = f"persist_fail_{uuid.uuid4().hex[:8]}"

        def _fake_update_session(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(fresh_session_manager.store, "update_session", _fake_update_session)
        with pytest.raises(RuntimeError, match="boom"):
            await fresh_session_manager.set_last_execution_id(session_id, "req-1")

        assert "Failed to persist metadata-only session update" not in caplog.text

    @pytest.mark.asyncio
    async def test_add_message_still_uses_normal_persistence_path(self, fresh_session_manager):
        import uuid

        session_id = f"persist_msg_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.add_message(session_id, "user", "hello", wait_for_save=True)
        restored = await fresh_session_manager.get_session(session_id)

        assert restored["history"][0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_active_skill_session_roundtrip(self, fresh_session_manager):
        """Active skill session should persist via metadata-compatible path."""
        import uuid

        session_id = f"skill_{uuid.uuid4().hex[:8]}"
        await fresh_session_manager.clear_history(session_id)
        skill_state = {"skill_name": "demo", "step": 2}
        await fresh_session_manager.set_active_skill_session(session_id, skill_state)

        restored = await fresh_session_manager.get_active_skill_session(session_id)
        assert restored == skill_state


class TestSessionManagerEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_empty_content(self, fresh_session_manager):
        """Test handling empty content."""
        import uuid
        session_id = f"empty_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "")
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_special_characters_content(self, fresh_session_manager):
        """Test handling special characters."""
        import uuid
        session_id = f"special_{uuid.uuid4().hex[:8]}"
        special_content = "Hello! Global 中文 Celebration @#$%"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", special_content)
        history = await fresh_session_manager.get_history(session_id)
        
        assert history[0]["content"] == special_content

    @pytest.mark.asyncio
    async def test_long_content(self, fresh_session_manager):
        """Test handling long content."""
        import uuid
        session_id = f"long_{uuid.uuid4().hex[:8]}"
        long_content = ("word " * 1000).strip()
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", long_content)
        history = await fresh_session_manager.get_history(session_id)
        
        assert history[0]["content"] == long_content

    @pytest.mark.asyncio
    async def test_multiple_roles(self, fresh_session_manager):
        """Test messages with multiple roles."""
        import uuid
        session_id = f"roles_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "Hello")
        await fresh_session_manager.add_message(session_id, "assistant", "Hi there!")
        await fresh_session_manager.add_message(session_id, "user", "How are you?")
        await fresh_session_manager.add_message(session_id, "assistant", "I am doing well, thanks!")
        
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 4


class TestSessionManagerEditDelete:
    """Tests for edit/delete functionality."""

    @pytest.mark.asyncio
    async def test_edit_message(self, fresh_session_manager):
        """Test editing an existing message."""
        import uuid
        session_id = f"edit_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        msg_id = await fresh_session_manager.add_message(session_id, "user", "Original content")
        
        # Edit the message
        result = await fresh_session_manager.edit_message(session_id, msg_id, "Edited content")
        
        assert result is True
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1
        assert history[0]["content"] == "Edited content"
        assert history[0]["id"] == msg_id

    @pytest.mark.asyncio
    async def test_edit_message_not_found(self, fresh_session_manager):
        """Test editing a non-existent message returns False."""
        import uuid
        session_id = f"edit_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "Hello")
        
        result = await fresh_session_manager.edit_message(session_id, "non_existent_id", "New content")
        
        assert result is False
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1
        assert history[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_delete_message(self, fresh_session_manager):
        """Test deleting a specific message by ID."""
        import uuid
        session_id = f"delete_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        msg_id1 = await fresh_session_manager.add_message(session_id, "user", "First")
        msg_id2 = await fresh_session_manager.add_message(session_id, "user", "Second")
        
        # Delete the first message
        result = await fresh_session_manager.delete_message(session_id, msg_id1)
        
        assert result is True
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1
        assert history[0]["id"] == msg_id2

    @pytest.mark.asyncio
    async def test_delete_message_not_found(self, fresh_session_manager):
        """Test deleting a non-existent message returns False."""
        import uuid
        session_id = f"delete_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "Hello")
        
        result = await fresh_session_manager.delete_message(session_id, "non_existent_id")
        
        assert result is False
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_delete_messages_after(self, fresh_session_manager):
        """Test deleting all messages after a specific message."""
        import uuid
        session_id = f"truncate_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        msg_id1 = await fresh_session_manager.add_message(session_id, "user", "First")
        msg_id2 = await fresh_session_manager.add_message(session_id, "user", "Second")
        msg_id3 = await fresh_session_manager.add_message(session_id, "user", "Third")
        
        # Delete all messages after the first one
        deleted_count = await fresh_session_manager.delete_messages_after(session_id, msg_id1)
        
        assert deleted_count == 2
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1
        assert history[0]["id"] == msg_id1

    @pytest.mark.asyncio
    async def test_delete_messages_after_not_found(self, fresh_session_manager):
        """Test deleting after a non-existent message returns 0."""
        import uuid
        session_id = f"truncate_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        await fresh_session_manager.add_message(session_id, "user", "First")
        
        deleted_count = await fresh_session_manager.delete_messages_after(session_id, "non_existent")
        
        assert deleted_count == 0
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_delete_last_message(self, fresh_session_manager):
        """Test deleting the last message in conversation."""
        import uuid
        session_id = f"delete_last_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        msg_id = await fresh_session_manager.add_message(session_id, "user", "Only message")
        
        deleted_count = await fresh_session_manager.delete_messages_after(session_id, msg_id)
        
        assert deleted_count == 0
        history = await fresh_session_manager.get_history(session_id)
        assert len(history) == 1

    @pytest.mark.asyncio
    async def test_message_id_in_history(self, fresh_session_manager):
        """Test that added messages have an ID field."""
        import uuid
        session_id = f"id_{uuid.uuid4().hex[:8]}"
        
        await fresh_session_manager.clear_history(session_id)
        msg_id = await fresh_session_manager.add_message(session_id, "user", "Hello")
        
        history = await fresh_session_manager.get_history(session_id)
        assert "id" in history[0]
        assert history[0]["id"] == msg_id
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0
