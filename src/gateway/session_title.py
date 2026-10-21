"""Generated session titles: one small model call after the first answered turn.

The Portal lists sessions by name, and until now that name was the first user
message cut at 30 characters: it said what was asked, not what the conversation
is about. After a chat turn that produced an answer in a session that has no
name yet, the runtime asks the member's own provider (same credentials and
proxy as the chat; the family's cheap model at low reasoning effort, without
streaming) for a short title and stores it as ``generated_session_name`` in
the session metadata. A manual rename (``custom_session_name``) always wins
and is never overwritten; the generated name is written once and can be
regenerated on request (``POST /api/sessions/{id}/title/generate``).

Only the Portal's chat entry points opt in (``title_after_turn`` on
``run_runtime_chat`` / ``resume_runtime_chat``); tasks, Jira comments and
other callers of the chat primitive never pay for a title.

Everything here is best effort: a timeout or a provider error leaves the
session as it was, and nothing blocks or fails the chat turn that triggered it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from typing import Any, Callable, Mapping, Optional

from src.config import config
from src.efp_runtime.llm.adapter import DefaultLLMEventAdapter
from src.efp_runtime.llm.events import LLMEventType
from src.efp_runtime.llm.models import cheap_model_for_family, is_auto_model_id
from src.efp_runtime.llm.request import PreparedProviderRequest, ProviderRequest, RequestMessage, RequestMessagePart
from src.efp_runtime.loop.provider import RuntimeRequest
from src.efp_runtime.loop.runner import LoopStatus
from src.efp_runtime.session.gateway_facade import get_runtime_session_store
from src.efp_runtime.session.models import MessageRole
from src.efp_runtime.session.search import assistant_message_text, is_task_session_id, member_message_text
from src.runtime.portal_session_metadata_client import publish_session_title_in_background
from src.utils.truncate import truncate

logger = logging.getLogger(__name__)

CUSTOM_NAME_KEY = "custom_session_name"
GENERATED_NAME_KEY = "generated_session_name"

# The prompt asks for 40 characters; the hard cut after cleaning leaves room
# for a model that counts differently, and the rename endpoint's 120 is the
# ceiling for any name.
TITLE_MAX_CHARS = 60
# Of each side of the first turn. A title needs the gist, not the transcript.
TITLE_INPUT_MAX_CHARS = 1500
# A low-effort, non-streaming call for a ten-token reply; a provider that is
# slower than this keeps the derived name.
TITLE_TIMEOUT_SECONDS = 20.0
TITLE_REASONING_EFFORT = "low"
TITLE_TASK_TYPE = "session_title"

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

_TITLE_PREFIX = re.compile(r"^(?:title|session title|session name|name|\u6807\u9898)\s*[:\uff1a]\s*", re.IGNORECASE)
_QUOTES = "\"'`\u201c\u201d\u2018\u2019\u300c\u300d\u300e\u300f\u300a\u300b"
_EMPHASIS = "*_"
_TRAILING_PUNCTUATION = ".\u3002!\uff01"

# In-flight title tasks by session id: the strong references asyncio does not
# keep, the one-at-a-time guard, and what the generate endpoint waits on when
# it arrives while the background call is still running.
_title_tasks: dict[str, asyncio.Task] = {}


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
    # Quotes, a "Title:" label, markdown emphasis and a trailing period can
    # wrap each other ('"Title: Foo".'), so peel them until nothing changes.
    while text:
        before = text
        text = _TITLE_PREFIX.sub("", text).strip()
        text = text.strip(_QUOTES).strip()
        text = text.strip(_EMPHASIS).strip()
        text = text.rstrip(_TRAILING_PUNCTUATION).strip()
        if text == before:
            break
    text = re.sub(r"\s+", " ", text)
    if len(text) > TITLE_MAX_CHARS:
        text = text[: TITLE_MAX_CHARS - 3].rstrip() + "..."
    return text


def _provider_family() -> str:
    llm = config.llm if isinstance(config.llm, Mapping) else {}
    provider = str(llm.get("provider") or "").strip().lower()
    return "ai_platform" if provider in {"ai_platform", "ai-platform"} else "github_copilot"


def title_model_for_config() -> str:
    """The cheap model for the configured provider family.

    ``llm.title_model`` overrides it, except an auto id: auto routing is a
    per-session Copilot choice with its own token, not a model for a one-shot
    call, so it falls back to the family's cheap model.
    """

    family = _provider_family()
    llm = config.llm if isinstance(config.llm, Mapping) else {}
    override = str(llm.get("title_model") or "").strip()
    if override and not is_auto_model_id(override):
        return override
    if override:
        logger.warning("llm.title_model %r is an auto id; titles use the family's cheap model instead", override)
    return cheap_model_for_family(family)


def _excerpt(text: str) -> str:
    collapsed = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    return truncate(collapsed, TITLE_INPUT_MAX_CHARS, "...")


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


def first_turn_texts(messages: Any) -> tuple[str, str]:
    """The first member message and the final answer of that turn ("" when missing).

    A tool-using turn stores one assistant message per step (a preamble with
    the tool call, then the answer); the title comes from the last assistant
    text before the next member message, not the preamble.
    """

    user_text = ""
    assistant_text = ""
    for message in messages or []:
        role = getattr(message, "role", None)
        if role is MessageRole.USER:
            if not user_text:
                user_text = member_message_text(message)
                continue
            if assistant_text:
                break
            continue
        if user_text and role is MessageRole.ASSISTANT:
            text = assistant_message_text(message)
            if text:
                assistant_text = text
    return user_text, assistant_text


def _event_type_value(event: Any) -> str:
    event_type = getattr(event, "type", None)
    return event_type.value if isinstance(event_type, LLMEventType) else str(event_type or "")


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
        event_type = _event_type_value(event)
        if event_type in (LLMEventType.PROVIDER_ERROR.value, LLMEventType.ERROR.value):
            raise TitleGenerationError(str(getattr(event, "error", None) or "provider error"))
        if event_type == LLMEventType.TEXT_DELTA.value:
            deltas.append(str(getattr(event, "delta", "") or ""))
        elif event_type == LLMEventType.TEXT_END.value:
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
    """Book the title call like a chat turn (usage.jsonl), with its own task type."""

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


def _configure_for_titles(provider: Any) -> None:
    """A ten-token reply does not need the chat's reasoning effort or an SSE stream."""

    if hasattr(provider, "reasoning_effort"):
        try:
            provider.reasoning_effort = TITLE_REASONING_EFFORT
        except Exception:  # pragma: no cover - a provider that validates on assignment keeps its own
            logger.debug("title reasoning effort not applied", exc_info=True)
    if hasattr(provider, "stream"):
        provider.stream = False


