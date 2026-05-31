"""Runtime execution for triggered external events."""

from __future__ import annotations

from typing import Any, Dict

from src.external_cli import confluence as confluence_cli
from src.external_cli import github as github_cli
from src.external_cli import jira as jira_cli
from src.gateway.runtime_chat import run_runtime_chat


def _require(payload: Dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Missing required field: {key}")
    return value


async def _run_agent_response(
    message: str,
    session_id: str,
    execution_metadata: Dict[str, Any] | None = None,
) -> str:
    result = await run_runtime_chat(
        message=message,
        session_id=session_id,
        user_name="triggered-event",
        request_path="triggered_event",
        track_usage=False,
        execution_metadata=execution_metadata if isinstance(execution_metadata, dict) else None,
    )
    response_text = str(result.get("response") or result.get("output") or "").strip()
    if not response_text:
        raise RuntimeError("Agent returned empty response for triggered event")
    return response_text


ALLOWED_GITHUB_MENTION_COMMENT_KINDS = {"issue_comment", "pull_request_review_comment", "commit_comment", "discussion_comment"}
ALLOWED_GITHUB_MENTION_REPLY_MODES = {"same_surface", "timeline"}

def _resolve_secondary_action(source_kind: str, payload: Dict[str, Any] | None = None) -> tuple[str, str]:
    normalized_source_kind = str(source_kind or "").strip().lower()
    payload = payload if isinstance(payload, dict) else {}
    if normalized_source_kind == "github.mention":
        comment_kind = str(payload.get("comment_kind") or "").strip().lower()
        if comment_kind and comment_kind not in ALLOWED_GITHUB_MENTION_COMMENT_KINDS:
            raise ValueError(f"Unsupported GitHub mention comment_kind: {comment_kind}")
        reply_mode = str(payload.get("reply_mode") or "same_surface").strip().lower()
        if reply_mode not in ALLOWED_GITHUB_MENTION_REPLY_MODES:
            raise ValueError(f"Unsupported GitHub mention reply_mode: {reply_mode}")
        if comment_kind == "commit_comment":
            return ("adapter:github:add_commit_comment", "adapter_action")
        if comment_kind == "discussion_comment":
            return ("adapter:github:add_discussion_comment", "adapter_action")
        if comment_kind == "pull_request_review_comment" and reply_mode == "same_surface":
            return ("adapter:github:reply_review_comment", "adapter_action")
        return ("adapter:github:add_comment", "adapter_action")
    mapping = {
        "jira.assigned": ("adapter:jira:add_comment", "adapter_action"),
        "jira.mention": ("adapter:jira:add_comment", "adapter_action"),
        "confluence.mention": ("adapter:confluence:add_comment", "adapter_action"),
    }
    resolved = mapping.get(normalized_source_kind)
    if not resolved:
        raise ValueError(f"Unsupported source_kind: {source_kind}")
    return resolved


def _evaluate_action_gate(
    payload: Dict[str, Any],
    *,
    action_id: str,
    capability_type: str,
) -> Dict[str, Any]:
    gate = payload.get("_action_gate")
    if not callable(gate):
        return {"blocked": False}
    gate_result = gate(action_id, {"capability_type": capability_type})
    if not isinstance(gate_result, dict):
        return {"blocked": True, "reason": "invalid_action_gate_result", "error": "invalid action gate response"}
    return gate_result


async def run_triggered_event_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    source_kind = str(payload.get("source_kind") or "").strip().lower()
    session_id = str(_require(payload, "session_id"))
    execution_metadata = payload.get("_execution_metadata")
    if not isinstance(execution_metadata, dict):
        execution_metadata = None
    secondary_action_id, secondary_action_capability_type = _resolve_secondary_action(source_kind, payload)
    def _append_github_auto_reply_marker(response_text: str, payload: Dict[str, Any]) -> str:
        if "<!-- efp:auto-reply" in response_text:
            return response_text
        task_id = payload.get("task_id") or payload.get("_runtime_task_id") or payload.get("dedupe_key") or ""
        source_comment_id = payload.get("comment_id") or payload.get("review_comment_id") or ""
        rule_id = payload.get("automation_rule_id") or payload.get("rule_id") or ""
        marker = (
            f"<!-- efp:auto-reply source=github-comment-mention "
            f"task_id={task_id} source_comment_id={source_comment_id} rule_id={rule_id} -->"
        )
        return response_text.rstrip() + "\n\n" + marker
    if source_kind == "github.mention":
        owner = str(_require(payload, "owner"))
        repo = str(_require(payload, "repo"))
        comment_kind = str(payload.get("comment_kind") or "issue_comment").strip().lower()
        if comment_kind not in ALLOWED_GITHUB_MENTION_COMMENT_KINDS:
            raise ValueError(f"Unsupported GitHub mention comment_kind: {comment_kind}")
        reply_mode = str(payload.get("reply_mode") or "same_surface").strip().lower()
        if reply_mode not in ALLOWED_GITHUB_MENTION_REPLY_MODES:
            raise ValueError(f"Unsupported GitHub mention reply_mode: {reply_mode}")
        context_type = str(payload.get("context_type") or "")
        issue_number = payload.get("issue_number")
        pull_number = payload.get("pull_number")
        comment_id = payload.get("comment_id")
        review_comment_id = payload.get("review_comment_id")
        author = str(payload.get("author") or "unknown")
        author_association = str(payload.get("author_association") or "")
        comment_url = str(payload.get("html_url") or payload.get("url") or "")
        comment_body = str(payload.get("body") or "")
        path = payload.get("path")
        line = payload.get("line")
        side = payload.get("side")
        diff_hunk = payload.get("diff_hunk")
        message = (
            "你在 GitHub 评论中被提及。\n"
            f"仓库: {owner}/{repo}\n"
            f"Surface/comment_kind: {comment_kind}\n"
            f"Context type: {context_type}\n"
            f"Issue number: {issue_number}\n"
            f"Pull number: {pull_number}\n"
            f"Comment id: {comment_id}\n"
            f"Review comment id: {review_comment_id}\n"
            f"Commit SHA: {payload.get('commit_sha') or payload.get('commit_id')}\n"
            f"Discussion number: {payload.get('discussion_number')}\n"
            f"Discussion id: {payload.get('discussion_id')}\n"
            f"Discussion comment id: {payload.get('discussion_comment_id')}\n"
            f"Notification id: {payload.get('notification_id')}\n"
            f"Notification reason: {payload.get('notification_reason')}\n"
            f"Notification subject type: {payload.get('notification_subject_type')}\n"
            f"Notification URL: {payload.get('notification_url')}\n"
            f"Notification updated at: {payload.get('notification_updated_at')}\n"
            f"作者: {author}\n"
            f"Author association: {author_association}\n"
            f"评论链接: {comment_url}\n"
            f"Path: {path}\n"
            f"Line: {line}\n"
            f"Side: {side}\n"
            f"Diff hunk:\n{diff_hunk}\n"
            f"评论内容:\n{comment_body}\n\n"
            "请生成一条可以直接发布到 GitHub 的回复。\n"
            "不要输出隐藏推理过程。\n"
            "不要声称执行了未实际执行的操作。\n"
            "如果信息不足，请提出具体澄清问题。\n"
            "回复要简洁、可执行。"
        )
        response_text = await _run_agent_response(
            message,
            session_id,
            execution_metadata=execution_metadata,
        )
        gate_result = _evaluate_action_gate(
            payload,
            action_id=secondary_action_id,
            capability_type=secondary_action_capability_type,
        )
        if gate_result.get("blocked"):
            blocked_reason = str(gate_result.get("reason") or "capability_policy_blocked")
            return {
                "success": False,
                "source_kind": source_kind,
                "response": response_text,
                "secondary_action_attempted": True,
                "secondary_action_success": False,
                "secondary_action_id": secondary_action_id,
                "secondary_action_capability_type": secondary_action_capability_type,
                "error": str(gate_result.get("error") or f"capability policy blocked for secondary action: {secondary_action_id}"),
                "blocked": True,
                "blocked_reason": blocked_reason,
            }
        response_to_post = _append_github_auto_reply_marker(response_text, payload)
        if secondary_action_id == "adapter:github:add_commit_comment":
            commit_sha = str(payload.get("commit_sha") or payload.get("commit_id") or "").strip()
            if not commit_sha:
                raise ValueError("Missing required field: commit_sha")
            position = payload.get("position")
            await github_cli.add_commit_comment(
                owner=owner,
                repo=repo,
                commit_sha=commit_sha,
                comment=response_to_post,
                path=str(path).strip() if isinstance(path, str) and path.strip() else None,
                line=int(line) if isinstance(line, int) or (isinstance(line, str) and str(line).strip().isdigit()) else None,
                position=int(position) if isinstance(position, int) or (isinstance(position, str) and str(position).strip().isdigit()) else None,
            )
        elif secondary_action_id == "adapter:github:add_discussion_comment":
            discussion_id = str(payload.get("discussion_id") or "").strip()
            if not discussion_id:
                raise ValueError("Missing required field: discussion_id")
            reply_to_id = payload.get("reply_to_id") or payload.get("discussion_comment_id") or payload.get("comment_id")
            await github_cli.add_discussion_comment(
                discussion_id,
                response_to_post,
                reply_to_id=str(reply_to_id) if reply_to_id else None,
            )
        elif secondary_action_id == "adapter:github:reply_review_comment":
            pull_number_value = int(_require(payload, "pull_number"))
            source_comment_id = int(
                payload.get("in_reply_to_id") or payload.get("review_comment_id") or payload.get("comment_id") or 0
            )
            if source_comment_id <= 0:
                raise ValueError("Missing required field: comment_id for review comment reply")
            await github_cli.reply_pr_review_comment(owner, repo, pull_number_value, source_comment_id, response_to_post)
        else:
            issue_number_value = int(payload.get("issue_number") or payload.get("pull_number") or 0)
            if issue_number_value <= 0:
                raise ValueError("Missing required field: issue_number")
            await github_cli.add_comment(owner, repo, issue_number_value, response_to_post)
        return {
            "success": True,
            "source_kind": source_kind,
            "response": response_text,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": secondary_action_id,
            "secondary_action_capability_type": secondary_action_capability_type,
            "posted_comment": response_to_post,
        }

    if source_kind == "jira.assigned":
        issue_key = str(_require(payload, "issue_key"))
        summary = str(payload.get("summary") or "")
        status = str(payload.get("status") or "")
        assignee = str(payload.get("assignee") or "")
        message = (
            "你被指派到一个 Jira issue。\n"
            f"Issue: {issue_key}\n"
            f"Summary: {summary}\n"
            f"Status: {status}\n"
            f"Assignee: {assignee}\n\n"
            "请先审阅该 issue，再生成首条处理评论（包含你的理解、下一步、缺失信息）。"
        )
        response_text = await _run_agent_response(
            message,
            session_id,
            execution_metadata=execution_metadata,
        )
        gate_result = _evaluate_action_gate(
            payload,
            action_id=secondary_action_id,
            capability_type=secondary_action_capability_type,
        )
        if gate_result.get("blocked"):
            blocked_reason = str(gate_result.get("reason") or "capability_policy_blocked")
            return {
                "success": False,
                "source_kind": source_kind,
                "response": response_text,
                "secondary_action_attempted": True,
                "secondary_action_success": False,
                "secondary_action_id": secondary_action_id,
                "secondary_action_capability_type": secondary_action_capability_type,
                "error": str(gate_result.get("error") or f"capability policy blocked for secondary action: {secondary_action_id}"),
                "blocked": True,
                "blocked_reason": blocked_reason,
            }
        await jira_cli.add_comment(issue_key, response_text)
        return {
            "success": True,
            "source_kind": source_kind,
            "response": response_text,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": secondary_action_id,
            "secondary_action_capability_type": secondary_action_capability_type,
        }

    if source_kind == "jira.mention":
        issue_key = str(_require(payload, "issue_key"))
        author = str(payload.get("author") or "unknown")
        comment_body = str(payload.get("body") or "")
        message = (
            "你在 Jira comment 中被提及。\n"
            f"Issue: {issue_key}\n"
            f"作者: {author}\n"
            f"评论内容:\n{comment_body}\n\n"
            "请生成简洁且有帮助的回复。"
        )
        response_text = await _run_agent_response(
            message,
            session_id,
            execution_metadata=execution_metadata,
        )
        gate_result = _evaluate_action_gate(
            payload,
            action_id=secondary_action_id,
            capability_type=secondary_action_capability_type,
        )
        if gate_result.get("blocked"):
            blocked_reason = str(gate_result.get("reason") or "capability_policy_blocked")
            return {
                "success": False,
                "source_kind": source_kind,
                "response": response_text,
                "secondary_action_attempted": True,
                "secondary_action_success": False,
                "secondary_action_id": secondary_action_id,
                "secondary_action_capability_type": secondary_action_capability_type,
                "error": str(gate_result.get("error") or f"capability policy blocked for secondary action: {secondary_action_id}"),
                "blocked": True,
                "blocked_reason": blocked_reason,
            }
        await jira_cli.add_comment(issue_key, response_text)
        return {
            "success": True,
            "source_kind": source_kind,
            "response": response_text,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": secondary_action_id,
            "secondary_action_capability_type": secondary_action_capability_type,
        }

    if source_kind == "confluence.mention":
        page_id = str(_require(payload, "page_id"))
        title = str(payload.get("title") or "")
        space_key = str(payload.get("space_key") or payload.get("space") or "")
        author = str(payload.get("author") or "unknown")
        comment_body = str(payload.get("body") or "")
        message = (
            "你在 Confluence page comment 中被提及。\n"
            f"页面标题: {title}\n"
            f"空间: {space_key}\n"
            f"作者: {author}\n"
            f"评论内容:\n{comment_body}\n\n"
            "请生成简洁且有帮助的回复。"
        )
        response_text = await _run_agent_response(
            message,
            session_id,
            execution_metadata=execution_metadata,
        )
        gate_result = _evaluate_action_gate(
            payload,
            action_id=secondary_action_id,
            capability_type=secondary_action_capability_type,
        )
        if gate_result.get("blocked"):
            blocked_reason = str(gate_result.get("reason") or "capability_policy_blocked")
            return {
                "success": False,
                "source_kind": source_kind,
                "response": response_text,
                "secondary_action_attempted": True,
                "secondary_action_success": False,
                "secondary_action_id": secondary_action_id,
                "secondary_action_capability_type": secondary_action_capability_type,
                "error": str(gate_result.get("error") or f"capability policy blocked for secondary action: {secondary_action_id}"),
                "blocked": True,
                "blocked_reason": blocked_reason,
            }
        await confluence_cli.add_comment(page_id, response_text)
        return {
            "success": True,
            "source_kind": source_kind,
            "response": response_text,
            "secondary_action_attempted": True,
            "secondary_action_success": True,
            "secondary_action_id": secondary_action_id,
            "secondary_action_capability_type": secondary_action_capability_type,
        }

    raise ValueError(f"Unsupported source_kind: {source_kind}")
