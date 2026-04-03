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


@pytest.mark.asyncio
async def test_github_get_pr_file_patch_formats_patch(monkeypatch):
    async def _fake_get_pr_files(owner, repo, pull_number):
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

