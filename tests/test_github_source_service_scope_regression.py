from pathlib import Path


def test_github_source_service_is_repo_file_only_v1():
    source = Path("src/github/source_service.py").read_text(encoding="utf-8")

    assert '"source_kind": "repo_file"' in source
    assert '"attachments_supported": False' in source
    assert '"issue_pr_assets_supported": False' in source
