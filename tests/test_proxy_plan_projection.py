"""Where each connector's proxy lands: the environment, the tools config, inspect-image, the LLM transports."""

from __future__ import annotations

import base64
import json
import os
import urllib.request

from src.config import Config
from src.efp_runtime.llm import provider as provider_module
from src.external_cli import inspect_image_config as iic
from src.external_cli import profile_config as profile_config_module
from src.gateway import runtime_chat

PROXY_ENV_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY")


def proxy_section(**assignments):
    return {
        "enabled": True,
        "default": "corp-a",
        "proxies": [
            {"name": "corp-a", "url": "http://proxy-a.example.test:3128", "username": "ua", "password": "pa", "no_proxy": "localhost,db.internal"},
            {"name": "corp-b", "url": "https://proxy-b.example.test", "username": "ub", "password": "pb"},
        ],
        "assignments": assignments,
    }


def _clear_proxy_env(monkeypatch):
    for key in PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key in ("EFP_PROXY_CORP_A_USERNAME", "EFP_PROXY_CORP_A_PASSWORD", "EFP_PROXY_CORP_B_USERNAME", "EFP_PROXY_CORP_B_PASSWORD"):
        monkeypatch.delenv(key, raising=False)


def test_apply_proxy_exports_the_default_proxy_and_every_credential(tmp_path, monkeypatch):
    _clear_proxy_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "proxy:\n"
        "  enabled: true\n"
        "  default: corp-a\n"
        "  proxies:\n"
        "    - name: corp-a\n"
        "      url: http://proxy-a.example.test:3128\n"
        "      username: ua\n"
        "      password: pa\n"
        "      no_proxy: localhost,db.internal\n"
        "    - name: corp-b\n"
        "      url: https://proxy-b.example.test\n"
        "      username: ub\n"
        "      password: pb\n"
        "  assignments:\n"
        "    llm: corp-b\n"
    )
    config = Config(str(config_path))
    config.apply_proxy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        assert os.environ[key] == "http://ua:pa@proxy-a.example.test:3128"
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "localhost,db.internal"
    # mobile-auto names its proxy credentials by variable, so every proxy's are exported.
    assert os.environ["EFP_PROXY_CORP_B_USERNAME"] == "ub"
    assert os.environ["EFP_PROXY_CORP_B_PASSWORD"] == "pb"
    assert config.proxy_plan().choice("llm").setting == "https://ub:pb@proxy-b.example.test"
    summary = config.proxy_plan().summary()
    assert summary["default"] == "corp-a" and summary["assignments"] == {"llm": "corp-b"}
    assert "pb" not in str(summary)


def test_apply_proxy_disabled_list_clears_the_environment(tmp_path, monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://inherited.example.test:1")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "proxy:\n  enabled: false\n  proxies:\n    - name: corp-a\n      url: http://proxy-a.example.test:3128\n"
    )
    Config(str(config_path)).apply_proxy()
    assert "HTTPS_PROXY" not in os.environ


def test_env_overlay_keeps_the_named_proxy_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  provider: github_copilot\n")
    monkeypatch.setenv(
        "EFP_PROFILE_CONFIG",
        json.dumps(
            {
                "runtime_profile_id": "rp_1",
                "name": "profile",
                "revision": 3,
                "runtime_type": "native",
                "config": {
                    "proxy": {
                        "enabled": True,
                        "default": "corp-b",
                        "proxies": [{"name": "corp-b", "url": "https://proxy-b.example.test"}],
                        "assignments": {"pgsql": "corp-b"},
                        "unexpected": 1,
                    }
                },
            }
        ),
    )
    cfg = Config(str(config_path))
    assert cfg.proxy["default"] == "corp-b"
    assert cfg.proxy["proxies"][0]["name"] == "corp-b"
    assert cfg.proxy["assignments"] == {"pgsql": "corp-b"}
    assert "unexpected" not in cfg.proxy


