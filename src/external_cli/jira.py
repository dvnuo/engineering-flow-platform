"""Jira adapter backed by the external jira CLI."""

from __future__ import annotations

import json
from typing import Any

from src.external_cli.runner import ExternalCLIError, run_json


def _json_args(*args: str) -> list[str]:
    return ["jira", *[str(arg) for arg in args], "--json"]


async def get_issue(issue_key: str) -> dict:
    return await run_json(_json_args("issue", "get", issue_key))


async def search_issues(jql: str, *, max_results: int = 50, start: int | None = None, fields: list[str] | None = None) -> dict:
    args = _json_args("issue", "search", "--jql", jql, "--limit", str(int(max_results)))
    if start is not None:
        args[-1:-1] = ["--start", str(int(start))]
    for field in fields or []:
        args[-1:-1] = ["--fields", str(field)]
    return await run_json(args)


async def update_issue(issue_key: str, *, summary: str | None = None, description: str | None = None, fields: dict | None = None) -> dict:
    args = ["jira", "issue", "update", issue_key]
    if summary:
        args.extend(["--summary", str(summary)])
    if description:
        args.extend(["--description", str(description)])
    for key, value in (fields or {}).items():
        args.extend(["--field", f"{key}={json.dumps(value, ensure_ascii=False)}"])
    args.append("--json")
    return await run_json(args)


async def assign_issue(issue_key: str, assignee: str | None = None) -> dict:
    if not assignee:
        raise ValueError("assignee is required")
    return await run_json(_json_args("issue", "assign", issue_key, "--user", assignee))


async def transition_issue(issue_key: str, *, transition: str | None = None, transition_id: str | None = None, comment: str | None = None, fields: dict | None = None) -> dict:
    args = ["jira", "issue", "transition", issue_key]
    if transition_id:
        args.extend(["--transition-id", str(transition_id)])
    elif transition:
        args.extend(["--to", str(transition)])
    else:
        raise ValueError("transition or transition_id is required")
    if comment:
        args.extend(["--comment", str(comment)])
    for key, value in (fields or {}).items():
        args.extend(["--field", f"{key}={json.dumps(value, ensure_ascii=False)}"])
    args.append("--json")
    return await run_json(args)


async def add_comment(issue_key: str, comment: str) -> dict:
    return await run_json(_json_args("comment", "add", issue_key, "--body-stdin"), input_text=str(comment))


async def add_comment_long(issue_key: str, comment: str, *, chunk_size: int = 30000) -> dict:
    text = str(comment or "")
    if len(text) <= chunk_size:
        return await add_comment(issue_key, text)
    results = []
    for index in range(0, len(text), chunk_size):
        chunk = text[index : index + chunk_size]
        results.append(await add_comment(issue_key, chunk))
    return {"success": True, "chunks": len(results), "results": results}


async def export_issues_to_markdown(**_kwargs: Any) -> dict:
    raise ExternalCLIError(
        "Jira markdown export is not supported by the external jira CLI adapter; "
        "use jira issue get/search JSON commands and render markdown outside the runtime."
    )
