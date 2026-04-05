"""Tests for WebChat UI module."""

import asyncio
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

    class _FakeBus:
        def __init__(self):
            self.request = None

        async def execute(self, request):
            self.request = request
            return type("R", (), {"status": "success", "output_payload": {"response": "ok"}})()

    fake_bus = _FakeBus()
    captured_kwargs = {}

    def _build_bus(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return fake_bus

    monkeypatch.setattr(webchat, "build_default_execution_bus", _build_bus)
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
    assert fake_bus.request.metadata["path"] == "/api/chat/stream"
    assert fake_bus.request.metadata["persist_last_execution_id"] is True
    assert ("event_emitter" not in captured_kwargs) or (captured_kwargs.get("event_emitter") is None)


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

    class _FakeBus:
        async def execute(self, _request):
            return type("R", (), {"status": "error", "output_payload": {"error": {"message": "boom"}}})()

    monkeypatch.setattr(webchat, "build_default_execution_bus", lambda *args, **kwargs: _FakeBus())

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