async def _build_title_provider(provider_builder: ProviderBuilder, execution_metadata: Mapping[str, Any] | None) -> tuple[Any, str]:
    """The provider for the title call, built off the loop (a Copilot token exchange blocks) and typed for the endpoint."""

    try:
        provider, model = await asyncio.to_thread(provider_builder, title_model_for_config(), execution_metadata=execution_metadata)
    except TitleGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - RuntimeChatError (401/400) and transport errors
        status_code = getattr(exc, "status_code", None)
        raise TitleGenerationError(
            f"Title provider unavailable: {exc}",
            status_code=status_code if isinstance(status_code, int) and 400 <= status_code < 600 else 502,
        ) from exc
    _configure_for_titles(provider)
    return provider, str(model)


async def generate_session_title(
    session_id: str,
    *,
    provider_builder: ProviderBuilder,
    execution_metadata: Mapping[str, Any] | None = None,
    store: Any = None,
    force: bool = False,
    timeout: float = TITLE_TIMEOUT_SECONDS,
    agent_id: str | None = None,
    track_usage: bool = True,
) -> Optional[str]:
    """Generate and store a title for ``session_id``; the stored name, or None when nothing changed.

    None means the session keeps the name it has: a manual name exists, or a
    generated one does and ``force`` is false. A missing session raises
    ``KeyError``; a session without an answered turn, a provider failure and
    a timeout raise ``TitleGenerationError``. With ``agent_id`` the new name is
    also sent to the Portal, in the background.
    """

    store = store or get_runtime_session_store()
    summary = store.get_session_summary(session_id)  # KeyError when unknown
    if summary.custom_name:
        return None
    if summary.generated_name and not force:
        return None

    messages = await asyncio.to_thread(store.read_history, session_id)
    user_text, assistant_text = first_turn_texts(messages)
    if not user_text or not assistant_text:
        raise TitleGenerationError("The session has no answered turn to name yet.", status_code=422)

    provider, model = await _build_title_provider(provider_builder, execution_metadata)
    request = build_title_request(session_id, user_text=user_text, assistant_text=assistant_text)
    try:
        text, usage = await asyncio.wait_for(_invoke_for_text(provider, request), timeout)
    except asyncio.TimeoutError as exc:
        raise TitleGenerationError(f"Title generation timed out after {timeout:g}s.", status_code=504) from exc
    except TitleGenerationError:
        raise
    except Exception as exc:  # noqa: BLE001 - any transport failure is a 502 here
        raise TitleGenerationError(f"Title generation failed: {exc}") from exc

    # The call happened: book it whatever becomes of the reply (an auto model
    # reports the id it was routed to).
    if track_usage:
        _record_usage(str(getattr(provider, "last_payload_model", None) or model), usage, session_id)

    title = clean_title(text)
    if not title:
        raise TitleGenerationError("The model returned no usable title.")

    # A rename that landed while the model was thinking wins.
    if store.get_session_summary(session_id).custom_name:
        return None
    # A title is not activity: the session keeps its place in the list.
    await asyncio.to_thread(store.update_session, session_id, metadata={GENERATED_NAME_KEY: title}, touch=False)
    if agent_id:
        publish_session_title_in_background(agent_id=agent_id, session_id=session_id, title=title)
    return title


