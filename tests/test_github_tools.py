import pytest

from src.github import api as github_api


@pytest.mark.asyncio
async def test_github_get_pr_formats_metadata(monkeypatch):
    async def _fake_get_pull_request(owner, repo, pull_number):
        return {
            "title": "Improve review output",
            "body": "Adds better summary and findings.",
            "state": "open",
            "draft": False,
            "user": {"login": "octocat"},
            "base": {"ref": "main"},
            "head": {"ref": "feature/review-v2", "sha": "abc123def456"},
            "mergeable": True,
            "changed_files": 5,
            "additions": 120,
            "deletions": 40,
        }

    monkeypatch.setattr(github_api.github_channel, "get_pull_request", _fake_get_pull_request)

    result = await github_api.github_get_pr("acme", "repo", 42)

    assert "**PR acme/repo#42: Improve review output**" in result
    assert "- draft: false" in result
    assert "- mergeable: true" in result
    assert "- changed_files: 5" in result
    assert "- latest_commit_sha: abc123def456" in result
    assert "**Body (quoted):**" in result
    assert "> Adds better summary and findings." in result


@pytest.mark.asyncio
async def test_github_get_pr_quotes_and_truncates_body(monkeypatch):
    long_body = "A" * 4105

    async def _fake_get_pull_request(owner, repo, pull_number):
        return {
            "title": "T",
            "body": long_body,
            "state": "open",
            "user": {"login": "octocat"},
            "base": {"ref": "main"},
            "head": {"ref": "feature/review-v2", "sha": "abc123def456"},
        }

    monkeypatch.setattr(github_api.github_channel, "get_pull_request", _fake_get_pull_request)

    result = await github_api.github_get_pr("acme", "repo", 42)
    assert "**Body (quoted):**" in result
    assert "... (truncated, total 4105 chars)" in result
    assert "> A" in result


@pytest.mark.asyncio
async def test_github_get_pr_file_patch_formats_patch(monkeypatch):
    async def _fake_get_pr_files(owner, repo, pull_number, page=1, per_page=100):
        return [
            {
                "filename": "src/app.py",
                "status": "modified",
                "additions": 10,
                "deletions": 2,
                "patch": "@@ -1,2 +1,3 @@\n-print('x')\n+print('y')",
            }
        ]

    monkeypatch.setattr(github_api.github_channel, "get_pr_files", _fake_get_pr_files)

    result = await github_api.github_get_pr_file_patch("acme", "repo", 7, "src/app.py")

    assert "**PR #7 File Patch: `src/app.py`**" in result
    assert "- status: modified" in result
    assert "```diff" in result
    assert "+print('y')" in result


@pytest.mark.asyncio
async def test_github_get_pr_file_patch_returns_error_when_not_found(monkeypatch):
    async def _fake_get_pr_files(owner, repo, pull_number, page=1, per_page=100):
        return [{"filename": "src/other.py", "status": "modified", "additions": 1, "deletions": 0}]

    monkeypatch.setattr(github_api.github_channel, "get_pr_files", _fake_get_pr_files)

    result = await github_api.github_get_pr_file_patch("acme", "repo", 7, "src/missing.py")
    assert result == "Error: File `src/missing.py` not found in PR #7"


@pytest.mark.asyncio
async def test_github_get_pr_file_patch_searches_paginated_files(monkeypatch):
    calls = []

    async def _fake_get_pr_files(owner, repo, pull_number, page=1, per_page=100):
        calls.append((page, per_page))
        if page == 1:
            return [{"filename": f"src/file_{i}.py"} for i in range(100)]
        return [{"filename": "src/target.py", "status": "modified", "additions": 2, "deletions": 1, "patch": "+ok"}]

    monkeypatch.setattr(github_api.github_channel, "get_pr_files", _fake_get_pr_files)

    result = await github_api.github_get_pr_file_patch("acme", "repo", 7, "src/target.py")
    assert "src/target.py" in result
    assert calls[0] == (1, 100)
    assert calls[1] == (2, 100)


@pytest.mark.asyncio
async def test_github_get_pr_file_patch_uses_robust_markdown_fence(monkeypatch):
    async def _fake_get_pr_files(owner, repo, pull_number, page=1, per_page=100):
        return [
            {
                "filename": "src/app.py",
                "status": "modified",
                "additions": 1,
                "deletions": 0,
                "patch": "line1\n````\nline2",
            }
        ]

    monkeypatch.setattr(github_api.github_channel, "get_pr_files", _fake_get_pr_files)

    result = await github_api.github_get_pr_file_patch("acme", "repo", 7, "src/app.py")
    assert "`````diff" in result
    assert result.endswith("`````")


@pytest.mark.asyncio
async def test_channel_get_pr_files_passes_pagination_params(monkeypatch):
    captured = {}

    async def _fake_request(method, endpoint, **kwargs):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["params"] = kwargs.get("params")
        return []

    monkeypatch.setattr(github_api.github_channel, "_request", _fake_request)

    await github_api.github_channel.get_pr_files("acme", "repo", 99, page=3, per_page=50)
    assert captured["method"] == "GET"
    assert captured["endpoint"] == "/repos/acme/repo/pulls/99/files"
    assert captured["params"] == {"page": 3, "per_page": 50}
