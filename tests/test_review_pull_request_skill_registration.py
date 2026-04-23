import pytest


@pytest.mark.asyncio
async def test_review_pull_request_skill_is_registered_and_executable(monkeypatch):
    from src.agents.executor import execute_skill, list_available_skills

    assert "review-pull-request" in list_available_skills()

    async def _fake_get_pull_request(owner, repo, pull_number):
        return {
            "title": "Improve runtime dispatch",
            "body": "Move polling to portal",
            "base": {"ref": "main"},
            "head": {"ref": "feature/runtime-only", "sha": "abc123"},
        }

    async def _fake_get_pr_files(owner, repo, pull_number):
        return [{"filename": "src/runtime/github_review.py", "status": "modified", "additions": 10, "deletions": 2}]

    async def _fake_get_pr_diff(owner, repo, pull_number):
        return {"diff": "diff --git a/x b/x\n+new line"}

    async def _fake_get_pr_comments(owner, repo, pull_number):
        return [{"id": 1, "body": "existing"}]

    async def _fake_list_pr_reviews(owner, repo, pull_number):
        return [{"id": 2, "state": "COMMENTED"}]

    async def _fake_responses(*args, **kwargs):
        return {"content": "## Pull Request Summary\nLooks good with suggestions."}

    async def _unexpected_writeback(*args, **kwargs):
        raise AssertionError("skill shim must not submit GitHub review writeback directly")

    monkeypatch.setattr("skills.review-pull-request.skill.github_channel.get_pull_request", _fake_get_pull_request)
    monkeypatch.setattr("skills.review-pull-request.skill.github_channel.get_pr_files", _fake_get_pr_files)
    monkeypatch.setattr("skills.review-pull-request.skill.github_channel.get_pr_diff", _fake_get_pr_diff)
    monkeypatch.setattr("skills.review-pull-request.skill.github_channel.get_pr_comments", _fake_get_pr_comments)
    monkeypatch.setattr("skills.review-pull-request.skill.github_channel.list_pr_reviews", _fake_list_pr_reviews)
    monkeypatch.setattr("src.agents.llm.llm_client.responses", _fake_responses)
    monkeypatch.setattr("skills.review-pull-request.skill.github_channel.add_pr_review_comment", _unexpected_writeback)

    result = await execute_skill(
        "review-pull-request",
        _use_execution_bus=False,
        owner="acme",
        repo="repo",
        pull_number=1,
        review_event="APPROVE",
    )

    assert result.success is True
    assert "Pull Request Summary" in result.output
    assert result.data["requested_review_event"] == "APPROVE"
    assert "review_event" not in result.data
