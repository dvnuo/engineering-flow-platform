"""Boot-time EFP_PROFILE_CONFIG projection: bootstrap_profile_boot + GET /ready."""

import json
import os

import pytest

import src.config as config_module
from src.config import Config
from src.external_cli import profile_config as profile_config_module
from src.gateway import server as gateway_server


def _write_base_config(path):
    path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  model: gpt-4o\n"
        "proxy:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )


def _payload(config, *, profile_id="rp_boot", revision=7):
    return json.dumps(
        {
            "runtime_profile_id": profile_id,
            "name": "boot-profile",
            "revision": revision,
            "runtime_type": "native",
            "config": config,
        }
    )


@pytest.fixture()
def boot_state_reset(monkeypatch):
    monkeypatch.setattr(
        config_module,
        "_profile_boot_state",
        {"completed": False, "ready": False, "error": None},
    )


def _install_config(tmp_path, monkeypatch, overlay_config, **payload_kwargs):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    if overlay_config is None:
        monkeypatch.delenv("EFP_PROFILE_CONFIG", raising=False)
    else:
        monkeypatch.setenv("EFP_PROFILE_CONFIG", _payload(overlay_config, **payload_kwargs))
    cfg = Config(str(config_path))
    monkeypatch.setattr(config_module, "config", cfg)
    return cfg


def test_bootstrap_exports_tools_config_env_vars_scrubs_env_and_applies_env_sections(
    tmp_path, monkeypatch, boot_state_reset
):
    for key in (
        "EFP_JENKINS_USERNAME",
        "EFP_JENKINS_PASSWORD",
        "JENKINS_USERNAME",
        "JENKINS_PASSWORD",
        "EFP_CONFIG_JSON",
        "JIRA_DEFAULT_INSTANCE",
        "JIRA_INSTANCES_0_BASE_URL",
        "JIRA_INSTANCES_0_AUTH_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    applied = []

    def _record_apply(overlay, **kwargs):
        # The scrub must happen only AFTER the projection snapshotted os.environ.
        assert "EFP_PROFILE_CONFIG" in os.environ
        applied.append((json.loads(json.dumps(overlay)), kwargs))

    monkeypatch.setattr(
        profile_config_module, "apply_runtime_profile_external_config", _record_apply
    )

    cfg = _install_config(
        tmp_path,
        monkeypatch,
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "jira-main",
                        "url": "https://jira.example.test",
                        "username": "bot",
                        "api_token": "jira-token",
                        "api_version": "3",
                    }
                ],
            },
            "jenkins": {"enabled": True, "username": "jenkins-user", "password": "jenkins-password"},
            "github": {"enabled": True, "access_token": "gh-token"},
        },
    )

    assert config_module.bootstrap_profile_boot() is True

    # External CLI projection received the filtered overlay with instructions.
    assert len(applied) == 1
    overlay, kwargs = applied[0]
    assert overlay["github"]["access_token"] == "gh-token"
    assert overlay["jira"]["instances"][0]["api_token"] == "jira-token"
    assert overlay["instruction_texts"]
    assert kwargs["config_path"] == cfg.config_path

    # The full profile blob is scrubbed before any child process can spawn.
    assert "EFP_PROFILE_CONFIG" not in os.environ

    # The bare-name, indexed tools config env vars carry the RootConfig subset.
    assert os.environ["JIRA_DEFAULT_INSTANCE"] == "jira-main"
    assert os.environ["JIRA_INSTANCES_0_NAME"] == "jira-main"
    assert os.environ["JIRA_INSTANCES_0_BASE_URL"] == "https://jira.example.test"
    assert os.environ["JIRA_INSTANCES_0_REST_PATH"] == "/rest/api/3"
    assert os.environ["JIRA_INSTANCES_0_API_VERSION"] == "3"
    assert os.environ["JIRA_INSTANCES_0_AUTH_TYPE"] == "basic_api_key"
    assert os.environ["JIRA_INSTANCES_0_AUTH_USERNAME"] == "bot"
    assert os.environ["JIRA_INSTANCES_0_AUTH_API_KEY"] == "jira-token"
    assert os.environ["JENKINS_ENABLED"] == "true"
    assert os.environ["JENKINS_USERNAME"] == "jenkins-user"
    assert os.environ["JENKINS_PASSWORD"] == "jenkins-password"

    # github is projected via real CLIs, never through the tools config env vars.
    assert not any(key.startswith("GITHUB_") for key in os.environ)

    # The legacy single-blob env var is no longer exported.
    assert "EFP_CONFIG_JSON" not in os.environ

    # apply_jenkins_env ran exactly once at boot.
    assert os.environ["EFP_JENKINS_USERNAME"] == "jenkins-user"
    assert os.environ["EFP_JENKINS_PASSWORD"] == "jenkins-password"

    state = config_module.get_profile_boot_state()
    assert state == {"completed": True, "ready": True, "error": None}
    assert cfg.get_external_config_status()["success"] is True


