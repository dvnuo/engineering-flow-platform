import importlib.util
from pathlib import Path


def _load_manifest_formatter():
    module_path = Path("src/github/source_manifest.py")
    spec = importlib.util.spec_from_file_location("github_source_manifest_test_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.format_github_source_manifest


def test_projectable_manifest_contains_required_refs_and_preview():
    format_manifest = _load_manifest_formatter()
    bundle = {
        "metadata": {
            "owner": "acme",
            "repo": "platform",
            "path": "docs/spec.md",
            "branch": "main",
            "source_kind": "repo_file",
        },
        "artifact_refs": [{"artifact_id": "a1"}],
        "context_ref": "ctx-1",
        "digest_ref": "dig-1",
        "content_markdown": "hello world",
        "completeness_ledger": {"source_complete": True, "partial_reasons": []},
    }

    manifest = format_manifest(bundle)
    assert "file: acme/platform:docs/spec.md@main" in manifest
    assert "source_kind: repo_file" in manifest
    assert "artifact_refs: ['a1']" in manifest
    assert "context_ref: ctx-1" in manifest
    assert "digest_ref: dig-1" in manifest
    assert "source_complete: True" in manifest
    assert "[preview]" in manifest


def test_non_projectable_manifest_uses_high_level_projection_message():
    format_manifest = _load_manifest_formatter()
    bundle = {
        "metadata": {
            "owner": "acme",
            "repo": "platform",
            "path": "docs/logo.png",
            "branch": "main",
            "source_kind": "repo_file",
        },
        "artifact_refs": [{"artifact_id": "a2"}],
        "context_ref": None,
        "digest_ref": None,
        "content_markdown": "",
        "completeness_ledger": {"source_complete": False, "partial_reasons": ["non_projectable_file"]},
    }

    manifest = format_manifest(bundle)
    assert "materialized_as_artifact_only" in manifest
    assert "utf-8 decode failed" not in manifest


def test_issue_manifest_includes_issue_identity_and_refs():
    format_manifest = _load_manifest_formatter()
    bundle = {
        "metadata": {"source_kind": "issue", "repo_full_name": "acme/platform", "issue_number": 12},
        "artifact_refs": [{"artifact_id": "att-1"}],
        "context_ref": "ctx://s1/issue",
        "digest_ref": "ctx://s1/issue/digest",
        "body_markdown": "Issue body",
        "completeness_ledger": {"source_complete": True, "partial_reasons": []},
    }
    manifest = format_manifest(bundle)
    assert "source_kind: issue" in manifest
    assert "issue: acme/platform#12" in manifest
    assert "artifact_refs: ['att-1']" in manifest
    assert "context_ref: ctx://s1/issue" in manifest
    assert "digest_ref: ctx://s1/issue/digest" in manifest


def test_pr_manifest_includes_pr_identity_and_refs():
    format_manifest = _load_manifest_formatter()
    bundle = {
        "metadata": {"source_kind": "pull_request", "repo_full_name": "acme/platform", "pull_number": 34},
        "artifact_refs": [{"artifact_id": "att-2"}],
        "context_ref": "ctx://s1/pr",
        "digest_ref": "ctx://s1/pr/digest",
        "body_markdown": "PR body",
        "completeness_ledger": {"source_complete": False, "partial_reasons": ["x"]},
    }
    manifest = format_manifest(bundle)
    assert "source_kind: pull_request" in manifest
    assert "pull_request: acme/platform#34" in manifest
    assert "artifact_refs: ['att-2']" in manifest
    assert "context_ref: ctx://s1/pr" in manifest
    assert "digest_ref: ctx://s1/pr/digest" in manifest
    assert "[preview]" in manifest
