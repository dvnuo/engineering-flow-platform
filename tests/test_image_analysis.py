"""Image analysis on AI Platform through inspect-image (Copilot vision is off).

- The chat provider decides whether attached images are inlined into the
  model request (AI Platform) or handed to the agent for the inspect-image CLI
  (GitHub Copilot, unless EFP_COPILOT_VISION_VIA_CHAT says vision is back).
- Boot projects the profile's AI Platform account into a config file of
  inspect-image's own plus environment variables, and reports it.
- The native projection appends the image-analysis instructions.
"""
import asyncio
import json
import os

import pytest
from ruamel.yaml import YAML

import src.config as config_module
from src.config import Config
from src.efp_runtime.llm.vision import COPILOT_VISION_VIA_CHAT_ENV, chat_provider_accepts_images
from src.external_cli import inspect_image_config as iic
from src.external_cli import profile_config as profile_config_module
from src.gateway.image_handoff import HANDOFF_HOWTO, HANDOFF_NOT_CONFIGURED, build_image_handoff
from src.runtime_profile_projection import (
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS,
    project_canonical_for_runtime,
)


def _vision_llm(provider="github_copilot", enabled=True, vision_model="gpt-5.6-sol", **auth_overrides):
    auth = {"username": "u", "password": "pw", "usercase": "uc"}
    auth.update(auth_overrides)
    llm = {
        "provider": provider,
        "model": "gpt-5.6-terra",
        "api_key": "ghu",
        "ai_platform": {
            "chat": {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"},
            "ib2b": {"host": "https://ib2b.int", "uri": "/dsp/token"},
            "auth": {**auth, "trust_token_header": "X-Trust", "tracking_prefix": "EFP"},
        },
    }
    if enabled is not None:
        llm["vision"] = {"enabled": enabled, "model": vision_model}
    return llm


# --- which provider sees images -------------------------------------------------


def test_chat_provider_accepts_images(monkeypatch):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    assert chat_provider_accepts_images({"provider": "ai_platform"})
    assert chat_provider_accepts_images({"provider": "ai-platform"})
    assert not chat_provider_accepts_images({"provider": "github_copilot"})
    assert not chat_provider_accepts_images({"provider": "openai"})
    assert not chat_provider_accepts_images({})
    # The day Copilot vision returns, one variable flips it back on.
    monkeypatch.setenv(COPILOT_VISION_VIA_CHAT_ENV, "1")
    assert chat_provider_accepts_images({"provider": "github_copilot"})
    assert chat_provider_accepts_images({"provider": "github_copilot"}, environ={COPILOT_VISION_VIA_CHAT_ENV: "off"}) is False


# --- inspect-image settings -----------------------------------------------------


def test_resolve_image_analysis_settings():
    settings, reason = iic.resolve_image_analysis_settings({"llm": _vision_llm()})
    assert reason is None
    assert settings.model == "gpt-5.6-sol"
    assert settings.chat_host == "https://chat.int"
    assert settings.ib2b_uri == "/dsp/token"
    assert settings.trust_token_header == "X-Trust"

    _settings, reason = iic.resolve_image_analysis_settings({"llm": _vision_llm(enabled=False)})
    assert reason == "image analysis is off for this profile"

    _settings, reason = iic.resolve_image_analysis_settings({"llm": _vision_llm(usercase="")})
    assert "usercase" in reason

    missing_chat = {"llm": _vision_llm()}
    missing_chat["llm"]["ai_platform"]["chat"] = {}
    _settings, reason = iic.resolve_image_analysis_settings(missing_chat)
    assert "chat endpoint" in reason

    # An AI Platform chat profile reads images with its chat model without a vision block.
    settings, reason = iic.resolve_image_analysis_settings({"llm": _vision_llm(provider="ai_platform", enabled=None)})
    assert reason is None and settings.model == "gpt-5.6-terra"

    # A Copilot chat model is never used for images; a non-AI-Platform id lands on the default.
    settings, _reason = iic.resolve_image_analysis_settings({"llm": _vision_llm(vision_model="")})
    assert settings.model == "gpt-5.4"
    assert iic.coerce_vision_model("gpt-5.4-mini") == "gpt-5.4"
    assert iic.coerce_vision_model("ai-platform/gpt-5.6-luna") == "gpt-5.6-luna"


def test_apply_projection_writes_a_config_with_environment_references(tmp_path, monkeypatch):
    monkeypatch.delenv("EFP_MAX_UPLOAD_MB", raising=False)
    projection = iic.apply_image_analysis_projection({"llm": _vision_llm()}, config_dir=tmp_path)
    assert projection.configured is True
    assert projection.model == "gpt-5.6-sol"
    config_path = tmp_path / "inspect-image.yaml"
    assert projection.config_path == str(config_path)
    text = config_path.read_text(encoding="utf-8")
    assert "pw" not in text.replace("${EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD}", "")
    loaded = YAML().load(text)
    assert loaded["inspect_image"]["provider"] == "ai_platform"
    assert loaded["inspect_image"]["defaults"]["model"] == "gpt-5.6-sol"
    # Whatever the upload API accepts (25 MiB by default) inspect-image must take too.
    assert loaded["inspect_image"]["limits"]["max_image_bytes"] == 25 * 1024 * 1024
    assert loaded["ai_platform"]["chat"] == {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"}
    assert loaded["ai_platform"]["ib2b"] == {"host": "https://ib2b.int", "uri": "/dsp/token"}
    auth = loaded["ai_platform"]["auth"]
    assert auth["username"] == "${EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME}"
    assert auth["password"] == "${EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD}"
    assert auth["usercase"] == "${EFP_INSPECT_IMAGE_AI_PLATFORM_USERCASE}"
    assert auth["trust_token_header"] == "X-Trust"
    assert auth["token_file"] == str(tmp_path / "tmp" / "inspect-image-ai-platform-token")
    assert projection.env == {
        "INSPECT_IMAGE_CONFIG": str(config_path),
        "EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME": "u",
        "EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD": "pw",
        "EFP_INSPECT_IMAGE_AI_PLATFORM_USERCASE": "uc",
    }

    environ = {"INSPECT_IMAGE_CONFIG": "/stale", "OTHER": "1"}
    iic.export_image_analysis_env(projection, environ)
    assert environ["INSPECT_IMAGE_CONFIG"] == str(config_path)
    assert environ["EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD"] == "pw"
    assert environ["OTHER"] == "1"

    # A stale token from a previous account is dropped, and turning image
    # analysis off removes the file and exports nothing.
    token_path = tmp_path / "tmp" / "inspect-image-ai-platform-token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("old-jwt", encoding="utf-8")
    iic.apply_image_analysis_projection({"llm": _vision_llm()}, config_dir=tmp_path)
    assert not token_path.exists()
    token_path.write_text("old-jwt", encoding="utf-8")
    off = iic.apply_image_analysis_projection({"llm": _vision_llm(enabled=False)}, config_dir=tmp_path)
    assert off.configured is False
    assert off.env == {}
    assert not config_path.exists()
    assert not token_path.exists()
    iic.export_image_analysis_env(off, environ)
    assert "INSPECT_IMAGE_CONFIG" not in environ
    assert "EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD" not in environ


def test_inspect_image_size_limit_follows_the_upload_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "40")
    assert iic.max_image_bytes() == 40 * 1024 * 1024
    iic.apply_image_analysis_projection({"llm": _vision_llm()}, config_dir=tmp_path)
    loaded = YAML().load((tmp_path / "inspect-image.yaml").read_text(encoding="utf-8"))
    assert loaded["inspect_image"]["limits"]["max_image_bytes"] == 40 * 1024 * 1024
    # A broken value falls back to the upload API's own default, as the API does.
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "lots")
    assert iic.max_image_bytes() == 25 * 1024 * 1024


