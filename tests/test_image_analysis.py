"""Image analysis on AI Platform through inspect-image (the Image analysis connector).

- Every chat model is treated as having no vision: attached images are
  inlined into the model request only when the deployment switch
  EFP_CHAT_VISION is on (EFP_COPILOT_VISION_VIA_CHAT is the same switch),
  whatever the provider. Otherwise they are handed to the agent, for the
  inspect-image CLI when the connector is configured.
- The connector is the profile's ``image_analysis`` section, independent of
  ``llm``. Boot projects it into a config file of inspect-image's own plus
  environment variables, and reports it on GET /ready.
- The native projection appends the image-analysis instructions.
- The file parser's vision path obeys the same switch.
"""
import asyncio
import json
import os

import pytest
from ruamel.yaml import YAML

import src.config as config_module
from src.config import Config
from src.efp_runtime.llm.vision import (
    CHAT_VISION_ENV,
    COPILOT_VISION_VIA_CHAT_ENV,
    chat_provider_accepts_images,
    chat_vision_enabled,
)
from src.external_cli import inspect_image_config as iic
from src.external_cli import profile_config as profile_config_module
from src.gateway.attachment_handoff import (
    ATTACHED_FILES_HEADER,
    HANDOFF_HEADER,
    HANDOFF_IMAGE_ANALYSIS_HINT,
    HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE,
    HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE,
    HANDOFF_IMAGES_INSPECT,
    HANDOFF_WITH_BRIDGE,
    HANDOFF_WITHOUT_BRIDGE,
    build_attachment_handoff,
)
from src.runtime_profile_projection import (
    RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS,
    _has_image_analysis_config,
    project_canonical_for_runtime,
)


