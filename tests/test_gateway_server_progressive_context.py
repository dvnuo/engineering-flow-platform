import pytest

from src.gateway.server import Gateway, handle_jira_message


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_handle_jira_message_uses_run_chat_execution(monkeypatch):
    captured = {}

    async def _fake_run_chat_execution(**kwargs):
        captured.update(kwargs)
        return {"response": "jira-ok"}

    monkeypatch.setattr("src.gateway.server.run_chat_execution", _fake_run_chat_execution)

    response = await handle_jira_message(
        message="hello",
        session_id="jira-session",
        user_name="jira-user",
        issue_key="ENG-1",
    )

    assert response == "jira-ok"
    assert captured["message"] == "hello"
    assert captured["session_id"] == "jira-session"
    assert captured["user_name"] == "jira-user"


@pytest.mark.asyncio
async def test_handle_test_message_uses_run_chat_execution(monkeypatch):
    captured = {}

    async def _fake_run_chat_execution(**kwargs):
        captured.update(kwargs)
        return {"response": "test-ok", "usage": {"prompt_tokens": 1}}

    monkeypatch.setattr("src.gateway.server.run_chat_execution", _fake_run_chat_execution)

    fake_request = _FakeRequest({
        "message": "ping",
        "session_id": "http-session",
        "reasoning_replay": True,
    })

    response = await Gateway.handle_test_message(object(), fake_request)
    payload = response.text

    assert response.status == 200
    assert "test-ok" in payload
    assert "http-session" in payload
    assert captured["message"] == "ping"
    assert captured["session_id"] == "http-session"
    assert captured["user_name"] == "http-tester"
    assert captured["reasoning_replay"] is True
