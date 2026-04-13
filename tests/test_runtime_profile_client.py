import pytest

from src.runtime import runtime_profile_client


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return str(self._payload)


class _RequestContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeClientSession:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        return _RequestContext(self._response)


@pytest.mark.asyncio
async def test_bootstrap_runtime_profile_apply(monkeypatch):
    monkeypatch.setattr(runtime_profile_client, "get_portal_internal_base_url", lambda: "http://portal")
    monkeypatch.setattr(runtime_profile_client, "get_portal_agent_id", lambda: "agent-1")
    monkeypatch.setattr(runtime_profile_client, "build_portal_internal_api_headers", lambda include_content_type=False: {})
    monkeypatch.setattr(
        runtime_profile_client,
        "ClientSession",
        lambda headers=None: _FakeClientSession(
            _FakeResponse(
                200,
                {
                    "runtime_profile_id": "rp_1",
                    "runtime_profile_context": {
                        "runtime_profile_id": "rp_1",
                        "name": "Default Runtime",
                        "revision": 3,
                        "managed_sections": ["llm", "proxy", "jira", "confluence", "github", "git", "debug"],
                        "config": {"jira": {"enabled": True}},
                        "source": "portal.runtime_profile",
                    },
                },
            )
        ),
    )

    captured = {}
    monkeypatch.setattr(
        runtime_profile_client.config,
        "set_managed_overlay",
        lambda rp_id, revision, overlay: captured.update(
            {"runtime_profile_id": rp_id, "revision": revision, "overlay": overlay}
        ) or ["jira"],
    )

    ok = await runtime_profile_client.bootstrap_runtime_profile_from_portal()
    assert ok is True
    assert captured["runtime_profile_id"] == "rp_1"
    assert captured["revision"] == 3
    assert captured["overlay"] == {"jira": {"enabled": True}}


@pytest.mark.asyncio
async def test_bootstrap_runtime_profile_clear(monkeypatch):
    monkeypatch.setattr(runtime_profile_client, "get_portal_internal_base_url", lambda: "http://portal")
    monkeypatch.setattr(runtime_profile_client, "get_portal_agent_id", lambda: "agent-1")
    monkeypatch.setattr(runtime_profile_client, "build_portal_internal_api_headers", lambda include_content_type=False: {})
    monkeypatch.setattr(
        runtime_profile_client,
        "ClientSession",
        lambda headers=None: _FakeClientSession(
            _FakeResponse(
                200,
                {
                    "runtime_profile_id": None,
                    "revision": None,
                    "runtime_profile_context": None,
                },
            )
        ),
    )

    cleared = {"value": False}
    monkeypatch.setattr(runtime_profile_client.config, "clear_managed_overlay", lambda: cleared.update({"value": True}))

    ok = await runtime_profile_client.bootstrap_runtime_profile_from_portal()
    assert ok is True
    assert cleared["value"] is True


@pytest.mark.asyncio
async def test_bootstrap_runtime_profile_failure_is_non_fatal(monkeypatch):
    monkeypatch.setattr(runtime_profile_client, "get_portal_internal_base_url", lambda: "http://portal")
    monkeypatch.setattr(runtime_profile_client, "get_portal_agent_id", lambda: "agent-1")

    class _FailingSession:
        async def __aenter__(self):
            raise RuntimeError("boom")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(runtime_profile_client, "ClientSession", lambda headers=None: _FailingSession())

    ok = await runtime_profile_client.bootstrap_runtime_profile_from_portal()
    assert ok is False


@pytest.mark.asyncio
async def test_bootstrap_runtime_profile_apply_legacy_direct_config_shape(monkeypatch):
    monkeypatch.setattr(runtime_profile_client, "get_portal_internal_base_url", lambda: "http://portal")
    monkeypatch.setattr(runtime_profile_client, "get_portal_agent_id", lambda: "agent-1")
    monkeypatch.setattr(runtime_profile_client, "build_portal_internal_api_headers", lambda include_content_type=False: {})
    monkeypatch.setattr(
        runtime_profile_client,
        "ClientSession",
        lambda headers=None: _FakeClientSession(
            _FakeResponse(
                200,
                {
                    "runtime_profile_id": "rp_legacy",
                    "revision": 2,
                    "runtime_profile_context": {"jira": {"enabled": True}},
                },
            )
        ),
    )

    captured = {}
    monkeypatch.setattr(
        runtime_profile_client.config,
        "set_managed_overlay",
        lambda rp_id, revision, overlay: captured.update(
            {"runtime_profile_id": rp_id, "revision": revision, "overlay": overlay}
        ) or ["jira"],
    )

    ok = await runtime_profile_client.bootstrap_runtime_profile_from_portal()
    assert ok is True
    assert captured == {
        "runtime_profile_id": "rp_legacy",
        "revision": 2,
        "overlay": {"jira": {"enabled": True}},
    }