# --- the handoff block ----------------------------------------------------------


def test_build_image_handoff():
    files = [
        {"path": "/workspace/uploads/abc.png", "name": "shot.png", "content_type": "image/png", "size_bytes": 1234},
        {"path": "/workspace/uploads/def.jpg", "name": "def.jpg", "content_type": "image/jpeg", "size_bytes": 99},
    ]
    text = build_image_handoff(files, image_analysis={"configured": True})
    assert text.startswith("Attached image files.")
    assert '- /workspace/uploads/abc.png (image/png, 1234 bytes, attached as "shot.png")' in text
    assert "- /workspace/uploads/def.jpg (image/jpeg, 99 bytes)" in text
    assert HANDOFF_HOWTO in text
    assert HANDOFF_NOT_CONFIGURED not in text

    unconfigured = build_image_handoff(files, image_analysis={"configured": False, "reason": "off"})
    assert HANDOFF_NOT_CONFIGURED in unconfigured
    assert HANDOFF_HOWTO not in unconfigured
    assert build_image_handoff([], image_analysis={"configured": True}) == ""


def _image_chat_fixture(monkeypatch, tmp_path, *, provider):
    from src.gateway import runtime_api
    from src.utils.file_parser import storage

    captured = {}
    one = tmp_path / "one.png"
    two = tmp_path / "two.png"
    one.write_bytes(b"one")
    two.write_bytes(b"two")

    class _Meta:
        def __init__(self, file_id: str, session_id: str):
            self.file_id = file_id
            self.session_id = session_id
            self.content_type = "image/png"
            self.original_filename = f"{file_id}-shot.png"

    metadata_map = {"f1": _Meta("f1", "s1"), "f2": _Meta("f2", "s1")}
    file_map = {"f1": one, "f2": two}

    async def _fake_run_chat_via_execution_bus(**kwargs):
        captured.update(kwargs)
        return {"response": "ok", "usage": {}}

    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(runtime_api, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(runtime_api.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5.6-terra", "provider": provider}}, raising=False)
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    monkeypatch.setattr(runtime_api.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(runtime_api.runtime_session_artifacts, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(runtime_api, "get_metadata", lambda file_id: metadata_map[file_id])
    monkeypatch.setattr(storage, "get_file_path", lambda file_id: file_map[file_id])
    monkeypatch.setattr(runtime_api, "get_image_analysis_state", lambda: {"configured": True, "model": "gpt-5.4"})
    return runtime_api, captured, one, two


@pytest.mark.asyncio
async def test_api_chat_hands_images_to_inspect_image_for_a_copilot_profile(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, one, two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot")

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "what is on these?", "session_id": "s1", "attachments": ["f1", "f2"]}

    response = await runtime_api.api_chat(_Request())
    assert response.status == 200
    # Nothing is inlined for a model that cannot see it...
    assert captured["attached_images"] is None
    assert captured["message"] == "what is on these?"
    # ...the agent is told where the files are and how to read them.
    handoff = captured["transient_model_message"]
    assert f"- {one} (image/png, 3 bytes, attached as \"f1-shot.png\")" in handoff
    assert f"- {two} (image/png, 3 bytes, attached as \"f2-shot.png\")" in handoff
    assert "inspect-image inspect --image <path>" in handoff
    assert HANDOFF_NOT_CONFIGURED not in handoff
    assert captured["attachments"] == ["f1", "f2"]


@pytest.mark.asyncio
async def test_api_chat_with_only_an_image_still_runs_and_names_the_missing_connector(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, one, _two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot")
    monkeypatch.setattr(runtime_api, "get_image_analysis_state", lambda: {"configured": False, "reason": "image analysis is off for this profile"})

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "", "session_id": "s1", "attachments": ["f1"]}

    response = await runtime_api.api_chat(_Request())
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert str(one) in handoff
    assert HANDOFF_NOT_CONFIGURED in handoff


@pytest.mark.asyncio
async def test_api_chat_handoff_keeps_the_parse_failure_notice_for_other_files(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, one, _two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot")

    async def _fake_ensure(**kwargs):
        return {"context_file_ids": [], "failures": [{"file_id": "csv_bad", "error": "bad csv"}]}

    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "", "session_id": "s1", "attachments": ["f1", "csv_bad"]}

    response = await runtime_api.api_chat(_Request())
    # The image alone keeps the turn alive even though the CSV failed to parse...
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"] is None
    transient = captured["transient_model_message"]
    # ...and the model hears about the failed file before the handoff.
    assert "csv_bad" in transient and "bad csv" in transient
    assert transient.index("bad csv") < transient.index("inspect-image inspect")
    assert str(one) in transient


@pytest.mark.asyncio
async def test_api_chat_inlines_images_for_an_ai_platform_profile(monkeypatch, tmp_path):
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)
    runtime_api, captured, _one, _two = _image_chat_fixture(monkeypatch, tmp_path, provider="ai_platform")

    class _Request:
        app = {}
        headers = {}

        async def json(self):
            return {"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}

    response = await runtime_api.api_chat(_Request())
    assert response.status == 200
    assert len(captured["attached_images"]) == 2
    assert captured["attached_images"][0].startswith("data:image/png;base64,")
    assert captured["transient_model_message"] is None


# --- instructions and boot --------------------------------------------------------


def test_native_projection_appends_the_image_analysis_instructions():
    on = project_canonical_for_runtime({"llm": _vision_llm()}, "native")
    assert on["instruction_texts"] == [RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    assert RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS not in on["instruction_texts"]
    assert "instruction_texts" not in project_canonical_for_runtime({"llm": _vision_llm()}, "opencode")
    assert "instruction_texts" not in project_canonical_for_runtime({"llm": _vision_llm(enabled=False)}, "native")
    assert "instruction_texts" not in project_canonical_for_runtime({"llm": _vision_llm(password="")}, "native")
    chat = project_canonical_for_runtime({"llm": _vision_llm(provider="ai_platform", enabled=None)}, "native")
    assert RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS in chat["instruction_texts"]


def _boot_with(tmp_path, monkeypatch, overlay):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  provider: openai\n  model: gpt-4o\nproxy:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv(
        "EFP_PROFILE_CONFIG",
        json.dumps({"runtime_profile_id": "rp_vision", "name": "vision", "revision": 3, "config": overlay}),
    )
    monkeypatch.setattr(config_module, "_profile_boot_state", {"completed": False, "ready": False, "error": None})
    monkeypatch.setattr(config_module, "_image_analysis_state", dict(config_module._image_analysis_state))
    monkeypatch.setattr(profile_config_module, "apply_runtime_profile_external_config", lambda overlay, **kwargs: None)
    for key in iic.MANAGED_ENV_VARS:
        monkeypatch.setenv(key, "")
    cfg = Config(str(config_path))
    monkeypatch.setattr(config_module, "config", cfg)
    assert config_module.bootstrap_profile_boot() is True
    return config_path


def test_bootstrap_projects_inspect_image_config_and_exports_the_account(tmp_path, monkeypatch):
    config_path = _boot_with(tmp_path, monkeypatch, {"llm": _vision_llm()})

    inspect_config = tmp_path / "inspect-image.yaml"
    assert inspect_config.exists()
    assert os.environ["INSPECT_IMAGE_CONFIG"] == str(inspect_config)
    assert os.environ["EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME"] == "u"
    assert os.environ["EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD"] == "pw"
    assert os.environ["EFP_INSPECT_IMAGE_AI_PLATFORM_USERCASE"] == "uc"
    state = config_module.get_image_analysis_state()
    assert state["configured"] is True
    assert state["model"] == "gpt-5.6-sol"
    assert state["config_path"] == str(inspect_config)
    # The runtime's own config is never rewritten by the projection.
    assert "inspect_image" not in config_path.read_text(encoding="utf-8")


def test_bootstrap_without_image_analysis_exports_nothing(tmp_path, monkeypatch):
    (tmp_path / "inspect-image.yaml").write_text("stale: true\n", encoding="utf-8")
    _boot_with(tmp_path, monkeypatch, {"llm": _vision_llm(enabled=False)})

    assert not (tmp_path / "inspect-image.yaml").exists()
    assert os.environ.get("INSPECT_IMAGE_CONFIG", "") == ""
    assert os.environ.get("EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD", "") == ""
    state = config_module.get_image_analysis_state()
    assert state["configured"] is False
    assert state["reason"] == "image analysis is off for this profile"
