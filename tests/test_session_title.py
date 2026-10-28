"""Generated session titles: the one-shot model call after an answered turn."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from src.efp_runtime.llm.events import LLMEvent, LLMEventType
from src.efp_runtime.llm.models import cheap_model_for_family
from src.efp_runtime.reply_language import ReplyLanguage
from src.efp_runtime.session.file_store import FileSessionStore, build_session_summary
from src.efp_runtime.session.gateway_facade import (
    RuntimeSessionManager,
    legacy_messages_to_runtime,
    resolve_session_display_name,
    resolve_session_name,
    resolve_session_name_source,
)
from src.efp_runtime.session.models import Message, MessagePart, MessageRole
from src.efp_runtime.session.search import session_display_name
from src.gateway import runtime_chat, session_title
from src.gateway.session_title import (
    GENERATED_NAME_KEY,
    TitleGenerationError,
    build_title_request,
    clean_title,
    first_turn_texts,
    generate_session_title,
    inflight_title_task,
    schedule_session_title,
    should_generate_after_turn,
    title_instructions,
)


USER_TEXT ="Our release pipeline fails at the deploy stage with a permissions error, how do I fix it?"
ASSISTANT_TEXT = "The deploy stage runs as the jenkins user; grant it the deploy role or run the stage with the service account."


class _FakeProvider:
    """A provider that answers with a fixed mapping (or raises), remembering what it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        if callable(self.reply):
            return await self.reply(request)
        return self.reply


class _ChatLikeProvider(_FakeProvider):
    """A provider shaped like the chat one: streaming on, a reasoning effort, a routed model."""

    def __init__(self, reply):
        super().__init__(reply)
        self.stream = True
        self.reasoning_effort = "high"
        self.last_payload_model = "gpt-6-luna-routed"


def _builder(reply, *, model="gpt-6-luna", provider_class=_FakeProvider, error=None):
    providers = []

    def build(requested_model, *, execution_metadata=None):
        if error is not None:
            raise error
        provider = provider_class(reply)
        provider.requested_model = requested_model
        provider.execution_metadata = execution_metadata
        providers.append(provider)
        return provider, model

    build.providers = providers
    return build


def _store_with_turn(tmp_path, session_id="s-1", *, messages=None, metadata=None):
    store = FileSessionStore(tmp_path / "sessions")
    store.create_session(session_id=session_id, metadata=metadata or {})
    history = messages if messages is not None else [
        {"role": "user", "content": USER_TEXT},
        {"role": "assistant", "content": ASSISTANT_TEXT},
    ]
    store.replace_history(session_id, legacy_messages_to_runtime(session_id, history))
    return store


def _completed_result(text=ASSISTANT_TEXT):
    message = Message(role=MessageRole.ASSISTANT, parts=[MessagePart.text_part(text)])
    return SimpleNamespace(status="completed", final_assistant_message=message)


@pytest.fixture(autouse=True)
def _no_usage_files(monkeypatch):
    """Keep the usage tracker from writing under the home directory."""

    import src.sessions.usage as usage_module

    recorded = []
    monkeypatch.setattr(usage_module.usage_tracker, "record_usage", lambda **kwargs: recorded.append(kwargs))
    session_title._title_tasks.clear()
    yield recorded
    session_title._title_tasks.clear()


@pytest.fixture
def published(monkeypatch):
    """The Portal publishes a title would start, recorded instead."""

    calls = []
    monkeypatch.setattr(
        session_title,
        "publish_session_title_in_background",
        lambda *, agent_id, session_id, title: calls.append((agent_id, session_id, title)),
    )
    return calls


