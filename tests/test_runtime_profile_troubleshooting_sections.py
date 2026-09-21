"""Projection of the troubleshooting sections (nexus/splunk/appd/pgsql).

nexus/splunk/appd are multi-instance products like jira: the Portal shape
``{enabled, default_instance, instances[{name,url,username,password|token,...}]}``
becomes the tools ``InstanceConfig`` shape with canonical auth and is flattened
into ``EFP_<PRODUCT>_INSTANCES_<i>_*``. pgsql carries connection fields and is
copied verbatim. The CLI instruction text must teach all four.
"""
from __future__ import annotations

from src import config as config_module
from src.external_cli import profile_config as profile_config_module
from src.runtime_profile_projection import (
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    _has_enabled_external_cli_config,
    _has_enabled_pgsql_config,
    project_canonical_for_runtime,
)


def _env(config: dict) -> dict[str, str]:
    return profile_config_module.flatten_config_to_env(profile_config_module.build_tools_config_json(config))


def test_nexus_instances_project_like_jira_with_empty_rest_path():
    env = _env({
        "nexus": {
            "enabled": True,
            "default_instance": "main",
            "instances": [{"name": "main", "url": "https://nexus.example.test/", "username": "svc-reader", "password": "nexus-password"}],
        }
    })
    assert env["EFP_NEXUS_DEFAULT_INSTANCE"] == "main"
    assert env["EFP_NEXUS_INSTANCES_0_NAME"] == "main"
    assert env["EFP_NEXUS_INSTANCES_0_BASE_URL"] == "https://nexus.example.test"
    assert env["EFP_NEXUS_INSTANCES_0_AUTH_TYPE"] == "basic_password"
    assert env["EFP_NEXUS_INSTANCES_0_AUTH_USERNAME"] == "svc-reader"
    assert env["EFP_NEXUS_INSTANCES_0_AUTH_PASSWORD"] == "nexus-password"
    # The CLI owns its /service/rest/v1 prefix, so no Atlassian-style rest_path.
    assert "EFP_NEXUS_INSTANCES_0_REST_PATH" not in env


def test_splunk_instances_carry_token_and_search_defaults():
    env = _env({
        "splunk": {
            "enabled": True,
            "instances": [
                {
                    "name": "prod",
                    "url": "https://splunk-api.example.test:8089",
                    "token": "splunk-token",
                    "default_index": "app_prod",
                    "default_earliest": "-4h",
                    "max_results": 500,
                }
            ],
        }
    })
    assert env["EFP_SPLUNK_DEFAULT_INSTANCE"] == "prod"
    assert env["EFP_SPLUNK_INSTANCES_0_BASE_URL"] == "https://splunk-api.example.test:8089"
    assert env["EFP_SPLUNK_INSTANCES_0_AUTH_TYPE"] == "bearer_token"
    assert env["EFP_SPLUNK_INSTANCES_0_AUTH_TOKEN"] == "splunk-token"
    assert env["EFP_SPLUNK_INSTANCES_0_DEFAULT_INDEX"] == "app_prod"
    assert env["EFP_SPLUNK_INSTANCES_0_DEFAULT_EARLIEST"] == "-4h"
    assert env["EFP_SPLUNK_INSTANCES_0_MAX_RESULTS"] == "500"


def test_splunk_namespace_reaches_the_cli():
    # Saved searches, macros, lookups and index visibility belong to a Splunk
    # app, so a profile that names one must carry it through or the CLI
    # addresses the global namespace and reports nothing.
    env = _env({
        "splunk": {
            "enabled": True,
            "instances": [
                {
                    "name": "prod",
                    "url": "https://splunk.example.test:8089",
                    "token": "t",
                    "app": "cmb_search",
                    "owner": "user001",
                }
            ],
        }
    })
    assert env["EFP_SPLUNK_INSTANCES_0_APP"] == "cmb_search"
    assert env["EFP_SPLUNK_INSTANCES_0_OWNER"] == "user001"

    # Absent is absent: an instance without an app keeps the global namespace.
    plain = _env({"splunk": {"enabled": True, "instances": [{"name": "prod", "url": "https://s:8089", "token": "t"}]}})
    assert not any(key.endswith("_APP") or key.endswith("_OWNER") for key in plain)


def test_appd_api_client_auth_keeps_account_and_secret_as_api_key():
    env = _env({
        "appd": {
            "enabled": True,
            "instances": [
                {
                    "name": "prod",
                    "url": "https://appd.example.test",
                    "account": "customer1",
                    "auth_type": "api_client",
                    "username": "efp-reader",
                    "token": "client-secret",
                },
                {
                    "name": "basic",
                    "url": "https://appd2.example.test",
                    "account": "customer1",
                    "username": "user@customer1",
                    "password": "pw",
                },
            ],
        }
    })
    assert env["EFP_APPD_INSTANCES_0_ACCOUNT"] == "customer1"
    assert env["EFP_APPD_INSTANCES_0_AUTH_TYPE"] == "api_client"
    assert env["EFP_APPD_INSTANCES_0_AUTH_USERNAME"] == "efp-reader"
    assert env["EFP_APPD_INSTANCES_0_AUTH_API_KEY"] == "client-secret"
    assert "EFP_APPD_INSTANCES_0_AUTH_TOKEN" not in env
    assert env["EFP_APPD_INSTANCES_1_AUTH_TYPE"] == "basic_password"
    assert env["EFP_APPD_INSTANCES_1_AUTH_PASSWORD"] == "pw"


