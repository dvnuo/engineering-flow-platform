import pytest
from types import SimpleNamespace

from src.gateway import runtime_api
from src.utils.file_parser.models import ParseResult, Block
from src.hooks.file_context.models import Chunk


@pytest.mark.asyncio
async def test_parse_file_into_context_fallback_content(monkeypatch):
    calls = {"saved": [], "status": []}
    monkeypatch.setattr(runtime_api, "get_metadata", lambda fid: SimpleNamespace(session_id="s1", original_filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: None)
    monkeypatch.setattr(runtime_api.file_context_storage, "add_file_to_session", lambda *a, **k: None)
    monkeypatch.setattr(runtime_api.file_context_storage, "update_file_status", lambda *a, **k: calls["status"].append((a, k)))
    monkeypatch.setattr(runtime_api.file_context_storage, "delete_file_chunks", lambda f: 0)
    monkeypatch.setattr(runtime_api.file_context_storage, "save_chunk", lambda c: calls["saved"].append(c))
    monkeypatch.setattr(runtime_api.retrieval_engine, "rebuild_index", lambda s: None)

    async def fake_parse(_fid, _opt):
        return ParseResult(success=True, content_type="text/csv", file_id="f1", filename="a.csv", markdown="", blocks=[Block(chunk_id="b1", type="table", content="", markdown="|h|\n|--|\n|v|", method="pandas", confidence=0.95, extracted_at="2026-01-01T00:00:00Z")], json={})
    monkeypatch.setattr(runtime_api, "parse_file", fake_parse)

    out = await runtime_api._parse_file_into_file_context(session_id="s1", file_id="f1")
    assert out["success"] is True
    assert calls["saved"][0].content.strip()
    assert "|h|" in calls["saved"][0].content


@pytest.mark.asyncio
async def test_parse_file_into_context_contentless_fails_without_deleting(monkeypatch):
    calls = {"status": [], "deleted": 0}
    monkeypatch.setattr(runtime_api, "get_metadata", lambda fid: SimpleNamespace(session_id="s1", original_filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: None)
    monkeypatch.setattr(runtime_api.file_context_storage, "add_file_to_session", lambda *a, **k: None)
    monkeypatch.setattr(runtime_api.file_context_storage, "update_file_status", lambda *a, **k: calls["status"].append((a, k)))
    monkeypatch.setattr(runtime_api.file_context_storage, "delete_file_chunks", lambda f: calls.__setitem__("deleted", calls["deleted"] + 1))
    monkeypatch.setattr(runtime_api.file_context_storage, "save_chunk", lambda c: None)

    async def fake_parse(_fid, _opt):
        return ParseResult(success=True, content_type="text/csv", file_id="f1", filename="a.csv", markdown="", blocks=[], json={})
    monkeypatch.setattr(runtime_api, "parse_file", fake_parse)

    out = await runtime_api._parse_file_into_file_context(session_id="s1", file_id="f1")
    assert out["success"] is False
    assert "did not produce any text chunks" in out["error"]
    assert calls["deleted"] == 0
    assert any(kwargs.get("status") == "failed" for _args, kwargs in calls["status"])
    assert out["saved_chunks"] == 0


@pytest.mark.asyncio
async def test_ensure_attachment_context_rejects_contentless_parse(monkeypatch):
    monkeypatch.setattr(runtime_api, "get_metadata", lambda fid: SimpleNamespace(session_id="s1", original_filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(parse_status="pending"))
    monkeypatch.setattr(runtime_api.file_context_storage, "add_file_to_session", lambda *a, **k: None)
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_chunks", lambda _f: [])

    async def fake_parse_ctx(**kwargs):
        return {"success": True}
    monkeypatch.setattr(runtime_api, "_parse_file_into_file_context", fake_parse_ctx)

    out = await runtime_api._ensure_chat_attachment_context(session_id="s1", attachment_ids=["f1"])
    assert "f1" not in out["context_file_ids"]
    assert any(item["file_id"] == "f1" for item in out["failures"])


def test_direct_prompt_returns_none_for_header_only(monkeypatch):
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_chunks", lambda _f: [])
    out = runtime_api._build_direct_attachment_context_prompt(session_id="s1", file_ids=["f1"], user_question="q")
    assert out is None


def test_direct_prompt_truncated_chunk_counts_as_context(monkeypatch):
    big_text = "col1,col2\n" + ("alice,30\n" * 5000)
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename="big.csv", content_type="text/csv"))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_chunks", lambda _f: [Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="paragraph", content=big_text, source="x", content_hash="h")])
    out = runtime_api._build_direct_attachment_context_prompt(session_id="s1", file_ids=["f1"], user_question="Please summarize", max_chars=500)
    assert out is not None
    assert "big.csv" in out
    assert ("alice" in out) or ("col1" in out)
    assert len(out) < 1000


def test_prepare_transient_model_message_fallback_on_inject_exception(monkeypatch):
    monkeypatch.setattr(runtime_api, "inject_context", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename="a.csv", content_type="text/csv"))
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_chunks", lambda _f: [Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="table", content="", markdown="|name|age|\n|---|---|\n|alice|30|", source="x", content_hash="h")])
    transient, citations, source, excerpt_file_ids = runtime_api._prepare_attachment_transient_model_message(session_id="s1", context_file_ids=["f1"], model_context_query="Please summarize the attached file(s).")
    assert transient and "alice" in transient and "name" in transient
    assert transient != "[attachment]"
    assert isinstance(citations, list)
    assert source == "direct_fallback"
    assert excerpt_file_ids == ["f1"]