def _image_analysis(enabled=True, model="gpt-5.6-sol", **auth_overrides):
    auth = {"username": "u", "password": "pw", "usercase": "uc"}
    auth.update(auth_overrides)
    return {
        "enabled": enabled,
        "model": model,
        "ai_platform": {
            "chat": {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"},
            "ib2b": {"host": "https://ib2b.int", "uri": "/dsp/token"},
            "auth": {**auth, "trust_token_header": "X-Trust", "tracking_prefix": "EFP"},
        },
    }


def _profile(provider="github_copilot", **image_analysis_overrides):
    """A profile whose chat provider is ``provider`` and whose Image analysis connector is filled in."""
    return {
        "llm": {"provider": provider, "model": "gpt-5.6-terra", "api_key": "ghu"},
        "image_analysis": _image_analysis(**image_analysis_overrides),
    }


def _llm_only_ai_platform_profile():
    """An AI Platform chat profile with its own account and no Image analysis connector."""
    return {
        "llm": {
            "provider": "ai_platform",
            "model": "gpt-5.6-terra",
            "ai_platform": {
                "chat": {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"},
                "ib2b": {"host": "https://ib2b.int", "uri": "/dsp/token"},
                "auth": {"username": "u", "password": "pw", "usercase": "uc"},
            },
        }
    }


def _switch_off(monkeypatch):
    monkeypatch.delenv(CHAT_VISION_ENV, raising=False)
    monkeypatch.delenv(COPILOT_VISION_VIA_CHAT_ENV, raising=False)


# --- the switch: whether the chat model is sent images ----------------------------


def test_chat_provider_accepts_images(monkeypatch):
    _switch_off(monkeypatch)
    # No provider sees images by default, AI Platform included.
    assert not chat_provider_accepts_images({"provider": "ai_platform"})
    assert not chat_provider_accepts_images({"provider": "ai-platform"})
    assert not chat_provider_accepts_images({"provider": "github_copilot"})
    assert not chat_provider_accepts_images({"provider": "openai"})
    assert not chat_provider_accepts_images({})
    assert not chat_provider_accepts_images(None)
    assert not chat_vision_enabled()
    # The deployment switch turns it on for any provider...
    monkeypatch.setenv(CHAT_VISION_ENV, "1")
    assert chat_provider_accepts_images({"provider": "github_copilot"})
    assert chat_provider_accepts_images({"provider": "ai_platform"})
    assert chat_provider_accepts_images({})
    assert chat_vision_enabled()
    # ...and the older name is the same switch.
    monkeypatch.delenv(CHAT_VISION_ENV)
    assert not chat_provider_accepts_images({"provider": "github_copilot"})
    monkeypatch.setenv(COPILOT_VISION_VIA_CHAT_ENV, "1")
    assert chat_provider_accepts_images({"provider": "github_copilot"})
    assert chat_provider_accepts_images({"provider": "ai_platform"})
    assert chat_provider_accepts_images({"provider": "github_copilot"}, environ={COPILOT_VISION_VIA_CHAT_ENV: "off"}) is False
    for value in ("1", "true", "TRUE", "yes", "on", " On "):
        assert chat_provider_accepts_images({}, environ={CHAT_VISION_ENV: value}) is True, value
    for value in ("", "0", "false", "no", "off", "maybe"):
        assert chat_provider_accepts_images({"provider": "ai_platform"}, environ={CHAT_VISION_ENV: value}) is False, value


# --- inspect-image settings -----------------------------------------------------


def test_resolve_image_analysis_settings():
    settings, reason = iic.resolve_image_analysis_settings(_profile())
    assert reason is None
    assert settings.model == "gpt-5.6-sol"
    assert settings.chat_host == "https://chat.int"
    assert settings.ib2b_uri == "/dsp/token"
    assert settings.trust_token_header == "X-Trust"
    assert (settings.username, settings.password, settings.usercase) == ("u", "pw", "uc")

    # The reasons GET /ready reports, in the order the settings are checked.
    _settings, reason = iic.resolve_image_analysis_settings(_profile(enabled=False))
    assert reason == "image analysis is off for this profile"
    assert iic.resolve_image_analysis_settings({})[1] == "image analysis is off for this profile"
    assert iic.resolve_image_analysis_settings(None)[1] == "image analysis is off for this profile"

    for missing in ("username", "password", "usercase"):
        _settings, reason = iic.resolve_image_analysis_settings(_profile(**{missing: ""}))
        assert reason == "AI Platform username, password, and usercase are required", missing

    missing_chat = _profile()
    missing_chat["image_analysis"]["ai_platform"]["chat"] = {}
    assert iic.resolve_image_analysis_settings(missing_chat) == (None, "AI Platform chat endpoint is not configured")

    missing_ib2b = _profile()
    missing_ib2b["image_analysis"]["ai_platform"]["ib2b"] = {"host": "", "uri": "/dsp/token"}
    assert iic.resolve_image_analysis_settings(missing_ib2b) == (None, "AI Platform iB2B endpoint is not configured")

    # The section is the connector: an AI Platform chat profile with its own
    # account and no image_analysis section has no image analysis, and nothing
    # of llm (account, model) is read.
    assert iic.resolve_image_analysis_settings(_llm_only_ai_platform_profile()) == (None, "image analysis is off for this profile")
    borrowed = _llm_only_ai_platform_profile()
    borrowed["image_analysis"] = {"enabled": True}
    assert iic.resolve_image_analysis_settings(borrowed) == (None, "AI Platform username, password, and usercase are required")
    for provider in ("github_copilot", "ai_platform"):
        settings, reason = iic.resolve_image_analysis_settings(_profile(provider=provider, model=""))
        assert reason is None and settings.model == "gpt-5.4", provider

    # The model is one AI Platform serves; anything else lands on the default.
    assert iic.resolve_image_analysis_settings(_profile(model="gpt-5.4-mini"))[0].model == "gpt-5.4"
    assert iic.resolve_image_analysis_settings(_profile(model="ai-platform/gpt-5.6-luna"))[0].model == "gpt-5.6-luna"
    assert iic.coerce_vision_model("gpt-5.4-mini") == "gpt-5.4"
    assert iic.coerce_vision_model("ai-platform/gpt-5.6-luna") == "gpt-5.6-luna"

    # Endpoint paths and the two header settings fall back to inspect-image's own defaults.
    bare = _profile()
    bare["image_analysis"]["ai_platform"] = {
        "chat": {"host": "https://chat.int/"},
        "ib2b": {"host": "https://ib2b.int"},
        "auth": {"username": "u", "password": "pw", "usercase": "uc"},
    }
    settings, reason = iic.resolve_image_analysis_settings(bare)
    assert reason is None
    assert settings.chat_host == "https://chat.int" and settings.chat_uri == "/v1/api/v1/chat/completions"
    assert settings.ib2b_uri == iic.DEFAULT_IB2B_URI
    assert settings.trust_token_header == iic.DEFAULT_TRUST_TOKEN_HEADER and settings.tracking_prefix == "EFP"


def test_apply_projection_writes_a_config_with_environment_references(tmp_path, monkeypatch):
    monkeypatch.delenv("EFP_MAX_UPLOAD_MB", raising=False)
    projection = iic.apply_image_analysis_projection(_profile(), config_dir=tmp_path)
    assert projection.configured is True
    assert projection.model == "gpt-5.6-sol"
    config_path = tmp_path / "inspect-image.yaml"
    assert projection.config_path == str(config_path)
    text = config_path.read_text(encoding="utf-8")
    assert "pw" not in text.replace("${EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD}", "")
    loaded = YAML().load(text)
    assert loaded["version"] == 1
    assert loaded["inspect_image"]["provider"] == "ai_platform"
    assert loaded["inspect_image"]["defaults"]["model"] == "gpt-5.6-sol"
    # Whatever the upload API accepts (25 MiB by default) inspect-image must take too.
    assert loaded["inspect_image"]["limits"]["max_image_bytes"] == 25 * 1024 * 1024
    # No Proxy connector: the CLI follows the environment and the file names no proxy.
    assert "api" not in loaded["inspect_image"]
    assert loaded["ai_platform"]["chat"] == {"host": "https://chat.int", "uri": "/v1/api/v1/chat/completions"}
    assert loaded["ai_platform"]["ib2b"] == {"host": "https://ib2b.int", "uri": "/dsp/token"}
    auth = loaded["ai_platform"]["auth"]
    assert auth["username"] == "${EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME}"
    assert auth["password"] == "${EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD}"
    assert auth["usercase"] == "${EFP_INSPECT_IMAGE_AI_PLATFORM_USERCASE}"
    assert auth["trust_token_header"] == "X-Trust"
    assert auth["tracking_prefix"] == "EFP"
    assert auth["token_file"] == str(tmp_path / "tmp" / "inspect-image-ai-platform-token")
    assert projection.env == {
        "INSPECT_IMAGE_CONFIG": str(config_path),
        "EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME": "u",
        "EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD": "pw",
        "EFP_INSPECT_IMAGE_AI_PLATFORM_USERCASE": "uc",
    }
    assert projection.status() == {"configured": True, "reason": None, "model": "gpt-5.6-sol", "config_path": str(config_path)}
    if os.name != "nt":
        assert config_path.stat().st_mode & 0o777 == 0o600

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
    iic.apply_image_analysis_projection(_profile(), config_dir=tmp_path)
    assert not token_path.exists()
    token_path.write_text("old-jwt", encoding="utf-8")
    off = iic.apply_image_analysis_projection(_profile(enabled=False), config_dir=tmp_path)
    assert off.configured is False
    assert off.env == {}
    assert off.status() == {"configured": False, "reason": "image analysis is off for this profile", "model": None, "config_path": None}
    assert not config_path.exists()
    assert not token_path.exists()
    iic.export_image_analysis_env(off, environ)
    assert "INSPECT_IMAGE_CONFIG" not in environ
    assert "EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD" not in environ
    assert environ == {"OTHER": "1"}

    # An incomplete account is not configured either: the same cleanup.
    iic.apply_image_analysis_projection(_profile(), config_dir=tmp_path)
    assert config_path.exists()
    incomplete = iic.apply_image_analysis_projection(_profile(password=""), config_dir=tmp_path)
    assert incomplete.configured is False and incomplete.env == {}
    assert not config_path.exists()


def test_inspect_image_size_limit_follows_the_upload_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "40")
    assert iic.max_image_bytes() == 40 * 1024 * 1024
    iic.apply_image_analysis_projection(_profile(), config_dir=tmp_path)
    loaded = YAML().load((tmp_path / "inspect-image.yaml").read_text(encoding="utf-8"))
    assert loaded["inspect_image"]["limits"]["max_image_bytes"] == 40 * 1024 * 1024
    # A broken value falls back to the upload API's own default, as the API does.
    monkeypatch.setenv("EFP_MAX_UPLOAD_MB", "lots")
    assert iic.max_image_bytes() == 25 * 1024 * 1024


# --- the handoff block ----------------------------------------------------------


def test_build_attachment_handoff_for_images():
    files = [
        {"path": "/workspace/uploads/abc.png", "name": "shot.png", "content_type": "image/png", "size_bytes": 1234, "status": "unreadable"},
        {"path": "/workspace/uploads/def.jpg", "name": "def.jpg", "content_type": "image/jpeg", "size_bytes": 99, "status": "unreadable"},
    ]
    text = build_attachment_handoff(files, bridge_available=False, image_analysis_configured=True)
    assert text.startswith(ATTACHED_FILES_HEADER)
    assert '- /workspace/uploads/abc.png (image/png, 1234 bytes, attached as "shot.png"; not readable by the chat model)' in text
    assert "- /workspace/uploads/def.jpg (image/jpeg, 99 bytes; not readable by the chat model)" in text
    assert text.endswith(f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE}")
    assert 'inspect-image inspect --image <path> --prompt "<what to read or explain>" --json' in text
    assert HANDOFF_WITHOUT_BRIDGE not in text and HANDOFF_IMAGE_ANALYSIS_HINT not in text

    unconfigured = build_attachment_handoff(files, bridge_available=False, image_analysis_configured=False)
    assert unconfigured.endswith(f"{HANDOFF_HEADER} {HANDOFF_WITHOUT_BRIDGE} {HANDOFF_IMAGE_ANALYSIS_HINT}")
    assert HANDOFF_IMAGES_INSPECT not in unconfigured and "inspect-image" not in unconfigured
    assert build_attachment_handoff([], bridge_available=False, image_analysis_configured=True) == ""


class _Request:
    app = {}
    headers = {}

    def __init__(self, payload):
        self._payload = payload
        self._items = {}

    async def json(self):
        return self._payload

    # api_chat parks the attachment citations on the request (``request['file_citations']``).
    def __setitem__(self, key, value):
        self._items[key] = value

    def __getitem__(self, key):
        return self._items[key]

    def get(self, key, default=None):
        return self._items.get(key, default)


def _image_chat_fixture(monkeypatch, tmp_path, *, provider, configured=True, bridge=False):
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

    trusted = {"connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1"}}} if bridge else {}
    state = (
        {"configured": True, "reason": None, "model": "gpt-5.4", "config_path": "/root/.efp/inspect-image.yaml"}
        if configured
        else {"configured": False, "reason": "image analysis is off for this profile", "model": None, "config_path": None}
    )
    monkeypatch.setattr(runtime_api, "_extract_trusted_control_plane_metadata", lambda request, data: dict(trusted))
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", _fake_run_chat_via_execution_bus)
    monkeypatch.setattr(runtime_api, "inject_context", lambda **kwargs: (kwargs["message"], "ok", []))
    monkeypatch.setattr(runtime_api.global_config, "_config", {"llm": {"api_key": "k", "model": "gpt-5.6-terra", "provider": provider}}, raising=False)
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    monkeypatch.setattr(runtime_api.session_manager, "get_session", lambda _sid: asyncio.sleep(0, result={"history": [{}], "channel": "", "metadata": {}}))
    monkeypatch.setattr(runtime_api.runtime_session_artifacts, "save_session", lambda **kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(runtime_api, "get_metadata", lambda file_id: metadata_map[file_id])
    monkeypatch.setattr(storage, "get_file_path", lambda file_id: file_map[file_id])
    monkeypatch.setattr(runtime_api, "get_image_analysis_state", lambda: dict(state))
    return runtime_api, captured, one, two


@pytest.mark.asyncio
async def test_api_chat_hands_images_to_inspect_image_for_a_copilot_profile(monkeypatch, tmp_path):
    _switch_off(monkeypatch)
    runtime_api, captured, one, two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot")

    response = await runtime_api.api_chat(_Request({"message": "what is on these?", "session_id": "s1", "attachments": ["f1", "f2"]}))
    assert response.status == 200
    # Nothing is inlined for a model that cannot see it...
    assert captured["attached_images"] is None
    assert captured["message"] == "what is on these?"
    # ...the agent is told where the files are and how to read them.
    handoff = captured["transient_model_message"]
    assert f"- {one} (image/png, 3 bytes, attached as \"f1-shot.png\"; not readable by the chat model)" in handoff
    assert f"- {two} (image/png, 3 bytes, attached as \"f2-shot.png\"; not readable by the chat model)" in handoff
    assert "inspect-image inspect --image <path>" in handoff
    assert handoff.endswith(f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITHOUT_BRIDGE}")
    assert HANDOFF_WITHOUT_BRIDGE not in handoff and HANDOFF_IMAGE_ANALYSIS_HINT not in handoff
    assert captured["attachments"] == ["f1", "f2"]


@pytest.mark.asyncio
async def test_api_chat_stream_hands_images_to_inspect_image_with_the_bridge_as_the_fallback(monkeypatch, tmp_path):
    _switch_off(monkeypatch)
    runtime_api, captured, one, _two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", bridge=True)

    class _FakeStreamResponse:
        def __init__(self, *args, **kwargs):
            self.status = kwargs.get("status", 200)
            self.headers = kwargs.get("headers", {})

        async def prepare(self, _request):
            return None

        async def write(self, _data):
            return None

        async def write_eof(self):
            return None

    monkeypatch.setattr(runtime_api.web, "StreamResponse", _FakeStreamResponse)

    await runtime_api.api_chat_stream(_Request({"message": "read it", "session_id": "s1", "attachments": ["f1"]}))
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert str(one) in handoff
    # inspect-image first; Productivity Suite through the bridge only when it fails.
    assert handoff.endswith(f"{HANDOFF_HEADER} {HANDOFF_IMAGES_INSPECT} {HANDOFF_IMAGES_FALLBACK_WITH_BRIDGE}")
    assert HANDOFF_WITH_BRIDGE not in handoff


@pytest.mark.asyncio
async def test_api_chat_with_only_an_image_still_runs_and_names_the_missing_connector(monkeypatch, tmp_path):
    _switch_off(monkeypatch)
    runtime_api, captured, one, _two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot", configured=False)

    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f1"]}))
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"] is None
    handoff = captured["transient_model_message"]
    assert str(one) in handoff
    # No connector and no bridge: the files cannot be analyzed here, and the
    # block names the connector that would change that.
    assert handoff.endswith(f"{HANDOFF_HEADER} {HANDOFF_WITHOUT_BRIDGE} {HANDOFF_IMAGE_ANALYSIS_HINT}")
    assert "Image analysis connector" in handoff and "inspect-image" not in handoff


@pytest.mark.asyncio
async def test_api_chat_handoff_keeps_the_parse_failure_notice_for_other_files(monkeypatch, tmp_path):
    _switch_off(monkeypatch)
    runtime_api, captured, one, _two = _image_chat_fixture(monkeypatch, tmp_path, provider="github_copilot")

    async def _fake_ensure(**kwargs):
        return {"context_file_ids": [], "failures": [{"file_id": "csv_bad", "error": "bad csv"}]}

    monkeypatch.setattr(runtime_api, "_ensure_chat_attachment_context", _fake_ensure)

    response = await runtime_api.api_chat(_Request({"message": "", "session_id": "s1", "attachments": ["f1", "csv_bad"]}))
    # The image alone keeps the turn alive even though the CSV failed to parse...
    assert response.status == 200
    assert captured["message"] == "[image]"
    assert captured["attached_images"] is None
    transient = captured["transient_model_message"]
    # ...and the model hears about the failed file next to the handoff: the
    # attached-files block leads, the parse notice follows it.
    assert "csv_bad" in transient and "bad csv" in transient
    assert transient.startswith(ATTACHED_FILES_HEADER)
    assert transient.index("inspect-image inspect") < transient.index("bad csv")
    assert str(one) in transient


@pytest.mark.asyncio
async def test_api_chat_inlines_images_for_no_provider_by_default_and_for_any_with_the_switch(monkeypatch, tmp_path):
    # Default: no provider is sent image parts, AI Platform included; the
    # images go to the agent for inspect-image instead.
    for provider in ("ai_platform", "github_copilot"):
        _switch_off(monkeypatch)
        runtime_api, captured, one, two = _image_chat_fixture(monkeypatch, tmp_path, provider=provider)
        response = await runtime_api.api_chat(_Request({"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}))
        assert response.status == 200, provider
        assert captured["attached_images"] is None, provider
        handoff = captured["transient_model_message"]
        assert f"- {one} (image/png, 3 bytes, attached as \"f1-shot.png\"; not readable by the chat model)" in handoff
        assert HANDOFF_IMAGES_INSPECT in handoff, provider

    # The deployment switch inlines them for any provider, under either name,
    # and then nothing in the block points at inspect-image.
    for provider, switch in (
        ("ai_platform", CHAT_VISION_ENV),
        ("github_copilot", CHAT_VISION_ENV),
        ("ai_platform", COPILOT_VISION_VIA_CHAT_ENV),
        ("github_copilot", COPILOT_VISION_VIA_CHAT_ENV),
    ):
        _switch_off(monkeypatch)
        monkeypatch.setenv(switch, "1")
        runtime_api, captured, one, two = _image_chat_fixture(monkeypatch, tmp_path, provider=provider)
        response = await runtime_api.api_chat(_Request({"message": "compare", "session_id": "s1", "attachments": ["f1", "f2"]}))
        assert response.status == 200, (provider, switch)
        assert len(captured["attached_images"]) == 2, (provider, switch)
        assert captured["attached_images"][0].startswith("data:image/png;base64,")
        listed = captured["transient_model_message"]
        assert f"- {one} (image/png, 3 bytes, attached as \"f1-shot.png\"; sent to the model as an image)" in listed
        assert f"- {two} (image/png, 3 bytes, attached as \"f2-shot.png\"; sent to the model as an image)" in listed
        assert HANDOFF_HEADER not in listed and "inspect-image" not in listed, (provider, switch)


# --- the file parser's vision path -------------------------------------------------


@pytest.mark.asyncio
async def test_parse_image_with_vision_obeys_the_switch(monkeypatch, tmp_path):
    from src.gateway import runtime_chat
    from src.utils.file_parser import image as image_module
    from src.utils.file_parser.models import ParseResult

    picture = tmp_path / "abc_shot.png"
    picture.write_bytes(b"not really a png")
    chat_calls = []
    ocr_calls = []

    async def _fake_run_runtime_chat(**kwargs):
        chat_calls.append(kwargs)
        return {"response": "a login form"}

    async def _fake_ocr(file_path, options):
        ocr_calls.append(file_path)
        return ParseResult(success=True, content_type="image/png", file_id="abc", filename="abc_shot.png", markdown="OCR TEXT")

    monkeypatch.setattr(runtime_chat, "run_runtime_chat", _fake_run_runtime_chat)
    monkeypatch.setattr(image_module, "get_image_for_llm", lambda file_path, constraints=None: "data:image/jpeg;base64,AAAA")
    monkeypatch.setattr(image_module, "validate_image_for_llm", lambda file_path, constraints: (True, None))
    monkeypatch.setattr(image_module, "parse_image_with_ocr", _fake_ocr)

    # Switch off (the default): the image never goes to the chat model, whoever asks.
    _switch_off(monkeypatch)
    result = await image_module.parse_image_with_vision(str(picture), {"vision_enabled": True})
    assert result.success is False
    assert result.error == image_module.CHAT_VISION_OFF_ERROR and "EFP_CHAT_VISION" in result.error
    assert chat_calls == []
    # parse_image asked for vision takes the path without the chat model.
    result = await image_module.parse_image(str(picture), {"vision_enabled": True})
    assert result.success is True and result.markdown == "OCR TEXT"
    assert chat_calls == [] and ocr_calls == [str(picture)]

    # Switch on, under either name: the vision path runs as before.
    for switch in (CHAT_VISION_ENV, COPILOT_VISION_VIA_CHAT_ENV):
        _switch_off(monkeypatch)
        monkeypatch.setenv(switch, "1")
        chat_calls.clear()
        ocr_calls.clear()
        result = await image_module.parse_image(str(picture), {"vision_enabled": True})
        assert result.success is True and result.markdown == "## Image Analysis\n\na login form", switch
        assert [call["attached_images"] for call in chat_calls] == [["data:image/jpeg;base64,AAAA"]], switch
        assert ocr_calls == []
        # Without the option nobody asked for vision: still the OCR path.
        chat_calls.clear()
        result = await image_module.parse_image(str(picture), {})
        assert result.markdown == "OCR TEXT" and chat_calls == [], switch


# --- instructions and boot --------------------------------------------------------


def test_native_projection_appends_the_image_analysis_instructions():
    on = project_canonical_for_runtime(_profile(), "native")
    assert on["instruction_texts"] == [RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    assert RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS not in on["instruction_texts"]
    # The section itself travels on untouched.
    assert on["image_analysis"] == _image_analysis()
    assert "instruction_texts" not in project_canonical_for_runtime(_profile(), "opencode")
    assert "instruction_texts" not in project_canonical_for_runtime(_profile(enabled=False), "native")
    assert "instruction_texts" not in project_canonical_for_runtime(_profile(password=""), "native")
    # Whatever the chat provider is: the connector decides, llm does not.
    chat = project_canonical_for_runtime(_profile(provider="ai_platform"), "native")
    assert chat["instruction_texts"] == [RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    assert "instruction_texts" not in project_canonical_for_runtime(_llm_only_ai_platform_profile(), "native")
    # Next to the connector CLIs it comes second and is added once.
    both = _profile()
    both["github"] = {"enabled": True, "api_token": "t"}
    both["instruction_texts"] = ["Be brief.", RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS]
    assert project_canonical_for_runtime(both, "native")["instruction_texts"] == [
        "Be brief.",
        RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS,
        RUNTIME_PROFILE_CLI_TOOL_INSTRUCTIONS,
    ]
    # One line that names the CLI, the connector and both fallbacks.
    text = RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS
    assert "\n" not in text
    assert text.startswith("Images are read through the inspect-image CLI on AI Platform (the Image analysis connector);")
    assert 'inspect-image inspect --image <path> --prompt "<what to read or explain>" --json' in text
    assert "never run inspect-image auth login" in text
    assert "Productivity Suite through the Local bridge" in text
    assert text.endswith("the Image analysis connector under Portal > Connectors needs checking.")


def test_the_instruction_predicate_and_the_cli_projection_agree_on_configured():
    cases = [
        _profile(),
        _profile(provider="ai_platform"),
        _profile(enabled=False),
        _profile(enabled="true"),
        _profile(enabled="off"),
        _profile(username=""),
        _profile(password="  "),
        _profile(usercase=None),
        _llm_only_ai_platform_profile(),
        {},
        {"image_analysis": "yes"},
        {"image_analysis": {"enabled": True}},
    ]
    no_chat = _profile()
    no_chat["image_analysis"]["ai_platform"]["chat"]["host"] = ""
    no_ib2b = _profile()
    del no_ib2b["image_analysis"]["ai_platform"]["ib2b"]
    cases += [no_chat, no_ib2b]
    for case in cases:
        settings, _reason = iic.resolve_image_analysis_settings(case)
        assert _has_image_analysis_config(case) is (settings is not None), case
    assert _has_image_analysis_config(_profile()) is True
    assert _has_image_analysis_config(_profile(enabled="true")) is True


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


def test_the_overlay_keeps_the_image_analysis_section(tmp_path, monkeypatch):
    overlay = _profile()
    # Keys outside the section's shape are not the connector's and are dropped.
    overlay["image_analysis"]["provider"] = "github_copilot"
    overlay["image_analysis"]["ai_platform"]["responses"] = {"host": "https://chat.int", "uri": "/v1/responses"}
    overlay["image_analysis"]["ai_platform"]["auth"]["token"] = "stale-jwt"
    _boot_with(tmp_path, monkeypatch, overlay)

    cfg = config_module.config
    assert "image_analysis" in cfg.get_managed_overlay_meta()["managed_sections"]
    assert cfg.get_effective_config()["image_analysis"] == _image_analysis()
    assert "image_analysis" in Config.MANAGED_OVERLAY_SECTIONS
    assert Config.PORTAL_MANAGED_FIELD_TREE["image_analysis"] == {
        "enabled": True,
        "model": True,
        "ai_platform": {
            "chat": {"host": True, "uri": True},
            "ib2b": {"host": True, "uri": True},
            "auth": {"username": True, "password": True, "usercase": True, "trust_token_header": True, "tracking_prefix": True},
        },
    }
    # The projection came from the section and added the instructions.
    assert RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS in cfg.get_effective_config()["instruction_texts"]


@pytest.mark.asyncio
async def test_bootstrap_projects_inspect_image_config_and_exports_the_account(tmp_path, monkeypatch):
    from src.gateway import server as gateway_server

    config_path = _boot_with(tmp_path, monkeypatch, _profile())

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
    # The runtime's own config is never rewritten by the projection, and the
    # password is in neither file.
    assert "inspect_image" not in config_path.read_text(encoding="utf-8")
    assert "pw" not in inspect_config.read_text(encoding="utf-8").replace("${EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD}", "")

    # GET /ready reports it, without the account.
    response = await gateway_server.Gateway.handle_ready(None, None)
    body = json.loads(response.body)
    assert response.status == 200
    assert body["image_analysis"] == {"configured": True, "reason": None, "model": "gpt-5.6-sol", "config_path": str(inspect_config)}
    assert "pw" not in json.dumps(body["image_analysis"]) and "password" not in json.dumps(body["image_analysis"])


def test_bootstrap_without_image_analysis_exports_nothing(tmp_path, monkeypatch):
    (tmp_path / "inspect-image.yaml").write_text("stale: true\n", encoding="utf-8")
    _boot_with(tmp_path, monkeypatch, _profile(enabled=False))

    assert not (tmp_path / "inspect-image.yaml").exists()
    assert os.environ.get("INSPECT_IMAGE_CONFIG", "") == ""
    assert os.environ.get("EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD", "") == ""
    state = config_module.get_image_analysis_state()
    assert state["configured"] is False
    assert state["reason"] == "image analysis is off for this profile"


def test_bootstrap_ignores_the_chat_provider_account_for_image_analysis(tmp_path, monkeypatch):
    """An AI Platform chat profile does not get image analysis from its chat account."""
    _boot_with(tmp_path, monkeypatch, _llm_only_ai_platform_profile())

    assert not (tmp_path / "inspect-image.yaml").exists()
    assert os.environ.get("INSPECT_IMAGE_CONFIG", "") == ""
    assert config_module.get_image_analysis_state() == {
        "configured": False,
        "reason": "image analysis is off for this profile",
        "model": None,
        "config_path": None,
    }
    assert RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS not in (config_module.config.get_effective_config().get("instruction_texts") or [])


def test_bootstrap_reports_a_failed_image_analysis_projection(tmp_path, monkeypatch):
    def _broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(iic, "apply_image_analysis_projection", _broken)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  provider: openai\n  model: gpt-4o\nproxy:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv(
        "EFP_PROFILE_CONFIG",
        json.dumps({"runtime_profile_id": "rp_vision", "name": "vision", "revision": 3, "config": _profile()}),
    )
    monkeypatch.setattr(config_module, "_profile_boot_state", {"completed": False, "ready": False, "error": None})
    monkeypatch.setattr(config_module, "_image_analysis_state", dict(config_module._image_analysis_state))
    monkeypatch.setattr(profile_config_module, "apply_runtime_profile_external_config", lambda overlay, **kwargs: None)
    cfg = Config(str(config_path))
    monkeypatch.setattr(config_module, "config", cfg)

    assert config_module.bootstrap_profile_boot() is False
    assert config_module.get_profile_boot_state()["error"] == "Failed to project image analysis settings: disk full"
    state = config_module.get_image_analysis_state()
    assert state["configured"] is False and state["reason"] == "projection failed: disk full"
