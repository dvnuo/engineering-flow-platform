"""Tests for WebChat UI module."""

import asyncio
import json
import os
import pytest
from pathlib import Path

try:
    from src.gateway.webchat import setup_webchat_routes, load_template
except ImportError:
    pytest.skip("WebChat module not available", allow_module_level=True)


class TestWebChatTemplate:
    """Tests for WebChat template loading."""
    
    def test_load_template(self):
        """Test loading HTML template from file."""
        html = load_template("webchat.html")
        assert html is not None
        assert len(html) > 0
        assert "<!DOCTYPE html>" in html
    
    def test_template_structure(self):
        """Test template has correct structure."""
        html = load_template("webchat.html")
        
        # Check for key elements
        assert 'class="header"' in html
        assert 'class="chat-container"' in html
        assert 'class="messages"' in html
        assert 'id="messageInput"' in html
        assert 'id="sendButton"' in html
        assert 'id="typing"' in html
    
    def test_template_links_static(self):
        """Test template links to static CSS and JS files."""
        html = load_template("webchat.html")
        
        assert './static/css/webchat.css' in html
        assert './static/js/webchat.js' in html
        assert './static/vendor/highlightjs/github-dark.min.css' in html
        assert './static/vendor/highlightjs/highlight.min.js' in html
        assert './static/vendor/marked/marked.min.js' in html
        assert 'https://cdnjs.cloudflare.com' not in html
        assert 'https://fonts.googleapis.com' not in html


class TestWebChatStaticFiles:
    """Tests for static files."""
    
    def test_css_file_exists(self):
        """Test CSS file exists and has content."""
        css_path = Path(__file__).parent.parent / "gateway" / "static" / "css" / "webchat.css"
        assert css_path.exists()
        
        with open(css_path, 'r') as f:
            css = f.read()
        
        assert len(css) > 0
        assert '.message {' in css
        assert '.input-field {' in css
        assert '.send-button {' in css
    
    def test_js_file_exists(self):
        """Test JS file exists and has content."""
        js_path = Path(__file__).parent.parent / "gateway" / "static" / "js" / "webchat.js"
        assert js_path.exists()
        
        with open(js_path, 'r') as f:
            js = f.read()
        
        assert len(js) > 0
        assert 'function sendMessage()' in js
        assert 'addMessage(' in js
        assert 'escapeHtml(' in js



class TestWebChatRoutes:
    """Tests for WebChat route registration."""
    
    def test_setup_webchat_routes_returns_none(self):
        """Test setup_webchat_routes modifies app in-place."""
        from aiohttp import web
        app = web.Application()
        result = setup_webchat_routes(app)
        assert result is None
    
    def test_routes_registered(self):
        """Test expected routes are registered."""
        from aiohttp import web
        app = web.Application()
        setup_webchat_routes(app)
        
        routes = [r.resource.canonical for r in app.router.routes() if r.resource]
        
        assert '/chat' in routes
        assert '/api/chat' in routes
        assert '/api/sessions' in routes
        assert '/api/usage' in routes
        assert '/api/clear' in routes
    
    def test_static_route_registered(self):
        """Test static file route is registered."""
        from aiohttp import web
        app = web.Application()
        setup_webchat_routes(app)
        
        routes = [r.resource.canonical for r in app.router.routes() if r.resource]
        
        # Check for static route pattern
        static_routes = [r for r in routes if '/static/' in r]
        assert len(static_routes) > 0


