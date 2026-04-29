import pytest


@pytest.mark.asyncio
async def test_review_pull_request_skill_is_registered_but_requires_chat_tool_loop():
    from src.agents.executor import execute_skill, list_available_skills

    assert "review-pull-request" in list_available_skills()

    result = await execute_skill(
        "review-pull-request",
        _use_execution_bus=False,
        owner="acme",
        repo="repo",
        pull_number=1,
        review_event="APPROVE",
    )

    assert result.success is False
    assert "chat/tool-loop skill" in (result.error or "")
    assert result.data["execution_mode"] == "chat_tool_loop_required"
