"""The Proxy connector resolved into a choice per connector (src/utils/proxy_plan.py)."""

from __future__ import annotations

from src.utils.proxy_plan import (
    KIND_ENVIRONMENT,
    KIND_NONE,
    KIND_PROXY,
    SOURCE_ASSIGNMENT,
    SOURCE_DEFAULT,
    SOURCE_DISABLED,
    SOURCE_ENVIRONMENT,
    SOURCE_INSTANCE,
    SOURCE_NO_PROXY,
    SOURCE_UNKNOWN,
    build_proxy_plan,
    hostname_of,
    no_proxy_exempts,
    normalize_proxy_section,
)


def list_section(**overrides):
    section = {
        "enabled": True,
        "default": "corp-a",
        "proxies": [
            {
                "name": "corp-a",
                "url": "http://proxy-a.example.test:3128",
                "username": "ua",
                "password": "p:a",
                "no_proxy": "localhost,.svc.cluster.local,db.internal",
            },
            {"name": "corp-b", "url": "https://proxy-b.example.test"},
        ],
        "assignments": {
            "llm": "corp-b",
            "pgsql": "corp-b",
            "jira": "",
            "github": "corp-a",
            "splunk": "none",
            "nexus": "ghost",
        },
    }
    section.update(overrides)
    return section


def test_legacy_section_reads_as_one_default_proxy():
    legacy = {"enabled": True, "url": "http://proxy.example.test:8080", "username": "u", "password": "p", "noProxy": "a.internal"}
    section = normalize_proxy_section(legacy)
    assert section["default"] == "default"
    assert section["proxies"] == [
        {"name": "default", "url": "http://proxy.example.test:8080", "username": "u", "password": "p", "no_proxy": "a.internal"}
    ]
    assert section["assignments"] == {}
    plan = build_proxy_plan(legacy)
    assert plan.configured and plan.default.name == "default"
    assert plan.environment()["HTTPS_PROXY"] == "http://u:p@proxy.example.test:8080"
    assert plan.environment()["NO_PROXY"] == "a.internal"
    # Every connector follows the environment, exactly as before named proxies.
    for connector in ("llm", "jira", "pgsql", "browserstack"):
        assert plan.choice(connector).kind == KIND_ENVIRONMENT


def test_normalize_skips_unusable_entries_and_falls_back_to_the_first_default():
    section = normalize_proxy_section(
        {
            "enabled": "true",
            "default": "missing",
            "proxies": [
                {"name": "", "url": "http://unnamed.example.test:1"},
                {"url": ""},
                "not a mapping",
                {"name": "dup", "url": "http://dup-1.example.test:1"},
                {"name": "dup", "url": "http://dup-2.example.test:1"},
            ],
            "assignments": {"jira": "dup", "": "x"},
        }
    )
    assert [item["name"] for item in section["proxies"]] == ["proxy-1", "dup"]
    assert section["proxies"][1]["url"] == "http://dup-1.example.test:1"
    assert section["default"] == "proxy-1"
    assert section["assignments"] == {"jira": "dup"}
    assert section["enabled"] is True
    plan = build_proxy_plan({"enabled": True, "default": "missing", "proxies": [{"name": "a", "url": "http://a.example.test:1"}]})
    assert plan.default.name == "a"
    assert any("missing" in warning for warning in plan.warnings)


def test_environment_carries_the_default_proxy_only():
    plan = build_proxy_plan(list_section())
    env = plan.environment()
    expected = "http://ua:p%3Aa@proxy-a.example.test:3128"
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        assert env[key] == expected
    assert env["NO_PROXY"] == env["no_proxy"] == "localhost,.svc.cluster.local,db.internal"
    assert len(env) == 8
    # corp-b never reaches the environment; it is for the connectors assigned to it.
    assert "proxy-b" not in "".join(env.values())

    assert build_proxy_plan(list_section(enabled=False)).environment() == {}
    assert build_proxy_plan({"enabled": True, "proxies": []}).environment() == {}
    assert build_proxy_plan(None).environment() == {}

    no_proxy_default = build_proxy_plan(list_section(proxies=[{"name": "a", "url": "http://a.example.test:1"}], default="a"))
    assert no_proxy_default.environment()["NO_PROXY"] == "localhost,127.0.0.1,169.254.169.254,.svc.cluster.local"