@pytest.mark.asyncio
async def test_run_chat_via_execution_bus_retains_attachments(monkeypatch):
    captured = {}
    async def fake_runtime_chat(**kwargs):
        captured.update(kwargs)
        return {"response": "ok"}
    monkeypatch.setattr(runtime_api, "run_runtime_chat", fake_runtime_chat)

    await runtime_api._run_chat_via_execution_bus(session_id="s1", message="m", user_name="u", portal_user_id=None, portal_user_name=None, attachments=["f1"])
    assert captured["attachments"] == ["f1"]

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
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    async def _fake_ensure(**kwargs):
        return {"context_file_ids": ["f1"], "failures": []}
    async def _fake_images(**kwargs):
        return []
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(runtime_api, "_prepare_attachment_transient_model_message", lambda **kwargs: (None, [], "none", []))
    called = {"run": 0}
    async def fake_run(*args, **kwargs):
        called["run"] += 1
        return {}
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", fake_run)

    chat_resp = await runtime_api.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert chat_resp.status == 400

    stream_resp = await runtime_api.api_chat_stream(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert stream_resp.status == 400
    assert called["run"] == 0


@pytest.mark.asyncio
async def test_api_chat_empty_message_csv_attachment_builds_transient_context(monkeypatch):
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    captured = {}

    async def _fake_images(**kwargs):
        return []
    async def _fake_ensure(**kwargs):
        captured["ensure_called"] = True
        return {"context_file_ids": ["f1"], "failures": []}
    def _fake_prepare(**kwargs):
        captured["model_context_query"] = kwargs.get("model_context_query")
        return ("name,age\nalice,30", [], "direct_fallback", ["f1"])
    async def _fake_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "request_id": "r1"}

    monkeypatch.setattr(runtime_api, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_prepare_attachment_transient_model_message", _fake_prepare)
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _r: (None, None))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_bus)
    monkeypatch.setattr(runtime_api, "build_runtime_response_payload", lambda _result, session_id: {"response": "ok", "session_id": session_id})
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    async def _fake_get_session(_sid):
        return None
    monkeypatch.setattr(runtime_api.session_manager, "get_session", _fake_get_session)

    resp = await runtime_api.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
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
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    called = {"run": 0}
    async def _fake_images(**kwargs):
        return []
    async def _fake_ensure(**kwargs):
        return {"context_file_ids": [], "failures": [{"file_id": "f1", "error": "bad csv"}]}
    async def _fake_bus(**kwargs):
        called["run"] += 1
        return {}
    monkeypatch.setattr(runtime_api, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_bus)

    resp = await runtime_api.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert resp.status == 400
    payload = __import__("json").loads(resp.text)
    assert payload["error"] == "attachment_parse_failed"
    assert payload["failures"][0]["file_id"] == "f1"
    assert called["run"] == 0


def test_failure_notice_builder_includes_failed_attachment():
    notice = runtime_api._build_attachment_parse_failure_notice([{"file_id": "bad.csv-id", "error": "bad csv"}])
    assert "bad.csv-id" in notice
    assert "bad csv" in notice
    assert "Do not claim" in notice
    assert runtime_api._build_attachment_parse_failure_notice([]) == ""


