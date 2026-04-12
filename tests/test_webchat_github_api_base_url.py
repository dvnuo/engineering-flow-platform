import json

import pytest

from src.gateway import webchat


class DummyRequest:
    def __init__(self, data=None):
        self._data = data or {}

    async def json(self):
        return self._data


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_base,expected_prefix",
    [
        ("", "https://api.github.com/copilot/token_verification"),
        ("https://github.com", "https://api.github.com/copilot/token_verification"),
        (
            "https://github.company.com",
            "https://github.company.com/api/v3/copilot/token_verification",
        ),
        (
            "https://github.company.com/api/v3",
            "https://github.company.com/api/v3/copilot/token_verification",
        ),
    ],
)
async def test_copilot_auth_start_uses_normalized_github_base(monkeypatch, configured_base, expected_prefix):
    captured = {}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            return FakeResponse(
                201,
                {
                    "device_code": "dev-code",
                    "user_code": "user-code",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete": "https://github.com/login/device?user_code=user-code",
                    "expires_in": 600,
                    "interval": 5,
                },
            )

    monkeypatch.setattr(webchat.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(webchat.global_config, "_config", {"github": {"base_url": configured_base}}, raising=False)

    response = await webchat.api_copilot_auth_start(DummyRequest())
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["auth_id"]
    assert captured["url"] == expected_prefix


@pytest.mark.asyncio
async def test_copilot_auth_check_uses_normalized_enterprise_base(monkeypatch):
    captured = {}
    webchat._pending_authorizations["auth-1"] = {
        "expires_at": 9999999999,
        "status": "pending",
        "token": None,
    }

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            return FakeResponse(400, {"error": "authorization_pending"})

    monkeypatch.setattr(webchat.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(
        webchat.global_config,
        "_config",
        {"github": {"base_url": "https://github.company.com"}},
        raising=False,
    )

    response = await webchat.api_copilot_auth_check(
        DummyRequest({"auth_id": "auth-1", "device_code": "device-code"})
    )
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["status"] == "pending"
    assert captured["url"] == "https://github.company.com/api/v3/copilot/token_verification"
