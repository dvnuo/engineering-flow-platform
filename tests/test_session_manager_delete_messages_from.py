import asyncio

import pytest

from src.sessions.manager import SessionManager
from src.sessions import manager as manager_module


@pytest.mark.asyncio
async def test_delete_messages_from_deletes_target_and_following_messages(monkeypatch):
    session_id = "s-delete-from"
    session_data = {
        "history": [
            {"id": "u1", "role": "user", "content": "first"},
            {"id": "a1", "role": "assistant", "content": "first answer"},
            {"id": "u2", "role": "user", "content": "second"},
            {"id": "a2", "role": "assistant", "content": "second answer"},
        ],
        "metadata": {},
        "channel": "",
    }

    manager = SessionManager()
    manager.auto_save = True
    manager.persistence_enabled = True

    save_calls = []

    async def _fake_save_session(**kwargs):
        save_calls.append(kwargs)
        return True

    monkeypatch.setattr(manager, "get_session", lambda _sid: asyncio.sleep(0, result=session_data))
    monkeypatch.setattr(manager_module.session_persistence, "save_session", _fake_save_session)

    deleted_count = await manager.delete_messages_from(session_id, "u2", wait_for_save=True)
    assert deleted_count == 2
    assert [msg["id"] for msg in session_data["history"]] == ["u1", "a1"]
    assert len(save_calls) == 1
    assert [msg["id"] for msg in save_calls[0]["messages"]] == ["u1", "a1"]

    deleted_missing = await manager.delete_messages_from(session_id, "missing")
    assert deleted_missing == 0
    assert [msg["id"] for msg in session_data["history"]] == ["u1", "a1"]
    assert len(save_calls) == 1
