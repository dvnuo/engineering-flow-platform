"""Tests for Gateway server."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import patch, AsyncMock
import json

from openclaw_mini.gateway.server import Gateway, verify_discord_signature


def test_verify_discord_signature_valid():
    """Test valid signature verification."""
    import hmac
    import hashlib
    
    payload = b'{"type": 0}'
    secret = "test_secret"
    
    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    signature = f"sha256={expected}"
    
    assert verify_discord_signature(payload, signature, secret) is True


def test_verify_discord_signature_invalid():
    """Test invalid signature verification."""
    payload = b'{"type": 0}'
    signature = "sha256=invalid_signature"
    
    assert verify_discord_signature(payload, signature, "test_secret") is False


def test_verify_discord_signature_skip():
    """Test signature verification is skipped when no secret."""
    payload = b'{"type": 0}'
    signature = ""
    
    # Should skip verification if secret is empty
    assert verify_discord_signature(payload, signature, "") is True


def test_gateway_init():
    """Test Gateway initialization."""
    gateway = Gateway()
    assert gateway.host == "0.0.0.0"
    assert gateway.port == 8000
    assert hasattr(gateway, 'app')
    assert hasattr(gateway, 'runner') is False
    assert hasattr(gateway, 'site') is False


def test_gateway_routes_registered():
    """Test that routes are registered."""
    gateway = Gateway()
    routes = list(gateway.app.router.routes())
    route_paths = [r.resource.canonical if r.resource else None for r in routes]
    
    assert any("/health" in str(p) for p in route_paths)
    assert any("/webhook/discord" in str(p) for p in route_paths)


if __name__ == "__main__":
    test_verify_discord_signature_valid()
    test_verify_discord_signature_invalid()
    test_verify_discord_signature_skip()
    test_gateway_init()
    test_gateway_routes_registered()
    print("All gateway tests passed!")
