import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from src.config import Config
from src.external_cli import profile_config as profile_config_module


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _CliRecorder:
    def __init__(self, *, record_env=False):
        self.calls = []
        self.git_values = {}
        self.record_env = record_env

    def run(self, args, input=None, text=False, capture_output=False, check=False, env=None):
        args = list(args)
        call = {
            "args": args,
            "input": input,
            "text": text,
            "capture_output": capture_output,
            "check": check,
        }
        if self.record_env:
            call["env"] = dict(env or {})
        self.calls.append(call)
        if args[:4] == ["git", "config", "--global", "--get"]:
            value = self.git_values.get(args[4])
            if value is None:
                return _FakeCompleted(returncode=1)
            return _FakeCompleted(stdout=f"{value}\n")
        if args[:4] == ["git", "config", "--global", "--unset"]:
            self.git_values.pop(args[4], None)
            return _FakeCompleted()
        if args[:3] == ["git", "config", "--global"] and len(args) == 5:
            self.git_values[args[3]] = args[4]
            return _FakeCompleted()
        return _FakeCompleted()


def _command_calls(recorder, prefix):
    return [call for call in recorder.calls if call["args"][: len(prefix)] == prefix]


def _command_indices(recorder, prefix):
    return [index for index, call in enumerate(recorder.calls) if call["args"][: len(prefix)] == prefix]


RUNTIME_OVERLAY_FIELDS = {
    "enabled_tools": ["read"],
    "disabled_tools": ["write"],
    "tool_permissions": {"bash": "ask"},
    "max_iterations": 12,
    "doom_loop_threshold": 4,
    "max_context_parts": 40,
    "max_context_chars": 120000,
    "max_context_tokens": 64000,
    "context_reserve_chars": 2000,
    "context_reserve_tokens": 1000,
    "compaction_auto": False,
    "compaction_prune": True,
    "compaction_tail_turns": 3,
    "compaction_preserve_recent_chars": 3000,
    "compaction_preserve_recent_tokens": 1500,
    "compaction_reserved_chars": 4000,
    "compaction_tool_output_max_chars": 5000,
    "compaction_prune_min_chars": 20000,
    "compaction_prune_protect_chars": 40000,
    "enable_compaction_summarizer": True,
    "enable_context_overflow_retry": False,
    "enable_session_revert_snapshots": False,
    "skill_directories": ["/workspace/.efp/skills"],
    "active_skills": ["review"],
    "command_directories": ["/workspace/.efp/commands"],
    "enable_command_expansion": False,
    "system_prompt_texts": ["system"],
    "system_prompt_paths": ["/workspace/system.md"],
    "include_default_system_prompt": False,
    "include_environment_context": False,
    "max_system_prompt_chars": 10000,
    "include_runtime_reminders": False,
    "instruction_texts": ["instruction"],
    "instruction_paths": ["/workspace/instructions.md"],
    "include_default_instructions": False,
    "attach_read_instructions": False,
    "max_instruction_chars": 9000,
    "include_skill_sidecar_content": True,
    "max_skill_sidecar_chars": 8000,
    "max_command_chars": 7000,
    "resolve_prompt_references": False,
    "max_prompt_reference_chars": 6000,
    "max_prompt_directory_entries": 50,
    "runtime_mode": "plan",
    "enable_plan_tool": True,
    "plan_mode_read_only": False,
    "enable_question_tool": True,
    "enable_lsp_tool": True,
    "inject_background_task_results": False,
    "model_aware_tool_selection": False,
    "structured_output_schema": {"type": "object", "properties": {}},
    "tool_output_max_lines": 100,
    "tool_output_max_bytes": 4096,
    "tool_output_truncation_direction": "tail",
    "archive_truncated_tool_outputs": False,
    "tool_output_dir": "/workspace/.efp/tool-output",
    "emit_llm_stream_events": False,
    "track_usage": False,
}


def _write_base_config(path):
    path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  model: gpt-4o\n"
        "  api_base: https://api.local\n"
        "  max_retries: 3\n"
        "jira:\n"
        "  enabled: false\n"
        "proxy:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )


def _read_yaml(path):
    return YAML().load(path.read_text(encoding="utf-8")) or {}


def test_runtime_profile_apply_writes_config_yaml_without_sidecar(tmp_path):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    updated = cfg.set_managed_overlay(
        "rp_1",
        3,
        {
            "llm": {"provider": "anthropic"},
            "jira": {"enabled": True},
            "unknown": {"x": 1},
        },
    )
    assert updated == ["jira", "llm"]
    assert not runtime_profile_path.exists()

    cfg.load()
    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "anthropic"
    assert effective["llm"].get("model") is None
    assert "jira" not in effective
    assert "unknown" not in effective

    meta = cfg.get_managed_overlay_meta()
    assert meta["runtime_profile_id"] == "rp_1"
    assert meta["revision"] == 3
    assert meta["managed_sections"] == ["jira", "llm"]


