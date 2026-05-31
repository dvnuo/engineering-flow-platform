import pytest

from src.efp_runtime.session.gateway_facade import RuntimeSessionManager


@pytest.mark.asyncio
async def test_delete_messages_from_deletes_target_and_following_messages():
    session_id = "s-delete-from"
    manager = RuntimeSessionManager()
    await manager.clear_history(session_id)

    u1 = await manager.add_message(session_id, "user", "first")
    a1 = await manager.add_message(session_id, "assistant", "first answer")
    u2 = await manager.add_message(session_id, "user", "second")
    await manager.add_message(session_id, "assistant", "second answer")

    deleted_count = await manager.delete_messages_from(session_id, u2, wait_for_save=True)
    assert deleted_count == 2

    history = await manager.get_history(session_id)
    assert [msg["id"] for msg in history] == [u1, a1]
    assert [msg["content"] for msg in history] == ["first", "first answer"]

    deleted_missing = await manager.delete_messages_from(session_id, "missing")
    assert deleted_missing == 0
    assert [msg["content"] for msg in await manager.get_history(session_id)] == ["first", "first answer"]
