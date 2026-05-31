import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_shared_loader_module_with_github_stubs():
    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []
    runtime_pkg = types.ModuleType("src.runtime")
    runtime_pkg.__path__ = []

    rb_assets = types.ModuleType("src.runtime.requirement_bundle_assets")
    rb_assets.parse_bundle_ref = lambda bundle_ref: types.SimpleNamespace(owner="acme", repo="repo", path="bundles/a", branch="main")
    rb_assets.prepare_github_doc_source = None

    modules = {
        "src": src_pkg,
        "src.runtime": runtime_pkg,
        "src.runtime.requirement_bundle_assets": rb_assets,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.runtime = runtime_pkg
    runtime_pkg.requirement_bundle_assets = rb_assets
    spec = importlib.util.spec_from_file_location("skills.shared_bundle_source_loaders", Path("tests/fixtures/skills/shared_bundle_source_loaders.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("skills.shared_bundle_source_loaders", None)

    return module, _cleanup


@pytest.mark.asyncio
async def test_load_github_doc_sources_returns_real_refs(monkeypatch):
    module, cleanup = _load_shared_loader_module_with_github_stubs()
    try:
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

        monkeypatch.setattr(module, "prepare_github_doc_source", _fake_prepare)

        out = await module._load_github_doc_sources(
            {"repo": "acme/repo", "path": "bundles/a", "branch": "main"},
            ["docs/spec.md"],
            session_id="s1",
        )

        assert out[0]["content"] == "hello"
        assert out[0]["artifact_refs"] == [{"artifact_id": "a-1"}]
        assert out[0]["context_ref"] == "ctx-1"
        assert out[0]["digest_ref"] == "dig-1"
        assert out[0]["source_kind"] == "repo_file"
    finally:
        cleanup()