# --- cleaning ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"Release pipeline permissions"', "Release pipeline permissions"),
        ("Title: Release pipeline permissions.", "Release pipeline permissions"),
        ("\u201cRelease   pipeline\u201d\nSecond line ignored", "Release pipeline"),
        ("\u6807\u9898\uff1a\u53d1\u5e03\u6d41\u6c34\u7ebf\u6743\u9650\u95ee\u9898\u3002", "\u53d1\u5e03\u6d41\u6c34\u7ebf\u6743\u9650\u95ee\u9898"),
        # Wrapped in each other: the quotes hide the label, the label hides the period.
        ('"Title: Release pipeline permissions."', "Release pipeline permissions"),
        ("**Release pipeline permissions**", "Release pipeline permissions"),
        ("_Release pipeline permissions_", "Release pipeline permissions"),
        ("\n\nSession name: 'Deploy stage fix'!", "Deploy stage fix"),
        ("  \n\n  ", ""),
        (None, ""),
    ],
)
def test_clean_title_strips_quotes_prefixes_emphasis_and_periods(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_bounds_the_length():
    cleaned = clean_title("x" * 100)
    assert len(cleaned) == session_title.TITLE_MAX_CHARS
    assert cleaned.endswith("...")
    assert clean_title("x" * session_title.TITLE_MAX_CHARS) == "x" * session_title.TITLE_MAX_CHARS


# --- the request --------------------------------------------------------------


def test_build_title_request_carries_instructions_and_the_first_turn():
    request = build_title_request("s-1", user_text=USER_TEXT, assistant_text=ASSISTANT_TEXT)
    messages = request.provider_request.messages
    assert [message.role for message in messages] == ["system", "user"]
    assert "Reply with the title only" in messages[0].text
    assert USER_TEXT in messages[1].text
    assert ASSISTANT_TEXT in messages[1].text
    assert request.metadata["task_type"] == "session_title"
    assert request.messages == []
    assert request.provider_request.tools == []


def test_build_title_request_cuts_long_inputs():
    request = build_title_request("s-1", user_text="u" * 5000, assistant_text="a" * 5000)
    prompt = request.provider_request.messages[1].text
    assert "u" * session_title.TITLE_INPUT_MAX_CHARS + "..." in prompt
    assert "u" * (session_title.TITLE_INPUT_MAX_CHARS + 1) not in prompt


USER_LANGUAGE_RULE = "- The same language as the user's message."
ANSWER_LANGUAGE_RULE = "- The same language as the assistant's answer."


def test_title_instructions_follow_the_users_message_by_default():
    instructions = title_instructions()

    assert instructions == title_instructions(None) == session_title.TITLE_INSTRUCTIONS
    assert f"{USER_LANGUAGE_RULE}\n" in instructions
    assert ANSWER_LANGUAGE_RULE not in instructions
    assert build_title_request("s-1", user_text=USER_TEXT, assistant_text=ASSISTANT_TEXT).provider_request.messages[0].text == instructions


@pytest.mark.parametrize("reply_language", [ReplyLanguage("zh-Hans", False), ReplyLanguage("zh-Hant-HK", True), ReplyLanguage("en", True)])
def test_title_instructions_follow_the_answer_when_a_reply_language_applies(reply_language):
    # The answer was written under the "Reply language" block (strict, or
    # following the member's message), so it is the language signal.
    instructions = title_instructions(reply_language)

    assert f"{ANSWER_LANGUAGE_RULE}\n" in instructions
    assert USER_LANGUAGE_RULE not in instructions
    # Only that one line changes.
    assert instructions.replace(ANSWER_LANGUAGE_RULE, USER_LANGUAGE_RULE, 1) == session_title.TITLE_INSTRUCTIONS
    request = build_title_request("s-1", user_text=USER_TEXT, assistant_text=ASSISTANT_TEXT, reply_language=reply_language)
    assert request.provider_request.messages[0].text == instructions
    assert USER_TEXT in request.provider_request.messages[1].text


def test_first_turn_texts_prefers_the_members_own_words():
    messages = legacy_messages_to_runtime(
        "s-1",
        [
            {"role": "user", "content": "[attachment context]\n\nWhat does this CSV show?", "metadata": {"original_user_message": "What does this CSV show?"}},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "Monthly revenue by region."},
        ],
    )
    assert first_turn_texts(messages) == ("What does this CSV show?", "Monthly revenue by region.")


