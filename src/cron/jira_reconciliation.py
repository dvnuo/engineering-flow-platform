"""Periodic Jira reconciliation that replays normalized external events into Portal."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List

from aiohttp import ClientSession

from src.config import config
from src.external_cli import jira as jira_cli
from src.utils.portal_internal_api import build_portal_internal_api_headers, get_portal_internal_base_url

logger = logging.getLogger(__name__)


class JiraReconciliationRunner:
    def __init__(self) -> None:
        self.running = False

    def _base_url(self) -> str:
        return get_portal_internal_base_url()

    def _headers(self) -> Dict[str, str]:
        return build_portal_internal_api_headers(include_content_type=True)

    @staticmethod
    def _first_non_empty(mapping: Dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = mapping.get(key)
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _normalize_string_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

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

    @classmethod
    def _normalize_workflow_rule(cls, rule: Dict[str, Any]) -> Dict[str, Any] | None:
        if not isinstance(rule, dict):
            return None
        provider_type = str(cls._first_non_empty(rule, "provider_type", "system_type") or "").strip().lower()
        enabled_value = rule["enabled"] if "enabled" in rule else rule.get("is_enabled")
        enabled = bool(enabled_value is True)
        project_keys = cls._normalize_string_list(rule.get("project_keys"))
        if not project_keys:
            project_keys = cls._normalize_string_list(rule.get("project_key"))
        trigger_statuses = cls._normalize_string_list(rule.get("trigger_statuses"))
        if not trigger_statuses:
            trigger_statuses = cls._normalize_string_list(rule.get("trigger_status"))
        return {
            "id": str(cls._first_non_empty(rule, "id", "rule_id") or "").strip() or None,
            "provider_type": provider_type,
            "enabled": enabled,
            "project_keys": project_keys,
            "trigger_statuses": trigger_statuses,
            "assignee_binding": cls._first_non_empty(rule, "assignee_binding"),
            "skill_name": cls._first_non_empty(rule, "skill_name"),
            "success_transition": cls._first_non_empty(rule, "success_transition"),
            "failure_transition": cls._first_non_empty(rule, "failure_transition"),
        }

    async def _get_json(self, path: str, *, session: ClientSession | None = None) -> Dict[str, Any]:
        url = f"{self._base_url()}{path}"
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
        url = f"{self._base_url()}{path}"
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

    async def fetch_enabled_workflow_rules(self, *, session: ClientSession | None = None) -> List[Dict[str, Any]]:
        data = await self._get_json("/api/internal/workflow-transition-rules", session=session)
        rules = self._extract_list_payload(data, "items", "rules")
        normalized_rules: List[Dict[str, Any]] = []
        for rule in rules:
            normalized = self._normalize_workflow_rule(rule) if isinstance(rule, dict) else None
            if not normalized:
                continue
            if normalized["provider_type"] == "jira" and normalized["enabled"] is True:
                normalized_rules.append(normalized)
        return normalized_rules

    async def fetch_identity_bindings(self, *, session: ClientSession | None = None) -> List[Dict[str, Any]]:
        data = await self._get_json("/api/internal/agent-identity-bindings", session=session)
        return self._extract_list_payload(data, "items")

    @staticmethod
    def _extract_issue_assignee(issue_fields: Dict[str, Any]) -> str | None:
        assignee = issue_fields.get("assignee") if isinstance(issue_fields, dict) else None
        if not isinstance(assignee, dict):
            return None
        for key in ("accountId", "name", "emailAddress", "displayName"):
            value = assignee.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def build_external_event_ingress_request(self, *, rule: Dict[str, Any], issue: Dict[str, Any], identity_bindings: List[Dict[str, Any]]) -> Dict[str, Any] | None:
        issue_key = str(issue.get("key") or "").strip()
        issue_fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        if not issue_key:
            return None

        project_key = str(self._first_non_empty(issue_fields.get("project", {}) if isinstance(issue_fields.get("project"), dict) else {}, "key") or "").strip()
        if not project_key:
            project_key = str(self._first_non_empty(rule, "project_key") or "").strip()
        if not project_key and rule.get("project_keys"):
            project_key = str(rule["project_keys"][0]).strip()

        issue_type = str(self._first_non_empty(issue_fields.get("issuetype", {}) if isinstance(issue_fields.get("issuetype"), dict) else {}, "name") or "").strip() or "Task"
        trigger_status = ""
        status_obj = issue_fields.get("status") if isinstance(issue_fields.get("status"), dict) else {}
        if isinstance(status_obj, dict):
            trigger_status = str(status_obj.get("name") or status_obj.get("id") or "").strip()
        if not trigger_status and rule.get("trigger_statuses"):
            trigger_status = str(rule["trigger_statuses"][0]).strip()
        issue_assignee = self._extract_issue_assignee(issue_fields)
        external_account_id = issue_assignee or str(rule.get("assignee_binding") or "").strip() or None
        rule_id = str(rule.get("id") or "").strip() or "unknown-rule"
        if not project_key or not trigger_status:
            return None

        payload_json = {
            "issue_key": issue_key,
            "project_key": project_key,
            "issue_type": issue_type,
            "trigger_status": trigger_status,
            "issue_assignee": issue_assignee,
            "workflow_rule_id": rule_id,
            "mode": "reconciliation",
            "identity_bindings": identity_bindings,
        }
        metadata_json = {
            "provider": "jira",
            "mode": "reconciliation",
            "workflow_rule_id": rule_id,
        }
        return {
            "source_type": "jira",
            "event_type": "workflow_review_requested",
            "external_account_id": external_account_id,
            "target_ref": project_key,
            "dedupe_key": f"jira:reconciliation:{rule_id}:{issue_key}:{trigger_status}",
            "payload_json": json.dumps(payload_json, ensure_ascii=False),
            "metadata_json": json.dumps(metadata_json, ensure_ascii=False),
            "project_key": project_key,
            "issue_type": issue_type,
            "trigger_status": trigger_status,
            "issue_key": issue_key,
            "issue_assignee": issue_assignee,
        }

    async def reconcile_once(self) -> None:
        base_url = self._base_url()
        if not base_url:
            logger.debug("Jira reconciliation skipped: portal_internal_base_url is not configured")
            return

        try:
            async with ClientSession(headers=self._headers()) as session:
                rules = await self.fetch_enabled_workflow_rules(session=session)
                identity_bindings = await self.fetch_identity_bindings(session=session)

                for rule in rules:
                    project_keys = [str(x).strip() for x in (rule.get("project_keys") or []) if str(x).strip()]
                    trigger_statuses = [str(x).strip() for x in (rule.get("trigger_statuses") or []) if str(x).strip()]

                    if not project_keys:
                        continue

                    jql = f"project IN ({','.join(project_keys)})"
                    if trigger_statuses:
                        quoted = ",".join(f'\"{s}\"' for s in trigger_statuses)
                        jql = f"{jql} AND status IN ({quoted})"

                    try:
                        search_result = await jira_cli.search_issues(jql, max_results=50)
                    except Exception as exc:
                        logger.warning("Jira reconciliation search failed for rule=%s: %s", rule.get("id"), exc)
                        continue

                    issues = search_result.get("issues") if isinstance(search_result, dict) and isinstance(search_result.get("issues"), list) else []
                    for issue in issues:
                        try:
                            ingress_payload = self.build_external_event_ingress_request(rule=rule, issue=issue, identity_bindings=identity_bindings)
                            if not ingress_payload:
                                continue
                            await self._post_json("/api/internal/external-events/ingest", ingress_payload, session=session)
                        except Exception as exc:
                            logger.warning(
                                "Jira reconciliation ingest failed for rule=%s issue=%s: %s",
                                rule.get("id"),
                                issue.get("key") if isinstance(issue, dict) else None,
                                exc,
                            )
        except Exception as exc:
            logger.warning("Jira reconciliation failed to load Portal control-plane exports: %s", exc)
            return

    async def start(self) -> None:
        self.running = True
        while self.running:
            await self.reconcile_once()
            await asyncio.sleep(get_interval_seconds())

    async def stop(self) -> None:
        self.running = False


_runner = JiraReconciliationRunner()


def is_enabled() -> bool:
    return bool(config.get("server.jira_reconciliation_enabled", False))


def get_interval_seconds() -> int:
    value = int(config.get("server.jira_reconciliation_interval_seconds", 300) or 300)
    return max(30, value)


async def start_reconciliation() -> None:
    await _runner.start()


async def stop_reconciliation() -> None:
    await _runner.stop()
