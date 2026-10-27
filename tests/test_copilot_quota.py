"""GET /api/copilot/quota: parsing GitHub's quota payload, caching, failure handling."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from src.gateway import copilot_quota


PAYLOAD = {
    "copilot_plan": "business",
    "quota_reset_date": "2026-11-01",
    "quota_snapshots": {
        "premium_interactions": {
            "entitlement": 300,
            "remaining": 120.5,
            "percent_remaining": 40.2,
            "unlimited": False,
            "overage_count": 0,
            "overage_permitted": True,
        },
        "chat": {"unlimited": True},
        "ai_credits": {"entitlement": 1900, "remaining": 400},
    },
}


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, body: bytes | None = None, error: Exception | None = None) -> None:
        self.body = body or b"{}"
        self.error = error
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _Response(self.body)


@pytest.fixture(autouse=True)
def _clear_cache():
    copilot_quota.clear_quota_cache()
    yield
    copilot_quota.clear_quota_cache()


def test_parse_quota_payload_projects_snapshots_and_credits():
    parsed = copilot_quota.parse_copilot_quota(PAYLOAD, fetched_at="t")
    assert parsed["available"] is True
    assert parsed["plan"] == "business"
    assert parsed["reset_date"] == "2026-11-01"
    assert [snapshot["id"] for snapshot in parsed["snapshots"]] == [
        "premium_interactions",
        "chat",
        "ai_credits",
    ]
    premium = parsed["snapshots"][0]
    assert premium["label"] == "Premium requests"
    assert premium["used"] == 179.5
    assert premium["percent_remaining"] == 40.2
    assert premium["overage_permitted"] is True
    assert parsed["snapshots"][1]["unlimited"] is True
    credits = parsed["credits"]
    assert credits["id"] == "ai_credits"
    assert credits["used"] == 1500
    assert credits["percent_remaining"] == pytest.approx(21.1, abs=0.1)
    assert "quota_snapshots" in parsed["raw_keys"]
    assert parsed["error"] is None


def test_parse_quota_payload_accepts_list_snapshots_and_rejects_non_mappings():
    parsed = copilot_quota.parse_copilot_quota(
        {"quota_snapshots": [{"quota_id": "chat", "unlimited": True}], "plan": "pro"},
        fetched_at="t",
    )
    assert parsed["snapshots"][0]["id"] == "chat"
    assert parsed["plan"] == "pro"
    assert parsed["credits"] is None
    rejected = copilot_quota.parse_copilot_quota(["nope"], fetched_at="t")
    assert rejected["available"] is False
    assert rejected["snapshots"] == []


def test_fetch_requires_a_github_source_token():
    result = copilot_quota.fetch_copilot_quota("tid=plugin-token")
    assert result["available"] is False
    assert "GitHub token" in result["error"]
    assert copilot_quota.fetch_copilot_quota("")["available"] is False
    assert copilot_quota.fetch_copilot_quota(None)["available"] is False


def test_fetch_caches_per_credential_and_honours_refresh():
    opener = _Opener(json.dumps(PAYLOAD).encode("utf-8"))

    first = copilot_quota.fetch_copilot_quota("ghu_abc", opener=opener, now=100.0)
    assert first["available"] is True
    assert first["cached"] is False
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.full_url == "https://api.github.com/copilot_internal/user"
    assert request.get_header("Authorization") == "Bearer ghu_abc"
    assert request.get_header("Accept") == "application/json"

    second = copilot_quota.fetch_copilot_quota("ghu_abc", opener=opener, now=130.0)
    assert second["cached"] is True
    assert len(opener.requests) == 1

    third = copilot_quota.fetch_copilot_quota("ghu_abc", opener=opener, now=130.0, force=True)
    assert third["cached"] is False
    assert len(opener.requests) == 2

    copilot_quota.fetch_copilot_quota("ghu_abc", opener=opener, now=200.0)
    assert len(opener.requests) == 3

    copilot_quota.fetch_copilot_quota("ghu_other", opener=opener, now=200.0)
    assert len(opener.requests) == 4


def test_fetch_reports_http_errors_without_the_token_and_caches_them_briefly():
    error = urllib.error.HTTPError(
        "https://api.github.com/copilot_internal/user",
        401,
        "Unauthorized",
        hdrs=None,
        fp=io.BytesIO(b'{"message": "bad ghu_secret"}'),
    )
    opener = _Opener(error=error)

    result = copilot_quota.fetch_copilot_quota("ghu_secret", opener=opener, now=0.0)
    assert result["available"] is False
    assert "401" in result["error"]
    assert "ghu_secret" not in result["error"]

    again = copilot_quota.fetch_copilot_quota("ghu_secret", opener=opener, now=5.0)
    assert again["cached"] is True
    assert len(opener.requests) == 1

    copilot_quota.fetch_copilot_quota("ghu_secret", opener=opener, now=20.0)
    assert len(opener.requests) == 2


def test_fetch_reports_unreachable_and_non_json():
    down = _Opener(error=urllib.error.URLError("no route"))
    result = copilot_quota.fetch_copilot_quota("ghu_down", opener=down, now=0.0)
    assert result["available"] is False
    assert "unreachable" in result["error"]

    garbage = _Opener(b"<html>")
    result = copilot_quota.fetch_copilot_quota("ghu_html", opener=garbage, now=0.0)
    assert result["available"] is False
    assert "non-JSON" in result["error"]


def test_runtime_lookup_uses_the_env_token(monkeypatch):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "ghu_env")
    calls: list[str] = []

    def fake_fetch(token, **kwargs):
        calls.append(token)
        return copilot_quota.parse_copilot_quota(PAYLOAD, fetched_at="t")

    monkeypatch.setattr(copilot_quota, "_fetch", fake_fetch)
    monkeypatch.setattr(copilot_quota, "_runtime_opener", lambda: None)

    result = copilot_quota.copilot_quota_for_runtime(force=True)

    assert result["available"] is True
    assert calls == ["ghu_env"]


def test_runtime_lookup_without_a_credential_is_unavailable(monkeypatch):
    monkeypatch.delenv("EFP_GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.setattr(copilot_quota, "_runtime_credential", lambda: None)

    result = copilot_quota.copilot_quota_for_runtime()

    assert result["available"] is False
    assert "no Copilot credential" in result["error"]
