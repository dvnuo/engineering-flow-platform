"""Runtime execution for triggered external events."""

from __future__ import annotations

from typing import Any, Dict

from src.agents.core import agent
from src.channels.confluence import confluence_channel
from src.channels.github import github_channel
from src.channels.jira import jira_channel


def _require(payload: Dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"Missing required field: {key}")
    return value


async def run_triggered_event_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    source_kind = str(payload.get("source_kind") or "").strip().lower()
    if source_kind == "github.mention":
        owner = str(_require(payload, "owner"))
        repo = str(_require(payload, "repo"))
        issue_number = int(payload.get("issue_number") or payload.get("pull_number") or 0)
        if issue_number <= 0:
            raise ValueError("Missing required field: issue_number")
        author = str(payload.get("author") or "unknown")
        comment_url = str(payload.get("html_url") or payload.get("url") or "")
        comment_body = str(payload.get("body") or "")
        message = (
            "你在 GitHub issue/PR 评论中被提及。\n"
            f"仓库: {owner}/{repo}\n"
            f"编号: #{issue_number}\n"
            f"作者: {author}\n"
            f"评论链接: {comment_url}\n"
            f"评论内容:\n{comment_body}\n\n"
            "请生成一段简洁、直接、可直接发布的回复。必要时可使用已有工具查看更多上下文。"
        )
        result = await agent.process(message=message)
        response_text = str(result.get("response") or result.get("output") or "").strip()
        if not response_text:
            raise RuntimeError("Agent returned empty response for github mention")
        await github_channel.add_comment(owner, repo, issue_number, response_text)
        return {"success": True, "source_kind": source_kind, "response": response_text}

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
        result = await agent.process(message=message)
        response_text = str(result.get("response") or result.get("output") or "").strip()
        if not response_text:
            raise RuntimeError("Agent returned empty response for jira assigned")
        await jira_channel.add_comment(issue_key, response_text)
        return {"success": True, "source_kind": source_kind, "response": response_text}

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
        result = await agent.process(message=message)
        response_text = str(result.get("response") or result.get("output") or "").strip()
        if not response_text:
            raise RuntimeError("Agent returned empty response for jira mention")
        await jira_channel.add_comment(issue_key, response_text)
        return {"success": True, "source_kind": source_kind, "response": response_text}

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
        result = await agent.process(message=message)
        response_text = str(result.get("response") or result.get("output") or "").strip()
        if not response_text:
            raise RuntimeError("Agent returned empty response for confluence mention")
        await confluence_channel.add_comment(page_id, response_text)
        return {"success": True, "source_kind": source_kind, "response": response_text}

    raise ValueError(f"Unsupported source_kind: {source_kind}")
