import json

import pytest

from tests._lightweight_runtime_api_loader import load_runtime_api_lightweight


class _Req:
    def __init__(self, payload=None, headers=None):
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted(monkeypatch):
    runtime_api, cleanup = load_runtime_api_lightweight()
    captured = {}
    external_status = {
        "success": False,
        "error": "External CLI command failed: gh auth login exited with 1",
        "operation": "apply",
    }
    monkeypatch.setattr(
        runtime_api.global_config,
        "set_managed_overlay",
        lambda rp_id, revision, cfg: captured.update(
            {"runtime_profile_id": rp_id, "revision": revision, "config": cfg}
        ) or ["llm", "proxy"],
    )
    monkeypatch.setattr(runtime_api.global_config, "get_external_config_status", lambda: external_status)

    try:
        req = _Req(
            payload={
                "runtime_profile_id": "rp_x",
                "revision": 3,
                "config": {"llm": {"provider": "openai"}, "proxy": {"enabled": True}},
            },
            headers={"X-Portal-Author-Source": "portal"},
        )
        resp = await runtime_api.api_apply_runtime_profile(req)

        assert resp.status == 200
        body = json.loads(resp.body.decode("utf-8"))
        assert body["success"] is True
        assert body["updated_sections"] == ["llm", "proxy"]
        assert body["cleared"] is False
        assert body["external_config_status"] == external_status
        assert captured["runtime_profile_id"] == "rp_x"
        assert captured["revision"] == 3
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_clear(monkeypatch):
    runtime_api, cleanup = load_runtime_api_lightweight()
    cleared = {"value": False}
    monkeypatch.setattr(runtime_api.global_config, "clear_managed_overlay", lambda: cleared.update({"value": True}))

    try:
        req = _Req(
            payload={"runtime_profile_id": None, "revision": None, "config": {}},
            headers={"X-Portal-Author-Source": "portal"},
        )
        resp = await runtime_api.api_apply_runtime_profile(req)

        assert resp.status == 200
        body = json.loads(resp.body.decode("utf-8"))
        assert body["cleared"] is True
        assert body["external_config_status"] == {"success": True, "error": None, "operation": None}
        assert cleared["value"] is True
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_untrusted_rejected():
    runtime_api, cleanup = load_runtime_api_lightweight()
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={},
    )
    try:
        resp = await runtime_api.api_apply_runtime_profile(req)
        assert resp.status == 403
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted_succeeds_with_portal_source_marker(monkeypatch):
    runtime_api, cleanup = load_runtime_api_lightweight()
    monkeypatch.setattr(runtime_api.global_config, "set_managed_overlay", lambda *_args, **_kwargs: ["jira"])
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={"X-Portal-Author-Source": "portal"},
    )
    try:
        resp = await runtime_api.api_apply_runtime_profile(req)
        assert resp.status == 200
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted_ignores_unrecognized_header(monkeypatch):
    runtime_api, cleanup = load_runtime_api_lightweight()
    monkeypatch.setattr(runtime_api.global_config, "set_managed_overlay", lambda *_args, **_kwargs: ["jira"])
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={"X-Portal-Author-Source": "portal", "X-Arbitrary-Header": "wrong"},
    )
    try:
        resp = await runtime_api.api_apply_runtime_profile(req)
        assert resp.status == 200
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_internal_apply_runtime_profile_trusted_remains_valid_with_extra_header(monkeypatch):
    runtime_api, cleanup = load_runtime_api_lightweight()
    monkeypatch.setattr(runtime_api.global_config, "set_managed_overlay", lambda *_args, **_kwargs: ["jira"])
    req = _Req(
        payload={"runtime_profile_id": "rp_x", "revision": 1, "config": {"jira": {"enabled": True}}},
        headers={"X-Portal-Author-Source": "portal", "X-Arbitrary-Header": "anything"},
    )
    try:
        resp = await runtime_api.api_apply_runtime_profile(req)
        assert resp.status == 200
    finally:
        cleanup()
