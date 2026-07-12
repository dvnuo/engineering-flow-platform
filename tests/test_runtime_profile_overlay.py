import hashlib
import json
import os

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


def _profile_payload(config, *, profile_id="rp_1", revision=3, runtime_type="native", name="profile"):
    return json.dumps(
        {
            "runtime_profile_id": profile_id,
            "name": name,
            "revision": revision,
            "runtime_type": runtime_type,
            "config": config,
        }
    )


def _env_config(tmp_path, monkeypatch, overlay_config=None, **payload_kwargs):
    config_path = tmp_path / "config.yaml"
    if not config_path.exists():
        _write_base_config(config_path)
    if overlay_config is None:
        monkeypatch.delenv("EFP_PROFILE_CONFIG", raising=False)
    else:
        monkeypatch.setenv(
            "EFP_PROFILE_CONFIG", _profile_payload(overlay_config, **payload_kwargs)
        )
    return Config(str(config_path))


# ---------------------------------------------------------------------------
# EFP_PROFILE_CONFIG env overlay: merge, filtering, metadata
# ---------------------------------------------------------------------------


def test_env_overlay_merges_whitelisted_sections_and_keeps_base_read_only(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    original_text = config_path.read_text(encoding="utf-8")

    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            "llm": {"provider": "anthropic"},
            "jira": {"enabled": True},
            "unknown": {"x": 1},
        },
    )

    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "anthropic"
    # Deep merge: unmanaged base llm fields survive the overlay.
    assert effective["llm"]["model"] == "gpt-4o"
    assert effective["llm"]["api_base"] == "https://api.local"
    assert effective["jira"]["enabled"] is True
    assert "unknown" not in effective

    meta = cfg.get_managed_overlay_meta()
    assert meta["runtime_profile_id"] == "rp_1"
    assert meta["revision"] == 3
    assert meta["managed_sections"] == ["jira", "llm"]

    # Base config.yaml is never rewritten.
    assert config_path.read_text(encoding="utf-8") == original_text


def test_env_overlay_absent_env_is_dev_mode(tmp_path, monkeypatch):
    cfg = _env_config(tmp_path, monkeypatch, None)

    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "openai"
    assert cfg.get_managed_overlay_meta() == {
        "runtime_profile_id": None,
        "revision": None,
        "managed_sections": [],
    }
    assert cfg._profile_env_present is False
    assert cfg._profile_load_error is None


def test_env_overlay_empty_config_is_valid_empty_profile(tmp_path, monkeypatch):
    cfg = _env_config(tmp_path, monkeypatch, {}, profile_id=None, revision=None)

    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "openai"
    assert cfg._profile_env_present is True
    assert cfg._profile_load_error is None
    assert cfg.get_managed_overlay_meta() == {
        "runtime_profile_id": None,
        "revision": None,
        "managed_sections": [],
    }


def test_env_overlay_invalid_json_records_load_error_without_crashing(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    monkeypatch.setenv("EFP_PROFILE_CONFIG", "{not json")

    cfg = Config(str(config_path))

    assert cfg._profile_env_present is True
    assert cfg._profile_load_error is not None
    assert "Invalid EFP_PROFILE_CONFIG" in cfg._profile_load_error
    # Base config remains usable.
    assert cfg.get_effective_config()["llm"]["provider"] == "openai"


def test_env_overlay_filters_unmanaged_nested_fields(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            "llm": {
                "provider": "openai",
                "model": "gpt-5",
                "api_base": "https://should-not-be-merged",
            },
            "github": {
                "enabled": True,
                "base_url": "https://github.example/api",
                "unexpected_nested": {"x": 1},
            },
        },
    )

    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "openai"
    assert effective["llm"]["model"] == "gpt-5"
    assert effective["llm"]["api_base"] == "https://api.local"
    assert effective["github"]["enabled"] is True
    assert effective["github"]["base_url"] == "https://github.example/api"
    assert "unexpected_nested" not in effective["github"]


def test_env_overlay_applies_runtime_top_level_fields_and_filters_unknown(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write("workspace_root: /user/workspace\n")

    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            **RUNTIME_OVERLAY_FIELDS,
            "workspace_root": "/portal/workspace",
            "default_provider_id": "openai",
            "default_model": "gpt-other",
            "compaction_preserve_recent_turns": 10,
            "mcp_servers": {"filesystem": {}},
        },
        revision=4,
    )

    effective = cfg.get_effective_config()
    for field, expected in RUNTIME_OVERLAY_FIELDS.items():
        assert effective[field] == expected
    assert effective["workspace_root"] == "/user/workspace"
    assert "default_provider_id" not in effective
    assert "default_model" not in effective
    assert "compaction_preserve_recent_turns" not in effective
    assert "mcp_servers" not in effective


