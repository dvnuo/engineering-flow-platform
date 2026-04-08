import pytest

from src.runtime.adapter_executor import (
    ACTION_ID_TO_EXECUTOR,
    _build_portal_headers,
    execute_adapter_action,
    execute_jira_workflow_action,
    validate_enabled_adapter_actions_have_executors,
)
from src.runtime.leader_delegation_adapter import create_portal_delegation_from_runtime
from src.utils.internal_api_keys import build_portal_internal_api_headers


@pytest.mark.asyncio
async def test_execute_adapter_action_runs_registered_jira_action(monkeypatch):
    async def _fake_get_issue(issue_key):
        return f"Issue {issue_key} details"

    monkeypatch.setattr("src.jira.jira_get_issue", _fake_get_issue)

    result = await execute_adapter_action("adapter:jira:read_issue", {"issue_key": "PROJ-1"})

    assert result["success"] is True
    assert result["action_id"] == "adapter:jira:read_issue"
    assert result["system"] == "jira"
    assert len(result["runtime_events"]) >= 2


@pytest.mark.asyncio
async def test_execute_adapter_action_unknown_action_returns_error():
    result = await execute_adapter_action("adapter:jira:unknown", {"issue_key": "PROJ-1"})

    assert result["success"] is False
    assert "Unsupported adapter action" in result["error"]


@pytest.mark.asyncio
async def test_execute_jira_workflow_action_transition_issue(monkeypatch):
    async def _fake_transition(issue_key, to_status, comment=None):
        return f"{issue_key} transitioned to {to_status}"

    monkeypatch.setattr("src.jira.jira_transition", _fake_transition)

    result = await execute_jira_workflow_action(
        "transition_issue",
        {"issue_key": "PROJ-2", "transition": "Done", "comment": "done"},
    )

    assert result["success"] is True
    assert result["system"] == "jira"
    assert result["action_name"] == "transition_issue"


@pytest.mark.asyncio
async def test_execute_adapter_action_add_comment(monkeypatch):
    async def _fake_add_comment(issue_key, comment):
        return f"Comment added to {issue_key}: {comment}"

    monkeypatch.setattr("src.jira.jira_add_comment", _fake_add_comment)

    result = await execute_adapter_action(
        "adapter:jira:add_comment",
        {"issue_key": "PROJ-4", "comment": "Looks good"},
    )

    assert result["success"] is True
    assert result["action_id"] == "adapter:jira:add_comment"


@pytest.mark.asyncio
async def test_execute_adapter_action_github_review_pull_request(monkeypatch):
    captured = {}

    async def _fake_submit(owner, repo, pull_number, body=None, event="COMMENT", **kwargs):
        captured.update(
            {
                "owner": owner,
                "repo": repo,
                "pull_number": pull_number,
                "body": body,
                "event": event,
                **kwargs,
            }
        )
        return {"id": 99, "state": "submitted"}

    monkeypatch.setattr("src.github.github_submit_pr_review", _fake_submit)

    result = await execute_adapter_action(
        "adapter:github:review_pull_request",
        {"owner": "acme", "repo": "demo", "pull_number": 12, "comment": "Looks good", "review_event": "APPROVE"},
    )

    assert result["success"] is True
    assert result["action_id"] == "adapter:github:review_pull_request"
    assert result["system"] == "github"
    assert captured["owner"] == "acme"
    assert captured["repo"] == "demo"
    assert captured["pull_number"] == 12
    assert captured["event"] == "APPROVE"
    assert result["result"]["review_event"] == "APPROVE"


@pytest.mark.asyncio
async def test_execute_adapter_action_github_review_pull_request_rejects_invalid_event():
    result = await execute_adapter_action(
        "adapter:github:review_pull_request",
        {"owner": "acme", "repo": "demo", "pull_number": 12, "review_event": "BOGUS"},
    )
    assert result["success"] is False
    assert "Invalid review_event" in str(result["error"])


