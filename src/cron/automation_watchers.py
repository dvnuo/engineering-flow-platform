"""Portal-driven automation watchers for external-event ingress."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiohttp import ClientSession

from src.channels.confluence import confluence_channel
from src.channels.github import github_channel
from src.channels.jira import jira_channel
from src.config import config
from src.utils.portal_internal_api import (
    build_portal_internal_api_headers,
    build_portal_internal_url,
    get_portal_agent_id,
    get_portal_internal_base_url,
    is_portal_internal_configured,
)

logger = logging.getLogger(__name__)

_MAX_DEDUPE_KEYS_PER_RULE = 1000
_MENTION_PATTERN = re.compile(r"(?<![\\w])@([A-Za-z0-9][A-Za-z0-9_.-]{0,63})")


def _extract_mentions(text: str) -> set[str]:
    if not isinstance(text, str) or not text.strip():
        return set()
    return {match.group(1).strip().lower() for match in _MENTION_PATTERN.finditer(text) if match.group(1).strip()}


@dataclass
class IdentityBinding:
    id: str
    agent_id: str
    system_type: str
    external_account_id: Optional[str]
    username: Optional[str]
    scope: Dict[str, Any]
    enabled: bool


@dataclass
class AutomationRule:
    source_kind: str
    source_type: str
    event_type: str
    scope: Dict[str, Any]
    include_review_comments: bool
    binding_id: str
    binding_lookup_username: Optional[str]
    external_account_id: Optional[str]
    automation_rule: str


class AutomationWatcherManager:
    def __init__(self) -> None:
        self._running = False
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._last_check_by_rule: dict[str, datetime] = {}
        self._seen_event_keys: dict[str, set[str]] = {}
        self._seen_event_order: dict[str, deque[str]] = {}

    def _base_url(self) -> str:
        return get_portal_internal_base_url()

    def _agent_id(self) -> str:
        return get_portal_agent_id()

    def _headers(self) -> Dict[str, str]:
        return build_portal_internal_api_headers(include_content_type=True)

    async def _get_json(self, path: str, *, session: ClientSession | None = None) -> Dict[str, Any]:
        url = build_portal_internal_url(path)
        if session is not None:
            async with session.get(url) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}: {data}")
                return data if isinstance(data, dict) else {"items": data}
        async with ClientSession(headers=self._headers()) as temp_session:
            async with temp_session.get(url) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}: {data}")
                return data if isinstance(data, dict) else {"items": data}

    async def _post_json(self, path: str, payload: Dict[str, Any], *, session: ClientSession | None = None) -> None:
        url = build_portal_internal_url(path)
        if session is not None:
            async with session.post(url, json=payload) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"HTTP {response.status}: {body}")
            return
        async with ClientSession(headers=self._headers()) as temp_session:
            async with temp_session.post(url, json=payload) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"HTTP {response.status}: {body}")

    @staticmethod
    def _parse_mapping(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    @staticmethod
    def _extract_list_payload(raw: Any, *keys: str) -> List[Dict[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            for key in keys:
                value = raw.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    async def fetch_runtime_profile_config(self, agent_id: str, *, session: ClientSession | None = None) -> Dict[str, Any]:
        data = await self._get_json(f"/api/internal/agents/{agent_id}/runtime-context", session=session)
        context = data.get("runtime_profile_context") if isinstance(data.get("runtime_profile_context"), dict) else {}
        return self._parse_mapping(context.get("config"))

    async def fetch_identity_bindings(self, *, session: ClientSession | None = None) -> List[IdentityBinding]:
        data = await self._get_json("/api/internal/agent-identity-bindings?enabled=true", session=session)
        items = self._extract_list_payload(data, "items")
        normalized: list[IdentityBinding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                IdentityBinding(
                    id=str(item.get("id") or "").strip(),
                    agent_id=str(item.get("agent_id") or "").strip(),
                    system_type=str(item.get("system_type") or item.get("provider_type") or "").strip().lower(),
                    external_account_id=(str(item.get("external_account_id") or "").strip() or None),
                    username=(str(item.get("username") or "").strip() or None),
                    scope=self._parse_mapping(item.get("scope") or item.get("scope_json")),
                    enabled=bool(item.get("enabled", True)),
                )
            )
        return normalized

    @staticmethod
    def _normalize_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @classmethod
    def _merge_scope_dimension(cls, binding_scope: Dict[str, Any], automation_scope: Dict[str, Any], key: str) -> List[str]:
        binding_values = cls._normalize_list(binding_scope.get(key))
        if binding_values:
            return binding_values
        return cls._normalize_list(automation_scope.get(key))

    @classmethod
    def build_automation_rules(cls, runtime_config: Dict[str, Any], bindings: List[IdentityBinding], agent_id: str) -> List[AutomationRule]:
        rules: list[AutomationRule] = []
        for binding in bindings:
            if not binding.enabled or binding.agent_id != agent_id:
                continue

            if binding.system_type == "github":
                github_cfg = runtime_config.get("github") if isinstance(runtime_config.get("github"), dict) else {}
                if not github_cfg.get("enabled"):
                    continue
                automation = github_cfg.get("automation") if isinstance(github_cfg.get("automation"), dict) else {}
                review_requests = automation.get("review_requests") if isinstance(automation.get("review_requests"), dict) else {}
                mentions = automation.get("mentions") if isinstance(automation.get("mentions"), dict) else {}
                if review_requests.get("enabled"):
                    scope = {"repos": cls._merge_scope_dimension(binding.scope, review_requests, "repos")}
                    rules.append(
                        AutomationRule(
                            source_kind="github.pull_request_review_requested",
                            source_type="github",
                            event_type="pull_request_review_requested",
                            scope=scope,
                            include_review_comments=False,
                            binding_id=binding.id,
                            binding_lookup_username=binding.username or binding.external_account_id,
                            external_account_id=binding.external_account_id or binding.username,
                            automation_rule="github.review_requests",
                        )
                    )
                if mentions.get("enabled"):
                    scope = {"repos": cls._merge_scope_dimension(binding.scope, mentions, "repos")}
                    rules.append(
                        AutomationRule(
                            source_kind="github.mention",
                            source_type="github",
                            event_type="mention",
                            scope=scope,
                            include_review_comments=bool(mentions.get("include_review_comments")),
                            binding_id=binding.id,
                            binding_lookup_username=binding.username or binding.external_account_id,
                            external_account_id=binding.external_account_id or binding.username,
                            automation_rule="github.mentions",
                        )
                    )

            if binding.system_type == "jira":
                jira_cfg = runtime_config.get("jira") if isinstance(runtime_config.get("jira"), dict) else {}
                if not jira_cfg.get("enabled"):
                    continue
                automation = jira_cfg.get("automation") if isinstance(jira_cfg.get("automation"), dict) else {}
                assignments = automation.get("assignments") if isinstance(automation.get("assignments"), dict) else {}
                mentions = automation.get("mentions") if isinstance(automation.get("mentions"), dict) else {}
                if assignments.get("enabled"):
                    scope = {"projects": cls._merge_scope_dimension(binding.scope, assignments, "projects")}
                    rules.append(
                        AutomationRule(
                            source_kind="jira.assigned",
                            source_type="jira",
                            event_type="assigned",
                            scope=scope,
                            include_review_comments=False,
                            binding_id=binding.id,
                            binding_lookup_username=binding.username or binding.external_account_id,
                            external_account_id=binding.external_account_id or binding.username,
                            automation_rule="jira.assignments",
                        )
                    )
                if mentions.get("enabled"):
                    scope = {"projects": cls._merge_scope_dimension(binding.scope, mentions, "projects")}
                    rules.append(
                        AutomationRule(
                            source_kind="jira.mention",
                            source_type="jira",
                            event_type="mention",
                            scope=scope,
                            include_review_comments=False,
                            binding_id=binding.id,
                            binding_lookup_username=binding.username or binding.external_account_id,
                            external_account_id=binding.external_account_id or binding.username,
                            automation_rule="jira.mentions",
                        )
                    )

            if binding.system_type == "confluence":
                conf_cfg = runtime_config.get("confluence") if isinstance(runtime_config.get("confluence"), dict) else {}
                if not conf_cfg.get("enabled"):
                    continue
                automation = conf_cfg.get("automation") if isinstance(conf_cfg.get("automation"), dict) else {}
                mentions = automation.get("mentions") if isinstance(automation.get("mentions"), dict) else {}
                if mentions.get("enabled"):
                    scope = {"spaces": cls._merge_scope_dimension(binding.scope, mentions, "spaces")}
                    rules.append(
                        AutomationRule(
                            source_kind="confluence.mention",
                            source_type="confluence",
                            event_type="mention",
                            scope=scope,
                            include_review_comments=False,
                            binding_id=binding.id,
                            binding_lookup_username=binding.username or binding.external_account_id,
                            external_account_id=binding.external_account_id or binding.username,
                            automation_rule="confluence.mentions",
                        )
                    )
        return rules

    @staticmethod
    def _rule_key(rule: AutomationRule) -> str:
        return f"{rule.binding_id}:{rule.source_kind}:{json.dumps(rule.scope, sort_keys=True)}"

    def _is_seen(self, rule_key: str, dedupe_key: str) -> bool:
        return dedupe_key in self._seen_event_keys.get(rule_key, set())

    def _mark_seen(self, rule_key: str, dedupe_key: str) -> None:
        if rule_key not in self._seen_event_keys:
            self._seen_event_keys[rule_key] = set()
            self._seen_event_order[rule_key] = deque()
        seen_set = self._seen_event_keys[rule_key]
        order = self._seen_event_order[rule_key]
        if dedupe_key in seen_set:
            return
        seen_set.add(dedupe_key)
        order.append(dedupe_key)
        while len(order) > _MAX_DEDUPE_KEYS_PER_RULE:
            seen_set.discard(order.popleft())

    @staticmethod
    def _extract_mentions(text: str) -> set[str]:
        return _extract_mentions(text)

    def _mention_matches_rule(self, rule: AutomationRule, mentions: set[str]) -> bool:
        lookup = str(rule.binding_lookup_username or "").strip().lower()
        external = str(rule.external_account_id or "").strip().lower()
        return (lookup and lookup in mentions) or (external and external in mentions)

    def _build_poll_metadata(self, rule: AutomationRule) -> str:
        metadata: Dict[str, Any] = {
            "trigger_mode": "poll",
            "source_kind": rule.source_kind,
            "watcher_agent_id": self._agent_id(),
            "binding_id": rule.binding_id,
            "automation_rule": rule.automation_rule,
        }
        if rule.binding_lookup_username:
            metadata["binding_lookup_username"] = rule.binding_lookup_username
        return json.dumps(metadata, ensure_ascii=False)

    async def _ingest_event(self, payload: Dict[str, Any], *, session: ClientSession | None = None) -> None:
        await self._post_json("/api/internal/external-events/ingest", payload, session=session)

    async def _poll_github_review_requests(self, rule: AutomationRule, *, session: ClientSession | None = None) -> None:
        rule_key = self._rule_key(rule)
        repos = self._normalize_list(rule.scope.get("repos"))
        lookup_name = str(rule.binding_lookup_username or "").strip()
        if not repos or not lookup_name:
            return
        for repo_ref in repos:
            if "/" not in repo_ref:
                continue
            owner, repo = repo_ref.split("/", 1)
            query = f"repo:{owner}/{repo} is:pr is:open review-requested:{lookup_name}"
            try:
                result = await github_channel.search_issues(query, max_results=50)
            except Exception as exc:
                logger.warning("GitHub review polling failed for %s: %s", repo_ref, exc)
                continue
            items = result.get("items") if isinstance(result, dict) and isinstance(result.get("items"), list) else []
            for item in items:
                pull_number = item.get("number") if isinstance(item, dict) else None
                if not isinstance(pull_number, int):
                    continue
                try:
                    pr = await github_channel.get_pull_request(owner, repo, pull_number)
                    head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
                    if not head_sha:
                        continue
                    dedupe_key = f"github:review:{owner}/{repo}:{pull_number}:{lookup_name}:{head_sha}"
                    if self._is_seen(rule_key, dedupe_key):
                        continue
                    await self._ingest_event(
                        {
                            "source_type": "github",
                            "event_type": "pull_request_review_requested",
                            "external_account_id": rule.external_account_id,
                            "target_ref": f"{owner}/{repo}",
                            "dedupe_key": dedupe_key,
                            "payload_json": json.dumps(
                                {
                                    "owner": owner,
                                    "repo": repo,
                                    "pull_number": pull_number,
                                    "reviewer": lookup_name,
                                    "head_sha": head_sha,
                                },
                                ensure_ascii=False,
                            ),
                            "metadata_json": self._build_poll_metadata(rule),
                        },
                        session=session,
                    )
                    self._mark_seen(rule_key, dedupe_key)
                except Exception as exc:
                    logger.warning("GitHub review ingest failed for %s#%s: %s", repo_ref, pull_number, exc)

    async def _poll_github_mentions(self, rule: AutomationRule, *, session: ClientSession | None = None) -> None:
        rule_key = self._rule_key(rule)
        repos = self._normalize_list(rule.scope.get("repos"))
        if not repos:
            return
        last_check = self._last_check_by_rule.get(rule_key)
        for repo_ref in repos:
            if "/" not in repo_ref:
                continue
            owner, repo = repo_ref.split("/", 1)
            try:
                comments = await github_channel.get_recent_issue_comments(repo_ref, since=last_check)
            except Exception as exc:
                logger.warning("GitHub mention polling failed for %s: %s", repo_ref, exc)
                continue
            for comment in comments:
                comment_id = str(comment.get("id") or "").strip()
                if not comment_id:
                    continue
                mentions = self._extract_mentions(str(comment.get("body") or ""))
                if not self._mention_matches_rule(rule, mentions):
                    continue
                issue_number = comment.get("issue_number")
                url = str(comment.get("url") or "")
                if (not issue_number) and url:
                    match = re.search(r"/issues/(\d+)", url)
                    if match:
                        issue_number = int(match.group(1))
                dedupe_key = f"github:mention:{owner}/{repo}:{comment_id}"
                if self._is_seen(rule_key, dedupe_key):
                    continue
                await self._ingest_event(
                    {
                        "source_type": "github",
                        "event_type": "mention",
                        "external_account_id": rule.external_account_id,
                        "target_ref": f"{owner}/{repo}",
                        "dedupe_key": dedupe_key,
                        "payload_json": json.dumps(
                            {
                                "owner": owner,
                                "repo": repo,
                                "comment_id": comment_id,
                                "author": comment.get("author"),
                                "body": comment.get("body"),
                                "url": url,
                                "html_url": comment.get("html_url") or url,
                                "issue_number": issue_number,
                            },
                            ensure_ascii=False,
                        ),
                        "metadata_json": self._build_poll_metadata(rule),
                    },
                    session=session,
                )
                self._mark_seen(rule_key, dedupe_key)

            if not rule.include_review_comments:
                continue

            try:
                pulls_result = await github_channel.search_issues(f"repo:{owner}/{repo} is:pr is:open", max_results=20)
            except Exception as exc:
                logger.warning("GitHub PR scan failed for review comments %s: %s", repo_ref, exc)
                continue
            pulls = pulls_result.get("items") if isinstance(pulls_result, dict) and isinstance(pulls_result.get("items"), list) else []
            for pr_item in pulls:
                pull_number = pr_item.get("number") if isinstance(pr_item, dict) else None
                if not isinstance(pull_number, int):
                    continue
                try:
                    review_comments = await github_channel.get_pr_comments(owner, repo, pull_number)
                except Exception as exc:
                    logger.warning("GitHub review comments fetch failed for %s#%s: %s", repo_ref, pull_number, exc)
                    continue
                if not isinstance(review_comments, list):
                    continue
                for review_comment in review_comments:
                    comment_id = str(review_comment.get("id") or "").strip()
                    if not comment_id:
                        continue
                    body = str(review_comment.get("body") or "")
                    mentions = self._extract_mentions(body)
                    if not self._mention_matches_rule(rule, mentions):
                        continue
                    dedupe_key = f"github:mention:{owner}/{repo}:review_comment:{comment_id}"
                    if self._is_seen(rule_key, dedupe_key):
                        continue
                    await self._ingest_event(
                        {
                            "source_type": "github",
                            "event_type": "mention",
                            "external_account_id": rule.external_account_id,
                            "target_ref": f"{owner}/{repo}",
                            "dedupe_key": dedupe_key,
                            "payload_json": json.dumps(
                                {
                                    "owner": owner,
                                    "repo": repo,
                                    "pull_number": pull_number,
                                    "issue_number": pull_number,
                                    "comment_id": comment_id,
                                    "author": (review_comment.get("user") or {}).get("login"),
                                    "body": body,
                                    "url": review_comment.get("html_url") or review_comment.get("url"),
                                    "html_url": review_comment.get("html_url") or review_comment.get("url"),
                                    "comment_type": "review_comment",
                                },
                                ensure_ascii=False,
                            ),
                            "metadata_json": self._build_poll_metadata(rule),
                        },
                        session=session,
                    )
                    self._mark_seen(rule_key, dedupe_key)

    async def _poll_jira_assigned(self, rule: AutomationRule, *, session: ClientSession | None = None) -> None:
        rule_key = self._rule_key(rule)
        projects = self._normalize_list(rule.scope.get("projects"))
        if not projects:
            return
        quoted = ",".join(projects)
        jql = f'project IN ({quoted}) AND updated >= "-1h" ORDER BY updated DESC'
        try:
            result = await jira_channel.search_issues(jql, max_results=50)
        except Exception as exc:
            logger.warning("Jira assignment polling search failed: %s", exc)
            return
        issues = result.get("issues") if isinstance(result, dict) and isinstance(result.get("issues"), list) else []
        for issue in issues:
            fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
            issue_key = str(issue.get("key") or "").strip()
            if not issue_key:
                continue
            assignee_data = fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}
            assignee_candidates = {
                str(assignee_data.get("accountId") or "").strip().lower(),
                str(assignee_data.get("name") or "").strip().lower(),
                str(assignee_data.get("displayName") or "").strip().lower(),
                str(assignee_data.get("emailAddress") or "").strip().lower(),
            }
            assignee_candidates.discard("")
            binding_candidates = {
                str(rule.external_account_id or "").strip().lower(),
                str(rule.binding_lookup_username or "").strip().lower(),
            }
            binding_candidates.discard("")
            if not assignee_candidates.intersection(binding_candidates):
                continue
            updated = str(fields.get("updated") or issue.get("updated") or "")
            assignee_value = str(assignee_data.get("accountId") or assignee_data.get("name") or assignee_data.get("displayName") or "")
            project_key = str((fields.get("project") or {}).get("key") or (issue_key.split("-", 1)[0] if "-" in issue_key else "")).strip()
            dedupe_key = f"jira:assigned:{issue_key}:{assignee_value}:{updated}"
            if self._is_seen(rule_key, dedupe_key):
                continue
            await self._ingest_event(
                {
                    "source_type": "jira",
                    "event_type": "assigned",
                    "external_account_id": rule.external_account_id,
                    "target_ref": project_key,
                    "dedupe_key": dedupe_key,
                    "payload_json": json.dumps(
                        {
                            "issue_key": issue_key,
                            "project_key": project_key,
                            "summary": fields.get("summary"),
                            "status": (fields.get("status") or {}).get("name"),
                            "assignee": assignee_value,
                            "issue_url": issue.get("self"),
                        },
                        ensure_ascii=False,
                    ),
                    "metadata_json": self._build_poll_metadata(rule),
                },
                session=session,
            )
            self._mark_seen(rule_key, dedupe_key)

    async def _poll_jira_mentions(self, rule: AutomationRule, *, session: ClientSession | None = None) -> None:
        rule_key = self._rule_key(rule)
        projects = self._normalize_list(rule.scope.get("projects"))
        if not projects:
            return
        quoted = ",".join(projects)
        jql = f'project IN ({quoted}) AND updated >= "-1h" ORDER BY updated DESC'
        try:
            result = await jira_channel.search_issues(jql, max_results=50)
        except Exception as exc:
            logger.warning("Jira mention polling search failed: %s", exc)
            return
        issues = result.get("issues") if isinstance(result, dict) and isinstance(result.get("issues"), list) else []
        for issue in issues:
            issue_key = str(issue.get("key") or "").strip()
            if not issue_key:
                continue
            fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
            project_key = str((fields.get("project") or {}).get("key") or (issue_key.split("-", 1)[0] if "-" in issue_key else "")).strip()
            try:
                comments = await jira_channel.get_comments(issue_key)
            except Exception as exc:
                logger.warning("Jira mention comments failed for %s: %s", issue_key, exc)
                continue
            for comment in comments:
                comment_id = str(comment.get("id") or "").strip()
                if not comment_id:
                    continue
                body = str(comment.get("body") or "")
                mentions = self._extract_mentions(body)
                if not self._mention_matches_rule(rule, mentions):
                    continue
                dedupe_key = f"jira:mention:{issue_key}:{comment_id}"
                if self._is_seen(rule_key, dedupe_key):
                    continue
                await self._ingest_event(
                    {
                        "source_type": "jira",
                        "event_type": "mention",
                        "external_account_id": rule.external_account_id,
                        "target_ref": project_key,
                        "dedupe_key": dedupe_key,
                        "payload_json": json.dumps(
                            {
                                "issue_key": issue_key,
                                "project_key": project_key,
                                "comment_id": comment_id,
                                "author": comment.get("author"),
                                "body": body,
                            },
                            ensure_ascii=False,
                        ),
                        "metadata_json": self._build_poll_metadata(rule),
                    },
                    session=session,
                )
                self._mark_seen(rule_key, dedupe_key)

    async def _poll_confluence_mentions(self, rule: AutomationRule, *, session: ClientSession | None = None) -> None:
        rule_key = self._rule_key(rule)
        spaces = self._normalize_list(rule.scope.get("spaces"))
        if not spaces:
            return
        for space in spaces:
            try:
                pages_result = await confluence_channel.search_pages(f'space = "{space}" AND type = page', limit=20)
            except Exception as exc:
                logger.warning("Confluence mention search failed for %s: %s", space, exc)
                continue
            pages = pages_result.get("results") if isinstance(pages_result, dict) and isinstance(pages_result.get("results"), list) else []
            for page in pages:
                page_id = str(page.get("id") or "").strip()
                if not page_id:
                    continue
                space_key = str(((page.get("space") or {}).get("key") or space)).strip()
                try:
                    comments = await confluence_channel.get_comments(page_id)
                except Exception as exc:
                    logger.warning("Confluence comments failed for page=%s: %s", page_id, exc)
                    continue
                for comment in comments:
                    comment_id = str(comment.get("id") or "").strip()
                    if not comment_id:
                        continue
                    body = str(((comment.get("body") or {}).get("storage") or {}).get("value") or comment.get("body") or "")
                    mentions = self._extract_mentions(body)
                    if not self._mention_matches_rule(rule, mentions):
                        continue
                    dedupe_key = f"confluence:mention:{page_id}:{comment_id}"
                    if self._is_seen(rule_key, dedupe_key):
                        continue
                    await self._ingest_event(
                        {
                            "source_type": "confluence",
                            "event_type": "mention",
                            "external_account_id": rule.external_account_id,
                            "target_ref": space_key,
                            "dedupe_key": dedupe_key,
                            "payload_json": json.dumps(
                                {
                                    "space_key": space_key,
                                    "page_id": page_id,
                                    "comment_id": comment_id,
                                    "author": (comment.get("version") or {}).get("by", {}).get("displayName") or comment.get("author"),
                                    "body": body,
                                    "title": page.get("title"),
                                },
                                ensure_ascii=False,
                            ),
                            "metadata_json": self._build_poll_metadata(rule),
                        },
                        session=session,
                    )
                    self._mark_seen(rule_key, dedupe_key)

    async def _poll_rule(self, rule: AutomationRule, *, session: ClientSession | None = None) -> None:
        if rule.source_kind == "github.pull_request_review_requested":
            await self._poll_github_review_requests(rule, session=session)
        elif rule.source_kind == "github.mention":
            await self._poll_github_mentions(rule, session=session)
        elif rule.source_kind == "jira.assigned":
            await self._poll_jira_assigned(rule, session=session)
        elif rule.source_kind == "jira.mention":
            await self._poll_jira_mentions(rule, session=session)
        elif rule.source_kind == "confluence.mention":
            await self._poll_confluence_mentions(rule, session=session)

    async def run_once(self) -> None:
        if not is_portal_internal_configured():
            logger.debug("Automation watchers skipped: Portal internal config incomplete")
            return

        try:
            async with ClientSession(headers=self._headers()) as session:
                agent_id = self._agent_id()
                runtime_config = await self.fetch_runtime_profile_config(agent_id, session=session)
                bindings = await self.fetch_identity_bindings(session=session)
                rules = self.build_automation_rules(runtime_config, bindings, agent_id)
                for rule in rules:
                    try:
                        await self._poll_rule(rule, session=session)
                    except Exception as exc:
                        logger.warning("Automation watcher failed for source_kind=%s binding=%s: %s", rule.source_kind, rule.binding_id, exc)
                    finally:
                        self._last_check_by_rule[self._rule_key(rule)] = datetime.now(timezone.utc)
        except Exception as exc:
            logger.warning("Automation watchers failed to load Portal control-plane exports: %s", exc)

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                logger.debug("Automation watchers already running; start() is a no-op")
                return
            self._running = True
            self._stop_event.clear()
        while self._running:
            await self.run_once()
            if not self._running:
                break
            timeout = get_interval_seconds()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break

    async def stop(self) -> None:
        async with self._lock:
            if not self._running:
                logger.debug("Automation watchers already stopped; stop() is a no-op")
                self._stop_event.set()
                return
            self._running = False
            self._stop_event.set()


_runner = AutomationWatcherManager()


def get_interval_seconds() -> int:
    value = int(config.get("server.subscription_watchers_interval_seconds", 60) or 60)
    return max(15, value)


def is_enabled() -> bool:
    if not bool(config.get("server.subscription_watchers_enabled", True)):
        return False
    return is_portal_internal_configured()


async def start_automation_watchers() -> None:
    await _runner.start()


async def stop_automation_watchers() -> None:
    await _runner.stop()
