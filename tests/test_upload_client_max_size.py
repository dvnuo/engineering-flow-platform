"""Upload size: runtime aiohttp client_max_size is env-configurable.

The gateway aiohttp app previously used aiohttp's 1MB default, silently
capping every upload (and returning 413) regardless of the Portal's limit.
These lock the EFP_MAX_UPLOAD_MB wiring and its transport headroom.
"""

from pathlib import Path

from src.gateway import server

_HEADROOM = server.UPLOAD_TRANSPORT_HEADROOM_MB
_DEFAULT = server.DEFAULT_MAX_UPLOAD_MB


def test_default_client_max_size(monkeypatch):
    monkeypatch.delenv("EFP_MAX_UPLOAD_MB", raising=False)
    assert server.resolve_upload_client_max_size() == (_DEFAULT + _HEADROOM) * 1024 * 1024


def test_env_override(monkeypatch):
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "50")
    assert server.resolve_upload_client_max_size() == (50 + _HEADROOM) * 1024 * 1024


def test_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "not-a-number")
    assert server.resolve_upload_client_max_size() == (_DEFAULT + _HEADROOM) * 1024 * 1024


def test_non_positive_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "0")
    assert server.resolve_upload_client_max_size() == (_DEFAULT + _HEADROOM) * 1024 * 1024


def test_runtime_headroom_exceeds_user_cap():
    # The runtime must never be the gate for a file the Portal accepted.
    assert _HEADROOM > 0


def test_application_is_wired_with_client_max_size():
    source = Path("src/gateway/server.py").read_text(encoding="utf-8")
    assert "web.Application(client_max_size=resolve_upload_client_max_size())" in source
