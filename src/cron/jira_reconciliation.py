"""Periodic Jira reconciliation that replays normalized external events into Portal."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from aiohttp import ClientSession

from src.channels.jira import jira_channel
from src.config import config
from src.utils.internal_api_keys import build_portal_internal_api_headers, get_portal_internal_base_url

logger = logging.getLogger(__name__)


class JiraReconciliationRunner:
    def __init__(self) -> None:
        self.running = False

    def _base_url(self) -> str:
        return get_portal_internal_base_url()

    def _headers(self) -> Dict[str, str]:
        return build_portal_internal_api_headers(include_content_type=True)

    async def _get_json(self, path: str) -> Dict[str, Any]:
        url = f"{self._base_url()}{path}"
        async with ClientSession(headers=self._headers()) as session:
            async with session.get(url) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}: {data}")
                return data if isinstance(data, dict) else {"items": data}

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> None:
        url = f"{self._base_url()}{path}"
        async with ClientSession(headers=self._headers()) as session:
            async with session.post(url, json=payload) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"HTTP {response.status}: {body}")

    async def fetch_enabled_workflow_rules(self) -> List[Dict[str, Any]]:
        data = await self._get_json("/api/internal/workflow-transition-rules")
        rules = data.get("items") if isinstance(data.get("items"), list) else data.get("rules")
        if not isinstance(rules, list):
            return []
        return [rule for rule in rules if isinstance(rule, dict) and rule.get("enabled") and str(rule.get("provider_type") or "").lower() == "jira"]

    async def fetch_identity_bindings(self) -> List[Dict[str, Any]]:
        data = await self._get_json("/api/internal/agent-identity-bindings")
        items = data.get("items") if isinstance(data.get("items"), list) else []
        return [item for item in items if isinstance(item, dict)]

    def build_normalized_external_event(self, *, rule: Dict[str, Any], issue: Dict[str, Any], identity_bindings: List[Dict[str, Any]]) -> Dict[str, Any]:
        issue_key = str(issue.get("key") or "").strip()
        issue_fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        status = ""
        status_obj = issue_fields.get("status") if isinstance(issue_fields.get("status"), dict) else {}
        if isinstance(status_obj, dict):
            status = str(status_obj.get("name") or status_obj.get("id") or "").strip()

        return {
            "event_type": "jira.issue.updated",
            "event_key": f"jira:reconciliation:{rule.get('id') or rule.get('rule_id')}:{issue_key}",
            "source_type": "jira",
            "source_ref": issue_key,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "provider": "jira",
                "mode": "reconciliation",
                "workflow_rule_id": rule.get("id") or rule.get("rule_id"),
                "issue": {
                    "key": issue_key,
                    "status": status,
                    "fields": issue_fields,
                },
                "identity_bindings": identity_bindings,
            },
        }

    async def reconcile_once(self) -> None:
        base_url = self._base_url()
        if not base_url:
            logger.debug("Jira reconciliation skipped: portal_internal_base_url is not configured")
            return

        try:
            rules = await self.fetch_enabled_workflow_rules()
            identity_bindings = await self.fetch_identity_bindings()
        except Exception as exc:
            logger.warning("Jira reconciliation failed to load Portal control-plane exports: %s", exc)
            return

        for rule in rules:
            project_keys = []
            trigger_statuses = []
            if isinstance(rule.get("project_keys"), list):
                project_keys = [str(x).strip() for x in rule.get("project_keys") if str(x).strip()]
            elif isinstance(rule.get("project_key"), str) and rule.get("project_key").strip():
                project_keys = [rule.get("project_key").strip()]
            if isinstance(rule.get("trigger_statuses"), list):
                trigger_statuses = [str(x).strip() for x in rule.get("trigger_statuses") if str(x).strip()]
            elif isinstance(rule.get("trigger_status"), str) and rule.get("trigger_status").strip():
                trigger_statuses = [rule.get("trigger_status").strip()]

            if not project_keys:
                continue

            jql = f"project IN ({','.join(project_keys)})"
            if trigger_statuses:
                quoted = ",".join(f'\"{s}\"' for s in trigger_statuses)
                jql = f"{jql} AND status IN ({quoted})"

            try:
                search_result = await jira_channel.search_issues(jql, max_results=50)
            except Exception as exc:
                logger.warning("Jira reconciliation search failed for rule=%s: %s", rule.get("id") or rule.get("rule_id"), exc)
                continue

            issues = search_result.get("issues") if isinstance(search_result, dict) and isinstance(search_result.get("issues"), list) else []
            for issue in issues:
                try:
                    event_payload = self.build_normalized_external_event(rule=rule, issue=issue, identity_bindings=identity_bindings)
                    await self._post_json("/api/internal/external-events/ingest", event_payload)
                except Exception as exc:
                    logger.warning(
                        "Jira reconciliation ingest failed for rule=%s issue=%s: %s",
                        rule.get("id") or rule.get("rule_id"),
                        issue.get("key") if isinstance(issue, dict) else None,
                        exc,
                    )

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
