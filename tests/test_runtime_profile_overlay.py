import os

from src.config import Config


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
