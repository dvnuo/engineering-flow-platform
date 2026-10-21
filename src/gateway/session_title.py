"""Generated session titles: one small model call after the first completed turn.

The Portal lists sessions by name, and until now that name was the first user
message cut at 30 characters: it said what was asked, not what the conversation
is about. After the first turn that produced an answer, the runtime asks the
member's own provider (same credentials and proxy as the chat, a cheap model)
for a short title and stores it as ``generated_session_name`` in the session
metadata. A manual rename (``custom_session_name``) always wins and is never
overwritten; the generated name is written once and can be regenerated on
request (``POST /api/sessions/{id}/title/generate``).

Everything here is best effort: a timeout or a provider error leaves the
session as it was, and nothing blocks or fails the chat turn that triggered it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import inspect
import logging
import re
from typing import Any, Callable, Mapping, Optional

from src.config import config
from src.efp_runtime.llm.adapter import DefaultLLMEventAdapter
from src.efp_runtime.llm.events import LLMEventType
from src.efp_runtime.llm.request import PreparedProviderRequest, ProviderRequest, RequestMessage, RequestMessagePart
from src.efp_runtime.loop.provider import RuntimeRequest
from src.efp_runtime.loop.runner import LoopStatus
from src.efp_runtime.session.gateway_facade import get_runtime_session_store
from src.efp_runtime.session.models import MessagePartType, MessageRole
from src.efp_runtime.session.search import TASK_SESSION_ID_PREFIXES
from src.runtime.portal_session_metadata_client import publish_session_title

logger = logging.getLogger(__name__)

CUSTOM_NAME_KEY = "custom_session_name"
GENERATED_NAME_KEY = "generated_session_name"
GENERATED_NAME_AT_KEY = "generated_session_name_at"
GENERATED_NAME_MODEL_KEY = "generated_session_name_model"

# The prompt asks for 40 characters; the hard cut after cleaning leaves room
# for a model that counts differently, and the rename endpoint's 120 is the
# ceiling for any name.
TITLE_MAX_CHARS = 60
# Of each side of the first turn. A title needs the gist, not the transcript.
TITLE_INPUT_MAX_CHARS = 1500
TITLE_TIMEOUT_SECONDS = 10.0
TITLE_TASK_TYPE = "session_title"

# The cheap model per provider family; each id is one the chat path already
# accepts (src/efp_runtime/llm/models.py). ``llm.title_model`` in the runtime
# config overrides both.
TITLE_MODELS = {
    "github_copilot": "gpt-6-luna",
    "ai_platform": "gpt-5.6-luna",
}

TITLE_INSTRUCTIONS = (
    "You name chat sessions for a list of past conversations. "
    "Given the first message of a conversation and the assistant's answer, reply with a short title.\n"
    "Rules:\n"
    "- A noun phrase of at most 40 characters (about 20 characters for Chinese, Japanese or Korean).\n"
    "- The same language as the user's message.\n"
    "- Name the topic or task, not the fact that it was asked or answered.\n"
    "- No quotes, no trailing period, no prefix such as 'Title:' or 'Session about'.\n"
    "Reply with the title only."
)

ProviderBuilder = Callable[..., Any]

_TITLE_PREFIX = re.compile(r"^(?:title|session title|session name|name|标题)\s*[:：]\s*", re.IGNORECASE)
_QUOTES = "\"'`“”‘’「」『』《》"
_TRAILING_PUNCTUATION = ".。!！"

_inflight: set[str] = set()


class TitleGenerationError(RuntimeError):
    """The title could not be generated; ``status_code`` is what an endpoint reports."""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def clean_title(raw: Any) -> str:
    """The model's reply as a name: first line, no quotes, no prefix, no period, bounded."""

    text = ""
    for line in str(raw or "").splitlines():
        candidate = line.strip()
        if candidate:
            text = candidate
            break
    if not text:
        return ""
    text = _TITLE_PREFIX.sub("", text).strip()
    text = text.strip(_QUOTES).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(_TRAILING_PUNCTUATION).strip()
    if len(text) > TITLE_MAX_CHARS:
        text = text[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return text


def title_model_for_config() -> str:
    """The cheap model for the configured provider family (``llm.title_model`` overrides)."""

    llm = config.llm if isinstance(config.llm, Mapping) else {}
    override = str(llm.get("title_model") or "").strip()
    if override:
        return override
    provider = str(llm.get("provider") or "").strip().lower()
    family = "ai_platform" if provider in {"ai_platform", "ai-platform"} else "github_copilot"
    return TITLE_MODELS[family]


def _excerpt(text: str) -> str:
    collapsed = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(collapsed) <= TITLE_INPUT_MAX_CHARS:
        return collapsed
    return collapsed[:TITLE_INPUT_MAX_CHARS].rstrip() + "…"


def build_title_request(session_id: str, *, user_text: str, assistant_text: str) -> RuntimeRequest:
    """The one-shot provider request: instructions plus the first turn's excerpts."""

    prompt = (
        "User message:\n"
        f"{_excerpt(user_text)}\n\n"
        "Assistant answer:\n"
        f"{_excerpt(assistant_text)}\n\n"
        "Title:"
    )
    provider_request = ProviderRequest(
        messages=[
            RequestMessage(role="system", parts=[RequestMessagePart(type="text", text=TITLE_INSTRUCTIONS)]),
            RequestMessage(role="user", parts=[RequestMessagePart(type="text", text=prompt)]),
        ],
        metadata={"task_type": TITLE_TASK_TYPE},
    )
    return RuntimeRequest(
        session_id=session_id,
        messages=[],
        iteration=0,
        max_iterations=1,
        metadata={"task_type": TITLE_TASK_TYPE},
        provider_request=provider_request,
        prepared_request=PreparedProviderRequest(request=provider_request),
    )


def _message_text(message: Any) -> str:
    parts = getattr(message, "parts", None) or []
    chunks = [
        str(part.text)
        for part in parts
        if getattr(part, "type", None) is MessagePartType.TEXT and getattr(part, "text", None)
    ]
    return "\n".join(chunks).strip()


def _user_text(message: Any) -> str:
    # A turn whose model prompt was expanded (attachment context ahead of the
    # question) keeps the member's own words in metadata; the title should
    # come from those, not from the composed prompt.
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, Mapping):
        original = metadata.get("original_user_message")
        if isinstance(original, str) and original.strip():
            return original.strip()
    return _message_text(message)


