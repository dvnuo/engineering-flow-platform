"""Tests for WebChat UI module."""

import asyncio
import json
import os
import subprocess
import pytest
from pathlib import Path

try:
    from src.gateway.webchat import setup_webchat_routes, load_template
except ImportError:
    pytest.skip("WebChat module not available", allow_module_level=True)


INTERNAL_HEADERS = {}


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
        assert '<header' in html
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
        css_path = Path(__file__).parent.parent / "src" / "gateway" / "static" / "css" / "webchat.css"
        assert css_path.exists()
        
        with open(css_path, 'r') as f:
            css = f.read()
        
        assert len(css) > 0
        assert '.message {' in css
        assert '.input-field {' in css
        assert '.send-button {' in css
        assert '.copy-code-button' in css
        assert '.message-codeblock-toolbar' in css
        assert '.message-table-wrap' in css
    
    def test_js_file_exists(self):
        """Test JS file exists and has content."""
        js_path = Path(__file__).parent.parent / "src" / "gateway" / "static" / "js" / "webchat.js"
        assert js_path.exists()
        
        with open(js_path, 'r') as f:
            js = f.read()
        
        assert len(js) > 0
        assert 'function sendMessage()' in js
        assert 'addMessage(' in js
        assert 'escapeHtml(' in js
        assert 'renderDisplayBlocks(' in js
        assert 'enhanceRenderedMessage(' in js
        assert 'function getBlockText(block, preferCode = false)' in js
        assert 'hasMeaningfulDisplayBlocks(' in js
        assert 'hasMeaningfulDisplayBlocks(lastMsg.display_blocks)' in js