class TestWebChatDirectoryStructure:
    """Tests for proper directory structure."""
    
    def test_templates_directory_exists(self):
        """Test templates directory exists."""
        templates_dir = Path(__file__).parent.parent / "gateway" / "templates"
        assert templates_dir.exists()
        assert templates_dir.is_dir()
    
    def test_static_directory_exists(self):
        """Test static directory exists."""
        static_dir = Path(__file__).parent.parent / "gateway" / "static"
        assert static_dir.exists()
        assert static_dir.is_dir()
    
    def test_css_subdirectory_exists(self):
        """Test CSS subdirectory exists."""
        css_dir = Path(__file__).parent.parent / "gateway" / "static" / "css"
        assert css_dir.exists()
        assert css_dir.is_dir()
    
    def test_js_subdirectory_exists(self):
        """Test JS subdirectory exists."""
        js_dir = Path(__file__).parent.parent / "gateway" / "static" / "js"
        assert js_dir.exists()
        assert js_dir.is_dir()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_edit_delete_routes_registered():
    """Test new edit/delete routes are registered."""
    from aiohttp import web
    from src.gateway.webchat import setup_webchat_routes
    
    app = web.Application()
    setup_webchat_routes(app)
    
    routes = [r.resource.canonical for r in app.router.routes() if r.resource]
    
    # Check new routes exist
    assert '/api/sessions/{session_id}/messages/{message_id}/edit' in routes
    assert '/api/sessions/{session_id}/messages/{message_id}/delete-from-here' in routes


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_non_stream(monkeypatch):
    from src.gateway import webchat

    async def fake_run_chat_execution(agent, **kwargs):
        assert kwargs["portal_user_id"] == "p-1"
        return {"response": "ok", "usage": {"total_tokens": 1}}

    monkeypatch.setattr(webchat, "run_chat_execution", fake_run_chat_execution)
    result = await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id="p-1",
        portal_user_name="Portal User",
    )
    assert result["response"] == "ok"


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_stream(monkeypatch):
    from src.gateway import webchat

    async def fake_run_chat_execution(agent, **kwargs):
        stream_callback = kwargs.get("stream_callback")
        await stream_callback.put("{\"type\":\"progress\"}")
        return {"response": "streamed"}

    monkeypatch.setattr(webchat, "run_chat_execution", fake_run_chat_execution)
    import asyncio
    queue = asyncio.Queue()
    result = await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-stream",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
        stream_callback=queue,
    )
    assert result["response"] == "streamed"
    assert not queue.empty()


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_sets_request_path_metadata(monkeypatch):
    from src.gateway import webchat

    captured = {}
    async def _fake_execute_chat_orchestration(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"status": "success", "output_payload": {"response": "ok"}})()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)
    async def _fake_run_chat_execution(*args, **kwargs):
        return {"response": "ignored"}
    monkeypatch.setattr(webchat, "run_chat_execution", _fake_run_chat_execution)

    await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
        request_path="/api/chat/stream",
    )
    assert captured["metadata"]["path"] == "/api/chat/stream"
    assert captured["metadata"]["persist_last_execution_id"] is True


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_merges_execution_metadata_without_overriding_path(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_execute_chat_orchestration(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"status": "success", "output_payload": {"response": "ok"}})()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)
    monkeypatch.setattr(webchat, "run_chat_execution", lambda *args, **kwargs: {"response": "ignored"})

    await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
        request_path="/api/chat",
        execution_metadata={"path": "/forged", "allowed_capability_ids": ["tool:run_command"]},
    )

    assert captured["metadata"]["path"] == "/api/chat"
    assert captured["metadata"]["allowed_capability_ids"] == ["tool:run_command"]


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_persists_last_execution_id(monkeypatch):
    from src.gateway import webchat

    calls = []

    async def _fake_set_last_execution_id(session_id, request_id):
        calls.append((session_id, request_id))

    async def fake_run_chat_execution(agent, **kwargs):
        return {"response": "ok"}

    monkeypatch.setattr(webchat.session_manager, "set_last_execution_id", _fake_set_last_execution_id)
    monkeypatch.setattr(webchat, "run_chat_execution", fake_run_chat_execution)

    await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-meta",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
    )
    assert calls
    assert calls[0][0] == "s-meta"
    assert str(calls[0][1]).startswith("chat-")


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_raises_on_error_status(monkeypatch):
    from aiohttp import web
    from src.gateway import webchat

    async def _fake_execute_chat_orchestration(**kwargs):
            return type("R", (), {"status": "error", "output_payload": {"error": {"message": "boom"}}})()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)

    async def _fake_run_chat_execution(*args, **kwargs):
        return {"response": "ignored"}

    monkeypatch.setattr(webchat, "run_chat_execution", _fake_run_chat_execution)

    with pytest.raises(web.HTTPInternalServerError):
        await webchat._run_chat_via_execution_bus(
            agent=object(),
            session_id="s-chat",
            message="hello",
            user_name="u1",
            portal_user_id=None,
            portal_user_name=None,
        )


