from src.utils.file_parser.excel import _rows_to_table_block
from src.hooks.file_context.models import Chunk, RetrievalResult, RetrievalRequest
from src.hooks.file_context.injection import build_rag_prompt
from src.hooks.file_context.retrieval import RetrievalEngine


def test_rows_to_table_block_content_has_markdown_text():
    block = _rows_to_table_block([["name", "age"], ["alice", "30"]], 1, "Sheet1", "f1", "csv", "python-csv")
    assert "name" in block.content and "alice" in block.content
    assert "name" in (block.markdown or "") and "alice" in (block.markdown or "")


def test_build_rag_prompt_uses_markdown_fallback():
    chunk = Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="table", content="", markdown="| name | age |\n| --- | --- |\n| alice | 30 |", source="x", content_hash="h")
    rr = RetrievalResult(chunks=[chunk], total_chunks=1, estimated_tokens=10, budget_status="direct", citations=[])
    prompt, _ = build_rag_prompt("Please summarize the attached file(s).", rr)
    assert "alice" in prompt and "name" in prompt
    assert prompt != "Please summarize the attached file(s)."


def test_retrieval_uses_markdown_fallback(monkeypatch):
    chunk = Chunk(chunk_id="c1", file_id="f1", session_id="s1", type="table", content="", markdown="alice revenue 123", source="x", content_hash="h")
    engine = RetrievalEngine()
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_session_chunks", lambda _sid: [chunk])
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_file_chunks", lambda _fid: [chunk])
    res = engine.retrieve(RetrievalRequest(session_id="s1", query="revenue", file_ids=["f1"]))
    assert res.chunks
    assert "revenue" in res.citations[0]["preview"] or "alice" in res.citations[0]["preview"]