def test_first_turn_texts_pairs_the_question_with_the_final_answer_of_its_turn():
    # A tool-using turn stores the preamble and the answer as two assistant
    # messages; the next member message starts the second turn.
    messages = legacy_messages_to_runtime(
        "s-1",
        [
            {"role": "user", "content": "Why is the deploy stage red?"},
            {"role": "assistant", "content": "Let me read the pipeline log."},
            {"role": "assistant", "content": "The jenkins user lacks the deploy role."},
            {"role": "user", "content": "Thanks, and the tests?"},
            {"role": "assistant", "content": "They pass."},
        ],
    )
    assert first_turn_texts(messages) == ("Why is the deploy stage red?", "The jenkins user lacks the deploy role.")


def test_first_turn_texts_skips_runtime_injected_turns():
    messages = legacy_messages_to_runtime(
        "s-1",
        [
            {"role": "user", "content": "[background task result]", "metadata": {"source": "background_task.injected"}},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    )
    assert first_turn_texts(messages) == ("hello", "hi")


def test_first_turn_texts_without_an_answer():
    messages = legacy_messages_to_runtime("s-1", [{"role": "user", "content": "hello"}])
    assert first_turn_texts(messages) == ("hello", "")


# --- generation ---------------------------------------------------------------


async def test_generate_session_title_stores_the_cleaned_name(tmp_path, _no_usage_files):
    store = _store_with_turn(tmp_path)
    updated_before = store.get_session("s-1").updated_at
    build = _builder({"content": '"Release pipeline permissions."', "usage": {"prompt_tokens": 80, "completion_tokens": 6}})

    title = await generate_session_title("s-1", provider_builder=build, store=store, execution_metadata={"k": "v"})

    assert title == "Release pipeline permissions"
    session = store.get_session("s-1")
    assert session.metadata == {GENERATED_NAME_KEY: "Release pipeline permissions"}
    assert session.title is None  # the title column stays for manual names
    assert session.updated_at == updated_before  # a name is not activity
    provider = build.providers[0]
    assert provider.requested_model == "gpt-6-luna"
    assert provider.execution_metadata == {"k": "v"}
    assert _no_usage_files == [
        {
            "provider": "github_copilot",
            "model": "gpt-6-luna",
            "input_tokens": 80,
            "output_tokens": 6,
            "session_id": "s-1",
            "task_type": "session_title",
        }
    ]


async def test_generate_session_title_turns_the_chat_provider_into_a_one_shot(tmp_path, _no_usage_files):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": "Deploy stage permissions"}, provider_class=_ChatLikeProvider)

    assert await generate_session_title("s-1", provider_builder=build, store=store) == "Deploy stage permissions"

    provider = build.providers[0]
    assert provider.stream is False
    assert provider.reasoning_effort == session_title.TITLE_REASONING_EFFORT == "low"
    # Usage is booked under the model the call was routed to.
    assert _no_usage_files[0]["model"] == "gpt-6-luna-routed"


async def test_generate_session_title_adds_no_reasoning_effort_the_chat_does_not_send(tmp_path):
    store = _store_with_turn(tmp_path)

    class _NoEffortProvider(_ChatLikeProvider):
        def __init__(self, reply):
            super().__init__(reply)
            self.reasoning_effort = None

    build = _builder({"content": "Deploy stage permissions"}, provider_class=_NoEffortProvider)
    assert await generate_session_title("s-1", provider_builder=build, store=store) == "Deploy stage permissions"
    assert build.providers[0].reasoning_effort is None
    assert build.providers[0].stream is False


async def test_generate_session_title_reads_a_non_streaming_responses_answer_with_the_real_provider(tmp_path, _no_usage_files):
    # The Copilot provider as the chat builds it (streaming, effort high),
    # answered by a recorded non-streaming /responses body.
    from src.efp_runtime.llm.provider import GitHubCopilotProvider, RecordingTransport

    store = _store_with_turn(tmp_path)
    transport = RecordingTransport([
        {
            "id": "resp_1",
            "model": "gpt-6-luna",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "\"Deploy stage permissions\""}]},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 9},
        }
    ])

    def build(requested_model, *, execution_metadata=None):
        provider = GitHubCopilotProvider(transport=transport, model=requested_model, stream=True, reasoning_effort="high")
        return provider, requested_model

    assert await generate_session_title("s-1", provider_builder=build, store=store) == "Deploy stage permissions"
    payload = transport.payloads[0]
    assert payload["stream"] is False
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["model"] == "gpt-6-luna"
    assert "tools" not in payload or payload["tools"]
    assert _no_usage_files[0]["input_tokens"] == 120
    assert _no_usage_files[0]["output_tokens"] == 9