@pytest.mark.asyncio
async def test_execute_adapter_action_github_unsupported_action_failed():
    result = await execute_adapter_action("adapter:github:not_supported", {"owner": "acme"})
    assert result["success"] is False
    assert result["system"] == "github"
    assert "Unsupported adapter action" in result["error"]


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_create_delegation_missing_required_fields(monkeypatch):
    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    result = await execute_adapter_action("adapter:portal:create_delegation", {"group_id": "g-1"})
    assert result["success"] is False
    assert result["system"] == "portal"
    assert "Missing required fields" in result["error"]


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_missing_base_url_mentions_env_and_config(monkeypatch):
    monkeypatch.delenv("PORTAL_INTERNAL_BASE_URL", raising=False)
    monkeypatch.setattr("src.runtime.adapter_executor.get_portal_internal_base_url", lambda: "")

    result = await execute_adapter_action(
        "adapter:portal:create_delegation",
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "agent-1",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "skill",
        },
    )

    assert result["success"] is False
    assert result["system"] == "portal"
    assert "PORTAL_INTERNAL_BASE_URL" in result["error"]
    assert "server.portal_internal_base_url" in result["error"]


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_create_delegation_missing_skill_name_fails(monkeypatch):
    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    result = await execute_adapter_action(
        "adapter:portal:create_delegation",
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "a-1",
            "objective": "x",
            "visibility": "leader_only",
        },
    )
    assert result["success"] is False
    assert "skill_name" in result["error"]


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_create_delegation_normalizes_structured_payload(monkeypatch):
    captured = {}

    async def _fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"success": True, "error": None, "result": {"delegation_id": "d-1"}}

    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    monkeypatch.setenv("PORTAL_INTERNAL_API_KEY", "tok-1")
    monkeypatch.setattr("src.runtime.adapter_executor._post_portal_json", _fake_post)

    result = await execute_adapter_action(
        "adapter:portal:create_delegation",
        {
            "group_id": "g-1",
            "leader_agent_id": "leader-1",
            "assignee_agent_id": "agent-2",
            "objective": "Review",
            "visibility": "leader_only",
            "skill_name": "delegation",
            "scoped_context_payload": {"k": "v"},
            "input_artifacts": [{"artifact_id": "a1"}],
            "expected_output_schema": {"required": ["summary"]},
            "retry_policy": {"max_retries": 2},
            "skill_kwargs": {"x": 1},
        },
    )
    assert result["success"] is True
    assert result["system"] == "portal"
    assert captured["url"] == "https://portal.internal/api/internal/agent-delegations"
    assert captured["headers"]["X-Internal-Api-Key"] == "tok-1"
    assert "X-Portal-Internal-Api-Key" not in captured["headers"]
    assert isinstance(captured["payload"]["scoped_context_payload_json"], str)
    assert isinstance(captured["payload"]["input_artifacts_json"], str)
    assert isinstance(captured["payload"]["expected_output_schema_json"], str)
    assert isinstance(captured["payload"]["retry_policy_json"], str)
    assert isinstance(captured["payload"]["skill_kwargs_json"], str)


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_read_actions_use_get(monkeypatch):
    captured = []

    async def _fake_get(url, headers):
        captured.append((url, headers))
        return {"success": True, "error": None, "result": {"items": []}}

    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    monkeypatch.setenv("PORTAL_INTERNAL_API_KEY", "k-1")
    monkeypatch.setattr("src.runtime.adapter_executor._get_portal_json", _fake_get)

    result_a = await execute_adapter_action("adapter:portal:list_group_delegations", {"group_id": "group-1"})
    result_b = await execute_adapter_action("adapter:portal:get_group_task_board", {"group_id": "group-1"})
    result_c = await execute_adapter_action("adapter:portal:list_group_coordination_runs", {"group_id": "group-1"})
    result_d = await execute_adapter_action("adapter:portal:get_coordination_run", {"coordination_run_id": "coord-1"})
    result_e = await execute_adapter_action("adapter:portal:get_specialist_pool", {"group_id": "group-1"})

    assert result_a["success"] is True
    assert result_b["success"] is True
    assert result_c["success"] is True
    assert result_d["success"] is True
    assert result_e["success"] is True
    assert captured[0][0] == "https://portal.internal/api/internal/agent-groups/group-1/delegations"
    assert captured[1][0] == "https://portal.internal/api/internal/agent-groups/group-1/task-board"
    assert captured[2][0] == "https://portal.internal/api/internal/agent-groups/group-1/coordination-runs"
    assert captured[3][0] == "https://portal.internal/api/internal/coordination-runs/coord-1"
    assert captured[4][0] == "https://portal.internal/api/internal/agent-groups/group-1/specialist-pool"
    assert captured[0][1]["X-Internal-Api-Key"] == "k-1"


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_create_task_agent_uses_post_and_normalizes_json(monkeypatch):
    captured = {}

    async def _fake_post(url, payload, headers):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"success": True, "error": None, "result": {"agent_id": "ta-1"}}

    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    monkeypatch.setenv("PORTAL_INTERNAL_API_KEY", "k-1")
    monkeypatch.setattr("src.runtime.adapter_executor._post_portal_json", _fake_post)

    result = await execute_adapter_action(
        "adapter:portal:create_task_agent",
        {
            "group_id": "group-1",
            "leader_agent_id": "leader-1",
            "template_agent_id": "tmpl-1",
            "name": "task-agent-1",
            "metadata": {"scope": "x"},
            "tags": ["runtime"],
        },
    )

    assert result["success"] is True
    assert captured["url"] == "https://portal.internal/api/internal/agent-groups/group-1/task-agents"
    assert isinstance(captured["payload"]["metadata"], str)
    assert isinstance(captured["payload"]["tags"], str)
    assert captured["headers"]["X-Internal-Api-Key"] == "k-1"


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_create_task_agent_missing_leader_rejected_before_http(monkeypatch):
    async def _fake_post(_url, _payload, _headers):
        raise AssertionError("HTTP should not be called when required fields are missing")

    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    monkeypatch.setattr("src.runtime.adapter_executor._post_portal_json", _fake_post)
    result = await execute_adapter_action(
        "adapter:portal:create_task_agent",
        {"group_id": "group-1", "template_agent_id": "tmpl-1", "name": "task-agent-1"},
    )
    assert result["success"] is False
    assert "leader_agent_id" in result["error"]


