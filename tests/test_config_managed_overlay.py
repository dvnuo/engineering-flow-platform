from ruamel.yaml import YAML

from src.config import Config


yaml = YAML()


def test_set_managed_overlay_prunes_stale_llm_temperature_and_response_flow(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  model: gpt-4\n"
        "  temperature: 0.2\n"
        "  tools: []\n"
        "  response_flow:\n"
        "    plan_policy: always\n"
        "  tool_loop:\n"
        "    one_tool_per_turn: true\n"
        "proxy:\n"
        "  enabled: false\n"
        "session:\n"
        "  timeout_minutes: 30\n",
        encoding="utf-8",
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        runtime_profile_id="rp-1",
        revision=2,
        overlay_config={
            "llm": {
                "provider": "github_copilot",
                "model": "gpt-5.4-mini",
                "tools": ["*"],
            }
        },
    )

    with config_path.open("r", encoding="utf-8") as handle:
        written = yaml.load(handle)

    assert written["llm"]["provider"] == "github_copilot"
    assert written["llm"]["model"] == "gpt-5.4-mini"
    assert written["llm"]["tools"] == ["*"]
    assert "temperature" not in written["llm"]
    assert "response_flow" not in written["llm"]
    assert written["session"]["timeout_minutes"] == 30


def test_set_managed_overlay_writes_and_prunes_llm_timeout_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "llm:\n"
        "  provider: openai\n"
        "  timeout_ms: 120000\n"
        "  timeout_seconds: 120\n"
        "  timeout: 120000\n",
        encoding="utf-8",
    )

    cfg = Config(str(config_path))
    cfg.set_managed_overlay(
        runtime_profile_id="rp-timeout",
        revision=1,
        overlay_config={
            "llm": {
                "provider": "github_copilot",
                "timeout_ms": 600000,
            }
        },
    )

    with config_path.open("r", encoding="utf-8") as handle:
        written = yaml.load(handle)

    assert written["llm"]["provider"] == "github_copilot"
    assert written["llm"]["timeout_ms"] == 600000
    assert "timeout_seconds" not in written["llm"]
    assert "timeout" not in written["llm"]

    cfg.set_managed_overlay(
        runtime_profile_id="rp-timeout",
        revision=2,
        overlay_config={"llm": {"provider": "github_copilot"}},
    )

    with config_path.open("r", encoding="utf-8") as handle:
        written = yaml.load(handle)

    assert written["llm"]["provider"] == "github_copilot"
    assert "timeout_ms" not in written["llm"]
    assert "timeout_seconds" not in written["llm"]
    assert "timeout" not in written["llm"]
