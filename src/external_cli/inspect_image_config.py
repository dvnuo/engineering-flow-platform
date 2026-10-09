"""Project the profile's image-analysis settings into the inspect-image CLI config.

Image analysis always runs on AI Platform through the inspect-image CLI
(GitHub Copilot's models no longer accept images). When the profile turned it
on (``llm.vision.enabled``) or the chat provider itself is AI Platform, and the
AI Platform account plus the deployment-managed chat and iB2B endpoints are
present, this writes a config file of inspect-image's own next to the runtime
config and exports:

- ``INSPECT_IMAGE_CONFIG``: the file; inspect-image prefers it over
  ``EFP_CONFIG``, which this runtime exports for the other CLIs.
- ``EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME`` / ``_PASSWORD`` / ``_USERCASE``:
  referenced from the file as ``${NAME}``, so the password never lands on disk.

inspect-image exchanges the short-lived iB2B JWT itself and keeps it in
``token_file``; it rewrites its config file on every refresh, which is why it
must not share the runtime's own, never-rewritten ``config.yaml``.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from ruamel.yaml import YAML

from src.efp_runtime.llm.models import AI_PLATFORM_MODEL_IDS, DEFAULT_AI_PLATFORM_MODEL
from src.efp_runtime.llm.vision import normalize_provider_id
from src.utils.file_parser.validators import resolve_max_upload_mb
from src.utils.proxy_plan import build_proxy_plan, hostname_of

INSPECT_IMAGE_CONFIG_ENV = "INSPECT_IMAGE_CONFIG"
USERNAME_ENV = "EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME"
PASSWORD_ENV = "EFP_INSPECT_IMAGE_AI_PLATFORM_PASSWORD"
USERCASE_ENV = "EFP_INSPECT_IMAGE_AI_PLATFORM_USERCASE"
MANAGED_ENV_VARS = (INSPECT_IMAGE_CONFIG_ENV, USERNAME_ENV, PASSWORD_ENV, USERCASE_ENV)
CONFIG_FILE_NAME = "inspect-image.yaml"
TOKEN_FILE_NAME = "inspect-image-ai-platform-token"
DEFAULT_CHAT_URI = "/v1/api/v1/chat/completions"
DEFAULT_IB2B_URI = "/dsp/rest-sts/DSP_iB2B/iB2B_tokenTranslator_v2?_action=translate"
DEFAULT_TRUST_TOKEN_HEADER = "X-XXXX-E2E-Trust-Token"
DEFAULT_TRACKING_PREFIX = "EFP"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

_yaml = YAML()
_yaml.default_flow_style = False


@dataclass(frozen=True)
class ImageAnalysisSettings:
    model: str
    chat_host: str
    chat_uri: str
    ib2b_host: str
    ib2b_uri: str
    username: str
    password: str
    usercase: str
    trust_token_header: str
    tracking_prefix: str
    # The proxy the Model provider connector was assigned, in inspect-image's
    # api.proxy vocabulary: "" follows the environment, "none" connects
    # directly, a URL names the proxy (src/utils/proxy_plan.py).
    proxy: str = ""


@dataclass(frozen=True)
class ImageAnalysisProjection:
    configured: bool
    reason: str | None = None
    model: str | None = None
    config_path: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "reason": self.reason,
            "model": self.model,
            "config_path": self.config_path,
        }


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE_VALUES


def _text(mapping: Mapping[str, Any], key: str) -> str:
    return str(mapping.get(key) or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def max_image_bytes() -> int:
    """inspect-image's size limit: whatever the upload API accepts.

    Every image the handoff names came through the attachment API, which
    caps a file at EFP_MAX_UPLOAD_MB (25 MiB by default), so inspect-image
    must take the same size or an upload that succeeded is handed to a
    command that then refuses it. inspect-image's own default is 3 MiB.
    """
    return resolve_max_upload_mb() * 1024 * 1024


def coerce_vision_model(model: Any) -> str:
    """A model AI Platform serves; anything else lands on the AI Platform default."""
    text = str(model or "").strip()
    if "/" in text:
        text = text.split("/", 1)[1].strip()
    return text if text in AI_PLATFORM_MODEL_IDS else DEFAULT_AI_PLATFORM_MODEL


def image_analysis_requested(llm: Mapping[str, Any] | None) -> bool:
    """The member turned image analysis on, or chats on AI Platform already."""
    llm = _mapping(llm)
    vision = _mapping(llm.get("vision"))
    return _flag(vision.get("enabled")) or normalize_provider_id(llm.get("provider")) == "ai_platform"


def resolve_image_analysis_settings(
    config: Mapping[str, Any] | None,
) -> tuple[ImageAnalysisSettings | None, str | None]:
    """Settings for inspect-image, or (None, why not)."""
    llm = _mapping(_mapping(config).get("llm"))
    if not image_analysis_requested(llm):
        return None, "image analysis is off for this profile"
    ai_platform = _mapping(llm.get("ai_platform"))
    auth = _mapping(ai_platform.get("auth"))
    chat = _mapping(ai_platform.get("chat"))
    ib2b = _mapping(ai_platform.get("ib2b"))
    username, password, usercase = _text(auth, "username"), _text(auth, "password"), _text(auth, "usercase")
    if not (username and password and usercase):
        return None, "AI Platform username, password, and usercase are required"
    chat_host = _text(chat, "host").rstrip("/")
    if not chat_host:
        return None, "AI Platform chat endpoint is not configured"
    ib2b_host = _text(ib2b, "host").rstrip("/")
    if not ib2b_host:
        return None, "AI Platform iB2B endpoint is not configured"
    vision = _mapping(llm.get("vision"))
    model = _text(vision, "model")
    if not model and normalize_provider_id(llm.get("provider")) == "ai_platform":
        model = _text(llm, "model")
    proxy = build_proxy_plan(_mapping(config).get("proxy")).choice("llm", host=hostname_of(chat_host)).setting
    return (
        ImageAnalysisSettings(
            model=coerce_vision_model(model),
            chat_host=chat_host,
            chat_uri=_text(chat, "uri") or DEFAULT_CHAT_URI,
            ib2b_host=ib2b_host,
            ib2b_uri=_text(ib2b, "uri") or DEFAULT_IB2B_URI,
            username=username,
            password=password,
            usercase=usercase,
            trust_token_header=_text(auth, "trust_token_header") or DEFAULT_TRUST_TOKEN_HEADER,
            tracking_prefix=_text(auth, "tracking_prefix") or DEFAULT_TRACKING_PREFIX,
            proxy=proxy,
        ),
        None,
    )


def build_inspect_image_config(settings: ImageAnalysisSettings, *, token_file: Path) -> dict[str, Any]:
    """The YAML inspect-image reads; credentials are environment references."""
    inspect_image: dict[str, Any] = {
        "provider": "ai_platform",
        "defaults": {"model": settings.model},
        "limits": {"max_image_bytes": max_image_bytes()},
    }
    if settings.proxy:
        # inspect-image reads api.proxy the way the other CLIs read an
        # instance's proxy field; an empty value is left out so the file stays
        # what it was for a profile that follows the environment.
        inspect_image["api"] = {"proxy": settings.proxy}
    return {
        "version": 1,
        "inspect_image": inspect_image,
        "ai_platform": {
            "chat": {"host": settings.chat_host, "uri": settings.chat_uri},
            "ib2b": {"host": settings.ib2b_host, "uri": settings.ib2b_uri},
            "auth": {
                "username": "${" + USERNAME_ENV + "}",
                "password": "${" + PASSWORD_ENV + "}",
                "usercase": "${" + USERCASE_ENV + "}",
                "trust_token_header": settings.trust_token_header,
                "tracking_prefix": settings.tracking_prefix,
                "token_file": str(token_file),
            },
        },
    }


def inspect_image_config_path(config_dir: Path) -> Path:
    return Path(config_dir) / CONFIG_FILE_NAME


def inspect_image_token_path(config_dir: Path) -> Path:
    return Path(config_dir) / "tmp" / TOKEN_FILE_NAME


def apply_image_analysis_projection(
    config: Mapping[str, Any] | None,
    *,
    config_dir: Path,
) -> ImageAnalysisProjection:
    """Write (or remove) inspect-image's config and return what to export."""
    config_path = inspect_image_config_path(config_dir)
    token_path = inspect_image_token_path(config_dir)
    settings, reason = resolve_image_analysis_settings(config)
    if settings is None:
        clear_image_analysis_projection(config_dir)
        return ImageAnalysisProjection(configured=False, reason=reason)
    payload = build_inspect_image_config(settings, token_file=token_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        _yaml.dump(payload, handle)
    _chmod_private(config_path)
    # inspect-image keeps the short-lived JWT in token_file; start from none so
    # a changed account never reuses a token issued for the previous one.
    _remove_file_if_exists(token_path)
    return ImageAnalysisProjection(
        configured=True,
        model=settings.model,
        config_path=str(config_path),
        env={
            INSPECT_IMAGE_CONFIG_ENV: str(config_path),
            USERNAME_ENV: settings.username,
            PASSWORD_ENV: settings.password,
            USERCASE_ENV: settings.usercase,
        },
    )


def clear_image_analysis_projection(config_dir: Path) -> None:
    _remove_file_if_exists(inspect_image_config_path(config_dir))
    _remove_file_if_exists(inspect_image_token_path(config_dir))


def export_image_analysis_env(
    projection: ImageAnalysisProjection,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Replace the managed variables in ``environ`` with the projection's."""
    env = os.environ if environ is None else environ
    for key in MANAGED_ENV_VARS:
        env.pop(key, None)
    env.update(projection.env)


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _remove_file_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
