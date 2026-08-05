from __future__ import annotations

import json
from pathlib import Path

import pytest

from efp_runtime.compaction import ContextBudget, TailTurnCompactionStrategy
from efp_runtime.config_loader import load_runtime_config
from efp_runtime.models import CompactionPart, Message, MessagePart, ToolCall
from efp_runtime.runtime import RuntimeConfig


def test_runtime_config_validates_compaction_policy_defaults():
    config = RuntimeConfig(
        default_provider_id=" github-copilot ",
        default_model=" gpt-5 ",
        max_context_tokens=1000,
        context_reserve_tokens=100,
        compaction_auto=False,
        compaction_prune=False,
        compaction_tail_turns=3,
        compaction_preserve_recent_chars=1200,
        compaction_preserve_recent_tokens=800,
        compaction_reserved_chars=4000,
        compaction_tool_output_max_chars=1500,
    )

    assert config.default_provider_id == "github-copilot"
    assert config.default_model == "gpt-5"
    assert config.max_context_tokens == 1000
    assert config.context_reserve_tokens == 100
    assert config.compaction_auto is False
    assert config.compaction_prune is False
    assert config.compaction_tail_turns == 3
    assert config.compaction_preserve_recent_chars == 1200
    assert config.compaction_preserve_recent_tokens == 800
    assert config.compaction_reserved_chars == 4000
    assert config.compaction_tool_output_max_chars == 1500


@pytest.mark.parametrize(
    "field",
    [
        "compaction_tail_turns",
        "compaction_preserve_recent_chars",
        "compaction_preserve_recent_tokens",
        "compaction_reserved_chars",
        "compaction_tool_output_max_chars",
        "max_context_tokens",
        "context_reserve_tokens",
    ],
)
def test_runtime_config_rejects_negative_compaction_policy_ints(field: str):
    with pytest.raises(ValueError, match=field):
        RuntimeConfig(**{field: -1})


@pytest.mark.parametrize(
    "field",
    ["max_context_parts", "max_context_chars", "max_context_tokens"],
)
def test_runtime_config_rejects_zero_context_size_caps(field: str):
    """Zero is a typo, not a way to disable a cap.

    ``max_context_tokens: 0`` used to be accepted and produced a one-character
    budget - every request cut to system context plus the latest turn - while
    also flipping the deployment into rewriting stored history at that budget.
    It is rejected rather than coerced to None so the config file can never say
    ``0`` while the runtime silently runs the catalog default; the way to turn a
    cap off is to omit the key.
    """

    with pytest.raises(ValueError, match=field):
        RuntimeConfig(**{field: 0})


@pytest.mark.parametrize(
    "field",
    [
        "context_reserve_chars",
        "context_reserve_tokens",
        "compaction_reserved_chars",
        "compaction_preserve_recent_chars",
        "compaction_preserve_recent_tokens",
    ],
)
def test_runtime_config_keeps_zero_reserve_knobs(field: str):
    """Zero is meaningful for a reserve and must stay legal.

    "No response headroom, spend the whole budget on prompt" is distinct from
    unset ("use the model's declared reserve"), and a zero reserve cannot shrink
    the prompt budget. Only the three size caps are strictly positive; pinned so
    a later consistency cleanup cannot sweep these up with them.
    """

    assert getattr(RuntimeConfig(**{field: 0}), field) == 0


@pytest.mark.parametrize("value", [True, False, "5", 2.5])
@pytest.mark.parametrize(
    "field",
    [
        "max_context_parts",
        "max_context_chars",
        "max_context_tokens",
        "context_reserve_chars",
    ],
)
def test_runtime_config_rejects_bool_and_non_int_context_ints(field: str, value):
    """These four were validated by a bare ``<`` comparison.

    ``True`` therefore passed as a 1-unit budget (``True < 1`` is False) and a
    string raised a fieldless TypeError from the comparison itself rather than a
    ValueError naming the field.
    """

    with pytest.raises(ValueError, match=field):
        RuntimeConfig(**{field: value})


@pytest.mark.parametrize("field", ["default_provider_id", "default_model"])
def test_runtime_config_rejects_blank_model_context_strings(field: str):
    with pytest.raises(ValueError, match=field):
        RuntimeConfig(**{field: "   "})