def first_turn_texts(messages: Any) -> tuple[str, str]:
    """The first user message and the first assistant text after it ("" when missing)."""

    user_text = ""
    for message in messages or []:
        role = getattr(message, "role", None)
        if not user_text:
            if role is MessageRole.USER:
                user_text = _user_text(message)
            continue
        if role is MessageRole.ASSISTANT:
            assistant_text = _message_text(message)
            if assistant_text:
                return user_text, assistant_text
    return user_text, ""


async def _invoke_for_text(provider: Any, request: RuntimeRequest) -> tuple[str, dict[str, Any]]:
    """Run one provider call and return (text, usage), whichever shape the provider answers in."""

    output = provider.invoke(request)
    if inspect.isawaitable(output):
        output = await output
    if isinstance(output, Mapping):
        events: Any = DefaultLLMEventAdapter().normalize_response(output)
    else:
        events = output

    deltas: list[str] = []
    ended_text = ""
    usage: dict[str, Any] = {}

    def consume(event: Any) -> None:
        nonlocal ended_text, usage
        event_type = getattr(event, "type", None)
        if event_type in (LLMEventType.PROVIDER_ERROR, LLMEventType.ERROR):
            raise TitleGenerationError(str(getattr(event, "error", None) or "provider error"))
        if event_type is LLMEventType.TEXT_DELTA:
            deltas.append(str(getattr(event, "delta", "") or ""))
        elif event_type is LLMEventType.TEXT_END:
            ended_text = str(getattr(event, "text", "") or "") or ended_text
        event_usage = getattr(event, "usage", None)
        if isinstance(event_usage, Mapping) and event_usage:
            usage = dict(event_usage)

    if hasattr(events, "__aiter__"):
        async for event in events:
            consume(event)
    else:
        for event in events or []:
            consume(event)
    return ("".join(deltas) or ended_text), usage