@pytest.mark.asyncio
async def test_api_chat_image_plus_failed_csv_allows_image_and_warns_model(monkeypatch):
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    # These turns inline the image: without the flag a non-AI-Platform chat hands images to the attached-files analysis skill instead.
    monkeypatch.setenv("EFP_COPILOT_VISION_VIA_CHAT", "1")
    captured = {}
    async def _fake_images(**kwargs): return ["data:image/png;base64,abc"]
    async def _fake_ensure(**kwargs): return {"context_file_ids": [], "failures": [{"file_id": "csv_bad", "error": "bad csv"}]}
    async def _fake_bus(**kwargs): captured.update(kwargs); return {"response": "ok", "request_id": "r1"}
    monkeypatch.setattr(runtime_api, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _r: (None, None))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_bus)
    monkeypatch.setattr(runtime_api, "build_runtime_response_payload", lambda _result, session_id: {"response": "ok", "session_id": session_id})
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    async def _fake_get_session(_sid): return None
    monkeypatch.setattr(runtime_api.session_manager, "get_session", _fake_get_session)

    resp = await runtime_api.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["img1", "csv_bad"]}))
    assert resp.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"]
    assert captured["attachments"] == ["img1", "csv_bad"]
    assert "csv_bad" in captured["transient_model_message"]
    assert "bad csv" in captured["transient_model_message"]
    assert "Do not claim" in captured["transient_model_message"]


@pytest.mark.asyncio
async def test_api_chat_good_csv_plus_failed_csv_warns_model(monkeypatch):
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    captured = {}
    async def _fake_images(**kwargs): return []
    async def _fake_ensure(**kwargs): return {"context_file_ids": ["csv_good"], "failures": [{"file_id": "csv_bad", "error": "bad csv"}]}
    def _fake_prepare(**kwargs): return ("name,age\nalice,30", [], "direct_fallback", ["csv_good"])
    async def _fake_bus(**kwargs): captured.update(kwargs); return {"response": "ok", "request_id": "r1"}
    monkeypatch.setattr(runtime_api, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_prepare_attachment_transient_model_message", _fake_prepare)
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _r: (None, None))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_bus)
    monkeypatch.setattr(runtime_api, "build_runtime_response_payload", lambda _result, session_id: {"response": "ok", "session_id": session_id})
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    async def _fake_get_session(_sid): return None
    monkeypatch.setattr(runtime_api.session_manager, "get_session", _fake_get_session)

    resp = await runtime_api.api_chat(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["csv_good", "csv_bad"]}))
    assert resp.status == 200
    assert "alice" in captured["transient_model_message"]
    assert "csv_bad" in captured["transient_model_message"]
    assert "bad csv" in captured["transient_model_message"]
    assert captured["transient_model_message"] != "[attachment]"
    assert captured["message"] == "[attachment]"


@pytest.mark.asyncio
async def test_api_chat_stream_image_plus_failed_csv_warns_model(monkeypatch):
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    # These turns inline the image: without the flag a non-AI-Platform chat hands images to the attached-files analysis skill instead.
    monkeypatch.setenv("EFP_COPILOT_VISION_VIA_CHAT", "1")
    captured = {}
    async def _fake_images(**kwargs): return ["data:image/png;base64,abc"]
    async def _fake_ensure(**kwargs): return {"context_file_ids": [], "failures": [{"file_id": "csv_bad", "error": "bad csv"}]}
    async def _fake_bus(**kwargs): captured.update(kwargs); return {"response": "ok", "request_id": "r1", "usage": {}}
    monkeypatch.setattr(runtime_api, "_collect_attached_images", _fake_images)
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _r: (None, None))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_bus)
    monkeypatch.setattr(runtime_api, "build_runtime_response_payload", lambda _result, session_id: {"response": "ok", "session_id": session_id})

    class _FakeStreamResponse:
        def __init__(self, *args, **kwargs): self.status=kwargs.get("status",200)
        async def prepare(self, _req): return None
        async def write(self, _b): return None
        async def write_eof(self): return None
    monkeypatch.setattr(runtime_api.web, "StreamResponse", _FakeStreamResponse)

    resp = await runtime_api.api_chat_stream(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["img1", "csv_bad"]}))
    assert getattr(resp, "status", 0) == 200
    assert "csv_bad" in captured["transient_model_message"]
    assert "Do not claim" in captured["transient_model_message"]


