"""GitHub adapter backed by installed gh and git tooling."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from src.external_cli.runner import run_json, run_text


PUBLIC_GITHUB_WEB_HOST = "github.com"
PUBLIC_GITHUB_API_BASE = "https://api.github.com"
PUBLIC_GITHUB_API_HOST = "api.github.com"


@dataclass(frozen=True)
class GitHubDocRef:
    owner: str
    repo: str
    branch: str
    path: str


def normalize_github_api_base_url(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return PUBLIC_GITHUB_API_BASE
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    host = parsed.hostname
    if not host:
        return PUBLIC_GITHUB_API_BASE
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    path = (parsed.path or "").rstrip("/")
    if host.lower() in {PUBLIC_GITHUB_WEB_HOST, PUBLIC_GITHUB_API_HOST}:
        return PUBLIC_GITHUB_API_BASE
    if path == "":
        path = "/api/v3"
    elif path.lower() == "/api/v3":
        path = "/api/v3"
    return urlunsplit(("https", netloc, path, "", ""))


def github_hostname_from_base_url(raw: str | None) -> str:
    normalized = normalize_github_api_base_url(raw)
    parsed = urlsplit(normalized)
    if parsed.netloc == PUBLIC_GITHUB_API_HOST:
        return PUBLIC_GITHUB_WEB_HOST
    return parsed.netloc or PUBLIC_GITHUB_WEB_HOST


def _allowed_github_hosts() -> set[str]:
    return {PUBLIC_GITHUB_WEB_HOST, PUBLIC_GITHUB_API_HOST}


def parse_github_doc_ref(raw: str, default_ref: Any) -> GitHubDocRef:
    normalized = str(raw or "").strip()
    if not normalized:
        raise ValueError("github_doc_ref is required")
    if normalized.startswith(("http://", "https://")):
        parsed = urlparse(normalized)
        if parsed.netloc.lower() not in _allowed_github_hosts():
            raise ValueError(f"Unsupported GitHub doc URL host: {parsed.netloc}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 5 or parts[2] != "blob":
            raise ValueError("GitHub doc URL must be in /owner/repo/blob/<branch>/<path> format")
        return GitHubDocRef(owner=parts[0], repo=parts[1], branch=parts[3], path="/".join(parts[4:]).strip("/"))
    return GitHubDocRef(
        owner=_ref_value(default_ref, "owner"),
        repo=_ref_value(default_ref, "repo"),
        branch=_ref_value(default_ref, "branch"),
        path=normalized.strip("/"),
    )


def _ref_value(default_ref: Any, key: str) -> str:
    value = getattr(default_ref, key, None)
    if value is None and isinstance(default_ref, dict):
        value = default_ref.get(key)
    return str(value or "")


def _repo(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


def _contents_endpoint(owner: str, repo: str, path: str) -> str:
    return f"repos/{owner}/{repo}/contents/{quote(str(path).strip('/'), safe='/')}"


async def get_pull_request(owner: str, repo: str, pull_number: int) -> dict:
    raw = await run_json(
        [
            "gh",
            "pr",
            "view",
            str(int(pull_number)),
            "--repo",
            _repo(owner, repo),
            "--json",
            "headRefOid,headRefName,baseRefName,number,state,title,url",
        ]
    )
    head_sha = raw.get("headRefOid")
    if isinstance(head_sha, str) and head_sha:
        raw.setdefault("head", {})["sha"] = head_sha
    return raw


async def add_comment(owner: str, repo: str, issue_number: int, comment: str) -> dict:
    await run_text(
        ["gh", "issue", "comment", str(int(issue_number)), "--repo", _repo(owner, repo), "--body-file", "-"],
        input_text=str(comment),
    )
    return {"success": True, "commented": True}


async def review_pull_request(
    *,
    owner: str,
    repo: str,
    pull_number: int,
    body: str | None = None,
    event: str = "COMMENT",
    commit_id: str | None = None,
    path: str | None = None,
    line: int | None = None,
) -> dict:
    _ = (commit_id, path, line)
    normalized_event = str(event or "COMMENT").strip().upper()
    event_flag = {
        "APPROVE": "--approve",
        "REQUEST_CHANGES": "--request-changes",
        "COMMENT": "--comment",
    }.get(normalized_event)
    if event_flag is None:
        raise ValueError(f"Invalid review_event: {event}")
    await run_text(
        ["gh", "pr", "review", str(int(pull_number)), "--repo", _repo(owner, repo), event_flag, "--body-file", "-"],
        input_text=str(body or ""),
    )
    return {"success": True, "review_event": normalized_event}


async def reply_pr_review_comment(owner: str, repo: str, pull_number: int, comment_id: int, comment: str) -> dict:
    payload = json.dumps({"body": str(comment)}, ensure_ascii=False)
    return await run_json(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo}/pulls/{int(pull_number)}/comments/{int(comment_id)}/replies",
            "--method",
            "POST",
            "--input",
            "-",
        ],
        input_text=payload,
    )


async def add_commit_comment(
    *,
    owner: str,
    repo: str,
    commit_sha: str,
    comment: str,
    path: str | None = None,
    line: int | None = None,
    position: int | None = None,
) -> dict:
    payload: dict[str, Any] = {"body": str(comment)}
    if path:
        payload["path"] = str(path)
    if line is not None:
        payload["line"] = int(line)
    if position is not None:
        payload["position"] = int(position)
    return await run_json(
        ["gh", "api", f"repos/{owner}/{repo}/commits/{commit_sha}/comments", "--method", "POST", "--input", "-"],
        input_text=json.dumps(payload, ensure_ascii=False),
    )


async def add_discussion_comment(discussion_id: str, comment: str, reply_to_id: str | None = None) -> dict:
    query = """