def test_runtime_profile_atlassian_overlay_is_external_only_and_not_portal_persisted(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    applied = []

    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: applied.append(json.loads(json.dumps(overlay))),
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_atlassian",
        1,
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "jira-main",
                        "url": "https://jira.example.test",
                        "username": "bot",
                        "token": "jira-token",
                    }
                ],
            },
            "confluence": {
                "enabled": True,
                "instances": [
                    {
                        "name": "docs",
                        "url": "https://conf.example.test/wiki",
                        "username": "bot",
                        "token": "conf-token",
                    }
                ],
            },
            "aws": {
                "enabled": True,
                "domain": "HBEU",
                "username": "aws-user",
                "password": "aws-password",
            },
            "jenkins": {
                "enabled": True,
                "instances": [
                    {
                        "name": "ci",
                        "url": "https://jenkins.example.test",
                        "username": "jenkins-user",
                        "password": "jenkins-password",
                    }
                ],
            },
        },
    )

    persisted = _read_yaml(config_path)
    assert "jira" not in persisted
    assert "confluence" not in persisted
    assert "jenkins" not in persisted
    assert persisted["aws"] == {
        "enabled": True,
        "domain": "HBEU",
        "username": "aws-user",
        "password": "aws-password",
    }
    assert persisted["instruction_texts"]
    assert "jira-token" not in config_path.read_text(encoding="utf-8")
    assert "conf-token" not in config_path.read_text(encoding="utf-8")
    assert "jenkins-password" not in config_path.read_text(encoding="utf-8")
    assert "aws-password" in config_path.read_text(encoding="utf-8")
    assert applied[0]["jira"]["instances"][0]["token"] == "jira-token"
    assert applied[0]["confluence"]["instances"][0]["token"] == "conf-token"
    assert applied[0]["aws"]["domain"] == "HBEU"
    assert applied[0]["aws"]["password"] == "aws-password"
    assert applied[0]["jenkins"]["instances"][0]["password"] == "jenkins-password"
    assert cfg.get_managed_overlay_meta()["managed_sections"] == [
        "aws",
        "confluence",
        "instruction_texts",
        "jenkins",
        "jira",
    ]


def test_runtime_profile_aws_external_config_runs_aws_auth_tool_and_clears_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/local/bin")
    for key in ("password", "AD_PASS"):
        monkeypatch.delenv(key, raising=False)
    recorder = _CliRecorder(record_env=True)
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    profile_config_module.apply_runtime_profile_external_config(
        {
            "aws": {
                "enabled": True,
                "domain": "HBEU",
                "username": "aws-user",
                "password": "aws-password",
            }
        }
    )

    aws_config = home / ".aws" / "config"
    aws_credentials = home / ".aws" / "credentials"
    assert not aws_config.exists()
    assert not aws_credentials.exists()

    auth_path = home / ".config" / "efp" / "runtime-profile-aws-adfs-auth.json"
    helper_path = home / ".config" / "efp" / "aws-adfs-credential-process.py"
    assert not auth_path.exists()
    assert not helper_path.exists()

    assume_call = next(call for call in recorder.calls if call["args"][0] == "aws-auth")
    assume_args = assume_call["args"]
    assert assume_args == [
        "aws-auth",
        "auth",
        "login",
        "--domain",
        "HBEU",
        "--username",
        "aws-user",
        "--password-stdin",
        "--json",
    ]
    assert assume_call["input"] == "aws-password"
    assert "aws-password" not in " ".join(assume_args)
    assume_env = assume_call["env"]
    assert "AD_PASS" not in assume_env
    assert "password" not in assume_env
    assert "/app/venv/bin" in assume_env["PATH"]

    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata_text = metadata_path.read_text(encoding="utf-8")
    assert "aws-password" not in metadata_text
    metadata = json.loads(metadata_text)
    assert metadata["aws"]["auth_type"] == "aws_auth_cli"

    profile_config_module.clear_runtime_profile_external_config()
    assert not aws_config.exists()
    assert not aws_credentials.exists()
    assert not auth_path.exists()
    assert not helper_path.exists()
    assert not metadata_path.exists()


