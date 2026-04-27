from src.hooks.file_context.models import Chunk, RetrievalRequest, SessionFileMeta
from src.hooks.file_context.retrieval import RetrievalEngine
from src.config import config


def _make_chunk(chunk_id: str, content_chars: int = 40000) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        file_id="f1",
        session_id="s1",
        type="paragraph",
        content="A" * content_chars,
        source="pymupdf",
        content_hash=f"h-{chunk_id}",
    )


def test_retrieval_engine_uses_model_aware_thresholds_for_large_models(monkeypatch):
    engine = RetrievalEngine()
    chunks = [_make_chunk("c1", 80000), _make_chunk("c2", 200000)]
    files = [SessionFileMeta(file_id="f1", session_id="s1", filename="doc.md", content_type="text/markdown", parse_status="completed")]

    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_session_chunks", lambda _sid: chunks)
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_session_completed_files", lambda _sid: files)
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_file_chunks", lambda _fid: chunks)

    original_model = config.llm.get("model")
    config.llm["model"] = "gpt-5.4-mini"
    try:
        # estimated_tokens ~= 20k
        r1 = engine.retrieve(RetrievalRequest(session_id="s1", query="A", top_k=1))
        assert r1.estimated_tokens >= 20000
        assert r1.budget_status in {"top-k", "summarize"}

        # estimated_tokens ~= 50k
        r2 = engine.retrieve(RetrievalRequest(session_id="s1", query="A", top_k=2))
        assert r2.estimated_tokens >= 50000
        assert r2.budget_status in {"top-k", "summarize"}
    finally:
        if original_model is None:
            config.llm.pop("model", None)
        else:
            config.llm["model"] = original_model


def test_retrieval_engine_fallback_thresholds_for_unknown_model(monkeypatch):
    engine = RetrievalEngine()
    chunk = _make_chunk("c1", 36000)  # ~9000 tokens
    files = [SessionFileMeta(file_id="f1", session_id="s1", filename="doc.md", content_type="text/markdown", parse_status="completed")]
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_session_chunks", lambda _sid: [chunk])
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_session_completed_files", lambda _sid: files)
    monkeypatch.setattr("src.hooks.file_context.retrieval.storage.get_file_chunks", lambda _fid: [chunk])

    original_model = config.llm.get("model")
    config.llm["model"] = "unknown-model"
    try:
        result = engine.retrieve(RetrievalRequest(session_id="s1", query="A", top_k=1, max_tokens=100))
        assert result.budget_status == "error"
    finally:
        if original_model is None:
            config.llm.pop("model", None)
        else:
            config.llm["model"] = original_model


def test_retrieval_engine_uses_default_llm_model_when_config_model_missing(monkeypatch):
    engine = RetrievalEngine()

    had_model = "model" in config.llm
    original_model = config.llm.get("model")
    try:
        config.llm.pop("model", None)

        direct, topk, summarize = engine._resolve_budget_thresholds(
            RetrievalRequest(session_id="s1", query="A", top_k=1)
        )

        assert direct == 13600
        assert topk == 54400
        assert summarize == 128000
    finally:
        if had_model:
            config.llm["model"] = original_model
        else:
            config.llm.pop("model", None)