@pytest.mark.asyncio
async def test_api_chat_stream_runs_when_the_only_attachment_failed_to_parse_but_is_stored(monkeypatch):
    """The stream is refused only when nothing of the attachments is available; a stored file goes by path."""
    runtime_api.global_config._config.setdefault("llm", {})
    runtime_api.global_config._config["llm"].update({"api_key": "k", "model": "gpt-4o"})
    captured = {}
    stored = [{"file_id": "x1", "path": "/workspace/uploads/x1.xlsx", "name": "book.xlsx", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size_bytes": 10, "unreadable": False}]
    async def _fake_ensure(**kwargs): return {"context_file_ids": [], "failures": [{"file_id": "x1", "error": "File is not a zip file"}]}
    async def _fake_bus(**kwargs): captured.update(kwargs); return {"response": "ok", "request_id": "r1", "usage": {}}
    monkeypatch.setattr(runtime_api, "_collect_attached_files", lambda **kwargs: list(stored))
    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _r: (None, None))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_bus)
    monkeypatch.setattr(runtime_api, "build_runtime_response_payload", lambda _result, session_id: {"response": "ok", "session_id": session_id})

    class _FakeStreamResponse:
        def __init__(self, *args, **kwargs): self.status=kwargs.get("status",200)
        async def prepare(self, _req): return None
        async def write(self, _b): return None
        async def write_eof(self): return None
    monkeypatch.setattr(runtime_api.web, "StreamResponse", _FakeStreamResponse)

    resp = await runtime_api.api_chat_stream(_FakeRequest({"message": "", "session_id": "s1", "attachments": ["x1"]}))
    assert getattr(resp, "status", 0) == 200
    assert captured["message"] == "[attachment]"
    prompt = captured["transient_model_message"]
    assert "/workspace/uploads/x1.xlsx" in prompt
    assert "it could not be parsed, read it from its path" in prompt
    assert "File is not a zip file" in prompt and "Do not claim" in prompt


def test_direct_context_reports_the_files_whose_text_fitted(monkeypatch):
    """The fallback stops at its budget: a later file is left out and must not be called excerpted."""
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_meta", lambda s, f: SimpleNamespace(filename=f"{f}.csv", content_type="text/csv"))
    chunks = {
        "f1": [Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="paragraph", content="row,value\n" + ("a,1\n" * 100), source="x", content_hash="h1")],
        "f2": [Chunk(chunk_id="c2", file_id="f2", session_id="s1", type="paragraph", content="name,age\nalice,30", source="x", content_hash="h2")],
    }
    monkeypatch.setattr(runtime_api.file_context_storage, "get_file_chunks", lambda f: chunks[f])
    prompt, included = runtime_api._direct_attachment_context(session_id="s1", file_ids=["f1", "f2"], user_question="q", max_chars=300)
    assert prompt is not None and "row,value" in prompt and "alice" not in prompt
    assert included == ["f1"]
    prompt, included = runtime_api._direct_attachment_context(session_id="s1", file_ids=["f1", "f2"], user_question="q")
    assert "alice" in prompt
    assert included == ["f1", "f2"]


def test_prepare_transient_model_message_reports_the_files_retrieval_cited(monkeypatch):
    """Top-k retrieval runs across all the files: a file without a cited chunk has no excerpt in the prompt."""
    monkeypatch.setattr(
        runtime_api,
        "inject_context",
        lambda **kwargs: ("Based on the following context ...", "top-k", [{"chunk_id": "c9", "file_id": "f2", "page": None, "type": "table", "preview": "x"}]),
    )
    transient, citations, source, excerpt_file_ids = runtime_api._prepare_attachment_transient_model_message(session_id="s1", context_file_ids=["f1", "f2"], model_context_query="compare them")
    assert transient.startswith("Based on") and source == "inject_context"
    assert citations[0]["file_id"] == "f2"
    assert excerpt_file_ids == ["f2"]


def test_failure_notice_names_the_workspace_path_of_a_stored_failed_file():
    """A failed file still stored for this session is named with its path; a missing one is not promised one."""
    notice = runtime_api._build_attachment_parse_failure_notice(
        [{"file_id": "f9", "error": "bad csv"}, {"file_id": "gone", "error": "File not found"}],
        [{"file_id": "f9", "path": "/workspace/uploads/f9.csv"}],
    )
    assert "- f9: bad csv (still in the workspace at /workspace/uploads/f9.csv)" in notice
    assert "- gone: File not found (not available in the workspace)" in notice
    assert "paths listed under attached files" not in notice