async def test_generate_session_title_can_skip_usage_tracking(tmp_path, _no_usage_files):
    store = _store_with_turn(tmp_path)
    await generate_session_title("s-1", provider_builder=_builder({"content": "T"}), store=store, track_usage=False)
    assert _no_usage_files == []


def _system_text(build):
    return build.providers[0].requests[0].provider_request.messages[0].text


async def test_generate_session_title_asks_for_the_answers_language_with_a_reply_language(tmp_path):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": "T"})

    await generate_session_title("s-1", provider_builder=build, store=store, reply_language=ReplyLanguage("zh-Hans", True))
    assert ANSWER_LANGUAGE_RULE in _system_text(build)
    assert USER_LANGUAGE_RULE not in _system_text(build)

    plain = _builder({"content": "T"})
    await generate_session_title("s-1", provider_builder=plain, store=store, force=True)
    assert USER_LANGUAGE_RULE in _system_text(plain)
    assert ANSWER_LANGUAGE_RULE not in _system_text(plain)


async def test_generate_session_title_books_usage_even_when_the_reply_is_unusable(tmp_path, _no_usage_files):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": '""', "usage": {"prompt_tokens": 80, "completion_tokens": 2}})
    with pytest.raises(TitleGenerationError):
        await generate_session_title("s-1", provider_builder=build, store=store)
    assert _no_usage_files[0]["input_tokens"] == 80


async def test_generate_session_title_reads_streamed_text_events(tmp_path):
    store = _store_with_turn(tmp_path)

    async def stream(_request):
        async def events():
            yield LLMEvent(LLMEventType.TEXT_START)
            yield LLMEvent(LLMEventType.TEXT_DELTA, delta="Deploy stage ")
            yield LLMEvent(LLMEventType.TEXT_DELTA, delta="permissions")
            yield LLMEvent(LLMEventType.TEXT_END, text="Deploy stage permissions")
            yield LLMEvent(LLMEventType.FINISH, usage={"prompt_tokens": 1, "completion_tokens": 1})

        return events()

    title = await generate_session_title("s-1", provider_builder=_builder(stream), store=store)
    assert title == "Deploy stage permissions"


async def test_generate_session_title_keeps_a_manual_name(tmp_path):
    store = _store_with_turn(tmp_path, metadata={"custom_session_name": "My deploy notes"})
    build = _builder({"content": "Something else"})

    assert await generate_session_title("s-1", provider_builder=build, store=store) is None
    assert build.providers == []
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata


async def test_generate_session_title_keeps_a_generated_name_unless_forced(tmp_path):
    store = _store_with_turn(tmp_path, metadata={GENERATED_NAME_KEY: "First title"})
    build = _builder({"content": "Second title"})

    assert await generate_session_title("s-1", provider_builder=build, store=store) is None
    assert build.providers == []

    assert await generate_session_title("s-1", provider_builder=build, store=store, force=True) == "Second title"
    assert store.get_session("s-1").metadata[GENERATED_NAME_KEY] == "Second title"


