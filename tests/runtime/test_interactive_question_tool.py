"""The question tool is opt-in, and only interactive chat opts in.

Calling it parks the run until somebody answers. Portal draws an answer card
for a member watching the transcript, so chat can afford to ask; a scheduled
task, a Jira webhook, and a sub-agent cannot -- nothing there would ever answer,
and the work would sit blocked until it aged out.

`run_runtime_chat` is shared by all of them, which is why the flag defaults to
off and the interactive entry points turn it on, rather than the reverse. A task
path added later then inherits silence instead of a stall.
"""
from __future__ import annotations

import inspect

import pytest

from src.gateway import runtime_api, runtime_chat


@pytest.fixture(autouse=True)
def _quiet_config(monkeypatch):
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"session": {"max_iterations": 2}},
        raising=False,
    )


def _config(**kwargs):
    return runtime_chat._runtime_config("request-model", track_usage=False, **kwargs)


def test_the_question_tool_is_off_for_a_caller_that_says_nothing():
    assert _config().enable_question_tool is False


def test_interactive_chat_turns_the_question_tool_on():
    assert _config(interactive=True).enable_question_tool is True


@pytest.mark.parametrize("entry_point", [runtime_chat.run_runtime_chat, runtime_chat.resume_runtime_chat])
def test_the_shared_entry_points_default_to_not_asking(entry_point):
    # Tasks, sub-agents, and the Jira handler all call these without naming
    # `interactive`. The default is the only thing standing between them and a
    # run that blocks on a question nobody can see.
    assert inspect.signature(entry_point).parameters["interactive"].default is False


@pytest.mark.parametrize("configured", [True, False])
def test_a_runtime_profile_still_decides_for_itself(configured):
    # An operator who pinned the flag in the profile means it in both
    # directions; the interactive default must not quietly override either.
    resolved = _config(
        interactive=True,
        runtime_profile_config={"enable_question_tool": configured},
    )

    assert resolved.enable_question_tool is configured


def test_a_profile_that_says_nothing_about_questions_keeps_the_interactive_default():
    resolved = _config(interactive=True, runtime_profile_config={"enabled_tools": ["read"]})

    assert resolved.enable_question_tool is True


@pytest.mark.parametrize("configured", [True, False])
def test_a_managed_overlay_also_wins(monkeypatch, configured):
    monkeypatch.setattr(
        runtime_chat,
        "_active_managed_overlay_runtime_config",
        lambda: {"enable_question_tool": configured},
    )

    assert _config(interactive=True).enable_question_tool is configured


def test_the_flag_actually_puts_the_tool_in_front_of_the_model(monkeypatch, tmp_path):
    # The config field is only worth setting if it survives the whole way to the
    # registry the loop offers. Everything else here asserts on the flag.
    from efp_runtime.loop import ScriptedLLMProvider
    from efp_runtime.runtime import AgentRuntime

    monkeypatch.setattr(runtime_chat, "_runtime_workspace_root", lambda: tmp_path)

    def _registry(**kwargs):
        return AgentRuntime(
            provider=ScriptedLLMProvider([]),
            config=_config(**kwargs),
        ).tool_runtime.registry

    assert _registry(interactive=True).get("question") is not None
    assert _registry().get("question") is None


@pytest.mark.asyncio
async def test_the_chat_endpoints_ask_for_an_answerable_run(monkeypatch):
    captured: dict = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return {"response": "ok"}

    monkeypatch.setattr(runtime_api, "run_runtime_chat", _capture)

    await runtime_api._run_chat_via_execution_bus(
        session_id="s1",
        message="hello",
        user_name="member",
        portal_user_id=None,
        portal_user_name=None,
    )

    assert captured["interactive"] is True
