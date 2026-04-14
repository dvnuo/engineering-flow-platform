import pytest

from src.gateway import webchat


class _Req:
    def __init__(self, payload=None, headers=None):
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        webchat.global_config,
        "set_managed_overlay",
        lambda rp_id, revision, cfg: captured.update(
            {"runtime_profile_id": rp_id, "revision": revision, "config": cfg}
        ) or ["llm", "proxy"],
    )

    req = _Req(
        payload={
            "runtime_profile_id": "rp_x",
            "revision": 3,
            "config": {"llm": {"provider": "openai"}, "proxy": {"enabled": True}},
        },
        headers={"X-Portal-Author-Source": "portal"},
    )
    resp = await webchat.api_apply_runtime_profile(req)

    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert '"success": true' in body
    assert '"updated_sections": ["llm", "proxy"]' in body
    assert '"cleared": false' in body
    assert captured["runtime_profile_id"] == "rp_x"
    assert captured["revision"] == 3


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_clear(monkeypatch):
    cleared = {"value": False}
    monkeypatch.setattr(webchat.global_config, "clear_managed_overlay", lambda: cleared.update({"value": True}))

    req = _Req(
        payload={"runtime_profile_id": None, "revision": None, "config": {}},
        headers={"X-Portal-Author-Source": "portal"},
    )
    resp = await webchat.api_apply_runtime_profile(req)

    assert resp.status == 200
    body = resp.body.decode("utf-8")
    assert '"cleared": true' in body
    assert cleared["value"] is True


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_untrusted_rejected():
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={},
    )
    resp = await webchat.api_apply_runtime_profile(req)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted_succeeds_with_portal_source_marker(monkeypatch):
    monkeypatch.setattr(webchat.global_config, "set_managed_overlay", lambda *_args, **_kwargs: ["jira"])
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={"X-Portal-Author-Source": "portal"},
    )
    resp = await webchat.api_apply_runtime_profile(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted_ignores_unrecognized_header(monkeypatch):
    monkeypatch.setattr(webchat.global_config, "set_managed_overlay", lambda *_args, **_kwargs: ["jira"])
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={"X-Portal-Author-Source": "portal", "X-Arbitrary-Header": "wrong"},
    )
    resp = await webchat.api_apply_runtime_profile(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted_remains_valid_with_extra_header(monkeypatch):
    monkeypatch.setattr(webchat.global_config, "set_managed_overlay", lambda *_args, **_kwargs: ["jira"])
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={"X-Portal-Author-Source": "portal", "X-Arbitrary-Header": "anything"},
    )
    resp = await webchat.api_apply_runtime_profile(req)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_api_get_config_returns_effective_with_runtime_profile_meta(monkeypatch):
    monkeypatch.setattr(webchat.global_config, "get_effective_config", lambda: {"jira": {"enabled": True}, "ssh": {"x": 1}})
    monkeypatch.setattr(
        webchat.global_config,
        "get_managed_overlay_meta",
        lambda: {"runtime_profile_id": "rp_1", "revision": 2, "managed_sections": ["jira"]},
    )

    resp = await webchat.api_get_config(_Req())
    assert resp.status == 200
    data = resp.body.decode("utf-8")
    assert '"runtime_profile_id": "rp_1"' in data
    assert '"jira": {"enabled": true}' in data
    assert '"ssh"' not in data
