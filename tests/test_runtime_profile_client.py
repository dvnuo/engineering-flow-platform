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


def test_extract_runtime_profile_overlay_structured_shape():
    payload = {
        "runtime_profile_id": "rp_1",
        "runtime_profile_context": {
            "runtime_profile_id": "rp_1",
            "name": "Default Runtime",
            "revision": 3,
            "managed_sections": ["llm", "proxy", "jira", "confluence", "github", "git", "debug"],
            "config": {"jira": {"enabled": True}},
            "source": "portal.runtime_profile",
        },
    }

    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id == "rp_1"
    assert revision == 3
    assert overlay_config == {"jira": {"enabled": True}}
    assert clear_flag is False


def test_extract_runtime_profile_overlay_null_context_means_clear():
    payload = {"runtime_profile_id": None, "runtime_profile_context": None}
    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id is None
    assert revision is None
    assert overlay_config is None
    assert clear_flag is True


def test_extract_runtime_profile_overlay_malformed_structured_shape_returns_invalid():
    payload = {
        "runtime_profile_id": "rp_bad",
        "runtime_profile_context": {
            "runtime_profile_id": "rp_bad",
            "revision": 5,
            "config": "not-a-dict",
        },
    }
    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert (runtime_profile_id, revision, overlay_config, clear_flag) == (None, None, None, False)


def test_extract_runtime_profile_overlay_uses_nested_profile_id_when_top_level_missing():
    payload = {
        "runtime_profile_context": {
            "runtime_profile_id": "rp_nested",
            "revision": 4,
            "config": {"proxy": {"enabled": True}},
        }
    }
    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id == "rp_nested"
    assert revision == 4
    assert overlay_config == {"proxy": {"enabled": True}}
    assert clear_flag is False


def test_extract_runtime_profile_overlay_prefers_nested_revision_over_top_level():
    payload = {
        "runtime_profile_id": "rp_1",
        "revision": 99,
        "runtime_profile_context": {
            "runtime_profile_id": "rp_1",
            "revision": 3,
            "config": {"jira": {"enabled": True}},
        },
    }
    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id == "rp_1"
    assert revision == 3
    assert overlay_config == {"jira": {"enabled": True}}
    assert clear_flag is False


def test_extract_runtime_profile_overlay_ignores_owner_and_default_metadata():
    payload = {
        "runtime_profile_id": "rp_user_1",
        "runtime_profile_context": {
            "runtime_profile_id": "rp_user_1",
            "name": "Default Runtime",
            "revision": 7,
            "owner_user_id": 42,
            "is_default": True,
            "bound_agent_count": 3,
            "managed_sections": ["llm", "proxy", "jira", "confluence", "github", "git", "debug"],
            "config": {"llm": {"provider": "openai"}},
            "source": "portal.runtime_profile",
        },
    }

    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id == "rp_user_1"
    assert revision == 7
    assert overlay_config == {"llm": {"provider": "openai"}}
    assert clear_flag is False


def test_extract_runtime_profile_overlay_keeps_working_when_portal_adds_more_non_config_fields():
    payload = {
        "runtime_profile_id": "rp_future_meta",
        "runtime_profile_context": {
            "runtime_profile_id": "rp_future_meta",
            "revision": 11,
            "display_label": "Team A / Default",
            "ui_badges": ["default", "user-scoped"],
            "description": "Portal UI-only metadata should not affect runtime overlay parsing.",
            "config": {"github": {"enabled": True}},
        },
    }

    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id == "rp_future_meta"
    assert revision == 11
    assert overlay_config == {"github": {"enabled": True}}
    assert clear_flag is False


def test_runtime_profile_client_ignores_additional_portal_control_plane_metadata():
    payload = {
        "runtime_profile_context": {
            "config": {
                "llm": {"provider": "openai", "model": "gpt-4.1"},
                "tools": {"bash": {"enabled": True}},
            },
            "revision": 7,
        },
        "owner_user_id": 42,
        "display_label": "owner-managed profile",
        "ui_badges": ["self-service"],
        "read_only": False,
        "binding_count": 2,
        "subscription_count": 3,
    }

    runtime_profile_id, revision, overlay_config, clear_flag = runtime_profile_client._extract_runtime_profile_overlay(payload)
    assert runtime_profile_id is None
    assert revision == 7
    assert overlay_config == {
        "llm": {"provider": "openai", "model": "gpt-4.1"},
        "tools": {"bash": {"enabled": True}},
    }
    assert clear_flag is False
    assert "owner_user_id" not in overlay_config
    assert "display_label" not in overlay_config
    assert "ui_badges" not in overlay_config
    assert "binding_count" not in overlay_config
    assert "subscription_count" not in overlay_config


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
async def test_bootstrap_runtime_profile_apply_ignores_extra_portal_metadata(monkeypatch):
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
                    "runtime_profile_id": "rp_user_1",
                    "runtime_profile_context": {
                        "runtime_profile_id": "rp_user_1",
                        "revision": 8,
                        "owner_user_id": 42,
                        "is_default": True,
                        "bound_agent_count": 3,
                        "managed_sections": ["llm", "proxy", "jira", "confluence", "github", "git", "debug"],
                        "display_label": "Personal Default",
                        "ui_badges": ["default", "personal"],
                        "config": {"llm": {"provider": "openai"}},
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
        ) or ["llm"],
    )

    ok = await runtime_profile_client.bootstrap_runtime_profile_from_portal()
    assert ok is True
    assert captured["runtime_profile_id"] == "rp_user_1"
    assert captured["revision"] == 8
    assert captured["overlay"] == {"llm": {"provider": "openai"}}


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


@pytest.mark.asyncio
async def test_bootstrap_runtime_profile_malformed_payload_does_not_apply_or_clear(monkeypatch):
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
                    "runtime_profile_id": "rp_bad",
                    "runtime_profile_context": {
                        "runtime_profile_id": "rp_bad",
                        "revision": 5,
                        "config": "not-a-dict",
                    },
                },
            )
        ),
    )

    calls = {"apply": 0, "clear": 0}
    monkeypatch.setattr(
        runtime_profile_client.config,
        "set_managed_overlay",
        lambda *_args, **_kwargs: calls.update({"apply": calls["apply"] + 1}) or [],
    )
    monkeypatch.setattr(
        runtime_profile_client.config,
        "clear_managed_overlay",
        lambda: calls.update({"clear": calls["clear"] + 1}),
    )

    ok = await runtime_profile_client.bootstrap_runtime_profile_from_portal()
    assert ok is False
    assert calls["apply"] == 0
    assert calls["clear"] == 0
