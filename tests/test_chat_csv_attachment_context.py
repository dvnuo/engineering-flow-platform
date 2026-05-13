import pytest
from types import SimpleNamespace

from src.gateway import webchat
from src.utils.file_parser.models import ParseResult, Block


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