def _record_usage(model: str, usage: Mapping[str, Any], session_id: str) -> None:
    if not usage:
        return
    try:
        from src.sessions.usage import usage_tracker

        llm = config.llm if isinstance(config.llm, Mapping) else {}
        usage_tracker.record_usage(
            provider=str(llm.get("provider") or "github_copilot"),
            model=model,
            input_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            session_id=session_id,
            task_type=TITLE_TASK_TYPE,
        )
    except Exception:
        logger.debug("session title usage was not recorded", exc_info=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def generate_session_title(
    session_id: str,
    *,
    provider_builder: ProviderBuilder,
    execution_metadata: Mapping[str, Any] | None = None,
    store: Any = None,
    force: bool = False,
    timeout: float = TITLE_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Generate and store a title for ``session_id``; the stored name, or None when nothing changed.

    None means the session keeps the name it has: a manual name exists, or a
    generated one does and ``force`` is false. A missing session raises
    ``KeyError``; a session without a completed first turn, a provider failure
    and a timeout raise ``TitleGenerationError``.
    """

    store = store or get_runtime_session_store()
    session = store.get_session(session_id)  # KeyError when unknown
    metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
    if _non_empty(metadata.get(CUSTOM_NAME_KEY)):
        return None
    if not force and _non_empty(metadata.get(GENERATED_NAME_KEY)):
        return None

    user_text, assistant_text = first_turn_texts(session.messages)
    if not user_text or not assistant_text:
        raise TitleGenerationError("The session has no completed turn to name yet.", status_code=422)

    provider, model = provider_builder(title_model_for_config(), execution_metadata=execution_metadata)
    request = build_title_request(session_id, user_text=user_text, assistant_text=assistant_text)
    try:
        text, usage = await asyncio.wait_for(_invoke_for_text(provider, request), timeout)
    except asyncio.TimeoutError as exc:
        raise TitleGenerationError(f"Title generation timed out after {timeout:g}s.", status_code=504) from exc
    except TitleGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - any transport failure is a 502 here
        raise TitleGenerationError(f"Title generation failed: {exc}") from exc

    title = clean_title(text)
    if not title:
        raise TitleGenerationError("The model returned no usable title.")

    # A rename that landed while the model was thinking wins.
    latest = store.get_session(session_id)
    latest_metadata = latest.metadata if isinstance(latest.metadata, Mapping) else {}
    if _non_empty(latest_metadata.get(CUSTOM_NAME_KEY)):
        return None
    store.update_session(
        session_id,
        metadata={
            GENERATED_NAME_KEY: title,
            GENERATED_NAME_AT_KEY: _now_iso(),
            GENERATED_NAME_MODEL_KEY: str(model),
        },
    )
    _record_usage(str(model), usage, session_id)
    return title


def should_generate_after_turn(
    session_id: str,
    *,
    result: Any,
    execution_metadata: Mapping[str, Any] | None = None,
    store: Any = None,
) -> bool:
    """Only the first completed chat turn with an answer, in a session not yet named."""

    if not session_id or str(session_id).startswith(TASK_SESSION_ID_PREFIXES):
        return False
    metadata = execution_metadata if isinstance(execution_metadata, Mapping) else {}
    if any(metadata.get(key) for key in ("portal_task_id", "task_id")):
        return False
    if getattr(result, "status", None) != LoopStatus.COMPLETED:
        return False
    if not _message_text(getattr(result, "final_assistant_message", None)):
        return False
    store = store or get_runtime_session_store()
    try:
        summary = store.get_session_summary(session_id)
    except KeyError:
        return False
    if summary.user_message_count != 1:
        return False
    if summary.custom_name or getattr(summary, "generated_name", None):
        return False
    return True


def schedule_session_title(
    session_id: str,
    *,
    result: Any,
    provider_builder: ProviderBuilder,
    execution_metadata: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    store: Any = None,
) -> Optional[asyncio.Task]:
    """Start the title call in the background after a turn; the task, or None when not due."""

    if not should_generate_after_turn(
        session_id,
        result=result,
        execution_metadata=execution_metadata,
        store=store,
    ):
        return None
    if session_id in _inflight:
        return None
    _inflight.add(session_id)
    return asyncio.create_task(
        _generate_in_background(
            session_id,
            provider_builder=provider_builder,
            execution_metadata=execution_metadata,
            agent_id=agent_id,
            store=store,
        ),
        name=f"session-title:{session_id}",
    )


async def _generate_in_background(
    session_id: str,
    *,
    provider_builder: ProviderBuilder,
    execution_metadata: Mapping[str, Any] | None,
    agent_id: str | None,
    store: Any,
) -> None:
    try:
        title = await generate_session_title(
            session_id,
            provider_builder=provider_builder,
            execution_metadata=execution_metadata,
            store=store,
        )
        if title:
            logger.info("Session %s titled: %s", session_id, title)
            if agent_id:
                await publish_session_title(agent_id=agent_id, session_id=session_id, title=title)
    except TitleGenerationError as exc:
        logger.warning("Session %s keeps its derived name: %s", session_id, exc.message)
    except Exception:
        logger.warning("Session %s keeps its derived name (title generation failed)", session_id, exc_info=True)
    finally:
        _inflight.discard(session_id)


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "CUSTOM_NAME_KEY",
    "GENERATED_NAME_KEY",
    "TITLE_MODELS",
    "TitleGenerationError",
    "build_title_request",
    "clean_title",
    "first_turn_texts",
    "generate_session_title",
    "schedule_session_title",
    "should_generate_after_turn",
    "title_model_for_config",
]