def test_build_tools_config_json_materializes_the_assigned_proxies():
    effective = {
        "proxy": proxy_section(jira="corp-b", splunk="none", pgsql="corp-a", browserstack="corp-b", nexus="corp-a"),
        "jira": {"enabled": True, "instances": [{"name": "main", "base_url": "https://jira.example.test", "auth": {"type": "bearer_token", "secret": "t"}}]},
        "splunk": {"enabled": True, "instances": [{"name": "prod", "base_url": "https://splunk.example.test:8089", "auth": {"type": "bearer_token", "secret": "t"}}]},
        "nexus": {"enabled": True, "instances": [{"name": "repo", "base_url": "https://nexus.example.test"}]},
        "confluence": {"enabled": True, "instances": [{"name": "wiki", "base_url": "https://wiki.example.test", "auth": {"type": "bearer_token", "secret": "t"}, "proxy": "none"}]},
        "pgsql": {
            "enabled": True,
            "instances": [
                {"name": "orders", "host": "orders.internal", "database": "orders", "username": "ro"},
                {"name": "exempt", "host": "db.internal", "database": "db", "username": "ro"},
                {"name": "own", "host": "own.internal", "database": "db", "username": "ro", "proxy": "http://own.proxy.test:9"},
            ],
        },
        "mobile-auto": {"enabled": True, "browserstack": {"username": "bs", "access_key": "k", "http_proxy": {"force_proxy": True}}},
    }
    root = profile_config_module.build_tools_config_json(effective)

    assert root["jira"]["instances"][0]["proxy"] == "https://ub:pb@proxy-b.example.test"
    assert root["splunk"]["instances"][0]["proxy"] == "none"
    # corp-a is the default: the environment already is that proxy.
    assert "proxy" not in root["nexus"]["instances"][0]
    # A row's own field wins over the assignment.
    assert root["confluence"]["instances"][0]["proxy"] == "none"

    by_name = {row["name"]: row for row in root["pgsql"]["instances"]}
    # corp-a is the default: the environment already is that proxy, and the
    # CLI applies NO_PROXY per host the way it always did.
    assert "proxy" not in by_name["orders"]
    assert by_name["exempt"]["proxy"] == "none"  # corp-a's no_proxy exempts db.internal
    assert by_name["own"]["proxy"] == "http://own.proxy.test:9"

    browserstack = root["mobile-auto"]["browserstack"]
    assert browserstack["http_proxy"] == {
        "force_proxy": True,
        "proxy_host": "https://proxy-b.example.test",
        "proxy_port": 443,
        "proxy_user_env": "EFP_PROXY_CORP_B_USERNAME",
        "proxy_pass_env": "EFP_PROXY_CORP_B_PASSWORD",
    }
    # BrowserStackLocal takes --proxy-host as a bare host name.
    assert browserstack["local"]["proxy_host"] == "proxy-b.example.test"
    assert browserstack["local"]["proxy_port"] == 443
    assert browserstack["local"]["proxy_pass_env"] == "EFP_PROXY_CORP_B_PASSWORD"
    assert browserstack["username"] == "bs"

    env = profile_config_module.flatten_config_to_env(root)
    assert env["EFP_JIRA_INSTANCES_0_PROXY"] == "https://ub:pb@proxy-b.example.test"
    assert env["EFP_SPLUNK_INSTANCES_0_PROXY"] == "none"
    assert env["EFP_MOBILE_AUTO_BROWSERSTACK_HTTP_PROXY_PROXY_HOST"] == "https://proxy-b.example.test"


def test_build_tools_config_json_browserstack_none_switches_discovery_off():
    effective = {
        "proxy": proxy_section(browserstack="none"),
        "mobile-auto": {
            "enabled": True,
            "browserstack": {
                "username": "bs",
                # force_proxy without a host is a config error in mobile-auto: it goes too.
                "http_proxy": {"proxy_host": "old.proxy.test", "proxy_port": 1, "force_proxy": True, "no_proxy_hosts": ["old.internal"]},
                "local": {"force_proxy": True},
            },
        },
    }
    browserstack = profile_config_module.build_tools_config_json(effective)["mobile-auto"]["browserstack"]
    assert browserstack["http_proxy"] == {"disable_proxy_discovery": True}
    assert browserstack["local"] == {"disable_proxy_discovery": True}


def test_build_tools_config_json_browserstack_names_only_the_credential_halves_that_exist():
    section = proxy_section(browserstack="corp-b")
    section["proxies"][1] = {"name": "corp-b", "url": "https://proxy-b.example.test", "username": "ub"}
    effective = {"proxy": section, "mobile-auto": {"enabled": True, "browserstack": {"username": "bs", "access_key": "k"}}}
    browserstack = profile_config_module.build_tools_config_json(effective)["mobile-auto"]["browserstack"]
    for block in (browserstack["http_proxy"], browserstack["local"]):
        assert block["proxy_user_env"] == "EFP_PROXY_CORP_B_USERNAME"
        assert "proxy_pass_env" not in block
    assert browserstack["http_proxy"]["proxy_host"] == "https://proxy-b.example.test"
    assert browserstack["local"]["proxy_host"] == "proxy-b.example.test"


