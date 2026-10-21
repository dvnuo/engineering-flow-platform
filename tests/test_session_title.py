"""Generated session titles: the one-shot model call after the first answered turn."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.efp_runtime.llm.events import LLMEvent, LLMEventType
from src.efp_runtime.session.file_store import FileSessionStore, build_session_summary
from src.efp_runtime.session.gateway_facade import (
    RuntimeSessionManager,
    legacy_messages_to_runtime,
    resolve_session_display_name,
    resolve_session_name_source,
)
from src.efp_runtime.session.models import Message, MessagePart, MessageRole
from src.efp_runtime.session.search import session_display_name
from src.gateway import session_title
from src.gateway.session_title import (
    GENERATED_NAME_KEY,
    TitleGenerationError,
    build_title_request,
    clean_title,
    first_turn_texts,
    generate_session_title,
    schedule_session_title,
    should_generate_after_turn,
)


USER_TEXT = "Our release pipeline fails at the deploy stage with a permissions error, how do I fix it?"
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


def _builder(reply, *, model="gpt-6-luna"):
    providers = []

    def build(requested_model, *, execution_metadata=None):
        provider = _FakeProvider(reply)
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
    session_title._inflight.clear()
    yield recorded
    session_title._inflight.clear()


# --- cleaning ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('"Release pipeline permissions"', "Release pipeline permissions"),
        ("Title: Release pipeline permissions.", "Release pipeline permissions"),
        ("“Release   pipeline”\nSecond line ignored", "Release pipeline"),
        ("标题：发布流水线权限问题。", "发布流水线权限问题"),
        ("  \n\n  ", ""),
        (None, ""),
    ],
)
def test_clean_title_strips_quotes_prefixes_and_periods(raw, expected):
    assert clean_title(raw) == expected


def test_clean_title_bounds_the_length():
    long_title = "x" * 100
    cleaned = clean_title(long_title)
    assert len(cleaned) == session_title.TITLE_MAX_CHARS
    assert cleaned.endswith("…")


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


def test_build_title_request_cuts_long_inputs():
    request = build_title_request("s-1", user_text="u" * 5000, assistant_text="a" * 5000)
    prompt = request.provider_request.messages[1].text
    assert "u" * session_title.TITLE_INPUT_MAX_CHARS + "…" in prompt
    assert "u" * (session_title.TITLE_INPUT_MAX_CHARS + 1) not in prompt


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


def test_first_turn_texts_without_an_answer():
    messages = legacy_messages_to_runtime("s-1", [{"role": "user", "content": "hello"}])
    assert first_turn_texts(messages) == ("hello", "")


# --- generation ---------------------------------------------------------------


async def test_generate_session_title_stores_the_cleaned_name(tmp_path, _no_usage_files):
    store = _store_with_turn(tmp_path)
    build = _builder({"content": '"Release pipeline permissions."', "usage": {"prompt_tokens": 80, "completion_tokens": 6}})

    title = await generate_session_title("s-1", provider_builder=build, store=store, execution_metadata={"k": "v"})

    assert title == "Release pipeline permissions"
    metadata = store.get_session("s-1").metadata
    assert metadata[GENERATED_NAME_KEY] == "Release pipeline permissions"
    assert metadata["generated_session_name_model"] == "gpt-6-luna"
    assert metadata["generated_session_name_at"]
    assert store.get_session("s-1").title is None  # the title column stays for manual names
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


# --- when it runs -------------------------------------------------------------


def test_should_generate_after_the_first_answered_turn(tmp_path):
    store = _store_with_turn(tmp_path)
    assert should_generate_after_turn("s-1", result=_completed_result(), store=store) is True


@pytest.mark.parametrize(
    "session_id, metadata, history, result, execution_metadata",
    [
        ("task-1", {}, None, _completed_result(), {}),
        ("s-1", {}, None, _completed_result(), {"portal_task_id": "t-1"}),
        ("s-1", {}, None, SimpleNamespace(status="waiting_for_question", final_assistant_message=None), {}),
        ("s-1", {}, None, SimpleNamespace(status="error", final_assistant_message=_completed_result().final_assistant_message), {}),
        ("s-1", {}, None, _completed_result(""), {}),
        ("s-1", {"custom_session_name": "Mine"}, None, _completed_result(), {}),
        ("s-1", {GENERATED_NAME_KEY: "Done"}, None, _completed_result(), {}),
        (
            "s-1",
            {},
            [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "a"},
                {"role": "user", "content": "two"},
                {"role": "assistant", "content": "b"},
            ],
            _completed_result(),
            {},
        ),
    ],
    ids=["task session", "task metadata", "parked on a question", "errored", "no answer text", "manual name", "already generated", "second turn"],
)
def test_should_not_generate(tmp_path, session_id, metadata, history, result, execution_metadata):
    store = _store_with_turn(tmp_path, session_id, messages=history, metadata=metadata)
    assert should_generate_after_turn(session_id, result=result, execution_metadata=execution_metadata, store=store) is False


def test_should_not_generate_for_an_unknown_session(tmp_path):
    store = FileSessionStore(tmp_path / "sessions")
    assert should_generate_after_turn("missing", result=_completed_result(), store=store) is False


async def test_schedule_session_title_runs_in_the_background_and_tells_the_portal(tmp_path, monkeypatch):
    store = _store_with_turn(tmp_path)
    published = []

    async def fake_publish(*, agent_id, session_id, title):
        published.append((agent_id, session_id, title))

    monkeypatch.setattr(session_title, "publish_session_title", fake_publish)
    build = _builder({"content": "Release pipeline permissions"})

    task = schedule_session_title("s-1", result=_completed_result(), provider_builder=build, store=store, agent_id="agent-1")
    assert task is not None
    await task

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


async def test_schedule_session_title_swallows_failures(tmp_path, caplog):
    store = _store_with_turn(tmp_path)
    task = schedule_session_title("s-1", result=_completed_result(), provider_builder=_builder(RuntimeError("boom")), store=store)
    await task  # no exception escapes
    assert GENERATED_NAME_KEY not in store.get_session("s-1").metadata
    assert "s-1" not in session_title._inflight


# --- the name everywhere ------------------------------------------------------


def test_display_name_precedence():
    history = [{"role": "user", "content": "a long first message that gets cut at thirty"}]
    derived = {"metadata": {}, "history": history}
    generated = {"metadata": {GENERATED_NAME_KEY: "Generated name"}, "history": history}
    custom = {"metadata": {GENERATED_NAME_KEY: "Generated name", "custom_session_name": "Manual name"}, "history": history}

    assert resolve_session_display_name(derived) == "a long first message that g..."
    assert resolve_session_name_source(derived) == "derived"
    assert resolve_session_display_name(generated) == "Generated name"
    assert resolve_session_name_source(generated) == "generated"
    assert resolve_session_display_name(custom) == "Manual name"
    assert resolve_session_name_source(custom) == "custom"
    assert resolve_session_name_source({"metadata": {}, "history": []}) == "none"


def test_session_summary_carries_the_generated_name(tmp_path):
    store = _store_with_turn(tmp_path, metadata={GENERATED_NAME_KEY: " Generated name "})
    summary = build_session_summary(store.get_session("s-1"))
    assert summary.generated_name == "Generated name"
    assert summary.custom_name is None
    assert session_display_name(summary) == "Generated name"
    assert store.get_session_summary("s-1").generated_name == "Generated name"


async def test_manager_get_session_title_reports_manual_or_generated_names(tmp_path):
    store = _store_with_turn(tmp_path)
    manager = RuntimeSessionManager(store=store)

    assert await manager.get_session_title("s-1") is None
    store.update_session("s-1", metadata={GENERATED_NAME_KEY: "Generated name"})
    assert await manager.get_session_title("s-1") == "Generated name"
    await manager.rename_session("s-1", "Manual name")
    assert await manager.get_session_title("s-1") == "Manual name"
    assert await manager.get_session_title("missing") is None
    assert await manager.get_session_title("") is None


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