def test_choice_per_connector():
    plan = build_proxy_plan(list_section())

    jira = plan.choice("jira", host="jira.example.test")
    assert (jira.kind, jira.source, jira.setting) == (KIND_ENVIRONMENT, SOURCE_ENVIRONMENT, "")

    splunk = plan.choice("splunk", host="splunk.example.test")
    assert (splunk.kind, splunk.source, splunk.setting) == (KIND_NONE, SOURCE_ASSIGNMENT, "none")

    llm = plan.choice("llm", host="chat.example.test")
    assert (llm.kind, llm.source, llm.setting) == (KIND_PROXY, SOURCE_ASSIGNMENT, "https://proxy-b.example.test")
    assert llm.entry.name == "corp-b"

    # The default proxy is what the environment already carries: the tool
    # stays on the environment path and applies NO_PROXY per host itself.
    github = plan.choice("github", host="github.example.test")
    assert (github.kind, github.source, github.setting) == (KIND_ENVIRONMENT, SOURCE_DEFAULT, "")

    nexus = plan.choice("nexus", host="nexus.example.test")
    assert (nexus.kind, nexus.source, nexus.setting) == (KIND_ENVIRONMENT, SOURCE_UNKNOWN, "")
    assert any("nexus" in warning and "ghost" in warning for warning in plan.warnings)

    unassigned = plan.choice("confluence")
    assert (unassigned.kind, unassigned.setting) == (KIND_ENVIRONMENT, "")

    # A proxy whose no_proxy exempts the target host means a direct connection.
    exempt = plan.choice("pgsql", host="db.internal", instance_setting="corp-a")
    assert (exempt.kind, exempt.source, exempt.setting) == (KIND_NONE, SOURCE_NO_PROXY, "none")
    assert plan.choice("pgsql", host="orders.internal", instance_setting="corp-a").setting == "http://ua:p%3Aa@proxy-a.example.test:3128"

    # The row's own field wins over the assignment: none, a name, or a URL.
    assert plan.choice("pgsql", host="db.example.test", instance_setting="none").source == SOURCE_INSTANCE
    assert plan.choice("pgsql", host="db.example.test", instance_setting="none").setting == "none"
    own_url = plan.choice("pgsql", host="db.example.test", instance_setting="http://own.proxy.test:9")
    assert (own_url.kind, own_url.source, own_url.setting) == (KIND_PROXY, SOURCE_INSTANCE, "http://own.proxy.test:9")
    assert plan.choice("pgsql", host="db.example.test", instance_setting="corp-b").setting == "https://proxy-b.example.test"
    # An unknown name in the row falls back to the assignment.
    assert plan.choice("pgsql", host="db.example.test", instance_setting="nobody").setting == "https://proxy-b.example.test"

    disabled = build_proxy_plan(list_section(enabled=False)).choice("llm")
    assert (disabled.kind, disabled.source, disabled.setting) == (KIND_ENVIRONMENT, SOURCE_DISABLED, "")
    # A row's own none or URL stands on its own, connector switched off or
    # not; a name needs the connector's list.
    off = build_proxy_plan(list_section(enabled=False))
    assert off.choice("pgsql", instance_setting="none").setting == "none"
    assert off.choice("pgsql", instance_setting="http://own.proxy.test:9").setting == "http://own.proxy.test:9"
    assert off.choice("pgsql", instance_setting="corp-b").kind == KIND_ENVIRONMENT
    assert build_proxy_plan(None).choice("pgsql", instance_setting="127.0.0.1:3128").setting == "127.0.0.1:3128"
    # A row's own value that is an address rather than a name: a bare host
    # (a name never has a dot), an IP address, a bracketed IPv6 host.
    assert build_proxy_plan(None).choice("pgsql", instance_setting="proxy.corp").setting == "proxy.corp"
    assert build_proxy_plan(None).choice("pgsql", instance_setting="10.0.0.9").setting == "10.0.0.9"
    assert build_proxy_plan(None).choice("pgsql", instance_setting="[::1]:3128").setting == "[::1]:3128"
    assert build_proxy_plan(None).choice("pgsql", instance_setting="corp-c").kind == KIND_ENVIRONMENT


