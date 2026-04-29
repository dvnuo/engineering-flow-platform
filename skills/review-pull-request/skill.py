from __future__ import annotations

from src.agents.executor import SkillResult, skill


@skill(
    name="review-pull-request",
    description="Compatibility shim. Real PR review runs through chat/tool loop with skill.md.",
)
async def review_pull_request(*args, **kwargs) -> SkillResult:
    return SkillResult(
        success=False,
        error=(
            "review-pull-request is a chat/tool-loop skill. "
            "Do not execute skills/review-pull-request/skill.py directly. "
            "Use github_review_task or a chat turn with `/skill use review-pull-request`."
        ),
        data={"execution_mode": "chat_tool_loop_required"},
    )