def test_env_overlay_allows_llm_response_flow_subtree(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            "llm": {
                "provider": "openai",
                "response_flow": {
                    "plan_policy": "explicit_or_complex",
                    "staging_policy": "explicit_or_complex",
                },
            }
        },
    )

    effective = cfg.get_effective_config()
    assert effective["llm"]["response_flow"]["plan_policy"] == "explicit_or_complex"


def test_env_overlay_allows_proxy_no_proxy_fields(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
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

    assert cfg.proxy["no_proxy"] == "localhost,.svc"
    assert cfg.proxy["noProxy"] == "localhost,.camel"
    assert "unexpected_nested" not in cfg.proxy


def test_env_overlay_base_enc_value_logs_warning_and_is_left_as_is(tmp_path, monkeypatch, caplog):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  api_key: 'ENC:legacy-encrypted-value'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EFP_PROFILE_CONFIG", raising=False)
    caplog.set_level("WARNING")

    cfg = Config(str(config_path))

    assert cfg.llm["api_key"] == "ENC:legacy-encrypted-value"
    assert "no longer supported" in caplog.text
    assert "llm.api_key" in caplog.text


# ---------------------------------------------------------------------------
# External CLI instruction injection
# ---------------------------------------------------------------------------


def test_external_cli_instructions_are_injected_for_atlassian_config(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
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

    instructions = cfg.get_effective_config()["instruction_texts"]
    joined = "\n".join(instructions)
    assert "Use bash" in joined
    assert "jira, confluence, gh, aws, jenkins, mobile-auto, and git" in joined
    assert "always pass --json" in joined
    assert "EFP_JENKINS_USERNAME" in joined
    assert "EFP_JENKINS_PASSWORD" in joined
    assert "git for clone, fetch, push, and status" in joined
    assert "auth_failed" in joined
    assert "include_default_system_prompt" not in cfg.get_effective_config()
    assert cfg.get_managed_overlay_meta()["managed_sections"] == [
        "instruction_texts",
        "jira",
    ]


def test_external_cli_instructions_require_real_atlassian_url(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
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

    effective = cfg.get_effective_config()
    assert "instruction_texts" not in effective
    assert cfg.get_managed_overlay_meta()["managed_sections"] == ["confluence", "jira"]


def test_external_cli_instructions_preserve_portal_texts(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            "instruction_texts": ["Portal supplied instructions."],
            "github": {
                "enabled": True,
                "access_token": "gh-token",
                "api_base_url": "https://github.example.test/api/v3",
            },
        },
    )

    assert cfg.get_effective_config()["instruction_texts"] == [
        "Portal supplied instructions."
    ]


# ---------------------------------------------------------------------------
# Config instance normalization (unchanged consumer behavior)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# apply_mobile_env (boot-time env export)
# ---------------------------------------------------------------------------


def test_apply_mobile_env_sets_local_binary_from_default_or_leaves_path_fallback(monkeypatch):
    monkeypatch.delenv("BROWSERSTACK_LOCAL_BINARY", raising=False)
    cfg = Config.__new__(Config)
    cfg._config = {}
    cfg._mobile_env_vars = set()
    enabled_bs = {
        "mobile-auto": {
            "enabled": True,
            "browserstack": {"username": "u", "access_key": "k"},
        }
    }

    # No explicit local.binary, bundled default present -> default is exposed.
    monkeypatch.setattr(
        "src.config.os.path.exists",
        lambda p: p == Config.DEFAULT_BROWSERSTACK_LOCAL_BINARY_PATH,
    )
    cfg._config = enabled_bs
    cfg.apply_mobile_env()
    assert os.environ["BROWSERSTACK_LOCAL_BINARY"] == Config.DEFAULT_BROWSERSTACK_LOCAL_BINARY_PATH

    # Bundled default missing -> env is left unset so mobile-auto's PATH lookup
    # of "BrowserStackLocal" stays in effect instead of pinning a phantom path.
    monkeypatch.setattr("src.config.os.path.exists", lambda p: False)
    cfg.apply_mobile_env()
    assert "BROWSERSTACK_LOCAL_BINARY" not in os.environ

    # Explicit profile path is honored even when it does not exist on disk.
    cfg._config = {
        "mobile-auto": {
            "enabled": True,
            "browserstack": {
                "username": "u",
                "access_key": "k",
                "local": {"binary": "/opt/custom/BrowserStackLocal"},
            },
        }
    }
    cfg.apply_mobile_env()
    assert os.environ["BROWSERSTACK_LOCAL_BINARY"] == "/opt/custom/BrowserStackLocal"

    # Disabling mobile-auto clears the managed binary env var.
    cfg._config = {"mobile-auto": {"enabled": False}}
    cfg.apply_mobile_env()
    assert "BROWSERSTACK_LOCAL_BINARY" not in os.environ


def test_apply_mobile_env_supports_custom_env_names_from_overlay(tmp_path, monkeypatch):
    for key in [
        "BROWSERSTACK_USERNAME",
        "BROWSERSTACK_ACCESS_KEY",
        "BROWSERSTACK_LOCAL_BINARY",
        "CUSTOM_BS_USERNAME",
        "CUSTOM_BS_ACCESS_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            "mobile-auto": {
                "enabled": True,
                "default_provider": "browserstack",
                "browserstack": {
                    "username": "bs-user",
                    "access_key": "bs-access-key",
                    "username_env": "CUSTOM_BS_USERNAME",
                    "access_key_env": "CUSTOM_BS_ACCESS_KEY",
                },
            }
        },
    )
    cfg.apply_mobile_env()

    assert os.environ["CUSTOM_BS_USERNAME"] == "bs-user"
    assert os.environ["CUSTOM_BS_ACCESS_KEY"] == "bs-access-key"
    assert os.environ["BROWSERSTACK_USERNAME"] == "bs-user"
    assert os.environ["BROWSERSTACK_ACCESS_KEY"] == "bs-access-key"

    cfg._config = {}
    cfg.apply_mobile_env()
    for key in [
        "BROWSERSTACK_USERNAME",
        "BROWSERSTACK_ACCESS_KEY",
        "CUSTOM_BS_USERNAME",
        "CUSTOM_BS_ACCESS_KEY",
    ]:
        assert key not in os.environ


