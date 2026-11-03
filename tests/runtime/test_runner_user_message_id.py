"""A run reports the id of the stored user turn it answered."""

from __future__ import annotations

import pytest

from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.session.models import MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


@pytest.mark.asyncio
async def test_run_reports_the_stored_user_message_id():
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=ScriptedLLMProvider([{"content": "Done."}]),
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-ids", user_text="Go.")

    assert result.status == LoopStatus.COMPLETED
    user_messages = [message for message in store.read_history("session-ids") if message.role is MessageRole.USER]
    assert user_messages
    assert result.user_message_id == user_messages[-1].message_id
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.message_id
    assert result.final_assistant_message.message_id != result.user_message_id


@pytest.mark.asyncio
async def test_a_second_turn_reports_its_own_user_message():
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=ScriptedLLMProvider([{"content": "First."}, {"content": "Second."}]),
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    first = await runner.run(session_id="session-two", user_text="One.")
    second = await runner.run(session_id="session-two", user_text="Two.")

    user_ids = [message.message_id for message in store.read_history("session-two") if message.role is MessageRole.USER]
    assert user_ids == [first.user_message_id, second.user_message_id]
    assert first.user_message_id != second.user_message_id