def test_webchat_js_final_assistant_detection_respects_renderable_blocks():
    js_path = Path(__file__).parent.parent / "src" / "gateway" / "static" / "js" / "webchat.js"
    js = js_path.read_text(encoding='utf-8')
    parse_start = js.find("function parseDisplayBlocks(raw)")
    get_block_text_start = js.find("function getBlockText(block, preferCode = false)")
    has_renderable_start = js.find("function hasRenderableDisplayBlock(block)")
    has_meaningful_start = js.find("function hasMeaningfulDisplayBlocks(blocks)")
    render_single_start = js.find("function renderSingleDisplayBlock(block)")
    has_final_start = js.find("function hasFinalAssistant(sessionData)")
    poll_start = js.find("async function pollSessionUntilFinal()")

    assert parse_start != -1
    assert get_block_text_start != -1
    assert has_renderable_start != -1
    assert has_meaningful_start != -1
    assert render_single_start != -1
    assert has_final_start != -1
    assert poll_start != -1

    parse_fn = js[parse_start:get_block_text_start]
    get_block_text_fn = js[get_block_text_start:has_renderable_start]
    has_renderable_fn = js[has_renderable_start:has_meaningful_start]
    has_meaningful_fn = js[has_meaningful_start:render_single_start]
    has_final_fn = js[has_final_start:poll_start]

    empty_payload = {"messages": [{"role": "assistant", "display_blocks": [{"type": "tool_result", "title": "Bash", "content": "   "}]}]}
    filled_payload = {"messages": [{"role": "assistant", "display_blocks": [{"type": "tool_result", "title": "Bash", "output": "done"}]}]}
    script = f"""
{parse_fn}
{get_block_text_fn}
{has_renderable_fn}
{has_meaningful_fn}
{has_final_fn}
console.log(String(hasFinalAssistant({json.dumps(empty_payload)})));
console.log(String(hasFinalAssistant({json.dumps(filled_payload)})));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    outputs = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert outputs == ["false", "true"]



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
        
        assert '/' in routes
        assert '/api/chat' in routes
        assert '/api/sessions' in routes
        assert '/api/sessions/{session_id}/rename' in routes
        assert '/api/sessions/{session_id}' in routes
        assert '/api/usage' in routes
        assert '/api/clear' in routes

        delete_routes = [
            r for r in app.router.routes()
            if r.resource and r.resource.canonical == '/api/sessions/{session_id}' and r.method == 'DELETE'
        ]
        assert delete_routes
    
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
        templates_dir = Path(__file__).parent.parent / "src" / "gateway" / "templates"
        assert templates_dir.exists()
        assert templates_dir.is_dir()
    
    def test_static_directory_exists(self):
        """Test static directory exists."""
        static_dir = Path(__file__).parent.parent / "src" / "gateway" / "static"
        assert static_dir.exists()
        assert static_dir.is_dir()
    
    def test_css_subdirectory_exists(self):
        """Test CSS subdirectory exists."""
        css_dir = Path(__file__).parent.parent / "src" / "gateway" / "static" / "css"
        assert css_dir.exists()
        assert css_dir.is_dir()
    
    def test_js_subdirectory_exists(self):
        """Test JS subdirectory exists."""
        js_dir = Path(__file__).parent.parent / "src" / "gateway" / "static" / "js"
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


class _HeaderOnlyRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_is_trusted_portal_request_true_for_portal_source():
    from src.gateway import webchat

    assert webchat._is_trusted_portal_request(_HeaderOnlyRequest({"X-Portal-Author-Source": "portal"})) is True


def test_is_trusted_portal_request_false_when_missing_source():
    from src.gateway import webchat

    assert webchat._is_trusted_portal_request(_HeaderOnlyRequest({})) is False


def test_is_trusted_portal_request_false_for_non_portal_source():
    from src.gateway import webchat

    assert webchat._is_trusted_portal_request(_HeaderOnlyRequest({"X-Portal-Author-Source": "runtime"})) is False


def test_is_trusted_portal_request_depends_only_on_portal_source_marker():
    from src.gateway import webchat

    trusted = webchat._is_trusted_portal_request(
        _HeaderOnlyRequest({"X-Portal-Author-Source": "portal", "X-Arbitrary-Header": "ignored"})
    )
    untrusted = webchat._is_trusted_portal_request(
        _HeaderOnlyRequest({"X-Portal-Author-Source": "runtime", "X-Arbitrary-Header": "unused-value"})
    )
    assert trusted is True
    assert untrusted is False


def test_extract_trusted_model_override_accepts_trimmed_value_for_trusted_request():
    from src.gateway import webchat

    request = _HeaderOnlyRequest({"X-Portal-Author-Source": "portal"})
    assert webchat._extract_trusted_model_override(request, {"model_override": "  gpt-5  "}) == "gpt-5"


def test_extract_trusted_model_override_ignores_untrusted_request():
    from src.gateway import webchat

    request = _HeaderOnlyRequest({})
    assert webchat._extract_trusted_model_override(request, {"model_override": "gpt-5"}) is None


def test_resolve_webchat_session_id_avoids_same_second_collisions_without_client_session():
    from src.gateway import webchat

    first = webchat._resolve_webchat_session_id({})
    second = webchat._resolve_webchat_session_id({})
    assert first != second
    assert first.startswith("webchat_")
    assert second.startswith("webchat_")
    assert webchat._resolve_webchat_session_id({"session_id": "  s-1  "}) == "s-1"
    assert webchat._resolve_webchat_session_id({"session_id": ""}).startswith("webchat_")


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
        return type("R", (), {"status": "success", "output_payload": {"response": "ok"}, "runtime_events": []})()

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
async def test_chat_execution_bus_adapter_does_not_mutate_execution_output_payload(monkeypatch):
    from src.gateway import webchat

    captured = {}
    original_payload = {"response": "ok"}

    async def _fake_execute_chat_orchestration(**kwargs):
        execution_result = type("R", (), {"status": "success", "output_payload": original_payload, "runtime_events": []})()
        captured["execution_result"] = execution_result
        return execution_result

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)
    monkeypatch.setattr(webchat, "run_chat_execution", lambda *args, **kwargs: {"response": "ignored"})

    result = await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
    )

    execution_result = captured["execution_result"]
    assert result["_execution_result"] is execution_result
    assert "_execution_result" not in execution_result.output_payload
    assert result is not execution_result.output_payload


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_backfills_runtime_events_from_execution_result(monkeypatch):
    from src.gateway import webchat

    async def _fake_execute_chat_orchestration(**kwargs):
        return type(
            "R",
            (),
            {
                "status": "success",
                "output_payload": {"response": "ok"},
                "request_id": "req-1",
                "runtime_events": [{"event_type": "context_snapshot"}],
            },
        )()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)
    monkeypatch.setattr(webchat, "run_chat_execution", lambda *args, **kwargs: {"response": "ignored"})

    result = await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
    )

    assert result["runtime_events"][0]["event_type"] == "context_snapshot"


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_forwards_agent_id(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_execute_chat_orchestration(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"status": "success", "output_payload": {"response": "ok"}, "runtime_events": []})()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)
    monkeypatch.setattr(webchat, "run_chat_execution", lambda *args, **kwargs: {"response": "ignored"})

    await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
        agent_id="agent-77",
    )
    assert captured["agent_id"] == "agent-77"


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_merges_execution_metadata_without_overriding_path(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_execute_chat_orchestration(**kwargs):
        captured.update(kwargs)
        return type("R", (), {"status": "success", "output_payload": {"response": "ok"}, "runtime_events": []})()

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
async def test_chat_execution_bus_handler_uses_execution_request_metadata_for_agent(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_execution(agent, **kwargs):
        captured.update(kwargs)
        return {"response": "ok"}

    async def _fake_execute_chat_orchestration(**kwargs):
        req = type(
            "Req",
            (),
            {
                "input_payload": kwargs["input_payload"],
                "metadata": {"allowed_capability_ids": ["tool:jira_search"], "llm_tool_loop": {"one_tool_per_turn": True}},
                "request_id": kwargs["request_id"],
                "session_id": kwargs["session_id"],
            },
        )()
        output_payload = await kwargs["chat_handler"](req)
        return type("R", (), {"status": "success", "output_payload": output_payload, "runtime_events": []})()

    monkeypatch.setattr(webchat, "run_chat_execution", _fake_run_chat_execution)
    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)

    await webchat._run_chat_via_execution_bus(
        agent=object(),
        session_id="s-chat",
        message="hello",
        user_name="u1",
        portal_user_id=None,
        portal_user_name=None,
        execution_metadata={"allowed_capability_ids": ["tool:ignored_by_handler"]},
    )

    assert captured["execution_metadata"]["allowed_capability_ids"] == ["tool:jira_search"]
    assert captured["execution_metadata"]["llm_tool_loop"]["one_tool_per_turn"] is True


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
    import json
    from aiohttp import web
    from src.gateway import webchat

    async def _fake_execute_chat_orchestration(**kwargs):
        return type(
            "R",
            (),
            {
                "status": "error",
                "request_id": "chat-structured-1",
                "output_payload": {
                    "error": "Model output was truncated because max_output_tokens was reached.",
                    "error_type": "truncated_response",
                    "code": "max_output_tokens_exceeded",
                    "details": {"incomplete_reason": "max_output_tokens"},
                    "status_code": 500,
                },
                "runtime_events": [],
            },
        )()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)

    async def _fake_run_chat_execution(*args, **kwargs):
        return {"response": "ignored"}

    monkeypatch.setattr(webchat, "run_chat_execution", _fake_run_chat_execution)

    with pytest.raises(web.HTTPInternalServerError) as exc_info:
        await webchat._run_chat_via_execution_bus(
            agent=object(),
            session_id="s-chat",
            message="hello",
            user_name="u1",
            portal_user_id=None,
            portal_user_name=None,
        )
    body = json.loads(exc_info.value.text)
    assert body["error"]
    assert body["detail"] == body["error"]
    assert body["error_type"] == "truncated_response"
    assert body["code"] == "max_output_tokens_exceeded"
    assert body["details"]["incomplete_reason"] == "max_output_tokens"
    assert body["request_id"] == "chat-structured-1"


@pytest.mark.asyncio
async def test_chat_execution_bus_adapter_raises_on_legacy_string_error(monkeypatch):
    import json
    from aiohttp import web
    from src.gateway import webchat

    async def _fake_execute_chat_orchestration(**kwargs):
        return type(
            "R",
            (),
            {
                "status": "error",
                "request_id": "chat-legacy-1",
                "output_payload": {"error": "legacy failure"},
                "runtime_events": [],
            },
        )()

    monkeypatch.setattr(webchat, "execute_chat_orchestration", _fake_execute_chat_orchestration)
    monkeypatch.setattr(webchat, "run_chat_execution", lambda *args, **kwargs: {"response": "ignored"})

    with pytest.raises(web.HTTPInternalServerError) as exc_info:
        await webchat._run_chat_via_execution_bus(
            agent=object(),
            session_id="s-chat",
            message="hello",
            user_name="u1",
            portal_user_id=None,
            portal_user_name=None,
        )

    body = json.loads(exc_info.value.text)
    assert body["error"] == "legacy failure"
    assert body["detail"] == "legacy failure"
    assert body["error_type"] == ""
    assert body["code"] == ""
    assert body["details"] == {}
    assert body["request_id"] == "chat-legacy-1"


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
async def test_api_chat_failure_persists_system_error_and_failed_metadata(monkeypatch):
    from src.gateway import webchat

    add_message_calls = []
    metadata_calls = []

    monkeypatch.setattr(
        webchat.global_config,
        "_config",
        {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}},
        raising=False,
    )
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Agent One"))
    async def _failing_run_chat_via_execution_bus(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _failing_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_add_message(session_id, role, content, wait_for_save=False, extra=None):
        add_message_calls.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "wait_for_save": wait_for_save,
                "extra": extra or {},
            }
        )
        return "msg-system-error"

    async def _fake_publish_session_metadata(**kwargs):
        metadata_calls.append(kwargs)

    monkeypatch.setattr(webchat.session_manager, "add_message", _fake_add_message)
    monkeypatch.setattr(webchat, "publish_session_metadata", _fake_publish_session_metadata)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-failure"}

    response = await webchat.api_chat(_Request())
    payload = json.loads(response.text)

    assert response.status == 500
    assert "boom" in payload["error"] or payload["error"]
    assert payload["request_id"]
    assert payload["session_id"] == "s-failure"
    assert len(add_message_calls) == 1
    assert add_message_calls[0]["session_id"] == "s-failure"
    assert add_message_calls[0]["role"] == "assistant"
    assert add_message_calls[0]["wait_for_save"] is True
    assert "System error" in add_message_calls[0]["content"]
    assert "boom" in add_message_calls[0]["content"]
    assert add_message_calls[0]["extra"]["author_name"] == "System"
    assert add_message_calls[0]["extra"]["metadata"]["kind"] == "system_error"
    assert add_message_calls[0]["extra"]["metadata"]["exclude_from_model_context"] is True
    assert add_message_calls[0]["extra"]["metadata"]["ui_hint"] == "system_error"
    assert "boom" in add_message_calls[0]["extra"]["metadata"]["display_message"]
    assert metadata_calls
    assert metadata_calls[-1]["latest_event_type"] == "chat.failed"
    assert metadata_calls[-1]["latest_event_state"] == "error"


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
async def test_api_chat_stream_failure_persists_system_error_state(monkeypatch):
    from src.gateway import webchat

    persist_calls = []

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            self.writes.append(data.decode())

    async def _fake_run_chat_via_execution_bus(**kwargs):
        raise RuntimeError("stream boom")

    async def _fake_persist_chat_failure_state(**kwargs):
        persist_calls.append(kwargs)

    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Agent One"))
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "_persist_chat_failure_state", _fake_persist_chat_failure_state)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)
    monkeypatch.setattr(
        webchat.global_config,
        "_config",
        {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}},
        raising=False,
    )

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello stream", "session_id": "s-stream-failure"}

    response = await webchat.api_chat_stream(_Request())

    assert isinstance(response, _FakeStreamResponse)
    assert len(persist_calls) == 1
    call = persist_calls[0]
    assert call["agent_id"] == "agent-1"
    assert call["session_id"] == "s-stream-failure"
    assert call["request_id"]
    assert "stream boom" in call["user_message"]
    assert call["error_type"] == "RuntimeError"
    assert isinstance(call["metadata"], dict)


def test_webchat_source_guards_against_chat_metadata_event_name_drift():
    source = (Path(__file__).parent.parent / "src" / "gateway" / "webchat.py").read_text(encoding="utf-8")
    assert 'latest_event_type="chat.started"' in source
    assert 'default_event_type="chat.completed"' in source
    assert 'latest_event_type="chat.failed"' in source


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
        headers = {"X-Portal-Author-Source": "portal", "X-Portal-User-Id": " hdr-user \r\n", "X-Portal-User-Name": " hdr-name\t"}

        async def json(self):
            return {"message": "hello", "session_id": "s1"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] == "hdr-user"
    assert captured["portal_user_name"] == "hdr-name"


@pytest.mark.asyncio
async def test_api_chat_uses_trusted_portal_agent_name_for_assistant_author(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {
            "response": "ok",
            "usage": {},
            "author_name": kwargs["agent"].agent_name,
            "author_id": kwargs["agent"].agent_id,
            "author_type": "agent",
            "author_source": "runtime",
            "_execution_result": object(),
        }

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal", "X-Portal-Agent-Name": "Portal Agent"}

        async def json(self):
            return {"message": "hello", "session_id": "s-portal-agent-name"}

    resp = await webchat.api_chat(_Request())
    payload = json.loads(resp.text)
    assert resp.status == 200
    assert captured["agent"].agent_name == "Portal Agent"
    assert payload["author_name"] == "Portal Agent"
    assert payload["author_id"] == captured["agent"].agent_id


@pytest.mark.asyncio
async def test_api_chat_chatlog_includes_request_status_runtime_events_and_context_state(monkeypatch, tmp_path):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "ok",
            "usage": {},
            "request_id": "req-chatlog-1",
            "runtime_events": [{"event_type": "context_snapshot"}],
            "context_state": {"budget": {"usage_percent": 42.0}},
            "_execution_result": object(),
        }

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(
        webchat.global_config,
        "_config",
        {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}},
        raising=False,
    )
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(
        webchat.session_manager,
        "get_session",
        lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}),
    )
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(webchat.session_persistence, "storage_dir", tmp_path)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-chatlog"}

    response = await webchat.api_chat(_Request())
    assert response.status == 200

    chatlog_file = tmp_path / "chatlogs" / "s-chatlog.json"
    assert chatlog_file.exists()
    chatlog = json.loads(chatlog_file.read_text())
    assert chatlog["request_id"] == "req-chatlog-1"
    assert chatlog["status"] == "success"
    assert isinstance(chatlog["runtime_events"], list)
    assert isinstance(chatlog["context_state"], dict)


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
        headers = {"X-Portal-Author-Source": "portal", "X-Portal-User-Id": "stream-user", "X-Portal-User-Name": "stream-name"}

        async def json(self):
            return {"message": "hello", "session_id": "s2"}

    resp = await webchat.api_chat_stream(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] == "stream-user"
    assert captured["portal_user_name"] == "stream-name"


@pytest.mark.asyncio
async def test_api_chat_stream_uses_trusted_portal_agent_name_for_agent_identity(monkeypatch):
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
    monkeypatch.setattr(
        webchat.global_config,
        "_config",
        {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}},
        raising=False,
    )
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal", "X-Portal-Agent-Name": "Portal Agent"}

        async def json(self):
            return {"message": "hello", "session_id": "s-stream-agent-name"}

    response = await webchat.api_chat_stream(_Request())
    assert response.status == 200
    assert captured["agent"].agent_name == "Portal Agent"


@pytest.mark.asyncio
async def test_api_chat_uses_trusted_model_override_when_present(monkeypatch):
    from src.gateway import webchat

    captured = {}

    class _FakeAgentCore:
        def __init__(self, model, session_id, agent_id, agent_name):
            captured["model"] = model
            self.model = model
            self.agent_id = agent_id
            self.agent_name = agent_name

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(webchat, "AgentCore", _FakeAgentCore)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "default-model", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-model-1", "model_override": "gpt-5-override"}

    response = await webchat.api_chat(_Request())
    assert response.status == 200
    assert captured["model"] == "gpt-5-override"


@pytest.mark.asyncio
async def test_api_chat_ignores_model_override_for_untrusted_request(monkeypatch):
    from src.gateway import webchat

    captured = {}

    class _FakeAgentCore:
        def __init__(self, model, session_id, agent_id, agent_name):
            captured["model"] = model
            self.model = model
            self.agent_id = agent_id
            self.agent_name = agent_name

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(webchat, "AgentCore", _FakeAgentCore)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "default-model", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-model-2", "model_override": "gpt-5-override"}

    response = await webchat.api_chat(_Request())
    assert response.status == 200
    assert captured["model"] == "default-model"


@pytest.mark.asyncio
async def test_api_chat_stream_uses_trusted_model_override_when_present(monkeypatch):
    from src.gateway import webchat

    captured = {}

    class _FakeAgentCore:
        def __init__(self, model, session_id, agent_id, agent_name):
            captured["model"] = model
            self.model = model
            self.agent_id = agent_id
            self.agent_name = agent_name

    async def _fake_run_chat_via_execution_bus(**kwargs):
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

    monkeypatch.setattr(webchat, "AgentCore", _FakeAgentCore)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "default-model", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-model-stream-1", "model_override": "gpt-5-override"}

    response = await webchat.api_chat_stream(_Request())
    assert response.status == 200
    assert captured["model"] == "gpt-5-override"


@pytest.mark.asyncio
async def test_api_chat_stream_ignores_model_override_for_untrusted_request(monkeypatch):
    from src.gateway import webchat

    captured = {}

    class _FakeAgentCore:
        def __init__(self, model, session_id, agent_id, agent_name):
            captured["model"] = model
            self.model = model
            self.agent_id = agent_id
            self.agent_name = agent_name

    async def _fake_run_chat_via_execution_bus(**kwargs):
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

    monkeypatch.setattr(webchat, "AgentCore", _FakeAgentCore)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "default-model", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s-model-stream-2", "model_override": "gpt-5-override"}

    response = await webchat.api_chat_stream(_Request())
    assert response.status == 200
    assert captured["model"] == "default-model"


@pytest.mark.asyncio
async def test_api_chat_usage_tracker_records_actual_override_model(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "ok",
            "usage": {"prompt_tokens": 5, "completion_tokens": 6},
            "_llm_debug": {"request": {"model": "gpt-5-actual"}},
        }

    def _fake_record_usage(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.usage_tracker, "record_usage", _fake_record_usage)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "default-model", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-usage-1", "model_override": "gpt-5-override"}

    response = await webchat.api_chat(_Request())
    assert response.status == 200
    assert captured["model"] == "gpt-5-actual"


@pytest.mark.asyncio
async def test_api_chat_stream_usage_tracker_records_actual_override_model(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "ok",
            "usage": {"prompt_tokens": 5, "completion_tokens": 6},
            "_llm_debug": {"request": {"model": "gpt-5-actual"}},
        }

    def _fake_record_usage(**kwargs):
        captured.update(kwargs)

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
    monkeypatch.setattr(webchat.usage_tracker, "record_usage", _fake_record_usage)
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "default-model", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-usage-2", "model_override": "gpt-5-override"}

    response = await webchat.api_chat_stream(_Request())
    assert response.status == 200
    assert captured["model"] == "gpt-5-actual"


@pytest.mark.asyncio
async def test_api_chat_forwards_all_attached_images(monkeypatch, tmp_path):
    from src.gateway import webchat
    from src.utils.file_parser import storage

    captured = {}
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    one.write_bytes(b"one")
    two.write_bytes(b"two")

    class _Meta:
        def __init__(self, file_id: str, session_id: str):
            self.file_id = file_id
            self.session_id = session_id
            self.content_type = "image/png"

    metadata_map = {"f1": _Meta("f1", "s1"), "f2": _Meta("f2", "s1")}
    file_map = {"f1": one, "f2": two}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(webchat, "get_metadata", lambda file_id: metadata_map[file_id])
    monkeypatch.setattr(storage, "get_file_path", lambda file_id: file_map[file_id])

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}

    response = await webchat.api_chat(_Request())
    assert response.status == 200
    assert len(captured["attached_images"]) == 2
    assert captured["attached_images"][0].startswith("data:image/png;base64,")
    assert captured["attached_images"][1].startswith("data:image/png;base64,")
    assert captured["attached_images"][0].endswith("b25l")
    assert captured["attached_images"][1].endswith("dHdv")


@pytest.mark.asyncio
async def test_api_chat_stream_forwards_all_attached_images(monkeypatch, tmp_path):
    from src.gateway import webchat
    from src.utils.file_parser import storage

    captured = {}
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    one.write_bytes(b"one")
    two.write_bytes(b"two")

    class _Meta:
        def __init__(self, file_id: str, session_id: str):
            self.file_id = file_id
            self.session_id = session_id
            self.content_type = "image/png"

    metadata_map = {"f1": _Meta("f1", "s2"), "f2": _Meta("f2", "s2")}
    file_map = {"f1": one, "f2": two}

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
    monkeypatch.setattr(webchat, "get_metadata", lambda file_id: metadata_map[file_id])
    monkeypatch.setattr(storage, "get_file_path", lambda file_id: file_map[file_id])

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "compare", "session_id": "s2", "attachments": ["f1", "f2"]}

    response = await webchat.api_chat_stream(_Request())
    assert response.status == 200
    assert len(captured["attached_images"]) == 2
    assert captured["attached_images"][0].endswith("b25l")
    assert captured["attached_images"][1].endswith("dHdv")
    assert captured["attachments"] is None


@pytest.mark.asyncio
async def test_api_chat_forwards_all_attached_images_without_local_cap_config(monkeypatch, tmp_path):
    from src.gateway import webchat
    from src.utils.file_parser import storage

    captured = {}
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    one.write_bytes(b"one")
    two.write_bytes(b"two")

    class _Meta:
        def __init__(self, file_id: str, session_id: str):
            self.file_id = file_id
            self.session_id = session_id
            self.content_type = "image/png"

    metadata_map = {"f1": _Meta("f1", "s1"), "f2": _Meta("f2", "s1")}
    file_map = {"f1": one, "f2": two}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(webchat, "get_metadata", lambda file_id: metadata_map[file_id])
    monkeypatch.setattr(storage, "get_file_path", lambda file_id: file_map[file_id])

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}

    response = await webchat.api_chat(_Request())
    assert response.status == 200
    assert len(captured["attached_images"]) == 2


@pytest.mark.asyncio
async def test_api_chat_trusted_portal_metadata_passed_to_execution_bus(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

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
async def test_api_chat_best_effort_publishes_session_metadata(monkeypatch):
    from src.gateway import webchat

    published = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "ok",
            "usage": {},
            "_execution_result": type(
                "R",
                (),
                {"request_id": "exec-1", "status": "success", "runtime_events": [], "artifacts": {}, "output_payload": {}},
            )(),
        }

    async def _fake_publish_session_metadata(**kwargs):
        published.update(kwargs)

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "publish_session_metadata", _fake_publish_session_metadata)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Agent One"))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s-meta-chat"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert published["agent_id"] == "agent-1"
    assert published["session_id"] == "s-meta-chat"
    assert published["last_execution_id"] == "exec-1"
    assert published["latest_event_state"] == "success"


@pytest.mark.asyncio
async def test_api_chat_trusted_client_request_id_forwarded_and_started_metadata_published(monkeypatch):
    from src.gateway import webchat

    captured = {}
    publish_calls = []

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {
            "response": "ok",
            "usage": {},
            "request_id": kwargs["request_id"],
            "_execution_result": type(
                "R",
                (),
                {"request_id": kwargs["request_id"], "status": "success", "runtime_events": [], "artifacts": {}, "output_payload": {}},
            )(),
        }

    async def _fake_publish_session_metadata(**kwargs):
        publish_calls.append(kwargs)

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "publish_session_metadata", _fake_publish_session_metadata)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Agent One"))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-trusted-id", "client_request_id": "portal-chat-req-1"}

    resp = await webchat.api_chat(_Request())
    payload = json.loads(resp.text)
    assert resp.status == 200
    assert captured["request_id"] == "portal-chat-req-1"
    assert len(publish_calls) >= 2
    assert publish_calls[0]["latest_event_type"] == "chat.started"
    assert publish_calls[0]["latest_event_state"] == "running"
    assert publish_calls[0]["last_execution_id"] == "portal-chat-req-1"
    assert publish_calls[1]["last_execution_id"] == "portal-chat-req-1"
    assert payload["request_id"] == "portal-chat-req-1"


@pytest.mark.asyncio
async def test_api_chat_untrusted_client_request_id_not_accepted(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

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
            return {"message": "hello", "session_id": "s-untrusted-id", "client_request_id": "attacker-id"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["request_id"] != "attacker-id"
    assert captured["request_id"].startswith("chat-")


@pytest.mark.asyncio
async def test_api_chat_publish_failure_does_not_break_response(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "ok",
            "usage": {},
            "_execution_result": type(
                "R",
                (),
                {"request_id": "exec-2", "status": "success", "runtime_events": [], "artifacts": {}, "output_payload": {}},
            )(),
        }

    async def _failing_publish_session_metadata(**_kwargs):
        raise RuntimeError("portal unavailable")

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "publish_session_metadata", _failing_publish_session_metadata)
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-2", "Agent Two"))
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(webchat.session_persistence, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s-meta-chat-fail"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200




@pytest.mark.asyncio
async def test_api_chat_stream_emits_progress_before_done_while_task_running(monkeypatch):
    from src.gateway import webchat

    observed = {"task_running_during_progress": False}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        stream_callback = kwargs["stream_callback"]
        await stream_callback.put('{"type":"progress","step":1}')
        await asyncio.sleep(0.2)
        return {"response": "ok", "usage": {}}

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            decoded = data.decode()
            if "event: progress" in decoded:
                pending_tasks = [t for t in asyncio.all_tasks() if not t.done()]
                observed["task_running_during_progress"] = any(
                    getattr(getattr(t, "get_coro", lambda: None)(), "__name__", "") == "_fake_run_chat_via_execution_bus"
                    for t in pending_tasks
                )
            self.writes.append(decoded)

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)
    monkeypatch.setattr(webchat.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}}, raising=False)

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s-live-stream"}

    response = await webchat.api_chat_stream(_Request())

    progress_index = next(i for i, chunk in enumerate(response.writes) if "event: progress" in chunk)
    done_index = next(i for i, chunk in enumerate(response.writes) if "event: done" in chunk)
    assert progress_index < done_index
    assert observed["task_running_during_progress"] is True


@pytest.mark.asyncio
async def test_api_chat_stream_emits_final_payload_before_done(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**_kwargs):
        return {
            "response": "full assistant response",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            "events": [{"type": "llm_thinking", "message": "thinking"}],
            "runtime_events": [{"event_type": "execution.completed"}],
            "context_state": {"summary": "done", "next_step": ""},
        }

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            self.writes.append(data.decode())

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s-final-payload"}

    response = await webchat.api_chat_stream(_Request())

    final_index = next(i for i, chunk in enumerate(response.writes) if "event: final" in chunk)
    done_index = next(i for i, chunk in enumerate(response.writes) if "event: done" in chunk)
    assert final_index < done_index

    final_chunk = response.writes[final_index]
    final_data = json.loads(final_chunk.split("data: ", 1)[1].strip())
    assert final_data["response"] == "full assistant response"
    assert final_data["session_id"] == "s-final-payload"
    assert final_data["request_id"]
    start_chunk = next(chunk for chunk in response.writes if "event: start" in chunk)
    start_data = json.loads(start_chunk.split("data: ", 1)[1].strip())
    assert final_data["request_id"] == start_data["request_id"]
    assert final_data["usage"] == {"prompt_tokens": 1, "completion_tokens": 2}
    assert final_data["events"] == [{"type": "llm_thinking", "message": "thinking"}]
    assert final_data["runtime_events"] == [{"event_type": "execution.completed"}]
    assert final_data["context_state"] == {"summary": "done", "next_step": ""}


@pytest.mark.asyncio
async def test_api_chat_stream_done_event_has_json_payload_not_blank(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**_kwargs):
        return {"response": "ok", "usage": {}}

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            self.writes.append(data.decode())

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s-done-json"}

    response = await webchat.api_chat_stream(_Request())

    done_chunk = next(chunk for chunk in response.writes if "event: done" in chunk)
    assert "data: \n\n" not in done_chunk
    done_data = json.loads(done_chunk.split("data: ", 1)[1].strip())
    assert done_data["ok"] is True
    assert done_data["session_id"] == "s-done-json"
    assert done_data["request_id"]
    start_chunk = next(chunk for chunk in response.writes if "event: start" in chunk)
    start_data = json.loads(start_chunk.split("data: ", 1)[1].strip())
    assert done_data["request_id"] == start_data["request_id"]
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
async def test_api_chat_stream_start_event_contains_request_id_and_forwards_same_id(monkeypatch):
    from src.gateway import webchat

    captured = {}
    publish_calls = []

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {
            "response": "ok",
            "usage": {},
            "_execution_result": type(
                "R",
                (),
                {"request_id": kwargs["request_id"], "status": "success", "runtime_events": [], "artifacts": {}, "output_payload": {}},
            )(),
        }

    async def _fake_publish_session_metadata(**kwargs):
        publish_calls.append(kwargs)

    class _FakeStreamResponse:
        def __init__(self, status=200, headers=None):
            self.status = status
            self.headers = headers or {}
            self.writes = []

        async def prepare(self, request):
            return self

        async def write(self, data):
            self.writes.append(data.decode())

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat, "publish_session_metadata", _fake_publish_session_metadata)
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-stream", "Agent Stream"))
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-stream-req", "client_request_id": "portal-stream-req-1"}

    resp = await webchat.api_chat_stream(_Request())
    assert resp.status == 200
    start_chunk = next(chunk for chunk in resp.writes if "event: start" in chunk)
    start_data = json.loads(start_chunk.split("data: ", 1)[1].strip())
    assert start_data["session_id"] == "s-stream-req"
    assert start_data["request_id"] == "portal-stream-req-1"
    assert captured["request_id"] == "portal-stream-req-1"
    assert len(publish_calls) >= 2
    assert publish_calls[0]["latest_event_type"] == "chat.started"
    assert publish_calls[0]["latest_event_state"] == "running"


@pytest.mark.asyncio
async def test_api_chat_stream_first_start_event_request_id_matches_execution_request_id(monkeypatch):
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
            self.writes.append(data.decode())

    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s-stream-contract", "client_request_id": "portal-stream-req-1"}

    resp = await webchat.api_chat_stream(_Request())
    assert resp.status == 200
    assert resp.writes
    assert resp.writes[0].startswith("event: start")
    start_data = json.loads(resp.writes[0].split("data: ", 1)[1].strip())
    assert start_data["session_id"] == "s-stream-contract"
    assert start_data["request_id"] == "portal-stream-req-1"
    assert captured["request_id"] == "portal-stream-req-1"


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
async def test_api_chat_untrusted_portal_identity_is_ignored_and_trusted_header_precedence(monkeypatch):
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

    class _UntrustedBodyIdentityRequest:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "hello", "session_id": "s3", "portal_user_id": "body-id", "portal_user_name": "body-name", "user_name": "cli-user"}

    await webchat.api_chat(_UntrustedBodyIdentityRequest())
    assert captured["portal_user_id"] is None
    assert captured["portal_user_name"] is None
    assert captured["user_name"] == "cli-user"

    class _TrustedBodyOnlyRequest:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {"message": "hello", "session_id": "s3b", "portal_user_id": "body-only-id", "portal_user_name": "body-only-name"}

    await webchat.api_chat(_TrustedBodyOnlyRequest())
    assert captured["portal_user_id"] is None
    assert captured["portal_user_name"] is None

    class _TrustedConflictRequest:
        app = {}
        headers = {"X-Portal-Author-Source": "portal", "X-Portal-User-Id": "header-id", "X-Portal-User-Name": "header-name"}

        async def json(self):
            return {"message": "hello", "session_id": "s4", "portal_user_id": "body-id", "portal_user_name": "body-name"}

    await webchat.api_chat(_TrustedConflictRequest())
    assert captured["portal_user_id"] == "header-id"
    assert captured["portal_user_name"] == "header-name"


@pytest.mark.asyncio
async def test_api_chat_direct_runtime_user_name_does_not_become_portal_identity(monkeypatch):
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
        headers = {}

        async def json(self):
            return {
                "message": "hello",
                "session_id": "s-direct-identity",
                "user_name": "cli-user",
                "portal_user_name": "fake-portal",
            }

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] is None
    assert captured["portal_user_name"] is None
    assert captured["user_name"] == "cli-user"


@pytest.mark.asyncio
async def test_portal_trust_uses_portal_source_header_only(monkeypatch):
    from src.gateway import webchat

    captured = {}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

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
            "X-Portal-User-Id": "portal-u",
            "X-Portal-User-Name": "portal-name",
        }

        async def json(self):
            return {"message": "hello", "session_id": "s-portal-cfg", "metadata": {"allowed_actions": ["adapter:portal:get_group_task_board"]}}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] == "portal-u"
    assert captured["portal_user_name"] == "portal-name"
    assert captured["execution_metadata"]["allowed_actions"] == ["adapter:portal:get_group_task_board"]


@pytest.mark.asyncio
async def test_api_chat_stream_trusted_request_does_not_accept_body_portal_identity(monkeypatch):
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
    monkeypatch.setattr(webchat.web, "StreamResponse", _FakeStreamResponse)

    class _Request:
        app = {}
        headers = {"X-Portal-Author-Source": "portal"}

        async def json(self):
            return {
                "message": "hello",
                "session_id": "s-stream-identity",
                "portal_user_id": "body-id",
                "portal_user_name": "body-name",
            }

    resp = await webchat.api_chat_stream(_Request())
    assert resp.status == 200
    assert captured["portal_user_id"] is None
    assert captured["portal_user_name"] is None


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
    assert "/api/tasks/{task_id}/cancel" in routes
    assert "/api/tasks/{task_id}" in routes
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
        headers = INTERNAL_HEADERS
        query = {"type": "tool", "enabled": "true"}

    response = await webchat.api_capabilities(_Request())
    body = json.loads(response.body)
    assert response.status == 200
    assert body["count"] == 1
    assert body["capabilities"][0]["capability_id"] == "tool:read"
    assert body["catalog_version"] == "v-snap"
    assert body["generated_at"] == "2026-04-07T00:00:00Z"
    assert body["supports_snapshot_contract"] is True
    assert "tool_repo_url" not in body
    assert "tool_branch" not in body


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
        headers = INTERNAL_HEADERS
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
        headers = INTERNAL_HEADERS
        query = {"capability_id": "tool:write"}

    by_id_response = await webchat.api_capabilities(_ByCapabilityIdRequest())
    by_id_body = json.loads(by_id_response.body)
    assert by_id_response.status == 200
    assert by_id_body["count"] == 1
    assert by_id_body["capabilities"] == [{"capability_id": "tool:write", "type": "tool", "enabled": True}]

    class _ByTypeRequest:
        headers = INTERNAL_HEADERS
        query = {"type": "adapter_action"}

    by_type_response = await webchat.api_capabilities(_ByTypeRequest())
    by_type_body = json.loads(by_type_response.body)
    assert by_type_response.status == 200
    assert by_type_body["count"] == 1
    assert by_type_body["capabilities"][0]["type"] == "adapter_action"


@pytest.mark.asyncio
async def test_api_capabilities_accepts_default_request_headers(monkeypatch):
    from src.gateway import webchat

    class _Registry:
        def export_catalog_snapshot(self):
            return {"capabilities": [{"capability_id": "tool:read", "type": "tool", "enabled": True}], "count": 1, "catalog_version": "v", "generated_at": "2026-04-07T00:00:00Z"}

    monkeypatch.setattr(webchat, "get_capability_registry", lambda: _Registry())

    class _Request:
        headers = {}
        query = {}

    response = await webchat.api_capabilities(_Request())
    assert response.status == 200


@pytest.mark.asyncio
async def test_api_capabilities_ignores_unrecognized_header(monkeypatch):
    from src.gateway import webchat


    class _Registry:
        def export_catalog_snapshot(self):
            return {"capabilities": [{"capability_id": "tool:read", "type": "tool", "enabled": True}], "count": 1, "catalog_version": "v", "generated_at": "2026-04-07T00:00:00Z"}

    monkeypatch.setattr(webchat, "get_capability_registry", lambda: _Registry())

    class _Request:
        headers = {"X-Arbitrary-Header": "ignored"}
        query = {}

    response = await webchat.api_capabilities(_Request())
    assert response.status == 200


@pytest.mark.asyncio
async def test_api_skills_succeeds_when_external_tools_dir_is_empty(monkeypatch, tmp_path):
    from src.gateway import webchat
    from src.tools_external import reset_external_tool_registry_cache

    empty_tools = tmp_path / "empty-tools"
    empty_tools.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EFP_TOOLS_DIR", str(empty_tools))
    reset_external_tool_registry_cache()

    class _Request:
        headers = {}
        query = {}

    response = await webchat.api_skills(_Request())
    body = json.loads(response.body)
    assert response.status == 200
    assert "skills" in body


@pytest.mark.asyncio
async def test_api_tasks_execute_accepts_default_request_headers(monkeypatch):
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()

    class _Request:
        headers = {}

        async def json(self):
            return {"task_id": "task-open-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    async def _fake_execute_runtime_task_request(**kwargs):
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    spawned = []
    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    response = await webchat.api_tasks_execute(_Request())
    assert response.status == 202
    await spawned[0]


@pytest.mark.asyncio
async def test_api_tasks_execute_not_configured_still_accepts_request(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.global_config, "get", lambda *_args, **_kwargs: "")
    webchat.runtime_task_tracker.reset()

    class _Request:
        headers = {"X-Trace-Id": "trace-auth-503", "X-Portal-Dispatch-Id": "dispatch-1"}

        async def json(self):
            return {"task_id": "task-open-2", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    async def _fake_execute_runtime_task_request(**kwargs):
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    spawned = []
    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    response = await webchat.api_tasks_execute(_Request())

    assert response.status == 202
    await spawned[0]


@pytest.mark.asyncio
async def test_api_tasks_execute_accepts_empty_headers(monkeypatch):
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()

    class _Request:
        headers = {}

        async def json(self):
            return {"task_id": "task-default-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    async def _fake_execute_runtime_task_request(**kwargs):
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    spawned = []
    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    response = await webchat.api_tasks_execute(_Request())
    assert response.status == 202
    await spawned[0]


@pytest.mark.asyncio
async def test_api_tasks_execute_ignores_unrecognized_header(monkeypatch):
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()

    class _Request:
        headers = {"X-Arbitrary-Header": "ignored"}

        async def json(self):
            return {"task_id": "task-sideband-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    async def _fake_execute_runtime_task_request(**kwargs):
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    spawned = []
    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    response = await webchat.api_tasks_execute(_Request())
    assert response.status == 202
    await spawned[0]


@pytest.mark.asyncio
async def test_api_tasks_execute_adapter_action_task_success(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()

    captured = {}
    published = {}
    spawned = []

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
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-task-1", "Task Agent"))
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    async def _fake_publish_session_metadata(**kwargs):
        published.update(kwargs)

    monkeypatch.setattr(webchat, "publish_session_metadata", _fake_publish_session_metadata)

    class _Request:
        headers = INTERNAL_HEADERS
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
    assert len(spawned) == 1
    await spawned[0]

    assert response.status == 202
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["task_id"] == "task-1"
    assert body["status"] == "accepted"
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
    assert captured["agent_id"] == "agent-task-1"
    assert published["agent_id"] == "agent-task-1"
    assert published["session_id"] == "session-1"
    assert published["last_execution_id"] == "task-task-1"
    assert published["latest_event_state"] == "success"

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-1"}

    status_response = await webchat.api_task_status(_StatusRequest())
    status_body = json.loads(status_response.body)
    assert status_response.status == 200
    assert status_body["status"] == "success"
    assert status_body["accepted_at"]
    assert status_body["started_at"]
    assert status_body["finished_at"]


@pytest.mark.asyncio
async def test_api_tasks_execute_jira_workflow_review_task_success(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()
    spawned = []

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
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = INTERNAL_HEADERS
        async def json(self):
            return {
                "task_id": "task-2",
                "task_type": "jira_workflow_review_task",
                "input_payload": {"issue_key": "ENG-2"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    await spawned[0]

    assert response.status == 202
    assert body["task_id"] == "task-2"
    assert body["execution_type"] == "task"
    assert body["status"] == "accepted"

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-2"}

    status_response = await webchat.api_task_status(_StatusRequest())
    status_body = json.loads(status_response.body)
    assert status_body["status"] == "success"


@pytest.mark.asyncio
async def test_api_tasks_execute_github_review_task_reaches_execution_bus(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()
    spawned = []

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
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = INTERNAL_HEADERS
        async def json(self):
            return {
                "task_id": "gh-task-1",
                "task_type": "github_review_task",
                "input_payload": {"owner": "acme", "repo": "demo", "pull_number": 33},
                "metadata": {"trace_id": "t-1"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    await spawned[0]

    assert response.status == 202
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["task_id"] == "gh-task-1"
    assert body["execution_type"] == "task"
    assert body["status"] == "accepted"
    assert captured["input_payload"]["task_type"] == "github_review_task"
    assert captured["metadata"]["task_id"] == "gh-task-1"

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "gh-task-1"}

    status_response = await webchat.api_task_status(_StatusRequest())
    status_body = json.loads(status_response.body)
    assert status_body["runtime_events"][0]["task_id"] == "gh-task-1"


@pytest.mark.asyncio
async def test_api_tasks_execute_github_review_task_portal_automation_payload_trace(monkeypatch):
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()
    spawned = []

    async def _fake_run_github_review_task(_payload):
        return {
            "success": True,
            "error": None,
            "review_summary": "ok",
            "review_event": "COMMENT",
            "review_written": True,
            "comment_written": True,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": "adapter:github:review_pull_request",
            "source": "automation_rule",
            "rule_id": "rule-1",
            "automation_rule_id": "rule-1",
            "dedupe_key": "github:pr_review_requested:rule-1:Acme/Portal:42:sha:team:Acme/Reviewers",
            "review_target": {"type": "team", "name": "Acme/Reviewers"},
            "runtime_events": [],
            "result": {"id": 123},
        }

    monkeypatch.setattr("src.runtime.execution_bus.run_github_review_task", _fake_run_github_review_task)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = INTERNAL_HEADERS

        async def json(self):
            return {
                "task_id": "task-1",
                "task_type": "github_review_task",
                "input_payload": {
                    "source": "automation_rule",
                    "automation_rule": "github.pr_review_requested",
                    "automation_rule_id": "rule-1",
                    "rule_id": "rule-1",
                    "provider": "github",
                    "owner": "Acme",
                    "repo": "Portal",
                    "pull_number": 42,
                    "head_sha": "sha-contract",
                    "review_target": {"type": "team", "name": "Acme/Reviewers"},
                    "task_type": "github_review_task",
                    "skill_name": "review-pull-request",
                    "review_event": "COMMENT",
                    "dedupe_key": "github:pr_review_requested:rule-1:Acme/Portal:42:sha:team:Acme/Reviewers",
                },
                "metadata": {
                    "identity_binding": {
                        "id": "binding-gh-1",
                        "system_type": "github",
                        "external_account_id": "reviewer-bot",
                    }
                },
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    await spawned[0]

    assert response.status == 202
    assert body["ok"] is True
    assert body["accepted"] is True
    assert body["task_id"] == "task-1"

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-1"}

    status_response = await webchat.api_task_status(_StatusRequest())
    status_body = json.loads(status_response.body)
    assert status_response.status == 200
    assert status_body["status"] == "success"
    assert status_body["output_payload"]["task_type"] == "github_review_task"
    assert status_body["output_payload"]["review_event"] == "COMMENT"
    assert status_body["output_payload"]["source"] == "automation_rule"
    assert status_body["output_payload"]["rule_id"] == "rule-1"
    assert status_body["output_payload"]["automation_rule_id"] == "rule-1"
    assert status_body["output_payload"]["dedupe_key"] == "github:pr_review_requested:rule-1:Acme/Portal:42:sha:team:Acme/Reviewers"
    assert status_body["output_payload"]["review_target"] == {"type": "team", "name": "Acme/Reviewers"}

    final_events = [evt for evt in status_body["runtime_events"] if evt.get("event_type") == "task.github_review.completed"]
    assert final_events
    detail_payload = final_events[-1].get("detail_payload") or {}
    assert detail_payload.get("automation_rule_id") == "rule-1"
    assert detail_payload.get("dedupe_key") == "github:pr_review_requested:rule-1:Acme/Portal:42:sha:team:Acme/Reviewers"


@pytest.mark.asyncio
async def test_api_tasks_execute_missing_task_type_returns_400(monkeypatch):
    from src.gateway import webchat

    class _Request:
        headers = INTERNAL_HEADERS
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
async def test_api_tasks_execute_non_object_input_payload_returns_400(monkeypatch):
    from src.gateway import webchat

    class _Request:
        headers = INTERNAL_HEADERS
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
async def test_api_tasks_execute_non_object_context_ref_returns_400(monkeypatch):
    from src.gateway import webchat

    class _Request:
        headers = INTERNAL_HEADERS
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
    webchat.runtime_task_tracker.reset()
    spawned = []

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
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = INTERNAL_HEADERS
        async def json(self):
            return {
                "task_id": "task-5",
                "task_type": "adapter_action_task",
                "input_payload": {"action_id": "jira.transition"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    await spawned[0]
    assert response.status == 202
    assert body["status"] == "accepted"

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-5"}

    status_response = await webchat.api_task_status(_StatusRequest())
    status_body = json.loads(status_response.body)
    assert status_body["ok"] is False
    assert status_body["status"] == "blocked"
    assert status_body["error"] == "blocked by policy"


@pytest.mark.asyncio
async def test_api_tasks_execute_tracing_headers_merge_to_metadata_and_response(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()

    captured = {}
    spawned = []

    async def _fake_execute_runtime_task_request(**kwargs):
        captured.update(kwargs)
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = {
            **INTERNAL_HEADERS,
            "X-Trace-Id": "trace-200",
            "X-Span-Id": "span-200",
            "X-Parent-Span-Id": "parent-200",
            "X-Portal-Task-Id": "portal-task-200",
            "X-Portal-Dispatch-Id": "dispatch-200",
        }

        async def json(self):
            return {
                "task_id": "task-200",
                "task_type": "adapter_action_task",
                "input_payload": {"action_id": "jira.transition"},
                "metadata": {"custom": "value"},
            }

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    await spawned[0]

    assert response.status == 202
    assert captured["metadata"]["trace_id"] == "trace-200"
    assert captured["metadata"]["span_id"] == "span-200"
    assert captured["metadata"]["parent_span_id"] == "parent-200"
    assert captured["metadata"]["portal_dispatch_id"] == "dispatch-200"
    assert captured["metadata"]["portal_task_id"] == "portal-task-200"
    assert body["trace_id"] == "trace-200"
    assert body["portal_dispatch_id"] == "dispatch-200"

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-200"}

    status_response = await webchat.api_task_status(_StatusRequest())
    status_body = json.loads(status_response.body)
    assert status_body["trace_id"] == "trace-200"
    assert status_body["portal_dispatch_id"] == "dispatch-200"


@pytest.mark.asyncio
async def test_api_tasks_execute_accepts_without_waiting_for_terminal_result(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()

    started = asyncio.Event()
    release = asyncio.Event()
    called = {"count": 0}
    spawned = []

    async def _fake_execute_runtime_task_request(**kwargs):
        called["count"] += 1
        started.set()
        await release.wait()
        return type(
            "R",
            (),
            {
                "request_id": kwargs["request_id"],
                "status": "success",
                "output_payload": {"success": True},
                "artifacts": {},
                "runtime_events": [],
                "next_action_hint": None,
                "audit_ref": None,
            },
        )()

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _fake_execute_runtime_task_request)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _Request:
        headers = INTERNAL_HEADERS
        async def json(self):
            return {"task_id": "task-async-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    assert response.status == 202
    assert body["status"] == "accepted"
    await started.wait()
    assert called["count"] == 1
    release.set()
    await spawned[0]


@pytest.mark.asyncio
async def test_api_task_status_pending_accepts_unrecognized_header(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()

    class _ExecuteRequest:
        headers = INTERNAL_HEADERS
        async def json(self):
            return {"task_id": "task-pending-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    never = asyncio.Event()

    async def _never_finishes(**kwargs):
        await never.wait()
        return kwargs

    spawned = []
    monkeypatch.setattr(webchat, "execute_runtime_task_request", _never_finishes)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    await webchat.api_tasks_execute(_ExecuteRequest())

    class _StatusBadAuth:
        headers = {"X-Arbitrary-Header": "ignored"}
        match_info = {"task_id": "task-pending-1"}

    bad_auth_response = await webchat.api_task_status(_StatusBadAuth())
    assert bad_auth_response.status == 200

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-pending-1"}

    status_response = await webchat.api_task_status(_StatusRequest())
    payload = json.loads(status_response.body)
    assert status_response.status == 200
    assert payload["status"] in {"accepted", "running"}
    assert payload["finished_at"] is None

    spawned[0].cancel()


@pytest.mark.asyncio
async def test_api_task_status_returns_error_payload_when_background_crashes(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()
    spawned = []

    async def _boom(**_kwargs):
        raise RuntimeError("runtime boom")

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _boom)
    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda coro: spawned.append(asyncio.create_task(coro)) or spawned[-1])

    class _ExecuteRequest:
        headers = INTERNAL_HEADERS
        async def json(self):
            return {"task_id": "task-fail-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    execute_response = await webchat.api_tasks_execute(_ExecuteRequest())
    assert execute_response.status == 202
    await spawned[0]

    class _StatusRequest:
        headers = INTERNAL_HEADERS
        match_info = {"task_id": "task-fail-1"}

    status_response = await webchat.api_task_status(_StatusRequest())
    payload = json.loads(status_response.body)
    assert payload["status"] == "error"
    assert payload["ok"] is False
    assert payload["error"] == "runtime boom"


@pytest.mark.asyncio
async def test_api_tasks_execute_spawn_failure_removes_pending_record(monkeypatch):
    from src.gateway import webchat
    webchat.runtime_task_tracker.reset()

    monkeypatch.setattr(webchat, "_spawn_runtime_background_task", lambda _coro: (_ for _ in ()).throw(RuntimeError("spawn failed")))
    emitted = []

    async def _fake_emit_task_lifecycle_event(event_type, **kwargs):
        emitted.append((event_type, kwargs))

    monkeypatch.setattr(webchat, "_emit_task_lifecycle_event", _fake_emit_task_lifecycle_event)

    class _Request:
        headers = INTERNAL_HEADERS

        async def json(self):
            return {"task_id": "task-spawn-fail-1", "task_type": "adapter_action_task", "input_payload": {"action_id": "jira.transition"}}

    response = await webchat.api_tasks_execute(_Request())
    body = json.loads(response.body)
    assert response.status == 500
    assert body["error"] == "Internal server error"
    assert webchat.runtime_task_tracker.get("task-spawn-fail-1") is None
    assert emitted == []


def test_runtime_task_tracker_prune_removes_terminal_records_even_if_oldest_is_running():
    from src.runtime.runtime_task_tracker import RuntimeTaskTracker

    tracker = RuntimeTaskTracker(max_records=2)
    tracker.create_pending(
        task_id="task-running",
        request_id="task-running",
        task_type="adapter_action_task",
        source="portal",
        session_id=None,
        agent_id=None,
        trace_id=None,
        portal_dispatch_id=None,
        portal_task_id="task-running",
    )
    tracker.mark_running("task-running")

    tracker.create_pending(
        task_id="task-success",
        request_id="task-success",
        task_type="adapter_action_task",
        source="portal",
        session_id=None,
        agent_id=None,
        trace_id=None,
        portal_dispatch_id=None,
        portal_task_id="task-success",
    )
    tracker.mark_terminal("task-success", status="success", payload={"ok": True})

    tracker.create_pending(
        task_id="task-error",
        request_id="task-error",
        task_type="adapter_action_task",
        source="portal",
        session_id=None,
        agent_id=None,
        trace_id=None,
        portal_dispatch_id=None,
        portal_task_id="task-error",
    )
    tracker.mark_terminal("task-error", status="error", payload={"ok": False})
    tracker.prune()

    assert tracker.get("task-running") is not None
    remaining_terminal = [task_id for task_id in ("task-success", "task-error") if tracker.get(task_id) is not None]
    assert len(remaining_terminal) == 1


@pytest.mark.asyncio
async def test_api_chat_returns_display_blocks(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "## Hello\n\n```python\nprint('hi')\n```",
            "usage": {},
            "_execution_result": object(),
        }

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
            return {"message": "hello", "session_id": "s-display"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["response"] == "## Hello\n\n```python\nprint('hi')\n```"
    assert "display_blocks" in payload
    assert payload["display_blocks"][0]["type"] == "markdown"
    assert payload["display_blocks"][0]["content"] == payload["response"]


@pytest.mark.asyncio
async def test_api_load_session_backfills_assistant_display_blocks(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_get_existing_session(_session_id):
        return {
            "history": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello from history"},
            ],
            "metadata": {},
        }

    monkeypatch.setattr(webchat.session_manager, "get_existing_session", _fake_get_existing_session)

    class _Request:
        match_info = {"session_id": "s-load"}

    resp = await webchat.api_load_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assistant_msg = payload["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["display_blocks"][0]["type"] == "markdown"
    assert assistant_msg["display_blocks"][0]["content"] == "hello from history"


@pytest.mark.asyncio
async def test_api_load_session_normalizes_assistant_name_from_trusted_portal_header(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Portal Agent"))

    async def _fake_get_existing_session(_session_id):
        return {
            "history": [
                {
                    "role": "assistant",
                    "content": "hello from history",
                    "author_id": "agent-1",
                    "author_name": "Runtime Alias",
                    "author_type": "agent",
                    "author_source": "runtime",
                },
            ],
            "metadata": {},
        }

    monkeypatch.setattr(webchat.session_manager, "get_existing_session", _fake_get_existing_session)

    class _Request:
        match_info = {"session_id": "s-load-agent-name"}
        headers = {"X-Portal-Author-Source": "portal", "X-Portal-Agent-Name": "Portal Agent"}
        app = {}

    resp = await webchat.api_load_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["messages"][0]["author_name"] == "Portal Agent"


@pytest.mark.asyncio
async def test_api_load_session_backfills_user_author_from_trusted_portal_headers(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_get_existing_session(_session_id):
        return {
            "history": [
                {"role": "user", "content": "hello from history"},
            ],
            "metadata": {},
        }

    monkeypatch.setattr(webchat.session_manager, "get_existing_session", _fake_get_existing_session)

    class _Request:
        match_info = {"session_id": "s-load-user"}
        headers = {
            "X-Portal-Author-Source": "portal",
            "X-Portal-User-Id": "user-1",
            "X-Portal-User-Name": "Alice",
        }
        app = {}

    resp = await webchat.api_load_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    user_msg = payload["messages"][0]
    assert user_msg["author_name"] == "Alice"
    assert user_msg["author_id"] == "user-1"
    assert user_msg["author_type"] == "human"


@pytest.mark.asyncio
async def test_api_chat_preserves_structured_display_blocks(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "fallback text",
            "display_blocks": [
                {"type": "code", "lang": "python", "content": "print('hi')"}
            ],
            "usage": {},
            "_execution_result": object(),
        }

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
            return {"message": "hello", "session_id": "s-structured"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["display_blocks"][0]["type"] == "code"


@pytest.mark.asyncio
async def test_api_chat_accepts_legacy_content_payload(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "content": "hello from content",
            "role": "assistant",
            "usage": {},
            "_execution_result": object(),
        }

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
            return {"message": "hello", "session_id": "s-legacy"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["response"] == "hello from content"
    assert payload["display_blocks"][0]["content"] == "hello from content"


@pytest.mark.asyncio
async def test_api_chat_treats_whitespace_response_as_empty_and_falls_back_to_content(monkeypatch):
    from src.gateway import webchat

    async def _fake_run_chat_via_execution_bus(**kwargs):
        return {
            "response": "   ",
            "content": "hello from content",
            "usage": {},
            "_execution_result": object(),
        }

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
            return {"message": "hello", "session_id": "s-whitespace-fallback"}

    resp = await webchat.api_chat(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["response"] == "hello from content"


@pytest.mark.asyncio
async def test_api_load_session_keeps_existing_structured_display_blocks(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_get_existing_session(_session_id):
        return {
            "history": [
                {"role": "assistant", "content": "fallback", "display_blocks": [{"type": "code", "lang": "python", "content": "print('x')"}]},
            ],
            "metadata": {},
        }

    monkeypatch.setattr(webchat.session_manager, "get_existing_session", _fake_get_existing_session)

    class _Request:
        match_info = {"session_id": "s-keep"}

    resp = await webchat.api_load_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["messages"][0]["display_blocks"][0]["type"] == "code"


@pytest.mark.asyncio
async def test_api_load_session_returns_404_when_session_missing(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(webchat.session_manager, "get_existing_session", lambda _sid: asyncio.sleep(0, result=None))

    class _Request:
        match_info = {"session_id": "missing-session"}
        headers = {}
        app = {}

    resp = await webchat.api_load_session(_Request())
    assert resp.status == 404
    payload = json.loads(resp.text)
    assert payload["error"] == "Session not found"


@pytest.mark.asyncio
async def test_api_load_session_uses_custom_session_name_from_metadata(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_get_existing_session(_session_id):
        return {
            "history": [{"role": "user", "content": "fallback title"}],
            "metadata": {"custom_session_name": "My Custom Name"},
        }

    monkeypatch.setattr(webchat.session_manager, "get_existing_session", _fake_get_existing_session)

    class _Request:
        match_info = {"session_id": "session-custom-name"}
        headers = {}
        app = {}

    resp = await webchat.api_load_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["name"] == "My Custom Name"


@pytest.mark.asyncio
async def test_api_rename_session_success(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(
        webchat.session_manager,
        "rename_session",
        lambda _sid, _name: asyncio.sleep(0, result="Renamed Title"),
    )

    class _Request:
        match_info = {"session_id": "session-rename-ok"}

        async def json(self):
            return {"name": "Renamed Title"}

    resp = await webchat.api_rename_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["success"] is True
    assert payload["session_id"] == "session-rename-ok"
    assert payload["name"] == "Renamed Title"


@pytest.mark.asyncio
async def test_api_rename_session_returns_404_when_missing(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(
        webchat.session_manager,
        "rename_session",
        lambda _sid, _name: asyncio.sleep(0, result=None),
    )

    class _Request:
        match_info = {"session_id": "session-rename-missing"}

        async def json(self):
            return {"name": "Renamed Title"}

    resp = await webchat.api_rename_session(_Request())
    assert resp.status == 404
    payload = json.loads(resp.text)
    assert payload["error"] == "Session not found"


@pytest.mark.asyncio
async def test_api_rename_session_returns_400_for_bad_input(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)

    async def _fake_rename_session(_sid, _name):
        raise ValueError("Session name cannot be empty")

    monkeypatch.setattr(webchat.session_manager, "rename_session", _fake_rename_session)

    class _Request:
        match_info = {"session_id": "session-rename-invalid"}

        async def json(self):
            return {"name": "   "}

    resp = await webchat.api_rename_session(_Request())
    assert resp.status == 400
    payload = json.loads(resp.text)
    assert payload["error"] == "Session name cannot be empty"


@pytest.mark.asyncio
async def test_api_delete_session_success(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(
        webchat.session_manager,
        "delete_session",
        lambda _sid: asyncio.sleep(0, result=True),
    )

    class _Request:
        match_info = {"session_id": "session-delete-ok"}

    resp = await webchat.api_delete_session(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["success"] is True
    assert payload["session_id"] == "session-delete-ok"


@pytest.mark.asyncio
async def test_api_delete_session_returns_404_when_missing(monkeypatch):
    from src.gateway import webchat

    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    monkeypatch.setattr(
        webchat.session_manager,
        "delete_session",
        lambda _sid: asyncio.sleep(0, result=False),
    )

    class _Request:
        match_info = {"session_id": "session-delete-missing"}

    resp = await webchat.api_delete_session(_Request())
    assert resp.status == 404
    payload = json.loads(resp.text)
    assert payload["error"] == "Session not found"


@pytest.mark.asyncio
async def test_api_delete_conversation_from_deletes_target_message_and_following_messages(monkeypatch):
    from src.gateway import webchat

    calls = {"delete_messages_from": [], "delete_message": []}
    history = [
        {"id": "u1", "role": "user", "content": "first"},
        {"id": "a1", "role": "assistant", "content": "first answer"},
        {"id": "u2", "role": "user", "content": "second"},
        {"id": "a2", "role": "assistant", "content": "second answer"},
    ]

    async def _fake_get_history(_session_id):
        return history

    async def _fake_delete_messages_from(session_id, message_id, wait_for_save=False):
        calls["delete_messages_from"].append((session_id, message_id, wait_for_save))
        return 2

    async def _fake_delete_message(*args, **kwargs):
        calls["delete_message"].append((args, kwargs))
        return True

    monkeypatch.setattr(webchat.session_manager, "get_history", _fake_get_history)
    monkeypatch.setattr(webchat.session_manager, "delete_messages_from", _fake_delete_messages_from)
    monkeypatch.setattr(webchat.session_manager, "delete_message", _fake_delete_message)

    class _Request:
        match_info = {"session_id": "s1", "message_id": "u2"}

    resp = await webchat.api_delete_conversation_from(_Request())
    assert resp.status == 200
    payload = json.loads(resp.text)
    assert payload["success"] is True
    assert payload["deleted_count"] == 2
    assert calls["delete_messages_from"] == [("s1", "u2", True)]
    assert calls["delete_message"] == []

    class _MissingRequest:
        match_info = {"session_id": "s1", "message_id": "missing"}

    missing_resp = await webchat.api_delete_conversation_from(_MissingRequest())
    assert missing_resp.status == 404
    missing_payload = json.loads(missing_resp.text)
    assert missing_payload["error"] == "Message not found"
    assert calls["delete_messages_from"] == [("s1", "u2", True)]


def test_agent_assistant_display_helpers_minimal_payload():
    from src.agents.core import Agent

    agent = Agent.__new__(Agent)
    agent.agent_name = "Assistant"
    agent.agent_id = "agent-1"

    message_extra = agent._build_assistant_message_extra("hello")
    assert message_extra["display_blocks"][0]["type"] == "markdown"

    payload = agent._build_assistant_result_payload("hello", usage={}, user_message_id="u1")
    assert payload["response"] == "hello"
    assert payload["display_blocks"][0]["content"] == "hello"
    assert payload["user_message_id"] == "u1"
    assert payload["author_name"] == "Assistant"
    assert payload["author_id"] == "agent-1"
    assert payload["author_type"] == "agent"
    assert payload["author_source"] == "runtime"


def test_webchat_js_passes_display_blocks_in_both_session_render_paths():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    poll_render_anchor = "if (gotFinal && sessionData && sessionData.messages && sessionData.messages.length > 0)"
    poll_start = js.find(poll_render_anchor)
    assert poll_start != -1
    poll_chunk = js[poll_start: poll_start + 1200]
    assert "msg.display_blocks || null" in poll_chunk

    load_session_anchor = "messages.forEach(msg => {"
    load_start = js.rfind(load_session_anchor)
    assert load_start != -1
    load_chunk = js[load_start: load_start + 700]
    assert "msg.display_blocks || null" in load_chunk


def test_core_max_iterations_response_text_is_consistent():
    repo_root = Path(__file__).parent.parent
    core_py = (repo_root / "src" / "agents" / "core.py").read_text(encoding="utf-8")

    max_iter_anchor = "Stopped after reaching the maximum number of tool iterations before reliable completion."
    start = core_py.find(max_iter_anchor)
    assert start != -1
    chunk = core_py[start: start + 1100]

    assert 'send_event("complete", {' in chunk
    assert '"response": max_iterations_text' in chunk
    assert '_build_assistant_result_payload(' in chunk
    assert "Task completed after maximum iterations." not in core_py
    assert '"Task completed (max iterations reached)"' not in chunk


def test_webchat_js_block_only_final_assistant_detection_present():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    anchor = "function hasFinalAssistant(sessionData)"
    start = js.find(anchor)
    assert start != -1
    chunk = js[start:start + 900]

    assert "find(m => m.role === 'assistant')" in chunk
    assert "lastMsg.display_blocks" in chunk
    assert "lastMsg.content" in chunk
    assert "hasTextContent || hasDisplayBlocks" in chunk


def test_webchat_js_table_columns_compatibility_present():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    anchor = "function renderTableBlock(block)"
    start = js.find(anchor)
    assert start != -1
    chunk = js[start:start + 700]

    assert "block.headers" in chunk
    assert "block.columns" in chunk


def test_webchat_js_tool_result_richer_renderer_present():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    anchor = "if (blockType === 'tool_result')"
    start = js.find(anchor)
    assert start != -1
    chunk = js[start:start + 700]

    assert "message-tool-result" in chunk
    assert "message-tool-result-title" in chunk
    assert "is-${" in chunk
    assert "getBlockText(block" in chunk or "block.output" in js


def test_webchat_js_callout_richer_renderer_present():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    anchor = "if (blockType === 'callout')"
    start = js.find(anchor)
    assert start != -1
    chunk = js[start:start + 700]

    assert "message-callout" in chunk
    assert "message-callout-title" in chunk
    assert "is-${" in chunk
    assert "getBlockText(block" in chunk or "block.message" in js


def test_webchat_css_richer_block_variant_classes_present():
    repo_root = Path(__file__).parent.parent
    css = (repo_root / "src" / "gateway" / "static" / "css" / "webchat.css").read_text(encoding="utf-8")

    assert ".message-callout.is-success" in css
    assert ".message-callout.is-warning" in css
    assert ".message-callout.is-error" in css
    assert ".message-tool-result.is-success" in css
    assert ".message-tool-result.is-warning" in css
    assert ".message-tool-result.is-error" in css
    assert ".message-tool-result.is-running" in css


def test_webchat_js_renders_tool_result_output_and_callout_message():
    repo_root = Path(__file__).parent.parent
    js_path = repo_root / "src" / "gateway" / "static" / "js" / "webchat.js"
    js = js_path.read_text(encoding="utf-8")

    start_get = js.find("function getBlockText(")
    start_single = js.find("function renderSingleDisplayBlock(block)")
    start_code = js.find("function renderCodeBlock(block)")
    start_table = js.find("function renderTableBlock(block)")
    assert min(start_get, start_single, start_code, start_table) >= 0

    snippet = "\n".join([
        js[start_get:start_single],
        js[start_single:start_code],
        js[start_code:start_table],
    ])

    node_script = f"""
{snippet}
function renderMarkdown(text) {{
  return String(text || '');
}}
function escapeHtml(text) {{
  return String(text || '');
}}
const toolHtml = renderSingleDisplayBlock({{
  type: 'tool_result',
  title: 'Bash',
  status: 'success',
  output: 'done'
}});
const calloutHtml = renderSingleDisplayBlock({{
  type: 'callout',
  title: 'Note',
  message: 'hello'
}});
if (!toolHtml.includes('done')) throw new Error('tool_result output not rendered');
if (!calloutHtml.includes('hello')) throw new Error('callout message not rendered');
console.log('ok');
"""
    result = subprocess.run(
        ["node", "-e", node_script],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_webchat_js_provider_model_defaults_default_to_gpt_5_4_mini():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    github_start = js.find("github_copilot: [")
    openai_start = js.find("openai: [")
    assert github_start >= 0 and openai_start >= 0

    github_block = js[github_start:openai_start]
    openai_end = js.find("],", openai_start)
    openai_block = js[openai_start:openai_end]

    assert "{ value: 'gpt-5.4-mini', label: 'GPT-5.4 mini' }" in github_block
    assert "{ value: 'gpt-5.4-mini', label: 'GPT-5.4 mini' }" in openai_block
    assert "{ value: 'gpt-5-mini', label: 'GPT-5 mini' }" in github_block
    assert "{ value: 'gpt-5-mini', label: 'GPT-5 mini' }" in openai_block


@pytest.mark.asyncio
async def test_assistant_persist_and_result_payload_share_display_blocks(monkeypatch):
    from src.agents.core import Agent
    from src.agents import core as core_module

    agent = Agent.__new__(Agent)
    agent.agent_name = "Assistant"
    agent.agent_id = "agent-1"
    supplied_extra = {"display_blocks": [{"type": "code", "text": "print(1)", "language": "python"}]}

    captured = {}

    async def _fake_add_message(session_id, role, content, extra=None):
        captured["session_id"] = session_id
        captured["role"] = role
        captured["content"] = content
        captured["extra"] = extra or {}
        return "m-1"

    monkeypatch.setattr(core_module.session_manager, "add_message", _fake_add_message)

    persisted_extra = await agent._persist_assistant_message(
        "s1",
        "print(1)",
        extra=supplied_extra,
    )
    payload = agent._build_assistant_result_payload(
        "print(1)",
        usage={},
        user_message_id="u1",
        extra=supplied_extra,
    )

    assert captured["role"] == "assistant"
    assert persisted_extra["display_blocks"] == payload["display_blocks"]



@pytest.mark.asyncio
async def test_api_task_cancel_missing_returns_404():
    import json

    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()

    class _Req:
        headers = {}
        match_info = {"task_id": "missing"}

    response = await webchat.api_task_cancel(_Req())
    assert response.status == 404
    payload = json.loads(response.body)
    assert payload["error"] == "Task not found"


@pytest.mark.asyncio
async def test_api_task_cancel_cancels_running_background_task():
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()
    webchat.runtime_task_tracker.create_pending(task_id="t-cancel", request_id="r", task_type="x", source="portal", session_id="s", agent_id="a", trace_id="tr", portal_dispatch_id="pd", portal_task_id="pt")
    bg = asyncio.create_task(asyncio.sleep(999))
    webchat.runtime_task_tracker.set_background_task("t-cancel", bg)

    class _Req:
        headers = {}
        match_info = {"task_id": "t-cancel"}

    response = await webchat.api_task_cancel(_Req())
    assert response.status == 200
    record = webchat.runtime_task_tracker.get("t-cancel")
    assert record is not None and record.status == "cancelled"
    assert record.finished_at is not None
    assert (record.payload or {}).get("status") == "cancelled"
    assert record.finished_at is not None
    await asyncio.sleep(0)
    assert bg.cancelled() or bg.done()
    if not bg.done():
        bg.cancel()


@pytest.mark.asyncio
async def test_api_task_cancel_terminal_task_returns_existing_payload():
    import json

    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()
    webchat.runtime_task_tracker.create_pending(task_id="t-done", request_id="r", task_type="x", source="portal", session_id="s", agent_id="a", trace_id="tr", portal_dispatch_id="pd", portal_task_id="pt")
    webchat.runtime_task_tracker.mark_terminal("t-done", status="success", payload={"ok": True, "status": "success"})

    class _Req:
        headers = {}
        match_info = {"task_id": "t-done"}

    response = await webchat.api_task_cancel(_Req())
    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["status"] == "success"
    assert payload["cancel_requested"] is True
    assert webchat.runtime_task_tracker.get("t-done").status == "success"


def test_cancelled_task_is_not_overwritten_by_late_completion():
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()
    webchat.runtime_task_tracker.create_pending(task_id="t-late", request_id="r", task_type="x", source="portal", session_id="s", agent_id="a", trace_id="tr", portal_dispatch_id="pd", portal_task_id="pt")
    webchat.runtime_task_tracker.cancel("t-late", payload={"ok": False, "status": "cancelled"})
    webchat.runtime_task_tracker.mark_terminal("t-late", status="success", payload={"ok": True, "status": "success"})

    record = webchat.runtime_task_tracker.get("t-late")
    assert record is not None and record.status == "cancelled"
    assert record.finished_at is not None
    assert (record.payload or {}).get("status") == "cancelled"
    assert (record.payload or {}).get("status") == "cancelled"


@pytest.mark.asyncio
async def test_run_task_execution_background_cancelled_error_marks_cancelled_and_emits_event(monkeypatch):
    from src.gateway import webchat

    webchat.runtime_task_tracker.reset()
    webchat.runtime_task_tracker.create_pending(task_id="t-bg-cancel", request_id="r", task_type="x", source="portal", session_id="s", agent_id="a", trace_id="tr", portal_dispatch_id="pd", portal_task_id="pt")

    async def _cancelled(**kwargs):
        raise asyncio.CancelledError()

    emitted = []

    async def _emit(*args, **kwargs):
        emitted.append(kwargs.get("event_type") or (args[0] if args else None))

    monkeypatch.setattr(webchat, "execute_runtime_task_request", _cancelled)
    monkeypatch.setattr(webchat, "_emit_task_lifecycle_event", _emit)

    await webchat._run_task_execution_in_background(task_id="t-bg-cancel", request_id="r", task_type="x", session_id="s", source="portal", runtime_agent_id="a", context_ref=None, merged_input_payload={}, metadata={"portal_task_id":"pt"}, trace_headers={"trace_id":"tr", "portal_dispatch_id":"pd"})
    record = webchat.runtime_task_tracker.get("t-bg-cancel")
    assert record is not None and record.status == "cancelled"
    assert record.finished_at is not None
    assert (record.payload or {}).get("status") == "cancelled"
    assert "task.cancelled" in emitted
    assert "task.failed" not in emitted
    assert "task.failed" not in emitted


@pytest.mark.asyncio
async def test_run_task_execution_cancelled_during_started_event_marks_cancelled(monkeypatch):
    import src.gateway.webchat as webchat
    webchat.runtime_task_tracker.reset()
    webchat.runtime_task_tracker.create_pending(task_id="t-cancel-early", request_id="r", task_type="x", source="portal", session_id="s", agent_id="a", trace_id="tr", portal_dispatch_id="pd", portal_task_id="pt")

    emitted = []
    async def _emit(event_type, **kwargs):
        emitted.append(event_type)
        if event_type == "task.started":
            raise asyncio.CancelledError()
    monkeypatch.setattr(webchat, "_emit_task_lifecycle_event", _emit)
    await webchat._run_task_execution_in_background(task_id="t-cancel-early", request_id="r", task_type="x", session_id="s", source="portal", runtime_agent_id="a", context_ref=None, merged_input_payload={}, metadata={"portal_task_id":"pt"}, trace_headers={"trace_id":"tr", "portal_dispatch_id":"pd"})
    record = webchat.runtime_task_tracker.get("t-cancel-early")
    assert record is not None and record.status == "cancelled"
    assert record.finished_at is not None
    assert (record.payload or {}).get("status") == "cancelled"
    assert "task.failed" not in emitted