# ---------------------------------------------------------------------------
# External CLI projection (gh / aws / git only; no jira/confluence CLI writes)
# ---------------------------------------------------------------------------


def test_external_config_apply_skips_atlassian_and_jenkins_clis(tmp_path, monkeypatch):
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

    profile_config_module.apply_runtime_profile_external_config(
        {
            "jira": {
                "enabled": True,
                "instances": [
                    {
                        "name": "jira-main",
                        "url": "https://jira.example.test/",
                        "username": "bot",
                        "api_token": "jira-token",
                    }
                ],
            },
            "confluence": {
                "enabled": True,
                "instances": [
                    {
                        "name": "docs",
                        "url": "https://conf.example.test/",
                        "token": "conf-token",
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
                "username": "jenkins-user",
                "password": "jenkins-password",
            },
            "git": {"user": {"name": "Runtime Bot", "email": "runtime@example.test"}},
        }
    )

    # Jira/Confluence/Jenkins reach CLIs via EFP_-prefixed tools config env vars only, never CLI writes.
    assert _command_calls(recorder, ["jira"]) == []
    assert _command_calls(recorder, ["confluence"]) == []
    assert _command_calls(recorder, ["jenkins"]) == []

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

    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert "jira" not in metadata
    assert "confluence" not in metadata
    assert "jenkins" not in metadata
    assert metadata["gh"] == {"hosts": ["github.example.test"]}
    assert metadata["git"]["managed"] == {
        "user.name": "Runtime Bot",
        "user.email": "runtime@example.test",
    }

    metadata_text = json.dumps(metadata)
    all_argv = json.dumps([call["args"] for call in recorder.calls])
    for secret in ("jira-token", "conf-token", "gh-token", "jenkins-password"):
        assert secret not in all_argv
        assert secret not in metadata_text

    profile_config_module.clear_runtime_profile_external_config()
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
    assert not metadata_path.exists()


def test_external_config_apply_removes_previous_atlassian_metadata_instances(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    recorder = _CliRecorder()
    monkeypatch.setattr(profile_config_module.subprocess, "run", recorder.run)

    # Metadata written by an older image that projected jira/confluence via CLIs.
    metadata_path = home / ".config" / "efp" / "runtime-profile-external-config.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        json.dumps(
            {
                "version": 2,
                "managed_by": "efp_runtime_profile",
                "jira": {"instances": [{"name": "jira-main"}]},
                "confluence": {"instances": [{"name": "docs"}]},
            }
        ),
        encoding="utf-8",
    )

    profile_config_module.apply_runtime_profile_external_config({})

    assert _command_calls(recorder, ["jira", "--json", "instance", "remove"]) == [
        {
            "args": ["jira", "--json", "instance", "remove", "jira-main", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    assert _command_calls(recorder, ["confluence", "--json", "instance", "remove"]) == [
        {
            "args": ["confluence", "--json", "instance", "remove", "docs", "--yes"],
            "input": None,
            "text": True,
            "capture_output": True,
            "check": False,
        }
    ]
    assert not metadata_path.exists()


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

    assert captured["input"] == "aws-password"
    assert "AD_PASS" not in captured["env"]
    assert "aws-password" not in str(exc.value)
    assert "[REDACTED_SECRET]" in str(exc.value)
    assert not (home / ".aws" / "config").exists()
    assert not (home / ".aws" / "credentials").exists()


def test_external_config_apply_uses_config_path_and_profile_proxy_env(tmp_path, monkeypatch):
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

    profile_config_module.apply_runtime_profile_external_config(
        {
            "proxy": {
                "enabled": True,
                "url": "http://proxy.example.test:8080",
                "username": "proxy-user",
                "password": "proxy-password",
                "no_proxy": "localhost,.svc",
            },
            "github": {
                "enabled": True,
                "access_token": "gh-token",
                "api_base_url": "https://github.example.test/api/v3",
            },
        },
        config_path=config_path,
    )

    expected_proxy = "http://proxy-user:proxy-password@proxy.example.test:8080"
    gh_login = _command_calls(recorder, ["gh", "auth", "login"])
    assert len(gh_login) == 1
    call = gh_login[0]
    assert call["env"]["EFP_CONFIG"] == str(config_path)
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        assert call["env"][key] == expected_proxy
    assert call["env"]["no_proxy"] == "localhost,.svc"
    assert call["env"]["NO_PROXY"] == "localhost,.svc"


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


# ---------------------------------------------------------------------------
# Tools config builder (RootConfig shape) + env-var flattener
# ---------------------------------------------------------------------------


def test_build_tools_config_json_transforms_atlassian_and_copies_verbatim_sections():
    effective = {
        "version": 1,
        "jira": {
            "enabled": True,
            "default_instance": "jira-main",
            "instances": [
                {
                    "name": "jira-main",
                    "url": "https://jira.example.test/",
                    "username": "bot",
                    "api_token": "jira-token",
                    "api_version": "3",
                },
                {"name": "disabled", "url": "https://disabled.example.test", "enabled": False},
                {"name": "no-url"},
            ],
        },
        "confluence": {
            "enabled": True,
            "instances": [
                {
                    "name": "docs",
                    "url": "https://conf.example.test/wiki",
                    "token": "conf-token",
                },
                {
                    "name": "docs-password",
                    "baseUrl": "https://conf2.example.test",
                    "username": "bot",
                    "password": "conf-password",
                },
            ],
        },
        "jenkins": {"enabled": True, "username": "jenkins-user", "password": "jenkins-password"},
        "aws": {"enabled": True, "domain": "HBEU", "username": "aws-user", "password": "aws-password"},
        "mobile-auto": {
            "enabled": True,
            "default_provider": "browserstack",
            "browserstack": {"username": "bs-user", "access_key": "bs-key"},
        },
        "llm": {"provider": "github_copilot"},
        "session": {"timeout_minutes": 30},
    }

    root = profile_config_module.build_tools_config_json(effective)

    assert set(root.keys()) == {"version", "jira", "confluence", "jenkins", "aws", "mobile-auto"}
    assert root["version"] == 1

    assert root["jira"]["default_instance"] == "jira-main"
    assert root["jira"]["instances"] == [
        {
            "name": "jira-main",
            "base_url": "https://jira.example.test",
            "rest_path": "/rest/api/3",
            "api_version": "3",
            "auth": {
                "type": "basic_api_key",
                "username": "bot",
                "api_key": "jira-token",
            },
        }
    ]

    assert root["confluence"]["default_instance"] == "docs"
    assert root["confluence"]["instances"] == [
        {
            "name": "docs",
            "base_url": "https://conf.example.test/wiki",
            "rest_path": "/rest/api",
            "auth": {"type": "bearer_token", "token": "conf-token"},
        },
        {
            "name": "docs-password",
            "base_url": "https://conf2.example.test",
            "rest_path": "/rest/api",
            "auth": {
                "type": "basic_password",
                "username": "bot",
                "password": "conf-password",
            },
        },
    ]

    # Verbatim sections.
    assert root["jenkins"] == {"enabled": True, "username": "jenkins-user", "password": "jenkins-password"}
    assert root["aws"] == {"enabled": True, "domain": "HBEU", "username": "aws-user", "password": "aws-password"}
    assert root["mobile-auto"]["browserstack"]["access_key"] == "bs-key"

    # The whole payload round-trips as JSON.
    json.dumps(root)


def test_build_tools_config_json_omits_empty_and_disabled_sections():
    root = profile_config_module.build_tools_config_json(
        {
            "jira": {"enabled": False, "instances": [{"name": "x", "url": "https://x.test"}]},
            "confluence": {"enabled": True, "instances": []},
            "jenkins": {},
            "llm": {"provider": "github_copilot"},
        }
    )
    assert root == {}


def test_flatten_config_to_env_produces_efp_prefixed_indexed_vars():
    root = {
        "version": 1,
        "jira": {
            "default_instance": "jira-main",
            "instances": [
                {
                    "name": "jira-main",
                    "base_url": "https://jira.example.test",
                    "rest_path": "/rest/api/3",
                    "api_version": "3",
                    "auth": {"type": "basic_api_key", "username": "bot", "api_key": "jira-token"},
                },
                {
                    "name": "jira-2",
                    "base_url": "https://jira2.example.test",
                    "rest_path": "/rest/api/2",
                    "api_version": "2",
                    "auth": {"type": "bearer_token", "token": "jira2-token"},
                },
            ],
        },
        "aws": {"enabled": True, "domain": "HBEU", "username": "aws-user", "password": ""},
        "mobile-auto": {
            "browserstack": {
                "username": "bs-user",
                "no_proxy_hosts": ["host-a", "host-b"],
            },
        },
    }

    env = profile_config_module.flatten_config_to_env(root)

    assert env == {
        "EFP_VERSION": "1",
        "EFP_JIRA_DEFAULT_INSTANCE": "jira-main",
        "EFP_JIRA_INSTANCES_0_NAME": "jira-main",
        "EFP_JIRA_INSTANCES_0_BASE_URL": "https://jira.example.test",
        "EFP_JIRA_INSTANCES_0_REST_PATH": "/rest/api/3",
        "EFP_JIRA_INSTANCES_0_API_VERSION": "3",
        "EFP_JIRA_INSTANCES_0_AUTH_TYPE": "basic_api_key",
        "EFP_JIRA_INSTANCES_0_AUTH_USERNAME": "bot",
        "EFP_JIRA_INSTANCES_0_AUTH_API_KEY": "jira-token",
        "EFP_JIRA_INSTANCES_1_NAME": "jira-2",
        "EFP_JIRA_INSTANCES_1_BASE_URL": "https://jira2.example.test",
        "EFP_JIRA_INSTANCES_1_REST_PATH": "/rest/api/2",
        "EFP_JIRA_INSTANCES_1_API_VERSION": "2",
        "EFP_JIRA_INSTANCES_1_AUTH_TYPE": "bearer_token",
        "EFP_JIRA_INSTANCES_1_AUTH_TOKEN": "jira2-token",
        # bool renders lowercase; the empty-string "password" is omitted.
        "EFP_AWS_ENABLED": "true",
        "EFP_AWS_DOMAIN": "HBEU",
        "EFP_AWS_USERNAME": "aws-user",
        # "mobile-auto" -> MOBILE_AUTO; []string elements indexed by position.
        "EFP_MOBILE_AUTO_BROWSERSTACK_USERNAME": "bs-user",
        "EFP_MOBILE_AUTO_BROWSERSTACK_NO_PROXY_HOSTS_0": "host-a",
        "EFP_MOBILE_AUTO_BROWSERSTACK_NO_PROXY_HOSTS_1": "host-b",
    }

    # Empty/absent scalars emit no key at all.
    assert "EFP_AWS_PASSWORD" not in env


def test_flatten_config_to_env_omits_empty_and_none_and_renders_bool_false():
    root = {
        "aws": {"enabled": False, "domain": "D", "username": None, "password": ""},
        "empty": {},
    }
    env = profile_config_module.flatten_config_to_env(root)
    assert env == {"EFP_AWS_ENABLED": "false", "EFP_AWS_DOMAIN": "D"}
