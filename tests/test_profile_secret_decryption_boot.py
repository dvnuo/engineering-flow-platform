"""Boot-time decryption of ENC: sensitive values in the EFP_PROFILE_CONFIG.

The portal encrypts sensitive VALUES (api_key/token/password/...) of the
canonical profile config as ``ENC:<fernet-token>``. The runtime must decrypt
them at boot, immediately after parsing EFP_PROFILE_CONFIG and BEFORE the
per-runtime projection, so the projection and every downstream consumer see
plaintext.
"""

import base64
import hashlib
import json

import pytest

import src.config as config_module
from src.config import Config
from src.runtime_profile_encryption import ENC_PREFIX


TEST_KEY = "unit-test-config-key"


def _enc(value: str, key: str = TEST_KEY) -> str:
    """Encrypt ``value`` with the same Fernet derivation the portal uses."""
    from cryptography.fernet import Fernet

    fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))
    return ENC_PREFIX + fernet.encrypt(value.encode()).decode()


def _write_base_config(path):
    path.write_text(
        "llm:\n"
        "  provider: github_copilot\n"
        "  model: gpt-4o\n"
        "proxy:\n"
        "  enabled: false\n",
        encoding="utf-8",
    )


def _payload(config):
    return json.dumps(
        {
            "runtime_profile_id": "rp_enc",
            "name": "enc-profile",
            "revision": 3,
            "runtime_type": "native",
            "config": config,
        }
    )


def _canonical_with_secrets(*, encrypt: bool):
    def maybe(value):
        return _enc(value) if encrypt else value

    return {
        "llm": {
            "provider": "github_copilot",
            "model": "gpt-4o",
            "api_key": maybe("sk-live-secret"),
        },
        "jira": {
            "enabled": True,
            "instances": [
                {
                    "name": "jira-main",
                    "url": "https://jira.example.test",
                    "username": "bot",
                    "api_token": maybe("jira-token-secret"),
                    "password": maybe("jira-password-secret"),
                    "api_version": "3",
                }
            ],
        },
    }


@pytest.fixture()
def projection_spy(monkeypatch):
    """Capture the config handed to project_canonical_for_runtime."""
    captured = {}
    real = config_module.project_canonical_for_runtime

    def _spy(canonical, runtime_type):
        captured["config"] = json.loads(json.dumps(canonical))
        captured["runtime_type"] = runtime_type
        return real(canonical, runtime_type)

    monkeypatch.setattr(config_module, "project_canonical_for_runtime", _spy)
    return captured


def _build_config(tmp_path, monkeypatch, overlay_config):
    config_path = tmp_path / "config.yaml"
    _write_base_config(config_path)
    monkeypatch.setenv("EFP_PROFILE_CONFIG", _payload(overlay_config))
    return Config(str(config_path))


def test_enc_values_decrypt_to_plaintext_before_projection(
    tmp_path, monkeypatch, projection_spy
):
    monkeypatch.setenv("EFP_CONFIG_KEY", TEST_KEY)
    cfg = _build_config(tmp_path, monkeypatch, _canonical_with_secrets(encrypt=True))

    # No load error: decryption succeeded.
    assert cfg._profile_load_error is None

    # The projection received PLAINTEXT (decrypt ran first). No ENC: survives.
    projected_input = projection_spy["config"]
    assert projection_spy["runtime_type"] == "native"
    assert projected_input["llm"]["api_key"] == "sk-live-secret"
    instance = projected_input["jira"]["instances"][0]
    assert instance["api_token"] == "jira-token-secret"
    assert instance["password"] == "jira-password-secret"
    blob = json.dumps(projected_input)
    assert ENC_PREFIX not in blob


def test_no_enc_values_is_a_noop(tmp_path, monkeypatch, projection_spy):
    # No EFP_CONFIG_KEY at all; a plaintext canonical config must load fine.
    monkeypatch.delenv("EFP_CONFIG_KEY", raising=False)
    plaintext = _canonical_with_secrets(encrypt=False)
    cfg = _build_config(tmp_path, monkeypatch, plaintext)

    assert cfg._profile_load_error is None
    projected_input = projection_spy["config"]
    assert projected_input["llm"]["api_key"] == "sk-live-secret"
    instance = projected_input["jira"]["instances"][0]
    assert instance["api_token"] == "jira-token-secret"
    assert instance["password"] == "jira-password-secret"


def test_enc_present_without_key_records_load_error(
    tmp_path, monkeypatch, projection_spy
):
    monkeypatch.delenv("EFP_CONFIG_KEY", raising=False)
    cfg = _build_config(tmp_path, monkeypatch, _canonical_with_secrets(encrypt=True))

    # Decryption raised (ENC present, no key) -> surfaced like a parse error.
    assert cfg._profile_load_error is not None
    assert "Invalid EFP_PROFILE_CONFIG" in cfg._profile_load_error
    assert "EFP_CONFIG_KEY" in cfg._profile_load_error
    # Projection must NOT run when decryption fails.
    assert projection_spy == {}
