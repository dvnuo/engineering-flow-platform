"""Portal-driven subscription watchers for external-event ingress."""

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
from src.cron.mention_poller import MentionPoller
from src.utils.portal_internal_api import (
    build_portal_internal_api_headers,
    build_portal_internal_url,
    get_portal_agent_id,
    get_portal_internal_base_url,
    is_portal_internal_configured,
)

logger = logging.getLogger(__name__)

_MAX_DEDUPE_KEYS_PER_SUBSCRIPTION = 1000


@dataclass
class WatchSubscription:
    id: str
    agent_id: str
    source_type: str
    event_type: str
    mode: str
    source_kind: str
    target_ref: Optional[str]
    binding_id: Optional[str]
    enabled: bool
    config: Dict[str, Any]
    scope: Dict[str, Any]
    matcher: Dict[str, Any]
    routing: Dict[str, Any]
    poll_profile: Dict[str, Any]
    raw_config_json: Optional[str] = None
    raw_scope_json: Optional[str] = None
    dedupe_key_template: Optional[str] = None


class SubscriptionWatcherManager:
    def __init__(self) -> None:
        self._running = False
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._last_check_by_subscription: dict[str, datetime] = {}
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
    def _parse_json_field(raw: Any) -> Dict[str, Any]:
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

    @classmethod
    def _normalize_subscription(cls, raw: Dict[str, Any]) -> Optional[WatchSubscription]:
        if not isinstance(raw, dict):
            return None
        source_kind = str(raw.get("source_kind") or "").strip()
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in {"poll", "hybrid"} or not source_kind:
            return None

        config_dict = cls._parse_json_field(raw.get("config"))
        if not config_dict:
            config_dict = cls._parse_json_field(raw.get("config_json"))

        scope_dict = cls._parse_json_field(raw.get("scope"))
        if not scope_dict:
            scope_dict = cls._parse_json_field(raw.get("scope_json"))

        matcher_dict = cls._parse_json_field(raw.get("matcher"))
        if not matcher_dict:
            matcher_dict = cls._parse_json_field(raw.get("matcher_json"))

        routing_dict = cls._parse_json_field(raw.get("routing"))
        if not routing_dict:
            routing_dict = cls._parse_json_field(raw.get("routing_json"))

        poll_profile_dict = cls._parse_json_field(raw.get("poll_profile"))
        if not poll_profile_dict:
            poll_profile_dict = cls._parse_json_field(raw.get("poll_profile_json"))

        return WatchSubscription(
            id=str(raw.get("id") or "").strip(),
            agent_id=str(raw.get("agent_id") or "").strip(),
            source_type=str(raw.get("source_type") or raw.get("provider_type") or "").strip().lower(),
            event_type=str(raw.get("event_type") or "").strip(),
            mode=mode,
            source_kind=source_kind,
            target_ref=(str(raw.get("target_ref") or "").strip() or None),
            binding_id=(str(raw.get("binding_id") or "").strip() or None),
            enabled=bool(raw.get("enabled", True)),
            config=config_dict,
            scope=scope_dict,
            matcher=matcher_dict,
            routing=routing_dict,
            poll_profile=poll_profile_dict,
            raw_config_json=raw.get("config_json") if isinstance(raw.get("config_json"), str) else None,
            raw_scope_json=raw.get("scope_json") if isinstance(raw.get("scope_json"), str) else None,
            dedupe_key_template=(str(raw.get("dedupe_key_template") or "").strip() or None),
        )

    async def fetch_subscriptions(self, *, session: ClientSession | None = None) -> List[WatchSubscription]:
        path = f"/api/internal/external-event-subscriptions?agent_id={self._agent_id()}&enabled=true"
        data = await self._get_json(path, session=session)
        items = data.get("items") if isinstance(data.get("items"), list) else data
        raw_items = items if isinstance(items, list) else []
        normalized: List[WatchSubscription] = []
        for item in raw_items:
            sub = self._normalize_subscription(item) if isinstance(item, dict) else None
            if sub and sub.enabled and sub.id and sub.source_kind:
                normalized.append(sub)
        return normalized

    async def fetch_identity_bindings(self, *, session: ClientSession | None = None) -> List[Dict[str, Any]]:
        data = await self._get_json("/api/internal/agent-identity-bindings?enabled=true", session=session)
        items = data.get("items") if isinstance(data.get("items"), list) else []
        return [item for item in items if isinstance(item, dict)]

    @staticmethod
    def _resolve_binding_scope(raw_scope: Any) -> Dict[str, Any]:
        if isinstance(raw_scope, dict):
            return raw_scope
        if isinstance(raw_scope, str) and raw_scope.strip():
            try:
                parsed = json.loads(raw_scope)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _select_bindings(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        for binding in bindings:
            system_type = str(binding.get("system_type") or binding.get("provider_type") or "").strip().lower()
            agent_id = str(binding.get("agent_id") or "").strip()
            if system_type != subscription.source_type:
                continue
            if agent_id != subscription.agent_id:
                continue
            if subscription.binding_id:
                if str(binding.get("id") or "").strip() != subscription.binding_id:
                    continue
            selected.append(binding)
        return selected

    def _is_seen(self, subscription_id: str, dedupe_key: str) -> bool:
        return dedupe_key in self._seen_event_keys.get(subscription_id, set())

    def _mark_seen(self, subscription_id: str, dedupe_key: str) -> None:
        if subscription_id not in self._seen_event_keys:
            self._seen_event_keys[subscription_id] = set()
            self._seen_event_order[subscription_id] = deque()

        seen_set = self._seen_event_keys[subscription_id]
        order = self._seen_event_order[subscription_id]
        if dedupe_key in seen_set:
            return

        seen_set.add(dedupe_key)
        order.append(dedupe_key)
        while len(order) > _MAX_DEDUPE_KEYS_PER_SUBSCRIPTION:
            old_key = order.popleft()
            seen_set.discard(old_key)

    @staticmethod
    def _normalize_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _resolve_repo_list(self, subscription: WatchSubscription) -> List[str]:
        repos = self._normalize_list(subscription.scope.get("repos"))
        if repos:
            return repos
        repos = self._normalize_list(subscription.config.get("allowed_repos"))
        if repos:
            return repos
        target_ref = str(subscription.target_ref or "").strip()
        if re.match(r"^[^/\s]+/[^/\s]+$", target_ref):
            return [target_ref]
        return []

    def _resolve_projects(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]]) -> List[str]:
        projects = set(self._normalize_list(subscription.scope.get("projects")))
        target_ref = str(subscription.target_ref or "").strip()
        if re.match(r"^[A-Za-z][A-Za-z0-9_\-]+$", target_ref):
            projects.add(target_ref)
        for binding in bindings:
            scope = self._resolve_binding_scope(binding.get("scope") or binding.get("scope_json"))
            for project in self._normalize_list(scope.get("projects")):
                projects.add(project)
        return sorted(projects)

    def _resolve_spaces(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]]) -> List[str]:
        spaces = set(self._normalize_list(subscription.scope.get("spaces")))
        target_ref = str(subscription.target_ref or "").strip()
        if target_ref:
            spaces.add(target_ref)
        for binding in bindings:
            scope = self._resolve_binding_scope(binding.get("scope") or binding.get("scope_json"))
            for space in self._normalize_list(scope.get("spaces")):
                spaces.add(space)
        return sorted([x for x in spaces if x])

    @staticmethod
    def _extract_mentions(text: str) -> set[str]:
        return {m.lower() for m in MentionPoller.extract_mentions(text or "")}

    @staticmethod
    def _binding_external_id(binding: Dict[str, Any]) -> Optional[str]:
        external_id = str(binding.get("external_account_id") or "").strip()
        if external_id:
            return external_id
        username = str(binding.get("username") or "").strip()
        return username or None

    @staticmethod
    def _binding_lookup_name(binding: Dict[str, Any]) -> Optional[str]:
        username = str(binding.get("username") or "").strip()
        if username:
            return username
        external_id = str(binding.get("external_account_id") or "").strip()
        return external_id or None

    @classmethod
    def _iter_binding_reviewers(cls, bindings: List[Dict[str, Any]]) -> List[tuple[Dict[str, Any], str, str]]:
        reviewers: List[tuple[Dict[str, Any], str, str]] = []
        for binding in bindings:
            reviewer_login = cls._binding_lookup_name(binding)
            canonical_external_account_id = cls._binding_external_id(binding)
            if reviewer_login and canonical_external_account_id:
                reviewers.append((binding, reviewer_login, canonical_external_account_id))
        return reviewers

    @classmethod
    def _resolve_matched_binding(
        cls,
        bindings: List[Dict[str, Any]],
        mentions: set[str],
    ) -> Optional[Dict[str, Any]]:
        for binding in bindings:
            username = str(binding.get("username") or "").strip()
            external_id = str(binding.get("external_account_id") or "").strip()
            if username and username.lower() in mentions:
                return binding
            if external_id and external_id.lower() in mentions:
                return binding
        return None

    @staticmethod
    def _build_poll_metadata(
        subscription: WatchSubscription,
        binding: Dict[str, Any] | None = None,
        extra: Dict[str, Any] | None = None,
    ) -> str:
        metadata: Dict[str, Any] = {
            "trigger_mode": "poll",
            "source_kind": subscription.source_kind,
            "subscription_id": subscription.id,
            "subscription_mode": subscription.mode,
            "watcher_agent_id": subscription.agent_id,
            "binding_id": (binding or {}).get("id"),
        }
        if isinstance(extra, dict):
            metadata.update(extra)
        return json.dumps(metadata, ensure_ascii=False)

    async def _poll_github_review_requests(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]], *, session: ClientSession | None = None) -> None:
        repos = self._resolve_repo_list(subscription)
        if not repos:
            logger.warning("Subscription %s has no repos to scan for GitHub review polling", subscription.id)
            return

        for binding, reviewer_login, canonical_external_account_id in self._iter_binding_reviewers(bindings):
            for repo_ref in repos:
                if "/" not in repo_ref:
                    continue
                owner, repo_name = repo_ref.split("/", 1)
                query = f"repo:{owner}/{repo_name} is:pr is:open review-requested:{reviewer_login}"
                try:
                    result = await github_channel.search_issues(query, max_results=50)
                except Exception as exc:
                    logger.warning("GitHub review polling search failed for sub=%s repo=%s reviewer=%s: %s", subscription.id, repo_ref, reviewer_login, exc)
                    continue

                items = result.get("items") if isinstance(result, dict) and isinstance(result.get("items"), list) else []
                for item in items:
                    pull_number = item.get("number") if isinstance(item, dict) else None
                    if not isinstance(pull_number, int):
                        continue
                    try:
                        pr = await github_channel.get_pull_request(owner, repo_name, pull_number)
                        head_sha = str((pr.get("head") or {}).get("sha") or "").strip()
                        if not head_sha:
                            continue
                        dedupe_key = f"github:review:{owner}/{repo_name}:{pull_number}:{reviewer_login}:{head_sha}"
                        if self._is_seen(subscription.id, dedupe_key):
                            continue
                        payload = {
                            "source_type": "github",
                            "event_type": "pull_request_review_requested",
                            "external_account_id": canonical_external_account_id,
                            "target_ref": f"{owner}/{repo_name}",
                            "dedupe_key": dedupe_key,
                            "payload_json": json.dumps({
                                "owner": owner,
                                "repo": repo_name,
                                "pull_number": pull_number,
                                "reviewer": reviewer_login,
                                "head_sha": head_sha,
                            }, ensure_ascii=False),
                            "metadata_json": self._build_poll_metadata(subscription, binding=binding),
                        }
                        await self._post_json("/api/internal/external-events/ingest", payload, session=session)
                        self._mark_seen(subscription.id, dedupe_key)
                    except Exception as exc:
                        logger.warning("GitHub review polling ingest failed for sub=%s repo=%s pr=%s: %s", subscription.id, repo_ref, pull_number, exc)

    @staticmethod
    def _extract_github_comment_context(repo_ref: str, comment: Dict[str, Any]) -> Dict[str, Any]:
        owner, repo = repo_ref.split("/", 1)
        issue_number = comment.get("issue_number")
        extra = comment.get("extra") if isinstance(comment.get("extra"), dict) else {}
        if not issue_number:
            issue_number = extra.get("issue_number")
        url = str(comment.get("url") or "")
        if (not issue_number) and url:
            match = re.search(r"/issues/(\d+)", url)
            if match:
                issue_number = int(match.group(1))
        return {
            "owner": str(extra.get("owner") or owner),
            "repo": str(extra.get("repo") or repo),
            "issue_number": issue_number,
            "url": url,
        }

    async def _poll_github_mentions(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]], *, session: ClientSession | None = None) -> None:
        repos = self._resolve_repo_list(subscription)
        if not repos:
            logger.warning("Subscription %s has no repos to scan for GitHub mentions", subscription.id)
            return

        last_check = self._last_check_by_subscription.get(subscription.id)
        for repo_ref in repos:
            try:
                comments = await github_channel.get_recent_issue_comments(repo_ref, since=last_check)
            except Exception as exc:
                logger.warning("GitHub mention polling failed for sub=%s repo=%s: %s", subscription.id, repo_ref, exc)
                continue
            for comment in comments:
                comment_id = str(comment.get("id") or "").strip()
                if not comment_id:
                    continue
                mentions = self._extract_mentions(str(comment.get("body") or ""))
                matched_binding = self._resolve_matched_binding(bindings, mentions)
                if not matched_binding:
                    continue
                canonical_external_account_id = self._binding_external_id(matched_binding)
                if not canonical_external_account_id:
                    continue
                context = self._extract_github_comment_context(repo_ref, comment)
                target_ref = f"{context['owner']}/{context['repo']}"
                dedupe_key = f"github:mention:{target_ref}:{comment_id}"
                if self._is_seen(subscription.id, dedupe_key):
                    continue
                payload = {
                    "source_type": "github",
                    "event_type": "mention",
                    "external_account_id": canonical_external_account_id,
                    "target_ref": target_ref,
                    "dedupe_key": dedupe_key,
                    "payload_json": json.dumps({
                        "owner": context["owner"],
                        "repo": context["repo"],
                        "comment_id": comment_id,
                        "author": comment.get("author"),
                        "body": comment.get("body"),
                        "url": context["url"],
                        "issue_number": context["issue_number"],
                    }, ensure_ascii=False),
                    "metadata_json": self._build_poll_metadata(subscription, binding=matched_binding),
                }
                try:
                    await self._post_json("/api/internal/external-events/ingest", payload, session=session)
                    self._mark_seen(subscription.id, dedupe_key)
                except Exception as exc:
                    logger.warning("GitHub mention ingest failed for sub=%s repo=%s comment=%s: %s", subscription.id, repo_ref, comment_id, exc)

    async def _poll_jira_mentions(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]], *, session: ClientSession | None = None) -> None:
        projects = self._resolve_projects(subscription, bindings)
        if not projects:
            logger.warning("Subscription %s has no projects to scan for Jira mentions", subscription.id)
            return

        quoted = ",".join(projects)
        jql = f'project IN ({quoted}) AND updated >= "-1h"'
        try:
            result = await jira_channel.search_issues(jql, max_results=50)
        except Exception as exc:
            logger.warning("Jira mention polling search failed for sub=%s: %s", subscription.id, exc)
            return

        issues = result.get("issues") if isinstance(result, dict) and isinstance(result.get("issues"), list) else []
        for issue in issues:
            issue_key = str(issue.get("key") or "").strip()
            if not issue_key:
                continue
            project_key = str(((issue.get("fields") or {}).get("project") or {}).get("key") or "").strip()
            if not project_key:
                project_key = issue_key.split("-", 1)[0] if "-" in issue_key else (projects[0] if projects else "")
            try:
                comments = await jira_channel.get_comments(issue_key)
            except Exception as exc:
                logger.warning("Jira mention polling comments failed for sub=%s issue=%s: %s", subscription.id, issue_key, exc)
                continue

            for comment in comments:
                comment_id = str(comment.get("id") or "").strip()
                if not comment_id:
                    continue
                mentions = self._extract_mentions(str(comment.get("body") or ""))
                matched_binding = self._resolve_matched_binding(bindings, mentions)
                if not matched_binding:
                    continue
                canonical_external_account_id = self._binding_external_id(matched_binding)
                if not canonical_external_account_id:
                    continue
                dedupe_key = f"jira:mention:{issue_key}:{comment_id}"
                if self._is_seen(subscription.id, dedupe_key):
                    continue
                payload = {
                    "source_type": "jira",
                    "event_type": "mention",
                    "external_account_id": canonical_external_account_id,
                    "target_ref": project_key,
                    "dedupe_key": dedupe_key,
                    "payload_json": json.dumps({
                        "issue_key": issue_key,
                        "project_key": project_key,
                        "comment_id": comment_id,
                        "author": comment.get("author"),
                        "body": comment.get("body"),
                    }, ensure_ascii=False),
                    "metadata_json": self._build_poll_metadata(subscription, binding=matched_binding),
                }
                try:
                    await self._post_json("/api/internal/external-events/ingest", payload, session=session)
                    self._mark_seen(subscription.id, dedupe_key)
                except Exception as exc:
                    logger.warning("Jira mention ingest failed for sub=%s issue=%s comment=%s: %s", subscription.id, issue_key, comment_id, exc)

    async def _poll_confluence_mentions(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]], *, session: ClientSession | None = None) -> None:
        spaces = self._resolve_spaces(subscription, bindings)
        if not spaces:
            logger.warning("Subscription %s has no spaces to scan for Confluence mentions", subscription.id)
            return

        for space in spaces:
            try:
                pages_result = await confluence_channel.search_pages(f'space = "{space}" AND type = page', limit=20)
            except Exception as exc:
                logger.warning("Confluence mention polling search failed for sub=%s space=%s: %s", subscription.id, space, exc)
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
                    logger.warning("Confluence mention polling comments failed for sub=%s page=%s: %s", subscription.id, page_id, exc)
                    continue

                for comment in comments:
                    comment_id = str(comment.get("id") or "").strip()
                    if not comment_id:
                        continue
                    body = str(((comment.get("body") or {}).get("storage") or {}).get("value") or comment.get("body") or "")
                    mentions = self._extract_mentions(body)
                    matched_binding = self._resolve_matched_binding(bindings, mentions)
                    if not matched_binding:
                        continue
                    canonical_external_account_id = self._binding_external_id(matched_binding)
                    if not canonical_external_account_id:
                        continue
                    dedupe_key = f"confluence:mention:{page_id}:{comment_id}"
                    if self._is_seen(subscription.id, dedupe_key):
                        continue
                    payload = {
                        "source_type": "confluence",
                        "event_type": "mention",
                        "external_account_id": canonical_external_account_id,
                        "target_ref": space_key,
                        "dedupe_key": dedupe_key,
                        "payload_json": json.dumps({
                            "space_key": space_key,
                            "page_id": page_id,
                            "comment_id": comment_id,
                            "author": (comment.get("version") or {}).get("by", {}).get("displayName") or comment.get("author"),
                            "body": body,
                            "title": page.get("title"),
                        }, ensure_ascii=False),
                        "metadata_json": self._build_poll_metadata(subscription, binding=matched_binding),
                    }
                    try:
                        await self._post_json("/api/internal/external-events/ingest", payload, session=session)
                        self._mark_seen(subscription.id, dedupe_key)
                    except Exception as exc:
                        logger.warning("Confluence mention ingest failed for sub=%s page=%s comment=%s: %s", subscription.id, page_id, comment_id, exc)

    async def _poll_subscription(self, subscription: WatchSubscription, bindings: List[Dict[str, Any]], *, session: ClientSession | None = None) -> None:
        kind = subscription.source_kind
        if kind == "github.pull_request_review_requested":
            await self._poll_github_review_requests(subscription, bindings, session=session)
        elif kind == "github.mention":
            await self._poll_github_mentions(subscription, bindings, session=session)
        elif kind == "jira.mention":
            await self._poll_jira_mentions(subscription, bindings, session=session)
        elif kind == "confluence.mention":
            await self._poll_confluence_mentions(subscription, bindings, session=session)
        else:
            logger.debug("Unsupported source_kind=%s for subscription=%s", kind, subscription.id)

    async def run_once(self) -> None:
        if not is_portal_internal_configured():
            logger.debug("Subscription watchers skipped: Portal internal config incomplete")
            return

        try:
            async with ClientSession(headers=self._headers()) as session:
                subscriptions = await self.fetch_subscriptions(session=session)
                bindings = await self.fetch_identity_bindings(session=session)

                for subscription in subscriptions:
                    sub_bindings = self._select_bindings(subscription, bindings)
                    if not sub_bindings:
                        logger.debug("Skipping subscription=%s due to no matching bindings", subscription.id)
                        continue
                    try:
                        await self._poll_subscription(subscription, sub_bindings, session=session)
                    except Exception as exc:
                        logger.warning("Subscription watcher failed for subscription=%s source_kind=%s: %s", subscription.id, subscription.source_kind, exc)
                    finally:
                        self._last_check_by_subscription[subscription.id] = datetime.now(timezone.utc)
        except Exception as exc:
            logger.warning("Subscription watchers failed to load Portal control-plane exports: %s", exc)

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                logger.debug("Subscription watchers already running; start() is a no-op")
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
                logger.debug("Subscription watchers already stopped; stop() is a no-op")
                self._stop_event.set()
                return
            self._running = False
            self._stop_event.set()


_runner = SubscriptionWatcherManager()


def get_interval_seconds() -> int:
    value = int(config.get("server.subscription_watchers_interval_seconds", 60) or 60)
    return max(15, value)


def is_enabled() -> bool:
    if not bool(config.get("server.subscription_watchers_enabled", True)):
        return False
    return is_portal_internal_configured()


async def start_subscription_watchers() -> None:
    await _runner.start()


async def stop_subscription_watchers() -> None:
    await _runner.stop()