async def test_generate_session_title_needs_an_answered_turn(tmp_path):
    store = _store_with_turn(tmp_path, messages=[{"role": "user", "content": "hello"}])
    build = _builder({"content": "Hello"})

    with pytest.raises(TitleGenerationError) as excinfo:
        await generate_session_title("s-1", provider_builder=build, store=store)
    assert excinfo.value.status_code == 422
    assert build.providers == []


async def test_generate_session_title_unknown_session(tmp_path):
    store = FileSessionStore(tmp_path / "sessions")
    with pytest.raises(KeyError):
        await generate_session_title("missing", provider_builder=_builder({"content": "x"}), store=store)


async def test_generate_session_title_provider_error_leaves_the_session_alone(tmp_path):
    store = _store_with_turn(tmp_path)
    before = store.get_session("s-1").metadata
    build = _builder({"error": {"message": "rate limited", "type": "rate_limit"}})

    with pytest.raises(TitleGenerationError) as excinfo:
        await generate_session_title("s-1", provider_builder=build, store=store)
    assert excinfo.value.status_code == 502
    assert "rate limited" in excinfo.value.message
    assert store.get_session("s-1").metadata == before


async def test_generate_session_title_transport_exception_is_a_502(tmp_path):
    store = _store_with_turn(tmp_path)
    build = _builder(RuntimeError("connection reset"))

    with pytest.raises(TitleGenerationError) as excinfo:
        await generate_session_title("s-1", provider_builder=build, store=store)
    assert excinfo.value.status_code == 502
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata


async def test_generate_session_title_reports_a_provider_that_cannot_be_built(tmp_path):
    store = _store_with_turn(tmp_path)
    build = _builder(None, error=runtime_chat.RuntimeChatError("GitHub Copilot token is required", status_code=401))

    with pytest.raises(TitleGenerationError) as excinfo:
        await generate_session_title("s-1", provider_builder=build, store=store)
    assert excinfo.value.status_code == 401
    assert "token is required" in excinfo.value.message

    with pytest.raises(TitleGenerationError) as excinfo:
        await generate_session_title("s-1", provider_builder=_builder(None, error=OSError("no network")), store=store)
    assert excinfo.value.status_code == 502


async def test_generate_session_title_times_out(tmp_path):
    store = _store_with_turn(tmp_path)

    async def slow(_request):
        await asyncio.sleep(1)
        return {"content": "late"}

    with pytest.raises(TitleGenerationError) as excinfo:
        await generate_session_title("s-1", provider_builder=_builder(slow), store=store, timeout=0.01)
    assert excinfo.value.status_code == 504


async def test_generate_session_title_empty_reply_is_an_error(tmp_path):
    store = _store_with_turn(tmp_path)
    with pytest.raises(TitleGenerationError):
        await generate_session_title("s-1", provider_builder=_builder({"content": '""'}), store=store)
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata


async def test_a_rename_during_generation_wins(tmp_path):
    store = _store_with_turn(tmp_path)

    async def rename_meanwhile(_request):
        store.update_session("s-1", title="Renamed", metadata={"custom_session_name": "Renamed"})
        return {"content": "Generated"}

    assert await generate_session_title("s-1", provider_builder=_builder(rename_meanwhile), store=store) is None
    metadata = store.get_session("s-1").metadata
    assert metadata["custom_session_name"] == "Renamed"
    assert GENERATED_NAME_KEY not in metadata


async def test_generate_session_title_tells_the_portal_when_it_knows_the_agent(tmp_path, published):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": "Release pipeline permissions"})

    await generate_session_title("s-1", provider_builder=build, store=store, agent_id="agent-1")
    assert published == [("agent-1", "s-1", "Release pipeline permissions")]

    published.clear()
    await generate_session_title("s-1", provider_builder=build, store=store, force=True)
    assert published == []


# --- when it runs -------------------------------------------------------------


def test_should_generate_after_an_answered_turn(tmp_path):
    store = _store_with_turn(tmp_path)
    assert should_generate_after_turn("s-1", result=_completed_result(), store=store) is True


