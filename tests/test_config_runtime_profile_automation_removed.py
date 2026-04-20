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


def test_portal_managed_field_tree_provider_sections_do_not_include_automation():
    assert "automation" not in Config.PORTAL_MANAGED_FIELD_TREE["github"]
    assert "automation" not in Config.PORTAL_MANAGED_FIELD_TREE["jira"]
    assert "automation" not in Config.PORTAL_MANAGED_FIELD_TREE["confluence"]


def test_set_managed_overlay_ignores_provider_automation_subtrees(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        "rp-automation-removed",
        1,
        {
            "github": {
                "enabled": True,
                "api_token": "x",
                "automation": {
                    "review_requests": {"enabled": True, "repos": ["owner/repo"]},
                },
            },
            "jira": {
                "enabled": True,
                "automation": {
                    "assignments": {"enabled": True, "projects": ["ENG"]},
                },
            },
            "confluence": {
                "enabled": True,
                "automation": {
                    "mentions": {"enabled": True, "spaces": ["ENG"]},
                },
            },
        },
    )

    cfg.load()
    effective = cfg.get_effective_config()

    assert effective["github"]["enabled"] is True
    assert effective["github"]["api_token"] == "x"
    assert "automation" not in effective["github"]

    assert effective["jira"]["enabled"] is True
    assert "automation" not in effective["jira"]

    assert effective["confluence"]["enabled"] is True
    assert "automation" not in effective["confluence"]