def should_generate_after_turn(session_id: str, *, result: Any, store: Any = None) -> bool:
    """A completed chat turn with an answer, in a session that has no manual or generated name yet.

    Any such turn qualifies, not only the first: a first turn that failed, or
    a title call that did, gets its chance with the next answered turn.
    """

    if not session_id or is_task_session_id(session_id):
        return False
    if getattr(result, "status", None) != LoopStatus.COMPLETED:
        return False
    if not assistant_message_text(getattr(result, "final_assistant_message", None)):
        return False
    store = store or get_runtime_session_store()
    try:
        summary = store.get_session_summary(session_id)
    except KeyError:
        return False
    return not summary.custom_name and not summary.generated_name


def inflight_title_task(session_id: str) -> Optional[asyncio.Task]:
    task = _title_tasks.get(session_id)
    return task if task is not None and not task.done() else None


def schedule_session_title(
    session_id: str,
    *,
    result: Any,
    provider_builder: ProviderBuilder,
    execution_metadata: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    store: Any = None,
    track_usage: bool = True,
) -> Optional[asyncio.Task]:
    """Start the title call in the background after a turn; the task, or None when not due."""

    if not should_generate_after_turn(session_id, result=result, store=store):
        return None
    if inflight_title_task(session_id) is not None:
        return None
    task = asyncio.create_task(
        _generate_in_background(
            session_id,
            provider_builder=provider_builder,
            execution_metadata=execution_metadata,
            agent_id=agent_id,
            store=store,
            track_usage=track_usage,
        ),
        name=f"session-title:{session_id}",
    )
    _title_tasks[session_id] = task
    task.add_done_callback(lambda done, key=session_id: _title_tasks.pop(key, None) if _title_tasks.get(key) is done else None)
    return task


async def _generate_in_background(
    session_id: str,
    *,
    provider_builder: ProviderBuilder,
    execution_metadata: Mapping[str, Any] | None,
    agent_id: str | None,
    store: Any,
    track_usage: bool,
) -> Optional[str]:
    """The stored title, or None when the session keeps its name; never raises."""

    try:
        title = await generate_session_title(
            session_id,
            provider_builder=provider_builder,
            execution_metadata=execution_metadata,
            store=store,
            agent_id=agent_id,
            track_usage=track_usage,
        )
    except TitleGenerationError as exc:
        logger.warning("Session %s keeps its derived name: %s", session_id, exc.message)
        return None
    except Exception:
        logger.warning("Session %s keeps its derived name (title generation failed)", session_id, exc_info=True)
        return None
    if title:
        logger.info("Session %s titled: %s", session_id, title)
    return title


__all__ = [
    "CUSTOM_NAME_KEY",
    "GENERATED_NAME_KEY",
    "TITLE_REASONING_EFFORT",
    "TitleGenerationError",
    "build_title_request",
    "clean_title",
    "first_turn_texts",
    "generate_session_title",
    "inflight_title_task",
    "schedule_session_title",
    "should_generate_after_turn",
    "title_model_for_config",
]
