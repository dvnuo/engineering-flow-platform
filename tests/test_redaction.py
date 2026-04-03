"""Tests for redaction utilities."""

from src.utils.redaction import redact_text, redact_value, safe_preview


def test_sensitive_dict_keys_are_redacted():
    data = {"password": "p@ss", "token": "abc", "ok": "value"}
    out = redact_value(data)
    assert out["password"] == "***REDACTED***"
    assert out["token"] == "***REDACTED***"
    assert out["ok"] == "value"


def test_nested_structures_are_redacted():
    data = {
        "nested": [{"api_key": "secret123"}, {"x": "Authorization: Bearer abc"}],
        "tuple": ("token=abc", {"refresh_token": "zzz"}),
    }
    out = redact_value(data)
    assert out["nested"][0]["api_key"] == "***REDACTED***"
    assert "***REDACTED***" in out["nested"][1]["x"]
    assert "***REDACTED***" in out["tuple"][0]
    assert out["tuple"][1]["refresh_token"] == "***REDACTED***"


def test_text_patterns_are_redacted():
    text = "Authorization: Bearer abc Cookie: foo=bar password=xyz api_key=zzz secret=1"
    out = redact_text(text)
    assert "abc" not in out
    assert "foo=bar" not in out
    assert "xyz" not in out
    assert "zzz" not in out


def test_url_credentials_are_redacted():
    text = "fetch https://alice:hunter2@example.com/path"
    out = redact_text(text)
    assert "hunter2" not in out
    assert "***REDACTED***" in out


def test_private_key_block_is_redacted():
    text = """line\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\nend"""
    out = redact_text(text)
    assert "BEGIN PRIVATE KEY" not in out
    assert "***REDACTED***" in out


def test_safe_preview_redacts_before_truncating():
    value = {"Authorization": "Bearer abcdef", "payload": "x" * 500}
    out = safe_preview(value, limit=80)
    assert "abcdef" not in out
    assert "***REDACTED***" in out
    assert "chars hidden" in out


def test_camelcase_sensitive_keys_are_redacted():
    data = {
        "githubApiToken": "ghp_abc123",
        "openaiApiKey": "sk-test-value",
        "accessToken": "access-secret",
    }
    out = redact_value(data)
    assert out["githubApiToken"] == "***REDACTED***"
    assert out["openaiApiKey"] == "***REDACTED***"
    assert out["accessToken"] == "***REDACTED***"


def test_assignment_style_patterns_are_redacted():
    text = "access_token=abc refresh_token=xyz secret_key=qwe"
    out = redact_text(text)
    assert "abc" not in out
    assert "xyz" not in out
    assert "qwe" not in out
    assert out.count("***REDACTED***") >= 3


class StringifiesToSecret:
    def __str__(self):
        return "access_token=abc123 password=secret"


def test_safe_preview_redacts_custom_object_stringification():
    preview = safe_preview(StringifiesToSecret(), limit=200)
    assert "abc123" not in preview
    assert "secret" not in preview
    assert "***REDACTED***" in preview