def test_assigned_proxy_opener_ignores_the_environment_no_proxy(monkeypatch):
    # The environment's NO_PROXY is the default proxy's list; an assigned
    # proxy was chosen for this host already and applies to every request.
    monkeypatch.setenv("NO_PROXY", "chat.int")
    monkeypatch.setenv("no_proxy", "chat.int")
    handler = _proxy_handler_of(provider_module.build_proxy_opener("http://ub:p%3Ab@proxy-b.example.test:3128"))
    request = urllib.request.Request("https://chat.int/v1", method="POST")
    assert handler.https_open(request) is None
    assert request.host == "proxy-b.example.test:3128"
    assert request.get_header("Proxy-authorization") == "Basic " + base64.b64encode(b"ub:p:b").decode("ascii")
    # The stdlib handler would have let the environment's list skip the proxy.
    plain = urllib.request.ProxyHandler({"https": "http://proxy-b.example.test:3128"})
    request = urllib.request.Request("https://chat.int/v1")
    assert plain.https_open(request) is None and request.host == "chat.int"


def test_build_tools_config_json_without_assignments_is_what_it_always_was():
    effective = {
        "proxy": {"enabled": True, "url": "http://proxy.example.test:8080", "username": "u", "password": "p"},
        "jira": {"enabled": True, "instances": [{"name": "main", "base_url": "https://jira.example.test", "auth": {"type": "bearer_token", "secret": "t"}}]},
        "pgsql": {"enabled": True, "instances": [{"name": "orders", "host": "orders.internal", "database": "orders", "username": "ro"}]},
        "mobile-auto": {"enabled": True, "browserstack": {"username": "bs", "http_proxy": {"proxy_host": "mine.proxy.test", "proxy_port": 2}}},
    }
    root = profile_config_module.build_tools_config_json(effective)
    assert "proxy" not in root["jira"]["instances"][0]
    assert "proxy" not in root["pgsql"]["instances"][0]
    assert root["mobile-auto"]["browserstack"]["http_proxy"] == {"proxy_host": "mine.proxy.test", "proxy_port": 2}
    assert "local" not in root["mobile-auto"]["browserstack"]


def test_cli_environment_gets_the_default_proxy_and_the_credential_variables(monkeypatch):
    _clear_proxy_env(monkeypatch)
    cli_env = profile_config_module._build_cli_environment({"proxy": proxy_section(llm="corp-b")}, config_path="/tmp/config.yaml")
    assert cli_env.env["HTTPS_PROXY"] == "http://ua:pa@proxy-a.example.test:3128"
    assert cli_env.env["NO_PROXY"] == "localhost,db.internal"
    assert cli_env.env["EFP_PROXY_CORP_B_PASSWORD"] == "pb"
    assert "pa" in cli_env.secrets and "pb" in cli_env.secrets
    assert "http://ua:pa@proxy-a.example.test:3128" in cli_env.secrets
    assert "https://ub:pb@proxy-b.example.test" in cli_env.secrets

    redacted = profile_config_module.redact_runtime_profile_external_config_error(
        RuntimeError("failed via https://ub:pb@proxy-b.example.test with pb"), {"proxy": proxy_section()}
    )
    assert "pb" not in redacted


