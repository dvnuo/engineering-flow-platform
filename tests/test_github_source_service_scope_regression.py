from pathlib import Path


def test_repo_file_source_kind_contract_is_stable():
    source = Path("src/github/source_service.py").read_text(encoding="utf-8")

    assert '"source_kind": "repo_file"' in source