def test_runtime_profile_aws_auth_failure_redacts_password(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    captured = {}

    def fail_aws_auth(args, input=None, text=False, capture_output=False, check=False, env=None):
        captured["args"] = list(args)
        captured["input"] = input
        captured["env"] = dict(env or {})
        return _FakeCompleted(returncode=1, stderr="login failed for aws-password")

    monkeypatch.setattr(profile_config_module.subprocess, "run", fail_aws_auth)

    with pytest.raises(RuntimeError) as exc:
        profile_config_module.apply_runtime_profile_external_config(
            {
                "aws": {
                    "enabled": True,
                    "domain": "HBEU",
                    "username": "aws-user",
                    "password": "aws-password",
                }
            }
        )

    assert captured["args"] == [
        "aws-auth",
        "auth",
        "login",
        "--domain",
        "HBEU",
        "--username",
        "aws-user",
        "--password-stdin",
        "--json",
    ]
    assert captured["input"] == "aws-password"
    assert "AD_PASS" not in captured["env"]
    assert "aws-password" not in str(exc.value)
    assert "[REDACTED_SECRET]" in str(exc.value)
    assert not (home / ".aws" / "config").exists()
    assert not (home / ".aws" / "credentials").exists()


def test_runtime_profile_apply_prunes_previous_portal_atlassian_entries(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "jira:\n"
        "  enabled: true\n"
        "  instances:\n"
        "    - name: jira-main\n"
        "      url: https://jira.example.test\n"
        "      username: bot\n"
        "      token: jira-token\n"
        "confluence:\n"
        "  enabled: true\n"
        "  instances:\n"
        "    - name: docs\n"
        "      url: https://conf.example.test/wiki\n"
        "      username: bot\n"
        "      token: conf-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: None,
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay("rp_prune", 2, {"llm": {"provider": "anthropic"}})

    persisted = _read_yaml(config_path)
    assert "jira" not in persisted
    assert "confluence" not in persisted
    raw_text = config_path.read_text(encoding="utf-8")
    assert "jira-token" not in raw_text
    assert "conf-token" not in raw_text
    cfg.load()
    effective = cfg.get_effective_config()
    assert "jira" not in effective
    assert "confluence" not in effective


def test_runtime_profile_config_instances_normalize_cli_native_and_drop_blank_urls():
    cfg = Config.__new__(Config)
    cfg._config = {
        "jira": {
            "enabled": True,
            "instances": [
                {
                    "name": "jira-main",
                    "base_url": "https://jira.example.test/",
                    "rest_path": "/rest/api/3",
                },
                {
                    "name": "jira-main",
                    "url": "https://jira.example.test",
                    "username": "duplicate",
                },
                {"name": "name-only"},
                {"name": "jira-uri", "uri": "https://jira-uri.example.test"},
            ],
        },
        "confluence": {
            "enabled": True,
            "instances": [
                {"name": "docs", "baseUrl": "https://conf.example.test/wiki/"},
                {"name": "docs", "base_url": "https://conf.example.test/wiki"},
                {"name": "blank", "base_url": " "},
            ],
        },
    }

    jira_instances = cfg.get_jira_instances()
    assert [item["name"] for item in jira_instances] == ["jira-main", "jira-uri"]
    assert jira_instances[0]["url"] == "https://jira.example.test"
    assert jira_instances[0]["base_url"] == "https://jira.example.test/"
    assert jira_instances[1]["url"] == "https://jira-uri.example.test"

    confluence_instances = cfg.get_confluence_instances()
    assert [item["name"] for item in confluence_instances] == ["docs"]
    assert confluence_instances[0]["url"] == "https://conf.example.test/wiki"


def test_runtime_profile_apply_preserves_and_clears_runtime_top_level_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write("workspace_root: /user/workspace\n")

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_runtime",
        4,
        {
            **RUNTIME_OVERLAY_FIELDS,
            "workspace_root": "/portal/workspace",
            "default_provider_id": "openai",
            "default_model": "gpt-other",
            "compaction_preserve_recent_turns": 10,
            "mcp_servers": {"filesystem": {}},
        },
    )

    cfg.load()
    effective = cfg.get_effective_config()
    for field, expected in RUNTIME_OVERLAY_FIELDS.items():
        assert effective[field] == expected
    assert effective["workspace_root"] == "/user/workspace"
    assert "default_provider_id" not in effective
    assert "default_model" not in effective
    assert "compaction_preserve_recent_turns" not in effective
    assert "mcp_servers" not in effective

    cfg.clear_managed_overlay()
    cfg.load()
    cleared = cfg.get_effective_config()
    for field in RUNTIME_OVERLAY_FIELDS:
        assert field not in cleared
    assert cleared["workspace_root"] == "/user/workspace"


def test_runtime_profile_apply_preserves_unmanaged_llm_subtree(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_tools",
        1,
        {"llm": {"provider": "openai", "model": "gpt-4.1", "tools": ["git_clone", "jira_*"]}},
    )

    cfg.load()
    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "openai"
    assert effective["llm"]["model"] == "gpt-4.1"
    assert effective["llm"]["tools"] == ["git_clone", "jira_*"]
    assert effective["llm"]["api_base"] == "https://api.local"
    assert effective["llm"]["max_retries"] == 3


def test_runtime_profile_apply_filters_unmanaged_nested_fields_from_snapshot(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_filter",
        9,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-5",
                "api_base": "https://should-not-be-written",
            },
            "github": {
                "enabled": True,
                "base_url": "https://github.example/api",
                "unexpected_nested": {"x": 1},
            },
        },
    )

    cfg.load()
    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "openai"
    assert effective["llm"]["model"] == "gpt-5"
    assert effective["llm"]["api_base"] == "https://api.local"
    assert effective["github"]["enabled"] is True
    assert effective["github"]["base_url"] == "https://github.example/api"
    assert "unexpected_nested" not in effective["github"]


def test_runtime_profile_apply_prunes_stale_managed_proxy_fields(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ]:
        monkeypatch.delenv(key, raising=False)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_proxy",
        1,
        {"proxy": {"enabled": True, "url": "http://overlay.proxy.local:8080", "password": "secret"}},
    )
    assert os.environ["http_proxy"] == "http://overlay.proxy.local:8080"
    assert os.environ["all_proxy"] == "http://overlay.proxy.local:8080"
    assert os.environ["ALL_PROXY"] == "http://overlay.proxy.local:8080"

    cfg.set_managed_overlay("rp_proxy", 2, {"llm": {"provider": "openai"}})
    cfg.load()
    assert cfg.proxy.get("enabled") is None
    assert cfg.proxy.get("url") is None
    assert cfg.proxy.get("password") is None
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ]:
        assert key not in os.environ


def test_runtime_profile_apply_allows_proxy_no_proxy_fields(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ]:
        monkeypatch.delenv(key, raising=False)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_proxy_no_proxy",
        1,
        {
            "proxy": {
                "enabled": True,
                "url": "http://overlay.proxy.local:8080",
                "no_proxy": "localhost,.svc",
                "noProxy": "localhost,.camel",
                "unexpected_nested": {"x": 1},
            }
        },
    )

    cfg.load()
    assert cfg.proxy["no_proxy"] == "localhost,.svc"
    assert cfg.proxy["noProxy"] == "localhost,.camel"
    assert "unexpected_nested" not in cfg.proxy


def test_runtime_profile_apply_encrypts_sensitive_fields_in_config_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    monkeypatch.setenv("EFP_CONFIG_KEY", "test-key")

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_3",
        7,
        {"proxy": {"enabled": True, "url": "http://proxy:8080", "password": "secret"}},
    )

    raw_content = config_path.read_text(encoding="utf-8")
    assert "ENC:" in raw_content
    assert "secret" not in raw_content

    cfg.load()
    assert cfg.proxy.get("password") == "secret"


