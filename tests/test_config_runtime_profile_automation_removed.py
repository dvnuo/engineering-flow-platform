import json

from src.config import Config


def _write_base_config(path):
    path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "jira:\n"
        "  enabled: false\n"
        "confluence:\n"
        "  enabled: false\n"
        "github:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )


def _env_config(tmp_path, monkeypatch, overlay_config, profile_id="rp-env", revision=1):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    monkeypatch.setenv(
        "EFP_PROFILE_CONFIG",
        json.dumps(
            {
                "runtime_profile_id": profile_id,
                "name": "test",
                "revision": revision,
                "runtime_type": "native",
                "config": overlay_config,
            }
        ),
    )
    return Config(str(config_path))


def test_portal_managed_field_tree_provider_sections_do_not_include_automation():
    assert "automation" not in Config.PORTAL_MANAGED_FIELD_TREE["github"]
    assert "automation" not in Config.PORTAL_MANAGED_FIELD_TREE["jira"]
    assert "automation" not in Config.PORTAL_MANAGED_FIELD_TREE["confluence"]
    managed_tree_repr = repr(Config.PORTAL_MANAGED_FIELD_TREE)
    assert "review_requests" not in managed_tree_repr
    assert "assignments" not in managed_tree_repr
    assert "mentions" not in managed_tree_repr
    assert Config.PORTAL_MANAGED_FIELD_TREE["llm"]["response_flow"] is True


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
                    "default_skill_execution_style": "direct",
                    "ask_user_policy": "blocked_only",
                },
            }
        },
        profile_id="rp-response-flow",
    )
    effective = cfg.get_effective_config()
    assert effective["llm"]["response_flow"]["plan_policy"] == "explicit_or_complex"


def test_env_overlay_ignores_provider_automation_subtrees(tmp_path, monkeypatch):
    cfg = _env_config(
        tmp_path,
        monkeypatch,
        {
            "github": {
                "enabled": True,
                "api_token": "x",
                "base_url": "https://api.github.com",
                "automation": {
                    "review_requests": {"enabled": True, "repos": ["owner/repo"]},
                    "mentions": {"enabled": True, "repos": ["owner/repo"]},
                },
            },
            "jira": {
                "enabled": True,
                "automation": {
                    "assignments": {"enabled": True, "projects": ["ENG"]},
                    "mentions": {"enabled": True, "projects": ["ENG"]},
                },
            },
            "confluence": {
                "enabled": True,
                "automation": {
                    "mentions": {"enabled": True, "spaces": ["DOCS"]},
                },
            },
        },
        profile_id="rp-automation-removed",
    )

    effective = cfg.get_effective_config()

    assert effective["github"]["enabled"] is True
    assert effective["github"]["api_token"] == "x"
    assert effective["github"]["base_url"] == "https://api.github.com"
    assert "automation" not in effective["github"]
    assert "automation" not in effective["jira"]
    assert "automation" not in effective["confluence"]

    assert cfg.get_managed_overlay_meta()["managed_sections"] == [
        "confluence",
        "github",
        "instruction_texts",
        "jira",
    ]


def test_env_overlay_applies_llm_temperature(tmp_path, monkeypatch):
    cfg = _env_config(tmp_path, monkeypatch, {"llm": {"temperature": 0.2}}, profile_id="rp1")
    effective = cfg.get_effective_config()
    assert effective["llm"]["temperature"] == 0.2
