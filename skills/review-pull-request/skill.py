from __future__ import annotations

import logging
from typing import Any, Dict

from src.agents.executor import SkillResult, skill
from src.agents.llm import llm_client
from src.github.api import github_channel

logger = logging.getLogger(__name__)
_ALLOWED_REVIEW_EVENTS = {"COMMENT", "APPROVE", "REQUEST_CHANGES"}


async def _safe_fetch_pr_context(owner: str, repo: str, pull_number: int) -> dict[str, Any]:
    pr = await github_channel.get_pull_request(owner, repo, pull_number)
    files = await github_channel.get_pr_files(owner, repo, pull_number)
    diff_payload = await github_channel.get_pr_diff(owner, repo, pull_number)
    comments = await github_channel.get_pr_comments(owner, repo, pull_number)
    reviews = await github_channel.list_pr_reviews(owner, repo, pull_number)

    return {
        "pr": pr if isinstance(pr, dict) else {},
        "files": files if isinstance(files, list) else [],
        "diff": (diff_payload or {}).get("diff", "") if isinstance(diff_payload, dict) else "",
        "comments": comments if isinstance(comments, list) else [],
        "reviews": reviews if isinstance(reviews, list) else [],
    }


@skill(
    name="review-pull-request",
    description="Generate actionable pull-request review content from GitHub context.",
)
async def review_pull_request(
    owner: str,
    repo: str,
    pull_number: int,
    head_sha: str | None = None,
    review_target: Dict[str, Any] | None = None,
    review_event: str | None = None,
    max_files: int = 80,
    max_diff_chars: int = 60000,
) -> SkillResult:
    try:
        normalized_pull_number = int(pull_number)
        if normalized_pull_number <= 0:
            raise ValueError("pull_number must be a positive integer")
    except Exception as exc:
        return SkillResult(success=False, error=f"Invalid pull_number: {exc}")

    try:
        context = await _safe_fetch_pr_context(owner, repo, normalized_pull_number)
    except Exception as exc:
        logger.warning(
            "review-pull-request context fetch failed for %s/%s#%s: %s",
            owner,
            repo,
            normalized_pull_number,
            exc,
        )
        return SkillResult(success=False, error=f"Failed to fetch pull request context: {exc}")

    pr = context["pr"]
    normalized_review_event = (
        str(review_event).strip().upper()
        if isinstance(review_event, str) and str(review_event).strip()
        else "COMMENT"
    )
    if normalized_review_event not in _ALLOWED_REVIEW_EVENTS:
        normalized_review_event = "COMMENT"
    files = context["files"][: max(max_files, 1)]
    diff_text = str(context["diff"] or "")
    truncated = len(diff_text) > max(max_diff_chars, 1000)
    if truncated:
        diff_text = diff_text[: max(max_diff_chars, 1000)] + "\n\n[Diff truncated for context window safety]"

    file_lines = []
    for f in files:
        if not isinstance(f, dict):
            continue
        file_lines.append(
            f"- {f.get('filename', 'unknown')} [{f.get('status', 'modified')}] +{f.get('additions', 0)} -{f.get('deletions', 0)}"
        )

    prompt = (
        "You are an expert pull request reviewer. Provide high-signal, actionable feedback. "
        "Use review COMMENT style only; do not approve or request changes automatically.\n\n"
        f"Repository: {owner}/{repo}\n"
        f"PR: #{normalized_pull_number}\n"
        f"Title: {pr.get('title', '')}\n"
        f"Body: {pr.get('body', '')}\n"
        f"Base branch: {(pr.get('base') or {}).get('ref', '')}\n"
        f"Head branch: {(pr.get('head') or {}).get('ref', '')}\n"
        f"Head SHA (task): {head_sha or ''}\n"
        f"Review target: {review_target or {}}\n\n"
        f"Requested review event default: {normalized_review_event}\n"
        "Do not approve unless confidence is very high; default to COMMENT-style actionable feedback.\n\n"
        f"Changed files ({len(file_lines)} shown):\n" + ("\n".join(file_lines) or "- none") + "\n\n"
        f"Existing review comments count: {len(context['comments'])}\n"
        f"Existing review entries count: {len(context['reviews'])}\n\n"
        "Unified diff:\n"
        f"{diff_text}\n\n"
        "Return markdown with:\n"
        "1) Pull Request Summary\n"
        "2) Findings (severity, file, issue, why it matters, suggested fix)."
    )

    try:
        llm_response = await llm_client.responses(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1200,
        )
    except Exception as exc:
        logger.warning("review-pull-request llm call failed: %s", exc)
        return SkillResult(success=False, error=f"Failed to generate review content: {exc}")

    review_text = str((llm_response or {}).get("content") or "").strip()
    if not review_text:
        return SkillResult(success=False, error="LLM returned empty review content")

    return SkillResult(
        success=True,
        output=review_text,
        data={
            "review_summary": review_text,
            "review_event": normalized_review_event,
            "context": {
                "owner": owner,
                "repo": repo,
                "pull_number": normalized_pull_number,
                "files_considered": len(files),
                "diff_truncated": truncated,
            },
        },
    )
