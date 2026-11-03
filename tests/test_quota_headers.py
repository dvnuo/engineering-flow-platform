"""Copilot's per-answer quota headers: parsing, the per-credential record, the quota payload's ``live`` block."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from src.efp_runtime.llm import quota_headers as mod
from src.efp_runtime.llm.provider import GitHubCopilotHTTPTransport
from src.gateway import copilot_quota


class _Response:
    def __init__(self, body: bytes, headers=None) -> None:
        self._body = body
        self.headers = headers or {}

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
def _clear():
    mod.clear_quota_headers()
    copilot_quota.clear_quota_cache()
    yield
    mod.clear_quota_headers()
    copilot_quota.clear_quota_cache()


def test_parse_header_reads_entitlement_percent_overage_and_reset():
    parsed = mod.parse_quota_header("ent=1500&rem=62.5&ov=0&ovPerm=false&rst=2026-12-01")
    assert parsed == {
        "entitlement": 1500.0,
        "unlimited": False,
        "percent_remaining": 62.5,
        "overage_count": 0.0,
        "overage_permitted": False,
        "reset_date": "2026-12-01",
    }
    pooled = mod.parse_quota_header("ent=-1&rem=100&ovPerm=true")
    assert pooled["unlimited"] is True
    assert pooled["entitlement"] is None
    assert pooled["overage_permitted"] is True
    assert mod.parse_quota_header("") is None
    assert mod.parse_quota_header(None) is None
    assert mod.parse_quota_header("rem=12")["entitlement"] is None


def test_record_keeps_the_premium_snapshot_per_credential_with_rate_limits():
    headers = [
        ("Content-Type", "text/event-stream"),
        ("X-Quota-Snapshot-premium_interactions", "ent=1500&rem=40&ov=2&ovPerm=true&rst=2026-12-01"),
        ("x-quota-snapshot-chat", "ent=-1&rem=100"),
        ("x-usage-ratelimit-session", "ent=100&rem=90"),
    ]
    record = mod.record_quota_headers("ghu_abc", headers)
    assert record["id"] == "premium_interactions"
    assert record["entitlement"] == 1500
    assert record["percent_remaining"] == 40
    assert record["overage_count"] == 2
    assert record["overage_permitted"] is True
    assert record["reset_date"] == "2026-12-01"
    assert record["observed_at"]
    assert record["snapshots"]["chat"]["unlimited"] is True
    assert record["rate_limits"]["session"]["percent_remaining"] == 90

    latest = mod.latest_quota_header("ghu_abc")
    assert latest["id"] == "premium_interactions"
    assert mod.latest_quota_header("ghu_other") is None
    # An answer without a quota header leaves the last snapshot in place.
    assert mod.record_quota_headers("ghu_abc", [("content-type", "application/json")]) is None
    assert mod.latest_quota_header("ghu_abc")["percent_remaining"] == 40
    assert mod.record_quota_headers(None, headers) is None
    assert mod.record_quota_headers("", headers) is None


def test_http_message_headers_are_read_case_insensitively():
    message = Message()
    message["X-Quota-Snapshot-Premium_Models"] = "ent=1900&rem=75"
    record = mod.record_quota_headers("ghu_abc", message)
    assert record["id"] == "premium_models"
    assert record["entitlement"] == 1900
    assert record["percent_remaining"] == 75


def test_the_quota_payload_carries_the_live_header_even_when_github_is_down():
    mod.record_quota_headers("ghu_abc", [("x-quota-snapshot-premium_interactions", "ent=1500&rem=40")])
    opener = _Opener(json.dumps({"copilot_plan": "business", "quota_snapshots": {}}).encode("utf-8"))

    result = copilot_quota.fetch_copilot_quota("ghu_abc", opener=opener, now=0.0)
    assert result["available"] is True
    assert result["live"]["percent_remaining"] == 40
    cached = copilot_quota.fetch_copilot_quota("ghu_abc", opener=opener, now=10.0)
    assert cached["cached"] is True
    assert cached["live"]["percent_remaining"] == 40

    assert copilot_quota.fetch_copilot_quota("ghu_none", opener=opener, now=0.0)["live"] is None

    down = _Opener(error=urllib.error.URLError("no route"))
    unavailable = copilot_quota.fetch_copilot_quota("ghu_abc", opener=down, now=0.0, force=True)
    assert unavailable["available"] is False
    assert unavailable["live"]["percent_remaining"] == 40


def test_the_transport_records_the_headers_of_an_answer(monkeypatch):
    transport = GitHubCopilotHTTPTransport(token="tid=plugin-token", exchange_source_token=False)
    response = _Response(
        b'{"id": "resp_1", "output": []}',
        headers={"x-quota-snapshot-premium_interactions": "ent=1500&rem=10&ovPerm=false"},
    )
    monkeypatch.setattr(transport, "_open", lambda request, timeout=None: response)

    transport._send_sync({"model": "gpt-6-sol", "input": [], "stream": False})

    latest = mod.latest_quota_header("tid=plugin-token")
    assert latest["percent_remaining"] == 10
    assert latest["entitlement"] == 1500


def test_an_odd_header_object_never_fails_the_request(monkeypatch):
    transport = GitHubCopilotHTTPTransport(token="tid=plugin-token", exchange_source_token=False)

    class _Weird:
        def items(self):
            raise RuntimeError("no headers today")

    response = _Response(b'{"id": "resp_1"}', headers=_Weird())
    monkeypatch.setattr(transport, "_open", lambda request, timeout=None: response)

    assert transport._send_sync({"model": "gpt-6-sol", "input": []}) == {"id": "resp_1"}
    assert mod.latest_quota_header("tid=plugin-token") is None
