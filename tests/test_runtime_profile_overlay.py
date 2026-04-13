import os

from src.config import Config


def _write_base_config(path):
    path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  model: gpt-4o\n"
        "jira:\n"
        "  enabled: false\n"
        "proxy:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )


def test_runtime_profile_overlay_deep_merge_and_managed_sections(tmp_path):
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

    effective = cfg.get_effective_config()
    assert effective["llm"]["provider"] == "anthropic"
    assert effective["llm"]["model"] == "gpt-4o"
    assert effective["jira"]["enabled"] is True
    assert "unknown" not in effective

    meta = cfg.get_managed_overlay_meta()
    assert meta["runtime_profile_id"] == "rp_1"
    assert meta["revision"] == 3
    assert meta["managed_sections"] == ["jira", "llm"]


def test_runtime_profile_overlay_clear_restores_base(tmp_path):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    cfg.set_managed_overlay("rp_2", 1, {"jira": {"enabled": True}})
    assert cfg.jira.get("enabled") is True

    cfg.clear_managed_overlay()
    assert cfg.jira.get("enabled") is False
    assert not runtime_profile_path.exists()


def test_runtime_profile_overlay_encrypt_decrypt_sensitive_fields(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)
    monkeypatch.setenv("EFP_CONFIG_KEY", "test-key")

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    cfg.set_managed_overlay("rp_3", 7, {"proxy": {"enabled": True, "url": "http://proxy:8080", "password": "secret"}})

    raw_content = runtime_profile_path.read_text(encoding="utf-8")
    assert "ENC:" in raw_content
    assert "secret" not in raw_content

    cfg.load()
    assert cfg.proxy.get("password") == "secret"


def test_runtime_profile_overlay_proxy_applies_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"]:
        monkeypatch.delenv(key, raising=False)

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path
    cfg.set_managed_overlay("rp_4", 1, {"proxy": {"enabled": True, "url": "http://proxy.example.com:8080"}})

    assert os.environ["http_proxy"] == "http://proxy.example.com:8080"
    assert os.environ["HTTPS_PROXY"] == "http://proxy.example.com:8080"


def test_runtime_profile_overlay_section_removal_includes_proxy_change_and_rolls_back_to_base(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    runtime_profile_path = tmp_path / "runtime_profile.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.runtime_profile_path = runtime_profile_path

    apply_calls = {"count": 0}
    original_apply_proxy = cfg.apply_proxy

    def _count_apply_proxy():
        apply_calls["count"] += 1
        original_apply_proxy()

    monkeypatch.setattr(cfg, "apply_proxy", _count_apply_proxy)

    first_changed = cfg.set_managed_overlay(
        "rp_5",
        1,
        {
            "proxy": {"enabled": True, "url": "http://overlay.proxy.local:8080"},
            "llm": {"provider": "anthropic"},
        },
    )
    assert "proxy" in first_changed
    assert cfg.proxy.get("url") == "http://overlay.proxy.local:8080"

    second_changed = cfg.set_managed_overlay("rp_5", 2, {"llm": {"provider": "openai"}})
    assert "proxy" in second_changed
    assert apply_calls["count"] >= 2
    assert cfg.proxy.get("enabled") is False
