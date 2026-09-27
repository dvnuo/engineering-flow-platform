"""AWS account-matrix projection: env flattening, provider gating, KUBECONFIG.

The Portal stores the aws node as ``{enabled, provider, domain, username,
password, default_account, default_region, accounts[]}``. The native runtime
copies it verbatim into the tools config and flattens it into the
``EFP_AWS_*`` env vars the ``aws-auth`` CLI decodes, points kubectl at the
managed kubeconfig, and skips the directory-password login for the
assume-role provider.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src import config as config_module
from src.external_cli import profile_config as profile_config_module
from src.runtime_profile_projection import (
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    _has_enabled_aws_config,
    project_canonical_for_runtime,
)


AWS_MATRIX = {
    "enabled": True,
    "provider": "adfs-assume",
    "domain": "HBEU",
    "username": "aws-user",
    "password": "aws-password",
    "default_account": "cps-dev",
    "default_region": "eu-west-1",
    "session_duration_seconds": 3600,
    "accounts": [
        {
            "name": "cps-dev",
            "account_id": "111111111111",
            "role": "ADFS-ReadOnly",
            "regions": ["ap-east-1", "eu-west-1"],
        },
        {
            "name": "dcc-dev",
            "account_id": "222222222222",
            "role": "ADFS-ReadOnly",
            "enabled": False,
        },
    ],
    "eks_clusters": [
        {
            "account": "cps-dev",
            "cluster": "cps-dev-eks",
            "private_endpoint": "https://vpce-0ab.vpce-svc-1.eu-west-1.vpce.amazonaws.com",
        },
        {
            "account": "222222222222",
            "cluster": "dcc-eks",
            "region": "eu-west-1",
            "private_endpoint": "https://vpce-dcc.example.test",
            "tls_server_name": "dcc.internal.example.test",
            "enabled": False,
        },
    ],
}


def test_account_matrix_is_flattened_into_indexed_env_vars():
    root = profile_config_module.build_tools_config_json({"aws": AWS_MATRIX})
    env = profile_config_module.flatten_config_to_env(root)

    assert env["EFP_AWS_ENABLED"] == "true"
    assert env["EFP_AWS_PROVIDER"] == "adfs-assume"
    assert env["EFP_AWS_DEFAULT_ACCOUNT"] == "cps-dev"
    assert env["EFP_AWS_DEFAULT_REGION"] == "eu-west-1"
    assert env["EFP_AWS_SESSION_DURATION_SECONDS"] == "3600"
    assert env["EFP_AWS_ACCOUNTS_0_NAME"] == "cps-dev"
    assert env["EFP_AWS_ACCOUNTS_0_ACCOUNT_ID"] == "111111111111"
    assert env["EFP_AWS_ACCOUNTS_0_ROLE"] == "ADFS-ReadOnly"
    assert env["EFP_AWS_ACCOUNTS_0_REGIONS_0"] == "ap-east-1"
    # The private-endpoint rows ride along verbatim: the aws section is copied
    # whole, and the flattener recurses into a list inside a section the same
    # way it does into accounts[].regions[].
    assert env["EFP_AWS_EKS_CLUSTERS_0_ACCOUNT"] == "cps-dev"
    assert env["EFP_AWS_EKS_CLUSTERS_0_CLUSTER"] == "cps-dev-eks"
    assert env["EFP_AWS_EKS_CLUSTERS_0_PRIVATE_ENDPOINT"] == "https://vpce-0ab.vpce-svc-1.eu-west-1.vpce.amazonaws.com"
    assert "EFP_AWS_EKS_CLUSTERS_0_TLS_SERVER_NAME" not in env
    assert env["EFP_AWS_EKS_CLUSTERS_1_REGION"] == "eu-west-1"
    assert env["EFP_AWS_EKS_CLUSTERS_1_TLS_SERVER_NAME"] == "dcc.internal.example.test"
    assert env["EFP_AWS_EKS_CLUSTERS_1_ENABLED"] == "false"
    assert env["EFP_AWS_ACCOUNTS_0_REGIONS_1"] == "eu-west-1"
    assert env["EFP_AWS_ACCOUNTS_1_NAME"] == "dcc-dev"
    assert env["EFP_AWS_ACCOUNTS_1_ENABLED"] == "false"
    # The verbatim copy keeps the directory password for the ADFS providers.
    assert env["EFP_AWS_PASSWORD"] == "aws-password"


def test_portal_field_tree_accepts_matrix_fields():
    tree = config_module.Config.PORTAL_MANAGED_FIELD_TREE["aws"]
    for field in ("provider", "idp_url", "source_profile", "default_account", "default_region", "session_duration_seconds", "kubeconfig_path", "accounts"):
        assert tree.get(field) is True, field


def test_assume_role_provider_skips_directory_login(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    calls: list[list[str]] = []

    def record(args, **kwargs):
        calls.append(list(args))
        raise AssertionError("no CLI must run for assume-role")

    monkeypatch.setattr(profile_config_module.subprocess, "run", record)

    profile_config_module.apply_runtime_profile_external_config(
        {
            "aws": {
                "enabled": True,
                "provider": "assume-role",
                "source_profile": "base",
                "accounts": [{"name": "cps-dev", "account_id": "111111111111", "role": "ADFS-ReadOnly"}],
            }
        }
    )

    assert calls == []
    assert profile_config_module._build_aws_config({"aws": {"enabled": True, "provider": "assume-role"}}) is None
    # The ADFS providers still store the directory credentials through aws-auth.
    adfs = profile_config_module._build_aws_config({"aws": {"enabled": True, "domain": "HBEU", "username": "u", "password": "p"}})
    assert adfs is not None and adfs["command"][:3] == ["aws-auth", "auth", "login"]


def test_has_enabled_aws_config_honours_provider():
    assert _has_enabled_aws_config({"aws": AWS_MATRIX}) is True
    assert _has_enabled_aws_config({"aws": {"enabled": True, "domain": "HBEU", "username": "u"}}) is False
    assume_role = {"enabled": True, "provider": "assume-role", "accounts": [{"name": "x", "account_id": "111111111111", "role": "R"}]}
    assert _has_enabled_aws_config({"aws": assume_role}) is True
    assert _has_enabled_aws_config({"aws": {"enabled": True, "provider": "assume-role", "accounts": []}}) is False
    disabled_only = {"enabled": True, "provider": "assume-role", "accounts": [{"name": "x", "account_id": "111111111111", "enabled": False}]}
    assert _has_enabled_aws_config({"aws": disabled_only}) is False


def test_native_projection_teaches_aws_auth_and_kubectl_rules():
    projected = project_canonical_for_runtime({"aws": AWS_MATRIX}, "native")
    texts = projected.get("instruction_texts") or []
    assert texts == [RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS]
    text = texts[0]
    for token in (
        "aws-auth account list --json",
        "aws-auth login --account <name> --json",
        "--profile <name>",
        "aws-auth eks kubeconfig --account <name> --cluster <cluster> --json",
        "--context <name>/<cluster>",
        "never apply, delete, edit, patch, scale, rollout, exec, port-forward, or read secrets",
    ):
        assert token in text, token


def _config_with_aws(aws: dict) -> config_module.Config:
    cfg = config_module.Config.__new__(config_module.Config)
    cfg._config = {"aws": aws}
    return cfg


def test_apply_kube_env_points_kubectl_at_managed_kubeconfig(monkeypatch, tmp_path):
    monkeypatch.delenv("KUBECONFIG", raising=False)
    monkeypatch.setattr(config_module.Config, "DEFAULT_KUBECONFIG_PATH", tmp_path / ".efp" / "kube" / "config")

    _config_with_aws({"enabled": False}).apply_kube_env()
    assert "KUBECONFIG" not in os.environ

    _config_with_aws(AWS_MATRIX).apply_kube_env()
    assert os.environ["KUBECONFIG"] == str(tmp_path / ".efp" / "kube" / "config")

    monkeypatch.setenv("KUBECONFIG", "/operator/kubeconfig")
    _config_with_aws(AWS_MATRIX).apply_kube_env()
    assert os.environ["KUBECONFIG"] == "/operator/kubeconfig"

    monkeypatch.delenv("KUBECONFIG", raising=False)
    _config_with_aws({**AWS_MATRIX, "kubeconfig_path": "~/custom/kube"}).apply_kube_env()
    assert os.environ["KUBECONFIG"] == str(Path(os.path.expanduser("~/custom/kube")))


@pytest.mark.parametrize("field", ["provider", "accounts", "default_account", "eks_clusters"])
def test_overlay_filter_keeps_matrix_fields(field):
    overlay = {"aws": {"enabled": True, field: AWS_MATRIX[field]}}
    cfg = config_module.Config.__new__(config_module.Config)
    filtered = cfg._filter_by_field_tree(overlay, config_module.Config.PORTAL_MANAGED_FIELD_TREE)
    assert filtered["aws"][field] == AWS_MATRIX[field]


def test_native_projection_points_at_the_endpoint_probe_when_kubectl_cannot_connect():
    # A cluster behind PrivateLink fails in a way that looks like an outage
    # (no route) or a broken certificate. The model has to be told which
    # command turns that into a diagnosis instead of retrying kubectl.
    text = RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS
    assert "aws-auth eks endpoint --account <name> --cluster <cluster> --json" in text
    assert "private endpoint the AWS connector configures" in text