@pytest.mark.parametrize(
    (
        "payload",
        "expected_tail_turns",
        "expected_preserve",
        "expected_reserved",
        "expected_tool_max",
    ),
    [
        (
            {
                "compaction": {
                    "auto": False,
                    "prune": False,
                    "tail_turns": 3,
                    "preserve_recent_chars": 1400,
                    "reserved": 6000,
                    "tool_output_max_chars": 1750,
                }
            },
            3,
            1400,
            6000,
            1750,
        ),
        (
            {
                "compaction": {
                    "auto": False,
                    "prune": False,
                    "tailTurns": 4,
                    "preserveRecentChars": 1500,
                    "reserved": 7000,
                    "toolOutputMaxChars": 1800,
                }
            },
            4,
            1500,
            7000,
            1800,
        ),
    ],
)
def test_config_loader_maps_nested_compaction_policy(
    tmp_path: Path,
    payload: dict[str, object],
    expected_tail_turns: int,
    expected_preserve: int,
    expected_reserved: int,
    expected_tool_max: int,
):
    _write_json(tmp_path / "policy.json", payload)

    result = load_runtime_config(
        tmp_path,
        paths=["policy.json"],
        include_defaults=False,
    )

    assert result.config.compaction_auto is False
    assert result.config.compaction_prune is False
    assert result.config.compaction_tail_turns == expected_tail_turns
    assert result.config.compaction_preserve_recent_chars == expected_preserve
    assert result.config.compaction_reserved_chars == expected_reserved
    assert result.config.compaction_tool_output_max_chars == expected_tool_max
    assert result.metadata["unconsumed_config"] == {}