@pytest.mark.asyncio
async def test_api_chat_reraises_http_exception():
    from aiohttp import web
    from src.gateway import webchat

    class _Request:
        app = {}

        async def json(self):
            raise web.HTTPInternalServerError(text='{"error":"bus failed"}', content_type="application/json")

    with pytest.raises(web.HTTPInternalServerError):
        await webchat.api_chat(_Request())


@pytest.mark.asyncio
async def test_api_chat_stream_reraises_http_exception():
    from aiohttp import web
    from src.gateway import webchat

    class _Request:
        app = {}

        async def json(self):
            raise web.HTTPInternalServerError(text='{"error":"bus failed"}', content_type="application/json")

    with pytest.raises(web.HTTPInternalServerError):
        await webchat.api_chat_stream(_Request())


@pytest.mark.asyncio
async def test_api_chat_generic_exception_still_returns_error_response():
    from src.gateway import webchat

    class _Request:
        app = {}

        async def json(self):
            raise RuntimeError("bad")

    response = await webchat.api_chat(_Request())
    assert response.status == 500


@pytest.mark.asyncio
async def test_api_chat_stream_generic_exception_still_returns_error_response():
    from src.gateway import webchat

    class _Request:
        app = {}

        async def json(self):
            raise RuntimeError("bad")

    response = await webchat.api_chat_stream(_Request())
    assert response.status == 500