def test_inspect_image_config_takes_the_model_provider_proxy():
    llm = {
        "provider": "github_copilot",
        "model": "gpt-5.6-terra",
        "api_key": "ghu",
        "vision": {"enabled": True, "model": "gpt-5.4"},
        "ai_platform": {
            "chat": {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"},
            "ib2b": {"host": "https://ib2b.int", "uri": "/dsp/token"},
            "auth": {"username": "u", "password": "pw", "usercase": "uc"},
        },
    }
    settings, reason = iic.resolve_image_analysis_settings({"llm": llm, "proxy": proxy_section(llm="corp-b")})
    assert reason is None and settings.proxy == "https://ub:pb@proxy-b.example.test"
    payload = iic.build_inspect_image_config(settings, token_file=__import__("pathlib").Path("/tmp/token"))
    assert payload["inspect_image"]["api"] == {"proxy": "https://ub:pb@proxy-b.example.test"}

    settings, _ = iic.resolve_image_analysis_settings({"llm": llm, "proxy": proxy_section(llm="none")})
    assert iic.build_inspect_image_config(settings, token_file=__import__("pathlib").Path("/tmp/token"))["inspect_image"]["api"] == {"proxy": "none"}

    settings, _ = iic.resolve_image_analysis_settings({"llm": llm, "proxy": proxy_section()})
    assert settings.proxy == ""
    assert "api" not in iic.build_inspect_image_config(settings, token_file=__import__("pathlib").Path("/tmp/token"))["inspect_image"]


def _proxy_handler_of(opener):
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            return handler
    raise AssertionError("no ProxyHandler in the opener")


def _sends_directly(opener):
    """An opener built around an empty ProxyHandler registers no proxy at all:
    urllib only lists a ProxyHandler that has a scheme to open."""
    return not any(isinstance(handler, urllib.request.ProxyHandler) for handler in opener.handlers)


def test_build_proxy_opener_follows_the_setting():
    assert provider_module.build_proxy_opener("") is None
    assert provider_module.build_proxy_opener(None) is None
    assert provider_module.build_proxy_opener("environment") is None
    assert _sends_directly(provider_module.build_proxy_opener("none"))
    handler = _proxy_handler_of(provider_module.build_proxy_opener("http://ub:pb@proxy-b.example.test:3128"))
    assert handler.proxies == {"http": "http://ub:pb@proxy-b.example.test:3128", "https": "http://ub:pb@proxy-b.example.test:3128"}
    assert _proxy_handler_of(provider_module.build_proxy_opener("proxy.example.test:3128")).proxies["https"] == "http://proxy.example.test:3128"


def test_transports_send_through_their_opener(monkeypatch):
    copilot = provider_module.GitHubCopilotHTTPTransport(token="tok", exchange_source_token=False, proxy="none")
    assert copilot.proxy_setting == "none"
    assert _sends_directly(copilot._opener)
    assert provider_module.GitHubCopilotHTTPTransport(token="tok", exchange_source_token=False)._opener is None

    ai_platform = provider_module.AIPlatformHTTPTransport(chat_endpoint="https://chat.int/v1", token="t", proxy="http://p.example.test:1")
    assert _proxy_handler_of(ai_platform._opener).proxies["https"] == "http://p.example.test:1"

    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"token": "x", "expires_at": 4102444800}'

    def fake_open(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(ai_platform._opener, "open", fake_open)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("the environment path must not be used when a proxy setting is given")

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fail_urlopen)
    with ai_platform._open(urllib.request.Request("https://chat.int/v1", method="POST"), timeout=7) as response:
        assert response.read().startswith(b"{")
    assert seen == {"url": "https://chat.int/v1", "timeout": 7}

    # The token exchange goes through the same opener.
    opener_calls = []

    class FakeOpener:
        def open(self, request, timeout=None):
            opener_calls.append(request.full_url)
            return FakeResponse()

    exchange = provider_module.exchange_github_token_for_copilot_token("ghu_source", opener=FakeOpener())
    assert exchange.token == "x" and opener_calls == ["https://api.github.com/copilot_internal/v2/token"]


def test_runtime_chat_resolves_the_model_provider_proxy(monkeypatch):
    monkeypatch.setattr(runtime_chat.config, "_config", {"llm": {"api_key": "tok"}, "proxy": proxy_section(llm="corp-b")}, raising=False)
    assert runtime_chat._llm_proxy_setting("https://api.githubcopilot.com") == "https://ub:pb@proxy-b.example.test"
    monkeypatch.setattr(runtime_chat.config, "_config", {"llm": {"api_key": "tok"}, "proxy": proxy_section(llm="none")}, raising=False)
    assert runtime_chat._llm_proxy_setting("https://chat.int/v1") == "none"
    monkeypatch.setattr(runtime_chat.config, "_config", {"llm": {"api_key": "tok"}, "proxy": proxy_section()}, raising=False)
    assert runtime_chat._llm_proxy_setting("https://chat.int/v1") == ""
    monkeypatch.setattr(runtime_chat.config, "_config", {"llm": {"api_key": "tok"}}, raising=False)
    assert runtime_chat._llm_proxy_setting(None) == ""
