"""The session name endpoints: GET .../name and POST .../title/generate."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

try:
    from src.gateway import runtime_api
except ImportError:  # pragma: no cover - mirrors test_runtime_api.py
    pytest.skip("Runtime API module not available", allow_module_level=True)

from src.gateway.session_title import TitleGenerationError


class _Request:
    def __init__(self, session_id, body=None):
        self.match_info = {"session_id": session_id}
        self._body = body
        self.headers = {}
        self.app = {}

    async def json(self):
        if self._body is None:
            raise json.JSONDecodeError("empty", "", 0)
        return self._body


def _summary(**overrides):
    base = dict(
        session_id="s-1",
        title=None,
        custom_name=None,
        generated_name=None,
        user_message_count=1,
        first_user_preview="how do I fix the deploy stage permissions error in the release pipeline",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def store(monkeypatch):
    summaries = {}
    fake_store = SimpleNamespace(get_session_summary=lambda session_id: summaries[session_id] if session_id in summaries else (_ for _ in ()).throw(KeyError(session_id)))
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    monkeypatch.setattr(runtime_api.session_manager, "store", fake_store, raising=False)
    return summaries


def test_routes_registered():
    from aiohttp import web

    app = web.Application()
    runtime_api.setup_runtime_api_routes(app)
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/sessions/{session_id}/name") in routes
    assert ("POST", "/api/sessions/{session_id}/title/generate") in routes


async def test_name_endpoint_reports_the_derived_name(store):
    store["s-1"] = _summary()

    resp = await runtime_api.api_session_name(_Request("s-1"))

    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload == {
        "session_id": "s-1",
        "name": "how do I fix the deploy sta...",
        "name_source": "derived",
    }


async def test_name_endpoint_prefers_manual_then_generated(store):
    store["s-1"] = _summary(generated_name="Release pipeline permissions")
    payload = json.loads((await runtime_api.api_session_name(_Request("s-1"))).text)
    assert payload["name"] == "Release pipeline permissions"
    assert payload["name_source"] == "generated"

    store["s-1"] = _summary(generated_name="Release pipeline permissions", custom_name="My notes")
    payload = json.loads((await runtime_api.api_session_name(_Request("s-1"))).text)
    assert payload["name"] == "My notes"
    assert payload["name_source"] == "custom"


async def test_name_endpoint_404_for_an_unknown_session(store):
    resp = await runtime_api.api_session_name(_Request("missing"))
    assert resp.status == 404


async def test_name_endpoint_requires_a_session_id(store):
    resp = await runtime_api.api_session_name(_Request(""))
    assert resp.status == 400


async def test_sessions_list_carries_the_generated_name_and_its_source(monkeypatch):
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)

    async def summaries():
        return [
            _summary(session_id="s-1", generated_name="Release pipeline permissions", updated_at="2026-10-20T00:00:00", last_preview="ok"),
            _summary(session_id="s-2", updated_at="2026-10-20T00:00:00", last_preview="ok"),
        ]

    monkeypatch.setattr(runtime_api.session_manager, "list_session_summaries", summaries)

    class _ListRequest:
        query = {"limit": "10"}

    payload = json.loads((await runtime_api.api_sessions(_ListRequest())).text)
    by_id = {item["session_id"]: item for item in payload["sessions"]}
    assert by_id["s-1"]["name"] == "Release pipeline permissions"
    assert by_id["s-1"]["name_source"] == "generated"
    assert by_id["s-2"]["name"] == "how do I fix the deploy sta..."
    assert by_id["s-2"]["name_source"] == "derived"


@pytest.fixture
def generation(monkeypatch, store):
    calls = []
    outcome = {"value": "Release pipeline permissions"}

    async def fake_generate(session_id, **kwargs):
        calls.append((session_id, kwargs))
        value = outcome["value"]
        if isinstance(value, Exception):
            raise value
        if value is not None:
            store[session_id] = _summary(session_id=session_id, generated_name=value)
        return value

    import src.gateway.session_title as module

    monkeypatch.setattr(module, "generate_session_title", fake_generate)
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Assistant"))
    published = []

    async def fake_publish(*, agent_id, session_id, title):
        published.append((agent_id, session_id, title))

    monkeypatch.setattr(runtime_api, "publish_session_title", fake_publish)
    return SimpleNamespace(calls=calls, outcome=outcome, published=published)


async def test_generate_endpoint_stores_and_publishes(store, generation):
    store["s-1"] = _summary()

    resp = await runtime_api.api_generate_session_title(_Request("s-1", body={"force": True}))

    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload == {
        "success": True,
        "generated": True,
        "session_id": "s-1",
        "name": "Release pipeline permissions",
        "name_source": "generated",
    }
    session_id, kwargs = generation.calls[0]
    assert session_id == "s-1"
    assert kwargs["force"] is True
    assert kwargs["execution_metadata"] == {}
    assert callable(kwargs["provider_builder"])
    assert generation.published == [("agent-1", "s-1", "Release pipeline permissions")]


async def test_generate_endpoint_without_a_body_keeps_a_manual_name(store, generation):
    store["s-1"] = _summary(custom_name="My notes")
    generation.outcome["value"] = None

    resp = await runtime_api.api_generate_session_title(_Request("s-1"))

    payload = json.loads(resp.text)
    assert payload["generated"] is False
    assert payload["name"] == "My notes"
    assert payload["name_source"] == "custom"
    assert generation.calls[0][1]["force"] is False
    assert generation.published == []


async def test_generate_endpoint_404_for_an_unknown_session(store, generation):
    generation.outcome["value"] = KeyError("missing")
    resp = await runtime_api.api_generate_session_title(_Request("missing", body={}))
    assert resp.status == 404


async def test_generate_endpoint_reports_the_generation_error_status(store, generation):
    store["s-1"] = _summary()
    generation.outcome["value"] = TitleGenerationError("The session has no completed turn to name yet.", status_code=422)
    resp = await runtime_api.api_generate_session_title(_Request("s-1", body={}))
    assert resp.status == 422
    assert json.loads(resp.text)["error"] == "The session has no completed turn to name yet."


async def test_publish_metadata_enrichment_carries_the_session_title(monkeypatch):
    async def title(_session_id):
        return "Release pipeline permissions"

    async def none(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime_api.session_manager, "get_session_title", title, raising=False)
    monkeypatch.setattr(runtime_api.session_manager, "get_context_state", none)
    monkeypatch.setattr(runtime_api.session_manager, "get_active_skill_session", none)

    merged = await runtime_api._enrich_publish_metadata_with_context_preview({"a": 1}, session_id="s-1")
    assert merged["session_title"] == "Release pipeline permissions"
    assert merged["a"] == 1