def test_pgsql_section_is_copied_verbatim():
    env = _env({
        "pgsql": {
            "enabled": True,
            "default_instance": "orders-uat",
            "instances": [
                {"name": "orders-uat", "host": "orders-uat.example.test", "port": 5432, "database": "orders", "username": "efp_readonly", "password": "pg-password", "sslmode": "require"},
            ],
        }
    })
    assert env["EFP_PGSQL_ENABLED"] == "true"
    assert env["EFP_PGSQL_DEFAULT_INSTANCE"] == "orders-uat"
    assert env["EFP_PGSQL_INSTANCES_0_HOST"] == "orders-uat.example.test"
    assert env["EFP_PGSQL_INSTANCES_0_PORT"] == "5432"
    assert env["EFP_PGSQL_INSTANCES_0_DATABASE"] == "orders"
    assert env["EFP_PGSQL_INSTANCES_0_USERNAME"] == "efp_readonly"
    assert env["EFP_PGSQL_INSTANCES_0_PASSWORD"] == "pg-password"
    assert env["EFP_PGSQL_INSTANCES_0_SSLMODE"] == "require"


def test_pgsql_query_bounds_reach_the_cli():
    # The Portal stores them as ints; the pgsql CLI reads them as the statement
    # timeout and the row cap, falling back to 30s/5000 when they are absent.
    env = _env({
        "pgsql": {
            "enabled": True,
            "instances": [
                {
                    "name": "orders-uat",
                    "host": "h",
                    "database": "orders",
                    "username": "efp_readonly",
                    "statement_timeout_seconds": 10,
                    "max_rows": 500,
                },
            ],
        }
    })
    assert env["EFP_PGSQL_INSTANCES_0_STATEMENT_TIMEOUT_SECONDS"] == "10"
    assert env["EFP_PGSQL_INSTANCES_0_MAX_ROWS"] == "500"


def test_disabled_sections_and_instances_are_dropped():
    env = _env({
        "nexus": {"enabled": False, "instances": [{"name": "x", "url": "https://x", "token": "t"}]},
        "splunk": {"enabled": True, "instances": [{"name": "off", "url": "https://s", "token": "t", "enabled": False}]},
    })
    assert not any(key.startswith("EFP_NEXUS_") for key in env)
    assert not any(key.startswith("EFP_SPLUNK_") for key in env)


def test_a_disabled_verbatim_section_is_not_projected_at_all():
    # pgsql and aws are copied verbatim rather than rebuilt instance by
    # instance, so the disabled check has to live in the copy: the pgsql node of
    # the tools config has no section-level enabled flag to fall back on, and a
    # section the admin switched off must not put its password in the pod
    # environment for a tool nobody may use.
    env = _env({
        "pgsql": {
            "enabled": False,
            "instances": [{"name": "orders", "host": "h", "database": "o", "username": "u", "password": "pg-password"}],
        },
        "aws": {
            "enabled": False,
            "provider": "adfs-assume",
            "password": "domain-password",
            "accounts": [{"name": "cps-dev", "account_id": "111122223333", "role": "ADFS-ReadOnly"}],
        },
    })
    assert not any(key.startswith("EFP_PGSQL_") for key in env)
    assert not any(key.startswith("EFP_AWS_") for key in env)
    assert "pg-password" not in "".join(env.values())
    assert "domain-password" not in "".join(env.values())


def test_portal_field_tree_and_overlay_sections_accept_the_new_sections():
    for section in ("nexus", "splunk", "appd", "pgsql"):
        assert section in config_module.Config.MANAGED_OVERLAY_SECTIONS
        tree = config_module.Config.PORTAL_MANAGED_FIELD_TREE[section]
        assert tree == {"enabled": True, "instances": True, "default_instance": True}


def test_enablement_predicates():
    assert _has_enabled_pgsql_config({"pgsql": {"enabled": True, "instances": [{"name": "a", "host": "db.example.test"}]}}) is True
    assert _has_enabled_pgsql_config({"pgsql": {"enabled": True, "instances": [{"name": "a", "host": "db", "enabled": False}]}}) is False
    assert _has_enabled_pgsql_config({"pgsql": {"enabled": False, "instances": [{"name": "a", "host": "db"}]}}) is False
    assert _has_enabled_external_cli_config({"nexus": {"enabled": True, "instances": [{"name": "m", "url": "https://n"}]}}) is True
    assert _has_enabled_external_cli_config({"splunk": {"enabled": True, "instances": [{"name": "p", "url": "https://s:8089"}]}}) is True
    assert _has_enabled_external_cli_config({"appd": {"enabled": True, "instances": [{"name": "p", "url": "https://a"}]}}) is True
    assert _has_enabled_external_cli_config({"pgsql": {"enabled": True, "instances": [{"name": "a", "host": "db"}]}}) is True
    assert _has_enabled_external_cli_config({"nexus": {"enabled": True, "instances": []}}) is False


def test_native_projection_mentions_every_troubleshooting_cli():
    projected = project_canonical_for_runtime({"pgsql": {"enabled": True, "instances": [{"name": "a", "host": "db"}]}}, "native")
    assert projected["instruction_texts"] == [RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS]
    text = RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS
    for token in (
        "nexus component search --repository <repo>",
        "splunk search run --query",
        "always give a time range and a count",
        "appd snapshot list --app <app> --duration-mins 60 --errors-only --json",
        "pgsql query --sql",
        # pgsql is not read-only by construction any more: the instruction has
        # to name the real control so the model does not assume a safety net.
        "pgsql exec",
        "decided by the database role and endpoint",
        "For every nexus, splunk, appd, and pgsql command add --json",
    ):
        assert token in text, token