def test_runtime_profile_clear_removes_managed_subtree_and_metadata(tmp_path):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)
    runtime_profile_path.write_text("legacy: true\n", encoding="utf-8")

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    cfg.set_managed_overlay("rp_2", 1, {"jira": {"enabled": True}, "proxy": {"enabled": True, "url": "http://x"}})
    cfg.clear_managed_overlay()

    cfg.load()
    meta = cfg.get_managed_overlay_meta()
    assert meta == {"runtime_profile_id": None, "revision": None, "managed_sections": []}
    assert cfg.jira.get("enabled") is None
    assert "proxy" not in cfg.get_effective_config()
    assert not runtime_profile_path.exists()


def test_set_managed_overlay_external_cli_failure_is_non_fatal(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ]:
        monkeypatch.delenv(key, raising=False)
    token = "gh-secret-token"
    proxy_password = "proxy-url-secret"

    def _fail_external_config(_overlay, **_):
        raise RuntimeError(f"External CLI command failed: gh auth login stderr: {token} proxy {proxy_password}")

    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        _fail_external_config,
    )
    caplog.set_level("WARNING")

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    updated = cfg.set_managed_overlay(
        "rp_external_fail",
        5,
        {
            "github": {
                "enabled": True,
                "access_token": token,
            },
            "proxy": {
                "enabled": True,
                "url": f"http://proxy-user:{proxy_password}@proxy.example.test:8080",
            },
        },
    )

    assert updated == ["github", "instruction_texts", "proxy"]
    cfg.load()
    assert cfg.get_effective_config()["github"]["access_token"] == token
    assert cfg.get_managed_overlay_meta() == {
        "runtime_profile_id": "rp_external_fail",
        "revision": 5,
        "managed_sections": ["github", "instruction_texts", "proxy"],
    }
    status = cfg.get_external_config_status()
    assert status["operation"] == "apply"
    assert status["success"] is False
    assert "External CLI command failed" in status["error"]
    assert token not in status["error"]
    assert proxy_password not in status["error"]
    assert "[REDACTED_SECRET]" in status["error"]
    assert token not in caplog.text
    assert proxy_password not in caplog.text
    assert "Runtime profile external CLI config apply failed" in caplog.text
    assert not runtime_profile_path.exists()


def test_clear_managed_overlay_external_cli_failure_is_non_fatal(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)

    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda _overlay, **_: None,
    )
    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    cfg.set_managed_overlay(
        "rp_external_clear_fail",
        6,
        {"jira": {"enabled": True}},
    )

    monkeypatch.setattr(
        profile_config_module,
        "clear_runtime_profile_external_config",
        lambda **_: (_ for _ in ()).throw(RuntimeError("External CLI command failed: jira instance remove")),
    )
    caplog.set_level("WARNING")

    cfg.clear_managed_overlay()

    cfg.load()
    assert cfg.get_managed_overlay_meta() == {"runtime_profile_id": None, "revision": None, "managed_sections": []}
    assert cfg.jira.get("enabled") is None
    assert "proxy" not in cfg.get_effective_config()
    status = cfg.get_external_config_status()
    assert status == {
        "success": False,
        "error": "External CLI command failed: jira instance remove",
        "operation": "clear",
    }
    assert "Runtime profile external CLI config clear failed" in caplog.text
    assert not runtime_profile_path.exists()


def test_runtime_profile_load_removes_legacy_sidecar_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home_efp_dir = tmp_path / ".efp"
    home_efp_dir.mkdir(parents=True, exist_ok=True)

    config_path = home_efp_dir / "config.yaml"
    runtime_profile_path = home_efp_dir / "runtime_profile.yaml"
    _write_base_config(config_path)
    runtime_profile_path.write_text("llm:\n  provider: old\n", encoding="utf-8")

    cfg = Config(str(config_path))

    assert not runtime_profile_path.exists()
    assert cfg.get_effective_config()["llm"]["provider"] == "openai"