def test_credential_environment_and_summary_keep_secrets_apart():
    plan = build_proxy_plan(list_section())
    assert plan.credential_environment() == {"EFP_PROXY_CORP_A_USERNAME": "ua", "EFP_PROXY_CORP_A_PASSWORD": "p:a"}
    secrets = plan.redaction_secrets()
    assert "p:a" in secrets and "p%3Aa" in secrets
    assert "http://ua:p%3Aa@proxy-a.example.test:3128" in secrets
    summary = plan.summary()
    assert summary["default"] == "corp-a"
    assert summary["proxies"] == [
        {"name": "corp-a", "address": "proxy-a.example.test:3128"},
        {"name": "corp-b", "address": "proxy-b.example.test:443"},
    ]
    assert summary["assignments"]["splunk"] == "none"
    assert "p:a" not in str(summary) and "p%3Aa" not in str(summary)
    choice = plan.choice("llm").describe()
    assert choice == {"kind": "proxy", "source": "assignment", "proxy": "corp-b", "address": "proxy-b.example.test:443"}


def test_no_proxy_exempts():
    # Read the way Go's ProxyFromEnvironment reads NO_PROXY (golang.org/x/net/http/httpproxy).
    rules = "localhost,.svc.cluster.local, db.internal ,*.corp.test,https://nexus.example.test:8081,[::1],10.0.0.0/8,192.168.1.7"
    assert no_proxy_exempts("db.internal", rules)
    assert no_proxy_exempts("DB.internal.", rules)
    # A bare domain covers its subdomains too.
    assert no_proxy_exempts("replica.db.internal", rules)
    # A leading dot (or *.) covers the subdomains only.
    assert no_proxy_exempts("api.svc.cluster.local", rules)
    assert not no_proxy_exempts("svc.cluster.local", rules)
    assert no_proxy_exempts("a.corp.test", rules)
    assert not no_proxy_exempts("corp.test", rules)
    assert no_proxy_exempts("nexus.example.test", rules)
    # Addresses: one address, a CIDR block, and loopback always.
    assert no_proxy_exempts("192.168.1.7", rules)
    assert not no_proxy_exempts("192.168.1.8", rules)
    assert no_proxy_exempts("10.20.30.40", rules)
    assert not no_proxy_exempts("11.0.0.1", rules)
    assert no_proxy_exempts("127.0.0.1", "")
    assert no_proxy_exempts("127.0.0.2", "")
    assert no_proxy_exempts("[::1]", "")
    assert no_proxy_exempts("anything.example.test", "*")
    assert not no_proxy_exempts("db.internal.example.test", rules)
    assert not no_proxy_exempts("notdb.internal", rules)
    assert not no_proxy_exempts("", rules)
    assert not no_proxy_exempts("jira.example.test", rules)


def test_credential_variables_are_unique_per_proxy_and_only_the_halves_that_exist():
    section = list_section(
        proxies=[
            {"name": "corp-a", "url": "http://a.example.test:1", "username": "ua", "password": "pa"},
            {"name": "corp_a", "url": "http://b.example.test:1", "username": "ub"},
            {"name": "Corp-A", "url": "http://c.example.test:1", "password": "pc"},
            {"name": "plain", "url": "http://d.example.test:1"},
        ],
        assignments={"llm": "http://u:p@x.example.test:1", "jira": "corp_a"},
    )
    plan = build_proxy_plan(section)
    assert [item.credential_env_names() for item in plan.entries] == [
        ("EFP_PROXY_CORP_A_USERNAME", "EFP_PROXY_CORP_A_PASSWORD"),
        ("EFP_PROXY_CORP_A_2_USERNAME", "EFP_PROXY_CORP_A_2_PASSWORD"),
        ("EFP_PROXY_CORP_A_3_USERNAME", "EFP_PROXY_CORP_A_3_PASSWORD"),
        ("EFP_PROXY_PLAIN_USERNAME", "EFP_PROXY_PLAIN_PASSWORD"),
    ]
    # mobile-auto refuses a named variable that is set but empty: only the halves that exist.
    assert plan.credential_environment() == {
        "EFP_PROXY_CORP_A_USERNAME": "ua",
        "EFP_PROXY_CORP_A_PASSWORD": "pa",
        "EFP_PROXY_CORP_A_2_USERNAME": "ub",
        "EFP_PROXY_CORP_A_3_PASSWORD": "pc",
    }
    # An assignment that is not a name is neither repeated in a warning nor in the summary.
    assert plan.summary()["assignments"] == {"llm": "unlisted", "jira": "corp_a"}
    assert not any("x.example.test" in warning for warning in plan.warnings)
    assert any("llm is assigned the proxy an unlisted value" in warning for warning in plan.warnings)


def test_hostname_of():
    assert hostname_of("https://Jira.Example.test:8443/rest") == "jira.example.test"
    assert hostname_of("db.internal:5432") == "db.internal"
    assert hostname_of("db.internal") == "db.internal"
    assert hostname_of("") == ""
