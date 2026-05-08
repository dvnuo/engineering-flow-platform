from src.agents.llm import GitHubCopilotProvider
from src.config import config


def test_github_copilot_headers_include_integration_id(monkeypatch):
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN", "gho_TEST")

    provider = GitHubCopilotProvider()
    headers = provider._get_headers()

    assert headers["Authorization"] == "Bearer gho_TEST"
    assert headers["copilot-integration-id"] == "vscode-chat"
    assert headers["Accept"] == "application/vnd.github.copilot-chat-preview+json"


def test_github_copilot_api_key_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.setitem(config._config, "llm", {"api_key": "CONFIG_TOKEN"})

    provider = GitHubCopilotProvider()
    headers = provider._get_headers()

    assert headers["Authorization"] == "Bearer CONFIG_TOKEN"
    assert headers["copilot-integration-id"] == "vscode-chat"