def test_bootstrap_dev_mode_without_profile_env(tmp_path, monkeypatch, boot_state_reset):
    for key in ("EFP_CONFIG_JSON", "JIRA_INSTANCES_0_BASE_URL", "AWS_DOMAIN"):
        monkeypatch.delenv(key, raising=False)

    def _fail_apply(*_args, **_kwargs):
        raise AssertionError("external CLI projection must not run in dev mode")

    monkeypatch.setattr(
        profile_config_module, "apply_runtime_profile_external_config", _fail_apply
    )

    _install_config(tmp_path, monkeypatch, None)

    assert config_module.bootstrap_profile_boot() is True
    assert "EFP_CONFIG_JSON" not in os.environ
    assert "JIRA_INSTANCES_0_BASE_URL" not in os.environ
    assert config_module.get_profile_boot_state() == {
        "completed": True,
        "ready": True,
        "error": None,
    }


def test_bootstrap_empty_profile_config_is_ready(tmp_path, monkeypatch, boot_state_reset):
    for key in ("EFP_CONFIG_JSON", "JIRA_INSTANCES_0_BASE_URL", "AWS_DOMAIN"):
        monkeypatch.delenv(key, raising=False)
    applied = []
    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: applied.append(overlay),
    )

    _install_config(tmp_path, monkeypatch, {}, profile_id=None, revision=None)

    assert config_module.bootstrap_profile_boot() is True
    assert applied == [{}]
    # An empty profile flattens to zero tools config env vars (not env-managed).
    assert "EFP_CONFIG_JSON" not in os.environ
    assert "JIRA_INSTANCES_0_BASE_URL" not in os.environ
    assert "AWS_DOMAIN" not in os.environ
    assert config_module.get_profile_boot_state()["ready"] is True


def test_bootstrap_projection_failure_keeps_process_alive_but_unready(
    tmp_path, monkeypatch, boot_state_reset
):
    def _fail_apply(_overlay, **_kwargs):
        raise RuntimeError("External CLI command failed: gh auth login with gh-token")

    monkeypatch.setattr(
        profile_config_module, "apply_runtime_profile_external_config", _fail_apply
    )

    cfg = _install_config(
        tmp_path,
        monkeypatch,
        {"github": {"enabled": True, "access_token": "gh-token"}},
    )

    assert config_module.bootstrap_profile_boot() is False

    state = config_module.get_profile_boot_state()
    assert state["completed"] is True
    assert state["ready"] is False
    assert "External CLI command failed" in state["error"]
    assert "gh-token" not in state["error"]

    status = cfg.get_external_config_status()
    assert status["success"] is False
    assert status["operation"] == "apply"

    # The blob is still scrubbed even on failure.
    assert "EFP_PROFILE_CONFIG" not in os.environ


def test_bootstrap_invalid_profile_payload_is_unready(tmp_path, monkeypatch, boot_state_reset):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    monkeypatch.setenv("EFP_PROFILE_CONFIG", "{not json")
    cfg = Config(str(config_path))
    monkeypatch.setattr(config_module, "config", cfg)

    def _fail_apply(*_args, **_kwargs):
        raise AssertionError("projection must not run for an invalid payload")

    monkeypatch.setattr(
        profile_config_module, "apply_runtime_profile_external_config", _fail_apply
    )

    assert config_module.bootstrap_profile_boot() is False
    state = config_module.get_profile_boot_state()
    assert state["ready"] is False
    assert "Invalid EFP_PROFILE_CONFIG" in state["error"]
    assert "EFP_PROFILE_CONFIG" not in os.environ


async def _ready_response():
    response = await gateway_server.Gateway.handle_ready(None, None)
    return response.status, json.loads(response.body)


@pytest.mark.asyncio
async def test_ready_endpoint_gates_on_boot_projection(tmp_path, monkeypatch, boot_state_reset):
    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: None,
    )
    _install_config(
        tmp_path,
        monkeypatch,
        {"github": {"enabled": True, "access_token": "gh-token"}},
        profile_id="rp_ready",
        revision=11,
    )

    # 503 before the boot bootstrap flag is set.
    status, body = await _ready_response()
    assert status == 503
    assert body["ready"] is False
    assert body["error"]

    config_module.bootstrap_profile_boot()

    status, body = await _ready_response()
    assert status == 200
    assert body == {"ready": True, "runtime_profile_id": "rp_ready", "revision": 11}


@pytest.mark.asyncio
async def test_ready_endpoint_dev_mode_reports_null_profile(tmp_path, monkeypatch, boot_state_reset):
    _install_config(tmp_path, monkeypatch, None)
    config_module.bootstrap_profile_boot()

    status, body = await _ready_response()
    assert status == 200
    assert body == {"ready": True, "runtime_profile_id": None, "revision": None}


@pytest.mark.asyncio
async def test_ready_endpoint_reports_projection_failure(tmp_path, monkeypatch, boot_state_reset):
    def _fail_apply(_overlay, **_kwargs):
        raise RuntimeError("External CLI command failed: aws-auth login")

    monkeypatch.setattr(
        profile_config_module, "apply_runtime_profile_external_config", _fail_apply
    )
    _install_config(
        tmp_path,
        monkeypatch,
        {"aws": {"enabled": True, "domain": "D", "username": "u", "password": "p"}},
    )
    config_module.bootstrap_profile_boot()

    status, body = await _ready_response()
    assert status == 503
    assert body["ready"] is False
    assert "External CLI command failed" in body["error"]