def test_config_loader_maps_top_level_compaction_policy_fields(tmp_path: Path):
    _write_json(
        tmp_path / "policy.json",
        {
            "compaction_auto": False,
            "compaction_prune": False,
            "compaction_tail_turns": 5,
            "compaction_preserve_recent_chars": 1600,
            "compaction_reserved_chars": 8000,
            "compaction_tool_output_max_chars": 1900,
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["policy.json"],
        include_defaults=False,
    )

    assert result.config.compaction_auto is False
    assert result.config.compaction_prune is False
    assert result.config.compaction_tail_turns == 5
    assert result.config.compaction_preserve_recent_chars == 1600
    assert result.config.compaction_reserved_chars == 8000
    assert result.config.compaction_tool_output_max_chars == 1900
    assert result.metadata["unconsumed_config"] == {}


@pytest.mark.parametrize(
    "payload",
    [
        {"compaction": {"rewrite_stored_history": True}},
        {"compaction": {"rewriteStoredHistory": True}},
        {"compaction_rewrite_stored_history": True},
    ],
)
def test_config_loader_maps_the_stored_history_rewrite_opt_in(
    tmp_path: Path,
    payload: dict[str, object],
):
    """A config file can set only max_context_tokens of the size knobs.

    Without this alias, an efp.json deployment that used to get stored-history
    rewriting implied by that one knob would have no way to ask for it back.
    """

    _write_json(tmp_path / "policy.json", payload)

    result = load_runtime_config(
        tmp_path,
        paths=["policy.json"],
        include_defaults=False,
    )

    assert result.config.compaction_rewrite_stored_history is True
    assert result.metadata["unconsumed_config"] == {}


def test_runtime_config_defaults_to_not_rewriting_stored_history():
    assert RuntimeConfig().compaction_rewrite_stored_history is False
    assert RuntimeConfig(max_context_tokens=1000).compaction_rewrite_stored_history is (
        False
    )


def test_tail_turn_strategy_keeps_last_two_user_turns_and_marks_tail_start():
    messages = [
        _text("user", "old user " + "u" * 300, "msg-u1"),
        _text("assistant", "old answer " + "a" * 300, "msg-a1"),
        _text("user", "second request", "msg-u2"),
        _text("assistant", "second answer", "msg-a2"),
        _text("user", "third request", "msg-u3"),
        _text("assistant", "third answer", "msg-a3"),
    ]

    result = TailTurnCompactionStrategy(
        budget=ContextBudget(max_chars=200),
        preserve_recent_chars=200,
    ).compact(messages)

    assert result.compacted is True
    assert _visible_message_ids(result.messages) == [
        "msg-u2",
        "msg-a2",
        "msg-u3",
        "msg-a3",
    ]
    compaction = _tail_turn_compaction(result.messages)
    assert compaction.tail_start_message_id == "msg-u2"
    assert compaction.metadata["strategy"] == "tail_turn"
    assert compaction.metadata["tail_turns"] == 2
    assert compaction.metadata["preserve_recent_chars"] == 200
    assert compaction.metadata["tail_start_message_id"] == "msg-u2"


def test_tail_turn_strategy_splits_oversized_older_recent_turn():
    messages = [
        _text("user", "old user " + "u" * 300, "msg-u1"),
        _text("assistant", "old answer", "msg-a1"),
        _text("user", "oversized older recent request " + "x" * 100, "msg-u2"),
        _text("assistant", "suffix", "msg-a2"),
        _text("user", "now", "msg-u3"),
        _text("assistant", "done", "msg-a3"),
    ]

    result = TailTurnCompactionStrategy(
        budget=ContextBudget(max_chars=120),
        preserve_recent_chars=len("suffix") + len("now") + len("done"),
    ).compact(messages)

    assert result.compacted is True
    assert _visible_message_ids(result.messages) == ["msg-a2", "msg-u3", "msg-a3"]
    assert _tail_turn_compaction(result.messages).tail_start_message_id == "msg-a2"


def test_tail_turn_strategy_keeps_pending_tool_calls_visible():
    pending_call = ToolCall(
        call_id="call-pending",
        tool_name="write_file",
        arguments={"path": "created.txt", "content": "pending"},
    )
    messages = [
        _text("user", "old user " + "u" * 300, "msg-u1"),
        Message(
            role="assistant",
            message_id="msg-pending",
            parts=[MessagePart.tool_call_part(pending_call)],
        ),
        _text("user", "latest", "msg-u2"),
    ]

    result = TailTurnCompactionStrategy(
        budget=ContextBudget(max_chars=100),
        preserve_recent_chars=len("latest"),
    ).compact(messages)

    assert result.compacted is True
    calls = [
        part.tool_call.call_id
        for message in result.messages
        for part in message.parts
        if part.tool_call is not None
    ]
    assert calls == ["call-pending"]


def test_tail_turn_strategy_carries_previous_summary_from_whole_history():
    previous_summary = "Prior summary context marker."
    messages = [
        _text("user", "old user " + "u" * 300, "msg-u1"),
        _text("assistant", "old answer", "msg-a1"),
        _text("user", "latest request", "msg-u2"),
        _compaction_message(previous_summary, "msg-prev-summary"),
        _text("assistant", "latest answer", "msg-a2"),
    ]

    result = TailTurnCompactionStrategy(
        budget=ContextBudget(max_chars=120),
        tail_turns=1,
        preserve_recent_chars=5000,
    ).compact(messages)

    compaction = _tail_turn_compaction(result.messages)
    assert "msg-prev-summary" not in compaction.source_message_ids
    assert previous_summary in compaction.summary


def _text(role: str, text: str, message_id: str) -> Message:
    return Message.from_text(role, text, message_id=message_id)


def _compaction_message(summary: str, message_id: str) -> Message:
    compaction = CompactionPart(
        summary=summary,
        source_message_ids=["msg-prior"],
        auto=True,
    )
    return Message(
        role="system",
        message_id=message_id,
        parts=[MessagePart.compaction_part(compaction)],
    )


def _visible_message_ids(messages: list[Message]) -> list[str]:
    return [
        message.message_id
        for message in messages
        if not any(part.type == "compaction" for part in message.parts)
    ]


def _tail_turn_compaction(messages: list[Message]) -> CompactionPart:
    for message in messages:
        for part in message.parts:
            if (
                part.compaction is not None
                and part.compaction.metadata.get("strategy") == "tail_turn"
            ):
                return part.compaction
    raise AssertionError("tail-turn compaction part not found")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