@pytest.mark.asyncio
async def test_api_chat_resolves_portal_identity_from_headers(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_get_session(_session_id):
        return {"history": [{}], "channel": "", "metadata": {}}

    async def _fake_save_session(**kwargs):
        return True

    monkeypatch.setattr(webchat.session_manager, "get_session", _fake_get_session)
    monkeypatch.setattr(webchat.session_persistence, "save_session", _fake_save_session)

    class _Request:
        app = {}
        headers = {"X-Portal-User-Id": " hdr-user \r\n", "X-Portal-User-Name": " hdr-name\t"}

        async def json(self):
            return {"message": "hello", "session_id": "s1"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] == "hdr-user"
    assert captured["portal_user_name"] == "hdr-name"


@pytest.mark.asyncio
async def test_api_chat_stream_resolves_portal_identity_from_headers(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            self.writes.append(data)

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-User-Id": "stream-user", "X-Portal-User-Name": "stream-name"}

        async def json(self):
            return {"message": "hello", "session_id": "s2"}

    resp = await webchat.api_chat_stream(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] == "stream-user"
    assert captured["portal_user_name"] == "stream-name"


@pytest.mark.asyncio
async def test_api_chat_trusted_portal_metadata_passed_to_execution_bus(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.setenv("PORTAL_INTERNAL_API_KEY", "portal-secret")
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {
            "X-Portal-Author-Source": "portal",
            "X-Portal-Internal-Api-Key": "portal-secret",
        }

        async def json(self):
            return {
                "message": "hello",
                "session_id": "s-meta-1",
                "metadata": {
                    "capability_profile_id": "cp-1",
                    "policy_profile_id": "pp-1",
                    "allowed_capability_ids": ["adapter:portal:create_delegation"],
                    "policy_context": {"raw": True},
                },
            }

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["execution_metadata"]["capability_profile_id"] == "cp-1"
    assert captured["execution_metadata"]["policy_profile_id"] == "pp-1"
    assert captured["execution_metadata"]["allowed_capability_ids"] == ["adapter:portal:create_delegation"]


@pytest.mark.asyncio
async def test_api_chat_untrusted_request_ignores_governance_metadata(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.delenv("PORTAL_INTERNAL_API_KEY", raising=False)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {
                "message": "hello",
                "session_id": "s-meta-2",
                "metadata": {"allowed_capability_ids": ["adapter:portal:create_delegation"]},
            }

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["execution_metadata"] == {}


@pytest.mark.asyncio
async def test_api_chat_flattens_policy_context_derived_runtime_rules(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.delenv("PORTAL_INTERNAL_API_KEY", raising=False)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {
                "message": "hello",
                "session_id": "s-meta-3",
                "metadata": {
                    "policy_context": {
                        "derived_runtime_rules": {
                            "governance_require_explicit_allow": True,
                            "governance_external_allowlist": ["github_review_task"],
                        }
                    }
                },
            }

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["execution_metadata"]["governance_require_explicit_allow"] is True
    assert captured["execution_metadata"]["governance_external_allowlist"] == ["github_review_task"]


@pytest.mark.asyncio
async def test_api_chat_stream_trusted_portal_metadata_passed_to_execution_bus(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            self.writes.append(data)

    monkeypatch.delenv("PORTAL_INTERNAL_API_KEY", raising=False)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {
                "message": "hello",
                "session_id": "s-stream-meta",
                "metadata": {"allowed_actions": ["adapter:portal:get_group_task_board"]},
            }

    resp = await webchat.api_chat_stream(_Request())
    assert resp.status == 200
    assert captured["execution_metadata"]["allowed_actions"] == ["adapter:portal:get_group_task_board"]


@pytest.mark.asyncio
async def test_api_chat_rejects_non_object_metadata(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k"}}, raising=False)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-bad-meta", "metadata": ["not-an-object"]}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 400


@pytest.mark.asyncio
async def test_api_chat_body_identity_fallback_and_header_precedence(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_get_session(_session_id):
        return {"history": [{}], "channel": "", "metadata": {}}

    async def _fake_save_session(**kwargs):
        return True

    monkeypatch.setattr(webchat.session_manager, "get_session", _fake_get_session)
    monkeypatch.setattr(webchat.session_persistence, "save_session", _fake_save_session)

    class _BodyOnlyRequest:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s3", "portal_user_id": "body-id", "portal_user_name": "body-name"}

    await webchat.api_chat(_BodyOnlyRequest())
    assert captured["portal_user_id"] == "body-id"
    assert captured["portal_user_name"] == "body-name"

    class _ConflictRequest:
        app = {}
        headers = {"X-Portal-User-Id": "header-id", "X-Portal-User-Name": "header-name"}

        async def json(self):
            return {"message": "hello", "session_id": "s4", "portal_user_id": "body-id", "portal_user_name": "body-name"}

    await webchat.api_chat(_ConflictRequest())
    assert captured["portal_user_id"] == "header-id"
    assert captured["portal_user_name"] == "header-name"


def test_sanitize_portal_identity_value_none_returns_empty():
    from src.gateway import webchat

    assert webchat._sanitize_portal_identity_value(None) == ""


def test_sanitize_portal_identity_value_strips_controls_and_whitespace():
    from src.gateway import webchat

    assert webchat._sanitize_portal_identity_value(" \r\nabc\t\x00 ") == "abc"


def test_sanitize_portal_identity_value_truncates_to_max_length():
    from src.gateway import webchat

    raw = "x" * (webchat.MAX_PORTAL_IDENTITY_LENGTH + 25)
    sanitized = webchat._sanitize_portal_identity_value(raw)
    assert len(sanitized) == webchat.MAX_PORTAL_IDENTITY_LENGTH
    assert sanitized == "x" * webchat.MAX_PORTAL_IDENTITY_LENGTH


def test_sanitize_portal_identity_value_truncation_applies_after_sanitization():
    from src.gateway import webchat

    raw = " \n" + ("a" * (webchat.MAX_PORTAL_IDENTITY_LENGTH + 10)) + "\r\t "
    sanitized = webchat._sanitize_portal_identity_value(raw)
    assert len(sanitized) == webchat.MAX_PORTAL_IDENTITY_LENGTH
    assert sanitized == "a" * webchat.MAX_PORTAL_IDENTITY_LENGTH


def test_routes_include_tasks_execute_and_existing_chat_route():
    from aiohttp import web
    from src.gateway.webchat import setup_webchat_routes

    app = web.Application()
    setup_webchat_routes(app)

    routes = [r.resource.canonical for r in app.router.routes() if r.resource]
    assert "/api/tasks/execute" in routes
    assert "/api/capabilities" in routes
    assert "/api/chat" in routes


@pytest.mark.asyncio
async def test_api_capabilities_returns_catalog_and_filters(monkeypatch):
    from src.gateway import webchat

    class _Registry:
        def export_catalog_snapshot(self):
            capabilities = [
                {"capability_id": "tool:read", "type": "tool", "enabled": True},
                {"capability_id": "adapter:jira:read_issue", "type": "adapter_action", "enabled": False},
            ]
            return {
                "capabilities": capabilities,
                "count": len(capabilities),
                "catalog_version": "v-snap",
                "generated_at": "2026-04-07T00:00:00Z",
            }

    monkeypatch.setattr(webchat, "get_capability_registry", lambda: _Registry())

    class _Request:
        query = {"type": "tool", "enabled": "true"}

    response = await webchat.api_capabilities(_Request())
    body = json.loads(response.body)
    assert response.status == 200
    assert body["count"] == 1
    assert body["capabilities"][0]["capability_id"] == "tool:read"
    assert body["catalog_version"] == "v-snap"
    assert body["generated_at"] == "2026-04-07T00:00:00Z"
    assert body["supports_snapshot_contract"] is True


@pytest.mark.asyncio
async def test_api_capabilities_capability_id_not_found_returns_empty(monkeypatch):
    from src.gateway import webchat

    class _Registry:
        def export_catalog_snapshot(self):
            return {
                "capabilities": [{"capability_id": "tool:read", "type": "tool", "enabled": True}],
                "count": 1,
                "catalog_version": "v-snap",
                "generated_at": "2026-04-07T00:00:00Z",
            }

    monkeypatch.setattr(webchat, "get_capability_registry", lambda: _Registry())

    class _Request:
        query = {"capability_id": "adapter:jira:missing"}

    response = await webchat.api_capabilities(_Request())
    body = json.loads(response.body)
    assert response.status == 200
    assert body["count"] == 0
    assert body["capabilities"] == []
    assert body["catalog_version"] == "v-snap"


@pytest.mark.asyncio
async def test_api_capabilities_filters_by_capability_id_and_type(monkeypatch):
    from src.gateway import webchat

    class _Registry:
        def export_catalog_snapshot(self):
            capabilities = [
                {"capability_id": "tool:read", "type": "tool", "enabled": True},
                {"capability_id": "tool:write", "type": "tool", "enabled": True},
                {"capability_id": "adapter:jira:read_issue", "type": "adapter_action", "enabled": True},
            ]
            return {
                "capabilities": capabilities,
                "count": len(capabilities),
                "catalog_version": "v-snap",
                "generated_at": "2026-04-07T00:00:00Z",
            }

    monkeypatch.setattr(webchat, "get_capability_registry", lambda: _Registry())

    class _ByCapabilityIdRequest:
        query = {"capability_id": "tool:write"}

    by_id_response = await webchat.api_capabilities(_ByCapabilityIdRequest())
    by_id_body = json.loads(by_id_response.body)
    assert by_id_response.status == 200
    assert by_id_body["count"] == 1
    assert by_id_body["capabilities"] == [{"capability_id": "tool:write", "type": "tool", "enabled": True}]

    class _ByTypeRequest:
        query = {"type": "adapter_action"}

    by_type_response = await webchat.api_capabilities(_ByTypeRequest())
    by_type_body = json.loads(by_type_response.body)
    assert by_type_response.status == 200
    assert by_type_body["count"] == 1
    assert by_type_body["capabilities"][0]["type"] == "adapter_action"


@pytest.mark.asyncio
async def test_api_tasks_execute_adapter_action_task_success(monkeypatch):
    from src.gateway import webchat

    captured = {}
    async def _fake_execute_runtime_task_request(**kwargs):
        captured.update(kwargs)
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True, "result": {"id": "A-1"}},
                "artifacts": [],
                "runtime_events": [{"type": "task.adapter_action.completed", "task_id": kwargs["metadata"]["task_id"]}],
                "next_action_hint": None,
                "audit_ref": "audit-1",
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)

    class _Request:
        async def json(self):
            return {
                "task_id": "task-1",
                "task_type": "adapter_action_task",
                "session_id": "session-1",
                "source": "portal",
                "workflow_rule_id": "wf-1",
                "shared_context_ref": "ctx://1",
                "context_ref": {"workspace": "w1"},
                "metadata": {"custom": "value"},
                "input_payload": {"action_id": "jira.transition", "kwargs": {"issue_key": "ENG-1"}},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)

    assert response.status == 200
    assert body["ok"] is True
    assert body["task_id"] == "task-1"
    assert body["status"] == "success"
    assert captured["execution_type"] == "task"
    assert captured["input_payload"]["task_type"] == "adapter_action_task"
    assert captured["metadata"]["portal_task_id"] == "task-1"
    assert captured["metadata"]["task_id"] == "task-1"
    assert captured["metadata"]["portal_task_source"] == "portal"
    assert captured["metadata"]["portal_workflow_rule_id"] == "wf-1"
    assert captured["metadata"]["shared_context_ref"] == "ctx://1"
    assert captured["metadata"]["external_triggered"] is True
    assert captured["metadata"]["auto_run"] is True
    assert captured["metadata"]["governance_target"] == "adapter_action_task"
    assert captured["metadata"]["custom"] == "value"
    assert captured["input_payload"]["shared_context_ref"] == "ctx://1"
    assert captured["context_ref"] == {"workspace": "w1"}
    assert captured["request_id"] == "task-task-1"


@pytest.mark.asyncio
async def test_api_tasks_execute_jira_workflow_review_task_success(monkeypatch):
    from src.gateway import webchat

    async def _fake_execute_runtime_task_request(**kwargs):
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"result": {"issue_key": "ENG-2"}},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": "none",
                "audit_ref": None,
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)

    class _Request:
        async def json(self):
            return {
                "task_id": "task-2",
                "task_type": "jira_workflow_review_task",
                "input_payload": {"issue_key": "ENG-2"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)

    assert response.status == 200
    assert body["task_id"] == "task-2"
    assert body["execution_type"] == "task"
    assert body["status"] == "success"


@pytest.mark.asyncio
async def test_api_tasks_execute_github_review_task_reaches_execution_bus(monkeypatch):
    from src.gateway import webchat

    captured = {}
    async def _fake_execute_runtime_task_request(**kwargs):
        captured.update(kwargs)
        task_id = kwargs["metadata"]["task_id"]
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {
                    "task_type": "github_review_task",
                    "owner": "acme",
                    "repo": "demo",
                    "pull_number": 33,
                    "review_summary": "LGTM",
                    "comment_written": True,
                    "success": True,
                },
                "artifacts": {},
                "runtime_events": [{"event_type": "task.github_review.completed", "task_id": task_id}],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)

    class _Request:
        async def json(self):
            return {
                "task_id": "gh-task-1",
                "task_type": "github_review_task",
                "input_payload": {"owner": "acme", "repo": "demo", "pull_number": 33},
                "metadata": {"trace_id": "t-1"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)

    assert response.status == 200
    assert body["ok"] is True
    assert body["task_id"] == "gh-task-1"
    assert body["execution_type"] == "task"
    assert body["status"] == "success"
    assert captured["input_payload"]["task_type"] == "github_review_task"
    assert captured["metadata"]["task_id"] == "gh-task-1"
    assert body["runtime_events"][0]["task_id"] == "gh-task-1"


@pytest.mark.asyncio
async def test_api_tasks_execute_missing_task_type_returns_400():
    from src.gateway import webchat

    class _Request:
        async def json(self):
            return {
                "task_id": "task-3",
                "input_payload": {"action_id": "jira.transition"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    assert response.status == 400
    assert "task_type" in body["error"]


@pytest.mark.asyncio
async def test_api_tasks_execute_non_object_input_payload_returns_400():
    from src.gateway import webchat

    class _Request:
        async def json(self):
            return {
                "task_id": "task-4",
                "task_type": "adapter_action_task",
                "input_payload": "not-an-object",
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    assert response.status == 400
    assert "input_payload" in body["error"]


@pytest.mark.asyncio
async def test_api_tasks_execute_non_object_context_ref_returns_400():
    from src.gateway import webchat

    class _Request:
        async def json(self):
            return {
                "task_id": "task-4b",
                "task_type": "adapter_action_task",
                "context_ref": "not-an-object",
                "input_payload": {"action_id": "jira.transition"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    assert response.status == 400
    assert "context_ref" in body["error"]


@pytest.mark.asyncio
async def test_api_tasks_execute_blocked_result_returns_ok_false(monkeypatch):
    from src.gateway import webchat

    async def _fake_execute_runtime_task_request(**kwargs):
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "blocked",
                "output_payload": {"error": "blocked by policy"},
                "artifacts": {},
                "runtime_events": [{"type": "governance.audit"}],
                "next_action_hint": "request_approval",
                "audit_ref": "audit-2",
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)

    class _Request:
        async def json(self):
            return {
                "task_id": "task-5",
                "task_type": "adapter_action_task",
                "input_payload": {"action_id": "jira.transition"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    assert response.status == 200
    assert body["ok"] is False
    assert body["status"] == "blocked"
    assert body["error"] == "blocked by policy"
