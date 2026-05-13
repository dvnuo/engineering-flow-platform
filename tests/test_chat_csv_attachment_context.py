import pytest
from types import SimpleNamespace

from src.gateway import webchat
from src.utils.file_parser.models import ParseResult, Block
from src.hooks.file_context.models import Chunk


@pytest.mark.asyncio
async def test_parse_file_into_context_fallback_content(monkeypatch):
    calls = {"saved": [], "status": []}
    monkeypatch.setattr(webchat, "get_metadata", lambda fid: SimpleNamespace(session_id="s1", original_filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_meta", lambda s, f: None)
    monkeypatch.setattr(webchat.file_context_storage, "add_file_to_session", lambda *a, **k: None)
    monkeypatch.setattr(webchat.file_context_storage, "update_file_status", lambda *a, **k: calls["status"].append((a, k)))
    monkeypatch.setattr(webchat.file_context_storage, "delete_file_chunks", lambda f: 0)
    monkeypatch.setattr(webchat.file_context_storage, "save_chunk", lambda c: calls["saved"].append(c))
    monkeypatch.setattr(webchat.retrieval_engine, "rebuild_index", lambda s: None)

    async def fake_parse(_fid, _opt):
        return ParseResult(success=True, content_type="text/csv", file_id="f1", filename="a.csv", markdown="", blocks=[Block(chunk_id="b1", type="table", content="", markdown="|h|\n|--|\n|v|", method="pandas", confidence=0.95, extracted_at="2026-01-01T00:00:00Z")], json={})
    monkeypatch.setattr(webchat, "parse_file", fake_parse)

    out = await webchat._parse_file_into_file_context(session_id="s1", file_id="f1")
    assert out["success"] is True
    assert calls["saved"][0].content.strip()
    assert "|h|" in calls["saved"][0].content


@pytest.mark.asyncio
async def test_parse_file_into_context_contentless_fails_without_deleting(monkeypatch):
    calls = {"status": [], "deleted": 0}
    monkeypatch.setattr(webchat, "get_metadata", lambda fid: SimpleNamespace(session_id="s1", original_filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_meta", lambda s, f: None)
    monkeypatch.setattr(webchat.file_context_storage, "add_file_to_session", lambda *a, **k: None)
    monkeypatch.setattr(webchat.file_context_storage, "update_file_status", lambda *a, **k: calls["status"].append((a, k)))
    monkeypatch.setattr(webchat.file_context_storage, "delete_file_chunks", lambda f: calls.__setitem__("deleted", calls["deleted"] + 1))
    monkeypatch.setattr(webchat.file_context_storage, "save_chunk", lambda c: None)

    async def fake_parse(_fid, _opt):
        return ParseResult(success=True, content_type="text/csv", file_id="f1", filename="a.csv", markdown="", blocks=[], json={})
    monkeypatch.setattr(webchat, "parse_file", fake_parse)

    out = await webchat._parse_file_into_file_context(session_id="s1", file_id="f1")
    assert out["success"] is False
    assert "did not produce any text chunks" in out["error"]
    assert calls["deleted"] == 0
    assert any(kwargs.get("status") == "failed" for _args, kwargs in calls["status"])
    assert out["saved_chunks"] == 0


@pytest.mark.asyncio
async def test_ensure_attachment_context_rejects_contentless_parse(monkeypatch):
    monkeypatch.setattr(webchat, "get_metadata", lambda fid: SimpleNamespace(session_id="s1", original_filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(parse_status="pending"))
    monkeypatch.setattr(webchat.file_context_storage, "add_file_to_session", lambda *a, **k: None)
    monkeypatch.setattr(webchat.file_context_storage, "get_file_chunks", lambda _f: [])

    async def fake_parse_ctx(**kwargs):
        return {"success": True}
    monkeypatch.setattr(webchat, "_parse_file_into_file_context", fake_parse_ctx)

    out = await webchat._ensure_chat_attachment_context(session_id="s1", attachment_ids=["f1"])
    assert "f1" not in out["context_file_ids"]
    assert any(item["file_id"] == "f1" for item in out["failures"])


def test_direct_prompt_returns_none_for_header_only(monkeypatch):
    monkeypatch.setattr(webchat.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_chunks", lambda _f: [])
    out = webchat._build_direct_attachment_context_prompt(session_id="s1", file_ids=["f1"], user_question="q")
    assert out is None


def test_direct_prompt_truncated_chunk_counts_as_context(monkeypatch):
    big_text = "col1,col2\n" + ("alice,30\n" * 5000)
    monkeypatch.setattr(webchat.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename="big.csv", content_type="text/csv"))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_chunks", lambda _f: [Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="paragraph", content=big_text, source="x", content_hash="h")])
    out = webchat._build_direct_attachment_context_prompt(session_id="s1", file_ids=["f1"], user_question="Please summarize", max_chars=500)
    assert out is not None
    assert "big.csv" in out
    assert ("alice" in out) or ("col1" in out)
    assert len(out) < 1000


def test_prepare_transient_model_message_fallback_on_inject_exception(monkeypatch):
    monkeypatch.setattr(webchat, "inject_context", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(webchat.file_context_storage, "get_file_chunks", lambda _f: [Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="table", content="", markdown="|name|age|\n|---|---|\n|alice|30|", source="x", content_hash="h")])
    transient, citations, source = webchat._prepare_attachment_transient_model_message(session_id="s1", context_file_ids=["f1"], model_context_query="Please summarize the attached file(s).")
    assert transient and "alice" in transient and "name" in transient
    assert transient != "[attachment]"
    assert isinstance(citations, list)
    assert source == "direct_fallback"


@pytest.mark.asyncio
async def test_run_chat_via_execution_bus_retains_attachments(monkeypatch):
    captured = {}
    async def fake_run_chat_execution(*args, **kwargs):
        captured["attachments_run"] = kwargs.get("attachments")
        return {"response": "ok"}
    async def fake_execute_chat_orchestration(**kwargs):
        captured["payload"] = kwargs["input_payload"]
        req = SimpleNamespace(input_payload=kwargs["input_payload"], metadata=kwargs.get("metadata", {}), session_id="s1", request_id="r1")
        await kwargs["chat_handler"](req)
        return SimpleNamespace(output_payload={"response": "ok"}, request_id="r1", runtime_events=[], status="ok")
    monkeypatch.setattr(webchat, "run_chat_execution", fake_run_chat_execution)
    monkeypatch.setattr(webchat, "execute_chat_orchestration", fake_execute_chat_orchestration)

    await webchat._run_chat_via_execution_bus(agent=object(), session_id="s1", message="m", user_name="u", portal_user_id=None, portal_user_name=None, attachments=["f1"])
    assert captured["payload"]["attachments"] == ["f1"]
    assert captured["attachments_run"] == ["f1"]

class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}
        self.app = {}
        self.query = {}
        self.match_info = {}
        self._state = {}
    def __setitem__(self, key, value):
        self._state[key] = value
    def __getitem__(self, key):
        return self._state[key]
    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_api_chat_and_stream_use_unavailable_context_guard(monkeypatch):
    webchat.global_config._config.setdefault("llm", {})
    webchat.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    async def _fake_ensure(**kwargs):
        return {"context_file_ids": ["f1"], "failures": []}
    async def _fake_images(**kwargs):
        return []
    monkeypatch.setattr(webchat, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(webchat, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(webchat, "_prepare_attachment_transient_model_message", lambda **kwargs: (None, [], "none"))
    called = {"run": 0}
    async def fake_run(*args, **kwargs):
        called["run"] += 1
        return {}
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", fake_run)

    chat_resp = await webchat.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert chat_resp.status == 400

    stream_resp = await webchat.api_chat_stream(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert stream_resp.status == 400
    assert called["run"] == 0


@pytest.mark.asyncio
async def test_api_chat_empty_message_csv_attachment_builds_transient_context(monkeypatch):
    webchat.global_config._config.setdefault("llm", {})
    webchat.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    captured = {}

    async def _fake_images(**kwargs):
        return []
    async def _fake_ensure(**kwargs):
        captured["ensure_called"] = True
        return {"context_file_ids": ["f1"], "failures": []}
    def _fake_prepare(**kwargs):
        captured["model_context_query"] = kwargs.get("model_context_query")
        return ("name,age\nalice,30", [], "direct_fallback")
    async def _fake_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "request_id": "r1"}

    monkeypatch.setattr(webchat, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(webchat, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(webchat, "_prepare_attachment_transient_model_message", _fake_prepare)
    monkeypatch.setattr(webchat, "_resolve_runtime_agent_identity", lambda _r: (None, None))
    monkeypatch.setattr(webchat, "AgentCore", lambda **kwargs: object())
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_bus)
    monkeypatch.setattr(webchat, "build_webchat_response_payload", lambda _result, session_id: {"response": "ok", "session_id": session_id})
    monkeypatch.setattr(webchat.session_manager, "_initialized", True)
    async def _fake_get_session(_sid):
        return None
    monkeypatch.setattr(webchat.session_manager, "get_session", _fake_get_session)

    resp = await webchat.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert resp.status == 200
    assert captured["ensure_called"] is True
    assert captured["model_context_query"] == "Please summarize the attached file(s)."
    assert captured["message"] == "[attachment]"
    assert captured["attachments"] == ["f1"]
    assert "name" in captured["transient_model_message"]
    assert "alice" in captured["transient_model_message"]
    assert captured["transient_model_message"] != "[attachment]"


@pytest.mark.asyncio
async def test_api_chat_parse_failure_does_not_call_execution_bus(monkeypatch):
    webchat.global_config._config.setdefault("llm", {})
    webchat.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    called = {"run": 0}
    async def _fake_images(**kwargs):
        return []
    async def _fake_ensure(**kwargs):
        return {"context_file_ids": [], "failures": [{"file_id": "f1", "error": "bad csv"}]}
    async def _fake_bus(**kwargs):
        called["run"] += 1
        return {}
    monkeypatch.setattr(webchat, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(webchat, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(webchat, "_run_chat_via_execution_bus", _fake_bus)

    resp = await webchat.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert resp.status == 400
    payload = __import__("json").loads(resp.text)
    assert payload["error"] == "attachment_parse_failed"
    assert payload["failures"][0]["file_id"] == "f1"
    assert called["run"] == 0
