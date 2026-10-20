"""The PostgreSQL section's shared settings reach the pgsql CLI.

The Portal stores the connector's access policy and the query budget on the
section (PGSQL_CONNECTOR_SETTINGS_PLAN.md); the section is copied verbatim
into the tools config, so the managed field tree has to let the keys through
or the CLI never sees them and falls back to read-only / 30s / 5000.
"""
from __future__ import annotations

from src.config import Config
from src.external_cli import profile_config as profile_config_module


def _env(config: dict) -> dict[str, str]:
    return profile_config_module.flatten_config_to_env(profile_config_module.build_tools_config_json(config))


def test_the_field_tree_lets_the_shared_settings_through():
    tree = Config.PORTAL_MANAGED_FIELD_TREE["pgsql"]
    for key in ("access", "sslmode", "statement_timeout_seconds", "max_rows"):
        assert tree[key] is True, key
    config = Config.__new__(Config)
    filtered = config._filter_by_field_tree(
        {"pgsql": {"enabled": True, "access": "read_write", "sslmode": "verify-full", "statement_timeout_seconds": 12, "max_rows": 40, "junk": 1, "instances": []}},
        Config.PORTAL_MANAGED_FIELD_TREE,
    )
    assert filtered["pgsql"] == {"enabled": True, "access": "read_write", "sslmode": "verify-full", "statement_timeout_seconds": 12, "max_rows": 40, "instances": []}


def test_the_shared_settings_project_as_section_level_variables():
    env = _env({
        "pgsql": {
            "enabled": True,
            "default_instance": "orders-uat",
            "access": "read_write",
            "sslmode": "verify-full",
            "statement_timeout_seconds": 12,
            "max_rows": 40,
            "instances": [
                {"name": "orders-uat", "host": "orders-uat.example.test", "database": "orders", "username": "efp_readonly", "password": "pg-password"},
            ],
        }
    })
    assert env["EFP_PGSQL_ACCESS"] == "read_write"
    assert env["EFP_PGSQL_SSLMODE"] == "verify-full"
    assert env["EFP_PGSQL_STATEMENT_TIMEOUT_SECONDS"] == "12"
    assert env["EFP_PGSQL_MAX_ROWS"] == "40"
    # The rows carry connection fields only; the CLI resolves the rest from
    # the section.
    assert "EFP_PGSQL_INSTANCES_0_SSLMODE" not in env
    assert "EFP_PGSQL_INSTANCES_0_MAX_ROWS" not in env
    # A section without the keys sends none: the CLI's own defaults apply,
    # read-only among them.
    bare = _env({"pgsql": {"enabled": True, "instances": [{"name": "a", "host": "h", "database": "d", "username": "u"}]}})
    assert "EFP_PGSQL_ACCESS" not in bare
