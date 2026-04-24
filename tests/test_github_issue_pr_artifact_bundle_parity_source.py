from pathlib import Path


def _extract_function_block(source: str, signature: str) -> str:
    start = source.find(signature)
    assert start >= 0, f"missing function signature: {signature}"
    next_async = source.find("\nasync def ", start + len(signature))
    next_def = source.find("\ndef ", start + len(signature))
    candidates = [idx for idx in [next_async, next_def] if idx > start]
    end = min(candidates) if candidates else len(source)
    return source[start:end]


def test_issue_and_pr_paths_finalize_bundle_artifacts_after_persist():
    source = Path("src/github/source_service.py").read_text(encoding="utf-8")

    helper_block = _extract_function_block(source, "def _finalize_bundle_artifacts(")
    assert "bind_artifact_to_source_bundle(" in helper_block
    assert "attach_source_refs_to_artifact(" in helper_block
    assert "artifact_storage.get_artifact(" in helper_block
    assert "refreshed_bundle_artifact_refs" in helper_block

    issue_block = _extract_function_block(source, "async def prepare_github_issue_source(")
    assert "_finalize_bundle_artifacts(" in issue_block
    assert "bundle[\"asset_entries\"] = refreshed_asset_entries" in issue_block
    assert "bundle[\"artifact_refs\"] = refreshed_artifact_refs" in issue_block
    assert 'bundle_scope_id=f"github:{repo_full_name}#issue:{issue_number}"' in issue_block

    pr_block = _extract_function_block(source, "async def prepare_github_pr_source(")
    assert "_finalize_bundle_artifacts(" in pr_block
    assert "bundle[\"asset_entries\"] = refreshed_asset_entries" in pr_block
    assert "bundle[\"artifact_refs\"] = refreshed_artifact_refs" in pr_block
    assert 'bundle_scope_id=f"github:{repo_full_name}#pull_request:{pull_number}"' in pr_block