def test_a_later_turn_names_a_session_that_is_still_unnamed(tmp_path):
    # The first turn's title call failed (or the first turn had no answer):
    # the next answered turn gets its chance.
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "b"},
    ]
    store = _store_with_turn(tmp_path, messages=history)
    assert should_generate_after_turn("s-1", result=_completed_result(), store=store) is True


@pytest.mark.parametrize(
    "session_id, metadata, result",
    [
        ("task-1", {}, _completed_result()),
        ("agent-task:abc", {}, _completed_result()),
        ("s-1", {}, SimpleNamespace(status="waiting_for_question", final_assistant_message=None)),
        ("s-1", {}, SimpleNamespace(status="error", final_assistant_message=_completed_result().final_assistant_message)),
        ("s-1", {}, _completed_result("")),
        ("s-1", {"custom_session_name": "Mine"}, _completed_result()),
        ("s-1", {GENERATED_NAME_KEY: "Done"}, _completed_result()),
        ("", {}, _completed_result()),
    ],
    ids=["task session", "task session (colon prefix)", "parked on a question", "errored", "no answer text", "manual name", "already generated", "no session id"],
)
def test_should_not_generate(tmp_path, session_id, metadata, result):
    # Task ids are refused before the store is read (a colon is no file name on Windows).
    store = _store_with_turn(tmp_path, session_id if session_id == "s-1" else "s-1", metadata=metadata)
    assert should_generate_after_turn(session_id, result=result, store=store) is False


def test_should_not_generate_for_an_unknown_session(tmp_path):
    store = FileSessionStore(tmp_path / "sessions")
    assert should_generate_after_turn("missing", result=_completed_result(), store=store) is False


async def test_schedule_session_title_runs_in_the_background_and_tells_the_portal(tmp_path, published):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": "Release pipeline permissions"})

    task = schedule_session_title("s-1", result=_completed_result(), provider_builder=build, store=store, agent_id="agent-1")
    assert task is not None
    assert inflight_title_task("s-1") is task
    assert await task == "Release pipeline permissions"

    assert inflight_title_task("s-1") is None
    assert store.get_session("s-1").metadata[GENERATED_NAME_KEY] == "Release pipeline permissions"
    assert published == [("agent-1", "s-1", "Release pipeline permissions")]
    # Once named, the next turn does not schedule again.
    assert schedule_session_title("s-1", result=_completed_result(), provider_builder=build, store=store) is None