def test_runtime_profile_apply_calls_external_clis_and_clear(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ATLASSIAN_CONFIG", raising=False)
    monkeypatch.delenv("GH_CONFIG_DIR", raising=False)
    recorder = _CliRecorder()
    recorder.git_values = {
        "user.name": "Existing User",
        "user.email": "existing@example.test",
    }
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_external",
        1,
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "jira-main",
                        "url": "https://jira.example.test/",
                        "username": "bot",
                        "api_token": "jira-token",
                        "project_key": "ENG",
                        "api_version": "3",
                        "verify_ssl": False,
                    },
                    {"name": "disabled", "url": "https://disabled.example.test", "enabled": False},
                ],
            },
            "confluence": {
                "enabled": True,
                "instances": [
                    {
                        "name": "docs",
                        "url": "https://conf.example.test/",
                        "token": "conf-token",
                        "space_key": "DOCS",
                    }
                ],
            },
            "github": {
                "enabled": True,
                "access_token": "gh-token",
                "api_base_url": "https://github.example.test/api/v3",
            },
            "jenkins": {
                "enabled": True,
                "instances": [
                    {
                        "name": "ci",
                        "url": "https://jenkins.example.test/",
                        "username": "jenkins-user",
                        "password": "jenkins-password",
                    }
                ],
            },
            "git": {"user": {"name": "Runtime Bot", "email": "runtime@example.test"}},
        },
    )

    jira_remove = _command_calls(recorder, ["jira", "--json", "instance", "remove"])
    assert jira_remove == [
        {
            "args": ["jira", "--json", "instance", "remove", "jira-main", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    jira_add = _command_calls(recorder, ["jira", "--json", "instance", "add"])
    assert len(jira_add) == 1
    assert _command_indices(recorder, ["jira", "--json", "instance", "remove"])[0] < _command_indices(
        recorder,
        ["jira", "--json", "instance", "add"],
    )[0]
    assert jira_add[0]["args"] == [
        "jira",
        "--json",
        "instance",
        "add",
        "jira-main",
        "--base-url",
        "https://jira.example.test",
        "--rest-path",
        "/rest/api/3",
        "--api-version",
        "3",
        "--default",
        "--auth-type",
        "basic_api_key",
        "--username",
        "bot",
        "--api-key-stdin",
    ]
    assert jira_add[0]["input"] == "jira-token"

    confluence_remove = _command_calls(recorder, ["confluence", "--json", "instance", "remove"])
    assert confluence_remove == [
        {
            "args": ["confluence", "--json", "instance", "remove", "docs", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    confluence_add = _command_calls(recorder, ["confluence", "--json", "instance", "add"])
    assert len(confluence_add) == 1
    assert _command_indices(recorder, ["confluence", "--json", "instance", "remove"])[0] < _command_indices(
        recorder,
        ["confluence", "--json", "instance", "add"],
    )[0]
    assert confluence_add[0]["args"] == [
        "confluence",
        "--json",
        "instance",
        "add",
        "docs",
        "--base-url",
        "https://conf.example.test",
        "--rest-path",
        "/rest/api",
        "--default",
        "--auth-type",
        "bearer_token",
        "--token-stdin",
    ]
    assert confluence_add[0]["input"] == "conf-token"

    gh_login = _command_calls(recorder, ["gh", "auth", "login"])
    assert gh_login == [
        {
            "args": [
                "gh",
                "auth",
                "login",
                "--hostname",
                "github.example.test",
                "--with-token",
                "--git-protocol",
                "https",
            ],
            "input": "gh-token",
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    assert _command_calls(recorder, ["gh", "auth", "setup-git"]) == [
        {
            "args": ["gh", "auth", "setup-git", "--hostname", "github.example.test"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    jenkins_remove = _command_calls(recorder, ["jenkins", "instance", "remove"])
    assert jenkins_remove == [
        {
            "args": ["jenkins", "instance", "remove", "ci", "--yes", "--json"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    jenkins_add = _command_calls(recorder, ["jenkins", "instance", "add"])
    assert jenkins_add == [
        {
            "args": [
                "jenkins",
                "instance",
                "add",
                "ci",
                "--base-url",
                "https://jenkins.example.test",
                "--username",
                "jenkins-user",
                "--password-stdin",
                "--default",
                "--json",
            ],
            "input": "jenkins-password",
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    assert _command_calls(recorder, ["git", "config", "--global", "user.name"]) == [
        {
            "args": ["git", "config", "--global", "user.name", "Runtime Bot"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    assert _command_calls(recorder, ["git", "config", "--global", "user.email"]) == [
        {
            "args": ["git", "config", "--global", "user.email", "runtime@example.test"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]

    assert not (home / ".config" / "atlassian" / "config.json").exists()
    assert not (home / ".config" / "gh" / "hosts.yml").exists()
    assert not (home / ".config" / "efp" / "runtime-profile.gitconfig").exists()
    assert not (home / ".gitconfig").exists()

    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if os.name != "nt":
        assert metadata_path.stat().st_mode & 0o777 == 0o600
    assert metadata == {
        "version": 2,
        "managed_by": "efp_runtime_profile",
        "jira": {"instances": [{"name": "jira-main"}]},
        "confluence": {"instances": [{"name": "docs"}]},
        "gh": {"hosts": ["github.example.test"]},
        "jenkins": {"instances": [{"name": "ci"}]},
        "git": {
            "managed": {
                "user.name": "Runtime Bot",
                "user.email": "runtime@example.test",
            },
            "previous": {
                "user.name": "Existing User",
                "user.email": "existing@example.test",
            },
        },
    }

    metadata_text = json.dumps(metadata)
    all_argv = json.dumps([call["args"] for call in recorder.calls])
    for secret in ("jira-token", "conf-token", "gh-token", "jenkins-password"):
        assert secret not in all_argv
        assert secret not in metadata_text

    cfg.clear_managed_overlay()
    assert _command_calls(recorder, ["jira", "--json", "instance", "remove"]) == [
        {
            "args": ["jira", "--json", "instance", "remove", "jira-main", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
        {
            "args": ["jira", "--json", "instance", "remove", "jira-main", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
    ]
    assert _command_calls(recorder, ["confluence", "--json", "instance", "remove"]) == [
        {
            "args": ["confluence", "--json", "instance", "remove", "docs", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
        {
            "args": ["confluence", "--json", "instance", "remove", "docs", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
    ]
    assert _command_calls(recorder, ["jenkins", "instance", "remove"]) == [
        {
            "args": ["jenkins", "instance", "remove", "ci", "--yes", "--json"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
        {
            "args": ["jenkins", "instance", "remove", "ci", "--yes", "--json"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
    ]
    assert _command_calls(recorder, ["gh", "auth", "logout"]) == [
        {
            "args": ["gh", "auth", "logout", "--hostname", "github.example.test"],
            "input": "y\n",
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    assert _command_calls(recorder, ["git", "config", "--global", "user.name"])[-1] == {
        "args": ["git", "config", "--global", "user.name", "Existing User"],
        "input": None,
        "text": True,
        "capture_output": True,
        "check": False,
    }
    assert _command_calls(recorder, ["git", "config", "--global", "user.email"])[-1] == {
        "args": ["git", "config", "--global", "user.email", "existing@example.test"],
        "input": None,
        "text": True,
        "capture_output": True,
        "check": False,
    }
    assert not metadata_path.exists()


def test_managed_overlay_external_cli_env_uses_config_path_with_profile_proxy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    for key in [
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
        "no_proxy",
        "NO_PROXY",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EFP_CONFIG", str(tmp_path / "wrong-config.yaml"))
    recorder = _CliRecorder(record_env=True)
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_external_env",
        1,
        {
            "proxy": {
                "enabled": True,
                "url": "http://proxy.example.test:8080",
                "username": "proxy-user",
                "password": "proxy-password",
                "no_proxy": "localhost,.svc",
            },
            "jira": {
                "enabled": True,
                "instances": [{"name": "jira-main", "url": "https://jira.example.test"}],
            },
            "confluence": {
                "enabled": True,
                "instances": [{"name": "docs", "url": "https://conf.example.test/wiki"}],
            },
        },
    )

    expected_proxy = "http://proxy-user:proxy-password@proxy.example.test:8080"
    atlassian_calls = [call for call in recorder.calls if call["args"][0] in {"jira", "confluence"}]
    assert [call["args"][:4] for call in atlassian_calls] == [
        ["jira", "--json", "instance", "remove"],
        ["jira", "--json", "instance", "add"],
        ["confluence", "--json", "instance", "remove"],
        ["confluence", "--json", "instance", "add"],
    ]
    for call in atlassian_calls:
        assert call["env"]["EFP_CONFIG"] == str(config_path)
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            assert call["env"][key] == expected_proxy
        assert call["env"]["no_proxy"] == "localhost,.svc"
        assert call["env"]["NO_PROXY"] == "localhost,.svc"

    recorder.calls.clear()
    monkeypatch.setenv("EFP_CONFIG", str(tmp_path / "wrong-clear-config.yaml"))
    cfg.clear_managed_overlay()

    remove_prefixes = (
        ["jira", "--json", "instance", "remove"],
        ["confluence", "--json", "instance", "remove"],
    )
    remove_calls = [call for call in recorder.calls if call["args"][:4] in remove_prefixes]
    assert [call["args"][0] for call in remove_calls] == ["jira", "confluence"]
    for call in remove_calls:
        assert call["env"]["EFP_CONFIG"] == str(config_path)


def test_runtime_profile_external_cli_reapply_removes_same_name_before_add(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    recorder = _CliRecorder()
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    profile = {
        "jira": {
            "enabled": True,
            "instances": [
                {"name": "jira-main", "uri": "https://jira.example.test", "token": "jira-token"},
                {"name": "jira-empty"},
            ],
        },
        "confluence": {
            "enabled": True,
            "instances": [
                {"name": "docs", "baseUrl": "https://conf.example.test/wiki", "token": "conf-token"},
                {"name": "docs-empty", "url": ""},
            ],
        },
    }

    profile_config_module.apply_runtime_profile_external_config(profile)
    recorder.calls.clear()
    profile_config_module.apply_runtime_profile_external_config(profile)

    jira_remove = _command_calls(recorder, ["jira", "--json", "instance", "remove"])
    jira_add = _command_calls(recorder, ["jira", "--json", "instance", "add"])
    assert [call["args"][4] for call in jira_remove] == ["jira-main", "jira-main"]
    assert len(jira_add) == 1
    assert all(
        index < _command_indices(recorder, ["jira", "--json", "instance", "add"])[0]
        for index in _command_indices(recorder, ["jira", "--json", "instance", "remove"])
    )

    confluence_remove = _command_calls(recorder, ["confluence", "--json", "instance", "remove"])
    confluence_add = _command_calls(recorder, ["confluence", "--json", "instance", "add"])
    assert [call["args"][4] for call in confluence_remove] == ["docs", "docs"]
    assert len(confluence_add) == 1
    assert confluence_add[0]["args"][5:7] == ["--base-url", "https://conf.example.test/wiki"]
    assert all(
        index < _command_indices(recorder, ["confluence", "--json", "instance", "add"])[0]
        for index in _command_indices(recorder, ["confluence", "--json", "instance", "remove"])
    )

    all_argv = json.dumps([call["args"] for call in recorder.calls])
    assert "jira-empty" not in all_argv
    assert "docs-empty" not in all_argv
    metadata = json.loads(
        (home / ".config" / "efp" / "runtime-profile-external-config.json").read_text(encoding="utf-8")
    )
    assert metadata["jira"]["instances"] == [{"name": "jira-main"}]
    assert metadata["confluence"]["instances"] == [{"name": "docs"}]


def test_runtime_profile_external_cli_inherits_docker_proxy_env_without_profile_proxy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HTTPS_PROXY", "http://docker.proxy.local:8443")
    recorder = _CliRecorder(record_env=True)
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    profile_config_module.apply_runtime_profile_external_config(
        {
            "github": {
                "enabled": True,
                "access_token": "gh-token",
                "api_base_url": "https://github.example.test/api/v3",
            }
        }
    )

    gh_login = _command_calls(recorder, ["gh", "auth", "login"])
    assert len(gh_login) == 1
    assert gh_login[0]["env"]["HTTPS_PROXY"] == "http://docker.proxy.local:8443"


def test_runtime_profile_external_cli_uses_profile_proxy_env_and_redacts_metadata(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HTTPS_PROXY", "http://docker.proxy.local:8443")
    recorder = _CliRecorder(record_env=True)
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "version": 2,
                "managed_by": "efp_runtime_profile",
                "gh": {"hosts": ["old.example.test"]},
            }
        ),
        encoding="utf-8",
    )

    proxy_password = "p:a/s?s#%word"
    expected_proxy = "http://user%40name:p%3Aa%2Fs%3Fs%23%25word@proxy.example.test:8080"
    profile_config_module.apply_runtime_profile_external_config(
        {
            "proxy": {
                "enabled": True,
                "url": "http://olduser:oldpass@proxy.example.test:8080",
                "username": "user@name",
                "password": proxy_password,
                "noProxy": "localhost,.internal",
            },
            "github": {
                "enabled": True,
                "access_token": "gh-token",
                "api_base_url": "https://github.example.test/api/v3",
            },
        },
        config_path=config_path,
    )

    gh_logout = _command_calls(recorder, ["gh", "auth", "logout"])
    assert len(gh_logout) == 1
    gh_login = _command_calls(recorder, ["gh", "auth", "login"])
    assert len(gh_login) == 1
    for call in (gh_logout[0], gh_login[0]):
        assert call["env"]["EFP_CONFIG"] == str(config_path)
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            assert call["env"][key] == expected_proxy
        assert call["env"]["no_proxy"] == "localhost,.internal"
        assert call["env"]["NO_PROXY"] == "localhost,.internal"

    metadata_text = metadata_path.read_text(encoding="utf-8")
    command_argv = json.dumps([call["args"] for call in recorder.calls])
    assert proxy_password not in metadata_text
    assert expected_proxy not in metadata_text
    assert proxy_password not in command_argv
    assert expected_proxy not in command_argv


def test_runtime_profile_clear_unsets_git_values_without_previous(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    recorder = _CliRecorder()
    recorder.git_values = {
        "user.name": "Runtime Bot",
        "user.email": "runtime@example.test",
    }
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "version": 2,
                "managed_by": "efp_runtime_profile",
                "git": {
                    "managed": {
                        "user.name": "Runtime Bot",
                        "user.email": "runtime@example.test",
                    },
                    "previous": {
                        "user.name": None,
                        "user.email": None,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    profile_config_module.clear_runtime_profile_external_config()

    assert _command_calls(recorder, ["git", "config", "--global", "--unset"]) == [
        {
            "args": ["git", "config", "--global", "--unset", "user.name"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
        {
            "args": ["git", "config", "--global", "--unset", "user.email"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        },
    ]
    assert not metadata_path.exists()


def test_runtime_profile_atlassian_auth_inference_uses_stdin_flags(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    recorder = _CliRecorder()
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    profile_config_module.apply_runtime_profile_external_config(
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "jira-password",
                        "url": "https://jira-password.example.test",
                        "username": "bot",
                        "password": "jira-password-secret",
                    },
                    {
                        "name": "jira-api-key-only",
                        "url": "https://jira-api-key.example.test",
                        "api_key": "jira-api-key-secret",
                    },
                ],
            }
        }
    )

    calls = _command_calls(recorder, ["jira", "--json", "instance", "add"])
    assert len(calls) == 2
    assert calls[0]["args"][-5:] == [
        "--auth-type",
        "basic_password",
        "--username",
        "bot",
        "--password-stdin",
    ]
    assert calls[0]["input"] == "jira-password-secret"
    assert calls[1]["args"][-3:] == ["--auth-type", "bearer_token", "--token-stdin"]
    assert calls[1]["input"] == "jira-api-key-secret"

    all_argv = json.dumps([call["args"] for call in recorder.calls])
    metadata = (home / ".config" / "efp" / "runtime-profile-external-config.json").read_text(encoding="utf-8")
    for secret in ("jira-password-secret", "jira-api-key-secret"):
        assert secret not in all_argv
        assert secret not in metadata


def test_runtime_profile_clear_legacy_metadata_removes_generated_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    def fail_if_cli_called(*args, **kwargs):
        raise AssertionError("legacy metadata cleanup must not call external CLIs")

    monkeypatch.setattr(profile_config_module.subprocess, "run", fail_if_cli_called)

    atlassian_path = tmp_path / "custom" / "atlassian.json"
    atlassian_text = '{"version":1}\n'
    atlassian_path.parent.mkdir(parents=True)
    atlassian_path.write_text(atlassian_text, encoding="utf-8")

    hosts_path = home / ".config" / "gh" / "hosts.yml"
    hosts_path.parent.mkdir(parents=True)
    with hosts_path.open("w", encoding="utf-8") as handle:
        YAML().dump(
            {
                "old.example.test": {"oauth_token": "old-token", "git_protocol": "https"},
                "keep.example.test": {"oauth_token": "keep-token", "git_protocol": "https"},
            },
            handle,
        )

    generated_git = home / ".config" / "efp" / "runtime-profile.gitconfig"
    generated_git.parent.mkdir(parents=True)
    generated_git.write_text("[user]\n\tname = old\n", encoding="utf-8")
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[core]\n\teditor = vim\n\n"
        "# BEGIN EFP_RUNTIME_PROFILE_GIT_INCLUDE\n"
        "[include]\n"
        f"\tpath = {generated_git}\n"
        "# END EFP_RUNTIME_PROFILE_GIT_INCLUDE\n",
        encoding="utf-8",
    )

    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata_path.write_text(
        json.dumps(
                {
                    "version": 1,
                    "managed_by": "efp_runtime_profile",
                    "atlassian": {
                        "path": str(atlassian_path),
                        "sha256": hashlib.sha256(atlassian_path.read_bytes()).hexdigest(),
                    },
                "gh": {
                    "path": str(hosts_path),
                    "hosts": {
                        "old.example.test": {
                            "token_sha256": hashlib.sha256(b"old-token").hexdigest(),
                        }
                    },
                },
                "git": {
                    "gitconfig_path": str(gitconfig),
                    "generated_path": str(generated_git),
                },
            }
        ),
        encoding="utf-8",
    )

    profile_config_module.clear_runtime_profile_external_config()

    assert not atlassian_path.exists()
    assert not generated_git.exists()
    assert "runtime-profile.gitconfig" not in gitconfig.read_text(encoding="utf-8")
    hosts = YAML().load(hosts_path.read_text(encoding="utf-8"))
    assert "old.example.test" not in hosts
    assert hosts["keep.example.test"]["oauth_token"] == "keep-token"
    assert not metadata_path.exists()


def test_runtime_profile_external_cli_failure_redacts_secret(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    secret = "gh-secret-token"

    def fake_run(args, input=None, text=False, capture_output=False, check=False, env=None):
        assert secret not in json.dumps(list(args))
        assert input == secret
        return _FakeCompleted(
            returncode=2,
            stdout=f"stdout contains {secret}",
            stderr=f"stderr contains {secret}",
        )

    monkeypatch.setattr(profile_config_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        profile_config_module.apply_runtime_profile_external_config(
            {
                "github": {
                    "enabled": True,
                    "access_token": secret,
                    "api_base_url": "https://github.example.test/api/v3",
                }
            }
        )

    error_text = str(exc_info.value)
    assert secret not in error_text
    assert "[REDACTED_SECRET]" in error_text


def test_runtime_profile_external_cli_failure_redacts_profile_proxy_secrets(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    token = "gh-secret-token"
    proxy_password = "p:a/s?s#%word"
    encoded_proxy_password = "p%3Aa%2Fs%3Fs%23%25word"
    expected_proxy = f"http://user:{encoded_proxy_password}@proxy.example.test:8080"

    def fake_run(args, input=None, text=False, capture_output=False, check=False, env=None):
        assert env["HTTPS_PROXY"] == expected_proxy
        return _FakeCompleted(
            returncode=2,
            stdout=f"proxy failed through {expected_proxy}",
            stderr=f"password {proxy_password} encoded {encoded_proxy_password} token {token}",
        )

    monkeypatch.setattr(profile_config_module.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        profile_config_module.apply_runtime_profile_external_config(
            {
                "proxy": {
                    "enabled": True,
                    "url": "http://proxy.example.test:8080",
                    "username": "user",
                    "password": proxy_password,
                },
                "github": {
                    "enabled": True,
                    "access_token": token,
                    "api_base_url": "https://github.example.test/api/v3",
                },
            }
        )

    error_text = str(exc_info.value)
    assert token not in error_text
    assert proxy_password not in error_text
    assert encoded_proxy_password not in error_text
    assert expected_proxy not in error_text
    assert "[REDACTED_SECRET]" in error_text


def test_runtime_profile_external_cli_instructions_are_injected(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    applied = []

    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: applied.append(json.loads(json.dumps(overlay))),
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_instructions",
        1,
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "jira-main",
                        "url": "https://jira.example.test",
                        "token": "jira-token",
                    }
                ],
            }
        },
    )

    cfg.load()
    instructions = cfg.get_effective_config()["instruction_texts"]
    joined = "\n".join(instructions)
    assert "Use bash" in joined
    assert "jira, confluence, gh, aws, jenkins, and git" in joined
    assert "always pass --json" in joined
    assert "commands/schema/help llm" in joined
    assert "--dry-run" in joined
    assert "--yes" in joined
    assert "gh for GitHub issues, pull requests, and api calls" in joined
    assert "jenkins for Jenkins controller operations" in joined
    assert "git for clone, fetch, push, and status" in joined
    assert "Credentials were applied by the runtime profile through the real CLIs" in joined
    assert "auth_failed" in joined
    assert "include_default_system_prompt" not in cfg.get_effective_config()
    assert applied[0]["instruction_texts"] == instructions


def test_runtime_profile_external_cli_instructions_require_real_atlassian_url(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    applied = []

    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: applied.append(json.loads(json.dumps(overlay))),
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_no_instructions",
        1,
        {
            "jira": {
                "enabled": True,
                "instances": [{"name": "jira-main"}],
            },
            "confluence": {
                "enabled": True,
                "instances": [{"name": "docs", "baseUrl": " "}],
            },
        },
    )

    cfg.load()
    effective = cfg.get_effective_config()
    assert "instruction_texts" not in effective
    assert "jira" not in effective
    assert "confluence" not in effective
    assert "instruction_texts" not in applied[0]
    assert cfg.get_managed_overlay_meta()["managed_sections"] == ["confluence", "jira"]


def test_runtime_profile_external_cli_instructions_preserve_portal_texts(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)

    monkeypatch.setattr(
        profile_config_module,
        "apply_runtime_profile_external_config",
        lambda overlay, **_: None,
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_portal_instructions",
        1,
        {
            "instruction_texts": ["Portal supplied instructions."],
            "github": {
                "enabled": True,
                "access_token": "gh-token",
                "api_base_url": "https://github.example.test/api/v3",
            },
        },
    )

    cfg.load()
    assert cfg.get_effective_config()["instruction_texts"] == ["Portal supplied instructions."]
