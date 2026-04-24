import pytest


@pytest.mark.asyncio
async def test_load_github_doc_sources_returns_real_refs(monkeypatch):
    from skills.collect_requirements_to_bundle.skill import _load_github_doc_sources

    class _DocRef:
        owner = "acme"
        repo = "repo"
        branch = "main"
        path = "docs/spec.md"

    async def _fake_prepare(raw, default_ref, session_id=None):
        return {
            "doc_ref": _DocRef(),
            "bundle": {},
            "context_ref": "ctx-1",
            "digest_ref": "dig-1",
            "artifact_refs": [{"artifact_id": "a-1"}],
            "content_text": "hello",
        }

    monkeypatch.setattr("skills.collect_requirements_to_bundle.skill.prepare_github_doc_source", _fake_prepare)

    out = await _load_github_doc_sources(
        {"repo": "acme/repo", "path": "bundles/a", "branch": "main"},
        ["docs/spec.md"],
        session_id="s1",
    )

    assert out[0]["content"] == "hello"
    assert out[0]["artifact_refs"] == [{"artifact_id": "a-1"}]
    assert out[0]["context_ref"] == "ctx-1"
    assert out[0]["digest_ref"] == "dig-1"
    assert out[0]["source_kind"] == "repo_file"