async def test_schedule_session_title_does_not_run_twice_at_once(tmp_path):
    store = _store_with_turn(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(_request):
        started.set()
        await release.wait()
        return {"content": "Late title"}

    build = _builder(slow)
    first = schedule_session_title("s-1", result=_completed_result(), provider_builder=build, store=store)
    await started.wait()
    assert schedule_session_title("s-1", result=_completed_result(), provider_builder=build, store=store) is None
    release.set()
    await first
    assert len(build.providers) == 1


async def test_schedule_session_title_swallows_failures(tmp_path):
    store = _store_with_turn(tmp_path)
    task = schedule_session_title("s-1", result=_completed_result(), provider_builder=_builder(RuntimeError("boom")), store=store)
    assert await task is None  # no exception escapes
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata
    assert inflight_title_task("s-1") is None


async def test_schedule_session_title_passes_usage_tracking_through(tmp_path, _no_usage_files):
    store = _store_with_turn(tmp_path)
    task = schedule_session_title("s-1", result=_completed_result(), provider_builder=_builder({"content": "T"}), store=store, track_usage=False)
    await task
    assert _no_usage_files == []


async def test_schedule_session_title_passes_the_reply_language_through(tmp_path):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": "T"})
    task = schedule_session_title(
        "s-1",
        result=_completed_result(),
        provider_builder=build,
        store=store,
        reply_language=ReplyLanguage("zh-Hant-HK", False),
    )
    assert await task == "T"
    assert ANSWER_LANGUAGE_RULE in _system_text(build)


# --- only the Portal's chat opts in -------------------------------------------


def test_chat_primitives_do_not_title_by_default():
    # Jira comments, tasks and the vision path call these without the flag.
    assert inspect.signature(runtime_chat.run_runtime_chat).parameters["title_after_turn"].default is False
    assert inspect.signature(runtime_chat.resume_runtime_chat).parameters["title_after_turn"].default is False


def test_after_turn_hook_never_raises(monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime_chat, "schedule_session_title", explode)
    runtime_chat._schedule_session_title_after_turn("s-1", _completed_result(), execution_metadata=None, agent_id=None, track_usage=True)


def test_after_turn_hook_resolves_the_reply_language_from_the_turns_trusted_metadata(monkeypatch):
    captured = []
    monkeypatch.setattr(runtime_chat, "schedule_session_title", lambda session_id, **kwargs: captured.append(kwargs))

    def after_turn(execution_metadata):
        runtime_chat._schedule_session_title_after_turn(
            "s-1", _completed_result(), execution_metadata=execution_metadata, agent_id="agent-1", track_usage=True
        )
        return captured[-1]

    strict = after_turn({"portal_user": {"id": "77", "reply_language": "zh-Hans", "reply_language_strict": True}})
    assert strict["reply_language"] == ReplyLanguage("zh-Hans", True)
    assert strict["execution_metadata"] == {"portal_user": {"id": "77", "reply_language": "zh-Hans", "reply_language_strict": True}}
    assert strict["agent_id"] == "agent-1"
    assert after_turn({"portal_user": {"id": "77", "reply_language": "zh-Hant-HK"}})["reply_language"] == ReplyLanguage("zh-Hant-HK", False)
    # English without an explicit choice, an unknown tag, no portal_user: the instructions stay as they were.
    assert after_turn({"portal_user": {"id": "77", "reply_language": "en"}})["reply_language"] is None
    assert after_turn({"portal_user": {"id": "77", "reply_language": "fr", "reply_language_strict": True}})["reply_language"] is None
    assert after_turn({"portal_user_id": "77"})["reply_language"] is None
    assert after_turn(None)["reply_language"] is None


# --- the name everywhere ------------------------------------------------------


def test_display_name_precedence():
    history = [{"role": "user", "content": "a long first message that gets cut at thirty"}]
    derived = {"metadata": {}, "history": history}
    generated = {"metadata": {GENERATED_NAME_KEY: "Generated name"}, "history": history}
    custom = {"metadata": {GENERATED_NAME_KEY: "Generated name", "custom_session_name": "Manual name"}, "history": history}
    titled = {"metadata": {}, "title": "Imported title", "history": history}

    assert resolve_session_name(derived) == ("a long first message that g...", "derived")
    assert resolve_session_name(generated) == ("Generated name", "generated")
    assert resolve_session_name(custom) == ("Manual name", "custom")
    assert resolve_session_name(titled) == ("Imported title", "title")
    assert resolve_session_name({"metadata": {}, "history": []}) == ("New Chat", "none")
    # A first message without text is no name either.
    assert resolve_session_name({"metadata": {}, "history": [{"role": "user", "content": "   "}]}) == ("New Chat", "none")
    assert resolve_session_display_name(custom) == "Manual name"
    assert resolve_session_name_source(generated) == "generated"


def test_session_summary_carries_the_generated_name(tmp_path):
    store = _store_with_turn(tmp_path, metadata={GENERATED_NAME_KEY: " Generated name "})
    summary = build_session_summary(store.get_session("s-1"))
    assert summary.generated_name == "Generated name"
    assert summary.custom_name is None
    assert session_display_name(summary) == "Generated name"
    assert store.get_session_summary("s-1").generated_name == "Generated name"


async def test_manager_get_session_title_reports_manual_generated_or_set_names(tmp_path):
    store = _store_with_turn(tmp_path)
    manager = RuntimeSessionManager(store=store)

    assert await manager.get_session_title("s-1") is None
    store.update_session("s-1", title="Imported title")
    assert await manager.get_session_title("s-1") == "Imported title"
    store.update_session("s-1", metadata={GENERATED_NAME_KEY: "Generated name"})
    assert await manager.get_session_title("s-1") == "Generated name"
    await manager.rename_session("s-1", "Manual name")
    assert await manager.get_session_title("s-1") == "Manual name"
    assert await manager.get_session_title("missing") is None
    assert await manager.get_session_title("") is None


def test_update_session_without_touch_keeps_updated_at(tmp_path):
    store = _store_with_turn(tmp_path)
    before = store.get_session("s-1").updated_at
    store.update_session("s-1", metadata={"k": "v"}, touch=False)
    assert store.get_session("s-1").updated_at == before
    assert store.get_session("s-1").metadata["k"] == "v"


# --- the name follows the first message ----------------------------------------


def _history_ids(store, session_id="s-1"):
    return [message.message_id for message in store.read_history(session_id)]


async def test_editing_the_first_message_forgets_the_generated_name(tmp_path):
    store = _store_with_turn(tmp_path, metadata={GENERATED_NAME_KEY: "Release pipeline permissions"})
    manager = RuntimeSessionManager(store=store)
    first_user, first_assistant = _history_ids(store)

    assert await manager.edit_message("s-1", first_assistant, "An edited answer") is True
    assert store.get_session("s-1").metadata[GENERATED_NAME_KEY] == "Release pipeline permissions"

    assert await manager.edit_message("s-1", first_user, "A different question") is True
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata
    assert await manager.edit_message("s-1", "missing", "x") is False


async def test_deleting_the_first_message_forgets_the_generated_name(tmp_path):
    history = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "b"},
    ]
    store = _store_with_turn(tmp_path, messages=history, metadata={GENERATED_NAME_KEY: "About one"})
    manager = RuntimeSessionManager(store=store)
    first_user, _, second_user, second_assistant = _history_ids(store)

    assert await manager.delete_message("s-1", second_assistant) is True
    assert store.get_session("s-1").metadata[GENERATED_NAME_KEY] == "About one"
    assert await manager.delete_messages_from("s-1", second_user) == 1
    assert store.get_session("s-1").metadata[GENERATED_NAME_KEY] == "About one"

    assert await manager.delete_message("s-1", first_user) is True
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata


