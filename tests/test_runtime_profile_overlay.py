import os

from src.config import Config


RUNTIME_V2_OVERLAY_FIELDS = {
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
        "  system-prompt:\n"
        "    tools:\n"
        "      enabled: true\n"
        "jira:\n"
        "  enabled: false\n"
        "proxy:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )


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
    assert effective["jira"]["enabled"] is True
    assert "unknown" not in effective

    meta = cfg.get_managed_overlay_meta()
    assert meta["runtime_profile_id"] == "rp_1"
    assert meta["revision"] == 3
    assert meta["managed_sections"] == ["jira", "llm"]


def test_runtime_profile_apply_preserves_and_clears_runtime_v2_top_level_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write("workspace_root: /user/workspace\n")

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_runtime_v2",
        4,
        {
            **RUNTIME_V2_OVERLAY_FIELDS,
            "workspace_root": "/portal/workspace",
            "default_provider_id": "openai",
            "default_model": "gpt-other",
            "compaction_preserve_recent_turns": 10,
            "mcp_servers": {"filesystem": {}},
        },
    )

    cfg.load()
    effective = cfg.get_effective_config()
    for field, expected in RUNTIME_V2_OVERLAY_FIELDS.items():
        assert effective[field] == expected
    assert effective["workspace_root"] == "/user/workspace"
    assert "default_provider_id" not in effective
    assert "default_model" not in effective
    assert "compaction_preserve_recent_turns" not in effective
    assert "mcp_servers" not in effective

    cfg.clear_managed_overlay()
    cfg.load()
    cleared = cfg.get_effective_config()
    for field in RUNTIME_V2_OVERLAY_FIELDS:
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
    assert effective["llm"]["system-prompt"]["tools"]["enabled"] is True


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
                "system-prompt": {"tools": {"enabled": False}},
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
    assert effective["llm"]["system-prompt"]["tools"]["enabled"] is True
    assert effective["github"]["enabled"] is True
    assert effective["github"]["base_url"] == "https://github.example/api"
    assert "unexpected_nested" not in effective["github"]


def test_runtime_profile_apply_prunes_stale_managed_proxy_fields(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        monkeypatch.delenv(key, raising=False)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp_proxy",
        1,
        {"proxy": {"enabled": True, "url": "http://overlay.proxy.local:8080", "password": "secret"}},
    )
    assert os.environ["http_proxy"] == "http://overlay.proxy.local:8080"

    cfg.set_managed_overlay("rp_proxy", 2, {"llm": {"provider": "openai"}})
    cfg.load()
    assert cfg.proxy.get("enabled") is None
    assert cfg.proxy.get("url") is None
    assert cfg.proxy.get("password") is None


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
