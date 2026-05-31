import src.gateway.server as gateway_server
import pytest


@pytest.mark.asyncio
async def test_handle_jira_message_uses_runtime_chat(monkeypatch):
    captured = {}

    async def _fake_run_runtime_chat(**kwargs):
        captured.update(kwargs)
        return {"response": "jira-ok"}

    monkeypatch.setattr(gateway_server, "run_runtime_chat", _fake_run_runtime_chat)

    response = await gateway_server.handle_jira_message(
        message="hello",
        session_id="jira-session",
        user_name="jira-user",
        issue_key="ENG-1",
    )

    assert response == "jira-ok"
    assert captured["message"] == "hello"
    assert captured["session_id"] == "jira-session"
    assert captured["user_name"] == "jira-user"