async def test_deleting_from_the_first_message_forgets_the_generated_name(tmp_path):
    store = _store_with_turn(tmp_path, metadata={GENERATED_NAME_KEY: "Release pipeline permissions"})
    manager = RuntimeSessionManager(store=store)
    first_user, _ = _history_ids(store)

    assert await manager.delete_messages_from("s-1", first_user) == 2
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata
    assert store.read_history("s-1") == []


# --- the model ------------------------------------------------------------------


def test_cheap_model_for_family():
    assert cheap_model_for_family("github_copilot") == "gpt-6-luna"
    assert cheap_model_for_family("ai_platform") == "gpt-5.6-luna"
    assert cheap_model_for_family("unknown") == "gpt-6-luna"


def test_title_model_follows_the_configured_provider(monkeypatch):
    from src.config import config

    def set_llm(value):
        monkeypatch.setattr(type(config), "llm", property(lambda _self: value))

    set_llm({"provider": "github_copilot"})
    assert session_title.title_model_for_config() == "gpt-6-luna"
    set_llm({"provider": "ai_platform"})
    assert session_title.title_model_for_config() == "gpt-5.6-luna"
    set_llm({"provider": "ai_platform", "title_model": "gpt-5.4"})
    assert session_title.title_model_for_config() == "gpt-5.4"
    # Auto routing is a per-session Copilot choice, not a model for a one-shot call.
    set_llm({"provider": "github_copilot", "title_model": "auto"})
    assert session_title.title_model_for_config() == "gpt-6-luna"