mutation($discussionId: ID!, $body: String!, $replyToId: ID) {
  addDiscussionComment(input: {discussionId: $discussionId, body: $body, replyToId: $replyToId}) {
    comment { id url }
  }
}
"""
    payload = {"query": query, "variables": {"discussionId": discussion_id, "body": comment, "replyToId": reply_to_id}}
    return await run_json(["gh", "api", "graphql", "--input", "-"], input_text=json.dumps(payload, ensure_ascii=False))


async def get_file(owner: str, repo: str, path: str, ref: str = "") -> dict:
    args = ["gh", "api", _contents_endpoint(owner, repo, path), "--method", "GET"]
    if ref:
        args.extend(["-f", f"ref={ref}"])
    return await run_json(args)


async def create_or_update_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    *,
    sha: str | None = None,
    branch: str = "",
) -> dict:
    resolved_sha = sha
    if not resolved_sha:
        try:
            current = await get_file(owner, repo, path, branch)
            current_sha = current.get("sha")
            if isinstance(current_sha, str) and current_sha.strip():
                resolved_sha = current_sha.strip()
        except Exception:
            resolved_sha = None
    payload: dict[str, Any] = {
        "message": str(message),
        "content": base64.b64encode(str(content).encode("utf-8")).decode("ascii"),
    }
    if branch:
        payload["branch"] = str(branch)
    if resolved_sha:
        payload["sha"] = resolved_sha
    return await run_json(
        ["gh", "api", _contents_endpoint(owner, repo, path), "--method", "PUT", "--input", "-"],
        input_text=json.dumps(payload, ensure_ascii=False),
    )


async def prepare_github_file_source(raw: str, default_ref: Any, session_id: str | None = None) -> dict:
    _ = session_id
    doc_ref = parse_github_doc_ref(raw, default_ref)
    file_data = await get_file(doc_ref.owner, doc_ref.repo, doc_ref.path, doc_ref.branch)
    encoded = file_data.get("content")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ValueError(f"File not found or empty: {doc_ref.owner}/{doc_ref.repo}/{doc_ref.path}@{doc_ref.branch}")
    content_text = base64.b64decode(encoded).decode("utf-8")
    bundle = {
        "content_markdown": content_text,
        "artifact_refs": [],
        "context_ref": None,
        "digest_ref": None,
        "completeness_ledger": {
            "complete": session_id is not None,
            "partial_reasons": [] if session_id else ["session_scope_missing"],
        },
    }
    return {"doc_ref": doc_ref, "bundle": bundle}