@pytest.mark.asyncio
async def test_execute_adapter_action_portal_delete_task_agent_uses_delete(monkeypatch):
    captured = {}

    async def _fake_delete(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {"success": True, "error": None, "result": {"deleted": True}}

    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.internal")
    monkeypatch.setenv("PORTAL_INTERNAL_API_KEY", "k-1")
    monkeypatch.setattr("src.runtime.adapter_executor._delete_portal_json", _fake_delete)

    result = await execute_adapter_action(
        "adapter:portal:delete_task_agent",
        {"group_id": "group-1", "agent_id": "ta-1"},
    )

    assert result["success"] is True
    assert captured["url"] == "https://portal.internal/api/internal/agent-groups/group-1/task-agents/ta-1"
    assert captured["headers"]["X-Internal-Api-Key"] == "k-1"


def test_build_portal_headers_uses_config_fallback_api_key(monkeypatch):
    monkeypatch.delenv("PORTAL_INTERNAL_API_KEY", raising=False)
    monkeypatch.delenv("PORTAL_INTERNAL_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "src.utils.internal_api_keys.global_config.get",
        lambda key, default=None: "cfg-key" if key == "server.portal_internal_api_key" else default,
    )

    headers = _build_portal_headers()

    assert headers["Content-Type"] == "application/json"
    assert headers["X-Internal-Api-Key"] == "cfg-key"
    assert "Authorization" not in headers


def test_build_portal_headers_keeps_auth_token_and_config_fallback_api_key(monkeypatch):
    monkeypatch.delenv("PORTAL_INTERNAL_API_KEY", raising=False)
    monkeypatch.setenv("PORTAL_INTERNAL_AUTH_TOKEN", "legacy-token")
    monkeypatch.setattr(
        "src.utils.internal_api_keys.global_config.get",
        lambda key, default=None: "cfg-key-2" if key == "server.portal_internal_api_key" else default,
    )

    headers = _build_portal_headers()

    assert headers["Authorization"] == "Bearer legacy-token"
    assert headers["X-Internal-Api-Key"] == "cfg-key-2"


@pytest.mark.asyncio
async def test_execute_portal_action_uses_config_fallback_base_url(monkeypatch):
    captured = {}

    async def _fake_get(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {"success": True, "error": None, "result": {"items": []}}

    monkeypatch.delenv("PORTAL_INTERNAL_BASE_URL", raising=False)
    monkeypatch.setattr(
        "src.utils.internal_api_keys.global_config.get",
        lambda key, default=None: (
            "https://portal.cfg"
            if key == "server.portal_internal_base_url"
            else "cfg-key"
            if key == "server.portal_internal_api_key"
            else default
        ),
    )
    monkeypatch.setattr("src.runtime.adapter_executor._get_portal_json", _fake_get)

    result = await execute_adapter_action("adapter:portal:list_group_delegations", {"group_id": "group-1"})
    assert result["success"] is True
    assert captured["url"].startswith("https://portal.cfg/")


@pytest.mark.asyncio
async def test_execute_portal_action_base_url_env_precedence_over_config(monkeypatch):
    captured = {}

    async def _fake_get(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return {"success": True, "error": None, "result": {"items": []}}

    monkeypatch.setenv("PORTAL_INTERNAL_BASE_URL", "https://portal.env")
    monkeypatch.setattr(
        "src.utils.internal_api_keys.global_config.get",
        lambda key, default=None: (
            "https://portal.cfg"
            if key == "server.portal_internal_base_url"
            else "cfg-key"
            if key == "server.portal_internal_api_key"
            else default
        ),
    )
    monkeypatch.setattr("src.runtime.adapter_executor._get_portal_json", _fake_get)

    result = await execute_adapter_action("adapter:portal:get_group_task_board", {"group_id": "group-1"})
    assert result["success"] is True
    assert captured["url"].startswith("https://portal.env/")


def test_build_portal_internal_api_headers_auth_token_config_fallback(monkeypatch):
    monkeypatch.delenv("PORTAL_INTERNAL_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        "src.utils.internal_api_keys.global_config.get",
        lambda key, default=None: "tok-cfg" if key == "server.portal_internal_auth_token" else default,
    )

    headers = build_portal_internal_api_headers()

    assert headers["Authorization"] == "Bearer tok-cfg"


def test_build_portal_internal_api_headers_auth_token_env_precedence(monkeypatch):
    monkeypatch.setenv("PORTAL_INTERNAL_AUTH_TOKEN", "tok-env")
    monkeypatch.setattr(
        "src.utils.internal_api_keys.global_config.get",
        lambda key, default=None: "tok-cfg" if key == "server.portal_internal_auth_token" else default,
    )

    headers = build_portal_internal_api_headers()

    assert headers["Authorization"] == "Bearer tok-env"


@pytest.mark.asyncio
async def test_create_portal_delegation_from_runtime_normalizes_result(monkeypatch):
    async def _fake_execute_adapter_action(action_id, kwargs):
        assert action_id == "adapter:portal:create_delegation"
        return {"success": True, "result": {"delegation_id": "d-100"}, "error": None}

    monkeypatch.setattr("src.runtime.leader_delegation_adapter.execute_adapter_action", _fake_execute_adapter_action)
    result = await create_portal_delegation_from_runtime(
        {
            "group_id": "g-1",
            "leader_agent_id": "l-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
            "visibility": "leader_only",
        }
    )
    assert result["success"] is True
    assert result["delegation_id"] == "d-100"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_create_portal_delegation_from_runtime_missing_fields_error():
    result = await create_portal_delegation_from_runtime({"group_id": "g-1"})
    assert result["success"] is False
    assert result["delegation_id"] is None
    assert "Missing required fields" in result["error"]


@pytest.mark.asyncio
async def test_create_portal_delegation_from_runtime_routes_through_bus_helper(monkeypatch):
    calls = []

    async def _fake_bus_helper(action_id, kwargs, **_meta):
        calls.append((action_id, dict(kwargs)))
        return {"success": True, "result": {"delegation_id": "d-bus"}, "error": None}

    async def _fail_direct(*_args, **_kwargs):
        raise AssertionError("direct adapter executor path should not be used")

    monkeypatch.setattr("src.runtime.leader_delegation_adapter.execute_adapter_action_via_bus", _fake_bus_helper)
    monkeypatch.setattr("src.runtime.adapter_executor.execute_adapter_action", _fail_direct)
    result = await create_portal_delegation_from_runtime(
        {
            "group_id": "g-1",
            "leader_agent_id": "l-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
        }
    )
    assert result["success"] is True
    assert result["delegation_id"] == "d-bus"
    assert calls and calls[0][0] == "adapter:portal:create_delegation"


def test_enabled_adapter_actions_have_registered_executors():
    missing = validate_enabled_adapter_actions_have_executors()
    assert missing == []


def test_validate_enabled_adapter_actions_missing_executor(monkeypatch):
    monkeypatch.delitem(ACTION_ID_TO_EXECUTOR, "adapter:jira:read_issue", raising=False)
    missing = validate_enabled_adapter_actions_have_executors()
    assert "adapter:jira:read_issue" in missing


@pytest.mark.asyncio
async def test_create_portal_delegation_from_runtime_preserves_block_reason(monkeypatch):
    async def _fake_bus_helper(action_id, kwargs, **_meta):
        return {"success": False, "result": None, "error": "denied_adapter_actions", "reason": "denied_adapter_actions"}

    monkeypatch.setattr("src.runtime.leader_delegation_adapter.execute_adapter_action_via_bus", _fake_bus_helper)
    result = await create_portal_delegation_from_runtime(
        {
            "group_id": "g-1",
            "leader_agent_id": "l-1",
            "assignee_agent_id": "a-1",
            "objective": "Review",
        }
    )
    assert result["success"] is False
    assert result["delegation_id"] is None
    assert result["error"] == "denied_adapter_actions"
